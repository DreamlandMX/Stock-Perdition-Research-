"""Frozen, content-addressed training inputs shared by M4 model families."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any, Literal, Self

import numpy as np
import optuna
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator
from pydantic_core import to_jsonable_python

from stock_prediction.contracts.market_data import Identifier
from stock_prediction.contracts.research import Horizon, Sha256
from stock_prediction.modeling.contracts import (
    EvaluationRole,
    EvaluationWindow,
    SampleKey,
    TaskName,
)


class ModelInputError(ValueError):
    """Model data, schema, or serialized evidence failed validation."""


_SAMPLE_KEYS = TypeAdapter(tuple[SampleKey, ...])


class TrainingPartitionEvidence(BaseModel):
    """Exact registered TRAIN partition authorized for one task and horizon."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    partition_id: Identifier
    task: TaskName
    horizon: Horizon
    evaluation_window: EvaluationWindow
    registered_sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    registered_sample_keys_hash: Sha256
    split_manifest_hash: Sha256
    source_fold_hash: Sha256
    partition_hash: Sha256

    @model_validator(mode="after")
    def validate_registration(self) -> Self:
        if self.evaluation_window.role is not EvaluationRole.TRAIN:
            raise ValueError("training partition requires a TRAIN evaluation window")
        if self.registered_sample_keys_hash != _sample_keys_hash(
            self.registered_sample_keys
        ):
            raise ValueError("registered sample key hash is invalid")
        if any(key[2] != self.horizon for key in self.registered_sample_keys):
            raise ValueError("registered sample horizon is inconsistent")
        if any(
            not self.evaluation_window.start_session
            <= key[1].date()
            <= self.evaluation_window.end_session
            for key in self.registered_sample_keys
        ):
            raise ValueError("registered sample falls outside TRAIN window")
        if self.partition_hash != self.compute_hash():
            raise ValueError("training partition hash is invalid")
        return self

    def compute_hash(self) -> str:
        return _canonical_hash(
            self.model_dump(mode="python", exclude={"partition_hash"})
        )

    @classmethod
    def register(
        cls,
        *,
        partition_id: str,
        task: TaskName,
        horizon: Horizon,
        evaluation_window: EvaluationWindow,
        registered_sample_keys: Sequence[SampleKey],
        split_manifest_hash: str,
        source_fold_hash: str,
    ) -> "TrainingPartitionEvidence":
        keys = _SAMPLE_KEYS.validate_python(tuple(registered_sample_keys), strict=True)
        payload: dict[str, Any] = {
            "partition_id": partition_id,
            "task": task,
            "horizon": horizon,
            "evaluation_window": evaluation_window,
            "registered_sample_keys": keys,
            "registered_sample_keys_hash": _sample_keys_hash(keys),
            "split_manifest_hash": split_manifest_hash,
            "source_fold_hash": source_fold_hash,
        }
        payload["partition_hash"] = _canonical_hash(payload)
        return cls.model_validate(payload, strict=True)


class InnerFoldDataView(BaseModel):
    """Read-only hashes for data the tuning runner is authorized to expose."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    fold_id: Identifier
    training_partition_hash: Sha256
    calibration_input_hash: Sha256
    fold_input_hash: Sha256

    @classmethod
    def register(
        cls,
        *,
        fold_id: str,
        training_partition_hash: str,
        calibration_input_hash: str,
    ) -> "InnerFoldDataView":
        payload = {
            "fold_id": fold_id,
            "training_partition_hash": training_partition_hash,
            "calibration_input_hash": calibration_input_hash,
        }
        return cls(
            **payload,
            fold_input_hash=_canonical_hash(payload),
        )

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        if self.fold_input_hash != _canonical_hash(
            self.model_dump(mode="python", exclude={"fold_input_hash"})
        ):
            raise ValueError("inner-fold input hash is invalid")
        return self


class OptunaBudget(BaseModel):
    """Resource and evidence boundary for an inner-fold-only search."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    trial_count: int = Field(ge=1, le=100)
    parallel_trials: int = Field(default=2, ge=2, le=4)
    threads_per_trial: int = Field(default=4, ge=1, le=4)
    inner_folds: tuple[InnerFoldDataView, ...] = Field(min_length=2)
    seed: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_folds(self) -> Self:
        fold_ids = tuple(fold.fold_id for fold in self.inner_folds)
        if len(set(fold_ids)) != len(fold_ids):
            raise ValueError("inner fold IDs must be unique")
        if self.parallel_trials * self.threads_per_trial > 16:
            raise ValueError("Optuna budget cannot reserve more than 16 CPU threads")
        return self

    @property
    def inner_fold_ids(self) -> tuple[str, ...]:
        return tuple(fold.fold_id for fold in self.inner_folds)

    @property
    def inner_fold_hashes(self) -> tuple[str, ...]:
        return tuple(fold.fold_input_hash for fold in self.inner_folds)


