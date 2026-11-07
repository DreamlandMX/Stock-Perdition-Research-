"""Immutable development-candidate locks used by the later blind promotion."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from stock_prediction.live.config import LivePaths
from stock_prediction.live.promotion_windows import (
    NEXT_PROMOTION_WINDOW,
    PromotionWindowRegistry,
)


LATEST_LOCK_POINTER = "LATEST-FROZEN-CANDIDATE.json"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_candidate_lock(paths: LivePaths) -> dict[str, Any] | None:
    pointer = paths.models / LATEST_LOCK_POINTER
    if not pointer.is_file():
        return None
    try:
        pointer_payload = json.loads(pointer.read_text(encoding="utf-8"))
        lock_path = Path(str(pointer_payload["lock_path"]))
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if verify_candidate_lock(payload) else None


def verify_candidate_lock(payload: dict[str, Any]) -> bool:
    lock_hash = payload.get("lock_hash")
    if not isinstance(lock_hash, str):
        return False
    canonical = {key: value for key, value in payload.items() if key != "lock_hash"}
    expected = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if lock_hash != expected:
        return False
    for path_key, hash_key in (
        ("package_path", "package_hash"),
        ("report_path", "report_hash"),
        ("candidate_matrix_path", "candidate_matrix_file_hash"),
    ):
        path = Path(str(payload.get(path_key, "")))
        if not path.is_file() or file_sha256(path) != payload.get(hash_key):
            return False
    return True


def create_candidate_lock(
    paths: LivePaths,
    *,
    report: dict[str, Any],
    report_path: Path,
    package_path: Path,
    candidate_matrix_path: Path,
    promotion_window_id: str | None = None,
    frozen_at: datetime | None = None,
) -> dict[str, Any]:
    """Freeze a 17/17 development candidate without evaluating blind data."""

    if not bool(report.get("development_all_gates_passed")):
        raise ValueError("candidate_development_gates_incomplete")
    model_version = str(report["model_version"])
    package_hash = file_sha256(package_path)
    registry = PromotionWindowRegistry(paths)
    selected_window_id = promotion_window_id or NEXT_PROMOTION_WINDOW.window_id
    if selected_window_id == NEXT_PROMOTION_WINDOW.window_id:
        registry.register(NEXT_PROMOTION_WINDOW)
    registered_window = registry.load(selected_window_id)
    definition = registered_window.definition
    if definition.universe_version != str(report["universe_version"]):
        raise RuntimeError("candidate_promotion_window_universe_mismatch")
    if definition.package_sha256 is not None and (
        definition.package_sha256 != package_hash
        or frozen_at is None
        or definition.frozen_at != frozen_at.astimezone(UTC)
    ):
        raise RuntimeError("candidate_promotion_window_package_mismatch")
    lock_directory = paths.models / "candidate-locks"
    lock_directory.mkdir(parents=True, exist_ok=True)
    lock_path = lock_directory / f"{model_version}.json"
    payload: dict[str, Any] = {
        "lock_version": "candidate-lock-v1",
        "candidate_model_version": model_version,
        "run_id": str(report["run_id"]),
        "created_at": (frozen_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "development_data_cutoff": str(report["development_data_cutoff"]),
        "development_data_hash": str(report["development_data_hash"]),
        "dataset_revision": str(report["dataset_revision"]),
        "feature_set_version": str(report["feature_set_version"]),
        "universe_version": str(report["universe_version"]),
        "label_version": str(report.get("label_version", "live-labels-v2")),
        "random_seed": int(report.get("random_seed", 17)),
        "candidate_matrix_hash": str(report["candidate_matrix_hash"]),
        "package_path": str(package_path.resolve()),
        "package_hash": package_hash,
        "report_path": str(report_path.resolve()),
        "report_hash": file_sha256(report_path),
        "candidate_matrix_path": str(candidate_matrix_path.resolve()),
        "candidate_matrix_file_hash": file_sha256(candidate_matrix_path),
        "promotion_window_id": definition.window_id,
        "promotion_window_start": definition.start.isoformat(),
        "promotion_window_end": definition.end.isoformat(),
        "promotion_label_complete_through": (
            definition.label_complete_through.isoformat()
        ),
    }
    payload["lock_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    serialized = json.dumps(payload, indent=2, sort_keys=True)
    if lock_path.is_file():
        existing = json.loads(lock_path.read_text(encoding="utf-8"))
        if existing != payload:
            raise RuntimeError("candidate_lock_conflict")
    else:
        temporary = lock_path.with_suffix(".json.tmp")
        temporary.write_text(serialized, encoding="utf-8")
        os.replace(temporary, lock_path)
    pointer = paths.models / LATEST_LOCK_POINTER
    temporary_pointer = pointer.with_suffix(".json.tmp")
    temporary_pointer.write_text(
        json.dumps(
            {
                "candidate_model_version": model_version,
                "lock_path": str(lock_path.resolve()),
                "lock_hash": payload["lock_hash"],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.replace(temporary_pointer, pointer)
    return payload


__all__ = [
    "LATEST_LOCK_POINTER",
    "create_candidate_lock",
    "file_sha256",
    "latest_candidate_lock",
    "verify_candidate_lock",
]
