"""Strict, immutable contracts for M6 operational evidence."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import to_jsonable_python

from stock_prediction.contracts.market_data import Identifier, VersionId
from stock_prediction.contracts.time import UtcDatetime

Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
SafeRelativePath = Annotated[str, StringConstraints(min_length=1, max_length=512)]


def canonical_hash(value: object) -> str:
    payload = json.dumps(
        to_jsonable_python(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith(("/", "\\")):
        raise ValueError("path must be relative")
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path contains an unsafe segment")
    if "\\" in value or ":" in value:
        raise ValueError("path must use safe POSIX relative syntax")
    return value


class ModelArtifact(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    relative_path: SafeRelativePath
    sha256: Sha256
    size_bytes: int = Field(ge=0)

    _validate_path = field_validator("relative_path")(_safe_relative_path)


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["model-release-v1"] = "model-release-v1"
    model_version: VersionId
    task: Identifier
    horizon: int = Field(ge=1, le=20)
    calibration_version: VersionId | None = None
    registered_at: UtcDatetime
    source_decision_id: Identifier
    promotion_eligible: bool
    artifacts: tuple[ModelArtifact, ...] = Field(min_length=1)
    manifest_hash: Sha256

    @model_validator(mode="after")
    def validate_manifest(self) -> "ReleaseManifest":
        paths = [item.relative_path for item in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("release artifact paths must be unique")
        expected = canonical_hash(
            self.model_dump(mode="json", exclude={"manifest_hash"})
        )
        if self.manifest_hash != expected:
            raise ValueError("release manifest hash is invalid")
        return self


def build_release_manifest(**values: object) -> ReleaseManifest:
    payload = {"schema_version": "model-release-v1", **values}
    payload["manifest_hash"] = canonical_hash(payload)
    return ReleaseManifest.model_validate(payload, strict=True)


class LifecycleEventType(StrEnum):
    REGISTERED = "registered"
    CHALLENGER_MARKED = "challenger_marked"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"
    DEACTIVATED = "deactivated"
    REJECTED = "rejected"


class LifecycleEvent(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["lifecycle-event-v1"] = "lifecycle-event-v1"
    event_id: Identifier
    event_type: LifecycleEventType
    task: Identifier
    horizon: int = Field(ge=1, le=20)
    from_version: VersionId | None = None
    to_version: VersionId
    reason: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    recorded_at: UtcDatetime
    previous_event_hash: Sha256 | None = None
    event_hash: Sha256

    @model_validator(mode="after")
    def validate_event_hash(self) -> "LifecycleEvent":
        expected = canonical_hash(self.model_dump(mode="json", exclude={"event_hash"}))
        if self.event_hash != expected:
            raise ValueError("lifecycle event hash is invalid")
        if (
            self.event_type
            in {
                LifecycleEventType.PROMOTED,
                LifecycleEventType.ROLLED_BACK,
            }
            and self.from_version == self.to_version
        ):
            raise ValueError("lifecycle transition must change model version")
        return self


def build_lifecycle_event(**values: object) -> LifecycleEvent:
    payload = {"schema_version": "lifecycle-event-v1", **values}
    payload["event_hash"] = canonical_hash(payload)
    return LifecycleEvent.model_validate(payload, strict=True)


class LifecycleSnapshot(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    task: Identifier
    horizon: int = Field(ge=1, le=20)
    champion_version: VersionId | None
    challenger_versions: tuple[VersionId, ...]
    prior_champion_versions: tuple[VersionId, ...]
    rejected_versions: tuple[VersionId, ...]
    revision: int = Field(ge=0)
    last_event_at: UtcDatetime | None
    ledger_head_hash: Sha256 | None


class DriftFamily(StrEnum):
    DATA = "data"
    FEATURE = "feature"
    CALIBRATION = "calibration"
    CONCEPT = "concept"
    INTERVAL = "interval"


class DriftLevel(StrEnum):
    INSUFFICIENT_SAMPLES = "insufficient_samples"
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"


class DriftMetric(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    metric_id: Identifier
    family: DriftFamily
    metric_name: Identifier
    level: DriftLevel
    value: float | None
    sample_count: int = Field(ge=0)
    minimum_sample_size: int = Field(ge=1)
    warning_threshold: float = Field(ge=0)
    critical_threshold: float = Field(ge=0)
    units: Identifier
    detail: Annotated[str, StringConstraints(min_length=1, max_length=512)]

    @model_validator(mode="after")
    def validate_sample_semantics(self) -> "DriftMetric":
        enough = self.sample_count >= self.minimum_sample_size
        if not enough and self.level is not DriftLevel.INSUFFICIENT_SAMPLES:
            raise ValueError("insufficient samples must be explicit")
        if enough and self.level is DriftLevel.INSUFFICIENT_SAMPLES:
            raise ValueError("sufficient samples cannot be marked insufficient")
        if self.critical_threshold < self.warning_threshold:
            raise ValueError("critical threshold must not be below warning threshold")
        return self


class DriftReport(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    report_id: Identifier
    as_of: UtcDatetime
    model_version: VersionId
    dataset_revision: VersionId
    metrics: tuple[DriftMetric, ...] = Field(min_length=1)
    overall_level: DriftLevel

    @model_validator(mode="after")
    def validate_overall_level(self) -> "DriftReport":
        rank = {
            DriftLevel.INSUFFICIENT_SAMPLES: 0,
            DriftLevel.HEALTHY: 1,
            DriftLevel.WARNING: 2,
            DriftLevel.CRITICAL: 3,
        }
        sufficient = [
            item.level
            for item in self.metrics
            if item.level is not DriftLevel.INSUFFICIENT_SAMPLES
        ]
        expected = (
            max(sufficient, key=rank.__getitem__)
            if sufficient
            else DriftLevel.INSUFFICIENT_SAMPLES
        )
        if self.overall_level is not expected:
            raise ValueError("overall drift level does not match metric evidence")
        return self


class BackupItem(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    relative_path: SafeRelativePath
    sha256: Sha256
    size_bytes: int = Field(ge=0)

    _validate_path = field_validator("relative_path")(_safe_relative_path)


class BackupManifest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["operations-backup-v1"] = "operations-backup-v1"
    backup_id: Identifier
    created_at: UtcDatetime
    items: tuple[BackupItem, ...] = Field(min_length=1)
    manifest_hash: Sha256

    @model_validator(mode="after")
    def validate_manifest(self) -> "BackupManifest":
        paths = [item.relative_path for item in self.items]
        if len(paths) != len(set(paths)):
            raise ValueError("backup paths must be unique")
        expected = canonical_hash(
            self.model_dump(mode="json", exclude={"manifest_hash"})
        )
        if self.manifest_hash != expected:
            raise ValueError("backup manifest hash is invalid")
        return self


def build_backup_manifest(**values: object) -> BackupManifest:
    payload = {"schema_version": "operations-backup-v1", **values}
    payload["manifest_hash"] = canonical_hash(payload)
    return BackupManifest.model_validate(payload, strict=True)


class SloStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_COMPARABLE = "not_comparable"


class SloMeasurement(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    metric_id: Identifier
    p50_ms: float = Field(ge=0)
    p95_ms: float = Field(ge=0)
    target_ms: float = Field(gt=0)
    sample_count: int = Field(ge=1)
    workload: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    status: SloStatus


class DrillStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class RecoveryDrill(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    drill_id: Identifier
    scenario: Identifier
    status: DrillStatus
    started_at: UtcDatetime
    finished_at: UtcDatetime
    evidence: Annotated[str, StringConstraints(min_length=1, max_length=1024)]

    @model_validator(mode="after")
    def validate_times(self) -> "RecoveryDrill":
        if self.finished_at < self.started_at:
            raise ValueError("drill finish cannot precede start")
        return self
