"""Frozen train-only statistical and rule baselines for M4 comparisons."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, TypeVar

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sklearn.linear_model import (
    LogisticRegression,
    QuantileRegressor,
)

from stock_prediction.contracts.market_data import Identifier
from stock_prediction.contracts.research import Horizon, Sha256
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.modeling.contracts import TaskName


class BaselineInputError(ValueError):
    """Baseline training or inference input violates frozen fit evidence."""


class BaselineEvidence(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    task: TaskName
    horizon: Horizon
    training_start: UtcDatetime
    training_end: UtcDatetime
    dataset_input_hash: Sha256
    seed: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_training_window(self) -> "BaselineEvidence":
        if self.training_start > self.training_end:
            raise ValueError("baseline training window is reversed")
        return self


class HistoricalFrequencyBaseline(BaselineEvidence):
    baseline_kind: Literal["historical_frequency"] = "historical_frequency"
    class_order: tuple[Identifier, ...] = Field(min_length=2)
    class_probabilities: tuple[float, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_frequency(self) -> "HistoricalFrequencyBaseline":
        _validate_classes(self.class_order)
        if len(self.class_probabilities) != len(self.class_order):
            raise ValueError("class probabilities do not match class order")
        _validate_simplex(self.class_probabilities)
        return self

    @classmethod
    def fit(
        cls,
        labels: Sequence[str],
        *,
        classes: Sequence[str],
        task: TaskName,
        horizon: Horizon,
        training_start: UtcDatetime,
        training_end: UtcDatetime,
        dataset_input_hash: str,
        seed: int,
    ) -> "HistoricalFrequencyBaseline":
        class_order = _validate_classes(classes)
        label_order = tuple(labels)
        if not label_order:
            raise BaselineInputError("frequency baseline requires training labels")
        unknown = set(label_order) - set(class_order)
        if unknown:
            raise BaselineInputError("training labels contain an unknown class")
        counts = np.asarray(
            [label_order.count(name) for name in class_order], dtype=np.float64
        )
        probabilities = counts / len(label_order)
        return cls(
            task=task,
            horizon=horizon,
            training_start=training_start,
            training_end=training_end,
            dataset_input_hash=dataset_input_hash,
            seed=seed,
            class_order=class_order,
            class_probabilities=tuple(float(value) for value in probabilities),
        )

    def predict_proba(
        self, rows: Sequence[Mapping[str, object]] | int
    ) -> np.ndarray:
        artifact = _revalidate_artifact(self)
        row_count = _row_count(rows)
        return _readonly(
            np.tile(
                np.asarray(artifact.class_probabilities, dtype=np.float64),
                (row_count, 1),
            )
        )


class ChannelAtrRuleBaseline(BaselineEvidence):
    baseline_kind: Literal["channel_atr_rule"] = "channel_atr_rule"
    class_order: tuple[Identifier, ...] = Field(min_length=3, max_length=3)
    channel_buffer: float = Field(ge=0)
    atr_confirmation: float = Field(gt=0)
    winning_probability: float = Field(gt=0.5, lt=1)

    @model_validator(mode="after")
    def validate_rule_classes(self) -> "ChannelAtrRuleBaseline":
        if set(self.class_order) != {"down", "flat", "up"}:
            raise ValueError("channel rule classes must be down, flat, and up")
        return self

    @classmethod
    def create(
        cls,
        *,
        classes: Sequence[str],
        channel_buffer: float,
        task: TaskName,
        horizon: Horizon,
        training_start: UtcDatetime,
        training_end: UtcDatetime,
        dataset_input_hash: str,
        seed: int,
        atr_confirmation: float = 1.0,
        winning_probability: float = 0.85,
    ) -> "ChannelAtrRuleBaseline":
        class_order = _validate_classes(classes)
        try:
            return cls(
                task=task,
                horizon=horizon,
                training_start=training_start,
                training_end=training_end,
                dataset_input_hash=dataset_input_hash,
                seed=seed,
                class_order=class_order,
                channel_buffer=channel_buffer,
                atr_confirmation=atr_confirmation,
                winning_probability=winning_probability,
            )
        except ValueError as error:
            raise BaselineInputError("channel ATR rule parameters are invalid") from error

    def predict_proba(self, rows: Sequence[Mapping[str, object]]) -> np.ndarray:
        artifact = _revalidate_artifact(self)
        if not rows:
            raise BaselineInputError("channel rule requires at least one row")
        residual = (1.0 - artifact.winning_probability) / 2
        probabilities = np.empty(
            (len(rows), len(artifact.class_order)), dtype=np.float64
        )
        index_by_class = {
            name: index for index, name in enumerate(artifact.class_order)
        }
        for row_index, row in enumerate(rows):
            channel_position = _finite_feature(row, "channel_position")
            atr_score = _finite_feature(row, "atr_breakout_score")
            if (
                channel_position >= 1 + artifact.channel_buffer
                and atr_score >= artifact.atr_confirmation
            ):
                winner = "up"
            elif (
                channel_position <= -1 - artifact.channel_buffer
                and atr_score <= -artifact.atr_confirmation
            ):
                winner = "down"
            else:
                winner = "flat"
            probabilities[row_index, :] = residual
            probabilities[row_index, index_by_class[winner]] = (
                artifact.winning_probability
            )
        return _readonly(probabilities)


class LogisticBaseline(BaselineEvidence):
    baseline_kind: Literal["logistic"] = "logistic"
    class_order: tuple[Identifier, ...] = Field(min_length=2)
    feature_names: tuple[Identifier, ...] = Field(min_length=1)
    coefficients: tuple[tuple[float, ...], ...] = Field(min_length=1)
    intercepts: tuple[float, ...] = Field(min_length=1)
    c: float = Field(gt=0)
    max_iter: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_logistic_shape(self) -> "LogisticBaseline":
        _validate_classes(self.class_order)
        _validate_feature_names(self.feature_names)
        expected_rows = 1 if len(self.class_order) == 2 else len(self.class_order)
        if len(self.coefficients) != expected_rows or len(self.intercepts) != expected_rows:
            raise ValueError("logistic coefficient rows do not match classes")
        if any(len(row) != len(self.feature_names) for row in self.coefficients):
            raise ValueError("logistic coefficients do not match feature schema")
        return self

    @classmethod
    def fit(
        cls,
        features: object,
        labels: Sequence[str],
        *,
        feature_names: Sequence[str],
        classes: Sequence[str],
        c: float,
        max_iter: int,
        task: TaskName,
        horizon: Horizon,
        training_start: UtcDatetime,
        training_end: UtcDatetime,
        dataset_input_hash: str,
        seed: int,
    ) -> "LogisticBaseline":
        class_order = _validate_classes(classes)
        schema = _validate_feature_names(feature_names)
        matrix = _feature_matrix(features, len(schema))
        label_order = tuple(labels)
        if len(label_order) != len(matrix):
            raise BaselineInputError("logistic labels do not align with features")
        unknown = set(label_order) - set(class_order)
        if unknown:
            raise BaselineInputError("logistic labels contain an unknown class")
        index_by_class = {name: index for index, name in enumerate(class_order)}
        encoded = np.asarray([index_by_class[label] for label in label_order], dtype=np.int64)
        if set(encoded) != set(range(len(class_order))):
            raise BaselineInputError("logistic training requires every declared class")
        try:
            estimator = LogisticRegression(
                C=c,
                max_iter=max_iter,
                random_state=seed,
                solver="lbfgs",
            ).fit(matrix, encoded)
        except (TypeError, ValueError) as error:
            raise BaselineInputError("logistic training failed") from error
        return cls(
            task=task,
            horizon=horizon,
            training_start=training_start,
            training_end=training_end,
            dataset_input_hash=dataset_input_hash,
            seed=seed,
            class_order=class_order,
            feature_names=schema,
            coefficients=tuple(
                tuple(float(value) for value in row) for row in estimator.coef_
            ),
            intercepts=tuple(float(value) for value in estimator.intercept_),
            c=c,
            max_iter=max_iter,
        )

    def predict_proba(
        self,
        features: object,
        *,
        feature_names: Sequence[str],
    ) -> np.ndarray:
        artifact = _revalidate_artifact(self)
        _validate_inference_schema(feature_names, artifact.feature_names)
        matrix = _feature_matrix(features, len(artifact.feature_names))
        coefficients = np.asarray(artifact.coefficients, dtype=np.float64)
        intercepts = np.asarray(artifact.intercepts, dtype=np.float64)
        scores = matrix @ coefficients.T + intercepts
        if len(artifact.class_order) == 2:
            positive = 1.0 / (1.0 + np.exp(-np.clip(scores[:, 0], -709, 709)))
            probabilities = np.column_stack((1.0 - positive, positive))
        else:
            shifted = scores - np.max(scores, axis=1, keepdims=True)
            exponential = np.exp(shifted)
            probabilities = exponential / np.sum(exponential, axis=1, keepdims=True)
        return _readonly(probabilities)


class HistoricalQuantileBaseline(BaselineEvidence):
    baseline_kind: Literal["historical_quantile"] = "historical_quantile"
    quantiles: tuple[float, ...] = Field(min_length=1)
    fitted_quantiles: tuple[float, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_quantile_shape(self) -> "HistoricalQuantileBaseline":
        _validate_quantiles(self.quantiles)
        if len(self.quantiles) != len(self.fitted_quantiles):
            raise ValueError("fitted quantiles do not match quantile order")
        return self

    @classmethod
    def fit(
        cls,
        outcomes: Sequence[float],
        *,
        quantiles: Sequence[float],
        task: TaskName,
        horizon: Horizon,
        training_start: UtcDatetime,
        training_end: UtcDatetime,
        dataset_input_hash: str,
        seed: int,
    ) -> "HistoricalQuantileBaseline":
        quantile_order = _validate_quantiles(quantiles)
        values = _finite_vector(outcomes, "historical quantile outcomes")
        fitted = np.quantile(values, quantile_order, method="linear")
        return cls(
            task=task,
            horizon=horizon,
            training_start=training_start,
            training_end=training_end,
            dataset_input_hash=dataset_input_hash,
            seed=seed,
            quantiles=quantile_order,
            fitted_quantiles=tuple(float(value) for value in fitted),
        )

    def predict(self, row_count: int) -> np.ndarray:
        artifact = _revalidate_artifact(self)
        if row_count < 1:
            raise BaselineInputError("quantile prediction row count must be positive")
        return _readonly(
            np.tile(
                np.asarray(artifact.fitted_quantiles, dtype=np.float64),
                (row_count, 1),
            )
        )


class LinearQuantileBaseline(BaselineEvidence):
    baseline_kind: Literal["linear_quantile"] = "linear_quantile"
    feature_names: tuple[Identifier, ...] = Field(min_length=1)
    quantiles: tuple[float, ...] = Field(min_length=1)
    coefficients: tuple[tuple[float, ...], ...] = Field(min_length=1)
    intercepts: tuple[float, ...] = Field(min_length=1)
    alpha: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_linear_quantile_shape(self) -> "LinearQuantileBaseline":
        _validate_feature_names(self.feature_names)
        _validate_quantiles(self.quantiles)
        if len(self.coefficients) != len(self.quantiles) or len(self.intercepts) != len(
            self.quantiles
        ):
            raise ValueError("linear quantile rows do not match quantiles")
        if any(len(row) != len(self.feature_names) for row in self.coefficients):
            raise ValueError("linear quantile coefficients do not match feature schema")
        return self

    @classmethod
    def fit(
        cls,
        features: object,
        outcomes: Sequence[float],
        *,
        feature_names: Sequence[str],
        quantiles: Sequence[float],
        alpha: float,
        task: TaskName,
        horizon: Horizon,
        training_start: UtcDatetime,
        training_end: UtcDatetime,
        dataset_input_hash: str,
        seed: int,
    ) -> "LinearQuantileBaseline":
        schema = _validate_feature_names(feature_names)
        quantile_order = _validate_quantiles(quantiles)
        matrix = _feature_matrix(features, len(schema))
        targets = _finite_vector(outcomes, "linear quantile outcomes")
        if len(matrix) != len(targets):
            raise BaselineInputError("linear quantile outcomes do not align with features")
        if alpha < 0 or not np.isfinite(alpha):
            raise BaselineInputError("linear quantile alpha must be finite and non-negative")
        coefficients: list[tuple[float, ...]] = []
        intercepts: list[float] = []
        for quantile in quantile_order:
            try:
                estimator = QuantileRegressor(
                    quantile=quantile,
                    alpha=alpha,
                    solver="highs",
                ).fit(matrix, targets)
            except (TypeError, ValueError) as error:
                raise BaselineInputError("linear quantile training failed") from error
            coefficients.append(tuple(float(value) for value in estimator.coef_))
            intercepts.append(float(estimator.intercept_))
        return cls(
            task=task,
            horizon=horizon,
            training_start=training_start,
            training_end=training_end,
            dataset_input_hash=dataset_input_hash,
            seed=seed,
            feature_names=schema,
            quantiles=quantile_order,
            coefficients=tuple(coefficients),
            intercepts=tuple(intercepts),
            alpha=alpha,
        )

    def predict(
        self,
        features: object,
        *,
        feature_names: Sequence[str],
    ) -> np.ndarray:
        artifact = _revalidate_artifact(self)
        _validate_inference_schema(feature_names, artifact.feature_names)
        matrix = _feature_matrix(features, len(artifact.feature_names))
        coefficients = np.asarray(artifact.coefficients, dtype=np.float64)
        intercepts = np.asarray(artifact.intercepts, dtype=np.float64)
        return _readonly(matrix @ coefficients.T + intercepts)


def _validate_classes(classes: Sequence[str]) -> tuple[str, ...]:
    class_order = tuple(classes)
    if len(class_order) < 2 or len(set(class_order)) != len(class_order):
        raise BaselineInputError("classes must contain at least two unique values")
    return class_order


def _validate_feature_names(feature_names: Sequence[str]) -> tuple[str, ...]:
    schema = tuple(feature_names)
    if not schema or len(set(schema)) != len(schema):
        raise BaselineInputError("feature schema must contain unique names")
    return schema


def _validate_inference_schema(
    feature_names: Sequence[str],
    fitted_feature_names: tuple[str, ...],
) -> None:
    inference_schema = _validate_feature_names(feature_names)
    if len(inference_schema) != len(fitted_feature_names):
        raise BaselineInputError("feature schema does not match fitted baseline")
    if inference_schema != fitted_feature_names:
        raise BaselineInputError("feature order does not match fitted baseline")


def _validate_quantiles(quantiles: Sequence[float]) -> tuple[float, ...]:
    order = tuple(float(value) for value in quantiles)
    if not order or any(not np.isfinite(value) or not 0 < value < 1 for value in order):
        raise BaselineInputError("quantiles must be finite and strictly inside zero/one")
    if tuple(sorted(set(order))) != order:
        raise BaselineInputError("quantiles must be unique and increasing")
    return order


def _validate_simplex(probabilities: Sequence[float]) -> None:
    values = np.asarray(probabilities, dtype=np.float64)
    if (
        not np.all(np.isfinite(values))
        or np.any(values < 0)
        or np.any(values > 1)
        or not np.isclose(float(np.sum(values)), 1.0, rtol=0, atol=1e-9)
    ):
        raise BaselineInputError("class probabilities must form a finite simplex")


def _row_count(rows: Sequence[Mapping[str, object]] | int) -> int:
    count = rows if isinstance(rows, int) else len(rows)
    if count < 1:
        raise BaselineInputError("prediction requires at least one row")
    return count


def _finite_feature(row: Mapping[str, object], name: str) -> float:
    value = row.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BaselineInputError(f"{name} must be a finite numeric feature")
    numeric = float(value)
    if not np.isfinite(numeric):
        raise BaselineInputError(f"{name} must be a finite numeric feature")
    return numeric


def _feature_matrix(features: object, expected_columns: int) -> np.ndarray:
    try:
        matrix = np.asarray(features, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise BaselineInputError("features must be a finite numeric matrix") from error
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] != expected_columns:
        raise BaselineInputError("feature schema does not match fitted baseline")
    if not np.all(np.isfinite(matrix)):
        raise BaselineInputError("features must be finite")
    return matrix


def _finite_vector(values: Sequence[float], name: str) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1 or len(array) == 0:
        raise BaselineInputError(f"{name} must be a non-empty vector")
    if not np.all(np.isfinite(array)):
        raise BaselineInputError(f"{name} must be finite")
    return array


def _readonly(values: np.ndarray) -> np.ndarray:
    values.setflags(write=False)
    return values


BaselineArtifact = TypeVar("BaselineArtifact", bound=BaselineEvidence)


def _revalidate_artifact(artifact: BaselineArtifact) -> BaselineArtifact:
    try:
        validated = type(artifact).model_validate(
            artifact.model_dump(mode="python"), strict=True
        )
    except (ValidationError, ValueError) as error:
        raise BaselineInputError("baseline artifact failed strict validation") from error
    return validated
