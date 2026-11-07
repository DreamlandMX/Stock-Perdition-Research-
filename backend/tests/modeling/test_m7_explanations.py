from __future__ import annotations

import numpy as np
import pytest
import torch

from stock_prediction.modeling.m7_explanations import (
    build_m7_global_explanation,
    build_m7_prediction_explanation,
    integrated_gradients_up_minus_down,
    measure_m7_seed100_sla,
)


def test_global_explanation_aggregates_groups_and_fold_stability() -> None:
    report = build_m7_global_explanation(
        feature_names=("momentum", "volatility", "breadth"),
        feature_groups=("structure", "core", "context"),
        fold_attributions={
            f"fold-{fold}": np.asarray(
                ((0.4, -0.2, (-1.0 if fold == 3 else 1.0) * 0.1),)
            )
            for fold in range(4)
        },
        methods=("native_shap", "integrated_gradients"),
    )

    assert report.fold_count == 4
    assert sum(row.normalized_importance for row in report.feature_groups) == pytest.approx(
        1.0
    )
    by_name = {row.feature_name: row for row in report.features}
    assert by_name["momentum"].stable_across_folds is True
    assert by_name["breadth"].stable_across_folds is False
    assert report.non_causal is True


def test_prediction_explanation_keeps_only_dual_baseline_stable_factors() -> None:
    explanation = build_m7_prediction_explanation(
        prediction_id="prediction-1",
        package_sha256="a" * 64,
        method="integrated_gradients",
        method_version="m7-ig-v1",
        baseline_artifact_sha256="b" * 64,
        feature_names=("momentum", "volatility", "unstable"),
        feature_groups=("structure", "core", "context"),
        feature_values=(1.2, 0.4, -0.2),
        zero_baseline_attributions=(0.30, -0.20, 0.10),
        median_baseline_attributions=(0.20, -0.10, -0.10),
    )

    assert explanation.status == "available"
    assert [row.feature_name for row in explanation.top_positive] == ["momentum"]
    assert [row.feature_name for row in explanation.top_negative] == ["volatility"]
    assert explanation.non_causal is True
    assert explanation.target == "up_relative_to_down"


def test_prediction_explanation_marks_unstable_instead_of_inventing_factors() -> None:
    explanation = build_m7_prediction_explanation(
        prediction_id="prediction-2",
        package_sha256="a" * 64,
        method="native_shap",
        method_version="m7-shap-v1",
        baseline_artifact_sha256="b" * 64,
        feature_names=("factor",),
        feature_groups=("core",),
        feature_values=(1.0,),
        zero_baseline_attributions=(0.2,),
        median_baseline_attributions=(-0.1,),
    )

    assert explanation.status == "explanation_unstable"
    assert explanation.top_positive == ()
    assert explanation.top_negative == ()


def test_integrated_gradients_uses_32_steps_and_up_minus_down_target() -> None:
    class LinearHead(torch.nn.Module):
        def forward(self, values: torch.Tensor) -> torch.Tensor:
            down = 2.0 * values[:, 0] - values[:, 1]
            flat = values[:, 0] * 0.0
            up = -values[:, 0] + 3.0 * values[:, 1]
            return torch.stack((down, flat, up), dim=1)

    values = torch.tensor([[2.0, 4.0]], dtype=torch.float32)
    baseline = torch.zeros_like(values)

    attribution = integrated_gradients_up_minus_down(
        LinearHead(),
        values,
        baseline,
    )

    assert np.allclose(attribution.detach().numpy(), [[-6.0, 16.0]])


def test_seed100_sla_keeps_probabilities_when_explanations_fail() -> None:
    ticks = iter((0.0, 12.0, 12.0, 24.0))

    def explanations(_: np.ndarray) -> object:
        raise RuntimeError("explanation backend unavailable")

    report = measure_m7_seed100_sla(
        predict=lambda: np.tile(np.asarray((0.2, 0.3, 0.5)), (100, 1)),
        explain=explanations,
        monotonic_clock=lambda: next(ticks),
    )

    assert report.inference_sla_passed is True
    assert report.explanation_sla_passed is False
    assert report.probability_artifact_valid is True
    assert report.explanation_availability == "unavailable"
    assert report.warning == "m7_explanation_generation_failed"
