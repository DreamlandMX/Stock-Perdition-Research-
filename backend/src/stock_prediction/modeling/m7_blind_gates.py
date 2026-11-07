"""Frozen forward-blind gates for M7 capability bundles."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Literal, Self, TypeAlias

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_prediction.modeling.m7_metrics import (
    adaptive_classwise_ece,
    compare_direction_brier,
    date_block_bootstrap_delta_brier,
    range_exit_calibration_report,
)


M7BlindSampleKey: TypeAlias = tuple[str, date, int]
BlindMetric: TypeAlias = float | int | bool | str


class M7BlindDirectionEvidence(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    horizon: Literal[1, 5, 20]
    sample_keys: tuple[M7BlindSampleKey, ...] = Field(min_length=1)
    incumbent_sample_keys: tuple[M7BlindSampleKey, ...] | None = None
    labels: tuple[Literal["down", "flat", "up"], ...] = Field(min_length=1)
    candidate_probabilities: tuple[tuple[float, float, float], ...]
    incumbent_probabilities: tuple[tuple[float, float, float], ...]
    coverage_passed: bool
    artifact_integrity_passed: bool
    probability_validity_passed: bool
    inference_sla_passed: bool

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        incumbent_keys = self.incumbent_sample_keys or self.sample_keys
        if incumbent_keys != self.sample_keys:
            raise ValueError("m7_blind_direction_sample_keys_mismatch")
        row_count = len(self.sample_keys)
        if (
            len(set(self.sample_keys)) != row_count
            or len(self.labels) != row_count
            or len(self.candidate_probabilities) != row_count
            or len(self.incumbent_probabilities) != row_count
            or any(key[2] != self.horizon for key in self.sample_keys)
        ):
            raise ValueError("m7_blind_direction_evidence_misaligned")
        return self


class M7BlindRangeEvidence(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    sample_keys: tuple[M7BlindSampleKey, ...] = Field(min_length=1)
    incumbent_sample_keys: tuple[M7BlindSampleKey, ...] | None = None
    observed: tuple[Literal[0, 1], ...] = Field(min_length=1)
    candidate_probability_5d: tuple[float, ...]
    candidate_probability_20d: tuple[float, ...]
    incumbent_probability_20d: tuple[float, ...]
    five_day_anchor_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_c_index: float = Field(ge=0, le=1)
    incumbent_c_index: float = Field(ge=0, le=1)
    c_index_delta_lower_90: float
    coverage_passed: bool
    artifact_integrity_passed: bool
    inference_sla_passed: bool

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        incumbent_keys = self.incumbent_sample_keys or self.sample_keys
        if incumbent_keys != self.sample_keys:
            raise ValueError("m7_blind_range_sample_keys_mismatch")
        row_count = len(self.sample_keys)
        if (
            len(set(self.sample_keys)) != row_count
            or any(key[2] != 20 for key in self.sample_keys)
            or any(
                len(values) != row_count
                for values in (
                    self.observed,
                    self.candidate_probability_5d,
                    self.candidate_probability_20d,
                    self.incumbent_probability_20d,
                )
            )
        ):
            raise ValueError("m7_blind_range_evidence_misaligned")
        probabilities = (
            *self.candidate_probability_5d,
            *self.candidate_probability_20d,
            *self.incumbent_probability_20d,
        )
        if any(value < 0 or value > 1 for value in probabilities):
            raise ValueError("m7_blind_range_probability_invalid")
        return self


class M7BlindGateDecision(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    bundle_id: Literal["direction", "range_exit_20"]
    status: Literal["awaiting_manual_approval", "rejected"]
    failed_gates: tuple[str, ...]
    metrics: dict[str, BlindMetric]
    per_horizon: dict[str, dict[str, BlindMetric]]

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if len(set(self.failed_gates)) != len(self.failed_gates):
            raise ValueError("m7_blind_failed_gates_not_unique")
        if (self.status == "rejected") != bool(self.failed_gates):
            raise ValueError("m7_blind_decision_status_invalid")
        return self


def evaluate_m7_blind_direction(
    evidence: Mapping[int, M7BlindDirectionEvidence],
    *,
    n_bootstrap: int = 10_000,
) -> M7BlindGateDecision:
    if set(evidence) != {1, 5, 20} or any(
        evidence[horizon].horizon != horizon for horizon in evidence
    ):
        raise ValueError("m7_blind_direction_requires_three_horizons")
    failures: list[str] = []
    per_horizon: dict[str, dict[str, BlindMetric]] = {}
    candidate_brier: list[float] = []
    incumbent_brier: list[float] = []
    bootstrap_upper: list[float] = []
    for horizon in (1, 5, 20):
        row = evidence[horizon]
        classes = ("down", "flat", "up")
        comparison = compare_direction_brier(
            candidate_sample_keys=row.sample_keys,
            incumbent_sample_keys=row.incumbent_sample_keys or row.sample_keys,
            labels=row.labels,
            candidate_probabilities=row.candidate_probabilities,
            incumbent_probabilities=row.incumbent_probabilities,
            classes=classes,
        )
        candidate_ece = adaptive_classwise_ece(
            symbols=tuple(key[0] for key in row.sample_keys),
            labels=row.labels,
            probabilities=row.candidate_probabilities,
            classes=classes,
            minimum_bin_count=5,
        ).macro_ece
        incumbent_ece = adaptive_classwise_ece(
            symbols=tuple(key[0] for key in row.sample_keys),
            labels=row.labels,
            probabilities=row.incumbent_probabilities,
            classes=classes,
            minimum_bin_count=5,
        ).macro_ece
        bootstrap = date_block_bootstrap_delta_brier(
            sample_keys=row.sample_keys,
            labels=row.labels,
            candidate_probabilities=row.candidate_probabilities,
            incumbent_probabilities=row.incumbent_probabilities,
            classes=classes,
            n_draws=n_bootstrap,
        )
        candidate_brier.append(comparison.candidate.symbol_equal_brier)
        incumbent_brier.append(comparison.incumbent.symbol_equal_brier)
        bootstrap_upper.append(bootstrap.one_sided_upper_90)
        prefix = f"direction_{horizon}d"
        if (
            comparison.candidate.symbol_equal_brier
            > comparison.incumbent.symbol_equal_brier
        ):
            failures.append(f"{prefix}_brier")
        if candidate_ece > 0.05:
            failures.append(f"{prefix}_ece")
        if candidate_ece > incumbent_ece + 0.01:
            failures.append(f"{prefix}_ece_degradation")
        if bootstrap.one_sided_upper_90 > 0:
            failures.append(f"{prefix}_bootstrap")
        reliability = {
            "coverage": row.coverage_passed,
            "artifact_integrity": row.artifact_integrity_passed,
            "probability_validity": row.probability_validity_passed,
            "inference_sla": row.inference_sla_passed,
        }
        failures.extend(
            f"{prefix}_{name}" for name, passed in reliability.items() if not passed
        )
        per_horizon[str(horizon)] = {
            "candidate_brier": comparison.candidate.symbol_equal_brier,
            "incumbent_brier": comparison.incumbent.symbol_equal_brier,
            "candidate_ece": candidate_ece,
            "incumbent_ece": incumbent_ece,
            "bootstrap_delta_brier_upper_90": bootstrap.one_sided_upper_90,
            "sample_count": comparison.candidate.sample_count,
            **reliability,
        }
    candidate_macro = float(np.mean(candidate_brier))
    incumbent_macro = float(np.mean(incumbent_brier))
    relative_improvement = (incumbent_macro - candidate_macro) / incumbent_macro
    if candidate_macro >= incumbent_macro:
        failures.append("direction_macro_brier_improvement")
    return _blind_decision(
        bundle_id="direction",
        failures=failures,
        metrics={
            "candidate_macro_brier": candidate_macro,
            "incumbent_macro_brier": incumbent_macro,
            "macro_relative_brier_improvement": relative_improvement,
            "maximum_bootstrap_delta_brier_upper_90": max(bootstrap_upper),
            "evidence_scope": "blind_complete",
        },
        per_horizon=per_horizon,
    )


def evaluate_m7_blind_range(
    evidence: M7BlindRangeEvidence,
) -> M7BlindGateDecision:
    symbols = tuple(key[0] for key in evidence.sample_keys)
    candidate_brier = _symbol_equal_binary_brier(
        symbols, evidence.observed, evidence.candidate_probability_20d
    )
    incumbent_brier = _symbol_equal_binary_brier(
        symbols, evidence.observed, evidence.incumbent_probability_20d
    )
    candidate_ece = range_exit_calibration_report(
        symbols=symbols,
        observed=evidence.observed,
        cumulative_probability=evidence.candidate_probability_20d,
        minimum_bin_count=5,
    ).calibration_error
    incumbent_ece = range_exit_calibration_report(
        symbols=symbols,
        observed=evidence.observed,
        cumulative_probability=evidence.incumbent_probability_20d,
        minimum_bin_count=5,
    ).calibration_error
    monotonicity_violations = sum(
        short > long + 1e-12
        for short, long in zip(
            evidence.candidate_probability_5d,
            evidence.candidate_probability_20d,
            strict=True,
        )
    )
    failures: list[str] = []
    if candidate_brier > incumbent_brier:
        failures.append("range_brier")
    if candidate_ece >= incumbent_ece:
        failures.append("range_calibration_improvement")
    if monotonicity_violations:
        failures.append("range_monotonicity")
    if evidence.candidate_c_index < evidence.incumbent_c_index - 0.01:
        failures.append("range_c_index")
    if evidence.c_index_delta_lower_90 < -0.01:
        failures.append("range_c_index_bootstrap")
    reliability = {
        "coverage": evidence.coverage_passed,
        "artifact_integrity": evidence.artifact_integrity_passed,
        "inference_sla": evidence.inference_sla_passed,
    }
    failures.extend(name for name, passed in reliability.items() if not passed)
    metrics: dict[str, BlindMetric] = {
        "candidate_brier": candidate_brier,
        "incumbent_brier": incumbent_brier,
        "candidate_calibration_error": candidate_ece,
        "incumbent_calibration_error": incumbent_ece,
        "monotonicity_violations": monotonicity_violations,
        "candidate_c_index": evidence.candidate_c_index,
        "incumbent_c_index": evidence.incumbent_c_index,
        "c_index_delta_lower_90": evidence.c_index_delta_lower_90,
        "five_day_anchor_hash": evidence.five_day_anchor_hash,
        "sample_count": len(evidence.sample_keys),
        "evidence_scope": "blind_complete",
        **reliability,
    }
    return _blind_decision(
        bundle_id="range_exit_20",
        failures=failures,
        metrics=metrics,
        per_horizon={"20": metrics.copy()},
    )


def _symbol_equal_binary_brier(
    symbols: Sequence[str], observed: Sequence[int], probabilities: Sequence[float]
) -> float:
    losses = (np.asarray(probabilities) - np.asarray(observed)) ** 2
    symbol_order = np.asarray(symbols)
    return float(
        np.mean(
            [np.mean(losses[symbol_order == symbol]) for symbol in sorted(set(symbols))]
        )
    )


def _blind_decision(
    *,
    bundle_id: Literal["direction", "range_exit_20"],
    failures: Sequence[str],
    metrics: dict[str, BlindMetric],
    per_horizon: dict[str, dict[str, BlindMetric]],
) -> M7BlindGateDecision:
    unique = tuple(dict.fromkeys(failures))
    return M7BlindGateDecision(
        bundle_id=bundle_id,
        status="rejected" if unique else "awaiting_manual_approval",
        failed_gates=unique,
        metrics=metrics,
        per_horizon=per_horizon,
    )


__all__ = [
    "M7BlindDirectionEvidence",
    "M7BlindGateDecision",
    "M7BlindRangeEvidence",
    "evaluate_m7_blind_direction",
    "evaluate_m7_blind_range",
]
