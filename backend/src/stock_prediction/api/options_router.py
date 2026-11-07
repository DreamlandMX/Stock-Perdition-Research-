"""Read-only option data with independent snapshot and prediction state."""

from collections import OrderedDict
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from contextlib import nullcontext
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
from threading import RLock
from typing import Annotated, Self, Any, ParamSpec, TypeVar, cast

from fastapi import APIRouter, Query
from pydantic import Field, model_validator

from stock_prediction.api.errors import ApiError
from stock_prediction.api.read_models import ErrorResponse
from stock_prediction.contracts.options import (
    OPTION_SYMBOLS,
    OptionCoverage,
    OptionHeatmapReadModel,
    OptionMetric,
    OptionModel,
    OptionHeatmapSide,
    OptionAnalyticsReadModel,
    OptionStockReturn,
    OptionOverviewResponse,
    OptionCollectionResponse,
    OptionExperimentsResponse,
    OptionOverviewItem,
    OptionBoardItem,
    OptionBoardResponse,
    OptionHeaderData,
    OptionHeaderResponse,
    OptionChainSnapshot,
    OptionStructureMapReadModel,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.options.store import OptionSnapshotStore
from stock_prediction.options.structure import UNITS, build_option_heatmap
from stock_prediction.options.structure_map import build_option_structure_map
from stock_prediction.options.analytics import build_option_analytics
from stock_prediction.options.board import (
    build_option_board_item,
    build_option_board_market,
)
from stock_prediction.options.analytics import NEW_YORK
from stock_prediction.options.experiment import (
    OptionExperimentStore,
    read_option_experiments,
)
from stock_prediction.options.serving import (
    OptionForecastReadModel,
    OptionForecastHorizon,
    candidate_state,
    load_option_inference,
)
from stock_prediction.serving.repository import (
    ResearchReadRepository,
    SnapshotQuery,
    HistoryQuery,
    SnapshotNotFound,
    SnapshotIntegrityError,
)
from stock_prediction.options.stock_context import (
    stock_returns_from_history,
    stock_return_scope,
)
from stock_prediction.options.monitor import (
    build_option_monitor,
    build_option_collection,
    verified_option_sessions,
)
from stock_prediction.options.overview import (
    build_option_overview_item,
    build_interval_samples,
    summarize_interval_coverage,
    completed_returns,
)
from stock_prediction.contracts.serving import (
    ForecastAvailable,
    InstrumentHistory,
    ResolvedSnapshot,
)
from stock_prediction.serving.status import overlay_forecast
from stock_prediction.contracts.health import ComponentHealth

_P = ParamSpec("_P")
_T = TypeVar("_T")


class OptionParameters(OptionModel):
    stock_snapshot_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
    )
    metric: OptionMetric = "oi"
    side: OptionHeatmapSide = "call"
    snapshot_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
    )
    as_of: UtcDatetime | None = None
    dte_min: int = Field(default=1, ge=1, le=90)
    dte_max: int = Field(default=90, ge=1, le=90)
    strike_min_ratio: float = Field(default=0.7, gt=0, le=3)
    strike_max_ratio: float = Field(default=1.3, gt=0, le=3)

    @model_validator(mode="after")
    def window(self) -> Self:
        if self.dte_min > self.dte_max or self.strike_min_ratio > self.strike_max_ratio:
            raise ValueError("invalid option filter window")
        return self


class OptionSnapshotChoice(OptionModel):
    snapshot_id: str
    captured_at: UtcDatetime
    complete: bool


class OptionResponse(OptionModel):
    request_id: str
    generated_at: UtcDatetime
    data_status: str
    resolved_snapshot_id: str | None
    available_snapshots: tuple[OptionSnapshotChoice, ...]
    data: OptionHeatmapReadModel


class OptionAnalyticsResponse(OptionModel):
    request_id: str
    generated_at: UtcDatetime
    data: OptionAnalyticsReadModel | None
    reasons: tuple[str, ...] = ()


class OptionStructureMapResponse(OptionModel):
    request_id: str
    generated_at: UtcDatetime
    data_status: str
    resolved_snapshot_id: str | None
    data: OptionStructureMapReadModel


class OptionForecastParameters(OptionModel):
    option_snapshot_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
    )
    option_as_of: UtcDatetime | None = None
    stock_snapshot_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
    )
    stock_as_of: UtcDatetime | None = None


class OptionForecastResponse(OptionModel):
    request_id: str
    generated_at: UtcDatetime
    data: OptionForecastReadModel


class OptionMonitorResponse(OptionModel):
    request_id: str
    generated_at: UtcDatetime
    data: dict[str, Any]


class OptionOverviewParameters(OptionModel):
    symbol: str | None = None
    option_snapshot_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
    )
    snapshot_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
    )
    as_of: UtcDatetime | None = None

    @model_validator(mode="after")
    def selection(self) -> Self:
        if self.option_snapshot_id is not None and self.symbol is None:
            raise ValueError("option_snapshot_id requires symbol")
        return self


