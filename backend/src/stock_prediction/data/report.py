"""M2 operator reports and a fully offline end-to-end demonstration."""

from __future__ import annotations

from argparse import Namespace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from stock_prediction.contracts.ingestion import (
    IngestionPriority,
    IngestionStatus,
    IngestionTask,
)
from stock_prediction.contracts.market_data import MarketBar, MarketSession, Timeframe
from stock_prediction.data.adjustments import (
    AdjustedSeriesStore,
    AdjustmentEngine,
    AdjustmentMode,
)
from stock_prediction.data.catalog import DuckDbCatalog
from stock_prediction.data.quality import MarketBarQualityGate
from stock_prediction.data.raw_store import RawBatchStore
from stock_prediction.data.rebuild import MarketDataRebuilder
from stock_prediction.data.sessions import UsSessionRules
from stock_prediction.ingestion.service import IngestionCommitService
from stock_prediction.ingestion.state import IngestionStateStore


def run_m2_command(args: Namespace) -> dict[str, object]:
    command = str(args.command)
    if command == "m2-verify-raw":
        return verify_raw(Path(args.raw_root))
    if command == "m2-quality-report":
        return quality_report(Path(args.catalog))
    if command == "m2-task-status":
        return task_status(Path(args.state_db))
    if command == "m2-rebuild":
        return rebuild(Path(args.raw_root), Path(args.catalog))
    if command == "m2-offline-demo":
        return offline_demo(Path(args.work_root))
    if command == "m2-history-pilot":
        from stock_prediction.opend.history_pilot import run_bounded_history_pilot

        return run_bounded_history_pilot(
            symbol=str(args.symbol),
            timeframe=Timeframe(str(args.timeframe)),
            start=args.start,
            end=args.end,
        )
    raise ValueError("unsupported M2 command")


def verify_raw(root: Path) -> dict[str, object]:
    if not root.is_dir():
        raise FileNotFoundError("raw root does not exist")
    store = RawBatchStore(root)
    committed = store.committed_manifests()
    quarantined = store.quarantined_manifests()
    return {
        "status": "ok",
        "committed_batches": len(committed),
        "committed_rows": sum(item.row_count for item in committed),
        "quarantined_batches": len(quarantined),
        "dataset_revisions": sorted(
            {item.dataset_revision for item in committed}
        ),
    }


def quality_report(catalog_path: Path) -> dict[str, object]:
    if not catalog_path.is_file():
        raise FileNotFoundError("catalog does not exist")
    catalog = DuckDbCatalog(catalog_path)
    return {"status": "ok", "datasets": catalog.quality_summary()}


def task_status(state_path: Path) -> dict[str, object]:
    if not state_path.is_file():
        raise FileNotFoundError("state database does not exist")
    tasks = IngestionStateStore(state_path).list_tasks()
    return {
        "status": "ok",
        "tasks": [
            {
                "task_id": task.task_id,
                "task_status": task.status.value,
                "attempt": task.attempt,
                "state_version": task.state_version,
                "committed_batch_id": task.committed_batch_id,
                "committed_watermark": task.committed_watermark.isoformat()
                if task.committed_watermark is not None
                else None,
                "next_retry_at": task.next_retry_at.isoformat()
                if task.next_retry_at is not None
                else None,
            }
            for task in tasks
        ],
    }


def rebuild(raw_root: Path, catalog_path: Path) -> dict[str, object]:
    if not raw_root.is_dir():
        raise FileNotFoundError("raw root does not exist")
    store = RawBatchStore(raw_root)
    catalog = DuckDbCatalog(catalog_path)
    registered = catalog.rehydrate_from_raw(store)
    result = MarketDataRebuilder(catalog, store).rebuild()
    return {
        "status": "ok",
        "newly_registered_batches": registered,
        "rebuild_id": result.rebuild_id,
        "manifest_set_hash": result.manifest_set_hash,
        "raw_rows": result.raw_rows,
        "observation_revisions": result.observation_revisions,
        "canonical_rows": result.canonical_rows,
    }


