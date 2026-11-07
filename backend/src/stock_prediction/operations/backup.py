"""Checksum-bound local backups for raw evidence and model registry state."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import os
from pathlib import Path
import shutil

from pydantic import TypeAdapter, ValidationError

from stock_prediction.contracts.market_data import Identifier
from stock_prediction.operations.contracts import (
    BackupItem,
    BackupManifest,
    build_backup_manifest,
)
from stock_prediction.operations.disk import DiskSpaceGuard


class BackupError(RuntimeError):
    """Backup or restore evidence is unsafe, corrupt, or incomplete."""


_IDENTIFIER_ADAPTER: TypeAdapter[str] = TypeAdapter(Identifier)


class LocalBackupStore:
    def __init__(
        self,
        root: Path,
        *,
        disk_guard: DiskSpaceGuard | None = None,
    ) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.disk_guard = disk_guard or DiskSpaceGuard(
            minimum_free_bytes=2 * 1024**3,
            minimum_free_ratio=0.05,
        )

    def create(
        self,
        *,
        backup_id: str,
        sources: Mapping[str, Path],
        created_at: datetime,
    ) -> Path:
        backup_id = _identifier(backup_id, "backup ID")
        if not sources:
            raise BackupError("backup requires at least one source")
        inventory: list[tuple[str, Path]] = []
        planned_bytes = 0
        for label, source in sorted(sources.items()):
            safe_label = _identifier(label, "source label")
            source_root = source.resolve(strict=True)
            if source.is_symlink() or not source_root.is_dir():
                raise BackupError("backup sources must be regular directories")
            for path in sorted(source_root.rglob("*")):
                if path.is_symlink():
                    raise BackupError("backup sources cannot contain symlinks")
                if path.is_dir():
                    continue
                if not path.is_file():
                    raise BackupError("backup sources must contain regular files")
                if path.name.endswith(".lock") or ".staging" in path.parts:
                    continue
                relative = path.relative_to(source_root).as_posix()
                backup_relative = f"{safe_label}/{relative}"
                BackupItem(
                    relative_path=backup_relative,
                    sha256="0" * 64,
                    size_bytes=0,
                )
                inventory.append((backup_relative, path))
                planned_bytes += path.stat().st_size
        if not inventory:
            raise BackupError("backup sources contain no eligible files")
        self.disk_guard.require(self.root, planned_bytes=planned_bytes)
        final = self.root / backup_id
        if final.exists():
            self.verify(final)
            return final
        staging = self.root / f"{backup_id}.staging"
        if staging.exists():
            self._remove_internal(staging)
        (staging / "payload").mkdir(parents=True)
        items: list[BackupItem] = []
        try:
            for relative, source in inventory:
                destination = staging / "payload" / Path(relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                items.append(
                    BackupItem(
                        relative_path=relative,
                        sha256=_file_sha256(destination),
                        size_bytes=destination.stat().st_size,
                    )
                )
            manifest = build_backup_manifest(
                backup_id=backup_id,
                created_at=created_at,
                items=tuple(items),
            )
            _write_new(staging / "manifest.json", manifest.model_dump_json(indent=2))
            os.replace(staging, final)
        except Exception:
            if staging.exists():
                self._remove_internal(staging)
            raise
        self.verify(final)
        return final

    def verify(self, backup: Path) -> BackupManifest:
        backup_root = backup.resolve(strict=True)
        if (
            backup.is_symlink()
            or backup_root.parent != self.root
            or not backup_root.is_dir()
        ):
            raise BackupError("backup path is outside the configured store")
        manifest_path = backup_root / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise BackupError("backup manifest is missing")
        try:
            manifest = BackupManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8"), strict=True
            )
        except (OSError, ValidationError) as error:
            raise BackupError("backup manifest is invalid") from error
        if manifest.backup_id != backup_root.name:
            raise BackupError("backup directory and manifest disagree")
        payload_root = backup_root / "payload"
        expected_paths = {item.relative_path for item in manifest.items}
        actual_paths: set[str] = set()
        for path in payload_root.rglob("*"):
            if path.is_symlink():
                raise BackupError("backup payload contains a symlink")
            if path.is_file():
                actual_paths.add(path.relative_to(payload_root).as_posix())
        if actual_paths != expected_paths:
            raise BackupError("backup payload inventory differs from manifest")
        for item in manifest.items:
            path = payload_root / Path(item.relative_path)
            resolved = path.resolve(strict=True)
            if payload_root not in resolved.parents or not resolved.is_file():
                raise BackupError("backup payload path is unsafe")
            if resolved.stat().st_size != item.size_bytes:
                raise BackupError("backup payload size changed")
            if _file_sha256(resolved) != item.sha256:
                raise BackupError("backup payload checksum changed")
        return manifest

    def restore(self, backup: Path, *, target: Path) -> Path:
        manifest = self.verify(backup)
        target = target.resolve()
        if target.exists():
            raise BackupError("restore target must not already exist")
        staging = target.with_name(f"{target.name}.staging")
        if staging.exists():
            raise BackupError("restore staging target already exists")
        planned_bytes = sum(item.size_bytes for item in manifest.items)
        self.disk_guard.require(target, planned_bytes=planned_bytes)
        payload_root = backup.resolve(strict=True) / "payload"
        staging.mkdir(parents=True)
        try:
            for item in manifest.items:
                source = payload_root / Path(item.relative_path)
                destination = staging / Path(item.relative_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                if destination.stat().st_size != item.size_bytes:
                    raise BackupError("restored payload size differs")
                if _file_sha256(destination) != item.sha256:
                    raise BackupError("restored payload checksum differs")
            os.replace(staging, target)
        except Exception:
            if staging.exists():
                resolved = staging.resolve()
                if resolved.parent == target.parent:
                    shutil.rmtree(resolved)
            raise
        return target

    def _remove_internal(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved.parent != self.root:
            raise BackupError("refusing to remove path outside backup store")
        shutil.rmtree(resolved)


def _identifier(value: str, label: str) -> str:
    try:
        return _IDENTIFIER_ADAPTER.validate_python(value, strict=True)
    except ValidationError as error:
        raise BackupError(f"{label} is invalid") from error


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
