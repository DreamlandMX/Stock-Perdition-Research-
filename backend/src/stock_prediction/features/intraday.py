"""As-of aligned features from complete 60-minute regular-session windows."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from stock_prediction.contracts.market_data import UsSymbol, VersionId
from stock_prediction.contracts.time import UtcDatetime


class IntradayInputError(ValueError):
    """Intraday inputs do not match the explicit session schedule."""


class IntradaySessionSpec(BaseModel):
    """UTC session boundary supplied by the authoritative trading calendar."""

    model_config = ConfigDict(strict=True, frozen=True)

    symbol: UsSymbol
    session_date: date
    market_open_at: UtcDatetime
    market_close_at: UtcDatetime
    dataset_revision: VersionId
    adjustment_revision: VersionId

    @model_validator(mode="after")
    def validate_session(self) -> "IntradaySessionSpec":
        if self.market_open_at >= self.market_close_at:
            raise ValueError("market session boundaries are reversed")
        if self.market_open_at.date() != self.session_date:
            raise ValueError("market open date must match session date")
        return self


class HourlyBar(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    session_date: date
    window_start: UtcDatetime
    window_end: UtcDatetime
    adjusted_open: float = Field(gt=0)
    adjusted_high: float = Field(gt=0)
    adjusted_low: float = Field(gt=0)
    adjusted_close: float = Field(gt=0)
    adjusted_volume: float = Field(ge=0)
    source_available_at: UtcDatetime
    ingested_at: UtcDatetime
    complete_window: bool
    dataset_revision: VersionId
    adjustment_revision: VersionId

    @model_validator(mode="after")
    def validate_bar(self) -> "HourlyBar":
        if self.window_start >= self.window_end:
            raise ValueError("hourly bar boundaries are reversed")
        if not (
            self.adjusted_low
            <= min(self.adjusted_open, self.adjusted_close)
            <= max(self.adjusted_open, self.adjusted_close)
            <= self.adjusted_high
        ):
            raise ValueError("hourly open and close must lie inside high/low")
        if self.source_available_at < self.window_end:
            raise ValueError("hourly source timestamp precedes window completion")
        if self.source_available_at > self.ingested_at:
            raise ValueError("hourly source availability exceeds ingestion time")
        return self


class IntradayFeatures(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    session_date: date
    as_of: UtcDatetime
    dataset_revision: VersionId
    adjustment_revision: VersionId
    expected_hour_count: int = Field(ge=0)
    complete_hour_count: int = Field(ge=0)
    partial: bool
    first_hour_return: float | None
    last_hour_return: float | None
    hourly_realized_volatility: float | None = Field(default=None, ge=0)
    high_time_fraction: float | None = Field(default=None, ge=0, le=1)
    low_time_fraction: float | None = Field(default=None, ge=0, le=1)
    range_location: float | None = Field(default=None, ge=0, le=1)
    trend_consistency: float | None = Field(default=None, ge=0, le=1)
    reversal_count: int | None = Field(default=None, ge=0)
    relative_hourly_volume: float | None = Field(default=None, ge=0)
    source_max_available_at: UtcDatetime | None
    source_max_ingested_at: UtcDatetime | None


def aggregate_intraday_session(
    bars: Sequence[HourlyBar],
    session: IntradaySessionSpec,
    *,
    as_of: UtcDatetime | str,
    median_volume_by_hour: Mapping[int, float] | None = None,
) -> IntradayFeatures:
    """Aggregate only schedule-aligned full hours visible by the sample cutoff."""

    cutoff = TypeAdapter(UtcDatetime).validate_python(as_of)
    effective_end = min(cutoff, session.market_close_at)
    expected = _expected_windows(session.market_open_at, effective_end)
    expected_index = {window: index for index, window in enumerate(expected)}
    if any(item.symbol != session.symbol for item in bars):
        raise IntradayInputError("intraday symbol does not match session")
    if any(item.session_date != session.session_date for item in bars):
        raise IntradayInputError("intraday date does not match session")
    if any(item.dataset_revision != session.dataset_revision for item in bars):
        raise IntradayInputError("intraday dataset revision does not match session")
    if any(
        item.adjustment_revision != session.adjustment_revision for item in bars
    ):
        raise IntradayInputError("intraday adjustment revision does not match session")
    identities = [(item.window_start, item.window_end) for item in bars]
    if len(set(identities)) != len(identities):
        raise IntradayInputError("intraday windows must be unique")

    visible: dict[int, HourlyBar] = {}
    for item in bars:
        identity = (item.window_start, item.window_end)
        if not item.complete_window:
            continue
        if item.window_end - item.window_start != timedelta(hours=1):
            raise IntradayInputError("complete intraday window must be exactly 60 minutes")
        if identity not in expected_index:
            # Future aligned windows are harmless and excluded by cutoff. A visible
            # misaligned window is ambiguous and must not be silently resampled.
            if item.window_end <= effective_end:
                raise IntradayInputError("complete intraday window is off schedule")
            continue
        if item.source_available_at <= cutoff:
            visible[expected_index[identity]] = item

    expected_count = len(expected)
    complete_count = len(visible)
    partial = complete_count != expected_count
    ordered = [visible[index] for index in sorted(visible)]
    if not ordered:
        return IntradayFeatures(
            symbol=session.symbol,
            session_date=session.session_date,
            as_of=cutoff,
            dataset_revision=session.dataset_revision,
            adjustment_revision=session.adjustment_revision,
            expected_hour_count=expected_count,
            complete_hour_count=0,
            partial=True,
            first_hour_return=None,
            last_hour_return=None,
            hourly_realized_volatility=None,
            high_time_fraction=None,
            low_time_fraction=None,
            range_location=None,
            trend_consistency=None,
            reversal_count=None,
            relative_hourly_volume=None,
            source_max_available_at=None,
            source_max_ingested_at=None,
        )

    returns_by_index = {
        index: item.adjusted_close / item.adjusted_open - 1.0
        for index, item in visible.items()
    }
    returns = [returns_by_index[index] for index in sorted(returns_by_index)]
    session_direction = ordered[-1].adjusted_close - ordered[0].adjusted_open
    direction_sign = _sign(session_direction)
    matching = (
        sum(_sign(value) == direction_sign for value in returns)
        if direction_sign != 0
        else sum(_sign(value) == 0 for value in returns)
    )
    signs = [sign for value in returns if (sign := _sign(value)) != 0]
    reversals = sum(
        current != previous
        for previous, current in zip(signs, signs[1:], strict=False)
    )
    session_high = max(item.adjusted_high for item in ordered)
    session_low = min(item.adjusted_low for item in ordered)
    high_index = min(
        index for index, item in visible.items() if item.adjusted_high == session_high
    )
    low_index = min(
        index for index, item in visible.items() if item.adjusted_low == session_low
    )
    denominator = max(expected_count - 1, 1)
    volume_ratio = _relative_volume(visible, median_volume_by_hour)

    return IntradayFeatures(
        symbol=session.symbol,
        session_date=session.session_date,
        as_of=cutoff,
        dataset_revision=session.dataset_revision,
        adjustment_revision=session.adjustment_revision,
        expected_hour_count=expected_count,
        complete_hour_count=complete_count,
        partial=partial,
        first_hour_return=returns_by_index.get(0),
        last_hour_return=returns_by_index.get(expected_count - 1),
        hourly_realized_volatility=math.sqrt(
            sum(value * value for value in returns) / len(returns)
        ),
        high_time_fraction=high_index / denominator,
        low_time_fraction=low_index / denominator,
        range_location=(
            (ordered[-1].adjusted_close - session_low) / (session_high - session_low)
            if session_high > session_low
            else 0.5
        ),
        trend_consistency=matching / len(returns),
        reversal_count=reversals,
        relative_hourly_volume=volume_ratio,
        source_max_available_at=max(item.source_available_at for item in ordered),
        source_max_ingested_at=max(item.ingested_at for item in ordered),
    )


def _expected_windows(
    market_open_at: datetime, effective_end: datetime
) -> tuple[tuple[datetime, datetime], ...]:
    output: list[tuple[datetime, datetime]] = []
    start = market_open_at
    while start + timedelta(hours=1) <= effective_end:
        end = start + timedelta(hours=1)
        output.append((start, end))
        start = end
    return tuple(output)


def _relative_volume(
    visible: Mapping[int, HourlyBar],
    baselines: Mapping[int, float] | None,
) -> float | None:
    if baselines is None or any(
        index not in baselines or baselines[index] <= 0 for index in visible
    ):
        return None
    expected = sum(baselines[index] for index in visible)
    return sum(item.adjusted_volume for item in visible.values()) / expected


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0
