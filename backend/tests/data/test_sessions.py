from datetime import UTC, date, time

import pytest

from stock_prediction.contracts.market_data import Timeframe
from stock_prediction.data.sessions import NotTradingDayError, UsSessionRules


def test_session_schedule_converts_est_and_edt_to_utc() -> None:
    verified = {date(2026, 3, 6), date(2026, 3, 9)}
    rules = UsSessionRules(version="us-session-test-v1")

    before_dst = rules.schedule_for(date(2026, 3, 6), verified)
    after_dst = rules.schedule_for(date(2026, 3, 9), verified)

    assert before_dst.open_at.tzinfo is UTC
    assert before_dst.open_at.isoformat() == "2026-03-06T14:30:00+00:00"
    assert after_dst.open_at.isoformat() == "2026-03-09T13:30:00+00:00"
    assert before_dst.close_at.isoformat() == "2026-03-06T21:00:00+00:00"
    assert after_dst.close_at.isoformat() == "2026-03-09T20:00:00+00:00"


def test_fall_dst_boundary_uses_the_correct_offset() -> None:
    verified = {date(2026, 10, 30), date(2026, 11, 2)}
    rules = UsSessionRules(version="us-session-test-v1")

    friday = rules.schedule_for(date(2026, 10, 30), verified)
    monday = rules.schedule_for(date(2026, 11, 2), verified)

    assert friday.open_at.isoformat() == "2026-10-30T13:30:00+00:00"
    assert monday.open_at.isoformat() == "2026-11-02T14:30:00+00:00"


def test_half_day_has_four_exchange_aligned_60_minute_bars() -> None:
    session_date = date(2026, 11, 27)
    rules = UsSessionRules(
        version="us-session-test-v1",
        half_day_closes={session_date: time(13, 0)},
    )
    schedule = rules.schedule_for(session_date, {session_date})

    starts = rules.expected_bar_starts(schedule, Timeframe.MINUTE_60)

    assert schedule.is_half_day is True
    assert schedule.close_at.isoformat() == "2026-11-27T18:00:00+00:00"
    assert [value.isoformat() for value in starts] == [
        "2026-11-27T14:30:00+00:00",
        "2026-11-27T15:30:00+00:00",
        "2026-11-27T16:30:00+00:00",
        "2026-11-27T17:30:00+00:00",
    ]


def test_regular_day_has_seven_60_minute_bars_and_one_daily_slot() -> None:
    session_date = date(2026, 7, 10)
    rules = UsSessionRules(version="us-session-test-v1")
    schedule = rules.schedule_for(session_date, {session_date})

    assert len(rules.expected_bar_starts(schedule, Timeframe.MINUTE_60)) == 7
    assert rules.expected_bar_count(schedule, Timeframe.DAY_1) == 1


def test_local_rules_never_invent_an_unverified_trading_day() -> None:
    rules = UsSessionRules(
        version="us-session-test-v1",
        half_day_closes={date(2026, 11, 27): time(13, 0)},
    )

    with pytest.raises(NotTradingDayError, match="verified OpenD trading days"):
        rules.schedule_for(date(2026, 12, 25), {date(2026, 12, 24)})
