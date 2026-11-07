from datetime import UTC, date

import pytest
from pydantic import ValidationError

from stock_prediction.contracts.market_data import (
    AdjustmentKnowledge,
    CorporateActionObservation,
    CorporateActionType,
    MarketBar,
    MarketSession,
    Timeframe,
)


def valid_bar(**overrides: object) -> MarketBar:
    values: dict[str, object] = {
        "symbol": "US.SPY",
        "timeframe": Timeframe.DAY_1,
        "session": MarketSession.REGULAR,
        "session_date": date(2026, 7, 10),
        "bar_start": "2026-07-10T13:30:00Z",
        "open": 620.0,
        "high": 625.0,
        "low": 618.0,
        "close": 624.0,
        "volume": 50_000_000,
        "turnover": 31_100_000_000.0,
        "source_available_at": "2026-07-10T20:00:05Z",
        "ingested_at": "2026-07-10T20:01:00Z",
        "raw_batch_id": "batch-20260710-spy-1d",
        "schema_version": "market-bar-v1",
        "dataset_revision": "dataset-r1",
        "revision_no": 1,
    }
    values.update(overrides)
    return MarketBar(**values)


def test_market_bar_has_stable_enums_and_natural_key() -> None:
    bar = valid_bar()

    assert [item.value for item in Timeframe] == ["1d", "60m"]
    assert [item.value for item in MarketSession] == ["regular", "pre", "post"]
    assert bar.natural_key == (
        "US.SPY",
        "1d",
        "regular",
        "2026-07-10T13:30:00+00:00",
    )
    assert bar.bar_start.tzinfo is UTC
    assert bar.source_available_at.tzinfo is UTC
    assert bar.ingested_at.tzinfo is UTC


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "SPY"),
        ("bar_start", "2026-07-10T13:30:00"),
        ("open", float("nan")),
        ("close", float("inf")),
        ("volume", -1),
        ("turnover", -0.01),
        ("revision_no", 0),
    ],
)
def test_market_bar_rejects_invalid_primitives(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        valid_bar(**{field: value})


def test_market_bar_rejects_source_time_after_ingestion_time() -> None:
    with pytest.raises(ValidationError, match="source_available_at"):
        valid_bar(
            source_available_at="2026-07-10T20:02:00Z",
            ingested_at="2026-07-10T20:01:00Z",
        )


def test_market_bar_round_trips_as_json_without_nan_or_sdk_types() -> None:
    bar = valid_bar()

    restored = MarketBar.model_validate_json(bar.model_dump_json())

    assert restored == bar
    assert restored.model_dump(mode="json")["timeframe"] == "1d"


def test_corporate_action_marks_retrospective_rehab_as_not_point_in_time() -> None:
    action = CorporateActionObservation(
        observation_id="rehab-spy-20200101-r1",
        event_id="split-spy-20200101",
        revision_no=1,
        symbol="US.SPY",
        action_type=CorporateActionType.SPLIT,
        ex_date=date(2020, 1, 1),
        split_ratio=4.0,
        cash_amount=None,
        currency="USD",
        knowledge=AdjustmentKnowledge.RETROSPECTIVE,
        source_available_at="2026-07-10T20:00:00Z",
        ingested_at="2026-07-10T20:01:00Z",
        raw_batch_id="rehab-batch-1",
        schema_version="corporate-action-v1",
        dataset_revision="rehab-r1",
    )

    assert action.point_in_time_eligible is False


def test_split_requires_positive_ratio_and_dividend_requires_cash_amount() -> None:
    common: dict[str, object] = {
        "observation_id": "action-1",
        "event_id": "event-1",
        "revision_no": 1,
        "symbol": "US.SPY",
        "ex_date": date(2020, 1, 1),
        "currency": "USD",
        "knowledge": AdjustmentKnowledge.POINT_IN_TIME,
        "source_available_at": "2019-12-01T20:00:00Z",
        "ingested_at": "2019-12-01T20:01:00Z",
        "raw_batch_id": "action-batch-1",
        "schema_version": "corporate-action-v1",
        "dataset_revision": "action-r1",
    }

    with pytest.raises(ValidationError, match="split_ratio"):
        CorporateActionObservation(
            **common,
            action_type=CorporateActionType.SPLIT,
            split_ratio=None,
            cash_amount=None,
        )
    with pytest.raises(ValidationError, match="cash_amount"):
        CorporateActionObservation(
            **common,
            action_type=CorporateActionType.CASH_DIVIDEND,
            split_ratio=None,
            cash_amount=None,
        )
