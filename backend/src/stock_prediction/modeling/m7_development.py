"""Fold-local M7 calibration, finite blending, and metric evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
from sklearn.metrics import accuracy_score, f1_score

from stock_prediction.modeling.contracts import M7FittedObjectEvidence, SampleKey
from stock_prediction.modeling.m7_calibration import (
    M7BlendCandidate,
    M7DirectionCalibrator,
    M7FiniteBlend,
    fit_m7_direction_calibrators,
    select_finite_m7_blend,
)
from stock_prediction.modeling.m7_gates import M7DirectionFoldMetrics
from stock_prediction.modeling.m7_metrics import (
    adaptive_classwise_ece,
    direction_brier_report,
)


class M7DevelopmentInputError(ValueError):
    """Fold predictions or evidence are incomplete, misaligned, or invalid."""


@dataclass(frozen=True, slots=True)
class M7DirectionCandidatePredictions:
    candidate_id: str
    family: str
    calibration_probabilities: np.ndarray
    test_probabilities: np.ndarray
    deterministic: bool

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.family:
            raise M7DevelopmentInputError("m7_direction_candidate_identity_invalid")
        calibration = _probability_matrix(self.calibration_probabilities)
        test = _probability_matrix(self.test_probabilities)
        object.__setattr__(self, "calibration_probabilities", calibration)
        object.__setattr__(self, "test_probabilities", test)


@dataclass(frozen=True, slots=True)
class M7DirectionFoldEvaluation:
    metrics: M7DirectionFoldMetrics
    blend: M7FiniteBlend
    calibration_methods: Mapping[str, str]
    test_probabilities: np.ndarray
    incumbent_probabilities: np.ndarray
    outer_test_sample_keys: tuple[SampleKey, ...]
    outer_test_labels: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "calibration_methods",
            MappingProxyType(dict(self.calibration_methods)),
        )


@dataclass(frozen=True, slots=True)
class M7DirectionFoldSelection:
    """Calibration-only selection evidence, frozen before outer-test evaluation."""

    classes: tuple[str, str, str]
    calibration_sample_keys: tuple[SampleKey, ...]
    candidate_ids: tuple[str, ...]
    blend: M7FiniteBlend
    calibrators: Mapping[str, M7DirectionCalibrator]

    def __post_init__(self) -> None:
        object.__setattr__(self, "calibrators", MappingProxyType(dict(self.calibrators)))


def select_m7_direction_fold(
    *,
    classes: Sequence[str],
    calibration_sample_keys: Sequence[SampleKey],
    calibration_labels: Sequence[str],
    candidates: Sequence[M7DirectionCandidatePredictions],
    calibrator_fit_evidence: M7FittedObjectEvidence,
    blend_fit_evidence: M7FittedObjectEvidence,
) -> M7DirectionFoldSelection:
    """Freeze calibrators and blend using calibration evidence only."""

    class_order = tuple(classes)
    calibration_keys = tuple(calibration_sample_keys)
    calibration_label_order = tuple(calibration_labels)
    supplied = tuple(candidates)
    if (
        len(class_order) != 3
        or len(set(class_order)) != 3
        or not calibration_keys
        or len(set(calibration_keys)) != len(calibration_keys)
        or len(calibration_keys) != len(calibration_label_order)
        or set(calibration_label_order) - set(class_order)
        or not supplied
        or len({row.candidate_id for row in supplied}) != len(supplied)
        or tuple(calibrator_fit_evidence.sample_keys) != calibration_keys
        or tuple(blend_fit_evidence.sample_keys) != calibration_keys
        or any(
            row.calibration_probabilities.shape != (len(calibration_keys), 3)
            for row in supplied
        )
    ):
        raise M7DevelopmentInputError("m7_direction_selection_alignment_invalid")
    encoded_calibration = np.asarray(
        [class_order.index(label) for label in calibration_label_order],
        dtype=np.int64,
    )
    fold_ids = _blocked_oof_ids(calibration_keys)
    selected_calibrators: dict[str, M7DirectionCalibrator] = {}
    calibration_candidates: list[M7BlendCandidate] = []
    for row in supplied:
        logits = np.log(np.clip(row.calibration_probabilities, 1e-12, 1.0))
        calibrators = fit_m7_direction_calibrators(
            logits,
            encoded_calibration,
            fit_evidence=calibrator_fit_evidence,
            oof_fold_ids=fold_ids,
        )
        selected = min(
            calibrators,
            key=lambda item: (
                item.calibrated_brier,
                item.method,
                item.calibrator_hash,
            ),
        )
        selected_calibrators[row.candidate_id] = selected
        calibration_candidates.append(
            M7BlendCandidate(
                candidate_id=row.candidate_id,
                family=row.family,
                probabilities=selected.apply(logits),
            )
        )
    blend = select_finite_m7_blend(
        calibration_candidates,
        encoded_calibration,
        fit_evidence=blend_fit_evidence,
        registered_candidate_ids=tuple(row.candidate_id for row in supplied),
    )
    return M7DirectionFoldSelection(
        classes=(class_order[0], class_order[1], class_order[2]),
        calibration_sample_keys=calibration_keys,
        candidate_ids=tuple(row.candidate_id for row in supplied),
        blend=blend,
        calibrators=selected_calibrators,
    )


def evaluate_m7_direction_fold(
    *,
    fold_id: str,
    horizon: int,
    classes: Sequence[str],
    calibration_sample_keys: Sequence[SampleKey],
    calibration_labels: Sequence[str],
    outer_test_sample_keys: Sequence[SampleKey],
    outer_test_labels: Sequence[str],
    candidates: Sequence[M7DirectionCandidatePredictions],
    frequency_probabilities: object,
    incumbent_probabilities: object,
    calibrator_fit_evidence: M7FittedObjectEvidence,
    blend_fit_evidence: M7FittedObjectEvidence,
    label_health_passed: bool,
    eligibility_coverage_passed: bool,
    stability_passed: bool,
    selection: M7DirectionFoldSelection | None = None,
) -> M7DirectionFoldEvaluation:
    """Select only on calibration evidence and evaluate the outer test once."""

    class_order = tuple(classes)
    calibration_keys = tuple(calibration_sample_keys)
    test_keys = tuple(outer_test_sample_keys)
    calibration_label_order = tuple(calibration_labels)
    test_label_order = tuple(outer_test_labels)
    supplied = tuple(candidates)
    if (
        not fold_id
        or horizon not in (1, 5, 20)
        or len(class_order) != 3
        or len(set(class_order)) != 3
        or not calibration_keys
        or not test_keys
        or len(set(calibration_keys)) != len(calibration_keys)
        or len(set(test_keys)) != len(test_keys)
        or len(calibration_keys) != len(calibration_label_order)
        or len(test_keys) != len(test_label_order)
        or set(calibration_label_order) - set(class_order)
        or set(test_label_order) - set(class_order)
        or not supplied
        or len({row.candidate_id for row in supplied}) != len(supplied)
        or tuple(calibrator_fit_evidence.sample_keys) != calibration_keys
        or tuple(blend_fit_evidence.sample_keys) != calibration_keys
    ):
        raise M7DevelopmentInputError("m7_direction_fold_alignment_invalid")
    if any(
        row.calibration_probabilities.shape != (len(calibration_keys), 3)
        or row.test_probabilities.shape != (len(test_keys), 3)
        for row in supplied
    ):
        raise M7DevelopmentInputError("m7_direction_prediction_alignment_invalid")
    frozen_selection = selection or select_m7_direction_fold(
        classes=class_order,
        calibration_sample_keys=calibration_keys,
        calibration_labels=calibration_label_order,
        candidates=supplied,
        calibrator_fit_evidence=calibrator_fit_evidence,
        blend_fit_evidence=blend_fit_evidence,
    )
    candidate_ids = tuple(row.candidate_id for row in supplied)
    if (
        frozen_selection.classes != class_order
        or frozen_selection.calibration_sample_keys != calibration_keys
        or frozen_selection.candidate_ids != candidate_ids
        or set(frozen_selection.calibrators) != set(candidate_ids)
    ):
        raise M7DevelopmentInputError("m7_direction_selection_identity_mismatch")
    blend = frozen_selection.blend
    selected_calibrators = frozen_selection.calibrators
    by_id = {row.candidate_id: row for row in supplied}
    calibrated_test = tuple(
        M7BlendCandidate(
            candidate_id=candidate_id,
            family=by_id[candidate_id].family,
            probabilities=selected_calibrators[candidate_id].apply(
                np.log(
                    np.clip(
                        by_id[candidate_id].test_probabilities,
                        1e-12,
                        1.0,
                    )
                )
            ),
        )
        for candidate_id in blend.member_ids
    )
    test_probabilities = blend.apply(calibrated_test)
    frequency = _aligned_probability_matrix(frequency_probabilities, len(test_keys))
    incumbent = _aligned_probability_matrix(incumbent_probabilities, len(test_keys))
    symbols = tuple(key[0] for key in test_keys)
    candidate_brier = direction_brier_report(
        symbols=symbols,
        labels=test_label_order,
        probabilities=test_probabilities,
        classes=class_order,
    )
    frequency_brier = direction_brier_report(
        symbols=symbols,
        labels=test_label_order,
        probabilities=frequency,
        classes=class_order,
    )
    incumbent_brier = direction_brier_report(
        symbols=symbols,
        labels=test_label_order,
        probabilities=incumbent,
        classes=class_order,
    )
    calibration = adaptive_classwise_ece(
        symbols=symbols,
        labels=test_label_order,
        probabilities=test_probabilities,
        classes=class_order,
    )
    predicted = np.argmax(test_probabilities, axis=1)
    expected = np.asarray(
        [class_order.index(label) for label in test_label_order],
        dtype=np.int64,
    )
    probability_validity = bool(
        np.all(np.isfinite(test_probabilities))
        and np.all(test_probabilities >= 0)
        and np.all(test_probabilities <= 1)
        and np.allclose(
            test_probabilities.sum(axis=1),
            1.0,
            rtol=0.0,
            atol=1e-9,
        )
    )
    metrics = M7DirectionFoldMetrics(
        fold_id=fold_id,
        candidate_brier=candidate_brier.symbol_equal_brier,
        frequency_brier=frequency_brier.symbol_equal_brier,
        incumbent_brier=incumbent_brier.symbol_equal_brier,
        adaptive_macro_ece=calibration.macro_ece,
        label_health_passed=label_health_passed,
        eligibility_coverage_passed=eligibility_coverage_passed,
        probability_validity_passed=probability_validity,
        determinism_passed=all(
            by_id[candidate_id].deterministic
            for candidate_id in blend.member_ids
        ),
        stability_passed=stability_passed,
        accuracy=float(accuracy_score(expected, predicted)),
        macro_f1=float(f1_score(expected, predicted, average="macro")),
    )
    return M7DirectionFoldEvaluation(
        metrics=metrics,
        blend=blend,
        calibration_methods={
            candidate_id: calibrator.method
            for candidate_id, calibrator in selected_calibrators.items()
        },
        test_probabilities=test_probabilities,
        incumbent_probabilities=incumbent,
        outer_test_sample_keys=test_keys,
        outer_test_labels=test_label_order,
    )


def _blocked_oof_ids(keys: Sequence[SampleKey]) -> tuple[int, ...]:
    dates = tuple(key[1] for key in keys)
    if dates != tuple(sorted(dates)):
        raise M7DevelopmentInputError("m7_calibration_keys_not_time_ordered")
    unique_dates = tuple(sorted(set(dates)))
    if len(unique_dates) < 2:
        raise M7DevelopmentInputError("m7_calibration_dates_insufficient")
    blocks = np.array_split(
        np.asarray(unique_dates, dtype=object), min(4, len(unique_dates))
    )
    by_date = {
        value: block_index
        for block_index, block in enumerate(blocks)
        for value in block.tolist()
    }
    return tuple(by_date[value] for value in dates)


def _probability_matrix(values: object) -> np.ndarray:
    matrix = np.asarray(values, dtype=np.float64)
    if (
        matrix.ndim != 2
        or matrix.shape[1] != 3
        or not np.all(np.isfinite(matrix))
        or np.any(matrix < 0)
        or np.any(matrix > 1)
        or not np.allclose(matrix.sum(axis=1), 1.0, rtol=0.0, atol=1e-9)
    ):
        raise M7DevelopmentInputError("m7_direction_probability_invalid")
    output = matrix.copy()
    output.setflags(write=False)
    return output


def _aligned_probability_matrix(values: object, row_count: int) -> np.ndarray:
    matrix = _probability_matrix(values)
    if matrix.shape != (row_count, 3):
        raise M7DevelopmentInputError("m7_direction_baseline_alignment_invalid")
    return matrix


__all__ = [
    "M7DevelopmentInputError",
    "M7DirectionCandidatePredictions",
    "M7DirectionFoldEvaluation",
    "M7DirectionFoldSelection",
    "evaluate_m7_direction_fold",
    "select_m7_direction_fold",
]
