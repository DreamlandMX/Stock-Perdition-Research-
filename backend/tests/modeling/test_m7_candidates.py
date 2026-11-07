from __future__ import annotations

from datetime import date
from itertools import product
from threading import Barrier, Lock
from time import sleep
import warnings

import numpy as np
import pandas as pd
import pytest
from lightgbm import LGBMRegressor

import stock_prediction.modeling.m7_candidates as m7_candidates
from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V6,
    CANDIDATE_RECIPE_SEED100_TARGETED_V7,
    CANDIDATE_RECIPE_SEED100_TARGETED_V8,
    CANDIDATE_RECIPE_SEED100_TARGETED_V9,
)

from stock_prediction.modeling.m7_candidates import (
    M7CandidateTerminalError,
    fit_m7_catboost_candidate,
    fit_m7_quantile_cdf_candidate,
    map_quantile_cdf_direction,
    predict_m7_catboost,
    predict_m7_quantile_cdf,
    registered_m7_frozen_baselines,
    replay_m7_frozen_baselines,
    resolve_m7_candidate_configuration,
)
from stock_prediction.modeling.m7_recipe import registered_m7_candidate_matrix


CLASSES = ("down", "flat", "up")
FEATURES = ("return_20", "volatility_20", "asset_type", "sector", "regime")
CATEGORICAL = ("asset_type", "sector", "regime")


def _classification_frame(count: int = 90) -> tuple[pd.DataFrame, tuple[str, ...]]:
    labels = tuple(CLASSES[index % 3] for index in range(count))
    frame = pd.DataFrame(
        {
            "return_20": [(-1 + index % 3) * 0.04 for index in range(count)],
            "volatility_20": [0.1 + (index % 7) * 0.01 for index in range(count)],
            "asset_type": [
                "etf" if index % 5 == 0 else "stock" for index in range(count)
            ],
            "sector": [
                "technology" if index % 2 else "financials" for index in range(count)
            ],
            "regime": ["downtrend", "range", "uptrend"] * (count // 3),
        }
    )
    return frame, labels


def test_every_registered_tree_configuration_resolves_without_expanding_matrix() -> (
    None
):
    matrix = registered_m7_candidate_matrix()
    tree_rows = [
        row
        for row in matrix.configurations
        if row.family in {"catboost_multiclass", "quantile_cdf"}
    ]

    assert len(tree_rows) == 18
    assert all(
        resolve_m7_candidate_configuration(row.configuration_id) == row
        for row in tree_rows
    )
    with pytest.raises(M7CandidateTerminalError, match="unregistered_configuration"):
        resolve_m7_candidate_configuration("m7-catboost_multiclass-99")


def test_catboost_adapter_uses_governed_categories_weights_and_raw_brier_selection() -> (
    None
):
    train, labels = _classification_frame()
    selection, selection_labels = _classification_frame(30)
    candidate = fit_m7_catboost_candidate(
        configuration_id="m7-catboost_multiclass-01",
        train_features=train,
        train_labels=labels,
        train_weights=np.linspace(0.5, 1.5, len(train)),
        selection_features=selection,
        selection_labels=selection_labels,
        feature_names=FEATURES,
        categorical_feature_names=CATEGORICAL,
        classes=CLASSES,
        synthetic_iteration_cap=12,
        timeout_seconds=60,
        recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V6,
    )
    probabilities = predict_m7_catboost(candidate, selection)

    assert candidate.configuration_id == "m7-catboost_multiclass-01"
    assert candidate.boosting_type == "Ordered"
    assert candidate.thread_count == 4
    assert candidate.best_iteration <= 12
    assert candidate.selection_metric == "multiclass_brier"
    assert candidate.promotion_eligible is False
    assert candidate.ineligibility_reason == "synthetic_iteration_cap"
    assert probabilities.shape == (30, 3)
    assert np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-9)


def test_multiclass_brier_metric_scores_softmax_probabilities_row_equally() -> None:
    assert hasattr(m7_candidates, "M7MulticlassBrierMetric")
    metric = m7_candidates.M7MulticlassBrierMetric()
    approximations = (
        np.log(np.asarray([0.7, 0.1])),
        np.log(np.asarray([0.2, 0.2])),
        np.log(np.asarray([0.1, 0.7])),
    )

    error_sum, row_count = metric.evaluate(
        approximations,
        np.asarray([0.0, 2.0]),
        None,
    )

    assert metric.is_max_optimal() is False
    assert row_count == 2.0
    assert error_sum == pytest.approx(0.28)
    assert metric.get_final_error(error_sum, row_count) == pytest.approx(0.14)


