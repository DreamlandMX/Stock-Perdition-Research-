"""Non-zero trading cost and capacity scenarios."""

from __future__ import annotations

import math
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_prediction.contracts.market_data import VersionId


class CostInputError(ValueError):
    """Cost inputs are invalid."""


class CostScenario(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)

    half_spread_bps: float = Field(ge=0)
    liquidity_bps: float = Field(ge=0)
    commission_bps: float = Field(ge=0)
    regulatory_fee_bps: float = Field(ge=0)
    max_adv_participation: float = Field(gt=0, le=1)
    scenario_version: VersionId

    @model_validator(mode="after")
    def require_nonzero_cost(self) -> Self:
        if sum(
            (
                self.half_spread_bps,
                self.liquidity_bps,
                self.commission_bps,
                self.regulatory_fee_bps,
            )
        ) <= 0:
            raise ValueError("main cost scenario must be non-zero")
        return self


class RoundTripCost(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)

    entry_cost: float = Field(ge=0)
    exit_cost: float = Field(ge=0)
    total_cost: float = Field(ge=0)
    participation_rate: float = Field(ge=0)
    peak_participation_rate: float = Field(ge=0)
    capacity_exceeded: bool


class SideCost(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)
    cost: float = Field(ge=0)
    participation_rate: float = Field(ge=0)
    capacity_exceeded: bool


def estimate_side_cost(
    *,
    notional: float,
    average_daily_dollar_volume: float,
    scenario: CostScenario,
    side: Literal["entry", "exit"],
) -> SideCost:
    if not math.isfinite(notional) or not math.isfinite(average_daily_dollar_volume):
        raise CostInputError("cost inputs must be finite")
    if notional <= 0 or average_daily_dollar_volume <= 0:
        raise CostInputError("cost notional and ADV must be positive")
    checked = CostScenario.model_validate(scenario.model_dump(mode="python"), strict=True)
    participation = notional / average_daily_dollar_volume
    bps = _side_bps(participation, checked, regulatory=side == "exit")
    return SideCost(
        cost=notional * bps / 10_000,
        participation_rate=participation,
        capacity_exceeded=participation > checked.max_adv_participation,
    )


def estimate_round_trip_cost(
    *,
    entry_notional: float,
    exit_notional: float,
    entry_average_daily_dollar_volume: float,
    exit_average_daily_dollar_volume: float,
    scenario: CostScenario,
) -> RoundTripCost:
    values = (
        entry_notional,
        exit_notional,
        entry_average_daily_dollar_volume,
        exit_average_daily_dollar_volume,
    )
    if any(not math.isfinite(value) for value in values):
        raise CostInputError("cost inputs must be finite")
    if (
        entry_notional <= 0
        or exit_notional <= 0
        or entry_average_daily_dollar_volume <= 0
        or exit_average_daily_dollar_volume <= 0
    ):
        raise CostInputError("cost notionals and ADV must be positive")
    checked = CostScenario.model_validate(scenario.model_dump(mode="python"), strict=True)
    entry_participation = entry_notional / entry_average_daily_dollar_volume
    exit_participation = exit_notional / exit_average_daily_dollar_volume
    entry = estimate_side_cost(
        notional=entry_notional,
        average_daily_dollar_volume=entry_average_daily_dollar_volume,
        scenario=checked,
        side="entry",
    )
    exit_side = estimate_side_cost(
        notional=exit_notional,
        average_daily_dollar_volume=exit_average_daily_dollar_volume,
        scenario=checked,
        side="exit",
    )
    entry_cost = entry.cost
    exit_cost = exit_side.cost
    peak = max(entry_participation, exit_participation)
    return RoundTripCost(
        entry_cost=entry_cost,
        exit_cost=exit_cost,
        total_cost=entry_cost + exit_cost,
        participation_rate=entry_participation,
        peak_participation_rate=peak,
        capacity_exceeded=peak > checked.max_adv_participation,
    )


def _side_bps(participation: float, scenario: CostScenario, *, regulatory: bool) -> float:
    liquidity_multiplier = math.sqrt(
        max(participation, 0.0) / scenario.max_adv_participation
    )
    return (
        scenario.half_spread_bps
        + scenario.liquidity_bps * liquidity_multiplier
        + scenario.commission_bps
        + (scenario.regulatory_fee_bps if regulatory else 0.0)
    )
