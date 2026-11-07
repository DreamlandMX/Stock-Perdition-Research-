"""Immutable M7 explanation records, Integrated Gradients, and batch SLA evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import time
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
import torch


class M7ExplanationDriver(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    feature_name: str = Field(min_length=1)
    feature_group: str = Field(min_length=1)
    feature_value: float
    contribution: float
    direction: Literal["positive", "negative"]


class M7PredictionExplanation(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    prediction_id: str = Field(min_length=1)
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    method: str = Field(min_length=1)
    method_version: str = Field(min_length=1)
    baseline_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target: Literal["up_relative_to_down"] = "up_relative_to_down"
    status: Literal["available", "explanation_unstable"]
    top_positive: tuple[M7ExplanationDriver, ...]
    top_negative: tuple[M7ExplanationDriver, ...]
    stable_factor_count: int = Field(ge=0)
    non_causal: Literal[True] = True
    caveat: str = "Associational explanation only; not a causal claim."


class M7GlobalFeatureImportance(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    feature_name: str = Field(min_length=1)
    feature_group: str = Field(min_length=1)
    mean_absolute_contribution: float = Field(ge=0)
    mean_signed_contribution: float
    stable_across_folds: bool


class M7GlobalFeatureGroupImportance(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    feature_group: str = Field(min_length=1)
    normalized_importance: float = Field(ge=0, le=1)


class M7GlobalExplanation(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    methods: tuple[str, ...] = Field(min_length=1)
    target: Literal["up_relative_to_down"] = "up_relative_to_down"
    fold_count: int = Field(ge=4)
    features: tuple[M7GlobalFeatureImportance, ...] = Field(min_length=1)
    feature_groups: tuple[M7GlobalFeatureGroupImportance, ...] = Field(min_length=1)
    non_causal: Literal[True] = True
    caveat: str = "Associational explanation only; not a causal claim."


@dataclass(frozen=True, slots=True)
class M7InferenceSLAReport:
    inference_seconds: float
    explanation_seconds: float
    total_seconds: float
    seed100_row_count: int
    inference_sla_passed: bool
    explanation_sla_passed: bool
    probability_artifact_valid: bool
    explanation_availability: Literal["available", "unavailable"]
    warning: str | None
    budget_seconds: float = 300.0


def build_m7_global_explanation(
    *,
    feature_names: Sequence[str],
    feature_groups: Sequence[str],
    fold_attributions: Mapping[str, object],
    methods: Sequence[str],
) -> M7GlobalExplanation:
    """Aggregate signed up-minus-down contributions without mixing fold identity."""

    names = tuple(feature_names)
    groups = tuple(feature_groups)
    method_order = tuple(dict.fromkeys(methods))
    folds = tuple(sorted(fold_attributions))
    matrices = {
        fold_id: np.asarray(fold_attributions[fold_id], dtype=np.float64)
        for fold_id in folds
    }
    if (
        not names
        or len(set(names)) != len(names)
        or len(groups) != len(names)
        or len(folds) < 4
        or not method_order
        or any(
            matrix.ndim != 2
            or matrix.shape[0] < 1
            or matrix.shape[1] != len(names)
            or not np.all(np.isfinite(matrix))
            for matrix in matrices.values()
        )
    ):
        raise ValueError("m7_global_explanation_inputs_invalid")
    stacked = np.vstack(tuple(matrices.values()))
    feature_rows: list[M7GlobalFeatureImportance] = []
    absolute_by_feature: dict[str, float] = {}
    for index, feature_name in enumerate(names):
        fold_means = np.asarray(
            [np.mean(matrices[fold_id][:, index]) for fold_id in folds],
            dtype=np.float64,
        )
        signs = np.sign(fold_means)
        stable = bool(np.all(signs == signs[0]) and signs[0] != 0)
        mean_absolute = float(np.mean(np.abs(stacked[:, index])))
        absolute_by_feature[feature_name] = mean_absolute
        feature_rows.append(
            M7GlobalFeatureImportance(
                feature_name=feature_name,
                feature_group=groups[index],
                mean_absolute_contribution=mean_absolute,
                mean_signed_contribution=float(np.mean(stacked[:, index])),
                stable_across_folds=stable,
            )
        )
    group_values = {
        group: sum(
            absolute_by_feature[name]
            for name, feature_group in zip(names, groups, strict=True)
            if feature_group == group
        )
        for group in sorted(set(groups))
    }
    total = sum(group_values.values())
    if total <= 0:
        raise ValueError("m7_global_explanation_zero_importance")
    return M7GlobalExplanation(
        methods=method_order,
        fold_count=len(folds),
        features=tuple(
            sorted(
                feature_rows,
                key=lambda row: (-row.mean_absolute_contribution, row.feature_name),
            )
        ),
        feature_groups=tuple(
            M7GlobalFeatureGroupImportance(
                feature_group=group,
                normalized_importance=value / total,
            )
            for group, value in group_values.items()
        ),
    )


def build_m7_prediction_explanation(
    *,
    prediction_id: str,
    package_sha256: str,
    method: str,
    method_version: str,
    baseline_artifact_sha256: str,
    feature_names: Sequence[str],
    feature_groups: Sequence[str],
    feature_values: Sequence[float],
    zero_baseline_attributions: Sequence[float],
    median_baseline_attributions: Sequence[float],
) -> M7PredictionExplanation:
    """Persist at most five signed factors that agree under both fixed baselines."""

    names = tuple(feature_names)
    groups = tuple(feature_groups)
    values = np.asarray(tuple(feature_values), dtype=np.float64)
    zero = np.asarray(tuple(zero_baseline_attributions), dtype=np.float64)
    median = np.asarray(tuple(median_baseline_attributions), dtype=np.float64)
    if (
        not names
        or len(set(names)) != len(names)
        or len(groups) != len(names)
        or values.shape != (len(names),)
        or zero.shape != values.shape
        or median.shape != values.shape
        or not np.all(np.isfinite(values))
        or not np.all(np.isfinite(zero))
        or not np.all(np.isfinite(median))
    ):
        raise ValueError("m7_explanation_inputs_invalid")
    stable = (np.sign(zero) == np.sign(median)) & (np.sign(zero) != 0)
    contribution = (zero + median) / 2.0
    indexes = [index for index, value in enumerate(stable) if bool(value)]
    positive = sorted(
        (index for index in indexes if contribution[index] > 0),
        key=lambda index: (-abs(float(contribution[index])), names[index]),
    )[:5]
    negative = sorted(
        (index for index in indexes if contribution[index] < 0),
        key=lambda index: (-abs(float(contribution[index])), names[index]),
    )[:5]

    def driver(
        index: int,
        direction: Literal["positive", "negative"],
    ) -> M7ExplanationDriver:
        return M7ExplanationDriver(
            feature_name=names[index],
            feature_group=groups[index],
            feature_value=float(values[index]),
            contribution=float(contribution[index]),
            direction=direction,
        )

    available = bool(indexes)
    return M7PredictionExplanation(
        prediction_id=prediction_id,
        package_sha256=package_sha256,
        method=method,
        method_version=method_version,
        baseline_artifact_sha256=baseline_artifact_sha256,
        status="available" if available else "explanation_unstable",
        top_positive=tuple(driver(index, "positive") for index in positive),
        top_negative=tuple(driver(index, "negative") for index in negative),
        stable_factor_count=len(indexes),
    )


def integrated_gradients_up_minus_down(
    model: Callable[[torch.Tensor], torch.Tensor],
    values: torch.Tensor,
    baseline: torch.Tensor,
    *,
    steps: int = 32,
) -> torch.Tensor:
    """Attribute the up-minus-down logit using the frozen 32-step rule."""

    if steps != 32 or values.shape != baseline.shape or values.ndim < 2:
        raise ValueError("m7_integrated_gradients_contract_invalid")
    if not torch.isfinite(values).all() or not torch.isfinite(baseline).all():
        raise ValueError("m7_integrated_gradients_input_non_finite")
    total_gradient = torch.zeros_like(values)
    delta = values - baseline
    for step in range(1, steps + 1):
        interpolated = (baseline + (float(step) / steps) * delta).detach()
        interpolated.requires_grad_(True)
        logits = model(interpolated)
        if logits.shape != (values.shape[0], 3) or not torch.isfinite(logits).all():
            raise ValueError("m7_integrated_gradients_logits_invalid")
        target = logits[:, 2] - logits[:, 0]
        gradient = torch.autograd.grad(target.sum(), interpolated)[0]
        total_gradient += gradient.detach()
    attribution = delta * total_gradient / float(steps)
    if not torch.isfinite(attribution).all():
        raise ValueError("m7_integrated_gradients_output_non_finite")
    return attribution


def measure_m7_seed100_sla(
    *,
    predict: Callable[[], object],
    explain: Callable[[np.ndarray], Any],
    monotonic_clock: Callable[[], float] = time.monotonic,
    budget_seconds: float = 300.0,
) -> M7InferenceSLAReport:
    """Measure immutable probability generation and post-inference explanations."""

    if budget_seconds <= 0:
        raise ValueError("m7_inference_sla_budget_invalid")
    inference_started = monotonic_clock()
    probabilities = np.asarray(predict(), dtype=np.float64)
    inference_finished = monotonic_clock()
    probability_valid = bool(
        probabilities.shape == (100, 3)
        and np.all(np.isfinite(probabilities))
        and np.all(probabilities >= 0)
        and np.all(probabilities <= 1)
        and np.allclose(
            probabilities.sum(axis=1),
            1.0,
            rtol=0.0,
            atol=1e-9,
        )
    )
    if not probability_valid:
        raise ValueError("m7_seed100_probability_artifact_invalid")
    explanation_started = monotonic_clock()
    warning: str | None = None
    available = True
    try:
        result = explain(probabilities.copy())
        if result is None:
            raise ValueError("m7_explanation_output_missing")
    except Exception:  # explanation failure must not mutate valid probabilities
        available = False
        warning = "m7_explanation_generation_failed"
    explanation_finished = monotonic_clock()
    inference_seconds = inference_finished - inference_started
    explanation_seconds = explanation_finished - explanation_started
    total_seconds = explanation_finished - inference_started
    return M7InferenceSLAReport(
        inference_seconds=inference_seconds,
        explanation_seconds=explanation_seconds,
        total_seconds=total_seconds,
        seed100_row_count=len(probabilities),
        inference_sla_passed=inference_seconds <= budget_seconds,
        explanation_sla_passed=available and total_seconds <= budget_seconds,
        probability_artifact_valid=True,
        explanation_availability="available" if available else "unavailable",
        warning=warning,
        budget_seconds=budget_seconds,
    )


__all__ = [
    "M7ExplanationDriver",
    "M7GlobalExplanation",
    "M7GlobalFeatureGroupImportance",
    "M7GlobalFeatureImportance",
    "M7InferenceSLAReport",
    "M7PredictionExplanation",
    "build_m7_global_explanation",
    "build_m7_prediction_explanation",
    "integrated_gradients_up_minus_down",
    "measure_m7_seed100_sla",
]
