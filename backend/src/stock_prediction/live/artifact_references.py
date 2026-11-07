"""Portable, root-contained references for immutable live artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactReferenceError(RuntimeError):
    """An artifact reference is malformed, unsafe, missing, or corrupt."""


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    artifact_id: str
    relative_path: str
    sha256: str
    schema_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        _validate_identifier(self.artifact_id, error_code="artifact_id_invalid")
        _validate_identifier(
            self.schema_version,
            error_code="artifact_schema_version_invalid",
        )
        normalized = _normalized_relative_path(self.relative_path)
        digest = self.sha256.lower()
        if _SHA256.fullmatch(digest) is None:
            raise ArtifactReferenceError("artifact_hash_invalid")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ArtifactReferenceError("artifact_created_at_invalid")
        object.__setattr__(self, "relative_path", normalized)
        object.__setattr__(self, "sha256", digest)

    def as_dict(self) -> dict[str, str]:
        return {
            "artifact_id": self.artifact_id,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "schema_version": self.schema_version,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ArtifactReference:
        try:
            created_at = datetime.fromisoformat(str(payload["created_at"]))
            return cls(
                artifact_id=str(payload["artifact_id"]),
                relative_path=str(payload["relative_path"]),
                sha256=str(payload["sha256"]),
                schema_version=str(payload["schema_version"]),
                created_at=created_at,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ArtifactReferenceError("artifact_reference_invalid") from error


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise ArtifactReferenceError("artifact_missing") from error
    return digest.hexdigest()


def build_artifact_reference(
    *,
    artifact_root: Path,
    artifact_id: str,
    target: Path,
    schema_version: str,
    created_at: datetime,
) -> ArtifactReference:
    root = artifact_root.resolve()
    resolved = target.resolve()
    _require_contained(root, resolved)
    if not resolved.is_file():
        raise ArtifactReferenceError("artifact_missing")
    relative_path = resolved.relative_to(root).as_posix()
    return ArtifactReference(
        artifact_id=artifact_id,
        relative_path=relative_path,
        sha256=sha256_file(resolved),
        schema_version=schema_version,
        created_at=created_at,
    )


def resolve_artifact_reference(
    artifact_root: Path,
    reference: ArtifactReference,
) -> Path:
    root = artifact_root.resolve()
    relative = Path(*PurePosixPath(reference.relative_path).parts)
    resolved = (root / relative).resolve()
    _require_contained(root, resolved)
    if not resolved.is_file():
        raise ArtifactReferenceError("artifact_missing")
    if sha256_file(resolved) != reference.sha256:
        raise ArtifactReferenceError("artifact_hash_mismatch")
    return resolved


def _normalized_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ArtifactReferenceError("artifact_path_invalid")
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value.replace("\\", "/"))
    if windows.drive or windows.root or posix.is_absolute():
        raise ArtifactReferenceError("artifact_path_invalid")
    if any(part in {"", ".", ".."} for part in posix.parts):
        raise ArtifactReferenceError("artifact_path_invalid")
    normalized = posix.as_posix()
    if not normalized or normalized == ".":
        raise ArtifactReferenceError("artifact_path_invalid")
    return normalized


def _require_contained(root: Path, target: Path) -> None:
    if target == root or root not in target.parents:
        raise ArtifactReferenceError("artifact_path_outside_root")


def _validate_identifier(value: str, *, error_code: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 256
        or any(ord(character) < 32 for character in value)
    ):
        raise ArtifactReferenceError(error_code)


__all__ = [
    "ArtifactReference",
    "ArtifactReferenceError",
    "build_artifact_reference",
    "resolve_artifact_reference",
    "sha256_file",
]
