"""Effective-date corporate-action adjustment and data-quality helpers.

The live catalog intentionally keeps the raw OpenD bars immutable.  This module
builds a versioned, training-only price path where an action is applied on its
effective date and never before it was effective. This alone does not establish
historical knowledge: callers must select the observable revision first.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd


PRICE_COLUMNS = ("open", "high", "low", "close")
UNEXPLAINED_RETURN_LIMIT = 0.50


def apply_forward_adjustment(
    value: float | np.ndarray | pd.Series,
    factor_a: float,
    factor_b: float,
) -> float | np.ndarray | pd.Series:
    """Apply OpenD's documented affine forward-adjustment formula."""

    return value * factor_a + factor_b


def build_point_in_time_adjusted_bars(
    bars: pd.DataFrame,
    rehab: pd.DataFrame,
) -> pd.DataFrame:
    """Return raw bars plus split-safe capital-gain and total-return paths.

    Each corporate action is consumed only on ``ex_div_date``.  The action's
    affine factors adjust the previous close into the current share basis when
    the one-day return is calculated.  Chaining those returns produces a
    continuous model price path without applying an action before its effective
    date. The legacy name does not guarantee strict historical PIT: this function
    does not select observation vintages. Pass a selected snapshot for as-of work.
    """

    if bars.empty:
        return bars.copy()
    required = {"symbol", "session_date", *PRICE_COLUMNS, "volume"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"bars missing required columns: {sorted(missing)}")

    normalized_actions = _normalize_actions(rehab)
    frames: list[pd.DataFrame] = []
    for symbol, source in bars.groupby("symbol", sort=False):
        actions = normalized_actions[
            normalized_actions["symbol"].eq(str(symbol))
        ].copy()
        frames.append(_adjust_symbol(source, actions))
    return pd.concat(frames, ignore_index=True)


def _normalize_actions(rehab: pd.DataFrame) -> pd.DataFrame:
    columns = (
        "symbol",
        "ex_div_date",
        "split_ratio",
        "per_cash_div",
        "special_dividend",
        "forward_adj_factor_a",
        "forward_adj_factor_b",
    )
    if rehab.empty:
        return pd.DataFrame(columns=columns)
    result = rehab.copy()
    for column in columns:
        if column not in result.columns:
            result[column] = np.nan
    result["ex_div_date"] = pd.to_datetime(result["ex_div_date"]).dt.date
    result.sort_values(["symbol", "ex_div_date"], inplace=True)
    return result[list(columns)]


def _compose_affine(actions: pd.DataFrame) -> tuple[float, float]:
    """Compose ``x -> a*x+b`` actions in effective-date row order."""

    factor_a = 1.0
    factor_b = 0.0
    for row in actions.itertuples(index=False):
        action_a = _finite_or_default(row.forward_adj_factor_a, 1.0)
        action_b = _finite_or_default(row.forward_adj_factor_b, 0.0)
        factor_b = factor_b * action_a + action_b
        factor_a *= action_a
    return factor_a, factor_b


