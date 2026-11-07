"""Batch construction of the horizon-invariant core daily feature table."""

from __future__ import annotations

from collections.abc import Sequence

import pyarrow as pa

from stock_prediction.research.primitives import MarketPrimitives


class CoreFeatureError(ValueError):
    """Core feature rows have incompatible identity or revision evidence."""


CORE_FEATURE_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("asset_group", pa.string(), nullable=False),
        pa.field("session_date", pa.date32(), nullable=False),
        pa.field("as_of", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("horizon", pa.int16(), nullable=True),
        pa.field("feature_set_version", pa.string(), nullable=False),
        pa.field("universe_version", pa.string(), nullable=False),
        pa.field("dataset_revision", pa.string(), nullable=False),
        pa.field("adjustment_revision", pa.string(), nullable=False),
        pa.field("calendar_version", pa.string(), nullable=False),
        pa.field("calendar_hash", pa.string(), nullable=False),
        pa.field("calendar_source", pa.string(), nullable=False),
        pa.field("source_max_available_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("source_max_ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("anchor_close", pa.float64(), nullable=False),
        pa.field("return_1", pa.float64()),
        pa.field("return_5", pa.float64()),
        pa.field("return_20", pa.float64()),
        pa.field("atr20", pa.float64()),
        pa.field("atr_percent20", pa.float64()),
        pa.field("realized_volatility20", pa.float64()),
        pa.field("downside_volatility20", pa.float64()),
        pa.field("max_drawdown20", pa.float64()),
        pa.field("max_up_move20", pa.float64()),
        pa.field("efficiency_ratio20", pa.float64()),
        pa.field("normalized_slope20", pa.float64()),
        pa.field("trend_r2_20", pa.float64()),
        pa.field("distance_ma20", pa.float64()),
        pa.field("distance_ma50", pa.float64()),
        pa.field("channel_width20", pa.float64()),
        pa.field("channel_position20", pa.float64()),
        pa.field("bollinger_bandwidth20", pa.float64()),
        pa.field("volume_ratio20", pa.float64()),
        pa.field("turnover_ratio20", pa.float64()),
        pa.field("data_quality_score", pa.float64(), nullable=False),
        pa.field("missing_session_count20", pa.int16(), nullable=False),
        pa.field("valid_core", pa.bool_(), nullable=False),
        pa.field("missing_core_window", pa.bool_(), nullable=False),
    ]
)


def build_core_feature_table(
    rows: Sequence[MarketPrimitives],
    *,
    feature_set_version: str,
    universe_version: str,
) -> pa.Table:
    """Build every supplied symbol through one deterministic Arrow table path."""

    ordered = sorted(rows, key=lambda row: (row.symbol, row.session_date))
    identities = [(row.symbol, row.session_date) for row in ordered]
    if len(set(identities)) != len(identities):
        raise CoreFeatureError("core feature input contains a duplicate sample key")
    if len({row.dataset_revision for row in ordered}) > 1:
        raise CoreFeatureError("core feature input mixes dataset revisions")
    if any(row.feature_set_version != feature_set_version for row in ordered):
        raise CoreFeatureError("core feature version does not match primitives")
    if len(
        {
            (row.calendar_version, row.calendar_hash, row.calendar_source)
            for row in ordered
        }
    ) > 1:
        raise CoreFeatureError("core feature input mixes calendar evidence")

    records = [
        {
            "symbol": row.symbol,
            "asset_group": row.asset_group,
            "session_date": row.session_date,
            "as_of": row.source_max_available_at,
            "horizon": None,
            "feature_set_version": feature_set_version,
            "universe_version": universe_version,
            "dataset_revision": row.dataset_revision,
            "adjustment_revision": row.adjustment_revision,
            "calendar_version": row.calendar_version,
            "calendar_hash": row.calendar_hash,
            "calendar_source": row.calendar_source.value,
            "source_max_available_at": row.source_max_available_at,
            "source_max_ingested_at": row.source_max_ingested_at,
            "anchor_close": row.anchor_close,
            "return_1": row.return_1,
            "return_5": row.return_5,
            "return_20": row.return_20,
            "atr20": row.atr20,
            "atr_percent20": row.atr_percent20,
            "realized_volatility20": row.realized_volatility20,
            "downside_volatility20": row.downside_volatility20,
            "max_drawdown20": row.max_drawdown20,
            "max_up_move20": row.max_up_move20,
            "efficiency_ratio20": row.efficiency_ratio20,
            "normalized_slope20": row.normalized_slope20,
            "trend_r2_20": row.trend_r2_20,
            "distance_ma20": row.distance_ma20,
            "distance_ma50": row.distance_ma50,
            "channel_width20": row.channel_width20,
            "channel_position20": row.channel_position20,
            "bollinger_bandwidth20": row.bollinger_bandwidth20,
            "volume_ratio20": row.volume_ratio20,
            "turnover_ratio20": row.turnover_ratio20,
            "data_quality_score": row.data_coverage20,
            "missing_session_count20": row.missing_session_count20,
            "valid_core": row.valid_core,
            "missing_core_window": not row.valid_core,
        }
        for row in ordered
    ]
    return pa.Table.from_pylist(records, schema=CORE_FEATURE_SCHEMA)
