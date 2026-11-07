"""Durable standard-budget model-training queue and worker."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import inspect
import json
from typing import Callable
from uuid import uuid4

from filelock import FileLock, Timeout

from stock_prediction.contracts.operations import (
    EodWorkerStatus,
    ModelCapabilityBundle,
    ModelTrainingReadiness,
    SubmissionDisposition,
    TrainingTrigger,
    UpdateOperation,
    UpdateOperationStage,
    UpdateOperationStatus,
)
from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V10,
    CANDIDATE_RECIPE_V1,
    candidate_recipe,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.locking import LiveWriterLease
from stock_prediction.live.m7_audit import M7AuditLog
from stock_prediction.live.m7_bundle_executor import M7Seed100BundleExecutor
from stock_prediction.live.m7_cuda import (
    M7CudaPreflightError,
    M7CudaRuntime,
    validate_m7_cuda_runtime,
)
from stock_prediction.live.m7_partial_research import (
    M7PartialResearchResult,
    M7PartialResearchService,
    M7PartialResearchUnavailable,
)
from stock_prediction.live.operation_store import EodOperationStore
from stock_prediction.live.publication import LiveSnapshotPublisher
from stock_prediction.live.promotion_windows import (
    PromotionWindowNotReadyError,
    model_training_readiness,
)
from stock_prediction.live.training import (
    LiveModelTrainer,
    candidate_training_fingerprint,
    candidate_training_report_fingerprint,
)
from stock_prediction.live.targeted_training import (
    M7TargetedTrainingCancelled,
    TargetedModelTrainer,
)
from stock_prediction.operations.registry import LocalModelRegistry


_ACTIVE = {
    UpdateOperationStatus.QUEUED,
    UpdateOperationStatus.RUNNING,
    UpdateOperationStatus.CANCEL_REQUESTED,
}
_INTERRUPTED_ERROR_CODE = "model_training_worker_process_tree_lost"


class PromotionWindowAlreadyConsumedError(RuntimeError):
    """The frozen one-time promotion window cannot be evaluated again."""


class CandidateRecipeExhaustedError(RuntimeError):
    """No further preregistered candidate recipe may use this development set."""


class CandidateRecipeVersionMismatchError(RuntimeError):
    """The requested recipe is stale, unknown, or not the next registered round."""


ReadinessProvider = Callable[[LivePaths], ModelTrainingReadiness]
FingerprintProvider = Callable[..., str]
CudaPreflight = Callable[[], M7CudaRuntime]
TargetedTrainerFactory = Callable[
    [UpdateOperation, Callable[[], bool]], TargetedModelTrainer
]


class ModelTrainingOperationStore(EodOperationStore):
    """Separate queue so an EOD update and a deep training run cannot collide."""

    def __init__(
        self,
        paths: LivePaths,
        *,
        readiness_provider: ReadinessProvider | None = None,
        fingerprint_provider: FingerprintProvider | None = None,
    ) -> None:
        super().__init__(paths)
        self.directory = paths.root / "operations" / "model-training"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._mutation_lock = FileLock(str(self.directory / ".queue.lock"))
        self._readiness_provider = readiness_provider or model_training_readiness
        self._fingerprint_provider = (
            fingerprint_provider or candidate_training_fingerprint
        )

    def submit(
        self,
        *,
        candidate_recipe_version: str | None = None,
        bundles: tuple[ModelCapabilityBundle, ...] | None = None,
        trigger: TrainingTrigger = "manual",
        completed_not_before: datetime | None = None,
    ) -> tuple[UpdateOperation, bool]:
        del completed_not_before
        with self._mutation_lock:
            latest = self.latest()
            if latest is not None and latest.status in _ACTIVE:
                if (
                    candidate_recipe_version is not None
                    and latest.candidate_recipe_version is not None
                    and candidate_recipe_version != latest.candidate_recipe_version
                ):
                    raise CandidateRecipeVersionMismatchError(
                        "candidate_recipe_request_does_not_match_active_operation"
                    )
                if bundles != latest.requested_bundles or trigger != (
                    latest.training_trigger or "manual"
                ):
                    raise CandidateRecipeVersionMismatchError(
                        "targeted_request_does_not_match_active_operation"
                    )
                return (
                    latest.model_copy(
                        update={
                            "submission_disposition": (
                                SubmissionDisposition.REUSED_ACTIVE
                            )
                        }
                    ),
                    False,
                )
            readiness = self.readiness()
            expected_recipe = (
                CANDIDATE_RECIPE_SEED100_TARGETED_V10
                if bundles is not None
                else readiness.next_candidate_recipe_version
            )
            if (
                expected_recipe is None
                and readiness.can_submit_candidate_training
                and readiness.latest_candidate_recipe_version is None
            ):
                # Compatibility for test doubles and clients created before
                # finite recipe metadata was introduced.
                expected_recipe = CANDIDATE_RECIPE_V1
            if expected_recipe is None:
                if (
                    candidate_recipe_version is not None
                    and candidate_recipe_version
                    == readiness.latest_candidate_recipe_version
                ):
                    recipe = candidate_recipe(candidate_recipe_version)
                    fingerprint = self._fingerprint(
                        candidate_recipe_version,
                        bundles=bundles,
                        trigger=trigger,
                    )
                    if (
                        latest is not None
                        and latest.status
                        in {
                            UpdateOperationStatus.SUCCEEDED_DATA_ONLY,
                            UpdateOperationStatus.SUCCEEDED_FORECAST_READY,
                        }
                        and (
                            latest.data_hash == fingerprint
                            or latest.candidate_recipe_version
                            == candidate_recipe_version
                        )
                    ):
                        return (
                            latest.model_copy(
                                update={
                                    "no_change": True,
                                    "submission_disposition": (
                                        SubmissionDisposition.REUSED_COMPLETED
                                    ),
                                }
                            ),
                            False,
                        )
                    completed = self._completed_for_fingerprint(fingerprint)
                    if completed is not None:
                        return (
                            completed.model_copy(
                                update={
                                    "no_change": True,
                                    "candidate_recipe_version": (
                                        candidate_recipe_version
                                    ),
                                    "candidate_generation": recipe.generation,
                                    "submission_disposition": (
                                        SubmissionDisposition.REUSED_COMPLETED
                                    ),
                                }
                            ),
                            False,
                        )
                raise CandidateRecipeExhaustedError(
                    readiness.candidate_training_block_reason
                    or "no_additional_candidate_recipe_registered"
                )
            selected_recipe = candidate_recipe_version or expected_recipe
            if selected_recipe != expected_recipe:
                raise CandidateRecipeVersionMismatchError(
                    f"candidate_recipe_version_mismatch:{selected_recipe}:{expected_recipe}"
                )
            if selected_recipe == CANDIDATE_RECIPE_SEED100_TARGETED_V10:
                if bundles not in {("direction",), ("range_exit_20",)}:
                    raise CandidateRecipeVersionMismatchError(
                        "m7_v10_requires_one_explicit_bundle"
                    )
                if bundles == ("range_exit_20",) and not self._has_terminal_direction_v10():
                    raise CandidateRecipeVersionMismatchError(
                        "m7_range_phase_requires_terminal_direction"
                    )
            recipe = candidate_recipe(selected_recipe)
            fingerprint = self._fingerprint(
                selected_recipe,
                bundles=bundles,
                trigger=trigger,
            )
            if (
                latest is not None
                and latest.status
                in {
                    UpdateOperationStatus.SUCCEEDED_DATA_ONLY,
                    UpdateOperationStatus.SUCCEEDED_FORECAST_READY,
                }
                and latest.data_hash == fingerprint
            ):
                return (
                    latest.model_copy(
                        update={
                            "no_change": True,
                            "submission_disposition": (
                                SubmissionDisposition.REUSED_COMPLETED
                            ),
                        }
                    ),
                    False,
                )
            completed = self._completed_for_fingerprint(fingerprint)
            if completed is not None:
                self.save(completed)
                return (
                    completed.model_copy(
                        update={
                            "no_change": True,
                            "candidate_recipe_version": selected_recipe,
                            "candidate_generation": recipe.generation,
                            "submission_disposition": (
                                SubmissionDisposition.REUSED_COMPLETED
                            ),
                        }
                    ),
                    False,
                )
            now = datetime.now(UTC)
            operation = UpdateOperation(
                operation_id=f"model-training-{now:%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}",
                operation_type="model_training",
                status=UpdateOperationStatus.QUEUED,
                progress_percent=0,
                data_hash=fingerprint,
                candidate_recipe_version=selected_recipe,
                candidate_generation=recipe.generation,
                submission_disposition=SubmissionDisposition.CREATED,
                requested_bundles=bundles,
                training_trigger=trigger,
                created_at=now,
                updated_at=now,
            )
            self.save(operation)
            return operation, True

    def _fingerprint(
        self,
        recipe_version: str,
        *,
        bundles: tuple[ModelCapabilityBundle, ...] | None,
        trigger: TrainingTrigger,
    ) -> str:
        parameters = inspect.signature(self._fingerprint_provider).parameters
        if len(parameters) >= 2:
            base = self._fingerprint_provider(self.paths, recipe_version)
        else:
            base = self._fingerprint_provider(self.paths)
        if bundles is None and trigger == "manual":
            return base
        return hashlib.sha256(
            json.dumps(
                {
                    "base_fingerprint": base,
                    "bundles": bundles,
                    "trigger": trigger,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    def _completed_for_fingerprint(self, fingerprint: str) -> UpdateOperation | None:
        matches: list[UpdateOperation] = []
        report_fingerprints = self._training_report_fingerprints()
        for path in self.directory.glob("model-training-*.json"):
            try:
                operation = UpdateOperation.model_validate_json(
                    path.read_bytes(), strict=True
                )
            except (OSError, ValueError):
                continue
            if operation.status not in {
                UpdateOperationStatus.SUCCEEDED_DATA_ONLY,
                UpdateOperationStatus.SUCCEEDED_FORECAST_READY,
            }:
                continue
            semantic_fingerprint = (
                report_fingerprints.get(operation.model_version)
                if operation.model_version is not None
                else None
            )
            if (
                operation.data_hash == fingerprint
                or semantic_fingerprint == fingerprint
            ):
                matches.append(operation)
        return (
            max(matches, key=lambda item: item.finished_at or item.updated_at)
            if matches
            else None
        )

    def _has_terminal_direction_v10(self) -> bool:
        for path in self.directory.glob("model-training-*.json"):
            try:
                operation = UpdateOperation.model_validate_json(
                    path.read_bytes(), strict=True
                )
            except (OSError, ValueError):
                continue
            if (
                operation.candidate_recipe_version
                == CANDIDATE_RECIPE_SEED100_TARGETED_V10
                and operation.requested_bundles == ("direction",)
                and operation.status not in _ACTIVE
            ):
                return True
        return False

    def _training_report_fingerprints(self) -> dict[str, str]:
        fingerprints: dict[str, str] = {}
        for path in self.paths.models.glob("runs/*/training-report.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            model_version = payload.get("model_version")
            fingerprint = candidate_training_report_fingerprint(payload)
            if isinstance(model_version, str) and fingerprint is not None:
                fingerprints[model_version] = fingerprint
        return fingerprints

    def readiness(self) -> ModelTrainingReadiness:
        return self._readiness_provider(self.paths)


class ModelTrainingCoordinator:
    """Execute one immediate development-candidate run and publish research."""

    def __init__(
        self,
        paths: LivePaths,
        *,
        trainer_factory: Callable[[], LiveModelTrainer] | None = None,
        publisher_factory: Callable[[], LiveSnapshotPublisher] | None = None,
        readiness_provider: ReadinessProvider | None = None,
        fingerprint_provider: FingerprintProvider | None = None,
        targeted_trainer_factory: TargetedTrainerFactory | None = None,
        cuda_preflight: CudaPreflight | None = None,
    ) -> None:
        self.paths = paths
        self.store = ModelTrainingOperationStore(
            paths,
            readiness_provider=readiness_provider,
            fingerprint_provider=fingerprint_provider,
        )
        self._uses_default_trainer = trainer_factory is None
        self.trainer_factory = trainer_factory or (lambda: LiveModelTrainer(paths))
        self.cuda_preflight = cuda_preflight or validate_m7_cuda_runtime
        self.targeted_trainer_factory = targeted_trainer_factory or (
            lambda operation, cancellation_requested: TargetedModelTrainer(
                paths,
                run_id=operation.operation_id,
                bundles=operation.requested_bundles or (),
                recipe_version=(
                    operation.candidate_recipe_version
                    or CANDIDATE_RECIPE_SEED100_TARGETED_V10
                ),
                bundle_executor=M7Seed100BundleExecutor(
                    paths,
                    run_id=operation.operation_id,
                    recipe_version=(
                        operation.candidate_recipe_version
                        or CANDIDATE_RECIPE_SEED100_TARGETED_V10
                    ),
                    cancellation_requested=cancellation_requested,
                    progress_callback=lambda stage, percent, detail, active_trial_id: (
                        self._stage(
                            operation.operation_id,
                            UpdateOperationStage(stage),
                            percent,
                            detail,
                            active_trial_id=active_trial_id,
                        )
                    ),
                ),
                cancellation_requested=cancellation_requested,
            )
        )
        self.publisher_factory = publisher_factory or (
            lambda: LiveSnapshotPublisher(paths)
        )

    def submit(
        self,
        *,
        candidate_recipe_version: str | None = None,
        bundles: tuple[ModelCapabilityBundle, ...] | None = None,
        trigger: TrainingTrigger = "manual",
    ) -> tuple[UpdateOperation, bool]:
        return self.store.submit(
            candidate_recipe_version=candidate_recipe_version,
            bundles=bundles,
            trigger=trigger,
        )

    def request_cancel(self, operation_id: str) -> UpdateOperation:
        return self.store.request_cancel(operation_id)

    def get(self, operation_id: str) -> UpdateOperation | None:
        return self.store.get(operation_id)

    def latest(self) -> UpdateOperation | None:
        return self.store.latest()

    def worker_status(self) -> EodWorkerStatus:
        return self.store.worker_status()

    def readiness(self) -> ModelTrainingReadiness:
        return self.store.readiness()

    def partial_result(
        self,
        operation_id: str,
        symbol: str,
    ) -> M7PartialResearchResult:
        operation = self.store.get(operation_id)
        if operation is None:
            return M7PartialResearchUnavailable(reason="m7_partial_run_not_found")
        bundles = operation.requested_bundles or ()
        if "direction" in bundles:
            bundle: ModelCapabilityBundle = "direction"
        elif "range_exit_20" in bundles:
            bundle = "range_exit_20"
        else:
            return M7PartialResearchUnavailable(
                reason="m7_partial_bundle_not_requested"
            )
        directory = self.paths.models / "runs" / operation_id
        if not directory.is_dir():
            return M7PartialResearchUnavailable(reason="m7_partial_no_valid_fold")
        return M7PartialResearchService(self.paths).result(
            run_id=operation_id,
            bundle_id=bundle,
            symbol=symbol,
        )

    def reconcile_interrupted_operation(self) -> UpdateOperation | None:
        """Fail closed work that belonged to an earlier scheduler process."""

        operation = self.store.latest()
        if operation is None or operation.status not in {
            UpdateOperationStatus.RUNNING,
            UpdateOperationStatus.CANCEL_REQUESTED,
        }:
            return operation
        try:
            with LiveWriterLease(
                self.paths,
                task_id=f"model-training-reconcile:{operation.operation_id}",
                timeout=0,
            ):
                current = self.store.get(operation.operation_id)
                if current is None or current.status not in {
                    UpdateOperationStatus.RUNNING,
                    UpdateOperationStatus.CANCEL_REQUESTED,
                }:
                    return current
                return self._reconcile_unowned_operation(current)
        except Timeout:
            # A held cross-process writer lease is authoritative evidence that
            # the operation may still have a live owner. The active worker (or
            # a later lease-free reconciliation) must make the terminal write.
            return self.store.get(operation.operation_id)

    def _reconcile_unowned_operation(
        self, operation: UpdateOperation
    ) -> UpdateOperation:
        if operation.status is UpdateOperationStatus.CANCEL_REQUESTED:
            return self._finalize_cancellation(operation)

        active_stage = operation.current_stage or UpdateOperationStage.DATA_REPAIR
        now = datetime.now(UTC)
        self.store.update(
            operation.operation_id,
            stage=active_stage,
            phase_status="failed",
            phase_detail=_INTERRUPTED_ERROR_CODE,
            active_trial_id=None,
        )
        if operation.requested_bundles is not None:
            M7AuditLog(self.paths).append_once(
                event_type="training_failed",
                recorded_at=now,
                actor="model-training-worker",
                run_id=operation.operation_id,
                bundle_id=None,
                package_sha256=None,
                detail={
                    "reason": _INTERRUPTED_ERROR_CODE,
                    "stage": active_stage.value,
                },
            )
        return self.store.update(
            operation.operation_id,
            status=UpdateOperationStatus.FAILED,
            error_code=_INTERRUPTED_ERROR_CODE,
            diagnostic_detail=_INTERRUPTED_ERROR_CODE,
            retry_action="启动新的完整候选训练 operation；禁止恢复本次残缺运行。",
            retry_recommendation=(
                "保留旧 run 产物与 staging 作为中断证据；不要排名、冻结或晋升。"
            ),
            active_trial_id=None,
            automatic_promotion=(
                "kept_current"
                if operation.requested_bundles is not None
                else operation.automatic_promotion
            ),
            finished_at=now,
        )

    def run_pending(self) -> UpdateOperation | None:
        operation = self.store.latest()
        if operation is None or operation.status not in _ACTIVE:
            return None
        if operation.status is UpdateOperationStatus.CANCEL_REQUESTED:
            return self._finalize_cancellation(operation)
        try:
            with LiveWriterLease(
                self.paths,
                task_id=operation.operation_id,
                timeout=0,
            ):
                return self._run(operation)
        except Timeout:
            return self.store.get(operation.operation_id)

    def _run(self, operation: UpdateOperation) -> UpdateOperation:
        operation_id = operation.operation_id
        if operation.started_at is None:
            self.store.update(
                operation_id,
                status=UpdateOperationStatus.RUNNING,
                started_at=datetime.now(UTC),
            )
        active_stage = UpdateOperationStage.DATA_REPAIR
        training: dict[str, object] | None = None
        try:
            if operation.candidate_recipe_version == CANDIDATE_RECIPE_SEED100_TARGETED_V10:
                active_stage = UpdateOperationStage.RUNTIME_PREFLIGHT
                self._stage(operation_id, active_stage, 1, "正在验证 CUDA 深度模型运行环境")
                self.cuda_preflight()
            self._stage(
                operation_id, active_stage, 2, "正在读取版本化复权数据与异常隔离结果"
            )

            def progress(stage: str, percent: int, detail: str) -> None:
                nonlocal active_stage
                active_stage = UpdateOperationStage(stage)
                self._stage(operation_id, active_stage, percent, detail)

            trainer: TargetedModelTrainer | LiveModelTrainer
            if operation.requested_bundles is not None and self._uses_default_trainer:
                trainer = self.targeted_trainer_factory(
                    operation,
                    lambda: (
                        (current := self.store.get(operation_id)) is not None
                        and current.status is UpdateOperationStatus.CANCEL_REQUESTED
                    ),
                )
            else:
                trainer = self.trainer_factory()
            if isinstance(trainer, LiveModelTrainer):
                training = trainer.train(
                    progress_callback=progress,
                    recipe_version=(
                        operation.candidate_recipe_version or CANDIDATE_RECIPE_V1
                    ),
                )
            else:
                training = trainer.train(progress_callback=progress)
            current = self.store.get(operation_id)
            if (
                current is not None
                and current.status is UpdateOperationStatus.CANCEL_REQUESTED
            ):
                return self._finalize_cancellation(current)
            active_stage = UpdateOperationStage.CHAMPION_PUBLICATION
            self._stage(
                operation_id,
                active_stage,
                92,
                (
                    "候选已冻结，等待独立晋升盲测"
                    if training.get("development_all_gates_passed")
                    else "开发门禁未全部通过；研究候选已保存"
                ),
            )
            active_stage = UpdateOperationStage.FINAL_SNAPSHOT_PUBLICATION
            self._stage(operation_id, active_stage, 96, "正在发布不可变预测快照")
            publication = self.publisher_factory().publish()
            readiness = str(publication["prediction_readiness"])
            completed_bundles = training.get("completed_bundles")
            if not isinstance(completed_bundles, (list, tuple)):
                completed_bundles = operation.requested_bundles or ()
            shadow_package_id = training.get("shadow_package_id")
            blind_window_id = training.get("blind_window_id")
            evidence_scope = training.get("evidence_scope")
            finished = datetime.now(UTC)
            return self.store.update(
                operation_id,
                status=(
                    UpdateOperationStatus.SUCCEEDED_FORECAST_READY
                    if readiness == "forecast_ready"
                    else UpdateOperationStatus.SUCCEEDED_DATA_ONLY
                ),
                stage=active_stage,
                progress_percent=100,
                phase_status="completed",
                phase_detail=readiness,
                snapshot_id=str(publication["snapshot_id"]),
                data_hash=str(
                    training.get("candidate_fingerprint") or operation.data_hash
                ),
                model_version=str(training["model_version"]),
                automatic_promotion=(str(training["automatic_promotion"])),
                completed_bundles=tuple(completed_bundles),
                shadow_package_id=(
                    str(shadow_package_id)
                    if isinstance(shadow_package_id, str)
                    else None
                ),
                blind_window_id=(
                    str(blind_window_id) if isinstance(blind_window_id, str) else None
                ),
                evidence_scope=(
                    str(evidence_scope) if isinstance(evidence_scope, str) else None
                ),
                finished_at=finished,
            )
        except M7TargetedTrainingCancelled:
            current = self.store.get(operation_id) or operation
            return self._finalize_cancellation(current)
        except Exception as error:
            if (
                training is not None
                and training.get("automatic_promotion") == "promoted"
                and active_stage
                in {
                    UpdateOperationStage.CHAMPION_PUBLICATION,
                    UpdateOperationStage.FINAL_SNAPSHOT_PUBLICATION,
                }
            ):
                self._rollback_failed_publication(training)
            self.store.update(
                operation_id,
                stage=active_stage,
                phase_status="failed",
                phase_detail=str(error),
            )
            if operation.requested_bundles is not None:
                M7AuditLog(self.paths).append(
                    event_type="training_failed",
                    recorded_at=datetime.now(UTC),
                    actor="model-training-worker",
                    run_id=operation_id,
                    bundle_id=None,
                    package_sha256=None,
                    detail={"reason": str(error), "stage": active_stage.value},
                )
            return self.store.update(
                operation_id,
                status=UpdateOperationStatus.FAILED,
                error_code=_training_error_code(error),
                diagnostic_detail=str(error),
                retry_action="查看失败阶段与训练报告；修复数据或标签后重新提交。",
                retry_recommendation="不要修改门禁阈值或重复消费晋升窗口。",
                automatic_promotion=(
                    "kept_current" if operation.requested_bundles is not None else None
                ),
                finished_at=datetime.now(UTC),
            )

    def _finalize_cancellation(self, operation: UpdateOperation) -> UpdateOperation:
        now = datetime.now(UTC)
        if operation.requested_bundles is not None:
            M7AuditLog(self.paths).append_once(
                event_type="training_cancelled",
                recorded_at=now,
                actor="model-training-worker",
                run_id=operation.operation_id,
                bundle_id=None,
                package_sha256=None,
                detail={"reason": "cancel_requested"},
            )
        return self.store.update(
            operation.operation_id,
            status=UpdateOperationStatus.CANCELLED,
            cancelled_at=now,
            finished_at=now,
            active_trial_id=None,
            automatic_promotion=(
                "kept_current"
                if operation.requested_bundles is not None
                else operation.automatic_promotion
            ),
        )

    def _stage(
        self,
        operation_id: str,
        stage: UpdateOperationStage,
        progress: int,
        detail: str,
        *,
        active_trial_id: str | None = None,
    ) -> None:
        current = self.store.get(operation_id)
        if (
            current is not None
            and current.current_stage is not None
            and current.current_stage is not stage
        ):
            self.store.update(
                operation_id,
                stage=current.current_stage,
                phase_status="completed",
                phase_detail=(
                    current.phases[-1].detail if current.phases else "阶段完成"
                ),
            )
        self.store.update(
            operation_id,
            status=UpdateOperationStatus.RUNNING,
            stage=stage,
            progress_percent=progress,
            phase_status="running",
            phase_detail=detail,
            active_trial_id=active_trial_id,
        )

    def _rollback_failed_publication(self, training: dict[str, object]) -> None:
        """Restore every promoted component when immutable publication fails."""

        registry = LocalModelRegistry(self.paths.models / "registry")
        component_releases = training.get("component_releases")
        if isinstance(component_releases, list):
            for component in component_releases:
                if not isinstance(component, dict) or not component.get("gate_passed"):
                    continue
                task = component.get("task")
                horizon = component.get("horizon")
                previous = component.get("previous_champion_version")
                if not isinstance(task, str) or not isinstance(horizon, int):
                    continue
                if isinstance(previous, str):
                    registry.rollback(
                        task=task,
                        horizon=horizon,
                        target_version=previous,
                        reason=(
                            "automatic task-component rollback after formal "
                            "snapshot publication failure"
                        ),
                        recorded_at=datetime.now(UTC),
                    )
                else:
                    registry.deactivate_champion(
                        task=task,
                        horizon=horizon,
                        reason=(
                            "automatic task-component deactivation after first "
                            "formal snapshot publication failure"
                        ),
                        recorded_at=datetime.now(UTC),
                    )

        previous = training.get("previous_champion_version")
        if isinstance(previous, str):
            registry.rollback(
                task="forecast_bundle",
                horizon=20,
                target_version=previous,
                reason="automatic rollback after formal snapshot publication failure",
                recorded_at=datetime.now(UTC),
            )
        else:
            registry.deactivate_champion(
                task="forecast_bundle",
                horizon=20,
                reason=(
                    "automatic deactivation after first formal snapshot "
                    "publication failure"
                ),
                recorded_at=datetime.now(UTC),
            )


def _training_error_code(error: Exception) -> str:
    if isinstance(error, M7CudaPreflightError):
        return "m7_cuda_preflight_failed"
    value = str(error).strip()
    known = {
        "label_degenerate",
        "promotion_window_already_consumed",
        "unexplained_corporate_action_return",
        "live_pipeline_busy",
    }
    return value if value in known else "model_training_failed"


__all__ = [
    "CandidateRecipeExhaustedError",
    "CandidateRecipeVersionMismatchError",
    "ModelTrainingCoordinator",
    "ModelTrainingOperationStore",
    "PromotionWindowAlreadyConsumedError",
    "PromotionWindowNotReadyError",
]
