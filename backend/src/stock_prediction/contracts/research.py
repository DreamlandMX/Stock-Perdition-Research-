"""Point-in-time research, feature, regime, and label contracts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from stock_prediction.contracts.market_data import Identifier, UsSymbol, VersionId
from stock_prediction.contracts.time import UtcDatetime

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Horizon = Literal[1, 5, 20]
FeatureValue = float | int | str | bool | None


def hash_feature_values(values: Mapping[str, FeatureValue]) -> str:
    """Return the canonical SHA-256 for the exact untransformed feature values."""

    return hashlib.sha256(
        json.dumps(
            dict(values),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class PointInTimeEligibility(StrEnum):
    ELIGIBLE = "eligible"
    FORWARD_ONLY = "forward_only"
    INELIGIBLE = "ineligible"


class CalendarSource(StrEnum):
    OPEND_VERIFIED = "opend_verified"
    SYNTHETIC_OFFLINE = "synthetic_offline"


class RegimeLabel(StrEnum):
    RANGE = "range"
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    TRANSITION = "transition"
    INVALID = "invalid"


class BreakoutEvent(StrEnum):
    NONE = "none"
    UP = "up"
    DOWN = "down"
    EXTREME_BOTH = "extreme_both"
    UNRESOLVED = "unresolved"


class DirectionLabel(StrEnum):
    UP = "up"
    FLAT = "flat"
    DOWN = "down"
    UNRESOLVED = "unresolved"


class FalseBreakoutResolution(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    IMMEDIATE_FAILURE = "immediate_failure"
    DELAYED_FAILURE = "delayed_failure"
    CONFIRMED_BREAKOUT = "confirmed_breakout"
    UNRESOLVED = "unresolved"


class ArtifactStatus(StrEnum):
    CURRENT = "current"
    INVALIDATED = "invalidated"
    FAILED = "failed"


class FundamentalObservation(BaseModel):
    """One versioned metric value with explicit market and local availability."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    observation_id: Identifier
    event_id: Identifier
    revision_no: int = Field(ge=1)
    symbol: UsSymbol
    metric: Identifier
    value_numeric: float | None = None
    value_text: Annotated[str, StringConstraints(min_length=1, max_length=512)] | None = None
    unit: Identifier
    report_period: date | None
    published_at: UtcDatetime | None
    source_available_at: UtcDatetime
    ingested_at: UtcDatetime
    source_field: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    point_in_time_eligibility: PointInTimeEligibility
    raw_batch_id: Identifier
    schema_version: VersionId
    dataset_revision: VersionId

    @model_validator(mode="after")
    def validate_point_in_time_evidence(self) -> "FundamentalObservation":
        if (self.value_numeric is None) == (self.value_text is None):
            raise ValueError("exactly one fundamental value is required")
        if self.source_available_at > self.ingested_at:
            raise ValueError("source_available_at must not be after ingested_at")
        if self.published_at is not None and self.published_at > self.source_available_at:
            raise ValueError("published_at must not be after source_available_at")
        if (
            self.point_in_time_eligibility is PointInTimeEligibility.ELIGIBLE
            and self.published_at is None
        ):
            raise ValueError("published_at is required for point-in-time eligibility")
        return self

    @property
    def point_in_time_eligible(self) -> bool:
        return self.point_in_time_eligibility is PointInTimeEligibility.ELIGIBLE


