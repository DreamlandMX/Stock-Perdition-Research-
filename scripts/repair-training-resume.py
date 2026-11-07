"""One-time guarded identity migration for the M7 Parquet resume fix."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from filelock import FileLock, Timeout
import torch

from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_bundle_executor import _DEVELOPMENT_CUTOFF
from stock_prediction.live.m7_executor import (
    materialize_m7_development_dataset,
    prepare_m7_feature_data,
)
from stock_prediction.live.training_job import (
    _matches_worker,
    _read_json,
    _select_job,
    _workspace_identity,
    _write_json_atomic,
)
from stock_prediction.modeling.m7_trial_protocol import M7DeepTrialRequest
from stock_prediction.modeling.report import deterministic_hash, file_sha256


_PATCH_ID = "m7-arrow-storage-compare-v1"
_EXPECTED_FAILURE = "M7BundleExecutionError: m7_executor_artifact_resume_mismatch"
_ALLOWED_CHANGE = "backend/src/stock_prediction/live/m7_executor.py"
_MODEL_SOURCES = (
    "backend/src/stock_prediction/features/m7.py",
    "backend/src/stock_prediction/live/m7_direction_executor.py",
    "backend/src/stock_prediction/live/m7_trial_supervisor.py",
    "backend/src/stock_prediction/live/m7_trial_worker.py",
    "backend/src/stock_prediction/modeling/m7_deep_models.py",
    "backend/src/stock_prediction/modeling/m7_recipe.py",
    "backend/src/stock_prediction/modeling/m7_sequences.py",
    "backend/src/stock_prediction/modeling/m7_trial_protocol.py",
)


def repair(workspace: Path, run_id: str, baseline_sha256: str) -> dict[str, Any]:
    workspace = workspace.resolve()
    job_dir, job = _select_job(workspace, run_id)
    baseline_path = job_dir / "resume-repair-baseline.json"
    try:
        baseline_bytes = baseline_path.read_bytes()
        baseline = json.loads(baseline_bytes)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("resume_repair_baseline_invalid") from error
    if hashlib.sha256(baseline_bytes).hexdigest() != baseline_sha256:
        raise RuntimeError("resume_repair_baseline_hash_mismatch")
    if (
        baseline.get("schema_version") != "training-resume-repair-baseline-v1"
        or baseline.get("run_id") != run_id
        or baseline.get("job_identity") != job.get("identity")
    ):
        raise RuntimeError("resume_repair_baseline_identity_mismatch")
    if job.get("state") != "failed" or job.get("detail") != _EXPECTED_FAILURE:
        raise RuntimeError("resume_repair_job_state_invalid")
    if _matches_worker(job):
        raise RuntimeError("resume_repair_worker_running")

    try:
        lock = FileLock(str(job_dir / "worker.lock"), timeout=0)
        with lock:
            current_identity = _workspace_identity(workspace)
            old_identity = baseline["job_identity"]
            if (
                current_identity.get("feature_pointer_sha256")
                != old_identity.get("feature_pointer_sha256")
                or current_identity.get("feature_pointer")
                != old_identity.get("feature_pointer")
            ):
                raise RuntimeError("resume_repair_feature_pointer_changed")
            changed_files = _changed_files(workspace, baseline)
            if changed_files != [_ALLOWED_CHANGE]:
                raise RuntimeError(
                    "resume_repair_unexpected_source_changes:"
                    + ",".join(changed_files)
                )
            if current_identity.get("code_sha256") == old_identity.get("code_sha256"):
                raise RuntimeError("resume_repair_patch_not_applied")

            evidence = _verify_frozen_run(workspace, run_id)
            _verify_dataset_materialization(
                workspace,
                f"m7-{run_id}",
                str(evidence["feature_sha256"]),
            )
            if _workspace_identity(workspace) != current_identity:
                raise RuntimeError("resume_repair_workspace_changed_during_validation")
            latest = _read_json(job_dir / "job.json")
            if latest != job or _matches_worker(latest):
                raise RuntimeError("resume_repair_job_changed_during_validation")

            migrated_at = datetime.now(UTC).isoformat()
            migrations = latest.get("identity_migrations", [])
            if not isinstance(migrations, list):
                raise RuntimeError("resume_repair_migration_history_invalid")
            migration = {
                "patch_id": _PATCH_ID,
                "migrated_at": migrated_at,
                "baseline_sha256": baseline_sha256,
                "old_identity": old_identity,
                "new_identity": current_identity,
                "changed_files": changed_files,
                "checkpoint": evidence,
            }
            latest["identity"] = current_identity
            latest["identity_migrations"] = [*migrations, migration]
            latest["updated_at"] = migrated_at
            _write_json_atomic(job_dir / "job.json", latest)
            return migration
    except Timeout as error:
        raise RuntimeError("resume_repair_worker_lock_busy") from error


def _changed_files(workspace: Path, baseline: dict[str, Any]) -> list[str]:
    saved = baseline.get("files")
    if not isinstance(saved, dict):
        raise RuntimeError("resume_repair_baseline_files_invalid")
    source_root = workspace / "backend/src/stock_prediction"
    paths = list(source_root.rglob("*.py")) if source_root.is_dir() else []
    for relative in (Path("uv.lock"), Path("scripts/model-training.ps1")):
        path = workspace / relative
        if path.is_file():
            paths.append(path)
    current = {
        path.relative_to(workspace).as_posix(): {
            "sha256": file_sha256(path),
            "size": path.stat().st_size,
        }
        for path in paths
    }
    return sorted(
        relative
        for relative in set(saved) | set(current)
        if saved.get(relative) != current.get(relative)
    )


def _verify_frozen_run(workspace: Path, job_run_id: str) -> dict[str, Any]:
    paths = LivePaths.active(workspace)
    m7_run_id = f"m7-{job_run_id}"
    executor = paths.models / "runs" / m7_run_id / "executor"
    requests = list((executor / "deep-trials" / ".staging").glob("*/request.json"))
    if len(requests) != 1:
        raise RuntimeError("resume_repair_staging_request_invalid")
    try:
        request = M7DeepTrialRequest.model_validate_json(
            requests[0].read_text(encoding="utf-8"), strict=True
        )
    except (OSError, ValueError) as error:
        raise RuntimeError("resume_repair_staging_request_invalid") from error
    if (
        request.run_id != m7_run_id
        or not request.full_iterations
        or request.timeout_seconds is not None
        or request.configuration_id != "m7-patchtst-04"
    ):
        raise RuntimeError("resume_repair_staging_request_identity_mismatch")

    feature = (
        paths.root
        / Path(*PurePosixPath(request.feature_artifact.relative_path).parts)
    ).resolve()
    if paths.root.resolve() not in feature.parents or not feature.is_file():
        raise RuntimeError("resume_repair_feature_artifact_missing")
    if file_sha256(feature) != request.feature_artifact.sha256:
        raise RuntimeError("resume_repair_feature_artifact_hash_mismatch")
    lock = workspace / "uv.lock"
    if not lock.is_file() or file_sha256(lock) != request.dependency_lock_sha256:
        raise RuntimeError("resume_repair_dependency_identity_mismatch")
    identities = {
        relative: file_sha256(workspace / Path(*relative.split("/")))
        for relative in _MODEL_SOURCES
    }
    if deterministic_hash(identities) != request.code_identity_sha256:
        raise RuntimeError("resume_repair_model_code_identity_mismatch")

    checkpoint = executor / "deep-checkpoints" / f"{request.trial_id}.pt"
    try:
        payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
        completed_epochs = payload["completed_epochs"]
        max_epochs = payload["config"]["parameters"]["max_epochs"]
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise RuntimeError("resume_repair_checkpoint_invalid") from error
    if (
        payload.get("identity") != request.request_hash
        or completed_epochs != 57
        or max_epochs != 80
    ):
        raise RuntimeError("resume_repair_checkpoint_identity_mismatch")
    return {
        "trial_id": request.trial_id,
        "request_hash": request.request_hash,
        "feature_sha256": request.feature_artifact.sha256,
        "checkpoint_sha256": file_sha256(checkpoint),
        "completed_epochs": completed_epochs,
        "max_epochs": max_epochs,
    }


def _verify_dataset_materialization(
    workspace: Path,
    m7_run_id: str,
    expected_feature_sha256: str,
) -> None:
    paths = LivePaths.active(workspace)
    prepared = prepare_m7_feature_data(paths, development_cutoff=_DEVELOPMENT_CUTOFF)
    materialized = materialize_m7_development_dataset(
        paths,
        run_id=m7_run_id,
        prepared=prepared,
    )
    if materialized.feature_reference.sha256 != expected_feature_sha256:
        raise RuntimeError("resume_repair_materialized_feature_mismatch")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--baseline-sha256", required=True)
    arguments = parser.parse_args()
    result = repair(
        arguments.workspace_root,
        arguments.run_id,
        arguments.baseline_sha256,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
