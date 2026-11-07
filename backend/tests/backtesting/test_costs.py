from __future__ import annotations

import pytest

from stock_prediction.backtesting.costs import (
    CostInputError,
    CostScenario,
    estimate_round_trip_cost,
)


def test_main_cost_scenario_cannot_be_zero() -> None:
    with pytest.raises(ValueError, match="non-zero"):
        CostScenario(
            half_spread_bps=0.0,
            liquidity_bps=0.0,
            commission_bps=0.0,
            regulatory_fee_bps=0.0,
            max_adv_participation=0.10,
            scenario_version="zero-v1",
        )


def test_round_trip_cost_and_capacity_are_nonzero_and_symmetric() -> None:
    scenario = CostScenario(
        half_spread_bps=2.0,
        liquidity_bps=3.0,
        commission_bps=1.0,
        regulatory_fee_bps=0.5,
        max_adv_participation=0.10,
        scenario_version="base-v1",
    )
    result = estimate_round_trip_cost(
        entry_notional=100_000.0,
        exit_notional=110_000.0,
        entry_average_daily_dollar_volume=500_000.0,
        exit_average_daily_dollar_volume=1_000_000.0,
        scenario=scenario,
    )

    assert result.total_cost > 0
    assert result.participation_rate == pytest.approx(0.20)
    assert result.capacity_exceeded is True
    assert result.entry_cost > 0 and result.exit_cost > 0


def test_cost_model_rejects_non_finite_inputs() -> None:
    scenario = CostScenario(
        half_spread_bps=2.0,
        liquidity_bps=3.0,
        commission_bps=1.0,
        regulatory_fee_bps=0.5,
        max_adv_participation=0.10,
        scenario_version="base-v1",
    )
    with pytest.raises(CostInputError, match="finite"):
        estimate_round_trip_cost(
            entry_notional=float("nan"),
            exit_notional=1.0,
            entry_average_daily_dollar_volume=1.0,
            exit_average_daily_dollar_volume=1.0,
            scenario=scenario,
        )