def offline_demo(work_root: Path) -> dict[str, object]:
    if work_root.exists() and any(work_root.iterdir()):
        raise FileExistsError("offline demo root must be empty")
    work_root.mkdir(parents=True, exist_ok=True)
    raw_store = RawBatchStore(work_root / "raw")
    catalog = DuckDbCatalog(work_root / "catalog.duckdb")
    state = IngestionStateStore(work_root / "state.duckdb")
    session_date = date(2026, 7, 10)
    rules = UsSessionRules(version="offline-demo-session-v1")
    schedule = rules.schedule_for(session_date, {session_date})
    starts = rules.expected_bar_starts(schedule, Timeframe.MINUTE_60)
    batch_id = "offline-demo-batch-1"
    task_id = "offline-demo-task-1"
    ingested_at = datetime(2026, 7, 10, 20, 1, tzinfo=UTC)
    rows = [
        MarketBar(
            symbol="US.SPY",
            timeframe=Timeframe.MINUTE_60,
            session=MarketSession.REGULAR,
            session_date=session_date,
            bar_start=start,
            open=620.0 + index,
            high=621.0 + index,
            low=619.0 + index,
            close=620.5 + index,
            volume=1_000_000 + index,
            turnover=(620.5 + index) * (1_000_000 + index),
            source_available_at=min(
                start + timedelta(hours=1), schedule.close_at
            ),
            ingested_at=ingested_at,
            raw_batch_id=batch_id,
            schema_version="market-bar-v1",
            dataset_revision="offline-dataset-r1",
            revision_no=1,
        )
        for index, start in enumerate(starts)
    ]
    quality = MarketBarQualityGate(rules).evaluate(
        rows,
        schedules={session_date: schedule},
        checked_at=datetime(2026, 7, 10, 20, 2, tzinfo=UTC),
    )
    staged = raw_store.stage_market_bars(
        rows,
        task_id=task_id,
        batch_id=batch_id,
        request_start=session_date,
        request_end=session_date,
        request_fingerprint="d" * 64,
        opend_version="offline",
        sdk_version="offline",
        created_at=ingested_at,
    )
    initial = IngestionTask(
        task_id=task_id,
        dataset="market_bars",
        market="US",
        symbol="US.SPY",
        timeframe=Timeframe.MINUTE_60,
        requested_start=session_date,
        requested_end=session_date,
        priority=IngestionPriority.HIGH,
        attempt=0,
        status=IngestionStatus.PLANNED,
        created_at=ingested_at,
        updated_at=ingested_at,
    )
    state.create(initial)
    state.transition(task_id, IngestionStatus.CAPABILITY_CHECK, updated_at=ingested_at)
    state.transition(task_id, IngestionStatus.FETCHING, updated_at=ingested_at)
    state.transition(
        task_id,
        IngestionStatus.STAGED,
        updated_at=ingested_at,
        staged_batch_id=batch_id,
    )
    committed = IngestionCommitService(
        state,
        raw_store,
        catalog,
        clock=lambda: datetime(2026, 7, 10, 20, 2, tzinfo=UTC),
    ).commit_staged(
        task_id=task_id,
        staged=staged,
        quality_report=quality,
        next_page_token=None,
        committed_at=datetime(2026, 7, 10, 20, 2, tzinfo=UTC),
    )
    rebuilt = MarketDataRebuilder(catalog, raw_store).rebuild()
    adjusted = AdjustmentEngine().adjust(
        rows,
        [],
        mode=AdjustmentMode.POINT_IN_TIME,
        as_of=datetime(2026, 7, 10, 20, 2, tzinfo=UTC),
        adjustment_revision="offline-adjustment-r1",
    )
    AdjustedSeriesStore(catalog).register(adjusted)
    return {
        "status": "ok",
        "task_status": committed.status.value,
        "committed_batches": 1,
        "quality_score": quality.quality_score,
        "raw_rows": rebuilt.raw_rows,
        "canonical_rows": rebuilt.canonical_rows,
        "adjusted_rows": len(adjusted.rows),
        "rebuild_id": rebuilt.rebuild_id,
    }