def test_catboost_adapter_lets_validation_brier_stop_training(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ValidationStoppedClassifier:
        classes_ = np.asarray(CLASSES)
        tree_count_ = 7

        def __init__(self, **parameters: object) -> None:
            assert parameters["iterations"] == 1200
            assert parameters["use_best_model"] is True
            assert isinstance(
                parameters["eval_metric"],
                m7_candidates.M7MulticlassBrierMetric,
            )

        def fit(
            self,
            train_features: pd.DataFrame,
            train_labels: np.ndarray,
            **parameters: object,
        ) -> None:
            assert len(train_features) == len(train_labels) == 90
            assert parameters["early_stopping_rounds"] == 100
            selection_features, selection_labels = parameters["eval_set"]  # type: ignore[misc]
            assert len(selection_features) == len(selection_labels) == 30

        @staticmethod
        def get_best_iteration() -> int:
            return 6

        @staticmethod
        def predict_proba(features: pd.DataFrame) -> np.ndarray:
            return np.full((len(features), 3), 1 / 3)

        @staticmethod
        def staged_predict_proba(_features: pd.DataFrame) -> object:
            raise AssertionError("true early stopping must not replay all trees")

    monkeypatch.setattr(
        m7_candidates,
        "CatBoostClassifier",
        ValidationStoppedClassifier,
    )
    train, labels = _classification_frame()
    selection, selection_labels = _classification_frame(30)

    candidate = fit_m7_catboost_candidate(
        configuration_id="m7-catboost_multiclass-01",
        train_features=train,
        train_labels=labels,
        train_weights=np.ones(len(train)),
        selection_features=selection,
        selection_labels=selection_labels,
        feature_names=FEATURES,
        categorical_feature_names=CATEGORICAL,
        classes=CLASSES,
        timeout_seconds=60,
        recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V6,
    )

    assert candidate.best_iteration == 7
    assert candidate.selection_brier == pytest.approx(2 / 3)
    assert candidate.promotion_eligible is True


def test_catboost_rejects_symbol_identity_nan_and_terminal_timeout() -> None:
    train, labels = _classification_frame()
    selection, selection_labels = _classification_frame(30)
    train["symbol"] = "US.AAPL"
    with pytest.raises(M7CandidateTerminalError, match="categorical_contract"):
        fit_m7_catboost_candidate(
            configuration_id="m7-catboost_multiclass-01",
            train_features=train,
            train_labels=labels,
            train_weights=np.ones(len(train)),
            selection_features=selection.assign(symbol="US.AAPL"),
            selection_labels=selection_labels,
            feature_names=(*FEATURES, "symbol"),
            categorical_feature_names=(*CATEGORICAL, "symbol"),
            classes=CLASSES,
            synthetic_iteration_cap=4,
        )

    train, labels = _classification_frame()
    train.loc[0, "return_20"] = np.nan
    with pytest.raises(M7CandidateTerminalError, match="non_finite"):
        fit_m7_catboost_candidate(
            configuration_id="m7-catboost_multiclass-01",
            train_features=train,
            train_labels=labels,
            train_weights=np.ones(len(train)),
            selection_features=selection,
            selection_labels=selection_labels,
            feature_names=FEATURES,
            categorical_feature_names=CATEGORICAL,
            classes=CLASSES,
            synthetic_iteration_cap=4,
        )

    with pytest.raises(M7CandidateTerminalError, match="timeout"):
        fit_m7_catboost_candidate(
            configuration_id="m7-catboost_multiclass-01",
            train_features=_classification_frame()[0],
            train_labels=_classification_frame()[1],
            train_weights=np.ones(90),
            selection_features=selection,
            selection_labels=selection_labels,
            feature_names=FEATURES,
            categorical_feature_names=CATEGORICAL,
            classes=CLASSES,
            synthetic_iteration_cap=4,
            timeout_seconds=0,
        )


def test_quantile_projection_and_cdf_mapping_enforce_probability_invariants() -> None:
    crossed = np.array(
        [
            [0.02, -0.01, 0.00, 0.03, 0.01],
            [-0.04, -0.02, 0.00, 0.02, 0.04],
        ]
    )
    result = map_quantile_cdf_direction(
        crossed,
        quantile_levels=(0.10, 0.25, 0.50, 0.75, 0.90),
        direction_thresholds=(0.01, 0.01),
    )

    assert np.all(np.diff(result.projected_quantiles, axis=1) >= 0)
    assert np.all(result.probabilities >= 0)
    assert np.all(result.probabilities <= 1)
    assert np.allclose(result.probabilities.sum(axis=1), 1.0, atol=1e-9)


def test_v8_quantile_mapping_is_bit_exact_for_every_five_point_plateau() -> None:
    levels = np.asarray((0.10, 0.25, 0.50, 0.75, 0.90), dtype=float)
    rows: list[np.ndarray] = []
    thresholds: list[float] = []
    for breaks in product((False, True), repeat=4):
        group_ids = [0]
        for split in breaks:
            group_ids.append(group_ids[-1] + int(split))
        group_index = np.asarray(group_ids, dtype=int)
        group_count = int(group_index[-1]) + 1
        group_values = (
            np.asarray((0.0,))
            if group_count == 1
            else np.linspace(-0.04, 0.04, group_count)
        )
        row = group_values[group_index]
        unique_values = np.unique(row)
        cases = {0.0, 0.01, 0.02, 0.04, 0.05}
        for value in unique_values:
            magnitude = abs(float(value))
            cases.update(
                {
                    magnitude,
                    float(np.nextafter(magnitude, 0.0)),
                    float(np.nextafter(magnitude, np.inf)),
                }
            )
        for left, right in zip(unique_values[:-1], unique_values[1:], strict=True):
            cases.add(abs(float((left + right) / 2.0)))
        for threshold in sorted(value for value in cases if value >= 0.0):
            rows.append(row.copy())
            thresholds.append(threshold)
    matrix = np.asarray(rows, dtype=float)
    threshold_array = np.asarray(thresholds, dtype=float)

    reference = map_quantile_cdf_direction(
        matrix,
        quantile_levels=levels,
        direction_thresholds=threshold_array,
    )
    optimized = map_quantile_cdf_direction(
        matrix,
        quantile_levels=levels,
        direction_thresholds=threshold_array,
        mapping_policy="scipy_isotonic_compact_interp_v1",
    )

    assert len(matrix) == 155
    np.testing.assert_array_equal(
        optimized.projected_quantiles,
        reference.projected_quantiles,
    )
    np.testing.assert_array_equal(optimized.probabilities, reference.probabilities)


def test_v8_quantile_mapping_preserves_staged_brier_and_selection() -> None:
    rng = np.random.default_rng(20260828)
    levels = (0.10, 0.25, 0.50, 0.75, 0.90)
    thresholds = rng.uniform(0.0, 0.05, size=100)
    labels = np.asarray(CLASSES)[rng.integers(0, 3, size=100)]
    reference_scores: list[float] = []
    optimized_scores: list[float] = []
    for _ in range(20):
        matrix = rng.normal(0.0, 0.04, size=(100, 5))
        reference = map_quantile_cdf_direction(
            matrix,
            quantile_levels=levels,
            direction_thresholds=thresholds,
        )
        optimized = map_quantile_cdf_direction(
            matrix,
            quantile_levels=levels,
            direction_thresholds=thresholds,
            mapping_policy="scipy_isotonic_compact_interp_v1",
        )
        np.testing.assert_array_equal(optimized.probabilities, reference.probabilities)
        reference_scores.append(
            m7_candidates._row_equal_brier(labels, reference.probabilities, CLASSES)
        )
        optimized_scores.append(
            m7_candidates._row_equal_brier(labels, optimized.probabilities, CLASSES)
        )

    np.testing.assert_array_equal(reference_scores, optimized_scores)
    assert int(np.argmin(reference_scores)) == int(np.argmin(optimized_scores))


def test_v8_quantile_mapping_rejects_unknown_policy() -> None:
    with pytest.raises(M7CandidateTerminalError, match="mapping_policy_invalid"):
        map_quantile_cdf_direction(
            np.zeros((1, 5)),
            quantile_levels=(0.10, 0.25, 0.50, 0.75, 0.90),
            direction_thresholds=(0.01,),
            mapping_policy="unknown",
        )


def test_v9_quantile_fit_scheduler_is_bounded_and_returns_quantile_order() -> None:
    scheduler = getattr(m7_candidates, "_fit_quantile_models_ordered", None)
    assert scheduler is not None
    active = 0
    maximum_active = 0
    lock = Lock()
    first_batch = Barrier(4)

    def fit_one(index: int, quantile: float) -> tuple[int, float]:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        if index < 4:
            first_batch.wait(timeout=2)
        sleep((5 - index) * 0.002)
        with lock:
            active -= 1
        return index, quantile

    fitted = scheduler(
        (0.10, 0.25, 0.50, 0.75, 0.90),
        fit_one,
        execution_policy="lightgbm_threadpool_ordered_v1",
        parallel_workers=4,
    )

    assert fitted == (
        (0, 0.10),
        (1, 0.25),
        (2, 0.50),
        (3, 0.75),
        (4, 0.90),
    )
    assert maximum_active == 4


def test_v9_quantile_fit_scheduler_fails_closed_on_worker_crash() -> None:
    def fit_one(index: int, quantile: float) -> tuple[int, float]:
        if index == 2:
            raise RuntimeError("worker crashed")
        return index, quantile

    with pytest.raises(M7CandidateTerminalError, match="m7_candidate_fit_failed"):
        m7_candidates._fit_quantile_models_ordered(
            (0.10, 0.25, 0.50, 0.75, 0.90),
            fit_one,
            execution_policy="lightgbm_threadpool_ordered_v1",
            parallel_workers=4,
        )


def test_v9_parallel_quantile_selection_is_bit_exact_for_all_400_stages() -> None:
    selector = getattr(m7_candidates, "_select_quantile_iteration", None)
    assert selector is not None
    rng = np.random.default_rng(20260829)
    row_count = 24
    iteration_count = 400
    staged = tuple(
        np.cumsum(
            rng.normal(0.0, 0.002, size=(row_count, iteration_count)),
            axis=1,
        )
        for _ in range(5)
    )
    labels = tuple(CLASSES[index % 3] for index in range(row_count))
    thresholds = np.linspace(0.002, 0.03, row_count)
    common = {
        "staged_quantiles": staged,
        "selection_labels": labels,
        "direction_thresholds": thresholds,
        "classes": CLASSES,
        "quantile_levels": (0.10, 0.25, 0.50, 0.75, 0.90),
        "mapping_policy": "scipy_isotonic_compact_interp_v1",
    }

    serial = selector(
        **common,
        execution_policy="serial_ordered_v1",
        parallel_workers=1,
    )
    parallel = selector(
        **common,
        execution_policy="joblib_loky_ordered_chunks_v1",
        parallel_workers=8,
    )

    assert len(parallel.brier_vector) == iteration_count
    np.testing.assert_array_equal(parallel.brier_vector, serial.brier_vector)
    assert parallel.best_iteration == serial.best_iteration
    assert parallel.best_brier == serial.best_brier


def test_v9_single_synthetic_stage_is_warning_free() -> None:
    selector = m7_candidates._select_quantile_iteration
    staged = tuple(
        np.asarray([[-0.02], [-0.01], [0.01]], dtype=float) + quantile * 0.001
        for quantile in range(5)
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        selected = selector(
            staged_quantiles=staged,
            selection_labels=CLASSES,
            direction_thresholds=(0.01, 0.01, 0.01),
            classes=CLASSES,
            quantile_levels=(0.10, 0.25, 0.50, 0.75, 0.90),
            mapping_policy="scipy_isotonic_compact_interp_v1",
            execution_policy="joblib_loky_ordered_chunks_v1",
            parallel_workers=8,
            timeout_seconds=60,
        )

    assert selected.best_iteration == 1
    assert len(selected.brier_vector) == 1


@pytest.mark.parametrize(
    ("worker_error", "terminal_error"),
    (
        (m7_candidates.MultiprocessingTimeoutError(), "m7_candidate_timeout"),
        (MemoryError(), "m7_candidate_oom"),
        (RuntimeError("worker crashed"), "m7_quantile_selection_worker_failed"),
    ),
)
def test_v9_parallel_quantile_selection_fails_closed_on_worker_error(
    monkeypatch: pytest.MonkeyPatch,
    worker_error: Exception,
    terminal_error: str,
) -> None:
    class FailingParallel:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __call__(self, _tasks: object) -> object:
            raise worker_error

    monkeypatch.setattr(m7_candidates, "Parallel", FailingParallel)
    staged = tuple(np.zeros((3, 2), dtype=float) for _ in range(5))

    with pytest.raises(M7CandidateTerminalError, match=terminal_error):
        m7_candidates._select_quantile_iteration(
            staged_quantiles=staged,
            selection_labels=CLASSES,
            direction_thresholds=(0.01, 0.01, 0.01),
            classes=CLASSES,
            quantile_levels=(0.10, 0.25, 0.50, 0.75, 0.90),
            mapping_policy="scipy_isotonic_compact_interp_v1",
            execution_policy="joblib_loky_ordered_chunks_v1",
            parallel_workers=8,
            timeout_seconds=60,
        )


@pytest.mark.parametrize(
    "scores",
    (
        ((1, 0.5), (3, 0.4)),
        ((1, 0.5), (1, 0.4)),
        ((2, 0.5), (1, 0.4)),
        ((1, 0.5), (2, float("nan"))),
    ),
)
def test_v9_quantile_selection_rejects_invalid_worker_results(
    scores: tuple[tuple[int, float], ...],
) -> None:
    validator = getattr(m7_candidates, "_validate_quantile_stage_scores", None)
    assert validator is not None

    with pytest.raises(M7CandidateTerminalError, match="stage_results_invalid"):
        validator(scores, expected_iterations=2)


def test_v9_quantile_candidate_records_frozen_execution_policy() -> None:
    train, labels = _classification_frame()
    train = train.drop(columns=list(CATEGORICAL))
    selection, selection_labels = _classification_frame(30)
    selection = selection.drop(columns=list(CATEGORICAL))
    target = np.asarray(
        [{"down": -0.04, "flat": 0.0, "up": 0.04}[label] for label in labels]
    )

    candidate = fit_m7_quantile_cdf_candidate(
        configuration_id="m7-quantile_cdf-01",
        train_features=train,
        train_returns=target,
        train_weights=np.ones(len(train)),
        selection_features=selection,
        selection_labels=selection_labels,
        selection_direction_thresholds=np.full(len(selection), 0.01),
        feature_names=("return_20", "volatility_20"),
        classes=CLASSES,
        synthetic_iteration_cap=4,
        timeout_seconds=60,
        recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V9,
    )

    assert candidate.fit_execution_policy == "lightgbm_threadpool_ordered_v1"
    assert candidate.fit_parallel_workers == 4
    assert candidate.selection_execution_policy == "joblib_loky_ordered_chunks_v1"
    assert candidate.selection_parallel_workers == 8
    assert len(candidate.selection_brier_vector) == 4
    assert candidate.selection_brier == min(candidate.selection_brier_vector)


def test_quantile_leaf_path_staging_matches_every_reference_iteration() -> None:
    train, labels = _classification_frame()
    train = train.drop(columns=list(CATEGORICAL))
    target = np.asarray(
        [{"down": -0.04, "flat": 0.0, "up": 0.04}[label] for label in labels]
    )
    selection = train.iloc[:30].copy()
    model = LGBMRegressor(
        objective="quantile",
        alpha=0.5,
        n_estimators=12,
        learning_rate=0.05,
        num_leaves=7,
        min_child_samples=5,
        n_jobs=1,
        random_state=41,
        verbosity=-1,
    )
    model.fit(train, target)
    reference = np.column_stack(
        [model.predict(selection, num_iteration=index) for index in range(1, 13)]
    )

    staging = getattr(m7_candidates, "_lightgbm_staged_predictions", None)
    assert staging is not None
    reconstructed = staging(model, selection, iterations=12)

    np.testing.assert_array_equal(reconstructed, reference)


def test_v7_quantile_candidate_uses_one_leaf_prediction_per_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor = m7_candidates.LGBMRegressor
    prediction_calls: list[list[dict[str, object]]] = []

    def tracked_regressor(**parameters: object) -> LGBMRegressor:
        model = constructor(**parameters)
        calls: list[dict[str, object]] = []
        original_predict = model.predict

        def tracked_predict(*args: object, **kwargs: object) -> np.ndarray:
            calls.append(dict(kwargs))
            return np.asarray(original_predict(*args, **kwargs), dtype=float)

        model.predict = tracked_predict  # type: ignore[method-assign]
        prediction_calls.append(calls)
        return model

    monkeypatch.setattr(m7_candidates, "LGBMRegressor", tracked_regressor)
    train, labels = _classification_frame()
    train = train.drop(columns=list(CATEGORICAL))
    selection, selection_labels = _classification_frame(30)
    selection = selection.drop(columns=list(CATEGORICAL))
    target = np.asarray(
        [{"down": -0.04, "flat": 0.0, "up": 0.04}[label] for label in labels]
    )

    candidate = fit_m7_quantile_cdf_candidate(
        configuration_id="m7-quantile_cdf-01",
        train_features=train,
        train_returns=target,
        train_weights=np.ones(len(train)),
        selection_features=selection,
        selection_labels=selection_labels,
        selection_direction_thresholds=np.full(len(selection), 0.01),
        feature_names=("return_20", "volatility_20"),
        classes=CLASSES,
        synthetic_iteration_cap=8,
        timeout_seconds=60,
        recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V7,
    )

    assert candidate.best_iteration <= 8
    assert len(prediction_calls) == 5
    assert all(
        sum(call.get("pred_leaf") is True for call in calls) == 1
        for calls in prediction_calls
    )
    assert all(len(calls) <= 3 for calls in prediction_calls)


def test_v8_quantile_candidate_records_exact_mapping_policy() -> None:
    train, labels = _classification_frame()
    train = train.drop(columns=list(CATEGORICAL))
    selection, selection_labels = _classification_frame(30)
    selection = selection.drop(columns=list(CATEGORICAL))
    target = np.asarray(
        [{"down": -0.04, "flat": 0.0, "up": 0.04}[label] for label in labels]
    )

    candidate = fit_m7_quantile_cdf_candidate(
        configuration_id="m7-quantile_cdf-01",
        train_features=train,
        train_returns=target,
        train_weights=np.ones(len(train)),
        selection_features=selection,
        selection_labels=selection_labels,
        selection_direction_thresholds=np.full(len(selection), 0.01),
        feature_names=("return_20", "volatility_20"),
        classes=CLASSES,
        synthetic_iteration_cap=4,
        timeout_seconds=60,
        recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V8,
    )

    assert candidate.quantile_mapping_policy == "scipy_isotonic_compact_interp_v1"


def test_quantile_candidate_fits_only_registered_configuration() -> None:
    train, labels = _classification_frame()
    train = train.drop(columns=list(CATEGORICAL))
    selection, selection_labels = _classification_frame(30)
    selection = selection.drop(columns=list(CATEGORICAL))
    target = np.asarray(
        [{"down": -0.04, "flat": 0.0, "up": 0.04}[label] for label in labels]
    )
    candidate = fit_m7_quantile_cdf_candidate(
        configuration_id="m7-quantile_cdf-01",
        train_features=train,
        train_returns=target,
        train_weights=np.ones(len(train)),
        selection_features=selection,
        selection_labels=selection_labels,
        selection_direction_thresholds=np.full(len(selection), 0.01),
        feature_names=("return_20", "volatility_20"),
        classes=CLASSES,
        synthetic_iteration_cap=8,
        timeout_seconds=60,
    )
    probabilities = predict_m7_quantile_cdf(
        candidate,
        selection,
        direction_thresholds=np.full(len(selection), 0.01),
    )

    assert candidate.best_iteration <= 8
    assert candidate.promotion_eligible is False
    assert probabilities.shape == (30, 3)
    assert np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-9)


def test_frozen_baseline_replay_requires_all_registered_outputs_on_same_keys() -> None:
    baseline_ids = registered_m7_frozen_baselines()
    keys = (
        ("US.AAPL", date(2026, 1, 2), 5),
        ("US.SPY", date(2026, 1, 2), 5),
    )
    features = pd.DataFrame({"x": [1.0, 2.0]})
    probabilities = np.array([[0.2, 0.3, 0.5], [0.4, 0.3, 0.3]])
    replayers = {
        baseline_id: (lambda _frame, values=probabilities: (keys, values.copy()))
        for baseline_id in baseline_ids
    }

    replay = replay_m7_frozen_baselines(
        sample_keys=keys,
        features=features,
        classes=CLASSES,
        replayers=replayers,
    )

    assert tuple(replay) == baseline_ids
    assert all(np.array_equal(value, probabilities) for value in replay.values())

    missing = dict(replayers)
    missing.pop(baseline_ids[-1])
    with pytest.raises(M7CandidateTerminalError, match="baseline_registry_mismatch"):
        replay_m7_frozen_baselines(
            sample_keys=keys,
            features=features,
            classes=CLASSES,
            replayers=missing,
        )

    mismatched = dict(replayers)
    mismatched[baseline_ids[0]] = lambda _frame: (tuple(reversed(keys)), probabilities)
    with pytest.raises(M7CandidateTerminalError, match="baseline_sample_keys_mismatch"):
        replay_m7_frozen_baselines(
            sample_keys=keys,
            features=features,
            classes=CLASSES,
            replayers=mismatched,
        )
