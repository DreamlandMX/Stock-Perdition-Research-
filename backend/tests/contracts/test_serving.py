from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from stock_prediction.contracts.health import (
    Completeness,
    Connectivity,
    Freshness,
    Readiness,
)
from stock_prediction.contracts.research import RegimeLabel
from stock_prediction.contracts.serving import (
    BacktestReadModel,
    BreakoutProbabilities,
    CalibratedInterval,
    CalibrationBin,
    CalibrationEvidence,
    CapacitySensitivityEvidence,
    CostSensitivityEvidence,
    DeflatedSharpeEvidence,
    DiagnosticSliceEvidence,
    DataHealth,
    DataHealthReadModel,
    DirectionProbabilities,
    EconomicBacktestPoint,
    EvaluationWindowEvidence,
    FeatureFamilyEvidence,
    FoldMetrics,
    ForecastAvailable,
    ForecastBundle,
    ForecastEvidence,
    ForecastResult,
    ForecastUnavailable,
    HorizonForecast,
    InstrumentMetadata,
    InstrumentPage,
    IntervalCalibrationEvidence,
    MarketProxy,
    MetricEvidence,
    ModelMatrixReadModel,
    ModelTaskMatrixRow,
    OverviewReadModel,
    PboEvidence,
    PriceBand,
    RangeExitHazard,
    RangeExitMetadata,
    ResearchEvidenceScope,
    ResearchModelEvidence,
    ResearchModelValues,
    ResearchPriceProjection,
    RegimeDistribution,
    RegimeProbabilities,
    ResolvedSnapshot,
    ReturnBasis,
    ReturnQuantiles,
    ScreenRow,
    ShadowWindowProgress,
    ServingOverlay,
    ServingStatus,
    SimilarCaseQuantiles,
    SimilarCaseSummary,
    SortDirection,
    SurvivalCalibrationPoint,
    TaskForecastEvidence,
    TrialLedgerEvidence,
    SectorRegimeCell,
    UniverseReadModel,
    UniverseVersion,
)
from stock_prediction.modeling.confidence import ConfidenceLevel, RejectionReason


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is UTC
    return parsed


def valid_resolved_snapshot() -> ResolvedSnapshot:
    return ResolvedSnapshot(
        snapshot_id="snapshot-20260710-r1",
        revision_no=1,
        as_of=utc("2026-07-10T20:00:00Z"),
        dataset_revision="dataset-r1",
        feature_set_version="features-v1",
        universe_version="universe-v1",
        model_version="model-v1",
        calibration_version="calibration-v1",
    )


def valid_similar_case_summary() -> SimilarCaseSummary:
    return SimilarCaseSummary(
        effective_independent_count=24,
        insufficient_evidence=False,
        median_distance=0.72,
        return_quantiles=SimilarCaseQuantiles(q10=-0.04, q50=0.02, q90=0.08),
        win_rate=0.62,
        breakout_rate=0.33,
        worst_mae_case_id="case-20081010-aapl",
    )


def valid_forecast_evidence() -> ForecastEvidence:
    return ForecastEvidence(
        top_feature_families=(
            FeatureFamilyEvidence(
                family="price_action",
                contribution=0.31,
                training_percentile=0.84,
            ),
        ),
        regime_probabilities=RegimeProbabilities(
            range=0.55,
            uptrend=0.25,
            downtrend=0.10,
            transition=0.10,
        ),
        regime_probabilities_unavailable_reason=None,
        regime_model_version="regime-model-v1",
        ood_score=0.4,
        ood_method="robust_mahalanobis",
        ood_threshold=3.0,
        model_disagreement=0.08,
        model_disagreement_summary="classifier_quantile_aligned",
        similar_case_summary=valid_similar_case_summary(),
        calibration_evidence=CalibrationEvidence(
            probability_bin_sample_count=340,
            calibration_error=0.025,
            cqr_empirical_coverage=0.81,
            cqr_average_width=0.12,
        ),
        data_quality_score=0.98,
        missing_rate=0.0,
        evidence_cutoff=utc("2026-07-10T20:00:00Z"),
    )


def horizon_forecast(horizon: int) -> HorizonForecast:
    return HorizonForecast(
        horizon=horizon,
        forecast_direction_probabilities=DirectionProbabilities(
            up=0.56, flat=0.24, down=0.20
        ),
        breakout_probabilities=BreakoutProbabilities(up=0.26, down=0.14, none=0.60),
        range_exit_probability=None if horizon == 1 else 0.31,
        range_exit_hazard_curve=(
            ()
            if horizon == 1
            else tuple(
                RangeExitHazard(
                    trading_day=trading_day,
                    hazard_probability=0.31 if trading_day == horizon else 0.0,
                    cumulative_probability=0.31 if trading_day == horizon else 0.0,
                )
                for trading_day in range(1, horizon + 1)
            )
        ),
        range_exit_metadata=RangeExitMetadata(
            confirmation_days=2,
            censoring_rule="right_censored_at_horizon",
            sample_denominator=480,
            not_generated_reason=(
                "two_day_confirmation_required" if horizon == 1 else None
            ),
        ),
        capital_gain_return_quantiles=ReturnQuantiles(
            q10=-0.04, q25=-0.01, q50=0.02, q75=0.05, q90=0.09
        ),
        total_return_quantiles=ReturnQuantiles(
            q10=-0.039, q25=-0.009, q50=0.021, q75=0.051, q90=0.091
        ),
        executable_direction_probabilities=DirectionProbabilities(
            up=0.53, flat=0.26, down=0.21
        ),
        executable_return_quantiles=ReturnQuantiles(
            q10=-0.045, q25=-0.015, q50=0.015, q75=0.045, q90=0.085
        ),
        calibrated_return_intervals=(
            CalibratedInterval(
                return_basis=ReturnBasis.CAPITAL_GAIN,
                lower=-0.055,
                upper=0.102,
                coverage_target=0.80,
                method="cqr",
                calibration_version="calibration-v1",
            ),
            CalibratedInterval(
                return_basis=ReturnBasis.TOTAL_RETURN,
                lower=-0.054,
                upper=0.103,
                coverage_target=0.80,
                method="cqr",
                calibration_version="calibration-v1",
            ),
            CalibratedInterval(
                return_basis=ReturnBasis.EXECUTABLE,
                lower=-0.060,
                upper=0.095,
                coverage_target=0.80,
                method="cqr",
                calibration_version="calibration-v1",
            ),
        ),
        interval_method="cqr",
        coverage_target=0.80,
        forecast_mfe_quantiles=ReturnQuantiles(
            q10=0.0, q25=0.01, q50=0.03, q75=0.06, q90=0.10
        ),
        forecast_mae_quantiles=ReturnQuantiles(
            q10=-0.10, q25=-0.06, q50=-0.03, q75=-0.01, q90=0.0
        ),
        executable_mfe_quantiles=ReturnQuantiles(
            q10=0.0, q25=0.008, q50=0.025, q75=0.055, q90=0.09
        ),
        executable_mae_quantiles=ReturnQuantiles(
            q10=-0.11, q25=-0.065, q50=-0.035, q75=-0.012, q90=0.0
        ),
        reference_price=220.0,
        reference_price_at=utc("2026-07-10T20:00:00Z"),
        currency="USD",
        adjustment_mode="split_adjusted_capital_gain_and_total_return",
        price_band=PriceBand(q10=211.2, q25=217.8, q50=224.4, q75=231.0, q90=239.8),
        price_band_unavailable_reason=None,
    )


