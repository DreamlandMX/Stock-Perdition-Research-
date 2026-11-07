"""Immutable model releases with a hash-chained lifecycle event ledger."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import shutil

from filelock import FileLock
from pydantic import TypeAdapter, ValidationError

from stock_prediction.contracts.market_data import VersionId
from stock_prediction.operations.contracts import (
    LifecycleEvent,
    LifecycleEventType,
    LifecycleSnapshot,
    ModelArtifact,
    ReleaseManifest,
    build_lifecycle_event,
    build_release_manifest,
)
from stock_prediction.operations.disk import DiskSpaceGuard


class ModelRegistryError(RuntimeError):
    """Registry evidence is missing, corrupt, or requests an illegal transition."""


class ModelArtifactMissing(ModelRegistryError):
    """A registered model artifact is missing or no longer a regular file."""


_VERSION_ADAPTER: TypeAdapter[str] = TypeAdapter(VersionId)


class LocalModelRegistry:
    def __init__(
        self,
        root: Path,
        *,
        disk_guard: DiskSpaceGuard | None = None,
    ) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.releases_root = self.root / "releases"
        self.releases_root.mkdir(exist_ok=True)
        self.ledger_path = self.root / "lifecycle-events.jsonl"
        self._lock = FileLock(str(self.root / "registry.lock"))
        self.disk_guard = disk_guard or DiskSpaceGuard(
            minimum_free_bytes=2 * 1024**3,
            minimum_free_ratio=0.05,
        )

    def register_release(
        self,
        *,
        model_version: str,
        task: str,
        horizon: int,
        calibration_version: str | None,
        source_decision_id: str,
        promotion_eligible: bool,
        artifacts: Mapping[str, Path],
        registered_at: datetime,
    ) -> ReleaseManifest:
        model_version = _validated_version(model_version)
        if not artifacts:
            raise ModelRegistryError("release requires at least one artifact")
        planned_bytes = 0
        normalized: list[tuple[str, Path]] = []
        for name, source in sorted(artifacts.items()):
            relative = ModelArtifact(
                relative_path=f"artifacts/{name}",
                sha256="0" * 64,
                size_bytes=0,
            ).relative_path
            source_path = source.resolve(strict=True)
            if source.is_symlink() or not source_path.is_file():
                raise ModelRegistryError("release sources must be regular files")
            planned_bytes += source_path.stat().st_size
            normalized.append((relative, source_path))
        self.disk_guard.require(self.root, planned_bytes=planned_bytes)
        with self._lock:
            final = self.releases_root / model_version
            if final.exists():
                existing = self._load_manifest(model_version, verify_artifacts=True)
                expected_identity = (
                    task,
                    horizon,
                    calibration_version,
                    source_decision_id,
                    promotion_eligible,
                )
                actual_identity = (
                    existing.task,
                    existing.horizon,
                    existing.calibration_version,
                    existing.source_decision_id,
                    existing.promotion_eligible,
                )
                if expected_identity != actual_identity:
                    raise ModelRegistryError(
                        "model version already owns another release"
                    )
                self._ensure_registered_event(existing)
                return existing
            staging = self.releases_root / f"{model_version}.staging"
            if staging.exists():
                self._remove_internal_tree(staging)
            (staging / "artifacts").mkdir(parents=True)
            descriptors: list[ModelArtifact] = []
            try:
                for relative, source in normalized:
                    destination = staging / Path(relative)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
                    descriptors.append(
                        ModelArtifact(
                            relative_path=relative,
                            sha256=_file_sha256(destination),
                            size_bytes=destination.stat().st_size,
                        )
                    )
                manifest = build_release_manifest(
                    model_version=model_version,
                    task=task,
                    horizon=horizon,
                    calibration_version=calibration_version,
                    registered_at=registered_at,
                    source_decision_id=source_decision_id,
                    promotion_eligible=promotion_eligible,
                    artifacts=tuple(descriptors),
                )
                _write_new(
                    staging / "manifest.json", manifest.model_dump_json(indent=2)
                )
                os.replace(staging, final)
            except Exception:
                if staging.exists():
                    self._remove_internal_tree(staging)
                raise
            self._append_event(
                event_type=LifecycleEventType.REGISTERED,
                task=task,
                horizon=horizon,
                from_version=None,
                to_version=model_version,
                reason="immutable release registered",
                recorded_at=registered_at,
            )
            return manifest

    def mark_challenger(
        self,
        model_version: str,
        *,
        reason: str,
        recorded_at: datetime,
    ) -> LifecycleSnapshot:
        with self._lock:
            release = self._load_manifest(model_version, verify_artifacts=True)
            snapshot = self._snapshot_unlocked(release.task, release.horizon)
            if model_version == snapshot.champion_version:
                raise ModelRegistryError("champion cannot also be marked challenger")
            if model_version not in snapshot.challenger_versions:
                self._append_event(
                    event_type=LifecycleEventType.CHALLENGER_MARKED,
                    task=release.task,
                    horizon=release.horizon,
                    from_version=snapshot.champion_version,
                    to_version=model_version,
                    reason=reason,
                    recorded_at=recorded_at,
                )
            return self._snapshot_unlocked(release.task, release.horizon)

    def promote(
        self,
        model_version: str,
        *,
        reason: str,
        recorded_at: datetime,
    ) -> LifecycleSnapshot:
        with self._lock:
            release = self._load_manifest(model_version, verify_artifacts=True)
            if not release.promotion_eligible:
                raise ModelRegistryError("release did not pass the promotion gate")
            snapshot = self._snapshot_unlocked(release.task, release.horizon)
            if snapshot.champion_version == model_version:
                return snapshot
            self._append_event(
                event_type=LifecycleEventType.PROMOTED,
                task=release.task,
                horizon=release.horizon,
                from_version=snapshot.champion_version,
                to_version=model_version,
                reason=reason,
                recorded_at=recorded_at,
            )
            return self._snapshot_unlocked(release.task, release.horizon)

    def reject(
        self,
        model_version: str,
        *,
        reason: str,
        recorded_at: datetime,
    ) -> LifecycleSnapshot:
        with self._lock:
            release = self._load_manifest(model_version, verify_artifacts=False)
            snapshot = self._snapshot_unlocked(release.task, release.horizon)
            if snapshot.champion_version == model_version:
                raise ModelRegistryError(
                    "active champion must be rolled back before rejection"
                )
            if model_version not in snapshot.rejected_versions:
                self._append_event(
                    event_type=LifecycleEventType.REJECTED,
                    task=release.task,
                    horizon=release.horizon,
                    from_version=snapshot.champion_version,
                    to_version=model_version,
                    reason=reason,
                    recorded_at=recorded_at,
                )
            return self._snapshot_unlocked(release.task, release.horizon)

    def rollback(
        self,
        *,
        task: str,
        horizon: int,
        reason: str,
        recorded_at: datetime,
        target_version: str | None = None,
    ) -> LifecycleSnapshot:
        with self._lock:
            snapshot = self._snapshot_unlocked(task, horizon)
            if snapshot.champion_version is None:
                raise ModelRegistryError("there is no champion to roll back")
            target = target_version or next(
                (
                    version
                    for version in snapshot.prior_champion_versions
                    if version != snapshot.champion_version
                ),
                None,
            )
            if target is None:
                raise ModelRegistryError("there is no prior champion to restore")
            release = self._load_manifest(target, verify_artifacts=True)
            if (release.task, release.horizon) != (task, horizon):
                raise ModelRegistryError("rollback target belongs to another task")
            self._append_event(
                event_type=LifecycleEventType.ROLLED_BACK,
                task=task,
                horizon=horizon,
                from_version=snapshot.champion_version,
                to_version=target,
                reason=reason,
                recorded_at=recorded_at,
            )
            return self._snapshot_unlocked(task, horizon)

    def deactivate_champion(
        self,
        *,
        task: str,
        horizon: int,
        reason: str,
        recorded_at: datetime,
    ) -> LifecycleSnapshot:
        """Deactivate a first Champion when no prior version can be restored."""

        with self._lock:
            snapshot = self._snapshot_unlocked(task, horizon)
            champion = snapshot.champion_version
            if champion is None:
                raise ModelRegistryError("there is no champion to deactivate")
            if snapshot.prior_champion_versions:
                raise ModelRegistryError(
                    "champion has a prior version and must be rolled back"
                )
            self._append_event(
                event_type=LifecycleEventType.DEACTIVATED,
                task=task,
                horizon=horizon,
                from_version=champion,
                to_version=champion,
                reason=reason,
                recorded_at=recorded_at,
            )
            return self._snapshot_unlocked(task, horizon)

    def recover_champion(
        self,
        *,
        task: str,
        horizon: int,
        recorded_at: datetime,
    ) -> LifecycleSnapshot:
        with self._lock:
            snapshot = self._snapshot_unlocked(task, horizon)
            if snapshot.champion_version is None:
                raise ModelRegistryError("there is no champion to verify")
            try:
                self._load_manifest(snapshot.champion_version, verify_artifacts=True)
            except ModelRegistryError:
                return self.rollback(
                    task=task,
                    horizon=horizon,
                    reason="automatic recovery after champion integrity failure",
                    recorded_at=recorded_at,
                )
            return snapshot

    def snapshot(self, task: str, horizon: int) -> LifecycleSnapshot:
        with self._lock:
            return self._snapshot_unlocked(task, horizon)

    def verify_release(self, model_version: str) -> ReleaseManifest:
        with self._lock:
            return self._load_manifest(model_version, verify_artifacts=True)

    def events(self) -> tuple[LifecycleEvent, ...]:
        with self._lock:
            return self._read_events()

    def _snapshot_unlocked(self, task: str, horizon: int) -> LifecycleSnapshot:
        champion: str | None = None
        challengers: list[str] = []
        prior: list[str] = []
        rejected: list[str] = []
        relevant: list[LifecycleEvent] = []
        for event in self._read_events():
            if (event.task, event.horizon) != (task, horizon):
                continue
            relevant.append(event)
            version = event.to_version
            if event.event_type is LifecycleEventType.CHALLENGER_MARKED:
                _append_unique(challengers, version)
                if version in rejected:
                    rejected.remove(version)
            elif event.event_type is LifecycleEventType.PROMOTED:
                if champion is not None and champion != version:
                    prior = [champion, *[item for item in prior if item != champion]]
                champion = version
                challengers = [item for item in challengers if item != version]
                rejected = [item for item in rejected if item != version]
            elif event.event_type is LifecycleEventType.ROLLED_BACK:
                if champion is not None and champion != version:
                    _append_unique(rejected, champion)
                champion = version
                prior = [item for item in prior if item != version]
                challengers = [item for item in challengers if item != version]
            elif event.event_type is LifecycleEventType.DEACTIVATED:
                if champion != version:
                    raise ModelRegistryError("model registry ledger is invalid")
                _append_unique(rejected, version)
                champion = None
                challengers = [item for item in challengers if item != version]
            elif event.event_type is LifecycleEventType.REJECTED:
                _append_unique(rejected, version)
                challengers = [item for item in challengers if item != version]
        last = relevant[-1] if relevant else None
        return LifecycleSnapshot(
            task=task,
            horizon=horizon,
            champion_version=champion,
            challenger_versions=tuple(challengers),
            prior_champion_versions=tuple(prior),
            rejected_versions=tuple(rejected),
            revision=len(relevant),
            last_event_at=None if last is None else last.recorded_at,
            ledger_head_hash=None if last is None else last.event_hash,
        )

    def _load_manifest(
        self, model_version: str, *, verify_artifacts: bool
    ) -> ReleaseManifest:
        model_version = _validated_version(model_version)
        release_dir = self.releases_root / model_version
        manifest_path = release_dir / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ModelArtifactMissing("release manifest is missing")
        try:
            manifest = ReleaseManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8"), strict=True
            )
        except (OSError, ValidationError) as error:
            raise ModelRegistryError("release manifest is invalid") from error
        if manifest.model_version != model_version:
            raise ModelRegistryError("release directory and manifest disagree")
        if verify_artifacts:
            for artifact in manifest.artifacts:
                path = release_dir / Path(artifact.relative_path)
                try:
                    resolved = path.resolve(strict=True)
                except OSError as error:
                    raise ModelArtifactMissing(
                        "registered model artifact is missing"
                    ) from error
                if (
                    path.is_symlink()
                    or release_dir not in resolved.parents
                    or not resolved.is_file()
                ):
                    raise ModelArtifactMissing("registered model artifact is unsafe")
                if resolved.stat().st_size != artifact.size_bytes:
                    raise ModelRegistryError("registered model artifact size changed")
                if _file_sha256(resolved) != artifact.sha256:
                    raise ModelRegistryError("registered model artifact hash changed")
        return manifest

    def _ensure_registered_event(self, manifest: ReleaseManifest) -> None:
        if any(
            event.event_type is LifecycleEventType.REGISTERED
            and event.to_version == manifest.model_version
            for event in self._read_events()
        ):
            return
        self._append_event(
            event_type=LifecycleEventType.REGISTERED,
            task=manifest.task,
            horizon=manifest.horizon,
            from_version=None,
            to_version=manifest.model_version,
            reason="recovered immutable release registration",
            recorded_at=manifest.registered_at,
        )

    def _append_event(
        self,
        *,
        event_type: LifecycleEventType,
        task: str,
        horizon: int,
        from_version: str | None,
        to_version: str,
        reason: str,
        recorded_at: datetime,
    ) -> LifecycleEvent:
        events = self._read_events()
        event = build_lifecycle_event(
            event_id=f"evt-{len(events) + 1:08d}",
            event_type=event_type,
            task=task,
            horizon=horizon,
            from_version=from_version,
            to_version=to_version,
            reason=reason,
            recorded_at=recorded_at.astimezone(UTC),
            previous_event_hash=None if not events else events[-1].event_hash,
        )
        payload = event.model_dump_json() + "\n"
        with self.ledger_path.open("ab") as stream:
            stream.write(payload.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        return event

    def _read_events(self) -> tuple[LifecycleEvent, ...]:
        if not self.ledger_path.exists():
            return ()
        if self.ledger_path.is_symlink() or not self.ledger_path.is_file():
            raise ModelRegistryError("lifecycle ledger is unsafe")
        events: list[LifecycleEvent] = []
        previous: str | None = None
        try:
            lines = self.ledger_path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines, start=1):
                event = LifecycleEvent.model_validate_json(line, strict=True)
                if event.event_id != f"evt-{index:08d}":
                    raise ModelRegistryError("lifecycle event sequence is invalid")
                if event.previous_event_hash != previous:
                    raise ModelRegistryError("lifecycle event hash chain is invalid")
                events.append(event)
                previous = event.event_hash
        except (OSError, ValidationError) as error:
            raise ModelRegistryError("lifecycle ledger is invalid") from error
        return tuple(events)

    def _remove_internal_tree(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved.parent != self.releases_root:
            raise ModelRegistryError("refusing to remove path outside releases root")
        shutil.rmtree(resolved)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _validated_version(value: str) -> str:
    try:
        return _VERSION_ADAPTER.validate_python(value, strict=True)
    except ValidationError as error:
        raise ModelRegistryError("model version is invalid") from error


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_new(path: Path, payload: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
