from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb
import pytest

from stock_prediction.contracts.research import (
    FundamentalObservation,
    PointInTimeEligibility,
)
from stock_prediction.research.asof import (
    AsOfSelectionError,
    MissingObservationReason,
    select_observations_as_of,
)
from stock_prediction.sql_assets import load_sql_asset


AS_OF = datetime(2026, 7, 10, 20, tzinfo=UTC)


def observation(
    *,
    event_id: str = "event-aapl-revenue-2026q2",
    observation_id: str = "obs-aapl-revenue-2026q2-r1",
    revision_no: int = 1,
    symbol: str = "US.AAPL",
    metric: str = "revenue",
    report_period: date = date(2026, 6, 30),
    published_at: str | None = "2026-07-01T12:00:00Z",
    source_available_at: str = "2026-07-01T12:05:00Z",
    ingested_at: str = "2026-07-01T12:06:00Z",
    eligibility: PointInTimeEligibility = PointInTimeEligibility.ELIGIBLE,
    value: float = 100.0,
) -> FundamentalObservation:
    return FundamentalObservation(
        observation_id=observation_id,
        event_id=event_id,
        revision_no=revision_no,
        symbol=symbol,
        metric=metric,
        value_numeric=value,
        value_text=None,
        unit="usd_millions",
        report_period=report_period,
        published_at=published_at,
        source_available_at=source_available_at,
        ingested_at=ingested_at,
        source_field="financial.revenue",
        point_in_time_eligibility=eligibility,
        raw_batch_id=f"batch-{observation_id}",
        schema_version="fundamental-v1",
        dataset_revision="dataset-r1",
    )


def test_selects_latest_market_visible_revision_and_only_audits_local_lag() -> None:
    original = observation()
    restatement = observation(
        observation_id="obs-aapl-revenue-2026q2-r2",
        revision_no=2,
        published_at="2026-07-09T12:00:00Z",
        source_available_at="2026-07-09T12:05:00Z",
        # Local collection happened after the historical sample cutoff. The source
        # timestamps, not this audit timestamp, control historical visibility.
        ingested_at="2026-07-12T12:06:00Z",
        value=110.0,
    )

    [selected] = select_observations_as_of([original, restatement], AS_OF)

    assert selected.observation == restatement
    assert selected.missing_reason is None
    assert selected.local_ingestion_lag_seconds == pytest.approx(259260.0)


def test_revision_observed_by_source_after_cutoff_is_not_visible() -> None:
    original = observation()
    future = observation(
        observation_id="obs-aapl-revenue-2026q2-r2",
        revision_no=2,
        published_at="2026-07-11T12:00:00Z",
        source_available_at="2026-07-11T12:05:00Z",
        ingested_at="2026-07-11T12:06:00Z",
        value=120.0,
    )

    [selected] = select_observations_as_of([future, original], AS_OF)

    assert selected.observation == original
    assert selected.visible_revision_count == 1


def test_current_only_field_returns_explicit_historical_missing_reason() -> None:
    current_snapshot = observation(
        event_id="event-aapl-analyst-rating",
        observation_id="obs-aapl-analyst-rating-r1",
        metric="analyst_rating",
        report_period=date(2026, 7, 10),
        published_at=None,
        source_available_at="2026-07-10T19:00:00Z",
        ingested_at="2026-07-10T19:01:00Z",
        eligibility=PointInTimeEligibility.FORWARD_ONLY,
    )

    [selected] = select_observations_as_of([current_snapshot], AS_OF)

    assert selected.observation is None
    assert selected.missing_reason is MissingObservationReason.FORWARD_ONLY
    assert selected.visible_revision_count == 0


