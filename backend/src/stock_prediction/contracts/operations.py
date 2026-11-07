"""Contracts for durable localhost research operations."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.contracts.text import LocalizableText


class UpdateOperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED_FORECAST_READY = "succeeded_forecast_ready"
    SUCCEEDED_DATA_ONLY = "succeeded_data_only"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    FAILED = "failed"


class UpdateOperationStage(StrEnum):
    RUNTIME_PREFLIGHT = "runtime_preflight"
    OPEND_PREFLIGHT = "opend_preflight"
    INCREMENTAL_COLLECTION = "incremental_collection"
    DATA_QUALITY = "data_quality"
    FEATURE_BUILD = "feature_build"
    MARKET_SNAPSHOT_PUBLICATION = "market_snapshot_publication"
    MODEL_TRAINING = "model_training"
    PROMOTION_GATE = "promotion_gate"
    CHAMPION_SWITCH = "champion_switch"
    BATCH_INFERENCE = "batch_inference"
    FINAL_SNAPSHOT_PUBLICATION = "final_snapshot_publication"
    DATA_REPAIR = "data_repair"
    LABEL_HEALTH = "label_health"
    CANDIDATE_SEARCH = "candidate_search"
    OOS_VALIDATION = "oos_validation"
    PROMOTION_WINDOW = "promotion_window"
    CHAMPION_PUBLICATION = "champion_publication"
    EXECUTION_GATE = "execution_gate"


class EodWorkerState(StrEnum):
    READY = "ready"
    STALE = "stale"
    OFFLINE = "offline"


class ModelTrainingReadinessState(StrEnum):
    COLLECTING = "collecting"
    READY = "ready"
    CONSUMED = "consumed"


class CandidateTrainingState(StrEnum):
    READY = "ready"
    RUNNING = "running"
    DEVELOPMENT_FAILED = "development_failed"
    FROZEN = "frozen"


class CandidateTrainingAction(StrEnum):
    AVAILABLE = "available"
    ACTIVE = "active"
    EXHAUSTED = "exhausted"
    FROZEN = "frozen"


class SubmissionDisposition(StrEnum):
    CREATED = "created"
    REUSED_ACTIVE = "reused_active"
    REUSED_COMPLETED = "reused_completed"


ModelCapabilityBundle = Literal["direction", "range_exit_20"]
TrainingTrigger = Literal["manual", "scheduled", "drift"]
BlindEvidenceScope = Literal[
    "optimization_reused",
    "shadow_forward",
    "blind_complete",
]


class CandidateTrainingRequest(BaseModel):
    """Optional explicit candidate recipe requested by a newer client."""

    model_config = ConfigDict(strict=True, extra="forbid")

    candidate_recipe_version: str | None = None
    bundles: tuple[ModelCapabilityBundle, ...] | None = None
    trigger: TrainingTrigger = "manual"

    @field_validator("bundles", mode="before")
    @classmethod
    def validate_unique_bundles(
        cls,
        value: object,
    ) -> tuple[ModelCapabilityBundle, ...] | None:
        if isinstance(value, list):
            value = tuple(value)
        if value is not None and not isinstance(value, tuple):
            raise ValueError("requested capability bundles must be a sequence")
        if value is not None and len(set(value)) != len(value):
            raise ValueError("requested capability bundles must be unique")
        return cast(tuple[ModelCapabilityBundle, ...] | None, value)


class ModelPromotionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    training_run_id: str = Field(min_length=1)
    bundle_id: ModelCapabilityBundle
    expected_package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_incumbent_id: str | None
    approved_by: str = Field(default="local_user", min_length=1, max_length=128)


class CapabilityReadiness(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    bundle_id: ModelCapabilityBundle
    development_state: str = Field(min_length=1)
    shadow_state: str = Field(min_length=1)
    blind_state: str = Field(min_length=1)
    promotion_state: str = Field(min_length=1)
    passed_tasks: int = Field(ge=0)
    total_tasks: int = Field(gt=0)
    block_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_counts(self) -> CapabilityReadiness:
        if self.passed_tasks > self.total_tasks:
            raise ValueError("passed tasks cannot exceed total tasks")
        if len(set(self.block_reasons)) != len(self.block_reasons):
            raise ValueError("capability block reasons must be unique")
        return self


class ModelTrainingReadiness(BaseModel):
    """Independent candidate-development and one-time promotion readiness."""

    model_config = ConfigDict(strict=True)

    state: ModelTrainingReadinessState
    window_id: str = Field(min_length=1)
    window_start: date
    window_end: date
    label_complete_through: date
    automatic_training_at: UtcDatetime
    data_cutoff: date | None = None
    anchor_sessions_collected: int = Field(ge=0)
    required_anchor_sessions: int = Field(gt=0)
    fully_labeled_sessions: int = Field(ge=0)
    required_fully_labeled_sessions: int = Field(gt=0)
    label_horizon: int = Field(gt=0)
    candidate_training_state: CandidateTrainingState = CandidateTrainingState.READY
    candidate_data_cutoff: date = date(2026, 6, 29)
    candidate_passed_tasks: int = Field(default=0, ge=0, le=17)
    candidate_total_tasks: int = Field(default=17, gt=0)
    frozen_candidate_version: str | None = None
    frozen_candidate_at: UtcDatetime | None = None
    can_submit_candidate_training: bool = True
    latest_candidate_recipe_version: str | None = None
    next_candidate_recipe_version: str | None = None
    candidate_training_action: CandidateTrainingAction = (
        CandidateTrainingAction.AVAILABLE
    )
    candidate_training_block_reason: str | None = None
    can_submit_promotion: bool = False
    can_submit: bool
    detail: str = Field(min_length=1)
    capability_readiness: tuple[CapabilityReadiness, ...] | None = None

    @field_validator("capability_readiness")
    @classmethod
    def validate_unique_capabilities(
        cls,
        value: tuple[CapabilityReadiness, ...] | None,
    ) -> tuple[CapabilityReadiness, ...] | None:
        if value is None:
            return None
        bundle_ids = tuple(item.bundle_id for item in value)
        if len(set(bundle_ids)) != len(bundle_ids):
            raise ValueError("capability readiness bundles must be unique")
        return value


class EodWorkerStatus(BaseModel):
    """Health of the process that claims and executes queued EOD operations."""

    model_config = ConfigDict(strict=True)

    state: EodWorkerState
    last_heartbeat: UtcDatetime | None = None
    stale_after_seconds: int = Field(default=10, ge=1)
    detail: str = Field(min_length=1)


class OperationPhase(BaseModel):
    model_config = ConfigDict(strict=True)

    stage: UpdateOperationStage
    status: str
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    detail: str | None = None


class UpdateOperation(BaseModel):
    """Persisted state returned by all EOD operation endpoints."""

    model_config = ConfigDict(strict=True)

    operation_id: str = Field(min_length=1)
    operation_type: str = "eod_update"
    status: UpdateOperationStatus
    current_stage: UpdateOperationStage | None = None
    progress_percent: int = Field(ge=0, le=100)
    target_session: date | None = None
    actual_cutoff: date | None = None
    previous_cutoff: date | None = None
    session_advanced: bool | None = None
    retry_attempt: int = Field(default=0, ge=0)
    next_retry_at: UtcDatetime | None = None
    retry_deadline_at: UtcDatetime | None = None
    snapshot_id: str | None = None
    data_hash: str | None = None
    model_version: str | None = None
    automatic_promotion: str | None = None
    error_code: str | None = None
    diagnostic_detail: str | None = None
    retry_action: str | None = None
    retry_recommendation: str | None = None
    retry_recommendation_ref: LocalizableText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    no_change: bool = False
    candidate_recipe_version: str | None = None
    candidate_generation: int | None = Field(default=None, ge=1)
    submission_disposition: SubmissionDisposition | None = None
    requested_bundles: tuple[ModelCapabilityBundle, ...] | None = None
    training_trigger: TrainingTrigger | None = None
    completed_bundles: tuple[ModelCapabilityBundle, ...] | None = None
    active_trial_id: str | None = None
    cancel_requested_at: UtcDatetime | None = None
    cancelled_at: UtcDatetime | None = None
    evidence_scope: BlindEvidenceScope | None = None
    shadow_package_id: str | None = None
    blind_window_id: str | None = None
    promotion_request: ModelPromotionRequest | None = None
    created_at: UtcDatetime
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    updated_at: UtcDatetime
    phases: tuple[OperationPhase, ...] = ()

    @model_validator(mode="after")
    def validate_capability_progress(self) -> UpdateOperation:
        requested = self.requested_bundles
        if requested is not None and len(set(requested)) != len(requested):
            raise ValueError("requested capability bundles must be unique")
        completed = self.completed_bundles or ()
        if len(set(completed)) != len(completed):
            raise ValueError("completed capability bundles must be unique")
        if requested is not None and not set(completed).issubset(requested):
            raise ValueError("completed bundles must be requested")
        return self


__all__ = [
    "BlindEvidenceScope",
    "CandidateTrainingAction",
    "CandidateTrainingRequest",
    "CapabilityReadiness",
    "EodWorkerState",
    "EodWorkerStatus",
    "CandidateTrainingState",
    "ModelTrainingReadiness",
    "ModelTrainingReadinessState",
    "ModelCapabilityBundle",
    "ModelPromotionRequest",
    "OperationPhase",
    "SubmissionDisposition",
    "TrainingTrigger",
    "UpdateOperation",
    "UpdateOperationStage",
    "UpdateOperationStatus",
]
