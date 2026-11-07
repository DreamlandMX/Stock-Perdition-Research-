from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import subprocess
import sys

import pytest

import stock_prediction.live.candidate_recipes as candidate_recipes

from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V4,
    CANDIDATE_RECIPE_SEED100_TARGETED_V5,
    CANDIDATE_RECIPE_SEED100_TARGETED_V10,
    CANDIDATE_RECIPE_UNIVERSE204_V1,
    candidate_recipe,
    next_candidate_recipe_version,
)
from stock_prediction.modeling.m7_recipe import (
    M7DevelopmentEvidence,
    M7RecipeEvidenceConsumedError,
    M7RecipeEvidenceLedger,
    registered_m7_candidate_matrix,
)
from stock_prediction.universe.seed import SEED_UNIVERSE_VERSION


def test_generation_four_recipe_has_the_exact_finite_family_limits() -> None:
    recipe = candidate_recipe(CANDIDATE_RECIPE_SEED100_TARGETED_V4)
    matrix = registered_m7_candidate_matrix()

    assert recipe.generation == 4
    assert recipe.targeted_only is True
    assert matrix.recipe_id == CANDIDATE_RECIPE_SEED100_TARGETED_V4
    assert matrix.recipe_generation == 4
    assert matrix.universe_version == SEED_UNIVERSE_VERSION
    assert matrix.feature_set_version == "live-daily-features-v5-seed100"
    assert matrix.label_version == "live-labels-v2"
    assert matrix.direction_horizons == (1, 5, 20)
    assert len(matrix.configurations) == 34

    family_counts: dict[str, int] = {}
    for configuration in matrix.configurations:
        family_counts[configuration.family] = (
            family_counts.get(configuration.family, 0) + 1
        )
    assert family_counts == {
        "catboost_multiclass": 12,
        "quantile_cdf": 6,
        "dlinear": 8,
        "patchtst": 8,
    }
    assert len({row.configuration_id for row in matrix.configurations}) == 34


def test_deep_matrix_counts_seeds_inside_the_registered_limits() -> None:
    matrix = registered_m7_candidate_matrix()
    dlinear = [row for row in matrix.configurations if row.family == "dlinear"]
    patchtst = [row for row in matrix.configurations if row.family == "patchtst"]

    assert {int(row.parameters["sequence_length"]) for row in dlinear} == {64, 128}
    assert {row.seed for row in dlinear} == {17, 29}
    assert all(int(row.parameters["max_epochs"]) == 80 for row in dlinear)
    assert all(int(row.parameters["patience"]) == 10 for row in dlinear)
    assert {int(row.parameters["sequence_length"]) for row in patchtst} == {64, 128}
    assert {int(row.parameters["patch_length"]) for row in patchtst} == {8, 16}
    assert {row.seed for row in patchtst} == {17, 29}
    assert all(int(row.parameters["attention_heads"]) == 4 for row in patchtst)
    assert all(int(row.parameters["max_epochs"]) == 80 for row in patchtst)


def test_generation_five_registers_isolated_deep_batch_without_mutating_v4() -> None:
    v4 = registered_m7_candidate_matrix(recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V4)
    v5 = registered_m7_candidate_matrix(recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V5)

    assert candidate_recipe(CANDIDATE_RECIPE_SEED100_TARGETED_V5).generation == 5
    assert v5.recipe_generation == 5
    assert v5.matrix_hash != v4.matrix_hash
    assert len(v5.configurations) == len(v4.configurations) == 34
    assert all(
        "batch_size" not in row.parameters
        for row in v4.configurations
        if row.family in {"dlinear", "patchtst"}
    )
    assert all(
        row.parameters["batch_size"] == 64
        and row.parameters["execution_policy"] == "isolated_child_v1"
        for row in v5.configurations
        if row.family in {"dlinear", "patchtst"}
    )


def test_generation_six_adds_true_brier_early_stopping_without_shrinking_matrix() -> (
    None
):
    assert hasattr(candidate_recipes, "CANDIDATE_RECIPE_SEED100_TARGETED_V6")
    v6_recipe_id = candidate_recipes.CANDIDATE_RECIPE_SEED100_TARGETED_V6
    v5 = registered_m7_candidate_matrix(recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V5)
    v6 = registered_m7_candidate_matrix(recipe_id=v6_recipe_id)

    assert candidate_recipe(v6_recipe_id).generation == 6
    assert v6.recipe_generation == 6
    assert len(v6.configurations) == len(v5.configurations) == 34
    assert v6.matrix_hash != v5.matrix_hash
    assert all(
        row.parameters["max_iterations"] == 1200
        and row.parameters["early_stopping_metric"] == "multiclass_brier"
        and row.parameters["early_stopping_rounds"] == 100
        for row in v6.configurations
        if row.family == "catboost_multiclass"
    )
    assert all(
        "early_stopping_rounds" not in row.parameters
        for row in v5.configurations
        if row.family == "catboost_multiclass"
    )