def build_options_router(
    *,
    store: OptionSnapshotStore,
    clock: Callable[[], datetime],
    request_id_factory: Callable[[], str],
    repository: ResearchReadRepository | None = None,
    component_provider: Callable[[], list[ComponentHealth]] | None = None,
    stock_returns_provider: Callable[[str, datetime], tuple[OptionStockReturn, ...]]
    | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1",
        responses={code: {"model": ErrorResponse} for code in (404, 422, 503)},
    )
    derived: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
    in_flight: dict[tuple[Any, ...], Future[Any]] = {}
    derived_lock = RLock()
    request_history: ContextVar[
        dict[tuple[str, str, str], InstrumentHistory] | None
    ] = ContextVar("option_stock_history", default=None)

    request_resolutions: ContextVar[
        dict[str, tuple[ResolvedSnapshot | None, str | None]] | None
    ] = ContextVar("option_snapshot_resolutions", default=None)

    def request_inputs(endpoint: Callable[_P, _T]) -> Callable[_P, _T]:
        @wraps(endpoint)
        def scoped(*args: _P.args, **kwargs: _P.kwargs) -> _T:
            token = request_history.set({})
            resolution_token = request_resolutions.set({})
            try:
                with (
                    getattr(repository, "snapshot_resolution_scope", nullcontext)(),
                    store.read_scope(),
                    stock_return_scope(),
                ):
                    return endpoint(*args, **kwargs)
            finally:
                request_history.reset(token)
                request_resolutions.reset(resolution_token)

        return scoped

    def resolve_snapshot(query: SnapshotQuery) -> ResolvedSnapshot:
        assert repository is not None
        scope = request_resolutions.get()
        key = query.model_dump_json()
        if scope is not None and key in scope:
            selected, error = scope[key]
            if selected is None:
                raise SnapshotNotFound(error or "no matching research snapshot")
            return selected
        try:
            selected = repository.resolve_snapshot(query)
        except SnapshotNotFound as error:
            if scope is not None:
                scope[key] = None, str(error)
            raise
        if scope is not None:
            scope[key] = selected, None
        return selected

    def history(
        stock: ResolvedSnapshot, symbol: str, query: HistoryQuery
    ) -> InstrumentHistory:
        assert repository is not None
        scope = request_history.get()
        key = (stock.model_dump_json(), symbol, repr(query))
        if scope is not None and key in scope:
            return scope[key]
        result = repository.history(stock, symbol, query)
        if scope is not None:
            scope[key] = result
        return result

    def prefetch_history(stock: ResolvedSnapshot, symbols: Sequence[str]) -> None:
        reader = getattr(repository, "history_batch", None)
        scope = request_history.get()
        if reader is None or scope is None:
            return
        identity = stock.model_dump_json()
        for offset in range(0, len(symbols), 64):
            for query in (HistoryQuery(), HistoryQuery(adjustment="raw")):
                try:
                    batch = reader(stock, tuple(symbols[offset : offset + 64]), query)
                except (
                    SnapshotNotFound,
                    SnapshotIntegrityError,
                    OSError,
                    ValueError,
                    RuntimeError,
                ):
                    continue
                for item in batch.items:
                    if item.history is not None:
                        scope[(identity, item.symbol, repr(query))] = item.history

    def cached_result(key: tuple[Any, ...], build: Callable[[], _T]) -> _T:
        with derived_lock:
            if key in derived:
                derived.move_to_end(key)
                return cast(_T, derived[key])
            pending = in_flight.get(key)
            leader = pending is None
            if leader:
                pending = in_flight[key] = Future()
        if not leader:
            assert pending is not None
            return cast(_T, pending.result())
        assert pending is not None
        try:
            result = build()
            with derived_lock:
                if getattr(result, "data_status", None) != "unavailable":
                    derived[key] = result
                while len(derived) > 2 * len(OPTION_SYMBOLS):
                    derived.popitem(last=False)
            pending.set_result(result)
            return result
        except BaseException as error:
            pending.set_exception(error)
            raise
        finally:
            with derived_lock:
                in_flight.pop(key, None)

    def snapshot_key(snapshot: OptionChainSnapshot, at: datetime) -> tuple[Any, ...]:
        market = snapshot.market_as_of or snapshot.underlying_at
        return (
            snapshot.symbol,
            snapshot.snapshot_id,
            store.identity(snapshot),
            snapshot.source_available_at <= at,
            snapshot.captured_at <= at,
            market is not None and market <= at,
        )

    def read(symbol: str, parameters: OptionParameters) -> OptionResponse:
        now = clock()
        supported = symbol in OPTION_SYMBOLS
        snapshot = None
        choices: tuple[OptionSnapshotChoice, ...] = ()
        attempt = None
        try:
            if supported:
                snapshot = store.load(
                    symbol, parameters.snapshot_id, as_of=parameters.as_of
                )
                choices = tuple(
                    OptionSnapshotChoice(
                        snapshot_id=s.snapshot_id,
                        captured_at=s.captured_at,
                        complete=s.complete,
                    )
                    for s in reversed(store.snapshots(symbol))
                    if parameters.as_of is None
                    or s.source_available_at <= parameters.as_of
                )
                if parameters.snapshot_id is not None and snapshot is None:
                    raise ApiError(
                        status_code=404,
                        error_code="option_snapshot_not_found",
                        message="Option snapshot unavailable for the requested time",
                        retryable=False,
                        affected_scope=[symbol],
                    )
                if parameters.snapshot_id is None and parameters.as_of is None:
                    candidate = store.latest_attempt(symbol)
                    if (
                        candidate is not None
                        and not candidate.complete
                        and snapshot is not None
                        and candidate.snapshot_id != snapshot.snapshot_id
                    ):
                        attempt = candidate
        except (OSError, ValueError, KeyError) as error:
            raise ApiError(
                status_code=503,
                error_code="option_snapshot_invalid",
                message="Persisted option snapshot could not be read",
                retryable=True,
                affected_scope=[symbol],
            ) from error
        if snapshot is None:
            data = OptionHeatmapReadModel(
                symbol=symbol,
                snapshot=None,
                status="unavailable" if supported else "unsupported",
                metric=parameters.metric,
                side=parameters.side,
                unit=UNITS[parameters.metric],
                aggregation="sum"
                if parameters.metric in ("oi", "volume", "gamma", "premium")
                else "arithmetic_mean_not_sum",
                coverage=OptionCoverage(
                    expected=0, valid=0, missing=0, excluded=0, discovery_complete=False
                ),
                reasons=(
                    "no_option_snapshot"
                    if supported
                    else "symbol_outside_option_scope",
                ),
            )
        else:
            if (
                parameters.dte_min < snapshot.dte_min
                or parameters.dte_max > snapshot.dte_max
                or parameters.strike_min_ratio < snapshot.strike_min_ratio
                or parameters.strike_max_ratio > snapshot.strike_max_ratio
            ):
                raise ApiError(
                    status_code=422,
                    error_code="option_window_outside_snapshot",
                    message="Requested range exceeds the captured option window",
                    retryable=False,
                    affected_scope=[symbol],
                )
            data = build_option_heatmap(
                snapshot,
                metric=parameters.metric,
                side=parameters.side,
                dte_min=parameters.dte_min,
                dte_max=parameters.dte_max,
                strike_min_ratio=parameters.strike_min_ratio,
                strike_max_ratio=parameters.strike_max_ratio,
            ).model_copy(update={"latest_attempt": attempt})
        return OptionResponse(
            request_id=request_id_factory(),
            generated_at=now,
            data_status=data.status,
            resolved_snapshot_id=snapshot.snapshot_id if snapshot else None,
            available_snapshots=choices,
            data=data,
        )

    @router.get(
        "/instruments/{symbol}/options/heatmap",
        response_model=OptionResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    @request_inputs
    def heatmap(
        symbol: str, parameters: Annotated[OptionParameters, Query()]
    ) -> OptionResponse:
        return read(symbol, parameters)

    @router.get(
        "/instruments/{symbol}/options/structure-map",
        response_model=OptionStructureMapResponse,
    )
    @request_inputs
    def structure_map(
        symbol: str, parameters: Annotated[OptionParameters, Query()]
    ) -> OptionStructureMapResponse:
        # Reuse the existing snapshot/range validation; the legacy metric remains unchanged.
        resolved = read(symbol, parameters)
        snapshot = (
            store.load(symbol, resolved.resolved_snapshot_id, as_of=parameters.as_of)
            if resolved.resolved_snapshot_id is not None
            else None
        )
        if snapshot is None:
            data = OptionStructureMapReadModel(
                symbol=symbol,
                status=resolved.data.status,
                exposure_coverage=resolved.data.coverage,
                turnover_coverage=resolved.data.coverage,
                reasons=resolved.data.reasons,
            )
        else:
            try:
                data = build_option_structure_map(
                    store.with_turnover(snapshot),
                    dte_min=parameters.dte_min,
                    dte_max=parameters.dte_max,
                    strike_min_ratio=parameters.strike_min_ratio,
                    strike_max_ratio=parameters.strike_max_ratio,
                )
            except (OSError, ValueError, KeyError) as error:
                raise ApiError(
                    status_code=503,
                    error_code="option_snapshot_invalid",
                    message="Persisted option structure inputs could not be read",
                    retryable=True,
                    affected_scope=[symbol],
                ) from error
        return OptionStructureMapResponse(
            request_id=resolved.request_id,
            generated_at=resolved.generated_at,
            data_status=data.status,
            resolved_snapshot_id=resolved.resolved_snapshot_id,
            data=data,
        )

    @router.get(
        "/instruments/{symbol}/options/header", response_model=OptionHeaderResponse
    )
    @request_inputs
    def header(
        symbol: str, parameters: Annotated[OptionParameters, Query()]
    ) -> OptionHeaderResponse:
        snapshot = None
        supported = symbol in OPTION_SYMBOLS
        try:
            if supported:
                snapshot = store.load(
                    symbol, parameters.snapshot_id, as_of=parameters.as_of
                )
                if parameters.snapshot_id is not None and snapshot is None:
                    raise ApiError(
                        status_code=404,
                        error_code="option_snapshot_not_found",
                        message="Option snapshot unavailable for the requested time",
                        retryable=False,
                        affected_scope=[symbol],
                    )
        except (OSError, ValueError, KeyError) as error:
            raise ApiError(
                status_code=503,
                error_code="option_snapshot_invalid",
                message="Persisted option snapshot could not be read",
                retryable=True,
                affected_scope=[symbol],
            ) from error
        data = (
            OptionHeaderData(
                symbol=symbol,
                snapshot_id=snapshot.snapshot_id,
                market_as_of=snapshot.market_as_of or snapshot.underlying_at,
                captured_at=snapshot.captured_at,
                complete=snapshot.complete,
                contract_count=len(snapshot.contracts),
                reasons=snapshot.errors,
            )
            if snapshot is not None
            else OptionHeaderData(
                symbol=symbol,
                reasons=(
                    "no_option_snapshot"
                    if supported
                    else "symbol_outside_option_scope",
                ),
            )
        )
        return OptionHeaderResponse(
            request_id=request_id_factory(),
            generated_at=clock(),
            data_status="available"
            if snapshot
            else "unavailable"
            if supported
            else "unsupported",
            resolved_snapshot_id=data.snapshot_id,
            data=data,
        )

    @router.get("/instruments/{symbol}/options/summary", response_model=OptionResponse)
    @request_inputs
    def summary(
        symbol: str, parameters: Annotated[OptionParameters, Query()]
    ) -> OptionResponse:
        return read(symbol, parameters)

    @router.get(
        "/instruments/{symbol}/options/analytics",
        response_model=OptionAnalyticsResponse,
    )
    @request_inputs
    def analytics(
        symbol: str, parameters: Annotated[OptionParameters, Query()]
    ) -> OptionAnalyticsResponse:
        resolved = read(symbol, parameters)
        snapshot = resolved.data.snapshot
        returns: tuple[OptionStockReturn, ...] = ()
        if snapshot is not None and repository is not None:
            try:
                stock = resolve_snapshot(
                    SnapshotQuery(
                        snapshot_id=parameters.stock_snapshot_id,
                        as_of=None
                        if parameters.stock_snapshot_id
                        else snapshot.source_available_at,
                    )
                )
                if stock.as_of > snapshot.source_available_at:
                    stock = resolve_snapshot(
                        SnapshotQuery(as_of=snapshot.source_available_at)
                    )
                returns = stock_returns_from_history(
                    history(stock, symbol, HistoryQuery()),
                    available_at=stock.as_of,
                    annualization_sessions=252,
                )
            except (SnapshotNotFound, SnapshotIntegrityError, ValueError):
                pass
        if not returns and snapshot is not None and stock_returns_provider is not None:
            try:
                returns = stock_returns_provider(
                    symbol, parameters.as_of or snapshot.source_available_at
                )
            except (OSError, ValueError, RuntimeError):
                pass
        if snapshot is not None:
            market = snapshot.market_as_of or snapshot.underlying_at
            if market is not None:
                returns = tuple(
                    completed_returns(
                        returns, verified_option_sessions(store, clock()), market
                    )
                )
        return OptionAnalyticsResponse(
            request_id=request_id_factory(),
            generated_at=clock(),
            data=build_option_analytics(
                snapshot,
                history=store.snapshots(symbol),
                as_of=parameters.as_of,
                stock_returns=returns,
            )
            if snapshot is not None
            else None,
            reasons=resolved.data.reasons if snapshot is None else (),
        )

    @router.get(
        "/instruments/{symbol}/options/forecast", response_model=OptionForecastResponse
    )
    @request_inputs
    def option_forecast(
        symbol: str, parameters: Annotated[OptionForecastParameters, Query()]
    ) -> OptionForecastResponse:
        experiments = OptionExperimentStore(store.root / "research" / "runs")
        try:
            state = candidate_state(experiments)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            from stock_prediction.options.serving import OptionCandidateState

            state = OptionCandidateState(status="candidate_evidence_invalid")
        data = OptionForecastReadModel(
            symbol=symbol,
            status="refused",
            candidate=state,
            reasons=("stock_forecast_unavailable",),
        )
        if repository is not None:
            stock = resolve_snapshot(
                SnapshotQuery(
                    snapshot_id=parameters.stock_snapshot_id,
                    as_of=parameters.stock_as_of,
                )
            )
            baseline = repository.forecast(stock, symbol, 5)
            data = data.model_copy(
                update={
                    "stock_snapshot_id": stock.snapshot_id,
                    "stock_cutoff": stock.as_of,
                }
            )
            if isinstance(baseline, ForecastAvailable):
                overlay = overlay_forecast(
                    baseline.bundle,
                    evaluated_at=clock(),
                    component_states=component_provider() if component_provider else (),
                )
                data = data.model_copy(
                    update={
                        "model_source": "stock_baseline",
                        "model_version": baseline.bundle.model_version,
                        "status": "refused"
                        if overlay.effective_abstain
                        else "baseline_fallback",
                        "reasons": tuple(str(r) for r in overlay.exclusion_reason)
                        if overlay.effective_abstain
                        else (
                            "option_candidate_not_promoted"
                            if not state.promoted
                            else "option_inference_unavailable",
                        ),
                        "horizons": ()
                        if overlay.effective_abstain
                        else tuple(
                            OptionForecastHorizon(
                                horizon_days=h.horizon,
                                q10=h.capital_gain_return_quantiles.q10,
                                q50=h.capital_gain_return_quantiles.q50,
                                q90=h.capital_gain_return_quantiles.q90,
                                probabilities=h.forecast_direction_probabilities.model_dump(),
                            )
                            for h in baseline.bundle.horizons
                        ),
                    }
                )
            else:
                data = data.model_copy(
                    update={
                        "reasons": (
                            baseline.not_generated_reason,
                            "option_candidate_not_promoted"
                            if not state.promoted
                            else "stock_input_unavailable",
                        )
                    }
                )
            if state.promoted and data.status != "refused":
                try:
                    saved = load_option_inference(
                        symbol, stock.snapshot_id, experiments
                    )
                    if (
                        saved is not None
                        and saved.status == "enhanced"
                        and saved.stock_cutoff == stock.as_of
                        and saved.generated_at is not None
                        and saved.generated_at <= clock()
                    ):
                        option_snapshot = store.load(
                            symbol, saved.option_snapshot_id, as_of=stock.as_of
                        )
                        protocol = (
                            experiments.directory(state.version or "")
                            / "registration.json"
                        )
                        import json

                        maximum_age = json.loads(protocol.read_text(encoding="utf-8"))[
                            "protocol"
                        ]["maximum_age_seconds"]
                        if (
                            option_snapshot is not None
                            and (
                                parameters.option_snapshot_id is None
                                or parameters.option_snapshot_id
                                == saved.option_snapshot_id
                            )
                            and (
                                parameters.option_as_of is None
                                or option_snapshot.source_available_at
                                <= parameters.option_as_of
                            )
                            and (
                                stock.as_of
                                - (
                                    option_snapshot.market_as_of
                                    or option_snapshot.underlying_at
                                    or option_snapshot.captured_at
                                )
                            ).total_seconds()
                            <= maximum_age
                        ):
                            data = saved
                        else:
                            data = data.model_copy(
                                update={
                                    "reasons": (
                                        "enhanced_option_input_expired_or_snapshot_mismatch",
                                    )
                                }
                            )
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    data = data.model_copy(
                        update={"reasons": ("enhanced_candidate_integrity_failed",)}
                    )
        return OptionForecastResponse(
            request_id=request_id_factory(), generated_at=clock(), data=data
        )

    @router.get(
        "/instruments/{symbol}/options/monitor", response_model=OptionMonitorResponse
    )
    @request_inputs
    def monitor(symbol: str) -> OptionMonitorResponse:
        report = build_option_monitor(
            store,
            evaluated_at=clock(),
            experiments_root=store.root / "research" / "runs",
        )
        report["symbols"] = (
            {symbol: report["symbols"][symbol]}
            if symbol in OPTION_SYMBOLS
            else {symbol: {"data_status": "unsupported"}}
        )
        return OptionMonitorResponse(
            request_id=request_id_factory(), generated_at=clock(), data=report
        )

    def return_context(
        symbol: str, at: datetime, stock: ResolvedSnapshot | None = None
    ) -> tuple[OptionStockReturn, ...]:
        returns: tuple[OptionStockReturn, ...] = ()
        if repository is not None:
            try:
                selected = stock or resolve_snapshot(SnapshotQuery(as_of=at))
                returns = stock_returns_from_history(
                    history(selected, symbol, HistoryQuery()),
                    available_at=selected.as_of,
                    annualization_sessions=252,
                )
            except (
                SnapshotNotFound,
                SnapshotIntegrityError,
                ValueError,
                OSError,
                RuntimeError,
            ):
                pass
        if not returns and stock_returns_provider is not None:
            try:
                returns = stock_returns_provider(symbol, at)
            except (OSError, ValueError, RuntimeError):
                pass
        return returns

    def historical_forecast(
        symbol: str, cutoff: datetime
    ) -> OptionForecastReadModel | None:
        if repository is None:
            return None
        try:
            stock = resolve_snapshot(SnapshotQuery(as_of=cutoff))
            baseline = repository.forecast(stock, symbol, 5)
            if (
                not isinstance(baseline, ForecastAvailable)
                or baseline.bundle.generated_at > cutoff
            ):
                return None
            overlay = overlay_forecast(
                baseline.bundle, evaluated_at=cutoff, component_states=()
            )
            if overlay.effective_abstain:
                return None
            from stock_prediction.options.serving import OptionCandidateState

            return OptionForecastReadModel(
                symbol=symbol,
                status="baseline_fallback",
                model_source="stock_baseline",
                model_version=baseline.bundle.model_version,
                stock_snapshot_id=stock.snapshot_id,
                stock_cutoff=stock.as_of,
                generated_at=baseline.bundle.generated_at,
                candidate=OptionCandidateState(status="historical_baseline"),
                horizons=tuple(
                    OptionForecastHorizon(
                        horizon_days=h.horizon,
                        q10=h.capital_gain_return_quantiles.q10,
                        q50=h.capital_gain_return_quantiles.q50,
                        q90=h.capital_gain_return_quantiles.q90,
                    )
                    for h in baseline.bundle.horizons
                ),
            )
        except (
            SnapshotNotFound,
            SnapshotIntegrityError,
            ValueError,
            OSError,
            RuntimeError,
        ):
            return None

    @router.get("/options/overview", response_model=OptionOverviewResponse)
    @request_inputs
    def options_overview(
        parameters: Annotated[OptionOverviewParameters, Query()],
    ) -> OptionOverviewResponse:
        now = clock()
        at = parameters.as_of or now
        symbols = (parameters.symbol,) if parameters.symbol else OPTION_SYMBOLS
        stock, stock_reasons = None, []
        if repository is not None:
            try:
                stock = resolve_snapshot(
                    SnapshotQuery(
                        snapshot_id=parameters.snapshot_id, as_of=parameters.as_of
                    )
                )
            except SnapshotNotFound:
                if parameters.snapshot_id is not None:
                    raise
                stock_reasons.append("stock_snapshot_unavailable")
            except (SnapshotIntegrityError, OSError, ValueError):
                stock_reasons.append("stock_snapshot_invalid")
        if stock is not None:
            prefetch_history(stock, symbols)
        sessions = verified_option_sessions(store, at)
        collection = build_option_collection(store, evaluated_at=at, sessions=sessions)
        items: list[OptionOverviewItem] = []
        samples: dict[int, list[tuple[datetime, bool, bool]]] = {
            h: [] for h in (1, 5, 20)
        }
        for symbol in symbols:
            with store.read_scope():
                if symbol not in OPTION_SYMBOLS:
                    items.append(
                        OptionOverviewItem(
                            symbol=symbol,
                            data_status="unsupported",
                            maturity="none",
                            reasons=("symbol_outside_option_scope",),
                        )
                    )
                    continue
                try:
                    option_history = store.snapshots(symbol)
                    snapshot = store.load(
                        symbol, parameters.option_snapshot_id, as_of=at
                    )
                    if parameters.option_snapshot_id is not None and snapshot is None:
                        raise ApiError(
                            status_code=404,
                            error_code="option_snapshot_not_found",
                            message="Option snapshot unavailable for the requested time",
                            retryable=False,
                            affected_scope=[symbol],
                        )
                    if snapshot is None:
                        attempt = store.latest_attempt(symbol, as_of=at)
                        items.append(
                            OptionOverviewItem(
                                symbol=symbol,
                                data_status="unavailable",
                                maturity="none",
                                history_days=0,
                                reasons=("no_complete_snapshot_available",)
                                + (
                                    (attempt.errors or ("incomplete_snapshots",))
                                    if attempt
                                    else ()
                                ),
                            )
                        )
                        continue
                    returns = completed_returns(
                        return_context(symbol, stock.as_of if stock else at, stock),
                        sessions,
                        stock.as_of if stock else at,
                    )
                    forecast = None
                    if stock is not None:
                        forecast = option_forecast(
                            symbol,
                            OptionForecastParameters(
                                option_snapshot_id=snapshot.snapshot_id
                                if snapshot
                                else None,
                                option_as_of=at,
                                stock_snapshot_id=stock.snapshot_id,
                            ),
                        ).data
                    item_at = (
                        min(at, snapshot.source_available_at)
                        if parameters.option_snapshot_id is not None and snapshot
                        else at
                    )
                    analytics_returns = (
                        return_context(
                            symbol,
                            snapshot.source_available_at,
                            stock
                            if stock and stock.as_of <= snapshot.source_available_at
                            else None,
                        )
                        if snapshot
                        else ()
                    )
                    key = (
                        "overview-v1",
                        snapshot_key(snapshot, item_at) if snapshot else None,
                        tuple(snapshot_key(s, item_at) for s in option_history),
                        stock.model_dump_json() if stock else None,
                        parameters.as_of,
                        tuple(returns),
                        tuple(analytics_returns),
                        forecast.model_dump_json() if forecast else None,
                        tuple(sorted(sessions.items())),
                    )

                    def build_item() -> OptionOverviewItem:
                        return build_option_overview_item(
                            symbol,
                            snapshot,
                            history=option_history,
                            returns=returns,
                            analytics_returns=analytics_returns,
                            forecast=forecast,
                            at=item_at,
                            sessions=sessions,
                        )

                    item = (
                        cached_result(key, build_item)
                        if snapshot and snapshot.complete
                        else build_item()
                    )
                    if item.analytics is not None and item.analytics.as_of != item_at:
                        item = item.model_copy(
                            update={
                                "analytics": item.analytics.model_copy(
                                    update={"as_of": item_at}
                                )
                            }
                        )
                    matched = build_interval_samples(
                        {symbol: option_history},
                        {symbol: returns},
                        sessions=sessions,
                        at=stock.as_of if stock else at,
                        forecast_reader=historical_forecast,
                    )
                    for horizon, values in matched.items():
                        samples[horizon].extend(values)
                    items.append(item)
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    SnapshotIntegrityError,
                ):
                    items.append(
                        OptionOverviewItem(
                            symbol=symbol,
                            data_status="unavailable",
                            maturity="none",
                            reasons=("snapshot_artifacts_unreadable",),
                        )
                    )
        coverage = summarize_interval_coverage(samples)
        available = any(i.analytics is not None for i in items)
        return OptionOverviewResponse(
            request_id=request_id_factory(),
            generated_at=now,
            data_status="available"
            if available and all(i.data_status == "available" for i in items)
            else "partial"
            if available
            else "unavailable",
            resolved_snapshot_id=stock.snapshot_id if stock else None,
            items=tuple(items),
            collection=collection,
            coverage=coverage,
            reasons=tuple(stock_reasons),
        )

    @router.get("/options/board", response_model=OptionBoardResponse)
    @request_inputs
    def options_board(
        parameters: Annotated[OptionOverviewParameters, Query()],
    ) -> OptionBoardResponse:
        now = clock()
        at = parameters.as_of or now
        symbols = (parameters.symbol,) if parameters.symbol else OPTION_SYMBOLS
        items, reasons = [], []
        resolved_stock_id = None
        stock_context = None
        if repository is not None and any(s in OPTION_SYMBOLS for s in symbols):
            try:
                stock_context = resolve_snapshot(
                    SnapshotQuery(
                        snapshot_id=parameters.snapshot_id, as_of=parameters.as_of
                    )
                )
            except SnapshotNotFound:
                if parameters.snapshot_id is not None:
                    raise
                reasons.append("stock_snapshot_unavailable")
            except (SnapshotIntegrityError, OSError, ValueError, RuntimeError):
                reasons.append("stock_snapshot_invalid")
        if stock_context is not None:
            prefetch_history(stock_context, symbols)
        for symbol in symbols:
            with store.read_scope():
                if symbol not in OPTION_SYMBOLS:
                    items.append(
                        OptionBoardItem(
                            symbol=symbol,
                            data_status="unsupported",
                            reasons=("symbol_outside_option_scope",),
                        )
                    )
                    reasons.append("symbol_outside_option_scope")
                    continue
                try:
                    snapshot = store.load(
                        symbol, parameters.option_snapshot_id, as_of=at
                    )
                    if parameters.option_snapshot_id is not None and snapshot is None:
                        raise ApiError(
                            status_code=404,
                            error_code="option_snapshot_not_found",
                            message="Option snapshot unavailable for the requested time",
                            retryable=False,
                            affected_scope=[symbol],
                        )
                    if snapshot is None:
                        attempt = store.latest_attempt(symbol, as_of=at)
                        items.append(
                            OptionBoardItem(
                                symbol=symbol,
                                reasons=("option_snapshot_unavailable",)
                                + (
                                    (attempt.errors or ("incomplete_snapshots",))
                                    if attempt
                                    else ()
                                ),
                            )
                        )
                        reasons.append("option_snapshot_unavailable")
                        continue
                    if (
                        snapshot.time_evidence != "historical_verified"
                        and snapshot.captured_at > at
                    ):
                        items.append(
                            OptionBoardItem(
                                symbol=symbol, reasons=("snapshot_not_available",)
                            )
                        )
                        reasons.append("snapshot_not_available")
                        continue
                    market_at = snapshot.market_as_of or snapshot.underlying_at
                    if market_at is None or snapshot.underlying_price is None:
                        reason = (
                            "market_time_unproven"
                            if market_at is None
                            else "underlying_price_unavailable"
                        )
                        items.append(
                            OptionBoardItem(
                                symbol=symbol,
                                snapshot_id=snapshot.snapshot_id,
                                spot=snapshot.underlying_price,
                                reasons=(reason,),
                            )
                        )
                        reasons.append(reason)
                        continue
                    closes: tuple[float, ...] = ()
                    stale_closes = False
                    if repository is not None:
                        cutoff = min(at, snapshot.source_available_at)
                        try:
                            stock = (
                                stock_context
                                if stock_context is not None
                                and (
                                    parameters.snapshot_id is not None
                                    or stock_context.as_of <= cutoff
                                )
                                else resolve_snapshot(SnapshotQuery(as_of=cutoff))
                            )
                            if stock.as_of <= cutoff:
                                stock_history = history(stock, symbol, HistoryQuery())
                                if not stock_history.bars:
                                    stock_history = history(
                                        stock, symbol, HistoryQuery(adjustment="raw")
                                    )
                                if (
                                    stock_history.timeframe == "1d"
                                    and stock_history.adjustment
                                    in (
                                        "split_adjusted",
                                        "raw",
                                    )
                                ):
                                    bars = [
                                        bar
                                        for bar in stock_history.bars
                                        if bar.at <= min(market_at, cutoff)
                                    ][-21:]
                                    stale_closes = (
                                        bool(bars)
                                        and bars[-1].at.astimezone(NEW_YORK).date()
                                        != market_at.astimezone(NEW_YORK).date()
                                    )
                                    if (
                                        not stale_closes
                                        and len(
                                            {
                                                bar.at.astimezone(NEW_YORK).date()
                                                for bar in bars
                                            }
                                        )
                                        == len(bars)
                                        and not any(bar.data_gap for bar in bars)
                                        and not (
                                            stock_history.adjustment == "raw"
                                            and bars
                                            and any(
                                                event.event_type == "split"
                                                and bars[0].at
                                                < event.occurred_at
                                                <= bars[-1].at
                                                for event in stock_history.events
                                            )
                                        )
                                    ):
                                        # Raw closes preserve demo RV only across windows without recorded splits.
                                        closes = tuple(bar.close for bar in bars)
                                resolved_stock_id = stock.snapshot_id
                            else:
                                reasons.append("stock_snapshot_after_option_cutoff")
                        except SnapshotNotFound:
                            if parameters.snapshot_id is not None:
                                raise
                            reasons.append("stock_snapshot_unavailable")
                        except (
                            SnapshotIntegrityError,
                            OSError,
                            ValueError,
                            RuntimeError,
                        ):
                            reasons.append("stock_snapshot_invalid")
                    market_date = market_at.astimezone(NEW_YORK).date()
                    key = (
                        "board-v1",
                        snapshot_key(snapshot, at),
                        parameters.as_of,
                        resolved_stock_id,
                        stock_context.model_dump_json() if stock_context else None,
                        closes,
                        market_date,
                    )

                    def build_item() -> OptionBoardItem:
                        return build_option_board_item(
                            snapshot, closes=closes, market_date=market_date
                        )

                    item = (
                        cached_result(key, build_item)
                        if snapshot.complete
                        else build_item()
                    )
                    if stale_closes:
                        item = item.model_copy(
                            update={
                                "reasons": (
                                    *item.reasons,
                                    "realized_vol_20_stock_date_mismatch",
                                )
                            }
                        )
                    items.append(item)
                except ArithmeticError:
                    items.append(
                        OptionBoardItem(
                            symbol=symbol, reasons=("board_numeric_overflow",)
                        )
                    )
                    reasons.append("board_numeric_overflow")
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    SnapshotIntegrityError,
                ):
                    items.append(
                        OptionBoardItem(
                            symbol=symbol, reasons=("snapshot_artifacts_unreadable",)
                        )
                    )
                    reasons.append("snapshot_artifacts_unreadable")
        available = any(i.data_status in ("available", "partial") for i in items)
        market = None
        if available:
            try:
                market = build_option_board_market(items)
            except (ArithmeticError, ValueError):
                reasons.append("market_numeric_overflow")
        return OptionBoardResponse(
            request_id=request_id_factory(),
            generated_at=now,
            data_status="unsupported"
            if all(i.data_status == "unsupported" for i in items)
            else "available"
            if available
            and market is not None
            and not reasons
            and all(i.data_status == "available" for i in items)
            else "partial"
            if available
            else "unavailable",
            resolved_snapshot_id=resolved_stock_id,
            items=tuple(items),
            market=market,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    @router.get("/options/collection", response_model=OptionCollectionResponse)
    @request_inputs
    def options_collection(
        as_of: UtcDatetime | None = None,
    ) -> OptionCollectionResponse:
        now = clock()
        at = as_of or now
        collection = build_option_collection(
            store, evaluated_at=at, sessions=verified_option_sessions(store, at)
        )
        return OptionCollectionResponse(
            request_id=request_id_factory(),
            generated_at=now,
            data_status="available"
            if collection.cells
            and all(c.status == "complete" for c in collection.cells)
            else "partial"
            if collection.sessions
            else "unavailable",
            collection=collection,
        )

    @router.get("/options/experiments", response_model=OptionExperimentsResponse)
    @request_inputs
    def options_experiments(
        as_of: UtcDatetime | None = None,
        snapshot_id: Annotated[
            str | None, Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
        ] = None,
    ) -> OptionExperimentsResponse:
        now = clock()
        at = as_of or now
        sessions = verified_option_sessions(store, at)
        collection = build_option_collection(store, evaluated_at=at, sessions=sessions)
        experiments, reasons = read_option_experiments(
            OptionExperimentStore(store.root / "research" / "runs"),
            at=at,
            history_days=collection.history_days,
            sessions=tuple(sessions),
        )
        samples: dict[int, list[tuple[datetime, bool, bool]]] = {
            h: [] for h in (1, 5, 20)
        }
        stock = None
        if repository is not None:
            try:
                stock = resolve_snapshot(
                    SnapshotQuery(snapshot_id=snapshot_id, as_of=as_of)
                )
            except (SnapshotNotFound, SnapshotIntegrityError, OSError, ValueError):
                reasons = tuple(dict.fromkeys((*reasons, "stock_snapshot_unavailable")))
        if stock is not None:
            prefetch_history(stock, OPTION_SYMBOLS)
        for symbol in OPTION_SYMBOLS:
            with store.read_scope():
                try:
                    option_history = store.snapshots(symbol)
                    actuals = (
                        return_context(symbol, at, stock)
                        if stock is not None or repository is None
                        else ()
                    )
                    matched = build_interval_samples(
                        {symbol: option_history},
                        {symbol: actuals},
                        sessions=sessions,
                        at=at,
                        forecast_reader=historical_forecast,
                    )
                    for horizon, values in matched.items():
                        samples[horizon].extend(values)
                except (OSError, ValueError, KeyError, TypeError):
                    reasons = tuple(
                        dict.fromkeys((*reasons, "snapshot_artifacts_unreadable"))
                    )
        coverage = summarize_interval_coverage(samples)
        return OptionExperimentsResponse(
            request_id=request_id_factory(),
            generated_at=now,
            data_status="available"
            if experiments and not reasons
            else "partial"
            if experiments
            else "unavailable",
            experiments=experiments,
            coverage=coverage,
            reasons=reasons,
            resolved_snapshot_id=stock.snapshot_id if stock else None,
        )

    return router
