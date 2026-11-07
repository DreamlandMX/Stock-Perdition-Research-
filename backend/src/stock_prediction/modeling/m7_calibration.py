"""Leakage-safe M7 calibration and finite probability blending."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import itertools
import json
import math
from typing import Literal

import numpy as np
from pydantic import ValidationError
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from stock_prediction.modeling.contracts import (
    M7FitComponent,
    M7FittedObjectEvidence,
    M7PartitionRole,
)


DirectionCalibrationMethod = Literal[
    "identity",
    "scalar_temperature",
    "vector_scaling",
]
RangeCalibrationMethod = Literal[
    "identity",
    "event_rate_shrinkage",
    "platt",
    "isotonic",
    "beta",
]


class M7CalibrationInputError(ValueError):
    """M7 calibration inputs violate identity, partition, or numerical rules."""


@dataclass(frozen=True, slots=True)
class M7DirectionCalibrator:
    method: DirectionCalibrationMethod
    scales: tuple[float, float, float]
    biases: tuple[float, float, float]
    fit_evidence_hash: str
    oof_fold_hash: str
    raw_brier: float
    calibrated_brier: float
    calibrator_hash: str = ""

    def __post_init__(self) -> None:
        expected = _hash_payload(self._payload())
        if self.calibrator_hash and self.calibrator_hash != expected:
            raise M7CalibrationInputError("m7_direction_calibrator_hash_invalid")
        object.__setattr__(self, "calibrator_hash", expected)

    def _payload(self) -> dict[str, object]:
        return {
            "method": self.method,
            "scales": self.scales,
            "biases": self.biases,
            "fit_evidence_hash": self.fit_evidence_hash,
            "oof_fold_hash": self.oof_fold_hash,
            "raw_brier": self.raw_brier,
            "calibrated_brier": self.calibrated_brier,
        }

    def apply(self, logits: object) -> np.ndarray:
        matrix = _finite_matrix(logits, columns=3, name="direction_logits")
        transformed = matrix * np.asarray(self.scales) + np.asarray(self.biases)
        return _readonly(_softmax(transformed))


@dataclass(frozen=True, slots=True)
class M7RangeCalibrator:
    method: RangeCalibrationMethod
    parameters: tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]
    x_thresholds: tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]
    y_thresholds: tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]
    fit_evidence_hash: str
    oof_fold_hash: str
    raw_brier: float
    calibrated_brier: float
    calibrator_hash: str = ""

    def __post_init__(self) -> None:
        expected = _hash_payload(self._payload())
        if self.calibrator_hash and self.calibrator_hash != expected:
            raise M7CalibrationInputError("m7_range_calibrator_hash_invalid")
        object.__setattr__(self, "calibrator_hash", expected)

    def _payload(self) -> dict[str, object]:
        return {
            "method": self.method,
            "parameters": self.parameters,
            "x_thresholds": self.x_thresholds,
            "y_thresholds": self.y_thresholds,
            "fit_evidence_hash": self.fit_evidence_hash,
            "oof_fold_hash": self.oof_fold_hash,
            "raw_brier": self.raw_brier,
            "calibrated_brier": self.calibrated_brier,
        }

    def apply(self, probabilities: object) -> np.ndarray:
        values = _probability_matrix(
            probabilities, columns=3, name="range_probabilities"
        )
        calibrated = np.empty_like(values)
        for column in range(3):
            calibrated[:, column] = _apply_range_column(
                self.method,
                values[:, column],
                self.parameters[column],
                self.x_thresholds[column],
                self.y_thresholds[column],
            )
        return project_range_exit_curve(calibrated)


@dataclass(frozen=True, slots=True)
class M7BlendCandidate:
    candidate_id: str
    family: str
    probabilities: np.ndarray = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.family:
            raise M7CalibrationInputError("m7_blend_candidate_identity_invalid")
        values = _probability_matrix(
            self.probabilities,
            columns=3,
            name=f"blend_candidate:{self.candidate_id}",
            simplex=True,
        )
        object.__setattr__(self, "probabilities", values)


@dataclass(frozen=True, slots=True)
class M7FiniteBlend:
    member_ids: tuple[str, ...]
    weights: tuple[float, ...]
    eligible_candidate_ids: tuple[str, ...]
    fit_evidence_hash: str
    calibration_brier: float
    blend_hash: str = ""

    def __post_init__(self) -> None:
        if (
            not 1 <= len(self.member_ids) <= 3
            or len(self.member_ids) != len(self.weights)
            or len(set(self.member_ids)) != len(self.member_ids)
            or not math.isclose(sum(self.weights), 1.0, abs_tol=1e-12)
            or any(
                weight <= 0 or not math.isclose(weight * 10, round(weight * 10))
                for weight in self.weights
            )
        ):
            raise M7CalibrationInputError("m7_blend_contract_invalid")
        expected = _hash_payload(
            {
                "member_ids": self.member_ids,
                "weights": self.weights,
                "eligible_candidate_ids": self.eligible_candidate_ids,
                "fit_evidence_hash": self.fit_evidence_hash,
                "calibration_brier": self.calibration_brier,
            }
        )
        if self.blend_hash and self.blend_hash != expected:
            raise M7CalibrationInputError("m7_blend_hash_invalid")
        object.__setattr__(self, "blend_hash", expected)

    def apply(self, candidates: Sequence[M7BlendCandidate]) -> np.ndarray:
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        if set(self.member_ids) - set(by_id):
            raise M7CalibrationInputError("m7_blend_member_missing")
        shapes = {
            by_id[candidate_id].probabilities.shape for candidate_id in self.member_ids
        }
        if len(shapes) != 1:
            raise M7CalibrationInputError("m7_blend_prediction_shape_mismatch")
        first = by_id[self.member_ids[0]].probabilities
        output = np.zeros_like(first)
        for candidate_id, weight in zip(self.member_ids, self.weights, strict=True):
            output += weight * by_id[candidate_id].probabilities
        return _readonly(_renormalize(output))


def fit_m7_direction_calibrators(
    logits: object,
    labels: object,
    *,
    fit_evidence: M7FittedObjectEvidence,
    oof_fold_ids: Sequence[int],
    vector_regularization: float = 0.01,
) -> tuple[M7DirectionCalibrator, ...]:
    evidence = _validate_fit_evidence(fit_evidence, M7FitComponent.CALIBRATOR)
    matrix = _finite_matrix(logits, columns=3, name="direction_logits")
    encoded = _class_labels(labels, len(matrix), classes=3)
    fold_hash = _validate_blocked_oof(evidence, oof_fold_ids, len(matrix))
    if not math.isfinite(vector_regularization) or vector_regularization <= 0:
        raise M7CalibrationInputError("m7_vector_regularization_invalid")
    one_hot = np.eye(3, dtype=np.float64)[encoded]
    raw = _softmax(matrix)
    raw_brier = _multiclass_brier(raw, one_hot)

    temperature_grid = np.exp(np.linspace(-3.0, 3.0, 401))
    losses = np.asarray(
        [
            _negative_log_likelihood(_softmax(matrix / value), encoded)
            for value in temperature_grid
        ]
    )
    temperature = float(temperature_grid[int(np.argmin(losses))])
    vector_scales, vector_biases = _fit_vector_scaling(
        matrix,
        one_hot,
        regularization=vector_regularization,
    )
    specifications: tuple[
        tuple[
            DirectionCalibrationMethod,
            tuple[float, float, float],
            tuple[float, float, float],
        ],
        ...,
    ] = (
        ("identity", (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)),
        (
            "scalar_temperature",
            (1.0 / temperature, 1.0 / temperature, 1.0 / temperature),
            (0.0, 0.0, 0.0),
        ),
        ("vector_scaling", vector_scales, vector_biases),
    )
    evidence_hash = _fit_evidence_hash(evidence)
    return tuple(
        M7DirectionCalibrator(
            method=method,
            scales=scales,
            biases=biases,
            fit_evidence_hash=evidence_hash,
            oof_fold_hash=fold_hash,
            raw_brier=raw_brier,
            calibrated_brier=_multiclass_brier(
                _softmax(matrix * np.asarray(scales) + np.asarray(biases)),
                one_hot,
            ),
        )
        for method, scales, biases in specifications
    )


def fit_m7_range_calibrators(
    probabilities: object,
    labels: object,
    *,
    fit_evidence: M7FittedObjectEvidence,
    oof_fold_ids: Sequence[int],
    shrinkage_strength: float = 20.0,
) -> tuple[M7RangeCalibrator, ...]:
    evidence = _validate_fit_evidence(fit_evidence, M7FitComponent.CALIBRATOR)
    values = _probability_matrix(probabilities, columns=3, name="range_probabilities")
    outcomes = _binary_matrix(labels, values.shape)
    fold_hash = _validate_blocked_oof(evidence, oof_fold_ids, len(values))
    if not math.isfinite(shrinkage_strength) or shrinkage_strength <= 0:
        raise M7CalibrationInputError("m7_range_shrinkage_strength_invalid")
    raw_projected = project_range_exit_curve(values)
    raw_brier = _binary_brier(raw_projected, outcomes)
    evidence_hash = _fit_evidence_hash(evidence)
    methods: tuple[RangeCalibrationMethod, ...] = (
        "identity",
        "event_rate_shrinkage",
        "platt",
        "isotonic",
        "beta",
    )
    results: list[M7RangeCalibrator] = []
    for method in methods:
        parameters: list[tuple[float, ...]] = []
        x_thresholds: list[tuple[float, ...]] = []
        y_thresholds: list[tuple[float, ...]] = []
        for column in range(3):
            fitted = _fit_range_column(
                method,
                values[:, column],
                outcomes[:, column],
                shrinkage_strength=shrinkage_strength,
            )
            parameters.append(fitted[0])
            x_thresholds.append(fitted[1])
            y_thresholds.append(fitted[2])
        artifact = M7RangeCalibrator(
            method=method,
            parameters=(parameters[0], parameters[1], parameters[2]),
            x_thresholds=(x_thresholds[0], x_thresholds[1], x_thresholds[2]),
            y_thresholds=(y_thresholds[0], y_thresholds[1], y_thresholds[2]),
            fit_evidence_hash=evidence_hash,
            oof_fold_hash=fold_hash,
            raw_brier=raw_brier,
            calibrated_brier=0.0,
        )
        calibrated = artifact.apply(values)
        results.append(
            M7RangeCalibrator(
                method=artifact.method,
                parameters=artifact.parameters,
                x_thresholds=artifact.x_thresholds,
                y_thresholds=artifact.y_thresholds,
                fit_evidence_hash=artifact.fit_evidence_hash,
                oof_fold_hash=artifact.oof_fold_hash,
                raw_brier=artifact.raw_brier,
                calibrated_brier=_binary_brier(calibrated, outcomes),
            )
        )
    return tuple(results)


def project_range_exit_curve(probabilities: object) -> np.ndarray:
    values = _probability_matrix(probabilities, columns=3, name="range_curve")
    projected = np.maximum.accumulate(values, axis=1)
    return _readonly(np.clip(projected, 0.0, 1.0))


def select_finite_m7_blend(
    candidates: Sequence[M7BlendCandidate],
    labels: object,
    *,
    fit_evidence: M7FittedObjectEvidence,
    registered_candidate_ids: Sequence[str],
) -> M7FiniteBlend:
    evidence = _validate_fit_evidence(fit_evidence, M7FitComponent.BLEND_WEIGHTS)
    supplied = tuple(candidates)
    registered = tuple(registered_candidate_ids)
    supplied_ids = tuple(candidate.candidate_id for candidate in supplied)
    if (
        not supplied
        or len(set(supplied_ids)) != len(supplied_ids)
        or len(set(registered)) != len(registered)
        or set(supplied_ids) != set(registered)
    ):
        raise M7CalibrationInputError("m7_blend_registered_candidate_set_mismatch")
    shape = supplied[0].probabilities.shape
    if any(candidate.probabilities.shape != shape for candidate in supplied):
        raise M7CalibrationInputError("m7_blend_prediction_shape_mismatch")
    if shape[0] != len(evidence.sample_keys):
        raise M7CalibrationInputError("m7_blend_fit_evidence_alignment_invalid")
    encoded = _class_labels(labels, shape[0], classes=shape[1])
    one_hot = np.eye(shape[1], dtype=np.float64)[encoded]
    by_family: dict[str, list[tuple[float, M7BlendCandidate]]] = defaultdict(list)
    for candidate in supplied:
        by_family[candidate.family].append(
            (_multiclass_brier(candidate.probabilities, one_hot), candidate)
        )
    eligible = tuple(
        candidate
        for family in sorted(by_family)
        for _, candidate in sorted(
            by_family[family],
            key=lambda item: (item[0], item[1].candidate_id),
        )[:2]
    )
    best: tuple[float, tuple[str, ...], tuple[float, ...]] | None = None
    for member_count in range(1, min(3, len(eligible)) + 1):
        for members in itertools.combinations(eligible, member_count):
            if any(
                count > 2 for count in Counter(item.family for item in members).values()
            ):
                continue
            for integer_weights in _positive_tenth_compositions(member_count):
                weights = tuple(value / 10.0 for value in integer_weights)
                probabilities = np.zeros_like(members[0].probabilities)
                for member, weight in zip(members, weights, strict=True):
                    probabilities += weight * member.probabilities
                score = _multiclass_brier(probabilities, one_hot)
                member_ids = tuple(member.candidate_id for member in members)
                row = (score, member_ids, weights)
                if best is None or row < best:
                    best = row
    if best is None:
        raise M7CalibrationInputError("m7_blend_no_valid_combination")
    return M7FiniteBlend(
        member_ids=best[1],
        weights=best[2],
        eligible_candidate_ids=tuple(candidate.candidate_id for candidate in eligible),
        fit_evidence_hash=_fit_evidence_hash(evidence),
        calibration_brier=best[0],
    )


def _validate_fit_evidence(
    evidence: M7FittedObjectEvidence,
    component: M7FitComponent,
) -> M7FittedObjectEvidence:
    try:
        checked = M7FittedObjectEvidence.model_validate(
            evidence.model_dump(mode="python"),
            strict=True,
        )
    except (AttributeError, ValidationError) as error:
        raise M7CalibrationInputError("m7_forbidden_fit_evidence") from error
    if (
        checked.component is not component
        or checked.fit_role is not M7PartitionRole.CALIBRATION
    ):
        raise M7CalibrationInputError("m7_forbidden_fit_evidence")
    return checked


def _validate_blocked_oof(
    evidence: M7FittedObjectEvidence,
    fold_ids: Sequence[int],
    row_count: int,
) -> str:
    folds = tuple(fold_ids)
    if len(evidence.sample_keys) != row_count or len(folds) != row_count:
        raise M7CalibrationInputError("m7_blocked_oof_alignment_invalid")
    if len(set(folds)) < 2 or any(
        not isinstance(value, int) or value < 0 for value in folds
    ):
        raise M7CalibrationInputError("m7_blocked_oof_invalid")
    dates = tuple(key[1] for key in evidence.sample_keys)
    if dates != tuple(sorted(dates)):
        raise M7CalibrationInputError("m7_blocked_oof_sample_order_invalid")
    seen: set[int] = set()
    previous: int | None = None
    for value in folds:
        if value != previous:
            if value in seen:
                raise M7CalibrationInputError("m7_blocked_oof_not_contiguous")
            seen.add(value)
            previous = value
    if tuple(sorted(seen)) != tuple(range(len(seen))):
        raise M7CalibrationInputError("m7_blocked_oof_ids_invalid")
    return _hash_payload(
        {"fold_ids": folds, "sample_keys_hash": evidence.sample_keys_hash}
    )


def _fit_vector_scaling(
    logits: np.ndarray,
    one_hot: np.ndarray,
    *,
    regularization: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    scales = np.ones(3, dtype=np.float64)
    biases = np.zeros(3, dtype=np.float64)
    learning_rate = 0.05
    for step in range(1500):
        probabilities = _softmax(logits * scales + biases)
        residual = (probabilities - one_hot) / len(logits)
        gradient_scales = np.sum(residual * logits, axis=0) + regularization * (
            scales - 1.0
        )
        gradient_biases = np.sum(residual, axis=0) + regularization * biases
        rate = learning_rate / math.sqrt(1.0 + step / 100.0)
        scales -= rate * gradient_scales
        biases -= rate * gradient_biases
    if not np.all(np.isfinite(scales)) or not np.all(np.isfinite(biases)):
        raise M7CalibrationInputError("m7_vector_scaling_fit_failed")
    return tuple(float(value) for value in scales), tuple(
        float(value) for value in biases
    )  # type: ignore[return-value]


def _fit_range_column(
    method: RangeCalibrationMethod,
    probabilities: np.ndarray,
    labels: np.ndarray,
    *,
    shrinkage_strength: float,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    if method == "identity":
        return (), (), ()
    if method == "event_rate_shrinkage":
        return (float(np.mean(labels)), shrinkage_strength, float(len(labels))), (), ()
    clipped = np.clip(probabilities, 1e-8, 1 - 1e-8)
    if method == "isotonic":
        estimator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        estimator.fit(clipped, labels)
        return (
            (),
            tuple(float(value) for value in estimator.X_thresholds_),
            tuple(float(value) for value in estimator.y_thresholds_),
        )
    features = _logit(clipped).reshape(-1, 1)
    if method == "beta":
        features = np.column_stack((np.log(clipped), np.log1p(-clipped)))
    coefficients, intercept = _fit_binary_logistic(features, labels)
    return (*coefficients, intercept), (), ()


def _fit_binary_logistic(
    features: np.ndarray,
    labels: np.ndarray,
) -> tuple[tuple[float, ...], float]:
    event_rate = float(np.clip(np.mean(labels), 1e-8, 1 - 1e-8))
    if len(set(labels.tolist())) < 2:
        intercept = math.log(event_rate / (1.0 - event_rate))
        return tuple(0.0 for _ in range(features.shape[1])), intercept
    try:
        estimator = LogisticRegression(
            solver="lbfgs",
            random_state=17,
            max_iter=1000,
            C=1.0,
        ).fit(features, labels)
    except (TypeError, ValueError) as error:
        raise M7CalibrationInputError("m7_binary_calibration_fit_failed") from error
    return (
        tuple(float(value) for value in estimator.coef_[0]),
        float(estimator.intercept_[0]),
    )


def _apply_range_column(
    method: RangeCalibrationMethod,
    probabilities: np.ndarray,
    parameters: tuple[float, ...],
    x_thresholds: tuple[float, ...],
    y_thresholds: tuple[float, ...],
) -> np.ndarray:
    clipped = np.clip(probabilities, 1e-8, 1 - 1e-8)
    if method == "identity":
        return probabilities
    if method == "event_rate_shrinkage":
        event_rate, strength, sample_count = parameters
        return (sample_count * probabilities + strength * event_rate) / (
            sample_count + strength
        )
    if method == "isotonic":
        return np.asarray(
            np.interp(clipped, x_thresholds, y_thresholds),
            dtype=np.float64,
        )
    if method == "platt":
        coefficient, intercept = parameters
        return _sigmoid(coefficient * _logit(clipped) + intercept)
    coefficient_positive, coefficient_negative, intercept = parameters
    return _sigmoid(
        coefficient_positive * np.log(clipped)
        + coefficient_negative * np.log1p(-clipped)
        + intercept
    )


def _positive_tenth_compositions(parts: int) -> tuple[tuple[int, ...], ...]:
    if parts == 1:
        return ((10,),)
    return tuple(
        values
        for values in itertools.product(range(1, 10), repeat=parts)
        if sum(values) == 10
    )


def _finite_matrix(values: object, *, columns: int, name: str) -> np.ndarray:
    matrix = np.asarray(values, dtype=np.float64)
    if (
        matrix.ndim != 2
        or matrix.shape[1] != columns
        or not np.all(np.isfinite(matrix))
    ):
        raise M7CalibrationInputError(f"m7_{name}_invalid")
    return matrix


def _probability_matrix(
    values: object,
    *,
    columns: int,
    name: str,
    simplex: bool = False,
) -> np.ndarray:
    matrix = _finite_matrix(values, columns=columns, name=name)
    if np.any(matrix < 0) or np.any(matrix > 1):
        raise M7CalibrationInputError(f"m7_{name}_invalid")
    if simplex and not np.allclose(matrix.sum(axis=1), 1.0, rtol=0.0, atol=1e-9):
        raise M7CalibrationInputError(f"m7_{name}_simplex_invalid")
    return _readonly(matrix.copy())


def _class_labels(values: object, count: int, *, classes: int) -> np.ndarray:
    labels = np.asarray(values, dtype=np.int64)
    if labels.shape != (count,) or np.any(labels < 0) or np.any(labels >= classes):
        raise M7CalibrationInputError("m7_class_labels_invalid")
    return labels


def _binary_matrix(values: object, shape: tuple[int, ...]) -> np.ndarray:
    labels = np.asarray(values, dtype=np.int64)
    if labels.shape != shape or np.any((labels != 0) & (labels != 1)):
        raise M7CalibrationInputError("m7_binary_labels_invalid")
    return labels


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - np.max(values, axis=1, keepdims=True)
    exponentiated = np.exp(shifted)
    return np.asarray(
        exponentiated / exponentiated.sum(axis=1, keepdims=True),
        dtype=np.float64,
    )


def _renormalize(values: np.ndarray) -> np.ndarray:
    if np.any(values < 0) or not np.all(np.isfinite(values)):
        raise M7CalibrationInputError("m7_blend_probability_invalid")
    totals = values.sum(axis=1, keepdims=True)
    if np.any(totals <= 0):
        raise M7CalibrationInputError("m7_blend_probability_invalid")
    return np.asarray(values / totals, dtype=np.float64)


def _negative_log_likelihood(probabilities: np.ndarray, labels: np.ndarray) -> float:
    selected = probabilities[np.arange(len(labels)), labels]
    return float(-np.mean(np.log(np.clip(selected, 1e-15, 1.0))))


def _multiclass_brier(probabilities: np.ndarray, one_hot: np.ndarray) -> float:
    return float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1)))


def _binary_brier(probabilities: np.ndarray, labels: np.ndarray) -> float:
    return float(np.mean((probabilities - labels) ** 2))


def _logit(values: np.ndarray) -> np.ndarray:
    return np.asarray(np.log(values / (1.0 - values)), dtype=np.float64)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return np.asarray(
        1.0 / (1.0 + np.exp(-np.clip(values, -500, 500))),
        dtype=np.float64,
    )


def _fit_evidence_hash(evidence: M7FittedObjectEvidence) -> str:
    return _hash_payload(evidence.model_dump(mode="json"))


def _hash_payload(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _readonly(values: np.ndarray) -> np.ndarray:
    values.setflags(write=False)
    return values


__all__ = [
    "M7BlendCandidate",
    "M7CalibrationInputError",
    "M7DirectionCalibrator",
    "M7FiniteBlend",
    "M7RangeCalibrator",
    "fit_m7_direction_calibrators",
    "fit_m7_range_calibrators",
    "project_range_exit_curve",
    "select_finite_m7_blend",
]
