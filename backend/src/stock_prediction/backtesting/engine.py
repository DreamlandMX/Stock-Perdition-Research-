"""Point-in-time, next-open, fixed-horizon long/cash backtest engine."""

from __future__ import annotations

import math
import hashlib
import json
import calendar as month_calendar
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Literal, Self

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import to_jsonable_python

from stock_prediction.backtesting.costs import (
    CostScenario,
    estimate_round_trip_cost,
    estimate_side_cost,
)
from stock_prediction.contracts.market_data import Identifier, UsSymbol, VersionId
from stock_prediction.contracts.research import Horizon, Sha256
from stock_prediction.contracts.time import UtcDatetime


class BacktestConfigError(ValueError):
    """Strategy, market data, or accounting evidence is invalid."""


class WeeklySignal(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    symbol: UsSymbol
    anchor_at: UtcDatetime
    score: float
    executable_up_probability: float = Field(ge=0, le=1)
    abstain: bool
    data_quality_pass: bool
    liquidity_eligible: bool
    score_definition: Identifier
    signal_version: VersionId
    model_evidence_hash: Sha256
    calibration_evidence_hash: Sha256
    dataset_input_hash: Sha256


class ExecutableBar(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    symbol: UsSymbol
    session_date: date
    session_open_at: UtcDatetime
    session_close_at: UtcDatetime
    open_price: float = Field(gt=0)
    close_price: float = Field(gt=0)
    average_daily_dollar_volume: float = Field(gt=0)
    cash_dividend: float = Field(ge=0)
    verified: bool
    adjustment_revision: VersionId
    adjustment_input_hash: Sha256
    point_in_time_eligible: bool
    adjustment_mode: Literal["point_in_time_split_adjusted"]
    cumulative_split_factor: float = Field(gt=0)
    dividend_basis: Literal["split_adjusted_per_share"]

    @model_validator(mode="after")
    def validate_session(self) -> Self:
        if self.session_open_at >= self.session_close_at:
            raise ValueError("session open must precede session close")
        if self.session_open_at.date() != self.session_date:
            raise ValueError("session open date does not match session_date")
        return self


class UniverseMembership(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    symbol: UsSymbol
    universe_version: VersionId
    effective_from: UtcDatetime
    effective_to: UtcDatetime | None
    observed_at: UtcDatetime
    point_in_time_eligible: bool
    asset_type: Literal["stock", "etf"]
    industry: Identifier
    eligible: bool

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("universe membership interval is empty")
        if self.observed_at > self.effective_from:
            raise ValueError("universe membership was observed after effective start")
        return self


class VerifiedMarketSession(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    session_date: date
    session_open_at: UtcDatetime
    session_close_at: UtcDatetime
    half_day: bool

    @model_validator(mode="after")
    def validate_session(self) -> Self:
        if self.session_open_at >= self.session_close_at:
            raise ValueError("market session open must precede close")
        if self.session_open_at.date() != self.session_date:
            raise ValueError("market session date is inconsistent")
        return self


class VerifiedMarketCalendar(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", revalidate_instances="always"
    )

    calendar_version: VersionId
    coverage_start: date
    coverage_end: date
    complete_through: date
    sessions: tuple[VerifiedMarketSession, ...] = Field(min_length=1)
    calendar_hash: Sha256

    @model_validator(mode="after")
    def validate_calendar(self) -> Self:
        if len({item.session_date for item in self.sessions}) != len(self.sessions):
            raise ValueError("market calendar sessions must be unique")
        ordered = tuple(sorted(self.sessions, key=lambda item: item.session_date))
        if ordered != self.sessions:
            raise ValueError("market calendar sessions must be ordered")
        if self.coverage_start != ordered[0].session_date:
            raise ValueError("calendar coverage start is inconsistent")
        if self.coverage_end != ordered[-1].session_date:
            raise ValueError("calendar coverage end is inconsistent")
        if self.complete_through < self.coverage_end:
            raise ValueError("calendar completeness precedes coverage end")
        if self.calendar_hash != _content_hash(
            self.model_dump(mode="python", exclude={"calendar_hash"})
        ):
            raise ValueError("market calendar hash is invalid")
        return self

    @classmethod
    def create(
        cls,
        *,
        calendar_version: str,
        sessions: Sequence[VerifiedMarketSession],
        complete_through: date,
    ) -> "VerifiedMarketCalendar":
        ordered = tuple(sorted(sessions, key=lambda item: item.session_date))
        payload: dict[str, object] = {
            "calendar_version": calendar_version,
            "coverage_start": ordered[0].session_date,
            "coverage_end": ordered[-1].session_date,
            "complete_through": complete_through,
            "sessions": ordered,
        }
        payload["calendar_hash"] = _content_hash(payload)
        return cls.model_validate(payload, strict=True)


class StrategySpec(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    horizon: Horizon
    top_k: int = Field(ge=1)
    initial_capital: float = Field(gt=0)
    single_stock_cap: float = Field(gt=0, le=1)
    industry_stock_cap: float = Field(gt=0, le=1)
    single_etf_cap: float = Field(gt=0, le=1)
    total_etf_cap: float = Field(gt=0, le=1)
    broad_etf_allowlist: tuple[UsSymbol, ...]
    cash_annual_yield: float = Field(ge=0)
    one_vintage: bool
    rebalance_rule: Literal[
        "daily_close", "weekly_last_full_session", "month_end_full_session"
    ]
    entry_rule: Literal["next_verified_open"]
    exit_rule: Literal["fixed_horizon_close"]
    cost_scenario: CostScenario
    strategy_version: VersionId
    universe_version: VersionId
    adjustment_revision: VersionId
    adjustment_input_hash: Sha256
    adjustment_mode: Literal["point_in_time_split_adjusted"]
    calendar_version: VersionId
    calendar_hash: Sha256
    post_entry_missing_exit_rule: Literal[
        "next_verified_close_then_total_loss"
    ]
    maximum_exit_delay_sessions: int = Field(ge=0, le=20)
    minimum_executable_up_probability: float = Field(ge=0, le=1)
    score_definition: Identifier
    required_signal_version: VersionId
    model_evidence_hash: Sha256
    calibration_evidence_hash: Sha256
    dataset_input_hash: Sha256
    economic_evidence_scope: Literal["promotion_eligible", "research_only"] = (
        "promotion_eligible"
    )

    @model_validator(mode="after")
    def validate_strategy(self) -> Self:
        if not self.one_vintage:
            raise ValueError("M4 backtest requires one vintage at a time")
        if self.industry_stock_cap < self.single_stock_cap:
            raise ValueError("industry cap cannot be below single-stock cap")
        if self.total_etf_cap < self.single_etf_cap:
            raise ValueError("total ETF cap cannot be below single ETF cap")
        if len(set(self.broad_etf_allowlist)) != len(self.broad_etf_allowlist):
            raise ValueError("ETF allowlist must be unique")
        expected_rules = {
            1: "daily_close",
            5: "weekly_last_full_session",
            20: "month_end_full_session",
        }
        if self.rebalance_rule != expected_rules[self.horizon]:
            raise ValueError("rebalance rule does not match forecast horizon")
        return self


class BacktestTrade(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    symbol: UsSymbol
    signal_at: UtcDatetime
    entry_at: UtcDatetime
    exit_at: UtcDatetime
    entry_price: float = Field(gt=0)
    exit_price: float = Field(ge=0)
    cash_dividend: float = Field(ge=0)
    portfolio_weight: float = Field(gt=0, le=1)
    entry_notional: float = Field(gt=0)
    exit_sale_notional: float = Field(ge=0)
    entry_average_daily_dollar_volume: float = Field(gt=0)
    exit_average_daily_dollar_volume: float = Field(ge=0)
    gross_return: float
    net_return: float
    total_cost: float = Field(ge=0)
    capacity_exceeded: bool
    scheduled_exit_at: UtcDatetime
    exit_resolution: Literal["scheduled_close", "delayed_close", "total_loss"]


class CostSensitivity(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )
    per_side_bps: int
    estimated_total_cost: float = Field(ge=0)
    net_portfolio_return: float


class CapacitySensitivity(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )
    portfolio_scale: float = Field(gt=0)
    capacity_exceeded_count: int = Field(ge=0)


class BacktestVintage(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    anchor_at: UtcDatetime
    entry_at: UtcDatetime
    exit_at: UtcDatetime
    starting_equity: float = Field(gt=0)
    ending_equity: float = Field(gt=0)
    invested_weight: float = Field(ge=0, le=1)
    cash_weight: float = Field(ge=0, le=1)
    cash_return: float = Field(ge=0)
    gross_return: float
    net_return: float


class BacktestResult(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    trades: tuple[BacktestTrade, ...]
    vintages: tuple[BacktestVintage, ...]
    failed_fills: int = Field(ge=0)
    rejected_signals: int = Field(ge=0)
    skipped_overlapping_vintages: int = Field(ge=0)
    cash_weight: float = Field(ge=0, le=1)
    turnover: float = Field(ge=0)
    gross_portfolio_return: float
    net_portfolio_return: float
    capacity_exceeded_count: int = Field(ge=0)
    cost_sensitivity: tuple[CostSensitivity, ...]
    capacity_sensitivity: tuple[CapacitySensitivity, ...]
    strategy_version: VersionId
    universe_version: VersionId
    adjustment_revision: VersionId
    adjustment_input_hash: Sha256
    adjustment_mode: Literal["point_in_time_split_adjusted"]
    calendar_version: VersionId
    calendar_hash: Sha256
    signal_version: VersionId
    model_evidence_hash: Sha256
    calibration_evidence_hash: Sha256
    dataset_input_hash: Sha256
    economic_evidence_scope: Literal["promotion_eligible", "research_only"]
    research_only_reasons: tuple[Identifier, ...]


class LongCashBacktester:
    def run(
        self,
        predictions: Sequence[WeeklySignal],
        executable_bars: Sequence[ExecutableBar],
        universe_history: Sequence[UniverseMembership],
        strategy: StrategySpec,
        *,
        market_calendar: VerifiedMarketCalendar,
    ) -> BacktestResult:
        try:
            checked_strategy = StrategySpec.model_validate(
                strategy.model_dump(mode="python"), strict=True
            )
            signals = tuple(
                WeeklySignal.model_validate(item.model_dump(mode="python"), strict=True)
                for item in predictions
            )
            bars = tuple(
                ExecutableBar.model_validate(item.model_dump(mode="python"), strict=True)
                for item in executable_bars
            )
            memberships = tuple(
                UniverseMembership.model_validate(
                    item.model_dump(mode="python"), strict=True
                )
                for item in universe_history
            )
            calendar_artifact = VerifiedMarketCalendar.model_validate(
                market_calendar.model_dump(mode="python"), strict=True
            )
        except (AttributeError, ValidationError) as error:
            raise BacktestConfigError(
                f"backtest input contract is invalid: {error}"
            ) from error
        if (
            calendar_artifact.calendar_version
            != checked_strategy.calendar_version
            or calendar_artifact.calendar_hash != checked_strategy.calendar_hash
        ):
            raise BacktestConfigError(
                "market calendar does not match frozen strategy"
            )
        sessions = calendar_artifact.sessions
        universe_versions = {item.universe_version for item in memberships}
        adjustment_revisions = {item.adjustment_revision for item in bars}
        adjustment_hashes = {item.adjustment_input_hash for item in bars}
        if memberships and (
            len(universe_versions) != 1
            or any(not item.point_in_time_eligible for item in memberships)
        ):
            raise BacktestConfigError(
                "backtest requires one point-in-time universe version"
            )
        if memberships and universe_versions != {
            checked_strategy.universe_version
        }:
            raise BacktestConfigError(
                "universe version does not match frozen strategy"
            )
        if bars and (
            len(adjustment_revisions) != 1
            or len(adjustment_hashes) != 1
            or any(not item.point_in_time_eligible for item in bars)
            or any(
                item.adjustment_mode != checked_strategy.adjustment_mode
                for item in bars
            )
        ):
            raise BacktestConfigError(
                "backtest requires one point-in-time adjustment artifact"
            )
        if bars and (
            adjustment_revisions != {checked_strategy.adjustment_revision}
            or adjustment_hashes != {checked_strategy.adjustment_input_hash}
        ):
            raise BacktestConfigError(
                "adjustment artifact does not match frozen strategy"
            )

        bars_by_symbol: dict[str, list[ExecutableBar]] = defaultdict(list)
        bar_keys: set[tuple[str, date]] = set()
        for bar in bars:
            key = (bar.symbol, bar.session_date)
            if key in bar_keys:
                raise BacktestConfigError(
                    "executable bars must be unique by symbol and session"
                )
            bar_keys.add(key)
            if bar.verified:
                bars_by_symbol[bar.symbol].append(bar)
        for symbol_bars in bars_by_symbol.values():
            symbol_bars.sort(key=lambda item: item.session_open_at)

        membership_by_symbol: dict[str, list[UniverseMembership]] = defaultdict(list)
        membership_keys: set[tuple[str, UtcDatetime]] = set()
        for membership_record in memberships:
            key = (
                membership_record.symbol,
                membership_record.effective_from,
            )
            if key in membership_keys:
                raise BacktestConfigError(
                    "universe history must be unique by symbol and effective date"
                )
            membership_keys.add(key)
            membership_by_symbol[membership_record.symbol].append(
                membership_record
            )
        for history in membership_by_symbol.values():
            history.sort(key=lambda item: item.effective_from)

        for signal in signals:
            if (
                signal.score_definition != checked_strategy.score_definition
                or signal.signal_version
                != checked_strategy.required_signal_version
                or signal.model_evidence_hash
                != checked_strategy.model_evidence_hash
                or signal.calibration_evidence_hash
                != checked_strategy.calibration_evidence_hash
                or signal.dataset_input_hash
                != checked_strategy.dataset_input_hash
            ):
                raise BacktestConfigError(
                    "signal evidence does not match frozen strategy"
                )
        research_only_reasons: set[str] = set()
        if checked_strategy.economic_evidence_scope == "research_only":
            research_only_reasons.add("strategy_declared_research_only")
        if any(
            _membership_at(
                membership_by_symbol.get(signal.symbol, ()),
                signal.anchor_at,
            )
            is None
            for signal in signals
        ):
            research_only_reasons.add("missing_point_in_time_universe")
        economic_evidence_scope: Literal["promotion_eligible", "research_only"] = (
            "research_only" if research_only_reasons else "promotion_eligible"
        )

        grouped: dict[UtcDatetime, list[WeeklySignal]] = defaultdict(list)
        for signal in signals:
            grouped[signal.anchor_at].append(signal)

        trades: list[BacktestTrade] = []
        vintages: list[BacktestVintage] = []
        failed_fills = 0
        rejected_signals = 0
        skipped_vintages = 0
        active_until = None
        vintage_allocations: list[float] = []
        net_equity = checked_strategy.initial_capital
        gross_equity = checked_strategy.initial_capital
        last_exit_at = None
        for anchor_at in sorted(grouped):
            if active_until is not None and anchor_at < active_until:
                skipped_vintages += 1
                continue
            if not _is_rebalance_anchor(
                calendar_artifact,
                anchor_at,
                checked_strategy.rebalance_rule,
            ):
                raise BacktestConfigError(
                    "signal anchor does not match frozen rebalance rule"
                )
            schedule = _calendar_window(sessions, anchor_at, checked_strategy.horizon)
            if schedule is None:
                failed_fills += len(grouped[anchor_at])
                continue
            entry_session, exit_session, held_sessions = schedule
            if last_exit_at is not None and entry_session.session_open_at > last_exit_at:
                idle_days = (entry_session.session_open_at - last_exit_at).total_seconds() / 86400
                idle_return = math.pow(
                    1.0 + checked_strategy.cash_annual_yield,
                    idle_days / 365.0,
                ) - 1.0
                net_equity *= 1.0 + idle_return
                gross_equity *= 1.0 + idle_return
            best_by_symbol: dict[str, WeeklySignal] = {}
            for signal in grouped[anchor_at]:
                if (
                    signal.abstain
                    or not signal.data_quality_pass
                    or not signal.liquidity_eligible
                    or signal.executable_up_probability
                    < checked_strategy.minimum_executable_up_probability
                ):
                    rejected_signals += 1
                    continue
                current = best_by_symbol.get(signal.symbol)
                if current is None or signal.score > current.score:
                    best_by_symbol[signal.symbol] = signal
            candidates = sorted(
                best_by_symbol.values(),
                key=lambda item: (-item.score, item.symbol),
            )
            industry_weights: dict[str, float] = defaultdict(float)
            vintage_trades: list[BacktestTrade] = []
            vintage_starting_equity = net_equity
            etf_weight = 0.0
            target_equal_weight = 1.0 / checked_strategy.top_k
            for signal in candidates:
                if len(vintage_trades) >= checked_strategy.top_k:
                    break
                membership = _membership_at(
                    membership_by_symbol.get(signal.symbol, ()),
                    signal.anchor_at,
                )
                if membership is None or not membership.eligible:
                    failed_fills += 1
                    continue
                if (
                    membership.asset_type == "etf"
                    and signal.symbol not in checked_strategy.broad_etf_allowlist
                ):
                    failed_fills += 1
                    continue
                weight = (
                    min(
                        target_equal_weight,
                        checked_strategy.single_stock_cap,
                    )
                    if membership.asset_type == "stock"
                    else min(
                        target_equal_weight,
                        checked_strategy.single_etf_cap,
                    )
                )
                if membership.asset_type == "stock":
                    remaining = (
                        checked_strategy.industry_stock_cap
                        - industry_weights[membership.industry]
                    )
                    if weight > remaining + 1e-12:
                        continue
                else:
                    if weight > checked_strategy.total_etf_cap - etf_weight + 1e-12:
                        continue
                allocated = sum(trade.portfolio_weight for trade in vintage_trades)
                weight = min(weight, max(0.0, 1.0 - allocated))
                if weight <= 0:
                    continue
                execution = _execution_window(
                    bars_by_symbol.get(signal.symbol, ()),
                    held_sessions,
                    market_sessions=sessions,
                    maximum_exit_delay_sessions=(
                        checked_strategy.maximum_exit_delay_sessions
                    ),
                )
                if execution is None:
                    failed_fills += 1
                    continue
                (
                    entry_bar,
                    exit_bar,
                    dividend,
                    exit_resolution,
                    scheduled_exit_at,
                    resolution_at,
                ) = execution
                entry_notional = vintage_starting_equity * weight
                shares = entry_notional / entry_bar.open_price
                if exit_bar is None:
                    exit_price = 0.0
                    exit_sale_notional = 0.0
                    entry_cost = estimate_side_cost(
                        notional=entry_notional,
                        average_daily_dollar_volume=(
                            entry_bar.average_daily_dollar_volume
                        ),
                        scenario=checked_strategy.cost_scenario,
                        side="entry",
                    )
                    total_cost = entry_cost.cost
                    capacity_exceeded = entry_cost.capacity_exceeded
                    exit_at = resolution_at
                else:
                    exit_price = exit_bar.close_price
                    exit_sale_notional = shares * exit_price
                    round_trip = estimate_round_trip_cost(
                        entry_notional=entry_notional,
                        exit_notional=exit_sale_notional,
                        entry_average_daily_dollar_volume=(
                            entry_bar.average_daily_dollar_volume
                        ),
                        exit_average_daily_dollar_volume=(
                            exit_bar.average_daily_dollar_volume
                        ),
                        scenario=checked_strategy.cost_scenario,
                    )
                    total_cost = round_trip.total_cost
                    capacity_exceeded = round_trip.capacity_exceeded
                    exit_at = exit_bar.session_close_at
                gross_return = (exit_price + dividend) / entry_bar.open_price - 1
                net_return = gross_return - total_cost / entry_notional
                vintage_trades.append(
                    BacktestTrade(
                        symbol=signal.symbol,
                        signal_at=signal.anchor_at,
                        entry_at=entry_bar.session_open_at,
                        exit_at=exit_at,
                        entry_price=entry_bar.open_price,
                        exit_price=exit_price,
                        cash_dividend=dividend,
                        portfolio_weight=weight,
                        entry_notional=entry_notional,
                        exit_sale_notional=exit_sale_notional,
                        entry_average_daily_dollar_volume=(
                            entry_bar.average_daily_dollar_volume
                        ),
                        exit_average_daily_dollar_volume=(
                            exit_bar.average_daily_dollar_volume
                            if exit_bar is not None
                            else 0.0
                        ),
                        gross_return=gross_return,
                        net_return=net_return,
                        total_cost=total_cost,
                        capacity_exceeded=capacity_exceeded,
                        scheduled_exit_at=scheduled_exit_at,
                        exit_resolution=exit_resolution,
                    )
                )
                if membership.asset_type == "stock":
                    industry_weights[membership.industry] += weight
                else:
                    etf_weight += weight
            if vintage_trades:
                allocation = sum(trade.portfolio_weight for trade in vintage_trades)
                vintage_allocations.append(allocation)
                entry_at = min(trade.entry_at for trade in vintage_trades)
                active_until = max(trade.exit_at for trade in vintage_trades)
                last_exit_at = active_until
                holding_days = max(
                    0.0,
                    (active_until - entry_at).total_seconds() / 86400.0,
                )
                cash_return = (
                    math.pow(
                        1.0 + checked_strategy.cash_annual_yield,
                        holding_days / 365.0,
                    )
                    - 1.0
                )
                cash_weight = max(0.0, 1.0 - allocation)
                vintage_gross = sum(
                    trade.portfolio_weight * trade.gross_return
                    for trade in vintage_trades
                ) + cash_weight * cash_return
                vintage_net = sum(
                    trade.portfolio_weight * trade.net_return
                    for trade in vintage_trades
                ) + cash_weight * cash_return
                gross_equity *= 1.0 + vintage_gross
                net_equity *= 1.0 + vintage_net
                vintages.append(
                    BacktestVintage(
                        anchor_at=anchor_at,
                        entry_at=entry_at,
                        exit_at=active_until,
                        starting_equity=vintage_starting_equity,
                        ending_equity=net_equity,
                        invested_weight=allocation,
                        cash_weight=cash_weight,
                        cash_return=cash_return,
                        gross_return=vintage_gross,
                        net_return=vintage_net,
                    )
                )
                trades.extend(vintage_trades)

        cash_weight = 1.0 - (vintage_allocations[-1] if vintage_allocations else 0.0)
        gross_return = gross_equity / checked_strategy.initial_capital - 1.0
        net_return = net_equity / checked_strategy.initial_capital - 1.0
        vintage_turnovers = []
        for vintage in vintages:
            vintage_trades = [
                trade for trade in trades if trade.signal_at == vintage.anchor_at
            ]
            vintage_turnovers.append(
                sum(
                    trade.entry_notional + trade.exit_sale_notional
                    for trade in vintage_trades
                )
                / (2.0 * vintage.starting_equity)
            )
        turnover = float(np.mean(vintage_turnovers)) if vintage_turnovers else 0.0
        sensitivity_items: list[CostSensitivity] = []
        for bps in (5, 10, 20):
            replay_equity = checked_strategy.initial_capital
            total_cost = 0.0
            for vintage in vintages:
                vintage_trades = [
                    trade
                    for trade in trades
                    if trade.signal_at == vintage.anchor_at
                ]
                cost_return = sum(
                    (trade.entry_notional + trade.exit_sale_notional)
                    * bps
                    / 10_000
                    / vintage.starting_equity
                    for trade in vintage_trades
                )
                total_cost += cost_return * replay_equity
                replay_equity *= 1.0 + vintage.gross_return - cost_return
            sensitivity_items.append(
                CostSensitivity(
                    per_side_bps=bps,
                    estimated_total_cost=total_cost,
                    net_portfolio_return=(
                        replay_equity / checked_strategy.initial_capital - 1.0
                    ),
                )
            )
        sensitivity = tuple(sensitivity_items)
        capacity_sensitivity = tuple(
            CapacitySensitivity(
                portfolio_scale=scale,
                capacity_exceeded_count=sum(
                    max(
                        trade.entry_notional
                        / trade.entry_average_daily_dollar_volume,
                        (
                            trade.exit_sale_notional
                            / trade.exit_average_daily_dollar_volume
                            if trade.exit_average_daily_dollar_volume > 0
                            else 0.0
                        ),
                    )
                    * scale
                    > checked_strategy.cost_scenario.max_adv_participation
                    for trade in trades
                ),
            )
            for scale in (0.5, 1.0, 2.0, 5.0)
        )
        return BacktestResult(
            trades=tuple(trades),
            vintages=tuple(vintages),
            failed_fills=failed_fills,
            rejected_signals=rejected_signals,
            skipped_overlapping_vintages=skipped_vintages,
            cash_weight=max(0.0, min(1.0, cash_weight)),
            turnover=turnover,
            gross_portfolio_return=gross_return,
            net_portfolio_return=net_return,
            capacity_exceeded_count=sum(trade.capacity_exceeded for trade in trades),
            cost_sensitivity=sensitivity,
            capacity_sensitivity=capacity_sensitivity,
            strategy_version=checked_strategy.strategy_version,
            universe_version=checked_strategy.universe_version,
            adjustment_revision=checked_strategy.adjustment_revision,
            adjustment_input_hash=checked_strategy.adjustment_input_hash,
            adjustment_mode=checked_strategy.adjustment_mode,
            calendar_version=checked_strategy.calendar_version,
            calendar_hash=checked_strategy.calendar_hash,
            signal_version=checked_strategy.required_signal_version,
            model_evidence_hash=checked_strategy.model_evidence_hash,
            calibration_evidence_hash=(
                checked_strategy.calibration_evidence_hash
            ),
            dataset_input_hash=checked_strategy.dataset_input_hash,
            economic_evidence_scope=economic_evidence_scope,
            research_only_reasons=tuple(sorted(research_only_reasons)),
        )


def _membership_at(
    history: Sequence[UniverseMembership], as_of: UtcDatetime
) -> UniverseMembership | None:
    eligible = [
        item
        for item in history
        if item.effective_from <= as_of
        and (item.effective_to is None or as_of < item.effective_to)
        and item.observed_at <= as_of
    ]
    return eligible[-1] if eligible else None


def _calendar_window(
    sessions: Sequence[VerifiedMarketSession],
    anchor_at: UtcDatetime,
    horizon: int,
) -> tuple[
    VerifiedMarketSession,
    VerifiedMarketSession,
    tuple[VerifiedMarketSession, ...],
] | None:
    future = [
        item
        for item in sessions
        if item.session_open_at > anchor_at and not item.half_day
    ]
    if len(future) < horizon:
        return None
    selected = future[:horizon]
    return selected[0], selected[-1], tuple(selected)


def _is_rebalance_anchor(
    calendar_artifact: VerifiedMarketCalendar,
    anchor_at: UtcDatetime,
    rule: str,
) -> bool:
    full_sessions = tuple(
        item for item in calendar_artifact.sessions if not item.half_day
    )
    matching = [item for item in full_sessions if item.session_close_at == anchor_at]
    if not matching:
        return False
    session = matching[0]
    if rule == "daily_close":
        return calendar_artifact.complete_through >= session.session_date
    if rule == "weekly_last_full_session":
        same_week = [
            item
            for item in full_sessions
            if item.session_date.isocalendar()[:2]
            == session.session_date.isocalendar()[:2]
        ]
        week_complete = session.session_date + timedelta(
            days=6 - session.session_date.weekday()
        )
        return (
            calendar_artifact.complete_through >= week_complete
            and session.session_date == max(item.session_date for item in same_week)
        )
    if rule == "month_end_full_session":
        same_month = [
            item
            for item in full_sessions
            if (item.session_date.year, item.session_date.month)
            == (session.session_date.year, session.session_date.month)
        ]
        month_end = date(
            session.session_date.year,
            session.session_date.month,
            month_calendar.monthrange(
                session.session_date.year, session.session_date.month
            )[1],
        )
        return (
            calendar_artifact.complete_through >= month_end
            and session.session_date == max(item.session_date for item in same_month)
        )
    return False


def _content_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            to_jsonable_python(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _execution_window(
    bars: Sequence[ExecutableBar],
    sessions: Sequence[VerifiedMarketSession],
    *,
    market_sessions: Sequence[VerifiedMarketSession],
    maximum_exit_delay_sessions: int,
) -> tuple[
    ExecutableBar,
    ExecutableBar | None,
    float,
    Literal["scheduled_close", "delayed_close", "total_loss"],
    UtcDatetime,
    UtcDatetime,
] | None:
    by_date = {bar.session_date: bar for bar in bars}
    entry_session = sessions[0]
    exit_session = sessions[-1]
    entry = by_date.get(entry_session.session_date)
    if (
        entry is None
        or not entry.verified
        or not _bar_matches_session(entry, entry_session)
    ):
        return None
    exit_bar = by_date.get(exit_session.session_date)
    resolution: Literal["scheduled_close", "delayed_close", "total_loss"]
    if (
        exit_bar is not None
        and exit_bar.verified
        and _bar_matches_session(exit_bar, exit_session)
    ):
        resolution = "scheduled_close"
        resolution_at = exit_bar.session_close_at
    else:
        future_sessions = [
            item
            for item in market_sessions
            if item.session_open_at > exit_session.session_close_at
        ]
        if len(future_sessions) < maximum_exit_delay_sessions:
            raise BacktestConfigError(
                "market calendar lacks required exit delay coverage"
            )
        allowed_sessions = future_sessions[:maximum_exit_delay_sessions]
        exit_bar = next(
            (
                candidate
                for allowed_session in allowed_sessions
                if (candidate := by_date.get(allowed_session.session_date)) is not None
                and candidate.verified
                and _bar_matches_session(candidate, allowed_session)
            ),
            None,
        )
        resolution = "delayed_close" if exit_bar is not None else "total_loss"
        resolution_at = (
            exit_bar.session_close_at
            if exit_bar is not None
            else (
                allowed_sessions[-1].session_close_at
                if allowed_sessions
                else exit_session.session_close_at
            )
        )
    entitlement_end = (
        exit_bar.session_date if exit_bar is not None else exit_session.session_date
    )
    dividend = sum(
        bar.cash_dividend
        for bar in bars
        if entry.session_date < bar.session_date <= entitlement_end
    )
    return (
        entry,
        exit_bar,
        dividend,
        resolution,
        exit_session.session_close_at,
        resolution_at,
    )


def _bar_matches_session(
    bar: ExecutableBar, session: VerifiedMarketSession
) -> bool:
    return (
        bar.session_open_at == session.session_open_at
        and bar.session_close_at == session.session_close_at
    )
