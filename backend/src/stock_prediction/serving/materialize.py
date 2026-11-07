"""Atomic publication of immutable, already-computed research artifacts."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import shutil
from typing import Final, Mapping, TypeVar

from filelock import FileLock
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, TypeAdapter, ValidationError

from stock_prediction.contracts.serving import (
    ForecastUnavailable,
    ModelTaskMatrixRow,
    OverviewReadModel,
    PredictionReadiness,
    ResearchEvidenceScope,
    ResearchModelEvidence,
    ResolvedSnapshot,
    ScreenRow,
)
from stock_prediction.modeling.confidence import RejectionReason
from stock_prediction.serving.manifest import (
    EXPECTED_RESEARCH_TABLES,
    LEGACY_RESEARCH_TABLES,
    RESEARCH_TABLE_SCHEMAS,
    ResearchSnapshotManifest,
    SnapshotId,
    SnapshotTable,
    parquet_schema_hash,
)
from stock_prediction.serving.repository import SnapshotVersionConflict

NO_CHAMPION_REASON: Final = "no_champion_candidate"
ModelT = TypeVar("ModelT", bound=BaseModel)


class MaterializationError(RuntimeError):
    """A snapshot could not be safely materialized."""


@dataclass(frozen=True, slots=True)
class MaterializationResult:
    published_path: Path | None
    reason: str | None
    manifest: ResearchSnapshotManifest | None


def materialize_research_snapshot(
    root: Path,
    *,
    snapshot: ResolvedSnapshot,
    generated_at: datetime,
    tables: Mapping[str, pa.Table],
    champion_exists: bool,
) -> MaterializationResult:
    """Write validated Parquet artifacts to staging and publish by one rename."""

    try:
        TypeAdapter(SnapshotId).validate_python(snapshot.snapshot_id, strict=True)
    except ValidationError as error:
        raise MaterializationError("snapshot ID is not Windows-safe") from error
    _validate_tables(tables)
    verified_rows = (
        tuple(
            _validate_json_payload(ResearchModelEvidence, payload)
            for payload in tables["verified_research.parquet"]
            .column("payload_json")
            .to_pylist()
        )
        if "verified_research.parquet" in tables
        else ()
    )
    challenger_rows = (
        tuple(
            _validate_json_payload(ResearchModelEvidence, payload)
            for payload in tables["challenger_preview.parquet"]
            .column("payload_json")
            .to_pylist()
        )
        if "challenger_preview.parquet" in tables
        else ()
    )
    forecast_ready = snapshot.prediction_readiness is PredictionReadiness.FORECAST_READY
    if champion_exists != forecast_ready:
        raise MaterializationError(
            "champion availability must agree with snapshot prediction readiness"
        )
    if any(row.scope is not ResearchEvidenceScope.VERIFIED for row in verified_rows):
        raise MaterializationError(
            "verified research table cannot contain challenger evidence"
        )
    if any(
        row.scope is not ResearchEvidenceScope.CHALLENGER for row in challenger_rows
    ):
        raise MaterializationError(
            "challenger preview table cannot contain verified evidence"
        )
    if not forecast_ready:
        _validate_data_only_tables(tables)
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    final = root / snapshot.snapshot_id
    staging = root / f"{snapshot.snapshot_id}.staging"
    lock = FileLock(str(root / ".research-snapshot.lock"))
    with lock:
        if os.path.lexists(final):
            raise SnapshotVersionConflict(
                f"snapshot {snapshot.snapshot_id} is already published"
            )
        if os.path.lexists(staging):
            raise MaterializationError("snapshot staging directory already exists")
        staging.mkdir()
        try:
            manifest_tables: list[SnapshotTable] = []
            publication_tables = (
                EXPECTED_RESEARCH_TABLES
                if "verified_research.parquet" in tables
                else LEGACY_RESEARCH_TABLES
            )
            for path in publication_tables:
                destination = staging / path
                pq.write_table(tables[path], destination)
                content = destination.read_bytes()
                persisted = pq.read_table(pa.BufferReader(content))
                manifest_tables.append(
                    SnapshotTable(
                        path=path,
                        schema_hash=parquet_schema_hash(RESEARCH_TABLE_SCHEMAS[path]),
                        content_hash=hashlib.sha256(content).hexdigest(),
                        row_count=persisted.num_rows,
                    )
                )
            manifest = ResearchSnapshotManifest.create(
                snapshot=snapshot,
                generated_at=generated_at,
                tables=tuple(manifest_tables),
            )
            _verify_staged_tables(staging, manifest)
            staging.joinpath("manifest.json").write_bytes(
                manifest.model_dump_json().encode("utf-8")
            )
            os.replace(staging, final)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
    return MaterializationResult(
        published_path=final,
        reason=None,
        manifest=manifest,
    )


def _validate_tables(tables: Mapping[str, pa.Table]) -> None:
    paths = frozenset(tables)
    if paths not in {
        frozenset(LEGACY_RESEARCH_TABLES),
        frozenset(EXPECTED_RESEARCH_TABLES),
    }:
        raise MaterializationError(
            "materialization requires all required research tables"
        )
    for path in paths:
        expected_schema = RESEARCH_TABLE_SCHEMAS[path]
        table = tables[path]
        if not table.schema.equals(expected_schema, check_metadata=True):
            raise MaterializationError(
                f"{path} schema must exactly match the authoritative Arrow schema"
            )
        for field in expected_schema:
            if not field.nullable and table.column(field.name).null_count:
                raise MaterializationError(
                    f"{path} contains null data in non-nullable column {field.name}"
                )


def _validate_data_only_tables(tables: Mapping[str, pa.Table]) -> None:
    screen_rows = tuple(
        _validate_json_payload(ScreenRow, payload)
        for payload in tables["screen.parquet"].column("payload_json").to_pylist()
    )
    forecasts = tuple(
        _validate_json_payload(ForecastUnavailable, payload)
        for payload in tables["forecasts.parquet"].column("payload_json").to_pylist()
    )
    overview_rows = tuple(
        _validate_json_payload(OverviewReadModel, payload)
        for payload in tables["overview.parquet"].column("payload_json").to_pylist()
    )
    model_rows = tuple(
        _validate_json_payload(ModelTaskMatrixRow, payload)
        for payload in tables["models.parquet"].column("payload_json").to_pylist()
    )

    instrument_symbols = set(tables["instruments.parquet"].column("symbol").to_pylist())
    if not instrument_symbols:
        raise MaterializationError("data-only snapshots require instruments")
    if (
        len(forecasts) != len(instrument_symbols)
        or {row.symbol for row in forecasts} != instrument_symbols
    ):
        raise MaterializationError(
            "data-only snapshots require one unavailable forecast per instrument"
        )
    if any(
        row.not_generated_reason != NO_CHAMPION_REASON
        or row.effective_abstain is not True
        for row in forecasts
    ):
        raise MaterializationError(
            "data-only forecasts must abstain because no champion exists"
        )

    expected_screen_keys = {
        (symbol, horizon) for symbol in instrument_symbols for horizon in (1, 5, 20)
    }
    if {(row.symbol, row.horizon) for row in screen_rows} != expected_screen_keys:
        raise MaterializationError(
            "data-only snapshots require one screen row per instrument and horizon"
        )
    if len(screen_rows) != len(expected_screen_keys) or any(
        not row.effective_abstain
        or row.model_version is not None
        or row.abstain_reasons != (RejectionReason.NO_CHAMPION_CANDIDATE,)
        for row in screen_rows
    ):
        raise MaterializationError(
            "data-only screen rows must contain only no-champion abstentions"
        )
    if any(row.top_opportunities for row in overview_rows):
        raise MaterializationError(
            "data-only overview cannot publish top opportunities"
        )
    if any(
        (
            row.gate_passed is True
            and (
                row.champion_version is None
                or row.calibration_version is None
                or row.unavailable_reason is not None
            )
        )
        or (
            row.gate_passed is not True
            and (
                row.champion_version is not None
                or row.calibration_version is not None
                or row.unavailable_reason != NO_CHAMPION_REASON
            )
        )
        for row in model_rows
    ):
        raise MaterializationError(
            "data-only model matrix has inconsistent task-level lifecycle state"
        )
    history_symbols = set(
        tables["history_bars.parquet"]
        .filter(
            pa.compute.equal(tables["history_bars.parquet"].column("timeframe"), "1d")
        )
        .column("symbol")
        .to_pylist()
    )
    if history_symbols != instrument_symbols:
        raise MaterializationError(
            "data-only snapshots require daily history for every instrument"
        )


def _validate_json_payload(model: type[ModelT], payload: str) -> ModelT:
    try:
        return TypeAdapter(model).validate_json(payload, strict=True)
    except ValidationError as error:
        raise MaterializationError(
            f"data-only snapshot contains invalid {model.__name__} payload"
        ) from error


def _verify_staged_tables(staging: Path, manifest: ResearchSnapshotManifest) -> None:
    for evidence in manifest.tables:
        path = staging / evidence.path
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != evidence.content_hash:
            raise MaterializationError(f"{evidence.path} content hash mismatch")
        table = pq.read_table(pa.BufferReader(content))
        if table.num_rows != evidence.row_count:
            raise MaterializationError(f"{evidence.path} row count mismatch")
        expected_schema = RESEARCH_TABLE_SCHEMAS[evidence.path]
        if not table.schema.equals(expected_schema, check_metadata=True):
            raise MaterializationError(f"{evidence.path} schema mismatch")
        if parquet_schema_hash(expected_schema) != evidence.schema_hash:
            raise MaterializationError(f"{evidence.path} schema hash mismatch")


__all__ = [
    "MaterializationError",
    "MaterializationResult",
    "NO_CHAMPION_REASON",
    "materialize_research_snapshot",
]
