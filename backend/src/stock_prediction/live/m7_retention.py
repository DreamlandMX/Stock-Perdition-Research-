"""Reference-aware M7 failed-trial checkpoint retention."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_audit import M7AuditLog
from stock_prediction.modeling.report import (
    deterministic_hash,
    file_sha256,
    write_immutable_json,
)


class M7RetentionError(RuntimeError):
    """Failed-trial retention metadata or references are unsafe."""


class M7FailedTrialMetadata(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["m7-failed-trial-v1"] = "m7-failed-trial-v1"
    trial_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    status: Literal["failed"]
    created_at: UtcDatetime
    checkpoint_relative_paths: tuple[str, ...]
    parameters: dict[str, object]
    metrics: dict[str, object]

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        if len(set(self.checkpoint_relative_paths)) != len(
            self.checkpoint_relative_paths
        ):
            raise ValueError("M7 failed checkpoint paths must be unique")
        for value in self.checkpoint_relative_paths:
            path = PurePosixPath(value)
            if (
                path.is_absolute()
                or not path.parts
                or ".." in path.parts
                or path.parts[0] != "checkpoints"
            ):
                raise ValueError("M7 failed checkpoint path is unsafe")
        return self


@dataclass(frozen=True, slots=True)
class M7RetentionResult:
    deleted_checkpoints: tuple[Path, ...]
    deletion_records: tuple[Path, ...]
    retained_referenced: tuple[Path, ...]
    retained_young: tuple[Path, ...]


class M7RetentionManager:
    def __init__(self, paths: LivePaths) -> None:
        self.paths = paths
        self.models_root = paths.models.resolve()
        self.failed_root = self.models_root / "m7-failed-trials"
        self.records_root = self.models_root / "m7-retention" / "deletions"
        self.records_root.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(
            str(self.models_root / "m7-retention" / ".retention.lock")
        )

    def cleanup_failed_checkpoints(self, *, now: datetime) -> M7RetentionResult:
        deleted: list[Path] = []
        records: list[Path] = []
        referenced: list[Path] = []
        young: list[Path] = []
        with self._lock:
            references = self._reference_tokens()
            if not self.failed_root.is_dir():
                return M7RetentionResult((), (), (), ())
            for metadata_path in sorted(self.failed_root.glob("*/metadata.json")):
                metadata = self._load_metadata(metadata_path)
                trial_root = metadata_path.parent.resolve()
                if trial_root.name != metadata.trial_id:
                    raise M7RetentionError("m7_failed_trial_identity_mismatch")
                is_young = now.astimezone(metadata.created_at.tzinfo) - (
                    metadata.created_at
                ) <= timedelta(days=90)
                for relative in metadata.checkpoint_relative_paths:
                    checkpoint = trial_root / Path(*PurePosixPath(relative).parts)
                    resolved = checkpoint.resolve()
                    if trial_root not in resolved.parents:
                        raise M7RetentionError("m7_failed_checkpoint_outside_trial")
                    if not checkpoint.exists():
                        continue
                    if checkpoint.is_symlink() or not resolved.is_file():
                        raise M7RetentionError("m7_failed_checkpoint_unsafe")
                    if is_young:
                        young.append(resolved)
                        continue
                    model_relative = resolved.relative_to(self.models_root).as_posix()
                    if model_relative in references:
                        referenced.append(resolved)
                        continue
                    digest = file_sha256(resolved)
                    size = resolved.stat().st_size
                    resolved.unlink()
                    payload = {
                        "schema_version": "m7-retention-deletion-v1",
                        "trial_id": metadata.trial_id,
                        "checkpoint_relative_path": model_relative,
                        "checkpoint_sha256": digest,
                        "size_bytes": size,
                        "deleted_at": now.isoformat().replace("+00:00", "Z"),
                        "reason": "unreferenced_failed_trial_checkpoint_older_than_90_days",
                        "metadata_retained": True,
                    }
                    identity = deterministic_hash(payload)
                    record = self.records_root / f"{identity}.json"
                    write_immutable_json(record, payload, pretty=False)
                    M7AuditLog(self.paths).append(
                        event_type="retention_deleted",
                        recorded_at=now,
                        actor="m7-retention-worker",
                        run_id=None,
                        bundle_id=None,
                        package_sha256=None,
                        detail={
                            "trial_id": metadata.trial_id,
                            "checkpoint_sha256": digest,
                            "size_bytes": size,
                        },
                    )
                    deleted.append(resolved)
                    records.append(record)
        return M7RetentionResult(
            deleted_checkpoints=tuple(deleted),
            deletion_records=tuple(records),
            retained_referenced=tuple(referenced),
            retained_young=tuple(young),
        )

    def _load_metadata(self, path: Path) -> M7FailedTrialMetadata:
        try:
            return M7FailedTrialMetadata.model_validate_json(
                path.read_text(encoding="utf-8"), strict=True
            )
        except (OSError, ValidationError) as error:
            raise M7RetentionError("m7_failed_trial_metadata_invalid") from error

    def _reference_tokens(self) -> set[str]:
        tokens: set[str] = set()
        if not self.models_root.is_dir():
            return tokens
        for path in self.models_root.rglob("*.json"):
            if self.failed_root in path.resolve().parents:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            _collect_strings(payload, tokens)
        return tokens


def _collect_strings(value: object, target: set[str]) -> None:
    if isinstance(value, str):
        target.add(value.replace("\\", "/"))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _collect_strings(key, target)
            _collect_strings(item, target)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            _collect_strings(item, target)


__all__ = [
    "M7FailedTrialMetadata",
    "M7RetentionError",
    "M7RetentionManager",
    "M7RetentionResult",
]
