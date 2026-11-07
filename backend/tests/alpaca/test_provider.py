from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from stock_prediction.alpaca.provider import (
    AlpacaAuthenticationError,
    AlpacaMarketDataProvider,
    AlpacaRawBar,
    AlpacaSipProvider,
    ProviderSession,
    from_alpaca_symbol,
    resample_regular_session,
    to_alpaca_symbol,
)
from stock_prediction.contracts.market_data import Timeframe


def _bar(
    hour: int,
    minute: int,
    *,
    session_date: date = date(2026, 8, 12),
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int,
) -> AlpacaRawBar:
    return AlpacaRawBar(
        symbol="AAPL",
        timestamp=datetime.combine(
            session_date, datetime.min.time(), UTC
        ).replace(hour=hour, minute=minute),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        vwap=close,
    )


def test_symbol_mapping_preserves_alpaca_dot_class_symbols() -> None:
    assert to_alpaca_symbol("US.BRK.B") == "BRK.B"
    assert from_alpaca_symbol("BRK.B") == "US.BRK.B"
    assert to_alpaca_symbol("US.EWJ") == "EWJ"


def test_regular_session_resampling_starts_at_0930_and_merges_close_auction() -> None:
    session = ProviderSession(
        session_date=date(2026, 8, 12),
        opens_at=datetime(2026, 8, 12, 13, 30, tzinfo=UTC),
        closes_at=datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
    )
    bars = (
        _bar(13, 30, open_=100, high=102, low=99, close=101, volume=10),
        _bar(14, 0, open_=101, high=104, low=100, close=103, volume=20),
        _bar(19, 30, open_=110, high=111, low=109, close=110.5, volume=30),
        _bar(20, 0, open_=110.5, high=112, low=110, close=111, volume=40),
    )

    result = resample_regular_session(bars, session=session)

    assert [bar.bar_start.hour for bar in result] == [13, 19]
    assert result[0].timeframe is Timeframe.MINUTE_60
    assert result[0].open == 100
    assert result[0].high == 104
    assert result[0].low == 99
    assert result[0].close == 103
    assert result[0].volume == 30
    assert result[-1].close == 111
    assert result[-1].volume == 70
    assert result[-1].source_available_at == datetime(
        2026, 8, 12, 20, 15, tzinfo=UTC
    )


def test_half_day_resampling_emits_partial_last_bucket() -> None:
    session = ProviderSession(
        session_date=date(2026, 11, 27),
        opens_at=datetime(2026, 11, 27, 14, 30, tzinfo=UTC),
        closes_at=datetime(2026, 11, 27, 18, 0, tzinfo=UTC),
    )
    bars = tuple(
        _bar(
            14 + (30 + index * 30) // 60,
            (30 + index * 30) % 60,
            session_date=date(2026, 11, 27),
            open_=100 + index,
            high=101 + index,
            low=99 + index,
            close=100.5 + index,
            volume=10,
        )
        for index in range(8)
    )

    result = resample_regular_session(bars, session=session)

    assert len(result) == 4
    assert result[-1].bar_start == datetime(2026, 11, 27, 17, 30, tzinfo=UTC)
    assert result[-1].source_available_at == datetime(
        2026, 11, 27, 18, 15, tzinfo=UTC
    )


class _ForbiddenClient:
    def get_stock_bars(self, request: object) -> object:
        raise AssertionError("missing credentials must fail before network access")


def test_missing_credentials_fail_without_leaking_secret(tmp_path: Path) -> None:
    provider = AlpacaSipProvider(
        environ={"APCA_API_KEY_ID": "visible-key-only"},
        stock_client=_ForbiddenClient(),
        corporate_actions_client=object(),
    )

    with pytest.raises(AlpacaAuthenticationError) as captured:
        provider.preflight(workspace_root=tmp_path, symbols=("US.AAPL",))

    assert "visible-key-only" not in str(captured.value)


class _RecordingClient:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def get_stock_bars(self, request: object) -> dict[str, list[object]]:
        self.requests.append(request)
        return {"AAPL": []}


def test_history_requests_are_always_raw_sip() -> None:
    client = _RecordingClient()
    provider = AlpacaSipProvider(
        environ={
            "APCA_API_KEY_ID": "key",
            "APCA_API_SECRET_KEY": "secret",
        },
        stock_client=client,
        corporate_actions_client=object(),
    )

    provider.read_daily_bars(
        symbols=("US.AAPL",),
        start=date(2026, 8, 10),
        end=date(2026, 8, 11),
    )

    request = client.requests[0]
    assert str(request.feed).lower().endswith("sip")
    assert str(request.adjustment).lower().endswith("raw")
    assert str(request.timeframe).lower() in {"1day", "day"}


def test_iex_provider_pins_raw_iex_feed() -> None:
    client = _RecordingClient()
    provider = AlpacaMarketDataProvider(
        environ={"APCA_API_KEY_ID": "key", "APCA_API_SECRET_KEY": "secret"},
        stock_client=client,
        corporate_actions_client=object(),
        feed="iex",
    )

    provider.read_daily_bars(
        symbols=("US.AAPL",), start=date(2026, 8, 10), end=date(2026, 8, 11)
    )

    assert str(client.requests[0].feed).lower().endswith("iex")
    assert str(client.requests[0].adjustment).lower().endswith("raw")