def horizon_without_survival(horizon: int) -> HorizonForecast:
    forecast = horizon_forecast(horizon)
    metadata = forecast.range_exit_metadata.model_copy(
        update={"not_generated_reason": "not_range_regime"}
    )
    return HorizonForecast.model_validate(
        {
            **forecast.model_dump(),
            "range_exit_probability": None,
            "range_exit_hazard_curve": (),
            "range_exit_metadata": metadata,
        },
        strict=True,
    )


def valid_screen_row() -> ScreenRow:
    return ScreenRow(
        symbol="US.AAPL",
        name="Apple Inc.",
        asset_type="stock",
        sector="technology",
        regime=RegimeLabel.RANGE,
        horizon=5,
        executable_up_probability=0.53,
        breakout_up_probability=0.26,
        breakout_down_probability=0.14,
        executable_q50=0.015,
        executable_q10=-0.045,
        forecast_up_probability=0.56,
        capital_gain_q50=0.02,
        capital_gain_q10=-0.04,
        interval_width=0.157,
        similar_case_count=24,
        confidence_level=ConfidenceLevel.MEDIUM,
        data_freshness_seconds=120,
        serving_status=ServingStatus.LIVE,
        effective_abstain=False,
        abstain_reasons=(),
        model_version="model-v1",
        as_of=utc("2026-07-10T20:00:00Z"),
    )


def valid_forecast_bundle_payload() -> dict[str, object]:
    evidence = valid_forecast_evidence()
    return {
        "symbol": "US.AAPL",
        "as_of": utc("2026-07-10T20:00:00Z"),
        "current_regime": RegimeLabel.RANGE,
        "regime_probabilities": evidence.regime_probabilities,
        "regime_probabilities_unavailable_reason": None,
        "current_event_flags": ("range_compression",),
        "horizons": tuple(horizon_forecast(value) for value in (1, 5, 20)),
        "invalidation_conditions": (
            "close_above_frozen_channel",
            "corporate_action_revision",
        ),
        "confidence_level": ConfidenceLevel.MEDIUM,
        "confidence_score": 0.72,
        "ood_score": evidence.ood_score,
        "model_disagreement": evidence.model_disagreement,
        "similar_case_summary": evidence.similar_case_summary,
        "evidence": evidence,
        "abstain": False,
        "abstain_reasons": (),
        "dataset_revision": "dataset-r1",
        "feature_set_version": "features-v1",
        "label_version": "labels-v1",
        "universe_version": "universe-v1",
        "model_version": "model-v1",
        "calibration_version": "calibration-v1",
        "distance_version": "distance-v1",
        "data_freshness_seconds": 120,
        "data_quality_status": "current",
        "generated_at": utc("2026-07-10T20:02:00Z"),
        "inference_latency_ms": 4.2,
        "inference_latency_scope": "per_symbol_batch_end_to_end",
        "revision_no": 1,
        "snapshot_id": "snapshot-20260710-r1",
        "valid_until": utc("2026-07-13T20:00:00Z"),
    }


def test_atomic_models_are_strict_frozen_and_forbid_extra_fields() -> None:
    probabilities = DirectionProbabilities(up=0.5, flat=0.3, down=0.2)
    with pytest.raises(ValidationError, match="frozen"):
        probabilities.up = 0.4
    with pytest.raises(ValidationError):
        DirectionProbabilities.model_validate(
            {"up": "0.5", "flat": 0.3, "down": 0.2}, strict=True
        )
    with pytest.raises(ValidationError, match="extra"):
        DirectionProbabilities.model_validate(
            {"up": 0.5, "flat": 0.3, "down": 0.2, "other": 0.0},
            strict=True,
        )


def test_probability_models_require_finite_unit_sums() -> None:
    with pytest.raises(ValidationError, match="sum to 1"):
        DirectionProbabilities(up=0.5, flat=0.3, down=0.3)
    with pytest.raises(ValidationError):
        DirectionProbabilities(up=float("nan"), flat=0.5, down=0.5)
    with pytest.raises(ValidationError, match="sum to 1"):
        RegimeProbabilities(range=0.5, uptrend=0.3, downtrend=0.3, transition=0.0)


def test_return_quantiles_and_calibrated_intervals_are_ordered() -> None:
    with pytest.raises(ValidationError, match="monotonic"):
        ReturnQuantiles(q10=-0.1, q25=0.0, q50=-0.01, q75=0.05, q90=0.1)
    with pytest.raises(ValidationError, match="lower"):
        CalibratedInterval(
            return_basis=ReturnBasis.TOTAL_RETURN,
            lower=0.05,
            upper=-0.02,
            coverage_target=0.8,
            method="cqr",
            calibration_version="calibration-v1",
        )


def test_forecast_bundle_requires_all_three_horizons() -> None:
    payload = valid_forecast_bundle_payload()
    payload["horizons"] = payload["horizons"][:2]  # type: ignore[index]
    with pytest.raises(ValidationError, match="1, 5, and 20"):
        ForecastBundle.model_validate(payload, strict=True)