def _adjust_symbol(source: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    frame = source.copy().sort_values("session_date").reset_index(drop=True)
    frame["session_date"] = pd.to_datetime(frame["session_date"]).dt.date
    actions_by_date = {
        action_date: group.copy()
        for action_date, group in actions.groupby("ex_div_date", sort=False)
    }

    adjusted_close: list[float] = []
    adjusted_open: list[float] = []
    adjusted_high: list[float] = []
    adjusted_low: list[float] = []
    adjusted_volume: list[float] = []
    capital_returns: list[float] = []
    total_returns: list[float] = []
    action_explained: list[bool] = []
    action_dates: list[str | None] = []
    cumulative_split_volume_factor = 1.0
    previous_raw_close: float | None = None
    previous_adjusted_close: float | None = None

    for row in frame.itertuples(index=False):
        action_rows = actions_by_date.get(row.session_date)
        has_action = action_rows is not None and not action_rows.empty
        factor_a, factor_b = (
            _compose_affine(action_rows)
            if action_rows is not None and not action_rows.empty
            else (1.0, 0.0)
        )
        if has_action and action_rows is not None:
            for split_ratio in action_rows["split_ratio"].dropna().astype(float):
                if split_ratio > 0:
                    cumulative_split_volume_factor *= split_ratio

        raw_close = float(row.close)
        if previous_raw_close is None or previous_adjusted_close is None:
            capital_return = np.nan
            current_adjusted_close = raw_close
        else:
            comparable_previous = float(
                apply_forward_adjustment(previous_raw_close, factor_a, factor_b)
            )
            capital_return = (
                raw_close / comparable_previous - 1.0
                if comparable_previous > 0
                else np.nan
            )
            current_adjusted_close = previous_adjusted_close * (1.0 + capital_return)

        scale = current_adjusted_close / raw_close if raw_close > 0 else np.nan
        adjusted_open.append(float(row.open) * scale)
        adjusted_high.append(float(row.high) * scale)
        adjusted_low.append(float(row.low) * scale)
        adjusted_close.append(current_adjusted_close)
        adjusted_volume.append(float(row.volume) * cumulative_split_volume_factor)
        capital_returns.append(capital_return)

        cash_dividend = 0.0
        if has_action and action_rows is not None:
            cash_dividend = float(
                action_rows[["per_cash_div", "special_dividend"]]
                .fillna(0.0)
                .to_numpy(dtype=float)
                .sum()
            )
        total_returns.append(
            capital_return
            if np.isnan(capital_return) or previous_raw_close in (None, 0)
            else capital_return + cash_dividend / float(previous_raw_close)
        )
        action_explained.append(has_action)
        action_dates.append(row.session_date.isoformat() if has_action else None)
        previous_raw_close = raw_close
        previous_adjusted_close = current_adjusted_close

    frame["raw_open"] = frame["open"].astype(float)
    frame["raw_high"] = frame["high"].astype(float)
    frame["raw_low"] = frame["low"].astype(float)
    frame["raw_close"] = frame["close"].astype(float)
    frame["raw_volume"] = frame["volume"].astype(float)
    frame["open"] = adjusted_open
    frame["high"] = adjusted_high
    frame["low"] = adjusted_low
    frame["close"] = adjusted_close
    frame["volume"] = adjusted_volume
    frame["capital_gain_return_1"] = capital_returns
    frame["total_return_1"] = total_returns
    frame["corporate_action_effective"] = action_explained
    frame["corporate_action_date"] = action_dates
    frame["quality_quarantined"] = (
        frame["capital_gain_return_1"].abs() > UNEXPLAINED_RETURN_LIMIT
    ) & ~frame["corporate_action_effective"]
    frame["quality_failure_code"] = pd.Series(None, index=frame.index, dtype=object)
    frame.loc[frame["quality_quarantined"], "quality_failure_code"] = (
        "unexplained_absolute_return_gt_50pct"
    )
    return frame


def window_has_quarantine(
    values: Iterable[bool],
    *,
    horizon: int,
) -> np.ndarray:
    """Flag anchors whose future label window contains a quarantined row."""

    series = pd.Series(tuple(bool(value) for value in values), dtype=bool)
    windows = pd.concat(
        [series.shift(-step).fillna(False) for step in range(1, horizon + 1)],
        axis=1,
    )
    return np.asarray(windows.any(axis=1).to_numpy(dtype=bool), dtype=bool)


def _finite_or_default(value: Any, default: float) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return default
    return converted if np.isfinite(converted) else default


__all__ = [
    "PRICE_COLUMNS",
    "UNEXPLAINED_RETURN_LIMIT",
    "apply_forward_adjustment",
    "build_point_in_time_adjusted_bars",
    "window_has_quarantine",
]
