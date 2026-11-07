"""Health dimensions for services and data components."""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from stock_prediction.contracts.time import UtcDatetime


class Connectivity(StrEnum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class Freshness(StrEnum):
    CURRENT = "current"
    DELAYED = "delayed"
    STALE = "stale"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class Completeness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    EMPTY = "empty"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class Readiness(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class ComponentHealth(BaseModel):
    """Independent health dimensions for a single component."""

    model_config = ConfigDict(strict=True)

    component: str
    connectivity: Connectivity
    freshness: Freshness
    completeness: Completeness
    readiness: Readiness
    last_success_at: UtcDatetime | None = None
    data_cutoff: date | None = None
    expected_cutoff: date | None = None
    snapshot_cutoff: date | None = None
    lag_sessions: int | None = None
    last_pipeline_success_at: UtcDatetime | None = None
    daily_symbols_complete: int | None = None
    daily_symbols_total: int | None = None
    provider: str | None = None
    feed: str | None = None
    provider_version: str | None = None
    delay_minutes: int | None = None
    opend_version: str | None = None
    quota_total: int | None = None
    quota_used: int | None = None
    quota_remaining: int | None = None


class HealthResponse(BaseModel):
    """Top-level service and data health response."""

    model_config = ConfigDict(strict=True)

    request_id: str
    generated_at: UtcDatetime
    service_status: Readiness
    data_status: Readiness
    components: list[ComponentHealth]