def test_generation_seven_registers_exact_linear_time_quantile_staging() -> None:
    assert hasattr(candidate_recipes, "CANDIDATE_RECIPE_SEED100_TARGETED_V7")
    v7_recipe_id = candidate_recipes.CANDIDATE_RECIPE_SEED100_TARGETED_V7
    v6_recipe_id = candidate_recipes.CANDIDATE_RECIPE_SEED100_TARGETED_V6
    v6 = registered_m7_candidate_matrix(recipe_id=v6_recipe_id)
    v7 = registered_m7_candidate_matrix(recipe_id=v7_recipe_id)

    assert candidate_recipe(v7_recipe_id).generation == 7
    assert v7.recipe_generation == 7
    assert len(v7.configurations) == len(v6.configurations) == 34
    assert v7.matrix_hash != v6.matrix_hash
    assert all(
        row.parameters["staged_prediction_policy"] == "leaf_path_cumsum_v1"
        for row in v7.configurations
        if row.family == "quantile_cdf"
    )
    assert all(
        "staged_prediction_policy" not in row.parameters
        for row in v6.configurations
        if row.family == "quantile_cdf"
    )
    v6_quantiles = {
        row.configuration_id: row
        for row in v6.configurations
        if row.family == "quantile_cdf"
    }
    for v7_row in (row for row in v7.configurations if row.family == "quantile_cdf"):
        v6_payload = v6_quantiles[v7_row.configuration_id].canonical_payload()
        v7_payload = v7_row.canonical_payload()
        assert isinstance(v7_payload["parameters"], dict)
        v7_payload["parameters"].pop("staged_prediction_policy")
        assert v7_payload == v6_payload
    assert [
        row.canonical_payload()
        for row in v7.configurations
        if row.family != "quantile_cdf"
    ] == [
        row.canonical_payload()
        for row in v6.configurations
        if row.family != "quantile_cdf"
    ]


def test_generation_eight_registers_exact_quantile_mapping_policy() -> None:
    assert hasattr(candidate_recipes, "CANDIDATE_RECIPE_SEED100_TARGETED_V8")
    v8_recipe_id = candidate_recipes.CANDIDATE_RECIPE_SEED100_TARGETED_V8
    v7_recipe_id = candidate_recipes.CANDIDATE_RECIPE_SEED100_TARGETED_V7
    v7 = registered_m7_candidate_matrix(recipe_id=v7_recipe_id)
    v8 = registered_m7_candidate_matrix(recipe_id=v8_recipe_id)

    assert candidate_recipe(v8_recipe_id).generation == 8
    assert v8.recipe_generation == 8
    assert len(v8.configurations) == len(v7.configurations) == 34
    assert v8.matrix_hash != v7.matrix_hash
    assert all(
        row.parameters["staged_prediction_policy"] == "leaf_path_cumsum_v1"
        and row.parameters["quantile_mapping_policy"]
        == "scipy_isotonic_compact_interp_v1"
        for row in v8.configurations
        if row.family == "quantile_cdf"
    )
    assert all(
        "quantile_mapping_policy" not in row.parameters
        for row in v7.configurations
        if row.family == "quantile_cdf"
    )
    v7_quantiles = {
        row.configuration_id: row
        for row in v7.configurations
        if row.family == "quantile_cdf"
    }
    for v8_row in (row for row in v8.configurations if row.family == "quantile_cdf"):
        v7_payload = v7_quantiles[v8_row.configuration_id].canonical_payload()
        v8_payload = v8_row.canonical_payload()
        assert isinstance(v8_payload["parameters"], dict)
        v8_payload["parameters"].pop("quantile_mapping_policy")
        assert v8_payload == v7_payload
    assert [
        row.canonical_payload()
        for row in v8.configurations
        if row.family != "quantile_cdf"
    ] == [
        row.canonical_payload()
        for row in v7.configurations
        if row.family != "quantile_cdf"
    ]


