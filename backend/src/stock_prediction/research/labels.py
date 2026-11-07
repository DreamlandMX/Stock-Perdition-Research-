"""Versioned current-state and future-outcome labels with frozen boundaries."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_prediction.contracts.market_data import UsSymbol, VersionId
from stock_prediction.contracts.research import (
    BreakoutEvent,
    DirectionLabel,
    FalseBreakoutResolution,
    Horizon,
    HorizonTarget,
    LabelBundle,
    RegimeLabel,
    RegimeState,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.research.calendar import TradingCalendarArtifact
from stock_prediction.research.primitives import MarketPrimitives
from stock_prediction.research.thresholds import RegimeThresholdArtifact


class LabelInputError(ValueError):
    """Label inputs do not form an unambiguous anchor and future path."""


HORIZONS: tuple[Horizon, ...] = (1, 5, 20)


class RegimeAnchor(BaseModel):
    """Information visible at the completed anchor session."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    as_of: UtcDatetime
    adjusted_high: float = Field(gt=0)
    adjusted_low: float = Field(gt=0)
    adjusted_close: float = Field(gt=0)
    complete_regular_session: bool
    corporate_action_clean: bool
    higher_highs: bool
    higher_lows: bool
    lower_highs: bool
    lower_lows: bool
    compression_percentile: float = Field(ge=0, le=1)
    dataset_revision: VersionId

    @model_validator(mode="after")
    def validate_price_range(self) -> "RegimeAnchor":
        if not self.adjusted_low <= self.adjusted_close <= self.adjusted_high:
            raise ValueError("anchor close must lie inside high/low")
        return self


class LabelBar(BaseModel):
    """One scheduled future session used only for outcome construction."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    session_date: date
    bar_at: UtcDatetime
    adjusted_open: float = Field(gt=0)
    adjusted_high: float = Field(gt=0)
    adjusted_low: float = Field(gt=0)
    adjusted_close: float = Field(gt=0)
    total_return_index: float = Field(gt=0)
    cash_distribution: float = Field(ge=0)
    complete_regular_session: bool
    corporate_action_clean: bool
    regime_state: RegimeLabel
    dataset_revision: VersionId
    adjustment_revision: VersionId

    @model_validator(mode="after")
    def validate_prices(self) -> "LabelBar":
        if not (
            self.adjusted_low
            <= min(self.adjusted_open, self.adjusted_close)
            <= max(self.adjusted_open, self.adjusted_close)
            <= self.adjusted_high
        ):
            raise ValueError("open and close must lie inside future high/low")
        return self


class LabelConfig(BaseModel):
    """Versioned semantics that must change whenever a label rule changes."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    label_version: VersionId
    atr_buffer_multiplier: float = Field(ge=0)
    flat_band_atr_multiplier: float = Field(ge=0)
    false_breakout_sessions: int = Field(ge=1)
    range_exit_confirmation_sessions: int = Field(ge=2)


