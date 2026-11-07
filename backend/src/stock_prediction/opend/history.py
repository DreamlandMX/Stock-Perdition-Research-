"""Paged OpenD history reader and strict MarketBar normalization."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
import math
from types import TracebackType
from typing import Any, Literal, Protocol, Self
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from stock_prediction.contracts.market_data import (
    Identifier,
    MarketBar,
    MarketSession,
    Timeframe,
    UsSymbol,
    VersionId,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.opend.errors import OpenDErrorClass
from stock_prediction.opend.ports import RawHistoryPage, SdkCallResult

_NEW_YORK = ZoneInfo("America/New_York")
_REQUIRED_COLUMNS = {
    "code",
    "time_key",
    "open",
    "high",
    "low",
    "close",
    "volume",
}


class HistoryReadRequest(BaseModel):
    """One bounded, unadjusted OpenD K-line request."""

    model_config = ConfigDict(strict=True, frozen=True)

    code: UsSymbol
    timeframe: Timeframe
    start: date
    end: date
    max_count: int = Field(ge=1, le=1000)
    batch_id: Identifier
    schema_version: VersionId
    dataset_revision: VersionId

    @model_validator(mode="after")
    def validate_range(self) -> "HistoryReadRequest":
        if self.start > self.end:
            raise ValueError("start must not be after end")
        return self


@dataclass(frozen=True, slots=True)
class HistoryPage:
    page_index: int
    rows: tuple[MarketBar, ...]
    next_page_token: str | None


class PagedHistoryContext(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]: ...

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


class HistoryReadError(RuntimeError):
    """Sanitized failure from pagination or history normalization."""

    def __init__(
        self,
        code: str,
        error_class: OpenDErrorClass = OpenDErrorClass.PARTIAL_RESPONSE,
    ) -> None:
        self.code = code
        self.error_class = error_class
        super().__init__(code)


class OpenDHistoryReader:
    """Use exactly one read-only context for a bounded paged request."""

    def __init__(
        self,
        *,
        context_factory: Callable[[], PagedHistoryContext],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_pages: int = 10_000,
    ) -> None:
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        self.context_factory = context_factory
        self.clock = clock
        self.max_pages = max_pages

    def read(self, request: HistoryReadRequest) -> tuple[HistoryPage, ...]:
        with self.context_factory() as context:
            return tuple(self.iter_pages(request, context=context))

    def iter_pages(
        self,
        request: HistoryReadRequest,
        *,
        context: PagedHistoryContext,
        initial_page_token: str | None = None,
        initial_page_index: int = 0,
        prior_watermark: datetime | None = None,
        prior_page_tokens: Collection[str] = (),
    ) -> Iterator[HistoryPage]:
        """Yield normalized history pages from one caller-owned context."""

        if (
            initial_page_index < 0
            or initial_page_index > self.max_pages
            or (initial_page_index == 0) != (initial_page_token is None)
        ):
            raise HistoryReadError("invalid_history_resume_state")
        requested_tokens = set(prior_page_tokens)
        if initial_page_token is not None:
            requested_tokens.add(initial_page_token)
        seen_natural_keys: set[tuple[str, str, str, str]] = set()
        last_bar_start = prior_watermark
        page_token = initial_page_token
        for page_index in range(initial_page_index + 1, self.max_pages + 1):
            result = context.request_history_kline_page(
                code=request.code,
                timeframe=request.timeframe.value,
                start=request.start.isoformat(),
                end=request.end.isoformat(),
                max_count=request.max_count,
                page_token=page_token,
            )
            if not result.is_ok or result.value is None:
                error_class = (
                    result.error.error_class
                    if result.error is not None
                    else OpenDErrorClass.PARTIAL_RESPONSE
                )
                raise HistoryReadError("opend_history_request_failed", error_class)
            observed_at = TypeAdapter(UtcDatetime).validate_python(self.clock())
            normalized = self._normalize_page(
                result.value,
                request=request,
                page_index=page_index,
                observed_at=observed_at,
            )
            if normalized.next_page_token is not None and not normalized.rows:
                raise HistoryReadError("empty_intermediate_history_page")
            page_starts = [row.bar_start for row in normalized.rows]
            page_keys = [row.natural_key for row in normalized.rows]
            if (
                page_starts != sorted(page_starts)
                or len(set(page_keys)) != len(page_keys)
                or any(key in seen_natural_keys for key in page_keys)
                or (
                    last_bar_start is not None
                    and page_starts
                    and page_starts[0] <= last_bar_start
                )
            ):
                raise HistoryReadError("invalid_history_page_sequence")
            seen_natural_keys.update(page_keys)
            if page_starts:
                last_bar_start = page_starts[-1]
            next_token = result.value.next_page_token
            if next_token is None:
                yield normalized
                return
            if next_token in requested_tokens or next_token == page_token:
                raise HistoryReadError("repeated_page_token")
            requested_tokens.add(next_token)
            page_token = next_token
            yield normalized
        raise HistoryReadError("history_page_limit_exceeded")

    @staticmethod
    def _normalize_page(
        raw_page: RawHistoryPage,
        *,
        request: HistoryReadRequest,
        page_index: int,
        observed_at: datetime,
    ) -> HistoryPage:
        frame = raw_page.frame
        if not isinstance(frame, pd.DataFrame) or not _REQUIRED_COLUMNS <= set(
            frame.columns
        ):
            raise HistoryReadError("invalid_history_page")
        rows: list[MarketBar] = []
        try:
            for raw in frame.to_dict(orient="records"):
                if raw["code"] != request.code:
                    raise ValueError
                local_start = _local_datetime(raw["time_key"])
                if not request.start <= local_start.date() <= request.end:
                    raise ValueError
                if request.timeframe is Timeframe.MINUTE_60:
                    # OpenD labels intraday bars by their end, including short closes.
                    local_end = local_start
                    if local_end.time() in (time(13, 0), time(16, 0)):
                        local_start -= timedelta(minutes=30)
                    elif (
                        time(10, 30) <= local_end.time() <= time(15, 30)
                        and local_end.minute == 30
                        and local_end.second == local_end.microsecond == 0
                    ):
                        local_start -= timedelta(hours=1)
                    else:
                        raise ValueError
                prices = {
                    field: _finite_float(raw[field])
                    for field in ("open", "high", "low", "close")
                }
                volume = _nonnegative_integer(raw["volume"])
                turnover_raw = raw.get("turnover")
                turnover = (
                    None
                    if turnover_raw is None or pd.isna(turnover_raw)
                    else _nonnegative_float(turnover_raw)
                )
                source_available_at = (
                    local_end.astimezone(UTC)
                    if request.timeframe is Timeframe.MINUTE_60
                    else _source_available_at(local_start, request.timeframe)
                )
                rows.append(
                    MarketBar(
                        symbol=request.code,
                        timeframe=request.timeframe,
                        session=MarketSession.REGULAR,
                        session_date=local_start.date(),
                        bar_start=local_start.astimezone(UTC),
                        **prices,
                        volume=volume,
                        turnover=turnover,
                        source_available_at=source_available_at,
                        ingested_at=observed_at,
                        raw_batch_id=request.batch_id,
                        schema_version=request.schema_version,
                        dataset_revision=request.dataset_revision,
                        revision_no=1,
                    )
                )
        except (KeyError, TypeError, ValueError, OverflowError, ValidationError):
            raise HistoryReadError("invalid_history_page") from None
        return HistoryPage(
            page_index=page_index,
            rows=tuple(rows),
            next_page_token=raw_page.next_page_token,
        )


def _local_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_NEW_YORK)
    return parsed.astimezone(_NEW_YORK)


def _finite_float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError
    return parsed


def _nonnegative_integer(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0 or not parsed.is_integer():
        raise ValueError
    return int(parsed)


def _nonnegative_float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError
    return parsed


def _source_available_at(local_start: datetime, timeframe: Timeframe) -> datetime:
    market_close = datetime.combine(local_start.date(), time(16, 0), _NEW_YORK)
    if timeframe is Timeframe.DAY_1:
        return market_close.astimezone(UTC)
    return min(local_start + timedelta(hours=1), market_close).astimezone(UTC)
