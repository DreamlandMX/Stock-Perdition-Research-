"""Read-only DuckDB repository over immutable research snapshots."""

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import stat
from statistics import median
import tempfile
from threading import RLock
from typing import Annotated, Final, Iterator, Literal, Protocol, TypeAlias

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
)

from stock_prediction.contracts.market_data import Identifier, UsSymbol, VersionId
from stock_prediction.contracts.research import Horizon, RegimeLabel
from stock_prediction.contracts.serving import (
    BacktestReadModel,
    CandidateRankChange,
    CandidateEvaluationSummary,
    DataHealth,
    DataHealthReadModel,
    ForecastAvailable,
    ForecastResult,
    HistoryBar,
    HistoryEvent,
    HistoryBatchItem,
    HistoryBatchReadModel,
    InstrumentHistory,
    InstrumentMetadata,
    InstrumentPage,
    ModelMatrixReadModel,
    ModelTaskMatrixRow,
    OverviewCandidateChurn,
    OverviewAnomalySnapshot,
    OverviewComparisonSnapshot,
    OverviewEvidenceCoverage,
    OverviewHistoryPoint,
    OverviewInternalEvidence,
    OverviewMarketPoint,
    OverviewReadModel,
    PredictionBasis,
    ResearchModelEvidence,
    ResearchOpportunityRow,
    RegimeTransitionCell,
    ResolvedSnapshot,
    ScreenConfidenceFloor,
    ScreenPage,
    ScreenReturnBasis,
    ScreenRow,
    SectorUptrendChange,
    SectorScorecard,
    ServingStatus,
    SnapshotServingReference,
    SnapshotContextReadModel,
    SortDirection,
    UniverseReadModel,
    UniverseVersion,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.serving.manifest import (
    RESEARCH_TABLE_SCHEMAS,
    ResearchSnapshotManifest,
    SnapshotId,
    SnapshotTable,
    parquet_schema_hash,
    resolve_repository_path,
    resolve_table_path,
)

SearchText = Annotated[str, StringConstraints(min_length=1, max_length=128)]
InstrumentSortKey: TypeAlias = Literal["symbol", "name", "sector", "asset_type"]
ScreenSortKey: TypeAlias = Literal[
    "default",
    "symbol",
    "executable_up_probability",
    "breakout_up_probability",
    "breakout_down_probability",
    "executable_q50",
    "capital_gain_q50",
    "interval_width",
    "similar_case_count",
    "confidence_level",
    "data_freshness_seconds",
    "rule_return_5",
    "rule_return_20",
    "rule_atr_pct_20",
    "rule_channel_position_20",
    "rule_volume_z_20",
    "verified_q10",
    "verified_q50",
]
DataHealthSortKey: TypeAlias = Literal[
    "component", "dataset", "symbol", "serving_status"
]

_SECTOR_SEARCH_ALIASES: Final[dict[str, str]] = {
    "ΘÇÜΣ┐í": "communication",
    "ΘÇÜΣ┐íµ£ìσèí": "communication",
    "µ╢êΦ┤╣": "consumer",
    "Φâ╜µ║É": "energy_materials",
    "µ¥ÉµûÖ": "energy_materials",
    "Φâ╜µ║ÉΣ╕Äµ¥ÉµûÖ": "energy_materials",
    "ΘçæΦ₧ì": "financials",
    "σî╗τûù": "healthcare",
    "σî╗τûùΣ┐¥σüÑ": "healthcare",
    "σ╖ÑΣ╕Ü": "industrials",
    "σà╢Σ╗û": "other",
    "τºæµèÇ": "technology",
}


class _StrictQuery(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class SnapshotQuery(_StrictQuery):
    as_of: UtcDatetime | None = None
    snapshot_id: SnapshotId | None = None
    dataset_revision: VersionId | None = None
    feature_set_version: VersionId | None = None
    universe_version: VersionId | None = None
    model_version: VersionId | None = None
    calibration_version: VersionId | None = None


class InstrumentQuery(_StrictQuery):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    sort_key: InstrumentSortKey = "symbol"
    sort_direction: SortDirection = SortDirection.ASC
    search: SearchText | None = None
    sector: Identifier | None = None
    asset_type: Identifier | None = None


class ScreenQuery(_StrictQuery):
    horizon: Horizon = 5
    prediction_basis: PredictionBasis = PredictionBasis.EXECUTABLE
    return_basis: ScreenReturnBasis = ScreenReturnBasis.EXECUTABLE
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    sort_key: ScreenSortKey = "default"
    sort_direction: SortDirection = SortDirection.DESC
    sector: Identifier | None = None
    asset_type: Identifier | None = None
    regime: RegimeLabel | None = None
    min_probability: float | None = Field(default=None, ge=0, le=1)
    max_q10: float | None = Field(default=None, ge=-1, le=1)
    minimum_confidence: ScreenConfidenceFloor | None = None
    serving_status: ServingStatus | None = None
    effective_abstain: bool | None = None
    exclude_rankings: bool = False


class HistoryQuery(_StrictQuery):
    timeframe: Literal["1d", "60m"] = "1d"
    adjustment: Literal["raw", "split_adjusted", "total_return"] = "split_adjusted"
    from_at: UtcDatetime | None = None
    to_at: UtcDatetime | None = None


class BacktestQuery(_StrictQuery):
    model_version: VersionId | None = None
    strategy_version: VersionId | None = None
    universe_version: VersionId | None = None
    split_version: VersionId | None = None
    task: Identifier | None = None
    horizon: Horizon | None = None
    asset_group: Identifier | None = None
    evaluation_window_id: Identifier | None = None
    cost_scenario: Identifier | None = None
    fold_id: Identifier | None = None


class ModelQuery(_StrictQuery):
    task: Identifier | None = None
    horizon: Horizon | None = None
    asset_group: Identifier | None = None


class DataHealthQuery(_StrictQuery):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    sort_key: DataHealthSortKey = "component"
    sort_direction: SortDirection = SortDirection.ASC
    component: Identifier | None = None
    dataset: Identifier | None = None
    symbol: UsSymbol | None = None
    serving_status: ServingStatus | None = None


class UnknownSymbol(RuntimeError):
    """The requested path value is not a recognized symbol identity."""


class SymbolNotInUniverse(RuntimeError):
    """A valid symbol is absent from the resolved snapshot universe."""


class SnapshotNotFound(RuntimeError):
    """No published snapshot satisfies the requested identity."""


class BacktestNotFound(SnapshotNotFound):
    """No persisted backtest evidence satisfies the requested identity."""


class SnapshotVersionConflict(RuntimeError):
    """A requested snapshot conflicts with explicit version evidence."""


class SnapshotIntegrityError(RuntimeError):
    """Published snapshot content does not match its immutable manifest."""


class ResearchReadRepository(Protocol):
    def resolve_snapshot(self, query: SnapshotQuery) -> ResolvedSnapshot: ...

    def snapshot_context(
        self, snapshot: ResolvedSnapshot, symbols: tuple[str, ...] = ()
    ) -> SnapshotContextReadModel: ...

    def history_batch(
        self, snapshot: ResolvedSnapshot, symbols: tuple[str, ...], query: HistoryQuery
    ) -> HistoryBatchReadModel: ...

    def overview(
        self, snapshot: ResolvedSnapshot, horizon: Horizon
    ) -> OverviewReadModel: ...

    def instruments(
        self, snapshot: ResolvedSnapshot, query: InstrumentQuery
    ) -> InstrumentPage: ...

    def instrument(
        self, snapshot: ResolvedSnapshot, symbol: str
    ) -> InstrumentMetadata: ...

    def screen(self, snapshot: ResolvedSnapshot, query: ScreenQuery) -> ScreenPage: ...

    def snapshot_serving_reference(
        self, snapshot: ResolvedSnapshot
    ) -> SnapshotServingReference: ...

    def forecast(
        self, snapshot: ResolvedSnapshot, symbol: str, horizon: Horizon = 5
    ) -> ForecastResult: ...

    def history(
        self, snapshot: ResolvedSnapshot, symbol: str, query: HistoryQuery
    ) -> InstrumentHistory: ...

    def backtests(
        self, snapshot: ResolvedSnapshot, query: BacktestQuery
    ) -> BacktestReadModel: ...

    def models(
        self, snapshot: ResolvedSnapshot, query: ModelQuery
    ) -> ModelMatrixReadModel: ...

    def data_health(
        self, snapshot: ResolvedSnapshot, query: DataHealthQuery
    ) -> DataHealthReadModel: ...

    def universes(self, snapshot: ResolvedSnapshot) -> UniverseReadModel: ...


_DIRECTION_SQL: Final = {
    SortDirection.ASC: "ASC",
    SortDirection.DESC: "DESC",
}
_INSTRUMENT_SORT_SQL: Final = {
    "symbol": "symbol",
    "name": "name",
    "sector": "sector",
    "asset_type": "asset_type",
}
_SCREEN_SORT_SQL: Final = {
    "symbol": "symbol",
    "executable_up_probability": "executable_up_probability",
    "breakout_up_probability": "breakout_up_probability",
    "breakout_down_probability": "breakout_down_probability",
    "executable_q50": "executable_q50",
    "capital_gain_q50": "capital_gain_q50",
    "interval_width": "interval_width",
    "similar_case_count": "similar_case_count",
    "data_freshness_seconds": "data_freshness_seconds",
    "rule_return_5": (
        "CAST(json_extract(payload_json, '$.rule_evidence.return_5') AS DOUBLE)"
    ),
    "rule_return_20": (
        "CAST(json_extract(payload_json, '$.rule_evidence.return_20') AS DOUBLE)"
    ),
    "rule_atr_pct_20": (
        "CAST(json_extract(payload_json, '$.rule_evidence.atr_pct_20') AS DOUBLE)"
    ),
    "rule_channel_position_20": (
        "CAST(json_extract(payload_json, "
        "'$.rule_evidence.channel_position_20') AS DOUBLE)"
    ),
    "rule_volume_z_20": (
        "CAST(json_extract(payload_json, '$.rule_evidence.volume_z_20') AS DOUBLE)"
    ),
    "verified_q10": (
        "CAST(json_extract(payload_json, "
        "'$.verified_research.values.capital_gain_q10') AS DOUBLE)"
    ),
    "verified_q50": (
        "CAST(json_extract(payload_json, "
        "'$.verified_research.values.capital_gain_q50') AS DOUBLE)"
    ),
}
_HEALTH_SORT_SQL: Final = {
    "component": "component",
    "dataset": "dataset",
    "symbol": "symbol",
    "serving_status": "serving_status",
}
_FORECAST_ADAPTER: TypeAdapter[ForecastResult] = TypeAdapter(ForecastResult)
_SCREEN_CACHE_MAX_ENTRIES: Final = 256


@dataclass(frozen=True)
class _CachedManifest:
    manifest: ResearchSnapshotManifest
    file_identities: tuple[tuple[str, tuple[int, int, int, int, int, int]], ...]


@dataclass(frozen=True)
class _CachedVerifiedTable:
    content_hash: str
    file_identity: tuple[int, int, int, int, int, int]
    path: Path


class DuckDbResearchRepository:
    """Content-verifying read repository with fixed SQL shape allowlists."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._manifest_cache: dict[Path, _CachedManifest] = {}
        self._resolution_manifests: ContextVar[
            dict[str, tuple[ResearchSnapshotManifest, ...]] | None
        ] = ContextVar("research_resolution_manifests", default=None)
        self._forecast_index_cache: dict[Path, dict[str, ForecastResult]] = {}
        self._verified_table_directory = tempfile.TemporaryDirectory(
            prefix="stock-prediction-research-cache-"
        )
        self._verified_table_root = Path(self._verified_table_directory.name)
        self._verified_table_cache: dict[tuple[Path, str], _CachedVerifiedTable] = {}
        self._verified_table_lock = RLock()
        self._screen_cache: OrderedDict[tuple[str, ScreenQuery], ScreenPage] = (
            OrderedDict()
        )
        self._overview_cache: OrderedDict[tuple[object, ...], OverviewReadModel] = (
            OrderedDict()
        )
        self._overview_lock = RLock()
        self._history_cache: OrderedDict[
            tuple[str, str, HistoryQuery], InstrumentHistory
        ] = OrderedDict()
        self._history_lock = RLock()

    @contextmanager
    def snapshot_resolution_scope(self) -> Iterator[None]:
        token = self._resolution_manifests.set({})
        try:
            yield
        finally:
            self._resolution_manifests.reset(token)

    @staticmethod
    def _anomaly_snapshot(
        snapshot_id: str,
        rows: tuple[ScreenRow, ...],
        regime_change_count: int | None,
    ) -> OverviewAnomalySnapshot:
        rules = tuple(
            row.rule_evidence for row in rows if row.rule_evidence is not None
        )
        volume_values = tuple(
            value for item in rules if (value := item.volume_z_20) is not None
        )
        channel_values = tuple(
            value for item in rules if (value := item.channel_position_20) is not None
        )
        return OverviewAnomalySnapshot(
            snapshot_id=snapshot_id,
            total_symbols=len(rows),
            volume_anomalies=(
                sum(abs(value) >= 2 for value in volume_values)
                if volume_values
                else None
            ),
            upper_channel_count=(
                sum(value >= 1 for value in channel_values) if channel_values else None
            ),
            lower_channel_count=(
                sum(value <= 0 for value in channel_values) if channel_values else None
            ),
            breakout_count=(
                sum(item.breakout_up_rule or item.breakout_down_rule for item in rules)
                if rules
                else None
            ),
            regime_change_count=regime_change_count,
        )

    @staticmethod
    def _overview_market_point(
        row: ScreenRow,
        previous: ScreenRow | None,
    ) -> OverviewMarketPoint:
        evidence = row.rule_evidence
        return OverviewMarketPoint(
            symbol=row.symbol,
            name=row.name,
            asset_type=row.asset_type,
            sector=row.sector,
            regime=row.regime,
            previous_regime=previous.regime if previous is not None else None,
            last_close=evidence.last_close if evidence is not None else None,
            session_return=evidence.session_return if evidence is not None else None,
            return_5=evidence.return_5 if evidence is not None else None,
            return_20=evidence.return_20 if evidence is not None else None,
            atr_pct_20=evidence.atr_pct_20 if evidence is not None else None,
            channel_position_20=(
                evidence.channel_position_20 if evidence is not None else None
            ),
            channel_width_20=(
                evidence.channel_width_20 if evidence is not None else None
            ),
            volume_z_20=evidence.volume_z_20 if evidence is not None else None,
            breakout_up_rule=(
                evidence.breakout_up_rule if evidence is not None else None
            ),
            breakout_down_rule=(
                evidence.breakout_down_rule if evidence is not None else None
            ),
            intraday_available=(
                evidence.intraday_available if evidence is not None else None
            ),
            regime_changed=(
                previous.regime is not row.regime if previous is not None else None
            ),
        )

    @staticmethod
    def _candidate_churn(
        current: tuple[ResearchOpportunityRow, ...],
        previous: tuple[ResearchOpportunityRow, ...],
    ) -> OverviewCandidateChurn:
        current_ranks = {row.symbol: rank for rank, row in enumerate(current, 1)}
        previous_ranks = {row.symbol: rank for rank, row in enumerate(previous, 1)}
        names = {row.symbol: row.name for row in (*current, *previous)}
        changes: list[CandidateRankChange] = []
        for row in current:
            current_rank = current_ranks[row.symbol]
            previous_rank = previous_ranks.get(row.symbol)
            changes.append(
                CandidateRankChange(
                    symbol=row.symbol,
                    name=row.name,
                    status="entered" if previous_rank is None else "retained",
                    current_rank=current_rank,
                    previous_rank=previous_rank,
                    rank_change=(
                        previous_rank - current_rank
                        if previous_rank is not None
                        else None
                    ),
                )
            )
        for row in previous:
            if row.symbol not in current_ranks:
                changes.append(
                    CandidateRankChange(
                        symbol=row.symbol,
                        name=names[row.symbol],
                        status="exited",
                        current_rank=None,
                        previous_rank=previous_ranks[row.symbol],
                        rank_change=None,
                    )
                )
        return OverviewCandidateChurn(changes=tuple(changes))

    @staticmethod
    def _sector_scorecards(
        current_rows: tuple[ScreenRow, ...],
        previous_rows: tuple[ScreenRow, ...],
    ) -> tuple[SectorScorecard, ...]:
        def grouped(rows: tuple[ScreenRow, ...]) -> dict[str, list[ScreenRow]]:
            result: dict[str, list[ScreenRow]] = {}
            for row in rows:
                result.setdefault(row.sector, []).append(row)
            return result

        current = grouped(current_rows)
        previous = grouped(previous_rows)
        cards: list[SectorScorecard] = []
        for sector, rows in current.items():
            rules = tuple(
                row.rule_evidence for row in rows if row.rule_evidence is not None
            )

            def values(field: str) -> tuple[float, ...]:
                return tuple(
                    value
                    for item in rules
                    if (value := getattr(item, field)) is not None
                )

            session_returns = values("session_return")
            returns_5 = values("return_5")
            returns_20 = values("return_20")
            atr_values = values("atr_pct_20")
            volume_values = values("volume_z_20")
            ranked = sorted(
                (
                    (item.rule_evidence.return_20, item.symbol)
                    for item in rows
                    if item.rule_evidence is not None
                    and item.rule_evidence.return_20 is not None
                ),
                key=lambda item: (item[0], item[1]),
            )
            uptrend_fraction = sum(
                row.regime is RegimeLabel.UPTREND for row in rows
            ) / len(rows)
            previous_sector = previous.get(sector, [])
            comparable_sector = len(rows) >= 5 and len(previous_sector) >= 5
            previous_fraction = (
                sum(row.regime is RegimeLabel.UPTREND for row in previous_sector)
                / len(previous_sector)
                if comparable_sector
                else None
            )
            cards.append(
                SectorScorecard(
                    sector=sector,
                    sample_count=len(rows),
                    median_session_return=(
                        median(session_returns) if session_returns else None
                    ),
                    median_return_5=median(returns_5) if returns_5 else None,
                    median_return_20=median(returns_20) if returns_20 else None,
                    uptrend_fraction=uptrend_fraction,
                    previous_sample_count=(
                        len(previous_sector) if comparable_sector else None
                    ),
                    previous_uptrend_fraction=previous_fraction,
                    uptrend_change=(
                        uptrend_fraction - previous_fraction
                        if previous_fraction is not None
                        else None
                    ),
                    median_atr_pct_20=median(atr_values) if atr_values else None,
                    volume_anomaly_count=(
                        sum(abs(value) >= 2 for value in volume_values)
                        if volume_values
                        else None
                    ),
                    breakout_up_count=(
                        sum(item.breakout_up_rule for item in rules) if rules else None
                    ),
                    breakout_down_count=(
                        sum(item.breakout_down_rule for item in rules)
                        if rules
                        else None
                    ),
                    leader_symbol=ranked[-1][1] if ranked else None,
                    laggard_symbol=ranked[0][1] if ranked else None,
                )
            )
        return tuple(sorted(cards, key=lambda item: item.sector))

    def instrument(self, snapshot: ResolvedSnapshot, symbol: str) -> InstrumentMetadata:
        directory, manifest = self._verified_directory(snapshot)
        checked_symbol = _validated_symbol(symbol)
        payloads = self._payloads(
            directory,
            manifest,
            "instruments.parquet",
            where="symbol = ?",
            parameters=[checked_symbol],
            order_by="symbol ASC",
            limit=1,
        )
        if not payloads:
            raise SymbolNotInUniverse("symbol is not in the resolved universe")
        return InstrumentMetadata.model_validate_json(payloads[0], strict=True)

    def resolve_snapshot(self, query: SnapshotQuery) -> ResolvedSnapshot:
        if query.snapshot_id is not None:
            directory = self.root / query.snapshot_id
            if not os.path.lexists(directory):
                raise SnapshotNotFound("requested research snapshot was not found")
            manifest = self._load_manifest(directory)
            self._check_exact_consistency(manifest, query)
            return manifest.resolved_snapshot()

        manifests = self._published_manifests()
        candidates = [
            manifest
            for manifest in manifests
            if (query.as_of is None or manifest.as_of <= query.as_of)
            and _matches_version_filters(manifest, query)
        ]
        if not candidates:
            raise SnapshotNotFound("no published research snapshot matches the query")
        selected = max(
            candidates,
            key=lambda value: (value.as_of, value.revision_no, value.snapshot_id),
        )
        return selected.resolved_snapshot()

    def overview(
        self, snapshot: ResolvedSnapshot, horizon: Horizon
    ) -> OverviewReadModel:
        _, manifest = self._verified_directory(snapshot)
        published = self._published_manifests()
        key = (
            manifest.manifest_hash,
            horizon,
            tuple(item.manifest_hash for item in published),
        )
        with self._overview_lock:
            cached = self._overview_cache.get(key)
            if cached is not None:
                self._overview_cache.move_to_end(key)
                return cached
            result = self._build_overview(snapshot, horizon, published)
            self._overview_cache[key] = result
            while len(self._overview_cache) > 32:
                self._overview_cache.popitem(last=False)
            return result

    def _build_overview(
        self,
        snapshot: ResolvedSnapshot,
        horizon: Horizon,
        published: tuple[ResearchSnapshotManifest, ...],
    ) -> OverviewReadModel:
        directory, manifest = self._verified_directory(snapshot)
        payloads = self._payloads(
            directory,
            manifest,
            "overview.parquet",
            where="horizon = ?",
            parameters=[horizon],
            order_by="horizon ASC",
        )
        if len(payloads) != 1:
            raise SnapshotIntegrityError("overview.parquet has no unique horizon row")
        base = OverviewReadModel.model_validate_json(payloads[0], strict=True)
        research_evidence: dict[str, list[ResearchModelEvidence]] = {}
        challenger_evidence: dict[str, list[ResearchModelEvidence]] = {}
        for table_name, destination in (
            ("verified_research.parquet", research_evidence),
            ("challenger_preview.parquet", challenger_evidence),
        ):
            if not any(table.path == table_name for table in manifest.tables):
                continue
            for payload in self._payloads(
                directory,
                manifest,
                table_name,
                where="",
                parameters=[],
                order_by="symbol ASC, horizon ASC",
            ):
                evidence = ResearchModelEvidence.model_validate_json(
                    payload,
                    strict=True,
                )
                destination.setdefault(evidence.symbol, []).append(evidence)
        research_opportunities = tuple(
            row.model_copy(
                update={
                    "verified_research_by_horizon": tuple(
                        research_evidence.get(row.symbol, ())
                    )
                }
            )
            for row in base.research_opportunities
        )
        challenger_opportunities = tuple(
            row.model_copy(
                update={
                    "challenger_preview_by_horizon": tuple(
                        challenger_evidence.get(row.symbol, ())
                    )
                }
            )
            for row in base.challenger_opportunities
        )
        top_payloads = self._payloads(
            directory,
            manifest,
            "screen.parquet",
            where=(
                "horizon = ? AND effective_abstain = FALSE "
                "AND serving_status NOT IN (?, ?, ?)"
            ),
            parameters=[
                horizon,
                ServingStatus.STALE.value,
                ServingStatus.OFFLINE.value,
                ServingStatus.UNAVAILABLE.value,
            ],
            order_by=_screen_default_order(
                PredictionBasis.EXECUTABLE,
                ScreenReturnBasis.EXECUTABLE,
                SortDirection.DESC,
            ),
            limit=10,
        )
        top = tuple(
            ScreenRow.model_validate_json(payload, strict=True)
            for payload in top_payloads
        )
        internal_evidence = self._overview_internal_evidence(
            directory,
            manifest,
            base,
            horizon,
            research_opportunities,
            challenger_opportunities,
            published,
        )
        return OverviewReadModel.model_validate(
            {
                **base.model_dump(mode="python"),
                "proxies": tuple(
                    proxy.model_copy(
                        update={
                            "last_close": proxy.last_close
                            if not proxy.trend
                            else proxy.trend[-1]
                        }
                    )
                    for proxy in base.proxies
                ),
                "top_opportunities": top,
                "research_opportunities": research_opportunities,
                "challenger_opportunities": challenger_opportunities,
                "internal_evidence": internal_evidence,
            },
            strict=True,
        )

    def _overview_internal_evidence(
        self,
        directory: Path,
        manifest: ResearchSnapshotManifest,
        overview: OverviewReadModel,
        horizon: Horizon,
        research_opportunities: tuple[ResearchOpportunityRow, ...],
        challenger_opportunities: tuple[ResearchOpportunityRow, ...],
        published: tuple[ResearchSnapshotManifest, ...],
    ) -> OverviewInternalEvidence:
        prior_manifests = self._compatible_prior_manifests(manifest, published)
        comparison = prior_manifests[0] if prior_manifests else None
        comparison_predecessor = (
            prior_manifests[1] if len(prior_manifests) > 1 else None
        )
        current_rows = self._overview_screen_rows(directory, manifest, horizon)
        comparison_rows = (
            self._overview_screen_rows(
                self.root / comparison.snapshot_id,
                comparison,
                horizon,
            )
            if comparison is not None
            else ()
        )
        comparison_predecessor_rows = (
            self._overview_screen_rows(
                self.root / comparison_predecessor.snapshot_id,
                comparison_predecessor,
                horizon,
            )
            if comparison_predecessor is not None
            else ()
        )

        history_sources = tuple(reversed(prior_manifests[:4])) + (manifest,)
        history = tuple(
            self._overview_history_point(
                source,
                overview
                if source.snapshot_id == manifest.snapshot_id
                else self._overview_base(source, horizon),
                current_rows
                if source.snapshot_id == manifest.snapshot_id
                else self._overview_screen_rows(
                    self.root / source.snapshot_id,
                    source,
                    horizon,
                ),
            )
            for source in history_sources
        )

        return_values = tuple(
            value
            for row in current_rows
            if row.rule_evidence is not None
            and (value := row.rule_evidence.session_return) is not None
        )
        advancers: int | None
        decliners: int | None
        unchanged: int | None
        if return_values:
            return_median = median(return_values)
            return_dispersion = median(
                abs(value - return_median) for value in return_values
            )
            advancers = sum(value > 0 for value in return_values)
            decliners = sum(value < 0 for value in return_values)
            unchanged = sum(value == 0 for value in return_values)
        else:
            return_median = None
            return_dispersion = None
            advancers = decliners = unchanged = None

        rule_evidence = tuple(
            row.rule_evidence for row in current_rows if row.rule_evidence is not None
        )
        breakout_up: int | None
        breakout_down: int | None
        if rule_evidence:
            breakout_up = sum(item.breakout_up_rule for item in rule_evidence)
            breakout_down = sum(item.breakout_down_rule for item in rule_evidence)
        else:
            breakout_up = breakout_down = None
        volume_values = tuple(
            value for item in rule_evidence if (value := item.volume_z_20) is not None
        )
        volume_anomalies = (
            sum(abs(value) >= 2 for value in volume_values) if volume_values else None
        )

        previous_by_symbol = {row.symbol: row for row in comparison_rows}
        market_points = tuple(
            self._overview_market_point(row, previous_by_symbol.get(row.symbol))
            for row in current_rows
        )
        comparable = tuple(
            (previous_by_symbol[row.symbol], row)
            for row in current_rows
            if row.symbol in previous_by_symbol
        )
        transition_counts: dict[tuple[RegimeLabel, RegimeLabel], int] = {}
        for previous, current in comparable:
            key = (previous.regime, current.regime)
            transition_counts[key] = transition_counts.get(key, 0) + 1
        transition_cells = tuple(
            RegimeTransitionCell(
                previous_regime=previous,
                current_regime=current,
                count=count,
            )
            for (previous, current), count in sorted(
                transition_counts.items(),
                key=lambda item: (item[0][0].value, item[0][1].value),
            )
        )

        coverage = OverviewEvidenceCoverage(
            total_symbols=len(current_rows),
            comparable_symbols=len(comparable),
            rule_evidence_count=len(rule_evidence),
            session_return_count=sum(
                item.session_return is not None for item in rule_evidence
            ),
            return_5_count=sum(item.return_5 is not None for item in rule_evidence),
            return_20_count=sum(item.return_20 is not None for item in rule_evidence),
            atr_count=sum(item.atr_pct_20 is not None for item in rule_evidence),
            channel_count=sum(
                item.channel_position_20 is not None
                and item.channel_width_20 is not None
                for item in rule_evidence
            ),
            volume_z_count=len(volume_values),
            intraday_available_count=sum(
                item.intraday_available for item in rule_evidence
            ),
        )

        regime_changes: int | None = None
        to_uptrend: int | None = None
        to_downtrend: int | None = None
        sector_changes: tuple[SectorUptrendChange, ...] = ()
        verified_churn: OverviewCandidateChurn | None = None
        challenger_churn: OverviewCandidateChurn | None = None
        previous_regime_change_count: int | None = None
        if comparison is not None:
            regime_changes = sum(
                previous.regime != current.regime for previous, current in comparable
            )
            to_uptrend = sum(
                previous.regime is not RegimeLabel.UPTREND
                and current.regime is RegimeLabel.UPTREND
                for previous, current in comparable
            )
            to_downtrend = sum(
                previous.regime is not RegimeLabel.DOWNTREND
                and current.regime is RegimeLabel.DOWNTREND
                for previous, current in comparable
            )
            sector_changes = self._sector_uptrend_changes(
                current_rows,
                comparison_rows,
            )
            previous_overview = self._overview_base(comparison, horizon)
            verified_churn = self._candidate_churn(
                research_opportunities,
                previous_overview.research_opportunities,
            )
            challenger_churn = self._candidate_churn(
                challenger_opportunities,
                previous_overview.challenger_opportunities,
            )
            if comparison_predecessor is not None:
                predecessor_by_symbol = {
                    row.symbol: row for row in comparison_predecessor_rows
                }
                previous_regime_change_count = sum(
                    predecessor_by_symbol[row.symbol].regime is not row.regime
                    for row in comparison_rows
                    if row.symbol in predecessor_by_symbol
                )

        return OverviewInternalEvidence(
            comparison_snapshot=(
                OverviewComparisonSnapshot(
                    snapshot_id=comparison.snapshot_id,
                    revision_no=comparison.revision_no,
                    as_of=comparison.as_of,
                    generated_at=comparison.generated_at,
                )
                if comparison is not None
                else None
            ),
            history=history,
            advancers=advancers,
            decliners=decliners,
            unchanged=unchanged,
            regime_changes=regime_changes,
            to_uptrend=to_uptrend,
            to_downtrend=to_downtrend,
            breakout_up=breakout_up,
            breakout_down=breakout_down,
            volume_anomalies=volume_anomalies,
            median_session_return=return_median,
            return_dispersion=return_dispersion,
            market_points=market_points,
            regime_transition_cells=transition_cells,
            sector_scorecards=self._sector_scorecards(
                current_rows,
                comparison_rows,
            ),
            verified_candidate_churn=verified_churn,
            challenger_candidate_churn=challenger_churn,
            evidence_coverage=coverage,
            current_anomaly_snapshot=self._anomaly_snapshot(
                manifest.snapshot_id,
                current_rows,
                regime_changes,
            ),
            previous_anomaly_snapshot=(
                self._anomaly_snapshot(
                    comparison.snapshot_id,
                    comparison_rows,
                    previous_regime_change_count,
                )
                if comparison is not None
                else None
            ),
            sector_uptrend_changes=sector_changes,
        )

    def _compatible_prior_manifests(
        self,
        current: ResearchSnapshotManifest,
        published: tuple[ResearchSnapshotManifest, ...],
    ) -> tuple[ResearchSnapshotManifest, ...]:
        latest_by_as_of: dict[UtcDatetime, ResearchSnapshotManifest] = {}
        for candidate in published:
            if (
                candidate.as_of >= current.as_of
                or candidate.generated_at >= current.generated_at
                or candidate.universe_version != current.universe_version
                or candidate.feature_set_version != current.feature_set_version
            ):
                continue
            existing = latest_by_as_of.get(candidate.as_of)
            if existing is None or (
                candidate.revision_no,
                candidate.generated_at,
                candidate.snapshot_id,
            ) > (
                existing.revision_no,
                existing.generated_at,
                existing.snapshot_id,
            ):
                latest_by_as_of[candidate.as_of] = candidate
        return tuple(
            sorted(
                latest_by_as_of.values(),
                key=lambda item: (item.as_of, item.revision_no, item.snapshot_id),
                reverse=True,
            )
        )

    def _overview_base(
        self,
        manifest: ResearchSnapshotManifest,
        horizon: Horizon,
    ) -> OverviewReadModel:
        payloads = self._payloads(
            self.root / manifest.snapshot_id,
            manifest,
            "overview.parquet",
            where="horizon = ?",
            parameters=[horizon],
            order_by="horizon ASC",
        )
        if len(payloads) != 1:
            raise SnapshotIntegrityError("overview.parquet has no unique horizon row")
        return OverviewReadModel.model_validate_json(payloads[0], strict=True)

    def _overview_screen_rows(
        self,
        directory: Path,
        manifest: ResearchSnapshotManifest,
        horizon: Horizon,
    ) -> tuple[ScreenRow, ...]:
        return tuple(
            ScreenRow.model_validate_json(payload, strict=True)
            for payload in self._payloads(
                directory,
                manifest,
                "screen.parquet",
                where="horizon = ?",
                parameters=[horizon],
                order_by="symbol ASC",
            )
        )

    @staticmethod
    def _overview_history_point(
        manifest: ResearchSnapshotManifest,
        overview: OverviewReadModel,
        rows: tuple[ScreenRow, ...],
    ) -> OverviewHistoryPoint:
        returns = tuple(
            value
            for row in rows
            if row.rule_evidence is not None
            and (value := row.rule_evidence.session_return) is not None
        )
        volumes = tuple(
            value
            for row in rows
            if row.rule_evidence is not None
            and (value := row.rule_evidence.volume_z_20) is not None
        )
        return_median = median(returns) if returns else None
        return OverviewHistoryPoint(
            snapshot_id=manifest.snapshot_id,
            revision_no=manifest.revision_no,
            as_of=manifest.as_of,
            market_breadth=overview.market_breadth,
            uptrend_fraction=(
                sum(row.regime is RegimeLabel.UPTREND for row in rows) / len(rows)
                if rows
                else 0.0
            ),
            median_session_return=return_median,
            return_dispersion=(
                median(abs(value - return_median) for value in returns)
                if return_median is not None
                else None
            ),
            volume_anomaly_count=(
                sum(abs(value) >= 2 for value in volumes) if volumes else None
            ),
        )

    @staticmethod
    def _sector_uptrend_changes(
        current_rows: tuple[ScreenRow, ...],
        previous_rows: tuple[ScreenRow, ...],
    ) -> tuple[SectorUptrendChange, ...]:
        def grouped(rows: tuple[ScreenRow, ...]) -> dict[str, tuple[int, int]]:
            totals: dict[str, int] = {}
            uptrends: dict[str, int] = {}
            for row in rows:
                totals[row.sector] = totals.get(row.sector, 0) + 1
                if row.regime is RegimeLabel.UPTREND:
                    uptrends[row.sector] = uptrends.get(row.sector, 0) + 1
            return {
                sector: (count, uptrends.get(sector, 0))
                for sector, count in totals.items()
            }

        current = grouped(current_rows)
        previous = grouped(previous_rows)
        changes = []
        for sector in current.keys() & previous.keys():
            current_count, current_uptrends = current[sector]
            previous_count, previous_uptrends = previous[sector]
            if current_count < 5 or previous_count < 5:
                continue
            current_fraction = current_uptrends / current_count
            previous_fraction = previous_uptrends / previous_count
            changes.append(
                SectorUptrendChange(
                    sector=sector,
                    sample_count=current_count,
                    previous_sample_count=previous_count,
                    uptrend_fraction=current_fraction,
                    previous_uptrend_fraction=previous_fraction,
                    change=current_fraction - previous_fraction,
                )
            )
        return tuple(sorted(changes, key=lambda item: (-item.change, item.sector)))

    def instruments(
        self, snapshot: ResolvedSnapshot, query: InstrumentQuery
    ) -> InstrumentPage:
        directory, manifest = self._verified_directory(snapshot)
        conditions: list[str] = []
        parameters: list[object] = []
        if query.search is not None:
            conditions.append("(symbol ILIKE ? OR name ILIKE ? OR sector ILIKE ?)")
            search = f"%{query.search}%"
            sector_term = _SECTOR_SEARCH_ALIASES.get(
                query.search.casefold(), query.search
            )
            sector_search = f"%{sector_term}%"
            parameters.extend((search, search, sector_search))
        if query.sector is not None:
            conditions.append("sector = ?")
            parameters.append(query.sector)
        if query.asset_type is not None:
            conditions.append("asset_type = ?")
            parameters.append(query.asset_type)
        where = " AND ".join(conditions)
        total = self._count(
            directory, manifest, "instruments.parquet", where, parameters
        )
        _validate_page(total, query.page, query.page_size)
        sort_column = _INSTRUMENT_SORT_SQL[query.sort_key]
        direction = _DIRECTION_SQL[query.sort_direction]
        tie_breaker = "" if sort_column == "symbol" else ", symbol ASC"
        payloads = self._payloads(
            directory,
            manifest,
            "instruments.parquet",
            where=where,
            parameters=parameters,
            order_by=f"{sort_column} {direction} NULLS LAST{tie_breaker}",
            limit=query.page_size,
            offset=(query.page - 1) * query.page_size,
        )
        items = tuple(
            InstrumentMetadata.model_validate_json(payload, strict=True)
            for payload in payloads
        )
        return InstrumentPage(
            items=items,
            total=total,
            page=query.page,
            page_size=query.page_size,
            sort_key=query.sort_key,
            sort_direction=query.sort_direction,
        )

    def screen(self, snapshot: ResolvedSnapshot, query: ScreenQuery) -> ScreenPage:
        directory, manifest = self._verified_directory(snapshot)
        cache_key = (manifest.manifest_hash, query)
        with self._verified_table_lock:
            cached = self._screen_cache.get(cache_key)
            if cached is not None:
                self._screen_cache.move_to_end(cache_key)
                return cached
        conditions = ["horizon = ?"]
        parameters: list[object] = [query.horizon]
        if query.exclude_rankings:
            conditions.append("FALSE")
        filter_values = (
            ("sector", query.sector),
            ("asset_type", query.asset_type),
            ("regime", query.regime.value if query.regime is not None else None),
            (
                "serving_status",
                query.serving_status.value
                if query.serving_status is not None
                else None,
            ),
            ("effective_abstain", query.effective_abstain),
        )
        for column, value in filter_values:
            if value is not None:
                conditions.append(f"{column} = ?")
                parameters.append(value)
        if query.min_probability is not None:
            conditions.append(f"{_screen_probability_sql(query.prediction_basis)} >= ?")
            parameters.append(query.min_probability)
        if query.max_q10 is not None:
            conditions.append(f"{_screen_q10_sql(query.return_basis)} >= ?")
            parameters.append(query.max_q10)
        if query.minimum_confidence is not None:
            conditions.append(
                "CASE confidence_level "
                "WHEN 'low' THEN 1 WHEN 'medium' THEN 2 WHEN 'high' THEN 3 "
                "ELSE 0 END >= ?"
            )
            parameters.append(
                {
                    ScreenConfidenceFloor.LOW: 1,
                    ScreenConfidenceFloor.MEDIUM: 2,
                    ScreenConfidenceFloor.HIGH: 3,
                }[query.minimum_confidence]
            )
        where = " AND ".join(conditions)
        if query.sort_key == "default":
            order_by = _screen_default_order(
                query.prediction_basis,
                query.return_basis,
                query.sort_direction,
            )
        else:
            direction = _DIRECTION_SQL[query.sort_direction]
            if query.sort_key == "confidence_level":
                order_by = _screen_confidence_order(query.sort_direction)
            else:
                sort_column = _SCREEN_SORT_SQL[query.sort_key]
                tie_breaker = "" if sort_column == "symbol" else ", symbol ASC"
                order_by = f"{sort_column} {direction} NULLS LAST{tie_breaker}"
        payloads, total = self._payloads_with_total(
            directory,
            manifest,
            "screen.parquet",
            where=where,
            parameters=parameters,
            order_by=order_by,
            limit=query.page_size,
            offset=(query.page - 1) * query.page_size,
        )
        _validate_page(total, query.page, query.page_size)
        items = tuple(
            ScreenRow.model_validate_json(payload, strict=True) for payload in payloads
        )
        page = ScreenPage(
            items=items,
            total=total,
            page=query.page,
            page_size=query.page_size,
            sort_key=query.sort_key,
            sort_direction=query.sort_direction,
            prediction_basis=query.prediction_basis,
            return_basis=query.return_basis,
        )
        with self._verified_table_lock:
            self._screen_cache[cache_key] = page
            self._screen_cache.move_to_end(cache_key)
            while len(self._screen_cache) > _SCREEN_CACHE_MAX_ENTRIES:
                self._screen_cache.popitem(last=False)
        return page

    def snapshot_serving_reference(
        self, snapshot: ResolvedSnapshot
    ) -> SnapshotServingReference:
        directory, manifest = self._verified_directory(snapshot)
        results = tuple(self._validated_forecast_index(directory, manifest).values())
        valid_until = tuple(
            result.bundle.valid_until
            for result in results
            if isinstance(result, ForecastAvailable)
        )
        return SnapshotServingReference(
            generated_at=manifest.generated_at,
            valid_until=min(valid_until) if valid_until else None,
            has_available_forecasts=bool(valid_until),
            has_verified_research_outputs=any(
                table.path == "verified_research.parquet" and table.row_count > 0
                for table in manifest.tables
            ),
            has_challenger_preview=any(
                table.path == "challenger_preview.parquet" and table.row_count > 0
                for table in manifest.tables
            ),
        )

    def forecast(
        self, snapshot: ResolvedSnapshot, symbol: str, horizon: Horizon = 5
    ) -> ForecastResult:
        directory, manifest = self._verified_directory(snapshot)
        checked_symbol = _validated_symbol(symbol)
        forecasts = self._validated_forecast_index(directory, manifest)
        result = forecasts.get(checked_symbol)
        if result is None:
            raise SymbolNotInUniverse("symbol is not in the resolved universe")
        if result.availability == "unavailable":
            return result.model_copy(update={"requested_horizon": horizon})
        selected_horizon = next(
            item for item in result.bundle.horizons if item.horizon == horizon
        )
        return result.model_copy(update={"selected_horizon": selected_horizon})

    def history(
        self, snapshot: ResolvedSnapshot, symbol: str, query: HistoryQuery
    ) -> InstrumentHistory:
        _, manifest = self._verified_directory(snapshot)
        checked = _validated_symbol(symbol)
        key = (manifest.manifest_hash, checked, query)
        with self._history_lock:
            cached = self._history_cache.get(key)
            if cached is not None:
                self._history_cache.move_to_end(key)
                return cached
            result = self.history_batch(snapshot, (checked,), query).items[0]
            if result.history is None:
                raise SymbolNotInUniverse("symbol is not in the resolved universe")
            self._history_cache[key] = result.history
            while len(self._history_cache) > 64:
                self._history_cache.popitem(last=False)
            return result.history

    def snapshot_context(
        self, snapshot: ResolvedSnapshot, symbols: tuple[str, ...] = ()
    ) -> SnapshotContextReadModel:
        directory, manifest = self._verified_directory(snapshot)
        checked = tuple(dict.fromkeys(_validated_symbol(symbol) for symbol in symbols))
        payloads = self._payloads(
            directory,
            manifest,
            "instruments.parquet",
            where="symbol IN (" + ",".join("?" for _ in checked) + ")"
            if checked
            else "FALSE",
            parameters=list(checked),
            order_by="symbol ASC",
        )
        return SnapshotContextReadModel(
            symbol_count=self._count(
                directory, manifest, "instruments.parquet", "", []
            ),
            instruments=tuple(
                InstrumentMetadata.model_validate_json(payload, strict=True)
                for payload in payloads
            ),
        )

    def history_batch(
        self, snapshot: ResolvedSnapshot, symbols: tuple[str, ...], query: HistoryQuery
    ) -> HistoryBatchReadModel:
        directory, manifest = self._verified_directory(snapshot)
        checked = tuple(dict.fromkeys(_validated_symbol(symbol) for symbol in symbols))
        if not checked or len(checked) > 64:
            raise ValueError("history batch requires 1 to 64 symbols")
        where_symbols = "symbol IN (" + ",".join("?" for _ in checked) + ")"
        metadata = self._payloads(
            directory,
            manifest,
            "instruments.parquet",
            where=where_symbols,
            parameters=list(checked),
            order_by="symbol ASC",
        )
        available = {
            InstrumentMetadata.model_validate_json(payload, strict=True).symbol
            for payload in metadata
        }
        bars: dict[str, list[HistoryBar]] = {symbol: [] for symbol in available}
        events: dict[str, list[HistoryEvent]] = {symbol: [] for symbol in available}
        for table, column in (
            ("history_bars.parquet", '"at"'),
            ("history_events.parquet", "occurred_at"),
        ):
            conditions = [where_symbols, "timeframe = ?", "adjustment = ?"]
            parameters: list[object] = [*checked, query.timeframe, query.adjustment]
            for operator, value in ((">=", query.from_at), ("<=", query.to_at)):
                if value is not None:
                    conditions.append(f"{column} {operator} ?")
                    parameters.append(value)
            with self._verified_table_copy(directory, manifest, table) as verified:
                with _connection() as connection:
                    rows = connection.execute(
                        f"SELECT symbol, payload_json, {column} FROM read_parquet(?) WHERE {' AND '.join(conditions)} ORDER BY symbol ASC, {column} ASC",
                        [str(verified), *parameters],
                    ).fetchall()
            for row_symbol, payload, indexed_at in rows:
                if row_symbol not in available:
                    raise SnapshotIntegrityError(
                        "history symbol is absent from the snapshot universe"
                    )
                if table == "history_bars.parquet":
                    bar = HistoryBar.model_validate_json(payload, strict=True)
                    if bar.at != indexed_at:
                        raise SnapshotIntegrityError(
                            "history bar time differs from its index"
                        )
                    bars[row_symbol].append(bar)
                else:
                    event = HistoryEvent.model_validate_json(payload, strict=True)
                    if event.occurred_at != indexed_at:
                        raise SnapshotIntegrityError(
                            "history event time differs from its index"
                        )
                    events[row_symbol].append(event)
        return HistoryBatchReadModel(
            items=tuple(
                HistoryBatchItem(
                    symbol=symbol,
                    status="available",
                    history=InstrumentHistory(
                        symbol=symbol,
                        timeframe=query.timeframe,
                        adjustment=query.adjustment,
                        bars=tuple(bars[symbol]),
                        events=tuple(events[symbol]),
                        gaps=(),
                    ),
                )
                if symbol in available
                else HistoryBatchItem(
                    symbol=symbol,
                    status="unavailable",
                    error_code="symbol_not_in_universe",
                )
                for symbol in checked
            )
        )

    def backtests(
        self, snapshot: ResolvedSnapshot, query: BacktestQuery
    ) -> BacktestReadModel:
        directory, manifest = self._verified_directory(snapshot)
        conditions: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("model_version", query.model_version),
            ("strategy_version", query.strategy_version),
            ("universe_version", query.universe_version),
            ("split_version", query.split_version),
            ("task", query.task),
            ("horizon", query.horizon),
            ("asset_group", query.asset_group),
            ("evaluation_window_id", query.evaluation_window_id),
            ("cost_scenario", query.cost_scenario),
        ):
            if value is not None:
                conditions.append(f"{column} = ?")
                parameters.append(value)
        payloads = self._payloads(
            directory,
            manifest,
            "backtests.parquet",
            where=" AND ".join(conditions),
            parameters=parameters,
            order_by=(
                "model_version DESC, strategy_version DESC, "
                "split_version DESC, task ASC, horizon ASC, asset_group ASC, "
                "evaluation_window_id DESC, cost_scenario ASC"
            ),
            limit=1,
        )
        if not payloads:
            raise BacktestNotFound("backtest record was not found")
        backtest = BacktestReadModel.model_validate_json(payloads[0], strict=True)
        for field, requested in (
            ("model_version", query.model_version),
            ("strategy_version", query.strategy_version),
            ("universe_version", query.universe_version),
            ("split_version", query.split_version),
            ("task", query.task),
            ("horizon", query.horizon),
            ("asset_group", query.asset_group),
            ("evaluation_window_id", query.evaluation_window_id),
            ("cost_scenario", query.cost_scenario),
        ):
            if requested is not None and getattr(backtest, field) != requested:
                raise SnapshotIntegrityError(
                    f"backtest physical identity disagrees with payload field {field}"
                )
        if query.fold_id is None:
            return backtest

        fold_metrics = tuple(
            fold for fold in backtest.fold_metrics if fold.fold_id == query.fold_id
        )
        economic_series = tuple(
            point
            for point in backtest.economic_series
            if point.fold_id == query.fold_id
        )
        if not fold_metrics or not economic_series:
            raise BacktestNotFound("backtest fold was not found")
        return backtest.model_copy(
            update={
                "fold_metrics": fold_metrics,
                "economic_series": economic_series,
            }
        )

    def models(
        self, snapshot: ResolvedSnapshot, query: ModelQuery
    ) -> ModelMatrixReadModel:
        directory, manifest = self._verified_directory(snapshot)
        conditions: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("task", query.task),
            ("horizon", query.horizon),
            ("asset_group", query.asset_group),
        ):
            if value is not None:
                conditions.append(f"{column} = ?")
                parameters.append(value)
        payloads = self._payloads(
            directory,
            manifest,
            "models.parquet",
            where=" AND ".join(conditions),
            parameters=parameters,
            order_by="task ASC, horizon ASC, asset_group ASC",
        )
        rows = tuple(
            ModelTaskMatrixRow.model_validate_json(payload, strict=True)
            for payload in payloads
        )
        # Legacy data-only snapshots used an evaluated status for configured tasks.
        rows = tuple(
            row.model_copy(
                update={
                    "status": "configured",
                    "unavailable_reason": "snapshot_evaluation_missing",
                }
            )
            if row.status == "challenger_evaluated"
            and row.challenger_version is None
            and row.evaluation_run_id is None
            and row.gate_passed is None
            else row
            for row in rows
        )
        evaluated = tuple(row for row in rows if row.gate_passed is not None)
        summary = None
        overview_payloads = self._payloads(
            directory,
            manifest,
            "overview.parquet",
            where="horizon = ?",
            parameters=[5],
            order_by="horizon ASC",
        )
        if len(overview_payloads) == 1:
            summary = OverviewReadModel.model_validate_json(
                overview_payloads[0],
                strict=True,
            ).candidate_summary
        if summary is None and evaluated:
            first = evaluated[0]
            if (
                first.evaluation_run_id is not None
                and first.challenger_version is not None
                and first.challenger_calibration_version is not None
                and first.candidate_generated_at is not None
            ):
                passed = sum(row.gate_passed is True for row in evaluated)
                summary = CandidateEvaluationSummary(
                    run_id=first.evaluation_run_id,
                    model_version=first.challenger_version,
                    calibration_version=first.challenger_calibration_version,
                    generated_at=first.candidate_generated_at,
                    passed_tasks=passed,
                    total_tasks=len(evaluated),
                    all_gates_passed=passed == len(evaluated),
                    failed_tasks=tuple(
                        f"{row.task} ┬╖ {row.horizon}µùÑ"
                        for row in evaluated
                        if row.gate_passed is False
                    ),
                )
        return ModelMatrixReadModel(
            rows=rows,
            candidate_summary=summary,
        )

    def data_health(
        self, snapshot: ResolvedSnapshot, query: DataHealthQuery
    ) -> DataHealthReadModel:
        directory, manifest = self._verified_directory(snapshot)
        conditions: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("component", query.component),
            ("dataset", query.dataset),
            ("symbol", query.symbol),
            (
                "serving_status",
                query.serving_status.value
                if query.serving_status is not None
                else None,
            ),
        ):
            if value is not None:
                conditions.append(f"{column} = ?")
                parameters.append(value)
        where = " AND ".join(conditions)
        total = self._count(
            directory, manifest, "data_health.parquet", where, parameters
        )
        _validate_page(total, query.page, query.page_size)
        sort_column = _HEALTH_SORT_SQL[query.sort_key]
        direction = _DIRECTION_SQL[query.sort_direction]
        tie_breaker = "" if sort_column == "symbol" else ", symbol ASC NULLS LAST"
        payloads = self._payloads(
            directory,
            manifest,
            "data_health.parquet",
            where=where,
            parameters=parameters,
            order_by=f"{sort_column} {direction} NULLS LAST{tie_breaker}",
            limit=query.page_size,
            offset=(query.page - 1) * query.page_size,
        )
        return DataHealthReadModel(
            items=tuple(
                DataHealth.model_validate_json(payload, strict=True)
                for payload in payloads
            ),
            total=total,
            page=query.page,
            page_size=query.page_size,
            sort_key=query.sort_key,
            sort_direction=query.sort_direction,
        )

    def universes(self, snapshot: ResolvedSnapshot) -> UniverseReadModel:
        directory, manifest = self._verified_directory(snapshot)
        payloads = self._payloads(
            directory,
            manifest,
            "universes.parquet",
            where="",
            parameters=[],
            order_by="effective_from DESC, universe_version ASC",
        )
        return UniverseReadModel(
            versions=tuple(
                UniverseVersion.model_validate_json(payload, strict=True)
                for payload in payloads
            )
        )

    def _published_manifests(self) -> tuple[ResearchSnapshotManifest, ...]:
        scope = self._resolution_manifests.get()
        if scope is not None and "published" in scope:
            return scope["published"]
        if not self.root.is_dir():
            return ()
        manifests: list[ResearchSnapshotManifest] = []
        identities: set[str] = set()
        for directory in sorted(self.root.iterdir(), key=lambda value: value.name):
            if directory.name.lower().endswith(".staging") or directory.name.startswith(
                "."
            ):
                continue
            if _is_reparse_point(directory):
                raise SnapshotIntegrityError(
                    "published directory failed integrity verification"
                )
            if not stat.S_ISDIR(directory.lstat().st_mode):
                continue
            manifest = self._load_manifest(directory)
            if manifest.snapshot_id in identities:
                raise SnapshotIntegrityError("duplicate snapshot identity is published")
            identities.add(manifest.snapshot_id)
            manifests.append(manifest)
        result = tuple(manifests)
        if scope is not None:
            scope["published"] = result
        return result

    def _verified_directory(
        self, snapshot: ResolvedSnapshot
    ) -> tuple[Path, ResearchSnapshotManifest]:
        directory = self.root / snapshot.snapshot_id
        if not os.path.lexists(directory):
            raise SnapshotNotFound("resolved research snapshot was not found")
        manifest = self._load_manifest(directory)
        if manifest.resolved_snapshot() != snapshot:
            raise SnapshotVersionConflict(
                "resolved snapshot versions no longer match the published manifest"
            )
        return directory, manifest

    def _load_manifest(self, directory: Path) -> ResearchSnapshotManifest:
        cached = self._manifest_cache.get(directory)
        if cached is not None:
            try:
                _validate_snapshot_directory(self.root, directory)
                directory_identity = _file_identity(directory.lstat())
                for relative_path, expected_identity in cached.file_identities:
                    path = directory / relative_path
                    # Cached names are verified direct children; the parent is checked above and below.
                    information = path.lstat()
                    attributes = int(getattr(information, "st_file_attributes", 0))
                    if not stat.S_ISREG(information.st_mode) or attributes & 0x400:
                        raise ValueError(
                            f"{relative_path} is not a regular confined file"
                        )
                    current_identity = _file_identity(information)
                    if current_identity != expected_identity:
                        raise ValueError(f"{relative_path} changed after verification")
                if _file_identity(directory.lstat()) != directory_identity:
                    raise ValueError("snapshot directory changed during verification")
            except (OSError, ValueError) as error:
                self._manifest_cache.pop(directory, None)
                self._forecast_index_cache.pop(directory, None)
                raise SnapshotIntegrityError(
                    "published snapshot content failed integrity verification"
                ) from error
            return cached.manifest
        try:
            _validate_snapshot_directory(self.root, directory)
            manifest_path = directory / "manifest.json"
            manifest_content, manifest_identity = (
                _read_confined_regular_bytes_with_identity(
                    self.root, manifest_path, "manifest.json"
                )
            )
            manifest = ResearchSnapshotManifest.model_validate_json(
                manifest_content,
                strict=True,
            )
            if manifest.snapshot_id != directory.name:
                raise ValueError("manifest identity does not match directory")
            file_identities = [("manifest.json", manifest_identity)]
            for evidence in manifest.tables:
                table_path = directory / evidence.path
                resolve_table_path(directory, evidence.path)
                content, identity = _read_confined_regular_bytes_with_identity(
                    self.root, table_path, evidence.path
                )
                _validate_parquet_content(content, evidence)
                file_identities.append((evidence.path, identity))
            for relative_path, expected_identity in file_identities:
                current_identity = _file_identity(
                    _validate_confined_regular_file(
                        self.root, directory / relative_path, relative_path
                    )
                )
                if current_identity != expected_identity:
                    raise ValueError(f"{relative_path} changed after verification")
            _validate_snapshot_directory(self.root, directory)
        except (OSError, ValidationError, ValueError) as error:
            detail = str(error)
            safe_detail = next(
                (
                    name
                    for name in (
                        "manifest.json",
                        "overview.parquet",
                        "instruments.parquet",
                        "screen.parquet",
                        "forecasts.parquet",
                        "history_bars.parquet",
                        "history_events.parquet",
                        "similar_cases.parquet",
                        "backtests.parquet",
                        "models.parquet",
                        "data_health.parquet",
                        "universes.parquet",
                        "directory",
                    )
                    if name in detail
                ),
                "snapshot content",
            )
            raise SnapshotIntegrityError(
                f"published {safe_detail} failed integrity verification"
            ) from error
        self._manifest_cache[directory] = _CachedManifest(
            manifest=manifest,
            file_identities=tuple(file_identities),
        )
        return manifest

    @contextmanager
    def _verified_table_copy(
        self,
        directory: Path,
        manifest: ResearchSnapshotManifest,
        table: str,
    ) -> Iterator[Path]:
        evidence = manifest.table(table)
        source = directory / table
        try:
            _validate_snapshot_directory(self.root, directory)
            copy = self._cached_verified_table(directory, source, table, evidence)
            _validate_snapshot_directory(self.root, directory)
            yield copy
        except (OSError, ValueError) as error:
            raise SnapshotIntegrityError(
                f"published {table} failed integrity verification"
            ) from error

    def _cached_verified_table(
        self,
        directory: Path,
        source: Path,
        table: str,
        evidence: SnapshotTable,
    ) -> Path:
        key = (directory, table)
        with self._verified_table_lock:
            cached = self._verified_table_cache.get(key)
            if cached is not None and cached.content_hash == evidence.content_hash:
                try:
                    resolve_repository_path(self._verified_table_root, cached.path)
                    information = cached.path.lstat()
                    if _is_reparse_point(cached.path) or not stat.S_ISREG(
                        information.st_mode
                    ):
                        raise ValueError("verified table cache is not a regular file")
                    if _file_identity(information) != cached.file_identity:
                        raise ValueError("verified table cache identity changed")
                except (OSError, ValueError):
                    self._verified_table_cache.pop(key, None)
                else:
                    return cached.path

            snapshot_cache = self._verified_table_root / directory.name
            snapshot_cache.mkdir(exist_ok=True)
            copy = snapshot_cache / table
            staging = snapshot_cache / f"{table}.staging"
            if staging.exists():
                staging.unlink()
            _copy_confined_regular_file(self.root, source, staging, table)
            content = staging.read_bytes()
            _validate_parquet_content(content, evidence)
            if _is_reparse_point(staging) or not stat.S_ISREG(staging.lstat().st_mode):
                raise ValueError(f"{table} verified copy is not a regular file")
            os.replace(staging, copy)
            information = copy.lstat()
            cached = _CachedVerifiedTable(
                content_hash=evidence.content_hash,
                file_identity=_file_identity(information),
                path=copy,
            )
            self._verified_table_cache[key] = cached
            return copy

    def _check_exact_consistency(
        self, manifest: ResearchSnapshotManifest, query: SnapshotQuery
    ) -> None:
        expected: tuple[tuple[str, object | None, object], ...] = (
            ("as_of", query.as_of, manifest.as_of),
            ("dataset_revision", query.dataset_revision, manifest.dataset_revision),
            (
                "feature_set_version",
                query.feature_set_version,
                manifest.feature_set_version,
            ),
            ("universe_version", query.universe_version, manifest.universe_version),
            ("model_version", query.model_version, manifest.model_version),
            (
                "calibration_version",
                query.calibration_version,
                manifest.calibration_version,
            ),
        )
        for field, requested, actual in expected:
            if requested is not None and requested != actual:
                raise SnapshotVersionConflict(
                    f"requested {field} conflicts with snapshot manifest"
                )

    def _count(
        self,
        directory: Path,
        manifest: ResearchSnapshotManifest,
        table: str,
        where: str,
        parameters: list[object],
    ) -> int:
        query = "SELECT COUNT(*) FROM read_parquet(?)"
        if where:
            query += f" WHERE {where}"
        with self._verified_table_copy(directory, manifest, table) as verified:
            with _connection() as connection:
                row = connection.execute(
                    query,
                    [str(verified), *parameters],
                ).fetchone()
        return int(row[0]) if row is not None else 0

    def _require_in_universe(
        self,
        directory: Path,
        manifest: ResearchSnapshotManifest,
        symbol: str,
    ) -> None:
        total = self._count(
            directory,
            manifest,
            "instruments.parquet",
            "symbol = ?",
            [symbol],
        )
        if total == 0:
            raise SymbolNotInUniverse("symbol is not in the resolved universe")
        if total != 1:
            raise SnapshotIntegrityError("instrument identity is duplicated")

    def _validated_forecast_index(
        self,
        directory: Path,
        manifest: ResearchSnapshotManifest,
    ) -> dict[str, ForecastResult]:
        cached = self._forecast_index_cache.get(directory)
        if cached is not None:
            return cached
        with (
            self._verified_table_copy(
                directory, manifest, "instruments.parquet"
            ) as verified_instruments,
            self._verified_table_copy(
                directory, manifest, "forecasts.parquet"
            ) as verified_forecasts,
        ):
            with _connection() as connection:
                instrument_rows = connection.execute(
                    "SELECT symbol FROM read_parquet(?) ORDER BY symbol ASC",
                    [str(verified_instruments)],
                ).fetchall()
                forecast_rows = connection.execute(
                    "SELECT symbol, availability, payload_json "
                    "FROM read_parquet(?) ORDER BY symbol ASC",
                    [str(verified_forecasts)],
                ).fetchall()

        try:
            instrument_symbols = tuple(
                _validated_symbol(str(row[0])) for row in instrument_rows
            )
            forecast_symbols = tuple(
                _validated_symbol(str(row[0])) for row in forecast_rows
            )
            if not instrument_symbols:
                raise SnapshotIntegrityError("instrument universe is empty")
            if len(instrument_symbols) != len(set(instrument_symbols)):
                raise SnapshotIntegrityError("instrument identity is duplicated")
            if len(forecast_symbols) != len(set(forecast_symbols)):
                raise SnapshotIntegrityError("forecast identity is duplicated")
            if set(forecast_symbols) != set(instrument_symbols):
                raise SnapshotIntegrityError(
                    "forecast coverage does not match the instrument universe"
                )

            forecasts: dict[str, ForecastResult] = {}
            for row_symbol, row in zip(forecast_symbols, forecast_rows, strict=True):
                result = _FORECAST_ADAPTER.validate_json(str(row[2]), strict=True)
                payload_symbol = (
                    result.bundle.symbol
                    if isinstance(result, ForecastAvailable)
                    else result.symbol
                )
                if row_symbol != payload_symbol:
                    raise SnapshotIntegrityError(
                        "forecast row identity does not match its payload"
                    )
                if str(row[1]) != result.availability:
                    raise SnapshotIntegrityError(
                        "forecast availability does not match its payload"
                    )
                forecasts[row_symbol] = result
        except (UnknownSymbol, ValidationError) as error:
            raise SnapshotIntegrityError(
                "forecast universe relation failed validation"
            ) from error
        self._forecast_index_cache[directory] = forecasts
        return forecasts

    def _payloads(
        self,
        directory: Path,
        manifest: ResearchSnapshotManifest,
        table: str,
        *,
        where: str,
        parameters: list[object],
        order_by: str,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[str, ...]:
        query = "SELECT payload_json FROM read_parquet(?)"
        if where:
            query += f" WHERE {where}"
        query += f" ORDER BY {order_by}"
        with self._verified_table_copy(directory, manifest, table) as verified:
            query_parameters: list[object] = [str(verified), *parameters]
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                query_parameters.extend((limit, offset))
            with _connection() as connection:
                rows = connection.execute(query, query_parameters).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _payloads_with_total(
        self,
        directory: Path,
        manifest: ResearchSnapshotManifest,
        table: str,
        *,
        where: str,
        parameters: list[object],
        order_by: str,
        limit: int,
        offset: int,
    ) -> tuple[tuple[str, ...], int]:
        query = "SELECT payload_json, COUNT(*) OVER() FROM read_parquet(?)"
        if where:
            query += f" WHERE {where}"
        query += f" ORDER BY {order_by} LIMIT ? OFFSET ?"
        with self._verified_table_copy(directory, manifest, table) as verified:
            with _connection() as connection:
                rows = connection.execute(
                    query,
                    [str(verified), *parameters, limit, offset],
                ).fetchall()
        if rows:
            return tuple(str(row[0]) for row in rows), int(rows[0][1])
        if offset == 0:
            return (), 0
        total = self._count(directory, manifest, table, where, parameters)
        return (), total


class UnavailableResearchRepository:
    """Production-safe repository used when no published snapshot is configured."""

    def _unavailable(self) -> None:
        raise SnapshotNotFound("research snapshot unavailable")

    def resolve_snapshot(self, query: SnapshotQuery) -> ResolvedSnapshot:
        self._unavailable()
        raise AssertionError("unreachable")

    def snapshot_context(
        self, snapshot: ResolvedSnapshot, symbols: tuple[str, ...] = ()
    ) -> SnapshotContextReadModel:
        self._unavailable()
        raise AssertionError("unreachable")

    def history_batch(
        self, snapshot: ResolvedSnapshot, symbols: tuple[str, ...], query: HistoryQuery
    ) -> HistoryBatchReadModel:
        self._unavailable()
        raise AssertionError("unreachable")

    def instrument(self, snapshot: ResolvedSnapshot, symbol: str) -> InstrumentMetadata:
        self._unavailable()
        raise AssertionError("unreachable")

    def overview(
        self, snapshot: ResolvedSnapshot, horizon: Horizon
    ) -> OverviewReadModel:
        self._unavailable()
        raise AssertionError("unreachable")

    def instruments(
        self, snapshot: ResolvedSnapshot, query: InstrumentQuery
    ) -> InstrumentPage:
        self._unavailable()
        raise AssertionError("unreachable")

    def screen(self, snapshot: ResolvedSnapshot, query: ScreenQuery) -> ScreenPage:
        self._unavailable()
        raise AssertionError("unreachable")

    def snapshot_serving_reference(
        self, snapshot: ResolvedSnapshot
    ) -> SnapshotServingReference:
        self._unavailable()
        raise AssertionError("unreachable")

    def forecast(
        self, snapshot: ResolvedSnapshot, symbol: str, horizon: Horizon = 5
    ) -> ForecastResult:
        self._unavailable()
        raise AssertionError("unreachable")

    def history(
        self, snapshot: ResolvedSnapshot, symbol: str, query: HistoryQuery
    ) -> InstrumentHistory:
        self._unavailable()
        raise AssertionError("unreachable")

    def backtests(
        self, snapshot: ResolvedSnapshot, query: BacktestQuery
    ) -> BacktestReadModel:
        self._unavailable()
        raise AssertionError("unreachable")

    def models(
        self, snapshot: ResolvedSnapshot, query: ModelQuery
    ) -> ModelMatrixReadModel:
        self._unavailable()
        raise AssertionError("unreachable")

    def data_health(
        self, snapshot: ResolvedSnapshot, query: DataHealthQuery
    ) -> DataHealthReadModel:
        self._unavailable()
        raise AssertionError("unreachable")

    def universes(self, snapshot: ResolvedSnapshot) -> UniverseReadModel:
        self._unavailable()
        raise AssertionError("unreachable")


def _is_reparse_point(path: Path) -> bool:
    information = path.lstat()
    attributes = int(getattr(information, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(information.st_mode) or bool(attributes & reparse_flag)


def _validate_snapshot_directory(root: Path, directory: Path) -> None:
    if _is_reparse_point(directory):
        raise ValueError("snapshot directory is a reparse point")
    information = directory.lstat()
    if not stat.S_ISDIR(information.st_mode):
        raise ValueError("snapshot directory is not a regular directory")
    resolved = resolve_repository_path(root, directory)
    if resolved.parent != root.resolve():
        raise ValueError("snapshot directory resolves outside repository root")


def _validate_confined_regular_file(
    root: Path, path: Path, label: str
) -> os.stat_result:
    resolve_repository_path(root, path)
    if _is_reparse_point(path):
        raise ValueError(f"{label} is a reparse point")
    information = path.lstat()
    if not stat.S_ISREG(information.st_mode):
        raise ValueError(f"{label} is not a regular file")
    return information


def _file_identity(
    information: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        information.st_mode,
        information.st_dev,
        information.st_ino,
        information.st_size,
        information.st_mtime_ns,
        information.st_ctime_ns,
    )


def _read_confined_regular_bytes_with_identity(
    root: Path, path: Path, label: str
) -> tuple[bytes, tuple[int, int, int, int, int, int]]:
    before = _validate_confined_regular_file(root, path, label)
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        if not os.path.samestat(before, opened):
            raise ValueError(f"{label} changed before it could be read")
        content = source.read()
    after = _validate_confined_regular_file(root, path, label)
    if _file_identity(before) != _file_identity(after):
        raise ValueError(f"{label} changed while it was read")
    return content, _file_identity(after)


def _copy_confined_regular_file(
    root: Path, source: Path, destination: Path, label: str
) -> None:
    before = _validate_confined_regular_file(root, source, label)
    with source.open("rb") as input_file, destination.open("xb") as output_file:
        opened = os.fstat(input_file.fileno())
        if not os.path.samestat(before, opened):
            raise ValueError(f"{label} changed before it could be copied")
        shutil.copyfileobj(input_file, output_file)
    after = _validate_confined_regular_file(root, source, label)
    if not os.path.samestat(before, after):
        raise ValueError(f"{label} changed while it was copied")


def _validate_parquet_content(content: bytes, evidence: SnapshotTable) -> None:
    if hashlib.sha256(content).hexdigest() != evidence.content_hash:
        raise ValueError(f"{evidence.path} content hash mismatch")
    expected_schema = RESEARCH_TABLE_SCHEMAS[evidence.path]
    if parquet_schema_hash(expected_schema) != evidence.schema_hash:
        raise ValueError(f"{evidence.path} authoritative schema hash mismatch")
    try:
        table = pq.read_table(pa.BufferReader(content))
    except Exception as error:
        raise ValueError(f"{evidence.path} is not valid Parquet") from error
    if table.num_rows != evidence.row_count:
        raise ValueError(f"{evidence.path} row count mismatch")
    if not table.schema.equals(expected_schema, check_metadata=True):
        raise ValueError(f"{evidence.path} schema mismatch")


def _matches_version_filters(
    manifest: ResearchSnapshotManifest, query: SnapshotQuery
) -> bool:
    return all(
        requested is None or requested == actual
        for requested, actual in (
            (query.dataset_revision, manifest.dataset_revision),
            (query.feature_set_version, manifest.feature_set_version),
            (query.universe_version, manifest.universe_version),
            (query.model_version, manifest.model_version),
            (query.calibration_version, manifest.calibration_version),
        )
    )


def _validated_symbol(symbol: str) -> str:
    try:
        return TypeAdapter(UsSymbol).validate_python(symbol, strict=True)
    except ValidationError as error:
        raise UnknownSymbol("symbol identity is not recognized") from error


def _screen_default_order(
    prediction_basis: PredictionBasis,
    return_basis: ScreenReturnBasis,
    direction: SortDirection,
) -> str:
    probability_column = _screen_probability_sql(prediction_basis)
    return_column = {
        ScreenReturnBasis.CAPITAL_GAIN: "capital_gain_q50",
        ScreenReturnBasis.EXECUTABLE: "executable_q50",
    }[return_basis]
    sql_direction = _DIRECTION_SQL[direction]
    return (
        "effective_abstain ASC, "
        f"{probability_column} {sql_direction} NULLS LAST, "
        f"{return_column} {sql_direction} NULLS LAST, symbol ASC"
    )


def _screen_probability_sql(prediction_basis: PredictionBasis) -> str:
    return {
        PredictionBasis.FORECAST: (
            "CAST(json_extract_string(payload_json, "
            "'$.forecast_up_probability') AS DOUBLE)"
        ),
        PredictionBasis.EXECUTABLE: "executable_up_probability",
    }[prediction_basis]


def _screen_q10_sql(return_basis: ScreenReturnBasis) -> str:
    field = {
        ScreenReturnBasis.CAPITAL_GAIN: "capital_gain_q10",
        ScreenReturnBasis.EXECUTABLE: "executable_q10",
    }[return_basis]
    return f"CAST(json_extract_string(payload_json, '$.{field}') AS DOUBLE)"


def _screen_confidence_order(direction: SortDirection) -> str:
    sql_direction = _DIRECTION_SQL[direction]
    rank = (
        "CASE confidence_level "
        "WHEN 'low' THEN 1 WHEN 'medium' THEN 2 WHEN 'high' THEN 3 END"
    )
    return (
        "CASE WHEN confidence_level IN ('low', 'medium', 'high') "
        "THEN 0 ELSE 1 END ASC, "
        f"{rank} {sql_direction} NULLS LAST, symbol ASC"
    )


def _validate_page(total: int, page: int, page_size: int) -> None:
    last_page = max(1, (total + page_size - 1) // page_size)
    if page > last_page:
        raise ValueError("page exceeds the available total")


def _connection() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute("SET TimeZone = 'UTC'")
    return connection


__all__ = [
    "BacktestNotFound",
    "BacktestQuery",
    "DataHealthQuery",
    "DuckDbResearchRepository",
    "HistoryQuery",
    "InstrumentQuery",
    "ModelQuery",
    "ResearchReadRepository",
    "ScreenQuery",
    "SnapshotIntegrityError",
    "SnapshotNotFound",
    "SnapshotQuery",
    "SnapshotVersionConflict",
    "SymbolNotInUniverse",
    "UnavailableResearchRepository",
    "UnknownSymbol",
]
