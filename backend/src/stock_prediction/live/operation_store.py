"""Atomic local queue for the single EOD update operation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from filelock import FileLock

from stock_prediction.contracts.operations import (
    EodWorkerState,
    EodWorkerStatus,
    OperationPhase,
    UpdateOperation,
    UpdateOperationStage,
    UpdateOperationStatus,
)
from stock_prediction.live.config import LivePaths


_ACTIVE = {
    UpdateOperationStatus.QUEUED,
    UpdateOperationStatus.RUNNING,
    UpdateOperationStatus.CANCEL_REQUESTED,
}
_SUCCEEDED = {
    UpdateOperationStatus.SUCCEEDED_DATA_ONLY,
    UpdateOperationStatus.SUCCEEDED_FORECAST_READY,
}
_WORKER_STALE_AFTER_SECONDS = 10


class OperationCancellationConflictError(RuntimeError):
    """A completed or failed operation cannot be cancelled."""


class EodOperationStore:
    def __init__(self, paths: LivePaths) -> None:
        self.paths = paths
        self.directory = paths.root / "operations"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._mutation_lock = FileLock(str(self.directory / ".queue.lock"))

    @property
    def execution_lock_path(self) -> Path:
        return self.paths.writer_lock

    @property
    def worker_status_path(self) -> Path:
        return self.directory / "WORKER.json"

    def heartbeat(self, *, now: datetime | None = None) -> EodWorkerStatus:
        at = (now or datetime.now(UTC)).astimezone(UTC)
        status = EodWorkerStatus(
            state=EodWorkerState.READY,
            last_heartbeat=at,
            stale_after_seconds=_WORKER_STALE_AFTER_SECONDS,
            detail="eod_worker_heartbeat_current",
        )
        _atomic_json(self.worker_status_path, status.model_dump(mode="json"))
        return status

    def mark_worker_offline(self, *, now: datetime | None = None) -> EodWorkerStatus:
        at = (now or datetime.now(UTC)).astimezone(UTC)
        status = EodWorkerStatus(
            state=EodWorkerState.OFFLINE,
            last_heartbeat=at,
            stale_after_seconds=_WORKER_STALE_AFTER_SECONDS,
            detail="eod_worker_stopped",
        )
        _atomic_json(self.worker_status_path, status.model_dump(mode="json"))
        return status

    def worker_status(self, *, now: datetime | None = None) -> EodWorkerStatus:
        checked_at = (now or datetime.now(UTC)).astimezone(UTC)
        if not self.worker_status_path.is_file():
            return EodWorkerStatus(
                state=EodWorkerState.OFFLINE,
                stale_after_seconds=_WORKER_STALE_AFTER_SECONDS,
                detail="eod_worker_heartbeat_missing",
            )
        try:
            stored = EodWorkerStatus.model_validate_json(
                self.worker_status_path.read_bytes(),
                strict=True,
            )
        except (OSError, ValueError):
            return EodWorkerStatus(
                state=EodWorkerState.OFFLINE,
                stale_after_seconds=_WORKER_STALE_AFTER_SECONDS,
                detail="eod_worker_heartbeat_invalid",
            )
        if stored.state is EodWorkerState.OFFLINE or stored.last_heartbeat is None:
            return stored
        stale_after = timedelta(seconds=stored.stale_after_seconds)
        if checked_at - stored.last_heartbeat <= stale_after:
            return stored.model_copy(
                update={
                    "state": EodWorkerState.READY,
                    "detail": "eod_worker_heartbeat_current",
                }
            )
        return stored.model_copy(
            update={
                "state": EodWorkerState.STALE,
                "detail": "eod_worker_heartbeat_stale",
            }
        )

    def submit(
        self,
        *,
        completed_not_before: datetime | None = None,
    ) -> tuple[UpdateOperation, bool]:
        """Create one queued operation, or reuse active/recent successful work."""

        with self._mutation_lock:
            latest = self.latest()
            if latest is not None and latest.status in _ACTIVE:
                return latest, False
            if (
                completed_not_before is not None
                and latest is not None
                and latest.status in _SUCCEEDED
                and latest.finished_at is not None
                and latest.finished_at >= completed_not_before.astimezone(UTC)
            ):
                return latest, False
            now = datetime.now(UTC)
            operation = UpdateOperation(
                operation_id=f"eod-{now:%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}",
                status=UpdateOperationStatus.QUEUED,
                progress_percent=0,
                created_at=now,
                updated_at=now,
            )
            self.save(operation)
            return operation, True

    def latest(self) -> UpdateOperation | None:
        pointer = self.directory / "LATEST.json"
        if not pointer.is_file():
            return None
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            operation_id = str(payload["operation_id"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return self.get(operation_id)

    def get(self, operation_id: str) -> UpdateOperation | None:
        if not operation_id or Path(operation_id).name != operation_id:
            return None
        path = self.directory / f"{operation_id}.json"
        if not path.is_file():
            return None
        return UpdateOperation.model_validate_json(path.read_bytes(), strict=True)

    def request_cancel(self, operation_id: str) -> UpdateOperation:
        with self._mutation_lock:
            current = self.get(operation_id)
            if current is None:
                raise KeyError(operation_id)
            if current.status in {
                UpdateOperationStatus.CANCEL_REQUESTED,
                UpdateOperationStatus.CANCELLED,
            }:
                return current
            if current.status not in {
                UpdateOperationStatus.QUEUED,
                UpdateOperationStatus.RUNNING,
            }:
                raise OperationCancellationConflictError("operation_not_cancellable")
            now = datetime.now(UTC)
            payload = current.model_dump(mode="python")
            payload.update(
                {
                    "status": (
                        UpdateOperationStatus.CANCELLED
                        if current.status is UpdateOperationStatus.QUEUED
                        else UpdateOperationStatus.CANCEL_REQUESTED
                    ),
                    "cancel_requested_at": now,
                    "cancelled_at": (
                        now if current.status is UpdateOperationStatus.QUEUED else None
                    ),
                    "finished_at": (
                        now
                        if current.status is UpdateOperationStatus.QUEUED
                        else current.finished_at
                    ),
                    "updated_at": now,
                    "active_trial_id": None,
                }
            )
            return self.save(UpdateOperation.model_validate(payload, strict=True))

    def completed_for_hash(
        self, data_hash: str, *, exclude_operation_id: str
    ) -> UpdateOperation | None:
        matches: list[UpdateOperation] = []
        for path in self.directory.glob("eod-*.json"):
            if path.stem == exclude_operation_id:
                continue
            try:
                operation = UpdateOperation.model_validate_json(
                    path.read_bytes(), strict=True
                )
            except (OSError, ValueError):
                continue
            if operation.data_hash == data_hash and operation.status in {
                UpdateOperationStatus.SUCCEEDED_DATA_ONLY,
                UpdateOperationStatus.SUCCEEDED_FORECAST_READY,
            }:
                matches.append(operation)
        return (
            max(matches, key=lambda item: item.finished_at or item.updated_at)
            if matches
            else None
        )

    def save(self, operation: UpdateOperation) -> UpdateOperation:
        checked = UpdateOperation.model_validate(operation, strict=True)
        path = self.directory / f"{checked.operation_id}.json"
        _atomic_json(path, checked.model_dump(mode="json"))
        _atomic_json(
            self.directory / "LATEST.json",
            {
                "operation_id": checked.operation_id,
                "updated_at": checked.updated_at.isoformat(),
            },
        )
        return checked

    def update(
        self,
        operation_id: str,
        *,
        status: UpdateOperationStatus | None = None,
        stage: UpdateOperationStage | None = None,
        progress_percent: int | None = None,
        phase_status: str | None = None,
        phase_detail: str | None = None,
        **changes: object,
    ) -> UpdateOperation:
        with self._mutation_lock:
            current = self.get(operation_id)
            if current is None:
                raise KeyError(operation_id)
            now = datetime.now(UTC)
            payload = current.model_dump(mode="python")
            if status is not None:
                payload["status"] = status
            if stage is not None:
                payload["current_stage"] = stage
            if progress_percent is not None:
                payload["progress_percent"] = progress_percent
            payload.update(changes)
            payload["updated_at"] = now
            phases = list(current.phases)
            if stage is not None and phase_status is not None:
                prior = next(
                    (
                        phase
                        for phase in reversed(current.phases)
                        if phase.stage is stage and phase.status == "running"
                    ),
                    None,
                )
                phases = [
                    phase
                    for phase in phases
                    if not (
                        phase.stage is stage
                        and phase.status in {"running", "completed"}
                    )
                ]
                started_at = (
                    prior.started_at
                    if prior is not None
                    else now
                    if phase_status == "running"
                    else None
                )
                if phase_status == "completed":
                    started_at = prior.started_at if prior is not None else now
                phases.append(
                    OperationPhase(
                        stage=stage,
                        status=phase_status,
                        started_at=started_at,
                        finished_at=now
                        if phase_status in {"completed", "failed"}
                        else None,
                        detail=phase_detail,
                    )
                )
            payload["phases"] = tuple(phases)
            updated = UpdateOperation.model_validate(payload, strict=True)
            return self.save(updated)


# Windows raises PermissionError (WinError 32) from os.replace while any other
# handle on the destination is still open. The UI polls these operation records
# several times per second and the scheduler heartbeats into the same directory,
# so short-lived collisions are expected rather than exceptional. Retrying keeps
# the whole EOD pipeline from failing on a transient handle, and the temporary
# file is always removed so a failed replace cannot leave orphaned ".tmp" files.
_REPLACE_RETRY_DELAYS = (0.02, 0.05, 0.1, 0.2, 0.4, 0.8)


def _replace_with_retry(source: Path, target: Path) -> None:
    for delay in _REPLACE_RETRY_DELAYS:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(delay)
    os.replace(source, target)


def _atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    try:
        _replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["EodOperationStore", "OperationCancellationConflictError"]