def test_forecast_bundle_requires_matching_regime_unavailable_reasons() -> None:
    evidence = ForecastEvidence.model_validate(
        {
            **valid_forecast_evidence().model_dump(),
            "regime_probabilities": None,
            "regime_probabilities_unavailable_reason": "regime_model_disabled",
            "regime_model_version": None,
        },
        strict=True,
    )
    payload = valid_forecast_bundle_payload()
    payload.update(
        regime_probabilities=None,
        regime_probabilities_unavailable_reason="regime_inputs_unavailable",
        evidence=evidence,
    )

    with pytest.raises(ValidationError, match="unavailable reason"):
        ForecastBundle.model_validate(payload, strict=True)


def test_forecast_bundle_requires_one_calibration_lineage() -> None:
    payload = valid_forecast_bundle_payload()
    horizons = list(payload["horizons"])  # type: ignore[arg-type]
    five_day = horizons[1]
    assert isinstance(five_day, HorizonForecast)
    intervals = list(five_day.calibrated_return_intervals)
    intervals[1] = intervals[1].model_copy(
        update={"calibration_version": "calibration-v2"}
    )
    horizons[1] = HorizonForecast.model_validate(
        {
            **five_day.model_dump(),
            "calibrated_return_intervals": tuple(intervals),
        },
        strict=True,
    )
    payload["horizons"] = tuple(horizons)

    with pytest.raises(ValidationError, match="calibration_version"):
        ForecastBundle.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "valid_until",
    (
        utc("2026-07-10T20:01:00Z"),
        utc("2026-07-10T20:02:00Z"),
    ),
)
def test_forecast_bundle_must_expire_after_generation(
    valid_until: datetime,
) -> None:
    payload = valid_forecast_bundle_payload()
    payload["valid_until"] = valid_until

    with pytest.raises(ValidationError, match="generated_at"):
        ForecastBundle.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("current_regime", RegimeLabel.INVALID),
        ("data_quality_status", "invalid"),
    ),
)
def test_pre_model_hard_gates_cannot_create_available_bundles(
    field: str, value: object
) -> None:
    payload = valid_forecast_bundle_payload()
    payload[field] = value
    with pytest.raises(ValidationError, match="ForecastUnavailable"):
        ForecastBundle.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "serving_status",
    (ServingStatus.STALE, ServingStatus.OFFLINE, ServingStatus.UNAVAILABLE),
)
def test_hard_serving_states_require_effective_abstention(
    serving_status: ServingStatus,
) -> None:
    with pytest.raises(ValidationError, match="effective abstain"):
        ServingOverlay(
            serving_status=serving_status,
            effective_abstain=False,
            evaluated_at=utc("2026-07-10T20:03:00Z"),
            exclusion_reason=(),
        )


def test_available_result_preserves_model_post_abstention() -> None:
    payload = valid_forecast_bundle_payload()
    payload.update(
        confidence_level=ConfidenceLevel.UNAVAILABLE,
        abstain=True,
        abstain_reasons=(RejectionReason.OOD,),
    )
    bundle = ForecastBundle.model_validate(payload, strict=True)
    serving = ServingOverlay(
        serving_status=ServingStatus.LIVE,
        effective_abstain=False,
        evaluated_at=utc("2026-07-10T20:03:00Z"),
        exclusion_reason=(),
    )
    with pytest.raises(ValidationError, match="effective abstain"):
        ForecastAvailable(
            bundle=bundle,
            serving=serving,
            evidence=bundle.evidence,
            similar_cases=(),
        )


def test_available_result_cannot_be_evaluated_before_generation() -> None:
    bundle = ForecastBundle.model_validate(valid_forecast_bundle_payload(), strict=True)
    serving = ServingOverlay(
        serving_status=ServingStatus.LIVE,
        effective_abstain=False,
        evaluated_at=utc("2026-07-10T20:01:00Z"),
        exclusion_reason=(),
    )

    with pytest.raises(ValidationError, match="evaluated_at"):
        ForecastAvailable(
            bundle=bundle,
            serving=serving,
            evidence=bundle.evidence,
            similar_cases=(),
        )

    boundary_serving = serving.model_copy(update={"evaluated_at": bundle.generated_at})
    assert ForecastAvailable(
        bundle=bundle,
        serving=boundary_serving,
        evidence=bundle.evidence,
        similar_cases=(),
    )


def test_market_proxy_prediction_fields_are_atomic() -> None:
    unavailable = MarketProxy(
        symbol="US.SPY",
        name="SPDR S&P 500 ETF Trust",
        regime=RegimeLabel.RANGE,
        regime_probability=None,
        direction=None,
        unavailable_reason="proxy_forecast_unavailable",
        session_return=0.001,
        trend=(600.0, 601.0),
        as_of=utc("2026-07-10T20:00:00Z"),
        serving_status=ServingStatus.UNAVAILABLE,
    )
    assert unavailable.unavailable_reason == "proxy_forecast_unavailable"

    with pytest.raises(ValidationError, match="atomic"):
        MarketProxy(
            **{
                **unavailable.model_dump(),
                "regime_probability": 0.7,
                "unavailable_reason": None,
            }
        )
    with pytest.raises(ValidationError, match="unavailable reason"):
        MarketProxy(
            **{
                **unavailable.model_dump(),
                "regime_probability": 0.7,
                "direction": "flat",
            }
        )


def test_horizon_forecast_rejects_unsupported_and_one_day_survival() -> None:
    one_day = horizon_forecast(1)
    with pytest.raises(ValidationError):
        HorizonForecast.model_validate(
            {**one_day.model_dump(), "horizon": 2}, strict=True
        )
    with pytest.raises(ValidationError, match="1-day survival"):
        HorizonForecast.model_validate(
            {**one_day.model_dump(), "range_exit_probability": 0.1}, strict=True
        )


