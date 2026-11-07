"""Provider-neutral contracts for the live market-data writer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

import pandas as pd

from stock_prediction.contracts.market_data import Timeframe


@dataclass(frozen=True, slots=True)
class ProviderSession:
    session_date: date
    opens_at: datetime
    closes_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderBar:
    symbol: str
    timeframe: Timeframe
    session_date: date
    bar_start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover: float | None
    source_available_at: datetime
    price_source: str = "unknown"
    volume_source: str = "unknown"
    raw_volume: int | None = None
    volume_scale: float | None = None
    volume_estimated: bool = False
    volume_observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProviderPreflightResult:
    ready: bool
    provider: str
    feed: str
    provider_version: str
    delay_minutes: int
    symbol_count: int
    active_tradable_count: int
    disk_free_bytes: int
    blockers: tuple[str, ...]
    sessions: tuple[ProviderSession, ...]
    instrument_metadata: tuple[dict[str, object], ...] = ()

    @property
    def end(self) -> date:
        if not self.sessions:
            raise RuntimeError("provider_calendar_unavailable")
        return max(item.session_date for item in self.sessions)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "ready" if self.ready else "blocked",
            "provider": self.provider,
            "feed": self.feed,
            "provider_version": self.provider_version,
            "delay_minutes": self.delay_minutes,
            "symbol_count": self.symbol_count,
            "active_tradable_count": self.active_tradable_count,
            "disk_free_bytes": self.disk_free_bytes,
            "blockers": list(self.blockers),
            "requests_history": False,
        }


class MarketDataProvider(Protocol):
    name: str
    feed: str
    delay_minutes: int

    def preflight(
        self, *, workspace_root: Path, symbols: Sequence[str]
    ) -> ProviderPreflightResult: ...

    def read_daily_bars(
        self, *, symbols: Sequence[str], start: date, end: date
    ) -> Sequence[ProviderBar]: ...

    def read_intraday_bars(
        self,
        *,
        symbols: Sequence[str],
        start: date,
        end: date,
        sessions: Mapping[date, ProviderSession],
    ) -> Sequence[ProviderBar]: ...

    def read_corporate_actions(
        self, *, symbols: Sequence[str], start: date, end: date
    ) -> Mapping[str, pd.DataFrame]: ...


__all__ = [
    "MarketDataProvider",
    "ProviderBar",
    "ProviderPreflightResult",
    "ProviderSession",
]
