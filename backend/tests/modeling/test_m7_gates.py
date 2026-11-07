from __future__ import annotations

import pytest

from stock_prediction.modeling.m7_gates import (
    M7DirectionFoldMetrics,
    M7RangeFoldMetrics,
    evaluate_m7_direction_bundle,
    evaluate_m7_direction_horizon,
    evaluate_m7_range_exit_20,
)
from stock_prediction.modeling.report import (
    M7_REPORT_SECTION_NAMES,
    build_m7_training_report_sections,
)


def _direction_folds(**overrides: object) -> tuple[M7DirectionFoldMetrics, ...]:
    rows = []
    for index in range(4):
        values = {
            "fold_id": f"fold-{index}",
            "candidate_brier": 0.190,
            "frequency_brier": 0.205,
            "incumbent_brier": 0.200,
            "adaptive_macro_ece": 0.040,
            "label_health_passed": True,
            "eligibility_coverage_passed": True,
            "probability_validity_passed": True,
            "determinism_passed": True,
            "stability_passed": True,
            "accuracy": 0.51,
            "macro_f1": 0.49,
        }
        values.update(overrides)
        rows.append(M7DirectionFoldMetrics(**values))
    return tuple(rows)


def test_direction_horizon_passes_exact_six_gate_groups() -> None:
    decision = evaluate_m7_direction_horizon(5, _direction_folds())

    assert decision.status == "passed"
    assert decision.failed_gates == ()
    assert decision.metrics["frequency_fold_wins"] == 4
    assert decision.metrics["incumbent_fold_wins"] == 4
    assert decision.metrics["ece_fold_passes"] == 4
    assert decision.metrics["maximum_relative_degradation"] < 0
    assert "accuracy_mean" in decision.diagnostics
    assert "macro_f1_mean" in decision.diagnostics


@pytest.mark.parametrize(
    ("overrides", "failure"),
    [
        ({"candidate_brier": 0.206}, "frequency_fold_wins"),
        ({"candidate_brier": 0.201}, "incumbent_fold_wins"),
        ({"adaptive_macro_ece": 0.051}, "ece_fold_passes"),
        ({"candidate_brier": 0.205}, "mean_brier"),
        ({"candidate_brier": 0.205}, "maximum_relative_degradation"),
        ({"determinism_passed": False}, "determinism"),
    ],
)
def test_direction_horizon_truth_table(
    overrides: dict[str, object], failure: str
) -> None:
    decision = evaluate_m7_direction_horizon(1, _direction_folds(**overrides))

    assert decision.status == "failed"
    assert failure in decision.failed_gates


def test_direction_horizon_allows_exact_two_percent_single_fold_degradation() -> None:
    folds = list(_direction_folds())
    folds[0] = M7DirectionFoldMetrics(
        **{
            **folds[0].model_dump(),
            "candidate_brier": 0.204,
        }
    )
    decision = evaluate_m7_direction_horizon(20, tuple(folds))

    assert "maximum_relative_degradation" not in decision.failed_gates


def test_direction_horizon_accepts_exactly_three_wins_and_three_ece_folds() -> None:
    folds = list(_direction_folds())
    folds[0] = folds[0].model_copy(
        update={"candidate_brier": 0.2, "adaptive_macro_ece": 0.051}
    )

    decision = evaluate_m7_direction_horizon(5, tuple(folds))

    assert decision.status == "passed"
    assert decision.metrics["frequency_fold_wins"] == 4
    assert decision.metrics["incumbent_fold_wins"] == 3
    assert decision.metrics["ece_fold_passes"] == 3


def test_one_failed_horizon_fails_atomic_bundle() -> None:
    horizons = {
        1: evaluate_m7_direction_horizon(1, _direction_folds()),
        5: evaluate_m7_direction_horizon(5, _direction_folds()),
        20: evaluate_m7_direction_horizon(
            20,
            _direction_folds(determinism_passed=False),
        ),
    }
    decision = evaluate_m7_direction_bundle(
        horizons,
        recipe_ids={1: "recipe-v4", 5: "recipe-v4", 20: "recipe-v4"},
        package_ids={1: "package-a", 5: "package-a", 20: "package-a"},
        bootstrap_delta_brier_upper_90=-0.0001,
        inference_sla_passed=True,
        explanation_sla_passed=True,
        economic_catastrophic_guardrail_passed=True,
    )

    assert decision.status == "failed"
    assert "horizon_20" in decision.failed_gates
    assert decision.metrics["shadow_freeze_eligible"] is False


def test_direction_bundle_enforces_improvement_bootstrap_identity_and_sla() -> None:
    weak_folds = _direction_folds(candidate_brier=0.1995)
    horizons = {
        horizon: evaluate_m7_direction_horizon(horizon, weak_folds)
        for horizon in (1, 5, 20)
    }
    decision = evaluate_m7_direction_bundle(
        horizons,
        recipe_ids={1: "recipe-a", 5: "recipe-a", 20: "recipe-b"},
        package_ids={1: "package-a", 5: "package-a", 20: "package-b"},
        bootstrap_delta_brier_upper_90=0.00001,
        inference_sla_passed=False,
        explanation_sla_passed=False,
        economic_catastrophic_guardrail_passed=False,
    )

    assert set(decision.failed_gates) == {
        "macro_relative_brier_improvement",
        "bootstrap_delta_brier_upper_90",
        "single_recipe",
        "single_package",
        "inference_sla",
        "explanation_sla",
        "economic_catastrophic_guardrail",
    }


