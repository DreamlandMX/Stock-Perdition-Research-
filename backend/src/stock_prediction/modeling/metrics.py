"""Deterministic statistical metrics for M4 model comparison and calibration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date
from types import MappingProxyType

import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_serializer,
    model_validator,
)
from sklearn.metrics import average_precision_score, log_loss

from stock_prediction.contracts.research import Horizon, Sha256
from stock_prediction.modeling.contracts import (
    EvaluationRole,
    EvaluationWindow,
    SampleKey,
    TaskName,
)


class MetricInputError(ValueError):
    """Metric inputs are empty, non-finite, misaligned, or semantically invalid."""


def relative_loss_improvement(*, candidate_loss: float, incumbent_loss: float) -> float:
    """Return positive improvement for a lower candidate loss."""

    if not np.isfinite(candidate_loss) or not np.isfinite(incumbent_loss):
        raise MetricInputError("loss values must be finite")
    if candidate_loss < 0 or incumbent_loss <= 0:
        raise MetricInputError(
            "loss values must be non-negative with positive incumbent"
        )
    return (incumbent_loss - candidate_loss) / incumbent_loss


class PerClassMetricReport(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    per_class: Mapping[str, float]
    macro_average: float

    @model_validator(mode="after")
    def freeze_mapping(self) -> "PerClassMetricReport":
        object.__setattr__(self, "per_class", MappingProxyType(dict(self.per_class)))
        return self

    @field_serializer("per_class")
    def serialize_mapping(self, value: Mapping[str, float]) -> dict[str, float]:
        return dict(value)


class CalibrationBin(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    bin_index: int = Field(ge=0)
    lower_bound: float = Field(ge=0, le=1)
    upper_bound: float = Field(ge=0, le=1)
    count: int = Field(ge=0)
    mean_confidence: float | None = Field(default=None, ge=0, le=1)
    empirical_accuracy: float | None = Field(default=None, ge=0, le=1)
    absolute_gap: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_empty_bin(self) -> "CalibrationBin":
        diagnostics = (
            self.mean_confidence,
            self.empirical_accuracy,
            self.absolute_gap,
        )
        if self.count == 0 and any(value is not None for value in diagnostics):
            raise ValueError("empty calibration bin cannot contain diagnostics")
        if self.count > 0 and any(value is None for value in diagnostics):
            raise ValueError("populated calibration bin requires diagnostics")
        return self


class CalibrationReport(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    bins: tuple[CalibrationBin, ...] = Field(min_length=1)
    ece: float = Field(ge=0, le=1)
    mce: float = Field(ge=0, le=1)
    sample_count: int = Field(ge=1)


class IntervalDiagnostics(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    target_coverage: float = Field(gt=0, lt=1)
    empirical_coverage: float = Field(ge=0, le=1)
    average_width: float = Field(ge=0)
    coverage_error: float = Field(ge=-1, le=1)
    sample_count: int = Field(ge=1)


class DailyIc(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    session: date
    ic: float = Field(ge=-1, le=1)
    sample_count: int = Field(ge=2)


class BootstrapInterval(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    point_estimate: float = Field(ge=-1, le=1)
    lower: float = Field(ge=-1, le=1)
    upper: float = Field(ge=-1, le=1)
    confidence: float = Field(gt=0, lt=1)
    n_bootstrap: int = Field(ge=1)
    n_dates: int = Field(ge=1)
    seed: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_interval(self) -> "BootstrapInterval":
        if self.lower > self.upper:
            raise ValueError("bootstrap interval is reversed")
        return self


class ClassFrequencyReference(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    task: TaskName
    horizon: Horizon
    training_window: EvaluationWindow
    class_order: tuple[str, ...] = Field(min_length=2)
    class_counts: tuple[int, ...] = Field(min_length=2)
    sample_count: int = Field(ge=1)
    training_sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    training_labels: tuple[str, ...] = Field(min_length=1)
    training_sample_keys_hash: Sha256
    training_labels_hash: Sha256
    reference_hash: Sha256

    @model_validator(mode="after")
    def validate_reference(self) -> "ClassFrequencyReference":
        if self.training_window.role is not EvaluationRole.TRAIN:
            raise ValueError("class frequency reference requires a TRAIN window")
        if len(set(self.class_order)) != len(self.class_order):
            raise ValueError("class frequency order must be unique")
        if len(self.class_counts) != len(self.class_order):
            raise ValueError("class frequency counts do not match class order")
        if any(count < 0 for count in self.class_counts):
            raise ValueError("class frequency counts must be non-negative")
        if sum(self.class_counts) != self.sample_count:
            raise ValueError("class frequency counts do not match sample count")
        if (
            len(self.training_sample_keys) != self.sample_count
            or len(self.training_labels) != self.sample_count
            or len(set(self.training_sample_keys)) != self.sample_count
        ):
            raise ValueError("class frequency sample keys and labels are not aligned")
        if any(label not in self.class_order for label in self.training_labels):
            raise ValueError("class frequency training labels contain an unknown class")
        expected_counts = tuple(
            self.training_labels.count(name) for name in self.class_order
        )
        if self.class_counts != expected_counts:
            raise ValueError("class frequency counts do not match training labels")
        if any(key[2] != self.horizon for key in self.training_sample_keys):
            raise ValueError("class frequency sample horizon does not match")
        if any(
            not self.training_window.start_session
            <= key[1].date()
            <= self.training_window.end_session
            for key in self.training_sample_keys
        ):
            raise ValueError("class frequency sample is outside TRAIN window")
        if self.training_sample_keys_hash != hash_sample_keys(
            self.training_sample_keys
        ):
            raise ValueError("class frequency sample key hash does not match")
        if self.training_labels_hash != hash_training_labels(
            self.training_sample_keys, self.training_labels
        ):
            raise ValueError("class frequency training label hash does not match")
        expected_hash = _class_frequency_reference_hash(
            task=self.task,
            horizon=self.horizon,
            training_window=self.training_window,
            class_order=self.class_order,
            class_counts=self.class_counts,
            sample_count=self.sample_count,
            training_sample_keys_hash=self.training_sample_keys_hash,
            training_labels_hash=self.training_labels_hash,
        )
        if self.reference_hash != expected_hash:
            raise ValueError("class frequency hash does not match count evidence")
        return self

    @property
    def probabilities(self) -> tuple[float, ...]:
        return tuple(count / self.sample_count for count in self.class_counts)


def fit_class_frequency_reference(
    labels: Sequence[str],
    *,
    sample_keys: Sequence[SampleKey],
    classes: Sequence[str],
    task: TaskName,
    horizon: Horizon,
    training_window: EvaluationWindow,
) -> ClassFrequencyReference:
    class_order = tuple(classes)
    label_order = tuple(labels)
    if len(class_order) < 2 or len(set(class_order)) != len(class_order):
        raise MetricInputError("class frequency classes must be unique")
    if not label_order:
        raise MetricInputError("class frequency requires training labels")
    unknown = set(label_order) - set(class_order)
    if unknown:
        raise MetricInputError("class frequency labels contain an unknown class")
    try:
        validated_keys = TypeAdapter(tuple[SampleKey, ...]).validate_python(
            tuple(sample_keys), strict=True
        )
        validated_window = EvaluationWindow.model_validate(
            training_window.model_dump(mode="python"), strict=True
        )
        if len(validated_keys) != len(label_order) or len(set(validated_keys)) != len(
            validated_keys
        ):
            raise ValueError("training labels require aligned unique sample keys")
        if any(key[2] != horizon for key in validated_keys):
            raise ValueError("training sample key horizon does not match reference")
        if any(
            not validated_window.start_session
            <= key[1].date()
            <= validated_window.end_session
            for key in validated_keys
        ):
            raise ValueError("training sample key is outside TRAIN window")
        sample_keys_hash = hash_sample_keys(validated_keys)
        labels_hash = hash_training_labels(validated_keys, label_order)
        class_counts = tuple(label_order.count(name) for name in class_order)
        return ClassFrequencyReference(
            task=task,
            horizon=horizon,
            training_window=validated_window,
            class_order=class_order,
            class_counts=class_counts,
            sample_count=len(label_order),
            training_sample_keys=validated_keys,
            training_labels=label_order,
            training_sample_keys_hash=sample_keys_hash,
            training_labels_hash=labels_hash,
            reference_hash=_class_frequency_reference_hash(
                task=task,
                horizon=horizon,
                training_window=validated_window,
                class_order=class_order,
                class_counts=class_counts,
                sample_count=len(label_order),
                training_sample_keys_hash=sample_keys_hash,
                training_labels_hash=labels_hash,
            ),
        )
    except (ValidationError, ValueError) as error:
        raise MetricInputError(
            f"class frequency reference is invalid: {error}"
        ) from error


def multiclass_brier_score(
    y_true: Sequence[str],
    probabilities: object,
    *,
    classes: Sequence[str],
) -> float:
    indices, matrix, class_order = _classification_inputs(
        y_true, probabilities, classes
    )
    one_hot = np.eye(len(class_order), dtype=np.float64)[indices]
    return float(np.mean(np.sum(np.square(matrix - one_hot), axis=1)))


def multiclass_brier_skill(
    y_true: Sequence[str],
    probabilities: object,
    *,
    classes: Sequence[str],
    reference: ClassFrequencyReference,
    task: TaskName,
    horizon: Horizon,
    dataset_input_hash: str,
    training_window_id: str,
    training_sample_keys_hash: str,
    training_labels_hash: str,
) -> float:
    indices, matrix, class_order = _classification_inputs(
        y_true, probabilities, classes
    )
    try:
        validated_reference = ClassFrequencyReference.model_validate(
            reference.model_dump(mode="python"), strict=True
        )
    except (ValidationError, ValueError) as error:
        raise MetricInputError(
            "class frequency reference artifact is invalid"
        ) from error
    if validated_reference.class_order != class_order:
        raise MetricInputError("train class frequency order must match classes")
    if validated_reference.task is not task or validated_reference.horizon != horizon:
        raise MetricInputError("class frequency task or horizon does not match")
    if (
        validated_reference.training_window.dataset_input_hash != dataset_input_hash
        or validated_reference.training_window.window_id != training_window_id
    ):
        raise MetricInputError("class frequency training evidence does not match")
    if (
        validated_reference.training_sample_keys_hash != training_sample_keys_hash
        or validated_reference.training_labels_hash != training_labels_hash
    ):
        raise MetricInputError("class frequency training labels do not match")
    reference_probabilities = np.asarray(
        validated_reference.probabilities, dtype=np.float64
    )
    _validate_probability_rows(reference_probabilities.reshape(1, -1))
    one_hot = np.eye(len(class_order), dtype=np.float64)[indices]
    model_score = float(np.mean(np.sum(np.square(matrix - one_hot), axis=1)))
    reference_score = float(
        np.mean(
            np.sum(
                np.square(reference_probabilities.reshape(1, -1) - one_hot),
                axis=1,
            )
        )
    )
    if reference_score <= 0:
        raise MetricInputError("train frequency reference has zero Brier loss")
    return 1.0 - model_score / reference_score


def multiclass_log_loss(
    y_true: Sequence[str],
    probabilities: object,
    *,
    classes: Sequence[str],
) -> float:
    indices, matrix, class_order = _classification_inputs(
        y_true, probabilities, classes
    )
    return float(log_loss(indices, matrix, labels=tuple(range(len(class_order)))))


def one_vs_rest_pr_auc(
    y_true: Sequence[str],
    probabilities: object,
    *,
    classes: Sequence[str],
) -> PerClassMetricReport:
    indices, matrix, class_order = _classification_inputs(
        y_true, probabilities, classes
    )
    scores: dict[str, float] = {}
    for class_index, class_name in enumerate(class_order):
        binary = (indices == class_index).astype(np.int8)
        if len(np.unique(binary)) != 2:
            raise MetricInputError(
                "PR-AUC requires positive and negative samples for every class"
            )
        scores[class_name] = float(
            average_precision_score(binary, matrix[:, class_index])
        )
    return PerClassMetricReport(
        per_class=scores,
        macro_average=float(np.mean(tuple(scores.values()))),
    )


def calibration_report(
    y_true: Sequence[str],
    probabilities: object,
    *,
    classes: Sequence[str],
    n_bins: int = 10,
) -> CalibrationReport:
    if n_bins < 2:
        raise MetricInputError("calibration requires at least two bins")
    indices, matrix, _ = _classification_inputs(y_true, probabilities, classes)
    predicted = np.argmax(matrix, axis=1)
    confidence = np.max(matrix, axis=1)
    correct = (predicted == indices).astype(np.float64)
    bin_ids = np.minimum((confidence * n_bins).astype(np.int64), n_bins - 1)
    bins: list[CalibrationBin] = []
    weighted_gap = 0.0
    maximum_gap = 0.0
    for bin_index in range(n_bins):
        mask = bin_ids == bin_index
        count = int(np.sum(mask))
        lower = bin_index / n_bins
        upper = (bin_index + 1) / n_bins
        if count == 0:
            bins.append(
                CalibrationBin(
                    bin_index=bin_index,
                    lower_bound=lower,
                    upper_bound=upper,
                    count=0,
                    mean_confidence=None,
                    empirical_accuracy=None,
                    absolute_gap=None,
                )
            )
            continue
        mean_confidence = float(np.mean(confidence[mask]))
        empirical_accuracy = float(np.mean(correct[mask]))
        gap = abs(empirical_accuracy - mean_confidence)
        weighted_gap += count * gap
        maximum_gap = max(maximum_gap, gap)
        bins.append(
            CalibrationBin(
                bin_index=bin_index,
                lower_bound=lower,
                upper_bound=upper,
                count=count,
                mean_confidence=mean_confidence,
                empirical_accuracy=empirical_accuracy,
                absolute_gap=gap,
            )
        )
    return CalibrationReport(
        bins=tuple(bins),
        ece=weighted_gap / len(indices),
        mce=maximum_gap,
        sample_count=len(indices),
    )


def mean_pinball_loss(
    y_true: Sequence[float],
    predictions: Sequence[float],
    *,
    quantile: float,
) -> float:
    if not 0 < quantile < 1:
        raise MetricInputError("quantile must be strictly between zero and one")
    actual, predicted = _paired_finite_vectors(y_true, predictions)
    residual = actual - predicted
    return float(np.mean(np.maximum(quantile * residual, (quantile - 1) * residual)))


def empirical_interval_diagnostics(
    y_true: Sequence[float],
    *,
    lower: Sequence[float],
    upper: Sequence[float],
    target_coverage: float,
) -> IntervalDiagnostics:
    if not 0 < target_coverage < 1:
        raise MetricInputError("target coverage must be strictly between zero and one")
    actual, lower_array = _paired_finite_vectors(y_true, lower)
    _, upper_array = _paired_finite_vectors(y_true, upper)
    if np.any(lower_array > upper_array):
        raise MetricInputError("lower interval bound exceeds upper bound")
    covered = (actual >= lower_array) & (actual <= upper_array)
    empirical = float(np.mean(covered))
    return IntervalDiagnostics(
        target_coverage=target_coverage,
        empirical_coverage=empirical,
        average_width=float(np.mean(upper_array - lower_array)),
        coverage_error=empirical - target_coverage,
        sample_count=len(actual),
    )


def quantile_crossing_rate(
    predictions: object,
    *,
    quantiles: Sequence[float],
) -> float:
    quantile_order = tuple(float(value) for value in quantiles)
    if not quantile_order or any(not 0 < value < 1 for value in quantile_order):
        raise MetricInputError("quantiles must be strictly between zero and one")
    if tuple(sorted(set(quantile_order))) != quantile_order:
        raise MetricInputError("quantiles must be unique and increasing")
    matrix = np.asarray(predictions, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        raise MetricInputError("quantile predictions must be a non-empty matrix")
    if matrix.shape[1] != len(quantile_order):
        raise MetricInputError("quantile columns do not match quantile order")
    if not np.all(np.isfinite(matrix)):
        raise MetricInputError("quantile predictions must be finite")
    crossed = np.any(np.diff(matrix, axis=1) < 0, axis=1)
    return float(np.mean(crossed))


def median_absolute_error(
    y_true: Sequence[float], predictions: Sequence[float]
) -> float:
    actual, predicted = _paired_finite_vectors(y_true, predictions)
    return float(np.mean(np.abs(actual - predicted)))


def spearman_ic_by_date(
    sessions: Sequence[date],
    outcomes: Sequence[float],
    scores: Sequence[float],
) -> tuple[DailyIc, ...]:
    if not (len(sessions) == len(outcomes) == len(scores)) or not sessions:
        raise MetricInputError("ranking inputs must be non-empty and aligned")
    actual, predicted = _paired_finite_vectors(outcomes, scores)
    grouped: dict[date, list[int]] = {}
    for index, session in enumerate(sessions):
        grouped.setdefault(session, []).append(index)
    results: list[DailyIc] = []
    for session in sorted(grouped):
        positions = grouped[session]
        if len(positions) < 2:
            raise MetricInputError(
                "each date cross-section requires at least two samples"
            )
        actual_slice = actual[positions]
        predicted_slice = predicted[positions]
        if len(np.unique(actual_slice)) < 2 or len(np.unique(predicted_slice)) < 2:
            raise MetricInputError("cross-section values must not be constant")
        actual_rank = _average_ranks(actual_slice)
        predicted_rank = _average_ranks(predicted_slice)
        ic = float(np.corrcoef(actual_rank, predicted_rank)[0, 1])
        if not np.isfinite(ic):
            raise MetricInputError("cross-section IC is not finite")
        results.append(DailyIc(session=session, ic=ic, sample_count=len(positions)))
    return tuple(results)


def date_block_bootstrap_ic(
    daily_ic: Sequence[DailyIc],
    *,
    n_bootstrap: int,
    confidence: float,
    seed: int,
) -> BootstrapInterval:
    if not daily_ic:
        raise MetricInputError("date-block bootstrap requires daily IC values")
    if n_bootstrap < 1:
        raise MetricInputError("n_bootstrap must be positive")
    if not 0 < confidence < 1:
        raise MetricInputError("confidence must be strictly between zero and one")
    if seed < 0:
        raise MetricInputError("seed must be non-negative")
    sessions = tuple(item.session for item in daily_ic)
    if len(set(sessions)) != len(sessions):
        raise MetricInputError("date-block bootstrap contains a duplicate session")
    values = np.asarray([item.ic for item in daily_ic], dtype=np.float64)
    if not np.all(np.isfinite(values)):
        raise MetricInputError("daily IC values must be finite")
    generator = np.random.default_rng(seed)
    sampled = generator.integers(0, len(values), size=(n_bootstrap, len(values)))
    bootstrap_means = np.mean(values[sampled], axis=1)
    alpha = (1 - confidence) / 2
    return BootstrapInterval(
        point_estimate=float(np.mean(values)),
        lower=float(np.quantile(bootstrap_means, alpha)),
        upper=float(np.quantile(bootstrap_means, 1 - alpha)),
        confidence=confidence,
        n_bootstrap=n_bootstrap,
        n_dates=len(values),
        seed=seed,
    )


def _classification_inputs(
    y_true: Sequence[str],
    probabilities: object,
    classes: Sequence[str],
) -> tuple[np.ndarray, np.ndarray, tuple[str, ...]]:
    class_order = tuple(classes)
    if len(class_order) < 2 or len(set(class_order)) != len(class_order):
        raise MetricInputError("classes must contain at least two unique values")
    labels = tuple(y_true)
    if not labels:
        raise MetricInputError("classification labels must not be empty")
    unknown = set(labels) - set(class_order)
    if unknown:
        raise MetricInputError("classification labels contain an unknown class")
    matrix = np.asarray(probabilities, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape != (len(labels), len(class_order)):
        raise MetricInputError("probability matrix shape does not match labels/classes")
    _validate_probability_rows(matrix)
    index_by_class = {name: index for index, name in enumerate(class_order)}
    indices = np.asarray([index_by_class[label] for label in labels], dtype=np.int64)
    return indices, matrix, class_order


def _validate_probability_rows(matrix: np.ndarray) -> None:
    if not np.all(np.isfinite(matrix)):
        raise MetricInputError("probabilities must be finite")
    if np.any(matrix < 0) or np.any(matrix > 1):
        raise MetricInputError("probabilities must fall between zero and one")
    if not np.allclose(np.sum(matrix, axis=1), 1.0, rtol=0, atol=1e-9):
        raise MetricInputError("probability rows must sum to one")


def _paired_finite_vectors(
    first: Sequence[float], second: Sequence[float]
) -> tuple[np.ndarray, np.ndarray]:
    left = np.asarray(first, dtype=np.float64)
    right = np.asarray(second, dtype=np.float64)
    if left.ndim != 1 or right.ndim != 1 or len(left) == 0 or len(left) != len(right):
        raise MetricInputError(
            "metric vectors must be non-empty, one-dimensional, and aligned"
        )
    if not np.all(np.isfinite(left)) or not np.all(np.isfinite(right)):
        raise MetricInputError("metric vectors must be finite")
    return left, right


def _average_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and values[order[end]] == values[order[start]]:
            end += 1
        average_rank = (start + 1 + end) / 2
        ranks[order[start:end]] = average_rank
        start = end
    return ranks


def hash_sample_keys(sample_keys: Sequence[SampleKey]) -> str:
    keys = tuple(sample_keys)
    return hashlib.sha256(
        json.dumps(
            tuple((key[0], key[1].isoformat(), key[2]) for key in keys),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def hash_training_labels(
    sample_keys: Sequence[SampleKey], labels: Sequence[str]
) -> str:
    keys = tuple(sample_keys)
    label_order = tuple(labels)
    if len(keys) != len(label_order):
        raise MetricInputError("training labels do not align with sample keys")
    return hashlib.sha256(
        json.dumps(
            tuple(
                (key[0], key[1].isoformat(), key[2], label)
                for key, label in zip(keys, label_order, strict=True)
            ),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _class_frequency_reference_hash(
    *,
    task: TaskName,
    horizon: Horizon,
    training_window: EvaluationWindow,
    class_order: tuple[str, ...],
    class_counts: tuple[int, ...],
    sample_count: int,
    training_sample_keys_hash: str,
    training_labels_hash: str,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "task": task.value,
                "horizon": horizon,
                "training_window": training_window.model_dump(mode="json"),
                "class_order": class_order,
                "class_counts": class_counts,
                "sample_count": sample_count,
                "training_sample_keys_hash": training_sample_keys_hash,
                "training_labels_hash": training_labels_hash,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