def test_generation_nine_adds_only_controlled_quantile_concurrency() -> None:
    assert hasattr(candidate_recipes, "CANDIDATE_RECIPE_SEED100_TARGETED_V9")
    v9_recipe_id = candidate_recipes.CANDIDATE_RECIPE_SEED100_TARGETED_V9
    v8_recipe_id = candidate_recipes.CANDIDATE_RECIPE_SEED100_TARGETED_V8
    v8 = registered_m7_candidate_matrix(recipe_id=v8_recipe_id)
    v9 = registered_m7_candidate_matrix(recipe_id=v9_recipe_id)

    assert candidate_recipe(v9_recipe_id).generation == 9
    assert v9.recipe_generation == 9
    assert len(v9.configurations) == len(v8.configurations) == 34
    assert v9.matrix_hash != v8.matrix_hash
    expected_delta = {
        "fit_execution_policy": "lightgbm_threadpool_ordered_v1",
        "fit_parallel_workers": 4,
        "selection_execution_policy": "joblib_loky_ordered_chunks_v1",
        "selection_parallel_workers": 8,
    }
    v8_quantiles = {
        row.configuration_id: row
        for row in v8.configurations
        if row.family == "quantile_cdf"
    }
    for v9_row in (row for row in v9.configurations if row.family == "quantile_cdf"):
        assert {
            name: v9_row.parameters[name] for name in expected_delta
        } == expected_delta
        v8_payload = v8_quantiles[v9_row.configuration_id].canonical_payload()
        v9_payload = v9_row.canonical_payload()
        assert isinstance(v9_payload["parameters"], dict)
        for name in expected_delta:
            v9_payload["parameters"].pop(name)
        assert v9_payload == v8_payload
    assert [
        row.canonical_payload()
        for row in v9.configurations
        if row.family != "quantile_cdf"
    ] == [
        row.canonical_payload()
        for row in v8.configurations
        if row.family != "quantile_cdf"
    ]


def test_generation_ten_preserves_the_v9_candidate_matrix_for_gpu_execution() -> None:
    v9 = registered_m7_candidate_matrix(recipe_id="candidate-recipe-seed100-targeted-v9")
    v10 = registered_m7_candidate_matrix(recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V10)

    assert candidate_recipe(CANDIDATE_RECIPE_SEED100_TARGETED_V10).generation == 10
    assert v10.recipe_generation == 10
    assert len(v10.configurations) == len(v9.configurations) == 34
    assert [row.canonical_payload() for row in v10.configurations] == [
        row.canonical_payload() for row in v9.configurations
    ]


def test_matrix_hash_is_stable_across_a_fresh_process() -> None:
    expected = registered_m7_candidate_matrix().matrix_hash
    command = (
        "from stock_prediction.modeling.m7_recipe import "
        "registered_m7_candidate_matrix; "
        "print(registered_m7_candidate_matrix().matrix_hash)"
    )

    completed = subprocess.run(
        [sys.executable, "-c", command],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == expected


def test_targeted_recipe_does_not_extend_the_legacy_automatic_sequence() -> None:
    assert next_candidate_recipe_version(CANDIDATE_RECIPE_UNIVERSE204_V1) is None


def test_inspected_recipe_evidence_cannot_be_reserved_again(tmp_path: Path) -> None:
    ledger = M7RecipeEvidenceLedger(tmp_path / "recipe-evidence.json")
    matrix = registered_m7_candidate_matrix()
    evidence = M7DevelopmentEvidence(
        recipe_id=matrix.recipe_id,
        recipe_generation=matrix.recipe_generation,
        candidate_matrix_hash=matrix.matrix_hash,
        data_cutoff=date(2026, 8, 21),
        dataset_revision="bars-r7",
        feature_input_hash="a" * 64,
        universe_version=SEED_UNIVERSE_VERSION,
    )

    reservation = ledger.reserve(evidence, run_id="run-1")
    assert reservation.state == "reserved"
    assert ledger.reserve(evidence, run_id="run-1") == reservation

    inspected = ledger.mark_inspected(evidence, run_id="run-1")
    assert inspected.state == "inspected"
    with pytest.raises(M7RecipeEvidenceConsumedError, match="already_inspected"):
        ledger.reserve(evidence, run_id="run-2")

    payload = json.loads((tmp_path / "recipe-evidence.json").read_text("utf-8"))
    assert payload["records"][0]["evidence_hash"] == evidence.evidence_hash
    assert payload["records"][0]["state"] == "inspected"
