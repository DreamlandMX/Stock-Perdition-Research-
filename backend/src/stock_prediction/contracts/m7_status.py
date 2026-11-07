"""Read-only M7 lifecycle status exposed to Research and Health."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from stock_prediction.contracts.operations import ModelCapabilityBundle
from stock_prediction.contracts.time import UtcDatetime

M7AuditEventType = Literal[
    "training_queued",
    "training_started",
    "training_cancelled",
    "training_completed",
    "training_failed",
    "shadow_frozen",
    "blind_started",
    "blind_completed",
    "blind_invalidated",
    "candidate_rejected",
    "manual_approval",
    "promotion_completed",
    "drift_warning",
    "drift_confirmed",
    "integrity_failure",
    "automatic_rollback",
    "retention_deleted",
]


class M7LifecycleEvidenceSection(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    status: Literal["completed", "failed", "not_run"]
    reason: str | None
    payload: JsonValue | None


class M7CapabilityLifecycleStatus(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    bundle_id: ModelCapabilityBundle
    development_state: str
    shadow_state: str
    blind_state: str
    promotion_state: str
    package_id: str | None = None
    package_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    code_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    universe_version: str | None = None
    dataset_revision: str | None = None
    feature_set_version: str | None = None
    feature_input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    label_version: str | None = None
    split_manifest_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidate_matrix_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    dependency_lock_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_components: tuple[str, ...] = ()
    blend_weights: tuple[str, ...] = ()
    blind_window_id: str | None = None
    incumbent_id: str | None = None
    blind_available_anchors: int = Field(ge=0, le=77)
    blind_mature_labels: int = Field(ge=0, le=77)
    blind_missing_anchors: int = Field(default=0, ge=0)
    blind_next_scheduled_session: date | None = None
    blind_last_available_anchor: date | None = None
    blind_report_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    blind_required_anchors: Literal[77] = 77
    promotion_allowed: bool
    is_active_champion: bool
    block_reasons: tuple[str, ...] = ()
    evidence_sections: dict[str, M7LifecycleEvidenceSection] = Field(
        default_factory=dict
    )


class M7LifecycleAuditEvent(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    event_id: str
    event_type: M7AuditEventType
    recorded_at: UtcDatetime
    actor: str
    run_id: str | None
    bundle_id: ModelCapabilityBundle | None
    detail: dict[str, str | int | float | bool | None]


class M7DriftLifecycleStatus(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    state: Literal["none", "warning", "review_required", "training_authorized"]
    consecutive_daily_warning_sessions: int = Field(ge=0)
    consecutive_mature_weekly_breaches: int = Field(ge=0)
    reason: str
    last_updated_at: UtcDatetime | None = None
    affected_scopes: tuple[str, ...] = ()
    affected_inputs: tuple[str, ...] = ()
    retryability: Literal["automatic", "manual", "not_applicable"] = "not_applicable"
    recommended_action: str = "continue_monitoring"
    feature_warning: bool | None = None
    prediction_warning: bool | None = None
    outcome_warning: bool | None = None
    core_feature_psi_fraction_above_020: float | None = Field(default=None, ge=0, le=1)
    missingness_rate: float | None = Field(default=None, ge=0, le=1)
    coverage_rate: float | None = Field(default=None, ge=0, le=1)
    ood_rate: float | None = Field(default=None, ge=0, le=1)


class M7PartialTrainingSummary(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    run_id: str
    recipe_id: Literal["candidate-recipe-seed100-targeted-v10"]
    bundle_id: ModelCapabilityBundle
    operation_status: str
    current_stage: str | None
    completion_state: Literal["incomplete"] = "incomplete"
    successful_trials: int = Field(ge=0)
    failed_trials: int = Field(ge=0)
    valid_folds: int = Field(ge=0, le=4)
    required_folds: Literal[4] = 4
    training_devices: tuple[str, ...]
    error_code: str | None
    generated_at: UtcDatetime


class IndependentTrainingSummary(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    run_id: str
    state: str
    source_status: Literal["ready", "unavailable"] = "ready"
    selected_for_research: bool = False
    completed_at: UtcDatetime | None = None
    feature_as_of: UtcDatetime | None = None
    development_data_cutoff: date | None = None
    main_model_version: str | None = None
    main_passed_tasks: int = Field(default=0, ge=0)
    main_total_tasks: int = Field(default=0, ge=0)
    m7_run_id: str | None = None
    m7_gate_status: str | None = None
    m7_failed_gates: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class M7LifecycleStatus(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["m7-lifecycle-status-v1"] = "m7-lifecycle-status-v1"
    source_status: Literal["ready", "partial", "unavailable"]
    recipe_id: Literal[
        "candidate-recipe-seed100-targeted-v9",
        "candidate-recipe-seed100-targeted-v10",
    ]
    latest_training_run_id: str | None
    latest_partial_run_id: str | None = None
    partial_training_summary: M7PartialTrainingSummary | None = None
    independent_training_summary: IndependentTrainingSummary | None = None
    latest_generated_at: UtcDatetime | None
    executor_configured: bool
    disclaimer: str
    capabilities: tuple[M7CapabilityLifecycleStatus, ...]
    drift: M7DriftLifecycleStatus
    recent_events: tuple[M7LifecycleAuditEvent, ...]
    errors: tuple[str, ...] = ()


__all__ = [
    "M7CapabilityLifecycleStatus",
    "M7DriftLifecycleStatus",
    "M7LifecycleAuditEvent",
    "M7LifecycleEvidenceSection",
    "M7LifecycleStatus",
    "M7PartialTrainingSummary",
]
