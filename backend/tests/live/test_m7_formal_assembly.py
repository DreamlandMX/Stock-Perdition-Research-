from __future__ import annotations

import pandas as pd
import pytest

from backend.tests.contracts.test_serving import valid_forecast_bundle_payload
from stock_prediction.contracts.serving import (
    ForecastAvailable,
    ForecastBundle,
    ServingOverlay,
    ServingStatus,
)
from stock_prediction.live.m7_formal_assembly import (
    M7FormalAssemblyError,
    apply_m7_formal_predictions,
)


def _forecast() -> ForecastAvailable:
    bundle = ForecastBundle.model_validate(valid_forecast_bundle_payload(), strict=True)
    return ForecastAvailable(
        bundle=bundle,
        serving=ServingOverlay(
            serving_status=ServingStatus.LIVE,
            effective_abstain=False,
            evaluated_at=bundle.generated_at,
            exclusion_reason=(),
        ),
        evidence=bundle.evidence,
        similar_cases=(),
    )


def _ledger(*, exit_by_20: float = 0.8) -> pd.DataFrame:
    rows = []
    for horizon in (1, 5, 20):
        for outcome, probability in (
            ("down", 0.15),
            ("flat", 0.25),
            ("up", 0.60),
        ):
            rows.append(
                {
                    "symbol": "US.AAPL",
                    "bundle_id": "direction",
                    "horizon": horizon,
                    "outcome": outcome,
                    "probability": probability,
                }
            )
    for horizon, probability in ((1, 0.1), (5, 0.3), (20, exit_by_20)):
        rows.append(
            {
                "symbol": "US.AAPL",
                "bundle_id": "range_exit_20",
                "horizon": horizon,
                "outcome": "exit_by",
                "probability": probability,
            }
        )
    return pd.DataFrame(rows)


def test_formal_assembly_replaces_only_promoted_tasks_and_preserves_legacy_thirteen() -> (
    None
):
    original = _forecast()

    assembled = apply_m7_formal_predictions(
        {"US.AAPL": original},
        _ledger(),
        model_version="m7-forecast-1234",
        calibration_version="m7-calibration-1234",
    )["US.AAPL"]

    one, five, twenty = assembled.bundle.horizons
    assert one.forecast_direction_probabilities.up == 0.60
    assert five.forecast_direction_probabilities.flat == 0.25
    assert twenty.forecast_direction_probabilities.down == 0.15
    assert (
        one.breakout_probabilities == original.bundle.horizons[0].breakout_probabilities
    )
    assert (
        five.range_exit_hazard_curve
        == original.bundle.horizons[1].range_exit_hazard_curve
    )
    assert twenty.range_exit_probability == 0.8
    assert twenty.range_exit_hazard_curve[-1].cumulative_probability == pytest.approx(
        0.8
    )
    assert assembled.bundle.model_version == "m7-forecast-1234"
    assert assembled.bundle.calibration_version == "m7-calibration-1234"
    assert all(
        interval.calibration_version == "m7-calibration-1234"
        for horizon in assembled.bundle.horizons
        for interval in horizon.calibrated_return_intervals
    )


def test_formal_assembly_fails_closed_when_m7_twenty_day_breaks_five_day_anchor() -> (
    None
):
    original = _forecast()
    five_day = original.bundle.horizons[1].range_exit_probability
    assert five_day is not None

    with pytest.raises(M7FormalAssemblyError, match="range_anchor_monotonicity"):
        apply_m7_formal_predictions(
            {"US.AAPL": original},
            _ledger(exit_by_20=five_day - 0.01),
            model_version="m7-forecast-1234",
            calibration_version="m7-calibration-1234",
        )
