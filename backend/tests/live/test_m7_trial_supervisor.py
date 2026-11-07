from __future__ import annotations

from collections.abc import Sequence
from datetime import date
import hashlib
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
import torch

from stock_prediction.features.m7 import (
    M7_CORE_V2_COLUMNS,
    M7_CROSS_SECTION_COLUMNS,
    M7_STRUCTURE_CONTEXT_COLUMNS,
)
from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V9,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_trial_supervisor import M7DeepTrialSupervisor
from stock_prediction.live.m7_trial_supervisor import _publish_trial_directory
from stock_prediction.modeling.m7_recipe import registered_m7_candidate_matrix
from stock_prediction.modeling.report import deterministic_hash
from stock_prediction.modeling.m7_trial_protocol import M7DeepTrialRequest


_STUB = Path(__file__).parents[1] / "fixtures" / "m7_trial_worker_stub.py"


def test_atomic_trial_publish_retries_transient_windows_handle_contention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / ".staging" / "trial"
    final = tmp_path / "trial"
    staging.mkdir(parents=True)
    (staging / "result.json").write_text("{}", encoding="utf-8")
    real_replace = os.replace
    attempts = 0

    def transient_replace(source: Path, target: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("transient scanner handle")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", transient_replace)

    _publish_trial_directory(staging, final)

    assert attempts == 2
    assert (final / "result.json").is_file()


def _request(paths: LivePaths, **updates: object) -> M7DeepTrialRequest:
    feature = (
        paths.models / "runs" / "run-1" / "executor" / "dataset" / "v5-features.parquet"
    )
    feature.parent.mkdir(parents=True, exist_ok=True)
    if not feature.exists():
        feature.write_bytes(b"governed-feature-input")
    payload: dict[str, object] = {
        "trial_id": "run-1.m7-outer-01.m7-dlinear-01",
        "run_id": "run-1",
        "fold_id": "m7-outer-01",
        "recipe_id": "candidate-recipe-seed100-targeted-v9",
        "candidate_matrix_hash": "a" * 64,
        "configuration_id": "m7-dlinear-01",
        "configuration_hash": "b" * 64,
        "partition_hash": "c" * 64,
        "feature_artifact": {
            "relative_path": feature.relative_to(paths.root).as_posix(),
            "sha256": hashlib.sha256(feature.read_bytes()).hexdigest(),
            "schema_version": "m7-v5-feature-frame-v1",
        },
        "train_sessions": (date(2020, 1, 2), date(2020, 1, 3)),
        "selection_sessions": (date(2020, 2, 3),),
        "calibration_sessions": (date(2020, 3, 2),),
        "outer_test_sessions": (date(2020, 4, 1),),
        "batch_size": 64,
        "device": "cpu",
        "timeout_seconds": 5.0,
        "max_rss_bytes": 24 * 1024**3,
        "max_private_bytes": 24 * 1024**3,
        "max_gpu_memory_bytes": 10 * 1024**3,
        "dependency_lock_sha256": "e" * 64,
        "code_identity_sha256": "f" * 64,
    }
    payload.update(updates)
    return M7DeepTrialRequest.model_validate(payload, strict=True)


def _command(mode: str):
    def build(request_path: Path, staging: Path, _artifact_root: Path) -> Sequence[str]:
        return (
            sys.executable,
            str(_STUB),
            "--request",
            str(request_path),
            "--staging",
            str(staging),
            "--mode",
            mode,
        )

    return build


def test_supervisor_accepts_only_complete_hash_verified_worker_output(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    request = _request(paths)

    supervised = M7DeepTrialSupervisor(
        paths,
        command_builder=_command("success"),
        poll_interval_seconds=0.01,
    ).execute(request)

    assert supervised.result.status == "succeeded"
    assert supervised.result.resource_observation.exit_code == 0
    assert supervised.trial_root.is_dir()
    assert (supervised.trial_root / "result.json").is_file()
    assert not supervised.trial_root.name.endswith(".staging")

    resumed = M7DeepTrialSupervisor(
        paths,
        command_builder=_command("abnormal"),
        poll_interval_seconds=0.01,
    ).execute(request)
    assert resumed.result.result_hash == supervised.result.result_hash


def test_supervisor_turns_timeout_abnormal_and_bad_artifact_into_terminal_failure(
    tmp_path: Path,
) -> None:
    cases = (
        ("timeout", "m7_deep_timeout", {"timeout_seconds": 0.05}),
        ("abnormal", "m7_deep_worker_exit_7", {}),
        ("partial", "m7_deep_worker_output_missing", {}),
        ("bad-hash", "m7_deep_worker_artifact_hash_mismatch", {}),
    )
    for index, (mode, expected, updates) in enumerate(cases, start=1):
        workspace = tmp_path / str(index)
        paths = LivePaths.from_workspace(workspace)
        paths.ensure()
        request = _request(
            paths,
            trial_id=f"run-1.m7-outer-01.m7-dlinear-0{index}",
            configuration_id=f"m7-dlinear-0{index}",
            **updates,
        )

        supervised = M7DeepTrialSupervisor(
            paths,
            command_builder=_command(mode),
            poll_interval_seconds=0.01,
        ).execute(request)

        assert supervised.result.status == "failed"
        assert supervised.result.terminal_failure_code == expected
        assert supervised.result.artifacts == ()


def test_supervisor_enforces_child_resource_limit(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    request = _request(
        paths,
        timeout_seconds=5.0,
        max_rss_bytes=1,
        max_private_bytes=1,
    )

    supervised = M7DeepTrialSupervisor(
        paths,
        command_builder=_command("timeout"),
        poll_interval_seconds=0.01,
    ).execute(request)

    assert supervised.result.status == "failed"
    assert supervised.result.terminal_failure_code == "m7_deep_resource_limit"
    assert supervised.result.resource_observation.peak_rss_bytes > 1


def test_supervisor_records_gpu_memory_reported_by_cuda_worker(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()

    supervised = M7DeepTrialSupervisor(
        paths,
        command_builder=_command("peak-gpu"),
        poll_interval_seconds=0.01,
    ).execute(_request(paths, max_gpu_memory_bytes=256))

    assert supervised.result.status == "succeeded"
    assert supervised.result.resource_observation.peak_gpu_memory_bytes == 128


def test_supervisor_sets_deterministic_cublas_environment_for_cuda_worker(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()

    supervised = M7DeepTrialSupervisor(
        paths,
        command_builder=_command("check-cublas"),
        poll_interval_seconds=0.01,
    ).execute(_request(paths, device="cuda"))

    assert supervised.result.status == "succeeded"


def test_supervisor_terminates_owned_child_when_cancellation_is_requested(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    request = _request(paths, timeout_seconds=5.0)

    supervised = M7DeepTrialSupervisor(
        paths,
        command_builder=_command("timeout"),
        poll_interval_seconds=0.01,
        cancellation_requested=lambda: True,
    ).execute(request)

    assert supervised.result.status == "failed"
    assert supervised.result.terminal_failure_code == "m7_deep_cancelled"
    assert supervised.result.artifacts == ()


def test_supervisor_terminates_child_and_publishes_failure_when_resource_probe_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a probe OSError leaking the child and masking the original failure."""

    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    request = _request(paths, timeout_seconds=5.0)

    def fail_probe(_pid: int) -> tuple[int, int, float]:
        raise OSError("resource probe failed")

    monkeypatch.setattr(
        "stock_prediction.live.m7_trial_supervisor._process_tree_resources",
        fail_probe,
    )

    supervised = M7DeepTrialSupervisor(
        paths,
        command_builder=_command("timeout"),
        poll_interval_seconds=0.01,
    ).execute(request)

    assert supervised.result.status == "failed"
    assert supervised.result.terminal_failure_code == "m7_deep_supervision_failed"
    assert supervised.result.resource_observation.exit_code != 0
    assert supervised.trial_root.is_dir()
    assert (supervised.trial_root / "result.json").is_file()


def test_real_worker_executes_one_registered_deep_trial_in_isolation(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    sessions = tuple(value.date() for value in pd.bdate_range("2025-01-02", periods=85))
    symbols = ("US.A", "US.B", "US.C")
    feature_names = tuple(
        dict.fromkeys(
            (
                *M7_CORE_V2_COLUMNS,
                *M7_CROSS_SECTION_COLUMNS,
                *M7_STRUCTURE_CONTEXT_COLUMNS,
            )
        )
    )
    rows: list[dict[str, object]] = []
    for session_index, session in enumerate(sessions):
        for symbol_index, symbol in enumerate(symbols):
            row: dict[str, object] = {
                "symbol": symbol,
                "session_date": session,
                "eligible": True,
            }
            for feature_index, name in enumerate(feature_names):
                row[name] = 0.01 + 0.001 * session_index + 0.0001 * feature_index
            for horizon in (1, 5, 20):
                row[f"direction_{horizon}"] = (-1, 0, 1)[
                    (session_index + symbol_index + horizon) % 3
                ]
            rows.append(row)
    feature = (
        paths.models / "runs" / "run-1" / "executor" / "dataset" / "v5-features.parquet"
    )
    feature.parent.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(feature, index=False)
    matrix = registered_m7_candidate_matrix(
        recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V9
    )
    configuration = next(
        row for row in matrix.configurations if row.configuration_id == "m7-dlinear-01"
    )
    request = _request(
        paths,
        recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V9,
        candidate_matrix_hash=matrix.matrix_hash,
        configuration_hash=deterministic_hash(configuration.canonical_payload()),
        feature_artifact={
            "relative_path": feature.relative_to(paths.root).as_posix(),
            "sha256": hashlib.sha256(feature.read_bytes()).hexdigest(),
            "schema_version": "m7-v5-feature-frame-v1",
        },
        train_sessions=sessions[:70],
        selection_sessions=sessions[70:75],
        calibration_sessions=sessions[75:80],
        outer_test_sessions=sessions[80:85],
        synthetic_epoch_cap=1,
        timeout_seconds=60.0,
    )

    supervised = M7DeepTrialSupervisor(
        paths,
        poll_interval_seconds=0.01,
    ).execute(request)

    assert supervised.result.status == "succeeded"
    assert supervised.result.candidate_metadata is not None
    assert supervised.result.candidate_metadata.promotion_eligible is False
    assert supervised.result.candidate_metadata.ineligibility_reason == (
        "synthetic_epoch_cap"
    )
    calibration_artifact = next(
        row
        for row in supervised.result.artifacts
        if row.role == "calibration_predictions"
    )
    with np.load(supervised.trial_root / calibration_artifact.relative_path) as values:
        assert values["h1"].shape == (15, 3)
        assert values["h5"].shape == (15, 3)
        assert values["h20"].shape == (15, 3)
    model_artifact = next(
        row for row in supervised.result.artifacts if row.role == "model"
    )
    model_payload = torch.load(
        supervised.trial_root / model_artifact.relative_path,
        map_location="cpu",
        weights_only=True,
    )
    assert set(model_payload["feature_medians"]) == set(feature_names)
    assert all(
        np.isfinite(value) for value in model_payload["feature_medians"].values()
    )
