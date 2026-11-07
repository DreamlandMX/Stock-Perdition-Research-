"""Read-only Yahoo daily-volume client with sanitized bounded retries."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import time
from typing import Any
from zoneinfo import ZoneInfo

import httpx


class YahooVolumeError(RuntimeError):
    """A sanitized auxiliary-volume failure."""


@dataclass(frozen=True, slots=True)
class YahooVolumeObservation:
    symbol: str
    session_date: date
    volume: int
    observed_at: datetime
    available_at: datetime


class YahooDailyVolumeProvider:
    name = "yahoo"

    def __init__(
        self,
        *,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client or httpx.Client(
            timeout=30.0,
            headers={"User-Agent": "PredictionStock/1.0 read-only-volume"},
        )
        self._sleep = sleep
        self._clock = clock

    def read_daily_volumes(
        self, *, symbols: Sequence[str], start: date, end: date
    ) -> Mapping[tuple[str, date], YahooVolumeObservation]:
        result: dict[tuple[str, date], YahooVolumeObservation] = {}
        unique_symbols = tuple(dict.fromkeys(symbols))

        def fetch(symbol: str) -> tuple[str, dict[str, Any], datetime]:
            ticker = to_yahoo_symbol(symbol)
            payload = self._request(ticker, start=start, end=end)
            return symbol, payload, self._clock()

        with ThreadPoolExecutor(max_workers=min(8, len(unique_symbols) or 1)) as pool:
            responses = pool.map(fetch, unique_symbols)
            for symbol, payload, observed_at in responses:
                self._parse_payload(
                    result,
                    symbol=symbol,
                    payload=payload,
                    observed_at=observed_at,
                    start=start,
                    end=end,
                )
        return result

    @staticmethod
    def _parse_payload(
        result: dict[tuple[str, date], YahooVolumeObservation],
        *,
        symbol: str,
        payload: dict[str, Any],
        observed_at: datetime,
        start: date,
        end: date,
    ) -> None:
            try:
                chart = payload["chart"]["result"][0]
                timestamps = chart["timestamp"]
                volumes = chart["indicators"]["quote"][0]["volume"]
            except (KeyError, IndexError, TypeError):
                raise YahooVolumeError("yahoo_volume_schema_invalid") from None
            for timestamp, volume in zip(timestamps, volumes, strict=True):
                session_date = datetime.fromtimestamp(int(timestamp), UTC).date()
                if start <= session_date <= end and volume is not None:
                    parsed = int(volume)
                    if parsed < 0:
                        raise YahooVolumeError("yahoo_volume_value_invalid")
                    observation = YahooVolumeObservation(
                        symbol=symbol,
                        session_date=session_date,
                        volume=parsed,
                        observed_at=observed_at,
                        available_at=datetime.combine(
                            session_date,
                            datetime.min.time().replace(hour=16, minute=15),
                            ZoneInfo("America/New_York"),
                        ).astimezone(UTC),
                    )
                    result[(symbol, session_date)] = observation

    def _request(self, ticker: str, *, start: date, end: date) -> dict[str, Any]:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params: dict[str, str | int] = {
            "period1": int(datetime.combine(start, datetime.min.time(), UTC).timestamp()),
            "period2": int(
                datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC).timestamp()
            ),
            "interval": "1d",
            "events": "history",
        }
        for attempt in range(5):
            try:
                response = self._client.get(url, params=params)
                status = int(response.status_code)
                if status == 200:
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise YahooVolumeError("yahoo_volume_schema_invalid")
                    return payload
                if status not in {429, 500, 502, 503, 504}:
                    raise YahooVolumeError("yahoo_volume_request_rejected")
                if attempt == 4:
                    break
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else float(2**attempt)
                self._sleep(min(delay, 30.0))
            except YahooVolumeError:
                raise
            except (httpx.TimeoutException, httpx.TransportError, ValueError):
                if attempt == 4:
                    break
                self._sleep(float(2**attempt))
        raise YahooVolumeError("yahoo_volume_request_failed")


def to_yahoo_symbol(symbol: str) -> str:
    ticker = symbol.removeprefix("US.")
    if not ticker or ticker == symbol:
        raise ValueError("invalid_us_symbol")
    return ticker.replace(".", "-")


__all__ = [
    "YahooDailyVolumeProvider",
    "YahooVolumeError",
    "YahooVolumeObservation",
    "to_yahoo_symbol",
]
