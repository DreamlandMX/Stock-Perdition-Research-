from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path

import pytest

from stock_prediction.live.artifact_references import (
    ArtifactReference,
    ArtifactReferenceError,
    build_artifact_reference,
    resolve_artifact_reference,
)


NOW = datetime(2026, 8, 24, 4, tzinfo=UTC)
PAYLOAD = b"feature-bytes"
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


def reference(relative_path: str, *, sha256: str = PAYLOAD_SHA256) -> ArtifactReference:
    return ArtifactReference(
        artifact_id="feature-v5-hash",
        relative_path=relative_path,
        sha256=sha256,
        schema_version="live-feature-artifact-v1",
        created_at=NOW,
    )


def test_reference_round_trips_inside_artifact_root(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    target = root / "features" / "v5" / "features.parquet"
    target.parent.mkdir(parents=True)
    target.write_bytes(PAYLOAD)

    artifact = build_artifact_reference(
        artifact_root=root,
        artifact_id="feature-v5-hash",
        target=target,
        schema_version="live-feature-artifact-v1",
        created_at=NOW,
    )

    assert artifact.relative_path == "features/v5/features.parquet"
    assert artifact.sha256 == PAYLOAD_SHA256
    assert ArtifactReference.from_dict(artifact.as_dict()) == artifact
    assert resolve_artifact_reference(root, artifact) == target.resolve()


@pytest.mark.parametrize(
    "relative_path",
    (
        "../outside.bin",
        "features/../../outside.bin",
        "C:/outside.bin",
        "C:outside.bin",
        "\\\\server\\share\\outside.bin",
        "/outside.bin",
        "",
    ),
)
def test_reference_rejects_non_relative_or_traversing_paths(
    relative_path: str,
) -> None:
    with pytest.raises(ArtifactReferenceError, match="artifact_path_invalid"):
        reference(relative_path)


def test_reference_rejects_target_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(PAYLOAD)

    with pytest.raises(ArtifactReferenceError, match="artifact_path_outside_root"):
        build_artifact_reference(
            artifact_root=root,
            artifact_id="outside-artifact",
            target=outside,
            schema_version="live-feature-artifact-v1",
            created_at=NOW,
        )


def test_reference_rejects_missing_target(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()

    with pytest.raises(ArtifactReferenceError, match="artifact_missing"):
        resolve_artifact_reference(root, reference("missing.bin"))


def test_reference_rejects_hash_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    target = root / "model.bin"
    target.parent.mkdir()
    target.write_bytes(b"tampered")

    with pytest.raises(ArtifactReferenceError, match="artifact_hash_mismatch"):
        resolve_artifact_reference(root, reference("model.bin"))


def test_reference_rejects_symlink_that_escapes_root(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(PAYLOAD)
    link = root / "escaped.bin"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")

    with pytest.raises(ArtifactReferenceError, match="artifact_path_outside_root"):
        resolve_artifact_reference(root, reference("escaped.bin"))


def test_reference_requires_timezone_aware_created_at() -> None:
    with pytest.raises(ArtifactReferenceError, match="artifact_created_at_invalid"):
        ArtifactReference(
            artifact_id="feature-v5-hash",
            relative_path="features.parquet",
            sha256=PAYLOAD_SHA256,
            schema_version="live-feature-artifact-v1",
            created_at=datetime(2026, 8, 24, 4),
        )