def assign_regime_state(
    anchor: RegimeAnchor,
    primitives: MarketPrimitives,
    thresholds: RegimeThresholdArtifact,
    *,
    atr_buffer_multiplier: float,
    label_version: str,
) -> RegimeState:
    """Assign a current state from anchor-and-past evidence only."""

    if anchor.symbol != primitives.symbol:
        raise LabelInputError("anchor and primitive symbol do not match")
    if anchor.dataset_revision != thresholds.dataset_revision:
        raise LabelInputError("threshold and anchor dataset revisions do not match")
    if anchor.dataset_revision != primitives.dataset_revision:
        raise LabelInputError("primitive and anchor dataset revisions do not match")
    if primitives.asset_group != thresholds.asset_group:
        raise LabelInputError("threshold asset group does not match primitives")
    if primitives.feature_set_version != thresholds.feature_set_version:
        raise LabelInputError("threshold feature set does not match primitives")
    if (
        primitives.calendar_version != thresholds.calendar_version
        or primitives.calendar_hash != thresholds.calendar_hash
        or primitives.calendar_source is not thresholds.calendar_source
    ):
        raise LabelInputError("threshold calendar evidence does not match primitives")
    if primitives.source_max_available_at > anchor.as_of:
        raise ValueError("primitive contains a future source timestamp")
    if (
        thresholds.source_max_available_at > anchor.as_of
        or thresholds.fitted_at > anchor.as_of
        or thresholds.train_end > anchor.as_of.date()
    ):
        raise ValueError("anchor state cannot use a future threshold artifact")
    if atr_buffer_multiplier < 0 or not math.isfinite(atr_buffer_multiplier):
        raise LabelInputError("ATR buffer multiplier must be finite and non-negative")

    invalid_reason = _invalid_reason(anchor, primitives)
    channel_high = primitives.prior_high_20
    channel_low = primitives.prior_low_20
    atr20 = primitives.atr20
    if invalid_reason is not None:
        return RegimeState(
            symbol=anchor.symbol,
            as_of=anchor.as_of,
            state=RegimeLabel.INVALID,
            channel_high=channel_high,
            channel_low=channel_low,
            atr20=atr20,
            efficiency_ratio=primitives.efficiency_ratio20,
            compression_percentile=anchor.compression_percentile,
            state_confidence=0.0,
            breakout_event=BreakoutEvent.NONE,
            extreme_event=False,
            label_version=label_version,
            threshold_version=thresholds.threshold_version,
            dataset_revision=anchor.dataset_revision,
            feature_set_version=primitives.feature_set_version,
            calendar_version=primitives.calendar_version,
            calendar_hash=primitives.calendar_hash,
            calendar_source=primitives.calendar_source,
            invalid_reason=invalid_reason,
        )

    assert channel_high is not None
    assert channel_low is not None
    assert atr20 is not None
    assert primitives.efficiency_ratio20 is not None
    assert primitives.normalized_slope20 is not None

    breakout = _bar_breakout(
        high=anchor.adjusted_high,
        low=anchor.adjusted_low,
        close=anchor.adjusted_close,
        channel_high=channel_high,
        channel_low=channel_low,
        atr20=atr20,
        buffer_multiplier=atr_buffer_multiplier,
    )
    if breakout is not BreakoutEvent.NONE:
        state = RegimeLabel.TRANSITION
        confidence = 0.7
    else:
        state, confidence = _rule_state(anchor, primitives, thresholds)

    return RegimeState(
        symbol=anchor.symbol,
        as_of=anchor.as_of,
        state=state,
        channel_high=channel_high,
        channel_low=channel_low,
        atr20=atr20,
        efficiency_ratio=primitives.efficiency_ratio20,
        compression_percentile=anchor.compression_percentile,
        state_confidence=confidence,
        breakout_event=breakout,
        extreme_event=breakout is BreakoutEvent.EXTREME_BOTH,
        label_version=label_version,
        threshold_version=thresholds.threshold_version,
        dataset_revision=anchor.dataset_revision,
        feature_set_version=primitives.feature_set_version,
        calendar_version=primitives.calendar_version,
        calendar_hash=primitives.calendar_hash,
        calendar_source=primitives.calendar_source,
        invalid_reason=None,
    )


def generate_label_bundle(
    anchor_state: RegimeState,
    anchor_bar: LabelBar,
    future_bars: Sequence[LabelBar],
    *,
    config: LabelConfig,
    calendar: TradingCalendarArtifact,
) -> LabelBundle:
    """Create 1/5/20 outcomes from `(t, t+h]` using frozen anchor evidence."""

    _validate_outcome_inputs(
        anchor_state,
        anchor_bar,
        future_bars,
        config,
        calendar,
    )
    ordered = tuple(future_bars)
    assert anchor_state.channel_high is not None
    assert anchor_state.channel_low is not None
    assert anchor_state.atr20 is not None

    false_resolution, false_event_at = _false_breakout_resolution(
        anchor_state,
        ordered,
        sessions=config.false_breakout_sessions,
    )
    targets = tuple(
        _horizon_target(
            horizon,
            anchor_state,
            anchor_bar,
            ordered,
            config,
        )
        for horizon in HORIZONS
    )
    return LabelBundle(
        symbol=anchor_state.symbol,
        anchor_at=anchor_state.as_of,
        anchor_state=anchor_state.state,
        frozen_channel_high=anchor_state.channel_high,
        frozen_channel_low=anchor_state.channel_low,
        frozen_atr20=anchor_state.atr20,
        atr_buffer_multiplier=config.atr_buffer_multiplier,
        false_breakout_resolution=false_resolution,
        false_breakout_event_at=false_event_at,
        targets=targets,
        label_version=config.label_version,
        threshold_version=anchor_state.threshold_version,
        dataset_revision=anchor_state.dataset_revision,
        adjustment_revision=anchor_bar.adjustment_revision,
        feature_set_version=anchor_state.feature_set_version,
        calendar_version=calendar.calendar_version,
        calendar_hash=calendar.input_hash,
        calendar_source=calendar.source,
    )