def test_survival_output_is_atomic_and_uses_consecutive_consistent_hazards() -> None:
    five_day = horizon_forecast(5)
    unavailable_metadata = five_day.range_exit_metadata.model_copy(
        update={"not_generated_reason": "survival_unavailable"}
    )
    with pytest.raises(ValidationError, match="atomic"):
        HorizonForecast.model_validate(
            {
                **five_day.model_dump(),
                "range_exit_hazard_curve": (),
                "range_exit_metadata": unavailable_metadata,
            },
            strict=True,
        )

    with pytest.raises(ValidationError, match="consecutive"):
        HorizonForecast.model_validate(
            {
                **five_day.model_dump(),
                "range_exit_hazard_curve": five_day.range_exit_hazard_curve[1:],
            },
            strict=True,
        )

    inconsistent = list(five_day.range_exit_hazard_curve)
    inconsistent[0] = inconsistent[0].model_copy(
        update={"hazard_probability": 0.10, "cumulative_probability": 0.0}
    )
    with pytest.raises(ValidationError, match="daily hazards"):
        HorizonForecast.model_validate(
            {**five_day.model_dump(), "range_exit_hazard_curve": tuple(inconsistent)},
            strict=True,
        )


def test_survival_output_is_allowed_only_for_range_regime() -> None:
    non_range_payload = valid_forecast_bundle_payload()
    non_range_payload["current_regime"] = RegimeLabel.UPTREND
    with pytest.raises(ValidationError, match="range regime"):
        ForecastBundle.model_validate(non_range_payload, strict=True)

    missing_range_payload = valid_forecast_bundle_payload()
    missing_range_payload["horizons"] = (
        horizon_forecast(1),
        horizon_without_survival(5),
        horizon_without_survival(20),
    )
    with pytest.raises(ValidationError, match="range regime"):
        ForecastBundle.model_validate(missing_range_payload, strict=True)

    non_range_payload["horizons"] = missing_range_payload["horizons"]
    assert ForecastBundle.model_validate(non_range_payload, strict=True)


def test_price_band_is_derived_from_reference_price_and_capital_gain_quantiles() -> (
    None
):
    five_day = horizon_forecast(5)
    assert five_day.price_band is not None
    mismatched_band = five_day.price_band.model_copy(update={"q50": 225.0})
    with pytest.raises(ValidationError, match="derived"):
        HorizonForecast.model_validate(
            {**five_day.model_dump(), "price_band": mismatched_band}, strict=True
        )


def test_forecast_bundle_rejects_duplicate_abstain_reasons() -> None:
    payload = valid_forecast_bundle_payload()
    payload.update(
        confidence_level=ConfidenceLevel.UNAVAILABLE,
        abstain=True,
        abstain_reasons=(
            RejectionReason.OOD,
            RejectionReason.OOD,
        ),
    )
    with pytest.raises(ValidationError, match="unique"):
        ForecastBundle.model_validate(payload, strict=True)


def test_resolved_snapshot_versions_are_immutable() -> None:
    snapshot = valid_resolved_snapshot()
    with pytest.raises(ValidationError, match="frozen"):
        snapshot.model_version = "model-v2"


def test_forecast_result_serializes_as_a_discriminated_union() -> None:
    bundle = ForecastBundle.model_validate(valid_forecast_bundle_payload(), strict=True)
    result = ForecastAvailable(
        bundle=bundle,
        serving=ServingOverlay(
            serving_status=ServingStatus.LIVE,
            effective_abstain=False,
            evaluated_at=utc("2026-07-10T20:03:00Z"),
            exclusion_reason=(),
        ),
        evidence=bundle.evidence,
        similar_cases=(),
    )
    adapter = TypeAdapter(ForecastResult)
    dumped = adapter.dump_json(result)
    assert b'"availability":"available"' in dumped
    assert adapter.validate_json(dumped) == result


def test_unavailable_result_never_contains_probabilities() -> None:
    result = ForecastUnavailable(
        symbol="US.AAPL",
        as_of=utc("2026-07-10T20:00:00Z"),
        serving_status=ServingStatus.UNAVAILABLE,
        not_generated_reason="no_champion_candidate",
        affected_inputs=("model_registry",),
        last_available_snapshot=None,
    )
    assert result.effective_abstain is True
    assert "probability" not in result.model_dump_json()
    with pytest.raises(ValidationError, match="extra"):
        ForecastUnavailable.model_validate(
            {**result.model_dump(), "up_probability": 0.0}, strict=True
        )


def research_evidence(
    horizon: int,
    *,
    currency: str = "USD",
    reference_price: float = 100.0,
) -> ResearchModelEvidence:
    as_of = utc("2026-07-10T20:00:00Z")
    return ResearchModelEvidence(
        scope=ResearchEvidenceScope.VERIFIED,
        symbol="US.AAPL",
        horizon=horizon,
        as_of=as_of,
        values=ResearchModelValues(capital_gain_q50=0.01 * horizon),
        price_projection=ResearchPriceProjection(
            reference_price=reference_price,
            reference_price_at=as_of,
            currency=currency,
            endpoint_price_band=PriceBand(
                q10=90.0,
                q25=95.0,
                q50=100.0,
                q75=105.0,
                q90=110.0,
            ),
            endpoint_price_unavailable_reason=None,
            stage_high_price_q50=None,
            stage_high_price_unavailable_reason="not_published",
            stage_low_price_q50=None,
            stage_low_price_unavailable_reason="not_published",
        ),
        tasks=(
            TaskForecastEvidence(
                task="return_fwd",
                horizon=horizon,
                scope=ResearchEvidenceScope.VERIFIED,
                gate_passed=True,
                model_version=f"return-v{horizon}",
                calibration_version=f"return-cal-v{horizon}",
                evaluation_run_id=f"return-eval-v{horizon}",
                fold_count=4,
            ),
        ),
    )


def unavailable_with_research(
    *evidence: ResearchModelEvidence,
) -> ForecastUnavailable:
    return ForecastUnavailable(
        symbol="US.AAPL",
        as_of=utc("2026-07-10T20:00:00Z"),
        serving_status=ServingStatus.UNAVAILABLE,
        not_generated_reason="no_champion_candidate",
        affected_inputs=("model_registry",),
        last_available_snapshot=None,
        verified_research=evidence,
    )


def test_unavailable_result_rejects_duplicate_verified_research_horizons() -> None:
    with pytest.raises(
        ValidationError, match="verified research horizons must be unique"
    ):
        unavailable_with_research(research_evidence(5), research_evidence(5))