class InnerFoldObjective(BaseModel):
    """Calibration/distribution loss first, ranking evidence second."""

    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    primary_metric: Literal["brier", "pinball_loss", "time_dependent_brier"]
    primary_value: float
    ranking_metric: Literal["pr_auc", "ic", "c_index"]
    ranking_value: float
    evaluated_fold_hashes: tuple[Sha256, ...] = Field(min_length=2)
    threads_used: int = Field(ge=1, le=4)


class TrialParameter(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    name: Identifier
    value: float | int | str | bool


class OptunaTrialEvidence(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    trial_number: int = Field(ge=0)
    state: Literal["complete", "failed", "pruned"]
    primary_metric: str | None
    primary_value: float | None
    ranking_metric: str | None
    ranking_value: float | None
    parameters: tuple[TrialParameter, ...]


class OptunaSearchResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    trial_count: int = Field(ge=1)
    parallel_trials: int = Field(ge=2, le=4)
    threads_per_trial: int = Field(ge=1, le=4)
    inner_fold_ids: tuple[Identifier, ...] = Field(min_length=2)
    inner_fold_hashes: tuple[Sha256, ...] = Field(min_length=2)
    seed: int = Field(ge=0)
    trials: tuple[OptunaTrialEvidence, ...] = Field(min_length=1)


def run_bounded_optuna(
    budget: OptunaBudget,
    folds: Sequence[ClassificationTuningFold],
    *,
    base_config: object,
) -> OptunaSearchResult:
    """Tune classification using only runner-owned registered inner-fold data."""

    checked = OptunaBudget.model_validate(budget.model_dump(mode="python"), strict=True)
    checked_folds = tuple(
        ClassificationTuningFold.model_validate(
            fold.model_dump(mode="python"), strict=True
        )
        for fold in folds
    )
    if tuple(fold.view for fold in checked_folds) != checked.inner_folds:
        raise ModelInputError("tuning folds do not match the registered Optuna budget")
    from stock_prediction.modeling.lightgbm_models import (
        ClassifierConfig,
        LightGbmClassifierTrainer,
    )
    from stock_prediction.modeling.metrics import (
        multiclass_brier_score,
        one_vs_rest_pr_auc,
    )

    try:
        template = ClassifierConfig.model_validate(base_config, strict=True)
    except ValidationError as error:
        raise ModelInputError("Optuna base classifier config is invalid") from error
    study = optuna.create_study(
        directions=("minimize", "maximize"),
        sampler=optuna.samplers.TPESampler(seed=checked.seed),
    )

    def evaluate(trial: optuna.Trial) -> tuple[float, float]:
        trial.set_user_attr("threads_per_trial", checked.threads_per_trial)
        trial.set_user_attr("inner_fold_ids", list(checked.inner_fold_ids))
        trial.set_user_attr("inner_fold_hashes", list(checked.inner_fold_hashes))
        config = template.model_copy(
            update={
                "num_threads": checked.threads_per_trial,
                "learning_rate": trial.suggest_float(
                    "learning_rate", 0.01, 0.2, log=True
                ),
                "num_leaves": trial.suggest_int("num_leaves", 7, 63),
                "min_data_in_leaf": trial.suggest_int(
                    "min_data_in_leaf", 5, 100
                ),
            }
        )
        try:
            config = ClassifierConfig.model_validate(
                config.model_dump(mode="python"), strict=True
            )
        except ValidationError as error:
            raise ModelInputError("Optuna suggested an invalid classifier config") from error
        brier_scores: list[float] = []
        ranking_scores: list[float] = []
        for fold in checked_folds:
            artifact = LightGbmClassifierTrainer(config).fit(fold.training_data)
            probabilities = artifact.predict_proba(
                fold.calibration_features(),
                feature_names=fold.feature_names,
                expected_artifact_hash=artifact.artifact_hash,
            )
            brier_scores.append(
                multiclass_brier_score(
                    fold.calibration_labels,
                    probabilities,
                    classes=config.class_order,
                )
            )
            ranking_scores.append(
                one_vs_rest_pr_auc(
                    fold.calibration_labels,
                    probabilities,
                    classes=config.class_order,
                ).macro_average
            )
        trial.set_user_attr("primary_metric", "brier")
        trial.set_user_attr("ranking_metric", "pr_auc")
        return float(np.mean(brier_scores)), float(np.mean(ranking_scores))

    study.optimize(
        evaluate,
        n_trials=checked.trial_count,
        n_jobs=checked.parallel_trials,
        catch=(ModelInputError,),
        show_progress_bar=False,
    )
    state_names = {
        optuna.trial.TrialState.COMPLETE: "complete",
        optuna.trial.TrialState.FAIL: "failed",
        optuna.trial.TrialState.PRUNED: "pruned",
    }
    trials: list[OptunaTrialEvidence] = []
    for trial in study.trials:
        state = state_names.get(trial.state)
        if state is None:
            raise ModelInputError("Optuna returned a non-terminal trial")
        values = list(trial.values) if trial.values is not None else []
        trials.append(
            OptunaTrialEvidence(
                trial_number=trial.number,
                state=state,  # type: ignore[arg-type]
                primary_metric=trial.user_attrs.get("primary_metric"),
                primary_value=float(values[0]) if len(values) > 0 else None,
                ranking_metric=trial.user_attrs.get("ranking_metric"),
                ranking_value=float(values[1]) if len(values) > 1 else None,
                parameters=tuple(
                    TrialParameter(name=name, value=value)
                    for name, value in sorted(trial.params.items())
                ),
            )
        )
    return OptunaSearchResult(
        trial_count=checked.trial_count,
        parallel_trials=checked.parallel_trials,
        threads_per_trial=checked.threads_per_trial,
        inner_fold_ids=checked.inner_fold_ids,
        inner_fold_hashes=checked.inner_fold_hashes,
        seed=checked.seed,
        trials=tuple(trials),
    )


def _canonical_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            to_jsonable_python(payload),
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


def _readonly_matrix(rows: Sequence[Sequence[float]]) -> np.ndarray:
    result = np.asarray(rows, dtype=np.float64)
    result.setflags(write=False)
    return result


def _validate_common(
    features: object,
    *,
    sample_keys: Sequence[SampleKey],
    feature_names: Sequence[str],
    partition: TrainingPartitionEvidence,
    dataset_input_hash: str,
) -> tuple[tuple[tuple[float, ...], ...], tuple[SampleKey, ...], tuple[str, ...]]:
    try:
        matrix = np.asarray(features, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ModelInputError("features must be a numeric matrix") from error
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
        raise ModelInputError("features must be a non-empty two-dimensional matrix")
    if not np.all(np.isfinite(matrix)):
        raise ModelInputError("features must contain only finite values")
    schema = tuple(feature_names)
    if len(schema) != matrix.shape[1] or len(set(schema)) != len(schema):
        raise ModelInputError("feature names must uniquely match the matrix columns")
    try:
        schema = TypeAdapter(tuple[Identifier, ...]).validate_python(schema, strict=True)
        keys = _SAMPLE_KEYS.validate_python(tuple(sample_keys), strict=True)
    except ValidationError as error:
        raise ModelInputError("feature names or sample keys are invalid") from error
    if len(keys) != len(matrix) or len(set(keys)) != len(keys):
        raise ModelInputError("sample keys must be unique and align with features")
    try:
        checked_partition = TrainingPartitionEvidence.model_validate(
            partition.model_dump(mode="python"), strict=True
        )
    except (AttributeError, ValidationError) as error:
        raise ModelInputError("registered TRAIN partition is invalid") from error
    if checked_partition.evaluation_window.dataset_input_hash != dataset_input_hash:
        raise ModelInputError("dataset hash does not match registered TRAIN partition")
    if keys != checked_partition.registered_sample_keys:
        raise ModelInputError(
            "training sample keys do not exactly match registered TRAIN partition"
        )
    rows = tuple(tuple(float(value) for value in row) for row in matrix)
    return rows, keys, schema


class ClassificationTrainingData(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    feature_rows: tuple[tuple[float, ...], ...] = Field(min_length=1)
    labels: tuple[Identifier, ...] = Field(min_length=1)
    sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    feature_names: tuple[Identifier, ...] = Field(min_length=1)
    partition: TrainingPartitionEvidence
    dataset_input_hash: Sha256
    input_hash: Sha256

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        _validate_training_shape(self.feature_rows, self.labels, self.sample_keys, self.feature_names)
        _validate_partition_binding(
            self.partition,
            self.sample_keys,
            self.dataset_input_hash,
        )
        if self.input_hash != self.compute_hash():
            raise ValueError("input_hash does not match classification evidence")
        return self

    def compute_hash(self) -> str:
        return _canonical_hash(
            {
                "feature_rows": self.feature_rows,
                "labels": self.labels,
                "sample_keys": self.sample_keys,
                "feature_names": self.feature_names,
                "partition": self.partition,
                "dataset_input_hash": self.dataset_input_hash,
            }
        )

    @classmethod
    def build(
        cls,
        features: object,
        labels: Sequence[str],
        *,
        sample_keys: Sequence[SampleKey],
        feature_names: Sequence[str],
        dataset_input_hash: str,
        partition: TrainingPartitionEvidence,
    ) -> "ClassificationTrainingData":
        rows, keys, schema = _validate_common(
            features,
            sample_keys=sample_keys,
            feature_names=feature_names,
            partition=partition,
            dataset_input_hash=dataset_input_hash,
        )
        label_tuple = tuple(labels)
        try:
            checked_labels = TypeAdapter(tuple[Identifier, ...]).validate_python(
                label_tuple, strict=True
            )
        except ValidationError as error:
            raise ModelInputError("classification labels are invalid") from error
        if len(checked_labels) != len(rows):
            raise ModelInputError("classification labels do not align with features")
        payload: dict[str, Any] = {
            "feature_rows": rows,
            "labels": checked_labels,
            "sample_keys": keys,
            "feature_names": schema,
            "partition": partition,
            "dataset_input_hash": dataset_input_hash,
        }
        payload["input_hash"] = _canonical_hash(payload)
        try:
            return cls.model_validate(payload, strict=True)
        except ValidationError as error:
            raise ModelInputError("classification training evidence is invalid") from error

    def features(self) -> np.ndarray:
        return _readonly_matrix(self.feature_rows)


class ClassificationTuningFold(BaseModel):
    """Runner-owned TRAIN plus later calibration data for one inner fold."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    view: InnerFoldDataView
    training_data: ClassificationTrainingData
    calibration_window: EvaluationWindow
    calibration_feature_rows: tuple[tuple[float, ...], ...] = Field(min_length=2)
    calibration_labels: tuple[Identifier, ...] = Field(min_length=2)
    calibration_sample_keys: tuple[SampleKey, ...] = Field(min_length=2)
    feature_names: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_fold(self) -> Self:
        try:
            training = ClassificationTrainingData.model_validate(
                self.training_data.model_dump(mode="python"), strict=True
            )
        except ValidationError as error:
            raise ValueError("inner-fold training data is invalid") from error
        if self.view.training_partition_hash != training.partition.partition_hash:
            raise ValueError("inner-fold training partition hash is inconsistent")
        if self.calibration_window.role is not EvaluationRole.CALIBRATION:
            raise ValueError("inner-fold evaluation requires a CALIBRATION window")
        if (
            self.calibration_window.dataset_input_hash
            != training.dataset_input_hash
        ):
            raise ValueError("inner-fold dataset hashes are inconsistent")
        if self.calibration_window.start_session <= training.partition.evaluation_window.end_session:
            raise ValueError("inner-fold calibration must follow training")
        if len(self.calibration_feature_rows) != len(self.calibration_labels) or len(
            self.calibration_feature_rows
        ) != len(self.calibration_sample_keys):
            raise ValueError("inner-fold calibration rows are misaligned")
        if any(len(row) != len(self.feature_names) for row in self.calibration_feature_rows):
            raise ValueError("inner-fold calibration feature schema is invalid")
        if self.feature_names != training.feature_names:
            raise ValueError("inner-fold train/calibration feature schemas differ")
        if any(key[2] != training.partition.horizon for key in self.calibration_sample_keys):
            raise ValueError("inner-fold calibration horizon is inconsistent")
        if set(self.calibration_sample_keys) & set(training.sample_keys):
            raise ValueError("inner-fold train and calibration samples overlap")
        if len(set(self.calibration_sample_keys)) != len(
            self.calibration_sample_keys
        ):
            raise ValueError("inner-fold calibration sample keys must be unique")
        if any(
            not self.calibration_window.start_session
            <= key[1].date()
            <= self.calibration_window.end_session
            for key in self.calibration_sample_keys
        ):
            raise ValueError("calibration sample falls outside registered window")
        if self.view.calibration_input_hash != self.compute_calibration_hash():
            raise ValueError("inner-fold calibration input hash is invalid")
        return self

    def compute_calibration_hash(self) -> str:
        return _canonical_hash(
            {
                "calibration_window": self.calibration_window,
                "calibration_feature_rows": self.calibration_feature_rows,
                "calibration_labels": self.calibration_labels,
                "calibration_sample_keys": self.calibration_sample_keys,
                "feature_names": self.feature_names,
            }
        )

    @classmethod
    def build(
        cls,
        *,
        fold_id: str,
        training_data: ClassificationTrainingData,
        calibration_window: EvaluationWindow,
        calibration_features: object,
        calibration_labels: Sequence[str],
        calibration_sample_keys: Sequence[SampleKey],
    ) -> "ClassificationTuningFold":
        matrix = np.asarray(calibration_features, dtype=np.float64)
        if matrix.ndim != 2 or not np.all(np.isfinite(matrix)):
            raise ModelInputError("calibration features must be a finite matrix")
        rows = tuple(tuple(float(value) for value in row) for row in matrix)
        labels = TypeAdapter(tuple[Identifier, ...]).validate_python(
            tuple(calibration_labels), strict=True
        )
        keys = _SAMPLE_KEYS.validate_python(tuple(calibration_sample_keys), strict=True)
        payload = {
            "calibration_window": calibration_window,
            "calibration_feature_rows": rows,
            "calibration_labels": labels,
            "calibration_sample_keys": keys,
            "feature_names": training_data.feature_names,
        }
        view = InnerFoldDataView.register(
            fold_id=fold_id,
            training_partition_hash=training_data.partition.partition_hash,
            calibration_input_hash=_canonical_hash(payload),
        )
        try:
            return cls.model_validate(
                {"view": view, "training_data": training_data, **payload},
                strict=True,
            )
        except ValidationError as error:
            raise ModelInputError("classification tuning fold is invalid") from error

    def calibration_features(self) -> np.ndarray:
        return _readonly_matrix(self.calibration_feature_rows)


class RegressionTrainingData(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    feature_rows: tuple[tuple[float, ...], ...] = Field(min_length=1)
    outcomes: tuple[float, ...] = Field(min_length=1)
    sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    feature_names: tuple[Identifier, ...] = Field(min_length=1)
    partition: TrainingPartitionEvidence
    dataset_input_hash: Sha256
    input_hash: Sha256

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        _validate_training_shape(self.feature_rows, self.outcomes, self.sample_keys, self.feature_names)
        _validate_partition_binding(
            self.partition,
            self.sample_keys,
            self.dataset_input_hash,
        )
        if self.input_hash != self.compute_hash():
            raise ValueError("input_hash does not match regression evidence")
        return self

    def compute_hash(self) -> str:
        return _canonical_hash(
            {
                "feature_rows": self.feature_rows,
                "outcomes": self.outcomes,
                "sample_keys": self.sample_keys,
                "feature_names": self.feature_names,
                "partition": self.partition,
                "dataset_input_hash": self.dataset_input_hash,
            }
        )

    @classmethod
    def build(
        cls,
        features: object,
        outcomes: Sequence[float] | np.ndarray,
        *,
        sample_keys: Sequence[SampleKey],
        feature_names: Sequence[str],
        dataset_input_hash: str,
        partition: TrainingPartitionEvidence,
    ) -> "RegressionTrainingData":
        rows, keys, schema = _validate_common(
            features,
            sample_keys=sample_keys,
            feature_names=feature_names,
            partition=partition,
            dataset_input_hash=dataset_input_hash,
        )
        try:
            values = np.asarray(outcomes, dtype=np.float64)
        except (TypeError, ValueError) as error:
            raise ModelInputError("regression outcomes must be numeric") from error
        if values.ndim != 1 or len(values) != len(rows):
            raise ModelInputError("regression outcomes do not align with features")
        if not np.all(np.isfinite(values)):
            raise ModelInputError("regression outcomes must contain only finite values")
        payload: dict[str, Any] = {
            "feature_rows": rows,
            "outcomes": tuple(float(value) for value in values),
            "sample_keys": keys,
            "feature_names": schema,
            "partition": partition,
            "dataset_input_hash": dataset_input_hash,
        }
        payload["input_hash"] = _canonical_hash(payload)
        try:
            return cls.model_validate(payload, strict=True)
        except ValidationError as error:
            raise ModelInputError("regression training evidence is invalid") from error

    def features(self) -> np.ndarray:
        return _readonly_matrix(self.feature_rows)


def revalidate_training_data(
    data: ClassificationTrainingData | RegressionTrainingData,
) -> ClassificationTrainingData | RegressionTrainingData:
    try:
        return type(data).model_validate(data.model_dump(mode="python"), strict=True)
    except ValidationError as error:
        raise ModelInputError("training evidence failed strict revalidation") from error


def _validate_training_shape(
    rows: Sequence[Sequence[float]],
    targets: Sequence[object],
    sample_keys: Sequence[SampleKey],
    feature_names: Sequence[str],
) -> None:
    if len(rows) != len(targets) or len(rows) != len(sample_keys):
        raise ValueError("training rows, targets, and sample keys must align")
    if not rows or not feature_names:
        raise ValueError("training evidence cannot be empty")
    if any(len(row) != len(feature_names) for row in rows):
        raise ValueError("feature rows do not match feature schema")
    if len(set(sample_keys)) != len(sample_keys):
        raise ValueError("sample keys must be unique")


def _validate_partition_binding(
    partition: TrainingPartitionEvidence,
    sample_keys: Sequence[SampleKey],
    dataset_input_hash: str,
) -> None:
    try:
        checked = TrainingPartitionEvidence.model_validate(
            partition.model_dump(mode="python"), strict=True
        )
    except (AttributeError, ValidationError) as error:
        raise ValueError("registered TRAIN partition is invalid") from error
    if checked.evaluation_window.role is not EvaluationRole.TRAIN:
        raise ValueError("training data requires a TRAIN partition")
    if checked.evaluation_window.dataset_input_hash != dataset_input_hash:
        raise ValueError("training data dataset hash differs from partition")
    if tuple(sample_keys) != checked.registered_sample_keys:
        raise ValueError("training data keys differ from registered partition")
