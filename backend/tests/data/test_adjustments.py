from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from stock_prediction.contracts.market_data import (
    AdjustmentKnowledge,
    CorporateActionObservation,
    CorporateActionType,
    MarketBar,
    MarketSession,
    Timeframe,
)
from stock_prediction.data.adjustments import (
    AdjustedSeriesStore,
    AdjustmentConflictError,
    AdjustmentEngine,
    AdjustmentMode,
    AdjustmentUnavailableError,
)
from stock_prediction.data.catalog import DuckDbCatalog


AS_OF = datetime(2026, 7, 10, 20, 0, tzinfo=UTC)


def daily_bar(
    session_date: date,
    *,
    close: float,
    batch_id: str,
    open_price: float | None = None,
) -> MarketBar:
    opening = close if open_price is None else open_price
    return MarketBar(
        symbol="US.TEST",
        timeframe=Timeframe.DAY_1,
        session=MarketSession.REGULAR,
        session_date=session_date,
        bar_start=f"{session_date.isoformat()}T04:00:00Z",
        open=opening,
        high=max(opening, close) + 1.0,
        low=min(opening, close) - 1.0,
        close=close,
        volume=1_000_000,
        turnover=close * 1_000_000,
        source_available_at=f"{session_date.isoformat()}T20:00:00Z",
        ingested_at="2026-07-10T19:00:00Z",
        raw_batch_id=batch_id,
        schema_version="market-bar-v1",
        dataset_revision="dataset-r1",
        revision_no=1,
    )


def action(
    action_type: CorporateActionType,
    *,
    ex_date: date,
    split_ratio: float | None = None,
    cash_amount: float | None = None,
    knowledge: AdjustmentKnowledge = AdjustmentKnowledge.POINT_IN_TIME,
    observed_at: str = "2026-07-10T18:00:00Z",
    event_id: str | None = None,
    observation_id: str | None = None,
    revision_no: int = 1,
) -> CorporateActionObservation:
    return CorporateActionObservation(
        observation_id=observation_id
        or f"{action_type.value}-{ex_date.isoformat()}-r{revision_no}",
        event_id=event_id or f"event-{action_type.value}-{ex_date.isoformat()}",
        revision_no=revision_no,
        symbol="US.TEST",
        action_type=action_type,
        ex_date=ex_date,
        split_ratio=split_ratio,
        cash_amount=cash_amount,
        currency="USD",
        knowledge=knowledge,
        source_available_at=observed_at,
        ingested_at=observed_at,
        raw_batch_id="rehab-batch-1",
        schema_version="corporate-action-v1",
        dataset_revision="rehab-r1",
    )


def test_split_adjustment_removes_false_fifty_percent_loss() -> None:
    bars = [
        daily_bar(date(2026, 7, 8), close=100.0, batch_id="bar-1"),
        daily_bar(date(2026, 7, 9), close=50.0, batch_id="bar-2"),
    ]
    split = action(
        CorporateActionType.SPLIT,
        ex_date=date(2026, 7, 9),
        split_ratio=2.0,
        knowledge=AdjustmentKnowledge.RETROSPECTIVE,
    )

    result = AdjustmentEngine().adjust(
        bars,
        [split],
        mode=AdjustmentMode.RESEARCH_RETROSPECTIVE,
        as_of=AS_OF,
        adjustment_revision="adjustment-r1",
    )

    assert [row.adjusted_close for row in result.rows] == [50.0, 50.0]
    assert [row.adjusted_volume for row in result.rows] == [2_000_000.0, 1_000_000.0]
    assert result.rows[1].capital_gain_return == pytest.approx(0.0)
    assert result.rows[1].total_return_index == pytest.approx(1.0)
    assert result.point_in_time_eligible is False