@pytest.mark.parametrize(
    ("second", "message"),
    [
        (research_evidence(5, reference_price=101.0), "price anchors must match"),
        (research_evidence(5, currency="HKD"), "price anchors must match"),
    ],
)
def test_unavailable_result_rejects_mixed_research_price_anchors(
    second: ResearchModelEvidence,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        unavailable_with_research(research_evidence(1), second)


def test_challenger_scope_still_rejects_passed_task_evidence() -> None:
    task = (
        research_evidence(20)
        .tasks[0]
        .model_copy(update={"scope": ResearchEvidenceScope.CHALLENGER})
    )

    with pytest.raises(ValidationError, match="failed task gate"):
        TaskForecastEvidence.model_validate(task.model_dump(), strict=True)


def test_shadow_evidence_requires_passed_development_and_incomplete_blind_window() -> (
    None
):
    as_of = utc("2026-08-24T20:00:00Z")
    evidence = ResearchModelEvidence(
        scope=ResearchEvidenceScope.SHADOW,
        symbol="US.AAPL",
        horizon=20,
        as_of=as_of,
        values=ResearchModelValues(forecast_up_probability=0.57),
        tasks=(
            TaskForecastEvidence(
                task="forecast_direction",
                horizon=20,
                scope=ResearchEvidenceScope.SHADOW,
                gate_passed=True,
                model_version="direction-shadow-v1",
                calibration_version="direction-shadow-cal-v1",
                evaluation_run_id="development-run-v1",
                fold_count=4,
            ),
        ),
        experimental_warning="开发门禁已通过，盲测尚未完成，不得用于正式预测。",
        package_id="direction-package-v1",
        blind_evidence_scope="shadow_forward",
        shadow_window_progress=ShadowWindowProgress(
            window_id="direction-shadow-window-v1",
            anchors_collected=20,
            mature_labels=0,
        ),
    )

    assert evidence.scope is ResearchEvidenceScope.SHADOW
    assert evidence.shadow_window_progress.required_anchors == 77
    assert evidence.shadow_window_progress.label_horizon == 20

    for missing_field in (
        "experimental_warning",
        "package_id",
        "shadow_window_progress",
    ):
        payload = evidence.model_dump()
        payload[missing_field] = None
        with pytest.raises(ValidationError, match="shadow evidence requires"):
            ResearchModelEvidence.model_validate(payload, strict=True)
    with pytest.raises(ValidationError, match="blind evidence must be incomplete"):
        ResearchModelEvidence.model_validate(
            {
                **evidence.model_dump(),
                "blind_evidence_scope": "blind_complete",
            },
            strict=True,
        )


def test_shadow_evidence_uses_only_the_explicit_shadow_preview_collection() -> None:
    as_of = utc("2026-08-24T20:00:00Z")
    shadow = ResearchModelEvidence(
        scope=ResearchEvidenceScope.SHADOW,
        symbol="US.AAPL",
        horizon=20,
        as_of=as_of,
        values=ResearchModelValues(range_exit_probability=0.31),
        tasks=(
            TaskForecastEvidence(
                task="range_exit",
                horizon=20,
                scope=ResearchEvidenceScope.SHADOW,
                gate_passed=True,
                model_version="range-exit-shadow-v1",
                calibration_version="range-exit-shadow-cal-v1",
                evaluation_run_id="development-run-v1",
                fold_count=4,
            ),
        ),
        experimental_warning="盲测尚未完成。",
        package_id="range-exit-package-v1",
        blind_evidence_scope="optimization_reused",
        shadow_window_progress=ShadowWindowProgress(
            window_id="range-exit-shadow-window-v1",
            anchors_collected=0,
            mature_labels=0,
        ),
    )
    unavailable = ForecastUnavailable(
        symbol="US.AAPL",
        as_of=as_of,
        serving_status=ServingStatus.UNAVAILABLE,
        not_generated_reason="no_champion_candidate",
        affected_inputs=("model_registry",),
        last_available_snapshot=None,
        shadow_preview=(shadow,),
    )

    assert unavailable.shadow_preview == (shadow,)
    with pytest.raises(ValidationError, match="verified research identity"):
        ForecastUnavailable.model_validate(
            {
                **unavailable.model_dump(),
                "verified_research": (shadow.model_dump(),),
                "shadow_preview": (),
            },
            strict=True,
        )


def test_screen_row_exposes_capital_gain_basis_and_similar_case_count() -> None:
    row = valid_screen_row()
    assert row.forecast_up_probability == 0.56
    assert row.capital_gain_q10 == -0.04
    assert row.capital_gain_q50 == 0.02
    assert row.similar_case_count == 24

    with pytest.raises(ValidationError, match="abstain"):
        ScreenRow.model_validate(
            {
                **row.model_dump(),
                "executable_up_probability": None,
                "breakout_up_probability": None,
                "breakout_down_probability": None,
                "executable_q50": None,
                "executable_q10": None,
                "forecast_up_probability": None,
                "capital_gain_q50": None,
                "capital_gain_q10": -0.04,
                "interval_width": None,
                "effective_abstain": True,
                "abstain_reasons": (RejectionReason.OOD,),
            },
            strict=True,
        )


@pytest.mark.parametrize(
    "serving_status",
    (ServingStatus.STALE, ServingStatus.OFFLINE, ServingStatus.UNAVAILABLE),
)
def test_screen_rows_in_hard_serving_states_require_abstention(
    serving_status: ServingStatus,
) -> None:
    row = valid_screen_row()

    with pytest.raises(ValidationError, match="effective abstain"):
        ScreenRow.model_validate(
            {**row.model_dump(), "serving_status": serving_status},
            strict=True,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("regime", RegimeLabel.INVALID),
        ("confidence_level", ConfidenceLevel.UNAVAILABLE),
    ),
)
def test_screen_rows_in_model_hard_states_are_non_rankable_abstentions(
    field: str,
    value: object,
) -> None:
    hard_state_payload = {**valid_screen_row().model_dump(), field: value}

    with pytest.raises(ValidationError, match="effective abstain"):
        ScreenRow.model_validate(hard_state_payload, strict=True)

    with pytest.raises(ValidationError, match="cannot publish ranking values"):
        ScreenRow.model_validate(
            {
                **hard_state_payload,
                "effective_abstain": True,
                "abstain_reasons": (RejectionReason.INSUFFICIENT_SIMILAR_CASES,),
            },
            strict=True,
        )

    non_rankable_payload = {
        **hard_state_payload,
        "executable_up_probability": None,
        "breakout_up_probability": None,
        "breakout_down_probability": None,
        "executable_q50": None,
        "executable_q10": None,
        "forecast_up_probability": None,
        "capital_gain_q50": None,
        "capital_gain_q10": None,
        "interval_width": None,
        "effective_abstain": True,
        "abstain_reasons": (RejectionReason.INSUFFICIENT_SIMILAR_CASES,),
    }
    row = ScreenRow.model_validate(non_rankable_payload, strict=True)
    assert row.effective_abstain is True
    assert row.abstain_reasons

    with pytest.raises(ValidationError, match="abstention and reasons"):
        ScreenRow.model_validate(
            {**non_rankable_payload, "abstain_reasons": ()},
            strict=True,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("regime", RegimeLabel.INVALID),
        ("confidence_level", ConfidenceLevel.UNAVAILABLE),
    ),
)
def test_overview_independently_rejects_model_hard_state_opportunities(
    field: str,
    value: object,
) -> None:
    hard_state_row = valid_screen_row().model_copy(update={field: value})
    overview = OverviewReadModel.model_construct(
        proxies=(),
        top_opportunities=(hard_state_row,),
        regime_distribution=(),
        sector_regime_matrix=(),
        market_breadth=0.5,
        abstain_count=0,
        abstain_reasons=(),
        data_confidence=0.9,
        batch_generated_at=utc("2026-07-10T20:03:00Z"),
        total_inference_latency_ms=20.0,
    )

    with pytest.raises(ValueError, match="top opportunities"):
        overview.validate_aggregates()


