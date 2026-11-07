"""Strict, OpenAPI-generating query contracts for research routes."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from stock_prediction.contracts.market_data import Identifier, UsSymbol, VersionId
from stock_prediction.contracts.research import Horizon, RegimeLabel
from stock_prediction.contracts.serving import (
    PredictionBasis,
    ResearchDisplayMode,
    ScreenConfidenceFloor,
    ScreenReturnBasis,
    ServingStatus,
    SortDirection,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.serving.repository import (
    BacktestQuery,
    DataHealthQuery,
    DataHealthSortKey,
    HistoryQuery,
    InstrumentQuery,
    InstrumentSortKey,
    ModelQuery,
    ScreenQuery,
    ScreenSortKey,
    SnapshotQuery,
)


def _parse_horizon(value: object) -> object:
    if isinstance(value, str) and value in {"1", "5", "20"}:
        return int(value)
    return value


QueryHorizon = Annotated[Horizon, BeforeValidator(_parse_horizon)]


class ForecastInclude(StrEnum):
    EVIDENCE = "evidence"
    SIMILAR_CASES = "similar_cases"
    EVIDENCE_AND_SIMILAR_CASES = "evidence,similar_cases"
    SIMILAR_CASES_AND_EVIDENCE = "similar_cases,evidence"

    @property
    def selections(self) -> frozenset[str]:
        return frozenset(self.value.split(","))


class _QueryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EmptyParameters(_QueryModel):
    pass


class SnapshotParameters(_QueryModel):
    as_of: UtcDatetime | None = None
    snapshot_id: Identifier | None = None
    dataset_revision: VersionId | None = None
    feature_set_version: VersionId | None = None
    universe_version: VersionId | None = None
    model_version: VersionId | None = None
    calibration_version: VersionId | None = None

    @property
    def is_exact(self) -> bool:
        return self.snapshot_id is not None

    @property
    def is_current(self) -> bool:
        return self.snapshot_id is None and self.as_of is None

    def to_snapshot_query(self) -> SnapshotQuery:
        return SnapshotQuery(
            as_of=self.as_of,
            snapshot_id=self.snapshot_id,
            dataset_revision=self.dataset_revision,
            feature_set_version=self.feature_set_version,
            universe_version=self.universe_version,
            model_version=self.model_version,
            calibration_version=self.calibration_version,
        )


class SnapshotContextParameters(SnapshotParameters):
    symbols: list[UsSymbol] = Field(default_factory=list, max_length=64)


class OverviewParameters(SnapshotParameters):
    horizon: QueryHorizon = 5
    mode: ResearchDisplayMode = ResearchDisplayMode.RESEARCH


class InstrumentParameters(SnapshotParameters):
    page: int = Field(default=1, ge=1, le=100)
    limit: int = Field(default=25, ge=1, le=100)
    sort: InstrumentSortKey = "symbol"
    direction: SortDirection = SortDirection.ASC
    search: str | None = Field(default=None, min_length=1, max_length=128)
    sector: Identifier | None = None
    asset_type: Identifier | None = None

    def to_repository_query(self) -> InstrumentQuery:
        return InstrumentQuery(
            page=self.page,
            page_size=self.limit,
            sort_key=self.sort,
            sort_direction=self.direction,
            search=self.search,
            sector=self.sector,
            asset_type=self.asset_type,
        )


class ScreenParameters(SnapshotParameters):
    horizon: QueryHorizon = 5
    mode: ResearchDisplayMode = ResearchDisplayMode.RESEARCH
    prediction_basis: PredictionBasis = PredictionBasis.EXECUTABLE
    return_basis: ScreenReturnBasis = ScreenReturnBasis.EXECUTABLE
    page: int = Field(default=1, ge=1, le=100)
    limit: int = Field(default=25, ge=1, le=100)
    sort: ScreenSortKey = "default"
    direction: SortDirection = SortDirection.DESC
    sector: Identifier | None = None
    asset_type: Identifier | None = None
    regime: RegimeLabel | None = None
    min_probability: float | None = Field(default=None, ge=0, le=1)
    max_q10: float | None = Field(default=None, ge=-1, le=1)
    confidence: ScreenConfidenceFloor | None = None
    serving_status: ServingStatus | None = None
    effective_abstain: bool | None = None

    def to_repository_query(self, *, exclude_rankings: bool = False) -> ScreenQuery:
        effective_abstain = self.effective_abstain
        if (
            self.is_current
            and self.serving_status is None
            and effective_abstain is None
        ):
            effective_abstain = False
        return ScreenQuery(
            horizon=self.horizon,
            prediction_basis=self.prediction_basis,
            return_basis=self.return_basis,
            page=self.page,
            page_size=self.limit,
            sort_key=self.sort,
            sort_direction=self.direction,
            sector=self.sector,
            asset_type=self.asset_type,
            regime=self.regime,
            min_probability=self.min_probability,
            max_q10=self.max_q10,
            minimum_confidence=self.confidence,
            serving_status=self.serving_status,
            effective_abstain=effective_abstain,
            exclude_rankings=exclude_rankings,
        )


class ForecastParameters(SnapshotParameters):
    horizon: QueryHorizon = 5
    mode: ResearchDisplayMode = ResearchDisplayMode.RESEARCH
    include: ForecastInclude | None = None

    @property
    def included(self) -> frozenset[str]:
        return self.include.selections if self.include is not None else frozenset()


class HistoryParameters(SnapshotParameters):
    timeframe: Literal["1d", "60m"] = "1d"
    adjustment: Literal["raw", "split_adjusted", "total_return"] = "split_adjusted"
    from_at: UtcDatetime | None = Field(default=None, alias="from")
    to_at: UtcDatetime | None = Field(default=None, alias="to")

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if (
            self.from_at is not None
            and self.to_at is not None
            and self.from_at > self.to_at
        ):
            raise ValueError("from must not follow to")
        return self

    def to_repository_query(self) -> HistoryQuery:
        return HistoryQuery(
            timeframe=self.timeframe,
            adjustment=self.adjustment,
            from_at=self.from_at,
            to_at=self.to_at,
        )


class HistoryBatchParameters(HistoryParameters):
    symbols: list[UsSymbol] = Field(min_length=1, max_length=64)


class BacktestParameters(SnapshotParameters):
    strategy_version: VersionId | None = None
    split_version: VersionId | None = None
    task: Identifier | None = None
    horizon: QueryHorizon | None = None
    asset_group: Identifier | None = None
    fold_id: Identifier | None = Field(default=None, alias="fold")
    evaluation_window_id: Identifier | None = None
    cost_scenario: Identifier | None = None

    def to_repository_query(self) -> BacktestQuery:
        return BacktestQuery(
            model_version=self.model_version,
            strategy_version=self.strategy_version,
            universe_version=self.universe_version,
            split_version=self.split_version,
            task=self.task,
            horizon=self.horizon,
            asset_group=self.asset_group,
            evaluation_window_id=self.evaluation_window_id,
            cost_scenario=self.cost_scenario,
            fold_id=self.fold_id,
        )


class ModelParameters(SnapshotParameters):
    task: Identifier | None = None
    horizon: QueryHorizon | None = None
    asset_group: Identifier | None = None
    status: Identifier | None = None

    def to_repository_query(self) -> ModelQuery:
        return ModelQuery(
            task=self.task,
            horizon=self.horizon,
            asset_group=self.asset_group,
        )


class DataHealthParameters(SnapshotParameters):
    page: int = Field(default=1, ge=1, le=100)
    limit: int = Field(default=25, ge=1, le=100)
    sort: DataHealthSortKey = "component"
    direction: SortDirection = SortDirection.ASC
    component: Identifier | None = None
    dataset: Identifier | None = None
    symbol: UsSymbol | None = None
    serving_status: ServingStatus | None = None

    def to_repository_query(self) -> DataHealthQuery:
        return DataHealthQuery(
            page=self.page,
            page_size=self.limit,
            sort_key=self.sort,
            sort_direction=self.direction,
            component=self.component,
            dataset=self.dataset,
            symbol=self.symbol,
            serving_status=self.serving_status,
        )


class UniverseParameters(SnapshotParameters):
    version: VersionId | None = None
    effective_at: UtcDatetime | None = None


def within_range(value: datetime, start: datetime | None, end: datetime | None) -> bool:
    return (start is None or value >= start) and (end is None or value <= end)


__all__ = [
    "SnapshotContextParameters",
    "HistoryBatchParameters",
    "BacktestParameters",
    "DataHealthParameters",
    "EmptyParameters",
    "ForecastInclude",
    "ForecastParameters",
    "HistoryParameters",
    "InstrumentParameters",
    "ModelParameters",
    "OverviewParameters",
    "PredictionBasis",
    "ScreenParameters",
    "SnapshotParameters",
    "UniverseParameters",
    "within_range",
]
