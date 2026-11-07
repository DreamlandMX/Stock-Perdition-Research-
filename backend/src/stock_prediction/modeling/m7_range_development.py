"""Fold-local M7 range-exit calibration and outer-test evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.metrics import roc_auc_score

from stock_prediction.modeling.contracts import M7FittedObjectEvidence, SampleKey
from stock_prediction.modeling.m7_calibration import (
    M7RangeCalibrator,
    fit_m7_range_calibrators,
    project_range_exit_curve,
)
from stock_prediction.modeling.m7_gates import M7RangeFoldMetrics
from stock_prediction.modeling.m7_metrics import range_exit_calibration_report


class M7RangeDevelopmentInputError(ValueError):
    """Range-exit probabilities, labels, or identities are invalid."""


@dataclass(frozen=True, slots=True)
class M7RangeFoldEvaluation:
    metrics: M7RangeFoldMetrics
    calibrator: M7RangeCalibrator
    test_probabilities: np.ndarray
    incumbent_probabilities: np.ndarray
    outer_test_sample_keys: tuple[SampleKey, ...]
    outer_test_labels: np.ndarray


def evaluate_m7_range_fold(
    *,
    fold_id: str,
    calibration_sample_keys: Sequence[SampleKey],
    calibration_probabilities: object,
    calibration_labels: object,
    outer_test_sample_keys: Sequence[SampleKey],
    outer_test_probabilities: object,
    outer_test_labels: object,
    incumbent_probabilities: object,
    null_probabilities: object,
    calibrator_fit_evidence: M7FittedObjectEvidence,
    label_censor_health_passed: bool,
    coverage_passed: bool,
    stability_passed: bool,
    determinism_passed: bool,
) -> M7RangeFoldEvaluation:
    calibration_keys = tuple(calibration_sample_keys)
    test_keys = tuple(outer_test_sample_keys)
    calibration = _curve(calibration_probabilities, len(calibration_keys))
    calibration_outcomes = _binary_labels(calibration_labels, len(calibration_keys))
    test_raw = _curve(outer_test_probabilities, len(test_keys))
    test_outcomes = _binary_labels(outer_test_labels, len(test_keys))
    incumbent = _curve(incumbent_probabilities, len(test_keys))
    null = _curve(null_probabilities, len(test_keys))
    if (
        not fold_id
        or not calibration_keys
        or not test_keys
        or len(set(calibration_keys)) != len(calibration_keys)
        or len(set(test_keys)) != len(test_keys)
        or tuple(calibrator_fit_evidence.sample_keys) != calibration_keys
    ):
        raise M7RangeDevelopmentInputError("m7_range_fold_alignment_invalid")
    fold_ids = _blocked_oof_ids(calibration_keys)
    calibrators = fit_m7_range_calibrators(
        calibration,
        calibration_outcomes,
        fit_evidence=calibrator_fit_evidence,
        oof_fold_ids=fold_ids,
    )
    selected = min(
        calibrators,
        key=lambda row: (row.calibrated_brier, row.method, row.calibrator_hash),
    )
    candidate = selected.apply(test_raw)
    symbols = tuple(key[0] for key in test_keys)
    calibration_report = range_exit_calibration_report(
        symbols=symbols,
        observed=test_outcomes[:, 2].tolist(),
        cumulative_probability=candidate[:, 2].tolist(),
    )
    candidate_brier = _symbol_equal_time_brier(symbols, candidate, test_outcomes)
    null_brier = _symbol_equal_time_brier(symbols, null, test_outcomes)
    incumbent_brier = _symbol_equal_time_brier(symbols, incumbent, test_outcomes)
    candidate_c_index = _c_index(test_outcomes[:, 2], candidate[:, 2])
    incumbent_c_index = _c_index(test_outcomes[:, 2], incumbent[:, 2])
    metrics = M7RangeFoldMetrics(
        fold_id=fold_id,
        candidate_time_brier=candidate_brier,
        null_time_brier=null_brier,
        incumbent_time_brier=incumbent_brier,
        cumulative_calibration_error=calibration_report.calibration_error,
        candidate_c_index=candidate_c_index,
        incumbent_c_index=incumbent_c_index,
        label_censor_health_passed=label_censor_health_passed,
        coverage_passed=coverage_passed,
        stability_passed=stability_passed,
        determinism_passed=determinism_passed,
    )
    return M7RangeFoldEvaluation(
        metrics=metrics,
        calibrator=selected,
        test_probabilities=candidate,
        incumbent_probabilities=incumbent,
        outer_test_sample_keys=test_keys,
        outer_test_labels=test_outcomes,
    )


def _curve(values: object, count: int) -> np.ndarray:
    matrix = np.asarray(values, dtype=np.float64)
    if (
        matrix.shape != (count, 3)
        or not np.all(np.isfinite(matrix))
        or np.any(matrix < 0)
        or np.any(matrix > 1)
    ):
        raise M7RangeDevelopmentInputError("m7_range_probability_invalid")
    return project_range_exit_curve(matrix)


def _binary_labels(values: object, count: int) -> np.ndarray:
    matrix = np.asarray(values, dtype=np.int8)
    if matrix.shape != (count, 3) or set(matrix.ravel().tolist()) - {0, 1}:
        raise M7RangeDevelopmentInputError("m7_range_labels_invalid")
    if np.any(np.diff(matrix, axis=1) < 0):
        raise M7RangeDevelopmentInputError("m7_range_labels_not_cumulative")
    return matrix


def _blocked_oof_ids(keys: Sequence[SampleKey]) -> tuple[int, ...]:
    dates = tuple(key[1] for key in keys)
    if dates != tuple(sorted(dates)):
        raise M7RangeDevelopmentInputError("m7_range_keys_not_time_ordered")
    unique_dates = tuple(sorted(set(dates)))
    if len(unique_dates) < 2:
        raise M7RangeDevelopmentInputError("m7_range_dates_insufficient")
    blocks = np.array_split(
        np.asarray(unique_dates, dtype=object), min(4, len(unique_dates))
    )
    by_date = {
        value: block_index
        for block_index, block in enumerate(blocks)
        for value in block.tolist()
    }
    return tuple(by_date[value] for value in dates)


def _symbol_equal_time_brier(
    symbols: Sequence[str], probabilities: np.ndarray, labels: np.ndarray
) -> float:
    losses = np.mean(np.square(probabilities - labels), axis=1)
    symbol_order = np.asarray(tuple(symbols))
    return float(
        np.mean(
            [
                float(np.mean(losses[symbol_order == symbol]))
                for symbol in sorted(set(symbol_order))
            ]
        )
    )


def _c_index(observed: np.ndarray, probabilities: np.ndarray) -> float:
    return (
        float(roc_auc_score(observed, probabilities))
        if len(set(observed.tolist())) == 2
        else 0.5
    )


__all__ = [
    "M7RangeDevelopmentInputError",
    "M7RangeFoldEvaluation",
    "evaluate_m7_range_fold",
]
