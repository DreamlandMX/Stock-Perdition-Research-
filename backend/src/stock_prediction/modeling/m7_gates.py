"""Exact development gates for M7 direction and range-exit bundles."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
import math
from typing import TypeVar

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator


class M7GateStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


MetricValue = float | int | bool | str
FoldMetric = TypeVar("FoldMetric", bound=BaseModel)


class M7DirectionFoldMetrics(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    fold_id: str = Field(min_length=1)
    candidate_brier: float = Field(ge=0)
    frequency_brier: float = Field(gt=0)
    incumbent_brier: float = Field(gt=0)
    adaptive_macro_ece: float = Field(ge=0, le=1)
    label_health_passed: bool
    eligibility_coverage_passed: bool
    probability_validity_passed: bool
    determinism_passed: bool
    stability_passed: bool
    accuracy: float | None = Field(default=None, ge=0, le=1)
    macro_f1: float | None = Field(default=None, ge=0, le=1)


class M7RangeFoldMetrics(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    fold_id: str = Field(min_length=1)
    candidate_time_brier: float = Field(ge=0)
    null_time_brier: float = Field(gt=0)
    incumbent_time_brier: float = Field(gt=0)
    cumulative_calibration_error: float = Field(ge=0, le=1)
    candidate_c_index: float = Field(ge=0, le=1)
    incumbent_c_index: float = Field(ge=0, le=1)
    label_censor_health_passed: bool
    coverage_passed: bool
    stability_passed: bool
    determinism_passed: bool


class M7GateDecision(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    gate_id: str = Field(min_length=1)
    status: M7GateStatus
    failed_gates: tuple[str, ...]
    metrics: dict[str, MetricValue]
    diagnostics: dict[str, MetricValue]
    reason: str | None

    @model_validator(mode="after")
    def validate_decision(self) -> "M7GateDecision":
        if len(set(self.failed_gates)) != len(self.failed_gates):
            raise ValueError("M7 failed gates must be unique")
        if self.status is M7GateStatus.PASSED and (
            self.failed_gates or self.reason is not None
        ):
            raise ValueError("M7 passing gate cannot contain failures")
        if self.status is M7GateStatus.FAILED and (
            not self.failed_gates or not self.reason
        ):
            raise ValueError("M7 failed gate requires failed gates and reason")
        return self


def evaluate_m7_direction_horizon(
    horizon: int,
    folds: Sequence[M7DirectionFoldMetrics],
) -> M7GateDecision:
    if horizon not in {1, 5, 20}:
        raise ValueError("m7_direction_horizon_invalid")
    rows = _exact_four_folds(folds)
    candidate = np.asarray([row.candidate_brier for row in rows], dtype=np.float64)
    frequency = np.asarray([row.frequency_brier for row in rows], dtype=np.float64)
    incumbent = np.asarray([row.incumbent_brier for row in rows], dtype=np.float64)
    relative_degradation = (candidate - incumbent) / incumbent
    frequency_wins = int(np.sum(candidate < frequency))
    incumbent_wins = int(np.sum(candidate < incumbent))
    ece_passes = sum(row.adaptive_macro_ece <= 0.05 for row in rows)
    mean_candidate = float(np.mean(candidate))
    mean_incumbent = float(np.mean(incumbent))
    maximum_degradation = float(np.max(relative_degradation))
    reliability = {
        "label_health": all(row.label_health_passed for row in rows),
        "eligibility_coverage": all(row.eligibility_coverage_passed for row in rows),
        "probability_validity": all(row.probability_validity_passed for row in rows),
        "determinism": all(row.determinism_passed for row in rows),
        "stability": all(row.stability_passed for row in rows),
    }
    failures: list[str] = []
    if frequency_wins < 3:
        failures.append("frequency_fold_wins")
    if incumbent_wins < 3:
        failures.append("incumbent_fold_wins")
    if ece_passes < 3:
        failures.append("ece_fold_passes")
    if mean_candidate > mean_incumbent + 1e-15:
        failures.append("mean_brier")
    if maximum_degradation > 0.02 + 1e-12:
        failures.append("maximum_relative_degradation")
    failures.extend(name for name, passed in reliability.items() if not passed)
    metrics: dict[str, MetricValue] = {
        "horizon": horizon,
        "fold_count": 4,
        "frequency_fold_wins": frequency_wins,
        "incumbent_fold_wins": incumbent_wins,
        "ece_fold_passes": ece_passes,
        "candidate_brier_mean": mean_candidate,
        "incumbent_brier_mean": mean_incumbent,
        "maximum_relative_degradation": maximum_degradation,
        **reliability,
    }
    diagnostics: dict[str, MetricValue] = {}
    accuracies = [row.accuracy for row in rows if row.accuracy is not None]
    macro_f1 = [row.macro_f1 for row in rows if row.macro_f1 is not None]
    if len(accuracies) == 4:
        diagnostics["accuracy_mean"] = float(np.mean(accuracies))
    if len(macro_f1) == 4:
        diagnostics["macro_f1_mean"] = float(np.mean(macro_f1))
    return _decision(
        gate_id=f"direction_horizon_{horizon}",
        failures=failures,
        metrics=metrics,
        diagnostics=diagnostics,
    )


def evaluate_m7_direction_bundle(
    horizon_decisions: Mapping[int, M7GateDecision],
    *,
    recipe_ids: Mapping[int, str],
    package_ids: Mapping[int, str],
    bootstrap_delta_brier_upper_90: float,
    inference_sla_passed: bool,
    explanation_sla_passed: bool,
    economic_catastrophic_guardrail_passed: bool,
) -> M7GateDecision:
    if set(horizon_decisions) != {1, 5, 20}:
        raise ValueError("m7_direction_bundle_horizons_invalid")
    if set(recipe_ids) != {1, 5, 20} or set(package_ids) != {1, 5, 20}:
        raise ValueError("m7_direction_bundle_identity_invalid")
    if not math.isfinite(bootstrap_delta_brier_upper_90):
        raise ValueError("m7_direction_bootstrap_invalid")
    candidate_mean = float(
        np.mean(
            [
                float(horizon_decisions[horizon].metrics["candidate_brier_mean"])
                for horizon in (1, 5, 20)
            ]
        )
    )
    incumbent_mean = float(
        np.mean(
            [
                float(horizon_decisions[horizon].metrics["incumbent_brier_mean"])
                for horizon in (1, 5, 20)
            ]
        )
    )
    relative_improvement = (incumbent_mean - candidate_mean) / incumbent_mean
    single_recipe = len(set(recipe_ids.values())) == 1
    single_package = len(set(package_ids.values())) == 1
    failures = [
        f"horizon_{horizon}"
        for horizon in (1, 5, 20)
        if horizon_decisions[horizon].status is M7GateStatus.FAILED
    ]
    if relative_improvement < 0.005 - 1e-12:
        failures.append("macro_relative_brier_improvement")
    if bootstrap_delta_brier_upper_90 > 0:
        failures.append("bootstrap_delta_brier_upper_90")
    if not single_recipe:
        failures.append("single_recipe")
    if not single_package:
        failures.append("single_package")
    if not inference_sla_passed:
        failures.append("inference_sla")
    if not explanation_sla_passed:
        failures.append("explanation_sla")
    if not economic_catastrophic_guardrail_passed:
        failures.append("economic_catastrophic_guardrail")
    metrics: dict[str, MetricValue] = {
        "candidate_macro_brier": candidate_mean,
        "incumbent_macro_brier": incumbent_mean,
        "macro_relative_brier_improvement": relative_improvement,
        "bootstrap_delta_brier_upper_90": bootstrap_delta_brier_upper_90,
        "single_recipe": single_recipe,
        "single_package": single_package,
        "inference_sla": inference_sla_passed,
        "explanation_sla": explanation_sla_passed,
        "shadow_freeze_eligible": not failures,
    }
    return _decision(
        gate_id="direction_bundle",
        failures=failures,
        metrics=metrics,
        diagnostics={"economic_guardrail_only": True},
    )


def evaluate_m7_range_exit_20(
    folds: Sequence[M7RangeFoldMetrics],
    *,
    c_index_delta_lower_90: float,
    monotonicity_violations: int,
    economic_catastrophic_guardrail_passed: bool,
) -> M7GateDecision:
    rows = _exact_four_folds(folds)
    if not math.isfinite(c_index_delta_lower_90):
        raise ValueError("m7_range_bootstrap_invalid")
    if monotonicity_violations < 0:
        raise ValueError("m7_range_monotonicity_count_invalid")
    candidate = np.asarray([row.candidate_time_brier for row in rows], dtype=np.float64)
    null = np.asarray([row.null_time_brier for row in rows], dtype=np.float64)
    incumbent = np.asarray([row.incumbent_time_brier for row in rows], dtype=np.float64)
    candidate_c_index = np.asarray(
        [row.candidate_c_index for row in rows], dtype=np.float64
    )
    incumbent_c_index = np.asarray(
        [row.incumbent_c_index for row in rows], dtype=np.float64
    )
    null_wins = int(np.sum(candidate < null))
    incumbent_wins = int(np.sum(candidate < incumbent))
    calibration_passes = sum(row.cumulative_calibration_error <= 0.05 for row in rows)
    mean_candidate = float(np.mean(candidate))
    mean_incumbent = float(np.mean(incumbent))
    mean_c_index_delta = float(np.mean(candidate_c_index - incumbent_c_index))
    reliability = {
        "label_censor_health": all(row.label_censor_health_passed for row in rows),
        "coverage": all(row.coverage_passed for row in rows),
        "stability": all(row.stability_passed for row in rows),
        "determinism": all(row.determinism_passed for row in rows),
    }
    failures: list[str] = []
    if null_wins < 3:
        failures.append("null_fold_wins")
    if incumbent_wins < 3:
        failures.append("incumbent_fold_wins")
    if calibration_passes < 3:
        failures.append("calibration_fold_passes")
    if mean_candidate > mean_incumbent + 1e-15:
        failures.append("mean_time_brier")
    if mean_c_index_delta < -0.01 - 1e-12:
        failures.append("mean_c_index_degradation")
    if c_index_delta_lower_90 < -0.01 - 1e-12:
        failures.append("bootstrap_c_index_degradation")
    if monotonicity_violations != 0:
        failures.append("monotonicity")
    failures.extend(name for name, passed in reliability.items() if not passed)
    if not economic_catastrophic_guardrail_passed:
        failures.append("economic_catastrophic_guardrail")
    metrics: dict[str, MetricValue] = {
        "horizon": 20,
        "fold_count": 4,
        "null_fold_wins": null_wins,
        "incumbent_fold_wins": incumbent_wins,
        "calibration_fold_passes": calibration_passes,
        "candidate_time_brier_mean": mean_candidate,
        "incumbent_time_brier_mean": mean_incumbent,
        "mean_c_index_delta": mean_c_index_delta,
        "c_index_delta_lower_90": c_index_delta_lower_90,
        "monotonicity_violations": monotonicity_violations,
        **reliability,
    }
    diagnostics: dict[str, MetricValue] = {
        "candidate_c_index_mean": float(np.mean(candidate_c_index)),
        "incumbent_c_index_mean": float(np.mean(incumbent_c_index)),
        "economic_guardrail_only": True,
    }
    return _decision(
        gate_id="range_exit_20",
        failures=failures,
        metrics=metrics,
        diagnostics=diagnostics,
    )


def _exact_four_folds(rows: Sequence[FoldMetric]) -> tuple[FoldMetric, ...]:
    checked = tuple(rows)
    fold_ids = tuple(str(getattr(row, "fold_id")) for row in checked)
    if len(checked) != 4 or len(set(fold_ids)) != 4:
        raise ValueError("m7_gate_requires_exactly_four_unique_folds")
    return checked


def _decision(
    *,
    gate_id: str,
    failures: Sequence[str],
    metrics: dict[str, MetricValue],
    diagnostics: dict[str, MetricValue],
) -> M7GateDecision:
    unique_failures = tuple(dict.fromkeys(failures))
    return M7GateDecision(
        gate_id=gate_id,
        status=M7GateStatus.FAILED if unique_failures else M7GateStatus.PASSED,
        failed_gates=unique_failures,
        metrics=metrics,
        diagnostics=diagnostics,
        reason=(";".join(unique_failures) if unique_failures else None),
    )


__all__ = [
    "M7DirectionFoldMetrics",
    "M7GateDecision",
    "M7GateStatus",
    "M7RangeFoldMetrics",
    "evaluate_m7_direction_bundle",
    "evaluate_m7_direction_horizon",
    "evaluate_m7_range_exit_20",
]