def test_daily_source_availability_uses_new_york_close_across_dst() -> None:
    class Raw:
        symbol = "AAPL"
        open = 100.0
        high = 101.0
        low = 99.0
        close = 100.5
        volume = 100
        vwap = 100.2

        def __init__(self, timestamp: datetime) -> None:
            self.timestamp = timestamp

    summer = Raw(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))
    winter = Raw(datetime(2026, 1, 12, 5, 0, tzinfo=UTC))

    rows = AlpacaSipProvider._normalize_daily_response(
        {"AAPL": [summer, winter]}
    )

    assert rows[0].source_available_at == datetime(2026, 8, 12, 20, 15, tzinfo=UTC)
    assert rows[1].source_available_at == datetime(2026, 1, 12, 21, 15, tzinfo=UTC)


def test_provider_skips_zero_volume_placeholders_and_zero_price_bars() -> None:
    class Raw:
        symbol = "EWG"
        high = 32.03
        close = 32.03
        vwap = 0.0

        def __init__(self, *, low: float, volume: int) -> None:
            self.timestamp = datetime(2024, 12, 23, 5, 0, tzinfo=UTC)
            self.open = 32.03
            self.low = low
            self.volume = volume

    valid = Raw(low=32.0, volume=10)
    valid.vwap = 32.02
    rows = AlpacaMarketDataProvider._normalize_daily_response(
        {"EWG": [Raw(low=32.03, volume=0), Raw(low=0.0, volume=10), valid]},
        feed="iex",
    )

    assert len(rows) == 1
    assert rows[0].volume == 10


def test_preflight_checks_assets_calendar_and_historical_sip_without_writes(
    tmp_path: Path,
) -> None:
    class Asset:
        symbol = "AAPL"
        status = "active"
        tradable = True

    class Calendar:
        date = date(2026, 8, 12)
        open = datetime(2026, 8, 12, 9, 30)
        close = datetime(2026, 8, 12, 16, 0)

    class ReferenceClient:
        def get_all_assets(self, request: object) -> list[object]:
            return [Asset()]

        def get_calendar(self, request: object) -> list[object]:
            return [Calendar()]

    stock = _RecordingClient()
    provider = AlpacaSipProvider(
        environ={
            "APCA_API_KEY_ID": "key",
            "APCA_API_SECRET_KEY": "secret",
        },
        stock_client=stock,
        reference_client=ReferenceClient(),
        corporate_actions_client=object(),
        clock=lambda: datetime(2026, 8, 13, 20, 20, tzinfo=UTC),
    )

    result = provider.preflight(workspace_root=tmp_path, symbols=("US.AAPL",))

    assert result.ready
    assert result.active_tradable_count == 1
    assert result.sessions[0].opens_at == datetime(
        2026, 8, 12, 13, 30, tzinfo=UTC
    )
    assert len(stock.requests) == 1
    assert str(stock.requests[0].feed).lower().endswith("sip")


class _HttpFailure(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class _RetryingClient:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.calls = 0

    def get_stock_bars(self, request: object) -> dict[str, list[object]]:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return {"AAPL": []}


def test_rate_limits_and_server_errors_retry_without_leaking_response_body() -> None:
    client = _RetryingClient(
        [_HttpFailure(429, "secret-body"), _HttpFailure(503, "secret-body")]
    )
    sleeps: list[float] = []
    provider = AlpacaSipProvider(
        environ={
            "APCA_API_KEY_ID": "key",
            "APCA_API_SECRET_KEY": "secret",
        },
        stock_client=client,
        corporate_actions_client=object(),
        sleep=sleeps.append,
    )

    assert provider.read_daily_bars(
        symbols=("US.AAPL",),
        start=date(2026, 8, 10),
        end=date(2026, 8, 11),
    ) == ()
    assert client.calls == 3
    assert sleeps == [1.0, 2.0]


def test_authentication_errors_fail_immediately_and_are_sanitized() -> None:
    client = _RetryingClient([_HttpFailure(403, "raw-secret-response")])
    provider = AlpacaSipProvider(
        environ={
            "APCA_API_KEY_ID": "key",
            "APCA_API_SECRET_KEY": "secret",
        },
        stock_client=client,
        corporate_actions_client=object(),
        sleep=lambda _: pytest.fail("authentication errors must not retry"),
    )

    with pytest.raises(AlpacaAuthenticationError) as captured:
        provider.read_daily_bars(
            symbols=("US.AAPL",),
            start=date(2026, 8, 10),
            end=date(2026, 8, 11),
        )

    assert client.calls == 1
    assert "raw-secret-response" not in str(captured.value)


def test_corporate_actions_map_splits_and_dividends_to_adjustment_evidence() -> None:
    class Action:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class Actions:
        data = {
            "forward_splits": [
                Action(symbol="AAPL", ex_date=date(2020, 8, 31), new_rate=4, old_rate=1)
            ],
            "cash_dividends": [
                Action(symbol="AAPL", ex_date=date(2020, 8, 7), rate=0.82)
            ],
        }

    class Client:
        def get_corporate_actions(self, request: object) -> Actions:
            return Actions()

    provider = AlpacaSipProvider(
        environ={"APCA_API_KEY_ID": "key", "APCA_API_SECRET_KEY": "secret"},
        stock_client=object(),
        corporate_actions_client=Client(),
    )

    frame = provider.read_corporate_actions(
        symbols=("US.AAPL",), start=date(2020, 1, 1), end=date(2020, 12, 31)
    )["US.AAPL"]

    split = frame.loc[frame["ex_div_date"].eq(date(2020, 8, 31))].iloc[0]
    dividend = frame.loc[frame["ex_div_date"].eq(date(2020, 8, 7))].iloc[0]
    assert split["split_ratio"] == pytest.approx(0.25)
    assert split["forward_adj_factor_a"] == pytest.approx(0.25)
    assert dividend["per_cash_div"] == pytest.approx(0.82)
    assert dividend["forward_adj_factor_b"] == pytest.approx(-0.82)
