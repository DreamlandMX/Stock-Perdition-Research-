from __future__ import annotations

import pandas as pd

from stock_prediction.live.provider_comparison import compare_provider_frames


def _frame(close_scale: float = 1.0, volume_scale: float = 1.0) -> pd.DataFrame:
    dates = pd.date_range("2026-01-02", periods=40, freq="B").date
    close = pd.Series([100.0 + index for index in range(40)]) * close_scale
    return pd.DataFrame(
        {
            "symbol": "US.AAPL",
            "session_date": dates,
            "close": close,
            "volume": pd.Series([1_000_000 + index * 100 for index in range(40)])
            * volume_scale,
        }
    )


def test_provider_comparison_passes_identical_daily_history() -> None:
    result = compare_provider_frames(_frame(), _frame())

    assert result["status"] == "pass"
    assert result["coverage_ratio"] == 1.0
    assert result["return_correlation"] == 1.0
    assert result["close_diff_bps_median"] == 0.0
    assert result["volume_diff_median"] == 0.0


def test_provider_comparison_fails_material_volume_difference() -> None:
    result = compare_provider_frames(_frame(), _frame(volume_scale=0.90))

    assert result["status"] == "fail"
    assert "volume_diff_median" in result["failed_gates"]
