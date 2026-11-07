"""Frozen, read-only FastAPI routes over persisted research snapshots."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Query

from stock_prediction.api.display_refs import enrich_display_refs

from stock_prediction.api.queries import (
    BacktestParameters,
    DataHealthParameters,
    ForecastParameters,
    HistoryParameters,
    HistoryBatchParameters,
    InstrumentParameters,
    ModelParameters,
    OverviewParameters,
    ScreenParameters,
    SnapshotParameters,
    SnapshotContextParameters,
    UniverseParameters,
    within_range,
)
from stock_prediction.api.read_models import ErrorResponse, ResearchResponse
from stock_prediction.contracts.health import (
    Completeness,
    ComponentHealth,
    Connectivity,
    Freshness,
    Readiness,
)
from stock_prediction.contracts.serving import (
    BacktestReadModel,
    DataHealthReadModel,
    ForecastAvailable,
    ForecastUnavailable,
    ForecastResult,
    HARD_ABSTAIN_SERVING_STATUSES,
    InstrumentHistory,
    HistoryBatchReadModel,
    SnapshotContextReadModel,
    InstrumentMetadata,
    InstrumentPage,
    ModelMatrixReadModel,
    OverviewReadModel,
    PredictionReadiness,
    ResearchDisplayMode,
    ResolvedSnapshot,
    ScreenPage,
    ServingStatus,
    UniverseReadModel,
)
from stock_prediction.serving.repository import ModelQuery, ResearchReadRepository
from stock_prediction.serving.status import overlay_forecast, overlay_snapshot


DataT = TypeVar("DataT")
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


def build_research_router(
    *,
    repository: ResearchReadRepository,
    component_provider: Callable[[], list[ComponentHealth]],
    clock: Callable[[], datetime],
    request_id_factory: Callable[[], str],
) -> APIRouter:
    """Bind the immutable repository and safe request metadata factories."""

    router = APIRouter(prefix="/api/v1")

    def resolve(parameters: SnapshotParameters) -> ResolvedSnapshot:
        return repository.resolve_snapshot(parameters.to_snapshot_query())

    def respond(
        *,
        snapshot: ResolvedSnapshot,
        data: DataT,
        components: Sequence[ComponentHealth] | None = None,
        serving_status: ServingStatus | None = None,
        effective_abstain: bool | None = None,
        exclusion_reason: tuple[str, ...] | None = None,
        evaluated_at: datetime | None = None,
    ) -> ResearchResponse[DataT]:
        request_time = evaluated_at if evaluated_at is not None else clock()
        states = tuple(components if components is not None else component_provider())
        status, reasons = _component_status(states)
        final_status = serving_status if serving_status is not None else status
        final_reasons = exclusion_reason if exclusion_reason is not None else reasons
        final_abstain = (
            effective_abstain
            if effective_abstain is not None
            else final_status in HARD_ABSTAIN_SERVING_STATUSES
        )
        model_rows = (
            repository.models(snapshot, ModelQuery()).rows
            if isinstance(data, (OverviewReadModel, ModelMatrixReadModel))
            and data.candidate_summary is not None
            and data.candidate_summary.failed_task_refs is None
            else ()
        )
        return ResearchResponse[DataT](
            request_id=request_id_factory(),
            generated_at=request_time,
            data_status=_data_status(states),
            serving_status=final_status,
            effective_abstain=final_abstain,
            evaluated_at=request_time,
            exclusion_reason=final_reasons,
            resolved_snapshot=snapshot,
            data=enrich_display_refs(data, model_rows=model_rows),
        )

    @router.get(
        "/snapshot-context",
        response_model=ResearchResponse[SnapshotContextReadModel],
        responses=ERROR_RESPONSES,
    )
    def snapshot_context(
        parameters: Annotated[SnapshotContextParameters, Query()],
    ) -> ResearchResponse[SnapshotContextReadModel]:
        snapshot = resolve(parameters)
        data = repository.snapshot_context(snapshot, tuple(parameters.symbols))
        components = tuple(component_provider())
        if snapshot.prediction_readiness is PredictionReadiness.DATA_ONLY:
            status, reasons = _component_status(components)
            return respond(
                snapshot=snapshot,
                data=data,
                components=components,
                serving_status=status,
                effective_abstain=True,
                exclusion_reason=tuple(
                    dict.fromkeys(("no_champion_candidate", *reasons))
                ),
            )
        serving = overlay_snapshot(
            repository.snapshot_serving_reference(snapshot),
            evaluated_at=clock(),
            component_states=components,
        )
        return respond(
            snapshot=snapshot,
            data=data,
            components=components,
            serving_status=serving.serving_status,
            effective_abstain=serving.effective_abstain,
            exclusion_reason=tuple(str(reason) for reason in serving.exclusion_reason),
            evaluated_at=serving.evaluated_at,
        )

    @router.get(
        "/history/batch",
        response_model=ResearchResponse[HistoryBatchReadModel],
        responses=ERROR_RESPONSES,
    )
    def history_batch(
        parameters: Annotated[HistoryBatchParameters, Query()],
    ) -> ResearchResponse[HistoryBatchReadModel]:
        snapshot = resolve(parameters)
        data = repository.history_batch(
            snapshot, tuple(parameters.symbols), parameters.to_repository_query()
        )
        return respond(snapshot=snapshot, data=data)

    @router.get(
        "/overview",
        response_model=ResearchResponse[OverviewReadModel],
        responses=ERROR_RESPONSES,
    )
    def overview(
        parameters: Annotated[OverviewParameters, Query()],
    ) -> ResearchResponse[OverviewReadModel]:
        snapshot = resolve(parameters)
        components = tuple(component_provider())
        evaluated_at = clock()
        data = repository.overview(snapshot, parameters.horizon)
        data = _overview_for_mode(data, parameters.mode)
        if snapshot.prediction_readiness is PredictionReadiness.DATA_ONLY:
            serving_status, component_reasons = _component_status(components)
            serving_effective_abstain = True
            serving_reasons = tuple(
                dict.fromkeys(("no_champion_candidate", *component_reasons))
            )
        else:
            serving = overlay_snapshot(
                repository.snapshot_serving_reference(snapshot),
                evaluated_at=evaluated_at,
                component_states=components,
            )
            serving_status = serving.serving_status
            serving_effective_abstain = serving.effective_abstain
            serving_reasons = tuple(str(reason) for reason in serving.exclusion_reason)
        if parameters.is_current and serving_status in HARD_ABSTAIN_SERVING_STATUSES:
            data = data.model_copy(update={"top_opportunities": ()})
        return respond(
            snapshot=snapshot,
            data=data,
            components=components,
            serving_status=serving_status,
            effective_abstain=serving_effective_abstain,
            exclusion_reason=serving_reasons,
            evaluated_at=evaluated_at,
        )

    @router.get(
        "/instruments",
        response_model=ResearchResponse[InstrumentPage],
        responses=ERROR_RESPONSES,
    )
    def instruments(
        parameters: Annotated[InstrumentParameters, Query()],
    ) -> ResearchResponse[InstrumentPage]:
        snapshot = resolve(parameters)
        data = repository.instruments(snapshot, parameters.to_repository_query())
        return respond(snapshot=snapshot, data=data)

    @router.get(
        "/instruments/{symbol}",
        response_model=ResearchResponse[InstrumentMetadata],
        responses=ERROR_RESPONSES,
    )
    def instrument(
        symbol: str,
        parameters: Annotated[SnapshotParameters, Query()],
    ) -> ResearchResponse[InstrumentMetadata]:
        snapshot = resolve(parameters)
        data = repository.instrument(snapshot, symbol)
        return respond(snapshot=snapshot, data=data)

    @router.get(
        "/screen",
        response_model=ResearchResponse[ScreenPage],
        responses=ERROR_RESPONSES,
    )
    def screen(
        parameters: Annotated[ScreenParameters, Query()],
    ) -> ResearchResponse[ScreenPage]:
        snapshot = resolve(parameters)
        components = tuple(component_provider())
        evaluated_at = clock()
        if snapshot.prediction_readiness is PredictionReadiness.DATA_ONLY:
            serving_status, component_reasons = _component_status(components)
            serving_effective_abstain = True
            serving_reasons = tuple(
                dict.fromkeys(("no_champion_candidate", *component_reasons))
            )
        else:
            serving = overlay_snapshot(
                repository.snapshot_serving_reference(snapshot),
                evaluated_at=evaluated_at,
                component_states=components,
            )
            serving_status = serving.serving_status
            serving_effective_abstain = serving.effective_abstain
            serving_reasons = tuple(str(reason) for reason in serving.exclusion_reason)
        exclude_rankings = (
            parameters.is_current and serving_status in HARD_ABSTAIN_SERVING_STATUSES
        )
        query = parameters.to_repository_query(exclude_rankings=exclude_rankings)
        if (
            snapshot.prediction_readiness is PredictionReadiness.DATA_ONLY
            and parameters.effective_abstain is None
        ):
            query = query.model_copy(
                update={"effective_abstain": True, "exclude_rankings": False}
            )
        data = repository.screen(snapshot, query)
        data = _screen_for_mode(data, parameters.mode)
        return respond(
            snapshot=snapshot,
            data=data,
            components=components,
            serving_status=serving_status,
            effective_abstain=serving_effective_abstain,
            exclusion_reason=serving_reasons,
            evaluated_at=evaluated_at,
        )

    @router.get(
        "/instruments/{symbol}/forecast",
        response_model=ResearchResponse[ForecastResult],
        responses=ERROR_RESPONSES,
    )
    def forecast(
        symbol: str,
        parameters: Annotated[ForecastParameters, Query()],
    ) -> ResearchResponse[ForecastResult]:
        snapshot = resolve(parameters)
        components = tuple(component_provider())
        data = repository.forecast(snapshot, symbol, parameters.horizon)
        if isinstance(data, ForecastUnavailable):
            data = _forecast_for_mode(data, parameters.mode)
        if isinstance(data, ForecastAvailable):
            evaluated_at = clock()
            serving = overlay_forecast(
                data.bundle,
                evaluated_at=evaluated_at,
                component_states=components,
            )
            included = parameters.included
            data = data.model_copy(
                update={
                    "serving": serving,
                    "evidence": data.bundle.evidence
                    if "evidence" in included
                    else None,
                    "similar_cases": data.similar_cases
                    if "similar_cases" in included
                    else (),
                }
            )
            reasons = tuple(str(reason) for reason in serving.exclusion_reason)
            return respond(
                snapshot=snapshot,
                data=data,
                components=components,
                serving_status=serving.serving_status,
                effective_abstain=serving.effective_abstain,
                exclusion_reason=reasons,
                evaluated_at=evaluated_at,
            )
        _component_serving_status, component_reasons = _component_status(components)
        reasons = tuple(dict.fromkeys(("unavailable", *component_reasons)))
        return respond(
            snapshot=snapshot,
            data=data,
            components=components,
            serving_status=ServingStatus.UNAVAILABLE,
            effective_abstain=True,
            exclusion_reason=reasons,
        )

    @router.get(
        "/instruments/{symbol}/history",
        response_model=ResearchResponse[InstrumentHistory],
        responses=ERROR_RESPONSES,
    )
    def history(
        symbol: str,
        parameters: Annotated[HistoryParameters, Query()],
    ) -> ResearchResponse[InstrumentHistory]:
        snapshot = resolve(parameters)
        data = repository.history(snapshot, symbol, parameters.to_repository_query())
        data = data.model_copy(
            update={
                "bars": tuple(
                    bar
                    for bar in data.bars
                    if within_range(bar.at, parameters.from_at, parameters.to_at)
                ),
                "events": tuple(
                    event
                    for event in data.events
                    if within_range(
                        event.occurred_at, parameters.from_at, parameters.to_at
                    )
                ),
            }
        )
        return respond(snapshot=snapshot, data=data)

    @router.get(
        "/backtests",
        response_model=ResearchResponse[BacktestReadModel],
        responses=ERROR_RESPONSES,
    )
    def backtests(
        parameters: Annotated[BacktestParameters, Query()],
    ) -> ResearchResponse[BacktestReadModel]:
        snapshot = resolve(parameters)
        data = repository.backtests(snapshot, parameters.to_repository_query())
        return respond(snapshot=snapshot, data=data)

    @router.get(
        "/models",
        response_model=ResearchResponse[ModelMatrixReadModel],
        responses=ERROR_RESPONSES,
    )
    def models(
        parameters: Annotated[ModelParameters, Query()],
    ) -> ResearchResponse[ModelMatrixReadModel]:
        snapshot = resolve(parameters)
        data = repository.models(snapshot, parameters.to_repository_query())
        if parameters.status is not None:
            data = data.model_copy(
                update={
                    "rows": tuple(
                        row for row in data.rows if row.status == parameters.status
                    )
                }
            )
        return respond(snapshot=snapshot, data=data)

    @router.get(
        "/data-health",
        response_model=ResearchResponse[DataHealthReadModel],
        responses=ERROR_RESPONSES,
    )
    def data_health(
        parameters: Annotated[DataHealthParameters, Query()],
    ) -> ResearchResponse[DataHealthReadModel]:
        snapshot = resolve(parameters)
        data = repository.data_health(snapshot, parameters.to_repository_query())
        return respond(snapshot=snapshot, data=data)

    @router.get(
        "/universes",
        response_model=ResearchResponse[UniverseReadModel],
        responses=ERROR_RESPONSES,
    )
    def universes(
        parameters: Annotated[UniverseParameters, Query()],
    ) -> ResearchResponse[UniverseReadModel]:
        snapshot = resolve(parameters)
        data = repository.universes(snapshot)
        versions = data.versions
        if parameters.version is not None:
            versions = tuple(
                version
                for version in versions
                if version.universe_version == parameters.version
            )
        if parameters.effective_at is not None:
            versions = tuple(
                version
                for version in versions
                if version.effective_from <= parameters.effective_at
                and (
                    version.effective_to is None
                    or parameters.effective_at < version.effective_to
                )
            )
        data = data.model_copy(update={"versions": versions})
        return respond(snapshot=snapshot, data=data)

    return router


def _overview_for_mode(
    data: OverviewReadModel,
    mode: ResearchDisplayMode,
) -> OverviewReadModel:
    if mode is ResearchDisplayMode.FORMAL:
        return data.model_copy(
            update={
                "research_opportunities": (),
                "challenger_opportunities": (),
            }
        )
    if mode is ResearchDisplayMode.RESEARCH:
        return data.model_copy(update={"challenger_opportunities": ()})
    return data


def _screen_for_mode(
    data: ScreenPage,
    mode: ResearchDisplayMode,
) -> ScreenPage:
    if mode is ResearchDisplayMode.CHALLENGER:
        return data
    updates = (
        {"verified_research": None, "challenger_preview": None}
        if mode is ResearchDisplayMode.FORMAL
        else {"challenger_preview": None}
    )
    return data.model_copy(
        update={"items": tuple(row.model_copy(update=updates) for row in data.items)}
    )


def _forecast_for_mode(
    data: ForecastUnavailable,
    mode: ResearchDisplayMode,
) -> ForecastUnavailable:
    if mode is ResearchDisplayMode.CHALLENGER:
        return data
    if mode is ResearchDisplayMode.FORMAL:
        return data.model_copy(
            update={"verified_research": (), "challenger_preview": ()}
        )
    return data.model_copy(update={"challenger_preview": ()})


def _data_status(components: Sequence[ComponentHealth]) -> Readiness:
    statuses = {component.readiness for component in components}
    if not statuses or Readiness.BLOCKED in statuses:
        return Readiness.BLOCKED
    if Readiness.DEGRADED in statuses:
        return Readiness.DEGRADED
    return Readiness.READY


def _component_status(
    components: Sequence[ComponentHealth],
) -> tuple[ServingStatus, tuple[str, ...]]:
    unavailable = not components or any(
        state.completeness is Completeness.EMPTY
        or state.connectivity is Connectivity.UNKNOWN
        or (
            state.connectivity is not Connectivity.DISCONNECTED
            and (
                state.readiness is Readiness.BLOCKED
                or state.freshness is Freshness.UNKNOWN
                or state.completeness is Completeness.UNKNOWN
            )
        )
        for state in components
    )
    offline = any(
        state.connectivity is Connectivity.DISCONNECTED for state in components
    )
    stale = any(state.freshness is Freshness.STALE for state in components)
    partial = any(
        state.completeness is Completeness.PARTIAL
        or state.readiness is Readiness.DEGRADED
        for state in components
    )
    delayed = any(state.freshness is Freshness.DELAYED for state in components)
    if unavailable:
        status = ServingStatus.UNAVAILABLE
    elif offline:
        status = ServingStatus.OFFLINE
    elif stale:
        status = ServingStatus.STALE
    elif partial:
        status = ServingStatus.PARTIAL
    elif delayed:
        status = ServingStatus.DELAYED
    else:
        status = ServingStatus.LIVE
    reasons = tuple(
        reason
        for reason, applies in (
            ("unavailable", unavailable),
            ("offline", offline),
            ("stale", stale),
        )
        if applies
    )
    return status, reasons


__all__ = ["build_research_router"]