def test_overview_top_opportunities_reject_hard_serving_states() -> None:
    stale_row = ScreenRow.model_validate(
        {
            **valid_screen_row().model_dump(),
            "executable_up_probability": None,
            "breakout_up_probability": None,
            "breakout_down_probability": None,
            "executable_q50": None,
            "executable_q10": None,
            "forecast_up_probability": None,
            "capital_gain_q50": None,
            "capital_gain_q10": None,
            "interval_width": None,
            "serving_status": ServingStatus.STALE,
            "effective_abstain": True,
            "abstain_reasons": (RejectionReason.STALE_DATA,),
        },
        strict=True,
    )

    with pytest.raises(ValidationError, match="top opportunities"):
        OverviewReadModel(
            proxies=(),
            top_opportunities=(stale_row,),
            regime_distribution=(),
            sector_regime_matrix=(),
            market_breadth=0.5,
            abstain_count=0,
            abstain_reasons=(),
            data_confidence=0.9,
            batch_generated_at=utc("2026-07-10T20:03:00Z"),
            total_inference_latency_ms=20.0,
        )


def test_zero_similar_cases_require_explicit_insufficient_evidence() -> None:
    with pytest.raises(ValidationError, match="zero similar cases"):
        SimilarCaseSummary(
            effective_independent_count=0,
            insufficient_evidence=False,
            median_distance=None,
            return_quantiles=None,
            win_rate=0.5,
            breakout_rate=0.5,
            worst_mae_case_id=None,
        )


def test_read_models_keep_tables_as_ordered_tuples_with_page_metadata() -> None:
    instrument = InstrumentMetadata(
        symbol="US.AAPL",
        name="Apple Inc.",
        asset_type="stock",
        sector="technology",
        currency="USD",
        exchange="NASDAQ",
        effective_from=utc("2026-01-02T00:00:00Z"),
        effective_to=None,
        universe_version="universe-v1",
    )
    page = InstrumentPage(
        items=(instrument,),
        total=1,
        page=1,
        page_size=25,
        sort_key="symbol",
        sort_direction=SortDirection.ASC,
    )
    assert page.items == (instrument,)
    with pytest.raises(ValidationError):
        InstrumentPage.model_validate(
            {**page.model_dump(), "items": [instrument.model_dump()]}, strict=True
        )


def test_backtest_slice_metrics_require_value_or_explicit_unavailable_reason() -> None:
    available = MetricEvidence(
        name="brier_skill",
        value=0.07,
        unavailable_reason=None,
    )
    unavailable = MetricEvidence(
        name="ranking_ic",
        value=None,
        unavailable_reason="insufficient_samples",
    )
    confidence = DiagnosticSliceEvidence(
        dimension="confidence",
        value="high",
        sample_count=120,
        metrics=(available, unavailable),
    )

    assert confidence.metrics == (available, unavailable)

    for payload in (
        {"name": "brier_skill", "value": None, "unavailable_reason": None},
        {
            "name": "brier_skill",
            "value": 0.07,
            "unavailable_reason": "not_available",
        },
        {"name": "brier_skill", "value": float("nan"), "unavailable_reason": None},
        {"name": "brier_skill", "value": float("inf"), "unavailable_reason": None},
    ):
        with pytest.raises(ValidationError):
            MetricEvidence.model_validate(payload, strict=True)

    with pytest.raises(ValidationError, match="metric names must be unique"):
        DiagnosticSliceEvidence(
            dimension="regime",
            value="range",
            sample_count=20,
            metrics=(available, available),
        )


def valid_fold_metrics() -> FoldMetrics:
    return FoldMetrics(
        fold_id="outer-1",
        evaluation_start=utc("2025-01-02T00:00:00Z"),
        evaluation_end=utc("2025-06-30T20:00:00Z"),
        sample_count=500,
        brier_score=0.19,
        brier_skill=0.05,
        pr_auc=0.58,
        expected_calibration_error=0.03,
        pinball_loss=0.04,
        ranking_ic=0.08,
        raw_interval_coverage=0.72,
        raw_interval_width=0.09,
        interval_coverage=0.81,
        interval_width=0.11,
        tail_failure_rate=0.02,
        survival_brier=0.17,
        concordance_index=0.61,
        worst_window=False,
    )


