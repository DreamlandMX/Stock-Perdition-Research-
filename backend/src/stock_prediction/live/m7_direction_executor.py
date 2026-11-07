"""Registered seed-100 direction candidate execution over the four M7 folds."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
import time
from typing import Any, Literal, Protocol, cast

import numpy as np
import pandas as pd
import psutil  # type: ignore[import-untyped]

from stock_prediction.features.m7 import (
    M7_CORE_V2_COLUMNS,
    M7_CROSS_SECTION_COLUMNS,
    M7_INTRADAY_COLUMNS,
    M7_STRUCTURE_CONTEXT_COLUMNS,
)
from stock_prediction.live.artifact_references import ArtifactReference
from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V9,
    CANDIDATE_RECIPE_SEED100_TARGETED_V10,
    CANDIDATE_RECIPE_V1,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_executor import (
    M7DevelopmentSplits,
    M7PreparedFeatureData,
)
from stock_prediction.live.training import (
    _FEATURES as LEGACY_FEATURES,
    _select_classifier_candidate,
)
from stock_prediction.live.m7_trial_supervisor import M7SupervisedTrial
from stock_prediction.modeling.contracts import (
    M7FitComponent,
    M7FittedObjectEvidence,
    M7FoldPartitions,
    SampleKey,
    hash_m7_sample_keys,
)
from stock_prediction.modeling.m7_candidates import (
    M7CatBoostCandidate,
    M7CandidateTerminalError,
    M7QuantileCDFCandidate,
    fit_m7_catboost_candidate,
    fit_m7_quantile_cdf_candidate,
    predict_m7_catboost,
    predict_m7_quantile_cdf,
)
from stock_prediction.modeling.m7_development import (
    M7DirectionCandidatePredictions,
    M7DirectionFoldEvaluation,
    M7DirectionFoldSelection,
    evaluate_m7_direction_fold,
    select_m7_direction_fold,
)
from stock_prediction.modeling.m7_deep_models import (
    M7DeepModelTerminalError,
    verify_m7_probability_replay,
)
from stock_prediction.modeling.m7_direction_package import (
    M7DirectionFrozenFold,
    M7DirectionFrozenMember,
    write_m7_direction_tree_candidate,
)
from stock_prediction.modeling.m7_gates import (
    M7GateDecision,
    evaluate_m7_direction_bundle,
    evaluate_m7_direction_horizon,
)
from stock_prediction.modeling.m7_economics import (
    M7EconomicDiagnostic,
    evaluate_m7_direction_economics,
)
from stock_prediction.modeling.m7_metrics import (
    adaptive_classwise_ece,
    compute_m7_training_weights,
    date_block_bootstrap_delta_brier,
    direction_brier_report,
)
from stock_prediction.modeling.m7_ablation import (
    M7FeatureAblationObservation,
    build_m7_feature_ablation_report,
)
from stock_prediction.modeling.m7_recipe import (
    M7CandidateConfiguration,
    M7CandidateMatrix,
    M7FeatureGroup,
    registered_m7_candidate_matrix,
)
from stock_prediction.modeling.m7_sequences import (
    M7SequenceBatch,
    make_m7_sequence_batch,
)
from stock_prediction.modeling.splits import bind_m7_horizon_fit_evidence
from stock_prediction.modeling.m7_trial_protocol import (
    M7DeepTrialRequest,
    M7TrialArtifactReference,
    M7TrialArtifactRole,
)
from stock_prediction.modeling.m7_package import M7PackageArtifactInput
from stock_prediction.modeling.report import deterministic_hash


_CLASSES = ("down", "flat", "up")
_LABEL_BY_VALUE = {-1: "down", 0: "flat", 1: "up"}


class M7DirectionExecutionError(RuntimeError):
    """The complete registered direction matrix cannot be evaluated safely."""


class M7TrialExecutor(Protocol):
    def execute(self, request: M7DeepTrialRequest) -> M7SupervisedTrial: ...


@dataclass(frozen=True, slots=True)
class M7DirectionTrialContext:
    paths: LivePaths
    run_id: str
    feature_reference: ArtifactReference
    dependency_lock_sha256: str
    code_identity_sha256: str
    supervisor: M7TrialExecutor
    recipe_id: str = CANDIDATE_RECIPE_SEED100_TARGETED_V9


@dataclass(frozen=True, slots=True)
class M7DirectionExecutionOptions:
    synthetic_tree_iteration_cap: int | None = None
    synthetic_deep_epoch_cap: int | None = None
    synthetic_configuration_limit_per_family_sequence: int | None = None
    candidate_timeout_seconds: float | None = None
    catboost_parallel_trials: int = 2
    deep_batch_size: int = 64
    bootstrap_draws: int = 10_000
    full_iterations: bool = False
    cache: Any | None = None

    def __post_init__(self) -> None:
        if self.catboost_parallel_trials not in {1, 2}:
            raise ValueError("m7_catboost_parallel_trials_invalid")


PartialFoldCallback = Callable[[Mapping[int, "M7DirectionFrozenFold"]], None]
ProgressCallback = Callable[[str, int, str, str | None], None]


def direction_candidate_work_units(
    configurations: Sequence[M7CandidateConfiguration], *, outer_fold_count: int
) -> int:
    """Return registered candidate units used by the live progress counter."""

    if outer_fold_count < 1:
        raise ValueError("m7_outer_fold_count_invalid")
    tree = sum(
        configuration.family in {"catboost_multiclass", "quantile_cdf"}
        for configuration in configurations
    )
    deep = sum(
        configuration.family in {"dlinear", "patchtst"}
        for configuration in configurations
    )
    return outer_fold_count * (tree * 3 + deep)


@dataclass(frozen=True, slots=True)
class M7DirectionDevelopmentOutcome:
    gate: M7GateDecision
    horizon_gates: Mapping[int, M7GateDecision]
    fold_evaluations: Mapping[int, tuple[M7DirectionFoldEvaluation, ...]]
    candidate_failures: tuple[Mapping[str, object], ...]
    economic_diagnostic: M7EconomicDiagnostic
    feature_ablation: Mapping[str, object]
    bootstrap_upper_90: float
    bootstrap_draws: int
    runtime_seconds: float
    frozen_folds: Mapping[int, tuple["M7DirectionFrozenFold", ...]]
    cpu_seconds: float
    peak_rss_bytes: int
    peak_private_bytes: int
    peak_gpu_memory_bytes: int


@dataclass(frozen=True, slots=True)
class _CompletedDeepTrial:
    request: M7DeepTrialRequest
    supervised: M7SupervisedTrial
    calibration_predictions: Mapping[int, np.ndarray]
    outer_test_predictions: Mapping[int, np.ndarray]


def run_m7_direction_development(
    prepared: M7PreparedFeatureData,
    splits: M7DevelopmentSplits,
    *,
    package_identity: str,
    trial_context: M7DirectionTrialContext,
    options: M7DirectionExecutionOptions | None = None,
    cancellation_check: Callable[[], None] | None = None,
    partial_fold_callback: PartialFoldCallback | None = None,
    progress_callback: ProgressCallback | None = None,
) -> M7DirectionDevelopmentOutcome:
    """Run all 34 registered configurations without expanding the recipe."""

    active = options or M7DirectionExecutionOptions()
    check_cancellation = cancellation_check or (lambda: None)
    frame = prepared.frame.copy()
    matrix = registered_m7_candidate_matrix(recipe_id=trial_context.recipe_id)
    registered_candidate_count = len(matrix.configurations)
    configurations = _limited_configurations(
        matrix.configurations,
        per_family_sequence_limit=(
            active.synthetic_configuration_limit_per_family_sequence
        ),
    )
    total_candidate_units = direction_candidate_work_units(
        configurations,
        outer_fold_count=len(splits.folds),
    )
    completed_candidate_units = 0

    def candidate_percent() -> int:
        return 10 + completed_candidate_units * 50 // total_candidate_units

    def candidate_started(
        fold: M7FoldPartitions,
        horizon: int | None,
        configuration: M7CandidateConfiguration,
    ) -> None:
        if progress_callback is None:
            return
        device = "GPU CUDA" if configuration.family in {"dlinear", "patchtst"} else "CPU"
        horizon_label = f"H{horizon}" if horizon is not None else "H1/H5/H20"
        trial_id = (
            f"{trial_context.run_id}.{fold.fold_id}.{configuration.configuration_id}"
            + (f".h{horizon}" if horizon is not None else "")
        )
        progress_callback(
            "candidate_search",
            candidate_percent(),
            (
                f"候选 {completed_candidate_units}/{total_candidate_units} · "
                f"outer {fold.fold_id.removeprefix('m7-outer-')}/4 · "
                f"{horizon_label} · {device} {configuration.family}"
            ),
            trial_id,
        )

    def candidate_finished(
        fold: M7FoldPartitions,
        horizon: int | None,
        configuration: M7CandidateConfiguration,
    ) -> None:
        nonlocal completed_candidate_units
        completed_candidate_units += 1
        if progress_callback is None:
            return
        device = "GPU CUDA" if configuration.family in {"dlinear", "patchtst"} else "CPU"
        horizon_label = f"H{horizon}" if horizon is not None else "H1/H5/H20"
        progress_callback(
            "candidate_search",
            candidate_percent(),
            (
                f"候选 {completed_candidate_units}/{total_candidate_units} · "
                f"outer {fold.fold_id.removeprefix('m7-outer-')}/4 · "
                f"{horizon_label} · {device} {configuration.family} 已完成"
            ),
            None,
        )

    def candidate_activity(
        fold: M7FoldPartitions, detail: str, trial_id: str | None = None
    ) -> None:
        if progress_callback is not None:
            progress_callback(
                "candidate_search",
                candidate_percent(),
                (
                    f"候选 {completed_candidate_units}/{total_candidate_units} · "
                    f"outer {fold.fold_id.removeprefix('m7-outer-')}/4 · {detail}"
                ),
                trial_id,
            )
    started = time.monotonic()
    cpu_started = time.process_time()
    fold_results: dict[int, list[M7DirectionFoldEvaluation]] = {
        1: [],
        5: [],
        20: [],
    }
    failures: list[Mapping[str, object]] = []
    frozen_folds: dict[int, list[M7DirectionFrozenFold]] = {1: [], 5: [], 20: []}
    deep_resources: list[Any] = []
    ablation_observations: list[M7FeatureAblationObservation] = []
    for fold in splits.folds:
        partition_frames = {
            "train": _partition_frame(frame, fold.train_sessions),
            "selection": _partition_frame(frame, fold.selection_sessions),
            "calibration": _partition_frame(frame, fold.calibration_sessions),
            "test": _partition_frame(frame, fold.outer_test_sessions),
        }
        tree_predictions: dict[int, list[M7DirectionCandidatePredictions]] = {
            1: [],
            5: [],
            20: [],
        }
        tree_artifacts: dict[tuple[int, str], M7PackageArtifactInput] = {}
        for horizon in (1, 5, 20):

            def write_tree_artifact(
                candidate: M7CatBoostCandidate | M7QuantileCDFCandidate,
                training_frame: pd.DataFrame,
                candidate_horizon: int,
            ) -> None:
                artifact_id = (
                    f"direction-{fold.fold_id}-h{candidate_horizon}-"
                    f"{candidate.configuration_id}"
                )
                artifact = write_m7_direction_tree_candidate(
                    trial_context.paths.models
                    / "runs"
                    / trial_context.run_id
                    / "executor"
                    / "tree-trials"
                    / fold.fold_id
                    / f"h{candidate_horizon}"
                    / f"{candidate.configuration_id}.pkl",
                    candidate=candidate,
                    training_frame=training_frame,
                    artifact_id=artifact_id,
                )
                tree_artifacts[(candidate_horizon, candidate.configuration_id)] = (
                    artifact
                )

            candidates, candidate_failures = _tree_candidates(
                configurations,
                partition_frames,
                horizon=horizon,
                options=active,
                artifact_writer=write_tree_artifact,
                cancellation_check=check_cancellation,
                candidate_started=lambda configuration: candidate_started(
                    fold, horizon, configuration
                ),
                candidate_finished=lambda configuration: candidate_finished(
                    fold, horizon, configuration
                ),
                recipe_id=trial_context.recipe_id,
            )
            tree_predictions[horizon].extend(candidates)
            failures.extend(
                {"fold_id": fold.fold_id, "horizon": horizon, **row}
                for row in candidate_failures
            )
            ablation_observations.extend(
                _feature_ablation_observations(
                    matrix,
                    fold_id=fold.fold_id,
                    partitions=partition_frames,
                    horizon=horizon,
                    options=active,
                    intraday_eligible=(
                        prepared.feature_result.group_status["intraday_optional"].status
                        == "eligible"
                    ),
                    main_cross_sectional_candidate=next(
                        (
                            row
                            for row in candidates
                            if row.candidate_id == "m7-catboost_multiclass-01"
                        ),
                        None,
                    ),
                    cancellation_check=check_cancellation,
                )
            )
            check_cancellation()
        deep, deep_failures, completed_deep_trials = _deep_candidates(
            configurations,
            matrix=matrix,
            fold=fold,
            partition_frames=partition_frames,
            context=trial_context,
            options=active,
            candidate_started=lambda configuration: candidate_started(
                fold, None, configuration
            ),
            candidate_finished=lambda configuration: candidate_finished(
                fold, None, configuration
            ),
        )
        for horizon, rows in deep.items():
            tree_predictions[horizon].extend(rows)
        failures.extend(
            {"fold_id": fold.fold_id, "horizon": "multi", **row}
            for row in deep_failures
        )
        deep_resources.extend(
            completed.supervised.result.resource_observation
            for completed in completed_deep_trials.values()
        )
        check_cancellation()
        selections: dict[int, M7DirectionFoldSelection] = {}
        fit_evidence: dict[
            int,
            tuple[M7FittedObjectEvidence, M7FittedObjectEvidence],
        ] = {}
        for horizon in (1, 5, 20):
            calibration = partition_frames["calibration"]
            calibration_keys = _frame_keys(calibration, horizon)
            calibration_labels = _labels(calibration, horizon)
            calibrator_evidence = bind_m7_horizon_fit_evidence(
                fold,
                component=M7FitComponent.CALIBRATOR,
                horizon=horizon,
                sample_keys=calibration_keys,
            )
            blend_evidence = bind_m7_horizon_fit_evidence(
                fold,
                component=M7FitComponent.BLEND_WEIGHTS,
                horizon=horizon,
                sample_keys=calibration_keys,
            )
            fit_evidence[horizon] = (calibrator_evidence, blend_evidence)
            selections[horizon] = select_m7_direction_fold(
                classes=_CLASSES,
                calibration_sample_keys=calibration_keys,
                calibration_labels=calibration_labels,
                candidates=tuple(tree_predictions[horizon]),
                calibrator_fit_evidence=calibrator_evidence,
                blend_fit_evidence=blend_evidence,
            )
        selected_ids = {
            candidate_id
            for selection in selections.values()
            for candidate_id in selection.blend.member_ids
        }
        candidate_activity(fold, "正在选择候选并进行 GPU replay")
        deterministic_deep, replay_failures = _replay_selected_deep_candidates(
            selected_ids,
            completed=completed_deep_trials,
            context=trial_context,
            replay_started=lambda trial_id: candidate_activity(
                fold, "GPU replay", trial_id
            ),
            replay_finished=lambda: candidate_activity(fold, "GPU replay 已完成"),
        )
        failures.extend(
            {"fold_id": fold.fold_id, "horizon": "multi", **row}
            for row in replay_failures
        )
        check_cancellation()
        family_by_horizon = {
            horizon: {row.candidate_id: row.family for row in rows}
            for horizon, rows in tree_predictions.items()
        }
        for horizon, selection in selections.items():
            members: list[M7DirectionFrozenMember] = []
            for candidate_id in selection.blend.member_ids:
                artifact = tree_artifacts.get((horizon, candidate_id))
                model_kind: Literal["tree", "deep"] = "tree"
                if artifact is None:
                    completed = completed_deep_trials.get(candidate_id)
                    if completed is None:
                        raise M7DirectionExecutionError(
                            "m7_selected_candidate_artifact_missing"
                        )
                    model_artifact = next(
                        (
                            row
                            for row in completed.supervised.result.artifacts
                            if row.role == "model"
                        ),
                        None,
                    )
                    if model_artifact is None:
                        raise M7DirectionExecutionError(
                            "m7_selected_deep_model_artifact_missing"
                        )
                    artifact = M7PackageArtifactInput(
                        artifact_id=(
                            f"direction-{fold.fold_id}-h{horizon}-{candidate_id}"
                        ),
                        category="model",
                        source_path=(
                            completed.supervised.trial_root
                            / Path(model_artifact.relative_path)
                        ),
                        schema_version="m7-deep-model-state-v1",
                        content_hash=model_artifact.sha256,
                    )
                    model_kind = "deep"
                members.append(
                    M7DirectionFrozenMember(
                        candidate_id=candidate_id,
                        family=family_by_horizon[horizon][candidate_id],
                        artifact=artifact,
                        model_kind=model_kind,
                    )
                )
            frozen_folds[horizon].append(
                M7DirectionFrozenFold(
                    fold_id=fold.fold_id,
                    horizon=horizon,
                    selection=selection,
                    members=tuple(members),
                )
            )
        for horizon in (1, 5, 20):
            tree_predictions[horizon] = [
                replace(
                    row,
                    deterministic=(
                        row.deterministic
                        and (
                            row.candidate_id not in completed_deep_trials
                            or row.candidate_id in deterministic_deep
                        )
                    ),
                )
                for row in tree_predictions[horizon]
            ]
            calibration = partition_frames["calibration"]
            test = partition_frames["test"]
            calibration_keys = _frame_keys(calibration, horizon)
            test_keys = _frame_keys(test, horizon)
            calibration_labels = _labels(calibration, horizon)
            test_labels = _labels(test, horizon)
            frequency, incumbent = _baseline_probabilities(
                partition_frames,
                horizon=horizon,
            )
            calibrator_evidence, blend_evidence = fit_evidence[horizon]
            evaluation = evaluate_m7_direction_fold(
                fold_id=fold.fold_id,
                horizon=horizon,
                classes=_CLASSES,
                calibration_sample_keys=calibration_keys,
                calibration_labels=calibration_labels,
                outer_test_sample_keys=test_keys,
                outer_test_labels=test_labels,
                candidates=tuple(tree_predictions[horizon]),
                frequency_probabilities=frequency,
                incumbent_probabilities=incumbent,
                calibrator_fit_evidence=calibrator_evidence,
                blend_fit_evidence=blend_evidence,
                label_health_passed=_label_health(
                    tuple(partition_frames.values()), horizon
                ),
                eligibility_coverage_passed=True,
                stability_passed=(
                    len(tree_predictions[horizon]) == registered_candidate_count
                ),
                selection=selections[horizon],
            )
            fold_results[horizon].append(evaluation)
        if partial_fold_callback is not None:
            partial_fold_callback(
                {horizon: frozen_folds[horizon][-1] for horizon in (1, 5, 20)}
            )
            candidate_activity(fold, "partial fold 已验证并原子发布")
        check_cancellation()
    horizon_gates = {
        horizon: evaluate_m7_direction_horizon(
            horizon,
            [row.metrics for row in fold_results[horizon]],
        )
        for horizon in (1, 5, 20)
    }
    economic = evaluate_m7_direction_economics(
        frame,
        candidate_probabilities={
            horizon: (
                tuple(
                    key
                    for evaluation in fold_results[horizon]
                    for key in evaluation.outer_test_sample_keys
                ),
                np.concatenate(
                    [
                        evaluation.test_probabilities
                        for evaluation in fold_results[horizon]
                    ],
                    axis=0,
                ),
            )
            for horizon in (1, 5, 20)
        },
        incumbent_probabilities={
            horizon: (
                tuple(
                    key
                    for evaluation in fold_results[horizon]
                    for key in evaluation.outer_test_sample_keys
                ),
                np.concatenate(
                    [
                        evaluation.incumbent_probabilities
                        for evaluation in fold_results[horizon]
                    ],
                    axis=0,
                ),
            )
            for horizon in (1, 5, 20)
        },
    )
    bootstrap = _aggregate_bootstrap(fold_results, n_draws=active.bootstrap_draws)
    gate = evaluate_m7_direction_bundle(
        horizon_gates,
        recipe_ids={horizon: matrix.recipe_id for horizon in (1, 5, 20)},
        package_ids={horizon: package_identity for horizon in (1, 5, 20)},
        bootstrap_delta_brier_upper_90=bootstrap,
        # The executor measures these from reloaded package artifacts before freeze.
        inference_sla_passed=True,
        explanation_sla_passed=True,
        economic_catastrophic_guardrail_passed=(economic.catastrophic_guardrail_passed),
    )
    process_memory = psutil.Process().memory_info()
    return M7DirectionDevelopmentOutcome(
        gate=gate,
        horizon_gates=horizon_gates,
        fold_evaluations={
            horizon: tuple(rows) for horizon, rows in fold_results.items()
        },
        candidate_failures=tuple(failures),
        economic_diagnostic=economic,
        feature_ablation=build_m7_feature_ablation_report(
            ablation_observations,
            intraday_status=prepared.feature_result.group_status[
                "intraday_optional"
            ].status,
            intraday_reason=prepared.feature_result.group_status[
                "intraday_optional"
            ].reason,
        ),
        bootstrap_upper_90=bootstrap,
        bootstrap_draws=active.bootstrap_draws,
        runtime_seconds=time.monotonic() - started,
        frozen_folds={horizon: tuple(rows) for horizon, rows in frozen_folds.items()},
        cpu_seconds=time.process_time() - cpu_started,
        peak_rss_bytes=max(
            [int(process_memory.rss), *(row.peak_rss_bytes for row in deep_resources)]
        ),
        peak_private_bytes=max(
            [
                int(getattr(process_memory, "private", 0)),
                *(row.peak_private_bytes for row in deep_resources),
            ]
        ),
        peak_gpu_memory_bytes=max(
            [0, *(row.peak_gpu_memory_bytes for row in deep_resources)]
        ),
    )


def _limited_configurations(
    configurations: Sequence[M7CandidateConfiguration],
    *,
    per_family_sequence_limit: int | None,
) -> tuple[M7CandidateConfiguration, ...]:
    registered = tuple(configurations)
    if per_family_sequence_limit is None:
        return registered
    if (
        not isinstance(per_family_sequence_limit, int)
        or isinstance(per_family_sequence_limit, bool)
        or per_family_sequence_limit < 1
    ):
        raise M7DirectionExecutionError("m7_synthetic_configuration_limit_invalid")
    counts: dict[tuple[str, int | None], int] = {}
    selected: list[M7CandidateConfiguration] = []
    for configuration in registered:
        sequence_length = configuration.parameters.get("sequence_length")
        group = (
            configuration.family,
            int(sequence_length) if sequence_length is not None else None,
        )
        count = counts.get(group, 0)
        if count >= per_family_sequence_limit:
            continue
        counts[group] = count + 1
        selected.append(configuration)
    return tuple(selected)


def _tree_candidates(
    configurations: Sequence[M7CandidateConfiguration],
    partitions: Mapping[str, pd.DataFrame],
    *,
    horizon: int,
    options: M7DirectionExecutionOptions,
    artifact_writer: Callable[
        [M7CatBoostCandidate | M7QuantileCDFCandidate, pd.DataFrame, int], object
    ]
    | None = None,
    cancellation_check: Callable[[], None] | None = None,
    candidate_started: Callable[[M7CandidateConfiguration], None] | None = None,
    candidate_finished: Callable[[M7CandidateConfiguration], None] | None = None,
    recipe_id: str = CANDIDATE_RECIPE_SEED100_TARGETED_V9,
) -> tuple[list[M7DirectionCandidatePredictions], list[Mapping[str, object]]]:
    train = partitions["train"]
    selection = partitions["selection"]
    calibration = partitions["calibration"]
    test = partitions["test"]
    labels = _labels(train, horizon)
    weights = compute_m7_training_weights(
        symbols=tuple(train["symbol"]),
        sessions=tuple(train["session_date"]),
        labels=labels,
        training_cutoff=max(train["session_date"]),
    ).combined_weights
    predictions: list[M7DirectionCandidatePredictions] = []
    failures: list[Mapping[str, object]] = []
    check_cancellation = cancellation_check or (lambda: None)
    report_started = candidate_started or (lambda _configuration: None)
    report_finished = candidate_finished or (lambda _configuration: None)
    tree_configurations = tuple(
        configuration
        for configuration in configurations
        if configuration.family in {"catboost_multiclass", "quantile_cdf"}
    )

    def fit_configuration(
        configuration: M7CandidateConfiguration,
    ) -> tuple[
        M7DirectionCandidatePredictions,
        M7CatBoostCandidate | M7QuantileCDFCandidate,
    ]:
        feature_names, categorical = _configuration_features(
            configuration,
            train,
        )
        transformed = _transform_partitions(
            partitions,
            feature_names=feature_names,
            categorical_feature_names=categorical,
        )
        if configuration.family == "catboost_multiclass":
            catboost_candidate = fit_m7_catboost_candidate(
                configuration_id=configuration.configuration_id,
                train_features=transformed["train"],
                train_labels=labels,
                train_weights=weights,
                selection_features=transformed["selection"],
                selection_labels=_labels(selection, horizon),
                feature_names=feature_names,
                categorical_feature_names=categorical,
                classes=_CLASSES,
                synthetic_iteration_cap=options.synthetic_tree_iteration_cap,
                timeout_seconds=options.candidate_timeout_seconds,
                recipe_id=recipe_id,
                full_iterations=options.full_iterations,
            )
            fitted_candidate: M7CatBoostCandidate | M7QuantileCDFCandidate = (
                catboost_candidate
            )
            calibration_probability = predict_m7_catboost(
                catboost_candidate,
                transformed["calibration"],
            )
            test_probability = predict_m7_catboost(
                catboost_candidate,
                transformed["test"],
            )
        else:
            quantile_candidate = fit_m7_quantile_cdf_candidate(
                configuration_id=configuration.configuration_id,
                train_features=transformed["train"],
                train_returns=train[f"return_fwd_{horizon}"].astype(float),
                train_weights=weights,
                selection_features=transformed["selection"],
                selection_labels=_labels(selection, horizon),
                selection_direction_thresholds=_direction_thresholds(
                    selection, horizon
                ),
                feature_names=feature_names,
                classes=_CLASSES,
                synthetic_iteration_cap=options.synthetic_tree_iteration_cap,
                timeout_seconds=options.candidate_timeout_seconds,
                recipe_id=recipe_id,
            )
            fitted_candidate = quantile_candidate
            calibration_probability = predict_m7_quantile_cdf(
                quantile_candidate,
                transformed["calibration"],
                direction_thresholds=_direction_thresholds(calibration, horizon),
            )
            test_probability = predict_m7_quantile_cdf(
                quantile_candidate,
                transformed["test"],
                direction_thresholds=_direction_thresholds(test, horizon),
            )
        return (
            M7DirectionCandidatePredictions(
                candidate_id=configuration.configuration_id,
                family=configuration.family,
                calibration_probabilities=calibration_probability,
                test_probabilities=test_probability,
                deterministic=fitted_candidate.promotion_eligible,
            ),
            fitted_candidate,
        )

    def run_configuration(
        configuration: M7CandidateConfiguration,
    ) -> tuple[
        M7DirectionCandidatePredictions,
        M7CatBoostCandidate | M7QuantileCDFCandidate,
    ]:
        if options.cache is None:
            return fit_configuration(configuration)
        key = (
            f"m7-tree-{min(train.session_date)}-{max(train.session_date)}-"
            f"{min(test.session_date)}-{max(test.session_date)}-h{horizon}-"
            f"{configuration.configuration_id}-{configuration.feature_group}"
        )
        return cast(
            tuple[
                M7DirectionCandidatePredictions,
                M7CatBoostCandidate | M7QuantileCDFCandidate,
            ],
            cast(Any, options.cache).run(
                key, lambda: fit_configuration(configuration)
            ),
        )

    def accept_result(
        configuration: M7CandidateConfiguration,
        result: tuple[
            M7DirectionCandidatePredictions,
            M7CatBoostCandidate | M7QuantileCDFCandidate,
        ],
    ) -> None:
        prediction, fitted_candidate = result
        if artifact_writer is not None:
            artifact_writer(fitted_candidate, train, horizon)
        predictions.append(prediction)

    catboost_configurations = tuple(
        row for row in tree_configurations if row.family == "catboost_multiclass"
    )
    with ThreadPoolExecutor(
        max_workers=options.catboost_parallel_trials,
        thread_name_prefix="m7-catboost",
    ) as executor:
        for offset in range(
            0, len(catboost_configurations), options.catboost_parallel_trials
        ):
            batch = catboost_configurations[
                offset : offset + options.catboost_parallel_trials
            ]
            futures: tuple[
                tuple[
                    M7CandidateConfiguration,
                    Future[
                        tuple[
                            M7DirectionCandidatePredictions,
                            M7CatBoostCandidate | M7QuantileCDFCandidate,
                        ]
                    ],
                ],
                ...,
            ] = tuple()
            submitted: list[
                tuple[
                    M7CandidateConfiguration,
                    Future[
                        tuple[
                            M7DirectionCandidatePredictions,
                            M7CatBoostCandidate | M7QuantileCDFCandidate,
                        ]
                    ],
                ]
            ] = []
            for configuration in batch:
                report_started(configuration)
                submitted.append(
                    (configuration, executor.submit(run_configuration, configuration))
                )
            futures = tuple(submitted)
            for configuration, future in futures:
                completed = False
                try:
                    accept_result(configuration, future.result())
                    completed = True
                except (M7CandidateTerminalError, MemoryError, ValueError) as error:
                    failures.append(
                        {
                            "configuration_id": configuration.configuration_id,
                            "failure": str(error),
                        }
                    )
                    completed = True
                if completed:
                    report_finished(configuration)
            check_cancellation()

    for configuration in tree_configurations:
        if configuration.family != "quantile_cdf":
            continue
        report_started(configuration)
        completed = False
        try:
            accept_result(configuration, run_configuration(configuration))
            completed = True
        except (M7CandidateTerminalError, MemoryError, ValueError) as error:
            failures.append(
                {
                    "configuration_id": configuration.configuration_id,
                    "failure": str(error),
                }
            )
            completed = True
        if completed:
            report_finished(configuration)
        check_cancellation()
    return predictions, failures


def _feature_ablation_observations(
    matrix: M7CandidateMatrix,
    *,
    fold_id: str,
    partitions: Mapping[str, pd.DataFrame],
    horizon: int,
    options: M7DirectionExecutionOptions,
    intraday_eligible: bool,
    main_cross_sectional_candidate: M7DirectionCandidatePredictions | None,
    cancellation_check: Callable[[], None] | None = None,
) -> tuple[M7FeatureAblationObservation, ...]:
    """Run one fixed CatBoost control across the five governed feature sets."""

    base = next(
        row
        for row in matrix.configurations
        if row.configuration_id == "m7-catboost_multiclass-01"
    )
    feature_sets = [
        "core_v2",
        "core_v2_cross_sectional",
        "core_v2_structure_context",
        "full_daily",
    ]
    if intraday_eligible:
        feature_sets.append("full_daily_intraday_optional")
    if main_cross_sectional_candidate is None:
        raise M7DirectionExecutionError("m7_feature_ablation_main_candidate_missing")
    output: list[M7FeatureAblationObservation] = []
    test = partitions["test"]
    labels = _labels(test, horizon)
    symbols = tuple(test["symbol"].astype(str))
    remaining_feature_sets = [
        feature_set
        for feature_set in feature_sets
        if feature_set != "core_v2_cross_sectional"
    ]
    configurations = tuple(
        replace(base, feature_group=cast(M7FeatureGroup, feature_set))
        for feature_set in remaining_feature_sets
    )
    candidates, failures = _tree_candidates(
        configurations,
        partitions,
        horizon=horizon,
        options=options,
        cancellation_check=cancellation_check,
    )
    if failures or len(candidates) != len(configurations):
        raise M7DirectionExecutionError("m7_feature_ablation_candidate_failed")
    candidate_by_feature_set = dict(
        zip(remaining_feature_sets, candidates, strict=True)
    )
    candidate_by_feature_set["core_v2_cross_sectional"] = main_cross_sectional_candidate
    for feature_set in feature_sets:
        candidate = candidate_by_feature_set[feature_set]
        brier = direction_brier_report(
            symbols=symbols,
            labels=labels,
            probabilities=candidate.test_probabilities,
            classes=_CLASSES,
        )
        calibration = adaptive_classwise_ece(
            symbols=symbols,
            labels=labels,
            probabilities=candidate.test_probabilities,
            classes=_CLASSES,
        )
        output.append(
            M7FeatureAblationObservation(
                feature_set=feature_set,
                fold_id=fold_id,
                horizon=horizon,
                symbol_equal_brier=brier.symbol_equal_brier,
                adaptive_macro_ece=calibration.macro_ece,
                sample_count=len(test),
                deterministic=candidate.deterministic,
            )
        )
    return tuple(output)


def _deep_candidates(
    configurations: Sequence[M7CandidateConfiguration],
    *,
    matrix: M7CandidateMatrix,
    fold: M7FoldPartitions,
    partition_frames: Mapping[str, pd.DataFrame],
    context: M7DirectionTrialContext,
    options: M7DirectionExecutionOptions,
    candidate_started: Callable[[M7CandidateConfiguration], None] | None = None,
    candidate_finished: Callable[[M7CandidateConfiguration], None] | None = None,
) -> tuple[
    dict[int, list[M7DirectionCandidatePredictions]],
    list[Mapping[str, object]],
    dict[str, _CompletedDeepTrial],
]:
    output: dict[int, list[M7DirectionCandidatePredictions]] = {1: [], 5: [], 20: []}
    failures: list[Mapping[str, object]] = []
    completed: dict[str, _CompletedDeepTrial] = {}
    deep_configurations = [
        row for row in configurations if row.family in {"dlinear", "patchtst"}
    ]
    expected_calibration_hash = hash_m7_sample_keys(
        _frame_keys(partition_frames["calibration"], 1)
    )
    expected_test_hash = hash_m7_sample_keys(_frame_keys(partition_frames["test"], 1))
    report_started = candidate_started or (lambda _configuration: None)
    report_finished = candidate_finished or (lambda _configuration: None)
    for configuration in deep_configurations:
        report_started(configuration)
        request = _build_deep_trial_request(
            configuration,
            matrix=matrix,
            fold=fold,
            context=context,
            options=options,
        )
        supervised = context.supervisor.execute(request)
        result = supervised.result
        if result.status == "failed":
            failures.append(
                {
                    "configuration_id": configuration.configuration_id,
                    "failure": result.terminal_failure_code,
                    "resource_observation": result.resource_observation.model_dump(
                        mode="json"
                    ),
                }
            )
            report_finished(configuration)
            continue
        metadata = result.candidate_metadata
        if metadata is None or (
            metadata.calibration_sample_keys_hash != expected_calibration_hash
            or metadata.outer_test_sample_keys_hash != expected_test_hash
        ):
            failures.append(
                {
                    "configuration_id": configuration.configuration_id,
                    "failure": "m7_deep_trial_sample_identity_mismatch",
                }
            )
            report_finished(configuration)
            continue
        try:
            calibration = _load_trial_predictions(
                supervised,
                role="calibration_predictions",
            )
            test = _load_trial_predictions(
                supervised,
                role="outer_test_predictions",
            )
            completed[configuration.configuration_id] = _CompletedDeepTrial(
                request=request,
                supervised=supervised,
                calibration_predictions=calibration,
                outer_test_predictions=test,
            )
            for horizon in (1, 5, 20):
                output[horizon].append(
                    M7DirectionCandidatePredictions(
                        candidate_id=configuration.configuration_id,
                        family=configuration.family,
                        calibration_probabilities=calibration[horizon],
                        test_probabilities=test[horizon],
                        deterministic=metadata.promotion_eligible,
                    )
                )
        except (KeyError, OSError, ValueError):
            failures.append(
                {
                    "configuration_id": configuration.configuration_id,
                    "failure": "m7_deep_trial_prediction_artifact_invalid",
                }
            )
        report_finished(configuration)
    return output, failures, completed


def _replay_selected_deep_candidates(
    selected_ids: set[str],
    *,
    completed: Mapping[str, _CompletedDeepTrial],
    context: M7DirectionTrialContext,
    replay_started: Callable[[str], None] | None = None,
    replay_finished: Callable[[], None] | None = None,
) -> tuple[set[str], list[Mapping[str, object]]]:
    deterministic: set[str] = set()
    failures: list[Mapping[str, object]] = []
    report_started = replay_started or (lambda _trial_id: None)
    report_finished = replay_finished or (lambda: None)
    for configuration_id in sorted(selected_ids & set(completed)):
        reference = completed[configuration_id]
        payload = reference.request.model_dump(
            mode="python",
            exclude={"request_hash"},
        )
        payload["trial_id"] = f"{reference.request.trial_id}.replay"
        replay_request = M7DeepTrialRequest.model_validate(payload, strict=True)
        report_started(replay_request.trial_id)
        replay = context.supervisor.execute(replay_request)
        result = replay.result
        reference_metadata = reference.supervised.result.candidate_metadata
        replay_metadata = result.candidate_metadata
        failure: str | None = None
        if result.status != "succeeded":
            failure = result.terminal_failure_code or "m7_deep_replay_failed"
        elif reference_metadata is None or replay_metadata is None:
            failure = "m7_deep_replay_metadata_missing"
        elif reference_metadata.state_hash != replay_metadata.state_hash:
            failure = "m7_deep_non_deterministic"
        else:
            try:
                replay_calibration = _load_trial_predictions(
                    replay,
                    role="calibration_predictions",
                )
                replay_test = _load_trial_predictions(
                    replay,
                    role="outer_test_predictions",
                )
                verify_m7_probability_replay(
                    reference.calibration_predictions,
                    replay_calibration,
                    device=reference.request.device,
                )
                verify_m7_probability_replay(
                    reference.outer_test_predictions,
                    replay_test,
                    device=reference.request.device,
                )
            except (KeyError, OSError, ValueError, M7DeepModelTerminalError):
                failure = "m7_deep_non_deterministic"
        if failure is None:
            deterministic.add(configuration_id)
        else:
            failures.append(
                {
                    "configuration_id": configuration_id,
                    "failure": failure,
                    "phase": "fresh_process_replay",
                    "resource_observation": result.resource_observation.model_dump(
                        mode="json"
                    ),
                }
            )
        report_finished()
    return deterministic, failures


def _build_deep_trial_request(
    configuration: M7CandidateConfiguration,
    *,
    matrix: M7CandidateMatrix,
    fold: M7FoldPartitions,
    context: M7DirectionTrialContext,
    options: M7DirectionExecutionOptions,
) -> M7DeepTrialRequest:
    if options.deep_batch_size != 64:
        raise M7DirectionExecutionError("m7_deep_batch_size_identity_mismatch")
    if context.recipe_id not in {
        CANDIDATE_RECIPE_SEED100_TARGETED_V9,
        CANDIDATE_RECIPE_SEED100_TARGETED_V10,
    }:
        raise M7DirectionExecutionError("m7_deep_recipe_identity_mismatch")
    return M7DeepTrialRequest(
        trial_id=(f"{context.run_id}.{fold.fold_id}.{configuration.configuration_id}"),
        run_id=context.run_id,
        fold_id=fold.fold_id,
        recipe_id=cast(
            Literal[
                "candidate-recipe-seed100-targeted-v9",
                "candidate-recipe-seed100-targeted-v10",
            ],
            context.recipe_id,
        ),
        candidate_matrix_hash=matrix.matrix_hash,
        configuration_id=configuration.configuration_id,
        configuration_hash=deterministic_hash(configuration.canonical_payload()),
        partition_hash=fold.partition_hash,
        feature_artifact=M7TrialArtifactReference(
            relative_path=context.feature_reference.relative_path,
            sha256=context.feature_reference.sha256,
            schema_version=context.feature_reference.schema_version,
        ),
        train_sessions=fold.train_sessions,
        selection_sessions=fold.selection_sessions,
        calibration_sessions=fold.calibration_sessions,
        outer_test_sessions=fold.outer_test_sessions,
        batch_size=64,
        device=(
            "cuda"
            if context.recipe_id == CANDIDATE_RECIPE_SEED100_TARGETED_V10
            else "cpu"
        ),
        synthetic_epoch_cap=options.synthetic_deep_epoch_cap,
        timeout_seconds=(
            None
            if options.full_iterations
            else options.candidate_timeout_seconds or 2 * 60 * 60
        ),
        full_iterations=options.full_iterations,
        max_rss_bytes=24 * 1024**3,
        max_private_bytes=24 * 1024**3,
        max_gpu_memory_bytes=10 * 1024**3,
        dependency_lock_sha256=context.dependency_lock_sha256,
        code_identity_sha256=context.code_identity_sha256,
    )


def _load_trial_predictions(
    supervised: M7SupervisedTrial,
    *,
    role: M7TrialArtifactRole,
) -> dict[int, np.ndarray]:
    artifact = next(row for row in supervised.result.artifacts if row.role == role)
    path = supervised.trial_root / Path(*artifact.relative_path.split("/"))
    with np.load(path, allow_pickle=False) as values:
        return {
            1: np.asarray(values["h1"], dtype=np.float64),
            5: np.asarray(values["h5"], dtype=np.float64),
            20: np.asarray(values["h20"], dtype=np.float64),
        }


def _make_sequence_batch(
    history: pd.DataFrame,
    partition: pd.DataFrame,
    *,
    feature_names: Sequence[str],
    sequence_length: int,
    weighted: bool,
    trim_insufficient_history: bool = False,
) -> M7SequenceBatch:
    names = tuple(feature_names)
    by_symbol: dict[str, tuple[tuple[date, ...], np.ndarray, dict[date, int]]] = {}
    for symbol, rows in history.groupby("symbol", sort=False):
        ordered = rows.sort_values("session_date")
        dates = tuple(ordered["session_date"])
        by_symbol[str(symbol)] = (
            dates,
            ordered.loc[:, names].to_numpy(dtype=np.float32),
            {value: index for index, value in enumerate(dates)},
        )
    if trim_insufficient_history:
        ready_from = {
            symbol: values[0][sequence_length - 1]
            for symbol, values in by_symbol.items()
            if len(values[0]) >= sequence_length
        }
        first_ready = partition["symbol"].map(ready_from)
        partition = partition[
            first_ready.notna() & (partition["session_date"] >= first_ready)
        ].reset_index(drop=True)
        if partition.empty:
            raise M7DirectionExecutionError("m7_sequence_history_insufficient")
    count = len(partition)
    sequences = np.empty((count, sequence_length, len(names)), dtype=np.float32)
    windows: list[tuple[date, ...]] = []
    sample_keys: list[tuple[str, date]] = []
    for output_index, row in enumerate(partition.itertuples(index=False)):
        dates, values, positions = by_symbol[row.symbol]
        position = positions[row.session_date]
        start = position - sequence_length + 1
        if start < 0:
            raise M7DirectionExecutionError("m7_sequence_history_insufficient")
        sequences[output_index] = values[start : position + 1]
        windows.append(dates[start : position + 1])
        sample_keys.append((row.symbol, row.session_date))
    labels_by_horizon = {
        horizon: np.asarray(
            [_CLASSES.index(value) for value in _labels(partition, horizon)],
            dtype=np.int64,
        )
        for horizon in (1, 5, 20)
    }
    if weighted:
        weights_by_horizon = {
            horizon: np.asarray(
                compute_m7_training_weights(
                    symbols=tuple(partition["symbol"]),
                    sessions=tuple(partition["session_date"]),
                    labels=_labels(partition, horizon),
                    training_cutoff=max(partition["session_date"]),
                ).combined_weights,
                dtype=np.float32,
            )
            for horizon in (1, 5, 20)
        }
    else:
        weights_by_horizon = {
            horizon: np.ones(count, dtype=np.float32) for horizon in (1, 5, 20)
        }
    return make_m7_sequence_batch(
        sample_keys=sample_keys,
        input_sessions=windows,
        sequences=sequences,
        labels_by_horizon=labels_by_horizon,
        weights_by_horizon=weights_by_horizon,
        availability_by_horizon={
            horizon: np.ones(count, dtype=bool) for horizon in (1, 5, 20)
        },
        feature_names=names,
    )


def _baseline_probabilities(
    partitions: Mapping[str, pd.DataFrame],
    *,
    horizon: int,
) -> tuple[np.ndarray, np.ndarray]:
    train = partitions["train"]
    selection = partitions["selection"]
    test = partitions["test"]
    counts = pd.Series(_labels(train, horizon)).value_counts()
    frequency_row = np.asarray(
        [float(counts.get(name, 0)) / len(train) for name in _CLASSES],
        dtype=np.float64,
    )
    frequency = np.tile(frequency_row, (len(test), 1))
    model, _candidate_id = _select_classifier_candidate(
        train,
        selection,
        f"direction_{horizon}",
        task_name="forecast_direction",
        recipe_version=CANDIDATE_RECIPE_V1,
    )
    raw = np.asarray(model.predict_proba(test[list(LEGACY_FEATURES)]), dtype=float)
    by_class = {int(value): index for index, value in enumerate(model.classes_)}
    incumbent = raw[:, [by_class[-1], by_class[0], by_class[1]]]
    return frequency, incumbent


def _configuration_features(
    configuration: M7CandidateConfiguration,
    frame: pd.DataFrame,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    groups = {
        "core_v2": M7_CORE_V2_COLUMNS,
        "core_v2_cross_sectional": (*M7_CORE_V2_COLUMNS, *M7_CROSS_SECTION_COLUMNS),
        "core_v2_structure_context": (
            *M7_CORE_V2_COLUMNS,
            *M7_STRUCTURE_CONTEXT_COLUMNS,
        ),
        "full_daily": (
            *M7_CORE_V2_COLUMNS,
            *M7_CROSS_SECTION_COLUMNS,
            *M7_STRUCTURE_CONTEXT_COLUMNS,
        ),
        "full_daily_intraday_optional": (
            *M7_CORE_V2_COLUMNS,
            *M7_CROSS_SECTION_COLUMNS,
            *M7_STRUCTURE_CONTEXT_COLUMNS,
            *M7_INTRADAY_COLUMNS,
        ),
    }
    desired = groups.get(configuration.feature_group)
    if desired is None:
        raise M7DirectionExecutionError("m7_direction_feature_group_ineligible")
    if configuration.family == "quantile_cdf":
        return _numeric_features(frame, desired), ()
    categorical = tuple(
        name
        for name in ("asset_type", "sector", "regime")
        if name in frame and configuration.feature_group == "full_daily"
    )
    numeric = _numeric_features(frame, desired)
    return (*numeric, *categorical), categorical


def _numeric_features(
    frame: pd.DataFrame,
    names: Sequence[str],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            name
            for name in names
            if name in frame and pd.api.types.is_numeric_dtype(frame[name])
        )
    )


def _transform_partitions(
    partitions: Mapping[str, pd.DataFrame],
    *,
    feature_names: Sequence[str],
    categorical_feature_names: Sequence[str],
) -> dict[str, pd.DataFrame]:
    names = tuple(feature_names)
    categorical = set(categorical_feature_names)
    numeric = tuple(name for name in names if name not in categorical)
    train = partitions["train"]
    medians = train.loc[:, numeric].median().fillna(0.0)
    vocabularies = {
        name: set(train[name].dropna().astype(str)) | {"unavailable"}
        for name in categorical
    }
    output: dict[str, pd.DataFrame] = {}
    for partition_name, source in partitions.items():
        transformed = source.loc[:, names].copy()
        transformed.loc[:, numeric] = transformed.loc[:, numeric].fillna(medians)
        for name in categorical:
            values = transformed[name].fillna("unavailable").astype(str)
            transformed[name] = values.where(
                values.isin(vocabularies[name]), "unavailable"
            )
        output[partition_name] = transformed
    return output


def _partition_frame(frame: pd.DataFrame, sessions: Sequence[date]) -> pd.DataFrame:
    selected = frame[
        frame["session_date"].isin(set(sessions)) & frame["eligible"].astype(bool)
    ].copy()
    required_labels = [f"direction_{horizon}" for horizon in (1, 5, 20)]
    selected = selected.dropna(subset=required_labels)
    selected.sort_values(["session_date", "symbol"], inplace=True, kind="stable")
    if selected.empty:
        raise M7DirectionExecutionError("m7_direction_partition_empty")
    return selected.reset_index(drop=True)


def _labels(frame: pd.DataFrame, horizon: int) -> tuple[str, ...]:
    try:
        return tuple(
            _LABEL_BY_VALUE[int(value)]
            for value in frame[f"direction_{horizon}"].astype(int)
        )
    except KeyError as error:
        raise M7DirectionExecutionError("m7_direction_label_invalid") from error


def _frame_keys(frame: pd.DataFrame, horizon: int) -> tuple[SampleKey, ...]:
    typed_horizon = cast(Literal[1, 5, 20], horizon)
    return tuple(
        (
            row.symbol,
            datetime.combine(row.session_date, datetime.min.time(), tzinfo=UTC),
            typed_horizon,
        )
        for row in frame.itertuples(index=False)
    )


def _direction_thresholds(frame: pd.DataFrame, horizon: int) -> np.ndarray:
    return np.asarray(
        np.maximum(
            0.002,
            0.25
            * frame["atr_pct_20"].astype(float).to_numpy()
            * np.sqrt(float(horizon)),
        ),
        dtype=np.float64,
    )


def _label_health(partitions: Sequence[pd.DataFrame], horizon: int) -> bool:
    for frame in partitions:
        shares = pd.Series(_labels(frame, horizon)).value_counts(normalize=True)
        if set(shares.index) != set(_CLASSES) or float(shares.min()) < 0.02:
            return False
    return True


def _aggregate_bootstrap(
    evaluations: Mapping[int, Sequence[M7DirectionFoldEvaluation]],
    *,
    n_draws: int,
) -> float:
    keys: list[tuple[str, date, int]] = []
    labels: list[str] = []
    candidate: list[np.ndarray] = []
    incumbent: list[np.ndarray] = []
    for horizon in (1, 5, 20):
        for fold in evaluations[horizon]:
            keys.extend(
                (f"{key[0]}:h{horizon}", key[1].date(), horizon)
                for key in fold.outer_test_sample_keys
            )
            labels.extend(fold.outer_test_labels)
            candidate.append(fold.test_probabilities)
            incumbent.append(fold.incumbent_probabilities)
    report = date_block_bootstrap_delta_brier(
        sample_keys=keys,
        labels=labels,
        candidate_probabilities=np.vstack(candidate),
        incumbent_probabilities=np.vstack(incumbent),
        classes=_CLASSES,
        n_draws=n_draws,
    )
    return report.one_sided_upper_90


__all__ = [
    "M7DirectionDevelopmentOutcome",
    "M7DirectionExecutionError",
    "M7DirectionExecutionOptions",
    "run_m7_direction_development",
]
