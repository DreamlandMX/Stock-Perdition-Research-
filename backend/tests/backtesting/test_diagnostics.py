from __future__ import annotations

import numpy as np

from stock_prediction.backtesting.diagnostics import (
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)


def test_deflated_sharpe_records_attempt_count_and_assumptions() -> None:
    returns = np.array([0.01, -0.005, 0.012, 0.003, -0.002] * 20)
    result = deflated_sharpe_ratio(
        returns,
        annualization=252,
        attempted_trials=25,
        attempted_trial_sharpes=np.linspace(-0.05, 0.10, 25),
    )

    assert result.attempted_trials == 25
    assert result.probability_sharpe_is_genuine is not None
    assert 0 <= result.probability_sharpe_is_genuine <= 1
    assert result.observation_count == len(returns)
    assert result.attempted_sharpe_std > 0
    assert result.assumptions


def test_dsr_does_not_invent_failed_trial_sharpes() -> None:
    result = deflated_sharpe_ratio(
        np.array([0.01, -0.01, 0.02, 0.0]),
        annualization=252,
        attempted_trials=5,
        attempted_trial_sharpes=np.array([0.1]),
    )

    assert result.attempted_trials == 5
    assert result.successful_trials_with_sharpe == 1
    assert result.status == "insufficient_trial_coverage"
    assert result.probability_sharpe_is_genuine is None


def test_dsr_reports_insufficient_variance_for_identical_trial_sharpes() -> None:
    result = deflated_sharpe_ratio(
        np.array([0.01, -0.01, 0.02, 0.0] * 10),
        annualization=252,
        attempted_trials=4,
        attempted_trial_sharpes=np.array([0.1, 0.1, 0.1, 0.1]),
    )

    assert result.status == "insufficient_trial_variance"
    assert result.probability_sharpe_is_genuine is None


def test_pbo_reports_insufficient_blocks_without_fake_number() -> None:
    result = probability_of_backtest_overfitting(
        np.array([[0.01, 0.02], [0.00, -0.01]]),
        minimum_blocks=4,
    )

    assert result.status == "insufficient_blocks"
    assert result.probability is None


def test_pbo_is_bounded_when_combinatorial_evidence_is_sufficient() -> None:
    matrix = np.array(
        [
            [0.01, 0.02, -0.01],
            [0.00, 0.03, 0.01],
            [-0.01, 0.01, 0.02],
            [0.02, -0.01, 0.00],
        ]
    )
    result = probability_of_backtest_overfitting(matrix, minimum_blocks=4)

    assert result.status == "available"
    assert result.probability is not None
    assert 0 <= result.probability <= 1


def test_pbo_refuses_unbounded_combinatorial_work() -> None:
    result = probability_of_backtest_overfitting(
        np.ones((30, 2)), minimum_blocks=4, max_combinations=1000
    )

    assert result.status == "combination_budget_exceeded"
    assert result.probability is None
    assert result.combination_count > 1000


def test_pbo_ties_use_average_rank_not_false_failure() -> None:
    result = probability_of_backtest_overfitting(
        np.ones((4, 3)), minimum_blocks=4
    )

    assert result.status == "available"
    assert result.probability == 0.0
