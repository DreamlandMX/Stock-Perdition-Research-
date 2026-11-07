"""Read-only Alpaca market-data adapters with session aggregation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as datetime_time, timedelta
import math
import os
from pathlib import Path
import shutil
import time
from typing import Any, cast
from zoneinfo import ZoneInfo

import alpaca
import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.enums import CorporateActionsType
from alpaca.data.historical.corporate_actions import CorporateActionsClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import CorporateActionsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.timeframe import TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest, GetCalendarRequest

from stock_prediction.contracts.market_data import Timeframe
from stock_prediction.market_data.ports import (
    ProviderBar,
    ProviderPreflightResult,
    ProviderSession,
)

_MIN_FREE_BYTES = 2 * 1024**3


class AlpacaProviderError(RuntimeError):
    """Safe provider failure that never embeds credentials or response bodies."""


class AlpacaAuthenticationError(AlpacaProviderError):
    pass


@dataclass(frozen=True, slots=True)
class AlpacaRawBar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None


def to_alpaca_symbol(symbol: str) -> str:
    if not symbol.startswith("US.") or len(symbol) <= 3:
        raise ValueError("invalid_us_symbol")
    return symbol[3:]


def from_alpaca_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("invalid_alpaca_symbol")
    return f"US.{normalized}"


def resample_regular_session(
    bars: Sequence[AlpacaRawBar], *, session: ProviderSession
) -> tuple[ProviderBar, ...]:
    """Aggregate 30-minute bars into OpenD-compatible 09:30 buckets."""

    ordered = sorted(
        (
            bar
            for bar in bars
            if session.opens_at <= bar.timestamp <= session.closes_at
        ),
        key=lambda bar: bar.timestamp,
    )
    grouped: dict[datetime, list[AlpacaRawBar]] = {}
    for bar in ordered:
        effective = (
            session.closes_at - timedelta(microseconds=1)
            if bar.timestamp == session.closes_at
            else bar.timestamp
        )
        elapsed = effective - session.opens_at
        bucket = session.opens_at + timedelta(
            hours=max(0, int(elapsed.total_seconds() // 3600))
        )
        grouped.setdefault(bucket, []).append(bar)
    result: list[ProviderBar] = []
    for bucket, members in sorted(grouped.items()):
        end = min(bucket + timedelta(hours=1), session.closes_at)
        turnover = sum(
            float(member.vwap) * member.volume
            for member in members
            if member.vwap is not None
        )
        result.append(
            ProviderBar(
                symbol=from_alpaca_symbol(members[0].symbol),
                timeframe=Timeframe.MINUTE_60,
                session_date=session.session_date,
                bar_start=bucket.astimezone(UTC),
                open=members[0].open,
                high=max(member.high for member in members),
                low=min(member.low for member in members),
                close=members[-1].close,
                volume=sum(member.volume for member in members),
                turnover=turnover if turnover > 0 else None,
                source_available_at=(end + timedelta(minutes=15)).astimezone(UTC),
                price_source="alpaca",
                volume_source="alpaca",
                raw_volume=sum(member.volume for member in members),
            )
        )
    return tuple(result)


class AlpacaMarketDataProvider:
    name = "alpaca"
    delay_minutes = 15

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        stock_client: Any | None = None,
        reference_client: Any | None = None,
        corporate_actions_client: Any | None = None,
        sleep: Any = time.sleep,
        clock: Any = lambda: datetime.now(UTC),
        feed: str = "iex",
    ) -> None:
        self.environ = os.environ if environ is None else environ
        self._stock_client = stock_client
        self._reference_client = reference_client
        self._corporate_actions_client = corporate_actions_client
        self._sleep = sleep
        self._clock = clock
        normalized_feed = feed.strip().lower()
        if normalized_feed not in {"iex", "sip"}:
            raise ValueError("unsupported_alpaca_feed")
        self.feed = normalized_feed

    @property
    def _data_feed(self) -> DataFeed:
        return DataFeed.IEX if self.feed == "iex" else DataFeed.SIP

    def _credentials(self) -> tuple[str, str]:
        key = self.environ.get("APCA_API_KEY_ID", "")
        secret = self.environ.get("APCA_API_SECRET_KEY", "")
        if not key or not secret:
            raise AlpacaAuthenticationError("alpaca_credentials_unavailable")
        return key, secret

    def _client(self) -> Any:
        key, secret = self._credentials()
        if self._stock_client is None:
            self._stock_client = StockHistoricalDataClient(key, secret)
        return self._stock_client

    def _references(self) -> Any:
        key, secret = self._credentials()
        if self._reference_client is None:
            self._reference_client = TradingClient(key, secret, paper=True)
        return self._reference_client

    def _actions(self) -> Any:
        key, secret = self._credentials()
        if self._corporate_actions_client is None:
            self._corporate_actions_client = CorporateActionsClient(key, secret)
        return self._corporate_actions_client

    def preflight(
        self, *, workspace_root: Path, symbols: Sequence[str]
    ) -> ProviderPreflightResult:
        self._credentials()
        free = shutil.disk_usage(workspace_root).free
        blockers: list[str] = []
        if free < _MIN_FREE_BYTES:
            blockers.append("disk_space_insufficient")
        try:
            assets = cast(Sequence[Any], self._call(
                lambda: self._references().get_all_assets(
                    GetAssetsRequest(
                        status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY
                    )
                )
            ))
            requested = {to_alpaca_symbol(symbol) for symbol in symbols}
            active = {
                str(getattr(asset, "symbol", ""))
                for asset in assets
                if str(getattr(asset, "status", "")).lower().endswith("active")
                and getattr(asset, "tradable", False) is True
            }
            active_count = len(requested & active)
            metadata: tuple[dict[str, object], ...] = tuple(
                {
                    "symbol": from_alpaca_symbol(str(getattr(asset, "symbol", ""))),
                    "name": str(getattr(asset, "name", "")).strip(),
                }
                for asset in assets
                if str(getattr(asset, "symbol", "")) in requested
                and str(getattr(asset, "name", "")).strip()
            )
            if active_count != len(requested):
                blockers.append("assets_not_active_tradable")
            today = self._clock().date()
            calendar = cast(Sequence[Any], self._call(
                lambda: self._references().get_calendar(
                    GetCalendarRequest(
                        start=date(2018, 1, 1),
                        end=today,
                    )
                )
            ))
            now = self._clock()
            sessions = tuple(
                session
                for session in (_provider_session(item) for item in calendar)
                if session.closes_at + timedelta(minutes=self.delay_minutes) <= now
            )
            if not sessions:
                blockers.append("calendar_unavailable")
            probe_end = max(item.session_date for item in sessions)
            probe_start = probe_end - timedelta(days=7)
            probe = StockBarsRequest(
                symbol_or_symbols=[to_alpaca_symbol(symbols[0])],
                timeframe=TimeFrame.Day,
                start=datetime.combine(probe_start, datetime.min.time(), UTC),
                end=datetime.combine(probe_end + timedelta(days=1), datetime.min.time(), UTC),
                adjustment=Adjustment.RAW,
                feed=self._data_feed,
                limit=1,
            )
            self._call(lambda: self._client().get_stock_bars(probe))
        except AlpacaProviderError:
            raise
        except Exception:
            raise AlpacaProviderError("alpaca_preflight_failed") from None
        return ProviderPreflightResult(
            ready=not blockers,
            provider=self.name,
            feed=self.feed,
            provider_version=getattr(alpaca, "__version__", "unknown"),
            delay_minutes=self.delay_minutes,
            symbol_count=len(symbols),
            active_tradable_count=active_count,
            disk_free_bytes=free,
            blockers=tuple(blockers),
            sessions=sessions,
            instrument_metadata=metadata,
        )

    def read_daily_bars(
        self, *, symbols: Sequence[str], start: date, end: date
    ) -> tuple[ProviderBar, ...]:
        request = StockBarsRequest(
            symbol_or_symbols=[to_alpaca_symbol(symbol) for symbol in symbols],
            timeframe=TimeFrame.Day,
            start=datetime.combine(start, datetime.min.time(), UTC),
            end=datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC),
            adjustment=Adjustment.RAW,
            feed=self._data_feed,
        )
        response = self._call(lambda: self._client().get_stock_bars(request))
        return self._normalize_daily_response(response, feed=self.feed)

    def read_intraday_bars(
        self,
        *,
        symbols: Sequence[str],
        start: date,
        end: date,
        sessions: Mapping[date, ProviderSession],
    ) -> tuple[ProviderBar, ...]:
        request = StockBarsRequest(
            symbol_or_symbols=[to_alpaca_symbol(symbol) for symbol in symbols],
            timeframe=TimeFrame(30, TimeFrameUnit.Minute),
            start=datetime.combine(start, datetime.min.time(), UTC),
            end=datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC),
            adjustment=Adjustment.RAW,
            feed=self._data_feed,
        )
        response = self._call(lambda: self._client().get_stock_bars(request))
        grouped: dict[tuple[str, date], list[AlpacaRawBar]] = {}
        new_york = ZoneInfo("America/New_York")
        for raw in _iter_response_bars(response):
            local_date = raw.timestamp.astimezone(new_york).date()
            session = sessions.get(local_date)
            if session is not None and not (
                session.opens_at <= raw.timestamp <= session.closes_at
            ):
                session = None
            if session is not None:
                grouped.setdefault((raw.symbol, session.session_date), []).append(raw)
        result: list[ProviderBar] = []
        for (_, session_date), members in sorted(grouped.items()):
            result.extend(
                resample_regular_session(members, session=sessions[session_date])
            )
        return tuple(result)

    def read_corporate_actions(
        self, *, symbols: Sequence[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]:
        request = CorporateActionsRequest(
            symbols=[to_alpaca_symbol(symbol) for symbol in symbols],
            types=[
                CorporateActionsType.FORWARD_SPLIT,
                CorporateActionsType.REVERSE_SPLIT,
                CorporateActionsType.CASH_DIVIDEND,
            ],
            start=start,
            end=end,
            limit=1000,
        )
        response = self._call(
            lambda: self._actions().get_corporate_actions(request)
        )
        data = getattr(response, "data", response)
        rows: dict[str, list[dict[str, object]]] = {
            symbol: [] for symbol in symbols
        }
        if isinstance(data, Mapping):
            for kind, actions in data.items():
                if not isinstance(actions, Sequence):
                    continue
                for action in actions:
                    symbol = from_alpaca_symbol(str(getattr(action, "symbol", "")))
                    if symbol not in rows:
                        continue
                    ex_date = getattr(action, "ex_date", None)
                    if not isinstance(ex_date, date):
                        continue
                    new_rate = _optional_number(getattr(action, "new_rate", None))
                    old_rate = _optional_number(getattr(action, "old_rate", None))
                    is_split = "split" in str(kind)
                    factor = (
                        old_rate / new_rate
                        if is_split
                        and new_rate is not None
                        and old_rate is not None
                        and new_rate > 0
                        else 1.0
                    )
                    cash = _optional_number(getattr(action, "rate", None))
                    rows[symbol].append(
                        {
                            "ex_div_date": ex_date,
                            "split_ratio": factor if is_split else None,
                            "per_cash_div": cash,
                            "special_dividend": None,
                            "forward_adj_factor_a": factor,
                            "forward_adj_factor_b": -cash if cash is not None else 0.0,
                            "backward_adj_factor_a": (
                                1.0 / factor if factor > 0 else 1.0
                            ),
                            "backward_adj_factor_b": cash or 0.0,
                        }
                    )
        return {symbol: pd.DataFrame(values) for symbol, values in rows.items()}

    def _call(self, operation: Any) -> object:
        for attempt in range(5):
            try:
                return operation()
            except Exception as error:
                status = _http_status(error)
                if status in {401, 403}:
                    raise AlpacaAuthenticationError(
                        "alpaca_authentication_failed"
                    ) from None
                retryable = status == 429 or (status is not None and status >= 500)
                if not retryable or attempt == 4:
                    raise AlpacaProviderError("alpaca_market_data_failed") from None
                self._sleep(_retry_delay(error, attempt))
        raise AlpacaProviderError("alpaca_market_data_failed")

    @staticmethod
    def _normalize_daily_response(
        response: object, *, feed: str = "iex"
    ) -> tuple[ProviderBar, ...]:
        result: list[ProviderBar] = []
        for raw in _iter_response_bars(response):
            timestamp = raw.timestamp.astimezone(UTC)
            session_date = timestamp.astimezone(ZoneInfo("America/New_York")).date()
            available_at = datetime.combine(
                session_date,
                datetime_time(16, 15),
                ZoneInfo("America/New_York"),
            ).astimezone(UTC)
            result.append(
                ProviderBar(
                    symbol=from_alpaca_symbol(raw.symbol),
                    timeframe=Timeframe.DAY_1,
                    session_date=session_date,
                    bar_start=timestamp,
                    open=raw.open,
                    high=raw.high,
                    low=raw.low,
                    close=raw.close,
                    volume=raw.volume,
                    turnover=(
                        raw.vwap * raw.volume if raw.vwap is not None else None
                    ),
                    source_available_at=available_at,
                    price_source=f"alpaca-{feed}",
                    volume_source=f"alpaca-{feed}",
                    raw_volume=raw.volume,
                    volume_estimated=False,
                )
            )
        return tuple(result)


def _iter_response_bars(response: object) -> tuple[AlpacaRawBar, ...]:
    data = getattr(response, "data", response)
    if not isinstance(data, Mapping):
        return ()
    result: list[AlpacaRawBar] = []
    for symbol, values in data.items():
        if not isinstance(values, Sequence):
            continue
        for value in values:
            timestamp = getattr(value, "timestamp", None)
            if not isinstance(timestamp, datetime):
                continue
            if _is_unavailable_bar(value):
                continue
            raw = AlpacaRawBar(
                symbol=str(symbol),
                timestamp=timestamp.astimezone(UTC),
                open=_positive(getattr(value, "open", None)),
                high=_positive(getattr(value, "high", None)),
                low=_positive(getattr(value, "low", None)),
                close=_positive(getattr(value, "close", None)),
                volume=_volume(getattr(value, "volume", None)),
                vwap=_optional_positive(getattr(value, "vwap", None)),
            )
            result.append(raw)
    return tuple(result)


def _is_unavailable_bar(value: object) -> bool:
    """Drop provider-declared no-trade placeholders and malformed zero prices."""

    volume = getattr(value, "volume", None)
    prices = tuple(getattr(value, field, None) for field in ("open", "high", "low", "close"))
    try:
        parsed_volume = float(volume)  # type: ignore[arg-type]
        parsed_prices = tuple(float(item) for item in prices)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if parsed_volume == 0:
        return True
    return any(item == 0 for item in parsed_prices)


def _positive(value: object) -> float:
    converted = float(value)  # type: ignore[arg-type]
    if not math.isfinite(converted) or converted <= 0:
        raise AlpacaProviderError("alpaca_invalid_bar")
    return converted


def _optional_positive(value: object) -> float | None:
    if value is None:
        return None
    return _positive(value)


def _optional_number(value: object) -> float | None:
    if value is None:
        return None
    try:
        converted = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _volume(value: object) -> int:
    converted = float(value)  # type: ignore[arg-type]
    if not math.isfinite(converted) or converted < 0 or not converted.is_integer():
        raise AlpacaProviderError("alpaca_invalid_bar")
    return int(converted)


def _http_status(error: Exception) -> int | None:
    direct = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    candidate = direct if direct is not None else getattr(response, "status_code", None)
    return candidate if isinstance(candidate, int) else None


def _provider_session(value: object) -> ProviderSession:
    session_date = getattr(value, "date", None)
    opens_at = getattr(value, "open", None)
    closes_at = getattr(value, "close", None)
    if (
        not isinstance(session_date, date)
        or not isinstance(opens_at, datetime)
        or not isinstance(closes_at, datetime)
    ):
        raise AlpacaProviderError("alpaca_invalid_calendar")
    new_york = ZoneInfo("America/New_York")
    if opens_at.tzinfo is None:
        opens_at = opens_at.replace(tzinfo=new_york)
    if closes_at.tzinfo is None:
        closes_at = closes_at.replace(tzinfo=new_york)
    return ProviderSession(
        session_date=session_date,
        opens_at=opens_at.astimezone(UTC),
        closes_at=closes_at.astimezone(UTC),
    )


def _retry_delay(error: Exception, attempt: int) -> float:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if isinstance(headers, Mapping):
        raw = headers.get("Retry-After")
        try:
            parsed = float(str(raw)) if raw is not None else 0.0
        except (TypeError, ValueError):
            parsed = 0.0
        if parsed > 0:
            return min(parsed, 60.0)
    return float(2**attempt)


__all__ = [
    "AlpacaAuthenticationError",
    "AlpacaProviderError",
    "AlpacaRawBar",
    "AlpacaMarketDataProvider",
    "AlpacaSipProvider",
    "ProviderSession",
    "from_alpaca_symbol",
    "resample_regular_session",
    "to_alpaca_symbol",
]


class AlpacaSipProvider(AlpacaMarketDataProvider):
    """Compatibility adapter for the unactivated SIP staged dataset."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(feed="sip", **kwargs)
