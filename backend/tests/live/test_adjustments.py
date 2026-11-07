from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from stock_prediction.live.adjustments import (
    apply_forward_adjustment,
    build_point_in_time_adjusted_bars,
    window_has_quarantine,
)
from stock_prediction.live.features import _first_breakout


def _bars(closes: list[float]) -> pd.DataFrame:
    sessions = pd.date_range("2026-01-02", periods=len(closes), freq="B")
    return pd.DataFrame(
        {
            "symbol": "US.TEST",
            "session_date": sessions.date,
            "open": closes,
            "high": np.asarray(closes) * 1.01,
            "low": np.asarray(closes) * 0.99,
            "close": closes,
            "volume": [200.0] * len(closes),
            "turnover": [20_000.0] * len(closes),
        }
    )


def test_forward_adjustment_uses_documented_affine_formula() -> None:
    assert apply_forward_adjustment(100.0, 0.5, 1.25) == pytest.approx(51.25)


def test_split_is_applied_only_on_effective_date_and_preserves_raw_bars() -> None:
    bars = _bars([100.0, 50.0, 51.0])
    raw_copy = bars.copy(deep=True)
    actions = pd.DataFrame(
        [
            {
                "symbol": "US.TEST",
                "ex_div_date": date(2026, 1, 5),
                "split_ratio": 0.5,
                "forward_adj_factor_a": 0.5,
                "forward_adj_factor_b": 0.0,
            }
        ]
    )

    adjusted = build_point_in_time_adjusted_bars(bars, actions)

    pd.testing.assert_frame_equal(bars, raw_copy)
    assert adjusted.loc[0, "close"] == pytest.approx(100.0)
    assert adjusted.loc[1, "close"] == pytest.approx(100.0)
    assert adjusted.loc[1, "capital_gain_return_1"] == pytest.approx(0.0)
    assert adjusted.loc[2, "capital_gain_return_1"] == pytest.approx(0.02)
    assert adjusted.loc[0, "volume"] == pytest.approx(200.0)
    assert adjusted.loc[1, "volume"] == pytest.approx(100.0)
    assert adjusted["turnover"].tolist() == [20_000.0] * 3
    assert adjusted["quality_quarantined"].sum() == 0


def test_future_action_does_not_change_an_earlier_information_set() -> None:
    bars = _bars([100.0, 102.0])
    future_action = pd.DataFrame(
        [
            {
                "symbol": "US.TEST",
                "ex_div_date": date(2026, 2, 2),
                "split_ratio": 0.5,
                "forward_adj_factor_a": 0.5,
                "forward_adj_factor_b": 0.0,
            }
        ]
    )

    adjusted = build_point_in_time_adjusted_bars(bars, future_action)

    assert adjusted["close"].tolist() == pytest.approx([100.0, 102.0])
    assert adjusted["corporate_action_effective"].tolist() == [False, False]


def test_unexplained_large_return_is_quarantined_and_invalidates_future_label() -> None:
    adjusted = build_point_in_time_adjusted_bars(
        _bars([100.0, 40.0, 41.0]), pd.DataFrame()
    )

    assert adjusted.loc[1, "quality_quarantined"]
    assert (
        adjusted.loc[1, "quality_failure_code"]
        == "unexplained_absolute_return_gt_50pct"
    )
    assert window_has_quarantine([False, True, False], horizon=2).tolist() == [
        True,
        False,
        False,
    ]


def test_breakout_uses_first_touch_and_excludes_same_day_two_sided_hits() -> None:
    future_highs = pd.DataFrame([[11.0, 9.0], [11.0, 9.0], [9.0, 11.0]])
    future_lows = pd.DataFrame([[8.0, 8.0], [6.0, 8.0], [6.0, 8.0]])

    direction, event_day, ambiguous = _first_breakout(
        future_highs,
        future_lows,
        upper=pd.Series([10.0, 10.0, 10.0]),
        lower=pd.Series([7.0, 7.0, 7.0]),
    )

    assert direction[0] == 1.0
    assert event_day[0] == 1.0
    assert np.isnan(direction[1])
    assert np.isnan(event_day[1])
    assert ambiguous.tolist() == [False, True, False]
    assert direction[2] == -1.0
    assert event_day[2] == 1.0
