import csv
import json
from datetime import timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stock_prediction.options.history import (
    OptionHistoryImportManifest,
    import_option_history,
)
from stock_prediction.options.store import OptionSnapshotStore
from test_analytics import NOW, chain


def inputs(tmp_path, suffix="csv", **updates):
    snapshot = chain()
    metadata = snapshot.model_dump(mode="json")
    metadata.pop("contracts")
    metadata.update(
        time_evidence="historical_verified",
        captured_at=(NOW + timedelta(days=2)).isoformat(),
    )
    manifest = OptionHistoryImportManifest(
        provider="licensed_vendor",
        feed="eod",
        normalization_version="canonical-options-v2",
        dataset_revision="history-test-v1",
        license_reference="license/test",
        availability_evidence=("delivery/test.csv",),
        iv_unit="annualized_decimal",
        delta_unit="signed_unit_v1",
        gamma_unit="per_usd_v1",
        snapshots={snapshot.snapshot_id: metadata},
        **updates,
    )
    rows = [
        r.model_dump(mode="json") | {"snapshot_id": snapshot.snapshot_id}
        for r in snapshot.contracts
    ]
    for row in rows:
        row["ingested_at"] = (NOW + timedelta(days=2)).isoformat()
        row.pop("quality_flags")
    path = tmp_path / f"input.{suffix}"
    if suffix == "csv":
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    else:
        pq.write_table(pa.Table.from_pylist(rows), path)
    return path, manifest


@pytest.mark.parametrize("suffix", ["csv", "parquet"])
def test_import_preserves_historical_times_provenance_and_is_idempotent(
    tmp_path, suffix
):
    source, manifest = inputs(tmp_path, suffix)
    store = OptionSnapshotStore(tmp_path / "store")
    report = import_option_history(source, manifest, store)
    snapshot = store.load("US.AAPL", as_of=NOW)
    assert snapshot is not None and snapshot.time_evidence == "historical_verified"
    assert snapshot.market_as_of == NOW and snapshot.source_available_at == NOW
    assert snapshot.contracts[0].multiplier == 100
    assert snapshot.provider == "licensed_vendor"
    assert report["snapshots"] == 1 and report["contracts"] == 8
    assert report["source_sha256"] and report["manifest_sha256"]
    assert import_option_history(source, manifest, store) == report
    artifact = store.root / "_history-imports" / report["import_id"]
    assert len(list(artifact.glob("*.parquet"))) == 1
    assert (
        json.loads((artifact / "manifest.json").read_text())["manifest"][
            "license_reference"
        ]
        == "license/test"
    )


def test_invalid_input_does_not_publish(tmp_path):
    source, manifest = inputs(tmp_path)
    content = source.read_text()
    source.write_text(content.replace("0.2", "nan"), encoding="utf-8")
    store = OptionSnapshotStore(tmp_path / "store")
    with pytest.raises(ValueError):
        import_option_history(source, manifest, store)
    assert store.load("US.AAPL") is None


def test_invalid_second_batch_does_not_publish_valid_first_batch(tmp_path):
    source, manifest = inputs(tmp_path)
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        rows = list(reader)
    bad_rows = [row | {"snapshot_id": "second", "iv": "nan"} for row in rows]
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows([*rows, *bad_rows])
    second = manifest.snapshots["current"] | {
        "snapshot_id": "second",
        "raw_batch_id": "second",
    }
    manifest = manifest.model_copy(
        update={"snapshots": manifest.snapshots | {"second": second}}
    )
    store = OptionSnapshotStore(tmp_path / "store")
    with pytest.raises(ValueError):
        import_option_history(source, manifest, store)
    assert store.load("US.AAPL") is None


def test_cache_tampering_and_field_mapping_collisions_are_rejected(tmp_path):
    source, manifest = inputs(tmp_path)
    store = OptionSnapshotStore(tmp_path / "store")
    report = import_option_history(source, manifest, store)
    cache = store.root / "_history-imports" / report["import_id"] / "current.parquet"
    cache.write_bytes(b"modified")
    with pytest.raises(ValueError, match="hash mismatch"):
        import_option_history(source, manifest, store)
    content = source.read_text()
    source.write_text(
        content.replace("iv,", "iv,other_iv,").replace(",0.2,", ",0.2,0.2,"),
        encoding="utf-8",
    )
    collision = manifest.model_copy(update={"field_map": {"iv": "other_iv"}})
    with pytest.raises(ValueError, match="multiple columns"):
        import_option_history(source, collision, store)


def test_manifest_cannot_supply_fake_dates_or_units(tmp_path):
    source, manifest = inputs(tmp_path)
    metadata = manifest.snapshots["current"].copy()
    metadata.pop("market_as_of")
    bad = manifest.model_copy(update={"snapshots": {"current": metadata}})
    with pytest.raises(ValueError, match="market-date evidence"):
        import_option_history(source, bad, OptionSnapshotStore(tmp_path / "store"))
    with pytest.raises(ValueError):
        OptionHistoryImportManifest.model_validate(
            manifest.model_dump() | {"license_reference": ""}
        )


def test_units_only_convert_when_manifest_explicit_and_missing_multiplier_stays_missing(
    tmp_path,
):
    source, manifest = inputs(tmp_path)
    source.write_text(
        source.read_text()
        .replace(",0.2,0.5,", ",20,50,")
        .replace(",100.0,True,", ",,True,"),
        encoding="utf-8",
    )
    manifest = manifest.model_copy(
        update={"iv_unit": "percent_annualized", "delta_unit": "signed_percent"}
    )
    store = OptionSnapshotStore(tmp_path / "store")
    import_option_history(source, manifest, store)
    snapshot = store.load("US.AAPL")
    assert snapshot.contracts[0].iv == 0.2
    assert snapshot.contracts[0].delta == 0.5
    assert snapshot.contracts[0].multiplier is None
