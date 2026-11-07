"""Immutable, content-addressed research snapshot manifests."""

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path, PurePath
from types import MappingProxyType
from typing import Annotated, Final, Literal, Mapping, Self

import pyarrow as pa
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)
from pydantic_core import to_jsonable_python

from stock_prediction.contracts.market_data import Identifier, VersionId
from stock_prediction.contracts.research import Sha256
from stock_prediction.contracts.serving import (
    PredictionReadiness,
    ExecutionReadiness,
    ResearchReadiness,
    ResolvedSnapshot,
)

LEGACY_RESEARCH_TABLES: Final[tuple[str, ...]] = (
    "overview.parquet",
    "instruments.parquet",
    "screen.parquet",
    "forecasts.parquet",
    "history_bars.parquet",
    "history_events.parquet",
    "similar_cases.parquet",
    "backtests.parquet",
    "models.parquet",
    "data_health.parquet",
    "universes.parquet",
)

EXPECTED_RESEARCH_TABLES: Final[tuple[str, ...]] = (
    *LEGACY_RESEARCH_TABLES[:4],
    "verified_research.parquet",
    "challenger_preview.parquet",
    *LEGACY_RESEARCH_TABLES[4:],
)

RESEARCH_TABLE_SCHEMAS: Final[Mapping[str, pa.Schema]] = MappingProxyType(
    {
        "overview.parquet": pa.schema(
            [
                pa.field("horizon", pa.int8(), nullable=False),
                pa.field("payload_json", pa.string(), nullable=False),
            ]
        ),
        "verified_research.parquet": pa.schema(
            [
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("horizon", pa.int8(), nullable=False),
                pa.field("payload_json", pa.string(), nullable=False),
            ]
        ),
        "challenger_preview.parquet": pa.schema(
            [
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("horizon", pa.int8(), nullable=False),
                pa.field("payload_json", pa.string(), nullable=False),
            ]
        ),
        "instruments.parquet": pa.schema(
            [
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("name", pa.string(), nullable=False),
                pa.field("asset_type", pa.string(), nullable=False),
                pa.field("sector", pa.string(), nullable=False),
                pa.field("payload_json", pa.string(), nullable=False),
            ]
        ),
        "screen.parquet": pa.schema(
            [
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("horizon", pa.int8(), nullable=False),
                pa.field("asset_type", pa.string(), nullable=False),
                pa.field("sector", pa.string(), nullable=False),
                pa.field("regime", pa.string(), nullable=False),
                pa.field("executable_up_probability", pa.float64(), nullable=True),
                pa.field("breakout_up_probability", pa.float64(), nullable=True),
                pa.field("breakout_down_probability", pa.float64(), nullable=True),
                pa.field("executable_q50", pa.float64(), nullable=True),
                pa.field("capital_gain_q50", pa.float64(), nullable=True),
                pa.field("interval_width", pa.float64(), nullable=True),
                pa.field("similar_case_count", pa.int64(), nullable=False),
                pa.field("confidence_level", pa.string(), nullable=False),
                pa.field("data_freshness_seconds", pa.int64(), nullable=False),
                pa.field("serving_status", pa.string(), nullable=False),
                pa.field("effective_abstain", pa.bool_(), nullable=False),
                pa.field("payload_json", pa.string(), nullable=False),
            ]
        ),
        "forecasts.parquet": pa.schema(
            [
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("availability", pa.string(), nullable=False),
                pa.field("payload_json", pa.string(), nullable=False),
            ]
        ),
        "history_bars.parquet": pa.schema(
            [
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("timeframe", pa.string(), nullable=False),
                pa.field("adjustment", pa.string(), nullable=False),
                pa.field("at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("payload_json", pa.string(), nullable=False),
            ]
        ),
        "history_events.parquet": pa.schema(
            [
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("timeframe", pa.string(), nullable=False),
                pa.field("adjustment", pa.string(), nullable=False),
                pa.field("occurred_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("payload_json", pa.string(), nullable=False),
            ]
        ),
        "similar_cases.parquet": pa.schema(
            [
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("rank", pa.int64(), nullable=False),
                pa.field("payload_json", pa.string(), nullable=False),
            ]
        ),
        "backtests.parquet": pa.schema(
            [
                pa.field("model_version", pa.string(), nullable=False),
                pa.field("strategy_version", pa.string(), nullable=False),
                pa.field("universe_version", pa.string(), nullable=False),
                pa.field("split_version", pa.string(), nullable=False),
                pa.field("task", pa.string(), nullable=False),
                pa.field("horizon", pa.int8(), nullable=False),
                pa.field("asset_group", pa.string(), nullable=False),
                pa.field("evaluation_window_id", pa.string(), nullable=False),
                pa.field("cost_scenario", pa.string(), nullable=False),
                pa.field("payload_json", pa.string(), nullable=False),
            ]
        ),
        "models.parquet": pa.schema(
            [
                pa.field("task", pa.string(), nullable=False),
                pa.field("horizon", pa.int8(), nullable=False),
                pa.field("asset_group", pa.string(), nullable=False),
                pa.field("payload_json", pa.string(), nullable=False),
            ]
        ),
        "data_health.parquet": pa.schema(
            [
                pa.field("component", pa.string(), nullable=False),
                pa.field("dataset", pa.string(), nullable=True),
                pa.field("symbol", pa.string(), nullable=True),
                pa.field("serving_status", pa.string(), nullable=False),
                pa.field("payload_json", pa.string(), nullable=False),
            ]
        ),
        "universes.parquet": pa.schema(
            [
                pa.field("universe_version", pa.string(), nullable=False),
                pa.field(
                    "effective_from", pa.timestamp("us", tz="UTC"), nullable=False
                ),
                pa.field("payload_json", pa.string(), nullable=False),
            ]
        ),
    }
)

_WINDOWS_RESERVED_BASENAMES: Final[frozenset[str]] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)
_WINDOWS_INVALID_CHARACTERS: Final[frozenset[str]] = frozenset('<>:"/\\|?*')


def validate_snapshot_id(value: str) -> str:
    """Reject snapshot identities that alias or fail on supported Windows hosts."""

    if value.lower().endswith(".staging"):
        raise ValueError("snapshot ID must not use the reserved .staging suffix")
    if value.endswith((".", " ")):
        raise ValueError("snapshot ID must not end with a dot or space")
    if any(character in _WINDOWS_INVALID_CHARACTERS for character in value) or any(
        ord(character) < 32 for character in value
    ):
        raise ValueError("snapshot ID contains a Windows-invalid character")
    basename = value.split(".", maxsplit=1)[0].upper()
    if basename in _WINDOWS_RESERVED_BASENAMES:
        raise ValueError("snapshot ID uses a reserved Windows device name")
    return value


SnapshotId = Annotated[Identifier, AfterValidator(validate_snapshot_id)]


def _require_utc(value: object) -> datetime:
    parsed = value
    if isinstance(parsed, str):
        try:
            parsed = datetime.fromisoformat(parsed.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(
                "manifest timestamps must be valid UTC datetimes"
            ) from error
    if not isinstance(parsed, datetime):
        raise ValueError("manifest timestamps must be UTC datetimes")
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None:
        raise ValueError("manifest timestamps must be UTC datetimes")
    if offset.total_seconds() != 0:
        raise ValueError("manifest timestamps must use UTC")
    return parsed.astimezone(UTC)


StrictUtcDatetime = Annotated[datetime, BeforeValidator(_require_utc)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class SnapshotTable(_StrictModel):
    """Integrity evidence for one exact Parquet file."""

    path: str
    schema_hash: Sha256
    content_hash: Sha256
    row_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        path = PurePath(self.path)
        if (
            path.is_absolute()
            or len(path.parts) != 1
            or path.name != self.path
            or self.path in {".", ".."}
            or "/" in self.path
            or "\\" in self.path
            or ":" in self.path
        ):
            raise ValueError("snapshot table path must be a safe relative filename")
        return self


class ResearchSnapshotManifest(_StrictModel):
    """Frozen publication record for one immutable research snapshot."""

    snapshot_id: SnapshotId
    revision_no: int = Field(ge=1)
    as_of: StrictUtcDatetime
    dataset_revision: VersionId
    feature_set_version: VersionId
    universe_version: VersionId
    prediction_readiness: PredictionReadiness
    activation_basis: Literal["manual_override"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    execution_readiness: ExecutionReadiness = Field(
        default=ExecutionReadiness.BLOCKED,
        exclude_if=lambda value: value is ExecutionReadiness.BLOCKED,
    )
    model_version: VersionId | None
    calibration_version: VersionId | None
    generated_at: StrictUtcDatetime
    status: Literal["published"] = "published"
    tables: tuple[SnapshotTable, ...]
    manifest_hash: Sha256

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        versions_available = (
            self.model_version is not None,
            self.calibration_version is not None,
        )
        if versions_available[0] != versions_available[1]:
            raise ValueError("model and calibration versions must be atomic")
        if self.prediction_readiness is PredictionReadiness.DATA_ONLY:
            if self.activation_basis is not None:
                raise ValueError("manual activation requires available model forecasts")
            if any(versions_available):
                raise ValueError("data-only manifests cannot name model versions")
        elif not all(versions_available):
            raise ValueError("forecast-ready manifests require model versions")
        paths = tuple(table.path for table in self.tables)
        if len(set(paths)) != len(paths):
            raise ValueError("snapshot table paths must be unique")
        if frozenset(paths) not in {
            frozenset(LEGACY_RESEARCH_TABLES),
            frozenset(EXPECTED_RESEARCH_TABLES),
        }:
            raise ValueError("manifest must contain all required research tables")
        for table in self.tables:
            expected_schema_hash = parquet_schema_hash(
                RESEARCH_TABLE_SCHEMAS[table.path]
            )
            if table.schema_hash != expected_schema_hash:
                raise ValueError(
                    f"{table.path} schema hash does not match the authoritative schema"
                )
        if self.generated_at < self.as_of:
            raise ValueError("manifest generated_at must not precede as_of")
        if self.manifest_hash != manifest_content_hash(
            self.model_dump(mode="python", exclude={"manifest_hash"})
        ):
            raise ValueError("manifest hash is invalid")
        return self

    @classmethod
    def create(
        cls,
        *,
        snapshot: ResolvedSnapshot,
        generated_at: datetime,
        tables: tuple[SnapshotTable, ...],
    ) -> "ResearchSnapshotManifest":
        payload: dict[str, object] = {
            **snapshot.model_dump(
                mode="python",
                exclude={"research_readiness"},
            ),
            "generated_at": generated_at,
            "status": "published",
            "tables": tables,
        }
        payload["manifest_hash"] = manifest_content_hash(payload)
        return cls.model_validate(payload, strict=True)

    def verify_manifest_hash(self) -> None:
        expected = manifest_content_hash(
            self.model_dump(mode="python", exclude={"manifest_hash"})
        )
        if expected != self.manifest_hash:
            raise ValueError("manifest hash is invalid")

    def resolved_snapshot(self) -> ResolvedSnapshot:
        table_rows = {table.path: table.row_count for table in self.tables}
        verified_count = table_rows.get("verified_research.parquet", 0)
        return ResolvedSnapshot(
            snapshot_id=self.snapshot_id,
            revision_no=self.revision_no,
            as_of=self.as_of,
            dataset_revision=self.dataset_revision,
            feature_set_version=self.feature_set_version,
            universe_version=self.universe_version,
            prediction_readiness=self.prediction_readiness,
            execution_readiness=self.execution_readiness,
            activation_basis=self.activation_basis,
            research_readiness=(
                ResearchReadiness.MANUAL_UNVERIFIED
                if self.activation_basis == "manual_override"
                else ResearchReadiness.FULL_VERIFIED
                if self.prediction_readiness is PredictionReadiness.FORECAST_READY
                else ResearchReadiness.PARTIAL_VERIFIED
                if verified_count > 0
                else ResearchReadiness.RULES_ONLY
            ),
            model_version=self.model_version,
            calibration_version=self.calibration_version,
        )

    def table(self, path: str) -> SnapshotTable:
        for table in self.tables:
            if table.path == path:
                return table
        raise KeyError(path)


def manifest_content_hash(value: object) -> str:
    """Hash a JSON value using the platform's canonical JSON encoding."""

    return hashlib.sha256(
        json.dumps(
            to_jsonable_python(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def parquet_schema_hash(schema: pa.Schema) -> str:
    """Hash the exact serialized Arrow schema associated with Parquet bytes."""

    return hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()


def resolve_table_path(snapshot_directory: Path, relative_path: str) -> Path:
    """Resolve a manifest table while enforcing the snapshot directory boundary."""

    directory = snapshot_directory.resolve()
    candidate = (directory / relative_path).resolve()
    try:
        candidate.relative_to(directory)
    except ValueError as error:
        raise ValueError(
            "snapshot table path resolves outside snapshot directory"
        ) from error
    if candidate.parent != directory:
        raise ValueError("snapshot table path resolves outside snapshot directory")
    return candidate


def resolve_repository_path(root: Path, candidate: Path) -> Path:
    """Resolve an entry and prove it stays beneath the configured repository root."""

    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("repository entry resolves outside repository root") from error
    return resolved_candidate


__all__ = [
    "EXPECTED_RESEARCH_TABLES",
    "RESEARCH_TABLE_SCHEMAS",
    "ResearchSnapshotManifest",
    "SnapshotId",
    "SnapshotTable",
    "manifest_content_hash",
    "parquet_schema_hash",
    "resolve_table_path",
    "resolve_repository_path",
    "validate_snapshot_id",
]
