from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Barrier, Lock
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import stock_prediction.live.m7_direction_executor as direction_executor

from stock_prediction.features.m7 import (
    M7_CORE_V2_COLUMNS,
    M7_CROSS_SECTION_COLUMNS,
    M7_STRUCTURE_CONTEXT_COLUMNS,
)
from stock_prediction.live.m7_direction_executor import (
    M7DirectionTrialContext,
    M7DirectionExecutionOptions,
    _build_deep_trial_request,
    _limited_configurations,
    _make_sequence_batch,
    _feature_ablation_observations,
    _tree_candidates,
    direction_candidate_work_units,
)
from stock_prediction.live.artifact_references import ArtifactReference
from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V7,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.modeling.m7_recipe import registered_m7_candidate_matrix
from stock_prediction.modeling.m7_development import M7DirectionCandidatePredictions


def _partition(start: date) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    numeric_features = tuple(
        dict.fromkeys(
            (
                *M7_CORE_V2_COLUMNS,
                *M7_CROSS_SECTION_COLUMNS,
                *M7_STRUCTURE_CONTEXT_COLUMNS,
            )
        )
    )
    for session_index in range(12):
        session = start + timedelta(days=session_index)
        for symbol_index in range(10):
            row: dict[str, object] = {
                "symbol": f"US.S{symbol_index:02d}",
                "session_date": session,
                "eligible": True,
                "asset_type": "stock",
                "sector": "test",
                "regime": "range",
            }
            for feature_index, name in enumerate(numeric_features):
                row[name] = 0.01 * (feature_index + 1) + 0.001 * session_index
            for horizon in (1, 5, 20):
                row[f"direction_{horizon}"] = (-1, 0, 1)[
                    (session_index + symbol_index) % 3
                ]
                row[f"return_fwd_{horizon}"] = row[f"direction_{horizon}"] * 0.01
            rows.append(row)
    return (
        pd.DataFrame(rows)
        .sort_values(["session_date", "symbol"])
        .reset_index(drop=True)
    )


def test_tree_candidate_adapter_runs_registered_catboost_and_quantile_configs() -> None:
    matrix = registered_m7_candidate_matrix()
    configurations = (matrix.configurations[0], matrix.configurations[12])
    partitions = {
        "train": _partition(date(2024, 1, 1)),
        "selection": _partition(date(2024, 2, 1)),
        "calibration": _partition(date(2024, 3, 1)),
        "test": _partition(date(2024, 4, 1)),
    }

    candidates, failures = _tree_candidates(
        configurations,
        partitions,
        horizon=5,
        options=M7DirectionExecutionOptions(
            synthetic_tree_iteration_cap=1,
            candidate_timeout_seconds=30,
            bootstrap_draws=10,
        ),
    )

    assert failures == []
    assert [row.family for row in candidates] == [
        "catboost_multiclass",
        "quantile_cdf",
    ]
    assert all(row.calibration_probabilities.shape == (120, 3) for row in candidates)
    assert all(row.test_probabilities.shape == (120, 3) for row in candidates)
    assert all(row.deterministic is False for row in candidates)


def test_direction_candidate_work_plan_is_280_units_for_four_outer_folds() -> None:
    matrix = registered_m7_candidate_matrix()

    assert direction_candidate_work_units(matrix.configurations, outer_fold_count=4) == 280


def test_full_daily_catboost_uses_only_governed_categorical_features() -> None:
    matrix = registered_m7_candidate_matrix(
        recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V7
    )
    configuration = next(
        row
        for row in matrix.configurations
        if row.configuration_id == "m7-catboost_multiclass-07"
    )
    partitions = {
        "train": _partition(date(2024, 1, 1)),
        "selection": _partition(date(2024, 2, 1)),
        "calibration": _partition(date(2024, 3, 1)),
        "test": _partition(date(2024, 4, 1)),
    }
    for frame in partitions.values():
        frame["spy_state"] = "risk_on"
        frame["qqq_state"] = "risk_off"

    candidates, failures = _tree_candidates(
        (configuration,),
        partitions,
        horizon=5,
        options=M7DirectionExecutionOptions(
            synthetic_tree_iteration_cap=1,
            candidate_timeout_seconds=30,
            bootstrap_draws=10,
        ),
    )

    assert failures == []
    assert [row.candidate_id for row in candidates] == ["m7-catboost_multiclass-07"]


