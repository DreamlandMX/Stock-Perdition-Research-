from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from stock_prediction.contracts.ingestion import (
    IngestionPriority,
    IngestionStatus,
    IngestionTask,
)
from stock_prediction.contracts.market_data import Timeframe
from stock_prediction.ingestion.state import (
    IngestionStateConflict,
    IngestionStateStore,
    InvalidIngestionTransition,
)


NOW = datetime(2026, 7, 10, 20, 0, tzinfo=UTC)


def task() -> IngestionTask:
    return IngestionTask(
        task_id="task-spy-1d",
        dataset="market_bars",
        market="US",
        symbol="US.SPY",
        timeframe=Timeframe.DAY_1,
        requested_start=date(2026, 7, 1),
        requested_end=date(2026, 7, 10),
        priority=IngestionPriority.HIGH,
        attempt=0,
        status=IngestionStatus.PLANNED,
        created_at=NOW,
        updated_at=NOW,
    )


def test_state_store_is_persistent_idempotent_and_versioned(tmp_path: Path) -> None:
    path = tmp_path / "state.duckdb"
    store = IngestionStateStore(path)

    assert store.create(task()) is True
    assert store.create(task()) is False
    capability = store.transition(
        "task-spy-1d",
        IngestionStatus.CAPABILITY_CHECK,
        updated_at=NOW + timedelta(seconds=1),
    )

    assert capability.state_version == 1
    reopened = IngestionStateStore(path)
    assert reopened.get("task-spy-1d") == capability

    conflicting = task().model_copy(update={"priority": IngestionPriority.LOW})
    with pytest.raises(IngestionStateConflict, match="task ID"):
        store.create(conflicting)


def test_legal_state_path_increments_attempt_and_commits_evidence(tmp_path: Path) -> None:
    store = IngestionStateStore(tmp_path / "state.duckdb")
    store.create(task())

    store.transition(
        "task-spy-1d", IngestionStatus.CAPABILITY_CHECK, updated_at=NOW
    )
    fetching = store.transition(
        "task-spy-1d", IngestionStatus.FETCHING, updated_at=NOW
    )
    staged = store.transition(
        "task-spy-1d",
        IngestionStatus.STAGED,
        updated_at=NOW,
        staged_batch_id="batch-spy-1d",
    )
    committed = store.transition(
        "task-spy-1d",
        IngestionStatus.COMMITTED,
        updated_at=NOW,
        committed_batch_id="batch-spy-1d",
        committed_watermark="2026-07-10T20:00:00Z",
    )

    assert fetching.attempt == 1
    assert staged.staged_batch_id == "batch-spy-1d"
    assert committed.staged_batch_id is None
    assert committed.committed_batch_id == "batch-spy-1d"
    assert committed.committed_watermark is not None
    assert committed.committed_watermark.tzinfo is UTC
    assert [event.to_status for event in store.events("task-spy-1d")] == [
        IngestionStatus.CAPABILITY_CHECK,
        IngestionStatus.FETCHING,
        IngestionStatus.STAGED,
        IngestionStatus.COMMITTED,
    ]


def test_illegal_transition_does_not_change_persisted_task(tmp_path: Path) -> None:
    store = IngestionStateStore(tmp_path / "state.duckdb")
    original = task()
    store.create(original)

    with pytest.raises(InvalidIngestionTransition, match="planned.*committed"):
        store.transition(
            original.task_id,
            IngestionStatus.COMMITTED,
            updated_at=NOW,
            committed_batch_id="batch-1",
            committed_watermark=NOW,
        )

    assert store.get(original.task_id) == original
    assert store.events(original.task_id) == []


def test_retry_requires_error_and_due_time_then_can_resume(tmp_path: Path) -> None:
    store = IngestionStateStore(tmp_path / "state.duckdb")
    store.create(task())
    store.transition("task-spy-1d", IngestionStatus.CAPABILITY_CHECK, updated_at=NOW)
    store.transition("task-spy-1d", IngestionStatus.FETCHING, updated_at=NOW)

    retrying = store.transition(
        "task-spy-1d",
        IngestionStatus.RETRYING,
        updated_at=NOW,
        error_class="rate_limited",
        next_retry_at=NOW + timedelta(seconds=30),
    )
    resumed = store.transition(
        "task-spy-1d",
        IngestionStatus.FETCHING,
        updated_at=NOW + timedelta(seconds=30),
    )

    assert retrying.error_class == "rate_limited"
    assert resumed.attempt == 2
    assert resumed.error_class is None
    assert resumed.next_retry_at is None


def test_stale_lease_can_be_reclaimed_but_live_lease_cannot(tmp_path: Path) -> None:
    store = IngestionStateStore(tmp_path / "state.duckdb")
    store.create(task())

    claimed = store.claim(
        "task-spy-1d", owner="worker-a", now=NOW, ttl=timedelta(seconds=30)
    )
    assert claimed.lease_owner == "worker-a"
    with pytest.raises(IngestionStateConflict, match="leased"):
        store.claim(
            "task-spy-1d",
            owner="worker-b",
            now=NOW + timedelta(seconds=10),
            ttl=timedelta(seconds=30),
        )

    reclaimed = store.claim(
        "task-spy-1d",
        owner="worker-b",
        now=NOW + timedelta(seconds=31),
        ttl=timedelta(seconds=30),
    )
    assert reclaimed.lease_owner == "worker-b"
