from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from stock_prediction.modeling.contracts import (
    M7FitComponent,
    M7FittedObjectEvidence,
    M7PartitionRole,
    hash_m7_sample_keys,
)
from stock_prediction.modeling.m7_range_development import evaluate_m7_range_fold


def _keys(count: int):
    start = datetime(2026, 1, 2, tzinfo=UTC)
    return tuple(
        (f"US.S{index % 10:02d}", start + timedelta(days=index // 10), 20)
        for index in range(count)
    )


def test_range_fold_selects_calibrator_projects_curve_and_evaluates_once() -> None:
    keys = _keys(120)
    event_day = np.asarray([(index % 20) + 1 for index in range(120)])
    event = np.asarray([index % 3 != 0 for index in range(120)], dtype=int)
    labels = np.column_stack([event * (event_day <= day) for day in (5, 10, 20)])
    raw = np.column_stack(
        (
            np.full(120, 0.15),
            np.full(120, 0.35),
            np.full(120, 0.55),
        )
    )
    evidence = M7FittedObjectEvidence(
        component=M7FitComponent.CALIBRATOR,
        fit_role=M7PartitionRole.CALIBRATION,
        partition_hash="a" * 64,
        sample_keys=keys,
        sample_keys_hash=hash_m7_sample_keys(keys),
    )

    result = evaluate_m7_range_fold(
        fold_id="outer-01",
        calibration_sample_keys=keys,
        calibration_probabilities=raw,
        calibration_labels=labels,
        outer_test_sample_keys=keys,
        outer_test_probabilities=raw,
        outer_test_labels=labels,
        incumbent_probabilities=raw,
        null_probabilities=np.tile(labels.mean(axis=0), (120, 1)),
        calibrator_fit_evidence=evidence,
        label_censor_health_passed=True,
        coverage_passed=True,
        stability_passed=True,
        determinism_passed=True,
    )

    assert result.metrics.fold_id == "outer-01"
    assert result.test_probabilities.shape == (120, 3)
    assert np.all(np.diff(result.test_probabilities, axis=1) >= 0)
    assert 0 <= result.metrics.cumulative_calibration_error <= 1
    assert result.calibrator.method in {
        "identity",
        "event_rate_shrinkage",
        "platt",
        "isotonic",
        "beta",
    }
