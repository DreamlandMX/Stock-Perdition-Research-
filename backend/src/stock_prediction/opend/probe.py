"""Safe, read-only capability probing for a local OpenD quote service."""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from math import isfinite
from numbers import Real
import re
from typing import Any, TypeGuard, TypeVar
from uuid import uuid4

import pandas as pd

from stock_prediction.contracts.capability import (
    CapabilitySnapshot,
    CapabilityStatus,
    CapabilityErrorClass,
    CompatibilityStatus,
    DataCapability,
    DegradationPolicy,
    PermissionScope,
    PointInTimeStatus,
)
from stock_prediction.opend.errors import (
    OpenDError,
    OpenDErrorClass,
    classify_sdk_exception,
    connection_error,
    partial_response_error,
)
from stock_prediction.opend.futu_adapter import FutuQuoteContext
from stock_prediction.opend.ports import ReadOnlyQuoteContext, SdkCallResult

LOGGER = logging.getLogger(__name__)
SAFE_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")

SEED_CODES = ["US.SPY", "US.AAPL"]
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ProbeIssue:
    """One sanitized issue encountered without aborting unrelated rows."""

    interface: str
    error: OpenDError


@dataclass(frozen=True, slots=True)
class _RowSpec:
    interface: str
    market: str
    asset_type: str
    timeframe: str | None
    point_in_time_status: PointInTimeStatus
    permission_scope: PermissionScope = PermissionScope.QUOTE
    degradation_policy: DegradationPolicy = DegradationPolicy.DEGRADE