def test_cash_dividend_is_excluded_from_capital_gain_and_included_in_total_return() -> (
    None
):
    bars = [
        daily_bar(date(2026, 7, 8), close=100.0, batch_id="bar-1"),
        daily_bar(date(2026, 7, 9), close=99.0, batch_id="bar-2"),
    ]
    dividend = action(
        CorporateActionType.CASH_DIVIDEND,
        ex_date=date(2026, 7, 9),
        cash_amount=1.0,
    )

    result = AdjustmentEngine().adjust(
        bars,
        [dividend],
        mode=AdjustmentMode.POINT_IN_TIME,
        as_of=AS_OF,
        adjustment_revision="adjustment-r1",
    )

    assert result.rows[1].capital_gain_return == pytest.approx(-0.01)
    assert result.rows[1].cash_distribution == pytest.approx(1.0)
    assert result.rows[1].total_return_index == pytest.approx(1.0)
    assert result.point_in_time_eligible is True


def test_real_nvda_close_prices_preserve_split_and_dividend_return_bases() -> None:
    # Prices: fixed live-20260911 snapshot, documented in nvda-real-split-check.json.
    # Events: NVIDIA June 2024 investor split FAQ (10:1; June 11 dividend $0.01).
    # Observation metadata remains synthetic; this is retrospective math, not PIT proof.
    bars = [
        daily_bar(date(2024, 6, day), close=close, batch_id=f"nvda-{day}").model_copy(
            update={"symbol": "US.NVDA"}
        )
        for day, close in [(7, 1208.88), (10, 121.79), (11, 120.91)]
    ]
    actions = [
        action(
            CorporateActionType.SPLIT,
            ex_date=date(2024, 6, 10),
            split_ratio=10.0,
            knowledge=AdjustmentKnowledge.RETROSPECTIVE,
        ).model_copy(update={"symbol": "US.NVDA"}),
        action(
            CorporateActionType.CASH_DIVIDEND,
            ex_date=date(2024, 6, 11),
            cash_amount=0.01,
            knowledge=AdjustmentKnowledge.RETROSPECTIVE,
        ).model_copy(update={"symbol": "US.NVDA"}),
    ]
    result = AdjustmentEngine().adjust(
        bars,
        actions,
        mode=AdjustmentMode.RESEARCH_RETROSPECTIVE,
        as_of=AS_OF,
        adjustment_revision="nvda-audit-only",
    )
    assert result.rows[0].adjusted_close == pytest.approx(120.888)
    assert result.rows[1].capital_gain_return == pytest.approx(121.79 / 120.888 - 1)
    assert result.rows[2].capital_gain_return == pytest.approx(120.91 / 121.79 - 1)
    assert result.rows[2].cash_distribution == pytest.approx(0.01)
    assert result.rows[2].total_return_index / result.rows[1].total_return_index == (
        pytest.approx(120.92 / 121.79)
    )
    assert result.point_in_time_eligible is False


def test_point_in_time_mode_refuses_retrospective_rehab() -> None:
    bars = [
        daily_bar(date(2026, 7, 8), close=100.0, batch_id="bar-1"),
        daily_bar(date(2026, 7, 9), close=50.0, batch_id="bar-2"),
    ]
    retrospective = action(
        CorporateActionType.SPLIT,
        ex_date=date(2026, 7, 9),
        split_ratio=2.0,
        knowledge=AdjustmentKnowledge.RETROSPECTIVE,
    )

    with pytest.raises(AdjustmentUnavailableError, match="point-in-time"):
        AdjustmentEngine().adjust(
            bars,
            [retrospective],
            mode=AdjustmentMode.POINT_IN_TIME,
            as_of=AS_OF,
            adjustment_revision="adjustment-r1",
        )


