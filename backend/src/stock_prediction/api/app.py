"""FastAPI application exposing persisted, read-only platform state."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, NoReturn
from uuid import uuid4

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from stock_prediction.api.dependencies import (
    CapabilityStore,
    ComponentStateProvider,
    ResearchReadRepository,
    persisted_component_state_provider,
)
from stock_prediction.api.read_models import CapabilityResponse, ErrorResponse
from stock_prediction.api.queries import EmptyParameters
from stock_prediction.api.operations_router import (
    ModelPromotionController,
    ModelTrainingController,
    OperationController,
    build_operations_router,
)
from stock_prediction.contracts.m7_status import M7LifecycleStatus
from stock_prediction.api.errors import ApiError
from stock_prediction.api.research_router import build_research_router
from stock_prediction.api.options_router import build_options_router
from stock_prediction.api.pulse_router import build_pulse_router
from stock_prediction.api.option_refresh_router import build_option_refresh_router
from stock_prediction.options.store import OptionSnapshotStore
from stock_prediction.contracts.options import OptionStockReturn
from stock_prediction.capabilities.store import JsonCapabilityStore
from stock_prediction.contracts.capability import CapabilitySnapshot, CapabilityStatus
from stock_prediction.contracts.health import (
    Completeness,
    ComponentHealth,
    Connectivity,
    Freshness,
    HealthResponse,
    Readiness,
)
from stock_prediction.serving.repository import (
    BacktestNotFound,
    SnapshotIntegrityError,
    SnapshotNotFound,
    SnapshotVersionConflict,
    SymbolNotInUniverse,
    UnavailableResearchRepository,
    UnknownSymbol,
)


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _default_request_id() -> str:
    return str(uuid4())


def _fallback_components() -> list[ComponentHealth]:
    return [
        ComponentHealth(
            component="opend",
            connectivity=Connectivity.DISCONNECTED,
            freshness=Freshness.UNKNOWN,
            completeness=Completeness.UNKNOWN,
            readiness=Readiness.BLOCKED,
        )
    ]


def _safe_clock(clock: Callable[[], datetime]) -> datetime:
    try:
        generated_at = clock()
        if not isinstance(generated_at, datetime):
            raise TypeError("clock must return datetime")
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            raise ValueError("clock must return timezone-aware datetime")
        return generated_at.astimezone(UTC)
    except Exception:
        return datetime.now(UTC)


def _safe_request_id(request_id_factory: Callable[[], str]) -> str:
    try:
        request_id = request_id_factory()
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request ID must be a non-empty string")
        return request_id
    except Exception:
        return str(uuid4())


def _safe_components(
    state_provider: ComponentStateProvider,
) -> list[ComponentHealth]:
    try:
        supplied = state_provider()
        if not isinstance(supplied, list) or not supplied:
            raise ValueError("component state must be a non-empty list")
        validated = [
            ComponentHealth.model_validate(component.model_dump(), strict=True)
            for component in supplied
            if isinstance(component, ComponentHealth)
        ]
        if len(validated) != len(supplied):
            raise ValueError("component state contains an invalid entry")
        return validated
    except Exception:
        return _fallback_components()


def _health_content(
    state_provider: ComponentStateProvider,
    clock: Callable[[], datetime],
    request_id_factory: Callable[[], str],
) -> dict[str, object]:
    try:
        components = _safe_components(state_provider)
        service_status, data_status = _health_statuses(components)
        component_payloads = [
            component.model_dump(mode="json", exclude_none=True)
            for component in components
        ]
    except Exception:
        components = _fallback_components()
        service_status, data_status = Readiness.DEGRADED, Readiness.BLOCKED
        component_payloads = [
            component.model_dump(mode="json", exclude_none=True)
            for component in components
        ]
    generated_at = _safe_clock(clock).isoformat().replace("+00:00", "Z")
    return {
        "request_id": _safe_request_id(request_id_factory),
        "generated_at": generated_at,
        "service_status": service_status.value,
        "data_status": data_status.value,
        "components": component_payloads,
    }


def _emergency_health_content() -> dict[str, object]:
    return {
        "request_id": str(uuid4()),
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "service_status": Readiness.DEGRADED.value,
        "data_status": Readiness.BLOCKED.value,
        "components": [
            {
                "component": "opend",
                "connectivity": Connectivity.DISCONNECTED.value,
                "freshness": Freshness.UNKNOWN.value,
                "completeness": Completeness.UNKNOWN.value,
                "readiness": Readiness.BLOCKED.value,
            }
        ],
    }


def _health_statuses(
    components: list[ComponentHealth],
) -> tuple[Readiness, Readiness]:
    readiness = {component.readiness for component in components}
    if Readiness.BLOCKED in readiness:
        return Readiness.DEGRADED, Readiness.BLOCKED
    if Readiness.DEGRADED in readiness:
        return Readiness.DEGRADED, Readiness.DEGRADED
    return Readiness.READY, Readiness.READY


def _capability_status(snapshot: CapabilitySnapshot) -> Readiness:
    statuses = {row.status for row in snapshot.capabilities}
    if statuses == {CapabilityStatus.AVAILABLE}:
        return Readiness.READY
    if CapabilityStatus.AVAILABLE in statuses:
        return Readiness.DEGRADED
    return Readiness.BLOCKED


def _allowance_status(snapshot: CapabilitySnapshot) -> CapabilityStatus:
    statuses = {row.status for row in snapshot.capabilities}
    for status in (
        CapabilityStatus.AVAILABLE,
        CapabilityStatus.DENIED,
        CapabilityStatus.UNAVAILABLE,
    ):
        if status in statuses:
            return status
    return CapabilityStatus.UNKNOWN


def create_app(
    *,
    options_store: OptionSnapshotStore | None = None,
    option_stock_returns_provider: Callable[
        [str, datetime], tuple[OptionStockReturn, ...]
    ]
    | None = None,
    capability_store: CapabilityStore | None = None,
    research_repository: ResearchReadRepository | None = None,
    state_provider: ComponentStateProvider | None = None,
    operation_controller: OperationController | None = None,
    model_training_controller: ModelTrainingController | None = None,
    model_promotion_controller: ModelPromotionController | None = None,
    m7_status_provider: Callable[[], M7LifecycleStatus] | None = None,
    clock: Callable[[], datetime] = _default_clock,
    request_id_factory: Callable[[], str] = _default_request_id,
) -> FastAPI:
    """Create the API with injected, side-effect-free read dependencies."""

    store = capability_store if capability_store is not None else JsonCapabilityStore()
    repository = (
        research_repository
        if research_repository is not None
        else UnavailableResearchRepository()
    )
    provide_states = (
        state_provider
        if state_provider is not None
        else persisted_component_state_provider(store, clock)
    )
    application = FastAPI(title="Stock Prediction API")

    def error_response(
        *,
        status_code: int,
        error_code: str,
        message: str,
        retryable: bool,
        affected_scope: list[str],
    ) -> JSONResponse:
        response = ErrorResponse(
            request_id=_safe_request_id(request_id_factory),
            generated_at=_safe_clock(clock),
            data_status=Readiness.BLOCKED,
            error_code=error_code,
            message=message,
            retryable=retryable,
            affected_scope=affected_scope,
        )
        return JSONResponse(
            status_code=status_code,
            content=response.model_dump(mode="json"),
        )

    def unavailable(message: str) -> NoReturn:
        raise ApiError(
            status_code=503,
            error_code="capabilities_unavailable",
            message=message,
            retryable=True,
            affected_scope=["data_capabilities"],
        )

    @application.exception_handler(ApiError)
    async def handle_api_error(_request: Request, error: ApiError) -> JSONResponse:
        return error_response(
            status_code=error.status_code,
            error_code=error.error_code,
            message=error.message,
            retryable=error.retryable,
            affected_scope=error.affected_scope,
        )

    @application.exception_handler(UnknownSymbol)
    async def handle_unknown_symbol(
        _request: Request, _error: UnknownSymbol
    ) -> JSONResponse:
        return error_response(
            status_code=404,
            error_code="unknown_symbol",
            message="The requested symbol is unknown",
            retryable=False,
            affected_scope=["symbol"],
        )

    @application.exception_handler(SymbolNotInUniverse)
    async def handle_not_in_universe(
        _request: Request, _error: SymbolNotInUniverse
    ) -> JSONResponse:
        return error_response(
            status_code=404,
            error_code="not_in_universe",
            message="The requested symbol is not in the resolved universe",
            retryable=False,
            affected_scope=["symbol", "universe"],
        )

    @application.exception_handler(BacktestNotFound)
    async def handle_backtest_not_found(
        _request: Request, _error: BacktestNotFound
    ) -> JSONResponse:
        return error_response(
            status_code=404,
            error_code="backtest_not_found",
            message="No matching backtest evidence is available",
            retryable=False,
            affected_scope=["backtest"],
        )

    @application.exception_handler(SnapshotNotFound)
    async def handle_snapshot_not_found(
        _request: Request, _error: SnapshotNotFound
    ) -> JSONResponse:
        return error_response(
            status_code=503,
            error_code="research_snapshot_unavailable",
            message="No matching research snapshot is available",
            retryable=True,
            affected_scope=["research_snapshot"],
        )

    @application.exception_handler(SnapshotVersionConflict)
    async def handle_snapshot_conflict(
        _request: Request, _error: SnapshotVersionConflict
    ) -> JSONResponse:
        return error_response(
            status_code=409,
            error_code="snapshot_version_conflict",
            message="The requested versions conflict with the research snapshot",
            retryable=False,
            affected_scope=["research_snapshot"],
        )

    @application.exception_handler(SnapshotIntegrityError)
    async def handle_snapshot_integrity(
        _request: Request, _error: SnapshotIntegrityError
    ) -> JSONResponse:
        return error_response(
            status_code=503,
            error_code="snapshot_integrity_error",
            message="The research snapshot failed integrity verification",
            retryable=True,
            affected_scope=["research_snapshot"],
        )

    @application.exception_handler(RequestValidationError)
    async def handle_request_validation(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return error_response(
            status_code=422,
            error_code="validation_error",
            message="The request parameters are invalid",
            retryable=False,
            affected_scope=["request"],
        )

    @application.exception_handler(ValueError)
    async def handle_value_error(_request: Request, _error: ValueError) -> JSONResponse:
        return error_response(
            status_code=422,
            error_code="validation_error",
            message="The request parameters are invalid",
            retryable=False,
            affected_scope=["request"],
        )

    @application.exception_handler(ResponseValidationError)
    @application.exception_handler(ValidationError)
    async def handle_corrupt_response(
        _request: Request, _error: Exception
    ) -> JSONResponse:
        return error_response(
            status_code=503,
            error_code="snapshot_integrity_error",
            message="The research snapshot failed integrity verification",
            retryable=True,
            affected_scope=["research_snapshot"],
        )

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        _request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        is_not_found = error.status_code == 404
        return error_response(
            status_code=error.status_code,
            error_code="not_found" if is_not_found else "http_error",
            message="Resource not found" if is_not_found else "Request not allowed",
            retryable=False,
            affected_scope=["api"],
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(
        _request: Request, _error: Exception
    ) -> JSONResponse:
        return error_response(
            status_code=500,
            error_code="internal_error",
            message="The request could not be completed",
            retryable=True,
            affected_scope=["api"],
        )

    @application.get(
        "/api/v1/health",
        response_model=HealthResponse,
        responses={422: {"model": ErrorResponse}},
    )
    def health(_parameters: Annotated[EmptyParameters, Query()]) -> JSONResponse:
        try:
            content = _health_content(provide_states, clock, request_id_factory)
        except Exception:
            content = _emergency_health_content()
        return JSONResponse(
            status_code=200,
            content=content,
        )

    @application.get(
        "/api/v1/data-capabilities",
        response_model=CapabilityResponse,
        responses={422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    )
    def data_capabilities(
        _parameters: Annotated[EmptyParameters, Query()],
    ) -> CapabilityResponse:
        try:
            snapshot = store.load_latest()
        except Exception:
            unavailable("Capability snapshots could not be read")
        if snapshot is None:
            unavailable("No capability snapshot is available")
        return CapabilityResponse(
            request_id=_safe_request_id(request_id_factory),
            generated_at=_safe_clock(clock),
            data_status=_capability_status(snapshot),
            snapshot=snapshot,
            current_health=tuple(_safe_components(provide_states)),
            allowance_status=_allowance_status(snapshot),
        )

    application.include_router(build_option_refresh_router(
        options_store if options_store is not None else OptionSnapshotStore(),
        lambda: _safe_clock(clock),
    ))
    application.include_router(
        build_pulse_router(
            options_store if options_store is not None else OptionSnapshotStore(),
            lambda: _safe_clock(clock),
            repository,
        ),
        prefix="/api/v1",
    )
    application.include_router(
        build_options_router(
            store=options_store if options_store is not None else OptionSnapshotStore(),
            clock=lambda: _safe_clock(clock),
            request_id_factory=lambda: _safe_request_id(request_id_factory),
            repository=repository,
            component_provider=lambda: _safe_components(provide_states),
            stock_returns_provider=option_stock_returns_provider,
        )
    )
    application.include_router(
        build_research_router(
            repository=repository,
            component_provider=lambda: _safe_components(provide_states),
            clock=lambda: _safe_clock(clock),
            request_id_factory=lambda: _safe_request_id(request_id_factory),
        )
    )
    application.include_router(
        build_operations_router(
            operation_controller,
            model_training_controller,
            model_promotion_controller,
            m7_status_provider,
        )
    )

    return application


app = create_app()
