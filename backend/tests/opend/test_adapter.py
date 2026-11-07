"""Unit tests for the read-only futu quote-context adapter."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from futu import AuType

from stock_prediction.opend.errors import OpenDError, OpenDErrorClass
from stock_prediction.opend.futu_adapter import FutuQuoteContext
from stock_prediction.opend.ports import SdkCallResult


def test_chinese_frequency_error_is_sanitized_and_waits_as_rate_limit():
    from stock_prediction.opend.errors import classify_sdk_error

    error = classify_sdk_error("请求频率限制，最多30秒内请求10次")
    assert error.error_class == OpenDErrorClass.RATE_LIMITED
    assert error.retryable
    assert error.message == "OpenD request rate limited"


@pytest.mark.parametrize("message", ["Connect timeout", "WSAECONNREFUSED"])
def test_disconnected_opend_is_classified_as_connection_failure(message):
    from stock_prediction.opend.errors import classify_sdk_error

    error = classify_sdk_error(message)
    assert error.error_class == OpenDErrorClass.CONNECTION_FAILED
    assert error.retryable


class FakeQuoteContext:
    def __init__(
        self,
        responses: dict[str, tuple[Any, ...] | BaseException] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.close_calls = 0
        self.history_kwargs: list[dict[str, object]] = []

    def _response(self, method_name: str) -> tuple[Any, ...]:
        response = self.responses[method_name]
        if isinstance(response, BaseException):
            raise response
        return response

    def close(self) -> None:
        self.close_calls += 1

    def get_global_state(self) -> tuple[Any, ...]:
        if "get_global_state" in self.responses:
            return self._response("get_global_state")
        return self.responses.get(
            "get_global_state",
            (0, {"qot_logined": "1", "server_ver": "10.7.0"}),
        )

    def get_market_state(self, code_list: list[str]) -> tuple[Any, ...]:
        return self._response("get_market_state")

    def get_history_kl_quota(self, get_detail: bool) -> tuple[Any, ...]:
        return self._response("get_history_kl_quota")

    def query_subscription(self, is_all_conn: bool) -> tuple[Any, ...]:
        return self._response("query_subscription")

    def get_market_snapshot(self, code_list: list[str]) -> tuple[Any, ...]:
        return self._response("get_market_snapshot")

    def get_option_expiration_date(self, code: str) -> tuple[Any, ...]:
        return self._response("get_option_expiration_date")

    def get_option_chain(self, code: str, *, start: str, end: str) -> tuple[Any, ...]:
        return self._response("get_option_chain")

    def get_rehab(self, code: str) -> tuple[Any, ...]:
        assert isinstance(code, str)
        return self._response("get_rehab")

    def request_trading_days(
        self,
        market: str,
        start: str,
        end: str,
        code: str | None,
    ) -> tuple[Any, ...]:
        return self._response("request_trading_days")

    def request_history_kline(self, **kwargs: object) -> tuple[Any, ...]:
        self.history_kwargs.append(kwargs)
        return self._response("request_history_kline")


def recording_factory(
    context: FakeQuoteContext,
) -> tuple[Callable[..., FakeQuoteContext], list[dict[str, object]]]:
    calls: list[dict[str, object]] = []

    def factory(**kwargs: object) -> FakeQuoteContext:
        calls.append(kwargs)
        return context

    return factory, calls


def test_context_closes_owned_quote_context_once_after_success() -> None:
    raw_context = FakeQuoteContext()
    factory, calls = recording_factory(raw_context)

    with FutuQuoteContext(
        host="127.0.0.1", port=11111, quote_context_factory=factory
    ) as context:
        result = context.get_global_state()

    assert result.is_ok
    assert result.value == {"qot_logined": "1", "server_ver": "10.7.0"}
    assert calls == [{"host": "127.0.0.1", "port": 11111}]
    assert raw_context.close_calls == 1


def test_context_closes_owned_quote_context_once_after_exception() -> None:
    raw_context = FakeQuoteContext()
    factory, _ = recording_factory(raw_context)

    with pytest.raises(RuntimeError, match="consumer failed"):
        with FutuQuoteContext(
            host="127.0.0.1", port=11111, quote_context_factory=factory
        ):
            raise RuntimeError("consumer failed")

    assert raw_context.close_calls == 1


def test_sdk_failure_is_typed_and_does_not_expose_raw_data() -> None:
    raw_context = FakeQuoteContext(
        {"get_global_state": (-1, "No permission for this quote right")}
    )
    factory, _ = recording_factory(raw_context)

    with FutuQuoteContext(
        host="127.0.0.1", port=11111, quote_context_factory=factory
    ) as context:
        result = context.get_global_state()

    assert not result.is_ok
    assert result.value is None
    assert result.error is not None
    assert result.error.error_class is OpenDErrorClass.PERMISSION_DENIED


@pytest.mark.parametrize(
    "method_name,invoke",
    [
        ("get_global_state", lambda context: context.get_global_state()),
        ("get_market_state", lambda context: context.get_market_state(["US.AAPL"])),
        ("get_history_kl_quota", lambda context: context.get_history_kl_quota(False)),
        ("query_subscription", lambda context: context.query_subscription(True)),
        (
            "get_market_snapshot",
            lambda context: context.get_market_snapshot(["US.AAPL"]),
        ),
        (
            "get_option_expiration_date",
            lambda context: context.get_option_expiration_date("US.AAPL"),
        ),
        (
            "get_option_chain",
            lambda context: context.get_option_chain(
                "US.AAPL", start="2026-10-11", end="2026-11-09"
            ),
        ),
        ("get_rehab", lambda context: context.get_rehab(["US.AAPL"])),
        (
            "request_trading_days",
            lambda context: context.request_trading_days(
                market="US", start="2026-07-10", end="2026-07-10"
            ),
        ),
    ],
)
def test_sdk_method_exception_becomes_sanitized_failure(
    method_name: str,
    invoke: Callable[[FutuQuoteContext], SdkCallResult[Any]],
) -> None:
    raw_context = FakeQuoteContext(
        {method_name: RuntimeError("raw secret SDK response body")}
    )
    factory, _ = recording_factory(raw_context)

    with FutuQuoteContext(quote_context_factory=factory) as context:
        result = invoke(context)

    assert not result.is_ok
    assert result.error is not None
    assert result.error.error_class is OpenDErrorClass.SDK_ERROR
    assert "raw secret" not in result.error.message
    assert raw_context.close_calls == 1


def test_sdk_call_result_rejects_missing_success_and_error() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        SdkCallResult[int]()


def test_history_request_adapts_sdk_three_tuple_without_exposing_page_key() -> None:
    frame = object()
    raw_context = FakeQuoteContext({"request_history_kline": (0, frame, "next")})
    factory, _ = recording_factory(raw_context)

    with FutuQuoteContext(quote_context_factory=factory) as context:
        result = context.request_history_kline(
            code="US.AAPL",
            timeframe="1d",
            start="2000-01-01",
            end="2026-07-11",
            max_count=1,
        )

    assert result.is_ok
    assert result.value == frame
    assert raw_context.history_kwargs[0]["autype"] == AuType.NONE
    assert raw_context.close_calls == 1


def test_paged_history_round_trips_an_opaque_sanitized_page_token() -> None:
    frame = object()
    raw_context = FakeQuoteContext(
        {"request_history_kline": (0, frame, b"opaque-sdk-page-key")}
    )
    factory, _ = recording_factory(raw_context)

    with FutuQuoteContext(quote_context_factory=factory) as context:
        first = context.request_history_kline_page(
            code="US.AAPL",
            timeframe="60m",
            start="2026-07-01",
            end="2026-07-10",
            max_count=100,
            page_token=None,
        )
        assert first.is_ok and first.value is not None
        raw_context.responses["request_history_kline"] = (0, frame, None)
        second = context.request_history_kline_page(
            code="US.AAPL",
            timeframe="60m",
            start="2026-07-01",
            end="2026-07-10",
            max_count=100,
            page_token=first.value.next_page_token,
            extended_time=True,
        )

    assert second.is_ok
    assert raw_context.history_kwargs[0].get("page_req_key") is None
    assert raw_context.history_kwargs[1]["page_req_key"] == b"opaque-sdk-page-key"
    assert raw_context.history_kwargs[0]["extended_time"] is False
    assert raw_context.history_kwargs[1]["extended_time"] is True


def test_malformed_page_token_fails_without_calling_sdk() -> None:
    raw_context = FakeQuoteContext()
    factory, _ = recording_factory(raw_context)

    with FutuQuoteContext(quote_context_factory=factory) as context:
        result = context.request_history_kline_page(
            code="US.AAPL",
            timeframe="60m",
            start="2026-07-01",
            end="2026-07-10",
            max_count=100,
            page_token="not valid base64!",
        )

    assert not result.is_ok
    assert result.error is not None
    assert result.error.error_class is OpenDErrorClass.PARTIAL_RESPONSE
    assert raw_context.history_kwargs == []


def test_get_rehab_uses_read_only_quote_context() -> None:
    frame = object()
    raw_context = FakeQuoteContext({"get_rehab": (0, frame)})
    factory, _ = recording_factory(raw_context)

    with FutuQuoteContext(quote_context_factory=factory) as context:
        result = context.get_rehab(["US.AAPL"])

    assert result.is_ok
    assert result.value is frame
    assert raw_context.close_calls == 1


@pytest.mark.parametrize("codes", [[], ["US.AAPL", "US.SPY"]])
def test_get_rehab_rejects_non_singleton_requests(codes: list[str]) -> None:
    raw_context = FakeQuoteContext()
    factory, _ = recording_factory(raw_context)
    with FutuQuoteContext(quote_context_factory=factory) as context:
        assert not context.get_rehab(codes).is_ok


def test_sdk_call_result_rejects_value_and_error_together() -> None:
    error = OpenDError(OpenDErrorClass.SDK_ERROR, "safe", retryable=True)

    with pytest.raises(ValueError, match="exactly one"):
        SdkCallResult(value=1, error=error)


def test_production_opend_code_contains_only_read_only_sdk_calls() -> None:
    source_root = Path(__file__).parents[2] / "src" / "stock_prediction" / "opend"
    production_source = "\n".join(
        path.read_text(encoding="utf-8") for path in source_root.glob("*.py")
    )
    forbidden_tokens = (
        "OpenTradeContext",
        "unlock_trade",
        "place_order",
        "get_acc_list",
        "accinfo_query",
        "position_list_query",
        "order_list_query",
        "get_max_trd_qty",
        ".subscribe(",
        ".unsubscribe(",
    )

    assert not [token for token in forbidden_tokens if token in production_source]
