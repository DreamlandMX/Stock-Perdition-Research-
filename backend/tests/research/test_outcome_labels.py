from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from stock_prediction.contracts.research import (
    BreakoutEvent,
    CalendarSource,
    DirectionLabel,
    FalseBreakoutResolution,
    RegimeLabel,
    RegimeState,
)
from stock_prediction.research.calendar import build_trading_calendar
from stock_prediction.research.labels import (
    LabelBar,
    LabelConfig,
    LabelInputError,
    generate_label_bundle,
)


ANCHOR_AT = datetime(2026, 7, 10, 20, tzinfo=UTC)


def state(
    *,
    regime: RegimeLabel = RegimeLabel.RANGE,
    breakout: BreakoutEvent = BreakoutEvent.NONE,
) -> RegimeState:
    return RegimeState(
        symbol="US.TEST",
        as_of=ANCHOR_AT,
        state=regime,
        channel_high=110.0,
        channel_low=90.0,
        atr20=4.0,
        efficiency_ratio=0.1,
        compression_percentile=0.2,
        state_confidence=0.8,
        breakout_event=breakout,
        extreme_event=breakout is BreakoutEvent.EXTREME_BOTH,
        label_version="labels-v1",
        threshold_version="threshold-v1",
        dataset_revision="dataset-r1",
        feature_set_version="features-v1",
        calendar_version="test-calendar-v1",
        calendar_hash=calendar().input_hash,
        calendar_source=CalendarSource.SYNTHETIC_OFFLINE,
        invalid_reason=None,
    )


def calendar():
    return build_trading_calendar(
        sessions=tuple(
            date(2026, 7, 10) + timedelta(days=index)
            for index in range(21)
        ),
        calendar_version="test-calendar-v1",
        session_rule_version="test-session-v1",
        dataset_revision="dataset-r1",
        source=CalendarSource.SYNTHETIC_OFFLINE,
        source_reference_ids=("test-calendar-fixture",),
        generated_at=datetime(2026, 7, 1, tzinfo=UTC),
    )


def bar(
    index: int,
    *,
    opening: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.0,
    total_return_index: float | None = None,
    cash_distribution: float = 0.0,
    complete: bool = True,
    regime: RegimeLabel = RegimeLabel.RANGE,
) -> LabelBar:
    session_date = date(2026, 7, 10) + timedelta(days=index)
    return LabelBar(
        symbol="US.TEST",
        session_date=session_date,
        bar_at=ANCHOR_AT + timedelta(days=index),
        adjusted_open=opening,
        adjusted_high=high,
        adjusted_low=low,
        adjusted_close=close,
        total_return_index=(
            close / 100.0 if total_return_index is None else total_return_index
        ),
        cash_distribution=cash_distribution,
        complete_regular_session=complete,
        corporate_action_clean=True,
        regime_state=regime,
        dataset_revision="dataset-r1",
        adjustment_revision="adjustment-r1",
    )


def anchor_bar(*, atr_zero: bool = False) -> LabelBar:
    del atr_zero
    return LabelBar(
        symbol="US.TEST",
        session_date=date(2026, 7, 10),
        bar_at=ANCHOR_AT,
        adjusted_open=99.0,
        adjusted_high=101.0,
        adjusted_low=98.0,
        adjusted_close=100.0,
        total_return_index=1.0,
        cash_distribution=0.0,
        complete_regular_session=True,
        corporate_action_clean=True,
        regime_state=RegimeLabel.RANGE,
        dataset_revision="dataset-r1",
        adjustment_revision="adjustment-r1",
    )


def config() -> LabelConfig:
    return LabelConfig(
        label_version="labels-v1",
        atr_buffer_multiplier=0.25,
        flat_band_atr_multiplier=0.10,
        false_breakout_sessions=3,
        range_exit_confirmation_sessions=2,
    )


def generate(
    future: list[LabelBar],
    *,
    anchor_state: RegimeState | None = None,
):
    return generate_label_bundle(
        anchor_state or state(),
        anchor_bar(),
        future,
        config=config(),
        calendar=calendar(),
    )


def test_frozen_boundary_exact_equality_and_first_touch_ordering() -> None:
    future = [bar(index) for index in range(1, 21)]
    future[0] = bar(1, high=111.0, close=111.0)
    future[1] = bar(2, low=89.0, close=89.0)

    result = generate(future)

    assert result.targets[0].breakout_event is BreakoutEvent.UP
    assert result.targets[0].breakout_event_at == future[0].bar_at
    assert result.targets[1].breakout_event is BreakoutEvent.UP
    assert result.targets[2].breakout_event is BreakoutEvent.UP


def test_down_boundary_equality_and_prior_future_high_do_not_move_frozen_channel() -> None:
    future = [bar(index) for index in range(1, 21)]
    # This intraday high would raise a moving channel, but has no close confirmation.
    future[0] = bar(1, high=120.0, close=100.0)
    future[1] = bar(2, high=111.0, close=111.0)
    assert generate(future).targets[1].breakout_event is BreakoutEvent.UP

    down = [bar(index) for index in range(1, 21)]
    down[0] = bar(1, low=89.0, close=89.0)
    assert generate(down).targets[0].breakout_event is BreakoutEvent.DOWN


def test_same_future_bar_double_touch_is_extreme_both() -> None:
    future = [bar(index) for index in range(1, 21)]
    future[0] = bar(1, high=111.0, low=89.0, close=100.0)

    result = generate(future)

    assert result.targets[0].breakout_event is BreakoutEvent.EXTREME_BOTH
    assert result.targets[0].breakout_event_at == future[0].bar_at
    assert result.targets[1].range_exit is False


