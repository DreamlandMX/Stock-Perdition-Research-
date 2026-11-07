from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from stock_prediction.modeling.contracts import (
    M7FitComponent,
    M7FittedObjectEvidence,
    M7PartitionRole,
    hash_m7_sample_keys,
)
from stock_prediction.modeling.m7_development import (
    M7DevelopmentInputError,
    M7DirectionCandidatePredictions,
    evaluate_m7_direction_fold,
)


CLASSES = ("down", "flat", "up")


def _keys(count: int, *, horizon: int) -> tuple[tuple[str, datetime, int], ...]:
    start = datetime(2026, 1, 2, tzinfo=UTC)
    return tuple(
        (f"US.S{index % 10:02d}", start + timedelta(days=index // 10), horizon)
        for index in range(count)
    )


def _evidence(
    component: M7FitComponent,
    keys: tuple[tuple[str, datetime, int], ...],
) -> M7FittedObjectEvidence:
    return M7FittedObjectEvidence(
        component=component,
        fit_role=M7PartitionRole.CALIBRATION,
        partition_hash="a" * 64,
        sample_keys=keys,
        sample_keys_hash=hash_m7_sample_keys(keys),
    )


def _probabilities(labels: tuple[str, ...], confidence: float) -> np.ndarray:
    matrix = np.full((len(labels), 3), (1.0 - confidence) / 2.0)
    for index, label in enumerate(labels):
        matrix[index, CLASSES.index(label)] = confidence
    return matrix


def test_direction_fold_selects_calibration_and_finite_blend_on_allowed_rows() -> None:
    calibration_keys = _keys(120, horizon=5)
    test_keys = tuple(
        (symbol, timestamp + timedelta(days=30), horizon)
        for symbol, timestamp, horizon in calibration_keys
    )
    calibration_labels = tuple(CLASSES[index % 3] for index in range(120))
    test_labels = tuple(CLASSES[(index + 1) % 3] for index in range(120))
    strong = M7DirectionCandidatePredictions(
        candidate_id="cat-strong",
        family="catboost_multiclass",
        calibration_probabilities=_probabilities(calibration_labels, 0.8),
        test_probabilities=_probabilities(test_labels, 0.8),
        deterministic=True,
    )
    weak = M7DirectionCandidatePredictions(
        candidate_id="quantile-weak",
        family="quantile_cdf",
        calibration_probabilities=np.full((120, 3), 1.0 / 3.0),
        test_probabilities=np.full((120, 3), 1.0 / 3.0),
        deterministic=True,
    )

    result = evaluate_m7_direction_fold(
        fold_id="outer-01",
        horizon=5,
        classes=CLASSES,
        calibration_sample_keys=calibration_keys,
        calibration_labels=calibration_labels,
        outer_test_sample_keys=test_keys,
        outer_test_labels=test_labels,
        candidates=(strong, weak),
        frequency_probabilities=np.full((120, 3), 1.0 / 3.0),
        incumbent_probabilities=_probabilities(test_labels, 0.55),
        calibrator_fit_evidence=_evidence(M7FitComponent.CALIBRATOR, calibration_keys),
        blend_fit_evidence=_evidence(M7FitComponent.BLEND_WEIGHTS, calibration_keys),
        label_health_passed=True,
        eligibility_coverage_passed=True,
        stability_passed=True,
    )

    assert result.metrics.fold_id == "outer-01"
    assert result.metrics.candidate_brier < result.metrics.incumbent_brier
    assert result.metrics.candidate_brier < result.metrics.frequency_brier
    assert result.metrics.probability_validity_passed is True
    assert result.metrics.determinism_passed is True
    assert "cat-strong" in result.blend.member_ids
    assert result.test_probabilities.shape == (120, 3)
    assert set(result.calibration_methods) == {"cat-strong", "quantile-weak"}


def test_direction_fold_rejects_prediction_identity_drift() -> None:
    keys = _keys(30, horizon=1)
    labels = tuple(CLASSES[index % 3] for index in range(30))
    candidate = M7DirectionCandidatePredictions(
        candidate_id="bad",
        family="catboost_multiclass",
        calibration_probabilities=np.full((30, 3), 1.0 / 3.0),
        test_probabilities=np.full((29, 3), 1.0 / 3.0),
        deterministic=True,
    )

    with pytest.raises(M7DevelopmentInputError, match="alignment"):
        evaluate_m7_direction_fold(
            fold_id="outer-01",
            horizon=1,
            classes=CLASSES,
            calibration_sample_keys=keys,
            calibration_labels=labels,
            outer_test_sample_keys=keys,
            outer_test_labels=labels,
            candidates=(candidate,),
            frequency_probabilities=np.full((30, 3), 1.0 / 3.0),
            incumbent_probabilities=np.full((30, 3), 1.0 / 3.0),
            calibrator_fit_evidence=_evidence(M7FitComponent.CALIBRATOR, keys),
            blend_fit_evidence=_evidence(M7FitComponent.BLEND_WEIGHTS, keys),
            label_health_passed=True,
            eligibility_coverage_passed=True,
            stability_passed=True,
        )


def test_direction_fold_determinism_gate_applies_to_selected_blend_members() -> None:
    calibration_keys = _keys(120, horizon=1)
    test_keys = tuple(
        (symbol, timestamp + timedelta(days=30), horizon)
        for symbol, timestamp, horizon in calibration_keys
    )
    labels = tuple(CLASSES[index % 3] for index in range(120))
    wrong_labels = tuple(CLASSES[(index + 1) % 3] for index in range(120))
    selected = M7DirectionCandidatePredictions(
        candidate_id="selected-deterministic",
        family="catboost_multiclass",
        calibration_probabilities=_probabilities(labels, 0.95),
        test_probabilities=_probabilities(labels, 0.95),
        deterministic=True,
    )
    rejected = M7DirectionCandidatePredictions(
        candidate_id="rejected-nondeterministic",
        family="patchtst",
        calibration_probabilities=_probabilities(wrong_labels, 0.99),
        test_probabilities=_probabilities(wrong_labels, 0.99),
        deterministic=False,
    )

    result = evaluate_m7_direction_fold(
        fold_id="outer-01",
        horizon=1,
        classes=CLASSES,
        calibration_sample_keys=calibration_keys,
        calibration_labels=labels,
        outer_test_sample_keys=test_keys,
        outer_test_labels=labels,
        candidates=(selected, rejected),
        frequency_probabilities=np.full((120, 3), 1.0 / 3.0),
        incumbent_probabilities=np.full((120, 3), 1.0 / 3.0),
        calibrator_fit_evidence=_evidence(M7FitComponent.CALIBRATOR, calibration_keys),
        blend_fit_evidence=_evidence(M7FitComponent.BLEND_WEIGHTS, calibration_keys),
        label_health_passed=True,
        eligibility_coverage_passed=True,
        stability_passed=True,
    )

    assert result.blend.member_ids == ("selected-deterministic",)
    assert result.metrics.determinism_passed is True
