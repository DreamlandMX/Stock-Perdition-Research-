"""Leakage-safe daily market primitives computed from adjusted bars."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_prediction.contracts.market_data import Identifier, UsSymbol, VersionId
from stock_prediction.contracts.research import CalendarSource, Sha256
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.research.calendar import TradingCalendarArtifact


class PrimitiveInputError(ValueError):
    """Daily inputs cannot form an unambiguous point-in-time series."""


class DailyFeatureInput(BaseModel):
    """Minimal split-adjusted, complete-session input for daily features."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    asset_group: Identifier
    session_date: date
    available_at: UtcDatetime
    ingested_at: UtcDatetime
    adjusted_high: float = Field(gt=0)
    adjusted_low: float = Field(gt=0)
    adjusted_close: float = Field(gt=0)
    adjusted_volume: float = Field(ge=0)
    turnover: float | None = Field(default=None, ge=0)
    complete_regular_session: bool
    dataset_revision: VersionId
    adjustment_revision: VersionId

    @model_validator(mode="after")
    def validate_prices(self) -> "DailyFeatureInput":
        if not (
            self.adjusted_low
            <= self.adjusted_close
            <= self.adjusted_high
        ):
            raise ValueError("adjusted close must lie inside high/low")
        if self.available_at > self.ingested_at:
            raise ValueError("daily source availability exceeds ingestion time")
        return self


