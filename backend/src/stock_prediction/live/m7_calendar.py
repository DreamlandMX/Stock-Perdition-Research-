"""Hash-bound OpenD calendar evidence for an M7 Seed100 promotion window."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
from typing import Protocol

from stock_prediction.live.config import LIVE_CALENDAR_VERSION, LivePaths
from stock_prediction.live.storage import LiveStorage
from stock_prediction.modeling.report import deterministic_hash, write_immutable_json
from stock_prediction.opend.futu_adapter import FutuQuoteContext
from stock_prediction.opend.ports import ReadOnlyQuoteContext


_REQUIRED_ELIGIBLE_SESSIONS = 99


class M7PromotionCalendarError(RuntimeError):
    """Authoritative future calendar evidence is unavailable or invalid."""


class _CalendarContextManager(ReadOnlyQuoteContext, Protocol):
    pass


@dataclass(frozen=True, slots=True)
class M7PromotionCalendarEvidence:
    eligible_sessions: tuple[date, ...]
    calendar_source: str
    source_hash: str
    artifact_path: Path
    observed_at: datetime


def resolve_seed100_promotion_calendar(
    paths: LivePaths,
    *,
    frozen_at: datetime,
    context_factory: Callable[[], _CalendarContextManager] = FutuQuoteContext,
    request_end: date | None = None,
    clock: Callable[[], datetime] | None = None,
) -> M7PromotionCalendarEvidence:
    """Fetch and persist enough authoritative sessions for 77+20 blind evidence."""

    if frozen_at.tzinfo is None or frozen_at.utcoffset() is None:
        raise ValueError("m7_promotion_frozen_at_timezone_required")
    frozen_date = frozen_at.astimezone(UTC).date()
    request_start = frozen_date + timedelta(days=1)
    requested_end = request_end or request_start + timedelta(days=550)
    if requested_end < request_start:
        raise ValueError("m7_promotion_calendar_range_invalid")

    try:
        with context_factory() as context:
            result = context.request_trading_days(
                market="US",
                start=request_start.isoformat(),
                end=requested_end.isoformat(),
                code=None,
            )
    except Exception as error:
        raise M7PromotionCalendarError("m7_promotion_calendar_unavailable") from error
    if not result.is_ok or not isinstance(result.value, list):
        raise M7PromotionCalendarError("m7_promotion_calendar_unavailable")

    normalized = _normalize_sessions(result.value, frozen_date=frozen_date)
    if len(normalized) < _REQUIRED_ELIGIBLE_SESSIONS:
        raise M7PromotionCalendarError("m7_promotion_calendar_incomplete")
    session_payload = [
        {"session_date": session.isoformat(), "day_type": day_type}
        for session, day_type in normalized
    ]
    source_hash = deterministic_hash(
        {"calendar_version": LIVE_CALENDAR_VERSION, "sessions": session_payload}
    )
    artifact_payload = {
        "schema_version": "m7-promotion-calendar-v1",
        "calendar_version": LIVE_CALENDAR_VERSION,
        "source_hash": source_hash,
        "request_start": request_start.isoformat(),
        "request_end": requested_end.isoformat(),
        "sessions": session_payload,
    }
    artifact_path = paths.models / "m7-calendars" / f"{source_hash}.json"
    _write_or_verify_immutable(artifact_path, artifact_payload)
    LiveStorage(paths).record_calendar(
        sessions=normalized,
        source_hash=source_hash,
        dataset_revision=paths.dataset_revision,
    )
    observed_at = (clock or (lambda: datetime.now(UTC)))()
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("m7_promotion_calendar_clock_timezone_required")
    return M7PromotionCalendarEvidence(
        eligible_sessions=tuple(session for session, _day_type in normalized),
        calendar_source=f"{LIVE_CALENDAR_VERSION}:{source_hash}",
        source_hash=source_hash,
        artifact_path=artifact_path,
        observed_at=observed_at,
    )


def _normalize_sessions(
    rows: list[dict[str, object]], *, frozen_date: date
) -> tuple[tuple[date, str], ...]:
    by_date: dict[date, str] = {}
    try:
        for row in rows:
            raw_date = row.get("time")
            raw_type = row.get("trade_date_type")
            if not isinstance(raw_date, str) or not isinstance(raw_type, str):
                raise ValueError
            session = date.fromisoformat(raw_date)
            day_type = raw_type.strip()
            if not day_type or session <= frozen_date:
                continue
            previous = by_date.setdefault(session, day_type)
            if previous != day_type:
                raise ValueError
    except (AttributeError, TypeError, ValueError) as error:
        raise M7PromotionCalendarError("m7_promotion_calendar_invalid") from error
    return tuple(sorted(by_date.items()))


def _write_or_verify_immutable(path: Path, payload: Mapping[str, object]) -> None:
    if not path.exists():
        write_immutable_json(path, payload)
        return
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M7PromotionCalendarError("m7_promotion_calendar_artifact_invalid") from error
    if existing != payload:
        raise M7PromotionCalendarError("m7_promotion_calendar_artifact_conflict")


__all__ = [
    "M7PromotionCalendarError",
    "M7PromotionCalendarEvidence",
    "resolve_seed100_promotion_calendar",
]
