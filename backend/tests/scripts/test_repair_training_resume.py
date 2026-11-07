from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "repair-training-resume.py"
)
RUN_ID = "training-20260921T190600Z-30e27d55"
OLD_IDENTITY = {
    "code_sha256": "1" * 64,
    "feature_pointer_sha256": "2" * 64,
    "feature_pointer": {"input_hash": "3" * 64},
}
NEW_IDENTITY = {**OLD_IDENTITY, "code_sha256": "4" * 64}


def _load_repair() -> object:
    assert SCRIPT_PATH.is_file(), "resume repair script is missing"
    spec = importlib.util.spec_from_file_location("training_resume_repair_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture(workspace: Path) -> tuple[Path, str]:
    source = workspace / "backend/src/stock_prediction/live/m7_executor.py"
    source.parent.mkdir(parents=True)
    source.write_text("old\n", encoding="utf-8")
    job_dir = workspace / "var/training-jobs" / RUN_ID
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "run_id": RUN_ID,
                "state": "failed",
                "detail": "M7BundleExecutionError: m7_executor_artifact_resume_mismatch",
                "identity": OLD_IDENTITY,
                "failed_tasks": 1,
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "schema_version": "training-resume-repair-baseline-v1",
        "run_id": RUN_ID,
        "job_identity": OLD_IDENTITY,
        "files": {
            source.relative_to(workspace).as_posix(): {
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "size": source.stat().st_size,
            }
        },
    }
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    baseline = job_dir / "resume-repair-baseline.json"
    baseline.write_bytes(data)
    source.write_text("fixed\n", encoding="utf-8")
    return baseline, hashlib.sha256(data).hexdigest()


def test_repair_updates_only_identity_and_keeps_failure_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repair = _load_repair()
    _, baseline_sha = _fixture(tmp_path)
    monkeypatch.setattr(repair, "_workspace_identity", lambda _workspace: NEW_IDENTITY)
    monkeypatch.setattr(repair, "_matches_worker", lambda _payload: False)
    monkeypatch.setattr(
        repair,
        "_verify_frozen_run",
        lambda *_args: {"completed_epochs": 57, "feature_sha256": "5" * 64},
    )
    monkeypatch.setattr(repair, "_verify_dataset_materialization", lambda *_args: None)

    result = repair.repair(tmp_path, RUN_ID, baseline_sha)

    job = json.loads(
        (tmp_path / "var/training-jobs" / RUN_ID / "job.json").read_text("utf-8")
    )
    assert result["changed_files"] == [
        "backend/src/stock_prediction/live/m7_executor.py"
    ]
    assert job["identity"] == NEW_IDENTITY
    assert job["state"] == "failed"
    assert job["failed_tasks"] == 1
    assert job["identity_migrations"][0]["old_identity"] == OLD_IDENTITY


def test_repair_rejects_extra_source_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repair = _load_repair()
    _, baseline_sha = _fixture(tmp_path)
    extra = tmp_path / "backend/src/stock_prediction/live/other.py"
    extra.write_text("changed\n", encoding="utf-8")
    monkeypatch.setattr(repair, "_workspace_identity", lambda _workspace: NEW_IDENTITY)
    monkeypatch.setattr(repair, "_matches_worker", lambda _payload: False)

    with pytest.raises(RuntimeError, match="resume_repair_unexpected_source_changes"):
        repair.repair(tmp_path, RUN_ID, baseline_sha)


def test_repair_rejects_feature_pointer_change_and_wrong_run_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repair = _load_repair()
    _, baseline_sha = _fixture(tmp_path)
    changed_pointer = {**NEW_IDENTITY, "feature_pointer_sha256": "9" * 64}
    monkeypatch.setattr(repair, "_workspace_identity", lambda _workspace: changed_pointer)
    monkeypatch.setattr(repair, "_matches_worker", lambda _payload: False)

    with pytest.raises(RuntimeError, match="resume_repair_feature_pointer_changed"):
        repair.repair(tmp_path, RUN_ID, baseline_sha)
    with pytest.raises(RuntimeError, match="training_job_not_found"):
        repair.repair(tmp_path, "training-missing", baseline_sha)


def test_frozen_run_verification_rejects_checkpoint_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repair = _load_repair()
    m7_run_id = f"m7-{RUN_ID}"
    executor = tmp_path / "var/live/models/runs" / m7_run_id / "executor"
    request_path = executor / "deep-trials/.staging/trial/request.json"
    request_path.parent.mkdir(parents=True)
    request_path.write_text("{}", encoding="utf-8")
    feature = tmp_path / "var/live/models/runs" / m7_run_id / "feature.parquet"
    feature.parent.mkdir(parents=True, exist_ok=True)
    feature.write_bytes(b"feature")
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"lock")
    for relative in repair._MODEL_SOURCES:
        source = tmp_path / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(relative, encoding="utf-8")
    code_identity = repair.deterministic_hash(
        {
            relative: repair.file_sha256(tmp_path / relative)
            for relative in repair._MODEL_SOURCES
        }
    )
    request = SimpleNamespace(
        run_id=m7_run_id,
        full_iterations=True,
        timeout_seconds=None,
        configuration_id="m7-patchtst-04",
        feature_artifact=SimpleNamespace(
            relative_path=f"models/runs/{m7_run_id}/feature.parquet",
            sha256=repair.file_sha256(feature),
        ),
        dependency_lock_sha256=repair.file_sha256(lock),
        code_identity_sha256=code_identity,
        trial_id=f"{m7_run_id}.m7-outer-01.m7-patchtst-04",
        request_hash="6" * 64,
    )
    checkpoint = executor / "deep-checkpoints" / f"{request.trial_id}.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"checkpoint")
    monkeypatch.setattr(
        repair,
        "M7DeepTrialRequest",
        SimpleNamespace(model_validate_json=lambda *_args, **_kwargs: request),
    )
    monkeypatch.setattr(
        repair.torch,
        "load",
        lambda *_args, **_kwargs: {
            "identity": "7" * 64,
            "completed_epochs": 57,
            "config": {"parameters": {"max_epochs": 80}},
        },
    )

    with pytest.raises(RuntimeError, match="resume_repair_checkpoint_identity_mismatch"):
        repair._verify_frozen_run(tmp_path, RUN_ID)