def _invalid_reason(
    anchor: RegimeAnchor, primitives: MarketPrimitives
) -> str | None:
    if not anchor.complete_regular_session:
        return "incomplete_anchor_session"
    if not anchor.corporate_action_clean:
        return "unresolved_corporate_action"
    if not primitives.valid_core:
        return "incomplete_core_window"
    evidence = (
        primitives.prior_high_20,
        primitives.prior_low_20,
        primitives.atr20,
        primitives.efficiency_ratio20,
        primitives.normalized_slope20,
    )
    if any(value is None for value in evidence):
        return "missing_regime_evidence"
    return None


def _rule_state(
    anchor: RegimeAnchor,
    primitives: MarketPrimitives,
    thresholds: RegimeThresholdArtifact,
) -> tuple[RegimeLabel, float]:
    assert primitives.efficiency_ratio20 is not None
    assert primitives.normalized_slope20 is not None
    values = thresholds.values
    efficient = primitives.efficiency_ratio20 >= values.efficiency_ratio_q75
    both_structures = (
        anchor.higher_highs
        and anchor.higher_lows
        and anchor.lower_highs
        and anchor.lower_lows
    )
    if efficient and both_structures:
        return RegimeLabel.TRANSITION, 0.3

    uptrend = (
        efficient
        and primitives.normalized_slope20 >= values.absolute_slope_q75
        and anchor.higher_highs
        and anchor.higher_lows
    )
    downtrend = (
        efficient
        and primitives.normalized_slope20 <= -values.absolute_slope_q75
        and anchor.lower_highs
        and anchor.lower_lows
    )
    if uptrend and downtrend:
        return RegimeLabel.TRANSITION, 0.3
    if uptrend:
        return RegimeLabel.UPTREND, 0.8
    if downtrend:
        return RegimeLabel.DOWNTREND, 0.8

    range_state = (
        primitives.channel_width20 is not None
        and primitives.channel_width20 <= values.channel_width_q25
        and primitives.efficiency_ratio20 <= values.efficiency_ratio_q25
        and abs(primitives.normalized_slope20) <= values.absolute_slope_q25
    )
    if range_state:
        return RegimeLabel.RANGE, 0.8
    return RegimeLabel.TRANSITION, 0.4


def _bar_breakout(
    *,
    high: float,
    low: float,
    close: float,
    channel_high: float,
    channel_low: float,
    atr20: float,
    buffer_multiplier: float,
) -> BreakoutEvent:
    buffer = atr20 * buffer_multiplier
    high_cross = high >= channel_high + buffer and high > channel_high
    low_cross = low <= channel_low - buffer and low < channel_low
    if high_cross and low_cross:
        return BreakoutEvent.EXTREME_BOTH
    if close >= channel_high + buffer and close > channel_high:
        return BreakoutEvent.UP
    if close <= channel_low - buffer and close < channel_low:
        return BreakoutEvent.DOWN
    return BreakoutEvent.NONE


