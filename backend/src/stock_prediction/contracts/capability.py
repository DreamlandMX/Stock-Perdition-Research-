"""Data-source capability probe contracts."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_prediction.contracts.time import UtcDatetime


class CapabilityStatus(StrEnum):
    AVAILABLE = "available"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class PointInTimeStatus(StrEnum):
    PROVEN = "proven"
    FORWARD_ONLY = "forward_only"
    UNPROVEN = "unproven"
    NOT_APPLICABLE = "not_applicable"


class CompatibilityStatus(StrEnum):
    COMPATIBLE = "compatible"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class PermissionScope(StrEnum):
    SYSTEM = "system"
    PUBLIC = "public"
    QUOTE = "quote"
    HISTORICAL = "historical"
    SUBSCRIPTION = "subscription"
    RESEARCH = "research"
    UNKNOWN = "unknown"


class TrainingEligibility(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


class DegradationPolicy(StrEnum):
    BLOCK = "block"
    DEGRADE = "degrade"
    DISABLE_FEATURE = "disable_feature"
    OPTIONAL = "optional"
    UNKNOWN = "unknown"


class CapabilityErrorClass(StrEnum):
    CONNECTION_FAILED = "connection_failed"
    NOT_LOGGED_IN = "not_logged_in"
    PERMISSION_DENIED = "permission_denied"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    PARTIAL_RESPONSE = "partial_response"
    SDK_ERROR = "sdk_error"


class DataCapability(BaseModel):
    """Capability of one interface/market/asset/timeframe key."""

    model_config = ConfigDict(strict=True, frozen=True)

    interface: str
    market: str
    asset_type: str
    timeframe: str | None
    status: CapabilityStatus
    point_in_time_status: PointInTimeStatus
    probed_at: UtcDatetime
    permission_scope: PermissionScope = PermissionScope.UNKNOWN
    quota_total: int | None = Field(default=None, ge=0)
    quota_used: int | None = Field(default=None, ge=0)
    quota_remaining: int | None = Field(default=None, ge=0)
    quota_reset_at: UtcDatetime | None = None
    rate_limit_calls: int | None = Field(default=None, ge=1)
    rate_limit_window: str | None = None
    safety_budget: int | None = Field(default=None, ge=0)
    earliest_available_at: UtcDatetime | None = None
    latest_available_at: UtcDatetime | None = None
    training_eligibility: TrainingEligibility = TrainingEligibility.UNKNOWN
    training_eligibility_reason: str = "not_evaluated"
    degradation_policy: DegradationPolicy = DegradationPolicy.UNKNOWN
    last_error_class: CapabilityErrorClass | None = None

    @model_validator(mode="after")
    def validate_governance(self) -> "DataCapability":
        quota = (self.quota_total, self.quota_used, self.quota_remaining)
        if any(value is not None for value in quota):
            if any(value is None for value in quota):
                raise ValueError("quota_total, quota_used, and quota_remaining are atomic")
            if self.quota_total != self.quota_used + self.quota_remaining:  # type: ignore[operator]
                raise ValueError("quota_total must equal quota_used + quota_remaining")

        rate = (self.rate_limit_calls, self.rate_limit_window, self.safety_budget)
        if any(value is not None for value in rate):
            if any(value is None for value in rate):
                raise ValueError("rate limit fields are atomic")
            expected_budget = int(self.rate_limit_calls * 0.8)  # type: ignore[operator]
            if self.safety_budget != expected_budget:
                raise ValueError("safety_budget must equal 80% of rate_limit_calls")

        if (
            self.earliest_available_at is not None
            and self.latest_available_at is not None
            and self.earliest_available_at > self.latest_available_at
        ):
            raise ValueError("earliest_available_at must not exceed latest_available_at")

        if self.status is CapabilityStatus.UNKNOWN:
            eligibility = TrainingEligibility.UNKNOWN
            reason = "capability_not_probed"
        elif (
            self.status is CapabilityStatus.AVAILABLE
            and self.point_in_time_status is PointInTimeStatus.PROVEN
        ):
            eligibility = TrainingEligibility.ELIGIBLE
            reason = "available_with_point_in_time_evidence"
        elif self.point_in_time_status is PointInTimeStatus.FORWARD_ONLY:
            eligibility = TrainingEligibility.INELIGIBLE
            reason = "forward_only_observations_cannot_backfill_history"
        elif self.point_in_time_status is PointInTimeStatus.UNPROVEN:
            eligibility = TrainingEligibility.INELIGIBLE
            reason = "point_in_time_evidence_unproven"
        elif self.point_in_time_status is PointInTimeStatus.NOT_APPLICABLE:
            eligibility = TrainingEligibility.INELIGIBLE
            reason = "not_a_historical_training_input"
        else:
            eligibility = TrainingEligibility.INELIGIBLE
            reason = "status_or_point_in_time_evidence_insufficient"
        object.__setattr__(self, "training_eligibility", eligibility)
        object.__setattr__(self, "training_eligibility_reason", reason)
        return self


class CapabilitySnapshot(BaseModel):
    """Version evidence and rows produced by one capability probe."""

    model_config = ConfigDict(strict=True)

    snapshot_id: str
    probed_at: UtcDatetime
    opend_version: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    sdk_version: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    compatibility_status: CompatibilityStatus = CompatibilityStatus.UNKNOWN
    capabilities: list[DataCapability] = Field(min_length=1)