def test_tree_candidate_adapter_emits_each_fitted_candidate_to_artifact_writer() -> (
    None
):
    matrix = registered_m7_candidate_matrix()
    configurations = (matrix.configurations[0], matrix.configurations[12])
    partitions = {
        "train": _partition(date(2024, 1, 1)),
        "selection": _partition(date(2024, 2, 1)),
        "calibration": _partition(date(2024, 3, 1)),
        "test": _partition(date(2024, 4, 1)),
    }
    emitted: list[tuple[str, int]] = []

    candidates, failures = _tree_candidates(
        configurations,
        partitions,
        horizon=5,
        options=M7DirectionExecutionOptions(
            synthetic_tree_iteration_cap=1,
            candidate_timeout_seconds=30,
            bootstrap_draws=10,
        ),
        artifact_writer=lambda candidate, _train, horizon: emitted.append(
            (candidate.configuration_id, horizon)
        ),
    )

    assert failures == []
    assert len(candidates) == 2
    assert emitted == [
        ("m7-catboost_multiclass-01", 5),
        ("m7-quantile_cdf-01", 5),
    ]


def test_tree_candidate_adapter_reports_started_and_finished_candidates() -> None:
    matrix = registered_m7_candidate_matrix()
    configurations = (matrix.configurations[0], matrix.configurations[12])
    partitions = {
        "train": _partition(date(2024, 1, 1)),
        "selection": _partition(date(2024, 2, 1)),
        "calibration": _partition(date(2024, 3, 1)),
        "test": _partition(date(2024, 4, 1)),
    }
    events: list[tuple[str, str]] = []

    candidates, failures = _tree_candidates(
        configurations,
        partitions,
        horizon=5,
        options=M7DirectionExecutionOptions(
            synthetic_tree_iteration_cap=1,
            candidate_timeout_seconds=30,
            bootstrap_draws=10,
        ),
        candidate_started=lambda configuration: events.append(
            ("started", configuration.configuration_id)
        ),
        candidate_finished=lambda configuration: events.append(
            ("finished", configuration.configuration_id)
        ),
    )

    assert failures == []
    assert len(candidates) == 2
    assert events == [
        ("started", "m7-catboost_multiclass-01"),
        ("finished", "m7-catboost_multiclass-01"),
        ("started", "m7-quantile_cdf-01"),
        ("finished", "m7-quantile_cdf-01"),
    ]


def test_tree_candidate_adapter_runs_two_catboost_trials_concurrently_and_keeps_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = registered_m7_candidate_matrix(
        recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V7
    )
    configurations = tuple(
        row for row in matrix.configurations if row.family == "catboost_multiclass"
    )[:2]
    partitions = {
        "train": _partition(date(2024, 1, 1)),
        "selection": _partition(date(2024, 2, 1)),
        "calibration": _partition(date(2024, 3, 1)),
        "test": _partition(date(2024, 4, 1)),
    }
    barrier = Barrier(2, timeout=2)
    lock = Lock()
    active = 0
    peak_active = 0

    def fit_candidate(**kwargs: object) -> SimpleNamespace:
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        try:
            barrier.wait()
            time.sleep(0.02)
            return SimpleNamespace(
                configuration_id=str(kwargs["configuration_id"]),
                promotion_eligible=True,
            )
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(direction_executor, "fit_m7_catboost_candidate", fit_candidate)
    monkeypatch.setattr(
        direction_executor,
        "predict_m7_catboost",
        lambda _candidate, frame: np.full((len(frame), 3), 1 / 3),
    )

    candidates, failures = _tree_candidates(
        configurations,
        partitions,
        horizon=5,
        options=M7DirectionExecutionOptions(
            synthetic_tree_iteration_cap=1,
            candidate_timeout_seconds=30,
            catboost_parallel_trials=2,
            bootstrap_draws=10,
        ),
    )

    assert failures == []
    assert peak_active == 2
    assert [row.candidate_id for row in candidates] == [
        "m7-catboost_multiclass-01",
        "m7-catboost_multiclass-02",
    ]


