"""Strict immutable request and result contracts for isolated M7 deep trials."""

from __future__ import annotations

from datetime import date
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_prediction.modeling.report import deterministic_hash


_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$"
_SHA256 = r"^[0-9a-f]{64}$"
_FAILURE_CODE = r"^[a-z0-9][a-z0-9._:-]{0,255}$"
_REQUIRED_SUCCESS_ROLES = {
    "model",
    "calibration_predictions",
    "outer_test_predictions",
}


class M7TrialArtifactReference(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    relative_path: str
    sha256: str = Field(pattern=_SHA256)
    schema_version: str = Field(pattern=_IDENTIFIER)

    @model_validator(mode="after")
    def validate_relative_path(self) -> Self:
        _require_safe_relative_path(self.relative_path, field="relative_path")
        return self


class M7DeepTrialRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["m7-deep-trial-request-v1"] = "m7-deep-trial-request-v1"
    trial_id: str = Field(pattern=_IDENTIFIER)
    run_id: str = Field(pattern=_IDENTIFIER)
    fold_id: str = Field(pattern=_IDENTIFIER)
    recipe_id: Literal[
        "candidate-recipe-seed100-targeted-v9",
        "candidate-recipe-seed100-targeted-v10",
    ]
    candidate_matrix_hash: str = Field(pattern=_SHA256)
    configuration_id: str = Field(pattern=r"^m7-(dlinear|patchtst)-[0-9]{2}$")
    configuration_hash: str = Field(pattern=_SHA256)
    partition_hash: str = Field(pattern=_SHA256)
    feature_artifact: M7TrialArtifactReference
    train_sessions: tuple[date, ...] = Field(min_length=1)
    selection_sessions: tuple[date, ...] = Field(min_length=1)
    calibration_sessions: tuple[date, ...] = Field(min_length=1)
    outer_test_sessions: tuple[date, ...] = Field(min_length=1)
    batch_size: Literal[64]
    device: Literal["cpu", "cuda"]
    synthetic_epoch_cap: int | None = Field(default=None, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0, le=24 * 60 * 60)
    full_iterations: bool = False
    max_rss_bytes: int = Field(gt=0, le=24 * 1024**3)
    max_private_bytes: int = Field(gt=0, le=24 * 1024**3)
    max_gpu_memory_bytes: int = Field(ge=0, le=10 * 1024**3)
    dependency_lock_sha256: str = Field(pattern=_SHA256)
    code_identity_sha256: str = Field(pattern=_SHA256)
    request_hash: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity_and_partitions(self) -> Self:
        feature_parts = PurePosixPath(self.feature_artifact.relative_path).parts
        if feature_parts[:3] != ("models", "runs", self.run_id):
            raise ValueError("m7_trial_feature_path_run_identity_mismatch")
        partitions = (
            self.train_sessions,
            self.selection_sessions,
            self.calibration_sessions,
            self.outer_test_sessions,
        )
        if any(tuple(sorted(set(values))) != values for values in partitions):
            raise ValueError("m7_trial_partition_sessions_not_canonical")
        seen: set[date] = set()
        for values in partitions:
            if seen & set(values):
                raise ValueError("m7_trial_partition_sessions_overlap")
            seen.update(values)
        if not (
            max(self.train_sessions)
            < min(self.selection_sessions)
            < min(self.calibration_sessions)
            < min(self.outer_test_sessions)
        ):
            raise ValueError("m7_trial_partition_order_invalid")
        expected = deterministic_hash(
            self.model_dump(mode="json", exclude={"request_hash"})
        )
        if self.request_hash and self.request_hash != expected:
            raise ValueError("m7_trial_request_hash_invalid")
        object.__setattr__(self, "request_hash", expected)
        return self


class M7TrialResourceObservation(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    wall_seconds: float = Field(ge=0)
    cpu_seconds: float = Field(ge=0)
    peak_rss_bytes: int = Field(ge=0)
    peak_private_bytes: int = Field(ge=0)
    peak_gpu_memory_bytes: int = Field(ge=0)
    exit_code: int


M7TrialArtifactRole = Literal[
    "model",
    "calibration_predictions",
    "outer_test_predictions",
]


class M7TrialArtifact(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    role: M7TrialArtifactRole
    relative_path: str
    sha256: str = Field(pattern=_SHA256)
    size_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_relative_path(self) -> Self:
        parts = _require_safe_relative_path(self.relative_path, field="artifact_path")
        if parts[0] != "artifacts":
            raise ValueError("m7_trial_artifact_path_invalid")
        return self


class M7TrialCandidateMetadata(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    family: Literal["dlinear", "patchtst"]
    state_hash: str = Field(pattern=_SHA256)
    best_epoch: int = Field(ge=1)
    completed_epochs: int | None = Field(default=None, ge=1)
    selection_macro_brier: float = Field(ge=0)
    promotion_eligible: bool
    ineligibility_reason: str | None = Field(default=None, pattern=_FAILURE_CODE)
    calibration_sample_keys_hash: str = Field(pattern=_SHA256)
    outer_test_sample_keys_hash: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def validate_eligibility_reason(self) -> Self:
        if self.promotion_eligible == (self.ineligibility_reason is not None):
            raise ValueError("m7_trial_candidate_eligibility_reason_invalid")
        return self


class M7DeepTrialWorkerOutput(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["m7-deep-trial-worker-output-v1"] = (
        "m7-deep-trial-worker-output-v1"
    )
    trial_id: str = Field(pattern=_IDENTIFIER)
    request_hash: str = Field(pattern=_SHA256)
    status: Literal["succeeded", "failed"]
    artifacts: tuple[M7TrialArtifact, ...]
    candidate_metadata: M7TrialCandidateMetadata | None
    terminal_failure_code: str | None = Field(default=None, pattern=_FAILURE_CODE)
    peak_gpu_memory_bytes: int = Field(default=0, ge=0)
    output_hash: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_atomic_state(self) -> Self:
        _validate_trial_payload_state(
            status=self.status,
            artifacts=self.artifacts,
            candidate_metadata=self.candidate_metadata,
            terminal_failure_code=self.terminal_failure_code,
        )
        expected = deterministic_hash(
            self.model_dump(mode="json", exclude={"output_hash"})
        )
        if self.output_hash and self.output_hash != expected:
            raise ValueError("m7_trial_worker_output_hash_invalid")
        object.__setattr__(self, "output_hash", expected)
        return self


class M7DeepTrialResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["m7-deep-trial-result-v1"] = "m7-deep-trial-result-v1"
    trial_id: str = Field(pattern=_IDENTIFIER)
    request_hash: str = Field(pattern=_SHA256)
    status: Literal["succeeded", "failed"]
    artifacts: tuple[M7TrialArtifact, ...]
    candidate_metadata: M7TrialCandidateMetadata | None
    resource_observation: M7TrialResourceObservation
    terminal_failure_code: str | None = Field(default=None, pattern=_FAILURE_CODE)
    result_hash: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_atomic_state(self) -> Self:
        _validate_trial_payload_state(
            status=self.status,
            artifacts=self.artifacts,
            candidate_metadata=self.candidate_metadata,
            terminal_failure_code=self.terminal_failure_code,
        )
        expected = deterministic_hash(
            self.model_dump(mode="json", exclude={"result_hash"})
        )
        if self.result_hash and self.result_hash != expected:
            raise ValueError("m7_trial_result_hash_invalid")
        object.__setattr__(self, "result_hash", expected)
        return self


def _validate_trial_payload_state(
    *,
    status: str,
    artifacts: tuple[M7TrialArtifact, ...],
    candidate_metadata: M7TrialCandidateMetadata | None,
    terminal_failure_code: str | None,
) -> None:
    roles = [row.role for row in artifacts]
    if status == "succeeded":
        if set(roles) != _REQUIRED_SUCCESS_ROLES or len(roles) != len(set(roles)):
            raise ValueError("m7_trial_success_artifacts_incomplete")
        if terminal_failure_code is not None or candidate_metadata is None:
            raise ValueError("m7_trial_success_metadata_invalid")
    else:
        if artifacts:
            raise ValueError("m7_trial_failed_artifacts_forbidden")
        if terminal_failure_code is None or candidate_metadata is not None:
            raise ValueError("m7_trial_failed_failure_code_required")


def _require_safe_relative_path(value: str, *, field: str) -> tuple[str, ...]:
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value.replace("\\", "/"))
    if (
        not value
        or value != value.strip()
        or windows.drive
        or windows.root
        or posix.is_absolute()
        or not posix.parts
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise ValueError(f"m7_trial_{field}_invalid")
    return posix.parts


__all__ = [
    "M7DeepTrialRequest",
    "M7DeepTrialResult",
    "M7DeepTrialWorkerOutput",
    "M7TrialArtifact",
    "M7TrialArtifactReference",
    "M7TrialCandidateMetadata",
    "M7TrialResourceObservation",
]
