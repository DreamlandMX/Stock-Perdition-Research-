"""Typed ports between capability probing and the futu SDK."""

from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Generic, Literal, Protocol, Self, TypeVar

from stock_prediction.opend.errors import OpenDError

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class SdkCallResult(Generic[T]):
    """Success or sanitized failure from one SDK call."""

    value: T | None = None
    error: OpenDError | None = None

    def __post_init__(self) -> None:
        has_value = self.value is not None
        has_error = self.error is not None
        if has_value == has_error:
            raise ValueError("SdkCallResult requires exactly one of value or error")

    @property
    def is_ok(self) -> bool:
        return self.value is not None

    @classmethod
    def success(cls, value: T) -> "SdkCallResult[T]":
        return cls(value=value)

    @classmethod
    def failure(cls, error: OpenDError) -> "SdkCallResult[T]":
        return cls(error=error)


@dataclass(frozen=True, slots=True)
class RawHistoryPage:
    """One SDK history frame plus an opaque, serialization-safe page token."""

    frame: Any
    next_page_token: str | None


class RawQuoteContext(Protocol):
    """The deliberately small read-only surface used from futu."""

    def close(self) -> None: ...

    def set_sync_query_connect_timeout(self, timeout: float) -> None: ...

    def get_global_state(self) -> tuple[Any, ...]: ...

    def get_market_state(self, code_list: list[str]) -> tuple[Any, ...]: ...

    def get_history_kl_quota(self, get_detail: bool) -> tuple[Any, ...]: ...

    def query_subscription(self, is_all_conn: bool) -> tuple[Any, ...]: ...

    def get_market_snapshot(self, code_list: list[str]) -> tuple[Any, ...]: ...

    def subscribe(
        self, code_list: list[str], subtype_list: list[str], **kwargs: object
    ) -> tuple[Any, ...]: ...

    def get_cur_kline(
        self, code: str, num: int, ktype: str, autype: str
    ) -> tuple[Any, ...]: ...

    def get_option_expiration_date(self, code: str) -> tuple[Any, ...]: ...

    def get_option_chain(
        self, code: str, *, start: str, end: str
    ) -> tuple[Any, ...]: ...

    def get_rehab(self, code: str) -> tuple[Any, ...]: ...

    def request_trading_days(
        self,
        market: str,
        start: str,
        end: str,
        code: str | None,
    ) -> tuple[Any, ...]: ...

    def request_history_kline(self, **kwargs: object) -> tuple[Any, ...]: ...


QuoteContextFactory = Callable[..., RawQuoteContext]


class ReadOnlyQuoteContext(Protocol):
    """Normalized read-only methods consumed by the capability probe."""

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]: ...

    def get_global_state(self) -> SdkCallResult[dict[str, object]]: ...

    def get_market_state(self, code_list: list[str]) -> SdkCallResult[Any]: ...

    def get_history_kl_quota(
        self, get_detail: bool = False
    ) -> SdkCallResult[tuple[int, int, list[object]]]: ...

    def query_subscription(
        self, is_all_conn: bool = True
    ) -> SdkCallResult[dict[str, object]]: ...

    def get_market_snapshot(self, code_list: list[str]) -> SdkCallResult[Any]: ...

    def get_rehab(self, code_list: list[str]) -> SdkCallResult[Any]: ...

    def request_trading_days(
        self,
        market: str,
        start: str,
        end: str,
        code: str | None = None,
    ) -> SdkCallResult[list[dict[str, object]]]: ...

    def request_history_kline(
        self,
        *,
        code: str,
        timeframe: str,
        start: str,
        end: str,
        max_count: int,
    ) -> SdkCallResult[Any]: ...

    def request_history_kline_page(
        self,
        *,
        code: str,
        timeframe: str,
        start: str,
        end: str,
        max_count: int,
        page_token: str | None,
    ) -> SdkCallResult[RawHistoryPage]: ...
