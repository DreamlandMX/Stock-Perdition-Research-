"""Assemble promoted M7 capabilities onto an existing formal forecast bundle."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from stock_prediction.contracts.research import RegimeLabel
from stock_prediction.contracts.serving import (
    DirectionProbabilities,
    ForecastAvailable,
    ForecastBundle,
    HorizonForecast,
    RangeExitHazard,
)


class M7FormalAssemblyError(RuntimeError):
    """M7 and preserved formal components cannot form one valid bundle."""


def apply_m7_formal_predictions(
    forecasts: dict[str, ForecastAvailable],
    predictions: pd.DataFrame,
    *,
    model_version: str,
    calibration_version: str,
) -> dict[str, ForecastAvailable]:
    required = {"symbol", "bundle_id", "horizon", "outcome", "probability"}
    if required - set(predictions.columns):
        raise M7FormalAssemblyError("m7_formal_assembly_schema_invalid")
    ledger = predictions.loc[:, sorted(required)].copy()
    ledger["symbol"] = ledger["symbol"].astype(str)
    ledger["bundle_id"] = ledger["bundle_id"].astype(str)
    ledger["outcome"] = ledger["outcome"].astype(str)
    ledger["horizon"] = pd.to_numeric(ledger["horizon"], errors="coerce")
    ledger["probability"] = pd.to_numeric(ledger["probability"], errors="coerce")
    keys = ["symbol", "bundle_id", "horizon", "outcome"]
    if ledger.duplicated(keys).any():
        raise M7FormalAssemblyError("m7_formal_assembly_sample_keys_duplicate")
    values = ledger["probability"].to_numpy(dtype=float)
    if not np.all(np.isfinite(values)) or np.any(values < 0) or np.any(values > 1):
        raise M7FormalAssemblyError("m7_formal_assembly_probability_invalid")
    if set(ledger["symbol"]) != set(forecasts):
        raise M7FormalAssemblyError("m7_formal_assembly_symbol_mismatch")
    return {
        symbol: _assemble_symbol(
            forecast,
            ledger[ledger["symbol"] == symbol],
            model_version=model_version,
            calibration_version=calibration_version,
        )
        for symbol, forecast in forecasts.items()
    }


def _assemble_symbol(
    forecast: ForecastAvailable,
    ledger: pd.DataFrame,
    *,
    model_version: str,
    calibration_version: str,
) -> ForecastAvailable:
    direction = ledger[ledger["bundle_id"] == "direction"]
    range_rows = ledger[ledger["bundle_id"] == "range_exit_20"]
    original_by_horizon = {
        horizon.horizon: horizon for horizon in forecast.bundle.horizons
    }
    assembled: list[HorizonForecast] = []
    for horizon_value in (1, 5, 20):
        original = original_by_horizon[horizon_value]
        updates: dict[str, object] = {
            "calibrated_return_intervals": tuple(
                interval.model_copy(update={"calibration_version": calibration_version})
                for interval in original.calibrated_return_intervals
            )
        }
        if not direction.empty:
            selected = direction[direction["horizon"] == horizon_value]
            by_outcome = {
                str(row.outcome): float(row.probability)
                for row in selected.itertuples(index=False)
            }
            if set(by_outcome) != {"down", "flat", "up"} or not math.isclose(
                sum(by_outcome.values()), 1.0, abs_tol=1e-9
            ):
                raise M7FormalAssemblyError(
                    "m7_formal_assembly_direction_horizon_invalid"
                )
            probabilities = DirectionProbabilities(
                down=by_outcome["down"],
                flat=by_outcome["flat"],
                up=by_outcome["up"],
            )
            updates["forecast_direction_probabilities"] = probabilities
            updates["executable_direction_probabilities"] = probabilities
        if (
            horizon_value == 20
            and not range_rows.empty
            and forecast.bundle.current_regime is RegimeLabel.RANGE
        ):
            target_rows = range_rows[
                (range_rows["horizon"] == 20) & (range_rows["outcome"] == "exit_by")
            ]
            if len(target_rows) != 1:
                raise M7FormalAssemblyError("m7_formal_assembly_range_horizon_invalid")
            target = float(target_rows.iloc[0]["probability"])
            five_day = original_by_horizon[5]
            curve = _extend_range_curve(five_day, target)
            updates.update(
                range_exit_probability=target,
                range_exit_hazard_curve=curve,
                range_exit_metadata=original.range_exit_metadata.model_copy(
                    update={"not_generated_reason": None}
                ),
            )
        candidate = HorizonForecast.model_validate(
            {**original.model_dump(mode="python"), **updates}, strict=True
        )
        assembled.append(candidate)
    bundle = ForecastBundle.model_validate(
        {
            **forecast.bundle.model_dump(mode="python"),
            "horizons": tuple(assembled),
            "model_version": model_version,
            "calibration_version": calibration_version,
        },
        strict=True,
    )
    selected = (
        None
        if forecast.selected_horizon is None
        else next(
            row
            for row in bundle.horizons
            if row.horizon == forecast.selected_horizon.horizon
        )
    )
    return ForecastAvailable.model_validate(
        {
            **forecast.model_dump(mode="python"),
            "bundle": bundle,
            "evidence": bundle.evidence,
            "selected_horizon": selected,
        },
        strict=True,
    )


def _extend_range_curve(
    five_day: HorizonForecast,
    target_probability: float,
) -> tuple[RangeExitHazard, ...]:
    curve = five_day.range_exit_hazard_curve
    if len(curve) != 5 or five_day.range_exit_probability is None:
        raise M7FormalAssemblyError("m7_formal_assembly_range_anchor_unavailable")
    anchor = five_day.range_exit_probability
    if target_probability < anchor - 1e-12:
        raise M7FormalAssemblyError("m7_formal_assembly_range_anchor_monotonicity")
    if anchor >= 1.0 - 1e-15:
        if target_probability < 1.0 - 1e-12:
            raise M7FormalAssemblyError("m7_formal_assembly_range_anchor_monotonicity")
        daily_hazard = 0.0
    else:
        daily_hazard = 1.0 - ((1.0 - target_probability) / (1.0 - anchor)) ** (
            1.0 / 15.0
        )
    result = list(curve)
    survival = 1.0 - anchor
    for day in range(6, 21):
        hazard = daily_hazard
        if day == 20 and survival > 0:
            hazard = 1.0 - (1.0 - target_probability) / survival
        hazard = float(np.clip(hazard, 0.0, 1.0))
        survival *= 1.0 - hazard
        cumulative = 1.0 - survival
        if day == 20:
            cumulative = target_probability
        result.append(
            RangeExitHazard(
                trading_day=day,
                hazard_probability=hazard,
                cumulative_probability=cumulative,
            )
        )
    return tuple(result)


__all__ = [
    "M7FormalAssemblyError",
    "apply_m7_formal_predictions",
]
