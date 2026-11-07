"""Single-connection OpenD preflight, bootstrap, and incremental refresh."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
import hashlib
import json
import shutil
import time as time_module
from types import TracebackType
from typing import Literal, Protocol, Self
from zoneinfo import ZoneInfo

import pandas as pd

from stock_prediction.contracts.market_data import Timeframe
from stock_prediction.ingestion.scheduler import EndpointRateLimiter
from stock_prediction.live.config import (
    LIVE_SCHEMA_VERSION,
    LivePaths,
)
from stock_prediction.live.storage import LiveStorage, task_id_for
from stock_prediction.live.universe_scope import (
    EXPANDED_LIVE_UNIVERSE,
    LiveUniverseSpec,
    resolve_live_universe,
)
from stock_prediction.opend.errors import OpenDErrorClass
from stock_prediction.opend.futu_adapter import FutuQuoteContext
from stock_prediction.opend.history import (
    HistoryReadError,
    HistoryReadRequest,
    OpenDHistoryReader,
    PagedHistoryContext,
)
from stock_prediction.opend.ports import (
    RawHistoryPage,
    ReadOnlyQuoteContext,
    SdkCallResult,
)

_NEW_YORK = ZoneInfo("America/New_York")
_DAILY_START = date(2018, 1, 1)
_MIN_FREE_BYTES = 2 * 1024**3


class LiveQuoteContext(ReadOnlyQuoteContext, PagedHistoryContext, Protocol):
    pass


@dataclass(frozen=True, slots=True)
class LivePreflightResult:
    ready: bool
    universe_version: str
    symbol_count: int
    daily_start: date
    intraday_start: date
    end: date
    opend_version: str
    history_quota_total: int
    history_quota_used: int
    history_quota_remaining: int
    new_history_symbols: int | None
    subscription_total: int
    subscription_used: int
    subscription_remaining: int
    disk_free_bytes: int
    existing_daily_symbols: int
    existing_intraday_symbols: int
    blockers: tuple[str, ...]
    trading_days: tuple[tuple[date, str], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "ready" if self.ready else "blocked",
            "universe_version": self.universe_version,
            "symbol_count": self.symbol_count,
            "daily_start": self.daily_start.isoformat(),
            "intraday_start": self.intraday_start.isoformat(),
            "end": self.end.isoformat(),
            "opend_version": self.opend_version,
            "history_quota": {
                "total": self.history_quota_total,
                "used": self.history_quota_used,
                "remaining": self.history_quota_remaining,
                "new_symbols_required": self.new_history_symbols,
            },
            "subscription_quota": {
                "total": self.subscription_total,
                "used": self.subscription_used,
                "remaining": self.subscription_remaining,
            },
            "disk_free_bytes": self.disk_free_bytes,
            "existing_watermarks": {
                "daily_symbols": self.existing_daily_symbols,
                "intraday_symbols": self.existing_intraday_symbols,
            },
            "blockers": list(self.blockers),
            "requests_history_kline": False,
        }


@dataclass(frozen=True, slots=True)
class ExpansionHistoryTask:
    symbol: str
    timeframe: Timeframe
    start: date
    end: date
    priority: str


class LiveOpenDService:
    """Execute live reads sequentially through one caller-visible quote context."""

    def __init__(
        self,
        paths: LivePaths,
        *,
        context_factory: Callable[[], LiveQuoteContext] = FutuQuoteContext,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time_module.monotonic,
        sleep: Callable[[float], None] = time_module.sleep,
        universe: LiveUniverseSpec | None = None,
    ) -> None:
        self.paths = paths
        self.storage = LiveStorage(paths)
        self.context_factory = context_factory
        self.clock = clock
        self.monotonic = monotonic
        self.sleep = sleep
        self.universe = universe or resolve_live_universe(paths)

    def preflight(self) -> LivePreflightResult:
        with self.context_factory() as context:
            result = self._preflight_with_context(context)
        self.storage.record_health(
            _health_payload(result, self.clock(), self.storage, self.paths)
        )
        return result

    def bootstrap(self) -> dict[str, object]:
        started = self.clock()
        with self.context_factory() as context:
            preflight = self._preflight_with_context(context, allow_full_quota=True)
            if not preflight.ready:
                raise RuntimeError("live_preflight_blocked")
            self._persist_calendar(preflight)
            limiter = _RateLimitedContext(
                context,
                limiter=EndpointRateLimiter(
                    {"request_history_kline": (30, 30.0)}, safety_ratio=0.8
                ),
                monotonic=self.monotonic,
                sleep=self.sleep,
            )
            summary = self._collect_ranges(
                limiter,
                ranges=(
                    (Timeframe.DAY_1, preflight.daily_start, preflight.end, "high"),
                    (
                        Timeframe.MINUTE_60,
                        preflight.intraday_start,
                        preflight.end,
                        "medium",
                    ),
                ),
            )
            rehab = self._collect_rehab(context)
            metadata = self._collect_instrument_metadata(context)
        finished = self.clock()
        coverage = self.storage.coverage()
        result: dict[str, object] = {
            "status": (
                "partial"
                if summary["failed"] or rehab["failed"] or metadata["failed"]
                else "ok"
            ),
            "mode": "bootstrap",
            "universe_version": self.universe.version,
            "symbols": len(self.universe.symbols),
            "tasks_committed": summary["committed"],
            "tasks_skipped": summary["skipped"],
            "tasks_failed": summary["failed"],
            "pages_committed": summary["pages"],
            "rows_committed": summary["rows"],
            "rehab_snapshots_committed": rehab["committed"],
            "rehab_snapshots_failed": rehab["failed"],
            "instrument_metadata_count": metadata["committed"],
            "instrument_metadata_failed": metadata["failed"],
            "coverage_rows": len(coverage),
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
        }
        self.storage.record_health(
            {
                **_health_payload(preflight, finished, self.storage, self.paths),
                "last_collection": result,
            }
        )
        return result

    def expand_universe(
        self,
        *,
        allow_zero_reserve: bool = False,
    ) -> dict[str, object]:
        """Backfill only active-universe symbol/timeframe pairs with no watermark."""

        if self.universe is not EXPANDED_LIVE_UNIVERSE:
            raise RuntimeError("universe_expansion_requires_staged_dataset")

        started = self.clock()
        with self.context_factory() as context:
            preflight = self._preflight_with_context(
                context,
                allow_full_quota=allow_zero_reserve,
            )
            if not preflight.ready:
                raise RuntimeError("live_preflight_blocked")
            self._persist_calendar(preflight)
            tasks = _missing_history_tasks(
                self.storage.coverage(),
                end=preflight.end,
                intraday_start=preflight.intraday_start,
                symbols=self.universe.symbols,
            )
            limiter = _RateLimitedContext(
                context,
                limiter=EndpointRateLimiter(
                    {"request_history_kline": (30, 30.0)}, safety_ratio=0.8
                ),
                monotonic=self.monotonic,
                sleep=self.sleep,
            )
            reader = OpenDHistoryReader(
                context_factory=lambda: limiter,
                clock=self.clock,
            )
            summary = {
                "committed": 0,
                "skipped": 0,
                "failed": 0,
                "pages": 0,
                "rows": 0,
            }
            for task in tasks:
                task_result = self._collect_task(
                    reader,
                    context=limiter,
                    symbol=task.symbol,
                    timeframe=task.timeframe,
                    start=task.start,
                    end=task.end,
                    priority=task.priority,
                )
                for key in summary:
                    summary[key] += task_result[key]
            expanded_symbols = tuple(sorted({task.symbol for task in tasks}))
            rehab = self._collect_rehab(context, symbols=expanded_symbols)
            metadata = self._collect_instrument_metadata(context)
        finished = self.clock()
        actual_cutoff, complete = self.storage.common_daily_cutoff(
            self.universe.symbols
        )
        status = (
            "ok"
            if summary["failed"] == 0
            and complete == len(self.universe.symbols)
            and actual_cutoff is not None
            and actual_cutoff >= preflight.end
            else "partial"
        )
        expansion_result: dict[str, object] = {
            "status": status,
            "mode": "universe_expansion",
            "universe_version": self.universe.version,
            "symbols": len(self.universe.symbols),
            "new_symbols_requested": len(expanded_symbols),
            "history_tasks_planned": len(tasks),
            "tasks_committed": summary["committed"],
            "tasks_skipped": summary["skipped"],
            "tasks_failed": summary["failed"],
            "pages_committed": summary["pages"],
            "rows_committed": summary["rows"],
            "rehab_snapshots_committed": rehab["committed"],
            "rehab_snapshots_failed": rehab["failed"],
            "instrument_metadata_count": metadata["committed"],
            "daily_symbols_complete": complete,
            "daily_symbols_total": len(self.universe.symbols),
            "data_cutoff": actual_cutoff.isoformat()
            if actual_cutoff is not None
            else None,
            "expected_cutoff": preflight.end.isoformat(),
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
        }
        self.storage.record_health(
            {
                **_health_payload(preflight, finished, self.storage, self.paths),
                "last_collection": expansion_result,
            }
        )
        return expansion_result

    def refresh(
        self,
        progress_callback: Callable[[int, int, str, Timeframe], None] | None = None,
    ) -> dict[str, object]:
        started = self.clock()
        with self.context_factory() as context:
            preflight = self._preflight_with_context(context, allow_full_quota=True)
            if any(
                blocker
                for blocker in preflight.blockers
                if blocker not in {"history_quota_reserve_exhausted"}
            ):
                raise RuntimeError("live_preflight_blocked")
            self._persist_calendar(preflight)
            limiter = _RateLimitedContext(
                context,
                limiter=EndpointRateLimiter(
                    {"request_history_kline": (30, 30.0)}, safety_ratio=0.8
                ),
                monotonic=self.monotonic,
                sleep=self.sleep,
            )
            ranges: list[tuple[Timeframe, date, date, str]] = []
            for timeframe, priority in (
                (Timeframe.DAY_1, "high"),
                (Timeframe.MINUTE_60, "medium"),
            ):
                starts = [
                    _watermark_date(self.storage.latest_watermark(symbol, timeframe))
                    for symbol in self.universe.symbols
                ]
                known = [value for value in starts if value is not None]
                if not known:
                    continue
                ranges.append((timeframe, min(known), preflight.end, priority))
            summary = self._collect_ranges(
                limiter,
                ranges=tuple(ranges),
                per_symbol_watermark=True,
                progress_callback=progress_callback,
            )
            rehab = self._collect_rehab(context)
            metadata = self._collect_instrument_metadata(context)
        finished = self.clock()
        result = {
            "status": "partial"
            if summary["failed"] or rehab["failed"] or metadata["failed"]
            else "ok",
            "mode": "refresh",
            "tasks_committed": summary["committed"],
            "tasks_skipped": summary["skipped"],
            "tasks_failed": summary["failed"],
            "pages_committed": summary["pages"],
            "rows_committed": summary["rows"],
            "rehab_snapshots_committed": rehab["committed"],
            "rehab_snapshots_failed": rehab["failed"],
            "instrument_metadata_count": metadata["committed"],
            "instrument_metadata_failed": metadata["failed"],
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
        }
        self.storage.record_health(
            {
                **_health_payload(preflight, finished, self.storage, self.paths),
                "last_collection": result,
            }
        )
        return result

    def _collect_instrument_metadata(self, context: LiveQuoteContext) -> dict[str, int]:
        """Capture current OpenD names without making them historical features."""

        result = context.get_market_snapshot(list(self.universe.symbols))
        if not result.is_ok or not isinstance(result.value, pd.DataFrame):
            return {"committed": 0, "failed": len(self.universe.symbols)}
        rows: list[dict[str, object]] = []
        for record in result.value.to_dict(orient="records"):
            symbol = str(record.get("code") or "").strip().upper()
            name = str(record.get("name") or "").strip()
            if symbol not in self.universe.symbols or not name:
                continue
            rows.append({"symbol": symbol, "name": name})
        if rows:
            self.storage.record_instrument_metadata(
                rows,
                observed_at=self.clock(),
            )
        return {
            "committed": len(rows),
            "failed": len(self.universe.symbols) - len(rows),
        }

    def _preflight_with_context(
        self,
        context: LiveQuoteContext,
        *,
        allow_full_quota: bool = False,
    ) -> LivePreflightResult:
        blockers: list[str] = []
        global_result = context.get_global_state()
        if not global_result.is_ok or not isinstance(global_result.value, dict):
            raise RuntimeError("opend_global_state_unavailable")
        global_state = global_result.value
        if global_state.get("qot_logined") is not True:
            blockers.append("opend_quote_not_logged_in")
        opend_version = str(global_state.get("server_ver") or "unknown")

        quota_result = context.get_history_kl_quota(True)
        used, remaining, details = _quota_tuple(quota_result)
        total = used + remaining
        detail_symbols = _quota_detail_symbols(details)

        subscription = context.query_subscription(True)
        sub_used, sub_remaining = _subscription_quota(subscription)

        today = self.clock().astimezone(_NEW_YORK).date()
        calendar_result = context.request_trading_days(
            market="US",
            start=_DAILY_START.isoformat(),
            end=today.isoformat(),
            code=None,
        )
        trading_days = _parse_trading_days(calendar_result)
        end = _last_completed_session(trading_days, self.clock())
        intraday_start = _subtract_two_years(end)

        coverage = self.storage.coverage()
        daily_symbols = {
            str(row["symbol"]) for row in coverage if row["timeframe"] == "1d"
        }
        intraday_symbols = {
            str(row["symbol"]) for row in coverage if row["timeframe"] == "60m"
        }
        known_symbols = detail_symbols | daily_symbols | intraday_symbols
        new_symbols = len(set(self.universe.symbols) - known_symbols)
        if (
            used > 0
            and not detail_symbols
            and len(daily_symbols) < len(self.universe.symbols)
        ):
            new_symbol_count: int | None = None
            blockers.append("history_quota_detail_unavailable")
        else:
            new_symbol_count = new_symbols
            if remaining < new_symbols:
                blockers.append("history_quota_insufficient")
            if not allow_full_quota and remaining == new_symbols and new_symbols > 0:
                blockers.append("history_quota_reserve_exhausted")

        disk_free = shutil.disk_usage(self.paths.root).free
        if disk_free < _MIN_FREE_BYTES:
            blockers.append("insufficient_disk_space")
        if len(self.universe.symbols) != len(set(self.universe.symbols)) or not all(
            symbol.startswith("US.") for symbol in self.universe.symbols
        ):
            blockers.append("universe_scope_mismatch")
        return LivePreflightResult(
            ready=not blockers,
            universe_version=self.universe.version,
            symbol_count=len(self.universe.symbols),
            daily_start=_DAILY_START,
            intraday_start=intraday_start,
            end=end,
            opend_version=opend_version,
            history_quota_total=total,
            history_quota_used=used,
            history_quota_remaining=remaining,
            new_history_symbols=new_symbol_count,
            subscription_total=sub_used + sub_remaining,
            subscription_used=sub_used,
            subscription_remaining=sub_remaining,
            disk_free_bytes=disk_free,
            existing_daily_symbols=len(daily_symbols),
            existing_intraday_symbols=len(intraday_symbols),
            blockers=tuple(blockers),
            trading_days=trading_days,
        )

    def _persist_calendar(self, preflight: LivePreflightResult) -> None:
        serialized = json.dumps(
            [(value.isoformat(), kind) for value, kind in preflight.trading_days],
            separators=(",", ":"),
        )
        source_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        self.storage.record_calendar(
            sessions=preflight.trading_days,
            source_hash=source_hash,
            dataset_revision=self.paths.dataset_revision,
        )
        directory = self.paths.raw / "calendar"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"opend-us-{source_hash[:16]}.json"
        if not target.exists():
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(serialized, encoding="utf-8")
            temporary.replace(target)

    def _collect_ranges(
        self,
        context: PagedHistoryContext,
        *,
        ranges: Sequence[tuple[Timeframe, date, date, str]],
        per_symbol_watermark: bool = False,
        progress_callback: Callable[[int, int, str, Timeframe], None] | None = None,
    ) -> dict[str, int]:
        counts = {"committed": 0, "skipped": 0, "failed": 0, "pages": 0, "rows": 0}
        total = len(ranges) * len(self.universe.symbols)
        completed = 0
        reader = OpenDHistoryReader(
            context_factory=lambda: context,
            clock=self.clock,
        )
        for timeframe, default_start, end, priority in ranges:
            for symbol in self.universe.symbols:
                start = default_start
                if per_symbol_watermark:
                    watermark = self.storage.latest_watermark(symbol, timeframe)
                    resolved = _watermark_date(watermark)
                    if resolved is None:
                        counts["skipped"] += 1
                        completed += 1
                        if progress_callback is not None:
                            progress_callback(completed, total, symbol, timeframe)
                        continue
                    start = resolved
                if start > end:
                    counts["skipped"] += 1
                    completed += 1
                    if progress_callback is not None:
                        progress_callback(completed, total, symbol, timeframe)
                    continue
                result = self._collect_task(
                    reader,
                    context=context,
                    symbol=symbol,
                    timeframe=timeframe,
                    start=start,
                    end=end,
                    priority=priority,
                )
                for key in counts:
                    counts[key] += result[key]
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, total, symbol, timeframe)
        return counts

    def _collect_rehab(
        self,
        context: LiveQuoteContext,
        *,
        symbols: Sequence[str] | None = None,
    ) -> dict[str, int]:
        """Capture one retrospective factor snapshot per fixed-universe symbol."""

        limiter = EndpointRateLimiter({"get_rehab": (60, 30.0)}, safety_ratio=0.8)
        counts = {"committed": 0, "failed": 0}
        for symbol in symbols or self.universe.symbols:
            while not limiter.allow("get_rehab", now=self.monotonic()):
                self.sleep(0.25)
            result = context.get_rehab([symbol])
            if not result.is_ok or result.value is None:
                counts["failed"] += 1
                continue
            observed_at = self.clock()
            try:
                self.storage.write_rehab_snapshot(
                    symbol=symbol,
                    frame=result.value,
                    observed_at=observed_at,
                    dataset_revision=self.paths.dataset_revision,
                )
            except (TypeError, ValueError, RuntimeError):
                counts["failed"] += 1
                continue
            counts["committed"] += 1
        return counts

    def _collect_task(
        self,
        reader: OpenDHistoryReader,
        *,
        context: PagedHistoryContext,
        symbol: str,
        timeframe: Timeframe,
        start: date,
        end: date,
        priority: str,
    ) -> dict[str, int]:
        counts = {"committed": 0, "skipped": 0, "failed": 0, "pages": 0, "rows": 0}
        task_id = task_id_for(symbol=symbol, timeframe=timeframe, start=start, end=end)
        state = self.storage.ensure_task(
            task_id=task_id,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            priority=priority,
            dataset_revision=self.paths.dataset_revision,
        )
        if state.status == "committed":
            counts["skipped"] = 1
            return counts
        request = HistoryReadRequest(
            code=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            max_count=1000,
            batch_id=task_id,
            schema_version=LIVE_SCHEMA_VERSION,
            dataset_revision=self.paths.dataset_revision,
        )
        restarted_from_origin = False
        for attempt in range(1, 4):
            current_state = self.storage.task(task_id)
            if current_state is None:
                raise RuntimeError("live_task_disappeared")
            self.storage.mark_fetching(task_id)
            try:
                prior_page_tokens = (
                    self.storage.committed_page_tokens(task_id)
                    if current_state.last_page_index > 0
                    else ()
                )
                pages = reader.iter_pages(
                    request,
                    context=context,
                    initial_page_token=current_state.next_page_token,
                    initial_page_index=current_state.last_page_index,
                    prior_watermark=current_state.watermark,
                    prior_page_tokens=prior_page_tokens,
                )
                for page in pages:
                    if not page.rows:
                        self.storage.complete_empty_task(task_id)
                        break
                    parts = self.storage.write_page(
                        task_id=task_id,
                        page_index=page.page_index,
                        rows=page.rows,
                    )
                    self.storage.commit_page(
                        task_id=task_id,
                        page_index=page.page_index,
                        next_page_token=page.next_page_token,
                        parts=parts,
                    )
                    counts["pages"] += 1
                    counts["rows"] += len(page.rows)
                counts["committed"] = 1
                return counts
            except HistoryReadError as error:
                if (
                    current_state.last_page_index > 0
                    and not restarted_from_origin
                    and error.error_class
                    in {
                        OpenDErrorClass.SDK_ERROR,
                        OpenDErrorClass.PARTIAL_RESPONSE,
                    }
                ):
                    self.storage.reset_pagination(task_id)
                    restarted_from_origin = True
                    self.sleep(float(2 ** (attempt - 1)))
                    continue
                retryable = error.error_class in {
                    OpenDErrorClass.CONNECTION_FAILED,
                    OpenDErrorClass.RATE_LIMITED,
                    OpenDErrorClass.PARTIAL_RESPONSE,
                    OpenDErrorClass.SDK_ERROR,
                }
                terminal = attempt == 3 or not retryable
                self.storage.mark_failed(
                    task_id,
                    status="blocked" if terminal else "retry",
                    error_code=error.error_class.value,
                )
                if terminal:
                    counts["failed"] = 1
                    return counts
                self.sleep(float(2 ** (attempt - 1)))
        counts["failed"] = 1
        return counts


class _RateLimitedContext:
    def __init__(
        self,
        context: LiveQuoteContext,
        *,
        limiter: EndpointRateLimiter,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self.context = context
        self.limiter = limiter
        self.monotonic = monotonic
        self.sleep = sleep

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        return False

    def request_history_kline_page(
        self,
        *,
        code: str,
        timeframe: str,
        start: str,
        end: str,
        max_count: int,
        page_token: str | None,
    ) -> SdkCallResult[RawHistoryPage]:
        while not self.limiter.allow("request_history_kline", now=self.monotonic()):
            self.sleep(0.25)
        return self.context.request_history_kline_page(
            code=code,
            timeframe=timeframe,
            start=start,
            end=end,
            max_count=max_count,
            page_token=page_token,
        )


def _missing_history_tasks(
    coverage: Sequence[dict[str, object]],
    *,
    end: date,
    intraday_start: date,
    symbols: Sequence[str] = EXPANDED_LIVE_UNIVERSE.symbols,
) -> tuple[ExpansionHistoryTask, ...]:
    available = {
        (str(row.get("symbol") or ""), str(row.get("timeframe") or "")): row
        for row in coverage
    }
    tasks: list[ExpansionHistoryTask] = []
    for symbol in symbols:
        for timeframe, start, priority in (
            (Timeframe.DAY_1, _DAILY_START, "high"),
            (Timeframe.MINUTE_60, intraday_start, "medium"),
        ):
            covered = available.get((symbol, timeframe.value))
            if covered is not None:
                minimum = covered.get("min_session_date")
                maximum = covered.get("max_session_date")
                if minimum is None and maximum is None:
                    continue
                try:
                    minimum_date = date.fromisoformat(str(minimum))
                    maximum_date = date.fromisoformat(str(maximum))
                except ValueError:
                    minimum_date = date.max
                    maximum_date = date.min
                if minimum_date <= start and maximum_date >= end:
                    continue
            tasks.append(
                ExpansionHistoryTask(
                    symbol=symbol,
                    timeframe=timeframe,
                    start=start,
                    end=end,
                    priority=priority,
                )
            )
    return tuple(tasks)


def _quota_tuple(
    result: SdkCallResult[tuple[int, int, list[object]]],
) -> tuple[int, int, list[object]]:
    if not result.is_ok or result.value is None:
        raise RuntimeError("history_quota_unavailable")
    used, remaining, details = result.value
    if (
        isinstance(used, bool)
        or isinstance(remaining, bool)
        or not isinstance(used, int)
        or not isinstance(remaining, int)
        or used < 0
        or remaining < 0
        or not isinstance(details, list)
    ):
        raise RuntimeError("history_quota_invalid")
    return used, remaining, details


def _quota_detail_symbols(details: Sequence[object]) -> set[str]:
    symbols: set[str] = set()
    for item in details:
        value: object | None = None
        if isinstance(item, dict):
            value = item.get("code") or item.get("stock_code")
        else:
            value = getattr(item, "code", None) or getattr(item, "stock_code", None)
        if isinstance(value, str) and value.startswith("US."):
            symbols.add(value)
    return symbols


def _subscription_quota(
    result: SdkCallResult[dict[str, object]],
) -> tuple[int, int]:
    if not result.is_ok or not isinstance(result.value, dict):
        raise RuntimeError("subscription_quota_unavailable")
    used = result.value.get("total_used")
    remaining = result.value.get("remain")
    if (
        isinstance(used, bool)
        or isinstance(remaining, bool)
        or not isinstance(used, int)
        or not isinstance(remaining, int)
    ):
        raise RuntimeError("subscription_quota_invalid")
    return used, remaining


def _parse_trading_days(
    result: SdkCallResult[list[dict[str, object]]],
) -> tuple[tuple[date, str], ...]:
    if not result.is_ok or not isinstance(result.value, list):
        raise RuntimeError("trading_calendar_unavailable")
    parsed: list[tuple[date, str]] = []
    for row in result.value:
        raw_date = row.get("time")
        raw_type = row.get("trade_date_type")
        if not isinstance(raw_date, str) or not isinstance(raw_type, str):
            raise RuntimeError("trading_calendar_invalid")
        parsed.append((date.fromisoformat(raw_date), raw_type))
    if not parsed:
        raise RuntimeError("trading_calendar_empty")
    unique = sorted(set(parsed), key=lambda item: item[0])
    return tuple(unique)


def _last_completed_session(
    sessions: Sequence[tuple[date, str]], now: datetime
) -> date:
    local = now.astimezone(_NEW_YORK)
    completed = [
        session
        for session, _kind in sessions
        if session < local.date()
        or (session == local.date() and local.time() >= time(16, 10))
    ]
    if not completed:
        raise RuntimeError("no_completed_us_session")
    return max(completed)


def _subtract_two_years(value: date) -> date:
    try:
        return value.replace(year=value.year - 2)
    except ValueError:
        return value.replace(year=value.year - 2, day=28)


def _watermark_date(value: datetime | None) -> date | None:
    return value.astimezone(_NEW_YORK).date() if value is not None else None


def _health_payload(
    preflight: LivePreflightResult,
    observed_at: datetime,
    storage: LiveStorage,
    paths: LivePaths,
) -> dict[str, object]:
    universe = resolve_live_universe(paths)
    actual_cutoff, complete_symbols = storage.common_daily_cutoff(universe.symbols)
    previous = storage.latest_health() or {}
    payload: dict[str, object] = {
        "connection_status": "connected",
        "observed_at": observed_at.isoformat(),
        "opend_version": preflight.opend_version,
        "history_quota_total": preflight.history_quota_total,
        "history_quota_used": preflight.history_quota_used,
        "history_quota_remaining": preflight.history_quota_remaining,
        "subscription_total": preflight.subscription_total,
        "subscription_used": preflight.subscription_used,
        "subscription_remaining": preflight.subscription_remaining,
        "data_cutoff": actual_cutoff.isoformat() if actual_cutoff is not None else None,
        "expected_cutoff": preflight.end.isoformat(),
        "snapshot_cutoff": _latest_snapshot_cutoff(paths),
        "lag_sessions": (
            sum(
                actual_cutoff < session <= preflight.end
                for session, _kind in preflight.trading_days
            )
            if actual_cutoff is not None
            else None
        ),
        "daily_symbols_complete": complete_symbols,
        "daily_symbols_total": len(universe.symbols),
        "blockers": list(preflight.blockers),
    }
    for key in ("last_pipeline_success_at", "last_operation_id"):
        if previous.get(key) is not None:
            payload[key] = previous[key]
    return payload


def _latest_snapshot_cutoff(paths: LivePaths) -> str | None:
    candidates: list[tuple[datetime, str]] = []
    for manifest_path in paths.research.glob("*/manifest.json"):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            generated_at = datetime.fromisoformat(
                str(payload["generated_at"]).replace("Z", "+00:00")
            )
            as_of = datetime.fromisoformat(str(payload["as_of"]).replace("Z", "+00:00"))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        candidates.append((generated_at, as_of.date().isoformat()))
    latest = max(candidates, default=None)
    return latest[1] if latest is not None else None


__all__ = ["LiveOpenDService", "LivePreflightResult", "LiveQuoteContext"]
