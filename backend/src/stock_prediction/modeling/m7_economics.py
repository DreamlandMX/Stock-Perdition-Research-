"""Research-only M7 direction economic diagnostics and catastrophic guardrail."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import cast

import numpy as np
import pandas as pd

from stock_prediction.modeling.contracts import SampleKey
from stock_prediction.contracts.research import Horizon


class M7EconomicInputError(ValueError):
    """Economic evidence is incomplete, misaligned, or non-causal."""


@dataclass(frozen=True, slots=True)
class M7EconomicSeriesMetrics:
    gross_return: float
    net_return: float
    annualized_sharpe: float
    maximum_drawdown: float
    average_turnover: float
    average_gross_exposure: float
    trade_count: int
    cohort_count: int
    capacity_proxy_usd: float
    cost_sensitivity: Mapping[int, float]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "cost_sensitivity",
            MappingProxyType(dict(self.cost_sensitivity)),
        )


@dataclass(frozen=True, slots=True)
class M7EconomicHorizonDiagnostic:
    horizon: int
    candidate: M7EconomicSeriesMetrics
    incumbent: M7EconomicSeriesMetrics
    spy: M7EconomicSeriesMetrics
    seed100_equal_weight: M7EconomicSeriesMetrics
    catastrophic: bool


@dataclass(frozen=True, slots=True)
class M7EconomicDiagnostic:
    horizons: Mapping[int, M7EconomicHorizonDiagnostic]
    catastrophic_horizon_count: int
    catastrophic_guardrail_passed: bool
    evidence_scope: str = "research_only"

    def __post_init__(self) -> None:
        object.__setattr__(self, "horizons", MappingProxyType(dict(self.horizons)))


def evaluate_m7_direction_economics(
    market_frame: pd.DataFrame,
    *,
    candidate_probabilities: Mapping[int, tuple[Sequence[SampleKey], object]],
    incumbent_probabilities: Mapping[int, tuple[Sequence[SampleKey], object]],
) -> M7EconomicDiagnostic:
    """Evaluate frozen probabilities without feeding economics back into selection."""

    if set(candidate_probabilities) != {1, 5, 20} or set(
        incumbent_probabilities
    ) != {1, 5, 20}:
        raise M7EconomicInputError("m7_economic_horizons_invalid")
    prepared = _prepare_market_frame(market_frame)
    diagnostics: dict[int, M7EconomicHorizonDiagnostic] = {}
    for horizon in (1, 5, 20):
        candidate_keys, candidate_values = candidate_probabilities[horizon]
        incumbent_keys, incumbent_values = incumbent_probabilities[horizon]
        keys = tuple(candidate_keys)
        if tuple(incumbent_keys) != keys:
            raise M7EconomicInputError("m7_economic_probability_keys_mismatch")
        candidate = _strategy_metrics(
            prepared,
            keys=keys,
            probabilities=_probability_matrix(candidate_values, len(keys)),
            horizon=horizon,
        )
        incumbent = _strategy_metrics(
            prepared,
            keys=keys,
            probabilities=_probability_matrix(incumbent_values, len(keys)),
            horizon=horizon,
        )
        spy = _benchmark_metrics(
            prepared,
            keys=keys,
            horizon=horizon,
            spy_only=True,
        )
        seed100 = _benchmark_metrics(
            prepared,
            keys=keys,
            horizon=horizon,
            spy_only=False,
        )
        catastrophic = m7_economic_horizon_is_catastrophic(
            candidate,
            incumbent,
        )
        diagnostics[horizon] = M7EconomicHorizonDiagnostic(
            horizon=horizon,
            candidate=candidate,
            incumbent=incumbent,
            spy=spy,
            seed100_equal_weight=seed100,
            catastrophic=catastrophic,
        )
    catastrophic_count = sum(row.catastrophic for row in diagnostics.values())
    return M7EconomicDiagnostic(
        horizons=diagnostics,
        catastrophic_horizon_count=catastrophic_count,
        catastrophic_guardrail_passed=m7_economic_bundle_guardrail_passed(
            tuple(row.catastrophic for row in diagnostics.values())
        ),
    )


def _prepare_market_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "symbol",
        "session_date",
        "source_available_at",
        "open",
        "close",
        "volume",
        "eligible",
    }
    if required - set(frame.columns):
        raise M7EconomicInputError("m7_economic_market_columns_missing")
    output = frame.loc[:, sorted(required)].copy()
    output["session_date"] = pd.to_datetime(output["session_date"]).dt.date
    if output.duplicated(["symbol", "session_date"]).any():
        raise M7EconomicInputError("m7_economic_market_identity_duplicate")
    output = output.sort_values(["symbol", "session_date"], kind="stable")
    output["dollar_volume"] = output["close"].astype(float) * output[
        "volume"
    ].astype(float)
    output["median_dollar_volume_20"] = output.groupby(
        "symbol", sort=False
    )["dollar_volume"].transform(
        lambda values: values.rolling(20, min_periods=19).median()
    )
    output["history_coverage_20"] = output.groupby("symbol", sort=False)[
        "close"
    ].transform(lambda values: values.notna().rolling(20, min_periods=1).mean())
    return output.reset_index(drop=True)


def _strategy_metrics(
    frame: pd.DataFrame,
    *,
    keys: tuple[SampleKey, ...],
    probabilities: np.ndarray,
    horizon: int,
) -> M7EconomicSeriesMetrics:
    signals = _direction_signals(probabilities)
    executions = _execution_rows(frame, keys=keys, horizon=horizon)
    executions["side"] = signals
    executions = executions[executions["side"] != 0].copy()
    executions = executions[
        (executions["side"] > 0) | executions["short_eligible"]
    ].copy()
    cohorts: list[tuple[float, float, float, int, float]] = []
    for _anchor, rows in executions.groupby("anchor_session", sort=True):
        weights = np.zeros(len(rows), dtype=float)
        for side in (1, -1):
            selected = np.flatnonzero(rows["side"].to_numpy(dtype=int) == side)
            if len(selected):
                magnitude = min(0.5 / len(selected), 0.05)
                weights[selected] = side * magnitude
        holding_returns = rows["holding_return"].to_numpy(dtype=float)
        gross_exposure = float(np.abs(weights).sum())
        gross = float(np.dot(weights, holding_returns))
        capacity = float(
            np.min(
                rows["median_dollar_volume_20"].to_numpy(dtype=float)
                * np.maximum(np.abs(weights), 1e-12)
            )
        )
        cohorts.append((gross, gross_exposure, 2.0 * gross_exposure, len(rows), capacity))
    return _series_metrics(cohorts, horizon=horizon, apply_costs=True)


def _benchmark_metrics(
    frame: pd.DataFrame,
    *,
    keys: tuple[SampleKey, ...],
    horizon: int,
    spy_only: bool,
) -> M7EconomicSeriesMetrics:
    anchors = tuple(sorted({key[1] for key in keys}))
    symbols = ("US.SPY",) if spy_only else tuple(sorted(set(frame["symbol"])))
    typed_horizon = cast(Horizon, horizon)
    benchmark_keys: tuple[SampleKey, ...] = tuple(
        (symbol, anchor, typed_horizon) for anchor in anchors for symbol in symbols
    )
    rows = _execution_rows(frame, keys=benchmark_keys, horizon=horizon, allow_missing=True)
    cohorts: list[tuple[float, float, float, int, float]] = []
    for _anchor, group in rows.groupby("anchor_session", sort=True):
        if group.empty:
            continue
        weight = 1.0 / len(group)
        gross = float(group["holding_return"].mean())
        capacity = float(group["median_dollar_volume_20"].min() * weight)
        cohorts.append((gross, 1.0, 0.0, len(group), capacity))
    return _series_metrics(cohorts, horizon=horizon, apply_costs=False)


def _execution_rows(
    frame: pd.DataFrame,
    *,
    keys: tuple[SampleKey, ...],
    horizon: int,
    allow_missing: bool = False,
) -> pd.DataFrame:
    by_symbol = {
        str(symbol): rows.reset_index(drop=True)
        for symbol, rows in frame.groupby("symbol", sort=False)
    }
    rows: list[dict[str, object]] = []
    for symbol, anchor, key_horizon in keys:
        if key_horizon != horizon:
            raise M7EconomicInputError("m7_economic_key_horizon_mismatch")
        history = by_symbol.get(symbol)
        if history is None:
            if allow_missing:
                continue
            raise M7EconomicInputError("m7_economic_symbol_missing")
        anchor_date = anchor.date()
        matches = np.flatnonzero(history["session_date"].to_numpy() == anchor_date)
        if len(matches) != 1:
            if allow_missing:
                continue
            raise M7EconomicInputError("m7_economic_anchor_missing")
        position = int(matches[0])
        entry_position = position + 1
        exit_position = position + horizon
        if exit_position >= len(history):
            if allow_missing:
                continue
            raise M7EconomicInputError("m7_economic_execution_window_missing")
        anchor_row = history.iloc[position]
        entry_row = history.iloc[entry_position]
        exit_row = history.iloc[exit_position]
        entry = float(entry_row["open"])
        exit_close = float(exit_row["close"])
        valid = bool(
            anchor_row["eligible"]
            and pd.notna(anchor_row["source_available_at"])
            and pd.notna(entry_row["source_available_at"])
            and math.isfinite(entry)
            and math.isfinite(exit_close)
            and entry > 0
            and exit_close > 0
        )
        if not valid:
            if allow_missing:
                continue
            raise M7EconomicInputError("m7_economic_execution_lineage_invalid")
        median_dollar_volume = float(anchor_row["median_dollar_volume_20"])
        coverage = float(anchor_row["history_coverage_20"])
        rows.append(
            {
                "symbol": symbol,
                "anchor_session": anchor_date,
                "exit_session": exit_row["session_date"],
                "holding_return": exit_close / entry - 1.0,
                "median_dollar_volume_20": median_dollar_volume,
                "short_eligible": bool(
                    float(anchor_row["close"]) >= 5.0
                    and math.isfinite(median_dollar_volume)
                    and median_dollar_volume >= 20_000_000.0
                    and coverage >= 0.95
                ),
            }
        )
    return pd.DataFrame.from_records(rows)


def _direction_signals(probabilities: np.ndarray) -> np.ndarray:
    down = probabilities[:, 0]
    up = probabilities[:, 2]
    maximum = probabilities.max(axis=1)
    long = (up == maximum) & (up >= 0.45) & (up - down >= 0.10)
    short = (down == maximum) & (down >= 0.45) & (down - up >= 0.10)
    return np.asarray(
        long.astype(np.int8) - short.astype(np.int8),
        dtype=np.int8,
    )


def m7_economic_horizon_is_catastrophic(
    candidate: M7EconomicSeriesMetrics,
    incumbent: M7EconomicSeriesMetrics,
) -> bool:
    drawdown_deterioration = abs(candidate.maximum_drawdown) - abs(
        incumbent.maximum_drawdown
    )
    drawdown_threshold = max(
        0.05,
        0.25 * abs(incumbent.maximum_drawdown),
    )
    drawdown_breached = (
        drawdown_deterioration > drawdown_threshold
        or math.isclose(
            drawdown_deterioration,
            drawdown_threshold,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    )
    return bool(
        candidate.annualized_sharpe < incumbent.annualized_sharpe - 0.25
        and drawdown_breached
    )


def m7_economic_bundle_guardrail_passed(
    catastrophic_horizons: Sequence[bool],
) -> bool:
    """Fail the bundle only when at least two registered horizons are catastrophic."""

    return sum(bool(value) for value in catastrophic_horizons) < 2


def _series_metrics(
    cohorts: Sequence[tuple[float, float, float, int, float]],
    *,
    horizon: int,
    apply_costs: bool,
) -> M7EconomicSeriesMetrics:
    if not cohorts:
        return M7EconomicSeriesMetrics(
            gross_return=0.0,
            net_return=0.0,
            annualized_sharpe=0.0,
            maximum_drawdown=0.0,
            average_turnover=0.0,
            average_gross_exposure=0.0,
            trade_count=0,
            cohort_count=0,
            capacity_proxy_usd=0.0,
            cost_sensitivity={bps: 0.0 for bps in (0, 5, 10, 20)},
        )
    values = np.asarray(cohorts, dtype=float)
    gross = values[:, 0]
    exposure = values[:, 1]
    turnover = values[:, 2]
    net = gross - (0.002 * exposure if apply_costs else 0.0)
    gross_equity = np.cumprod(1.0 + gross)
    net_equity = np.cumprod(1.0 + net)
    drawdown_equity = np.concatenate((np.asarray([1.0]), net_equity))
    running_high = np.maximum.accumulate(drawdown_equity)
    drawdown = drawdown_equity / running_high - 1.0
    standard_deviation = float(np.std(net, ddof=1)) if len(net) > 1 else 0.0
    sharpe = (
        float(np.mean(net) / standard_deviation * math.sqrt(252 / horizon))
        if standard_deviation > 0
        else 0.0
    )
    sensitivity = {
        bps: float(
            np.prod(
                1.0
                + gross
                - ((2.0 * bps / 10_000.0) * exposure if apply_costs else 0.0)
            )
            - 1.0
        )
        for bps in (0, 5, 10, 20)
    }
    return M7EconomicSeriesMetrics(
        gross_return=float(gross_equity[-1] - 1.0),
        net_return=float(net_equity[-1] - 1.0),
        annualized_sharpe=sharpe,
        maximum_drawdown=float(np.min(drawdown)),
        average_turnover=float(np.mean(turnover)),
        average_gross_exposure=float(np.mean(exposure)),
        trade_count=int(np.sum(values[:, 3])),
        cohort_count=len(cohorts),
        capacity_proxy_usd=float(np.min(values[:, 4])),
        cost_sensitivity=sensitivity,
    )


def _probability_matrix(values: object, row_count: int) -> np.ndarray:
    matrix = np.asarray(values, dtype=float)
    if (
        matrix.shape != (row_count, 3)
        or not np.all(np.isfinite(matrix))
        or np.any(matrix < 0)
        or np.any(matrix > 1)
        or not np.allclose(matrix.sum(axis=1), 1.0, rtol=0.0, atol=1e-9)
    ):
        raise M7EconomicInputError("m7_economic_probability_invalid")
    return matrix


__all__ = [
    "M7EconomicDiagnostic",
    "M7EconomicHorizonDiagnostic",
    "M7EconomicInputError",
    "M7EconomicSeriesMetrics",
    "evaluate_m7_direction_economics",
    "m7_economic_bundle_guardrail_passed",
    "m7_economic_horizon_is_catastrophic",
]
