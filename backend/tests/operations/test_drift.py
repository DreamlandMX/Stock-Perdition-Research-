from datetime import UTC, datetime

import pytest

from stock_prediction.operations.contracts import DriftFamily, DriftLevel
from stock_prediction.operations.drift import (
    assess_target_deviation,
    assess_upper_bound,
    brier_score,
    build_drift_report,
    population_stability_index,
)


def test_psi_and_brier_are_deterministic_and_bounded() -> None:
    reference = [float(value) for value in range(100)]
    assert population_stability_index(reference, reference) == pytest.approx(0.0)
    assert (
        population_stability_index(reference, [value + 100.0 for value in reference])
        > 1
    )
    assert brier_score([0.2, 0.8], [0, 1]) == pytest.approx(0.04)


def test_small_monitoring_window_is_visible_but_cannot_raise_alert() -> None:
    metric = assess_upper_bound(
        metric_id="feature-psi",
        family=DriftFamily.FEATURE,
        metric_name="psi",
        value=10.0,
        sample_count=19,
        minimum_sample_size=20,
        warning_threshold=0.1,
        critical_threshold=0.25,
        units="ratio",
        detail="rolling feature distribution",
    )
    assert metric.level is DriftLevel.INSUFFICIENT_SAMPLES
    assert metric.sample_count == 19
    assert metric.minimum_sample_size == 20


def test_report_surfaces_worst_sufficient_family_and_interval_sample_hint() -> None:
    feature = assess_upper_bound(
        metric_id="feature-psi",
        family=DriftFamily.FEATURE,
        metric_name="psi",
        value=0.12,
        sample_count=100,
        minimum_sample_size=50,
        warning_threshold=0.1,
        critical_threshold=0.25,
        units="ratio",
        detail="rolling feature distribution",
    )
    coverage = assess_target_deviation(
        metric_id="interval-coverage",
        family=DriftFamily.INTERVAL,
        metric_name="coverage_gap",
        observed=0.60,
        target=0.80,
        sample_count=20,
        minimum_sample_size=50,
        warning_threshold=0.05,
        critical_threshold=0.10,
        units="probability",
        detail="rolling CQR coverage",
    )
    report = build_drift_report(
        report_id="drift-20260721",
        as_of=datetime(2026, 7, 21, tzinfo=UTC),
        model_version="model-v1",
        dataset_revision="dataset-v1",
        metrics=[feature, coverage],
    )

    assert report.overall_level is DriftLevel.WARNING
    assert report.metrics[1].level is DriftLevel.INSUFFICIENT_SAMPLES
