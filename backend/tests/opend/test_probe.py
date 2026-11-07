"""Unit tests for the safe default OpenD capability probe."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

import pandas as pd  # type: ignore[import-untyped]
import numpy as np
import pytest

from stock_prediction.contracts.capability import (
    CapabilityStatus,
    CompatibilityStatus,
    PointInTimeStatus,
)
from stock_prediction.opend.errors import OpenDErrorClass, classify_sdk_error
from stock_prediction.opend.ports import SdkCallResult
from stock_prediction.opend.probe import CALENDAR_ROW, CapabilityProbe, EXTENSION_ROWS


PROBED_AT = datetime(2026, 7, 10, 22, 0, tzinfo=UTC)


class FakeReadOnlyContext:
    def __init__(self, responses: dict[str, SdkCallResult[Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, object]] = []
        self.close_calls = 0

    def __enter__(self) -> "FakeReadOnlyContext":
        return self

    def __exit__(self, *_args: object) -> Literal[False]:
        self.close_calls += 1
        return False

    def get_global_state(self) -> SdkCallResult[dict[str, object]]:
        self.calls.append(("get_global_state", None))
        return self.responses["get_global_state"]

    def get_market_state(self, code_list: list[str]) -> SdkCallResult[Any]:
        self.calls.append(("get_market_state", code_list))
        return self.responses["get_market_state"]

    def get_history_kl_quota(
        self, get_detail: bool = False
    ) -> SdkCallResult[tuple[int, int, list[object]]]:
        self.calls.append(("get_history_kl_quota", get_detail))
        return self.responses["get_history_kl_quota"]

    def query_subscription(
        self, is_all_conn: bool = True
    ) -> SdkCallResult[dict[str, object]]:
        self.calls.append(("query_subscription", is_all_conn))
        return self.responses["query_subscription"]

    def get_market_snapshot(self, code_list: list[str]) -> SdkCallResult[Any]:
        self.calls.append(("get_market_snapshot", code_list))
        return self.responses["get_market_snapshot"]

    def request_trading_days(
        self,
        market: str,
        start: str,
        end: str,
        code: str | None = None,
    ) -> SdkCallResult[list[dict[str, object]]]:
        self.calls.append(
            (
                "request_trading_days",
                {"market": market, "start": start, "end": end, "code": code},
            )
        )
        return self.responses["request_trading_days"]


def successful_responses() -> dict[str, SdkCallResult[Any]]:
    return {
        "get_global_state": SdkCallResult.success(
            {
                "qot_logined": "1",
                "trd_logined": "0",
                "server_ver": "10.7.3218",
            }
        ),
        "get_market_state": SdkCallResult.success(
            pd.DataFrame({"code": ["US.SPY", "US.AAPL"], "market_state": ["AFTER_HOURS_END"] * 2})
        ),
        "get_history_kl_quota": SdkCallResult.success((2, 98, [])),
        "query_subscription": SdkCallResult.success(
            {
                "total_used": 2,
                "remain": 298,
                "sub_list": {"QUOTE": ["US.SECRET_SYMBOL", "US.AAPL"]},
                "future_sdk_key": {"shape": "must be ignored"},
            }
        ),
        "get_market_snapshot": SdkCallResult.success(
            pd.DataFrame({"code": ["US.SPY", "US.AAPL"], "last_price": [625.0, 220.0]})
        ),
        "request_trading_days": SdkCallResult.success(
            [{"time": "2026-07-10", "trade_date_type": "WHOLE"}]
        ),
    }


def make_probe(
    factory: Callable[..., FakeReadOnlyContext],
) -> CapabilityProbe:
    return CapabilityProbe(
        quote_context_factory=factory,
        clock=lambda: PROBED_AT,
        snapshot_id_factory=lambda: "snapshot-test",
        sdk_version="10.8.6808",
    )


def rows_by_key(snapshot: object) -> dict[tuple[str, str, str, str | None], Any]:
    capabilities = getattr(snapshot, "capabilities")
    return {
        (row.interface, row.market, row.asset_type, row.timeframe): row
        for row in capabilities
    }


def test_available_probe_uses_fixed_read_only_order_and_separate_rows(
    caplog: Any,
) -> None:
    context = FakeReadOnlyContext(successful_responses())
    probe = make_probe(lambda **_kwargs: context)

    with caplog.at_level(logging.INFO):
        snapshot = probe.probe()

    assert context.calls == [
        ("get_global_state", None),
        ("get_market_state", ["US.SPY", "US.AAPL"]),
        ("get_history_kl_quota", False),
        ("query_subscription", True),
        ("get_market_snapshot", ["US.SPY", "US.AAPL"]),
        (
            "request_trading_days",
            {
                "market": "US",
                "start": "2026-06-30",
                "end": "2026-07-10",
                "code": None,
            },
        ),
    ]
    assert context.close_calls == 1
    assert snapshot.snapshot_id == "snapshot-test"
    assert snapshot.probed_at == PROBED_AT
    assert snapshot.opend_version == "10.7.3218"
    assert snapshot.sdk_version == "10.8.6808"
    assert probe.compatibility_status is CompatibilityStatus.DEGRADED

    rows = rows_by_key(snapshot)
    expected_available_keys = {
        ("get_global_state", "GLOBAL", "system", None),
        ("get_market_state", "US", "etf", None),
        ("get_market_state", "US", "stock", None),
        ("get_history_kl_quota", "US", "historical_quote", None),
        ("query_subscription", "US", "realtime_quote", None),
        ("get_market_snapshot", "US", "etf", None),
        ("get_market_snapshot", "US", "stock", None),
        ("request_trading_days", "US", "market_calendar", "1d"),
    }
    assert expected_available_keys <= rows.keys()
    assert all(rows[key].status is CapabilityStatus.AVAILABLE for key in expected_available_keys)
    assert rows[("request_history_kline", "US", "stock", "1d")].status is CapabilityStatus.UNKNOWN
    assert rows[("request_history_kline", "US", "stock", "60m")].status is CapabilityStatus.UNKNOWN

    assert "US.SECRET_SYMBOL" not in caplog.text
    assert "subscription_count=2" in caplog.text

    quota_row = rows[("get_history_kl_quota", "US", "historical_quote", None)]
    assert (quota_row.quota_total, quota_row.quota_used, quota_row.quota_remaining) == (
        100,
        2,
        98,
    )
    subscription_row = rows[("query_subscription", "US", "realtime_quote", None)]
    assert (
        subscription_row.quota_total,
        subscription_row.quota_used,
        subscription_row.quota_remaining,
    ) == (300, 2, 298)
    assert quota_row.quota_reset_at is None
    assert quota_row.rate_limit_calls is None


def test_extension_matrix_is_complete_declared_unknown_and_never_called() -> None:
    expected_interfaces = {
        "request_history_kline",
        "get_cur_kline",
        "get_rehab",
        "get_stock_quote",
        "get_order_book",
        "get_rt_ticker",
        "get_capital_flow",
        "get_capital_distribution",
        "get_daily_short_volume",
        "get_short_interest",
        "get_financials_statements",
        "get_financials_revenue_breakdown",
        "get_financials_earnings_price_move",
        "get_financials_earnings_price_history",
        "get_corporate_actions_dividends",
        "get_corporate_actions_buybacks",
        "get_corporate_actions_stock_splits",
        "get_dividend_calendar",
        "get_earnings_calendar",
        "get_economic_calendar",
        "get_research_analyst_consensus",
        "get_research_rating_summary",
        "get_research_morningstar_report",
        "get_rating_change",
        "get_valuation_detail",
    }
    assert expected_interfaces <= {row.interface for row in EXTENSION_ROWS}

    context = FakeReadOnlyContext(successful_responses())
    snapshot = make_probe(lambda **_kwargs: context).probe()
    called = {name for name, _arguments in context.calls}
    assert called.isdisjoint(expected_interfaces)
    extension_interfaces = {row.interface for row in snapshot.capabilities} & expected_interfaces
    assert extension_interfaces == expected_interfaces
    history_keys = {
        (row.asset_type, row.timeframe)
        for row in snapshot.capabilities
        if row.interface == "request_history_kline"
    }
    assert history_keys == {
        ("stock", "1d"),
        ("stock", "60m"),
        ("etf", "1d"),
        ("etf", "60m"),
    }
    assert all(
        row.status is CapabilityStatus.UNKNOWN
        and row.point_in_time_status is PointInTimeStatus.UNPROVEN
        for row in snapshot.capabilities
        if row.interface in expected_interfaces
    )
    specs = {row.interface: row for row in EXTENSION_ROWS}
    assert specs["request_history_kline"].permission_scope.value == "historical"
    assert specs["request_history_kline"].degradation_policy.value == "block"
    assert specs["get_order_book"].permission_scope.value == "subscription"
    assert specs["get_order_book"].degradation_policy.value == "disable_feature"
    assert specs["get_financials_statements"].permission_scope.value == "research"
    assert specs["get_financials_statements"].degradation_policy.value == "disable_feature"
    assert specs["get_daily_short_volume"].degradation_policy.value == "disable_feature"
    assert specs["get_short_interest"].degradation_policy.value == "disable_feature"
    assert specs["get_corporate_actions_dividends"].permission_scope.value == "historical"
    assert specs["get_corporate_actions_dividends"].degradation_policy.value == "block"
    assert specs["get_corporate_actions_stock_splits"].permission_scope.value == "historical"
    assert specs["get_corporate_actions_stock_splits"].degradation_policy.value == "block"
    assert specs["get_corporate_actions_buybacks"].degradation_policy.value == "disable_feature"
    assert specs["get_economic_calendar"].permission_scope.value == "public"
    assert specs["get_economic_calendar"].degradation_policy.value == "optional"
    assert CALENDAR_ROW.permission_scope.value == "public"
    assert CALENDAR_ROW.degradation_policy.value == "block"


def test_calendar_probe_uses_recent_window_when_clock_is_on_weekend() -> None:
    context = FakeReadOnlyContext(successful_responses())
    probe = CapabilityProbe(
        quote_context_factory=lambda **_kwargs: context,
        clock=lambda: datetime(2026, 7, 11, 12, 0, tzinfo=UTC),
        snapshot_id_factory=lambda: "weekend-calendar-window",
        sdk_version="10.8.6808",
    )

    probe.probe()

    assert context.calls[-1] == (
        "request_trading_days",
        {
            "market": "US",
            "start": "2026-07-01",
            "end": "2026-07-11",
            "code": None,
        },
    )


def test_permission_failure_does_not_erase_successful_core_capabilities() -> None:
    responses = successful_responses()
    responses["get_market_snapshot"] = SdkCallResult.failure(
        classify_sdk_error("No permission for snapshot quote right")
    )
    context = FakeReadOnlyContext(responses)
    probe = make_probe(lambda **_kwargs: context)

    snapshot = probe.probe()

    rows = rows_by_key(snapshot)
    assert rows[("get_market_state", "US", "stock", None)].status is CapabilityStatus.AVAILABLE
    assert rows[("get_market_snapshot", "US", "stock", None)].status is CapabilityStatus.DENIED
    assert rows[("get_market_snapshot", "US", "etf", None)].status is CapabilityStatus.DENIED
    assert (
        rows[("get_market_snapshot", "US", "stock", None)].last_error_class.value
        == "permission_denied"
    )
    assert probe.errors[0].interface == "get_market_snapshot"
    assert probe.errors[0].error.error_class is OpenDErrorClass.PERMISSION_DENIED


def test_partial_snapshot_is_recorded_without_erasing_other_successes() -> None:
    responses = successful_responses()
    responses["get_market_snapshot"] = SdkCallResult.success(
        pd.DataFrame({"code": ["US.AAPL"], "last_price": [220.0]})
    )
    probe = make_probe(lambda **_kwargs: FakeReadOnlyContext(responses))

    snapshot = probe.probe()

    rows = rows_by_key(snapshot)
    assert rows[("get_market_snapshot", "US", "stock", None)].status is CapabilityStatus.AVAILABLE
    assert rows[("get_market_snapshot", "US", "etf", None)].status is CapabilityStatus.UNAVAILABLE
    assert (
        rows[("get_market_snapshot", "US", "etf", None)].last_error_class.value
        == "partial_response"
    )
    assert rows[("get_market_snapshot", "US", "stock", None)].last_error_class is None
    assert any(
        issue.error.error_class is OpenDErrorClass.PARTIAL_RESPONSE
        for issue in probe.errors
    )


def test_probe_keeps_prior_rows_when_snapshot_call_raises() -> None:
    class RaisingSnapshotContext(FakeReadOnlyContext):
        def get_market_snapshot(self, code_list: list[str]) -> SdkCallResult[Any]:
            self.calls.append(("get_market_snapshot", code_list))
            raise ConnectionError("raw secret socket detail")

    context = RaisingSnapshotContext(successful_responses())
    probe = make_probe(lambda **_kwargs: context)

    snapshot = probe.probe()

    rows = rows_by_key(snapshot)
    assert rows[("get_market_state", "US", "stock", None)].status is CapabilityStatus.AVAILABLE
    assert rows[("get_market_snapshot", "US", "stock", None)].status is CapabilityStatus.UNAVAILABLE
    assert any(
        issue.interface == "get_market_snapshot"
        and issue.error.error_class is OpenDErrorClass.CONNECTION_FAILED
        for issue in probe.errors
    )
    assert not any(issue.interface == "get_global_state" for issue in probe.errors)


@pytest.mark.parametrize(
    "interface,response,expected_key",
    [
        (
            "get_market_state",
            pd.DataFrame({"code": ["US.SPY", "US.AAPL"]}),
            ("get_market_state", "US", "stock", None),
        ),
        (
            "get_market_state",
            pd.DataFrame(columns=["code", "market_state"]),
            ("get_market_state", "US", "stock", None),
        ),
        (
            "get_market_snapshot",
            pd.DataFrame({"code": ["US.SPY", "US.AAPL"]}),
            ("get_market_snapshot", "US", "stock", None),
        ),
        (
            "get_market_snapshot",
            pd.DataFrame(columns=["code", "last_price"]),
            ("get_market_snapshot", "US", "stock", None),
        ),
    ],
)
def test_dataframe_probe_requires_interface_columns_and_nonempty_rows(
    interface: str,
    response: pd.DataFrame,
    expected_key: tuple[str, str, str, str | None],
) -> None:
    responses = successful_responses()
    responses[interface] = SdkCallResult.success(response)
    probe = make_probe(lambda **_kwargs: FakeReadOnlyContext(responses))

    snapshot = probe.probe()

    assert rows_by_key(snapshot)[expected_key].status is CapabilityStatus.UNAVAILABLE
    assert any(
        issue.interface == interface
        and issue.error.error_class is OpenDErrorClass.PARTIAL_RESPONSE
        for issue in probe.errors
    )


@pytest.mark.parametrize("invalid_market_state", [None, ""])
def test_market_state_invalid_value_only_marks_affected_asset_partial(
    invalid_market_state: object,
) -> None:
    responses = successful_responses()
    responses["get_market_state"] = SdkCallResult.success(
        pd.DataFrame(
            {
                "code": ["US.SPY", "US.AAPL"],
                "market_state": [invalid_market_state, "AFTER_HOURS_END"],
            }
        )
    )
    probe = make_probe(lambda **_kwargs: FakeReadOnlyContext(responses))

    snapshot = probe.probe()

    rows = rows_by_key(snapshot)
    assert rows[("get_market_state", "US", "etf", None)].status is CapabilityStatus.UNAVAILABLE
    assert rows[("get_market_state", "US", "stock", None)].status is CapabilityStatus.AVAILABLE
    assert rows[("get_global_state", "GLOBAL", "system", None)].status is CapabilityStatus.AVAILABLE
    assert any(
        issue.interface == "get_market_state"
        and issue.error.error_class is OpenDErrorClass.PARTIAL_RESPONSE
        for issue in probe.errors
    )


@pytest.mark.parametrize(
    "invalid_last_price",
    [None, "625.0", True, float("nan"), float("inf"), float("-inf")],
)
def test_snapshot_invalid_price_only_marks_affected_asset_partial(
    invalid_last_price: object,
) -> None:
    responses = successful_responses()
    responses["get_market_snapshot"] = SdkCallResult.success(
        pd.DataFrame(
            {
                "code": ["US.SPY", "US.AAPL"],
                "last_price": [invalid_last_price, np.float64(220.0)],
            }
        )
    )
    probe = make_probe(lambda **_kwargs: FakeReadOnlyContext(responses))

    snapshot = probe.probe()

    rows = rows_by_key(snapshot)
    assert rows[("get_market_snapshot", "US", "etf", None)].status is CapabilityStatus.UNAVAILABLE
    assert rows[("get_market_snapshot", "US", "stock", None)].status is CapabilityStatus.AVAILABLE
    assert rows[("get_market_state", "US", "stock", None)].status is CapabilityStatus.AVAILABLE
    assert any(
        issue.interface == "get_market_snapshot"
        and issue.error.error_class is OpenDErrorClass.PARTIAL_RESPONSE
        for issue in probe.errors
    )


def test_snapshot_accepts_finite_numpy_numeric_prices() -> None:
    responses = successful_responses()
    responses["get_market_snapshot"] = SdkCallResult.success(
        pd.DataFrame(
            {
                "code": ["US.SPY", "US.AAPL"],
                "last_price": [np.int64(625), np.float64(220.0)],
            }
        )
    )
    probe = make_probe(lambda **_kwargs: FakeReadOnlyContext(responses))

    snapshot = probe.probe()

    rows = rows_by_key(snapshot)
    assert rows[("get_market_snapshot", "US", "etf", None)].status is CapabilityStatus.AVAILABLE
    assert rows[("get_market_snapshot", "US", "stock", None)].status is CapabilityStatus.AVAILABLE


@pytest.mark.parametrize(
    "subscription",
    [
        {"total_used": 2, "sub_list": {"QUOTE": ["US.AAPL"]}},
        {"total_used": 2, "remain": True, "sub_list": {"QUOTE": ["US.AAPL"]}},
        {"total_used": "2", "remain": 298, "sub_list": {"QUOTE": ["US.AAPL"]}},
        {"total_used": 2, "remain": 298, "sub_list": ["US.AAPL"]},
        {"total_used": 2, "remain": 298, "sub_list": {"QUOTE": "US.AAPL"}},
    ],
)
def test_subscription_probe_rejects_malformed_required_fields(
    subscription: dict[str, object],
) -> None:
    responses = successful_responses()
    responses["query_subscription"] = SdkCallResult.success(subscription)
    probe = make_probe(lambda **_kwargs: FakeReadOnlyContext(responses))

    snapshot = probe.probe()

    row = rows_by_key(snapshot)[("query_subscription", "US", "realtime_quote", None)]
    assert row.status is CapabilityStatus.UNAVAILABLE
    assert any(
        issue.interface == "query_subscription"
        and issue.error.error_class is OpenDErrorClass.PARTIAL_RESPONSE
        for issue in probe.errors
    )


@pytest.mark.parametrize(
    "calendar",
    [
        [{}],
        [{"time": "2026-07-10"}],
        [{"time": 20260710, "trade_date_type": "WHOLE"}],
        [{"time": "2026-07-10", "trade_date_type": None}],
    ],
)
def test_calendar_probe_requires_typed_time_and_trade_date_type(
    calendar: list[dict[str, object]],
) -> None:
    responses = successful_responses()
    responses["request_trading_days"] = SdkCallResult.success(calendar)
    probe = make_probe(lambda **_kwargs: FakeReadOnlyContext(responses))

    snapshot = probe.probe()

    row = rows_by_key(snapshot)[("request_trading_days", "US", "market_calendar", "1d")]
    assert row.status is CapabilityStatus.UNAVAILABLE
    assert any(
        issue.interface == "request_trading_days"
        and issue.error.error_class is OpenDErrorClass.PARTIAL_RESPONSE
        for issue in probe.errors
    )


def test_quote_login_is_required_but_trade_login_is_informational() -> None:
    responses = successful_responses()
    global_state = dict(responses["get_global_state"].value or {})
    global_state["qot_logined"] = "0"
    global_state["trd_logined"] = "1"
    responses["get_global_state"] = SdkCallResult.success(global_state)
    context = FakeReadOnlyContext(responses)
    probe = make_probe(lambda **_kwargs: context)

    snapshot = probe.probe()

    rows = rows_by_key(snapshot)
    assert rows[("get_global_state", "GLOBAL", "system", None)].status is CapabilityStatus.DENIED
    assert context.calls == [("get_global_state", None)]
    assert probe.errors[0].error.error_class is OpenDErrorClass.NOT_LOGGED_IN


def test_missing_quote_login_state_is_partial_response() -> None:
    responses = successful_responses()
    global_state = dict(responses["get_global_state"].value or {})
    del global_state["qot_logined"]
    responses["get_global_state"] = SdkCallResult.success(global_state)
    context = FakeReadOnlyContext(responses)
    probe = make_probe(lambda **_kwargs: context)

    snapshot = probe.probe()

    row = rows_by_key(snapshot)[("get_global_state", "GLOBAL", "system", None)]
    assert row.status is CapabilityStatus.UNAVAILABLE
    assert context.calls == [("get_global_state", None)]
    assert probe.errors[0].error.error_class is OpenDErrorClass.PARTIAL_RESPONSE


def test_unsafe_server_version_is_sanitized_without_erasing_quote_readiness() -> None:
    responses = successful_responses()
    global_state = dict(responses["get_global_state"].value or {})
    global_state["server_ver"] = "1007\naccount=123 credential=secret"
    responses["get_global_state"] = SdkCallResult.success(global_state)
    probe = make_probe(lambda **_kwargs: FakeReadOnlyContext(responses))

    snapshot = probe.probe()

    assert snapshot.opend_version == "unknown"
    assert rows_by_key(snapshot)[
        ("get_global_state", "GLOBAL", "system", None)
    ].status is CapabilityStatus.AVAILABLE
    assert any(
        issue.interface == "get_global_state"
        and issue.error.error_class is OpenDErrorClass.PARTIAL_RESPONSE
        for issue in probe.errors
    )


@pytest.mark.parametrize("quota", [(False, 98, []), (2, True, [])])
def test_quota_probe_rejects_boolean_counts(
    quota: tuple[object, object, list[object]],
) -> None:
    responses = successful_responses()
    responses["get_history_kl_quota"] = SdkCallResult.success(quota)
    probe = make_probe(lambda **_kwargs: FakeReadOnlyContext(responses))

    snapshot = probe.probe()

    row = rows_by_key(snapshot)[
        ("get_history_kl_quota", "US", "historical_quote", None)
    ]
    assert row.status is CapabilityStatus.UNAVAILABLE
    assert any(
        issue.interface == "get_history_kl_quota"
        and issue.error.error_class is OpenDErrorClass.PARTIAL_RESPONSE
        for issue in probe.errors
    )


@pytest.mark.parametrize("quota", [(-1, 99, []), (2, -1, [])])
def test_quota_probe_rejects_negative_counts(
    quota: tuple[int, int, list[object]],
) -> None:
    responses = successful_responses()
    responses["get_history_kl_quota"] = SdkCallResult.success(quota)
    probe = make_probe(lambda **_kwargs: FakeReadOnlyContext(responses))

    snapshot = probe.probe()

    row = rows_by_key(snapshot)[
        ("get_history_kl_quota", "US", "historical_quote", None)
    ]
    assert row.status is CapabilityStatus.UNAVAILABLE
    assert row.last_error_class.value == "partial_response"


@pytest.mark.parametrize(("total_used", "remain"), [(-1, 301), (2, -1)])
def test_subscription_probe_rejects_negative_counts(
    total_used: int,
    remain: int,
) -> None:
    responses = successful_responses()
    responses["query_subscription"] = SdkCallResult.success(
        {"total_used": total_used, "remain": remain, "sub_list": {}}
    )
    probe = make_probe(lambda **_kwargs: FakeReadOnlyContext(responses))

    snapshot = probe.probe()

    row = rows_by_key(snapshot)[("query_subscription", "US", "realtime_quote", None)]
    assert row.status is CapabilityStatus.UNAVAILABLE
    assert row.last_error_class.value == "partial_response"


def test_opend_offline_returns_unavailable_snapshot_without_raw_exception() -> None:
    def offline_factory(**_kwargs: object) -> FakeReadOnlyContext:
        raise ConnectionError("secret endpoint details must not escape")

    probe = make_probe(offline_factory)

    snapshot = probe.probe()

    rows = rows_by_key(snapshot)
    extension_interfaces = {spec.interface for spec in EXTENSION_ROWS}
    core_rows = [
        row
        for row in snapshot.capabilities
        if row.interface not in extension_interfaces
    ]
    assert core_rows
    assert all(row.status is CapabilityStatus.UNAVAILABLE for row in core_rows)
    assert rows[("request_history_kline", "US", "stock", "1d")].status is CapabilityStatus.UNKNOWN
    assert snapshot.opend_version == "unknown"
    assert probe.errors[0].error.error_class is OpenDErrorClass.CONNECTION_FAILED
    assert "secret endpoint" not in probe.errors[0].error.message
