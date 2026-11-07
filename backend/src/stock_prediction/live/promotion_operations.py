"""Durable queue for the independent one-time promotion blind test."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Callable
from uuid import uuid4

from filelock import FileLock, Timeout

from stock_prediction.contracts.operations import (
    EodWorkerStatus,
    ModelPromotionRequest,
    ModelTrainingReadiness,
    UpdateOperation,
    UpdateOperationStage,
    UpdateOperationStatus,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.locking import LiveWriterLease
from stock_prediction.live.m7_promotion import (
    M7PromotionService,
    load_active_m7_forecast_bundle,
)
from stock_prediction.live.model_operations import (
    PromotionWindowAlreadyConsumedError,
)
from stock_prediction.live.operation_store import EodOperationStore
from stock_prediction.live.promotion import LiveModelPromoter
from stock_prediction.live.promotion_windows import (
    PromotionWindowNotReadyError,
    model_training_readiness,
)
from stock_prediction.live.publication import LiveSnapshotPublisher
from stock_prediction.live.training import load_latest_training_report
from stock_prediction.modeling.m7_blind_gates import M7BlindGateDecision
from stock_prediction.modeling.m7_package import detect_m7_runtime_compatibility
from stock_prediction.modeling.report import file_sha256
from stock_prediction.operations.registry import LocalModelRegistry


_ACTIVE = {
    UpdateOperationStatus.QUEUED,
    UpdateOperationStatus.RUNNING,
    UpdateOperationStatus.CANCEL_REQUESTED,
}
ReadinessProvider = Callable[[LivePaths], ModelTrainingReadiness]
IncumbentProvider = Callable[[LivePaths, str], str | None]


class ModelPromotionRequestRequiredError(RuntimeError):
    """M7 packages require an explicit manual approval request."""


class PromotionPackageHashMismatchError(RuntimeError):
    """The approved package hash differs from immutable blind evidence."""


class PromotionIncumbentConflictError(RuntimeError):
    """The incumbent changed after the user reviewed the package."""


class PromotionApprovalUnavailableError(RuntimeError):
    """The requested bundle is not awaiting manual approval."""


class ModelPromotionOperationStore(EodOperationStore):
    def __init__(
        self,
        paths: LivePaths,
        *,
        readiness_provider: ReadinessProvider | None = None,
        incumbent_provider: IncumbentProvider | None = None,
    ) -> None:
        super().__init__(paths)
        self.directory = paths.root / "operations" / "model-promotion"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._mutation_lock = FileLock(str(self.directory / ".queue.lock"))
        self._readiness_provider = readiness_provider or model_training_readiness
        self._incumbent_provider = incumbent_provider or _current_incumbent

    def submit(
        self,
        *,
        completed_not_before: datetime | None = None,
        candidate_recipe_version: str | None = None,
    ) -> tuple[UpdateOperation, bool]:
        del completed_not_before, candidate_recipe_version
        try:
            report = load_latest_training_report(self.paths)
        except FileNotFoundError:
            report = None
        if isinstance(report, dict) and report.get("bundle_results"):
            raise ModelPromotionRequestRequiredError("model_promotion_request_required")
        with self._mutation_lock:
            latest = self.latest()
            if latest is not None and latest.status in _ACTIVE:
                return latest, False
            readiness = self.readiness()
            if readiness.state.value == "consumed":
                raise PromotionWindowAlreadyConsumedError(
                    "The frozen promotion window has already been consumed"
                )
            if not readiness.can_submit_promotion:
                raise PromotionWindowNotReadyError(
                    "Independent promotion evidence or a frozen candidate is unavailable"
                )
            now = datetime.now(UTC)
            operation = UpdateOperation(
                operation_id=(
                    f"model-promotion-{now:%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
                ),
                operation_type="model_promotion",
                status=UpdateOperationStatus.QUEUED,
                progress_percent=0,
                created_at=now,
                updated_at=now,
            )
            self.save(operation)
            return operation, True

    def submit_promotion(
        self,
        request: ModelPromotionRequest,
    ) -> tuple[UpdateOperation, bool]:
        binding = self._manual_approval_binding(request)
        with self._mutation_lock:
            latest = self.latest()
            if latest is not None and latest.status in _ACTIVE:
                if latest.promotion_request != request:
                    raise PromotionApprovalUnavailableError(
                        "different_model_promotion_is_active"
                    )
                return latest, False
            now = datetime.now(UTC)
            operation = UpdateOperation(
                operation_id=(
                    f"model-promotion-{now:%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
                ),
                operation_type="model_promotion",
                status=UpdateOperationStatus.QUEUED,
                progress_percent=0,
                data_hash=request.expected_package_sha256,
                model_version=binding["package_id"],
                automatic_promotion="awaiting_manual_approval",
                requested_bundles=(request.bundle_id,),
                evidence_scope="blind_complete",
                shadow_package_id=binding["package_id"],
                promotion_request=request,
                created_at=now,
                updated_at=now,
            )
            self.save(operation)
            return operation, True

    def _manual_approval_binding(
        self,
        request: ModelPromotionRequest,
    ) -> dict[str, str]:
        try:
            report = load_latest_training_report(self.paths)
        except FileNotFoundError as error:
            raise PromotionApprovalUnavailableError(
                "targeted_training_report_unavailable"
            ) from error
        if report.get("run_id") != request.training_run_id:
            raise PromotionApprovalUnavailableError("training_run_id_mismatch")
        bundle_results = report.get("bundle_results")
        if not isinstance(bundle_results, dict):
            raise PromotionApprovalUnavailableError("bundle_results_unavailable")
        result = bundle_results.get(request.bundle_id)
        if not isinstance(result, dict):
            raise PromotionApprovalUnavailableError(
                "bundle_not_awaiting_manual_approval"
            )
        package_sha256 = result.get("package_sha256")
        package_id = result.get("package_id")
        if not isinstance(package_sha256, str) or not isinstance(package_id, str):
            raise PromotionApprovalUnavailableError("package_binding_unavailable")
        if package_sha256 != request.expected_package_sha256:
            raise PromotionPackageHashMismatchError("model_package_hash_mismatch")
        if result.get("promotion_state") != "awaiting_manual_approval":
            window_id = result.get("blind_window_id")
            if not isinstance(window_id, str) or not _blind_approval_available(
                self.paths,
                window_id=window_id,
                bundle_id=request.bundle_id,
                package_sha256=package_sha256,
            ):
                raise PromotionApprovalUnavailableError(
                    "bundle_not_awaiting_manual_approval"
                )
        current_incumbent = result.get("incumbent_id")
        if (
            current_incumbent != request.expected_incumbent_id
            or self._incumbent_provider(self.paths, request.bundle_id)
            != request.expected_incumbent_id
        ):
            raise PromotionIncumbentConflictError("model_incumbent_conflict")
        return {
            "package_id": package_id,
            "package_sha256": package_sha256,
        }

    def readiness(self) -> ModelTrainingReadiness:
        return self._readiness_provider(self.paths)

    def current_incumbent(self, bundle_id: str) -> str | None:
        return self._incumbent_provider(self.paths, bundle_id)


class ModelPromotionCoordinator:
    def __init__(
        self,
        paths: LivePaths,
        *,
        promoter_factory: Callable[[], LiveModelPromoter] | None = None,
        publisher_factory: Callable[[], LiveSnapshotPublisher] | None = None,
        readiness_provider: ReadinessProvider | None = None,
        incumbent_provider: IncumbentProvider | None = None,
    ) -> None:
        self.paths = paths
        self.store = ModelPromotionOperationStore(
            paths,
            readiness_provider=readiness_provider,
            incumbent_provider=incumbent_provider,
        )
        self.promoter_factory = promoter_factory or (lambda: LiveModelPromoter(paths))
        self.publisher_factory = publisher_factory or (
            lambda: LiveSnapshotPublisher(paths)
        )

    def submit(self) -> tuple[UpdateOperation, bool]:
        return self.store.submit()

    def submit_promotion(
        self,
        request: ModelPromotionRequest,
    ) -> tuple[UpdateOperation, bool]:
        return self.store.submit_promotion(request)

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

    def run_pending(self) -> UpdateOperation | None:
        operation = self.store.latest()
        if operation is None or operation.status not in _ACTIVE:
            return None
        if operation.status is UpdateOperationStatus.CANCEL_REQUESTED:
            now = datetime.now(UTC)
            return self.store.update(
                operation.operation_id,
                status=UpdateOperationStatus.CANCELLED,
                cancelled_at=now,
                finished_at=now,
            )
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
        if operation.promotion_request is not None:
            return self._run_m7_promotion(operation)
        self.store.update(
            operation_id,
            status=UpdateOperationStatus.RUNNING,
            started_at=operation.started_at or datetime.now(UTC),
            stage=UpdateOperationStage.PROMOTION_WINDOW,
            progress_percent=5,
            phase_status="running",
            phase_detail="正在验证候选锁并准备一次性盲测",
        )

        def progress(stage: str, percent: int, detail: str) -> None:
            self.store.update(
                operation_id,
                status=UpdateOperationStatus.RUNNING,
                stage=UpdateOperationStage(stage),
                progress_percent=percent,
                phase_status="running",
                phase_detail=detail,
            )

        try:
            result = self.promoter_factory().promote(progress_callback=progress)
            publication = self.publisher_factory().publish()
            forecast_ready = publication["prediction_readiness"] == "forecast_ready"
            return self.store.update(
                operation_id,
                status=(
                    UpdateOperationStatus.SUCCEEDED_FORECAST_READY
                    if forecast_ready
                    else UpdateOperationStatus.SUCCEEDED_DATA_ONLY
                ),
                stage=UpdateOperationStage.FINAL_SNAPSHOT_PUBLICATION,
                progress_percent=100,
                phase_status="completed",
                phase_detail=str(publication["prediction_readiness"]),
                snapshot_id=str(publication["snapshot_id"]),
                model_version=str(result["model_version"]),
                automatic_promotion=str(result["automatic_promotion"]),
                finished_at=datetime.now(UTC),
            )
        except Exception as error:
            return self.store.update(
                operation_id,
                status=UpdateOperationStatus.FAILED,
                stage=UpdateOperationStage.PROMOTION_WINDOW,
                progress_percent=100,
                phase_status="failed",
                phase_detail=str(error),
                error_code="model_promotion_failed",
                diagnostic_detail=str(error),
                retry_action="检查候选锁、晋升窗口完整性和盲测报告。",
                retry_recommendation="不要重新搜索候选或重复消费同一晋升窗口。",
                finished_at=datetime.now(UTC),
            )

    def _run_m7_promotion(self, operation: UpdateOperation) -> UpdateOperation:
        request = operation.promotion_request
        assert request is not None
        operation_id = operation.operation_id
        self.store.update(
            operation_id,
            status=UpdateOperationStatus.RUNNING,
            started_at=operation.started_at or datetime.now(UTC),
            stage=UpdateOperationStage.PROMOTION_GATE,
            progress_percent=20,
            phase_status="running",
            phase_detail="正在重新验证盲测报告、模型包和 incumbent",
        )
        try:
            service = M7PromotionService(
                self.paths,
                current_runtime=detect_m7_runtime_compatibility(),
                incumbent_provider=self.store.current_incumbent,
            )
            result = service.promote(request, promoted_at=datetime.now(UTC))
            return self.store.update(
                operation_id,
                status=UpdateOperationStatus.SUCCEEDED_DATA_ONLY,
                stage=UpdateOperationStage.CHAMPION_PUBLICATION,
                progress_percent=100,
                phase_status="completed",
                phase_detail="M7 capability bundle manual CAS promotion completed",
                model_version=result.forecast_bundle_id,
                automatic_promotion="manual_approved",
                finished_at=datetime.now(UTC),
            )
        except Exception as error:
            return self.store.update(
                operation_id,
                status=UpdateOperationStatus.FAILED,
                stage=UpdateOperationStage.CHAMPION_PUBLICATION,
                progress_percent=100,
                phase_status="failed",
                phase_detail=str(error),
                error_code="model_promotion_failed",
                diagnostic_detail=str(error),
                retry_action="刷新盲测报告和 incumbent 后重新人工确认。",
                retry_recommendation="不要复用 stale approval 请求。",
                finished_at=datetime.now(UTC),
            )


def _current_incumbent(paths: LivePaths, bundle_id: str) -> str | None:
    active = load_active_m7_forecast_bundle(paths)
    if active is not None:
        component = next(
            (item for item in active.components if item.bundle_id == bundle_id), None
        )
        if component is not None:
            return component.package_id
    registry = LocalModelRegistry(paths.models / "registry")
    task = "forecast_bundle" if bundle_id == "direction" else "range_exit"
    return registry.snapshot(task, 20).champion_version


def _blind_approval_available(
    paths: LivePaths,
    *,
    window_id: str,
    bundle_id: str,
    package_sha256: str,
) -> bool:
    directory = paths.models / "m7-blind" / window_id
    try:
        terminal = json.loads((directory / "terminal.json").read_text("utf-8"))
        report_hash = terminal["blind_report_hash"]
        report_path = directory / "reports" / f"{report_hash}.json"
        if file_sha256(report_path) != report_hash:
            return False
        report = json.loads(report_path.read_text("utf-8"))
        decision = M7BlindGateDecision.model_validate_json(
            json.dumps(report["decision"]), strict=True
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return bool(
        terminal.get("status") == "consumed"
        and report.get("window_id") == window_id
        and report.get("bundle_id") == bundle_id
        and report.get("package_sha256") == package_sha256
        and report.get("evidence_scope") == "blind_complete"
        and decision.status == "awaiting_manual_approval"
    )


__all__ = [
    "ModelPromotionRequestRequiredError",
    "ModelPromotionCoordinator",
    "ModelPromotionOperationStore",
    "PromotionApprovalUnavailableError",
    "PromotionIncumbentConflictError",
    "PromotionPackageHashMismatchError",
]
