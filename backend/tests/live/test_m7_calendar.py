from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path

import pytest

from stock_prediction.live.config import LIVE_CALENDAR_VERSION, LivePaths
from stock_prediction.live.m7_calendar import (
    M7PromotionCalendarError,
    resolve_seed100_promotion_calendar,
)
from stock_prediction.live.storage import LiveStorage
from stock_prediction.opend.ports import SdkCallResult


class _CalendarContext:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.requests: list[tuple[str, str, str, str | None]] = []
        self.closed = False

    def __enter__(self) -> "_CalendarContext":
        return self

    def __exit__(self, *_args: object) -> bool:
        self.closed = True
        return False

    def request_trading_days(
        self,
        market: str,
        start: str,
        end: str,
        code: str | None = None,
    ) -> SdkCallResult[list[dict[str, object]]]:
        self.requests.append((market, start, end, code))
        return SdkCallResult.success(self.rows)


def _calendar_rows(start: date, count: int) -> list[dict[str, object]]:
    return [
        {
            "time": (start + timedelta(days=index)).isoformat(),
            "trade_date_type": "WHOLE",
        }
        for index in range(count)
    ]


def test_resolver_persists_hash_bound_opend_calendar_and_returns_future_sessions(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    context = _CalendarContext(_calendar_rows(date(2026, 8, 25), 120))
    frozen_at = datetime(2026, 8, 24, 20, tzinfo=UTC)

    evidence = resolve_seed100_promotion_calendar(
        paths,
        frozen_at=frozen_at,
        context_factory=lambda: context,
        request_end=date(2027, 4, 30),
    )

    assert len(evidence.eligible_sessions) == 120
    assert evidence.eligible_sessions[0] == date(2026, 8, 25)
    assert evidence.eligible_sessions[-1] == date(2026, 12, 22)
    assert evidence.calendar_source == f"{LIVE_CALENDAR_VERSION}:{evidence.source_hash}"
    assert len(evidence.source_hash) == 64
    assert context.requests == [("US", "2026-08-25", "2027-04-30", None)]
    assert context.closed is True
    artifact = paths.models / "m7-calendars" / f"{evidence.source_hash}.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["calendar_version"] == LIVE_CALENDAR_VERSION
    assert payload["source_hash"] == evidence.source_hash
    assert payload["sessions"][0] == {
        "session_date": "2026-08-25",
        "day_type": "WHOLE",
    }
    stored = LiveStorage(paths).trading_days(date(2026, 8, 25), date(2027, 4, 30))
    assert len(stored) == 120
    assert stored[date(2026, 8, 25)] == "WHOLE"


def test_resolver_fails_closed_when_opend_has_fewer_than_99_future_sessions(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    context = _CalendarContext(_calendar_rows(date(2026, 8, 25), 98))

    with pytest.raises(M7PromotionCalendarError, match="calendar_incomplete"):
        resolve_seed100_promotion_calendar(
            paths,
            frozen_at=datetime(2026, 8, 24, 20, tzinfo=UTC),
            context_factory=lambda: context,
            request_end=date(2027, 4, 30),
        )

    assert not (paths.models / "m7-calendars").exists()
    assert LiveStorage(paths).trading_days(
        date(2026, 8, 25), date(2027, 4, 30)
    ) == {}


def test_resolver_rejects_malformed_or_non_future_calendar_rows(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    rows = _calendar_rows(date(2026, 8, 25), 99)
    rows[-1] = {"time": "not-a-date", "trade_date_type": "WHOLE"}

    with pytest.raises(M7PromotionCalendarError, match="calendar_invalid"):
        resolve_seed100_promotion_calendar(
            paths,
            frozen_at=datetime(2026, 8, 24, 20, tzinfo=UTC),
            context_factory=lambda: _CalendarContext(rows),
            request_end=date(2027, 4, 30),
        )
