"""Cross-process lease for the single live OpenD and DuckDB writer."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

from filelock import FileLock

from stock_prediction.live.config import LivePaths


class LiveWriterLease:
    """File lock with an atomic PID/task heartbeat for local diagnosis."""

    def __init__(
        self,
        paths: LivePaths,
        *,
        task_id: str,
        timeout: float = 0,
        heartbeat_seconds: float = 5,
    ) -> None:
        self.paths = paths
        self.task_id = task_id
        self.timeout = timeout
        self.heartbeat_seconds = heartbeat_seconds
        self.pid = os.getpid()
        self.acquired_at: datetime | None = None
        self._lock = FileLock(str(paths.writer_lock), timeout=timeout)
        self._stop = Event()
        self._heartbeat_thread: Thread | None = None

    def __enter__(self) -> "LiveWriterLease":
        self.paths.ensure()
        self._lock.acquire()
        self.acquired_at = datetime.now(UTC)
        self._write_owner()
        self._heartbeat_thread = Thread(
            target=self._heartbeat,
            name=f"live-writer-heartbeat-{self.task_id}",
            daemon=True,
        )
        self._heartbeat_thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1)
        self._remove_owner_if_current()
        self._lock.release()

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            self._write_owner()

    def _write_owner(self) -> None:
        now = datetime.now(UTC)
        _atomic_json(
            self.paths.writer_lock_owner,
            {
                "pid": self.pid,
                "task_id": self.task_id,
                "acquired_at": (
                    self.acquired_at.isoformat()
                    if self.acquired_at is not None
                    else now.isoformat()
                ),
                "heartbeat_at": now.isoformat(),
            },
        )

    def _remove_owner_if_current(self) -> None:
        path = self.paths.writer_lock_owner
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                payload.get("pid") == self.pid
                and payload.get("task_id") == self.task_id
            ):
                path.unlink(missing_ok=True)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return


def _atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


__all__ = ["LiveWriterLease"]
