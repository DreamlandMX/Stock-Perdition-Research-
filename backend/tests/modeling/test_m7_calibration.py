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
from stock_prediction.modeling.m7_calibration import (
    M7BlendCandidate,
    M7CalibrationInputError,
    fit_m7_direction_calibrators,
    fit_m7_range_calibrators,
    project_range_exit_curve,
    select_finite_m7_blend,
)


def _keys(count: int = 12):
    return tuple(
        (
            f"US.{index:03d}",
            datetime(2026, 1, 2, 21, tzinfo=UTC) + timedelta(days=index),
            (1, 5, 20)[index % 3],
        )
        for index in range(count)
    )


def _evidence(
    component: M7FitComponent = M7FitComponent.CALIBRATOR,
) -> M7FittedObjectEvidence:
    keys = _keys()
    return M7FittedObjectEvidence(
        component=component,
        fit_role=M7PartitionRole.CALIBRATION,
        partition_hash="a" * 64,
        sample_keys=keys,
        sample_keys_hash=hash_m7_sample_keys(keys),
    )


def _direction_inputs():
    logits = np.asarray(
        [
            [3.0, 0.0, -1.0],
            [0.0, 2.0, -0.5],
            [-1.0, 0.0, 3.0],
            [2.0, 0.4, -0.5],
        ]
        * 3,
        dtype=np.float64,
    )
    labels = np.asarray([0, 1, 2, 0] * 3, dtype=np.int64)
    return logits, labels


def test_direction_options_are_exact_and_return_simplex_probabilities() -> None:
    logits, labels = _direction_inputs()
    calibrators = fit_m7_direction_calibrators(
        logits,
        labels,
        fit_evidence=_evidence(),
        oof_fold_ids=(0,) * 6 + (1,) * 6,
    )

    assert tuple(item.method for item in calibrators) == (
        "identity",
        "scalar_temperature",
        "vector_scaling",
    )
    for calibrator in calibrators:
        probabilities = calibrator.apply(logits)
        assert probabilities.shape == (12, 3)
        assert np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-9)
        assert np.all((probabilities >= 0) & (probabilities <= 1))
        assert len(calibrator.calibrator_hash) == 64
        assert calibrator.raw_brier >= 0
        assert calibrator.calibrated_brier >= 0


def test_calibration_rejects_outer_test_evidence_and_non_blocked_oof() -> None:
    logits, labels = _direction_inputs()
    keys = _keys()
    forged_outer = M7FittedObjectEvidence.model_construct(
        component=M7FitComponent.CALIBRATOR,
        fit_role=M7PartitionRole.OUTER_TEST,
        partition_hash="a" * 64,
        sample_keys=keys,
        sample_keys_hash=hash_m7_sample_keys(keys),
    )

    with pytest.raises(M7CalibrationInputError, match="forbidden_fit_evidence"):
        fit_m7_direction_calibrators(
            logits,
            labels,
            fit_evidence=forged_outer,
            oof_fold_ids=(0,) * 6 + (1,) * 6,
        )
    with pytest.raises(M7CalibrationInputError, match="blocked_oof"):
        fit_m7_direction_calibrators(
            logits,
            labels,
            fit_evidence=_evidence(),
            oof_fold_ids=(0, 1) * 6,
        )


def test_range_options_refit_on_oof_and_project_before_scoring() -> None:
    probabilities = np.asarray(
        [
            [0.30, 0.20, 0.70],
            [0.10, 0.50, 0.40],
            [0.80, 0.60, 0.55],
            [0.15, 0.35, 0.75],
        ]
        * 3,
        dtype=np.float64,
    )
    labels = np.asarray(
        [
            [0, 0, 1],
            [0, 1, 1],
            [1, 1, 1],
            [0, 0, 1],
        ]
        * 3,
        dtype=np.int64,
    )

    projected = project_range_exit_curve(probabilities)
    assert np.all(projected[:, 0] <= projected[:, 1])
    assert np.all(projected[:, 1] <= projected[:, 2])

    calibrators = fit_m7_range_calibrators(
        probabilities,
        labels,
        fit_evidence=_evidence(),
        oof_fold_ids=(0,) * 6 + (1,) * 6,
    )
    assert tuple(item.method for item in calibrators) == (
        "identity",
        "event_rate_shrinkage",
        "platt",
        "isotonic",
        "beta",
    )
    for calibrator in calibrators:
        output = calibrator.apply(probabilities)
        assert np.all((output >= 0) & (output <= 1))
        assert np.all(output[:, 0] <= output[:, 1])
        assert np.all(output[:, 1] <= output[:, 2])
        expected = float(np.mean((output - labels) ** 2))
        assert calibrator.calibrated_brier == pytest.approx(expected)


def test_finite_blend_uses_tenths_three_members_and_two_per_family() -> None:
    _, labels = _direction_inputs()
    baseline = np.eye(3, dtype=np.float64)[labels]
    uniform = np.full_like(baseline, 1 / 3)
    candidates = (
        M7BlendCandidate("tree-a", "catboost_multiclass", baseline * 0.8 + 0.2 / 3),
        M7BlendCandidate("tree-b", "catboost_multiclass", baseline * 0.7 + 0.3 / 3),
        M7BlendCandidate("tree-c", "catboost_multiclass", uniform),
        M7BlendCandidate("deep-a", "dlinear", baseline * 0.6 + 0.4 / 3),
        M7BlendCandidate("patch-a", "patchtst", uniform),
    )
    blend = select_finite_m7_blend(
        candidates,
        labels,
        fit_evidence=_evidence(M7FitComponent.BLEND_WEIGHTS),
        registered_candidate_ids=tuple(item.candidate_id for item in candidates),
    )

    assert 1 <= len(blend.member_ids) <= 3
    assert sum(blend.weights) == pytest.approx(1.0)
    assert all(
        round(weight * 10) == pytest.approx(weight * 10) for weight in blend.weights
    )
    selected_families = [
        next(item.family for item in candidates if item.candidate_id == candidate_id)
        for candidate_id in blend.member_ids
    ]
    assert all(
        selected_families.count(family) <= 2 for family in set(selected_families)
    )
    assert "tree-c" not in blend.eligible_candidate_ids
    assert np.allclose(blend.apply(candidates).sum(axis=1), 1.0, atol=1e-9)


def test_blend_rejects_unregistered_candidate_addition_and_outer_fit() -> None:
    _, labels = _direction_inputs()
    candidate = M7BlendCandidate(
        "tree-a",
        "catboost_multiclass",
        np.full((12, 3), 1 / 3),
    )
    with pytest.raises(M7CalibrationInputError, match="registered_candidate_set"):
        select_finite_m7_blend(
            (candidate,),
            labels,
            fit_evidence=_evidence(M7FitComponent.BLEND_WEIGHTS),
            registered_candidate_ids=("tree-a", "late-added"),
        )

    keys = _keys()
    forged_outer = M7FittedObjectEvidence.model_construct(
        component=M7FitComponent.BLEND_WEIGHTS,
        fit_role=M7PartitionRole.OUTER_TEST,
        partition_hash="a" * 64,
        sample_keys=keys,
        sample_keys_hash=hash_m7_sample_keys(keys),
    )
    with pytest.raises(M7CalibrationInputError, match="forbidden_fit_evidence"):
        select_finite_m7_blend(
            (candidate,),
            labels,
            fit_evidence=forged_outer,
            registered_candidate_ids=("tree-a",),
        )