def valid_backtest_read_model() -> BacktestReadModel:
    skill = MetricEvidence(
        name="brier_skill",
        value=0.07,
        unavailable_reason=None,
    )
    slices = tuple(
        DiagnosticSliceEvidence(
            dimension=dimension,
            value=value,
            sample_count=count,
            metrics=(skill,),
        )
        for dimension, value, count in (
            ("confidence", "high", 120),
            ("confidence", "medium", 180),
            ("confidence", "low", 100),
            ("confidence", "abstain", 100),
            ("regime", "range", 220),
            ("sector", "technology", 140),
            ("asset_type", "stock", 420),
            ("liquidity", "high_liquidity", 310),
            ("year", "2025", 500),
        )
    )
    return BacktestReadModel(
        task="forecast_direction",
        horizon=5,
        asset_group="us_equity",
        fold_metrics=(valid_fold_metrics(),),
        calibration_bins=(
            CalibrationBin(
                lower_bound=0.4,
                upper_bound=0.5,
                mean_prediction=0.46,
                observed_frequency=0.44,
                sample_count=120,
            ),
        ),
        interval_calibration=IntervalCalibrationEvidence(
            method="conformalized_quantile_regression",
            target_coverage=0.8,
            status="available",
            unavailable_reason=None,
        ),
        survival_calibration=(
            SurvivalCalibrationPoint(
                horizon=5,
                predicted_exit_probability=0.4,
                observed_exit_frequency=0.38,
                sample_count=180,
            ),
            SurvivalCalibrationPoint(
                horizon=20,
                predicted_exit_probability=0.7,
                observed_exit_frequency=0.68,
                sample_count=160,
            ),
        ),
        diagnostic_slices=slices,
        deflated_sharpe=DeflatedSharpeEvidence(
            observed_sharpe=0.5,
            expected_maximum_sharpe=0.2,
            status="insufficient_trial_variance",
            probability_sharpe_is_genuine=None,
            attempted_trials=4,
            successful_trials_with_sharpe=4,
            attempted_sharpe_mean=0.0,
            attempted_sharpe_std=0.0,
            observation_count=12,
            assumptions=("Returns are stationary within the evaluated window",),
        ),
        pbo=PboEvidence(
            status="insufficient_blocks",
            probability=None,
            block_count=3,
            combination_count=0,
        ),
        trial_ledger=TrialLedgerEvidence(
            registered=4,
            succeeded=1,
            failed=1,
            aborted=1,
            rejected=1,
            running=0,
        ),
        evaluation_windows=(
            EvaluationWindowEvidence(
                window_id="outer-window-v1",
                role="outer_test",
                scope="synthetic_offline",
                consumption_count=1,
                consumed_at=utc("2025-07-01T00:00:00Z"),
            ),
            EvaluationWindowEvidence(
                window_id="audit-lockbox-v1",
                role="lockbox",
                scope="production",
                consumption_count=0,
                consumed_at=None,
            ),
        ),
        cost_sensitivity=(
            CostSensitivityEvidence(
                per_side_bps=5,
                estimated_total_cost=0.004,
                net_portfolio_return=0.07,
            ),
            CostSensitivityEvidence(
                per_side_bps=10,
                estimated_total_cost=0.008,
                net_portfolio_return=0.06,
            ),
            CostSensitivityEvidence(
                per_side_bps=20,
                estimated_total_cost=0.016,
                net_portfolio_return=0.04,
            ),
        ),
        capacity_sensitivity=(
            CapacitySensitivityEvidence(
                portfolio_scale=0.5,
                fold_count=1,
                capacity_exceeded_count=0,
            ),
            CapacitySensitivityEvidence(
                portfolio_scale=1.0,
                fold_count=1,
                capacity_exceeded_count=0,
            ),
            CapacitySensitivityEvidence(
                portfolio_scale=2.0,
                fold_count=1,
                capacity_exceeded_count=2,
            ),
            CapacitySensitivityEvidence(
                portfolio_scale=5.0,
                fold_count=1,
                capacity_exceeded_count=4,
            ),
        ),
        economic_series=(
            EconomicBacktestPoint(
                fold_id="outer-1",
                at=utc("2025-01-02T20:00:00Z"),
                equity=1.0,
                drawdown=0.0,
                cash_fraction=1.0,
                turnover=0.0,
                cumulative_cost=0.0,
            ),
        ),
        model_version="model-v1",
        strategy_version="strategy-v1",
        universe_version="universe-v1",
        split_version="split-v1",
        evaluation_window_id="outer-window-v1",
        cost_scenario="base_costs",
        sample_count=500,
        evidence_scope="research_only",
        research_only_reasons=("synthetic_offline", "survivorship_limited"),
        survivorship_limited=True,
        survivorship_limitation_reason="static_seed_universe",
    )


def test_backtest_governance_contracts_reject_inconsistent_evidence() -> None:
    fold = valid_fold_metrics()
    for field, value in (
        ("pr_auc", 1.01),
        ("expected_calibration_error", -0.01),
        ("raw_interval_coverage", 1.01),
        ("tail_failure_rate", -0.01),
    ):
        with pytest.raises(ValidationError):
            FoldMetrics.model_validate(
                {**fold.model_dump(mode="python"), field: value},
                strict=True,
            )

    with pytest.raises(ValidationError, match="interval calibration"):
        IntervalCalibrationEvidence(
            method="conformalized_quantile_regression",
            target_coverage=0.8,
            status="unavailable",
            unavailable_reason=None,
        )
    with pytest.raises(ValidationError, match="DSR probability"):
        DeflatedSharpeEvidence(
            observed_sharpe=0.5,
            expected_maximum_sharpe=0.2,
            status="available",
            probability_sharpe_is_genuine=None,
            attempted_trials=4,
            successful_trials_with_sharpe=4,
            attempted_sharpe_mean=0.1,
            attempted_sharpe_std=0.02,
            observation_count=12,
            assumptions=("Stationary returns",),
        )
    with pytest.raises(ValidationError, match="PBO probability"):
        PboEvidence(
            status="insufficient_blocks",
            probability=0.5,
            block_count=3,
            combination_count=0,
        )
    with pytest.raises(ValidationError, match="trial counts"):
        TrialLedgerEvidence(
            registered=5,
            succeeded=1,
            failed=1,
            aborted=1,
            rejected=1,
            running=0,
        )
    with pytest.raises(ValidationError, match="window consumption"):
        EvaluationWindowEvidence(
            window_id="outer-window-v1",
            role="outer_test",
            scope="research_only",
            consumption_count=0,
            consumed_at=utc("2025-07-01T00:00:00Z"),
        )


