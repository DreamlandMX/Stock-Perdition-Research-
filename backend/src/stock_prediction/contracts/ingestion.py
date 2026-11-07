"""Persistent ingestion, batch-manifest, and quality-report contracts."""

from datetime import date
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from stock_prediction.contracts.market_data import Identifier, Timeframe, UsSymbol, VersionId
from stock_prediction.contracts.time import UtcDatetime

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
DatasetName = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]


class BatchCommitState(StrEnum):
    STAGED = "staged"
    COMMITTED = "committed"
    QUARANTINED = "quarantined"


class IngestionPriority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IngestionStatus(StrEnum):
    PLANNED = "planned"
    CAPABILITY_CHECK = "capability_check"
    FETCHING = "fetching"
    RETRYING = "retrying"
    STAGED = "staged"
    COMMITTED = "committed"
    BLOCKED = "blocked"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class QualitySeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class RawBatchManifest(BaseModel):
    """Integrity evidence for one immutable Parquet batch."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    batch_id: Identifier
    task_id: Identifier
    dataset: DatasetName
    market: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,8}$")]
    symbol: UsSymbol | None
    timeframe: Timeframe | None
    schema_version: VersionId
    dataset_revision: VersionId
    request_start: date
    request_end: date
    request_fingerprint: Sha256
    row_count: int = Field(ge=1)
    min_event_at: UtcDatetime
    max_event_at: UtcDatetime
    sha256: Sha256
    quality_report_sha256: Sha256 | None = None
    relative_data_path: str
    opend_version: VersionId
    sdk_version: VersionId
    created_at: UtcDatetime
    commit_state: BatchCommitState
    committed_at: UtcDatetime | None = None

    @field_validator("relative_data_path")
    @classmethod
    def validate_relative_data_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or ":" in value
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.name != "data.parquet"
        ):
            raise ValueError("relative_data_path must be a safe relative Parquet path")
        return value

    @model_validator(mode="after")
    def validate_manifest_evidence(self) -> "RawBatchManifest":
        if self.request_start > self.request_end:
            raise ValueError("request_start must not be after request_end")
        if self.min_event_at > self.max_event_at:
            raise ValueError("min_event_at must not be after max_event_at")
        if self.commit_state is BatchCommitState.COMMITTED:
            if self.committed_at is None:
                raise ValueError("committed_at is required for a committed manifest")
            if self.committed_at < self.created_at:
                raise ValueError("committed_at must not be before created_at")
        elif self.committed_at is not None:
            raise ValueError("committed_at is only valid for a committed manifest")
        if self.commit_state is BatchCommitState.STAGED:
            if self.quality_report_sha256 is not None:
                raise ValueError("staged manifest cannot bind final quality evidence")
        elif self.quality_report_sha256 is None:
            raise ValueError("finalized manifest requires quality_report_sha256")
        return self


class IngestionTask(BaseModel):
    """Durable state for one bounded ingestion request."""

    model_config = ConfigDict(strict=True, frozen=True)

    task_id: Identifier
    dataset: DatasetName
    market: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,8}$")]
    symbol: UsSymbol | None
    timeframe: Timeframe | None
    requested_start: date
    requested_end: date
    priority: IngestionPriority
    attempt: int = Field(ge=0)
    state_version: int = Field(default=0, ge=0)
    status: IngestionStatus
    error_class: Identifier | None = None
    next_retry_at: UtcDatetime | None = None
    committed_batch_id: Identifier | None = None
    committed_watermark: UtcDatetime | None = None
    staged_batch_id: Identifier | None = None
    resume_page_token: str | None = Field(default=None, max_length=4096)
    lease_owner: Identifier | None = None
    lease_expires_at: UtcDatetime | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @model_validator(mode="after")
    def validate_state_evidence(self) -> "IngestionTask":
        if self.requested_start > self.requested_end:
            raise ValueError("requested_start must not be after requested_end")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be before created_at")
        if self.status is IngestionStatus.RETRYING and (
            self.error_class is None or self.next_retry_at is None
        ):
            raise ValueError("retrying requires error_class and next_retry_at")
        if self.status in {
            IngestionStatus.BLOCKED,
            IngestionStatus.FAILED,
            IngestionStatus.QUARANTINED,
        } and self.error_class is None:
            raise ValueError(f"{self.status.value} requires error_class")
        if self.status is IngestionStatus.COMMITTED and (
            self.committed_batch_id is None or self.committed_watermark is None
        ):
            raise ValueError("committed requires batch ID and watermark")
        if self.status is IngestionStatus.STAGED and self.staged_batch_id is None:
            raise ValueError("staged requires staged_batch_id")
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("lease_owner and lease_expires_at are atomic")
        return self


class QualityIssue(BaseModel):
    """Stable, serializable quality finding."""

    model_config = ConfigDict(strict=True, frozen=True)

    code: Annotated[
        str,
        StringConstraints(
            min_length=1,
            max_length=64,
            pattern=r"^[a-z][a-z0-9_]*$",
        ),
    ]
    severity: QualitySeverity
    message: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    affected_rows: int = Field(ge=0)


class DataQualityReport(BaseModel):
    """Reconciled quality decision for a staged batch."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    report_id: Identifier
    batch_id: Identifier
    quality_rule_version: VersionId = "market-quality-v1"
    session_rule_version: VersionId | None = None
    checked_at: UtcDatetime
    input_rows: int = Field(ge=0)
    valid_rows: int = Field(ge=0)
    quarantined_rows: int = Field(ge=0)
    quality_score: float = Field(ge=0, le=1)
    quarantined: bool
    issues: list[QualityIssue]

    @model_validator(mode="after")
    def validate_reconciliation(self) -> "DataQualityReport":
        if self.valid_rows + self.quarantined_rows != self.input_rows:
            raise ValueError("input_rows must equal valid_rows + quarantined_rows")
        has_error = any(issue.severity is QualitySeverity.ERROR for issue in self.issues)
        if self.quarantined != has_error:
            raise ValueError("quarantined must match the presence of hard errors")
        return self
