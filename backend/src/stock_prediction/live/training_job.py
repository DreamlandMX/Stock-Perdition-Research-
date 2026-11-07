"""Independent, resumable local training job for the main and M7 direction models."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import ctypes
from datetime import UTC, datetime
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile
import threading
from typing import Any, TypeVar, cast
from uuid import uuid4

from filelock import FileLock, Timeout
import psutil  # type: ignore[import-untyped]

from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V10,
    CANDIDATE_RECIPE_UNIVERSE204_V1,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_bundle_executor import M7Seed100BundleExecutor
from stock_prediction.live.m7_direction_executor import (
    M7DirectionExecutionOptions,
    run_m7_direction_development,
)
from stock_prediction.live.targeted_training import M7TargetedTrainingCancelled
from stock_prediction.live.training import LiveModelTrainer, load_latest_training_report
from stock_prediction.contracts.m7_status import IndependentTrainingSummary
from stock_prediction.live.locking import LiveWriterLease
from stock_prediction.operations.registry import LocalModelRegistry
from stock_prediction.modeling.m7_recipe import registered_m7_candidate_matrix
from stock_prediction.modeling.m7_trial_protocol import (
    M7DeepTrialRequest,
    M7DeepTrialResult,
)
from stock_prediction.modeling.report import file_sha256


T = TypeVar("T")
_SOURCE_EXTENSIONS = {".py"}
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_STATE_LOCK = threading.Lock()


class JobCache:
    """Small hash-addressed pickle cache for completed candidate/task boundaries."""

    def __init__(
        self,
        directory: Path,
        *,
        cancellation_check: Callable[[], None] | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cancellation_check = cancellation_check or (lambda: None)
        self.progress_callback = progress_callback or (lambda _detail: None)
        self._lock = threading.Lock()

    def run(self, key: str, operation: Callable[[], T]) -> T:
        self.cancellation_check()
        try:
            value = cast(T, self.get(key))
        except KeyError:
            self.progress_callback(f"started:{key}")
        else:
            self.progress_callback(f"cached:{key}")
            return value
        value = operation()
        self.cancellation_check()
        self.put(key, value)
        self.progress_callback(f"completed:{key}")
        return value

    def get(self, key: str) -> Any:
        path = self._path(key)
        with self._lock:
            if not path.is_file():
                raise KeyError(key)
            try:
                envelope = pickle.loads(path.read_bytes())  # noqa: S301 - local job data
            except (OSError, pickle.PickleError, EOFError) as error:
                raise RuntimeError("training_cache_corrupt") from error
        if not isinstance(envelope, dict) or envelope.get("key") != key:
            raise RuntimeError("training_cache_identity_mismatch")
        value = envelope.get("value")
        digest = envelope.get("value_sha256")
        if (
            not isinstance(value, bytes)
            or not isinstance(digest, str)
            or hashlib.sha256(value).hexdigest() != digest
        ):
            raise RuntimeError("training_cache_integrity_mismatch")
        return pickle.loads(value)  # noqa: S301 - local identity-bound job data

    def put(self, key: str, value: Any) -> None:
        path = self._path(key)
        value_payload = pickle.dumps(value, protocol=5)
        payload = pickle.dumps(
            {
                "key": key,
                "value_sha256": hashlib.sha256(value_payload).hexdigest(),
                "value": value_payload,
            },
            protocol=5,
        )
        with self._lock:
            if path.exists():
                return
            _write_bytes_atomic(path, payload)

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.pkl"


def start_job(workspace: Path) -> dict[str, object]:
    workspace = workspace.resolve()
    jobs = _jobs_root(workspace)
    jobs.mkdir(parents=True, exist_ok=True)
    running = _running_job(workspace)
    if running is not None:
        raise RuntimeError(f"training_job_already_running:{running['run_id']}")
    identity = _workspace_identity(workspace)
    run_id = f"training-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    directory = jobs / run_id
    directory.mkdir()
    payload: dict[str, object] = {
        "schema_version": "model-training-job-v1",
        "run_id": run_id,
        "workspace": str(workspace),
        "state": "starting",
        "phase": "main_model",
        "created_at": _now(),
        "updated_at": _now(),
        "identity": identity,
        "pid": None,
        "process_created_at": None,
        "completed_tasks": 0,
        "failed_tasks": 0,
        "detail": "后台训练进程准备中",
    }
    _write_json_atomic(directory / "job.json", payload)
    return _spawn_worker(workspace, payload, directory)


def resume_job(workspace: Path, run_id: str | None = None) -> dict[str, object]:
    workspace = workspace.resolve()
    directory, payload = _select_job(workspace, run_id)
    current = job_status(workspace, str(payload["run_id"]))
    if current["state"] == "running":
        return current
    if payload.get("state") == "completed":
        raise RuntimeError("training_job_already_completed")
    if payload.get("identity") != _workspace_identity(workspace):
        raise RuntimeError("training_job_identity_changed")
    (directory / "cancel.requested").unlink(missing_ok=True)
    payload.update(
        state="starting",
        updated_at=_now(),
        detail="正在从已验证的任务、候选或 epoch 边界恢复",
    )
    _write_json_atomic(directory / "job.json", payload)
    return _spawn_worker(workspace, payload, directory)


def stop_job(workspace: Path, run_id: str | None = None) -> dict[str, object]:
    directory, payload = _select_job(workspace.resolve(), run_id)
    (directory / "cancel.requested").touch(exist_ok=True)
    payload.update(state="cancelling", updated_at=_now(), detail="已提交安全停止请求")
    _write_json_atomic(directory / "job.json", payload)
    return job_status(workspace.resolve(), str(payload["run_id"]))


def job_status(workspace: Path, run_id: str | None = None) -> dict[str, object]:
    directory, payload = _select_job(workspace.resolve(), run_id)
    live = _matches_worker(payload)
    state = str(payload.get("state"))
    if state in {"starting", "running", "cancelling"} and not live:
        state = "interrupted"
    epoch = _latest_epoch_progress(workspace.resolve(), str(payload["run_id"]))
    return {
        **payload,
        "state": state,
        "process_alive": live,
        "run_directory": str(directory),
        "stdout_log": str(directory / "stdout.log"),
        "stderr_log": str(directory / "stderr.log"),
        **epoch,
    }


def resolve_research_paths(paths: LivePaths, run_id: str | None = None) -> LivePaths:
    """Resolve a completed, pinned research source without changing formal models."""
    pointer_path = paths.root / "status" / "research-source.json"
    pointer = None
    if run_id is None:
        if not pointer_path.exists():
            return paths
        pointer = _read_json(pointer_path)
        run_id = pointer.get("run_id")
    if (
        not isinstance(run_id, str)
        or Path(run_id).name != run_id
        or run_id in {".", ".."}
    ):
        raise RuntimeError("research_source_run_id_invalid")
    workspace = paths.research.parent.parent
    directory, job = _select_job(workspace, run_id)
    if directory.is_symlink() or not directory.resolve().is_relative_to(
        _jobs_root(workspace).resolve()
    ):
        raise RuntimeError("research_source_path_invalid")
    if job.get("state") != "completed" or job.get("phase") != "completed":
        raise RuntimeError("research_source_not_completed")
    for name in ("main", "m7"):
        report_path = directory / f"{name}-report.json"
        if not report_path.is_file():
            raise RuntimeError("research_source_report_missing")
        if pointer is not None and pointer.get(f"{name}_report_sha256") != file_sha256(
            report_path
        ):
            raise RuntimeError("research_source_report_hash_mismatch")
    selected = replace(paths, models=directory / "artifacts" / "main-models")
    if not (selected.models / "registry" / "releases").is_dir():
        raise RuntimeError("research_source_registry_missing")
    if not selected.models.resolve().is_relative_to(directory.resolve()):
        raise RuntimeError("research_source_path_invalid")
    report = load_latest_training_report(selected)
    if (
        report != _read_json(directory / "main-report.json")
        or report.get("run_id") != f"main-{run_id}"
    ):
        raise RuntimeError("research_source_report_identity_invalid")
    job_identity = job.get("identity")
    identity = (
        job_identity.get("feature_pointer") if isinstance(job_identity, dict) else None
    )
    if not isinstance(identity, dict):
        raise RuntimeError("research_source_feature_identity_invalid")
    current = _read_json(paths.features / "LATEST.json")
    if any(
        report.get(key) != identity.get(key) or report.get(key) != current.get(key)
        for key in ("feature_set_version", "universe_version")
    ) or report.get("feature_input_hash") != identity.get("input_hash"):
        raise RuntimeError("research_source_feature_contract_mismatch")
    m7 = _read_json(directory / "m7-report.json")
    if (
        m7.get("run_id") != f"m7-{run_id}"
        or m7.get("full_iterations") is not True
        or m7.get("timeout_seconds") is not None
        or m7.get("candidate_failures") != []
    ):
        raise RuntimeError("research_source_m7_report_invalid")
    registry = LocalModelRegistry(selected.models / "registry")
    for version in [
        report["model_version"],
        *(row["model_version"] for row in report["component_releases"]),
    ]:
        release = registry.verify_release(version)
        if release.source_decision_id != report["run_id"]:
            raise RuntimeError("research_source_release_identity_invalid")
    return selected


def publish_research_job(workspace: Path, run_id: str | None) -> dict[str, object]:
    """Publish research first; update the pinned source only after success."""
    from stock_prediction.live.publication import LiveSnapshotPublisher

    if run_id is None:
        raise RuntimeError("training_job_run_id_required")
    paths = LivePaths.active(workspace.resolve())
    with LiveWriterLease(paths, task_id="publish-training-research", timeout=0):
        selected = resolve_research_paths(paths, run_id)
        directory = selected.models.parent.parent
        result = LiveSnapshotPublisher(paths, research_paths=selected).publish()
        source = {
            "run_id": run_id,
            "main_report_sha256": file_sha256(directory / "main-report.json"),
            "m7_report_sha256": file_sha256(directory / "m7-report.json"),
        }
        previous = paths.root / "status" / "research-source.json"
        if previous.exists() and _read_json(previous) != source:
            _write_bytes_atomic(
                previous.with_name("research-source.previous.json"),
                previous.read_bytes(),
            )
        if not previous.exists() or _read_json(previous) != source:
            _write_json_atomic(previous, source)
        return {**result, "research_training_run_id": run_id}


def independent_training_summary(paths: LivePaths) -> IndependentTrainingSummary | None:
    workspace = paths.research.parent.parent
    pointer_path = paths.root / "status" / "research-source.json"
    if not pointer_path.exists() and not any(_jobs_root(workspace).glob("*/job.json")):
        return None
    run_id = "unknown"
    try:
        pointer = _read_json(pointer_path) if pointer_path.exists() else {}
        requested = pointer.get("run_id")
        directory, job = _select_job(workspace, cast(str | None, requested))
        run_id = str(job["run_id"])
        if requested is not None:
            for name in ("main", "m7"):
                if pointer.get(f"{name}_report_sha256") != file_sha256(
                    directory / f"{name}-report.json"
                ):
                    raise RuntimeError("research_source_report_hash_mismatch")
        main = (
            _read_json(directory / "main-report.json")
            if (directory / "main-report.json").exists()
            else {}
        )
        m7 = (
            _read_json(directory / "m7-report.json")
            if (directory / "m7-report.json").exists()
            else {}
        )
        if job.get("state") == "completed" and (
            main.get("run_id") != f"main-{run_id}" or m7.get("run_id") != f"m7-{run_id}"
        ):
            raise RuntimeError("research_source_report_identity_invalid")
        tasks = cast(list[dict[str, Any]], main.get("tasks", []))
        feature = cast(dict[str, Any], job.get("identity", {})).get(
            "feature_pointer", {}
        )
        gate = cast(dict[str, Any], m7.get("gate", {}))
        return IndependentTrainingSummary.model_validate_json(
            json.dumps(
                {
                    "run_id": run_id,
                    "state": job["state"],
                    "selected_for_research": requested is not None,
                    "completed_at": job.get("completed_at"),
                    "feature_as_of": feature.get("as_of"),
                    "development_data_cutoff": main.get("development_data_cutoff"),
                    "main_model_version": main.get("model_version"),
                    "main_passed_tasks": sum(
                        row.get("gate_passed") is True for row in tasks
                    ),
                    "main_total_tasks": len(tasks),
                    "m7_run_id": m7.get("run_id"),
                    "m7_gate_status": gate.get("status"),
                    "m7_failed_gates": gate.get("failed_gates", []),
                }
            )
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        TypeError,
        AttributeError,
    ) as error:
        return IndependentTrainingSummary(
            run_id=run_id,
            state="unknown",
            source_status="unavailable",
            errors=(str(error),),
        )


def run_worker(workspace: Path, run_id: str) -> None:
    workspace = workspace.resolve()
    directory, payload = _select_job(workspace, run_id)
    lock = FileLock(str(directory / "worker.lock"))
    try:
        lock.acquire(timeout=0)
    except Timeout as error:
        raise RuntimeError("training_worker_already_owned") from error
    _prevent_sleep(True)
    try:
        if payload.get("identity") != _workspace_identity(workspace):
            raise RuntimeError("training_job_identity_changed")
        process = psutil.Process()
        payload.update(
            state="running",
            pid=process.pid,
            process_created_at=process.create_time(),
            updated_at=_now(),
            detail="主模型训练开始",
        )
        _write_json_atomic(directory / "job.json", payload)

        def cancel() -> None:
            _check_cancel(directory)

        def progress(detail: str) -> None:
            _update_progress(directory, detail)

        main_cache = JobCache(
            directory / "cache" / "main",
            cancellation_check=cancel,
            progress_callback=progress,
        )
        if not (directory / "main.completed").exists():
            active = LivePaths.active(workspace)
            main_paths = LivePaths(
                root=active.root,
                raw=active.raw,
                catalog=active.catalog,
                ingestion=active.ingestion,
                features=active.features,
                models=directory / "artifacts" / "main-models",
                research=directory / "artifacts" / "research",
                dataset_revision=active.dataset_revision,
            )
            report = LiveModelTrainer(main_paths, cache=main_cache).train(
                recipe_version=CANDIDATE_RECIPE_UNIVERSE204_V1,
                run_id=f"main-{run_id}",
                progress_callback=lambda stage, percent, detail: _update_progress(
                    directory,
                    detail,
                    phase="main_model",
                    percent=percent,
                    stage=stage,
                ),
            )
            _write_json_atomic(directory / "main-report.json", report)
            (directory / "main.completed").touch()
        cancel()
        _update_progress(directory, "M7 方向模型完整轮次训练开始", phase="m7_direction")
        if not (directory / "m7.completed").exists():
            _run_m7(workspace, directory, run_id)
            (directory / "m7.completed").touch()
        payload = _read_json(directory / "job.json")
        payload.update(
            state="completed",
            phase="completed",
            updated_at=_now(),
            completed_at=_now(),
            detail="主模型与 M7 方向模型全部训练完成；未晋升、未发布",
        )
        _write_json_atomic(directory / "job.json", payload)
    except M7TargetedTrainingCancelled:
        payload = _read_json(directory / "job.json")
        payload.update(state="stopped", updated_at=_now(), detail="已在安全边界停止")
        _write_json_atomic(directory / "job.json", payload)
    except Exception as error:
        payload = _read_json(directory / "job.json")
        failed_tasks = payload.get("failed_tasks", 0)
        payload.update(
            state="failed",
            updated_at=_now(),
            failed_tasks=failed_tasks + 1 if isinstance(failed_tasks, int) else 1,
            detail=f"{type(error).__name__}: {error}",
        )
        _write_json_atomic(directory / "job.json", payload)
        raise
    finally:
        _prevent_sleep(False)
        lock.release()


def _run_m7(workspace: Path, directory: Path, run_id: str) -> None:
    active = LivePaths.active(workspace)
    m7_run_id = f"m7-{run_id}"
    cache = JobCache(
        directory / "cache" / "m7",
        cancellation_check=lambda: _check_cancel(directory),
        progress_callback=lambda detail: _update_progress(
            directory, detail, phase="m7_direction"
        ),
    )
    options = M7DirectionExecutionOptions(
        candidate_timeout_seconds=None,
        full_iterations=True,
        cache=cache,
    )
    adapter = M7Seed100BundleExecutor(
        active,
        run_id=m7_run_id,
        recipe_version=CANDIDATE_RECIPE_SEED100_TARGETED_V10,
        direction_options=options,
        cancellation_requested=lambda: (directory / "cancel.requested").exists(),
        progress_callback=lambda stage, percent, detail, trial_id: _update_progress(
            directory,
            detail,
            phase="m7_direction",
            percent=percent,
            stage=stage,
            trial_id=trial_id,
        ),
    )
    prepared, materialized, splits = adapter._load_context()
    outcome = run_m7_direction_development(
        prepared,
        splits,
        package_identity=f"m7-direction-{m7_run_id}",
        trial_context=adapter._trial_context(materialized),
        options=options,
        cancellation_check=lambda: _check_cancel(directory),
        progress_callback=adapter.progress_callback,
    )
    if outcome.candidate_failures:
        raise RuntimeError(f"m7_candidate_failures:{len(outcome.candidate_failures)}")
    deep = _verify_deep_epochs(active, m7_run_id)
    report = {
        "run_id": m7_run_id,
        "recipe_id": CANDIDATE_RECIPE_SEED100_TARGETED_V10,
        "full_iterations": True,
        "timeout_seconds": None,
        "candidate_count": len(
            registered_m7_candidate_matrix(
                recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V10
            ).configurations
        ),
        "deep_trials": deep,
        "gate": outcome.gate.model_dump(mode="json"),
        "candidate_failures": [],
        "completed_at": _now(),
        "published": False,
        "promoted": False,
    }
    _write_json_atomic(directory / "m7-report.json", report)


def _verify_deep_epochs(paths: LivePaths, run_id: str) -> int:
    root = paths.models / "runs" / run_id / "executor" / "deep-trials"
    count = 0
    for request_path in root.glob("*/request.json"):
        request = M7DeepTrialRequest.model_validate_json(
            request_path.read_text(encoding="utf-8"), strict=True
        )
        result = M7DeepTrialResult.model_validate_json(
            (request_path.parent / "result.json").read_text(encoding="utf-8"),
            strict=True,
        )
        if result.status != "succeeded" or result.candidate_metadata is None:
            raise RuntimeError("m7_deep_trial_incomplete")
        configuration = next(
            row
            for row in registered_m7_candidate_matrix(
                recipe_id=request.recipe_id
            ).configurations
            if row.configuration_id == request.configuration_id
        )
        expected = int(configuration.parameters["max_epochs"])
        if result.candidate_metadata.completed_epochs != expected:
            raise RuntimeError("m7_deep_epochs_incomplete")
        count += 1
    if count == 0:
        raise RuntimeError("m7_deep_trials_missing")
    return count


def _spawn_worker(
    workspace: Path, payload: dict[str, object], directory: Path
) -> dict[str, object]:
    command = [
        sys.executable,
        "-u",
        "-m",
        "stock_prediction.live.training_job",
        "Worker",
        "--workspace-root",
        str(workspace),
        "--run-id",
        str(payload["run_id"]),
    ]
    pid = _launch_worker(
        command,
        cwd=workspace,
        stdout_path=directory / "stdout.log",
        stderr_path=directory / "stderr.log",
    )
    identity = _process_identity(pid)
    payload.update(
        state="running",
        pid=pid,
        process_created_at=(identity or {}).get("create_time"),
        updated_at=_now(),
        detail="后台训练进程已启动",
    )
    _write_json_atomic(directory / "job.json", payload)
    return job_status(workspace, str(payload["run_id"]))


def _launch_worker(
    command: list[str], *, cwd: Path, stdout_path: Path, stderr_path: Path
) -> int:
    flags = 0
    startupinfo = None
    if os.name == "nt":
        flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
            startupinfo=startupinfo,
            close_fds=True,
        )
    return process.pid


def _running_job(workspace: Path) -> dict[str, object] | None:
    for path in sorted(_jobs_root(workspace).glob("*/job.json"), reverse=True):
        try:
            payload = _read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if _matches_worker(payload):
            return payload
    return None


def _matches_worker(payload: dict[str, object]) -> bool:
    pid = payload.get("pid")
    if not isinstance(pid, int):
        return False
    identity = _process_identity(pid)
    if identity is None:
        return False
    expected_created = payload.get("process_created_at")
    if (
        expected_created is not None
        and abs(float(cast(Any, expected_created)) - identity["create_time"]) > 0.01
    ):
        return False
    return str(payload.get("run_id")) in " ".join(identity["cmdline"])


def _process_identity(pid: int) -> dict[str, Any] | None:
    try:
        process = psutil.Process(pid)
        return {"create_time": process.create_time(), "cmdline": process.cmdline()}
    except psutil.Error:
        return None


def _workspace_identity(workspace: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    source_root = workspace / "backend" / "src" / "stock_prediction"
    for path in sorted(source_root.rglob("*.py")) if source_root.is_dir() else ():
        digest.update(path.relative_to(workspace).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    for relative in (Path("uv.lock"), Path("scripts/model-training.ps1")):
        path = workspace / relative
        if path.is_file():
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    pointer = LivePaths.active(workspace).features / "LATEST.json"
    if not pointer.is_file():
        raise RuntimeError("training_feature_pointer_missing")
    return {
        "code_sha256": digest.hexdigest(),
        "feature_pointer_sha256": file_sha256(pointer),
        "feature_pointer": json.loads(pointer.read_text(encoding="utf-8")),
    }


def _select_job(workspace: Path, run_id: str | None) -> tuple[Path, dict[str, object]]:
    jobs = _jobs_root(workspace)
    if run_id is not None:
        directory = jobs / run_id
        if directory.parent != jobs or not directory.is_dir():
            raise RuntimeError("training_job_not_found")
    else:
        candidates = sorted(
            (path.parent for path in jobs.glob("*/job.json")),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise RuntimeError("training_job_not_found")
        directory = candidates[0]
    payload = _read_json(directory / "job.json")
    if payload.get("run_id") != directory.name:
        raise RuntimeError("training_job_identity_invalid")
    return directory, payload


def _update_progress(
    directory: Path,
    detail: str,
    *,
    phase: str | None = None,
    percent: int | None = None,
    stage: str | None = None,
    trial_id: str | None = None,
) -> None:
    with _STATE_LOCK:
        payload = _read_json(directory / "job.json")
        if phase is not None:
            payload["phase"] = phase
        if percent is not None:
            payload["progress_percent"] = percent
        if stage is not None:
            payload["stage"] = stage
        payload["trial_id"] = trial_id
        payload["detail"] = detail
        payload["updated_at"] = _now()
        if detail.startswith("completed:") or detail.startswith("cached:"):
            current = payload.get("completed_tasks", 0)
            payload["completed_tasks"] = current + 1 if isinstance(current, int) else 1
        _write_json_atomic(directory / "job.json", payload)


def _latest_epoch_progress(workspace: Path, run_id: str) -> dict[str, object]:
    checkpoint_root = (
        LivePaths.active(workspace).models
        / "runs"
        / f"m7-{run_id}"
        / "executor"
        / "deep-checkpoints"
    )
    checkpoints = sorted(
        checkpoint_root.glob("*.pt"), key=lambda path: path.stat().st_mtime
    )
    if not checkpoints:
        return {"completed_epoch": None, "total_epochs": None}
    try:
        import torch

        payload = torch.load(checkpoints[-1], map_location="cpu", weights_only=True)
        return {
            "completed_epoch": int(payload["completed_epochs"]),
            "total_epochs": int(payload["config"]["parameters"]["max_epochs"]),
            "epoch_checkpoint": str(checkpoints[-1]),
        }
    except (OSError, KeyError, TypeError, ValueError):
        return {"completed_epoch": None, "total_epochs": None}


def _check_cancel(directory: Path) -> None:
    if (directory / "cancel.requested").exists():
        raise M7TargetedTrainingCancelled("training_job_cancelled")


def _jobs_root(workspace: Path) -> Path:
    return workspace / "var" / "training-jobs"


def _prevent_sleep(enable: bool) -> None:
    if os.name != "nt":
        return
    value = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED if enable else _ES_CONTINUOUS
    ctypes.windll.kernel32.SetThreadExecutionState(value)


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("training_job_state_invalid")
    return value


def _write_json_atomic(path: Path, payload: object) -> None:
    data = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n"
    ).encode("utf-8")
    _write_bytes_atomic(path, data)


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="独立后台完整模型训练")
    parser.add_argument(
        "action",
        choices=("Start", "Status", "Resume", "Stop", "Worker", "PublishResearch"),
    )
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        cast(Any, sys.stdout).reconfigure(encoding="utf-8")
        cast(Any, sys.stderr).reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    try:
        if args.action == "Start":
            result = start_job(args.workspace_root)
        elif args.action == "Status":
            result = job_status(args.workspace_root, args.run_id)
        elif args.action == "Resume":
            result = resume_job(args.workspace_root, args.run_id)
        elif args.action == "Stop":
            result = stop_job(args.workspace_root, args.run_id)
        elif args.action == "PublishResearch":
            result = publish_research_job(args.workspace_root, args.run_id)
        else:
            if not args.run_id:
                raise RuntimeError("training_job_run_id_required")
            run_worker(args.workspace_root, args.run_id)
            result = job_status(args.workspace_root, args.run_id)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception as error:
        print(f"error_code={error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
