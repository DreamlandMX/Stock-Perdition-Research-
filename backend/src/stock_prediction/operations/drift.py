"""Sample-aware drift metrics that never turn small windows into alerts."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import math

import numpy as np

from stock_prediction.operations.contracts import (
    DriftFamily,
    DriftLevel,
    DriftMetric,
    DriftReport,
)


class DriftInputError(ValueError):
    """Drift input is non-finite, empty, or otherwise incomparable."""


def population_stability_index(
    reference: Sequence[float],
    current: Sequence[float],
    *,
    bins: int = 10,
) -> float:
    reference_array = _finite_array(reference, "reference")
    current_array = _finite_array(current, "current")
    if bins < 2:
        raise DriftInputError("PSI requires at least two bins")
    boundaries = np.unique(
        np.quantile(reference_array, np.linspace(0.0, 1.0, bins + 1))
    )
    if boundaries.size < 2:
        return 0.0 if np.allclose(reference_array[0], current_array) else 1.0
    boundaries[0] = -np.inf
    boundaries[-1] = np.inf
    reference_counts, _ = np.histogram(reference_array, bins=boundaries)
    current_counts, _ = np.histogram(current_array, bins=boundaries)
    epsilon = 1e-6
    reference_ratio = np.clip(reference_counts / reference_array.size, epsilon, None)
    current_ratio = np.clip(current_counts / current_array.size, epsilon, None)
    value = float(
        np.sum(
            (current_ratio - reference_ratio) * np.log(current_ratio / reference_ratio)
        )
    )
    if not math.isfinite(value):
        raise DriftInputError("PSI is non-finite")
    return value


def brier_score(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    probability_array = _finite_array(probabilities, "probabilities")
    outcome_array = _finite_array(outcomes, "outcomes")
    if probability_array.shape != outcome_array.shape:
        raise DriftInputError("probabilities and outcomes must align")
    if np.any((probability_array < 0) | (probability_array > 1)):
        raise DriftInputError("probabilities must be in [0, 1]")
    if np.any((outcome_array != 0) & (outcome_array != 1)):
        raise DriftInputError("outcomes must be binary")
    return float(np.mean((probability_array - outcome_array) ** 2))


def assess_upper_bound(
    *,
    metric_id: str,
    family: DriftFamily,
    metric_name: str,
    value: float,
    sample_count: int,
    minimum_sample_size: int,
    warning_threshold: float,
    critical_threshold: float,
    units: str,
    detail: str,
) -> DriftMetric:
    if not math.isfinite(value) or value < 0:
        raise DriftInputError("drift value must be finite and non-negative")
    if sample_count < minimum_sample_size:
        level = DriftLevel.INSUFFICIENT_SAMPLES
    elif value >= critical_threshold:
        level = DriftLevel.CRITICAL
    elif value >= warning_threshold:
        level = DriftLevel.WARNING
    else:
        level = DriftLevel.HEALTHY
    return DriftMetric(
        metric_id=metric_id,
        family=family,
        metric_name=metric_name,
        level=level,
        value=value,
        sample_count=sample_count,
        minimum_sample_size=minimum_sample_size,
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
        units=units,
        detail=detail,
    )


def assess_target_deviation(
    *,
    metric_id: str,
    family: DriftFamily,
    metric_name: str,
    observed: float,
    target: float,
    sample_count: int,
    minimum_sample_size: int,
    warning_threshold: float,
    critical_threshold: float,
    units: str,
    detail: str,
) -> DriftMetric:
    if not all(math.isfinite(value) for value in (observed, target)):
        raise DriftInputError("target drift inputs must be finite")
    return assess_upper_bound(
        metric_id=metric_id,
        family=family,
        metric_name=metric_name,
        value=abs(observed - target),
        sample_count=sample_count,
        minimum_sample_size=minimum_sample_size,
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
        units=units,
        detail=detail,
    )


def build_drift_report(
    *,
    report_id: str,
    as_of: datetime,
    model_version: str,
    dataset_revision: str,
    metrics: Sequence[DriftMetric],
) -> DriftReport:
    if not metrics:
        raise DriftInputError("drift report requires metrics")
    rank = {
        DriftLevel.INSUFFICIENT_SAMPLES: 0,
        DriftLevel.HEALTHY: 1,
        DriftLevel.WARNING: 2,
        DriftLevel.CRITICAL: 3,
    }
    sufficient = [
        metric.level
        for metric in metrics
        if metric.level is not DriftLevel.INSUFFICIENT_SAMPLES
    ]
    overall = (
        max(sufficient, key=rank.__getitem__)
        if sufficient
        else DriftLevel.INSUFFICIENT_SAMPLES
    )
    return DriftReport(
        report_id=report_id,
        as_of=as_of,
        model_version=model_version,
        dataset_revision=dataset_revision,
        metrics=tuple(metrics),
        overall_level=overall,
    )


def _finite_array(values: Sequence[float] | Sequence[int], name: str) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1 or array.size == 0 or not np.all(np.isfinite(array)):
        raise DriftInputError(f"{name} must be a non-empty finite vector")
    return array
