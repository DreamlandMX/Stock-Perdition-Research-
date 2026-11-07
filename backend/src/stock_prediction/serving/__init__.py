"""Immutable snapshot materialization and read-only research repository."""

from stock_prediction.serving.manifest import (
    EXPECTED_RESEARCH_TABLES,
    RESEARCH_TABLE_SCHEMAS,
    ResearchSnapshotManifest,
    SnapshotId,
    SnapshotTable,
)
from stock_prediction.serving.materialize import (
    MaterializationError,
    MaterializationResult,
    materialize_research_snapshot,
)
from stock_prediction.serving.repository import (
    BacktestQuery,
    DataHealthQuery,
    DuckDbResearchRepository,
    HistoryQuery,
    InstrumentQuery,
    ModelQuery,
    ResearchReadRepository,
    ScreenQuery,
    SnapshotIntegrityError,
    SnapshotNotFound,
    SnapshotQuery,
    SnapshotVersionConflict,
    SymbolNotInUniverse,
    UnavailableResearchRepository,
    UnknownSymbol,
)
from stock_prediction.serving.resolution import (
    FutureSnapshotQuery,
    SnapshotRevisionAmbiguity,
    resolve_snapshot_query,
)
from stock_prediction.serving.status import (
    ServingEvaluationTimeError,
    ServingExclusionReason,
    evaluate_snapshot_status,
    overlay_forecast,
    overlay_snapshot,
)

__all__ = [
    "BacktestQuery",
    "DataHealthQuery",
    "DuckDbResearchRepository",
    "EXPECTED_RESEARCH_TABLES",
    "FutureSnapshotQuery",
    "RESEARCH_TABLE_SCHEMAS",
    "HistoryQuery",
    "InstrumentQuery",
    "MaterializationError",
    "MaterializationResult",
    "ModelQuery",
    "ResearchReadRepository",
    "ResearchSnapshotManifest",
    "ScreenQuery",
    "SnapshotIntegrityError",
    "SnapshotId",
    "SnapshotNotFound",
    "SnapshotQuery",
    "SnapshotRevisionAmbiguity",
    "SnapshotTable",
    "SnapshotVersionConflict",
    "SymbolNotInUniverse",
    "UnknownSymbol",
    "UnavailableResearchRepository",
    "ServingEvaluationTimeError",
    "ServingExclusionReason",
    "evaluate_snapshot_status",
    "materialize_research_snapshot",
    "overlay_forecast",
    "overlay_snapshot",
    "resolve_snapshot_query",
]
