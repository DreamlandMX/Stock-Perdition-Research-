"""Child-process entry point for exactly one registered M7 deep trial."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
import sys
from typing import Literal, cast

import numpy as np
import pandas as pd
import torch
from pydantic import ValidationError

from stock_prediction.features.m7 import (
    M7_CORE_V2_COLUMNS,
    M7_CROSS_SECTION_COLUMNS,
    M7_STRUCTURE_CONTEXT_COLUMNS,
)
from stock_prediction.live.m7_direction_executor import (
    M7DirectionExecutionError,
    _frame_keys,
    _make_sequence_batch,
    _numeric_features,
    _partition_frame,
)
from stock_prediction.modeling.contracts import hash_m7_sample_keys
from stock_prediction.modeling.m7_deep_models import (
    M7DeepModelTerminalError,
    fit_m7_deep_candidate,
    predict_m7_deep_candidate,
)
from stock_prediction.modeling.m7_recipe import registered_m7_candidate_matrix
from stock_prediction.modeling.m7_trial_protocol import (
    M7DeepTrialRequest,
    M7DeepTrialWorkerOutput,
    M7TrialArtifact,
    M7TrialCandidateMetadata,
)
from stock_prediction.modeling.report import (
    deterministic_hash,
    file_sha256,
    write_immutable_json,
)


def execute_m7_deep_trial(
    *,
    request_path: Path,
    staging: Path,
    artifact_root: Path,
) -> M7DeepTrialWorkerOutput:
    request_file = request_path.resolve()
    trial_root = staging.resolve()
    root = artifact_root.resolve()
    if request_file.parent != trial_root or not request_file.is_file():
        raise RuntimeError("m7_deep_worker_request_path_invalid")
    if not root.is_dir() or trial_root == root:
        raise RuntimeError("m7_deep_worker_root_invalid")
    try:
        request = M7DeepTrialRequest.model_validate_json(
            request_file.read_text(encoding="utf-8"), strict=True
        )
    except (OSError, ValidationError) as error:
        raise RuntimeError("m7_deep_worker_request_invalid") from error
    try:
        return _execute_registered_request(request, trial_root=trial_root, root=root)
    except (M7DeepModelTerminalError, M7DirectionExecutionError) as error:
        output = M7DeepTrialWorkerOutput(
            trial_id=request.trial_id,
            request_hash=request.request_hash,
            status="failed",
            artifacts=(),
            candidate_metadata=None,
            terminal_failure_code=_terminal_failure_code(error),
        )
        _write_worker_output(trial_root, output)
        return output
    except MemoryError:
        output = M7DeepTrialWorkerOutput(
            trial_id=request.trial_id,
            request_hash=request.request_hash,
            status="failed",
            artifacts=(),
            candidate_metadata=None,
            terminal_failure_code="m7_deep_oom",
        )
        _write_worker_output(trial_root, output)
        return output


def _execute_registered_request(
    request: M7DeepTrialRequest,
    *,
    trial_root: Path,
    root: Path,
) -> M7DeepTrialWorkerOutput:
    matrix = registered_m7_candidate_matrix(recipe_id=request.recipe_id)
    if matrix.matrix_hash != request.candidate_matrix_hash:
        raise M7DeepModelTerminalError("m7_deep_candidate_matrix_hash_mismatch")
    try:
        configuration = next(
            row
            for row in matrix.configurations
            if row.configuration_id == request.configuration_id
        )
    except StopIteration as error:
        raise M7DeepModelTerminalError("m7_deep_configuration_unregistered") from error
    if configuration.family not in {"dlinear", "patchtst"}:
        raise M7DeepModelTerminalError("m7_deep_family_mismatch")
    if deterministic_hash(configuration.canonical_payload()) != (
        request.configuration_hash
    ):
        raise M7DeepModelTerminalError("m7_deep_configuration_hash_mismatch")
    if (
        configuration.parameters.get("batch_size") != request.batch_size
        or configuration.parameters.get("execution_policy") != "isolated_child_v1"
    ):
        raise M7DeepModelTerminalError("m7_deep_execution_identity_mismatch")
    feature_path = (
        root / Path(*PurePosixPath(request.feature_artifact.relative_path).parts)
    ).resolve()
    if root not in feature_path.parents or not feature_path.is_file():
        raise M7DeepModelTerminalError("m7_deep_feature_artifact_unavailable")
    if file_sha256(feature_path) != request.feature_artifact.sha256:
        raise M7DeepModelTerminalError("m7_deep_feature_artifact_hash_mismatch")
    frame = pd.read_parquet(feature_path)
    required = {
        "symbol",
        "session_date",
        "eligible",
        *(f"direction_{horizon}" for horizon in (1, 5, 20)),
    }
    if required - set(frame.columns):
        raise M7DeepModelTerminalError("m7_deep_feature_columns_missing")
    frame["session_date"] = pd.to_datetime(frame["session_date"]).dt.date
    feature_names = _numeric_features(
        frame,
        (
            *M7_CORE_V2_COLUMNS,
            *M7_CROSS_SECTION_COLUMNS,
            *M7_STRUCTURE_CONTEXT_COLUMNS,
        ),
    )
    if not feature_names:
        raise M7DeepModelTerminalError("m7_deep_numeric_features_missing")
    partitions = {
        "train": _partition_frame(frame, request.train_sessions),
        "selection": _partition_frame(frame, request.selection_sessions),
        "calibration": _partition_frame(frame, request.calibration_sessions),
        "test": _partition_frame(frame, request.outer_test_sessions),
    }
    medians = partitions["train"].loc[:, feature_names].median().fillna(0.0)
    transformed = frame.copy()
    transformed.loc[:, feature_names] = (
        transformed.loc[:, feature_names].fillna(medians).fillna(0.0)
    )
    sequence_length = int(configuration.parameters["sequence_length"])
    batches = {
        name: _make_sequence_batch(
            transformed,
            partition,
            feature_names=feature_names,
            sequence_length=sequence_length,
            weighted=name == "train",
            trim_insufficient_history=name == "train",
        )
        for name, partition in partitions.items()
    }
    if request.device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    candidate = fit_m7_deep_candidate(
        configuration_id=configuration.configuration_id,
        train_batch=batches["train"],
        selection_batch=batches["selection"],
        synthetic_epoch_cap=request.synthetic_epoch_cap,
        timeout_seconds=request.timeout_seconds,
        device=request.device,
        batch_size=request.batch_size,
        recipe_id=request.recipe_id,
        full_iterations=request.full_iterations,
        checkpoint_path=(
            root
            / "models"
            / "runs"
            / request.run_id
            / "executor"
            / "deep-checkpoints"
            / f"{request.trial_id}.pt"
        ),
        checkpoint_identity=request.request_hash,
    )
    calibration = predict_m7_deep_candidate(candidate, batches["calibration"])
    outer_test = predict_m7_deep_candidate(candidate, batches["test"])
    artifact_root = trial_root / "artifacts"
    artifact_root.mkdir()
    model_path = artifact_root / "model.pt"
    with model_path.open("xb") as stream:
        torch.save(
            {
                "schema_version": "m7-deep-model-state-v1",
                "recipe_id": request.recipe_id,
                "candidate_matrix_hash": request.candidate_matrix_hash,
                "configuration": configuration.canonical_payload(),
                "configuration_hash": request.configuration_hash,
                "partition_hash": request.partition_hash,
                "feature_names": candidate.feature_names,
                "feature_medians": {
                    str(name): float(value) for name, value in medians.items()
                },
                "sequence_length": candidate.sequence_length,
                "best_epoch": candidate.best_epoch,
                "completed_epochs": candidate.completed_epochs,
                "selection_macro_brier": candidate.selection_macro_brier,
                "state_hash": candidate.state_hash,
                "state_dict": candidate.model.state_dict(),
                "created_at": datetime.now(UTC).isoformat(),
            },
            stream,
        )
    calibration_path = artifact_root / "calibration-predictions.npz"
    outer_test_path = artifact_root / "outer-test-predictions.npz"
    _write_predictions(calibration_path, calibration)
    _write_predictions(outer_test_path, outer_test)
    artifacts = (
        _artifact("model", model_path, trial_root),
        _artifact("calibration_predictions", calibration_path, trial_root),
        _artifact("outer_test_predictions", outer_test_path, trial_root),
    )
    output = M7DeepTrialWorkerOutput(
        trial_id=request.trial_id,
        request_hash=request.request_hash,
        status="succeeded",
        artifacts=artifacts,
        candidate_metadata=M7TrialCandidateMetadata(
            family=configuration.family,
            state_hash=candidate.state_hash,
            best_epoch=candidate.best_epoch,
            completed_epochs=candidate.completed_epochs,
            selection_macro_brier=candidate.selection_macro_brier,
            promotion_eligible=candidate.promotion_eligible,
            ineligibility_reason=candidate.ineligibility_reason,
            calibration_sample_keys_hash=hash_m7_sample_keys(
                _frame_keys(partitions["calibration"], 1)
            ),
            outer_test_sample_keys_hash=hash_m7_sample_keys(
                _frame_keys(partitions["test"], 1)
            ),
        ),
        terminal_failure_code=None,
        peak_gpu_memory_bytes=(
            int(torch.cuda.max_memory_allocated())
            if request.device == "cuda"
            else 0
        ),
    )
    _write_worker_output(trial_root, output)
    return output


def _write_predictions(path: Path, predictions: object) -> None:
    values = cast(dict[int, np.ndarray], predictions)
    with path.open("xb") as stream:
        np.savez_compressed(
            stream,
            h1=np.asarray(values[1], dtype=np.float64),
            h5=np.asarray(values[5], dtype=np.float64),
            h20=np.asarray(values[20], dtype=np.float64),
        )


def _artifact(role: str, path: Path, root: Path) -> M7TrialArtifact:
    typed_role = cast(
        Literal["model", "calibration_predictions", "outer_test_predictions"],
        role,
    )
    return M7TrialArtifact(
        role=typed_role,
        relative_path=path.relative_to(root).as_posix(),
        sha256=file_sha256(path),
        size_bytes=path.stat().st_size,
    )


def _write_worker_output(root: Path, output: M7DeepTrialWorkerOutput) -> None:
    write_immutable_json(
        root / "worker-output.json",
        output.model_dump(mode="json"),
        pretty=False,
    )


def _terminal_failure_code(error: Exception) -> str:
    value = str(error)
    if value.startswith("m7_") and all(
        character.islower() or character.isdigit() or character in "._:-"
        for character in value
    ):
        return value[:256]
    return "m7_deep_terminal_failure"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--staging", required=True, type=Path)
    parser.add_argument("--artifact-root", required=True, type=Path)
    args = parser.parse_args(argv)
    execute_m7_deep_trial(
        request_path=args.request,
        staging=args.staging,
        artifact_root=args.artifact_root,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["execute_m7_deep_trial", "main"]