def test_point_in_time_mode_refuses_action_observed_after_as_of() -> None:
    bars = [
        daily_bar(date(2026, 7, 8), close=100.0, batch_id="bar-1"),
        daily_bar(date(2026, 7, 9), close=50.0, batch_id="bar-2"),
    ]
    late = action(
        CorporateActionType.SPLIT,
        ex_date=date(2026, 7, 9),
        split_ratio=2.0,
        observed_at="2026-07-11T18:00:00Z",
    )

    with pytest.raises(AdjustmentUnavailableError, match="not observable"):
        AdjustmentEngine().adjust(
            bars,
            [late],
            mode=AdjustmentMode.POINT_IN_TIME,
            as_of=AS_OF,
            adjustment_revision="adjustment-r1",
        )


def test_future_action_after_series_anchor_is_not_applied() -> None:
    bars = [
        daily_bar(date(2026, 7, 8), close=100.0, batch_id="bar-1"),
        daily_bar(date(2026, 7, 9), close=101.0, batch_id="bar-2"),
    ]
    future = action(
        CorporateActionType.SPLIT,
        ex_date=date(2026, 7, 20),
        split_ratio=2.0,
    )

    result = AdjustmentEngine().adjust(
        bars,
        [future],
        mode=AdjustmentMode.POINT_IN_TIME,
        as_of=AS_OF,
        adjustment_revision="adjustment-r1",
    )

    assert [row.adjusted_close for row in result.rows] == [100.0, 101.0]
    assert result.included_action_ids == ()


def test_latest_visible_action_revision_replaces_instead_of_double_counting() -> None:
    bars = [
        daily_bar(date(2026, 7, 8), close=100.0, batch_id="bar-1"),
        daily_bar(date(2026, 7, 9), close=25.0, batch_id="bar-2"),
    ]
    event_id = "split-event-1"
    old = action(
        CorporateActionType.SPLIT,
        ex_date=date(2026, 7, 9),
        split_ratio=2.0,
        event_id=event_id,
        revision_no=1,
    )
    revised = action(
        CorporateActionType.SPLIT,
        ex_date=date(2026, 7, 9),
        split_ratio=4.0,
        event_id=event_id,
        revision_no=2,
    )

    result = AdjustmentEngine().adjust(
        bars,
        [old, revised],
        mode=AdjustmentMode.POINT_IN_TIME,
        as_of=AS_OF,
        adjustment_revision="adjustment-r1",
    )

    assert [row.adjusted_close for row in result.rows] == [25.0, 25.0]
    assert result.included_action_ids == (revised.observation_id,)


def test_adjustment_artifact_is_idempotent_and_never_silently_overwritten(
    tmp_path: Path,
) -> None:
    bars = [
        daily_bar(date(2026, 7, 8), close=100.0, batch_id="bar-1"),
        daily_bar(date(2026, 7, 9), close=101.0, batch_id="bar-2"),
    ]
    engine = AdjustmentEngine()
    result = engine.adjust(
        bars,
        [],
        mode=AdjustmentMode.POINT_IN_TIME,
        as_of=AS_OF,
        adjustment_revision="adjustment-r1",
    )
    catalog = DuckDbCatalog(tmp_path / "catalog.duckdb")
    store = AdjustedSeriesStore(catalog)

    assert store.register(result) is True
    assert store.register(result) is False
    changed = engine.adjust(
        [bars[0], daily_bar(date(2026, 7, 9), close=102.0, batch_id="bar-3")],
        [],
        mode=AdjustmentMode.POINT_IN_TIME,
        as_of=AS_OF,
        adjustment_revision="adjustment-r1",
    )
    with pytest.raises(AdjustmentConflictError, match="adjustment revision"):
        store.register(changed)

    rows = catalog.query_dicts(
        """
        SELECT adjusted_close, adjustment_revision, point_in_time_eligible
        FROM adjusted_market_bars ORDER BY bar_start
        """
    )
    assert [row["adjusted_close"] for row in rows] == [100.0, 101.0]
    assert {row["adjustment_revision"] for row in rows} == {"adjustment-r1"}
    assert all(row["point_in_time_eligible"] for row in rows)
