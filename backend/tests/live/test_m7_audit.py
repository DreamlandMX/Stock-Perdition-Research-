from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_audit import M7AuditError, M7AuditLog


def test_audit_log_is_hash_chained_and_supports_health_consumption(
    tmp_path: Path,
) -> None:
    log = M7AuditLog(LivePaths.from_workspace(tmp_path))
    first = log.append(
        event_type="training_started",
        recorded_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
        actor="scheduler",
        run_id="run-1",
        bundle_id="direction",
        package_sha256=None,
        detail={"trigger": "scheduled"},
    )
    second = log.append(
        event_type="blind_completed",
        recorded_at=datetime(2026, 12, 1, 12, tzinfo=UTC),
        actor="blind-worker",
        run_id="run-1",
        bundle_id="direction",
        package_sha256="a" * 64,
        detail={"status": "awaiting_manual_approval"},
    )

    events = log.events()
    assert events == (first, second)
    assert second.previous_event_hash == first.event_hash


def test_audit_log_rejects_tampering(tmp_path: Path) -> None:
    log = M7AuditLog(LivePaths.from_workspace(tmp_path))
    log.append(
        event_type="training_failed",
        recorded_at=datetime.now(UTC),
        actor="worker",
        run_id="run-1",
        bundle_id=None,
        package_sha256=None,
        detail={"reason": "timeout"},
    )
    log.path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(M7AuditError, match="invalid"):
        log.events()


def test_append_once_returns_existing_semantic_event_without_duplicate(
    tmp_path: Path,
) -> None:
    log = M7AuditLog(LivePaths.from_workspace(tmp_path))
    first = log.append_once(
        event_type="training_failed",
        recorded_at=datetime(2026, 8, 29, 16, 16, tzinfo=UTC),
        actor="model-training-worker",
        run_id="model-training-interrupted-1",
        bundle_id=None,
        package_sha256=None,
        detail={
            "reason": "model_training_worker_process_tree_lost",
            "stage": "candidate_search",
        },
    )

    repeated = log.append_once(
        event_type="training_failed",
        recorded_at=datetime(2026, 8, 29, 16, 17, tzinfo=UTC),
        actor="model-training-worker",
        run_id="model-training-interrupted-1",
        bundle_id=None,
        package_sha256=None,
        detail={
            "reason": "model_training_worker_process_tree_lost",
            "stage": "candidate_search",
        },
    )

    assert repeated == first
    assert log.events() == (first,)
