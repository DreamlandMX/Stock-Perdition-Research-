"""Shared API and domain contracts."""

from typing import Any

from stock_prediction.contracts.ingestion import (
    DataQualityReport,
    IngestionTask,
    RawBatchManifest,
)
from stock_prediction.contracts.market_data import (
    CorporateActionObservation,
    MarketBar,
)
from stock_prediction.contracts.research import (
    FeatureArtifact,
    FeatureVector,
    FundamentalObservation,
    LabelBundle,
    RegimeState,
)

_SERVING_EXPORTS = {
    "BacktestReadModel",
    "DataHealth",
    "DataHealthReadModel",
    "DirectionProbabilities",
    "ForecastAvailable",
    "ForecastBundle",
    "ForecastEvidence",
    "ForecastResult",
    "ForecastUnavailable",
    "HorizonForecast",
    "InstrumentHistory",
    "InstrumentMetadata",
    "InstrumentPage",
    "ModelMatrixReadModel",
    "OverviewReadModel",
    "ResolvedSnapshot",
    "ReturnQuantiles",
    "ScreenPage",
    "ScreenRow",
    "ServingOverlay",
    "ServingStatus",
    "SimilarCaseView",
    "UniverseReadModel",
}


def __getattr__(name: str) -> Any:
    """Load M5 serving contracts lazily to avoid confidence import cycles."""

    if name in _SERVING_EXPORTS:
        from stock_prediction.contracts import serving

        return getattr(serving, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CorporateActionObservation",
    "DataQualityReport",
    "FeatureArtifact",
    "FeatureVector",
    "FundamentalObservation",
    "IngestionTask",
    "MarketBar",
    "RawBatchManifest",
    "LabelBundle",
    "RegimeState",
    *sorted(_SERVING_EXPORTS),
]
