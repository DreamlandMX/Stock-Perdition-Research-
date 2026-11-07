"""Immutable M5 forecast, evidence, and research-page read contracts."""

from __future__ import annotations

import math
from datetime import date
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from stock_prediction.contracts.health import (
    Completeness,
    Connectivity,
    Freshness,
    Readiness,
)
from stock_prediction.contracts.market_data import Identifier, UsSymbol, VersionId
from stock_prediction.contracts.operations import BlindEvidenceScope
from stock_prediction.contracts.research import Horizon, RegimeLabel
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.contracts.text import LocalizableText
from stock_prediction.modeling.confidence import ConfidenceLevel, RejectionReason

Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
DisplayText = Annotated[str, StringConstraints(min_length=1, max_length=512)]
PRICE_BAND_REL_TOLERANCE: Final = 1e-9
PRICE_BAND_ABS_TOLERANCE: Final = 1e-8


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class ServingStatus(StrEnum):
    LIVE = "live"
    DELAYED = "delayed"
    STALE = "stale"
    PARTIAL = "partial"
    OFFLINE = "offline"
    UNAVAILABLE = "unavailable"


HARD_ABSTAIN_SERVING_STATUSES: Final[frozenset[ServingStatus]] = frozenset(
    {
        ServingStatus.STALE,
        ServingStatus.OFFLINE,
        ServingStatus.UNAVAILABLE,
    }
)


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class PredictionBasis(StrEnum):
    FORECAST = "forecast"
    EXECUTABLE = "executable"


class PredictionReadiness(StrEnum):
    DATA_ONLY = "data_only"
    FORECAST_READY = "forecast_ready"


class ExecutionReadiness(StrEnum):
    BLOCKED = "blocked"
    READY = "ready"


class ResearchReadiness(StrEnum):
    RULES_ONLY = "rules_only"
    PARTIAL_VERIFIED = "partial_verified"
    FULL_VERIFIED = "full_verified"
    MANUAL_UNVERIFIED = "manual_unverified"


class ResearchDisplayMode(StrEnum):
    RESEARCH = "research"
    CHALLENGER = "challenger"
    FORMAL = "formal"


class ResearchEvidenceScope(StrEnum):
    VERIFIED = "verified"
    CHALLENGER = "challenger"
    SHADOW = "shadow"


class ReturnBasis(StrEnum):
    CAPITAL_GAIN = "capital_gain"
    TOTAL_RETURN = "total_return"
    EXECUTABLE = "executable"


class ScreenReturnBasis(StrEnum):
    CAPITAL_GAIN = "capital_gain"
    EXECUTABLE = "executable"


