"""Multiple-testing and backtest-overfitting diagnostics."""

from __future__ import annotations

import itertools
import math
from statistics import NormalDist
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class DeflatedSharpeResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)
    observed_sharpe: float
    expected_maximum_sharpe: float
    status: Literal[
        "available",
        "insufficient_trial_variance",
        "insufficient_trial_coverage",
    ]
    probability_sharpe_is_genuine: float | None = Field(default=None, ge=0, le=1)
    attempted_trials: int = Field(ge=1)
    successful_trials_with_sharpe: int = Field(ge=0)
    attempted_sharpe_mean: float
    attempted_sharpe_std: float = Field(ge=0)
    observation_count: int = Field(ge=3)
    assumptions: tuple[str, ...]


class PboResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)
    status: Literal[
        "available", "insufficient_blocks", "combination_budget_exceeded"
    ]
    probability: float | None = Field(default=None, ge=0, le=1)
    block_count: int = Field(ge=1)
    combination_count: int = Field(ge=0)


def deflated_sharpe_ratio(
    returns: object,
    *,
    annualization: int,
    attempted_trials: int,
    attempted_trial_sharpes: object,
    minimum_successful_trial_fraction: float = 0.80,
) -> DeflatedSharpeResult:
    values = np.asarray(returns, dtype=np.float64)
    if values.ndim != 1 or len(values) < 3 or not np.all(np.isfinite(values)):
        raise ValueError("DSR returns must be a finite vector with three observations")
    if (
        annualization < 1
        or attempted_trials < 1
        or not 0 < minimum_successful_trial_fraction <= 1
    ):
        raise ValueError("DSR annualization and attempts must be positive")
    trial_sharpes = np.asarray(attempted_trial_sharpes, dtype=np.float64)
    if (
        trial_sharpes.ndim != 1
        or len(trial_sharpes) > attempted_trials
        or not np.all(np.isfinite(trial_sharpes))
    ):
        raise ValueError("DSR successful Sharpes cannot exceed total attempts")
    std = float(np.std(values, ddof=1))
    raw_sharpe = float(np.mean(values) / std) if std else 0.0
    centered = values - np.mean(values)
    population_std = float(np.std(values))
    if population_std > 0:
        skewness = float(np.mean(np.power(centered / population_std, 3)))
        kurtosis = float(np.mean(np.power(centered / population_std, 4)))
    else:
        skewness = 0.0
        kurtosis = 3.0
    successful_trials = len(trial_sharpes)
    trial_mean = float(np.mean(trial_sharpes)) if successful_trials else 0.0
    trial_std = (
        float(np.std(trial_sharpes, ddof=1)) if successful_trials > 1 else 0.0
    )
    if attempted_trials == 1:
        expected_raw = 0.0
    else:
        normal = NormalDist()
        euler_gamma = 0.5772156649015329
        first = normal.inv_cdf(1.0 - 1.0 / attempted_trials)
        second = normal.inv_cdf(
            1.0 - 1.0 / (attempted_trials * math.e)
        )
        expected_standard_max = (
            (1.0 - euler_gamma) * first + euler_gamma * second
        )
        expected_raw = trial_std * expected_standard_max
    variance_adjustment = max(
        1e-12,
        1.0
        - skewness * raw_sharpe
        + ((kurtosis - 1.0) / 4.0) * raw_sharpe * raw_sharpe,
    )
    z_score = (
        (raw_sharpe - expected_raw)
        * math.sqrt(len(values) - 1)
        / math.sqrt(variance_adjustment)
    )
    enough_coverage = (
        successful_trials / attempted_trials
        >= minimum_successful_trial_fraction
    )
    enough_trial_variance = trial_std > np.finfo(np.float64).eps
    probability = (
        NormalDist().cdf(z_score)
        if successful_trials >= 2
        and enough_coverage
        and enough_trial_variance
        else None
    )
    observed = raw_sharpe * math.sqrt(annualization)
    expected_max = expected_raw * math.sqrt(annualization)
    return DeflatedSharpeResult(
        observed_sharpe=observed,
        expected_maximum_sharpe=expected_max,
        status=(
            "available"
            if probability is not None
            else (
                "insufficient_trial_coverage"
                if not enough_coverage
                else "insufficient_trial_variance"
            )
        ),
        probability_sharpe_is_genuine=(
            min(1.0, max(0.0, probability))
            if probability is not None
            else None
        ),
        attempted_trials=attempted_trials,
        successful_trials_with_sharpe=successful_trials,
        attempted_sharpe_mean=trial_mean,
        attempted_sharpe_std=trial_std,
        observation_count=len(values),
        assumptions=(
            "returns are stationary within the evaluated window",
            "attempt count includes failed and unreported trials",
            "skewness and kurtosis adjust the Sharpe sampling variance",
            "expected maximum Sharpe uses the registered trial Sharpe distribution",
        ),
    )


def probability_of_backtest_overfitting(
    block_strategy_returns: object,
    *,
    minimum_blocks: int,
    max_combinations: int = 100_000,
) -> PboResult:
    matrix = np.asarray(block_strategy_returns, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] < 1 or matrix.shape[1] < 2:
        raise ValueError("PBO requires blocks by at least two strategies")
    if (
        not np.all(np.isfinite(matrix))
        or minimum_blocks < 2
        or max_combinations < 1
    ):
        raise ValueError("PBO inputs must be finite and minimum blocks valid")
    blocks = matrix.shape[0]
    if blocks < minimum_blocks or blocks % 2:
        return PboResult(
            status="insufficient_blocks",
            probability=None,
            block_count=blocks,
            combination_count=0,
        )
    half = blocks // 2
    planned_combinations = math.comb(blocks, half)
    if planned_combinations > max_combinations:
        return PboResult(
            status="combination_budget_exceeded",
            probability=None,
            block_count=blocks,
            combination_count=planned_combinations,
        )
    failures = 0
    combinations = 0
    all_indices = set(range(blocks))
    for in_sample in itertools.combinations(range(blocks), half):
        out_sample = tuple(sorted(all_indices - set(in_sample)))
        winner = int(np.argmax(np.mean(matrix[list(in_sample)], axis=0)))
        out_scores = np.mean(matrix[list(out_sample)], axis=0)
        competitors = np.delete(out_scores, winner)
        percentile = (
            np.sum(competitors < out_scores[winner])
            + 0.5 * np.sum(competitors == out_scores[winner])
        ) / len(competitors)
        failures += int(percentile < 0.5)
        combinations += 1
    return PboResult(
        status="available",
        probability=failures / combinations,
        block_count=blocks,
        combination_count=combinations,
    )
