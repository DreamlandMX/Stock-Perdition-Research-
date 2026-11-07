from datetime import date

import pytest
from pydantic import ValidationError

from stock_prediction.contracts.research import (
    ArtifactStatus,
    BreakoutEvent,
    CalendarSource,
    DirectionLabel,
    FalseBreakoutResolution,
    FeatureArtifact,
    FeatureVector,
    FundamentalObservation,
    HorizonTarget,
    LabelBundle,
    PointInTimeEligibility,
    RegimeLabel,
    RegimeState,
)


def test_fundamental_observation_requires_ordered_point_in_time_evidence() -> None:
    observation = FundamentalObservation(
        observation_id="fund-aapl-revenue-2026q2-r1",
        event_id="fund-aapl-revenue-2026q2",
        revision_no=1,
        symbol="US.AAPL",
        metric="revenue",
        value_numeric=100.0,
        value_text=None,
        unit="USD_millions",
        report_period=date(2026, 6, 30),
        published_at="2026-07-30T20:05:00Z",
        source_available_at="2026-07-30T20:05:00Z",
        ingested_at="2026-07-30T20:06:00Z",
        source_field="income_statement.revenue",
        point_in_time_eligibility=PointInTimeEligibility.ELIGIBLE,
        raw_batch_id="fundamental-batch-1",
        schema_version="fundamental-v1",
        dataset_revision="fundamental-r1",
    )

    assert observation.point_in_time_eligible is True
    assert FundamentalObservation.model_validate_json(
        observation.model_dump_json()
    ) == observation

    with pytest.raises(ValidationError, match="published_at"):
        FundamentalObservation(
            **{
                **observation.model_dump(),
                "published_at": None,
                "point_in_time_eligibility": PointInTimeEligibility.ELIGIBLE,
            }
        )


def test_forward_only_fundamental_may_lack_publication_but_not_claim_eligibility() -> None:
    observation = FundamentalObservation(
        observation_id="snapshot-aapl-pe-r1",
        event_id="snapshot-aapl-pe",
        revision_no=1,
        symbol="US.AAPL",
        metric="pe_ratio",
        value_numeric=31.2,
        value_text=None,
        unit="ratio",
        report_period=None,
        published_at=None,
        source_available_at="2026-07-30T20:05:00Z",
        ingested_at="2026-07-30T20:06:00Z",
        source_field="snapshot.pe_ratio",
        point_in_time_eligibility=PointInTimeEligibility.FORWARD_ONLY,
        raw_batch_id="snapshot-batch-1",
        schema_version="fundamental-v1",
        dataset_revision="fundamental-r1",
    )

    assert observation.point_in_time_eligible is False


def test_regime_state_enforces_invalid_reason_and_frozen_evidence() -> None:
    state = RegimeState(
        symbol="US.SPY",
        as_of="2026-07-10T20:00:00Z",
        state=RegimeLabel.RANGE,
        channel_high=625.0,
        channel_low=600.0,
        atr20=5.0,
        efficiency_ratio=0.15,
        compression_percentile=0.1,
        state_confidence=0.8,
        breakout_event=BreakoutEvent.NONE,
        extreme_event=False,
        label_version="regime-v1",
        threshold_version="threshold-v1",
        dataset_revision="dataset-r1",
        feature_set_version="core-v1",
        calendar_version="test-calendar-v1",
        calendar_hash="b" * 64,
        calendar_source=CalendarSource.SYNTHETIC_OFFLINE,
        invalid_reason=None,
    )

    assert RegimeState.model_validate_json(state.model_dump_json()) == state
    with pytest.raises(ValidationError, match="invalid_reason"):
        RegimeState(
            **{
                **state.model_dump(),
                "state": RegimeLabel.INVALID,
                "invalid_reason": None,
            }
        )