def test_feature_ablation_reuses_main_cross_sectional_candidate_and_batches_remainder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = registered_m7_candidate_matrix(
        recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V7
    )
    partitions = {
        "train": _partition(date(2024, 1, 1)),
        "selection": _partition(date(2024, 2, 1)),
        "calibration": _partition(date(2024, 3, 1)),
        "test": _partition(date(2024, 4, 1)),
    }
    main_candidate = M7DirectionCandidatePredictions(
        candidate_id="m7-catboost_multiclass-01",
        family="catboost_multiclass",
        calibration_probabilities=np.full((120, 3), 1 / 3),
        test_probabilities=np.full((120, 3), 1 / 3),
        deterministic=True,
    )

    def batched_candidates(
        configurations: object,
        _partitions: object,
        **_kwargs: object,
    ) -> tuple[list[M7DirectionCandidatePredictions], list[object]]:
        rows = tuple(configurations)  # type: ignore[arg-type]
        assert [row.feature_group for row in rows] == [
            "core_v2",
            "core_v2_structure_context",
            "full_daily",
        ]
        return (
            [
                M7DirectionCandidatePredictions(
                    candidate_id="m7-catboost_multiclass-01",
                    family="catboost_multiclass",
                    calibration_probabilities=np.full((120, 3), 1 / 3),
                    test_probabilities=np.full((120, 3), 1 / 3),
                    deterministic=True,
                )
                for _row in rows
            ],
            [],
        )

    monkeypatch.setattr(direction_executor, "_tree_candidates", batched_candidates)

    observations = _feature_ablation_observations(
        matrix,
        fold_id="fold-1",
        partitions=partitions,
        horizon=5,
        options=M7DirectionExecutionOptions(),
        intraday_eligible=False,
        main_cross_sectional_candidate=main_candidate,
    )

    assert [row.feature_set for row in observations] == [
        "core_v2",
        "core_v2_cross_sectional",
        "core_v2_structure_context",
        "full_daily",
    ]


def test_tree_candidate_adapter_checks_cancellation_after_atomic_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = registered_m7_candidate_matrix(
        recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V7
    )
    configurations = tuple(
        row for row in matrix.configurations if row.family == "quantile_cdf"
    )[:2]
    partitions = {
        "train": _partition(date(2024, 1, 1)),
        "selection": _partition(date(2024, 2, 1)),
        "calibration": _partition(date(2024, 3, 1)),
        "test": _partition(date(2024, 4, 1)),
    }
    fitted: list[str] = []

    def fit_candidate(**kwargs: object) -> SimpleNamespace:
        configuration_id = str(kwargs["configuration_id"])
        fitted.append(configuration_id)
        return SimpleNamespace(
            configuration_id=configuration_id,
            promotion_eligible=True,
        )

    monkeypatch.setattr(
        direction_executor, "fit_m7_quantile_cdf_candidate", fit_candidate
    )
    monkeypatch.setattr(
        direction_executor,
        "predict_m7_quantile_cdf",
        lambda _candidate, frame, **_kwargs: np.full((len(frame), 3), 1 / 3),
    )

    def stop_after_first_candidate() -> None:
        raise RuntimeError("cancel_after_atomic_candidate")

    with pytest.raises(RuntimeError, match="cancel_after_atomic_candidate"):
        _tree_candidates(
            configurations,
            partitions,
            horizon=5,
            options=M7DirectionExecutionOptions(
                synthetic_tree_iteration_cap=1,
                candidate_timeout_seconds=30,
                bootstrap_draws=10,
            ),
            cancellation_check=stop_after_first_candidate,
        )

    assert fitted == ["m7-quantile_cdf-01"]


def test_sequence_adapter_uses_past_only_windows_and_horizon_weights() -> None:
    history = _partition(date(2024, 1, 1))
    partition = history[history["session_date"] >= date(2024, 1, 8)].copy()

    batch = _make_sequence_batch(
        history,
        partition,
        feature_names=("return_1", "return_20", "volatility_20"),
        sequence_length=4,
        weighted=True,
    )

    assert batch.sequences.shape == (50, 4, 3)
    assert all(
        window[-1] == key[1]
        for window, key in zip(batch.input_sessions, batch.sample_keys, strict=True)
    )
    assert all(len(window) == 4 for window in batch.input_sessions)
    assert np.allclose(batch.weights.mean(dim=0).numpy(), np.ones(3), atol=1e-6)