def test_direction_bundle_accepts_exact_half_percent_and_zero_bootstrap_bound() -> None:
    boundary_folds = _direction_folds(candidate_brier=0.199)
    horizons = {
        horizon: evaluate_m7_direction_horizon(horizon, boundary_folds)
        for horizon in (1, 5, 20)
    }

    decision = evaluate_m7_direction_bundle(
        horizons,
        recipe_ids={horizon: "recipe-v4" for horizon in (1, 5, 20)},
        package_ids={horizon: "package-a" for horizon in (1, 5, 20)},
        bootstrap_delta_brier_upper_90=0.0,
        inference_sla_passed=True,
        explanation_sla_passed=True,
        economic_catastrophic_guardrail_passed=True,
    )

    assert decision.status == "passed"
    assert decision.metrics["macro_relative_brier_improvement"] == pytest.approx(0.005)
    assert decision.metrics["shadow_freeze_eligible"] is True


def _range_folds(**overrides: object) -> tuple[M7RangeFoldMetrics, ...]:
    rows = []
    for index in range(4):
        values = {
            "fold_id": f"fold-{index}",
            "candidate_time_brier": 0.170,
            "null_time_brier": 0.190,
            "incumbent_time_brier": 0.180,
            "cumulative_calibration_error": 0.040,
            "candidate_c_index": 0.570,
            "incumbent_c_index": 0.575,
            "label_censor_health_passed": True,
            "coverage_passed": True,
            "stability_passed": True,
            "determinism_passed": True,
        }
        values.update(overrides)
        rows.append(M7RangeFoldMetrics(**values))
    return tuple(rows)


def test_range_gate_passes_and_c_index_remains_diagnostic_detail() -> None:
    decision = evaluate_m7_range_exit_20(
        _range_folds(),
        c_index_delta_lower_90=-0.009,
        monotonicity_violations=0,
        economic_catastrophic_guardrail_passed=True,
    )

    assert decision.status == "passed"
    assert decision.metrics["null_fold_wins"] == 4
    assert decision.metrics["incumbent_fold_wins"] == 4
    assert decision.diagnostics["candidate_c_index_mean"] == pytest.approx(0.57)


def test_range_gate_truth_table_catches_each_required_guardrail() -> None:
    decision = evaluate_m7_range_exit_20(
        _range_folds(
            candidate_time_brier=0.195,
            cumulative_calibration_error=0.051,
            candidate_c_index=0.55,
            coverage_passed=False,
        ),
        c_index_delta_lower_90=-0.02,
        monotonicity_violations=1,
        economic_catastrophic_guardrail_passed=False,
    )

    assert set(decision.failed_gates) == {
        "null_fold_wins",
        "incumbent_fold_wins",
        "calibration_fold_passes",
        "mean_time_brier",
        "mean_c_index_degradation",
        "bootstrap_c_index_degradation",
        "monotonicity",
        "coverage",
        "economic_catastrophic_guardrail",
    }


def test_report_requires_every_section_and_explicit_not_run_reason() -> None:
    completed = {
        name: {"evidence": name}
        for name in M7_REPORT_SECTION_NAMES
        if name not in {"drift_evidence", "window_consumption"}
    }
    sections = build_m7_training_report_sections(
        completed_sections=completed,
        failed_sections={},
        not_run_sections={
            "drift_evidence": "labels_not_mature",
            "window_consumption": "blind_window_not_registered",
        },
    )

    assert tuple(sections) == M7_REPORT_SECTION_NAMES
    assert sections["drift_evidence"]["status"] == "not_run"
    assert sections["drift_evidence"]["reason"] == "labels_not_mature"
    assert sections["bundle_results"]["status"] == "completed"
    assert sections["bundle_results"]["reason"] is None

    failed = build_m7_training_report_sections(
        completed_sections={
            name: {"evidence": name}
            for name in M7_REPORT_SECTION_NAMES
            if name != "bundle_results"
        },
        failed_sections={"bundle_results": "horizon_20"},
        not_run_sections={},
    )
    assert failed["bundle_results"] == {
        "status": "failed",
        "reason": "horizon_20",
        "payload": None,
    }

    with pytest.raises(ValueError, match="exactly_once"):
        build_m7_training_report_sections(
            completed_sections={"bundle_results": {}},
            failed_sections={},
            not_run_sections={},
        )
    with pytest.raises(ValueError, match="non_empty_reason"):
        build_m7_training_report_sections(
            completed_sections=completed,
            failed_sections={},
            not_run_sections={"drift_evidence": "", "window_consumption": "later"},
        )
