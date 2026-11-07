"""Supervise one isolated M7 deep trial and atomically accept its evidence."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import time
from typing import Final
from uuid import uuid4

import psutil  # type: ignore[import-untyped]
from pydantic import ValidationError

from stock_prediction.live.config import LivePaths
from stock_prediction.modeling.m7_trial_protocol import (
    M7DeepTrialRequest,
    M7DeepTrialResult,
    M7DeepTrialWorkerOutput,
    M7TrialResourceObservation,
)
from stock_prediction.modeling.report import file_sha256, write_immutable_json


CommandBuilder = Callable[[Path, Path, Path], Sequence[str]]
_WORKER_OUTPUT: Final = "worker-output.json"
_CUBLAS_WORKSPACE_CONFIG: Final = ":4096:8"


class M7TrialSupervisorError(RuntimeError):
    """The parent cannot create or safely publish the isolated trial boundary."""


@dataclass(frozen=True, slots=True)
class M7SupervisedTrial:
    result: M7DeepTrialResult
    trial_root: Path


class M7DeepTrialSupervisor:
    def __init__(
        self,
        paths: LivePaths,
        *,
        command_builder: CommandBuilder | None = None,
        poll_interval_seconds: float = 0.25,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("m7_trial_poll_interval_invalid")
        self.paths = paths
        self.command_builder = command_builder or _default_worker_command
        self.poll_interval_seconds = poll_interval_seconds
        self.cancellation_requested = cancellation_requested or (lambda: False)

    def execute(self, request: M7DeepTrialRequest) -> M7SupervisedTrial:
        self.paths.ensure()
        _verify_feature_input(self.paths.root, request)
        trials_root = (
            self.paths.models
            / "runs"
            / request.run_id
            / "executor"
            / "deep-trials"
        )
        final = trials_root / request.trial_id
        if final.exists():
            existing = _load_completed_trial(final, request)
            if existing is not None:
                return existing
            failed_root = trials_root / ".failed"
            failed_root.mkdir(parents=True, exist_ok=True)
            os.replace(final, failed_root / f"{request.trial_id}-{uuid4().hex}")
        staging_root = trials_root / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        staging = staging_root / f"{request.trial_id}-{uuid4().hex}"
        staging.mkdir()
        request_path = staging / "request.json"
        write_immutable_json(
            request_path,
            request.model_dump(mode="json"),
            pretty=False,
        )
        command = tuple(self.command_builder(request_path, staging, self.paths.root))
        if not command:
            raise M7TrialSupervisorError("m7_trial_worker_command_empty")
        stdout_path = staging / "stdout.log"
        stderr_path = staging / "stderr.log"
        started = time.monotonic()
        peak_rss = 0
        peak_private = 0
        peak_gpu = 0
        cpu_seconds = 0.0
        forced_failure: str | None = None
        process: subprocess.Popen[bytes] | None = None
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        child_environment = os.environ.copy()
        if request.device == "cuda":
            child_environment["CUBLAS_WORKSPACE_CONFIG"] = _CUBLAS_WORKSPACE_CONFIG
        try:
            with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    cwd=Path.cwd(),
                    creationflags=creationflags,
                    env=child_environment,
                )
                while process.poll() is None:
                    rss, private, cpu = _process_tree_resources(process.pid)
                    peak_rss = max(peak_rss, rss)
                    peak_private = max(peak_private, private)
                    cpu_seconds = max(cpu_seconds, cpu)
                    elapsed = time.monotonic() - started
                    if self.cancellation_requested():
                        forced_failure = "m7_deep_cancelled"
                    elif (
                        request.timeout_seconds is not None
                        and elapsed > request.timeout_seconds
                    ):
                        forced_failure = "m7_deep_timeout"
                    elif (
                        peak_rss > request.max_rss_bytes
                        or peak_private > request.max_private_bytes
                        or peak_gpu > request.max_gpu_memory_bytes
                    ):
                        forced_failure = "m7_deep_resource_limit"
                    if forced_failure is not None:
                        _terminate_process_tree(process)
                        break
                    time.sleep(self.poll_interval_seconds)
                exit_code = process.wait(timeout=10)
                rss, private, cpu = _process_tree_resources(process.pid)
                peak_rss = max(peak_rss, rss)
                peak_private = max(peak_private, private)
                cpu_seconds = max(cpu_seconds, cpu)
        except OSError as error:
            if process is None:
                forced_failure = "m7_deep_worker_start_failed"
                exit_code = 1
            else:
                forced_failure = "m7_deep_supervision_failed"
                if process.poll() is None:
                    _terminate_process_tree(process)
                exit_code = process.returncode if process.returncode is not None else 1
            try:
                (staging / "supervisor-error.log").write_text(
                    str(error), encoding="utf-8"
                )
            except OSError:
                pass
        wall_seconds = time.monotonic() - started
        resource = M7TrialResourceObservation(
            wall_seconds=float(wall_seconds),
            cpu_seconds=float(cpu_seconds),
            peak_rss_bytes=peak_rss,
            peak_private_bytes=peak_private,
            peak_gpu_memory_bytes=peak_gpu,
            exit_code=exit_code,
        )
        worker_output: M7DeepTrialWorkerOutput | None = None
        failure = forced_failure
        if failure is None and exit_code != 0:
            failure = (
                f"m7_deep_worker_exit_{exit_code}"
                if exit_code >= 0
                else f"m7_deep_worker_signal_{abs(exit_code)}"
            )
        if failure is None:
            output_path = staging / _WORKER_OUTPUT
            if not output_path.is_file():
                failure = "m7_deep_worker_output_missing"
            else:
                try:
                    worker_output = M7DeepTrialWorkerOutput.model_validate_json(
                        output_path.read_text(encoding="utf-8"), strict=True
                    )
                except (OSError, ValidationError):
                    failure = "m7_deep_worker_output_invalid"
        if failure is None and worker_output is not None:
            if (
                worker_output.trial_id != request.trial_id
                or worker_output.request_hash != request.request_hash
            ):
                failure = "m7_deep_worker_identity_mismatch"
            elif worker_output.status == "failed":
                failure = worker_output.terminal_failure_code
            else:
                failure = _verify_worker_artifacts(staging, worker_output)
        if worker_output is not None:
            peak_gpu = max(peak_gpu, worker_output.peak_gpu_memory_bytes)
            resource = resource.model_copy(
                update={"peak_gpu_memory_bytes": peak_gpu}
            )
            if failure is None and peak_gpu > request.max_gpu_memory_bytes:
                failure = "m7_deep_resource_limit"
        if failure is None and worker_output is not None:
            result = M7DeepTrialResult(
                trial_id=request.trial_id,
                request_hash=request.request_hash,
                status="succeeded",
                artifacts=worker_output.artifacts,
                candidate_metadata=worker_output.candidate_metadata,
                resource_observation=resource,
                terminal_failure_code=None,
            )
        else:
            result = M7DeepTrialResult(
                trial_id=request.trial_id,
                request_hash=request.request_hash,
                status="failed",
                artifacts=(),
                candidate_metadata=None,
                resource_observation=resource,
                terminal_failure_code=failure or "m7_deep_worker_unknown_failure",
            )
        write_immutable_json(
            staging / "result.json",
            result.model_dump(mode="json"),
            pretty=False,
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        _publish_trial_directory(staging, final)
        return M7SupervisedTrial(result=result, trial_root=final)


def _load_completed_trial(
    final: Path, request: M7DeepTrialRequest
) -> M7SupervisedTrial | None:
    result_path = final / "result.json"
    try:
        result = M7DeepTrialResult.model_validate_json(
            result_path.read_text(encoding="utf-8"), strict=True
        )
    except (OSError, ValidationError) as error:
        raise M7TrialSupervisorError("m7_trial_existing_result_invalid") from error
    if result.request_hash != request.request_hash or result.trial_id != request.trial_id:
        raise M7TrialSupervisorError("m7_trial_existing_identity_mismatch")
    if result.status != "succeeded":
        return None
    worker = M7DeepTrialWorkerOutput(
        trial_id=result.trial_id,
        request_hash=result.request_hash,
        status="succeeded",
        artifacts=result.artifacts,
        candidate_metadata=result.candidate_metadata,
        terminal_failure_code=None,
        peak_gpu_memory_bytes=result.resource_observation.peak_gpu_memory_bytes,
    )
    failure = _verify_worker_artifacts(final, worker)
    if failure is not None:
        raise M7TrialSupervisorError(failure)
    return M7SupervisedTrial(result=result, trial_root=final)


def _default_worker_command(
    request_path: Path,
    staging: Path,
    artifact_root: Path,
) -> Sequence[str]:
    return (
        sys.executable,
        "-m",
        "stock_prediction.live.m7_trial_worker",
        "--request",
        str(request_path),
        "--staging",
        str(staging),
        "--artifact-root",
        str(artifact_root),
    )


def _verify_feature_input(root: Path, request: M7DeepTrialRequest) -> None:
    resolved_root = root.resolve()
    feature = (
        resolved_root
        / Path(*PurePosixPath(request.feature_artifact.relative_path).parts)
    ).resolve()
    if resolved_root not in feature.parents or not feature.is_file():
        raise M7TrialSupervisorError("m7_trial_feature_input_unavailable")
    if file_sha256(feature) != request.feature_artifact.sha256:
        raise M7TrialSupervisorError("m7_trial_feature_input_hash_mismatch")


def _verify_worker_artifacts(
    staging: Path,
    output: M7DeepTrialWorkerOutput,
) -> str | None:
    root = staging.resolve()
    for artifact in output.artifacts:
        path = (
            root / Path(*PurePosixPath(artifact.relative_path).parts)
        ).resolve()
        if root not in path.parents or not path.is_file():
            return "m7_deep_worker_artifact_missing"
        if path.stat().st_size != artifact.size_bytes:
            return "m7_deep_worker_artifact_size_mismatch"
        if file_sha256(path) != artifact.sha256:
            return "m7_deep_worker_artifact_hash_mismatch"
    return None


def _process_tree_resources(pid: int) -> tuple[int, int, float]:
    try:
        parent = psutil.Process(pid)
        processes = (parent, *parent.children(recursive=True))
    except psutil.Error:
        return 0, 0, 0.0
    rss = 0
    private = 0
    cpu = 0.0
    for process in processes:
        try:
            memory = process.memory_info()
            full_memory = process.memory_full_info()
            times = process.cpu_times()
        except psutil.Error:
            continue
        rss += int(memory.rss)
        private += int(getattr(full_memory, "private", memory.rss))
        cpu += float(times.user + times.system)
    return rss, private, cpu


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
    except psutil.Error:
        children = []
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.Error:
            pass
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        for child in reversed(children):
            try:
                child.kill()
            except psutil.Error:
                pass
        process.kill()
        process.wait(timeout=5)


def _publish_trial_directory(staging: Path, final: Path) -> None:
    """Retry only transient Windows directory-handle contention."""

    deadline = time.monotonic() + 5.0
    while True:
        try:
            os.replace(staging, final)
            return
        except PermissionError as error:
            if os.name != "nt" or time.monotonic() >= deadline or final.exists():
                raise M7TrialSupervisorError(
                    "m7_trial_atomic_publish_failed"
                ) from error
            time.sleep(0.05)


__all__ = [
    "CommandBuilder",
    "M7DeepTrialSupervisor",
    "M7SupervisedTrial",
    "M7TrialSupervisorError",
]
