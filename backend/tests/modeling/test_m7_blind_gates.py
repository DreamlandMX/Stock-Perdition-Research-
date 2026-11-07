from __future__ import annotations

from datetime import date

import pytest

from stock_prediction.modeling.m7_blind_gates import (
    M7BlindDirectionEvidence,
    M7BlindRangeEvidence,
    evaluate_m7_blind_direction,
    evaluate_m7_blind_range,
)


def _direction_evidence(
    horizon: int, *, candidate_confidence: float = 0.98
) -> M7BlindDirectionEvidence:
    labels = ("up", "flat", "down") * 30
    candidate = []
    incumbent = []
    for label in labels:
        winning = {"down": 0, "flat": 1, "up": 2}[label]
        row = [(1.0 - candidate_confidence) / 2.0] * 3
        row[winning] = candidate_confidence
        candidate.append(tuple(row))
        old = [0.1, 0.1, 0.1]
        old[winning] = 0.8
        incumbent.append(tuple(old))
    return M7BlindDirectionEvidence(
        horizon=horizon,
        sample_keys=tuple(
            (f"S{index % 10:02d}", date(2026, 1, 2 + index // 10), horizon)
            for index in range(len(labels))
        ),
        labels=labels,
        candidate_probabilities=tuple(candidate),
        incumbent_probabilities=tuple(incumbent),
        coverage_passed=True,
        artifact_integrity_passed=True,
        probability_validity_passed=True,
        inference_sla_passed=True,
    )


def test_direction_blind_requires_all_horizons_and_macro_improvement() -> None:
    decision = evaluate_m7_blind_direction(
        {horizon: _direction_evidence(horizon) for horizon in (1, 5, 20)},
        n_bootstrap=200,
    )

    assert decision.status == "awaiting_manual_approval"
    assert decision.failed_gates == ()
    assert decision.metrics["macro_relative_brier_improvement"] > 0


def test_direction_blind_is_atomic_when_one_horizon_degrades() -> None:
    evidence = {horizon: _direction_evidence(horizon) for horizon in (1, 5, 20)}
    evidence[5] = _direction_evidence(5, candidate_confidence=0.30)

    decision = evaluate_m7_blind_direction(evidence, n_bootstrap=200)

    assert decision.status == "rejected"
    assert "direction_5d_brier" in decision.failed_gates


def test_direction_blind_rejects_misaligned_sample_keys() -> None:
    evidence = _direction_evidence(1)
    with pytest.raises(ValueError, match="sample_keys_mismatch"):
        M7BlindDirectionEvidence(
            **{
                **evidence.model_dump(),
                "incumbent_sample_keys": tuple(reversed(evidence.sample_keys)),
            }
        )


def test_range_blind_requires_calibration_improvement_brier_and_monotonicity() -> None:
    observed = (0, 1) * 50
    evidence = M7BlindRangeEvidence(
        sample_keys=tuple(
            (f"S{index % 10:02d}", date(2026, 4, 1 + index // 10), 20)
            for index in range(100)
        ),
        observed=observed,
        candidate_probability_5d=tuple(
            0.1 if value == 0 else 0.4 for value in observed
        ),
        candidate_probability_20d=tuple(
            0.2 if value == 0 else 0.8 for value in observed
        ),
        incumbent_probability_20d=tuple(
            0.35 if value == 0 else 0.65 for value in observed
        ),
        five_day_anchor_hash="a" * 64,
        candidate_c_index=0.61,
        incumbent_c_index=0.60,
        c_index_delta_lower_90=0.0,
        coverage_passed=True,
        artifact_integrity_passed=True,
        inference_sla_passed=True,
    )

    passed = evaluate_m7_blind_range(evidence)
    violated = evaluate_m7_blind_range(
        M7BlindRangeEvidence(
            **{
                **evidence.model_dump(),
                "candidate_probability_5d": tuple(0.9 for _ in observed),
            }
        )
    )

    assert passed.status == "awaiting_manual_approval"
    assert violated.status == "rejected"
    assert "range_monotonicity" in violated.failed_gates