GLOBAL_STATE_ROW = _RowSpec(
    "get_global_state", "GLOBAL", "system", None, PointInTimeStatus.NOT_APPLICABLE,
    PermissionScope.SYSTEM, DegradationPolicy.BLOCK
)
MARKET_STATE_ROWS = (
    _RowSpec("get_market_state", "US", "etf", None, PointInTimeStatus.FORWARD_ONLY),
    _RowSpec("get_market_state", "US", "stock", None, PointInTimeStatus.FORWARD_ONLY),
)
QUOTA_ROW = _RowSpec(
    "get_history_kl_quota",
    "US",
    "historical_quote",
    None,
    PointInTimeStatus.NOT_APPLICABLE,
    PermissionScope.HISTORICAL,
    DegradationPolicy.BLOCK,
)
SUBSCRIPTION_ROW = _RowSpec(
    "query_subscription",
    "US",
    "realtime_quote",
    None,
    PointInTimeStatus.NOT_APPLICABLE,
    PermissionScope.SUBSCRIPTION,
    DegradationPolicy.DEGRADE,
)
SNAPSHOT_ROWS = (
    _RowSpec("get_market_snapshot", "US", "etf", None, PointInTimeStatus.FORWARD_ONLY),
    _RowSpec("get_market_snapshot", "US", "stock", None, PointInTimeStatus.FORWARD_ONLY),
)
CALENDAR_ROW = _RowSpec(
    "request_trading_days",
    "US",
    "market_calendar",
    "1d",
    PointInTimeStatus.PROVEN,
    PermissionScope.PUBLIC,
    DegradationPolicy.BLOCK,
)
EXTENSION_ROWS = (
    _RowSpec(
        "request_history_kline",
        "US",
        "stock",
        "1d",
        PointInTimeStatus.UNPROVEN,
        PermissionScope.HISTORICAL,
        DegradationPolicy.BLOCK,
    ),
    _RowSpec(
        "request_history_kline",
        "US",
        "etf",
        "1d",
        PointInTimeStatus.UNPROVEN,
        PermissionScope.HISTORICAL,
        DegradationPolicy.BLOCK,
    ),
    _RowSpec(
        "request_history_kline",
        "US",
        "etf",
        "60m",
        PointInTimeStatus.UNPROVEN,
        PermissionScope.HISTORICAL,
        DegradationPolicy.BLOCK,
    ),
    _RowSpec(
        "request_history_kline",
        "US",
        "stock",
        "60m",
        PointInTimeStatus.UNPROVEN,
        PermissionScope.HISTORICAL,
        DegradationPolicy.BLOCK,
    ),
    _RowSpec(
        "get_cur_kline", "US", "stock", "1m", PointInTimeStatus.UNPROVEN,
        PermissionScope.SUBSCRIPTION, DegradationPolicy.DISABLE_FEATURE,
    ),
    _RowSpec(
        "get_rehab", "US", "stock", "1d", PointInTimeStatus.UNPROVEN,
        PermissionScope.HISTORICAL, DegradationPolicy.BLOCK,
    ),
    _RowSpec(
        "get_stock_quote", "US", "stock", None, PointInTimeStatus.UNPROVEN,
        PermissionScope.SUBSCRIPTION, DegradationPolicy.DEGRADE,
    ),
    _RowSpec(
        "get_order_book", "US", "stock", None, PointInTimeStatus.UNPROVEN,
        PermissionScope.SUBSCRIPTION, DegradationPolicy.DISABLE_FEATURE,
    ),
    _RowSpec(
        "get_rt_ticker", "US", "stock", None, PointInTimeStatus.UNPROVEN,
        PermissionScope.SUBSCRIPTION, DegradationPolicy.DISABLE_FEATURE,
    ),
    _RowSpec("get_capital_flow", "US", "stock", None, PointInTimeStatus.UNPROVEN),
    _RowSpec("get_capital_distribution", "US", "stock", None, PointInTimeStatus.UNPROVEN),
    _RowSpec(
        "get_daily_short_volume", "US", "stock", "1d",
        PointInTimeStatus.UNPROVEN, PermissionScope.QUOTE,
        DegradationPolicy.DISABLE_FEATURE,
    ),
    _RowSpec(
        "get_short_interest", "US", "stock", "1d",
        PointInTimeStatus.UNPROVEN, PermissionScope.QUOTE,
        DegradationPolicy.DISABLE_FEATURE,
    ),
    _RowSpec(
        "get_corporate_actions_dividends", "US", "stock", None,
        PointInTimeStatus.UNPROVEN, PermissionScope.HISTORICAL,
        DegradationPolicy.BLOCK,
    ),
    _RowSpec(
        "get_corporate_actions_stock_splits", "US", "stock", None,
        PointInTimeStatus.UNPROVEN, PermissionScope.HISTORICAL,
        DegradationPolicy.BLOCK,
    ),
    _RowSpec(
        "get_corporate_actions_buybacks", "US", "stock", None,
        PointInTimeStatus.UNPROVEN, PermissionScope.RESEARCH,
        DegradationPolicy.DISABLE_FEATURE,
    ),
    *(
        _RowSpec(
            interface,
            "US",
            "stock",
            None,
            PointInTimeStatus.UNPROVEN,
            PermissionScope.RESEARCH,
            DegradationPolicy.DISABLE_FEATURE,
        )
        for interface in (
            "get_financials_statements",
            "get_financials_revenue_breakdown",
            "get_financials_earnings_price_move",
            "get_financials_earnings_price_history",
            "get_research_analyst_consensus",
            "get_research_rating_summary",
            "get_research_morningstar_report",
            "get_rating_change",
            "get_valuation_detail",
        )
    ),
    *(
        _RowSpec(
            interface,
            "US",
            "market_event",
            None,
            PointInTimeStatus.UNPROVEN,
            PermissionScope.PUBLIC,
            DegradationPolicy.OPTIONAL,
        )
        for interface in (
            "get_dividend_calendar",
            "get_earnings_calendar",
            "get_economic_calendar",
        )
    ),
)
CORE_ROWS = (
    GLOBAL_STATE_ROW,
    *MARKET_STATE_ROWS,
    QUOTA_ROW,
    SUBSCRIPTION_ROW,
    *SNAPSHOT_ROWS,
    CALENDAR_ROW,
)

QuoteContextFactory = Callable[..., ReadOnlyQuoteContext]


def _default_context_factory(**kwargs: object) -> ReadOnlyQuoteContext:
    host = str(kwargs["host"])
    raw_port = kwargs["port"]
    if not isinstance(raw_port, int):
        raise TypeError("OpenD port must be an integer")
    port = raw_port
    return FutuQuoteContext(host=host, port=port)


def _installed_sdk_version() -> str:
    try:
        return version("futu-api")
    except PackageNotFoundError:
        return "unknown"