def test_feature_vector_rejects_future_sources_and_non_finite_values() -> None:
    vector = FeatureVector(
        symbol="US.SPY",
        as_of="2026-07-10T20:00:00Z",
        horizon=5,
        values={"return_5d": 0.02, "asset_type": "etf", "event_flag": False},
        feature_set_version="core-v1",
        universe_version="universe-v1",
        dataset_revision="dataset-r1",
        adjustment_revision="adjustment-r1",
        threshold_version="threshold-v1",
        calendar_version="test-calendar-v1",
        calendar_hash="b" * 64,
        calendar_source=CalendarSource.SYNTHETIC_OFFLINE,
        feature_lineage_hash="f" * 64,
        feature_content_hash="c" * 64,
        feature_values_hash="d" * 64,
        source_max_available_at="2026-07-10T20:00:00Z",
        source_max_ingested_at="2026-07-10T20:01:00Z",
        data_quality_score=0.95,
        missing_flags={"turnover_missing": False},
        ood_inputs={"robust_distance": 1.2},
    )

    assert FeatureVector.model_validate_json(vector.model_dump_json()) == vector
    with pytest.raises(ValidationError, match="source_max_available_at"):
        FeatureVector(
            **{
                **vector.model_dump(),
                "source_max_available_at": "2026-07-10T20:00:01Z",
            }
        )
    with pytest.raises(ValidationError):
        FeatureVector(
            **{**vector.model_dump(), "values": {"return_5d": float("nan")}}
        )


def target(horizon: int) -> HorizonTarget:
    return HorizonTarget(
        horizon=horizon,
        forecast_direction=DirectionLabel.UP,
        executable_direction=DirectionLabel.UP,
        capital_gain_return=0.02,
        total_return=0.021,
        executable_return=0.015,
        forecast_mfe=0.03,
        forecast_mae=-0.01,
        executable_mfe=0.025,
        executable_mae=-0.012,
        breakout_event=BreakoutEvent.NONE,
        breakout_event_at=None,
        range_exit=None if horizon == 1 else False,
        range_exit_at=None,
        label_end_at=f"2026-08-{horizon + 1:02d}T20:00:00Z",
        resolved=True,
    )


def test_label_bundle_requires_exact_horizons_and_no_one_day_range_exit() -> None:
    bundle = LabelBundle(
        symbol="US.SPY",
        anchor_at="2026-07-31T20:00:00Z",
        anchor_state=RegimeLabel.RANGE,
        frozen_channel_high=625.0,
        frozen_channel_low=600.0,
        frozen_atr20=5.0,
        atr_buffer_multiplier=0.25,
        false_breakout_resolution=FalseBreakoutResolution.NOT_APPLICABLE,
        false_breakout_event_at=None,
        targets=(target(1), target(5), target(20)),
        label_version="label-v1",
        threshold_version="threshold-v1",
        dataset_revision="dataset-r1",
        adjustment_revision="adjustment-r1",
        feature_set_version="core-v1",
        calendar_version="test-calendar-v1",
        calendar_hash="b" * 64,
        calendar_source=CalendarSource.SYNTHETIC_OFFLINE,
    )

    assert LabelBundle.model_validate_json(bundle.model_dump_json()) == bundle
    with pytest.raises(ValidationError, match="horizons"):
        LabelBundle(**{**bundle.model_dump(), "targets": (target(1), target(5))})
    with pytest.raises(ValidationError, match="range_exit"):
        HorizonTarget(**{**target(1).model_dump(), "range_exit": False})


def test_feature_artifact_binds_dependencies_and_invalidation_reason() -> None:
    artifact = FeatureArtifact(
        artifact_id="feature-spy-20260710-core-v1",
        dataset_revision="dataset-r1",
        feature_set_version="core-v1",
        symbol="US.SPY",
        as_of_start="2026-07-01T20:00:00Z",
        as_of_end="2026-07-10T20:00:00Z",
        horizon=None,
        input_hash="a" * 64,
        content_hash="c" * 64,
        dependency_hashes={"daily-bars": "b" * 64},
        schema_hash="c" * 64,
        row_count=8,
        generated_at="2026-07-10T20:02:00Z",
        status=ArtifactStatus.CURRENT,
        affected_start=None,
        affected_end=None,
        invalidation_reason=None,
        source_max_available_at="2026-07-10T20:00:00Z",
        source_max_ingested_at="2026-07-10T20:01:00Z",
    )

    assert FeatureArtifact.model_validate_json(artifact.model_dump_json()) == artifact
    with pytest.raises(ValidationError, match="invalidation_reason"):
        FeatureArtifact(
            **{
                **artifact.model_dump(),
                "status": ArtifactStatus.INVALIDATED,
                "invalidation_reason": None,
            }
        )