def test_sequence_adapter_can_trim_only_the_training_history_prefix() -> None:
    history = _partition(date(2024, 1, 1))

    batch = _make_sequence_batch(
        history,
        history,
        feature_names=("return_1", "return_20", "volatility_20"),
        sequence_length=4,
        weighted=True,
        trim_insufficient_history=True,
    )

    assert batch.sequences.shape == (90, 4, 3)
    assert min(key[1] for key in batch.sample_keys) == date(2024, 1, 4)


def test_smoke_limit_keeps_one_candidate_per_family_and_sequence_length() -> None:
    limited = _limited_configurations(
        registered_m7_candidate_matrix().configurations,
        per_family_sequence_limit=1,
    )

    assert [row.configuration_id for row in limited] == [
        "m7-catboost_multiclass-01",
        "m7-quantile_cdf-01",
        "m7-dlinear-01",
        "m7-dlinear-05",
        "m7-patchtst-01",
        "m7-patchtst-05",
    ]


def test_deep_trial_request_binds_v8_recipe_fold_and_batch_identity(
    tmp_path: Path,
) -> None:
    matrix = registered_m7_candidate_matrix(
        recipe_id="candidate-recipe-seed100-targeted-v9"
    )
    configuration = next(
        row for row in matrix.configurations if row.configuration_id == "m7-dlinear-01"
    )
    fold = SimpleNamespace(
        fold_id="m7-outer-01",
        partition_hash="a" * 64,
        train_sessions=(date(2020, 1, 2), date(2020, 1, 3)),
        selection_sessions=(date(2020, 2, 3),),
        calibration_sessions=(date(2020, 3, 2),),
        outer_test_sessions=(date(2020, 4, 1),),
    )
    context = M7DirectionTrialContext(
        paths=LivePaths.from_workspace(tmp_path),
        run_id="run-1",
        feature_reference=ArtifactReference(
            artifact_id="features",
            relative_path="models/runs/run-1/executor/dataset/v5-features.parquet",
            sha256="b" * 64,
            schema_version="m7-v5-feature-frame-v1",
            created_at=datetime(2026, 8, 24, tzinfo=UTC),
        ),
        dependency_lock_sha256="c" * 64,
        code_identity_sha256="d" * 64,
        supervisor=SimpleNamespace(),
    )

    request = _build_deep_trial_request(
        configuration,
        matrix=matrix,
        fold=fold,
        context=context,
        options=M7DirectionExecutionOptions(candidate_timeout_seconds=60),
    )

    assert request.recipe_id == "candidate-recipe-seed100-targeted-v9"
    assert request.batch_size == 64
    assert request.configuration_id == configuration.configuration_id
    assert request.partition_hash == fold.partition_hash
    assert request.trial_id == "run-1.m7-outer-01.m7-dlinear-01"


def test_v10_deep_trial_request_requires_cuda(tmp_path: Path) -> None:
    matrix = registered_m7_candidate_matrix(
        recipe_id="candidate-recipe-seed100-targeted-v10"
    )
    configuration = next(
        row for row in matrix.configurations if row.configuration_id == "m7-dlinear-01"
    )
    fold = SimpleNamespace(
        fold_id="m7-outer-01",
        partition_hash="a" * 64,
        train_sessions=(date(2020, 1, 2), date(2020, 1, 3)),
        selection_sessions=(date(2020, 2, 3),),
        calibration_sessions=(date(2020, 3, 2),),
        outer_test_sessions=(date(2020, 4, 1),),
    )
    context = M7DirectionTrialContext(
        paths=LivePaths.from_workspace(tmp_path),
        run_id="run-1",
        feature_reference=ArtifactReference(
            artifact_id="features",
            relative_path="models/runs/run-1/executor/dataset/v5-features.parquet",
            sha256="b" * 64,
            schema_version="m7-v5-feature-frame-v1",
            created_at=datetime(2026, 8, 24, tzinfo=UTC),
        ),
        dependency_lock_sha256="c" * 64,
        code_identity_sha256="d" * 64,
        supervisor=SimpleNamespace(),
        recipe_id="candidate-recipe-seed100-targeted-v10",
    )

    request = _build_deep_trial_request(
        configuration,
        matrix=matrix,
        fold=fold,
        context=context,
        options=M7DirectionExecutionOptions(candidate_timeout_seconds=60),
    )

    assert request.recipe_id == "candidate-recipe-seed100-targeted-v10"
    assert request.device == "cuda"
