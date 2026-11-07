from datetime import UTC, date, datetime
from types import TracebackType
from typing import Any, Literal, Self

import pandas as pd
import pytest

from stock_prediction.contracts.market_data import Timeframe
from stock_prediction.opend.errors import OpenDError, OpenDErrorClass
from stock_prediction.opend.history import (
    HistoryReadError,
    HistoryReadRequest,
    OpenDHistoryReader,
)
from stock_prediction.opend.ports import RawHistoryPage, SdkCallResult


def frame(
    *,
    code: str = "US.SPY",
    time_key: str = "2026-07-10 10:30:00",
    close: float = 621.0,
    turnover: float = 620_500_000.0,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": [code],
            "time_key": [time_key],
            "open": [620.0],
            "high": [622.0],
            "low": [619.0],
            "close": [close],
            "volume": [1_000_000],
            "turnover": [turnover],
        }
    )


class FakePagedContext:
    def __init__(self, responses: list[SdkCallResult[RawHistoryPage]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self.close_calls = 0

    def __enter__(self) -> Self:
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
        self.close_calls += 1

    def request_history_kline_page(
        self, **kwargs: object
    ) -> SdkCallResult[RawHistoryPage]:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def request(**overrides: Any) -> HistoryReadRequest:
    values: dict[str, Any] = {
        "code": "US.SPY",
        "timeframe": Timeframe.MINUTE_60,
        "start": date(2026, 7, 10),
        "end": date(2026, 7, 10),
        "max_count": 100,
        "batch_id": "batch-spy-60m-20260710",
        "schema_version": "market-bar-v1",
        "dataset_revision": "dataset-r1",
    }
    values.update(overrides)
    return HistoryReadRequest(**values)


def reader(context: FakePagedContext) -> OpenDHistoryReader:
    return OpenDHistoryReader(
        context_factory=lambda: context,
        clock=lambda: datetime(2026, 7, 10, 20, 2, tzinfo=UTC),
    )


def test_reader_normalizes_multiple_pages_and_closes_one_context() -> None:
    context = FakePagedContext(
        [
            SdkCallResult.success(RawHistoryPage(frame(), "page-2")),
            SdkCallResult.success(
                RawHistoryPage(frame(time_key="2026-07-10 11:30:00", close=622.0), None)
            ),
        ]
    )

    pages = reader(context).read(request())

    assert len(pages) == 2
    assert [page.page_index for page in pages] == [1, 2]
    assert [page.rows[0].close for page in pages] == [621.0, 622.0]
    assert pages[0].rows[0].bar_start.isoformat() == "2026-07-10T13:30:00+00:00"
    assert pages[0].rows[0].source_available_at.isoformat() == (
        "2026-07-10T14:30:00+00:00"
    )
    assert context.calls[0]["page_token"] is None
    assert context.calls[1]["page_token"] == "page-2"
    assert context.close_calls == 1


def test_iter_pages_resumes_from_committed_token_and_page_index() -> None:
    context = FakePagedContext(
        [
            SdkCallResult.success(
                RawHistoryPage(frame(time_key="2026-07-10 12:30:00"), "page-6")
            ),
            SdkCallResult.success(
                RawHistoryPage(frame(time_key="2026-07-10 13:30:00"), None)
            ),
        ]
    )

    pages = tuple(
        reader(context).iter_pages(
            request(),
            context=context,
            initial_page_token="page-5",
            initial_page_index=5,
        )
    )

    assert [page.page_index for page in pages] == [6, 7]
    assert [call["page_token"] for call in context.calls] == ["page-5", "page-6"]
    assert context.close_calls == 0


@pytest.mark.parametrize(
    ("initial_page_token", "initial_page_index"),
    [(None, -1), (None, 1), ("page-1", 0)],
)
def test_iter_pages_rejects_invalid_resume_state_before_request(
    initial_page_token: str | None,
    initial_page_index: int,
) -> None:
    context = FakePagedContext([])

    with pytest.raises(HistoryReadError, match="invalid_history_resume_state"):
        tuple(
            reader(context).iter_pages(
                request(),
                context=context,
                initial_page_token=initial_page_token,
                initial_page_index=initial_page_index,
            )
        )

    assert context.calls == []


def test_iter_pages_rejects_resume_token_returned_again() -> None:
    context = FakePagedContext(
        [SdkCallResult.success(RawHistoryPage(frame(), "page-5"))]
    )

    with pytest.raises(HistoryReadError, match="repeated_page_token"):
        tuple(
            reader(context).iter_pages(
                request(),
                context=context,
                initial_page_token="page-5",
                initial_page_index=5,
            )
        )


def test_each_page_records_its_actual_post_response_ingestion_time() -> None:
    context = FakePagedContext(
        [
            SdkCallResult.success(RawHistoryPage(frame(), "page-2")),
            SdkCallResult.success(
                RawHistoryPage(frame(time_key="2026-07-10 11:30:00"), None)
            ),
        ]
    )
    times = iter(
        [
            datetime(2026, 7, 10, 20, 1, tzinfo=UTC),
            datetime(2026, 7, 10, 20, 2, tzinfo=UTC),
        ]
    )
    active_reader = OpenDHistoryReader(
        context_factory=lambda: context, clock=lambda: next(times)
    )

    pages = active_reader.read(request())

    assert pages[0].rows[0].ingested_at.isoformat() == "2026-07-10T20:01:00+00:00"
    assert pages[1].rows[0].ingested_at.isoformat() == "2026-07-10T20:02:00+00:00"


def test_daily_bar_uses_market_midnight_and_close_as_source_availability() -> None:
    context = FakePagedContext(
        [
            SdkCallResult.success(
                RawHistoryPage(frame(time_key="2026-07-10 00:00:00"), None)
            )
        ]
    )

    pages = reader(context).read(request(timeframe=Timeframe.DAY_1))
    row = pages[0].rows[0]

    assert row.bar_start.isoformat() == "2026-07-10T04:00:00+00:00"
    assert row.source_available_at.isoformat() == "2026-07-10T20:00:00+00:00"


@pytest.mark.parametrize(
    ("label", "start", "end"),
    [
        (
            "2026-07-10 16:00:00",
            "2026-07-10T19:30:00+00:00",
            "2026-07-10T20:00:00+00:00",
        ),
        (
            "2025-11-28 13:00:00",
            "2025-11-28T17:30:00+00:00",
            "2025-11-28T18:00:00+00:00",
        ),
    ],
)
def test_intraday_short_close_uses_actual_end(label: str, start: str, end: str) -> None:
    context = FakePagedContext(
        [SdkCallResult.success(RawHistoryPage(frame(time_key=label), None))]
    )
    session_date = date.fromisoformat(label[:10])
    row = reader(context).read(request(start=session_date, end=session_date))[0].rows[0]
    assert row.bar_start.isoformat() == start
    assert row.source_available_at.isoformat() == end


def test_zero_turnover_is_preserved_as_valid_numeric_evidence() -> None:
    context = FakePagedContext(
        [SdkCallResult.success(RawHistoryPage(frame(turnover=0.0), None))]
    )

    pages = reader(context).read(request())

    assert pages[0].rows[0].turnover == 0.0


def test_repeated_page_token_fails_and_closes_context() -> None:
    context = FakePagedContext(
        [
            SdkCallResult.success(RawHistoryPage(frame(), "same-token")),
            SdkCallResult.success(
                RawHistoryPage(frame(time_key="2026-07-10 11:30:00"), "same-token")
            ),
        ]
    )

    with pytest.raises(HistoryReadError, match="repeated_page_token"):
        reader(context).read(request())

    assert context.close_calls == 1


def test_cross_page_out_of_order_or_empty_intermediate_page_fails() -> None:
    out_of_order = FakePagedContext(
        [
            SdkCallResult.success(
                RawHistoryPage(frame(time_key="2026-07-10 11:30:00"), "page-2")
            ),
            SdkCallResult.success(RawHistoryPage(frame(), None)),
        ]
    )
    with pytest.raises(HistoryReadError, match="invalid_history_page_sequence"):
        reader(out_of_order).read(request())

    empty = frame().iloc[0:0]
    empty_intermediate = FakePagedContext(
        [SdkCallResult.success(RawHistoryPage(empty, "page-2"))]
    )
    with pytest.raises(HistoryReadError, match="empty_intermediate_history_page"):
        reader(empty_intermediate).read(request())


@pytest.mark.parametrize(
    "bad_frame",
    [
        frame(code="US.QQQ"),
        frame(time_key="2026-07-11 09:30:00"),
        frame(time_key="2026-07-10 09:30:00"),
        frame(time_key="2026-07-10 16:30:00"),
        frame(close=float("nan")),
        frame().drop(columns=["volume"]),
    ],
)
def test_wrong_code_window_or_malformed_values_fail_closed(
    bad_frame: pd.DataFrame,
) -> None:
    context = FakePagedContext([SdkCallResult.success(RawHistoryPage(bad_frame, None))])

    with pytest.raises(HistoryReadError, match="invalid_history_page"):
        reader(context).read(request())

    assert context.close_calls == 1


def test_sdk_failure_preserves_only_stable_error_class() -> None:
    error = OpenDError(
        OpenDErrorClass.RATE_LIMITED,
        "OpenD request rate limited",
        retryable=True,
    )
    context = FakePagedContext([SdkCallResult.failure(error)])

    with pytest.raises(HistoryReadError) as captured:
        reader(context).read(request())

    assert captured.value.error_class is OpenDErrorClass.RATE_LIMITED
    assert "secret" not in str(captured.value)
    assert context.close_calls == 1
