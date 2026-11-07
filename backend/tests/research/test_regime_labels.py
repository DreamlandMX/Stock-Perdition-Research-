from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from stock_prediction.contracts.research import (
    BreakoutEvent,
    CalendarSource,
    RegimeLabel,
)
from stock_prediction.research.labels import RegimeAnchor, assign_regime_state
from stock_prediction.research.primitives import MarketPrimitives
from stock_prediction.research.thresholds import (
    RegimeThresholdArtifact,
    RegimeThresholdValues,
)


AS_OF = datetime(2026, 7, 10, 20, tzinfo=UTC)


def primitives(**updates: object) -> MarketPrimitives:
    values: dict[str, object] = {
        "symbol": "US.TEST",
        "asset_group": "stock",
        "session_date": date(2026, 7, 10),
        "source_max_available_at": AS_OF,
        "source_max_ingested_at": AS_OF + timedelta(minutes=1),
        "dataset_revision": "dataset-r1",
        "adjustment_revision": "adjustment-r1",
        "feature_set_version": "features-v1",
        "calendar_version": "test-calendar-v1",
        "calendar_hash": "b" * 64,
        "calendar_source": CalendarSource.SYNTHETIC_OFFLINE,
        "anchor_close": 100.0,
        "return_1": 0.0,
        "return_5": 0.0,
        "return_20": 0.0,
        "prior_high_20": 110.0,
        "prior_low_20": 90.0,
        "atr20": 4.0,
        "channel_width20": 0.2,
        "efficiency_ratio20": 0.1,
        "normalized_slope20": 0.0001,
        "trend_r2_20": 0.1,
        "realized_volatility20": 0.2,
        "downside_volatility20": 0.1,
        "max_drawdown20": -0.05,
        "max_up_move20": 0.02,
        "channel_position20": 0.5,
        "ma20": 100.0,
        "ma50": 100.0,
        "distance_ma20": 0.0,
        "distance_ma50": 0.0,
        "bollinger_bandwidth20": 0.05,
        "atr_percent20": 0.04,
        "volume_ratio20": 1.0,
        "turnover_ratio20": 1.0,
        "data_coverage20": 1.0,
        "missing_session_count20": 0,
        "valid_core": True,
    }
    values.update(updates)
    return MarketPrimitives(**values)


def thresholds() -> RegimeThresholdArtifact:
    return RegimeThresholdArtifact(
        threshold_version="threshold-v1",
        asset_group="stock",
        train_start=date(2025, 1, 1),
        train_end=date(2025, 12, 31),
        dataset_revision="dataset-r1",
        feature_set_version="features-v1",
        calendar_version="test-calendar-v1",
        calendar_hash="b" * 64,
        calendar_source=CalendarSource.SYNTHETIC_OFFLINE,
        quantile_method="linear",
        values=RegimeThresholdValues(
            channel_width_q25=0.25,
            channel_width_q75=0.50,
            efficiency_ratio_q25=0.20,
            efficiency_ratio_q75=0.70,
            absolute_slope_q25=0.001,
            absolute_slope_q75=0.005,
            realized_volatility_q25=0.10,
            realized_volatility_q75=0.40,
        ),
        input_hash="a" * 64,
        source_max_available_at="2025-12-31T21:00:00Z",
        sample_count=100,
        fitted_at="2026-01-01T00:00:00Z",
    )


def anchor(**updates: object) -> RegimeAnchor:
    values: dict[str, object] = {
        "symbol": "US.TEST",
        "as_of": AS_OF,
        "adjusted_high": 101.0,
        "adjusted_low": 99.0,
        "adjusted_close": 100.0,
        "complete_regular_session": True,
        "corporate_action_clean": True,
        "higher_highs": False,
        "higher_lows": False,
        "lower_highs": False,
        "lower_lows": False,
        "compression_percentile": 0.20,
        "dataset_revision": "dataset-r1",
    }
    values.update(updates)
    return RegimeAnchor(**values)


def assign(
    current: RegimeAnchor | None = None,
    primitive_values: MarketPrimitives | None = None,
    threshold_values: RegimeThresholdArtifact | None = None,
):
    return assign_regime_state(
        current or anchor(),
        primitive_values or primitives(),
        threshold_values or thresholds(),
        atr_buffer_multiplier=0.25,
        label_version="labels-v1",
    )


def test_range_and_directional_trends_use_frozen_training_thresholds() -> None:
    assert assign().state is RegimeLabel.RANGE

    up = assign(
        anchor(higher_highs=True, higher_lows=True),
        primitives(efficiency_ratio20=0.8, normalized_slope20=0.01),
    )
    down = assign(
        anchor(lower_highs=True, lower_lows=True),
        primitives(efficiency_ratio20=0.8, normalized_slope20=-0.01),
    )
    assert up.state is RegimeLabel.UPTREND
    assert down.state is RegimeLabel.DOWNTREND


def test_invalid_has_priority_and_conflicting_trend_evidence_transitions() -> None:
    invalid = assign(primitive_values=primitives(valid_core=False))
    assert invalid.state is RegimeLabel.INVALID
    assert invalid.invalid_reason == "incomplete_core_window"

    conflict = assign(
        anchor(
            higher_highs=True,
            higher_lows=True,
            lower_highs=True,
            lower_lows=True,
        ),
        primitives(efficiency_ratio20=0.8, normalized_slope20=0.01),
    )
    assert conflict.state is RegimeLabel.TRANSITION


def test_breakout_uses_close_confirmation_and_exact_atr_buffer_boundary() -> None:
    exact = assign(anchor(adjusted_high=111.0, adjusted_close=111.0))
    below = assign(anchor(adjusted_high=110.999, adjusted_close=110.999))

    assert exact.state is RegimeLabel.TRANSITION
    assert exact.breakout_event is BreakoutEvent.UP
    assert below.breakout_event is BreakoutEvent.NONE


def test_same_bar_double_touch_is_extreme_and_not_an_ordinary_direction() -> None:
    result = assign(anchor(adjusted_high=111.0, adjusted_low=89.0))

    assert result.state is RegimeLabel.TRANSITION
    assert result.breakout_event is BreakoutEvent.EXTREME_BOTH
    assert result.extreme_event is True


@pytest.mark.parametrize(
    "updates",
    [
        {"complete_regular_session": False},
        {"corporate_action_clean": False},
    ],
)
def test_incomplete_or_unclean_anchor_cannot_confirm_breakout(
    updates: dict[str, object]
) -> None:
    result = assign(anchor(adjusted_high=120.0, adjusted_close=120.0, **updates))

    assert result.state is RegimeLabel.INVALID
    assert result.breakout_event is BreakoutEvent.NONE


def test_current_state_api_has_no_future_path_and_rejects_future_primitive_source() -> None:
    future_source = AS_OF + timedelta(seconds=1)
    with pytest.raises(ValueError, match="future source"):
        assign(primitive_values=primitives(source_max_available_at=future_source))


def test_current_state_rejects_thresholds_fitted_or_available_after_anchor() -> None:
    future = AS_OF + timedelta(seconds=1)
    leaked = thresholds().model_copy(
        update={"source_max_available_at": future, "fitted_at": future}
    )

    with pytest.raises(ValueError, match="future threshold"):
        assign(threshold_values=leaked)


def test_current_state_rejects_threshold_feature_lineage_mismatch() -> None:
    with pytest.raises(ValueError, match="feature set"):
        assign(
            primitive_values=primitives(feature_set_version="features-v2")
        )
