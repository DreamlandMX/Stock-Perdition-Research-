from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from stock_prediction.yahoo.volume import (
    YahooDailyVolumeProvider,
    YahooVolumeError,
    to_yahoo_symbol,
)


class _Response:
    def __init__(
        self, status_code: int, payload: object, headers: dict[str, str] | None = None
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self) -> object:
        return self._payload


class _Client:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, *, params: dict[str, Any]) -> _Response:
        self.calls.append((url, params))
        return self.responses.pop(0)


def _payload(volume: int | None = 123) -> dict[str, object]:
    timestamp = int(datetime(2026, 8, 12, 13, 30, tzinfo=UTC).timestamp())
    return {
        "chart": {
            "result": [
                {
                    "timestamp": [timestamp],
                    "indicators": {"quote": [{"volume": [volume]}]},
                }
            ]
        }
    }


def test_yahoo_symbol_mapping_uses_dash_for_share_classes() -> None:
    assert to_yahoo_symbol("US.BRK.B") == "BRK-B"


def test_reads_only_daily_volume_and_records_observed_at() -> None:
    client = _Client([_Response(200, _payload())])
    observed = datetime(2026, 8, 13, 20, 20, tzinfo=UTC)
    provider = YahooDailyVolumeProvider(client=client, clock=lambda: observed)

    result = provider.read_daily_volumes(
        symbols=("US.AAPL",), start=date(2026, 8, 12), end=date(2026, 8, 12)
    )

    row = result[("US.AAPL", date(2026, 8, 12))]
    assert row.volume == 123
    assert row.observed_at == observed
    assert client.calls[0][1]["interval"] == "1d"


def test_retries_429_using_retry_after() -> None:
    client = _Client(
        [_Response(429, {}, {"Retry-After": "3"}), _Response(200, _payload())]
    )
    sleeps: list[float] = []
    provider = YahooDailyVolumeProvider(client=client, sleep=sleeps.append)

    provider.read_daily_volumes(
        symbols=("US.AAPL",), start=date(2026, 8, 12), end=date(2026, 8, 12)
    )

    assert sleeps == [3.0]


def test_missing_volume_remains_unavailable() -> None:
    provider = YahooDailyVolumeProvider(client=_Client([_Response(200, _payload(None))]))

    result = provider.read_daily_volumes(
        symbols=("US.AAPL",), start=date(2026, 8, 12), end=date(2026, 8, 12)
    )

    assert result == {}


def test_schema_failure_is_sanitized() -> None:
    provider = YahooDailyVolumeProvider(client=_Client([_Response(200, {})]))

    with pytest.raises(YahooVolumeError, match="yahoo_volume_schema_invalid"):
        provider.read_daily_volumes(
            symbols=("US.AAPL",), start=date(2026, 8, 12), end=date(2026, 8, 12)
        )