@pytest.mark.parametrize(
    ("changed", "match"),
    [
        ({"symbol": "US.MSFT"}, "stable identity"),
        ({"metric": "net_income"}, "stable identity"),
        ({"report_period": date(2026, 3, 31)}, "stable identity"),
    ],
)
def test_rejects_event_ids_whose_stable_identity_changes(
    changed: dict[str, object], match: str
) -> None:
    base = observation()
    second_args: dict[str, object] = {
        "observation_id": "obs-aapl-revenue-2026q2-r2",
        "revision_no": 2,
        "source_available_at": "2026-07-09T12:05:00Z",
        "ingested_at": "2026-07-09T12:06:00Z",
    }
    second_args.update(changed)

    with pytest.raises(AsOfSelectionError, match=match):
        select_observations_as_of([base, observation(**second_args)], AS_OF)


def test_rejects_duplicate_revision_numbers_and_ambiguous_source_times() -> None:
    duplicate_revision = observation(
        observation_id="obs-aapl-revenue-duplicate",
        value=101.0,
    )
    with pytest.raises(AsOfSelectionError, match="duplicate revision"):
        select_observations_as_of([observation(), duplicate_revision], AS_OF)

    same_source_time = observation(
        observation_id="obs-aapl-revenue-2026q2-r2",
        revision_no=2,
        value=102.0,
    )
    with pytest.raises(AsOfSelectionError, match="ambiguous source time"):
        select_observations_as_of([observation(), same_source_time], AS_OF)

    reversed_time = observation(
        observation_id="obs-aapl-revenue-2026q2-r2",
        revision_no=2,
        published_at="2026-06-30T12:00:00Z",
        source_available_at="2026-06-30T12:05:00Z",
        ingested_at="2026-06-30T12:06:00Z",
        value=103.0,
    )
    with pytest.raises(AsOfSelectionError, match="revision chronology"):
        select_observations_as_of([observation(), reversed_time], AS_OF)


def test_symbol_metric_and_event_groups_remain_isolated() -> None:
    msft = observation(
        event_id="event-msft-revenue-2026q2",
        observation_id="obs-msft-revenue-2026q2-r1",
        symbol="US.MSFT",
        value=80.0,
    )
    aapl_income = observation(
        event_id="event-aapl-income-2026q2",
        observation_id="obs-aapl-income-2026q2-r1",
        metric="net_income",
        value=25.0,
    )

    selected = select_observations_as_of(
        [msft, observation(), aapl_income], AS_OF
    )

    assert [(row.symbol, row.metric) for row in selected] == [
        ("US.AAPL", "net_income"),
        ("US.AAPL", "revenue"),
        ("US.MSFT", "revenue"),
    ]


def test_duckdb_asset_matches_python_visibility_and_audits_future_rows() -> None:
    rows = [
        observation(),
        observation(
            observation_id="obs-aapl-revenue-2026q2-r2",
            revision_no=2,
            published_at="2026-07-09T12:00:00Z",
            source_available_at="2026-07-09T12:05:00Z",
            ingested_at="2026-07-12T12:06:00Z",
            value=110.0,
        ),
        observation(
            observation_id="obs-aapl-revenue-2026q2-r3",
            revision_no=3,
            published_at="2026-07-11T12:00:00Z",
            source_available_at="2026-07-11T12:05:00Z",
            ingested_at="2026-07-11T12:06:00Z",
            value=120.0,
        ),
    ]
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE fundamental_observations_input AS
        SELECT * FROM (VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?),
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?),
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ) AS values_table(
            event_id, observation_id, revision_no, symbol, metric,
            report_period, published_at, source_available_at, ingested_at,
            point_in_time_eligibility, value_numeric
        )
        """,
        [
            value
            for row in rows
            for value in (
                row.event_id,
                row.observation_id,
                row.revision_no,
                row.symbol,
                row.metric,
                row.report_period,
                row.published_at,
                row.source_available_at,
                row.ingested_at,
                row.point_in_time_eligibility.value,
                row.value_numeric,
            )
        ],
    )

    result = connection.execute(
        load_sql_asset("m3/asof_observations.sql"), {"as_of": AS_OF}
    ).fetchall()

    assert result == [
        (
            "event-aapl-revenue-2026q2",
            "obs-aapl-revenue-2026q2-r2",
            2,
            "US.AAPL",
            "revenue",
            110.0,
            False,
            1,
        )
    ]
