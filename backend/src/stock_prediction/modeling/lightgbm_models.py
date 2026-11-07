"""Deterministic CPU LightGBM trainers and auditable native explanations."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self, cast

import lightgbm as lgb
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import to_jsonable_python

from stock_prediction.contracts.market_data import Identifier, VersionId
from stock_prediction.contracts.research import Horizon, Sha256
from stock_prediction.modeling.contracts import TaskName
from stock_prediction.modeling.training import (
    ClassificationTrainingData,
    ModelInputError,
    RegressionTrainingData,
    revalidate_training_data,
)


_QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)


class ClassifierConfig(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    task: TaskName
    horizon: Horizon
    model_version: VersionId
    asset_group: Identifier
    class_order: tuple[Identifier, ...] = Field(min_length=2)
    categorical_feature_names: tuple[Identifier, ...] = ()
    seed: int = Field(ge=0)
    num_threads: int = Field(default=12, ge=1, le=16)
    num_boost_round: int = Field(ge=1, le=5000)
    learning_rate: float = Field(gt=0, le=1)
    num_leaves: int = Field(ge=2, le=1024)
    min_data_in_leaf: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_classes(self) -> Self:
        if len(set(self.class_order)) != len(self.class_order):
            raise ValueError("class_order must contain unique names")
        if self.task not in {
            TaskName.FORECAST_DIRECTION,
            TaskName.EXECUTABLE_DIRECTION,
            TaskName.BREAKOUT,
            TaskName.RANGE_EXIT_SURVIVAL,
        }:
            raise ValueError("classifier task is not a classification task")
        return self


class QuantileConfig(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    task: TaskName
    horizon: Horizon
    model_version: VersionId
    asset_group: Identifier
    categorical_feature_names: tuple[Identifier, ...] = ()
    seed: int = Field(ge=0)
    num_threads: int = Field(default=12, ge=1, le=16)
    num_boost_round: int = Field(ge=1, le=5000)
    learning_rate: float = Field(gt=0, le=1)
    num_leaves: int = Field(ge=2, le=1024)
    min_data_in_leaf: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_task(self) -> Self:
        if self.task not in {
            TaskName.CAPITAL_GAIN_RETURN,
            TaskName.TOTAL_RETURN,
            TaskName.EXECUTABLE_RETURN,
            TaskName.FORECAST_MFE,
            TaskName.FORECAST_MAE,
            TaskName.EXECUTABLE_MFE,
            TaskName.EXECUTABLE_MAE,
        }:
            raise ValueError("quantile task is not a distribution task")
        return self


class FeatureDistribution(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    p05: float
    p25: float
    p50: float
    p75: float
    p95: float

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        values = (self.p05, self.p25, self.p50, self.p75, self.p95)
        if tuple(sorted(values)) != values:
            raise ValueError("feature distribution quantiles must be ordered")
        return self


class ClassifierArtifact(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    task: TaskName
    horizon: Horizon
    model_version: VersionId
    asset_group: Identifier
    class_order: tuple[Identifier, ...] = Field(min_length=2)
    feature_names: tuple[Identifier, ...] = Field(min_length=1)
    categorical_feature_names: tuple[Identifier, ...]
    feature_distributions: tuple[FeatureDistribution, ...] = Field(min_length=1)
    class_counts: tuple[int, ...] = Field(min_length=2)
    class_weights: tuple[float, ...] = Field(min_length=2)
    seed: int = Field(ge=0)
    num_threads: int = Field(ge=1, le=16)
    training_input_hash: Sha256
    training_partition_hash: Sha256
    dataset_input_hash: Sha256
    model_text: str = Field(min_length=1)
    model_hash: Sha256
    lightgbm_version: str = Field(min_length=1)
    numpy_version: str = Field(min_length=1)
    fit_duration_ms: float = Field(ge=0)
    training_config: ClassifierConfig
    artifact_hash: Sha256

    @model_validator(mode="before")
    @classmethod
    def bind_artifact_hash(cls, values: Any) -> Any:
        return _bind_artifact_hash(values)

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        if len(set(self.class_order)) != len(self.class_order):
            raise ValueError("artifact class order is invalid")
        if not (
            len(self.class_order)
            == len(self.class_counts)
            == len(self.class_weights)
        ):
            raise ValueError("artifact class evidence is misaligned")
        if len(self.feature_names) != len(self.feature_distributions):
            raise ValueError("artifact feature distributions are misaligned")
        if not set(self.categorical_feature_names).issubset(self.feature_names):
            raise ValueError("artifact categorical feature schema is invalid")
        if any(count <= 0 for count in self.class_counts):
            raise ValueError("artifact must contain every declared class")
        if _sha256(self.model_text) != self.model_hash:
            raise ValueError("artifact model hash does not match model text")
        _validate_booster_contract(
            self.model_text,
            objective="binary" if len(self.class_order) == 2 else "multiclass",
            feature_names=self.feature_names,
            class_count=len(self.class_order),
        )
        if self.training_config.task != self.task:
            raise ValueError("artifact task does not match training config")
        if self.training_config.horizon != self.horizon:
            raise ValueError("artifact horizon does not match training config")
        if self.training_config.model_version != self.model_version:
            raise ValueError("artifact version does not match training config")
        if self.training_config.asset_group != self.asset_group:
            raise ValueError("artifact asset group does not match training config")
        if self.training_config.class_order != self.class_order:
            raise ValueError("artifact class order does not match training config")
        if (
            self.training_config.categorical_feature_names
            != self.categorical_feature_names
        ):
            raise ValueError("artifact categorical schema does not match training config")
        if self.training_config.seed != self.seed or self.training_config.num_threads != self.num_threads:
            raise ValueError("artifact runtime evidence does not match training config")
        if self.artifact_hash != _artifact_hash(
            self.model_dump(mode="python", exclude={"artifact_hash"})
        ):
            raise ValueError("artifact hash does not match classifier evidence")
        return self

    def predict_proba(
        self,
        features: object,
        *,
        feature_names: Sequence[str],
        expected_artifact_hash: str,
    ) -> np.ndarray:
        artifact = _revalidate_classifier(self, expected_artifact_hash)
        matrix = _prediction_matrix(features, feature_names, artifact.feature_names)
        booster = _load_booster(artifact.model_text)
        raw = np.asarray(booster.predict(matrix), dtype=np.float64)
        if len(artifact.class_order) == 2:
            positive = raw.reshape(-1)
            probabilities = np.column_stack((1.0 - positive, positive))
        else:
            probabilities = raw.reshape(len(matrix), len(artifact.class_order))
        if (
            not np.all(np.isfinite(probabilities))
            or np.any(probabilities < 0.0)
            or np.any(probabilities > 1.0)
            or not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-8)
        ):
            raise ModelInputError("artifact produced invalid class probabilities")
        return _readonly(probabilities)


class QuantileModelSet(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    task: TaskName
    horizon: Horizon
    model_version: VersionId
    asset_group: Identifier
    quantiles: tuple[float, float, float, float, float]
    feature_names: tuple[Identifier, ...] = Field(min_length=1)
    categorical_feature_names: tuple[Identifier, ...]
    feature_distributions: tuple[FeatureDistribution, ...] = Field(min_length=1)
    seed: int = Field(ge=0)
    num_threads: int = Field(ge=1, le=16)
    training_input_hash: Sha256
    training_partition_hash: Sha256
    dataset_input_hash: Sha256
    model_texts: tuple[str, str, str, str, str]
    model_hashes: tuple[Sha256, Sha256, Sha256, Sha256, Sha256]
    lightgbm_version: str = Field(min_length=1)
    numpy_version: str = Field(min_length=1)
    fit_duration_ms: float = Field(ge=0)
    training_config: QuantileConfig
    artifact_hash: Sha256

    @model_validator(mode="before")
    @classmethod
    def bind_artifact_hash(cls, values: Any) -> Any:
        return _bind_artifact_hash(values)

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        if self.quantiles != _QUANTILES:
            raise ValueError("quantile artifact must contain the fixed five alphas")
        if len(self.feature_names) != len(self.feature_distributions):
            raise ValueError("artifact feature distributions are misaligned")
        if not set(self.categorical_feature_names).issubset(self.feature_names):
            raise ValueError("artifact categorical feature schema is invalid")
        if any(_sha256(text) != digest for text, digest in zip(self.model_texts, self.model_hashes, strict=True)):
            raise ValueError("quantile artifact model hash does not match model text")
        for text in self.model_texts:
            _validate_booster_contract(
                text,
                objective="quantile",
                feature_names=self.feature_names,
                class_count=1,
            )
        if (
            self.training_config.task != self.task
            or self.training_config.horizon != self.horizon
            or self.training_config.model_version != self.model_version
            or self.training_config.asset_group != self.asset_group
            or self.training_config.seed != self.seed
            or self.training_config.num_threads != self.num_threads
            or self.training_config.categorical_feature_names
            != self.categorical_feature_names
        ):
            raise ValueError("quantile artifact does not match training config")
        if self.artifact_hash != _artifact_hash(
            self.model_dump(mode="python", exclude={"artifact_hash"})
        ):
            raise ValueError("artifact hash does not match quantile evidence")
        return self

    def predict(
        self,
        features: object,
        *,
        feature_names: Sequence[str],
        expected_artifact_hash: str,
    ) -> np.ndarray:
        artifact = _revalidate_quantiles(self, expected_artifact_hash)
        matrix = _prediction_matrix(features, feature_names, artifact.feature_names)
        columns = [
            np.asarray(_load_booster(text).predict(matrix), dtype=np.float64).reshape(-1)
            for text in artifact.model_texts
        ]
        result = np.column_stack(columns)
        if not np.all(np.isfinite(result)):
            raise ModelInputError("quantile artifact produced non-finite predictions")
        return _readonly(result)


class FeatureContribution(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    feature_name: Identifier
    contribution: float
    feature_value: float
    training_percentile: float = Field(ge=0, le=1)


class TreeExplanation(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    model_version: VersionId
    class_name: Identifier
    raw_score: float
    bias: float
    contribution_sum: float
    top_contributions: tuple[FeatureContribution, ...]
    non_causal: Literal[True] = True


class LightGbmClassifierTrainer:
    def __init__(self, config: ClassifierConfig) -> None:
        self.config = ClassifierConfig.model_validate(
            config.model_dump(mode="python"), strict=True
        )

    def fit(self, data: ClassificationTrainingData) -> ClassifierArtifact:
        checked = revalidate_training_data(data)
        if not isinstance(checked, ClassificationTrainingData):
            raise ModelInputError("classifier requires classification training data")
        _validate_training_horizon(checked.sample_keys, self.config.horizon)
        if checked.partition.task != self.config.task:
            raise ModelInputError("training partition task does not match model config")
        if checked.partition.horizon != self.config.horizon:
            raise ModelInputError("training partition horizon does not match model config")
        labels = checked.labels
        unknown = set(labels) - set(self.config.class_order)
        if unknown:
            raise ModelInputError("training labels contain an unknown class")
        counts = tuple(labels.count(name) for name in self.config.class_order)
        if any(count == 0 for count in counts):
            raise ModelInputError("classifier training requires every declared class")
        class_count = len(self.config.class_order)
        weights = tuple(len(labels) / (class_count * count) for count in counts)
        index_by_class = {
            name: index for index, name in enumerate(self.config.class_order)
        }
        encoded = np.asarray([index_by_class[label] for label in labels], dtype=np.int32)
        row_weights = np.asarray([weights[index] for index in encoded], dtype=np.float64)
        categorical = _validate_categorical_schema(
            self.config.categorical_feature_names, checked.feature_names
        )
        params = _base_params(self.config)
        if class_count == 2:
            params["objective"] = "binary"
        else:
            params.update({"objective": "multiclass", "num_class": class_count})
        training_set = lgb.Dataset(
            checked.features(),
            label=encoded,
            weight=row_weights,
            feature_name=list(checked.feature_names),
            categorical_feature=list(categorical),
            free_raw_data=True,
        )
        started = time.perf_counter()
        try:
            booster = lgb.train(
                params,
                training_set,
                num_boost_round=self.config.num_boost_round,
            )
        except (TypeError, ValueError, lgb.basic.LightGBMError) as error:
            raise ModelInputError("LightGBM classifier training failed") from error
        duration = (time.perf_counter() - started) * 1000
        model_text = booster.model_to_string()
        payload: dict[str, object] = {
            "task": self.config.task,
            "horizon": self.config.horizon,
            "model_version": self.config.model_version,
            "asset_group": self.config.asset_group,
            "class_order": self.config.class_order,
            "feature_names": checked.feature_names,
            "categorical_feature_names": categorical,
            "feature_distributions": _feature_distributions(checked.features()),
            "class_counts": counts,
            "class_weights": weights,
            "seed": self.config.seed,
            "num_threads": self.config.num_threads,
            "training_input_hash": checked.input_hash,
            "training_partition_hash": checked.partition.partition_hash,
            "dataset_input_hash": checked.dataset_input_hash,
            "model_text": model_text,
            "model_hash": _sha256(model_text),
            "lightgbm_version": lgb.__version__,
            "numpy_version": np.__version__,
            "fit_duration_ms": duration,
            "training_config": self.config,
        }
        payload["artifact_hash"] = _artifact_hash(payload)
        return ClassifierArtifact.model_validate(payload, strict=True)


class LightGbmQuantileTrainer:
    def __init__(self, config: QuantileConfig) -> None:
        self.config = QuantileConfig.model_validate(
            config.model_dump(mode="python"), strict=True
        )

    def fit(self, data: RegressionTrainingData) -> QuantileModelSet:
        checked = revalidate_training_data(data)
        if not isinstance(checked, RegressionTrainingData):
            raise ModelInputError("quantile trainer requires regression training data")
        _validate_training_horizon(checked.sample_keys, self.config.horizon)
        if checked.partition.task != self.config.task:
            raise ModelInputError("training partition task does not match model config")
        if checked.partition.horizon != self.config.horizon:
            raise ModelInputError("training partition horizon does not match model config")
        matrix = checked.features()
        categorical = _validate_categorical_schema(
            self.config.categorical_feature_names, checked.feature_names
        )
        texts: list[str] = []
        started = time.perf_counter()
        for alpha in _QUANTILES:
            params = _base_params(self.config)
            params.update({"objective": "quantile", "alpha": alpha})
            training_set = lgb.Dataset(
                matrix,
                label=np.asarray(checked.outcomes, dtype=np.float64),
                feature_name=list(checked.feature_names),
                categorical_feature=list(categorical),
                free_raw_data=True,
            )
            try:
                booster = lgb.train(
                    params,
                    training_set,
                    num_boost_round=self.config.num_boost_round,
                )
            except (TypeError, ValueError, lgb.basic.LightGBMError) as error:
                raise ModelInputError("LightGBM quantile training failed") from error
            texts.append(booster.model_to_string())
        duration = (time.perf_counter() - started) * 1000
        model_texts = cast(tuple[str, str, str, str, str], tuple(texts))
        if len(model_texts) != 5:
            raise ModelInputError("quantile trainer did not produce five models")
        payload: dict[str, object] = {
            "task": self.config.task,
            "horizon": self.config.horizon,
            "model_version": self.config.model_version,
            "asset_group": self.config.asset_group,
            "quantiles": _QUANTILES,
            "feature_names": checked.feature_names,
            "categorical_feature_names": categorical,
            "feature_distributions": _feature_distributions(matrix),
            "seed": self.config.seed,
            "num_threads": self.config.num_threads,
            "training_input_hash": checked.input_hash,
            "training_partition_hash": checked.partition.partition_hash,
            "dataset_input_hash": checked.dataset_input_hash,
            "model_texts": model_texts,
            "model_hashes": cast(
                tuple[Sha256, Sha256, Sha256, Sha256, Sha256],
                tuple(_sha256(text) for text in model_texts),
            ),
            "lightgbm_version": lgb.__version__,
            "numpy_version": np.__version__,
            "fit_duration_ms": duration,
            "training_config": self.config,
        }
        payload["artifact_hash"] = _artifact_hash(payload)
        return QuantileModelSet.model_validate(payload, strict=True)


def explain_tree_prediction(
    artifact: ClassifierArtifact,
    features: object,
    *,
    feature_names: Sequence[str],
    class_name: str,
    top_n: int = 5,
    expected_artifact_hash: str,
) -> TreeExplanation:
    """Return native tree contributions as non-causal evidence."""

    checked = _revalidate_classifier(artifact, expected_artifact_hash)
    if class_name not in checked.class_order:
        raise ModelInputError("explanation class is not in artifact class order")
    if not 1 <= top_n <= 5:
        raise ModelInputError("top_n must be between one and five")
    row = _prediction_matrix(features, feature_names, checked.feature_names)
    if len(row) != 1:
        raise ModelInputError("tree explanation requires exactly one row")
    booster = _load_booster(checked.model_text)
    raw_values = np.asarray(booster.predict(row, raw_score=True), dtype=np.float64)
    contributions = np.asarray(booster.predict(row, pred_contrib=True), dtype=np.float64)
    class_index = checked.class_order.index(class_name)
    width = len(checked.feature_names) + 1
    if len(checked.class_order) == 2:
        signed = contributions.reshape(1, width)[0]
        raw_score = float(raw_values.reshape(-1)[0])
        if class_index == 0:
            signed = -signed
            raw_score = -raw_score
    else:
        signed = contributions.reshape(1, len(checked.class_order), width)[0, class_index]
        raw_score = float(raw_values.reshape(1, len(checked.class_order))[0, class_index])
    items = [
        FeatureContribution(
            feature_name=name,
            contribution=float(signed[index]),
            feature_value=float(row[0, index]),
            training_percentile=_training_percentile(
                float(row[0, index]), checked.feature_distributions[index]
            ),
        )
        for index, name in enumerate(checked.feature_names)
    ]
    items.sort(key=lambda item: (-abs(item.contribution), item.feature_name))
    return TreeExplanation(
        model_version=checked.model_version,
        class_name=class_name,
        raw_score=raw_score,
        bias=float(signed[-1]),
        contribution_sum=float(np.sum(signed)),
        top_contributions=tuple(items[:top_n]),
    )


def _base_params(config: ClassifierConfig | QuantileConfig) -> dict[str, object]:
    return {
        "metric": "None",
        "verbosity": -1,
        "learning_rate": config.learning_rate,
        "num_leaves": config.num_leaves,
        "min_data_in_leaf": config.min_data_in_leaf,
        "feature_pre_filter": False,
        "seed": config.seed,
        "feature_fraction_seed": config.seed,
        "bagging_seed": config.seed,
        "data_random_seed": config.seed,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": config.num_threads,
        "device_type": "cpu",
    }


def _validate_categorical_schema(
    categorical_names: Sequence[str], feature_names: Sequence[str]
) -> tuple[str, ...]:
    categorical = tuple(categorical_names)
    if len(set(categorical)) != len(categorical):
        raise ModelInputError("categorical feature names must be unique")
    if not set(categorical).issubset(feature_names):
        raise ModelInputError("categorical feature names must be in feature schema")
    return tuple(name for name in feature_names if name in categorical)


def _validate_training_horizon(
    sample_keys: Sequence[tuple[object, object, int]], expected_horizon: int
) -> None:
    if any(key[2] != expected_horizon for key in sample_keys):
        raise ModelInputError("training sample horizon does not match model config")


def _feature_distributions(matrix: np.ndarray) -> tuple[FeatureDistribution, ...]:
    values = np.quantile(matrix, (0.05, 0.25, 0.50, 0.75, 0.95), axis=0)
    return tuple(
        FeatureDistribution(
            p05=float(values[0, index]),
            p25=float(values[1, index]),
            p50=float(values[2, index]),
            p75=float(values[3, index]),
            p95=float(values[4, index]),
        )
        for index in range(matrix.shape[1])
    )


def _training_percentile(value: float, distribution: FeatureDistribution) -> float:
    points = np.asarray(
        [distribution.p05, distribution.p25, distribution.p50, distribution.p75, distribution.p95],
        dtype=np.float64,
    )
    percentages = np.asarray([0.05, 0.25, 0.50, 0.75, 0.95], dtype=np.float64)
    unique_points, indices = np.unique(points, return_index=True)
    unique_percentages = percentages[indices]
    if len(unique_points) == 1:
        return 0.5
    return float(np.interp(value, unique_points, unique_percentages, left=0.0, right=1.0))


def _prediction_matrix(
    features: object,
    supplied_names: Sequence[str],
    expected_names: Sequence[str],
) -> np.ndarray:
    if tuple(supplied_names) != tuple(expected_names):
        raise ModelInputError("feature order does not match the trained artifact")
    try:
        matrix = np.asarray(features, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ModelInputError("prediction features must be numeric") from error
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    if matrix.ndim != 2 or len(matrix) == 0 or matrix.shape[1] != len(expected_names):
        raise ModelInputError("prediction features do not match artifact schema")
    if not np.all(np.isfinite(matrix)):
        raise ModelInputError("prediction features must contain only finite values")
    return matrix


def _revalidate_classifier(
    artifact: ClassifierArtifact, expected_artifact_hash: str
) -> ClassifierArtifact:
    try:
        checked = ClassifierArtifact.model_validate(
            artifact.model_dump(mode="python"), strict=True
        )
    except (AttributeError, ValidationError) as error:
        raise ModelInputError("classifier artifact failed strict revalidation") from error
    if checked.artifact_hash != expected_artifact_hash:
        raise ModelInputError("classifier artifact does not match registered identity")
    return checked


def _revalidate_quantiles(
    artifact: QuantileModelSet, expected_artifact_hash: str
) -> QuantileModelSet:
    try:
        checked = QuantileModelSet.model_validate(
            artifact.model_dump(mode="python"), strict=True
        )
    except (AttributeError, ValidationError) as error:
        raise ModelInputError("quantile artifact failed strict revalidation") from error
    if checked.artifact_hash != expected_artifact_hash:
        raise ModelInputError("quantile artifact does not match registered identity")
    return checked


def _load_booster(model_text: str) -> lgb.Booster:
    try:
        return lgb.Booster(model_str=model_text)
    except lgb.basic.LightGBMError as error:
        raise ModelInputError("artifact contains an unreadable LightGBM model") from error


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifact_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            to_jsonable_python(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _bind_artifact_hash(values: Any) -> Any:
    if not isinstance(values, Mapping):
        return values
    payload = dict(values)
    supplied = payload.pop("artifact_hash", None)
    if supplied is None:
        raise ValueError("artifact hash is required from a trusted trainer")
    expected = _artifact_hash(payload)
    if supplied != expected:
        raise ValueError("artifact hash does not match serialized evidence")
    payload["artifact_hash"] = expected
    return payload


def _validate_booster_contract(
    model_text: str,
    *,
    objective: str,
    feature_names: Sequence[str],
    class_count: int,
) -> None:
    try:
        booster = lgb.Booster(model_str=model_text)
        model = booster.dump_model()
    except lgb.basic.LightGBMError as error:
        raise ValueError("artifact contains an unreadable LightGBM model") from error
    serialized_objective = str(model.get("objective", ""))
    if not serialized_objective.startswith(objective):
        raise ValueError("artifact LightGBM objective does not match model task")
    if tuple(booster.feature_name()) != tuple(feature_names):
        raise ValueError("artifact LightGBM feature schema does not match evidence")
    expected_internal_classes = 1 if class_count == 2 else class_count
    if objective == "quantile":
        expected_internal_classes = 1
    if int(model.get("num_class", 0)) != expected_internal_classes:
        raise ValueError("artifact LightGBM class count does not match evidence")
    expected_models = class_count if objective == "multiclass" else 1
    if booster.num_model_per_iteration() != expected_models:
        raise ValueError("artifact LightGBM tree layout does not match evidence")


def _readonly(values: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(result)):
        raise ModelInputError("model output must contain only finite values")
    result.setflags(write=False)
    return result


__all__ = [
    "ClassifierArtifact",
    "ClassifierConfig",
    "LightGbmClassifierTrainer",
    "LightGbmQuantileTrainer",
    "ModelInputError",
    "QuantileConfig",
    "QuantileModelSet",
    "TreeExplanation",
    "explain_tree_prediction",
]
