from __future__ import annotations

from datetime import UTC, date, datetime

import pyarrow as pa
import pytest

from stock_prediction.features.core import (
    CoreFeatureError,
    build_core_feature_table,
)
from stock_prediction.contracts.research import CalendarSource
from stock_prediction.research.primitives import MarketPrimitives


def primitive(symbol: str, *, session_date: date, valid: bool = True) -> MarketPrimitives:
    return MarketPrimitives(
        symbol=symbol,
        asset_group="etf" if symbol == "US.SPY" else "stock",
        session_date=session_date,
        source_max_available_at=datetime(2026, 7, 10, 20, tzinfo=UTC),
        source_max_ingested_at=datetime(2026, 7, 10, 20, 1, tzinfo=UTC),
        dataset_revision="dataset-r1",
        adjustment_revision="adjustment-r1",
        feature_set_version="core-v1",
        calendar_version="test-calendar-v1",
        calendar_hash="b" * 64,
        calendar_source=CalendarSource.SYNTHETIC_OFFLINE,
        anchor_close=100.0,
        return_1=0.01 if valid else None,
        return_5=0.03 if valid else None,
        return_20=0.08 if valid else None,
        prior_high_20=110.0 if valid else None,
        prior_low_20=90.0 if valid else None,
        atr20=4.0 if valid else None,
        channel_width20=0.20 if valid else None,
        channel_position20=0.50 if valid else None,
        efficiency_ratio20=0.40 if valid else None,
        normalized_slope20=0.002 if valid else None,
        trend_r2_20=0.60 if valid else None,
        realized_volatility20=0.25 if valid else None,
        downside_volatility20=0.15 if valid else None,
        max_drawdown20=-0.05 if valid else None,
        max_up_move20=0.03 if valid else None,
        ma20=98.0 if valid else None,
        ma50=95.0 if valid else None,
        distance_ma20=100.0 / 98.0 - 1.0 if valid else None,
        distance_ma50=100.0 / 95.0 - 1.0 if valid else None,
        bollinger_bandwidth20=0.10 if valid else None,
        atr_percent20=0.04 if valid else None,
        volume_ratio20=1.20 if valid else None,
        turnover_ratio20=1.10 if valid else None,
        data_coverage20=1.0 if valid else 0.8,
        missing_session_count20=0 if valid else 4,
        valid_core=valid,
    )


def test_builds_all_symbols_in_one_deterministic_arrow_table() -> None:
    rows = [
        primitive("US.SPY", session_date=date(2026, 7, 10)),
        primitive("US.AAPL", session_date=date(2026, 7, 10)),
    ]

    first = build_core_feature_table(
        rows,
        feature_set_version="core-v1",
        universe_version="universe-v1",
    )
    second = build_core_feature_table(
        list(reversed(rows)),
        feature_set_version="core-v1",
        universe_version="universe-v1",
    )

    assert first.equals(second)
    assert first.column("symbol").to_pylist() == ["US.AAPL", "US.SPY"]
    assert first.schema.field("as_of").type == pa.timestamp("us", tz="UTC")
    assert first.column("feature_set_version").to_pylist() == ["core-v1"] * 2
    assert first.column("calendar_hash").to_pylist() == ["b" * 64] * 2
    assert first.column("horizon").to_pylist() == [None, None]


def test_incomplete_row_preserves_nulls_and_missing_flags_not_zeroes() -> None:
    table = build_core_feature_table(
        [primitive("US.AAPL", session_date=date(2026, 7, 10), valid=False)],
        feature_set_version="core-v1",
        universe_version="universe-v1",
    )

    assert table.column("return_20").to_pylist() == [None]
    assert table.column("valid_core").to_pylist() == [False]
    assert table.column("missing_core_window").to_pylist() == [True]
    assert table.column("data_quality_score").to_pylist() == pytest.approx([0.8])


def test_rejects_duplicate_keys_and_mixed_dataset_revisions() -> None:
    row = primitive("US.AAPL", session_date=date(2026, 7, 10))
    with pytest.raises(CoreFeatureError, match="duplicate"):
        build_core_feature_table(
            [row, row],
            feature_set_version="core-v1",
            universe_version="universe-v1",
        )

    mixed = row.model_copy(update={"dataset_revision": "dataset-r2"})
    with pytest.raises(CoreFeatureError, match="dataset revision"):
        build_core_feature_table(
            [row, mixed.model_copy(update={"symbol": "US.MSFT"})],
            feature_set_version="core-v1",
            universe_version="universe-v1",
        )
