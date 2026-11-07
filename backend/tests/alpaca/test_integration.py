from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from stock_prediction.alpaca.provider import AlpacaMarketDataProvider
from stock_prediction.yahoo.volume import YahooDailyVolumeProvider


@pytest.mark.skipif(
    not (
        os.environ.get("RUN_ALPACA_IEX_YAHOO_INTEGRATION") == "1"
        and os.environ.get("APCA_API_KEY_ID")
        and os.environ.get("APCA_API_SECRET_KEY")
    ),
    reason="opt-in Alpaca IEX/Yahoo read-only integration test",
)
def test_iex_preflight_reads_assets_calendar_and_history(tmp_path: Path) -> None:
    result = AlpacaMarketDataProvider(feed="iex").preflight(
        workspace_root=tmp_path,
        symbols=("US.AAPL", "US.EWJ"),
    )

    assert result.ready
    assert result.provider == "alpaca"
    assert result.feed == "iex"
    assert result.active_tradable_count == 2
    assert result.sessions


@pytest.mark.skipif(
    os.environ.get("RUN_ALPACA_IEX_YAHOO_INTEGRATION") != "1",
    reason="opt-in Yahoo read-only integration test",
)
def test_yahoo_daily_volume_is_available() -> None:
    rows = YahooDailyVolumeProvider().read_daily_volumes(
        symbols=("US.AAPL",),
        start=date(2026, 8, 10),
        end=date(2026, 8, 12),
    )

    assert rows
