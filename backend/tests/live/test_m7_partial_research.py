from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_partial_research import (
    M7PartialFoldManifest,
    M7PartialResearchService,
)
from stock_prediction.universe.seed import SEED_UNIVERSE


def _direction_manifest(*, fold_id: str = "fold-01") -> M7PartialFoldManifest:
    return M7PartialFoldManifest(
        run_id="model-training-v10-test",
        recipe_id="candidate-recipe-seed100-targeted-v10",
        bundle_id="direction",
        fold_id=fold_id,
        as_of=date(2026, 6, 29),
        universe_version="m1-seed-2026-07-11-v1",
        dataset_revision="opend-live-market-bars-pit-adjusted-v2",
        feature_set_version="live-daily-features-v2",
        feature_input_hash="a" * 64,
        candidate_matrix_hash="b" * 64,
        selected_components=("m7-catboost_multiclass-01",),
        training_devices=("cuda:0", "cpu"),
        predictions=tuple(
            {
                "symbol": symbol,
                "horizon": horizon,
                "down_probability": 0.20,
                "flat_probability": 0.30,
                "up_probability": 0.50,
            }
            for symbol in SEED_UNIVERSE
            for horizon in (1, 5, 20)
        ),
        source_result_hashes=("c" * 64,),
    )


def test_partial_research_publishes_and_aggregates_a_verified_direction_fold(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    service = M7PartialResearchService(paths)

    published = service.publish(_direction_manifest())
    result = service.result(
        run_id="model-training-v10-test",
        bundle_id="direction",
        symbol="US.AAPL",
    )

    assert published.manifest_hash
    assert result.availability == "available"
    assert result.contributing_folds == ("fold-01",)
    assert result.required_folds == 4
    assert result.predictions[0].symbol == "US.AAPL"
    assert result.predictions[0].up_probability == 0.50


def test_partial_research_rejects_direction_probabilities_that_do_not_conserve() -> None:
    payload = _direction_manifest().model_dump(mode="python")
    payload["predictions"] = (
        {
            "symbol": "US.AAPL",
            "horizon": 1,
            "down_probability": 0.2,
            "flat_probability": 0.3,
            "up_probability": 0.4,
        },
    )
    with pytest.raises(ValueError, match="m7_partial_direction_probability_invalid"):
        M7PartialFoldManifest.model_validate(payload, strict=True)