def test_backtest_read_model_binds_folds_slices_survival_and_scope() -> None:
    backtest = valid_backtest_read_model()
    payload = backtest.model_dump(mode="python")

    assert backtest.fold_metrics[0].brier_skill == 0.05
    assert {point.horizon for point in backtest.survival_calibration} == {5, 20}
    assert backtest.diagnostic_slices[0].sample_count == 120
    assert backtest.evaluation_windows[1].consumed_at is None

    invalid_updates = (
        {
            "economic_series": (
                backtest.economic_series[0].model_copy(
                    update={"fold_id": "unknown-fold"}
                ),
            )
        },
        {"fold_metrics": (backtest.fold_metrics[0], backtest.fold_metrics[0])},
        {
            "survival_calibration": tuple(
                point for point in backtest.survival_calibration if point.horizon == 5
            )
        },
        {
            "diagnostic_slices": tuple(
                item for item in backtest.diagnostic_slices if item.dimension != "year"
            )
        },
        {"research_only_reasons": ()},
        {"survivorship_limitation_reason": None},
    )
    for update in invalid_updates:
        with pytest.raises(ValidationError):
            BacktestReadModel.model_validate({**payload, **update}, strict=True)


def test_backtest_model_and_universe_read_models_are_complete() -> None:
    backtest = valid_backtest_read_model()
    assert backtest.fold_metrics[0].sample_count == 500

    matrix = ModelMatrixReadModel(
        rows=(
            ModelTaskMatrixRow(
                task="forecast_direction",
                horizon=5,
                asset_group="us_equity",
                champion_version="model-v1",
                baseline_version="baseline-v1",
                challenger_version="challenger-v1",
                calibration_version="calibration-v1",
                status="champion",
                unavailable_reason=None,
            ),
        )
    )
    assert matrix.rows[0].horizon == 5

    universes = UniverseReadModel(
        versions=(
            UniverseVersion(
                universe_version="universe-v1",
                effective_from=utc("2026-01-02T00:00:00Z"),
                effective_to=None,
                member_count=100,
                reason="liquidity_seed_refresh",
                point_in_time_status="forward_only",
            ),
        )
    )
    assert universes.versions[0].member_count == 100


def test_page_metadata_cannot_claim_fewer_rows_than_are_returned() -> None:
    instrument = InstrumentMetadata(
        symbol="US.AAPL",
        name="Apple Inc.",
        asset_type="stock",
        sector="technology",
        currency="USD",
        exchange="NASDAQ",
        effective_from=utc("2026-01-02T00:00:00Z"),
        effective_to=None,
        universe_version="universe-v1",
    )
    with pytest.raises(ValidationError, match="total"):
        InstrumentPage(
            items=(instrument,),
            total=0,
            page=1,
            page_size=25,
            sort_key="symbol",
            sort_direction=SortDirection.ASC,
        )


def test_page_cannot_return_more_rows_than_page_size() -> None:
    instrument = InstrumentMetadata(
        symbol="US.AAPL",
        name="Apple Inc.",
        asset_type="stock",
        sector="technology",
        currency="USD",
        exchange="NASDAQ",
        effective_from=utc("2026-01-02T00:00:00Z"),
        effective_to=None,
        universe_version="universe-v1",
    )
    second = instrument.model_copy(update={"symbol": "US.MSFT", "name": "Microsoft"})
    with pytest.raises(ValidationError, match="page_size"):
        InstrumentPage(
            items=(instrument, second),
            total=2,
            page=1,
            page_size=1,
            sort_key="symbol",
            sort_direction=SortDirection.ASC,
        )


def test_overview_distribution_is_a_complete_probability_partition() -> None:
    proxy = MarketProxy(
        symbol="US.SPY",
        name="SPDR S&P 500 ETF Trust",
        regime=RegimeLabel.RANGE,
        regime_probability=0.7,
        direction="flat",
        unavailable_reason=None,
        session_return=0.001,
        trend=(600.0, 601.0),
        as_of=utc("2026-07-10T20:00:00Z"),
        serving_status=ServingStatus.LIVE,
    )
    with pytest.raises(ValidationError, match="sum to 1"):
        OverviewReadModel(
            proxies=(proxy,),
            top_opportunities=(),
            regime_distribution=(
                RegimeDistribution(regime=RegimeLabel.RANGE, count=70, fraction=0.7),
                RegimeDistribution(regime=RegimeLabel.UPTREND, count=40, fraction=0.4),
            ),
            sector_regime_matrix=(
                SectorRegimeCell(
                    sector="technology",
                    regime=RegimeLabel.RANGE,
                    count=10,
                    fraction=1.0,
                ),
            ),
            market_breadth=0.52,
            abstain_count=3,
            abstain_reasons=(),
            data_confidence=0.95,
            batch_generated_at=utc("2026-07-10T20:02:00Z"),
            total_inference_latency_ms=410.0,
        )


def test_data_health_reports_independent_dimensions_and_stable_page_order() -> None:
    health = DataHealth(
        component="collector",
        dataset="daily_bars",
        symbol=None,
        serving_status=ServingStatus.DELAYED,
        connectivity=Connectivity.CONNECTED,
        freshness=Freshness.DELAYED,
        completeness=Completeness.PARTIAL,
        readiness=Readiness.DEGRADED,
        last_success_at=utc("2026-07-10T20:00:00Z"),
        expected_next_at=utc("2026-07-10T20:05:00Z"),
        coverage_rate=0.96,
        missing_rate=0.04,
        duplicate_count=0,
        quarantine_count=2,
        watermark=utc("2026-07-10T20:00:00Z"),
        quota_total=1000,
        quota_used=800,
        quota_remaining=200,
        error_class="freshness_lag",
        message="Latest batch is delayed.",
        action="Wait for the scheduled retry.",
        affected_symbols=("US.AAPL",),
        affected_datasets=("daily_bars",),
    )
    page = DataHealthReadModel(
        items=(health,),
        total=1,
        page=1,
        page_size=25,
        sort_key="serving_status",
        sort_direction=SortDirection.ASC,
    )
    assert page.items == (health,)
    with pytest.raises(ValidationError, match="quota"):
        DataHealth.model_validate(
            {**health.model_dump(), "quota_remaining": 199}, strict=True
        )
