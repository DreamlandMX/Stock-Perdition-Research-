from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

from stock_prediction.contracts.market_data import Timeframe
from stock_prediction.live.config import ALPACA_SIP_DATASET_REVISION, LivePaths
from stock_prediction.live.market_data_service import LiveMarketDataService
from stock_prediction.market_data.ports import (
    ProviderBar,
    ProviderPreflightResult,
    ProviderSession,
)


class _Provider:
    name = "alpaca"
    feed = "sip"
    delay_minutes = 15

    def __init__(self) -> None:
        self.daily_calls = 0
        self.intraday_calls = 0
        self.session = ProviderSession(
            session_date=date(2026, 8, 12),
            opens_at=datetime(2026, 8, 12, 13, 30, tzinfo=UTC),
            closes_at=datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
        )

    def preflight(
        self, *, workspace_root: Path, symbols: Sequence[str]
    ) -> ProviderPreflightResult:
        return ProviderPreflightResult(
            ready=True,
            provider="alpaca",
            feed="sip",
            provider_version="0.44.0",
            delay_minutes=15,
            symbol_count=len(symbols),
            active_tradable_count=len(symbols),
            disk_free_bytes=10 * 1024**3,
            blockers=(),
            sessions=(self.session,),
        )

    def read_daily_bars(
        self, *, symbols: Sequence[str], start: date, end: date
    ) -> tuple[ProviderBar, ...]:
        self.daily_calls += 1
        return tuple(self._bar(symbol, Timeframe.DAY_1) for symbol in symbols)

    def read_intraday_bars(
        self,
        *,
        symbols: Sequence[str],
        start: date,
        end: date,
        sessions: dict[date, ProviderSession],
    ) -> tuple[ProviderBar, ...]:
        self.intraday_calls += 1
        return tuple(self._bar(symbol, Timeframe.MINUTE_60) for symbol in symbols)

    def read_corporate_actions(
        self, *, symbols: Sequence[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]:
        return {symbol: pd.DataFrame() for symbol in symbols}

    def _bar(self, symbol: str, timeframe: Timeframe) -> ProviderBar:
        start = (
            self.session.opens_at
            if timeframe is Timeframe.MINUTE_60
            else datetime(2026, 8, 12, 4, 0, tzinfo=UTC)
        )
        return ProviderBar(
            symbol=symbol,
            timeframe=timeframe,
            session_date=self.session.session_date,
            bar_start=start,
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=1_000_000,
            turnover=101_000_000.0,
            source_available_at=self.session.closes_at.replace(minute=15),
        )


def test_yahoo_volume_scales_intraday_shape_and_preserves_daily_total() -> None:
    from stock_prediction.yahoo.volume import YahooVolumeObservation

    observed = datetime(2026, 8, 12, 20, 20, tzinfo=UTC)
    available = datetime(2026, 8, 12, 20, 15, tzinfo=UTC)
    bars = tuple(
        ProviderBar(
            symbol="US.AAPL",
            timeframe=Timeframe.MINUTE_60,
            session_date=date(2026, 8, 12),
            bar_start=datetime(2026, 8, 12, hour, 30, tzinfo=UTC),
            open=100,
            high=102,
            low=99,
            close=101,
            volume=volume,
            turnover=float(volume * 100),
            source_available_at=available,
            price_source="alpaca-iex",
            volume_source="alpaca-iex",
        )
        for hour, volume in ((13, 1), (14, 3))
    )
    yahoo = YahooVolumeObservation(
        symbol="US.AAPL",
        session_date=date(2026, 8, 12),
        volume=100,
        observed_at=observed,
        available_at=available,
    )

    result = LiveMarketDataService._apply_yahoo_volume(
        bars, {("US.AAPL", date(2026, 8, 12)): yahoo}
    )

    assert [row.volume for row in result] == [25, 75]
    assert sum(row.volume for row in result) == 100
    assert all(row.raw_volume in {1, 3} for row in result)
    assert all(row.volume_scale == 25 for row in result)
    assert [row.turnover for row in result] == [2500.0, 7500.0]
    assert all(row.volume_estimated for row in result)
    assert all(row.source_available_at == available for row in result)
    assert all(row.volume_observed_at == observed for row in result)


def test_missing_yahoo_or_zero_iex_volume_makes_only_that_day_unavailable() -> None:
    from stock_prediction.yahoo.volume import YahooVolumeObservation

    available = datetime(2026, 8, 12, 20, 15, tzinfo=UTC)
    bars = (
        ProviderBar(
            symbol="US.AAPL",
            timeframe=Timeframe.MINUTE_60,
            session_date=date(2026, 8, 11),
            bar_start=datetime(2026, 8, 11, 13, 30, tzinfo=UTC),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=10,
            turnover=1_000.0,
            source_available_at=available,
        ),
        ProviderBar(
            symbol="US.AAPL",
            timeframe=Timeframe.MINUTE_60,
            session_date=date(2026, 8, 12),
            bar_start=datetime(2026, 8, 12, 13, 30, tzinfo=UTC),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=0,
            turnover=0.0,
            source_available_at=available,
        ),
    )
    observations = {
        ("US.AAPL", date(2026, 8, 12)): YahooVolumeObservation(
            symbol="US.AAPL",
            session_date=date(2026, 8, 12),
            volume=100,
            observed_at=available,
            available_at=available,
        )
    }

    assert LiveMarketDataService._apply_yahoo_volume(bars, observations) == ()


def test_preflight_is_read_only_and_reports_provider_fields(tmp_path: Path) -> None:
    provider = _Provider()
    paths = LivePaths.for_dataset(tmp_path, ALPACA_SIP_DATASET_REVISION)
    service = LiveMarketDataService(paths, provider=provider, symbols=("US.AAPL",))

    result = service.preflight()

    assert result.ready
    assert result.provider == "alpaca"
    assert result.feed == "sip"
    assert result.symbol_count == 1
    assert provider.daily_calls == 0
    assert provider.intraday_calls == 0
    assert paths.root.is_dir()
    assert not paths.catalog.exists()
    assert not paths.ingestion.exists()


def test_bootstrap_writes_only_staged_dataset_and_is_idempotent(tmp_path: Path) -> None:
    provider = _Provider()
    paths = LivePaths.for_dataset(tmp_path, ALPACA_SIP_DATASET_REVISION)
    legacy = LivePaths.from_workspace(tmp_path)
    service = LiveMarketDataService(paths, provider=provider, symbols=("US.AAPL",))

    first = service.bootstrap(
        daily_start=date(2026, 8, 12),
        intraday_start=date(2026, 8, 12),
    )
    second = service.bootstrap(
        daily_start=date(2026, 8, 12),
        intraday_start=date(2026, 8, 12),
    )

    assert first["tasks_committed"] == 2
    assert second["tasks_skipped"] == 2
    assert provider.daily_calls == 1
    assert provider.intraday_calls == 1
    assert not legacy.root.exists()
    assert paths.catalog.is_file()
    assert paths.ingestion.is_file()
    raw_files = tuple(paths.raw.rglob("*.parquet"))
    assert raw_files
    assert all(len(path.name) <= 33 for path in raw_files)
    assert {row["timeframe"] for row in service.storage.coverage()} == {
        "1d",
        "60m",
    }
    assert len(service.storage.rehab_coverage()) == 1