def _validate_outcome_inputs(
    anchor_state: RegimeState,
    anchor_bar: LabelBar,
    future_bars: Sequence[LabelBar],
    config: LabelConfig,
    calendar: TradingCalendarArtifact,
) -> None:
    if anchor_state.state is RegimeLabel.INVALID:
        raise LabelInputError("invalid anchor state cannot generate outcomes")
    if anchor_state.label_version != config.label_version:
        raise LabelInputError("label versions do not match")
    if anchor_state.as_of != anchor_bar.bar_at:
        raise LabelInputError("anchor state and bar times do not match")
    if anchor_state.symbol != anchor_bar.symbol:
        raise LabelInputError("anchor state and bar symbol do not match")
    if anchor_state.dataset_revision != anchor_bar.dataset_revision:
        raise LabelInputError("anchor state and bar dataset revisions do not match")
    if calendar.dataset_revision != anchor_bar.dataset_revision:
        raise LabelInputError("calendar and label dataset revisions do not match")
    if anchor_bar.session_date not in calendar.sessions:
        raise LabelInputError("anchor session is absent from trading calendar")
    if (
        anchor_state.calendar_version != calendar.calendar_version
        or anchor_state.calendar_hash != calendar.input_hash
        or anchor_state.calendar_source is not calendar.source
    ):
        raise LabelInputError("anchor state and trading calendar do not match")
    if not future_bars:
        raise LabelInputError("at least one future scheduled session is required")
    if any(item.symbol != anchor_state.symbol for item in future_bars):
        raise LabelInputError("future symbol does not match anchor")
    if any(
        item.adjustment_revision != anchor_bar.adjustment_revision
        for item in future_bars
    ):
        raise LabelInputError("future adjustment revision does not match anchor")
    if any(item.dataset_revision != anchor_bar.dataset_revision for item in future_bars):
        raise LabelInputError("future dataset revision does not match anchor")
    times = [item.bar_at for item in future_bars]
    dates = [item.session_date for item in future_bars]
    if len(set(times)) != len(times) or len(set(dates)) != len(dates):
        raise LabelInputError("future sessions must be unique")
    if times != sorted(times):
        raise LabelInputError("future sessions must be ordered")
    if any(item.bar_at <= anchor_state.as_of for item in future_bars):
        raise LabelInputError("future sessions must be after anchor")
    expected = tuple(
        session
        for session in calendar.sessions
        if session > anchor_bar.session_date
    )
    if tuple(dates) != expected[: len(dates)]:
        raise LabelInputError("future sessions must be an exact calendar prefix")


def _horizon_target(
    horizon: Horizon,
    anchor_state: RegimeState,
    anchor_bar: LabelBar,
    future_bars: Sequence[LabelBar],
    config: LabelConfig,
) -> HorizonTarget:
    path = tuple(future_bars[:horizon])
    complete = len(path) == horizon and all(
        item.complete_regular_session and item.corporate_action_clean for item in path
    )
    breakout, breakout_at = _first_breakout(
        path,
        anchor_state,
        config.atr_buffer_multiplier,
        horizon_complete=complete,
    )
    range_exit, range_exit_at = _range_exit(
        horizon,
        path,
        anchor_state,
        breakout,
        breakout_at,
        horizon_complete=complete,
        confirmation_sessions=config.range_exit_confirmation_sessions,
    )

    if complete:
        exit_bar = path[-1]
        forecast_return = exit_bar.adjusted_close / anchor_bar.adjusted_close - 1.0
        total_return = (
            exit_bar.total_return_index / anchor_bar.total_return_index - 1.0
        )
        entry = path[0].adjusted_open
        # Entry occurs after the first future session opens. A distribution tied
        # to that session's ex-date belongs to the pre-entry holder.
        distributions = sum(item.cash_distribution for item in path[1:])
        executable_return = (exit_bar.adjusted_close + distributions) / entry - 1.0
        forecast_mfe = max(
            0.0,
            max(item.adjusted_high / anchor_bar.adjusted_close - 1.0 for item in path),
        )
        forecast_mae = min(
            0.0,
            min(item.adjusted_low / anchor_bar.adjusted_close - 1.0 for item in path),
        )
        executable_mfe = max(
            0.0,
            max(item.adjusted_high / entry - 1.0 for item in path),
        )
        executable_mae = min(
            0.0,
            min(item.adjusted_low / entry - 1.0 for item in path),
        )
        forecast_direction = _direction(
            forecast_return,
            anchor_state.atr20,
            anchor_bar.adjusted_close,
            config.flat_band_atr_multiplier,
        )
        executable_direction = _direction(
            executable_return,
            anchor_state.atr20,
            entry,
            config.flat_band_atr_multiplier,
        )
    else:
        forecast_return = None
        total_return = None
        executable_return = None
        forecast_mfe = None
        forecast_mae = None
        executable_mfe = None
        executable_mae = None
        forecast_direction = DirectionLabel.UNRESOLVED
        executable_direction = DirectionLabel.UNRESOLVED

    return HorizonTarget(
        horizon=horizon,
        forecast_direction=forecast_direction,
        executable_direction=executable_direction,
        capital_gain_return=forecast_return,
        total_return=total_return,
        executable_return=executable_return,
        forecast_mfe=forecast_mfe,
        forecast_mae=forecast_mae,
        executable_mfe=executable_mfe,
        executable_mae=executable_mae,
        breakout_event=breakout,
        breakout_event_at=breakout_at,
        range_exit=range_exit,
        range_exit_at=range_exit_at,
        label_end_at=path[-1].bar_at,
        resolved=complete,
    )


