"""Immutable JSON persistence for capability probe snapshots."""

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from filelock import FileLock

from stock_prediction.contracts.capability import CapabilitySnapshot


DEFAULT_CAPABILITY_ROOT = Path("var/capabilities")
SNAPSHOT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
INVALID_SNAPSHOT_ID_MESSAGE = (
    "invalid snapshot_id: expected 1-128 safe ASCII characters without '..'"
)


class InvalidSnapshotIdError(ValueError):
    """Raised before persistence when a snapshot ID is not filesystem-safe."""


class JsonCapabilityStore:
    """Persist immutable snapshots and an atomically updated latest pointer."""

    def __init__(self, root: Path = DEFAULT_CAPABILITY_ROOT) -> None:
        self.root = root

    def save(self, snapshot: CapabilitySnapshot) -> Path:
        """Save a snapshot once and atomically publish it as the latest."""

        self._validate_snapshot_id(snapshot.snapshot_id)
        self.root.mkdir(parents=True, exist_ok=True)
        lock = FileLock(self.root / ".capability-store.lock")
        with lock:
            return self._save_locked(snapshot)

    def _save_locked(self, snapshot: CapabilitySnapshot) -> Path:
        existing = self._find_snapshot(snapshot.snapshot_id)
        if existing is not None:
            canonical_payload = existing.read_text(encoding="utf-8")
            canonical = CapabilitySnapshot.model_validate_json(canonical_payload)
            self._publish_if_latest(canonical, canonical_payload)
            return existing

        payload = self._serialize(snapshot)
        snapshot_path = self.root / self._snapshot_filename(snapshot)
        temporary_path = self._write_temporary(payload)
        try:
            try:
                os.link(temporary_path, snapshot_path)
            except FileExistsError:
                canonical = self._load(snapshot_path)
                if canonical.snapshot_id != snapshot.snapshot_id:
                    raise
                payload = snapshot_path.read_text(encoding="utf-8")
            else:
                canonical = snapshot
            finally:
                temporary_path.unlink(missing_ok=True)

            self._publish_if_latest(canonical, payload)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return snapshot_path

    def load_latest(self) -> CapabilitySnapshot | None:
        """Return the latest persisted snapshot, or ``None`` before first save."""

        latest_path = self.root / "latest.json"
        if not latest_path.exists():
            return None
        return self._load(latest_path)

    def _find_snapshot(self, snapshot_id: str) -> Path | None:
        if not self.root.exists():
            return None
        for path in self.root.glob("*.json"):
            if path.name == "latest.json":
                continue
            try:
                persisted = self._load(path)
            except (OSError, ValueError):
                continue
            if persisted.snapshot_id == snapshot_id:
                return path
        return None

    def _write_temporary(self, payload: str) -> Path:
        descriptor, raw_path = tempfile.mkstemp(
            dir=self.root,
            prefix=".capability-",
            suffix=".tmp",
            text=True,
        )
        path = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path

    def _publish_latest(self, payload: str) -> None:
        latest_temporary_path = self._write_temporary(payload)
        try:
            os.replace(latest_temporary_path, self.root / "latest.json")
        finally:
            latest_temporary_path.unlink(missing_ok=True)

    def _publish_if_latest(
        self,
        candidate: CapabilitySnapshot,
        canonical_payload: str,
    ) -> None:
        latest = self.load_latest()
        if latest is None or self._ordering_key(candidate) > self._ordering_key(latest):
            self._publish_latest(canonical_payload)

    @staticmethod
    def _ordering_key(snapshot: CapabilitySnapshot) -> tuple[datetime, str]:
        return snapshot.probed_at, snapshot.snapshot_id

    @staticmethod
    def _validate_snapshot_id(snapshot_id: str) -> None:
        if (
            SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id) is None
            or ".." in snapshot_id
        ):
            raise InvalidSnapshotIdError(INVALID_SNAPSHOT_ID_MESSAGE)

    @staticmethod
    def _serialize(snapshot: CapabilitySnapshot) -> str:
        return (
            json.dumps(
                snapshot.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    @staticmethod
    def _load(path: Path) -> CapabilitySnapshot:
        return CapabilitySnapshot.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _snapshot_filename(snapshot: CapabilitySnapshot) -> str:
        timestamp = snapshot.probed_at.strftime("%Y%m%dT%H%M%S.%fZ")
        readable_id = re.sub(r"[^A-Za-z0-9._-]+", "-", snapshot.snapshot_id).strip(
            ".-"
        )
        readable_id = readable_id[:48] or "snapshot"
        digest = hashlib.sha256(snapshot.snapshot_id.encode("utf-8")).hexdigest()[:12]
        return f"{timestamp}--{readable_id}-{digest}.json"
