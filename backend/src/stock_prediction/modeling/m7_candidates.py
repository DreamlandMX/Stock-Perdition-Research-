"""Finite M7 tree candidates, quantile-CDF mapping, and baseline replay."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from multiprocessing.context import TimeoutError as MultiprocessingTimeoutError
import time
from types import MappingProxyType
from typing import Any, TypeAlias

from catboost import CatBoostClassifier, CatBoostError
from joblib import Parallel, delayed  # type: ignore[import-untyped]
from lightgbm import LGBMRegressor
import numpy as np
import pandas as pd
from scipy.optimize import isotonic_regression  # type: ignore[import-untyped]
from sklearn.isotonic import IsotonicRegression

from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V4,
)
from stock_prediction.modeling.m7_recipe import (
    M7CandidateConfiguration,
    registered_m7_candidate_matrix,
)


M7SampleKey: TypeAlias = tuple[str, date, int]
M7BaselineReplayer: TypeAlias = Callable[
    [pd.DataFrame], tuple[Sequence[M7SampleKey], np.ndarray]
]

_GOVERNED_CATEGORICAL_FEATURES = {"asset_type", "sector", "regime"}
_FROZEN_BASELINES = (
    "historical_frequency",
    "logistic",
    "lightgbm_current",
    "threshold_decomposition",
    "unweighted_regularized_multiclass",
    "linear_blend",
    "nonlinear_blend",
)
_QUANTILE_LEVELS = (0.10, 0.25, 0.50, 0.75, 0.90)
M7_QUANTILE_MAPPING_POLICY_REFERENCE = "sklearn_isotonic_unique_interp_v1"
M7_QUANTILE_MAPPING_POLICY_SCIPY_COMPACT = "scipy_isotonic_compact_interp_v1"
M7_QUANTILE_FIT_POLICY_SERIAL = "serial_ordered_v1"
M7_QUANTILE_FIT_POLICY_THREADPOOL = "lightgbm_threadpool_ordered_v1"
M7_QUANTILE_SELECTION_POLICY_SERIAL = "serial_ordered_v1"
M7_QUANTILE_SELECTION_POLICY_LOKY = "joblib_loky_ordered_chunks_v1"
_QUANTILE_MAPPING_POLICIES = frozenset(
    {
        M7_QUANTILE_MAPPING_POLICY_REFERENCE,
        M7_QUANTILE_MAPPING_POLICY_SCIPY_COMPACT,
    }
)


class M7MulticlassBrierMetric:
    """CatBoost custom metric for row-equal multiclass Brier loss."""

    @staticmethod
    def is_max_optimal() -> bool:
        return False

    @staticmethod
    def evaluate(
        approxes: Sequence[Sequence[float]],
        target: Sequence[float],
        weight: Sequence[float] | None,
    ) -> tuple[float, float]:
        del weight
        logits = np.asarray(approxes, dtype=float).T
        logits -= np.max(logits, axis=1, keepdims=True)
        exponentials = np.exp(logits)
        probabilities = exponentials / exponentials.sum(axis=1, keepdims=True)
        targets = np.asarray(target, dtype=int)
        one_hot = np.eye(probabilities.shape[1])[targets]
        row_losses = np.sum(np.square(probabilities - one_hot), axis=1)
        return float(np.sum(row_losses)), float(len(row_losses))

    @staticmethod
    def get_final_error(error: float, weight: float) -> float:
        return error / weight


class M7CandidateTerminalError(RuntimeError):
    """A registered configuration failed terminally and must not mutate/retry."""


@dataclass(frozen=True, slots=True)
class M7CatBoostCandidate:
    configuration_id: str
    model: Any
    feature_names: tuple[str, ...]
    categorical_feature_names: tuple[str, ...]
    class_order: tuple[str, str, str]
    model_class_order: tuple[str, ...]
    best_iteration: int
    selection_brier: float
    selection_metric: str
    boosting_type: str
    thread_count: int
    seed: int
    promotion_eligible: bool
    ineligibility_reason: str | None


@dataclass(frozen=True, slots=True)
class M7QuantileCDFMapping:
    projected_quantiles: np.ndarray
    probabilities: np.ndarray


@dataclass(frozen=True, slots=True)
class M7QuantileSelection:
    brier_vector: tuple[float, ...]
    best_iteration: int
    best_brier: float


@dataclass(frozen=True, slots=True)
class M7QuantileCDFCandidate:
    configuration_id: str
    models: tuple[Any, ...]
    quantile_levels: tuple[float, ...]
    feature_names: tuple[str, ...]
    class_order: tuple[str, str, str]
    best_iteration: int
    selection_brier: float
    selection_metric: str
    seed: int
    promotion_eligible: bool
    ineligibility_reason: str | None
    quantile_mapping_policy: str = M7_QUANTILE_MAPPING_POLICY_REFERENCE
    fit_execution_policy: str = M7_QUANTILE_FIT_POLICY_SERIAL
    fit_parallel_workers: int = 1
    selection_execution_policy: str = M7_QUANTILE_SELECTION_POLICY_SERIAL
    selection_parallel_workers: int = 1
    selection_brier_vector: tuple[float, ...] = ()


def resolve_m7_candidate_configuration(
    configuration_id: str,
    *,
    recipe_id: str = CANDIDATE_RECIPE_SEED100_TARGETED_V4,
) -> M7CandidateConfiguration:
    configuration = next(
        (
            row
            for row in registered_m7_candidate_matrix(
                recipe_id=recipe_id
            ).configurations
            if row.configuration_id == configuration_id
        ),
        None,
    )
    if configuration is None:
        raise M7CandidateTerminalError("m7_unregistered_configuration")
    return configuration


def fit_m7_catboost_candidate(
    *,
    configuration_id: str,
    train_features: pd.DataFrame,
    train_labels: Sequence[str],
    train_weights: Sequence[float],
    selection_features: pd.DataFrame,
    selection_labels: Sequence[str],
    feature_names: Sequence[str],
    categorical_feature_names: Sequence[str],
    classes: Sequence[str],
    synthetic_iteration_cap: int | None = None,
    timeout_seconds: float | None = None,
    recipe_id: str = CANDIDATE_RECIPE_SEED100_TARGETED_V4,
    full_iterations: bool = False,
) -> M7CatBoostCandidate:
    configuration = resolve_m7_candidate_configuration(
        configuration_id,
        recipe_id=recipe_id,
    )
    if configuration.family != "catboost_multiclass":
        raise M7CandidateTerminalError("m7_candidate_family_mismatch")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise M7CandidateTerminalError("m7_candidate_timeout")
    class_order = _validate_class_order(classes)
    feature_order, categorical_order = _validate_catboost_features(
        train_features,
        selection_features,
        feature_names=feature_names,
        categorical_feature_names=categorical_feature_names,
    )
    train_label_order = _validate_labels(train_labels, class_order, len(train_features))
    selection_label_order = _validate_labels(
        selection_labels,
        class_order,
        len(selection_features),
    )
    weights = _validate_weights(train_weights, len(train_features))
    configured_iterations = int(configuration.parameters["max_iterations"])
    iterations, promotion_eligible, reason = _iteration_policy(
        configured_iterations,
        synthetic_iteration_cap,
    )
    early_stopping_rounds = (
        None
        if full_iterations
        else configuration.parameters.get("early_stopping_rounds")
    )
    started = time.monotonic()
    try:
        model_parameters: dict[str, object] = {
            "loss_function": "MultiClass",
            "iterations": iterations,
            "depth": int(configuration.parameters["depth"]),
            "learning_rate": float(configuration.parameters["learning_rate"]),
            "l2_leaf_reg": float(configuration.parameters["l2_leaf_reg"]),
            "random_strength": float(configuration.parameters["random_strength"]),
            "boosting_type": "Ordered",
            "random_seed": configuration.seed,
            "thread_count": 4,
            "allow_writing_files": False,
            "verbose": False,
            "use_best_model": early_stopping_rounds is not None,
        }
        if early_stopping_rounds is not None:
            model_parameters["eval_metric"] = M7MulticlassBrierMetric()
        model = CatBoostClassifier(
            **model_parameters,
        )
        if early_stopping_rounds is not None:
            model.fit(
                train_features.loc[:, feature_order],
                np.asarray(train_label_order, dtype=str),
                sample_weight=weights,
                cat_features=list(categorical_order),
                eval_set=(
                    selection_features.loc[:, feature_order],
                    np.asarray(selection_label_order, dtype=str),
                ),
                early_stopping_rounds=int(early_stopping_rounds),
            )
            best_iteration = int(model.get_best_iteration()) + 1
            probabilities = _reorder_probabilities(
                np.asarray(
                    model.predict_proba(selection_features.loc[:, feature_order]),
                    dtype=float,
                ),
                tuple(str(value) for value in model.classes_),
                class_order,
            )
            best_brier = _row_equal_brier(
                selection_label_order,
                probabilities,
                class_order,
            )
        else:
            model.fit(
                train_features.loc[:, feature_order],
                np.asarray(train_label_order, dtype=str),
                sample_weight=weights,
                cat_features=list(categorical_order),
            )
            best_iteration = 1
            best_brier = float("inf")
            for iteration, raw in enumerate(
                model.staged_predict_proba(selection_features.loc[:, feature_order]),
                start=1,
            ):
                probabilities = _reorder_probabilities(
                    np.asarray(raw, dtype=float),
                    tuple(str(value) for value in model.classes_),
                    class_order,
                )
                brier = _row_equal_brier(
                    selection_label_order,
                    probabilities,
                    class_order,
                )
                if brier < best_brier:
                    best_brier = brier
                    best_iteration = iteration
            model.shrink(ntree_end=best_iteration)
    except MemoryError as error:
        raise M7CandidateTerminalError("m7_candidate_oom") from error
    except CatBoostError as error:
        code = (
            "m7_candidate_oom"
            if "memory" in str(error).lower()
            else "m7_candidate_fit_failed"
        )
        raise M7CandidateTerminalError(code) from error
    if timeout_seconds is not None and time.monotonic() - started > timeout_seconds:
        raise M7CandidateTerminalError("m7_candidate_timeout")
    model_class_order = tuple(str(value) for value in model.classes_)
    candidate = M7CatBoostCandidate(
        configuration_id=configuration.configuration_id,
        model=model,
        feature_names=feature_order,
        categorical_feature_names=categorical_order,
        class_order=class_order,
        model_class_order=model_class_order,
        best_iteration=best_iteration,
        selection_brier=best_brier,
        selection_metric="multiclass_brier",
        boosting_type="Ordered",
        thread_count=4,
        seed=configuration.seed,
        promotion_eligible=promotion_eligible,
        ineligibility_reason=reason,
    )
    predict_m7_catboost(candidate, selection_features)
    return candidate


def predict_m7_catboost(
    candidate: M7CatBoostCandidate,
    features: pd.DataFrame,
) -> np.ndarray:
    _validate_prediction_frame(
        features,
        feature_names=candidate.feature_names,
        categorical_feature_names=candidate.categorical_feature_names,
    )
    raw = np.asarray(
        candidate.model.predict_proba(features.loc[:, candidate.feature_names]),
        dtype=float,
    )
    probabilities = _reorder_probabilities(
        raw,
        candidate.model_class_order,
        candidate.class_order,
    )
    _validate_probability_matrix(probabilities)
    return probabilities


def _fit_quantile_models_ordered(
    quantile_levels: Sequence[float],
    fit_one: Callable[[int, float], Any],
    *,
    execution_policy: str,
    parallel_workers: int,
) -> tuple[Any, ...]:
    levels = tuple(float(value) for value in quantile_levels)
    if levels != _QUANTILE_LEVELS:
        raise M7CandidateTerminalError("m7_quantile_fit_levels_invalid")
    if execution_policy == M7_QUANTILE_FIT_POLICY_SERIAL:
        if parallel_workers != 1:
            raise M7CandidateTerminalError("m7_quantile_fit_policy_invalid")
        return tuple(fit_one(index, quantile) for index, quantile in enumerate(levels))
    if execution_policy != M7_QUANTILE_FIT_POLICY_THREADPOOL or parallel_workers != 4:
        raise M7CandidateTerminalError("m7_quantile_fit_policy_invalid")
    with ThreadPoolExecutor(
        max_workers=parallel_workers,
        thread_name_prefix="m7-quantile-fit",
    ) as executor:
        futures = tuple(
            executor.submit(fit_one, index, quantile)
            for index, quantile in enumerate(levels)
        )
        try:
            return tuple(future.result() for future in futures)
        except M7CandidateTerminalError:
            raise
        except MemoryError:
            raise
        except Exception as error:
            raise M7CandidateTerminalError("m7_candidate_fit_failed") from error


def _quantile_stage_score_chunk(
    staged_quantiles: tuple[np.ndarray, ...],
    iterations: tuple[int, ...],
    selection_labels: tuple[str, ...],
    direction_thresholds: np.ndarray,
    classes: tuple[str, str, str],
    quantile_levels: tuple[float, ...],
    mapping_policy: str,
) -> tuple[tuple[int, float], ...]:
    scores: list[tuple[int, float]] = []
    for iteration in iterations:
        quantiles = np.column_stack(
            [values[:, iteration - 1] for values in staged_quantiles]
        )
        mapping = map_quantile_cdf_direction(
            quantiles,
            quantile_levels=quantile_levels,
            direction_thresholds=direction_thresholds,
            mapping_policy=mapping_policy,
        )
        brier = _row_equal_brier(
            selection_labels,
            mapping.probabilities,
            classes,
        )
        scores.append((iteration, brier))
    return tuple(scores)


def _validate_quantile_stage_scores(
    scores: Sequence[tuple[int, float]],
    *,
    expected_iterations: int,
) -> tuple[float, ...]:
    values = tuple(scores)
    expected_order = tuple(range(1, expected_iterations + 1))
    if (
        expected_iterations < 1
        or len(values) != expected_iterations
        or tuple(iteration for iteration, _brier in values) != expected_order
        or not np.all(np.isfinite([brier for _iteration, brier in values]))
    ):
        raise M7CandidateTerminalError("m7_quantile_stage_results_invalid")
    return tuple(float(brier) for _iteration, brier in values)


def _contiguous_iteration_chunks(
    iterations: int,
    workers: int,
) -> tuple[tuple[int, ...], ...]:
    chunk_count = min(iterations, workers)
    base, extra = divmod(iterations, chunk_count)
    chunks: list[tuple[int, ...]] = []
    start = 1
    for index in range(chunk_count):
        size = base + int(index < extra)
        chunks.append(tuple(range(start, start + size)))
        start += size
    return tuple(chunks)


def _select_quantile_iteration(
    *,
    staged_quantiles: Sequence[np.ndarray],
    selection_labels: Sequence[str],
    direction_thresholds: object,
    classes: Sequence[str],
    quantile_levels: Sequence[float],
    mapping_policy: str,
    execution_policy: str,
    parallel_workers: int,
    timeout_seconds: float | None = None,
) -> M7QuantileSelection:
    staged = tuple(np.asarray(values, dtype=float) for values in staged_quantiles)
    class_order = _validate_class_order(classes)
    label_order = _validate_labels(
        selection_labels,
        class_order,
        staged[0].shape[0] if staged else 0,
    )
    levels = tuple(float(value) for value in quantile_levels)
    if (
        len(staged) != len(levels)
        or not staged
        or staged[0].ndim != 2
        or staged[0].shape[1] < 1
        or any(values.shape != staged[0].shape for values in staged)
        or any(not np.all(np.isfinite(values)) for values in staged)
    ):
        raise M7CandidateTerminalError("m7_quantile_staged_predictions_invalid")
    thresholds = _validate_thresholds(direction_thresholds, staged[0].shape[0])
    _validate_quantile_mapping_policy(mapping_policy)
    iterations = staged[0].shape[1]
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise M7CandidateTerminalError("m7_candidate_timeout")
    if execution_policy == M7_QUANTILE_SELECTION_POLICY_SERIAL:
        if parallel_workers != 1:
            raise M7CandidateTerminalError("m7_quantile_selection_policy_invalid")
        scores = _quantile_stage_score_chunk(
            staged,
            tuple(range(1, iterations + 1)),
            label_order,
            thresholds,
            class_order,
            levels,
            mapping_policy,
        )
    elif (
        execution_policy == M7_QUANTILE_SELECTION_POLICY_LOKY and parallel_workers == 8
    ):
        chunks = _contiguous_iteration_chunks(iterations, parallel_workers)
        if len(chunks) == 1:
            selection_started = time.monotonic()
            scores = _quantile_stage_score_chunk(
                staged,
                chunks[0],
                label_order,
                thresholds,
                class_order,
                levels,
                mapping_policy,
            )
            if (
                timeout_seconds is not None
                and time.monotonic() - selection_started > timeout_seconds
            ):
                raise M7CandidateTerminalError("m7_candidate_timeout")
        else:
            try:
                chunk_results = Parallel(
                    n_jobs=min(parallel_workers, len(chunks)),
                    backend="loky",
                    batch_size=1,
                    max_nbytes="1M",
                    mmap_mode="r",
                    timeout=timeout_seconds,
                )(
                    delayed(_quantile_stage_score_chunk)(
                        staged,
                        chunk,
                        label_order,
                        thresholds,
                        class_order,
                        levels,
                        mapping_policy,
                    )
                    for chunk in chunks
                )
            except MultiprocessingTimeoutError as error:
                raise M7CandidateTerminalError("m7_candidate_timeout") from error
            except MemoryError as error:
                raise M7CandidateTerminalError("m7_candidate_oom") from error
            except Exception as error:
                raise M7CandidateTerminalError(
                    "m7_quantile_selection_worker_failed"
                ) from error
            scores = tuple(score for chunk in chunk_results for score in chunk)
    else:
        raise M7CandidateTerminalError("m7_quantile_selection_policy_invalid")
    brier_vector = _validate_quantile_stage_scores(
        scores,
        expected_iterations=iterations,
    )
    best_iteration = 1
    best_brier = float("inf")
    for iteration, brier in enumerate(brier_vector, start=1):
        if brier < best_brier:
            best_brier = brier
            best_iteration = iteration
    return M7QuantileSelection(
        brier_vector=brier_vector,
        best_iteration=best_iteration,
        best_brier=best_brier,
    )


def fit_m7_quantile_cdf_candidate(
    *,
    configuration_id: str,
    train_features: pd.DataFrame,
    train_returns: Sequence[float],
    train_weights: Sequence[float],
    selection_features: pd.DataFrame,
    selection_labels: Sequence[str],
    selection_direction_thresholds: object,
    feature_names: Sequence[str],
    classes: Sequence[str],
    synthetic_iteration_cap: int | None = None,
    timeout_seconds: float | None = None,
    recipe_id: str = CANDIDATE_RECIPE_SEED100_TARGETED_V4,
) -> M7QuantileCDFCandidate:
    configuration = resolve_m7_candidate_configuration(
        configuration_id,
        recipe_id=recipe_id,
    )
    if configuration.family != "quantile_cdf":
        raise M7CandidateTerminalError("m7_candidate_family_mismatch")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise M7CandidateTerminalError("m7_candidate_timeout")
    class_order = _validate_class_order(classes)
    feature_order = tuple(feature_names)
    _validate_numeric_frame(train_features, feature_order)
    _validate_numeric_frame(selection_features, feature_order)
    target = np.asarray(train_returns, dtype=float)
    if target.shape != (len(train_features),) or not np.all(np.isfinite(target)):
        raise M7CandidateTerminalError("m7_quantile_target_non_finite")
    weights = _validate_weights(train_weights, len(train_features))
    selection_label_order = _validate_labels(
        selection_labels,
        class_order,
        len(selection_features),
    )
    thresholds = _validate_thresholds(
        selection_direction_thresholds,
        len(selection_features),
    )
    configured_iterations = int(configuration.parameters["max_iterations"])
    quantile_mapping_policy = _validate_quantile_mapping_policy(
        configuration.parameters.get(
            "quantile_mapping_policy",
            M7_QUANTILE_MAPPING_POLICY_REFERENCE,
        )
    )
    fit_execution_policy = str(
        configuration.parameters.get(
            "fit_execution_policy",
            M7_QUANTILE_FIT_POLICY_SERIAL,
        )
    )
    fit_parallel_workers_value = configuration.parameters.get(
        "fit_parallel_workers",
        1,
    )
    selection_execution_policy = str(
        configuration.parameters.get(
            "selection_execution_policy",
            M7_QUANTILE_SELECTION_POLICY_SERIAL,
        )
    )
    selection_parallel_workers_value = configuration.parameters.get(
        "selection_parallel_workers",
        1,
    )
    if (
        not isinstance(fit_parallel_workers_value, int)
        or isinstance(fit_parallel_workers_value, bool)
        or not isinstance(selection_parallel_workers_value, int)
        or isinstance(selection_parallel_workers_value, bool)
    ):
        raise M7CandidateTerminalError("m7_quantile_execution_policy_invalid")
    fit_parallel_workers = fit_parallel_workers_value
    selection_parallel_workers = selection_parallel_workers_value
    iterations, promotion_eligible, reason = _iteration_policy(
        configured_iterations,
        synthetic_iteration_cap,
    )
    train_matrix = train_features.loc[:, feature_order]
    selection_matrix = selection_features.loc[:, feature_order]
    started = time.monotonic()

    def fit_one(quantile_index: int, quantile: float) -> LGBMRegressor:
        model = LGBMRegressor(
            objective="quantile",
            alpha=quantile,
            n_estimators=iterations,
            learning_rate=float(configuration.parameters["learning_rate"]),
            num_leaves=int(configuration.parameters["num_leaves"]),
            min_child_samples=20,
            reg_lambda=1.0,
            n_jobs=4,
            random_state=configuration.seed + quantile_index,
            verbosity=-1,
        )
        model.fit(
            train_matrix,
            target,
            sample_weight=weights,
        )
        return model

    try:
        models = _fit_quantile_models_ordered(
            _QUANTILE_LEVELS,
            fit_one,
            execution_policy=fit_execution_policy,
            parallel_workers=fit_parallel_workers,
        )
    except MemoryError as error:
        raise M7CandidateTerminalError("m7_candidate_oom") from error
    if timeout_seconds is not None and time.monotonic() - started > timeout_seconds:
        raise M7CandidateTerminalError("m7_candidate_timeout")
    staged_prediction_policy = configuration.parameters.get("staged_prediction_policy")
    if staged_prediction_policy == "leaf_path_cumsum_v1":
        try:
            staged_quantiles = tuple(
                _lightgbm_staged_predictions(
                    model,
                    selection_matrix,
                    iterations=iterations,
                )
                for model in models
            )
        except MemoryError as error:
            raise M7CandidateTerminalError("m7_candidate_oom") from error
    else:
        staged_quantiles = None
    remaining_timeout = None
    if timeout_seconds is not None:
        remaining_timeout = timeout_seconds - (time.monotonic() - started)
        if remaining_timeout <= 0:
            raise M7CandidateTerminalError("m7_candidate_timeout")
    if staged_quantiles is not None:
        selection = _select_quantile_iteration(
            staged_quantiles=staged_quantiles,
            selection_labels=selection_label_order,
            direction_thresholds=thresholds,
            classes=class_order,
            quantile_levels=_QUANTILE_LEVELS,
            mapping_policy=quantile_mapping_policy,
            execution_policy=selection_execution_policy,
            parallel_workers=selection_parallel_workers,
            timeout_seconds=remaining_timeout,
        )
    else:
        scores: list[tuple[int, float]] = []
        for iteration in range(1, iterations + 1):
            quantiles = np.column_stack(
                [
                    model.predict(
                        selection_matrix,
                        num_iteration=iteration,
                    )
                    for model in models
                ]
            )
            mapping = map_quantile_cdf_direction(
                quantiles,
                quantile_levels=_QUANTILE_LEVELS,
                direction_thresholds=thresholds,
                mapping_policy=quantile_mapping_policy,
            )
            scores.append(
                (
                    iteration,
                    _row_equal_brier(
                        selection_label_order,
                        mapping.probabilities,
                        class_order,
                    ),
                )
            )
        brier_vector = _validate_quantile_stage_scores(
            scores,
            expected_iterations=iterations,
        )
        best_iteration = 1
        best_brier = float("inf")
        for iteration, brier in enumerate(brier_vector, start=1):
            if brier < best_brier:
                best_brier = brier
                best_iteration = iteration
        selection = M7QuantileSelection(
            brier_vector=brier_vector,
            best_iteration=best_iteration,
            best_brier=best_brier,
        )
    if timeout_seconds is not None and time.monotonic() - started > timeout_seconds:
        raise M7CandidateTerminalError("m7_candidate_timeout")
    candidate = M7QuantileCDFCandidate(
        configuration_id=configuration.configuration_id,
        models=tuple(models),
        quantile_levels=_QUANTILE_LEVELS,
        feature_names=feature_order,
        class_order=class_order,
        best_iteration=selection.best_iteration,
        selection_brier=selection.best_brier,
        selection_metric="multiclass_brier",
        seed=configuration.seed,
        promotion_eligible=promotion_eligible,
        ineligibility_reason=reason,
        quantile_mapping_policy=quantile_mapping_policy,
        fit_execution_policy=fit_execution_policy,
        fit_parallel_workers=fit_parallel_workers,
        selection_execution_policy=selection_execution_policy,
        selection_parallel_workers=selection_parallel_workers,
        selection_brier_vector=selection.brier_vector,
    )
    predict_m7_quantile_cdf(
        candidate,
        selection_features,
        direction_thresholds=thresholds,
    )
    return candidate


def _lightgbm_staged_predictions(
    model: LGBMRegressor,
    features: pd.DataFrame,
    *,
    iterations: int,
) -> np.ndarray:
    if iterations <= 0:
        raise M7CandidateTerminalError("m7_quantile_staged_iterations_invalid")
    leaf_indices = np.asarray(
        model.predict(
            features,
            pred_leaf=True,
            num_iteration=iterations,
        )
    )
    if leaf_indices.ndim == 1:
        leaf_indices = leaf_indices.reshape(-1, 1)
    if (
        leaf_indices.shape[0] != len(features)
        or leaf_indices.shape[1] < 1
        or leaf_indices.shape[1] > iterations
    ):
        raise M7CandidateTerminalError("m7_quantile_staged_leaf_shape_invalid")

    tree_outputs = np.empty(leaf_indices.shape, dtype=float)
    booster = model.booster_
    for tree_index in range(leaf_indices.shape[1]):
        tree_leaf_indices = leaf_indices[:, tree_index].astype(int, copy=False)
        if np.any(tree_leaf_indices < 0):
            raise M7CandidateTerminalError("m7_quantile_staged_leaf_index_invalid")
        unique_leaf_indices = np.unique(tree_leaf_indices)
        leaf_outputs = {
            int(leaf_index): booster.get_leaf_output(tree_index, int(leaf_index))
            for leaf_index in unique_leaf_indices
        }
        tree_outputs[:, tree_index] = np.fromiter(
            (leaf_outputs[int(leaf_index)] for leaf_index in tree_leaf_indices),
            dtype=float,
            count=len(tree_leaf_indices),
        )

    staged = np.cumsum(tree_outputs, axis=1)
    if staged.shape[1] < iterations:
        staged = np.pad(
            staged,
            ((0, 0), (0, iterations - staged.shape[1])),
            mode="edge",
        )
    if not np.all(np.isfinite(staged)):
        raise M7CandidateTerminalError("m7_quantile_staged_prediction_non_finite")
    final_prediction = np.asarray(
        model.predict(features, num_iteration=iterations),
        dtype=float,
    )
    if not np.array_equal(staged[:, -1], final_prediction):
        raise M7CandidateTerminalError("m7_quantile_staged_reconstruction_mismatch")
    return staged


def predict_m7_quantile_cdf(
    candidate: M7QuantileCDFCandidate,
    features: pd.DataFrame,
    *,
    direction_thresholds: object,
    mapping_policy: str | None = None,
) -> np.ndarray:
    _validate_numeric_frame(features, candidate.feature_names)
    thresholds = _validate_thresholds(direction_thresholds, len(features))
    quantiles = np.column_stack(
        [
            model.predict(
                features.loc[:, candidate.feature_names],
                num_iteration=candidate.best_iteration,
            )
            for model in candidate.models
        ]
    )
    return map_quantile_cdf_direction(
        quantiles,
        quantile_levels=candidate.quantile_levels,
        direction_thresholds=thresholds,
        mapping_policy=(
            mapping_policy
            if mapping_policy is not None
            else getattr(
                candidate,
                "quantile_mapping_policy",
                M7_QUANTILE_MAPPING_POLICY_REFERENCE,
            )
        ),
    ).probabilities


def map_quantile_cdf_direction(
    quantile_predictions: object,
    *,
    quantile_levels: Sequence[float],
    direction_thresholds: object,
    mapping_policy: str = M7_QUANTILE_MAPPING_POLICY_REFERENCE,
) -> M7QuantileCDFMapping:
    policy = _validate_quantile_mapping_policy(mapping_policy)
    levels = np.asarray(tuple(quantile_levels), dtype=float)
    matrix = np.asarray(quantile_predictions, dtype=float)
    thresholds = _validate_thresholds(direction_thresholds, len(matrix))
    if (
        matrix.ndim != 2
        or matrix.shape[1] != len(levels)
        or len(levels) < 3
        or not np.all(np.isfinite(matrix))
        or not np.all(np.isfinite(levels))
        or np.any(levels <= 0)
        or np.any(levels >= 1)
        or not np.all(np.diff(levels) > 0)
    ):
        raise M7CandidateTerminalError("m7_quantile_predictions_invalid")
    projected = np.empty_like(matrix)
    probabilities = np.empty((len(matrix), 3), dtype=float)
    for index, row in enumerate(matrix):
        if policy == M7_QUANTILE_MAPPING_POLICY_REFERENCE:
            projected[index] = IsotonicRegression(increasing=True).fit_transform(
                levels,
                row,
            )
            compact_values = None
            compact_levels = None
        else:
            projected[index] = np.asarray(
                isotonic_regression(row, increasing=True).x,
                dtype=float,
            )
            plateau_ends = np.r_[
                projected[index, :-1] != projected[index, 1:],
                True,
            ]
            compact_values = projected[index, plateau_ends]
            compact_levels = levels[plateau_ends]
        if compact_values is None or compact_levels is None:
            lower_cdf = _interpolate_cdf(
                -thresholds[index],
                projected[index],
                levels,
            )
            upper_cdf = _interpolate_cdf(
                thresholds[index],
                projected[index],
                levels,
            )
        else:
            lower_cdf = _interpolate_compact_cdf(
                -thresholds[index],
                compact_values,
                compact_levels,
            )
            upper_cdf = _interpolate_compact_cdf(
                thresholds[index],
                compact_values,
                compact_levels,
            )
        probabilities[index] = (
            lower_cdf,
            max(upper_cdf - lower_cdf, 0.0),
            1.0 - upper_cdf,
        )
    probabilities = np.clip(probabilities, 0.0, 1.0)
    row_sums = probabilities.sum(axis=1, keepdims=True)
    if np.any(row_sums <= 0):
        raise M7CandidateTerminalError("m7_quantile_probability_invalid")
    probabilities /= row_sums
    _validate_probability_matrix(probabilities)
    return M7QuantileCDFMapping(
        projected_quantiles=projected,
        probabilities=probabilities,
    )


def registered_m7_frozen_baselines() -> tuple[str, ...]:
    return _FROZEN_BASELINES


def replay_m7_frozen_baselines(
    *,
    sample_keys: Sequence[M7SampleKey],
    features: pd.DataFrame,
    classes: Sequence[str],
    replayers: Mapping[str, M7BaselineReplayer],
) -> Mapping[str, np.ndarray]:
    keys = tuple(sample_keys)
    if set(replayers) != set(_FROZEN_BASELINES):
        raise M7CandidateTerminalError("m7_baseline_registry_mismatch")
    class_order = _validate_class_order(classes)
    output: dict[str, np.ndarray] = {}
    for baseline_id in _FROZEN_BASELINES:
        replay_keys, probabilities = replayers[baseline_id](features.copy())
        if tuple(replay_keys) != keys:
            raise M7CandidateTerminalError("m7_baseline_sample_keys_mismatch")
        matrix = np.asarray(probabilities, dtype=float)
        if matrix.shape != (len(keys), len(class_order)):
            raise M7CandidateTerminalError("m7_baseline_probability_shape_invalid")
        _validate_probability_matrix(matrix)
        output[baseline_id] = matrix.copy()
    return MappingProxyType(output)


def _validate_class_order(classes: Sequence[str]) -> tuple[str, str, str]:
    values = tuple(classes)
    if values != ("down", "flat", "up"):
        raise M7CandidateTerminalError("m7_direction_class_order_invalid")
    return values


def _validate_labels(
    labels: Sequence[str],
    classes: tuple[str, str, str],
    expected_count: int,
) -> tuple[str, ...]:
    values = tuple(labels)
    if (
        len(values) != expected_count
        or set(values) - set(classes)
        or set(values) != set(classes)
    ):
        raise M7CandidateTerminalError("m7_candidate_labels_invalid")
    return values


def _validate_weights(weights: Sequence[float], expected_count: int) -> np.ndarray:
    values = np.asarray(weights, dtype=float)
    if (
        values.shape != (expected_count,)
        or not np.all(np.isfinite(values))
        or np.any(values <= 0)
    ):
        raise M7CandidateTerminalError("m7_candidate_weights_invalid")
    return values


def _validate_catboost_features(
    train: pd.DataFrame,
    selection: pd.DataFrame,
    *,
    feature_names: Sequence[str],
    categorical_feature_names: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    feature_order = tuple(feature_names)
    categorical_order = tuple(categorical_feature_names)
    if (
        not feature_order
        or len(set(feature_order)) != len(feature_order)
        or "symbol" in feature_order
        or not set(categorical_order).issubset(_GOVERNED_CATEGORICAL_FEATURES)
        or not set(categorical_order).issubset(feature_order)
    ):
        raise M7CandidateTerminalError("m7_catboost_categorical_contract_invalid")
    for frame in (train, selection):
        _validate_prediction_frame(
            frame,
            feature_names=feature_order,
            categorical_feature_names=categorical_order,
        )
    return feature_order, categorical_order


def _validate_prediction_frame(
    frame: pd.DataFrame,
    *,
    feature_names: Sequence[str],
    categorical_feature_names: Sequence[str],
) -> None:
    if not feature_names or set(feature_names) - set(frame.columns) or frame.empty:
        raise M7CandidateTerminalError("m7_candidate_feature_schema_invalid")
    categorical = tuple(categorical_feature_names)
    numeric = tuple(name for name in feature_names if name not in categorical)
    if numeric:
        values = frame.loc[:, numeric].to_numpy(dtype=float)
        if not np.all(np.isfinite(values)):
            raise M7CandidateTerminalError("m7_candidate_feature_non_finite")
    if categorical:
        values = frame.loc[:, categorical]
        if (
            values.isna().any().any()
            or values.astype(str)
            .apply(lambda column: column.str.strip().eq(""))
            .any()
            .any()
        ):
            raise M7CandidateTerminalError("m7_catboost_categorical_contract_invalid")


def _validate_numeric_frame(
    frame: pd.DataFrame,
    feature_names: Sequence[str],
) -> None:
    names = tuple(feature_names)
    if (
        not names
        or len(set(names)) != len(names)
        or set(names) - set(frame.columns)
        or frame.empty
    ):
        raise M7CandidateTerminalError("m7_candidate_feature_schema_invalid")
    values = frame.loc[:, names].to_numpy(dtype=float)
    if not np.all(np.isfinite(values)):
        raise M7CandidateTerminalError("m7_candidate_feature_non_finite")


def _validate_thresholds(values: object, expected_count: int) -> np.ndarray:
    thresholds = np.asarray(values, dtype=float)
    if (
        thresholds.shape != (expected_count,)
        or not np.all(np.isfinite(thresholds))
        or np.any(thresholds < 0)
    ):
        raise M7CandidateTerminalError("m7_direction_threshold_invalid")
    return thresholds


def _iteration_policy(
    configured_iterations: int,
    synthetic_iteration_cap: int | None,
) -> tuple[int, bool, str | None]:
    if synthetic_iteration_cap is None:
        return configured_iterations, True, None
    if synthetic_iteration_cap < 1 or synthetic_iteration_cap > configured_iterations:
        raise M7CandidateTerminalError("m7_synthetic_iteration_cap_invalid")
    return synthetic_iteration_cap, False, "synthetic_iteration_cap"


def _reorder_probabilities(
    probabilities: np.ndarray,
    model_classes: Sequence[str],
    expected_classes: tuple[str, str, str],
) -> np.ndarray:
    if set(model_classes) != set(expected_classes):
        raise M7CandidateTerminalError("m7_candidate_model_classes_invalid")
    positions = [tuple(model_classes).index(name) for name in expected_classes]
    result = probabilities[:, positions]
    _validate_probability_matrix(result)
    return result


def _validate_probability_matrix(probabilities: np.ndarray) -> None:
    if (
        probabilities.ndim != 2
        or probabilities.shape[1] != 3
        or not np.all(np.isfinite(probabilities))
        or np.any(probabilities < 0)
        or np.any(probabilities > 1)
        or not np.allclose(probabilities.sum(axis=1), 1.0, rtol=0.0, atol=1e-9)
    ):
        raise M7CandidateTerminalError("m7_candidate_probability_invalid")


def _row_equal_brier(
    labels: Sequence[str],
    probabilities: np.ndarray,
    classes: tuple[str, str, str],
) -> float:
    positions = {name: index for index, name in enumerate(classes)}
    indices = np.asarray([positions[label] for label in labels], dtype=int)
    one_hot = np.eye(3)[indices]
    return float(np.mean(np.sum(np.square(probabilities - one_hot), axis=1)))


def _interpolate_cdf(
    threshold: float,
    quantile_values: np.ndarray,
    levels: np.ndarray,
) -> float:
    unique_values = np.unique(quantile_values)
    unique_levels = np.asarray(
        [float(np.max(levels[quantile_values == value])) for value in unique_values]
    )
    return float(
        np.interp(
            threshold,
            unique_values,
            unique_levels,
            left=0.0,
            right=1.0,
        )
    )


def _interpolate_compact_cdf(
    threshold: float,
    quantile_values: np.ndarray,
    levels: np.ndarray,
) -> float:
    return float(
        np.interp(
            threshold,
            quantile_values,
            levels,
            left=0.0,
            right=1.0,
        )
    )


def _validate_quantile_mapping_policy(value: object) -> str:
    if not isinstance(value, str) or value not in _QUANTILE_MAPPING_POLICIES:
        raise M7CandidateTerminalError("m7_quantile_mapping_policy_invalid")
    return value


__all__ = [
    "M7CandidateTerminalError",
    "M7MulticlassBrierMetric",
    "M7_QUANTILE_MAPPING_POLICY_REFERENCE",
    "M7_QUANTILE_MAPPING_POLICY_SCIPY_COMPACT",
    "M7CatBoostCandidate",
    "M7QuantileCDFCandidate",
    "M7QuantileCDFMapping",
    "fit_m7_catboost_candidate",
    "fit_m7_quantile_cdf_candidate",
    "map_quantile_cdf_direction",
    "predict_m7_catboost",
    "predict_m7_quantile_cdf",
    "registered_m7_frozen_baselines",
    "replay_m7_frozen_baselines",
    "resolve_m7_candidate_configuration",
]
