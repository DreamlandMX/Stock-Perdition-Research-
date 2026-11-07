"""Fold-local probability and interval calibration artifacts for M4 and M7."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from typing import Any, Self

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import to_jsonable_python
from sklearn.linear_model import LogisticRegression

from stock_prediction.contracts.market_data import Identifier
from stock_prediction.contracts.research import Horizon, Sha256
from stock_prediction.modeling.contracts import (
    EvaluationRole,
    EvaluationWindow,
    SampleKey,
    TaskName,
)
from stock_prediction.modeling.lightgbm_models import (
    ClassifierArtifact,
    QuantileModelSet,
)
from stock_prediction.modeling.m7_calibration import (
    M7BlendCandidate as M7BlendCandidate,
    M7CalibrationInputError as M7CalibrationInputError,
    M7DirectionCalibrator as M7DirectionCalibrator,
    M7FiniteBlend as M7FiniteBlend,
    M7RangeCalibrator as M7RangeCalibrator,
    fit_m7_direction_calibrators as fit_m7_direction_calibrators,
    fit_m7_range_calibrators as fit_m7_range_calibrators,
    project_range_exit_curve as project_range_exit_curve,
    select_finite_m7_blend as select_finite_m7_blend,
)


class CalibrationInputError(ValueError):
    """Calibration evidence, identity, or numerical input is invalid."""


class CalibrationPartitionEvidence(BaseModel):
    """Exact registered CALIBRATION sample partition."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    partition_id: Identifier
    task: TaskName
    horizon: Horizon
    asset_group: Identifier
    evaluation_window: EvaluationWindow
    registered_sample_keys: tuple[SampleKey, ...] = Field(min_length=2)
    registered_sample_keys_hash: Sha256
    split_manifest_hash: Sha256
    source_fold_hash: Sha256
    partition_hash: Sha256

    @model_validator(mode="after")
    def validate_partition(self) -> Self:
        _require_calibration_window(self.evaluation_window)
        if len(set(self.registered_sample_keys)) != len(self.registered_sample_keys):
            raise ValueError("calibration sample keys must be unique")
        if self.registered_sample_keys_hash != _sample_keys_hash(
            self.registered_sample_keys
        ):
            raise ValueError("calibration sample key hash is invalid")
        if any(key[2] != self.horizon for key in self.registered_sample_keys):
            raise ValueError("calibration sample horizon is inconsistent")
        if any(
            not self.evaluation_window.start_session
            <= key[1].date()
            <= self.evaluation_window.end_session
            for key in self.registered_sample_keys
        ):
            raise ValueError("calibration sample falls outside registered window")
        if self.partition_hash != _canonical_hash(
            self.model_dump(mode="python", exclude={"partition_hash"})
        ):
            raise ValueError("calibration partition hash is invalid")
        return self

    @classmethod
    def register(
        cls,
        *,
        partition_id: str,
        task: TaskName,
        horizon: Horizon,
        asset_group: str,
        evaluation_window: EvaluationWindow,
        registered_sample_keys: Sequence[SampleKey],
        split_manifest_hash: str,
        source_fold_hash: str,
    ) -> "CalibrationPartitionEvidence":
        if evaluation_window.role is not EvaluationRole.CALIBRATION:
            raise CalibrationInputError(
                "calibration partition requires a CALIBRATION window"
            )
        payload: dict[str, object] = {
            "partition_id": partition_id,
            "task": task,
            "horizon": horizon,
            "asset_group": asset_group,
            "evaluation_window": evaluation_window,
            "registered_sample_keys": tuple(registered_sample_keys),
            "registered_sample_keys_hash": _sample_keys_hash(registered_sample_keys),
            "split_manifest_hash": split_manifest_hash,
            "source_fold_hash": source_fold_hash,
        }
        payload["partition_hash"] = _canonical_hash(payload)
        try:
            return cls.model_validate(payload, strict=True)
        except ValidationError as error:
            raise CalibrationInputError("calibration partition is invalid") from error


