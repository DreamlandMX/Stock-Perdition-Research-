"""Stream canonical CSV/Parquet into immutable, provenance-backed history batches."""

import argparse
from collections.abc import Iterator
import csv
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from filelock import FileLock
from pydantic import Field, model_validator
import pyarrow as pa
import pyarrow.parquet as pq

from stock_prediction.contracts.options import (
    OptionChainSnapshot,
    OptionContract,
    OptionModel,
)
from stock_prediction.live.locking import _atomic_json
from stock_prediction.options.store import OptionSnapshotStore


class OptionHistoryImportManifest(OptionModel):
    provider: str = Field(min_length=1)
    feed: str = Field(min_length=1)
    normalization_version: str = Field(min_length=1)
    dataset_revision: str = Field(min_length=1)
    license_reference: str = Field(min_length=1)
    availability_evidence: tuple[str, ...] = Field(min_length=1)
    iv_unit: Literal["annualized_decimal", "percent_annualized"]
    delta_unit: Literal["unknown", "signed_unit_v1", "signed_percent"]
    gamma_unit: Literal["unknown", "per_usd_v1"]
    count_unit: Literal["contracts"] = "contracts"
    multiplier_unit: Literal["contracts_per_option"] = "contracts_per_option"
    encoding: Literal["utf-8", "utf-8-sig"] = "utf-8-sig"
    field_map: dict[str, str] = {}
    snapshots: dict[str, dict[str, Any]] = Field(min_length=1)

    @model_validator(mode="after")
    def check_manifest(self) -> "OptionHistoryImportManifest":
        if any(
            not value.strip()
            for value in (
                self.provider,
                self.feed,
                self.normalization_version,
                self.dataset_revision,
                self.license_reference,
            )
        ):
            raise ValueError("history provenance references cannot be blank")
        if any(not reference.strip() for reference in self.availability_evidence):
            raise ValueError("availability evidence references cannot be empty")
        known = {*OptionContract.model_fields, "snapshot_id"}
        if not set(self.field_map).issubset(known):
            raise ValueError("unknown canonical field in normalization map")
        if len(set(self.field_map.values())) != len(self.field_map):
            raise ValueError("normalization field mapping is not one-to-one")
        for identity, metadata in self.snapshots.items():
            if metadata.get("snapshot_id") != identity or "contracts" in metadata:
                raise ValueError(
                    "manifest requires snapshot metadata without contracts"
                )
            if not {"expected_contract_ids", "complete", "discovery_complete"}.issubset(
                metadata
            ):
                raise ValueError(
                    "manifest requires explicit discovery and coverage evidence"
                )
        return self


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _input_rows(source: Path, encoding: str) -> Iterator[dict[str, Any]]:
    if source.suffix.lower() == ".csv":
        with source.open("r", encoding=encoding, newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or len(set(reader.fieldnames)) != len(
                reader.fieldnames
            ):
                raise ValueError("CSV requires unique column headers")
            for row in reader:
                if None in row:
                    raise ValueError("CSV row has more fields than its header")
                yield dict(row)
    elif source.suffix.lower() == ".parquet":
        parquet = pq.ParquetFile(source)
        if len(set(parquet.schema.names)) != len(parquet.schema.names):
            raise ValueError("Parquet requires unique columns")
        for batch in parquet.iter_batches(batch_size=10000):
            yield from batch.to_pylist()
    else:
        raise ValueError("historical option input must be CSV or Parquet")


def _normalize(
    row: dict[str, Any], manifest: OptionHistoryImportManifest
) -> tuple[str, OptionContract]:
    reverse = {source: canonical for canonical, source in manifest.field_map.items()}
    if len({reverse.get(key, key) for key in row}) != len(row):
        raise ValueError("normalization maps multiple columns to one canonical field")
    normalized = {
        reverse.get(key, key): (None if value == "" else value)
        for key, value in row.items()
    }
    identity = normalized.pop("snapshot_id", None)
    if not isinstance(identity, str) or identity not in manifest.snapshots:
        raise ValueError("row snapshot identity is absent from manifest")
    for field in ("quality_flags",):
        field_value = normalized.get(field)
        if isinstance(field_value, str):
            normalized[field] = json.loads(field_value)
    iv, delta = normalized.get("iv"), normalized.get("delta")
    if manifest.iv_unit == "percent_annualized" and iv is not None:
        normalized["iv"] = float(iv) / 100
    if manifest.delta_unit == "signed_percent" and delta is not None:
        normalized["delta"] = float(delta) / 100
    normalized["delta_units"] = (
        "unknown" if manifest.delta_unit == "unknown" else "signed_unit_v1"
    )
    normalized["greek_units"] = manifest.gamma_unit
    return identity, OptionContract.model_validate(normalized)


def _snapshot(
    identity: str,
    contracts: list[OptionContract],
    manifest: OptionHistoryImportManifest,
) -> OptionChainSnapshot:
    metadata = manifest.snapshots[identity] | {
        "provider": manifest.provider,
        "feed": manifest.feed,
        "normalization_version": manifest.normalization_version,
        "dataset_revision": manifest.dataset_revision,
        "time_evidence": "historical_verified",
        "availability_evidence": manifest.availability_evidence,
        "license_reference": manifest.license_reference,
        "contracts": contracts,
    }
    return OptionChainSnapshot.model_validate(metadata)


def import_option_history(
    source: Path,
    manifest: OptionHistoryImportManifest,
    store: OptionSnapshotStore,
) -> dict[str, Any]:
    """Input must be grouped by snapshot_id; validate the entire file before publication.

    Each snapshot is atomic. A failed publish can be retried using the same immutable
    import artifact; the artifact never changes a batch's historical availability.
    """
    manifest = OptionHistoryImportManifest.model_validate(manifest.model_dump())
    source_hash = _sha256(source)
    manifest_bytes = json.dumps(
        manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    import_id = (
        "history-"
        + hashlib.sha256((source_hash + manifest_hash).encode()).hexdigest()[:32]
    )
    imports = store.root / "_history-imports"
    imports.mkdir(parents=True, exist_ok=True)
    target = imports / import_id
    with FileLock(str(imports / ".import.lock"), timeout=10):
        if not target.exists():
            with TemporaryDirectory(prefix=".staging-", dir=imports) as temporary:
                stage = Path(temporary)
                completed: set[str] = set()
                active: str | None = None
                contracts: list[OptionContract] = []
                contract_count = 0

                def write_snapshot(identity: str, rows: list[OptionContract]) -> None:
                    snapshot = _snapshot(identity, rows, manifest)
                    _atomic_json(
                        stage / f"{identity}.json", snapshot.model_dump(mode="json")
                    )
                    pq.write_table(
                        pa.Table.from_pylist(
                            [
                                r.model_dump(mode="json") | {"snapshot_id": identity}
                                for r in rows
                            ]
                        ),
                        stage / f"{identity}.parquet",
                        compression="zstd",
                    )

                for input_row in _input_rows(source, manifest.encoding):
                    identity, contract = _normalize(input_row, manifest)
                    if active is not None and identity != active:
                        write_snapshot(active, contracts)
                        completed.add(active)
                        contracts = []
                    if identity in completed:
                        raise ValueError(
                            "historical input must group contiguous snapshot identities"
                        )
                    active = identity
                    contracts.append(contract)
                    contract_count += 1
                if active is None:
                    raise ValueError("historical input has no rows")
                write_snapshot(active, contracts)
                completed.add(active)
                if completed != set(manifest.snapshots):
                    raise ValueError("manifest snapshot missing from input")
                if _sha256(source) != source_hash:
                    raise ValueError("historical source changed during import")
                report: dict[str, Any] = {
                    "import_id": import_id,
                    "source_sha256": source_hash,
                    "manifest_sha256": manifest_hash,
                    "snapshots": len(completed),
                    "contracts": contract_count,
                    "published": False,
                    "promoted": False,
                    "snapshot_ids": sorted(completed),
                    "normalized_sha256": {
                        path.name: _sha256(path) for path in sorted(stage.iterdir())
                    },
                }
                _atomic_json(
                    stage / "manifest.json",
                    {"manifest": manifest.model_dump(mode="json"), "report": report},
                )
                os.replace(stage, target)
        artifact = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        report = artifact["report"]
        if (
            report["source_sha256"] != source_hash
            or report["manifest_sha256"] != manifest_hash
        ):
            raise ValueError("historical import artifact conflict")
        if report["snapshot_ids"] != sorted(manifest.snapshots):
            raise ValueError("historical import snapshot identities conflict")
        for identity in sorted(manifest.snapshots):
            for suffix in ("json", "parquet"):
                name = f"{identity}.{suffix}"
                if _sha256(target / name) != report["normalized_sha256"].get(name):
                    raise ValueError("historical normalized artifact hash mismatch")

        def saved_snapshots() -> Iterator[OptionChainSnapshot]:
            for identity in sorted(manifest.snapshots):
                yield OptionChainSnapshot.model_validate_json(
                    (target / f"{identity}.json").read_text(encoding="utf-8")
                )

        for snapshot in saved_snapshots():
            existing = store.load(snapshot.symbol, snapshot.snapshot_id)
            if existing is not None and existing != snapshot:
                raise ValueError("option snapshot conflict")
        for snapshot in saved_snapshots():
            store.save(
                snapshot,
                {
                    "history_import_id": import_id,
                    "source_sha256": source_hash,
                    "manifest_sha256": manifest_hash,
                    "license_reference": manifest.license_reference,
                    "availability_evidence": list(manifest.availability_evidence),
                },
            )
        return dict(report)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import licensed, timestamp-evidenced canonical option history"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("var/options"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = import_option_history(
        args.source,
        OptionHistoryImportManifest.model_validate_json(
            args.manifest.read_text(encoding="utf-8")
        ),
        OptionSnapshotStore(args.root),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