class CapabilityProbe:
    """Run the fixed, non-mutating M1 OpenD capability probe."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        *,
        quote_context_factory: QuoteContextFactory = _default_context_factory,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        snapshot_id_factory: Callable[[], str] = lambda: str(uuid4()),
        sdk_version: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._quote_context_factory = quote_context_factory
        self._clock = clock
        self._snapshot_id_factory = snapshot_id_factory
        self._sdk_version = sdk_version or _installed_sdk_version()
        self.errors: tuple[ProbeIssue, ...] = ()
        self.compatibility_status = CompatibilityStatus.UNKNOWN

    def probe(self) -> CapabilitySnapshot:
        """Probe only the approved read-only interface sequence."""

        probed_at = self._clock()
        issues: list[ProbeIssue] = []
        try:
            context_manager = self._quote_context_factory(
                host=self._host, port=self._port
            )
        except Exception:
            rows, opend_version, issues = self._offline_result(probed_at)
        else:
            with context_manager as context:
                rows, opend_version = self._probe_connected(
                    context, probed_at, issues
                )

        self.errors = tuple(issues)
        self.compatibility_status = self._compatibility(opend_version, self._sdk_version)
        return CapabilitySnapshot(
            snapshot_id=self._snapshot_id_factory(),
            probed_at=probed_at,
            opend_version=opend_version,
            sdk_version=self._sdk_version,
            compatibility_status=self.compatibility_status,
            capabilities=rows,
        )

    def _offline_result(
        self, probed_at: datetime
    ) -> tuple[list[DataCapability], str, list[ProbeIssue]]:
        error = connection_error()
        rows = self._rows(
            CORE_ROWS, CapabilityStatus.UNAVAILABLE, probed_at, error=error
        )
        rows.extend(self._rows(EXTENSION_ROWS, CapabilityStatus.UNKNOWN, probed_at))
        return rows, "unknown", [ProbeIssue("get_global_state", error)]

    def _probe_connected(
        self,
        context: ReadOnlyQuoteContext,
        probed_at: datetime,
        issues: list[ProbeIssue],
    ) -> tuple[list[DataCapability], str]:
        rows: list[DataCapability] = []
        global_result = self._safe_call(context.get_global_state)
        opend_version = "unknown"
        if not global_result.is_ok:
            error = self._error_or_partial(global_result)
            issues.append(ProbeIssue("get_global_state", error))
            rows.append(
                self._row(GLOBAL_STATE_ROW, self._status(error), probed_at, error=error)
            )
            rows.extend(
                self._rows(
                    CORE_ROWS[1:], CapabilityStatus.UNAVAILABLE, probed_at, error=error
                )
            )
            rows.extend(self._rows(EXTENSION_ROWS, CapabilityStatus.UNKNOWN, probed_at))
            return rows, opend_version

        global_state = global_result.value
        if not isinstance(global_state, dict):
            error = partial_response_error()
            issues.append(ProbeIssue("get_global_state", error))
            rows.append(
                self._row(
                    GLOBAL_STATE_ROW, CapabilityStatus.UNAVAILABLE, probed_at, error=error
                )
            )
            rows.extend(
                self._rows(
                    CORE_ROWS[1:], CapabilityStatus.UNAVAILABLE, probed_at, error=error
                )
            )
            rows.extend(self._rows(EXTENSION_ROWS, CapabilityStatus.UNKNOWN, probed_at))
            return rows, opend_version

        server_ver = global_state.get("server_ver")
        if isinstance(server_ver, str) and SAFE_VERSION_PATTERN.fullmatch(server_ver):
            opend_version = server_ver
        elif server_ver is not None:
            issues.append(ProbeIssue("get_global_state", partial_response_error()))

        if "qot_logined" not in global_state:
            error = partial_response_error()
            issues.append(ProbeIssue("get_global_state", error))
            rows.append(
                self._row(
                    GLOBAL_STATE_ROW, CapabilityStatus.UNAVAILABLE, probed_at, error=error
                )
            )
            rows.extend(
                self._rows(
                    CORE_ROWS[1:], CapabilityStatus.UNAVAILABLE, probed_at, error=error
                )
            )
            rows.extend(self._rows(EXTENSION_ROWS, CapabilityStatus.UNKNOWN, probed_at))
            return rows, opend_version

        quote_login = self._parse_login_state(global_state["qot_logined"])
        if quote_login is None:
            error = partial_response_error()
            issues.append(ProbeIssue("get_global_state", error))
            rows.append(
                self._row(
                    GLOBAL_STATE_ROW, CapabilityStatus.UNAVAILABLE, probed_at, error=error
                )
            )
            rows.extend(
                self._rows(
                    CORE_ROWS[1:], CapabilityStatus.UNAVAILABLE, probed_at, error=error
                )
            )
            rows.extend(self._rows(EXTENSION_ROWS, CapabilityStatus.UNKNOWN, probed_at))
            return rows, opend_version
        if not quote_login:
            error = OpenDError(
                OpenDErrorClass.NOT_LOGGED_IN,
                "OpenD quote service is not logged in",
                retryable=False,
            )
            issues.append(ProbeIssue("get_global_state", error))
            rows.append(
                self._row(
                    GLOBAL_STATE_ROW, CapabilityStatus.DENIED, probed_at, error=error
                )
            )
            rows.extend(
                self._rows(
                    CORE_ROWS[1:], CapabilityStatus.UNAVAILABLE, probed_at, error=error
                )
            )
            rows.extend(self._rows(EXTENSION_ROWS, CapabilityStatus.UNKNOWN, probed_at))
            return rows, opend_version

        rows.append(self._row(GLOBAL_STATE_ROW, CapabilityStatus.AVAILABLE, probed_at))

        market_result = self._safe_call(
            lambda: context.get_market_state(SEED_CODES.copy())
        )
        rows.extend(
            self._dataframe_rows(
                "get_market_state",
                market_result,
                MARKET_STATE_ROWS,
                probed_at,
                issues,
            )
        )

        quota_result = self._safe_call(lambda: context.get_history_kl_quota(False))
        rows.append(self._quota_row(quota_result, probed_at, issues))

        subscription_result = self._safe_call(
            lambda: context.query_subscription(True)
        )
        rows.append(
            self._subscription_row(subscription_result, probed_at, issues)
        )

        snapshot_result = self._safe_call(
            lambda: context.get_market_snapshot(SEED_CODES.copy())
        )
        rows.extend(
            self._dataframe_rows(
                "get_market_snapshot",
                snapshot_result,
                SNAPSHOT_ROWS,
                probed_at,
                issues,
            )
        )

        probe_day = probed_at.date()
        calendar_result = self._safe_call(
            lambda: context.request_trading_days(
                market="US",
                start=(probe_day - timedelta(days=10)).isoformat(),
                end=probe_day.isoformat(),
                code=None,
            )
        )
        rows.append(self._calendar_row(calendar_result, probed_at, issues))
        rows.extend(self._rows(EXTENSION_ROWS, CapabilityStatus.UNKNOWN, probed_at))
        return rows, opend_version

    def _dataframe_rows(
        self,
        interface: str,
        result: SdkCallResult[Any],
        specs: tuple[_RowSpec, _RowSpec],
        probed_at: datetime,
        issues: list[ProbeIssue],
    ) -> list[DataCapability]:
        if not result.is_ok:
            error = self._error_or_partial(result)
            issues.append(ProbeIssue(interface, error))
            return self._rows(specs, self._status(error), probed_at, error=error)

        value = result.value
        required_columns = {
            "get_market_state": {"code", "market_state"},
            "get_market_snapshot": {"code", "last_price"},
        }[interface]
        if (
            not isinstance(value, pd.DataFrame)
            or value.empty
            or not required_columns <= set(value.columns)
        ):
            error = partial_response_error()
            issues.append(ProbeIssue(interface, error))
            return self._rows(
                specs, CapabilityStatus.UNAVAILABLE, probed_at, error=error
            )

        codes = value["code"].tolist()
        value_column = {
            "get_market_state": "market_state",
            "get_market_snapshot": "last_price",
        }[interface]
        target_by_asset = {"etf": "US.SPY", "stock": "US.AAPL"}
        field_validator = (
            self._is_nonempty_string
            if interface == "get_market_state"
            else self._is_finite_real
        )
        status_by_asset: dict[str, CapabilityStatus] = {}
        has_partial_response = any(
            not self._is_nonempty_string(code) for code in codes
        )
        field_values = value[value_column].tolist()
        for asset_type, target_code in target_by_asset.items():
            target_values = [
                field_value
                for code, field_value in zip(codes, field_values, strict=True)
                if isinstance(code, str) and code == target_code
            ]
            is_available = bool(target_values) and all(
                field_validator(field_value) for field_value in target_values
            )
            status_by_asset[asset_type] = (
                CapabilityStatus.AVAILABLE
                if is_available
                else CapabilityStatus.UNAVAILABLE
            )
            has_partial_response = has_partial_response or not is_available
        partial_error = partial_response_error() if has_partial_response else None
        if partial_error is not None:
            issues.append(ProbeIssue(interface, partial_error))
        return [
            self._row(
                spec,
                status_by_asset[spec.asset_type],
                probed_at,
                error=(
                    partial_error
                    if status_by_asset[spec.asset_type]
                    is CapabilityStatus.UNAVAILABLE
                    else None
                ),
            )
            for spec in specs
        ]

    def _quota_row(
        self,
        result: SdkCallResult[tuple[int, int, list[object]]],
        probed_at: datetime,
        issues: list[ProbeIssue],
    ) -> DataCapability:
        if not result.is_ok:
            error = self._error_or_partial(result)
            issues.append(ProbeIssue(QUOTA_ROW.interface, error))
            return self._row(QUOTA_ROW, self._status(error), probed_at, error=error)
        quota = result.value
        if (
            not isinstance(quota, tuple)
            or len(quota) != 3
            or not self._is_nonnegative_int(quota[0])
            or not self._is_nonnegative_int(quota[1])
            or not isinstance(quota[2], list)
        ):
            error = partial_response_error()
            issues.append(ProbeIssue(QUOTA_ROW.interface, error))
            return self._row(
                QUOTA_ROW, CapabilityStatus.UNAVAILABLE, probed_at, error=error
            )
        used_quota, remain_quota, _detail_list = quota
        if remain_quota <= 0:
            error = OpenDError(
                OpenDErrorClass.QUOTA_EXHAUSTED,
                "OpenD quota exhausted",
                retryable=False,
            )
            issues.append(ProbeIssue(QUOTA_ROW.interface, error))
            return self._row(
                QUOTA_ROW,
                CapabilityStatus.DENIED,
                probed_at,
                quota_total=used_quota + remain_quota,
                quota_used=used_quota,
                quota_remaining=remain_quota,
                error=error,
            )
        return self._row(
            QUOTA_ROW,
            CapabilityStatus.AVAILABLE,
            probed_at,
            quota_total=used_quota + remain_quota,
            quota_used=used_quota,
            quota_remaining=remain_quota,
        )

    def _subscription_row(
        self,
        result: SdkCallResult[dict[str, object]],
        probed_at: datetime,
        issues: list[ProbeIssue],
    ) -> DataCapability:
        if not result.is_ok:
            error = self._error_or_partial(result)
            issues.append(ProbeIssue(SUBSCRIPTION_ROW.interface, error))
            return self._row(
                SUBSCRIPTION_ROW, self._status(error), probed_at, error=error
            )
        subscription = result.value
        if not isinstance(subscription, dict):
            error = partial_response_error()
            issues.append(ProbeIssue(SUBSCRIPTION_ROW.interface, error))
            return self._row(
                SUBSCRIPTION_ROW, CapabilityStatus.UNAVAILABLE, probed_at, error=error
            )

        total_used = subscription.get("total_used")
        remain = subscription.get("remain")
        sub_list = subscription.get("sub_list")
        if (
            not self._is_nonnegative_int(total_used)
            or not self._is_nonnegative_int(remain)
            or not isinstance(sub_list, dict)
            or not all(
                isinstance(subtype, str)
                and isinstance(codes, list)
                and all(isinstance(code, str) for code in codes)
                for subtype, codes in sub_list.items()
            )
        ):
            error = partial_response_error()
            issues.append(ProbeIssue(SUBSCRIPTION_ROW.interface, error))
            return self._row(
                SUBSCRIPTION_ROW, CapabilityStatus.UNAVAILABLE, probed_at, error=error
            )
        count = sum(len(codes) for codes in sub_list.values())
        LOGGER.info("OpenD subscription_count=%d", count)
        if remain <= 0:
            error = OpenDError(
                OpenDErrorClass.QUOTA_EXHAUSTED,
                "OpenD quota exhausted",
                retryable=False,
            )
            issues.append(ProbeIssue(SUBSCRIPTION_ROW.interface, error))
            return self._row(
                SUBSCRIPTION_ROW,
                CapabilityStatus.DENIED,
                probed_at,
                quota_total=total_used + remain,
                quota_used=total_used,
                quota_remaining=remain,
                error=error,
            )
        return self._row(
            SUBSCRIPTION_ROW,
            CapabilityStatus.AVAILABLE,
            probed_at,
            quota_total=total_used + remain,
            quota_used=total_used,
            quota_remaining=remain,
        )

    def _calendar_row(
        self,
        result: SdkCallResult[list[dict[str, object]]],
        probed_at: datetime,
        issues: list[ProbeIssue],
    ) -> DataCapability:
        if not result.is_ok:
            error = self._error_or_partial(result)
            issues.append(ProbeIssue(CALENDAR_ROW.interface, error))
            return self._row(CALENDAR_ROW, self._status(error), probed_at, error=error)
        calendar = result.value
        if (
            not isinstance(calendar, list)
            or not calendar
            or not all(
                isinstance(day, dict)
                and isinstance(day.get("time"), str)
                and bool(day["time"])
                and isinstance(day.get("trade_date_type"), str)
                and bool(day["trade_date_type"])
                for day in calendar
            )
        ):
            error = partial_response_error()
            issues.append(ProbeIssue(CALENDAR_ROW.interface, error))
            return self._row(
                CALENDAR_ROW, CapabilityStatus.UNAVAILABLE, probed_at, error=error
            )
        return self._row(CALENDAR_ROW, CapabilityStatus.AVAILABLE, probed_at)

    @staticmethod
    def _error_or_partial(result: SdkCallResult[Any]) -> OpenDError:
        return result.error or partial_response_error()

    @staticmethod
    def _safe_call(call: Callable[[], SdkCallResult[T]]) -> SdkCallResult[T]:
        try:
            return call()
        except Exception as exception:
            return SdkCallResult.failure(classify_sdk_exception(exception))

    @staticmethod
    def _is_non_bool_int(value: object) -> TypeGuard[int]:
        return isinstance(value, int) and not isinstance(value, bool)

    @classmethod
    def _is_nonnegative_int(cls, value: object) -> TypeGuard[int]:
        return cls._is_non_bool_int(value) and value >= 0

    @staticmethod
    def _is_nonempty_string(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    @staticmethod
    def _is_finite_real(value: object) -> bool:
        if isinstance(value, bool) or not isinstance(value, Real):
            return False
        try:
            return isfinite(value)
        except (OverflowError, TypeError, ValueError):
            return False

    @staticmethod
    def _parse_login_state(value: object) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            if value in {0, 1}:
                return bool(value)
            return None
        if isinstance(value, str):
            normalized = value.casefold()
            if normalized in {"1", "true"}:
                return True
            if normalized in {"0", "false"}:
                return False
        return None

    @staticmethod
    def _status(error: OpenDError) -> CapabilityStatus:
        if error.error_class in {
            OpenDErrorClass.NOT_LOGGED_IN,
            OpenDErrorClass.PERMISSION_DENIED,
            OpenDErrorClass.QUOTA_EXHAUSTED,
        }:
            return CapabilityStatus.DENIED
        return CapabilityStatus.UNAVAILABLE

    @staticmethod
    def _row(
        spec: _RowSpec,
        status: CapabilityStatus,
        probed_at: datetime,
        *,
        quota_total: int | None = None,
        quota_used: int | None = None,
        quota_remaining: int | None = None,
        error: OpenDError | None = None,
    ) -> DataCapability:
        point_in_time_status = (
            spec.point_in_time_status
            if status is CapabilityStatus.AVAILABLE
            else PointInTimeStatus.UNPROVEN
        )
        return DataCapability(
            interface=spec.interface,
            market=spec.market,
            asset_type=spec.asset_type,
            timeframe=spec.timeframe,
            status=status,
            point_in_time_status=point_in_time_status,
            probed_at=probed_at,
            permission_scope=spec.permission_scope,
            degradation_policy=spec.degradation_policy,
            quota_total=quota_total,
            quota_used=quota_used,
            quota_remaining=quota_remaining,
            last_error_class=(
                CapabilityErrorClass(error.error_class.value)
                if error is not None
                else None
            ),
        )

    def _rows(
        self,
        specs: Iterable[_RowSpec],
        status: CapabilityStatus,
        probed_at: datetime,
        *,
        error: OpenDError | None = None,
    ) -> list[DataCapability]:
        return [self._row(spec, status, probed_at, error=error) for spec in specs]

    @staticmethod
    def _compatibility(opend_version: str, sdk_version: str) -> CompatibilityStatus:
        def major_minor(value: str) -> tuple[int, int] | None:
            try:
                major, minor, *_rest = value.split(".")
                return int(major), int(minor)
            except (TypeError, ValueError):
                return None

        opend_major_minor = major_minor(opend_version)
        sdk_major_minor = major_minor(sdk_version)
        if opend_major_minor is None or sdk_major_minor is None:
            return CompatibilityStatus.UNKNOWN
        if opend_major_minor == sdk_major_minor:
            return CompatibilityStatus.COMPATIBLE
        return CompatibilityStatus.DEGRADED
