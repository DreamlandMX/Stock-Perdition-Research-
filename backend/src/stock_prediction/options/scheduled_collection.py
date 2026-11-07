"""Daily forward collection sharing the live writer lease; half days are skipped."""

from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, date, datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

from filelock import Timeout

from stock_prediction.data.sessions import NotTradingDayError, UsSessionRules
from stock_prediction.contracts.options import AUTOMATIC_OPTION_SYMBOLS
from stock_prediction.live.config import LivePaths
from stock_prediction.live.locking import LiveWriterLease
from stock_prediction.opend.errors import classify_sdk_exception
from stock_prediction.opend.futu_adapter import FutuQuoteContext
from stock_prediction.options.collector import run_option_collection
from stock_prediction.options.monitor import (
    OptionCollectionRun,
    save_option_collection_run,
)
from stock_prediction.options.store import OptionSnapshotStore

NEW_YORK = ZoneInfo("America/New_York")


def run_scheduled_option_collection(
    paths: LivePaths,
    *,
    context_factory: Callable[[], Any] = FutuQuoteContext,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> OptionCollectionRun:
    started = clock()
    if started.tzinfo is None or started.utcoffset() is None:
        raise ValueError("option collection clock requires timezone")
    local = started.astimezone(NEW_YORK)
    day = local.date()
    planned = local.replace(hour=15, minute=40, second=0, microsecond=0).astimezone(UTC)
    live_root = paths.root.resolve()
    workspace = (
        live_root.parents[2]
        if live_root.parent.name == "live-datasets"
        else live_root.parents[1]
    )
    store = OptionSnapshotStore(workspace / "var" / "options")
    status: Literal["failed", "skipped"] = "failed"
    reason = "calendar_unavailable"
    half_day: bool | None = None
    collecting = False
    try:
        with LiveWriterLease(paths, task_id="option-collection", timeout=0):
            with context_factory() as context:
                result = context.request_trading_days(
                    market="US", start=day.isoformat(), end=day.isoformat(), code=None
                )
                if not result.is_ok:
                    reason = (
                        result.error.error_class.value
                        if result.error
                        else "calendar_unavailable"
                    )
                elif not isinstance(result.value, list):
                    reason = "calendar_invalid"
                else:
                    sessions: dict[date, str] = {}
                    for row in result.value:
                        session = date.fromisoformat(row["time"])
                        kind = row["trade_date_type"]
                        if session != day or kind not in (
                            "WHOLE",
                            "MORNING",
                            "AFTERNOON",
                        ):
                            raise ValueError("invalid verified trading calendar")
                        if session in sessions and sessions[session] != kind:
                            raise ValueError("conflicting verified trading calendar")
                        sessions[session] = kind
                    rules = UsSessionRules(
                        version="option-us-session-v1",
                        half_day_closes={
                            session: time(13)
                            for session, kind in sessions.items()
                            if kind != "WHOLE"
                        },
                    )
                    schedule = rules.schedule_for(day, sessions)
                    half_day = schedule.is_half_day
                    actual = clock()
                    if half_day:
                        status, reason = "skipped", "half_day_skipped"
                    elif actual < schedule.open_at:
                        status, reason = "skipped", "before_market_open"
                    elif actual >= schedule.close_at:
                        status, reason = "skipped", "after_market_close"
                    else:
                        collecting = True
                        return run_option_collection(
                            store,
                            symbols=AUTOMATIC_OPTION_SYMBOLS,
                            context_factory=lambda: nullcontext(context),
                            clock=clock,
                            scheduled_at=planned,
                            is_half_day=half_day,
                            started_at=started,
                        )
    except NotTradingDayError:
        status, reason = "skipped", "not_trading_day"
        half_day = False
    except Timeout as error:
        if error.lock_file != str(paths.writer_lock):
            raise
        status, reason = "skipped", "opend_busy"
    except (ValueError, KeyError, TypeError):
        if collecting:
            raise
        reason = "calendar_invalid"
    except Exception as error:
        if collecting:
            raise
        reason = classify_sdk_exception(error).error_class.value
    run = OptionCollectionRun(
        scheduled_at=planned,
        started_at=started,
        ended_at=clock(),
        trading_date=day,
        is_half_day=half_day,
        automatic=True,
        status=status,
        reason=reason,
        contracts=dict.fromkeys(AUTOMATIC_OPTION_SYMBOLS, 0),
        complete=dict.fromkeys(AUTOMATIC_OPTION_SYMBOLS, False),
    )
    save_option_collection_run(run, store)
    return run
