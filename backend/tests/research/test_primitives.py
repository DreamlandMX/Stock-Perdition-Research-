from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from stock_prediction.contracts.research import CalendarSource
from stock_prediction.research.calendar import build_trading_calendar
from stock_prediction.research.primitives import (
    DailyFeatureInput,
    PrimitiveInputError,
    compute_market_primitives,
    compute_market_primitives_batch,
)


def sessions(count: int) -> list[date]:
    start = date(2026, 1, 2)
    return [start + timedelta(days=index) for index in range(count)]


def calendar(expected: list[date]):
    return build_trading_calendar(
        sessions=expected,
        calendar_version="test-calendar-v1",
        session_rule_version="test-session-v1",
        dataset_revision="dataset-r1",
        source=CalendarSource.SYNTHETIC_OFFLINE,
        source_reference_ids=("test-calendar-fixture",),
        generated_at=datetime(2025, 12, 31, tzinfo=UTC),
    )


def bar(
    session_date: date,
    *,
    close: float,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1_000_000.0,
    turnover: float | None = None,
) -> DailyFeatureInput:
    return DailyFeatureInput(
        symbol="US.TEST",
        asset_group="stock",
        session_date=session_date,
        available_at=datetime.combine(
            session_date, datetime.min.time(), tzinfo=UTC
        )
        + timedelta(hours=21),
        ingested_at=datetime.combine(
            session_date, datetime.min.time(), tzinfo=UTC
        )
        + timedelta(hours=21, minutes=1),
        adjusted_high=close if high is None else high,
        adjusted_low=close if low is None else low,
        adjusted_close=close,
        adjusted_volume=volume,
        turnover=close * volume if turnover is None else turnover,
        complete_regular_session=True,
        dataset_revision="dataset-r1",
        adjustment_revision="adjustment-r1",
    )


def by_date(
    bars: list[DailyFeatureInput], expected: list[date]
) -> dict[date, object]:
    return {
        row.session_date: row
        for row in compute_market_primitives(
            bars,
            calendar=calendar(expected),
            feature_set_version="features-v1",
        )
    }


def test_prior_channel_excludes_anchor_and_future_spikes() -> None:
    expected = sessions(23)
    base = [bar(day, close=100.0 + index) for index, day in enumerate(expected)]
    anchor = expected[21]
    baseline = by_date(base, expected)[anchor]

    shocked = list(base)
    shocked[21] = bar(anchor, close=121.0, high=1_000.0, low=1.0)
    shocked[22] = bar(expected[22], close=122.0, high=2_000.0, low=0.5)
    result = by_date(shocked, expected)[anchor]

    assert result.prior_high_20 == baseline.prior_high_20 == 120.0
    assert result.prior_low_20 == baseline.prior_low_20 == 101.0
    assert result.atr20 != baseline.atr20


def test_zero_path_has_finite_zero_statistics() -> None:
    expected = sessions(52)
    result = by_date(
        [bar(day, close=50.0) for day in expected], expected
    )[expected[-1]]

    assert result.valid_core is True
    assert result.atr20 == pytest.approx(0.0)
    assert result.efficiency_ratio20 == pytest.approx(0.0)
    assert result.normalized_slope20 == pytest.approx(0.0)
    assert result.trend_r2_20 == pytest.approx(0.0)
    assert result.realized_volatility20 == pytest.approx(0.0)
    assert result.downside_volatility20 == pytest.approx(0.0)
    assert result.bollinger_bandwidth20 == pytest.approx(0.0)
    assert result.ma20 == pytest.approx(50.0)
    assert result.ma50 == pytest.approx(50.0)


def test_missing_expected_session_is_visible_in_coverage_not_silently_skipped() -> None:
    expected = sessions(25)
    missing = expected[10]
    inputs = [bar(day, close=100.0) for day in expected if day != missing]

    result = by_date(inputs, expected)[expected[20]]

    assert result.data_coverage20 == pytest.approx(20 / 21)
    assert result.missing_session_count20 == 1
    assert result.valid_core is False
    assert result.prior_high_20 is None
    assert result.atr20 is None


def test_short_history_returns_auditable_incomplete_row() -> None:
    expected = sessions(10)
    result = by_date(
        [bar(day, close=100.0 + index) for index, day in enumerate(expected)],
        expected,
    )[expected[-1]]

    assert result.valid_core is False
    assert result.data_coverage20 == pytest.approx(10 / 21)
    assert result.prior_high_20 is None
    assert result.ma20 is None


def test_adjusted_split_continuity_avoids_false_return_and_ratios_are_past_based() -> None:
    expected = sessions(52)
    inputs = [bar(day, close=50.0, volume=1_000_000.0) for day in expected]
    inputs[-1] = bar(expected[-1], close=50.0, volume=2_000_000.0)

    result = by_date(inputs, expected)[expected[-1]]

    assert result.realized_volatility20 == pytest.approx(0.0)
    assert result.volume_ratio20 == pytest.approx(2.0)
    assert result.turnover_ratio20 == pytest.approx(2.0)


def test_rejects_duplicate_sessions_mixed_symbols_and_non_monotone_availability() -> None:
    expected = sessions(2)
    first = bar(expected[0], close=100.0)
    with pytest.raises(PrimitiveInputError, match="duplicate session"):
        compute_market_primitives(
            [first, first],
            calendar=calendar(expected),
            feature_set_version="features-v1",
        )

    mixed = bar(expected[1], close=100.0).model_copy(
        update={"symbol": "US.OTHER"}
    )
    with pytest.raises(PrimitiveInputError, match="one symbol and asset group"):
        compute_market_primitives(
            [first, mixed],
            calendar=calendar(expected),
            feature_set_version="features-v1",
        )

    future_available = bar(expected[1], close=100.0).model_copy(
        update={"available_at": datetime(2026, 12, 1, 20, tzinfo=UTC)}
    )
    with pytest.raises(PrimitiveInputError, match="availability order"):
        compute_market_primitives(
            [first, future_available],
            calendar=calendar(expected),
            feature_set_version="features-v1",
        )


def test_batch_entry_computes_multiple_symbols_in_one_versioned_path() -> None:
    expected = sessions(25)
    first = [bar(day, close=100.0) for day in expected]
    second = [
        bar(day, close=200.0).model_copy(update={"symbol": "US.OTHER"})
        for day in expected
    ]

    result = compute_market_primitives_batch(
        {"US.TEST": first, "US.OTHER": second},
        calendar=calendar(expected),
        feature_set_version="features-v1",
    )

    assert len(result) == 50
    assert {item.symbol for item in result} == {"US.TEST", "US.OTHER"}


def test_ma50_lineage_includes_late_ingestion_from_full_window() -> None:
    expected = sessions(52)
    inputs = [bar(day, close=100.0) for day in expected]
    late_ingestion = datetime(2027, 1, 5, 12, tzinfo=UTC)
    inputs[2] = inputs[2].model_copy(update={"ingested_at": late_ingestion})

    result = by_date(inputs, expected)[expected[-1]]

    assert result.ma50 == pytest.approx(100.0)
    assert result.source_max_ingested_at == late_ingestion
