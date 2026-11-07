from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from stock_prediction.modeling.m7_trial_protocol import (
    M7DeepTrialRequest,
    M7DeepTrialResult,
    M7DeepTrialWorkerOutput,
    M7TrialArtifact,
    M7TrialCandidateMetadata,
    M7TrialResourceObservation,
)


def _request(**updates: object) -> M7DeepTrialRequest:
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
            "relative_path": "models/runs/run-1/executor/dataset/v5-features.parquet",
            "sha256": "d" * 64,
            "schema_version": "m7-v5-feature-frame-v1",
        },
        "train_sessions": (date(2020, 1, 2), date(2020, 1, 3)),
        "selection_sessions": (date(2020, 2, 3),),
        "calibration_sessions": (date(2020, 3, 2),),
        "outer_test_sessions": (date(2020, 4, 1),),
        "batch_size": 64,
        "device": "cpu",
        "timeout_seconds": 3600.0,
        "max_rss_bytes": 24 * 1024**3,
        "max_private_bytes": 24 * 1024**3,
        "max_gpu_memory_bytes": 10 * 1024**3,
        "dependency_lock_sha256": "e" * 64,
        "code_identity_sha256": "f" * 64,
    }
    payload.update(updates)
    return M7DeepTrialRequest.model_validate(payload, strict=True)


def test_trial_request_hash_round_trips_and_binds_registered_batch() -> None:
    request = _request()

    replay = M7DeepTrialRequest.model_validate_json(
        request.model_dump_json(), strict=True
    )

    assert replay == request
    assert len(request.request_hash) == 64
    assert request.batch_size == 64
    assert request.recipe_id == "candidate-recipe-seed100-targeted-v9"


def test_full_iteration_request_has_no_timeout_and_binds_policy_to_hash() -> None:
    request = _request(timeout_seconds=None, full_iterations=True)
    changed = _request(timeout_seconds=None, full_iterations=False)

    assert request.timeout_seconds is None
    assert request.full_iterations is True
    assert request.request_hash != changed.request_hash


def test_trial_request_can_be_rebound_to_a_fresh_replay_process() -> None:
    request = _request()
    payload = request.model_dump(mode="python", exclude={"request_hash"})
    payload["trial_id"] = f"{request.trial_id}.replay"

    replay = M7DeepTrialRequest.model_validate(payload, strict=True)

    assert replay.trial_id.endswith(".replay")
    assert replay.train_sessions == request.train_sessions
    assert replay.request_hash != request.request_hash


@pytest.mark.parametrize(
    ("updates", "error"),
    [
        (
            {
                "feature_artifact": {
                    "relative_path": "../escape.parquet",
                    "sha256": "d" * 64,
                    "schema_version": "m7-v5-feature-frame-v1",
                }
            },
            "relative_path",
        ),
        ({"recipe_id": "candidate-recipe-seed100-targeted-v4"}, "recipe_id"),
        ({"batch_size": 32}, "batch_size"),
        ({"selection_sessions": (date(2020, 1, 3),)}, "partition_sessions_overlap"),
        ({"train_sessions": (date(2020, 1, 3), date(2020, 1, 2))}, "canonical"),
    ],
)
def test_trial_request_rejects_unsafe_or_identity_changing_inputs(
    updates: dict[str, object], error: str
) -> None:
    with pytest.raises(ValidationError, match=error):
        _request(**updates)


def test_trial_request_rejects_hash_tampering() -> None:
    request = _request()
    payload = request.model_dump(mode="python")
    payload["code_identity_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="request_hash"):
        M7DeepTrialRequest.model_validate(payload, strict=True)


def test_trial_result_requires_atomic_success_artifacts_or_terminal_failure() -> None:
    request = _request()
    resource = M7TrialResourceObservation(
        wall_seconds=12.5,
        cpu_seconds=10.0,
        peak_rss_bytes=1024,
        peak_private_bytes=2048,
        peak_gpu_memory_bytes=0,
        exit_code=0,
    )
    artifacts = tuple(
        M7TrialArtifact(
            role=role,
            relative_path=f"artifacts/{role}.bin",
            sha256=str(index) * 64,
            size_bytes=100 + index,
        )
        for index, role in enumerate(
            ("model", "calibration_predictions", "outer_test_predictions"),
            start=1,
        )
    )
    metadata = M7TrialCandidateMetadata(
        family="dlinear",
        state_hash="4" * 64,
        best_epoch=1,
        selection_macro_brier=0.5,
        promotion_eligible=True,
        ineligibility_reason=None,
        calibration_sample_keys_hash="5" * 64,
        outer_test_sample_keys_hash="6" * 64,
    )
    worker_output = M7DeepTrialWorkerOutput(
        trial_id=request.trial_id,
        request_hash=request.request_hash,
        status="succeeded",
        artifacts=artifacts,
        candidate_metadata=metadata,
        terminal_failure_code=None,
    )

    succeeded = M7DeepTrialResult(
        trial_id=request.trial_id,
        request_hash=request.request_hash,
        status="succeeded",
        artifacts=artifacts,
        candidate_metadata=metadata,
        resource_observation=resource,
        terminal_failure_code=None,
    )
    failed = M7DeepTrialResult(
        trial_id=request.trial_id,
        request_hash=request.request_hash,
        status="failed",
        artifacts=(),
        candidate_metadata=None,
        resource_observation=resource.model_copy(update={"exit_code": 1}),
        terminal_failure_code="m7_deep_timeout",
    )

    assert len(worker_output.output_hash) == 64
    assert len(succeeded.result_hash) == 64
    assert failed.terminal_failure_code == "m7_deep_timeout"
    with pytest.raises(ValidationError, match="success_artifacts"):
        M7DeepTrialResult(
            trial_id=request.trial_id,
            request_hash=request.request_hash,
            status="succeeded",
            artifacts=(),
            candidate_metadata=metadata,
            resource_observation=resource,
            terminal_failure_code=None,
        )
    with pytest.raises(ValidationError, match="failed_artifacts"):
        M7DeepTrialResult(
            trial_id=request.trial_id,
            request_hash=request.request_hash,
            status="failed",
            artifacts=artifacts,
            candidate_metadata=None,
            resource_observation=resource,
            terminal_failure_code="m7_deep_oom",
        )
