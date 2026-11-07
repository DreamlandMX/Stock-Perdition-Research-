"""Versioned US regular-session expectations anchored to OpenD trading days."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

from stock_prediction.contracts.market_data import Timeframe

_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_NEW_YORK = ZoneInfo("America/New_York")


class SessionRuleError(ValueError):
    """A session cannot be established from verified inputs."""


class NotTradingDayError(SessionRuleError):
    """The requested date is absent from verified OpenD trading days."""


@dataclass(frozen=True, slots=True)
class SessionSchedule:
    session_date: date
    open_at: datetime
    close_at: datetime
    is_half_day: bool
    rule_version: str

    def __post_init__(self) -> None:
        if (
            self.open_at.tzinfo is None
            or self.close_at.tzinfo is None
            or self.open_at >= self.close_at
        ):
            raise SessionRuleError("session boundaries must be ordered and timezone-aware")


@dataclass(frozen=True, slots=True)
class UsSessionRules:
    """Local close-time metadata that never invents a trading date."""

    version: str
    regular_open: time = time(9, 30)
    regular_close: time = time(16, 0)
    half_day_closes: Mapping[date, time] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _VERSION.fullmatch(self.version):
            raise SessionRuleError("session rule version is invalid")
        if self.regular_open >= self.regular_close:
            raise SessionRuleError("regular session boundaries are invalid")
        if any(
            close <= self.regular_open or close >= self.regular_close
            for close in self.half_day_closes.values()
        ):
            raise SessionRuleError("half-day close must fall inside the regular session")

    def schedule_for(
        self, session_date: date, verified_trading_days: Collection[date]
    ) -> SessionSchedule:
        if session_date not in verified_trading_days:
            raise NotTradingDayError(
                "date is absent from verified OpenD trading days"
            )
        close_time = self.half_day_closes.get(session_date, self.regular_close)
        open_local = datetime.combine(session_date, self.regular_open, _NEW_YORK)
        close_local = datetime.combine(session_date, close_time, _NEW_YORK)
        return SessionSchedule(
            session_date=session_date,
            open_at=open_local.astimezone(UTC),
            close_at=close_local.astimezone(UTC),
            is_half_day=session_date in self.half_day_closes,
            rule_version=self.version,
        )

    def expected_bar_starts(
        self, schedule: SessionSchedule, timeframe: Timeframe
    ) -> tuple[datetime, ...]:
        if schedule.rule_version != self.version:
            raise SessionRuleError("schedule and rule versions do not match")
        if timeframe is Timeframe.DAY_1:
            return (schedule.open_at,)
        starts: list[datetime] = []
        cursor = schedule.open_at
        while cursor < schedule.close_at:
            starts.append(cursor)
            cursor += timedelta(hours=1)
        return tuple(starts)

    def expected_bar_count(
        self, schedule: SessionSchedule, timeframe: Timeframe
    ) -> int:
        return len(self.expected_bar_starts(schedule, timeframe))