def test_returns_direction_dividend_and_path_excursions_keep_bases_separate() -> None:
    future = [bar(index) for index in range(1, 21)]
    future[0] = bar(
        1,
        opening=102.0,
        high=106.0,
        low=98.0,
        close=104.0,
        total_return_index=1.05,
        cash_distribution=1.0,
    )

    target = generate(future).targets[0]

    assert target.capital_gain_return == pytest.approx(0.04)
    assert target.total_return == pytest.approx(0.05)
    assert target.executable_return == pytest.approx(2 / 102)
    assert target.forecast_direction is DirectionLabel.UP
    assert target.executable_direction is DirectionLabel.UP
    assert target.forecast_mfe == pytest.approx(0.06)
    assert target.forecast_mae == pytest.approx(-0.02)
    assert target.executable_mfe == pytest.approx(4 / 102)
    assert target.executable_mae == pytest.approx(-4 / 102)


def test_zero_atr_band_still_labels_exact_zero_flat() -> None:
    zero_atr_state = state().model_copy(update={"atr20": 0.0})
    future = [bar(index) for index in range(1, 21)]

    target = generate(future, anchor_state=zero_atr_state).targets[0]

    assert target.forecast_direction is DirectionLabel.FLAT
    assert target.executable_direction is DirectionLabel.FLAT


@pytest.mark.parametrize(
    ("closes", "expected"),
    [
        ([100.0, 112.0, 112.0], FalseBreakoutResolution.IMMEDIATE_FAILURE),
        ([112.0, 100.0, 112.0], FalseBreakoutResolution.DELAYED_FAILURE),
        ([112.0, 112.0, 112.0], FalseBreakoutResolution.CONFIRMED_BREAKOUT),
    ],
)
def test_false_breakout_resolution_uses_next_three_complete_sessions(
    closes: list[float], expected: FalseBreakoutResolution
) -> None:
    future = [bar(index, close=close, high=max(close, 101.0)) for index, close in enumerate(closes, 1)]
    future.extend(bar(index) for index in range(4, 21))
    breakout_state = state(regime=RegimeLabel.TRANSITION, breakout=BreakoutEvent.UP)

    result = generate(future, anchor_state=breakout_state)

    assert result.false_breakout_resolution is expected


def test_false_breakout_is_unresolved_when_confirmation_path_is_incomplete() -> None:
    future = [
        bar(1, high=112.0, close=112.0),
        bar(2, high=112.0, close=112.0, complete=False),
    ]
    breakout_state = state(regime=RegimeLabel.TRANSITION, breakout=BreakoutEvent.UP)

    result = generate(future, anchor_state=breakout_state)

    assert result.false_breakout_resolution is FalseBreakoutResolution.UNRESOLVED


def test_range_exit_needs_breakout_or_two_non_range_sessions_and_never_h1() -> None:
    future = [bar(index) for index in range(1, 21)]
    future[1] = bar(2, regime=RegimeLabel.TRANSITION)
    future[2] = bar(3, regime=RegimeLabel.UPTREND)

    result = generate(future)

    assert result.targets[0].range_exit is None
    assert result.targets[1].range_exit is True
    assert result.targets[1].range_exit_at == future[2].bar_at


def test_incomplete_horizon_is_right_censored_without_skipping_missing_session() -> None:
    future = [bar(1), bar(2, complete=False), bar(3)]

    result = generate(future)

    assert result.targets[0].resolved is True
    assert result.targets[1].resolved is False
    assert result.targets[1].forecast_direction is DirectionLabel.UNRESOLVED
    assert result.targets[1].breakout_event is BreakoutEvent.UNRESOLVED
    assert result.targets[1].range_exit is None


def test_future_path_changes_outcomes_but_frozen_anchor_evidence_is_unchanged() -> None:
    flat = [bar(index) for index in range(1, 21)]
    rally = [bar(index, high=120.0, close=120.0) for index in range(1, 21)]

    first = generate(flat)
    second = generate(rally)

    assert first.frozen_channel_high == second.frozen_channel_high == 110.0
    assert first.frozen_channel_low == second.frozen_channel_low == 90.0
    assert first.frozen_atr20 == second.frozen_atr20 == 4.0
    assert first.targets[0].forecast_direction is DirectionLabel.FLAT
    assert second.targets[0].forecast_direction is DirectionLabel.UP


def test_rejects_wrong_symbol_duplicate_or_pre_anchor_future_rows() -> None:
    wrong = bar(1).model_copy(update={"symbol": "US.OTHER"})
    with pytest.raises(LabelInputError, match="symbol"):
        generate([wrong])
    with pytest.raises(LabelInputError, match="unique"):
        generate([bar(1), bar(1)])
    with pytest.raises(LabelInputError, match="after anchor"):
        generate([anchor_bar()])

    wrong_revision = bar(1).model_copy(update={"dataset_revision": "dataset-r2"})
    with pytest.raises(LabelInputError, match="dataset revision"):
        generate([wrong_revision])

    with pytest.raises(LabelInputError, match="calendar prefix"):
        generate([bar(1), bar(3)])

    wrong_calendar_state = state().model_copy(update={"calendar_hash": "c" * 64})
    with pytest.raises(LabelInputError, match="trading calendar"):
        generate([bar(1)], anchor_state=wrong_calendar_state)
