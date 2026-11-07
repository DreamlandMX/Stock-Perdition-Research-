"""Read-only lifecycle-safe adapter around ``futu.OpenQuoteContext``."""

import base64
import binascii
from types import TracebackType
from typing import Any, Literal, TypeVar, cast

from futu import RET_OK, AuType, KLType, OpenQuoteContext, Session, SubType

from stock_prediction.opend.errors import (
    classify_sdk_error,
    classify_sdk_exception,
    partial_response_error,
)
from stock_prediction.opend.ports import (
    QuoteContextFactory,
    RawHistoryPage,
    RawQuoteContext,
    SdkCallResult,
)

T = TypeVar("T")


def _open_quote_context(
    *, host: str, port: int, is_async_connect: bool = False
) -> RawQuoteContext:
    return cast(
        RawQuoteContext,
        OpenQuoteContext(host=host, port=port, is_async_connect=is_async_connect),
    )


class FutuQuoteContext:
    """Own exactly one futu quote context and expose only read-only calls."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        *,
        quote_context_factory: QuoteContextFactory = _open_quote_context,
        connect_timeout_seconds: float | None = None,
    ) -> None:
        self._context = (
            quote_context_factory(host=host, port=port)
            if connect_timeout_seconds is None
            else quote_context_factory(host=host, port=port, is_async_connect=True)
        )
        if connect_timeout_seconds is not None:
            self._context.set_sync_query_connect_timeout(connect_timeout_seconds)
        self._closed = False

    def __enter__(self) -> "FutuQuoteContext":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        self.close()
        return False

    def close(self) -> None:
        """Close the owned SDK context once, even if called repeatedly."""

        if self._closed:
            return
        self._closed = True
        self._context.close()

    def get_global_state(self) -> SdkCallResult[dict[str, object]]:
        return self._call("get_global_state")

    def get_market_state(self, code_list: list[str]) -> SdkCallResult[Any]:
        return self._call("get_market_state", code_list)

    def get_history_kl_quota(
        self, get_detail: bool = False
    ) -> SdkCallResult[tuple[int, int, list[object]]]:
        return self._call("get_history_kl_quota", get_detail)

    def query_subscription(
        self, is_all_conn: bool = True
    ) -> SdkCallResult[dict[str, object]]:
        return self._call("query_subscription", is_all_conn)

    def get_market_snapshot(self, code_list: list[str]) -> SdkCallResult[Any]:
        return self._call("get_market_snapshot", code_list)

    def get_current_5m(self, code: str) -> SdkCallResult[Any]:
        subscribed: SdkCallResult[Any] = self._call(
            "subscribe",
            [code],
            [SubType.K_5M],
            subscribe_push=False,
            session=Session.RTH,
        )
        if not subscribed.is_ok:
            return subscribed
        return self._call("get_cur_kline", code, 1000, KLType.K_5M, AuType.NONE)

    def get_option_expiration_date(self, code: str) -> SdkCallResult[Any]:
        return self._call("get_option_expiration_date", code)

    def get_option_chain(
        self, code: str, *, start: str, end: str
    ) -> SdkCallResult[Any]:
        return self._call("get_option_chain", code, start=start, end=end)

    def get_rehab(self, code_list: list[str]) -> SdkCallResult[Any]:
        if len(code_list) != 1:
            return SdkCallResult.failure(partial_response_error())
        return self._call("get_rehab", code_list[0])

    def request_trading_days(
        self,
        market: str,
        start: str,
        end: str,
        code: str | None = None,
    ) -> SdkCallResult[list[dict[str, object]]]:
        return self._call(
            "request_trading_days",
            market=market,
            start=start,
            end=end,
            code=code,
        )

    def request_history_kline(
        self,
        *,
        code: str,
        timeframe: str,
        start: str,
        end: str,
        max_count: int,
    ) -> SdkCallResult[Any]:
        ktype = {"1d": KLType.K_DAY, "60m": KLType.K_60M}.get(timeframe)
        if ktype is None:
            return SdkCallResult.failure(partial_response_error())
        try:
            response = self._context.request_history_kline(
                code=code,
                start=start,
                end=end,
                ktype=ktype,
                autype=AuType.NONE,
                max_count=max_count,
            )
        except Exception as exception:
            return SdkCallResult.failure(classify_sdk_exception(exception))
        if not isinstance(response, tuple) or len(response) != 3:
            return SdkCallResult.failure(partial_response_error())
        ret, data, _page_key = response
        if ret != RET_OK:
            raw_message = data if isinstance(data, str) else ""
            return SdkCallResult.failure(classify_sdk_error(raw_message))
        return SdkCallResult.success(data)

    def request_history_kline_page(
        self,
        *,
        code: str,
        timeframe: str,
        start: str,
        end: str,
        max_count: int,
        page_token: str | None,
        extended_time: bool = False,
    ) -> SdkCallResult[RawHistoryPage]:
        ktype = {"1d": KLType.K_DAY, "60m": KLType.K_60M}.get(timeframe)
        if ktype is None:
            return SdkCallResult.failure(partial_response_error())
        page_key: bytes | None = None
        if page_token is not None:
            try:
                if len(page_token) > 4096:
                    raise ValueError
                page_key = base64.b64decode(
                    page_token.encode("ascii"), altchars=b"-_", validate=True
                )
                if not page_key:
                    raise ValueError
            except (UnicodeEncodeError, ValueError, binascii.Error):
                return SdkCallResult.failure(partial_response_error())
        try:
            response = self._context.request_history_kline(
                code=code,
                start=start,
                end=end,
                ktype=ktype,
                autype=AuType.NONE,
                max_count=max_count,
                page_req_key=page_key,
                extended_time=extended_time,
            )
        except Exception as exception:
            return SdkCallResult.failure(classify_sdk_exception(exception))
        if not isinstance(response, tuple) or len(response) != 3:
            return SdkCallResult.failure(partial_response_error())
        ret, data, raw_next_page_key = response
        if ret != RET_OK:
            raw_message = data if isinstance(data, str) else ""
            return SdkCallResult.failure(classify_sdk_error(raw_message))
        if raw_next_page_key in (None, b""):
            next_page_token = None
        elif isinstance(raw_next_page_key, bytes):
            next_page_token = base64.urlsafe_b64encode(raw_next_page_key).decode(
                "ascii"
            )
        else:
            return SdkCallResult.failure(partial_response_error())
        return SdkCallResult.success(
            RawHistoryPage(frame=data, next_page_token=next_page_token)
        )

    def _call(
        self, method_name: str, *args: object, **kwargs: object
    ) -> SdkCallResult[T]:
        method = getattr(self._context, method_name)
        try:
            response = method(*args, **kwargs)
        except Exception as exception:
            return SdkCallResult.failure(classify_sdk_exception(exception))
        if not isinstance(response, tuple) or len(response) != 2:
            return SdkCallResult.failure(partial_response_error())

        ret, data = response
        if ret != RET_OK:
            raw_message = data if isinstance(data, str) else ""
            return SdkCallResult.failure(classify_sdk_error(raw_message))
        if method_name == "subscribe" and data is None:
            data = {}
        return SdkCallResult.success(cast(T, data))