class MarketPrimitives(BaseModel):
    """Past-only primitives at one completed daily anchor."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    asset_group: Identifier
    session_date: date
    source_max_available_at: UtcDatetime
    source_max_ingested_at: UtcDatetime
    dataset_revision: VersionId
    adjustment_revision: VersionId
    feature_set_version: VersionId
    calendar_version: VersionId
    calendar_hash: Sha256
    calendar_source: CalendarSource
    anchor_close: float = Field(gt=0)
    return_1: float | None
    return_5: float | None
    return_20: float | None
    prior_high_20: float | None
    prior_low_20: float | None
    atr20: float | None = Field(default=None, ge=0)
    channel_width20: float | None = Field(default=None, ge=0)
    efficiency_ratio20: float | None = Field(default=None, ge=0, le=1)
    normalized_slope20: float | None
    trend_r2_20: float | None = Field(default=None, ge=0, le=1)
    realized_volatility20: float | None = Field(default=None, ge=0)
    downside_volatility20: float | None = Field(default=None, ge=0)
    max_drawdown20: float | None = Field(default=None, le=0)
    max_up_move20: float | None = Field(default=None, ge=0)
    channel_position20: float | None
    ma20: float | None = Field(default=None, gt=0)
    ma50: float | None = Field(default=None, gt=0)
    distance_ma20: float | None
    distance_ma50: float | None
    bollinger_bandwidth20: float | None = Field(default=None, ge=0)
    atr_percent20: float | None = Field(default=None, ge=0)
    volume_ratio20: float | None = Field(default=None, ge=0)
    turnover_ratio20: float | None = Field(default=None, ge=0)
    data_coverage20: float = Field(ge=0, le=1)
    missing_session_count20: int = Field(ge=0, le=21)
    valid_core: bool


def compute_market_primitives(
    bars: Sequence[DailyFeatureInput],
    *,
    calendar: TradingCalendarArtifact,
    feature_set_version: str,
) -> tuple[MarketPrimitives, ...]:
    """Compute daily primitives using explicit calendar positions and no future rows."""

    if not bars:
        return ()
    sessions = calendar.sessions

    ordered = sorted(bars, key=lambda item: item.session_date)
    partitions = {(item.symbol, item.asset_group) for item in ordered}
    if len(partitions) != 1:
        raise PrimitiveInputError("inputs must contain one symbol and asset group")
    if len({item.session_date for item in ordered}) != len(ordered):
        raise PrimitiveInputError("inputs contain a duplicate session")
    calendar_set = set(sessions)
    if any(item.session_date not in calendar_set for item in ordered):
        raise PrimitiveInputError("bar session is absent from expected calendar")
    if any(not item.complete_regular_session for item in ordered):
        raise PrimitiveInputError("daily primitives require complete regular sessions")
    if len({item.adjustment_revision for item in ordered}) != 1:
        raise PrimitiveInputError("inputs must use one adjustment revision")
    if len({item.dataset_revision for item in ordered}) != 1:
        raise PrimitiveInputError("inputs must use one dataset revision")
    if ordered[0].dataset_revision != calendar.dataset_revision:
        raise PrimitiveInputError("calendar and bars dataset revisions do not match")
    if any(
        item.available_at.date() > item.session_date + timedelta(days=1)
        for item in ordered
    ) or any(
        current.available_at <= previous.available_at
        for previous, current in zip(ordered, ordered[1:], strict=False)
    ):
        raise PrimitiveInputError("bar availability order is invalid")

    by_session = {item.session_date: item for item in ordered}
    position = {session: index for index, session in enumerate(sessions)}
    output: list[MarketPrimitives] = []
    for anchor in ordered:
        anchor_index = position[anchor.session_date]
        expected_window = sessions[max(0, anchor_index - 20) : anchor_index + 1]
        present_window = [by_session.get(session) for session in expected_window]
        present_count = sum(item is not None for item in present_window)
        missing_count = 21 - present_count
        coverage = present_count / 21
        core_is_complete = (
            anchor_index >= 20
            and len(expected_window) == 21
            and all(item is not None for item in present_window)
        )

        values: dict[str, float | None] = _empty_values()
        source_max = anchor.available_at
        ingested_max = anchor.ingested_at
        if core_is_complete:
            core = [item for item in present_window if item is not None]
            source_max = max(item.available_at for item in core)
            ingested_max = max(item.ingested_at for item in core)
            values = _complete_values(core, anchor)

        ma50: float | None = None
        if anchor_index >= 49:
            expected_50 = sessions[anchor_index - 49 : anchor_index + 1]
            present_50 = [by_session.get(session) for session in expected_50]
            if all(item is not None for item in present_50):
                ma50 = statistics.fmean(
                    item.adjusted_close
                    for item in present_50
                    if item is not None
                )
                source_max = max(
                    item.available_at for item in present_50 if item is not None
                )
                ingested_max = max(
                    item.ingested_at for item in present_50 if item is not None
                )
        distance_ma50 = (
            anchor.adjusted_close / ma50 - 1.0 if ma50 is not None else None
        )

        output.append(
            MarketPrimitives(
                symbol=anchor.symbol,
                asset_group=anchor.asset_group,
                session_date=anchor.session_date,
                source_max_available_at=source_max,
                source_max_ingested_at=ingested_max,
                dataset_revision=anchor.dataset_revision,
                adjustment_revision=anchor.adjustment_revision,
                feature_set_version=feature_set_version,
                calendar_version=calendar.calendar_version,
                calendar_hash=calendar.input_hash,
                calendar_source=calendar.source,
                anchor_close=anchor.adjusted_close,
                **values,
                ma50=ma50,
                distance_ma50=distance_ma50,
                data_coverage20=coverage,
                missing_session_count20=missing_count,
                valid_core=core_is_complete,
            )
        )
    return tuple(output)


def compute_market_primitives_batch(
    partitions: Mapping[str, Sequence[DailyFeatureInput]],
    *,
    calendar: TradingCalendarArtifact,
    feature_set_version: str,
) -> tuple[MarketPrimitives, ...]:
    """Compute every symbol through one deterministic batch entry point."""

    output: list[MarketPrimitives] = []
    revisions: set[str] = set()
    for symbol in sorted(partitions):
        rows = partitions[symbol]
        if any(item.symbol != symbol for item in rows):
            raise PrimitiveInputError("batch partition key does not match row symbol")
        revisions.update(item.dataset_revision for item in rows)
        output.extend(
            compute_market_primitives(
                rows,
                calendar=calendar,
                feature_set_version=feature_set_version,
            )
        )
    if len(revisions) > 1:
        raise PrimitiveInputError("batch primitive input mixes dataset revisions")
    return tuple(sorted(output, key=lambda item: (item.symbol, item.session_date)))


def _empty_values() -> dict[str, float | None]:
    return {
        "return_1": None,
        "return_5": None,
        "return_20": None,
        "prior_high_20": None,
        "prior_low_20": None,
        "atr20": None,
        "channel_width20": None,
        "efficiency_ratio20": None,
        "normalized_slope20": None,
        "trend_r2_20": None,
        "realized_volatility20": None,
        "downside_volatility20": None,
        "max_drawdown20": None,
        "max_up_move20": None,
        "channel_position20": None,
        "ma20": None,
        "distance_ma20": None,
        "bollinger_bandwidth20": None,
        "atr_percent20": None,
        "volume_ratio20": None,
        "turnover_ratio20": None,
    }


def _complete_values(
    core: Sequence[DailyFeatureInput], anchor: DailyFeatureInput
) -> dict[str, float | None]:
    prior = core[:-1]
    closes = [item.adjusted_close for item in core]
    current_20 = core[-20:]
    returns = [
        closes[index] / closes[index - 1] - 1.0
        for index in range(1, len(closes))
    ]
    true_ranges = [
        max(
            item.adjusted_high - item.adjusted_low,
            abs(item.adjusted_high - core[index - 1].adjusted_close),
            abs(item.adjusted_low - core[index - 1].adjusted_close),
        )
        for index, item in enumerate(core[1:], start=1)
    ]
    atr = statistics.fmean(true_ranges)
    prior_high = max(item.adjusted_high for item in prior)
    prior_low = min(item.adjusted_low for item in prior)
    path = sum(
        abs(closes[index] - closes[index - 1])
        for index in range(1, len(closes))
    )
    efficiency = abs(closes[-1] - closes[0]) / path if path else 0.0
    slope, r2 = _linear_trend([item.adjusted_close for item in current_20])
    realized = _annualized_root_mean_square(returns)
    downside = _annualized_root_mean_square(
        [min(value, 0.0) for value in returns]
    )
    ma20 = statistics.fmean(item.adjusted_close for item in current_20)
    close_std = statistics.pstdev(item.adjusted_close for item in current_20)
    running_peak = closes[0]
    max_drawdown = 0.0
    for close in closes:
        running_peak = max(running_peak, close)
        max_drawdown = min(max_drawdown, close / running_peak - 1.0)
    channel_range = prior_high - prior_low

    return {
        "return_1": closes[-1] / closes[-2] - 1.0,
        "return_5": closes[-1] / closes[-6] - 1.0,
        "return_20": closes[-1] / closes[0] - 1.0,
        "prior_high_20": prior_high,
        "prior_low_20": prior_low,
        "atr20": atr,
        "channel_width20": (prior_high - prior_low) / anchor.adjusted_close,
        "efficiency_ratio20": efficiency,
        "normalized_slope20": slope / ma20,
        "trend_r2_20": r2,
        "realized_volatility20": realized,
        "downside_volatility20": downside,
        "max_drawdown20": max_drawdown,
        "max_up_move20": max(0.0, max(returns)),
        "channel_position20": (
            (anchor.adjusted_close - prior_low) / channel_range
            if channel_range > 0
            else 0.5
        ),
        "ma20": ma20,
        "distance_ma20": anchor.adjusted_close / ma20 - 1.0,
        "bollinger_bandwidth20": 4.0 * close_std / ma20,
        "atr_percent20": atr / anchor.adjusted_close,
        "volume_ratio20": _ratio_to_prior_median(
            anchor.adjusted_volume,
            [item.adjusted_volume for item in prior],
        ),
        "turnover_ratio20": _optional_ratio_to_prior_median(
            anchor.turnover,
            [item.turnover for item in prior],
        ),
    }


def _linear_trend(values: Sequence[float]) -> tuple[float, float]:
    x_mean = (len(values) - 1) / 2
    y_mean = statistics.fmean(values)
    x_variance = sum((index - x_mean) ** 2 for index in range(len(values)))
    slope = (
        sum(
            (index - x_mean) * (value - y_mean)
            for index, value in enumerate(values)
        )
        / x_variance
    )
    fitted = [y_mean + slope * (index - x_mean) for index in range(len(values))]
    total = sum((value - y_mean) ** 2 for value in values)
    residual = sum(
        (value - estimate) ** 2
        for value, estimate in zip(values, fitted, strict=True)
    )
    r2 = 1.0 - residual / total if total else 0.0
    return slope, max(0.0, min(1.0, r2))


def _annualized_root_mean_square(values: Sequence[float]) -> float:
    return math.sqrt(statistics.fmean(value * value for value in values) * 252.0)


def _ratio_to_prior_median(current: float, prior: Sequence[float]) -> float | None:
    median = statistics.median(prior)
    return current / median if median > 0 else None


def _optional_ratio_to_prior_median(
    current: float | None, prior: Sequence[float | None]
) -> float | None:
    if current is None or any(value is None for value in prior):
        return None
    return _ratio_to_prior_median(
        current,
        [value for value in prior if value is not None],
    )
