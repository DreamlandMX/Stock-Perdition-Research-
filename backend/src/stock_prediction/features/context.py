"""Point-in-time universe breadth and aligned relative-strength context."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from stock_prediction.contracts.market_data import Identifier, UsSymbol, VersionId
from stock_prediction.contracts.time import UtcDatetime


class ContextInputError(ValueError):
    """Market context inputs are ambiguous or point-in-time incompatible."""


class UniverseMembership(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    symbol: UsSymbol
    asset_group: Identifier
    universe_version: VersionId
    effective_from: UtcDatetime
    effective_to: UtcDatetime | None

    @model_validator(mode="after")
    def validate_interval(self) -> "UniverseMembership":
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("universe membership interval is empty")
        return self


class BreadthObservation(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    as_of: UtcDatetime
    source_available_at: UtcDatetime
    ingested_at: UtcDatetime
    return_1: float
    above_ma20: bool | None
    new_high20: bool | None
    new_low20: bool | None
    high_volatility: bool | None
    benchmark_correlation20: float | None = Field(default=None, ge=-1, le=1)
    dataset_revision: VersionId

    @model_validator(mode="after")
    def validate_source_time(self) -> "BreadthObservation":
        if self.source_available_at > self.as_of:
            raise ValueError("breadth source timestamp exceeds observation as_of")
        if self.source_available_at > self.ingested_at:
            raise ValueError("breadth source availability exceeds ingestion time")
        return self


class MarketBreadth(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    as_of: UtcDatetime
    universe_version: VersionId
    dataset_revision: VersionId | None
    universe_count: int = Field(ge=1)
    constituent_count: int = Field(ge=0)
    coverage: float = Field(ge=0, le=1)
    minimum_coverage: float = Field(gt=0, le=1)
    partial: bool
    advance_count: int = Field(ge=0)
    decline_count: int = Field(ge=0)
    flat_count: int = Field(ge=0)
    advance_ratio: float | None = Field(default=None, ge=0, le=1)
    ma_participation: float | None = Field(default=None, ge=0, le=1)
    new_high_low_spread: float | None = Field(default=None, ge=-1, le=1)
    cross_section_median_return: float | None
    cross_section_dispersion: float | None = Field(default=None, ge=0)
    etf_diffusion: float | None = Field(default=None, ge=0, le=1)
    correlation_participation: float | None = Field(default=None, ge=0, le=1)
    volatility_participation: float | None = Field(default=None, ge=0, le=1)
    source_max_available_at: UtcDatetime | None
    source_max_ingested_at: UtcDatetime | None


class ReturnObservation(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    session_date: date
    return_1: float
    source_available_at: UtcDatetime
    ingested_at: UtcDatetime
    dataset_revision: VersionId

    @model_validator(mode="after")
    def validate_ingestion_time(self) -> "ReturnObservation":
        if self.source_available_at > self.ingested_at:
            raise ValueError("return source availability exceeds ingestion time")
        return self


class SectorMapping(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    symbol: UsSymbol
    sector_etf: UsSymbol
    mapping_version: VersionId
    effective_from: UtcDatetime
    effective_to: UtcDatetime | None

    @model_validator(mode="after")
    def validate_interval(self) -> "SectorMapping":
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("sector mapping interval is empty")
        return self


class RelativeContext(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    benchmark_symbol: UsSymbol
    sector_etf: UsSymbol
    as_of: UtcDatetime
    mapping_version: VersionId
    dataset_revision: VersionId
    requested_lookback_sessions: int = Field(ge=1)
    aligned_session_count: int = Field(ge=0)
    partial: bool
    market_relative_strength: float | None
    sector_relative_strength: float | None
    market_beta: float | None
    source_max_available_at: UtcDatetime | None
    source_max_ingested_at: UtcDatetime | None


def compute_market_breadth(
    memberships: Sequence[UniverseMembership],
    observations: Sequence[BreadthObservation],
    *,
    as_of: UtcDatetime | str,
    universe_version: str,
    minimum_coverage: float,
) -> MarketBreadth:
    """Compute breadth only for membership active at the historical cutoff."""

    cutoff = TypeAdapter(UtcDatetime).validate_python(as_of)
    if not 0 < minimum_coverage <= 1:
        raise ContextInputError("minimum coverage must lie in (0, 1]")
    active = [
        item
        for item in memberships
        if item.universe_version == universe_version
        and item.effective_from <= cutoff
        and (item.effective_to is None or cutoff < item.effective_to)
    ]
    if not active:
        raise ContextInputError("no active universe membership at cutoff")
    if len({item.symbol for item in active}) != len(active):
        raise ContextInputError("active universe contains duplicate symbols")
    active_by_symbol = {item.symbol: item for item in active}

    visible: dict[str, BreadthObservation] = {}
    seen_keys: set[tuple[str, object]] = set()
    for item in observations:
        key = (item.symbol, item.as_of)
        if key in seen_keys:
            raise ContextInputError("breadth observations contain duplicate keys")
        seen_keys.add(key)
        if (
            item.symbol in active_by_symbol
            and item.as_of == cutoff
            and item.source_available_at <= cutoff
        ):
            previous = visible.get(item.symbol)
            if previous is None or item.as_of > previous.as_of:
                visible[item.symbol] = item

    selected = list(visible.values())
    revisions = {item.dataset_revision for item in selected}
    if len(revisions) > 1:
        raise ContextInputError("breadth observations mix dataset revisions")
    universe_count = len(active)
    constituent_count = len(selected)
    coverage = constituent_count / universe_count
    partial = coverage < minimum_coverage
    returns = [item.return_1 for item in selected]
    advance_count = sum(value > 0 for value in returns)
    decline_count = sum(value < 0 for value in returns)
    flat_count = sum(value == 0 for value in returns)
    metrics = (
        _breadth_metrics(
            selected,
            active_by_symbol,
            minimum_coverage=minimum_coverage,
        )
        if not partial
        else {}
    )

    return MarketBreadth(
        as_of=cutoff,
        universe_version=universe_version,
        dataset_revision=next(iter(revisions)) if revisions else None,
        universe_count=universe_count,
        constituent_count=constituent_count,
        coverage=coverage,
        minimum_coverage=minimum_coverage,
        partial=partial,
        advance_count=advance_count,
        decline_count=decline_count,
        flat_count=flat_count,
        advance_ratio=metrics.get("advance_ratio"),
        ma_participation=metrics.get("ma_participation"),
        new_high_low_spread=metrics.get("new_high_low_spread"),
        cross_section_median_return=metrics.get("cross_section_median_return"),
        cross_section_dispersion=metrics.get("cross_section_dispersion"),
        etf_diffusion=metrics.get("etf_diffusion"),
        correlation_participation=metrics.get("correlation_participation"),
        volatility_participation=metrics.get("volatility_participation"),
        source_max_available_at=(
            max(item.source_available_at for item in selected) if selected else None
        ),
        source_max_ingested_at=(
            max(item.ingested_at for item in selected) if selected else None
        ),
    )


def compute_relative_context(
    symbol: str,
    observations: Sequence[ReturnObservation],
    mappings: Sequence[SectorMapping],
    *,
    as_of: UtcDatetime | str,
    mapping_version: str,
    benchmark_symbol: str,
    lookback_sessions: int,
    minimum_aligned_sessions: int,
    expected_sessions: Sequence[date],
    anchor_session: date,
) -> RelativeContext:
    """Compute relative strength and beta on the exact three-way date intersection."""

    cutoff = TypeAdapter(UtcDatetime).validate_python(as_of)
    if not 1 <= minimum_aligned_sessions <= lookback_sessions:
        raise ContextInputError("aligned-session requirements are invalid")
    expected = tuple(expected_sessions)
    if tuple(sorted(set(expected))) != expected or len(expected) < lookback_sessions:
        raise ContextInputError("expected sessions must be ordered and cover lookback")
    if expected[-1] != anchor_session or anchor_session != cutoff.date():
        raise ContextInputError("expected sessions must end at the anchor session")
    expected_window = set(expected[-lookback_sessions:])
    active_mappings = [
        item
        for item in mappings
        if item.symbol == symbol
        and item.mapping_version == mapping_version
        and item.effective_from <= cutoff
        and (item.effective_to is None or cutoff < item.effective_to)
    ]
    if len(active_mappings) != 1:
        raise ContextInputError("exactly one active sector mapping is required")
    mapping = active_mappings[0]
    required_symbols = {symbol, benchmark_symbol, mapping.sector_etf}
    by_symbol: defaultdict[str, dict[date, ReturnObservation]] = defaultdict(dict)
    revisions: set[str] = set()
    for item in observations:
        if item.symbol not in required_symbols:
            continue
        if (
            item.session_date not in expected_window
            or item.session_date > cutoff.date()
            or item.source_available_at > cutoff
        ):
            continue
        if item.session_date in by_symbol[item.symbol]:
            raise ContextInputError("return observations contain duplicate dates")
        by_symbol[item.symbol][item.session_date] = item
        revisions.add(item.dataset_revision)
    if len(revisions) != 1:
        raise ContextInputError("relative observations require one dataset revision")
    aligned_dates = sorted(
        set(by_symbol[symbol])
        & set(by_symbol[benchmark_symbol])
        & set(by_symbol[mapping.sector_etf])
        & expected_window
    )
    count = len(aligned_dates)
    partial = count < minimum_aligned_sessions
    used = [
        by_symbol[current_symbol][session]
        for current_symbol in (symbol, benchmark_symbol, mapping.sector_etf)
        for session in aligned_dates
    ]
    if partial:
        market_relative = None
        sector_relative = None
        beta = None
    else:
        stock_returns = [by_symbol[symbol][day].return_1 for day in aligned_dates]
        market_returns = [
            by_symbol[benchmark_symbol][day].return_1 for day in aligned_dates
        ]
        sector_returns = [
            by_symbol[mapping.sector_etf][day].return_1 for day in aligned_dates
        ]
        stock_growth = math.prod(1.0 + value for value in stock_returns)
        market_growth = math.prod(1.0 + value for value in market_returns)
        sector_growth = math.prod(1.0 + value for value in sector_returns)
        market_relative = stock_growth / market_growth - 1.0
        sector_relative = stock_growth / sector_growth - 1.0
        beta = _beta(stock_returns, market_returns)

    return RelativeContext(
        symbol=symbol,
        benchmark_symbol=benchmark_symbol,
        sector_etf=mapping.sector_etf,
        as_of=cutoff,
        mapping_version=mapping_version,
        dataset_revision=next(iter(revisions)),
        requested_lookback_sessions=lookback_sessions,
        aligned_session_count=count,
        partial=partial,
        market_relative_strength=market_relative,
        sector_relative_strength=sector_relative,
        market_beta=beta,
        source_max_available_at=(
            max(item.source_available_at for item in used) if used else None
        ),
        source_max_ingested_at=(
            max(item.ingested_at for item in used) if used else None
        ),
    )


def _breadth_metrics(
    selected: Sequence[BreadthObservation],
    memberships: dict[str, UniverseMembership],
    *,
    minimum_coverage: float,
) -> dict[str, float | None]:
    returns = [item.return_1 for item in selected]
    directional = [value for value in returns if value != 0]
    ma_values = [item.above_ma20 for item in selected if item.above_ma20 is not None]
    high_low_values = [
        item
        for item in selected
        if item.new_high20 is not None and item.new_low20 is not None
    ]
    new_highs = sum(item.new_high20 is True for item in high_low_values)
    new_lows = sum(item.new_low20 is True for item in high_low_values)
    etf_returns = [
        item.return_1
        for item in selected
        if memberships[item.symbol].asset_group == "etf"
    ]
    correlations = [
        item.benchmark_correlation20
        for item in selected
        if item.benchmark_correlation20 is not None
    ]
    volatility = [
        item.high_volatility
        for item in selected
        if item.high_volatility is not None
    ]
    median_return = statistics.median(returns)
    universe_count = len(memberships)
    active_etf_count = sum(
        item.asset_group == "etf" for item in memberships.values()
    )

    def enough(count: int, denominator: int = universe_count) -> bool:
        return denominator > 0 and count / denominator >= minimum_coverage

    return {
        "advance_ratio": (
            sum(value > 0 for value in directional) / len(directional)
            if directional
            else None
        ),
        "ma_participation": (
            sum(value is True for value in ma_values) / len(ma_values)
            if enough(len(ma_values))
            else None
        ),
        "new_high_low_spread": (
            (new_highs - new_lows) / len(high_low_values)
            if enough(len(high_low_values))
            else None
        ),
        "cross_section_median_return": median_return,
        "cross_section_dispersion": statistics.median(
            abs(value - median_return) for value in returns
        ),
        "etf_diffusion": (
            sum(value > 0 for value in etf_returns) / len(etf_returns)
            if enough(len(etf_returns), active_etf_count)
            else None
        ),
        "correlation_participation": (
            sum(value >= 0.5 for value in correlations) / len(correlations)
            if enough(len(correlations))
            else None
        ),
        "volatility_participation": (
            sum(value is True for value in volatility) / len(volatility)
            if enough(len(volatility))
            else None
        ),
    }


def _beta(stock_returns: Sequence[float], market_returns: Sequence[float]) -> float | None:
    market_mean = statistics.fmean(market_returns)
    market_variance = statistics.fmean(
        (value - market_mean) ** 2 for value in market_returns
    )
    if market_variance == 0:
        return None
    stock_mean = statistics.fmean(stock_returns)
    covariance = statistics.fmean(
        (stock - stock_mean) * (market - market_mean)
        for stock, market in zip(stock_returns, market_returns, strict=True)
    )
    return covariance / market_variance
