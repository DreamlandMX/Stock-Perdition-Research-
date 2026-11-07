from datetime import date, time, timedelta

import pytest

from stock_prediction.contracts.market_data import MarketBar, MarketSession, Timeframe
from stock_prediction.data.quality import MarketBarQualityGate
from stock_prediction.data.sessions import SessionSchedule, UsSessionRules


SESSION_DATE = date(2026, 7, 10)


def intraday_rows(*, batch_id: str = "batch-60m") -> list[MarketBar]:
    starts = [
        "2026-07-10T13:30:00Z",
        "2026-07-10T14:30:00Z",
        "2026-07-10T15:30:00Z",
        "2026-07-10T16:30:00Z",
        "2026-07-10T17:30:00Z",
        "2026-07-10T18:30:00Z",
        "2026-07-10T19:30:00Z",
    ]
    return [
        MarketBar(
            symbol="US.SPY",
            timeframe=Timeframe.MINUTE_60,
            session=MarketSession.REGULAR,
            session_date=SESSION_DATE,
            bar_start=start,
            open=620.0 + index,
            high=621.0 + index,
            low=619.0 + index,
            close=620.5 + index,
            volume=1_000_000 + index,
            turnover=620_000_000.0,
            source_available_at="2026-07-10T20:00:05Z",
            ingested_at="2026-07-10T20:01:00Z",
            raw_batch_id=batch_id,
            schema_version="market-bar-v1",
            dataset_revision="dataset-r1",
            revision_no=1,
        )
        for index, start in enumerate(starts)
    ]


def gate() -> tuple[MarketBarQualityGate, dict[date, SessionSchedule]]:
    rules = UsSessionRules(version="us-session-test-v1")
    schedule = rules.schedule_for(SESSION_DATE, {SESSION_DATE})
    return MarketBarQualityGate(rules), {SESSION_DATE: schedule}


def test_complete_regular_session_passes_all_hard_gates() -> None:
    quality, schedules = gate()

    report = quality.evaluate(
        intraday_rows(), schedules=schedules, checked_at="2026-07-10T20:02:00Z"
    )

    assert report.input_rows == 7
    assert report.valid_rows == 7
    assert report.quarantined is False
    assert report.quality_score == 1.0
    assert report.issues == []


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda rows: rows + [rows[-1]], "duplicate_natural_key"),
        (lambda rows: list(reversed(rows)), "out_of_order_bar"),
        (lambda rows: rows[:-1], "missing_expected_bar"),
    ],
)
def test_duplicate_out_of_order_and_missing_bars_quarantine_batch(
    mutate, expected_code: str
) -> None:
    quality, schedules = gate()

    report = quality.evaluate(
        mutate(intraday_rows()),
        schedules=schedules,
        checked_at="2026-07-10T20:02:00Z",
    )

    assert report.quarantined is True
    assert report.valid_rows == 0
    assert report.quarantined_rows == report.input_rows
    assert expected_code in {issue.code for issue in report.issues}


def test_invalid_ohlc_and_session_date_quarantine_batch() -> None:
    quality, schedules = gate()
    rows = intraday_rows()
    rows[0] = rows[0].model_copy(update={"high": 610.0})
    rows[1] = rows[1].model_copy(update={"session_date": date(2026, 7, 9)})

    report = quality.evaluate(
        rows, schedules=schedules, checked_at="2026-07-10T20:02:00Z"
    )

    codes = {issue.code for issue in report.issues}
    assert report.quarantined is True
    assert "invalid_ohlc" in codes
    assert "session_date_mismatch" in codes


def test_future_bar_and_unexpected_window_are_rejected() -> None:
    quality, schedules = gate()
    rows = intraday_rows()
    rows[-1] = rows[-1].model_copy(
        update={"bar_start": rows[-1].bar_start + timedelta(days=1)}
    )

    report = quality.evaluate(
        rows, schedules=schedules, checked_at="2026-07-10T20:02:00Z"
    )

    codes = {issue.code for issue in report.issues}
    assert "future_bar" in codes
    assert "unexpected_bar" in codes
    assert report.quarantined is True


def test_missing_turnover_is_soft_warning_not_quarantine() -> None:
    quality, schedules = gate()
    rows = intraday_rows()
    rows[0] = rows[0].model_copy(update={"turnover": None})

    report = quality.evaluate(
        rows, schedules=schedules, checked_at="2026-07-10T20:02:00Z"
    )

    assert report.quarantined is False
    assert report.valid_rows == 7
    assert report.quality_score < 1.0
    assert [issue.code for issue in report.issues] == ["missing_turnover"]


def test_empty_response_is_hard_failure_with_zero_rows() -> None:
    quality, schedules = gate()

    report = quality.evaluate(
        [],
        batch_id="batch-empty",
        schedules=schedules,
        checked_at="2026-07-10T20:02:00Z",
    )

    assert report.quarantined is True
    assert report.quarantined_rows == 0
    assert [issue.code for issue in report.issues] == ["empty_batch"]


def test_unexplained_large_jump_is_hard_failure() -> None:
    quality, schedules = gate()
    rows = intraday_rows()
    rows[0] = rows[0].model_copy(
        update={"open": 200.0, "high": 201.0, "low": 199.0, "close": 200.5}
    )

    report = quality.evaluate(
        rows,
        schedules=schedules,
        checked_at="2026-07-10T20:02:00Z",
        previous_close_by_symbol={"US.SPY": 100.0},
        explained_action_dates=set(),
    )

    assert "unexplained_price_jump" in {issue.code for issue in report.issues}
    assert report.quarantined is True


def test_half_day_coverage_accepts_four_bars() -> None:
    half_date = date(2026, 11, 27)
    rules = UsSessionRules(
        version="us-session-test-v1",
        half_day_closes={half_date: time(13, 0)},
    )
    schedule = rules.schedule_for(half_date, {half_date})
    rows = intraday_rows()
    starts = rules.expected_bar_starts(schedule, Timeframe.MINUTE_60)
    adjusted = [
        row.model_copy(
            update={
                "session_date": half_date,
                "bar_start": starts[index],
                "source_available_at": "2026-11-27T18:00:05Z",
                "ingested_at": "2026-11-27T18:01:00Z",
            }
        )
        for index, row in enumerate(rows[:4])
    ]

    report = MarketBarQualityGate(rules).evaluate(
        adjusted,
        schedules={half_date: schedule},
        checked_at="2026-11-27T18:02:00Z",
    )

    assert report.quarantined is False
    assert report.input_rows == 4
