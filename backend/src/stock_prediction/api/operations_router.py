"""Local-only control endpoints for durable EOD research updates."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, status

from stock_prediction.api.errors import ApiError
from stock_prediction.api.display_refs import operation_display_refs
from stock_prediction.api.read_models import ErrorResponse
from stock_prediction.contracts.m7_status import M7LifecycleStatus
from stock_prediction.contracts.operations import (
    CandidateTrainingRequest,
    EodWorkerState,
    EodWorkerStatus,
    ModelTrainingReadiness,
    ModelCapabilityBundle,
    ModelPromotionRequest,
    TrainingTrigger,
    UpdateOperation,
)
from stock_prediction.live.model_operations import (
    CandidateRecipeExhaustedError,
    CandidateRecipeVersionMismatchError,
    PromotionWindowAlreadyConsumedError,
    PromotionWindowNotReadyError,
)
from stock_prediction.live.operation_store import OperationCancellationConflictError
from stock_prediction.live.m7_partial_research import (
    M7PartialResearchResult,
    M7PartialResearchUnavailable,
)
from stock_prediction.live.promotion_operations import (
    ModelPromotionRequestRequiredError,
    PromotionApprovalUnavailableError,
    PromotionIncumbentConflictError,
    PromotionPackageHashMismatchError,
)


_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


class OperationController(Protocol):
    def submit(self) -> tuple[UpdateOperation, bool]: ...

    def get(self, operation_id: str) -> UpdateOperation | None: ...

    def latest(self) -> UpdateOperation | None: ...

    def request_cancel(self, operation_id: str) -> UpdateOperation: ...

    def worker_status(self) -> EodWorkerStatus: ...


class ModelTrainingController(OperationController, Protocol):
    def submit(
        self,
        *,
        candidate_recipe_version: str | None = None,
        bundles: tuple[ModelCapabilityBundle, ...] | None = None,
        trigger: TrainingTrigger = "manual",
    ) -> tuple[UpdateOperation, bool]: ...

    def readiness(self) -> ModelTrainingReadiness: ...

    def partial_result(
        self, operation_id: str, symbol: str
    ) -> M7PartialResearchResult: ...


class ModelPromotionController(OperationController, Protocol):
    def submit_promotion(
        self,
        request: ModelPromotionRequest,
    ) -> tuple[UpdateOperation, bool]: ...

    def readiness(self) -> ModelTrainingReadiness: ...


def build_operations_router(
    controller: OperationController | None,
    model_training_controller: ModelTrainingController | None = None,
    model_promotion_controller: ModelPromotionController | None = None,
    m7_status_provider: Callable[[], M7LifecycleStatus] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/operations", tags=["operations"])

    @router.get(
        "/m7-status",
        response_model=M7LifecycleStatus,
        responses=_ERROR_RESPONSES,
    )
    def m7_status() -> M7LifecycleStatus:
        if m7_status_provider is None:
            raise ApiError(
                status_code=503,
                error_code="m7_status_unavailable",
                message="The M7 lifecycle status provider is unavailable",
                retryable=True,
                affected_scope=["m7_lifecycle_status"],
            )
        return m7_status_provider()

    @router.post(
        "/eod-update",
        response_model=UpdateOperation,
        status_code=status.HTTP_202_ACCEPTED,
        responses=_ERROR_RESPONSES,
    )
    def submit_eod_update() -> UpdateOperation:
        if controller is None:
            raise ApiError(
                status_code=503,
                error_code="eod_worker_unavailable",
                message="The EOD update worker is unavailable",
                retryable=True,
                affected_scope=["eod_update_worker"],
            )
        worker = controller.worker_status()
        if worker.state is not EodWorkerState.READY:
            raise ApiError(
                status_code=503,
                error_code="eod_worker_unavailable",
                message="The EOD update worker is unavailable",
                retryable=True,
                affected_scope=["eod_update_worker"],
            )
        operation, _created = controller.submit()
        return operation_display_refs(operation)

    @router.get(
        "/eod-update/latest",
        response_model=UpdateOperation | None,
        responses=_ERROR_RESPONSES,
    )
    def latest_eod_update() -> UpdateOperation | None:
        # An absent queue or absent prior run is normal on an acceptance/demo
        # snapshot.  Returning a nullable 200 keeps the passive polling endpoint
        # quiet while the mutating POST remains unavailable.
        operation = None if controller is None else controller.latest()
        return None if operation is None else operation_display_refs(operation)

    @router.get(
        "/eod-update/worker",
        response_model=EodWorkerStatus,
        responses=_ERROR_RESPONSES,
    )
    def eod_update_worker() -> EodWorkerStatus:
        if controller is None:
            return EodWorkerStatus(
                state=EodWorkerState.OFFLINE,
                detail="eod_worker_queue_unavailable",
            )
        return controller.worker_status()

    @router.post(
        "/model-training",
        response_model=UpdateOperation,
        status_code=status.HTTP_202_ACCEPTED,
        responses=_ERROR_RESPONSES,
    )
    def submit_model_training(
        request: CandidateTrainingRequest | None = None,
    ) -> UpdateOperation:
        if model_training_controller is None:
            raise ApiError(
                status_code=503,
                error_code="model_worker_unavailable",
                message="The model training worker is unavailable",
                retryable=True,
                affected_scope=["model_training_worker"],
            )
        if request is not None and request.trigger != "manual":
            raise ApiError(
                status_code=403,
                error_code="training_trigger_unauthorized",
                message="Scheduled and drift triggers are internal-only",
                retryable=False,
                affected_scope=["model_training_trigger"],
            )
        worker = model_training_controller.worker_status()
        if worker.state is not EodWorkerState.READY:
            raise ApiError(
                status_code=503,
                error_code="model_worker_unavailable",
                message="The model training worker is unavailable",
                retryable=True,
                affected_scope=["model_training_worker"],
            )
        try:
            operation, _created = model_training_controller.submit(
                candidate_recipe_version=(
                    request.candidate_recipe_version if request is not None else None
                ),
                bundles=(request.bundles if request is not None else None),
                trigger=(request.trigger if request is not None else "manual"),
            )
        except CandidateRecipeExhaustedError as error:
            raise ApiError(
                status_code=409,
                error_code="candidate_recipe_exhausted",
                message=str(error),
                retryable=False,
                affected_scope=["candidate_recipe", "model_training"],
            ) from error
        except (CandidateRecipeVersionMismatchError, ValueError) as error:
            raise ApiError(
                status_code=409,
                error_code="candidate_recipe_version_mismatch",
                message=str(error),
                retryable=False,
                affected_scope=["candidate_recipe", "model_training"],
            ) from error
        except PromotionWindowAlreadyConsumedError as error:
            raise ApiError(
                status_code=409,
                error_code="promotion_window_already_consumed",
                message=str(error),
                retryable=False,
                affected_scope=["promotion_window", "model_training"],
            ) from error
        except PromotionWindowNotReadyError as error:
            raise ApiError(
                status_code=409,
                error_code="promotion_window_not_ready",
                message=str(error),
                retryable=True,
                affected_scope=["promotion_window", "model_training"],
            ) from error
        return operation_display_refs(operation)

    @router.get(
        "/model-training/latest",
        response_model=UpdateOperation | None,
        responses=_ERROR_RESPONSES,
    )
    def latest_model_training() -> UpdateOperation | None:
        operation = (
            None
            if model_training_controller is None
            else model_training_controller.latest()
        )
        return None if operation is None else operation_display_refs(operation)

    @router.get(
        "/model-training/{operation_id}/partial-result",
        response_model=M7PartialResearchResult,
        responses=_ERROR_RESPONSES,
    )
    def model_training_partial_result(
        operation_id: str,
        symbol: str,
    ) -> M7PartialResearchResult:
        if model_training_controller is None:
            return M7PartialResearchUnavailable(reason="m7_partial_run_not_found")
        return model_training_controller.partial_result(operation_id, symbol)

    @router.get(
        "/model-training/worker",
        response_model=EodWorkerStatus,
        responses=_ERROR_RESPONSES,
    )
    def model_training_worker() -> EodWorkerStatus:
        if model_training_controller is None:
            return EodWorkerStatus(
                state=EodWorkerState.OFFLINE,
                detail="model_worker_queue_unavailable",
            )
        return model_training_controller.worker_status()

    @router.get(
        "/model-training/readiness",
        response_model=ModelTrainingReadiness,
        responses=_ERROR_RESPONSES,
    )
    def model_training_readiness() -> ModelTrainingReadiness:
        if model_training_controller is None:
            raise ApiError(
                status_code=503,
                error_code="model_worker_unavailable",
                message="The model training readiness service is unavailable",
                retryable=True,
                affected_scope=["model_training_worker"],
            )
        return model_training_controller.readiness()

    @router.post(
        "/model-promotion",
        response_model=UpdateOperation,
        status_code=status.HTTP_202_ACCEPTED,
        responses=_ERROR_RESPONSES,
    )
    def submit_model_promotion(
        request: ModelPromotionRequest | None = None,
    ) -> UpdateOperation:
        if model_promotion_controller is None:
            raise ApiError(
                status_code=503,
                error_code="model_worker_unavailable",
                message="The model promotion worker is unavailable",
                retryable=True,
                affected_scope=["model_promotion_worker"],
            )
        if model_promotion_controller.worker_status().state is not EodWorkerState.READY:
            raise ApiError(
                status_code=503,
                error_code="model_worker_unavailable",
                message="The model promotion worker is unavailable",
                retryable=True,
                affected_scope=["model_promotion_worker"],
            )
        try:
            operation, _created = (
                model_promotion_controller.submit()
                if request is None
                else model_promotion_controller.submit_promotion(request)
            )
        except ModelPromotionRequestRequiredError as error:
            raise ApiError(
                status_code=409,
                error_code="model_promotion_request_required",
                message=str(error),
                retryable=False,
                affected_scope=["model_promotion_request"],
            ) from error
        except PromotionPackageHashMismatchError as error:
            raise ApiError(
                status_code=409,
                error_code="model_package_hash_mismatch",
                message=str(error),
                retryable=False,
                affected_scope=["model_package", "model_promotion"],
            ) from error
        except PromotionIncumbentConflictError as error:
            raise ApiError(
                status_code=409,
                error_code="model_incumbent_conflict",
                message=str(error),
                retryable=False,
                affected_scope=["model_incumbent", "model_promotion"],
            ) from error
        except PromotionApprovalUnavailableError as error:
            raise ApiError(
                status_code=409,
                error_code="model_promotion_not_awaiting_approval",
                message=str(error),
                retryable=False,
                affected_scope=["model_promotion_evidence"],
            ) from error
        except PromotionWindowAlreadyConsumedError as error:
            raise ApiError(
                status_code=409,
                error_code="promotion_window_already_consumed",
                message=str(error),
                retryable=False,
                affected_scope=["promotion_window", "model_promotion"],
            ) from error
        except PromotionWindowNotReadyError as error:
            raise ApiError(
                status_code=409,
                error_code="promotion_window_not_ready",
                message=str(error),
                retryable=True,
                affected_scope=["promotion_window", "model_promotion"],
            ) from error
        return operation_display_refs(operation)

    @router.get(
        "/model-promotion/latest",
        response_model=UpdateOperation | None,
        responses=_ERROR_RESPONSES,
    )
    def latest_model_promotion() -> UpdateOperation | None:
        operation = (
            None
            if model_promotion_controller is None
            else model_promotion_controller.latest()
        )
        return None if operation is None else operation_display_refs(operation)

    @router.get(
        "/model-promotion/worker",
        response_model=EodWorkerStatus,
        responses=_ERROR_RESPONSES,
    )
    def model_promotion_worker() -> EodWorkerStatus:
        if model_promotion_controller is None:
            return EodWorkerStatus(
                state=EodWorkerState.OFFLINE,
                detail="model_promotion_worker_queue_unavailable",
            )
        return model_promotion_controller.worker_status()

    @router.post(
        "/{operation_id}/cancel",
        response_model=UpdateOperation,
        responses=_ERROR_RESPONSES,
    )
    def cancel_operation(operation_id: str) -> UpdateOperation:
        controllers = tuple(
            item
            for item in (
                controller,
                model_training_controller,
                model_promotion_controller,
            )
            if item is not None
        )
        for operation_controller in controllers:
            operation = operation_controller.get(operation_id)
            if operation is None:
                continue
            if operation.operation_type not in {"model_training", "model_promotion"}:
                raise ApiError(
                    status_code=409,
                    error_code="operation_not_cancellable",
                    message="Only governed model operations support cancellation",
                    retryable=False,
                    affected_scope=[operation_id],
                )
            try:
                return operation_display_refs(
                    operation_controller.request_cancel(operation_id)
                )
            except OperationCancellationConflictError as error:
                raise ApiError(
                    status_code=409,
                    error_code="operation_not_cancellable",
                    message=str(error),
                    retryable=False,
                    affected_scope=[operation_id],
                ) from error
        raise HTTPException(status_code=404, detail="Unknown operation")

    @router.get(
        "/{operation_id}",
        response_model=UpdateOperation,
        responses=_ERROR_RESPONSES,
    )
    def get_operation(operation_id: str) -> UpdateOperation:
        if controller is None:
            raise HTTPException(status_code=503, detail="Operation queue unavailable")
        operation = controller.get(operation_id)
        if operation is None and model_training_controller is not None:
            operation = model_training_controller.get(operation_id)
        if operation is None and model_promotion_controller is not None:
            operation = model_promotion_controller.get(operation_id)
        if operation is None:
            raise HTTPException(status_code=404, detail="Unknown operation")
        return operation_display_refs(operation)

    return router


__all__ = [
    "ModelPromotionController",
    "ModelTrainingController",
    "OperationController",
    "build_operations_router",
]