class CalibratedInterval(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    lower: tuple[float, ...]
    upper: tuple[float, ...]
    target_coverage: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if not self.lower or len(self.lower) != len(self.upper):
            raise ValueError("calibrated interval arrays must align")
        if any(
            lower > upper for lower, upper in zip(self.lower, self.upper, strict=True)
        ):
            raise ValueError("calibrated interval lower bound exceeds upper bound")
        return self


class QuantileRearrangement(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    quantiles: tuple[tuple[float, float, float, float, float], ...]
    crossing_row_count: int = Field(ge=0)
    crossing_rate: float = Field(ge=0, le=1)
    maximum_violation: float = Field(ge=0)


def apply_temperature_scaling(
    logits: object,
    *,
    temperature: float,
    class_order: Sequence[str],
) -> np.ndarray:
    """Apply the production temperature-calibration transformation."""

    matrix = _finite_matrix(logits, "temperature logits")
    classes = tuple(class_order)
    if not math.isfinite(temperature) or temperature <= 0:
        raise CalibrationInputError("temperature must be finite and positive")
    if len(classes) < 2 or len(set(classes)) != len(classes):
        raise CalibrationInputError("temperature class order is invalid")
    if matrix.shape[1] != len(classes):
        raise CalibrationInputError("temperature logits do not match class schema")
    return _readonly(_softmax(matrix / temperature))


class TemperatureScaler(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    temperature: float = Field(gt=0)
    task: TaskName
    horizon: Horizon
    asset_group: Identifier
    class_order: tuple[Identifier, ...] = Field(min_length=2)
    calibration_window: EvaluationWindow
    calibration_partition_hash: Sha256
    source_model_artifact_hash: Sha256
    calibration_input_hash: Sha256
    sample_count: int = Field(ge=2)
    artifact_hash: Sha256

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        _require_calibration_window(self.calibration_window)
        _require_classification_task(self.task)
        if len(set(self.class_order)) != len(self.class_order):
            raise ValueError("temperature class order must be unique")
        _verify_artifact_hash(self)
        return self

    @classmethod
    def fit(
        cls,
        logits: object,
        labels: Sequence[str],
        *,
        class_order: Sequence[str],
        calibration_partition: CalibrationPartitionEvidence,
        sample_keys: Sequence[SampleKey],
        source_model: ClassifierArtifact,
        expected_source_model_artifact_hash: str,
    ) -> "TemperatureScaler":
        matrix = _finite_matrix(logits, "temperature logits")
        partition = _validated_partition(
            calibration_partition, sample_keys, len(matrix)
        )
        source = _validated_classifier_source(
            source_model,
            expected_source_model_artifact_hash,
            partition,
        )
        window = partition.evaluation_window
        classes = tuple(class_order)
        label_order = tuple(labels)
        if matrix.shape != (len(label_order), len(classes)):
            raise CalibrationInputError(
                "temperature labels and class schema do not align"
            )
        if len(set(classes)) != len(classes) or len(classes) < 2:
            raise CalibrationInputError("temperature class order is invalid")
        if classes != source.class_order:
            raise CalibrationInputError(
                "temperature class order does not match source model"
            )
        unknown = set(label_order) - set(classes)
        if unknown or set(label_order) != set(classes):
            raise CalibrationInputError(
                "temperature calibration requires every declared class"
            )
        encoded = np.asarray(
            [classes.index(label) for label in label_order], dtype=np.int64
        )
        candidates = np.exp(np.linspace(-3.0, 3.0, 401))
        losses = np.asarray(
            [_multiclass_log_loss(matrix / value, encoded) for value in candidates]
        )
        temperature = float(candidates[int(np.argmin(losses))])
        input_hash = _canonical_hash(
            {
                "logits": matrix.tolist(),
                "labels": label_order,
                "class_order": classes,
                "calibration_window": window,
                "calibration_partition_hash": partition.partition_hash,
                "sample_keys": tuple(sample_keys),
                "source_model_artifact_hash": source.artifact_hash,
            }
        )
        payload: dict[str, object] = {
            "temperature": temperature,
            "task": partition.task,
            "horizon": partition.horizon,
            "asset_group": partition.asset_group,
            "class_order": classes,
            "calibration_window": window,
            "calibration_partition_hash": partition.partition_hash,
            "source_model_artifact_hash": source.artifact_hash,
            "calibration_input_hash": input_hash,
            "sample_count": len(label_order),
        }
        payload["artifact_hash"] = _canonical_hash(payload)
        try:
            return cls.model_validate(payload, strict=True)
        except ValidationError as error:
            raise CalibrationInputError("temperature artifact is invalid") from error

    def apply(
        self,
        logits: object,
        *,
        expected_artifact_hash: str,
        source_model_artifact_hash: str,
    ) -> np.ndarray:
        artifact = _revalidate(self, TemperatureScaler, expected_artifact_hash)
        _verify_source_model(
            artifact.source_model_artifact_hash, source_model_artifact_hash
        )
        return apply_temperature_scaling(
            logits,
            temperature=artifact.temperature,
            class_order=artifact.class_order,
        )


class PlattScaler(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    coefficient: float
    intercept: float
    task: TaskName
    horizon: Horizon
    asset_group: Identifier
    calibration_window: EvaluationWindow
    calibration_partition_hash: Sha256
    source_model_artifact_hash: Sha256
    calibration_input_hash: Sha256
    sample_count: int = Field(ge=2)
    artifact_hash: Sha256

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        _require_calibration_window(self.calibration_window)
        _require_classification_task(self.task)
        _verify_artifact_hash(self)
        return self

    @classmethod
    def fit(
        cls,
        scores: Sequence[float],
        labels: Sequence[int],
        *,
        calibration_partition: CalibrationPartitionEvidence,
        sample_keys: Sequence[SampleKey],
        source_model: ClassifierArtifact,
        expected_source_model_artifact_hash: str,
    ) -> "PlattScaler":
        values = _finite_vector(scores, "Platt scores")
        partition = _validated_partition(
            calibration_partition, sample_keys, len(values)
        )
        source = _validated_classifier_source(
            source_model,
            expected_source_model_artifact_hash,
            partition,
        )
        if len(source.class_order) != 2:
            raise CalibrationInputError(
                "Platt calibration requires a binary source model"
            )
        window = partition.evaluation_window
        encoded = np.asarray(tuple(labels), dtype=np.int64)
        if encoded.ndim != 1 or len(encoded) != len(values) or set(encoded) != {0, 1}:
            raise CalibrationInputError(
                "Platt labels must align and contain zero and one"
            )
        try:
            estimator = LogisticRegression(
                solver="lbfgs", random_state=0, max_iter=1000
            ).fit(values.reshape(-1, 1), encoded)
        except (TypeError, ValueError) as error:
            raise CalibrationInputError("Platt calibration failed") from error
        input_hash = _canonical_hash(
            {
                "scores": values.tolist(),
                "labels": encoded.tolist(),
                "calibration_window": window,
                "calibration_partition_hash": partition.partition_hash,
                "sample_keys": tuple(sample_keys),
                "source_model_artifact_hash": source.artifact_hash,
            }
        )
        payload: dict[str, object] = {
            "coefficient": float(estimator.coef_[0, 0]),
            "intercept": float(estimator.intercept_[0]),
            "task": partition.task,
            "horizon": partition.horizon,
            "asset_group": partition.asset_group,
            "calibration_window": window,
            "calibration_partition_hash": partition.partition_hash,
            "source_model_artifact_hash": source.artifact_hash,
            "calibration_input_hash": input_hash,
            "sample_count": len(values),
        }
        payload["artifact_hash"] = _canonical_hash(payload)
        try:
            return cls.model_validate(payload, strict=True)
        except ValidationError as error:
            raise CalibrationInputError("Platt artifact is invalid") from error

    def apply(
        self,
        scores: Sequence[float],
        *,
        expected_artifact_hash: str,
        source_model_artifact_hash: str,
    ) -> np.ndarray:
        artifact = _revalidate(self, PlattScaler, expected_artifact_hash)
        _verify_source_model(
            artifact.source_model_artifact_hash, source_model_artifact_hash
        )
        values = _finite_vector(scores, "Platt scores")
        positive = 1.0 / (
            1.0 + np.exp(-(artifact.coefficient * values + artifact.intercept))
        )
        return _readonly(np.column_stack((1.0 - positive, positive)))


class CqrCalibrator(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    target_coverage: float = Field(gt=0, lt=1)
    task: TaskName
    horizon: Horizon
    asset_group: Identifier
    conformal_score: float
    finite_sample_rank: int = Field(ge=1)
    calibration_window: EvaluationWindow
    calibration_partition_hash: Sha256
    source_model_artifact_hash: Sha256
    calibration_input_hash: Sha256
    sample_count: int = Field(ge=2)
    artifact_hash: Sha256

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        _require_calibration_window(self.calibration_window)
        _require_distribution_task(self.task)
        expected_rank = math.ceil((self.sample_count + 1) * self.target_coverage)
        if expected_rank > self.sample_count:
            raise ValueError("CQR target coverage is unavailable for sample count")
        if self.finite_sample_rank != expected_rank:
            raise ValueError("CQR finite sample rank is inconsistent")
        _verify_artifact_hash(self)
        return self

    @classmethod
    def fit(
        cls,
        calibration_y: Sequence[float] | np.ndarray,
        q10: Sequence[float] | np.ndarray,
        q90: Sequence[float] | np.ndarray,
        *,
        target_coverage: float,
        calibration_partition: CalibrationPartitionEvidence,
        sample_keys: Sequence[SampleKey],
        source_model: QuantileModelSet,
        expected_source_model_artifact_hash: str,
    ) -> "CqrCalibrator":
        outcomes = _finite_vector(calibration_y, "CQR outcomes")
        partition = _validated_partition(
            calibration_partition, sample_keys, len(outcomes)
        )
        source = _validated_quantile_source(
            source_model,
            expected_source_model_artifact_hash,
            partition,
        )
        window = partition.evaluation_window
        lower = _finite_vector(q10, "CQR lower quantiles")
        upper = _finite_vector(q90, "CQR upper quantiles")
        if len(outcomes) < 2 or not (len(outcomes) == len(lower) == len(upper)):
            raise CalibrationInputError("CQR calibration arrays must align")
        if np.any(lower > upper):
            raise CalibrationInputError("CQR lower quantile exceeds upper quantile")
        if not math.isfinite(target_coverage) or not 0 < target_coverage < 1:
            raise CalibrationInputError(
                "CQR target coverage must be between zero and one"
            )
        scores = np.maximum(lower - outcomes, outcomes - upper)
        rank = math.ceil((len(scores) + 1) * target_coverage)
        if rank > len(scores):
            raise CalibrationInputError(
                "CQR calibration sample is too small for target coverage"
            )
        score = float(np.sort(scores)[rank - 1])
        input_hash = _canonical_hash(
            {
                "outcomes": outcomes.tolist(),
                "lower": lower.tolist(),
                "upper": upper.tolist(),
                "target_coverage": target_coverage,
                "calibration_window": window,
                "calibration_partition_hash": partition.partition_hash,
                "sample_keys": tuple(sample_keys),
                "source_model_artifact_hash": source.artifact_hash,
            }
        )
        payload: dict[str, object] = {
            "target_coverage": target_coverage,
            "task": partition.task,
            "horizon": partition.horizon,
            "asset_group": partition.asset_group,
            "conformal_score": score,
            "finite_sample_rank": rank,
            "calibration_window": window,
            "calibration_partition_hash": partition.partition_hash,
            "source_model_artifact_hash": source.artifact_hash,
            "calibration_input_hash": input_hash,
            "sample_count": len(scores),
        }
        payload["artifact_hash"] = _canonical_hash(payload)
        try:
            return cls.model_validate(payload, strict=True)
        except ValidationError as error:
            raise CalibrationInputError("CQR artifact is invalid") from error

    def apply(
        self,
        q10: Sequence[float],
        q90: Sequence[float],
        *,
        expected_artifact_hash: str,
        source_model_artifact_hash: str,
    ) -> CalibratedInterval:
        artifact = _revalidate(self, CqrCalibrator, expected_artifact_hash)
        _verify_source_model(
            artifact.source_model_artifact_hash, source_model_artifact_hash
        )
        lower = _finite_vector(q10, "CQR lower quantiles")
        upper = _finite_vector(q90, "CQR upper quantiles")
        if len(lower) == 0 or len(lower) != len(upper) or np.any(lower > upper):
            raise CalibrationInputError("CQR application intervals are invalid")
        return CalibratedInterval(
            lower=tuple(float(value - artifact.conformal_score) for value in lower),
            upper=tuple(float(value + artifact.conformal_score) for value in upper),
            target_coverage=artifact.target_coverage,
        )


def rearrange_quantiles(raw_quantiles: object) -> QuantileRearrangement:
    matrix = _finite_matrix(raw_quantiles, "raw quantiles")
    if matrix.shape[1] != 5:
        raise CalibrationInputError("quantile rearrangement requires q10 through q90")
    ordered = np.sort(matrix, axis=1)
    crossing = np.any(np.diff(matrix, axis=1) < 0, axis=1)
    maximum = float(np.max(np.abs(matrix - ordered))) if len(matrix) else 0.0
    rows = tuple(tuple(float(value) for value in row) for row in ordered)
    return QuantileRearrangement(
        quantiles=rows,  # type: ignore[arg-type]
        crossing_row_count=int(np.sum(crossing)),
        crossing_rate=float(np.mean(crossing)),
        maximum_violation=maximum,
    )


def _validated_window(window: EvaluationWindow) -> EvaluationWindow:
    try:
        checked = EvaluationWindow.model_validate(
            window.model_dump(mode="python"), strict=True
        )
    except (AttributeError, ValidationError) as error:
        raise CalibrationInputError("calibration window is invalid") from error
    try:
        _require_calibration_window(checked)
    except ValueError as error:
        raise CalibrationInputError(
            "calibration requires a CALIBRATION window"
        ) from error
    return checked


def _validated_partition(
    partition: CalibrationPartitionEvidence,
    sample_keys: Sequence[SampleKey],
    expected_count: int,
) -> CalibrationPartitionEvidence:
    try:
        checked = CalibrationPartitionEvidence.model_validate(
            partition.model_dump(mode="python"), strict=True
        )
    except (AttributeError, ValidationError) as error:
        raise CalibrationInputError("calibration partition is invalid") from error
    keys = tuple(sample_keys)
    if len(keys) != expected_count or keys != checked.registered_sample_keys:
        raise CalibrationInputError(
            "calibration sample keys do not exactly match registered partition"
        )
    return checked


def _validated_classifier_source(
    source: ClassifierArtifact,
    expected_artifact_hash: str,
    partition: CalibrationPartitionEvidence,
) -> ClassifierArtifact:
    try:
        checked = ClassifierArtifact.model_validate(
            source.model_dump(mode="python"), strict=True
        )
    except (AttributeError, ValidationError) as error:
        raise CalibrationInputError("source classifier artifact is invalid") from error
    if checked.artifact_hash != expected_artifact_hash:
        raise CalibrationInputError(
            "source classifier does not match registered identity"
        )
    if (
        checked.task != partition.task
        or checked.horizon != partition.horizon
        or checked.asset_group != partition.asset_group
        or checked.dataset_input_hash != partition.evaluation_window.dataset_input_hash
    ):
        raise CalibrationInputError(
            "source model task, horizon, or asset group does not match calibration"
        )
    return checked


def _validated_quantile_source(
    source: QuantileModelSet,
    expected_artifact_hash: str,
    partition: CalibrationPartitionEvidence,
) -> QuantileModelSet:
    try:
        checked = QuantileModelSet.model_validate(
            source.model_dump(mode="python"), strict=True
        )
    except (AttributeError, ValidationError) as error:
        raise CalibrationInputError("source quantile artifact is invalid") from error
    if checked.artifact_hash != expected_artifact_hash:
        raise CalibrationInputError(
            "source quantile model does not match registered identity"
        )
    if (
        checked.task != partition.task
        or checked.horizon != partition.horizon
        or checked.asset_group != partition.asset_group
        or checked.dataset_input_hash != partition.evaluation_window.dataset_input_hash
    ):
        raise CalibrationInputError(
            "source model task, horizon, or asset group does not match calibration"
        )
    return checked


def _require_calibration_window(window: EvaluationWindow) -> None:
    if window.role is not EvaluationRole.CALIBRATION:
        raise ValueError("calibrator must be fitted on a CALIBRATION window")


def _require_classification_task(task: TaskName) -> None:
    if task not in {
        TaskName.FORECAST_DIRECTION,
        TaskName.EXECUTABLE_DIRECTION,
        TaskName.BREAKOUT,
        TaskName.RANGE_EXIT_SURVIVAL,
    }:
        raise ValueError("probability calibration requires a classification task")


def _require_distribution_task(task: TaskName) -> None:
    if task not in {
        TaskName.CAPITAL_GAIN_RETURN,
        TaskName.TOTAL_RETURN,
        TaskName.EXECUTABLE_RETURN,
        TaskName.FORECAST_MFE,
        TaskName.FORECAST_MAE,
        TaskName.EXECUTABLE_MFE,
        TaskName.EXECUTABLE_MAE,
    }:
        raise ValueError("CQR requires a distribution task")


def _verify_artifact_hash(artifact: BaseModel) -> None:
    values = artifact.model_dump(mode="python", exclude={"artifact_hash"})
    if getattr(artifact, "artifact_hash") != _canonical_hash(values):
        raise ValueError("calibration artifact hash is invalid")


def _revalidate(
    artifact: Any, artifact_type: type[BaseModel], expected_artifact_hash: str
) -> Any:
    try:
        checked = artifact_type.model_validate(
            artifact.model_dump(mode="python"), strict=True
        )
    except (AttributeError, ValidationError) as error:
        raise CalibrationInputError(
            "calibration artifact failed strict revalidation"
        ) from error
    if getattr(checked, "artifact_hash") != expected_artifact_hash:
        raise CalibrationInputError(
            "calibration artifact does not match registered identity"
        )
    return checked


def _verify_source_model(expected: str, supplied: str) -> None:
    if expected != supplied:
        raise CalibrationInputError("calibration source model identity does not match")


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            to_jsonable_python(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sample_keys_hash(sample_keys: Sequence[SampleKey]) -> str:
    return hashlib.sha256(
        json.dumps(
            tuple((key[0], key[1].isoformat(), key[2]) for key in sample_keys),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _finite_matrix(values: object, name: str) -> np.ndarray:
    try:
        matrix = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise CalibrationInputError(f"{name} must be numeric") from error
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
        raise CalibrationInputError(f"{name} must be a non-empty matrix")
    if not np.all(np.isfinite(matrix)):
        raise CalibrationInputError(f"{name} must contain only finite values")
    return matrix


def _finite_vector(values: object, name: str) -> np.ndarray:
    try:
        vector = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise CalibrationInputError(f"{name} must be numeric") from error
    if vector.ndim != 1 or len(vector) == 0:
        raise CalibrationInputError(f"{name} must be a non-empty vector")
    if not np.all(np.isfinite(vector)):
        raise CalibrationInputError(f"{name} must contain only finite values")
    return vector


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=1, keepdims=True)
    exponent = np.exp(shifted)
    return np.asarray(
        exponent / np.sum(exponent, axis=1, keepdims=True), dtype=np.float64
    )


def _multiclass_log_loss(logits: np.ndarray, labels: np.ndarray) -> float:
    probabilities = _softmax(logits)
    selected = probabilities[np.arange(len(labels)), labels]
    return float(-np.mean(np.log(np.clip(selected, 1e-15, 1.0))))


def _readonly(values: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(result)):
        raise CalibrationInputError("calibrated output must contain finite values")
    result.setflags(write=False)
    return result
