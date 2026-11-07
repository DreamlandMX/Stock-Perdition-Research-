"""Durable, explicit option-only refreshes executed by the local scheduler."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
import json
import re
from uuid import uuid4

from filelock import FileLock, Timeout

from stock_prediction.contracts.options import OPTION_SYMBOLS, OptionModel
from stock_prediction.contracts.operations import EodWorkerStatus, EodWorkerState
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.live.config import LivePaths
from stock_prediction.live.locking import LiveWriterLease, _atomic_json
from stock_prediction.ingestion.scheduler import EndpointRateLimiter
from stock_prediction.opend.futu_adapter import FutuQuoteContext
from stock_prediction.options.collector import run_option_collection
from stock_prediction.options.store import OptionSnapshotStore
from typing import Literal

ItemStatus = Literal["queued", "running", "completed", "failed"]
JobStatus = Literal["queued", "running", "completed", "partial", "failed"]
_REFRESH_LIMITER = EndpointRateLimiter(
    {"chain": (10, 30), "snapshot": (60, 30), "expiration": (10, 30)}
)


class OptionRefreshRequest(OptionModel):
    symbol: str | None = None


class OptionRefreshItem(OptionModel):
    symbol: str
    status: ItemStatus = "queued"
    reason: str | None = None
    snapshot_id: str | None = None
    captured_at: UtcDatetime | None = None
    market_at: UtcDatetime | None = None


class OptionRefreshOperation(OptionModel):
    operation_id: str
    status: JobStatus = "queued"
    created_at: UtcDatetime
    updated_at: UtcDatetime
    items: tuple[OptionRefreshItem, ...]


class RefreshBusyError(RuntimeError):
    pass


def option_workspace(paths: LivePaths) -> Path:
    root = paths.root.resolve()
    return root.parents[2] if root.parent.name == "live-datasets" else root.parents[1]


class OptionRefreshStore:
    def __init__(self, options_root: Path):
        self.root = options_root / "_refresh"

    def heartbeat(self, now: datetime) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.root / "worker.json", {"heartbeat_at": now.isoformat()})

    def worker_status(self, now: datetime) -> EodWorkerStatus:
        try:
            at = datetime.fromisoformat(
                json.loads((self.root / "worker.json").read_bytes())["heartbeat_at"]
            )
        except (OSError, ValueError, KeyError, TypeError):
            at = None
        ready = at is not None and 0 <= (now - at).total_seconds() <= 15
        return EodWorkerStatus(
            state=EodWorkerState.READY if ready else EodWorkerState.OFFLINE,
            last_heartbeat=at,
            stale_after_seconds=15,
            detail="option_refresh_worker_ready"
            if ready
            else "option_refresh_worker_offline",
        )

    def get(self, identity: str) -> OptionRefreshOperation | None:
        if not re.fullmatch(r"option-refresh-[a-f0-9]{32}", identity):
            return None
        path = self.root / f"{identity}.json"
        return (
            OptionRefreshOperation.model_validate_json(path.read_bytes())
            if path.exists()
            else None
        )

    def latest(self) -> OptionRefreshOperation | None:
        pointer = self.root / "latest.json"
        if not pointer.exists():
            return None
        return self.get(json.loads(pointer.read_bytes())["operation_id"])

    def save(self, operation: OptionRefreshOperation) -> None:
        _atomic_json(
            self.root / f"{operation.operation_id}.json",
            operation.model_dump(mode="json"),
        )

    def submit(self, symbol: str | None, now: datetime) -> OptionRefreshOperation:
        symbols = (symbol,) if symbol is not None else OPTION_SYMBOLS
        if any(s not in OPTION_SYMBOLS for s in symbols):
            raise ValueError("symbol_outside_option_scope")
        self.root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.root / ".queue.lock"), timeout=2):
            active = self.latest()
            if active and active.status in ("queued", "running"):
                if set(symbols).issubset(i.symbol for i in active.items):
                    return active
                raise RefreshBusyError("option_refresh_busy")
            operation = OptionRefreshOperation(
                operation_id="option-refresh-" + uuid4().hex,
                created_at=now,
                updated_at=now,
                items=tuple(OptionRefreshItem(symbol=s) for s in symbols),
            )
            self.save(operation)
            _atomic_json(
                self.root / "latest.json", {"operation_id": operation.operation_id}
            )
            return operation


def run_option_refresh_tick(
    paths: LivePaths,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    options = OptionSnapshotStore(option_workspace(paths) / "var/options")
    jobs = OptionRefreshStore(options.root)
    jobs.heartbeat(clock())
    try:
        with FileLock(str(jobs.root / ".worker.lock"), timeout=0):
            operation = jobs.latest()
            if operation is None or operation.status not in ("queued", "running"):
                return
            # A running item with no execution lock owner survived a worker interruption.
            items = [
                i.model_copy(
                    update={"status": "failed", "reason": "worker_interrupted"}
                )
                if i.status == "running"
                else i
                for i in operation.items
            ]
            index = next((n for n, i in enumerate(items) if i.status == "queued"), None)
            if index is not None:
                try:
                    with LiveWriterLease(paths, task_id="option-refresh", timeout=0):
                        items[index] = items[index].model_copy(
                            update={"status": "running"}
                        )
                        jobs.save(
                            operation.model_copy(
                                update={
                                    "items": tuple(items),
                                    "status": "running",
                                    "updated_at": clock(),
                                }
                            )
                        )
                        try:
                            started = clock()
                            run = run_option_collection(
                                options,
                                symbols=(items[index].symbol,),
                                context_factory=lambda: FutuQuoteContext(
                                    connect_timeout_seconds=5
                                ),
                                clock=clock,
                                timeout_seconds=300,
                                intraday=True,
                                limiter=_REFRESH_LIMITER,
                                refresh_monitor=not any(
                                    i.status == "queued" for i in items[index + 1 :]
                                ),
                            )
                            snapshot = options.load(items[index].symbol)
                            success = (
                                run.status == "completed"
                                and snapshot is not None
                                and snapshot.complete
                                and snapshot.captured_at >= started
                            )
                            items[index] = items[index].model_copy(
                                update={
                                    "status": "completed" if success else "failed",
                                    "reason": None
                                    if success
                                    else (run.reason or "incomplete_snapshots"),
                                    "snapshot_id": snapshot.snapshot_id
                                    if success and snapshot
                                    else None,
                                    "captured_at": snapshot.captured_at
                                    if success and snapshot
                                    else None,
                                    "market_at": snapshot.underlying_at
                                    if success and snapshot
                                    else None,
                                }
                            )
                        except Exception:
                            items[index] = items[index].model_copy(
                                update={
                                    "status": "failed",
                                    "reason": "collection_failed",
                                }
                            )
                except Timeout:
                    return
            done = all(i.status in ("completed", "failed") for i in items)
            successes = sum(i.status == "completed" for i in items)
            status: JobStatus = (
                "running"
                if not done
                else "completed"
                if successes == len(items)
                else "partial"
                if successes
                else "failed"
            )
            jobs.save(
                operation.model_copy(
                    update={
                        "items": tuple(items),
                        "status": status,
                        "updated_at": clock(),
                    }
                )
            )
    except Timeout:
        return