class RegimeState(BaseModel):
    """Rule-derived current state and the frozen evidence used to assign it."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    as_of: UtcDatetime
    state: RegimeLabel
    channel_high: float | None = Field(default=None, gt=0)
    channel_low: float | None = Field(default=None, gt=0)
    atr20: float | None = Field(default=None, ge=0)
    efficiency_ratio: float | None = Field(default=None, ge=0, le=1)
    compression_percentile: float | None = Field(default=None, ge=0, le=1)
    state_confidence: float = Field(ge=0, le=1)
    breakout_event: BreakoutEvent
    extreme_event: bool
    label_version: VersionId
    threshold_version: VersionId
    dataset_revision: VersionId
    feature_set_version: VersionId
    calendar_version: VersionId
    calendar_hash: Sha256
    calendar_source: CalendarSource
    invalid_reason: Identifier | None

    @model_validator(mode="after")
    def validate_state_evidence(self) -> "RegimeState":
        evidence = (
            self.channel_high,
            self.channel_low,
            self.atr20,
            self.efficiency_ratio,
            self.compression_percentile,
        )
        if self.state is RegimeLabel.INVALID:
            if self.invalid_reason is None:
                raise ValueError("invalid_reason is required for invalid state")
        else:
            if self.invalid_reason is not None:
                raise ValueError("invalid_reason is only valid for invalid state")
            if any(value is None for value in evidence):
                raise ValueError("non-invalid state requires complete regime evidence")
            if self.channel_low >= self.channel_high:  # type: ignore[operator]
                raise ValueError("channel_low must be below channel_high")
        return self


class FeatureVector(BaseModel):
    """One point-in-time feature row shared by training and inference."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    as_of: UtcDatetime
    horizon: Horizon
    values: dict[str, FeatureValue]
    feature_set_version: VersionId
    universe_version: VersionId
    dataset_revision: VersionId
    adjustment_revision: VersionId
    threshold_version: VersionId
    calendar_version: VersionId
    calendar_hash: Sha256
    calendar_source: CalendarSource
    feature_lineage_hash: Sha256
    feature_content_hash: Sha256
    feature_values_hash: Sha256
    source_max_available_at: UtcDatetime
    source_max_ingested_at: UtcDatetime
    data_quality_score: float = Field(ge=0, le=1)
    missing_flags: dict[str, bool]
    ood_inputs: dict[str, float]

    @field_validator("values", "ood_inputs")
    @classmethod
    def reject_non_finite_values(cls, values: dict[str, object]) -> dict[str, object]:
        if any(
            isinstance(value, float) and not math.isfinite(value)
            for value in values.values()
        ):
            raise ValueError("feature values must be finite")
        return values

    @model_validator(mode="after")
    def validate_source_cutoff(self) -> "FeatureVector":
        if self.source_max_available_at > self.as_of:
            raise ValueError("source_max_available_at must not exceed as_of")
        if self.source_max_available_at > self.source_max_ingested_at:
            raise ValueError(
                "source_max_available_at must not exceed source_max_ingested_at"
            )
        return self


