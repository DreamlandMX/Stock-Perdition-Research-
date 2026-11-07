"""HTTP response envelopes generated into the public OpenAPI document."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from stock_prediction.contracts.capability import CapabilitySnapshot, CapabilityStatus
from stock_prediction.contracts.health import ComponentHealth, Readiness
from stock_prediction.contracts.market_data import Identifier
from stock_prediction.contracts.serving import ResolvedSnapshot, ServingStatus
from stock_prediction.contracts.time import UtcDatetime


class CapabilityResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    request_id: str
    generated_at: UtcDatetime
    data_status: Readiness
    snapshot: CapabilitySnapshot
    current_health: tuple[ComponentHealth, ...]
    allowance_status: CapabilityStatus


class ErrorResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    request_id: str
    generated_at: UtcDatetime
    data_status: Readiness
    error_code: str
    message: str
    retryable: bool
    affected_scope: list[str]


DataT = TypeVar("DataT")


class ResearchResponse(BaseModel, Generic[DataT]):
    model_config = ConfigDict(strict=True)

    request_id: str
    generated_at: UtcDatetime
    data_status: Readiness
    serving_status: ServingStatus
    effective_abstain: bool
    evaluated_at: UtcDatetime
    exclusion_reason: tuple[Identifier, ...]
    resolved_snapshot: ResolvedSnapshot
    data: DataT


__all__ = ["CapabilityResponse", "ErrorResponse", "ResearchResponse"]
