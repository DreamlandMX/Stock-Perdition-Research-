from __future__ import annotations

from datetime import UTC, date, datetime
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_formal_inference import (
    M7FormalBundleBinding,
    M7FormalInferenceError,
    M7FormalInferenceRunner,
    load_m7_formal_inference_result,
)
from stock_prediction.live.m7_promotion import M7ForecastBundleComponent
from stock_prediction.universe.seed import SEED_UNIVERSE


def _component() -> M7ForecastBundleComponent:
    return M7ForecastBundleComponent(
        bundle_id="direction",
        package_id="m7-direction-active",
        package_sha256="a" * 64,
        training_run_id="m7-run-001",
        blind_window_id="blind-001",
        blind_report_hash="b" * 64,
        previous_incumbent_id="legacy-direction-v1",
        verified_range_5d_anchor_hash=None,
    )


def _predictions() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for symbol_index, symbol in enumerate(SEED_UNIVERSE):
        up = 0.2 + symbol_index / 1000
        for horizon in (1, 5, 20):
            rows.extend(
                (
                    {
                        "symbol": symbol,
                        "horizon": horizon,
                        "outcome": "down",
                        "probability": 0.3,
                    },
                    {
                        "symbol": symbol,
                        "horizon": horizon,
                        "outcome": "flat",
                        "probability": 0.7 - up,
                    },
                    {
                        "symbol": symbol,
                        "horizon": horizon,
                        "outcome": "up",
                        "probability": up,
                    },
                )
            )
    return pd.DataFrame(rows)


def test_formal_runner_publishes_exact_seed100_component_once(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    component = _component()
    binding = M7FormalBundleBinding(
        forecast_bundle_id="m7-forecast-active",
        bundle_sha256="c" * 64,
        components=(component,),
    )
    observations = []
    runner = M7FormalInferenceRunner(
        paths,
        bundle_provider=lambda: binding,
        prediction_provider=lambda _component: (date(2026, 8, 25), _predictions()),
        integrity_observer=lambda observation, _generated_at: observations.append(
            observation
        ),
    )

    first = runner.run(generated_at=datetime(2026, 8, 25, 22, tzinfo=UTC))
    second = runner.run(generated_at=datetime(2026, 8, 25, 23, tzinfo=UTC))

    assert first is not None
    assert second == first
    assert first.availability == "available"
    assert first.session == date(2026, 8, 25)
    assert first.component_row_counts == {"direction": 900}
    assert len(observations) == 3
    artifact = pq.read_table(first.prediction_path).to_pandas()
    assert set(artifact["symbol"]) == set(SEED_UNIVERSE)
    assert set(artifact["evidence_scope"]) == {"formal"}
    assert set(artifact["package_sha256"]) == {"a" * 64}
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["prediction_sha256"] == first.prediction_sha256
    assert (
        load_m7_formal_inference_result(
            paths, binding=binding, session=date(2026, 8, 25)
        )
        == first
    )


def test_formal_runner_rejects_incomplete_current_universe_without_stale_substitution(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    binding = M7FormalBundleBinding(
        forecast_bundle_id="m7-forecast-active",
        bundle_sha256="c" * 64,
        components=(_component(),),
    )
    incomplete = _predictions()
    incomplete = incomplete[incomplete["symbol"] != SEED_UNIVERSE[-1]]
    runner = M7FormalInferenceRunner(
        paths,
        bundle_provider=lambda: binding,
        prediction_provider=lambda _component: (date(2026, 8, 25), incomplete),
        integrity_observer=lambda _observation, _generated_at: None,
    )

    result = runner.run(generated_at=datetime(2026, 8, 25, 22, tzinfo=UTC))

    assert result is not None
    assert result.availability == "unavailable"
    assert result.reason == "m7_formal_direction_sample_keys_invalid"
    assert result.prediction_path is None
    assert not list((paths.models / "m7-formal" / "predictions").rglob("*.parquet"))


def test_formal_loader_rejects_manifest_path_escape(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    binding = M7FormalBundleBinding(
        forecast_bundle_id="m7-forecast-active",
        bundle_sha256="c" * 64,
        components=(_component(),),
    )
    manifest = (
        paths.models
        / "m7-formal"
        / "predictions"
        / "2026-08-25"
        / f"{binding.bundle_sha256}.manifest.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "forecast_bundle_id": binding.forecast_bundle_id,
                "bundle_sha256": binding.bundle_sha256,
                "component_row_counts": {"direction": 900},
                "prediction_path": "../outside.parquet",
                "prediction_sha256": "d" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(M7FormalInferenceError, match="prediction_path_invalid"):
        load_m7_formal_inference_result(
            paths, binding=binding, session=date(2026, 8, 25)
        )