class HorizonTarget(BaseModel):
    """One fixed-horizon outcome whose event window is after the anchor."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    horizon: Horizon
    forecast_direction: DirectionLabel
    executable_direction: DirectionLabel
    capital_gain_return: float | None
    total_return: float | None
    executable_return: float | None
    forecast_mfe: float | None = Field(default=None, ge=0)
    forecast_mae: float | None = Field(default=None, le=0)
    executable_mfe: float | None = Field(default=None, ge=0)
    executable_mae: float | None = Field(default=None, le=0)
    breakout_event: BreakoutEvent
    breakout_event_at: UtcDatetime | None
    range_exit: bool | None
    range_exit_at: UtcDatetime | None
    label_end_at: UtcDatetime
    resolved: bool

    @model_validator(mode="after")
    def validate_horizon_semantics(self) -> "HorizonTarget":
        if self.horizon == 1 and (
            self.range_exit is not None or self.range_exit_at is not None
        ):
            raise ValueError("range_exit is not defined for horizon 1")
        event_requires_time = self.breakout_event in {
            BreakoutEvent.UP,
            BreakoutEvent.DOWN,
            BreakoutEvent.EXTREME_BOTH,
        }
        if event_requires_time != (self.breakout_event_at is not None):
            raise ValueError("breakout event time does not match breakout outcome")
        if self.range_exit is not True and self.range_exit_at is not None:
            raise ValueError("range_exit_at requires a positive range_exit")
        if self.resolved and any(
            value is None
            for value in (
                self.capital_gain_return,
                self.total_return,
                self.executable_return,
                self.forecast_mfe,
                self.forecast_mae,
                self.executable_mfe,
                self.executable_mae,
            )
        ):
            raise ValueError("resolved horizon requires complete return/path outcomes")
        return self


class LabelBundle(BaseModel):
    """All current-state evidence and future targets for one anchor."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    anchor_at: UtcDatetime
    anchor_state: RegimeLabel
    frozen_channel_high: float = Field(gt=0)
    frozen_channel_low: float = Field(gt=0)
    frozen_atr20: float = Field(ge=0)
    atr_buffer_multiplier: float = Field(ge=0)
    false_breakout_resolution: FalseBreakoutResolution
    false_breakout_event_at: UtcDatetime | None
    targets: tuple[HorizonTarget, ...]
    label_version: VersionId
    threshold_version: VersionId
    dataset_revision: VersionId
    adjustment_revision: VersionId
    feature_set_version: VersionId
    calendar_version: VersionId
    calendar_hash: Sha256
    calendar_source: CalendarSource

    @model_validator(mode="after")
    def validate_bundle(self) -> "LabelBundle":
        if self.frozen_channel_low >= self.frozen_channel_high:
            raise ValueError("frozen channel bounds are invalid")
        if tuple(target.horizon for target in self.targets) != (1, 5, 20):
            raise ValueError("targets must contain exact horizons 1, 5, and 20")
        for target in self.targets:
            if target.label_end_at <= self.anchor_at:
                raise ValueError("label_end_at must be after anchor_at")
            for event_at in (target.breakout_event_at, target.range_exit_at):
                if event_at is not None and not (
                    self.anchor_at < event_at <= target.label_end_at
                ):
                    raise ValueError("target event time must fall inside its horizon")
        if self.false_breakout_event_at is not None and (
            self.false_breakout_event_at <= self.anchor_at
        ):
            raise ValueError("false breakout event must be after anchor")
        return self


class FeatureArtifact(BaseModel):
    """Immutable feature artifact metadata and dependency evidence."""

    model_config = ConfigDict(strict=True, frozen=True)

    artifact_id: Identifier
    dataset_revision: VersionId
    feature_set_version: VersionId
    symbol: UsSymbol
    as_of_start: UtcDatetime
    as_of_end: UtcDatetime
    horizon: Horizon | None
    input_hash: Sha256
    content_hash: Sha256
    dependency_hashes: dict[str, Sha256]
    schema_hash: Sha256
    row_count: int = Field(ge=0)
    generated_at: UtcDatetime
    status: ArtifactStatus
    affected_start: UtcDatetime | None
    affected_end: UtcDatetime | None
    invalidation_reason: Identifier | None
    source_max_available_at: UtcDatetime
    source_max_ingested_at: UtcDatetime
    feature_values_hashes: tuple[Sha256, ...] = ()

    @model_validator(mode="after")
    def validate_artifact(self) -> "FeatureArtifact":
        if self.as_of_start > self.as_of_end:
            raise ValueError("as_of_start must not be after as_of_end")
        if self.source_max_available_at > self.as_of_end:
            raise ValueError("source_max_available_at must not exceed artifact range")
        if self.source_max_available_at > self.source_max_ingested_at:
            raise ValueError("artifact source availability exceeds ingestion time")
        if self.status is ArtifactStatus.CURRENT:
            if any(
                value is not None
                for value in (
                    self.affected_start,
                    self.affected_end,
                    self.invalidation_reason,
                )
            ):
                raise ValueError("current artifact cannot carry invalidation evidence")
        elif self.invalidation_reason is None:
            raise ValueError("invalidation_reason is required for non-current artifact")
        if (self.affected_start is None) != (self.affected_end is None):
            raise ValueError("affected range endpoints are atomic")
        if (
            self.affected_start is not None
            and self.affected_end is not None
            and self.affected_start > self.affected_end
        ):
            raise ValueError("affected range is invalid")
        return self