class ScreenConfidenceFloor(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ResolvedSnapshot(_StrictModel):
    snapshot_id: Identifier
    revision_no: int = Field(ge=1)
    as_of: UtcDatetime
    dataset_revision: VersionId
    feature_set_version: VersionId
    universe_version: VersionId
    prediction_readiness: PredictionReadiness = PredictionReadiness.FORECAST_READY
    activation_basis: Literal["manual_override"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    execution_readiness: ExecutionReadiness | None = Field(
        default=None,
        exclude_if=lambda value: value in {None, ExecutionReadiness.BLOCKED},
        validate_default=True,
    )
    research_readiness: ResearchReadiness = Field(
        default=ResearchReadiness.FULL_VERIFIED,
        exclude_if=lambda value: value is ResearchReadiness.RULES_ONLY,
    )
    model_version: VersionId | None
    calibration_version: VersionId | None

    @field_validator("execution_readiness", mode="before")
    @classmethod
    def default_execution_readiness(
        cls,
        value: ExecutionReadiness | str | None,
    ) -> ExecutionReadiness:
        return (
            ExecutionReadiness.BLOCKED if value is None else ExecutionReadiness(value)
        )

    @model_validator(mode="after")
    def validate_prediction_readiness(self) -> Self:
        if (self.activation_basis == "manual_override") != (
            self.research_readiness is ResearchReadiness.MANUAL_UNVERIFIED
        ):
            raise ValueError("manual activation requires explicit unverified status")
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
                raise ValueError("data-only snapshots cannot name model versions")
            if self.research_readiness is ResearchReadiness.FULL_VERIFIED:
                raise ValueError(
                    "data-only snapshots cannot claim full verified research"
                )
        elif not all(versions_available):
            raise ValueError("forecast-ready snapshots require model versions")
        elif self.research_readiness not in {
            ResearchReadiness.FULL_VERIFIED,
            ResearchReadiness.MANUAL_UNVERIFIED,
        }:
            raise ValueError("forecast-ready snapshots require full verified research")
        return self


class OverviewAnomalySnapshot(_StrictModel):
    snapshot_id: Identifier
    total_symbols: int = Field(ge=0)
    volume_anomalies: int | None = Field(default=None, ge=0)
    upper_channel_count: int | None = Field(default=None, ge=0)
    lower_channel_count: int | None = Field(default=None, ge=0)
    breakout_count: int | None = Field(default=None, ge=0)
    regime_change_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        counts = (
            self.volume_anomalies,
            self.upper_channel_count,
            self.lower_channel_count,
            self.breakout_count,
            self.regime_change_count,
        )
        if any(count is not None and count > self.total_symbols for count in counts):
            raise ValueError("anomaly count cannot exceed total symbols")
        return self


class SnapshotServingReference(_StrictModel):
    generated_at: UtcDatetime
    valid_until: UtcDatetime | None
    has_available_forecasts: bool
    has_verified_research_outputs: bool = Field(
        default=False,
        exclude_if=lambda value: not value,
    )
    has_challenger_preview: bool = Field(
        default=False,
        exclude_if=lambda value: not value,
    )
    has_shadow_preview: bool = Field(
        default=False,
        exclude_if=lambda value: not value,
    )

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.has_available_forecasts != (self.valid_until is not None):
            raise ValueError("snapshot forecast availability and validity must agree")
        return self


class DirectionProbabilities(_StrictModel):
    up: float = Field(ge=0, le=1)
    flat: float = Field(ge=0, le=1)
    down: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_sum(self) -> Self:
        if not math.isclose(self.up + self.flat + self.down, 1.0, abs_tol=1e-9):
            raise ValueError("direction probabilities must sum to 1")
        return self


class BreakoutProbabilities(_StrictModel):
    up: float = Field(ge=0, le=1)
    down: float = Field(ge=0, le=1)
    none: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_sum(self) -> Self:
        if not math.isclose(self.up + self.down + self.none, 1.0, abs_tol=1e-9):
            raise ValueError("breakout probabilities must sum to 1")
        return self


class RegimeProbabilities(_StrictModel):
    range: float = Field(ge=0, le=1)
    uptrend: float = Field(ge=0, le=1)
    downtrend: float = Field(ge=0, le=1)
    transition: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_sum(self) -> Self:
        total = self.range + self.uptrend + self.downtrend + self.transition
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError("regime probabilities must sum to 1")
        return self


class ReturnQuantiles(_StrictModel):
    q10: float
    q25: float
    q50: float
    q75: float
    q90: float

    @model_validator(mode="after")
    def validate_monotonicity(self) -> Self:
        values = (self.q10, self.q25, self.q50, self.q75, self.q90)
        if any(left > right for left, right in zip(values, values[1:])):
            raise ValueError("return quantiles must be monotonic")
        return self


class PriceBand(_StrictModel):
    q10: float = Field(gt=0)
    q25: float = Field(gt=0)
    q50: float = Field(gt=0)
    q75: float = Field(gt=0)
    q90: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_monotonicity(self) -> Self:
        values = (self.q10, self.q25, self.q50, self.q75, self.q90)
        if any(left > right for left, right in zip(values, values[1:])):
            raise ValueError("price-band quantiles must be monotonic")
        return self


class ResearchPriceProjection(_StrictModel):
    """Task-scoped price evidence that remains separate from formal forecasts."""

    reference_price: float = Field(gt=0)
    reference_price_at: UtcDatetime
    currency: Currency
    endpoint_price_band: PriceBand | None = None
    endpoint_price_unavailable_reason: Identifier | None = None
    stage_high_price_q50: float | None = Field(default=None, gt=0)
    stage_high_price_unavailable_reason: Identifier | None = None
    stage_low_price_q50: float | None = Field(default=None, gt=0)
    stage_low_price_unavailable_reason: Identifier | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        pairs = (
            (
                self.endpoint_price_band,
                self.endpoint_price_unavailable_reason,
                "endpoint price",
            ),
            (
                self.stage_high_price_q50,
                self.stage_high_price_unavailable_reason,
                "stage-high price",
            ),
            (
                self.stage_low_price_q50,
                self.stage_low_price_unavailable_reason,
                "stage-low price",
            ),
        )
        for value, reason, label in pairs:
            if (value is None) == (reason is None):
                raise ValueError(f"{label} requires exactly one value or reason")
        if (
            self.stage_high_price_q50 is not None
            and self.stage_high_price_q50 < self.reference_price
        ):
            raise ValueError("stage-high price cannot be below the reference price")
        if (
            self.stage_low_price_q50 is not None
            and self.stage_low_price_q50 > self.reference_price
        ):
            raise ValueError("stage-low price cannot exceed the reference price")
        return self


class CalibratedInterval(_StrictModel):
    return_basis: ReturnBasis
    lower: float
    upper: float
    coverage_target: float = Field(gt=0, lt=1)
    method: Identifier
    calibration_version: VersionId

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.lower > self.upper:
            raise ValueError("calibrated interval lower bound exceeds upper bound")
        return self


class RangeExitHazard(_StrictModel):
    trading_day: int = Field(ge=1, le=20)
    hazard_probability: float = Field(ge=0, le=1)
    cumulative_probability: float = Field(ge=0, le=1)


class RangeExitMetadata(_StrictModel):
    confirmation_days: Literal[2] = 2
    censoring_rule: Identifier
    sample_denominator: int = Field(ge=0)
    not_generated_reason: Identifier | None


class HorizonForecast(_StrictModel):
    horizon: Horizon
    forecast_direction_probabilities: DirectionProbabilities
    breakout_probabilities: BreakoutProbabilities
    range_exit_probability: float | None = Field(default=None, ge=0, le=1)
    range_exit_hazard_curve: tuple[RangeExitHazard, ...]
    range_exit_metadata: RangeExitMetadata
    capital_gain_return_quantiles: ReturnQuantiles
    total_return_quantiles: ReturnQuantiles
    executable_direction_probabilities: DirectionProbabilities
    executable_return_quantiles: ReturnQuantiles
    calibrated_return_intervals: tuple[CalibratedInterval, ...] = Field(
        min_length=3, max_length=3
    )
    interval_method: Identifier
    coverage_target: float = Field(gt=0, lt=1)
    forecast_mfe_quantiles: ReturnQuantiles
    forecast_mae_quantiles: ReturnQuantiles
    executable_mfe_quantiles: ReturnQuantiles
    executable_mae_quantiles: ReturnQuantiles
    reference_price: float = Field(gt=0)
    reference_price_at: UtcDatetime
    currency: Currency
    adjustment_mode: Identifier
    price_band: PriceBand | None
    price_band_unavailable_reason: Identifier | None

    @model_validator(mode="after")
    def validate_horizon_evidence(self) -> Self:
        if self.horizon == 1:
            if self.range_exit_probability is not None or self.range_exit_hazard_curve:
                raise ValueError("1-day survival output must not be generated")
            if self.range_exit_metadata.not_generated_reason is None:
                raise ValueError("1-day survival exclusion requires a reason")
        else:
            has_probability = self.range_exit_probability is not None
            has_curve = bool(self.range_exit_hazard_curve)
            if has_probability != has_curve:
                raise ValueError(
                    "range-exit probability and hazard curve must be atomic"
                )
            if (
                has_probability
                and self.range_exit_metadata.not_generated_reason is not None
            ):
                raise ValueError(
                    "generated survival output cannot have an unavailable reason"
                )
            if (
                not has_probability
                and self.range_exit_metadata.not_generated_reason is None
            ):
                raise ValueError("missing survival output requires a reason")

        days = tuple(point.trading_day for point in self.range_exit_hazard_curve)
        cumulative = tuple(
            point.cumulative_probability for point in self.range_exit_hazard_curve
        )
        if days and days != tuple(range(1, self.horizon + 1)):
            raise ValueError(
                "range-exit hazard days must be consecutive through the horizon"
            )
        if any(left > right for left, right in zip(cumulative, cumulative[1:])):
            raise ValueError("range-exit cumulative probability must be monotonic")
        survival_probability = 1.0
        for point in self.range_exit_hazard_curve:
            survival_probability *= 1.0 - point.hazard_probability
            expected_cumulative = 1.0 - survival_probability
            if not math.isclose(
                point.cumulative_probability,
                expected_cumulative,
                rel_tol=1e-9,
                abs_tol=1e-9,
            ):
                raise ValueError(
                    "range-exit cumulative probability must match daily hazards"
                )
        if (
            self.range_exit_probability is not None
            and cumulative
            and not math.isclose(
                cumulative[-1], self.range_exit_probability, abs_tol=1e-9
            )
        ):
            raise ValueError("range-exit probability must match the hazard curve")

        interval_bases = tuple(
            interval.return_basis for interval in self.calibrated_return_intervals
        )
        if set(interval_bases) != set(ReturnBasis):
            raise ValueError("calibrated intervals must cover every return basis")
        if any(
            interval.method != self.interval_method
            or interval.coverage_target != self.coverage_target
            for interval in self.calibrated_return_intervals
        ):
            raise ValueError("calibrated interval metadata is inconsistent")

        mfe_values = (
            *self.forecast_mfe_quantiles.model_dump().values(),
            *self.executable_mfe_quantiles.model_dump().values(),
        )
        mae_values = (
            *self.forecast_mae_quantiles.model_dump().values(),
            *self.executable_mae_quantiles.model_dump().values(),
        )
        if any(value < 0 for value in mfe_values):
            raise ValueError("MFE quantiles must be non-negative")
        if any(value > 0 for value in mae_values):
            raise ValueError("MAE quantiles must be non-positive")

        if (self.price_band is None) == (self.price_band_unavailable_reason is None):
            raise ValueError(
                "price band requires exactly one value or unavailable reason"
            )
        if self.price_band is not None:
            returns = self.capital_gain_return_quantiles
            return_values = (
                returns.q10,
                returns.q25,
                returns.q50,
                returns.q75,
                returns.q90,
            )
            band_values = (
                self.price_band.q10,
                self.price_band.q25,
                self.price_band.q50,
                self.price_band.q75,
                self.price_band.q90,
            )
            if any(
                not math.isclose(
                    actual,
                    self.reference_price * (1.0 + return_value),
                    rel_tol=PRICE_BAND_REL_TOLERANCE,
                    abs_tol=PRICE_BAND_ABS_TOLERANCE,
                )
                for actual, return_value in zip(band_values, return_values, strict=True)
            ):
                raise ValueError(
                    "price band must be derived from reference price and capital-gain quantiles"
                )
        return self


class SimilarCaseQuantiles(_StrictModel):
    q10: float
    q50: float
    q90: float

    @model_validator(mode="after")
    def validate_monotonicity(self) -> Self:
        if not self.q10 <= self.q50 <= self.q90:
            raise ValueError("similar-case quantiles must be monotonic")
        return self


class SimilarCaseSummary(_StrictModel):
    effective_independent_count: int = Field(ge=0)
    insufficient_evidence: bool
    median_distance: float | None = Field(default=None, ge=0)
    return_quantiles: SimilarCaseQuantiles | None
    win_rate: float | None = Field(default=None, ge=0, le=1)
    breakout_rate: float | None = Field(default=None, ge=0, le=1)
    worst_mae_case_id: Identifier | None

    @model_validator(mode="after")
    def validate_evidence_state(self) -> Self:
        if self.effective_independent_count == 0:
            if not self.insufficient_evidence:
                raise ValueError("zero similar cases must be insufficient evidence")
            if any(
                value is not None
                for value in (
                    self.median_distance,
                    self.return_quantiles,
                    self.win_rate,
                    self.breakout_rate,
                    self.worst_mae_case_id,
                )
            ):
                raise ValueError(
                    "empty similar-case evidence cannot publish statistics"
                )
        else:
            if any(
                value is None
                for value in (
                    self.median_distance,
                    self.return_quantiles,
                    self.worst_mae_case_id,
                )
            ):
                raise ValueError("similar-case evidence requires summary statistics")
        if self.insufficient_evidence and (
            self.win_rate is not None or self.breakout_rate is not None
        ):
            raise ValueError("insufficient similar cases cannot publish rates")
        if not self.insufficient_evidence and (
            self.win_rate is None or self.breakout_rate is None
        ):
            raise ValueError("sufficient similar cases require outcome rates")
        return self


class FeatureFamilyEvidence(_StrictModel):
    family: Identifier
    contribution: float
    training_percentile: float = Field(ge=0, le=1)


class CalibrationEvidence(_StrictModel):
    probability_bin_sample_count: int = Field(ge=0)
    calibration_error: float = Field(ge=0, le=1)
    cqr_empirical_coverage: float = Field(ge=0, le=1)
    cqr_average_width: float = Field(ge=0)


class ForecastEvidence(_StrictModel):
    top_feature_families: tuple[FeatureFamilyEvidence, ...] = Field(min_length=1)
    regime_probabilities: RegimeProbabilities | None
    regime_probabilities_unavailable_reason: Identifier | None
    regime_model_version: VersionId | None
    ood_score: float = Field(ge=0)
    ood_method: Identifier
    ood_threshold: float = Field(ge=0)
    model_disagreement: float = Field(ge=0, le=1)
    model_disagreement_summary: DisplayText
    similar_case_summary: SimilarCaseSummary
    calibration_evidence: CalibrationEvidence
    data_quality_score: float = Field(ge=0, le=1)
    missing_rate: float = Field(ge=0, le=1)
    evidence_cutoff: UtcDatetime

    @model_validator(mode="after")
    def validate_regime_evidence(self) -> Self:
        available = self.regime_probabilities is not None
        if available:
            if self.regime_model_version is None:
                raise ValueError("regime model version is required with probabilities")
            if self.regime_probabilities_unavailable_reason is not None:
                raise ValueError("available regime probabilities cannot have a reason")
        elif (
            self.regime_probabilities_unavailable_reason is None
            or self.regime_model_version is not None
        ):
            raise ValueError("unavailable regime probabilities require a reason")
        return self


class ForecastBundle(_StrictModel):
    symbol: UsSymbol
    as_of: UtcDatetime
    current_regime: RegimeLabel
    regime_probabilities: RegimeProbabilities | None
    regime_probabilities_unavailable_reason: Identifier | None
    current_event_flags: tuple[Identifier, ...]
    horizons: tuple[HorizonForecast, ...]
    invalidation_conditions: tuple[Identifier, ...] = Field(min_length=1)
    confidence_level: ConfidenceLevel
    confidence_score: float = Field(ge=0, le=1)
    ood_score: float = Field(ge=0)
    model_disagreement: float = Field(ge=0, le=1)
    similar_case_summary: SimilarCaseSummary
    evidence: ForecastEvidence
    abstain: bool
    abstain_reasons: tuple[RejectionReason, ...]
    dataset_revision: VersionId
    feature_set_version: VersionId
    label_version: VersionId
    universe_version: VersionId
    model_version: VersionId
    calibration_version: VersionId
    distance_version: VersionId
    data_freshness_seconds: int = Field(ge=0)
    data_quality_status: Literal["current", "partial", "invalid"]
    generated_at: UtcDatetime
    inference_latency_ms: float = Field(ge=0)
    inference_latency_scope: Identifier
    revision_no: int = Field(ge=1)
    snapshot_id: Identifier
    valid_until: UtcDatetime

    @model_validator(mode="after")
    def validate_bundle(self) -> Self:
        if tuple(item.horizon for item in self.horizons) != (1, 5, 20):
            raise ValueError("horizons must contain exact horizons 1, 5, and 20")
        if (
            self.current_regime is RegimeLabel.INVALID
            or self.data_quality_status == "invalid"
        ):
            raise ValueError(
                "pre-model hard-gate failures must use ForecastUnavailable"
            )
        survival_outputs = tuple(
            forecast.range_exit_probability is not None
            for forecast in self.horizons
            if forecast.horizon in (5, 20)
        )
        if self.current_regime is RegimeLabel.RANGE and not all(survival_outputs):
            raise ValueError("range regime requires 5/20-day survival output")
        if self.current_regime is not RegimeLabel.RANGE and any(survival_outputs):
            raise ValueError("survival output is allowed only for range regime")
        if len(set(self.current_event_flags)) != len(self.current_event_flags):
            raise ValueError("current event flags must be unique")
        if len(set(self.invalidation_conditions)) != len(self.invalidation_conditions):
            raise ValueError("invalidation conditions must be unique")
        if len(set(self.abstain_reasons)) != len(self.abstain_reasons):
            raise ValueError("abstain reasons must be unique")
        if self.abstain != bool(self.abstain_reasons):
            raise ValueError("abstain and abstain reasons must agree")
        if self.abstain != (self.confidence_level is ConfidenceLevel.UNAVAILABLE):
            raise ValueError("abstention must use unavailable confidence")

        if (self.regime_probabilities is None) == (
            self.regime_probabilities_unavailable_reason is None
        ):
            raise ValueError(
                "regime probabilities require exactly one value or unavailable reason"
            )
        if self.regime_probabilities != self.evidence.regime_probabilities:
            raise ValueError("bundle and evidence regime probabilities must agree")
        if (
            self.regime_probabilities_unavailable_reason
            != self.evidence.regime_probabilities_unavailable_reason
        ):
            raise ValueError(
                "bundle and evidence regime-probability unavailable reasons must agree"
            )
        if any(
            interval.calibration_version != self.calibration_version
            for forecast in self.horizons
            for interval in forecast.calibrated_return_intervals
        ):
            raise ValueError(
                "all calibrated intervals must use the bundle calibration_version"
            )
        if self.ood_score != self.evidence.ood_score:
            raise ValueError("bundle and evidence OOD scores must agree")
        if self.model_disagreement != self.evidence.model_disagreement:
            raise ValueError("bundle and evidence disagreement must agree")
        if self.similar_case_summary != self.evidence.similar_case_summary:
            raise ValueError("bundle and evidence similar-case summaries must agree")
        if self.evidence.evidence_cutoff > self.as_of:
            raise ValueError("evidence cutoff must not exceed forecast as-of")
        if self.generated_at < self.as_of:
            raise ValueError("generated_at must not precede forecast as-of")
        if self.valid_until <= self.as_of:
            raise ValueError("valid_until must be after forecast as-of")
        if self.valid_until <= self.generated_at:
            raise ValueError("valid_until must be after generated_at")
        return self


class SimilarCaseView(_StrictModel):
    case_id: Identifier
    as_of: UtcDatetime
    distance: float = Field(ge=0)
    regime: RegimeLabel
    return_quantiles: SimilarCaseQuantiles
    breakout_result: Literal["up", "down", "none"]
    mae: float = Field(le=0)
    worst_case: bool


class ServingOverlay(_StrictModel):
    serving_status: ServingStatus
    effective_abstain: bool
    evaluated_at: UtcDatetime
    exclusion_reason: tuple[Identifier, ...]

    @model_validator(mode="after")
    def validate_reasons(self) -> Self:
        if len(set(self.exclusion_reason)) != len(self.exclusion_reason):
            raise ValueError("serving exclusion reasons must be unique")
        if bool(self.exclusion_reason) and not self.effective_abstain:
            raise ValueError("serving exclusions require effective abstention")
        if (
            self.serving_status in HARD_ABSTAIN_SERVING_STATUSES
            and not self.effective_abstain
        ):
            raise ValueError(
                "stale, offline, and unavailable states require effective abstain"
            )
        return self


class ForecastAvailable(_StrictModel):
    availability: Literal["available"] = "available"
    bundle: ForecastBundle
    serving: ServingOverlay
    evidence: ForecastEvidence | None
    similar_cases: tuple[SimilarCaseView, ...]
    selected_horizon: HorizonForecast | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.evidence is not None and self.evidence != self.bundle.evidence:
            raise ValueError("expanded evidence must match the immutable bundle")
        if self.serving.evaluated_at < self.bundle.generated_at:
            raise ValueError(
                "serving evaluated_at must not precede bundle generated_at"
            )
        dynamically_abstained = (
            self.serving.serving_status in HARD_ABSTAIN_SERVING_STATUSES
            or bool(self.serving.exclusion_reason)
        )
        if self.serving.effective_abstain != (
            self.bundle.abstain or dynamically_abstained
        ):
            raise ValueError(
                "serving effective abstain must preserve bundle abstention and dynamic exclusions"
            )
        if (
            self.selected_horizon is not None
            and self.selected_horizon not in self.bundle.horizons
        ):
            raise ValueError("selected horizon must come from the immutable bundle")
        return self


class TaskForecastEvidence(_StrictModel):
    task: Identifier
    horizon: Horizon
    scope: ResearchEvidenceScope
    gate_passed: bool
    model_version: VersionId
    calibration_version: VersionId | None
    evaluation_run_id: Identifier
    component_evaluated_at: UtcDatetime | None = None
    fold_count: int = Field(ge=0)
    baseline_wins: int | None = Field(default=None, ge=0)
    positive_cost_windows: int | None = Field(default=None, ge=0)
    pinball_wins: int | None = Field(default=None, ge=0)
    coverage_windows_passed: int | None = Field(default=None, ge=0)
    evaluation_sample_count: int | None = Field(default=None, ge=1)
    evaluation_start: date | None = None
    evaluation_end: date | None = None
    worst_coverage: float | None = Field(default=None, ge=0, le=1)
    average_interval_width: float | None = Field(default=None, ge=0)
    average_pinball_loss: float | None = Field(default=None, ge=0)
    baseline_pinball_loss: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if (self.evaluation_start is None) != (self.evaluation_end is None):
            raise ValueError("evaluation dates must be supplied together")
        if (
            self.evaluation_start is not None
            and self.evaluation_end is not None
            and self.evaluation_start > self.evaluation_end
        ):
            raise ValueError("evaluation dates are inverted")
        if self.scope is ResearchEvidenceScope.VERIFIED and not self.gate_passed:
            raise ValueError("verified research evidence requires a passed task gate")
        if self.scope is ResearchEvidenceScope.CHALLENGER and self.gate_passed:
            raise ValueError("challenger evidence must represent a failed task gate")
        if self.scope is ResearchEvidenceScope.SHADOW and not self.gate_passed:
            raise ValueError("shadow evidence requires a passed development task gate")
        return self


class ShadowWindowProgress(_StrictModel):
    window_id: Identifier
    anchors_collected: int = Field(ge=0)
    required_anchors: int = Field(default=77, ge=1)
    mature_labels: int = Field(ge=0)
    required_mature_labels: int = Field(default=77, ge=1)
    label_horizon: Literal[20] = 20

    @model_validator(mode="after")
    def validate_progress(self) -> Self:
        if self.anchors_collected > self.required_anchors:
            raise ValueError("shadow anchors cannot exceed required anchors")
        if self.mature_labels > self.required_mature_labels:
            raise ValueError("mature labels cannot exceed required labels")
        if self.mature_labels > self.anchors_collected:
            raise ValueError("mature labels cannot exceed collected anchors")
        return self


class ResearchModelValues(_StrictModel):
    forecast_up_probability: float | None = Field(default=None, ge=0, le=1)
    breakout_up_probability: float | None = Field(default=None, ge=0, le=1)
    breakout_down_probability: float | None = Field(default=None, ge=0, le=1)
    range_exit_probability: float | None = Field(default=None, ge=0, le=1)
    capital_gain_q10: float | None = None
    capital_gain_q50: float | None = None
    capital_gain_q90: float | None = None
    calibrated_lower: float | None = None
    calibrated_upper: float | None = None
    mfe_q50: float | None = None
    mae_q50: float | None = None

    @model_validator(mode="after")
    def validate_values(self) -> Self:
        values = tuple(self.model_dump(mode="python").values())
        if not any(value is not None for value in values):
            raise ValueError("research model evidence requires at least one value")
        if (
            self.capital_gain_q10 is not None
            and self.capital_gain_q50 is not None
            and self.capital_gain_q10 > self.capital_gain_q50
        ):
            raise ValueError("research return quantiles must be monotonic")
        if (
            self.capital_gain_q50 is not None
            and self.capital_gain_q90 is not None
            and self.capital_gain_q50 > self.capital_gain_q90
        ):
            raise ValueError("research return quantiles must be monotonic")
        if (
            self.calibrated_lower is not None
            and self.calibrated_upper is not None
            and self.calibrated_lower > self.calibrated_upper
        ):
            raise ValueError("research calibrated interval is inverted")
        return self


class ResearchModelEvidence(_StrictModel):
    scope: ResearchEvidenceScope
    symbol: UsSymbol
    horizon: Horizon
    as_of: UtcDatetime
    values: ResearchModelValues
    price_projection: ResearchPriceProjection | None = None
    tasks: tuple[TaskForecastEvidence, ...] = Field(min_length=1)
    sample_abstain_reasons: tuple[RejectionReason, ...] = ()
    experimental_warning: DisplayText | None = None
    package_id: Identifier | None = None
    blind_evidence_scope: BlindEvidenceScope | None = None
    shadow_window_progress: ShadowWindowProgress | None = None

    @model_validator(mode="after")
    def validate_tasks(self) -> Self:
        if any(task.scope is not self.scope for task in self.tasks):
            raise ValueError("research evidence task scope must match its container")
        if len({(task.task, task.horizon) for task in self.tasks}) != len(self.tasks):
            raise ValueError("research evidence tasks must be unique")
        if self.scope is ResearchEvidenceScope.CHALLENGER:
            if self.experimental_warning is None:
                raise ValueError("challenger evidence requires an experimental warning")
        elif self.scope is ResearchEvidenceScope.SHADOW:
            if (
                self.experimental_warning is None
                or self.package_id is None
                or self.blind_evidence_scope is None
                or self.shadow_window_progress is None
            ):
                raise ValueError(
                    "shadow evidence requires a warning, package, blind scope, and window progress"
                )
            if self.blind_evidence_scope == "blind_complete":
                raise ValueError("shadow blind evidence must be incomplete")
        elif self.experimental_warning is not None:
            raise ValueError("verified evidence cannot carry a challenger warning")
        if self.scope is not ResearchEvidenceScope.SHADOW and any(
            value is not None
            for value in (
                self.package_id,
                self.blind_evidence_scope,
                self.shadow_window_progress,
            )
        ):
            raise ValueError("only shadow evidence may carry shadow identity")
        return self


class ForecastUnavailable(_StrictModel):
    availability: Literal["unavailable"] = "unavailable"
    symbol: UsSymbol
    as_of: UtcDatetime
    serving_status: ServingStatus
    effective_abstain: Literal[True] = True
    not_generated_reason: Identifier
    affected_inputs: tuple[Identifier, ...] = Field(min_length=1)
    last_available_snapshot: ResolvedSnapshot | None
    requested_horizon: Horizon | None = None
    verified_research: tuple[ResearchModelEvidence, ...] = Field(
        default=(),
    )
    challenger_preview: tuple[ResearchModelEvidence, ...] = Field(
        default=(),
    )
    shadow_preview: tuple[ResearchModelEvidence, ...] | None = None

    @model_validator(mode="after")
    def validate_inputs(self) -> Self:
        if len(set(self.affected_inputs)) != len(self.affected_inputs):
            raise ValueError("affected inputs must be unique")
        groups = (
            (
                "verified",
                ResearchEvidenceScope.VERIFIED,
                self.verified_research,
            ),
            (
                "challenger",
                ResearchEvidenceScope.CHALLENGER,
                self.challenger_preview,
            ),
            (
                "shadow",
                ResearchEvidenceScope.SHADOW,
                self.shadow_preview or (),
            ),
        )
        for label, expected_scope, evidence_group in groups:
            horizons = tuple(evidence.horizon for evidence in evidence_group)
            if len(set(horizons)) != len(horizons):
                raise ValueError(f"{label} research horizons must be unique")
            if any(
                evidence.scope is not expected_scope
                or evidence.symbol != self.symbol
                or evidence.as_of != self.as_of
                for evidence in evidence_group
            ):
                raise ValueError(f"{label} research identity must match the response")
            anchors = {
                (
                    projection.reference_price,
                    projection.reference_price_at,
                    projection.currency,
                )
                for evidence in evidence_group
                if (projection := evidence.price_projection) is not None
            }
            if len(anchors) > 1:
                raise ValueError(f"{label} research price anchors must match")
        return self


ForecastResult = Annotated[
    ForecastAvailable | ForecastUnavailable,
    Field(discriminator="availability"),
]


class MarketProxy(_StrictModel):
    symbol: UsSymbol
    name: DisplayText
    regime: RegimeLabel
    regime_probability: float | None = Field(default=None, ge=0, le=1)
    direction: Literal["up", "flat", "down"] | None
    unavailable_reason: Identifier | None
    last_close: float | None = Field(default=None, gt=0)
    session_return: float
    trend: tuple[float, ...]
    as_of: UtcDatetime
    serving_status: ServingStatus
    verified_research_by_horizon: tuple[ResearchModelEvidence, ...] | None = None
    challenger_preview_by_horizon: tuple[ResearchModelEvidence, ...] | None = None
    shadow_preview_by_horizon: tuple[ResearchModelEvidence, ...] | None = None

    @model_validator(mode="after")
    def validate_prediction_state(self) -> Self:
        values_available = (
            self.regime_probability is not None,
            self.direction is not None,
        )
        if values_available[0] != values_available[1]:
            raise ValueError("market-proxy prediction values must be atomic")
        if all(values_available) and self.unavailable_reason is not None:
            raise ValueError(
                "available market-proxy predictions cannot have an unavailable reason"
            )
        if not any(values_available) and self.unavailable_reason is None:
            raise ValueError(
                "unavailable market-proxy predictions require an unavailable reason"
            )
        verified_research = self.verified_research_by_horizon or ()
        challenger_research = self.challenger_preview_by_horizon or ()
        shadow_research = self.shadow_preview_by_horizon or ()
        for evidence in verified_research:
            if (
                evidence.scope is not ResearchEvidenceScope.VERIFIED
                or evidence.symbol != self.symbol
            ):
                raise ValueError("market-proxy verified research identity mismatch")
        for evidence in challenger_research:
            if (
                evidence.scope is not ResearchEvidenceScope.CHALLENGER
                or evidence.symbol != self.symbol
            ):
                raise ValueError("market-proxy challenger research identity mismatch")
        for evidence in shadow_research:
            if (
                evidence.scope is not ResearchEvidenceScope.SHADOW
                or evidence.symbol != self.symbol
            ):
                raise ValueError("market-proxy shadow research identity mismatch")
        verified_horizons = tuple(evidence.horizon for evidence in verified_research)
        challenger_horizons = tuple(
            evidence.horizon for evidence in challenger_research
        )
        shadow_horizons = tuple(evidence.horizon for evidence in shadow_research)
        if len(set(verified_horizons)) != len(verified_horizons):
            raise ValueError("market-proxy verified research horizons must be unique")
        if len(set(challenger_horizons)) != len(challenger_horizons):
            raise ValueError("market-proxy challenger research horizons must be unique")
        if len(set(shadow_horizons)) != len(shadow_horizons):
            raise ValueError("market-proxy shadow research horizons must be unique")
        return self


class RegimeDistribution(_StrictModel):
    regime: RegimeLabel
    count: int = Field(ge=0)
    fraction: float = Field(ge=0, le=1)


class OverviewComparisonSnapshot(_StrictModel):
    snapshot_id: Identifier
    revision_no: int = Field(ge=1)
    as_of: UtcDatetime
    generated_at: UtcDatetime

    @model_validator(mode="after")
    def validate_generation_time(self) -> Self:
        if self.generated_at < self.as_of:
            raise ValueError("comparison snapshot generated_at cannot precede as_of")
        return self


class OverviewHistoryPoint(_StrictModel):
    snapshot_id: Identifier
    revision_no: int = Field(ge=1)
    as_of: UtcDatetime
    market_breadth: float = Field(ge=0, le=1)
    uptrend_fraction: float = Field(ge=0, le=1)
    median_session_return: float | None = None
    return_dispersion: float | None = Field(default=None, ge=0)
    volume_anomaly_count: int | None = Field(default=None, ge=0)


class OverviewMarketPoint(_StrictModel):
    symbol: UsSymbol
    name: DisplayText
    asset_type: Identifier
    sector: Identifier
    regime: RegimeLabel
    previous_regime: RegimeLabel | None = None
    last_close: float | None = Field(default=None, gt=0)
    session_return: float | None = None
    return_5: float | None = None
    return_20: float | None = None
    atr_pct_20: float | None = Field(default=None, ge=0)
    channel_position_20: float | None = None
    channel_width_20: float | None = Field(default=None, ge=0)
    volume_z_20: float | None = None
    breakout_up_rule: bool | None = None
    breakout_down_rule: bool | None = None
    intraday_available: bool | None = None
    regime_changed: bool | None = None

    @model_validator(mode="after")
    def validate_comparison(self) -> Self:
        if self.previous_regime is None:
            if self.regime_changed is not None:
                raise ValueError("regime_changed requires a previous regime")
        elif self.regime_changed != (self.previous_regime is not self.regime):
            raise ValueError("regime_changed must match the two regimes")
        return self


class RegimeTransitionCell(_StrictModel):
    previous_regime: RegimeLabel
    current_regime: RegimeLabel
    count: int = Field(ge=1)


class SectorScorecard(_StrictModel):
    sector: Identifier
    sample_count: int = Field(ge=1)
    median_session_return: float | None = None
    median_return_5: float | None = None
    median_return_20: float | None = None
    uptrend_fraction: float = Field(ge=0, le=1)
    previous_sample_count: int | None = Field(default=None, ge=5)
    previous_uptrend_fraction: float | None = Field(default=None, ge=0, le=1)
    uptrend_change: float | None = Field(default=None, ge=-1, le=1)
    median_atr_pct_20: float | None = Field(default=None, ge=0)
    volume_anomaly_count: int | None = Field(default=None, ge=0)
    breakout_up_count: int | None = Field(default=None, ge=0)
    breakout_down_count: int | None = Field(default=None, ge=0)
    leader_symbol: UsSymbol | None = None
    laggard_symbol: UsSymbol | None = None

    @model_validator(mode="after")
    def validate_comparison(self) -> Self:
        comparison = (
            self.previous_sample_count,
            self.previous_uptrend_fraction,
            self.uptrend_change,
        )
        if any(value is None for value in comparison) and any(
            value is not None for value in comparison
        ):
            raise ValueError("sector comparison values must be atomic")
        if self.uptrend_change is not None and not math.isclose(
            self.uptrend_change,
            self.uptrend_fraction - self.previous_uptrend_fraction,  # type: ignore[operator]
            abs_tol=1e-9,
        ):
            raise ValueError("sector uptrend change must match the two fractions")
        return self


class CandidateRankChange(_StrictModel):
    symbol: UsSymbol
    name: DisplayText
    status: Literal["entered", "exited", "retained"]
    current_rank: int | None = Field(default=None, ge=1)
    previous_rank: int | None = Field(default=None, ge=1)
    rank_change: int | None = None

    @model_validator(mode="after")
    def validate_ranks(self) -> Self:
        if self.status == "entered":
            valid = self.current_rank is not None and self.previous_rank is None
        elif self.status == "exited":
            valid = self.current_rank is None and self.previous_rank is not None
        else:
            valid = self.current_rank is not None and self.previous_rank is not None
        if not valid:
            raise ValueError("candidate ranks must match churn status")
        expected_change = (
            self.previous_rank - self.current_rank  # type: ignore[operator]
            if self.status == "retained"
            else None
        )
        if self.rank_change != expected_change:
            raise ValueError("rank change must equal previous rank minus current rank")
        return self


class OverviewCandidateChurn(_StrictModel):
    changes: tuple[CandidateRankChange, ...] = ()

    @model_validator(mode="after")
    def validate_symbols(self) -> Self:
        symbols = tuple(change.symbol for change in self.changes)
        if len(set(symbols)) != len(symbols):
            raise ValueError("candidate churn symbols must be unique")
        return self


class OverviewEvidenceCoverage(_StrictModel):
    total_symbols: int = Field(ge=0)
    comparable_symbols: int = Field(ge=0)
    rule_evidence_count: int = Field(ge=0)
    session_return_count: int = Field(ge=0)
    return_5_count: int = Field(ge=0)
    return_20_count: int = Field(ge=0)
    atr_count: int = Field(ge=0)
    channel_count: int = Field(ge=0)
    volume_z_count: int = Field(ge=0)
    intraday_available_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        counts = (
            self.comparable_symbols,
            self.rule_evidence_count,
            self.session_return_count,
            self.return_5_count,
            self.return_20_count,
            self.atr_count,
            self.channel_count,
            self.volume_z_count,
            self.intraday_available_count,
        )
        if any(count > self.total_symbols for count in counts):
            raise ValueError("evidence coverage cannot exceed total symbols")
        return self


class SectorUptrendChange(_StrictModel):
    sector: Identifier
    sample_count: int = Field(ge=5)
    previous_sample_count: int = Field(ge=5)
    uptrend_fraction: float = Field(ge=0, le=1)
    previous_uptrend_fraction: float = Field(ge=0, le=1)
    change: float = Field(ge=-1, le=1)

    @model_validator(mode="after")
    def validate_change(self) -> Self:
        if not math.isclose(
            self.change,
            self.uptrend_fraction - self.previous_uptrend_fraction,
            abs_tol=1e-9,
        ):
            raise ValueError("sector uptrend change must match the two fractions")
        return self


class OverviewInternalEvidence(_StrictModel):
    comparison_snapshot: OverviewComparisonSnapshot | None = None
    history: tuple[OverviewHistoryPoint, ...] = Field(default=(), max_length=5)
    advancers: int | None = Field(default=None, ge=0)
    decliners: int | None = Field(default=None, ge=0)
    unchanged: int | None = Field(default=None, ge=0)
    regime_changes: int | None = Field(default=None, ge=0)
    to_uptrend: int | None = Field(default=None, ge=0)
    to_downtrend: int | None = Field(default=None, ge=0)
    breakout_up: int | None = Field(default=None, ge=0)
    breakout_down: int | None = Field(default=None, ge=0)
    volume_anomalies: int | None = Field(default=None, ge=0)
    median_session_return: float | None = None
    return_dispersion: float | None = Field(default=None, ge=0)
    market_points: tuple[OverviewMarketPoint, ...] = ()
    regime_transition_cells: tuple[RegimeTransitionCell, ...] = ()
    sector_scorecards: tuple[SectorScorecard, ...] = ()
    verified_candidate_churn: OverviewCandidateChurn | None = None
    challenger_candidate_churn: OverviewCandidateChurn | None = None
    evidence_coverage: OverviewEvidenceCoverage | None = None
    current_anomaly_snapshot: OverviewAnomalySnapshot | None = None
    previous_anomaly_snapshot: OverviewAnomalySnapshot | None = None
    sector_uptrend_changes: tuple[SectorUptrendChange, ...] = ()

    @model_validator(mode="after")
    def validate_evidence_groups(self) -> Self:
        for values, label in (
            ((self.advancers, self.decliners, self.unchanged), "advance counts"),
            (
                (self.regime_changes, self.to_uptrend, self.to_downtrend),
                "regime transition counts",
            ),
            ((self.breakout_up, self.breakout_down), "breakout counts"),
            (
                (self.median_session_return, self.return_dispersion),
                "cross-sectional return statistics",
            ),
        ):
            if any(value is None for value in values) and any(
                value is not None for value in values
            ):
                raise ValueError(f"{label} must be atomic")
        history_keys = tuple((point.as_of, point.revision_no) for point in self.history)
        if len(set(history_keys)) != len(history_keys):
            raise ValueError("overview history points must be unique")
        if tuple(point.as_of for point in self.history) != tuple(
            sorted(point.as_of for point in self.history)
        ):
            raise ValueError("overview history points must be chronological")
        sectors = tuple(item.sector for item in self.sector_uptrend_changes)
        if len(set(sectors)) != len(sectors):
            raise ValueError("sector uptrend changes must be unique")
        point_symbols = tuple(item.symbol for item in self.market_points)
        if len(set(point_symbols)) != len(point_symbols):
            raise ValueError("overview market points must be unique")
        transition_keys = tuple(
            (item.previous_regime, item.current_regime)
            for item in self.regime_transition_cells
        )
        if len(set(transition_keys)) != len(transition_keys):
            raise ValueError("overview regime transition cells must be unique")
        scorecard_sectors = tuple(item.sector for item in self.sector_scorecards)
        if len(set(scorecard_sectors)) != len(scorecard_sectors):
            raise ValueError("overview sector scorecards must be unique")
        if self.evidence_coverage is not None:
            if self.evidence_coverage.total_symbols != len(self.market_points):
                raise ValueError("evidence coverage must match market point count")
            if sum(item.count for item in self.regime_transition_cells) != (
                self.evidence_coverage.comparable_symbols
            ):
                raise ValueError(
                    "regime transitions must reconcile to comparable symbols"
                )
        if self.comparison_snapshot is None and any(
            value is not None
            for value in (self.regime_changes, self.to_uptrend, self.to_downtrend)
        ):
            raise ValueError("regime transitions require a comparison snapshot")
        if self.comparison_snapshot is None and (
            self.regime_transition_cells
            or self.verified_candidate_churn is not None
            or self.challenger_candidate_churn is not None
            or self.previous_anomaly_snapshot is not None
        ):
            raise ValueError("comparison evidence requires a comparison snapshot")
        return self


class SectorRegimeCell(_StrictModel):
    sector: Identifier
    regime: RegimeLabel
    count: int = Field(ge=0)
    fraction: float = Field(ge=0, le=1)


class RejectionReasonCount(_StrictModel):
    reason: RejectionReason
    count: int = Field(ge=1)


class RuleEvidence(_StrictModel):
    """Point-in-time market and rule evidence that remains usable while abstaining."""

    last_close: float = Field(gt=0)
    session_return: float | None = None
    return_5: float | None = None
    return_20: float | None = None
    atr_pct_20: float | None = Field(default=None, ge=0)
    channel_position_20: float | None = None
    channel_width_20: float | None = Field(default=None, ge=0)
    volume_z_20: float | None = None
    breakout_up_rule: bool
    breakout_down_rule: bool
    intraday_available: bool


class ResearchOpportunityRow(_StrictModel):
    symbol: UsSymbol
    name: DisplayText
    asset_type: Identifier
    sector: Identifier
    regime: RegimeLabel
    horizon: Horizon
    rule_evidence: RuleEvidence
    verified_research: ResearchModelEvidence | None = None
    challenger_preview: ResearchModelEvidence | None = None
    shadow_preview: ResearchModelEvidence | None = None
    verified_research_by_horizon: tuple[ResearchModelEvidence, ...] = Field(
        default=(),
    )
    challenger_preview_by_horizon: tuple[ResearchModelEvidence, ...] = Field(
        default=(),
    )
    shadow_preview_by_horizon: tuple[ResearchModelEvidence, ...] | None = None
    rank_basis: tuple[DisplayText, ...] = Field(min_length=1)
    as_of: UtcDatetime

    @model_validator(mode="after")
    def validate_layers(self) -> Self:
        if self.verified_research is not None:
            if self.verified_research.scope is not ResearchEvidenceScope.VERIFIED:
                raise ValueError("research opportunities require verified evidence")
            if (
                self.verified_research.symbol != self.symbol
                or self.verified_research.horizon != self.horizon
            ):
                raise ValueError("research opportunity evidence identity mismatch")
        if self.challenger_preview is not None:
            if self.challenger_preview.scope is not ResearchEvidenceScope.CHALLENGER:
                raise ValueError("challenger opportunity requires challenger evidence")
            if (
                self.challenger_preview.symbol != self.symbol
                or self.challenger_preview.horizon != self.horizon
            ):
                raise ValueError("challenger opportunity evidence identity mismatch")
        if self.shadow_preview is not None:
            if self.shadow_preview.scope is not ResearchEvidenceScope.SHADOW:
                raise ValueError("shadow opportunity requires shadow evidence")
            if (
                self.shadow_preview.symbol != self.symbol
                or self.shadow_preview.horizon != self.horizon
            ):
                raise ValueError("shadow opportunity evidence identity mismatch")
        for evidence in self.verified_research_by_horizon:
            if (
                evidence.scope is not ResearchEvidenceScope.VERIFIED
                or evidence.symbol != self.symbol
            ):
                raise ValueError("research opportunity horizon evidence mismatch")
        for evidence in self.challenger_preview_by_horizon:
            if (
                evidence.scope is not ResearchEvidenceScope.CHALLENGER
                or evidence.symbol != self.symbol
            ):
                raise ValueError("challenger opportunity horizon evidence mismatch")
        for evidence in self.shadow_preview_by_horizon or ():
            if (
                evidence.scope is not ResearchEvidenceScope.SHADOW
                or evidence.symbol != self.symbol
            ):
                raise ValueError("shadow opportunity horizon evidence mismatch")
        verified_horizons = tuple(
            evidence.horizon for evidence in self.verified_research_by_horizon
        )
        challenger_horizons = tuple(
            evidence.horizon for evidence in self.challenger_preview_by_horizon
        )
        shadow_horizons = tuple(
            evidence.horizon for evidence in self.shadow_preview_by_horizon or ()
        )
        if len(set(verified_horizons)) != len(verified_horizons):
            raise ValueError("verified opportunity horizons must be unique")
        if len(set(challenger_horizons)) != len(challenger_horizons):
            raise ValueError("challenger opportunity horizons must be unique")
        if len(set(shadow_horizons)) != len(shadow_horizons):
            raise ValueError("shadow opportunity horizons must be unique")
        return self


class ScreenRow(_StrictModel):
    symbol: UsSymbol
    name: DisplayText
    asset_type: Identifier
    sector: Identifier
    regime: RegimeLabel
    horizon: Horizon
    executable_up_probability: float | None = Field(default=None, ge=0, le=1)
    breakout_up_probability: float | None = Field(default=None, ge=0, le=1)
    breakout_down_probability: float | None = Field(default=None, ge=0, le=1)
    executable_q50: float | None
    executable_q10: float | None
    forecast_up_probability: float | None = Field(default=None, ge=0, le=1)
    capital_gain_q50: float | None
    capital_gain_q10: float | None
    interval_width: float | None = Field(default=None, ge=0)
    similar_case_count: int = Field(ge=0)
    confidence_level: ConfidenceLevel
    data_freshness_seconds: int = Field(ge=0)
    serving_status: ServingStatus
    effective_abstain: bool
    abstain_reasons: tuple[RejectionReason, ...]
    model_version: VersionId | None
    as_of: UtcDatetime
    rule_evidence: RuleEvidence | None = None
    verified_research: ResearchModelEvidence | None = None
    challenger_preview: ResearchModelEvidence | None = None
    shadow_preview: ResearchModelEvidence | None = None

    @model_validator(mode="after")
    def validate_rankable_values(self) -> Self:
        if (
            self.regime is RegimeLabel.INVALID
            or self.confidence_level is ConfidenceLevel.UNAVAILABLE
        ) and not self.effective_abstain:
            raise ValueError(
                "invalid regime and unavailable confidence require effective abstain"
            )
        if (
            self.serving_status in HARD_ABSTAIN_SERVING_STATUSES
            and not self.effective_abstain
        ):
            raise ValueError(
                "stale, offline, and unavailable screen rows require effective abstain"
            )
        values = (
            self.executable_up_probability,
            self.breakout_up_probability,
            self.breakout_down_probability,
            self.executable_q50,
            self.executable_q10,
            self.forecast_up_probability,
            self.capital_gain_q50,
            self.capital_gain_q10,
            self.interval_width,
        )
        if self.effective_abstain and any(value is not None for value in values):
            raise ValueError("abstain screen rows cannot publish ranking values")
        if not self.effective_abstain and any(value is None for value in values):
            raise ValueError("rankable screen rows require complete values")
        if self.effective_abstain != bool(self.abstain_reasons):
            raise ValueError("screen-row abstention and reasons must agree")
        if not self.effective_abstain and self.model_version is None:
            raise ValueError("rankable screen rows require a model version")
        if len(set(self.abstain_reasons)) != len(self.abstain_reasons):
            raise ValueError("screen-row abstain reasons must be unique")
        if (
            self.capital_gain_q10 is not None
            and self.capital_gain_q50 is not None
            and self.capital_gain_q10 > self.capital_gain_q50
        ):
            raise ValueError("screen-row capital-gain quantiles must be monotonic")
        for evidence, expected_scope, label in (
            (
                self.verified_research,
                ResearchEvidenceScope.VERIFIED,
                "verified",
            ),
            (
                self.challenger_preview,
                ResearchEvidenceScope.CHALLENGER,
                "challenger",
            ),
            (
                self.shadow_preview,
                ResearchEvidenceScope.SHADOW,
                "shadow",
            ),
        ):
            if evidence is not None and (
                evidence.scope is not expected_scope
                or evidence.symbol != self.symbol
                or evidence.horizon != self.horizon
                or evidence.as_of != self.as_of
            ):
                raise ValueError(f"screen-row {label} research identity mismatch")
        return self


class FailedTaskRef(_StrictModel):
    task: Identifier
    horizon: Horizon


class CandidateEvaluationSummary(_StrictModel):
    run_id: Identifier
    model_version: VersionId
    calibration_version: VersionId
    generated_at: UtcDatetime
    passed_tasks: int = Field(ge=0)
    total_tasks: int = Field(ge=1)
    all_gates_passed: bool
    failed_tasks: tuple[DisplayText, ...]
    failed_task_refs: tuple[FailedTaskRef, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    data_quality_status: Identifier | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    label_health_status: Identifier | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    promotion_window_status: Identifier | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    execution_readiness: ExecutionReadiness | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    failure_codes: tuple[Identifier, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.passed_tasks > self.total_tasks:
            raise ValueError("passed tasks cannot exceed total tasks")
        if self.all_gates_passed != (self.passed_tasks == self.total_tasks):
            raise ValueError("all-gates flag must agree with task counts")
        if len(self.failed_tasks) != self.total_tasks - self.passed_tasks:
            raise ValueError("failed-task list must reconcile to task counts")
        return self


class OverviewReadModel(_StrictModel):
    proxies: tuple[MarketProxy, ...]
    top_opportunities: tuple[ScreenRow, ...]
    research_opportunities: tuple[ResearchOpportunityRow, ...] = Field(
        default=(),
    )
    challenger_opportunities: tuple[ResearchOpportunityRow, ...] = Field(
        default=(),
    )
    shadow_opportunities: tuple[ResearchOpportunityRow, ...] | None = None
    regime_distribution: tuple[RegimeDistribution, ...]
    sector_regime_matrix: tuple[SectorRegimeCell, ...]
    market_breadth: float = Field(ge=0, le=1)
    abstain_count: int = Field(ge=0)
    abstain_reasons: tuple[RejectionReasonCount, ...]
    data_confidence: float = Field(ge=0, le=1)
    batch_generated_at: UtcDatetime
    total_inference_latency_ms: float = Field(ge=0)
    candidate_summary: CandidateEvaluationSummary | None = None
    internal_evidence: OverviewInternalEvidence | None = None

    @model_validator(mode="after")
    def validate_aggregates(self) -> Self:
        proxy_symbols = tuple(proxy.symbol for proxy in self.proxies)
        if len(set(proxy_symbols)) != len(proxy_symbols):
            raise ValueError("overview proxy symbols must be unique")
        regimes = tuple(bucket.regime for bucket in self.regime_distribution)
        if len(set(regimes)) != len(regimes):
            raise ValueError("overview regime buckets must be unique")
        if self.regime_distribution and not math.isclose(
            sum(bucket.fraction for bucket in self.regime_distribution),
            1.0,
            abs_tol=1e-9,
        ):
            raise ValueError("overview regime fractions must sum to 1")
        sector_keys = tuple(
            (cell.sector, cell.regime) for cell in self.sector_regime_matrix
        )
        if len(set(sector_keys)) != len(sector_keys):
            raise ValueError("sector-regime cells must be unique")
        sectors = {cell.sector for cell in self.sector_regime_matrix}
        for sector in sectors:
            fraction = sum(
                cell.fraction
                for cell in self.sector_regime_matrix
                if cell.sector == sector
            )
            if not math.isclose(fraction, 1.0, abs_tol=1e-9):
                raise ValueError("sector regime fractions must sum to 1")
        if any(
            row.effective_abstain
            or row.serving_status in HARD_ABSTAIN_SERVING_STATUSES
            or row.regime is RegimeLabel.INVALID
            or row.confidence_level is ConfidenceLevel.UNAVAILABLE
            for row in self.top_opportunities
        ):
            raise ValueError(
                "top opportunities cannot contain abstain or hard-state rows"
            )
        return self


class _PageModel(_StrictModel):
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    sort_key: Identifier
    sort_direction: SortDirection

    @model_validator(mode="after")
    def validate_page_bounds(self) -> Self:
        items = self.__dict__.get("items")
        if isinstance(items, tuple) and self.total < len(items):
            raise ValueError("page total cannot be smaller than returned items")
        if isinstance(items, tuple) and len(items) > self.page_size:
            raise ValueError("returned items cannot exceed page_size")
        last_page = max(1, (self.total + self.page_size - 1) // self.page_size)
        if self.page > last_page:
            raise ValueError("page exceeds the available total")
        return self


class ScreenPage(_PageModel):
    items: tuple[ScreenRow, ...]
    prediction_basis: PredictionBasis
    return_basis: ScreenReturnBasis


class InstrumentMetadata(_StrictModel):
    symbol: UsSymbol
    name: DisplayText
    asset_type: Identifier
    sector: Identifier
    currency: Currency
    exchange: Identifier
    effective_from: UtcDatetime
    effective_to: UtcDatetime | None
    universe_version: VersionId
    universe_role: Identifier | None = None
    observed_at: UtcDatetime | None = None
    metadata_version: VersionId | None = None

    @model_validator(mode="after")
    def validate_effective_range(self) -> Self:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("instrument effective_to must follow effective_from")
        return self


class InstrumentPage(_PageModel):
    items: tuple[InstrumentMetadata, ...]


class HistoryBar(_StrictModel):
    at: UtcDatetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)
    regime: RegimeLabel
    data_gap: bool

    @model_validator(mode="after")
    def validate_ohlc(self) -> Self:
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("history high is below an OHLC value")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("history low is above an OHLC value")
        return self


class HistoryEvent(_StrictModel):
    event_id: Identifier
    occurred_at: UtcDatetime
    event_type: Identifier
    label: DisplayText
    details: tuple[DisplayText, ...]
    label_ref: LocalizableText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    details_refs: tuple[LocalizableText, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class HistoryGap(_StrictModel):
    start_at: UtcDatetime
    end_at: UtcDatetime
    reason: Identifier

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end_at <= self.start_at:
            raise ValueError("history gap end must follow start")
        return self


class InstrumentHistory(_StrictModel):
    symbol: UsSymbol
    timeframe: Identifier
    adjustment: Identifier
    bars: tuple[HistoryBar, ...]
    events: tuple[HistoryEvent, ...]
    gaps: tuple[HistoryGap, ...]

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        bar_times = tuple(bar.at for bar in self.bars)
        event_times = tuple(event.occurred_at for event in self.events)
        if bar_times != tuple(sorted(bar_times)):
            raise ValueError("history bars must be ordered")
        if event_times != tuple(sorted(event_times)):
            raise ValueError("history events must be ordered")
        return self


class SnapshotContextReadModel(_StrictModel):
    symbol_count: int = Field(ge=0)
    instruments: tuple[InstrumentMetadata, ...] = ()


class HistoryBatchItem(_StrictModel):
    symbol: UsSymbol
    status: Literal["available", "unavailable"]
    history: InstrumentHistory | None = None
    error_code: Identifier | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.status == "available":
            if (
                self.history is None
                or self.history.symbol != self.symbol
                or self.error_code is not None
            ):
                raise ValueError(
                    "available history requires matching history and no error"
                )
        elif self.history is not None or self.error_code is None:
            raise ValueError("unavailable history requires an error and no history")
        return self


class HistoryBatchReadModel(_StrictModel):
    items: tuple[HistoryBatchItem, ...]


class MetricEvidence(_StrictModel):
    name: Identifier
    value: float | None
    unavailable_reason: Identifier | None

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if (self.value is None) == (self.unavailable_reason is None):
            raise ValueError(
                "metric evidence requires exactly one value or unavailable reason"
            )
        return self


class DiagnosticSliceEvidence(_StrictModel):
    dimension: Literal[
        "year", "regime", "sector", "asset_type", "liquidity", "confidence"
    ]
    value: Identifier
    sample_count: int = Field(ge=0)
    metrics: tuple[MetricEvidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        names = tuple(metric.name for metric in self.metrics)
        if len(set(names)) != len(names):
            raise ValueError("diagnostic-slice metric names must be unique")
        return self


class FoldMetrics(_StrictModel):
    fold_id: Identifier
    evaluation_start: UtcDatetime
    evaluation_end: UtcDatetime
    sample_count: int = Field(ge=1)
    brier_score: float = Field(ge=0)
    brier_skill: float = Field(le=1)
    pr_auc: float = Field(ge=0, le=1)
    expected_calibration_error: float = Field(ge=0, le=1)
    pinball_loss: float = Field(ge=0)
    ranking_ic: float = Field(ge=-1, le=1)
    raw_interval_coverage: float = Field(ge=0, le=1)
    raw_interval_width: float = Field(ge=0)
    interval_coverage: float = Field(ge=0, le=1)
    interval_width: float = Field(ge=0)
    tail_failure_rate: float = Field(ge=0, le=1)
    survival_brier: float = Field(ge=0)
    concordance_index: float = Field(ge=0, le=1)
    worst_window: bool

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.evaluation_end <= self.evaluation_start:
            raise ValueError("fold evaluation end must follow start")
        return self


class SurvivalCalibrationPoint(_StrictModel):
    horizon: Literal[5, 20]
    predicted_exit_probability: float = Field(ge=0, le=1)
    observed_exit_frequency: float = Field(ge=0, le=1)
    sample_count: int = Field(ge=1)


class CalibrationBin(_StrictModel):
    lower_bound: float = Field(ge=0, le=1)
    upper_bound: float = Field(ge=0, le=1)
    mean_prediction: float = Field(ge=0, le=1)
    observed_frequency: float = Field(ge=0, le=1)
    sample_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_bin(self) -> Self:
        if self.lower_bound >= self.upper_bound:
            raise ValueError("calibration-bin lower bound must precede upper bound")
        if not self.lower_bound <= self.mean_prediction <= self.upper_bound:
            raise ValueError("mean prediction must fall inside calibration bin")
        return self


class EconomicBacktestPoint(_StrictModel):
    fold_id: Identifier
    at: UtcDatetime
    equity: float = Field(ge=0)
    drawdown: float = Field(ge=-1, le=0)
    cash_fraction: float = Field(ge=0, le=1)
    turnover: float = Field(ge=0)
    cumulative_cost: float = Field(ge=0)


class IntervalCalibrationEvidence(_StrictModel):
    method: Identifier
    target_coverage: float = Field(gt=0, lt=1)
    status: Literal["available", "unavailable"]
    unavailable_reason: Identifier | None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if (self.status == "available") == (self.unavailable_reason is not None):
            raise ValueError(
                "interval calibration status and unavailable reason must agree"
            )
        return self


class DeflatedSharpeEvidence(_StrictModel):
    observed_sharpe: float
    expected_maximum_sharpe: float
    status: Literal[
        "available",
        "insufficient_trial_variance",
        "insufficient_trial_coverage",
    ]
    probability_sharpe_is_genuine: float | None = Field(default=None, ge=0, le=1)
    attempted_trials: int = Field(ge=1)
    successful_trials_with_sharpe: int = Field(ge=0)
    attempted_sharpe_mean: float
    attempted_sharpe_std: float = Field(ge=0)
    observation_count: int = Field(ge=3)
    assumptions: tuple[DisplayText, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.successful_trials_with_sharpe > self.attempted_trials:
            raise ValueError("successful DSR trials cannot exceed attempted trials")
        if (self.status == "available") != (
            self.probability_sharpe_is_genuine is not None
        ):
            raise ValueError("DSR probability must agree with availability status")
        if len(set(self.assumptions)) != len(self.assumptions):
            raise ValueError("DSR assumptions must be unique")
        return self


class PboEvidence(_StrictModel):
    status: Literal["available", "insufficient_blocks", "combination_budget_exceeded"]
    probability: float | None = Field(default=None, ge=0, le=1)
    block_count: int = Field(ge=1)
    combination_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if (self.status == "available") != (self.probability is not None):
            raise ValueError("PBO probability must agree with availability status")
        return self


class TrialLedgerEvidence(_StrictModel):
    registered: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    aborted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    running: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        terminal_and_running = (
            self.succeeded + self.failed + self.aborted + self.rejected + self.running
        )
        if terminal_and_running != self.registered:
            raise ValueError("trial counts must reconcile to registered trials")
        return self


class EvaluationWindowEvidence(_StrictModel):
    window_id: Identifier
    role: Literal["outer_test", "lockbox", "forward_shadow"]
    scope: Literal["production", "research_only", "synthetic_offline"]
    consumption_count: int = Field(ge=0, le=1)
    consumed_at: UtcDatetime | None

    @model_validator(mode="after")
    def validate_consumption(self) -> Self:
        if (self.consumption_count > 0) != (self.consumed_at is not None):
            raise ValueError("window consumption count and timestamp must agree")
        return self


class CostSensitivityEvidence(_StrictModel):
    per_side_bps: int = Field(ge=0)
    estimated_total_cost: float = Field(ge=0)
    net_portfolio_return: float


class CapacitySensitivityEvidence(_StrictModel):
    portfolio_scale: float = Field(gt=0)
    fold_count: int = Field(ge=1)
    capacity_exceeded_count: int = Field(ge=0)


class BacktestReadModel(_StrictModel):
    task: Identifier
    horizon: Horizon
    asset_group: Identifier
    fold_metrics: tuple[FoldMetrics, ...] = Field(min_length=1)
    calibration_bins: tuple[CalibrationBin, ...] = Field(min_length=1)
    interval_calibration: IntervalCalibrationEvidence
    survival_calibration: tuple[SurvivalCalibrationPoint, ...] = Field(min_length=2)
    diagnostic_slices: tuple[DiagnosticSliceEvidence, ...] = Field(min_length=1)
    deflated_sharpe: DeflatedSharpeEvidence
    pbo: PboEvidence
    trial_ledger: TrialLedgerEvidence
    evaluation_windows: tuple[EvaluationWindowEvidence, ...] = Field(min_length=2)
    cost_sensitivity: tuple[CostSensitivityEvidence, ...] = Field(min_length=1)
    capacity_sensitivity: tuple[CapacitySensitivityEvidence, ...] = Field(min_length=1)
    economic_series: tuple[EconomicBacktestPoint, ...] = Field(min_length=1)
    model_version: VersionId
    strategy_version: VersionId
    universe_version: VersionId
    split_version: VersionId
    evaluation_window_id: Identifier
    cost_scenario: Identifier
    sample_count: int = Field(ge=1)
    evidence_scope: Literal["promotion_eligible", "research_only"]
    research_only_reasons: tuple[Identifier, ...]
    survivorship_limited: bool
    survivorship_limitation_reason: Identifier | None

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        fold_ids = tuple(fold.fold_id for fold in self.fold_metrics)
        if len(set(fold_ids)) != len(fold_ids):
            raise ValueError("backtest fold IDs must be unique")
        if any(point.fold_id not in set(fold_ids) for point in self.economic_series):
            raise ValueError("economic-series fold must exist in fold metrics")
        for fold_id in set(fold_ids):
            times = tuple(
                point.at for point in self.economic_series if point.fold_id == fold_id
            )
            if times != tuple(sorted(times)):
                raise ValueError("economic-series points must be ordered within fold")

        survival_keys = tuple(
            (point.horizon, point.predicted_exit_probability)
            for point in self.survival_calibration
        )
        if len(set(survival_keys)) != len(survival_keys):
            raise ValueError("survival calibration keys must be unique")
        if {point.horizon for point in self.survival_calibration} != {5, 20}:
            raise ValueError("survival calibration requires both 5 and 20 day horizons")
        for horizon in (5, 20):
            probabilities = tuple(
                point.predicted_exit_probability
                for point in self.survival_calibration
                if point.horizon == horizon
            )
            if probabilities != tuple(sorted(probabilities)):
                raise ValueError("survival probabilities must be ordered by horizon")

        slice_keys = tuple(
            (item.dimension, item.value) for item in self.diagnostic_slices
        )
        if len(set(slice_keys)) != len(slice_keys):
            raise ValueError("diagnostic slice keys must be unique")
        required_dimensions = {
            "year",
            "regime",
            "sector",
            "asset_type",
            "liquidity",
            "confidence",
        }
        if {item.dimension for item in self.diagnostic_slices} != required_dimensions:
            raise ValueError("all required diagnostic slice dimensions must be present")
        confidence_slices = tuple(
            item for item in self.diagnostic_slices if item.dimension == "confidence"
        )
        if {item.value for item in confidence_slices} != {
            "high",
            "medium",
            "low",
            "abstain",
        }:
            raise ValueError("confidence diagnostics require all four slices")
        if sum(item.sample_count for item in confidence_slices) != self.sample_count:
            raise ValueError("confidence slice counts must equal backtest sample count")
        if any(
            item.sample_count > self.sample_count for item in self.diagnostic_slices
        ):
            raise ValueError("diagnostic slice count cannot exceed total sample count")

        window_ids = tuple(window.window_id for window in self.evaluation_windows)
        if len(set(window_ids)) != len(window_ids):
            raise ValueError("evaluation window IDs must be unique")
        if self.evaluation_window_id not in set(window_ids):
            raise ValueError("selected evaluation window must be disclosed")
        if not {"outer_test", "lockbox"}.issubset(
            {window.role for window in self.evaluation_windows}
        ):
            raise ValueError("outer-test and lockbox windows must be disclosed")

        cost_bps = tuple(item.per_side_bps for item in self.cost_sensitivity)
        if len(set(cost_bps)) != len(cost_bps) or cost_bps != tuple(sorted(cost_bps)):
            raise ValueError("cost sensitivity must use unique ordered basis points")
        capacity_scales = tuple(
            item.portfolio_scale for item in self.capacity_sensitivity
        )
        if len(set(capacity_scales)) != len(
            capacity_scales
        ) or capacity_scales != tuple(sorted(capacity_scales)):
            raise ValueError("capacity sensitivity must use unique ordered scales")
        if any(
            item.fold_count != len(self.fold_metrics)
            or item.capacity_exceeded_count > self.sample_count
            for item in self.capacity_sensitivity
        ):
            raise ValueError("capacity sensitivity must reconcile to folds and samples")

        if (self.evidence_scope == "research_only") != bool(self.research_only_reasons):
            raise ValueError("research-only scope and reasons must agree")
        if len(set(self.research_only_reasons)) != len(self.research_only_reasons):
            raise ValueError("research-only reasons must be unique")
        if self.survivorship_limited != (
            self.survivorship_limitation_reason is not None
        ):
            raise ValueError("survivorship limitation flag and reason must agree")
        return self


class ModelTaskMatrixRow(_StrictModel):
    task: Identifier
    horizon: Horizon
    asset_group: Identifier
    champion_version: VersionId | None
    baseline_version: VersionId
    challenger_version: VersionId | None
    calibration_version: VersionId | None
    status: Identifier
    unavailable_reason: Identifier | None
    gate_passed: bool | None = None
    fold_count: int | None = Field(default=None, ge=0)
    baseline_wins: int | None = Field(default=None, ge=0)
    positive_cost_windows: int | None = Field(default=None, ge=0)
    pinball_wins: int | None = Field(default=None, ge=0)
    coverage_windows_passed: int | None = Field(default=None, ge=0)
    evaluation_run_id: Identifier | None = None
    candidate_generated_at: UtcDatetime | None = None
    challenger_calibration_version: VersionId | None = None
    statistical_gate_passed: bool | None = None
    calibration_gate_passed: bool | None = None
    stability_gate_passed: bool | None = None
    promotion_window_passed: bool | None = None
    failure_codes: tuple[Identifier, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    component_validated_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def validate_champion(self) -> Self:
        if (self.champion_version is None) == (self.unavailable_reason is None):
            raise ValueError(
                "model task requires exactly one champion or unavailable reason"
            )
        return self


class ModelMatrixReadModel(_StrictModel):
    rows: tuple[ModelTaskMatrixRow, ...]
    candidate_summary: CandidateEvaluationSummary | None = None


class DataHealth(_StrictModel):
    component: Identifier
    dataset: Identifier | None
    symbol: UsSymbol | None
    serving_status: ServingStatus
    connectivity: Connectivity
    freshness: Freshness
    completeness: Completeness
    readiness: Readiness
    last_success_at: UtcDatetime | None
    expected_next_at: UtcDatetime | None
    coverage_rate: float | None = Field(default=None, ge=0, le=1)
    missing_rate: float | None = Field(default=None, ge=0, le=1)
    duplicate_count: int | None = Field(default=None, ge=0)
    quarantine_count: int | None = Field(default=None, ge=0)
    watermark: UtcDatetime | None
    quota_total: int | None = Field(default=None, ge=0)
    quota_used: int | None = Field(default=None, ge=0)
    quota_remaining: int | None = Field(default=None, ge=0)
    error_class: Identifier | None
    message: DisplayText | None
    action: DisplayText | None
    message_ref: LocalizableText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    action_ref: LocalizableText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    affected_symbols: tuple[UsSymbol, ...]
    affected_datasets: tuple[Identifier, ...]

    @model_validator(mode="after")
    def validate_health(self) -> Self:
        quotas = (self.quota_total, self.quota_used, self.quota_remaining)
        if any(value is None for value in quotas) and any(
            value is not None for value in quotas
        ):
            raise ValueError("quota metrics must be provided atomically")
        if self.quota_total is not None:
            if self.quota_used + self.quota_remaining != self.quota_total:  # type: ignore[operator]
                raise ValueError("quota metrics are inconsistent")
        diagnostics = (self.error_class, self.message, self.action)
        if any(value is None for value in diagnostics) and any(
            value is not None for value in diagnostics
        ):
            raise ValueError("health diagnostics must be provided atomically")
        if len(set(self.affected_symbols)) != len(self.affected_symbols):
            raise ValueError("affected symbols must be unique")
        if len(set(self.affected_datasets)) != len(self.affected_datasets):
            raise ValueError("affected datasets must be unique")
        return self


class DataHealthReadModel(_PageModel):
    items: tuple[DataHealth, ...]


class UniverseVersion(_StrictModel):
    universe_version: VersionId
    effective_from: UtcDatetime
    effective_to: UtcDatetime | None
    member_count: int = Field(ge=0)
    reason: DisplayText
    reason_ref: LocalizableText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    point_in_time_status: Literal[
        "proven", "forward_only", "unproven", "not_applicable"
    ]

    @model_validator(mode="after")
    def validate_effective_range(self) -> Self:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("universe effective_to must follow effective_from")
        return self


class UniverseReadModel(_StrictModel):
    versions: tuple[UniverseVersion, ...]


__all__ = [
    "BacktestReadModel",
    "BreakoutProbabilities",
    "CalibratedInterval",
    "CalibrationBin",
    "CalibrationEvidence",
    "CapacitySensitivityEvidence",
    "CostSensitivityEvidence",
    "DataHealth",
    "DataHealthReadModel",
    "DeflatedSharpeEvidence",
    "DiagnosticSliceEvidence",
    "DirectionProbabilities",
    "EconomicBacktestPoint",
    "EvaluationWindowEvidence",
    "FeatureFamilyEvidence",
    "FailedTaskRef",
    "FoldMetrics",
    "ForecastAvailable",
    "ForecastBundle",
    "ForecastEvidence",
    "ForecastResult",
    "ForecastUnavailable",
    "HistoryBar",
    "HistoryBatchItem",
    "HistoryBatchReadModel",
    "HistoryEvent",
    "HistoryGap",
    "HorizonForecast",
    "InstrumentHistory",
    "InstrumentMetadata",
    "InstrumentPage",
    "IntervalCalibrationEvidence",
    "MarketProxy",
    "LocalizableText",
    "MetricEvidence",
    "ModelMatrixReadModel",
    "ModelTaskMatrixRow",
    "CandidateRankChange",
    "OverviewCandidateChurn",
    "OverviewAnomalySnapshot",
    "OverviewComparisonSnapshot",
    "OverviewEvidenceCoverage",
    "OverviewHistoryPoint",
    "OverviewInternalEvidence",
    "OverviewMarketPoint",
    "OverviewReadModel",
    "PboEvidence",
    "PredictionBasis",
    "PredictionReadiness",
    "ExecutionReadiness",
    "PriceBand",
    "ResearchEvidenceScope",
    "ResearchModelEvidence",
    "ResearchModelValues",
    "ResearchOpportunityRow",
    "ResearchPriceProjection",
    "RangeExitHazard",
    "RangeExitMetadata",
    "RegimeDistribution",
    "RegimeProbabilities",
    "RegimeTransitionCell",
    "RejectionReasonCount",
    "ResolvedSnapshot",
    "ReturnBasis",
    "ScreenReturnBasis",
    "ScreenConfidenceFloor",
    "ReturnQuantiles",
    "ScreenPage",
    "ScreenRow",
    "RuleEvidence",
    "CandidateEvaluationSummary",
    "SectorRegimeCell",
    "SectorScorecard",
    "SectorUptrendChange",
    "ServingOverlay",
    "ServingStatus",
    "ShadowWindowProgress",
    "SnapshotServingReference",
    "SnapshotContextReadModel",
    "SimilarCaseQuantiles",
    "SimilarCaseSummary",
    "SimilarCaseView",
    "SortDirection",
    "SurvivalCalibrationPoint",
    "TaskForecastEvidence",
    "TrialLedgerEvidence",
    "UniverseReadModel",
    "UniverseVersion",
]
