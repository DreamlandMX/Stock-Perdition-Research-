"""Production-default fail-closed M7 seed-100 bundle execution adapter."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
import subprocess
from typing import Any

from stock_prediction.contracts.operations import ModelCapabilityBundle
from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V9,
    CANDIDATE_RECIPE_SEED100_TARGETED_V10,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_direction_executor import (
    M7DirectionExecutionOptions,
    M7DirectionTrialContext,
    run_m7_direction_development,
)
from stock_prediction.live.m7_calendar import resolve_seed100_promotion_calendar
from stock_prediction.live.m7_executor import (
    M7BundleExecutionError,
    M7DevelopmentSplits,
    M7MaterializedDevelopmentDataset,
    M7PreparedFeatureData,
    build_m7_development_folds,
    materialize_m7_development_dataset,
    prepare_m7_feature_data,
)
from stock_prediction.live.m7_range_executor import (
    M7RangeExecutionOptions,
    run_m7_range_development,
)
from stock_prediction.live.m7_trial_supervisor import M7DeepTrialSupervisor
from stock_prediction.live.targeted_training import (
    M7BundleExecutionResult,
    M7TargetedTrainingCancelled,
)
from stock_prediction.modeling.m7_dataset import M7DatasetCoverageError
from stock_prediction.modeling.m7_direction_package import (
    M7DirectionFoldEnsemble,
    M7DirectionPublicationIdentity,
    freeze_m7_direction_candidate,
)
from stock_prediction.live.m7_partial_research import (
    M7PartialDirectionPrediction,
    M7PartialFoldManifest,
    M7PartialRangePrediction,
    M7PartialResearchService,
)
from stock_prediction.modeling.m7_range_package import (
    M7RangeFoldEnsemble,
    M7RangePublicationIdentity,
    freeze_m7_range_candidate,
)
from stock_prediction.modeling.m7_recipe import registered_m7_candidate_matrix
from stock_prediction.modeling.report import deterministic_hash, file_sha256
from stock_prediction.operations.registry import LocalModelRegistry
from stock_prediction.universe.seed import SEED_UNIVERSE


_DEVELOPMENT_CUTOFF = date(2026, 6, 29)


class M7Seed100BundleExecutor:
    """Prepare governed inputs once, then execute each requested bundle independently."""

    def __init__(
        self,
        paths: LivePaths,
        *,
        run_id: str,
        recipe_version: str = CANDIDATE_RECIPE_SEED100_TARGETED_V9,
        direction_options: M7DirectionExecutionOptions | None = None,
        range_options: M7RangeExecutionOptions | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        progress_callback: Callable[[str, int, str, str | None], None] | None = None,
        incumbent_provider: Callable[[str, int], str | None] | None = None,
    ) -> None:
        self.paths = paths
        self.run_id = run_id
        if recipe_version not in {
            CANDIDATE_RECIPE_SEED100_TARGETED_V9,
            CANDIDATE_RECIPE_SEED100_TARGETED_V10,
        }:
            raise ValueError("m7_bundle_recipe_invalid")
        self.recipe_version = recipe_version
        self.direction_options = direction_options
        self.range_options = range_options
        self.cancellation_requested = cancellation_requested or (lambda: False)
        self.progress_callback = progress_callback
        self.incumbent_provider = incumbent_provider or self._registry_incumbent
        self._context: (
            tuple[
                M7PreparedFeatureData,
                M7MaterializedDevelopmentDataset,
                M7DevelopmentSplits,
            ]
            | None
        ) = None

    def __call__(self, bundle: ModelCapabilityBundle) -> M7BundleExecutionResult:
        try:
            prepared, materialized, splits = self._load_context()
            if bundle == "direction":
                incumbent_id = self.incumbent_provider("forecast_bundle", 20)
                outcome = run_m7_direction_development(
                    prepared,
                    splits,
                    package_identity=f"m7-direction-{self.run_id}",
                    trial_context=self._trial_context(materialized),
                    options=self.direction_options,
                    cancellation_check=self._check_cancellation,
                    progress_callback=self.progress_callback,
                    partial_fold_callback=(
                        lambda folds: self._publish_direction_partial(
                            prepared,
                            materialized,
                            registered_m7_candidate_matrix(
                                recipe_id=self.recipe_version
                            ).matrix_hash,
                            folds,
                        )
                        if self.recipe_version
                        == CANDIDATE_RECIPE_SEED100_TARGETED_V10
                        else None
                    ),
                )
                gate = outcome.gate.model_dump(mode="json")
                payloads: dict[str, object] = {
                    "feature_ablation": outcome.feature_ablation,
                    "calibration_selection": {
                        str(horizon): [
                            {
                                "fold_id": row.metrics.fold_id,
                                "methods": dict(row.calibration_methods),
                                "blend_members": list(row.blend.member_ids),
                                "blend_weights": list(row.blend.weights),
                            }
                            for row in folds
                        ]
                        for horizon, folds in outcome.fold_evaluations.items()
                    },
                    "baseline_deltas": {
                        str(horizon): decision.model_dump(mode="json")
                        for horizon, decision in outcome.horizon_gates.items()
                    },
                    "bootstrap_results": {
                        "delta_brier_upper_90": outcome.bootstrap_upper_90,
                        "draws": outcome.bootstrap_draws,
                        "block_length": 5,
                        "seed": 17,
                    },
                    "determinism_results": {
                        "passed": all(
                            row.metrics.determinism_passed
                            for folds in outcome.fold_evaluations.values()
                            for row in folds
                        ),
                        "candidate_failures": list(outcome.candidate_failures),
                    },
                    "economic_diagnostics": {
                        "status": "completed",
                        "evidence_scope": (outcome.economic_diagnostic.evidence_scope),
                        "catastrophic_horizon_count": (
                            outcome.economic_diagnostic.catastrophic_horizon_count
                        ),
                        "catastrophic_guardrail_passed": (
                            outcome.economic_diagnostic.catastrophic_guardrail_passed
                        ),
                        "horizons": {
                            str(horizon): _economic_horizon_payload(row)
                            for horizon, row in (
                                outcome.economic_diagnostic.horizons.items()
                            )
                        },
                    },
                    "explanation_manifest": {
                        "status": "not_run",
                        "reason": "development_gate_not_publishable",
                    },
                }
                if gate["status"] == "passed":
                    publication = self._freeze_direction_publication(
                        prepared,
                        materialized,
                        splits,
                        outcome,
                    )
                    if not (
                        publication.sla.inference_sla_passed
                        and publication.sla.explanation_sla_passed
                    ):
                        gate = _failed_gate(gate, "m7_seed100_sla_failed")
                    else:
                        calendar = resolve_seed100_promotion_calendar(
                            self.paths,
                            frozen_at=publication.build_spec.created_at,
                        )
                        payloads["explanation_manifest"] = {
                            "status": "completed",
                            "availability": (publication.sla.explanation_availability),
                            "inference_seconds": publication.sla.inference_seconds,
                            "explanation_seconds": (
                                publication.sla.explanation_seconds
                            ),
                            "seed100_row_count": publication.sla.seed100_row_count,
                            "target": "up_relative_to_down",
                            "dual_baseline_sign_stability": True,
                            "deep_integrated_gradients_steps": 32,
                            "non_causal_caveat": True,
                        }
                        determinism = payloads["determinism_results"]
                        if not isinstance(determinism, dict):
                            raise M7BundleExecutionError(
                                "m7_determinism_payload_invalid"
                            )
                        payloads["determinism_results"] = {
                            **determinism,
                            "package_reload_passed": True,
                            "calendar_source": calendar.calendar_source,
                            "calendar_source_hash": calendar.source_hash,
                        }
                        return M7BundleExecutionResult(
                            bundle_id=bundle,
                            development_gate_passed=True,
                            gate_payload=gate,
                            package_build_spec=publication.build_spec,
                            artifacts=publication.artifacts,
                            eligible_sessions=calendar.eligible_sessions,
                            incumbent_id=incumbent_id,
                            report_payloads=payloads,
                            runtime_observations=publication.runtime_observations,
                        )
            else:
                incumbent_id = self.incumbent_provider("range_exit", 20)
                five_day_anchor_id = self.incumbent_provider("range_exit", 5)
                range_outcome = run_m7_range_development(
                    prepared,
                    splits,
                    options=self.range_options,
                    cancellation_check=self._check_cancellation,
                    progress_callback=self.progress_callback,
                    partial_fold_callback=(
                        lambda fold_id, candidate_id, model, evaluation, feature_names: (
                            self._publish_range_partial(
                                prepared,
                                materialized,
                                registered_m7_candidate_matrix(
                                    recipe_id=self.recipe_version
                                ).matrix_hash,
                                fold_id,
                                candidate_id,
                                model,
                                evaluation,
                                feature_names,
                            )
                        )
                        if self.recipe_version
                        == CANDIDATE_RECIPE_SEED100_TARGETED_V10
                        else None
                    ),
                )
                gate = range_outcome.gate.model_dump(mode="json")
                payloads = {
                    "feature_ablation": _feature_ablation(prepared),
                    "calibration_selection": {
                        "range_exit_20": [
                            {
                                "fold_id": row.metrics.fold_id,
                                "method": row.calibrator.method,
                            }
                            for row in range_outcome.fold_evaluations
                        ]
                    },
                    "baseline_deltas": {
                        "range_exit_20": gate,
                    },
                    "bootstrap_results": {
                        "c_index_delta_lower_90": (
                            range_outcome.c_index_delta_lower_90
                        ),
                        "draws": 10_000,
                        "block_length": 5,
                        "seed": 17,
                    },
                    "determinism_results": {
                        "passed": all(
                            row.metrics.determinism_passed
                            for row in range_outcome.fold_evaluations
                        )
                    },
                    "economic_diagnostics": {
                        "status": "not_applicable",
                        "reason": "range_exit_probability_task",
                    },
                    "explanation_manifest": {
                        "status": "not_run",
                        "reason": "development_gate_not_publishable",
                    },
                }
                if gate["status"] == "passed" and five_day_anchor_id is None:
                    gate = _failed_gate(gate, "m7_range_five_day_anchor_unavailable")
                if gate["status"] == "passed":
                    publication = self._freeze_range_publication(
                        prepared,
                        materialized,
                        splits,
                        range_outcome,
                    )
                    if not (
                        publication.sla.inference_sla_passed
                        and publication.sla.explanation_sla_passed
                    ):
                        gate = _failed_gate(gate, "m7_seed100_sla_failed")
                    else:
                        calendar = resolve_seed100_promotion_calendar(
                            self.paths,
                            frozen_at=publication.build_spec.created_at,
                        )
                        payloads["explanation_manifest"] = {
                            "status": "completed",
                            "availability": (publication.sla.explanation_availability),
                            "inference_seconds": publication.sla.inference_seconds,
                            "explanation_seconds": (
                                publication.sla.explanation_seconds
                            ),
                            "seed100_row_count": publication.sla.seed100_row_count,
                            "non_causal_caveat": True,
                        }
                        determinism = payloads["determinism_results"]
                        if not isinstance(determinism, dict):
                            raise M7BundleExecutionError(
                                "m7_determinism_payload_invalid"
                            )
                        payloads["determinism_results"] = {
                            **determinism,
                            "package_reload_passed": True,
                            "calendar_source": calendar.calendar_source,
                            "calendar_source_hash": calendar.source_hash,
                        }
                        return M7BundleExecutionResult(
                            bundle_id=bundle,
                            development_gate_passed=True,
                            gate_payload=gate,
                            package_build_spec=publication.build_spec,
                            artifacts=publication.artifacts,
                            eligible_sessions=calendar.eligible_sessions,
                            incumbent_id=incumbent_id,
                            report_payloads=payloads,
                            runtime_observations=publication.runtime_observations,
                            range_five_day_anchor_id=five_day_anchor_id,
                        )
            if gate["status"] == "passed":
                gate = _publication_blocked_gate(gate)
            return M7BundleExecutionResult(
                bundle_id=bundle,
                development_gate_passed=False,
                gate_payload=gate,
                package_build_spec=None,
                artifacts=(),
                eligible_sessions=(),
                incumbent_id=None,
                report_payloads=payloads,
                runtime_observations={},
            )
        except M7TargetedTrainingCancelled:
            raise
        except (
            M7BundleExecutionError,
            M7DatasetCoverageError,
            RuntimeError,
            ValueError,
        ) as error:
            reason = str(error) or type(error).__name__
            return M7BundleExecutionResult(
                bundle_id=bundle,
                development_gate_passed=False,
                gate_payload={
                    "status": "failed",
                    "failed_gates": [reason],
                    "reason": reason,
                    "metrics": {},
                    "diagnostics": {},
                },
                package_build_spec=None,
                artifacts=(),
                eligible_sessions=(),
                incumbent_id=None,
                report_payloads={
                    "determinism_results": {
                        "passed": False,
                        "reason": reason,
                    }
                },
                runtime_observations={},
            )

    def _load_context(
        self,
    ) -> tuple[
        M7PreparedFeatureData,
        M7MaterializedDevelopmentDataset,
        M7DevelopmentSplits,
    ]:
        if self._context is None:
            prepared = prepare_m7_feature_data(
                self.paths,
                development_cutoff=_DEVELOPMENT_CUTOFF,
            )
            materialized = materialize_m7_development_dataset(
                self.paths,
                run_id=self.run_id,
                prepared=prepared,
            )
            self._context = (
                prepared,
                materialized,
                build_m7_development_folds(materialized.manifest),
            )
        return self._context

    def _trial_context(
        self,
        materialized: M7MaterializedDevelopmentDataset,
    ) -> M7DirectionTrialContext:
        workspace = _workspace_root(self.paths)
        dependency_lock = workspace / "uv.lock"
        if not dependency_lock.is_file():
            raise M7BundleExecutionError("m7_dependency_lock_unavailable")
        source_paths = (
            "backend/src/stock_prediction/features/m7.py",
            "backend/src/stock_prediction/live/m7_direction_executor.py",
            "backend/src/stock_prediction/live/m7_trial_supervisor.py",
            "backend/src/stock_prediction/live/m7_trial_worker.py",
            "backend/src/stock_prediction/modeling/m7_deep_models.py",
            "backend/src/stock_prediction/modeling/m7_recipe.py",
            "backend/src/stock_prediction/modeling/m7_sequences.py",
            "backend/src/stock_prediction/modeling/m7_trial_protocol.py",
        )
        identities: dict[str, str] = {}
        for relative in source_paths:
            path = workspace / Path(*relative.split("/"))
            if not path.is_file():
                raise M7BundleExecutionError("m7_code_identity_source_unavailable")
            identities[relative] = file_sha256(path)
        return M7DirectionTrialContext(
            paths=self.paths,
            run_id=self.run_id,
            feature_reference=materialized.feature_reference,
            dependency_lock_sha256=file_sha256(dependency_lock),
            code_identity_sha256=deterministic_hash(identities),
            supervisor=M7DeepTrialSupervisor(
                self.paths,
                cancellation_requested=self.cancellation_requested,
            ),
            recipe_id=self.recipe_version,
        )

    def _check_cancellation(self) -> None:
        if self.cancellation_requested():
            raise M7TargetedTrainingCancelled("m7_targeted_training_cancelled")

    def _publish_direction_partial(
        self,
        prepared: M7PreparedFeatureData,
        materialized: M7MaterializedDevelopmentDataset,
        candidate_matrix_hash: str,
        folds: Any,
    ) -> None:
        """Publish only a fully selected and replayed fold at its atomic boundary."""

        self._check_cancellation()
        selected = tuple(folds[horizon] for horizon in (1, 5, 20))
        fold_ids = {fold.fold_id for fold in selected}
        if len(fold_ids) != 1:
            raise M7BundleExecutionError("m7_partial_direction_fold_identity_invalid")
        latest = _latest_seed100_frame(prepared)
        ensemble = M7DirectionFoldEnsemble(selected, required_folds=1)
        predictions = tuple(
            M7PartialDirectionPrediction(
                symbol=str(symbol),
                horizon=horizon,
                down_probability=float(values[index, 0]),
                flat_probability=float(values[index, 1]),
                up_probability=float(values[index, 2]),
            )
            for horizon in (1, 5, 20)
            for values in (ensemble.predict(prepared.frame, horizon=horizon),)
            for index, symbol in enumerate(latest["symbol"].astype(str))
        )
        components = tuple(
            sorted(
                f"h{horizon}:{member.candidate_id}"
                for horizon, fold in zip((1, 5, 20), selected, strict=True)
                for member in fold.members
            )
        )
        source_hashes = tuple(
            sorted(
                member.artifact.content_hash
                for fold in selected
                for member in fold.members
            )
        )
        manifest = M7PartialFoldManifest(
            run_id=self.run_id,
            recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V10,
            bundle_id="direction",
            fold_id=next(iter(fold_ids)),
            as_of=max(latest["session_date"]),
            universe_version=materialized.manifest.universe_version,
            dataset_revision=materialized.manifest.dataset_revision,
            feature_set_version=materialized.manifest.feature_set_version,
            feature_input_hash=prepared.source_artifact.input_hash,
            candidate_matrix_hash=candidate_matrix_hash,
            selected_components=components,
            training_devices=("cpu", "cuda:0"),
            predictions=predictions,
            source_result_hashes=source_hashes,
        )
        self._check_cancellation()
        M7PartialResearchService(self.paths).publish(manifest)

    def _publish_range_partial(
        self,
        prepared: M7PreparedFeatureData,
        materialized: M7MaterializedDevelopmentDataset,
        candidate_matrix_hash: str,
        fold_id: str,
        candidate_id: str,
        model: Any,
        evaluation: Any,
        feature_names: tuple[str, ...],
    ) -> None:
        """Publish a verified CPU range fold without making it a package."""

        self._check_cancellation()
        if not evaluation.metrics.determinism_passed:
            raise M7BundleExecutionError("m7_partial_range_replay_failed")
        latest = _latest_seed100_frame(prepared)
        curves = M7RangeFoldEnsemble(
            models=(model.booster_,),
            calibrators=(evaluation.calibrator,),
            feature_names=feature_names,
        ).predict(latest)
        predictions = tuple(
            M7PartialRangePrediction(
                symbol=str(symbol),
                horizon=20,
                range_exit_probability=float(curves[index, 2]),
            )
            for index, symbol in enumerate(latest["symbol"].astype(str))
        )
        source_hash = deterministic_hash({"model": model.booster_.model_to_string()})
        manifest = M7PartialFoldManifest(
            run_id=self.run_id,
            recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V10,
            bundle_id="range_exit_20",
            fold_id=fold_id,
            as_of=max(latest["session_date"]),
            universe_version=materialized.manifest.universe_version,
            dataset_revision=materialized.manifest.dataset_revision,
            feature_set_version=materialized.manifest.feature_set_version,
            feature_input_hash=prepared.source_artifact.input_hash,
            candidate_matrix_hash=candidate_matrix_hash,
            selected_components=(f"range_exit_20:{candidate_id}",),
            training_devices=("cpu",),
            predictions=predictions,
            source_result_hashes=(source_hash,),
        )
        self._check_cancellation()
        M7PartialResearchService(self.paths).publish(manifest)

    def _registry_incumbent(self, task: str, horizon: int) -> str | None:
        return (
            LocalModelRegistry(self.paths.models / "registry")
            .snapshot(task, horizon)
            .champion_version
        )

    def _freeze_range_publication(
        self,
        prepared: M7PreparedFeatureData,
        materialized: M7MaterializedDevelopmentDataset,
        splits: M7DevelopmentSplits,
        outcome: Any,
    ) -> Any:
        workspace = _workspace_root(self.paths)
        dependency_lock = workspace / "uv.lock"
        if not dependency_lock.is_file():
            raise M7BundleExecutionError("m7_dependency_lock_unavailable")
        matrix = registered_m7_candidate_matrix(
            recipe_id=self.recipe_version
        )
        created_at = datetime.now(UTC)
        latest = _latest_seed100_frame(prepared)
        package_id = (
            f"m7-range-{self.run_id[:80]}-"
            f"{deterministic_hash({'run_id': self.run_id})[:12]}"
        )
        return freeze_m7_range_candidate(
            self.paths.models
            / "runs"
            / self.run_id
            / "package-inputs"
            / "range_exit_20",
            identity=M7RangePublicationIdentity(
                package_id=package_id,
                code_commit=_git_commit(workspace),
                universe_version=materialized.manifest.universe_version,
                dataset_revision=materialized.manifest.dataset_revision,
                feature_set_version=materialized.manifest.feature_set_version,
                feature_input_hash=prepared.source_artifact.input_hash,
                label_version=materialized.manifest.label_version,
                split_manifest_hash=splits.split_manifest_hash,
                candidate_matrix_hash=matrix.matrix_hash,
                dependency_lock_hash=file_sha256(dependency_lock),
                created_at=created_at,
                recipe_id=self.recipe_version,
                recipe_generation=registered_m7_candidate_matrix(
                    recipe_id=self.recipe_version
                ).recipe_generation,
            ),
            label_path=materialized.label_path,
            label_schema_version=materialized.label_reference.schema_version,
            label_content_hash=materialized.label_reference.sha256,
            models=outcome.fold_models,
            calibrators=tuple(
                evaluation.calibrator for evaluation in outcome.fold_evaluations
            ),
            feature_names=outcome.feature_names,
            latest_seed100_frame=latest,
            training_reference_frame=prepared.frame,
            training_wall_seconds=outcome.runtime_seconds,
            training_cpu_seconds=outcome.cpu_seconds,
        )

    def _freeze_direction_publication(
        self,
        prepared: M7PreparedFeatureData,
        materialized: M7MaterializedDevelopmentDataset,
        splits: M7DevelopmentSplits,
        outcome: Any,
    ) -> Any:
        workspace = _workspace_root(self.paths)
        dependency_lock = workspace / "uv.lock"
        if not dependency_lock.is_file():
            raise M7BundleExecutionError("m7_dependency_lock_unavailable")
        matrix = registered_m7_candidate_matrix(
            recipe_id=self.recipe_version
        )
        created_at = datetime.now(UTC)
        package_id = (
            f"m7-direction-{self.run_id[:76]}-"
            f"{deterministic_hash({'run_id': self.run_id})[:12]}"
        )
        return freeze_m7_direction_candidate(
            self.paths.models / "runs" / self.run_id / "package-inputs" / "direction",
            identity=M7DirectionPublicationIdentity(
                package_id=package_id,
                code_commit=_git_commit(workspace),
                universe_version=materialized.manifest.universe_version,
                dataset_revision=materialized.manifest.dataset_revision,
                feature_set_version=materialized.manifest.feature_set_version,
                feature_input_hash=prepared.source_artifact.input_hash,
                label_version=materialized.manifest.label_version,
                split_manifest_hash=splits.split_manifest_hash,
                candidate_matrix_hash=matrix.matrix_hash,
                dependency_lock_hash=file_sha256(dependency_lock),
                created_at=created_at,
                recipe_id=self.recipe_version,
                recipe_generation=registered_m7_candidate_matrix(
                    recipe_id=self.recipe_version
                ).recipe_generation,
            ),
            label_path=materialized.label_path,
            label_schema_version=materialized.label_reference.schema_version,
            label_content_hash=materialized.label_reference.sha256,
            frozen_folds=outcome.frozen_folds,
            latest_seed100_history=prepared.frame,
            training_wall_seconds=outcome.runtime_seconds,
            training_cpu_seconds=outcome.cpu_seconds,
            peak_rss_bytes=outcome.peak_rss_bytes,
            peak_private_bytes=outcome.peak_private_bytes,
            peak_gpu_memory_bytes=outcome.peak_gpu_memory_bytes,
        )


def _feature_ablation(prepared: M7PreparedFeatureData) -> dict[str, Any]:
    intraday = prepared.feature_result.group_status["intraday_optional"]
    return {
        "rows": [
            {"feature_set": "core_v2", "status": "completed", "role": "baseline"},
            {
                "feature_set": "core_v2_cross_sectional",
                "status": "completed",
                "role": "registered_candidates",
            },
            {
                "feature_set": "core_v2_structure_context",
                "status": "not_run",
                "reason": "standalone_ablation_executor_pending",
            },
            {
                "feature_set": "full_daily",
                "status": "completed",
                "role": "registered_candidates",
            },
            {
                "feature_set": "full_daily_intraday_optional",
                "status": intraday.status,
                "reason": intraday.reason,
            },
        ]
    }


def _economic_horizon_payload(row: Any) -> dict[str, Any]:
    def metrics(value: Any) -> dict[str, Any]:
        return {
            "gross_return": value.gross_return,
            "net_return": value.net_return,
            "annualized_sharpe": value.annualized_sharpe,
            "maximum_drawdown": value.maximum_drawdown,
            "average_turnover": value.average_turnover,
            "average_gross_exposure": value.average_gross_exposure,
            "trade_count": value.trade_count,
            "cohort_count": value.cohort_count,
            "capacity_proxy_usd": value.capacity_proxy_usd,
            "cost_sensitivity": dict(value.cost_sensitivity),
        }

    return {
        "horizon": row.horizon,
        "candidate": metrics(row.candidate),
        "incumbent": metrics(row.incumbent),
        "spy": metrics(row.spy),
        "seed100_equal_weight": metrics(row.seed100_equal_weight),
        "catastrophic": row.catastrophic,
    }


def _workspace_root(paths: LivePaths) -> Path:
    for candidate in (paths.root, *paths.root.parents):
        if (candidate / "uv.lock").is_file() and (candidate / "backend").is_dir():
            return candidate
    raise M7BundleExecutionError("m7_workspace_root_unavailable")


def _latest_seed100_frame(prepared: M7PreparedFeatureData) -> Any:
    eligible = prepared.frame[prepared.frame["eligible"].astype(bool)]
    if eligible.empty:
        raise M7BundleExecutionError("m7_seed100_inference_frame_unavailable")
    latest_session = max(eligible["session_date"])
    latest = eligible[eligible["session_date"] == latest_session].copy()
    latest.sort_values("symbol", inplace=True, kind="stable")
    if len(latest) != 100 or set(latest["symbol"].astype(str)) != set(SEED_UNIVERSE):
        raise M7BundleExecutionError("m7_seed100_inference_frame_incomplete")
    return latest.reset_index(drop=True)


def _git_commit(workspace: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise M7BundleExecutionError("m7_code_commit_unavailable") from error
    commit = completed.stdout.strip().lower()
    if len(commit) != 40 or any(value not in "0123456789abcdef" for value in commit):
        raise M7BundleExecutionError("m7_code_commit_invalid")
    return commit


def _publication_blocked_gate(gate: dict[str, Any]) -> dict[str, Any]:
    failed = [*gate.get("failed_gates", []), "shadow_publication_inputs_unavailable"]
    return {
        **gate,
        "status": "failed",
        "failed_gates": failed,
        "reason": ";".join(failed),
    }


def _failed_gate(gate: dict[str, Any], failure: str) -> dict[str, Any]:
    failed = [*gate.get("failed_gates", []), failure]
    return {
        **gate,
        "status": "failed",
        "failed_gates": failed,
        "reason": ";".join(failed),
    }


def m7_executor_configured() -> bool:
    return True


__all__ = ["M7Seed100BundleExecutor", "m7_executor_configured"]