def _first_breakout(
    path: Sequence[LabelBar],
    state: RegimeState,
    buffer_multiplier: float,
    *,
    horizon_complete: bool,
) -> tuple[BreakoutEvent, datetime | None]:
    assert state.channel_high is not None
    assert state.channel_low is not None
    assert state.atr20 is not None
    for item in path:
        if not item.complete_regular_session or not item.corporate_action_clean:
            return BreakoutEvent.UNRESOLVED, None
        event = _bar_breakout(
            high=item.adjusted_high,
            low=item.adjusted_low,
            close=item.adjusted_close,
            channel_high=state.channel_high,
            channel_low=state.channel_low,
            atr20=state.atr20,
            buffer_multiplier=buffer_multiplier,
        )
        if event is not BreakoutEvent.NONE:
            return event, item.bar_at
    if horizon_complete:
        return BreakoutEvent.NONE, None
    return BreakoutEvent.UNRESOLVED, None


def _range_exit(
    horizon: int,
    path: Sequence[LabelBar],
    anchor_state: RegimeState,
    breakout: BreakoutEvent,
    breakout_at: datetime | None,
    *,
    horizon_complete: bool,
    confirmation_sessions: int,
) -> tuple[bool | None, datetime | None]:
    if horizon == 1 or anchor_state.state is not RegimeLabel.RANGE:
        return None, None
    if breakout in {BreakoutEvent.UP, BreakoutEvent.DOWN}:
        return True, breakout_at

    consecutive = 0
    for item in path:
        if not item.complete_regular_session or not item.corporate_action_clean:
            return None, None
        if item.regime_state not in {RegimeLabel.RANGE, RegimeLabel.INVALID}:
            consecutive += 1
            if consecutive == confirmation_sessions:
                return True, item.bar_at
        else:
            consecutive = 0
    return (False, None) if horizon_complete else (None, None)


def _false_breakout_resolution(
    anchor_state: RegimeState,
    future_bars: Sequence[LabelBar],
    *,
    sessions: int,
) -> tuple[FalseBreakoutResolution, datetime | None]:
    if anchor_state.breakout_event not in {BreakoutEvent.UP, BreakoutEvent.DOWN}:
        return FalseBreakoutResolution.NOT_APPLICABLE, None
    assert anchor_state.channel_high is not None
    assert anchor_state.channel_low is not None
    path = future_bars[:sessions]
    for index, item in enumerate(path, start=1):
        if not item.complete_regular_session or not item.corporate_action_clean:
            return FalseBreakoutResolution.UNRESOLVED, None
        if anchor_state.channel_low <= item.adjusted_close <= anchor_state.channel_high:
            resolution = (
                FalseBreakoutResolution.IMMEDIATE_FAILURE
                if index == 1
                else FalseBreakoutResolution.DELAYED_FAILURE
            )
            return resolution, item.bar_at
    if len(path) == sessions:
        return FalseBreakoutResolution.CONFIRMED_BREAKOUT, None
    return FalseBreakoutResolution.UNRESOLVED, None


def _direction(
    value: float,
    atr20: float | None,
    reference_price: float,
    flat_multiplier: float,
) -> DirectionLabel:
    assert atr20 is not None
    band = atr20 * flat_multiplier / reference_price
    if abs(value) <= band:
        return DirectionLabel.FLAT
    return DirectionLabel.UP if value > 0 else DirectionLabel.DOWN
