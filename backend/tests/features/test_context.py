from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stock_prediction.features.context import (
    BreadthObservation,
    ContextInputError,
    ReturnObservation,
    SectorMapping,
    UniverseMembership,
    compute_market_breadth,
    compute_relative_context,
)


AS_OF = datetime(2026, 7, 10, 20, tzinfo=UTC)


def member(
    symbol: str,
    *,
    asset_group: str = "stock",
    start: datetime = AS_OF - timedelta(days=30),
    end: datetime | None = None,
    version: str = "universe-v1",
) -> UniverseMembership:
    return UniverseMembership(
        symbol=symbol,
        asset_group=asset_group,
        universe_version=version,
        effective_from=start,
        effective_to=end,
    )


def breadth(
    symbol: str,
    *,
    return_1: float,
    above_ma20: bool | None = True,
    new_high20: bool = False,
    new_low20: bool = False,
    high_volatility: bool = False,
    correlation: float = 0.7,
) -> BreadthObservation:
    return BreadthObservation(
        symbol=symbol,
        as_of=AS_OF,
        source_available_at=AS_OF,
        ingested_at=AS_OF + timedelta(minutes=1),
        return_1=return_1,
        above_ma20=above_ma20,
        new_high20=new_high20,
        new_low20=new_low20,
        high_volatility=high_volatility,
        benchmark_correlation20=correlation,
        dataset_revision="dataset-r1",
    )


def test_complete_breadth_uses_effective_versioned_membership() -> None:
    memberships = [
        member("US.AAPL"),
        member("US.MSFT"),
        member("US.SPY", asset_group="etf"),
        # Future entry must not enter the historical denominator.
        member("US.NVDA", start=AS_OF + timedelta(days=1)),
        # Expired membership must also stay out.
        member("US.OLD", end=AS_OF - timedelta(seconds=1)),
    ]
    observations = [
        breadth("US.AAPL", return_1=0.02, new_high20=True),
        breadth("US.MSFT", return_1=-0.01, above_ma20=False, new_low20=True),
        breadth("US.SPY", return_1=0.01),
        breadth("US.NVDA", return_1=0.50),
    ]

    result = compute_market_breadth(
        memberships,
        observations,
        as_of=AS_OF,
        universe_version="universe-v1",
        minimum_coverage=0.80,
    )

    assert result.universe_count == 3
    assert result.constituent_count == 3
    assert result.partial is False
    assert result.advance_ratio == pytest.approx(2 / 3)
    assert result.ma_participation == pytest.approx(2 / 3)
    assert result.new_high_low_spread == pytest.approx(0.0)
    assert result.etf_diffusion == pytest.approx(1.0)


def test_low_coverage_returns_partial_counts_without_rescaled_metrics() -> None:
    memberships = [member(f"US.T{index}") for index in range(5)]
    observations = [
        breadth("US.T0", return_1=0.01),
        breadth("US.T1", return_1=0.02),
    ]

    result = compute_market_breadth(
        memberships,
        observations,
        as_of=AS_OF,
        universe_version="universe-v1",
        minimum_coverage=0.80,
    )

    assert result.universe_count == 5
    assert result.constituent_count == 2
    assert result.coverage == pytest.approx(0.4)
    assert result.partial is True
    assert result.advance_count == 2
    assert result.advance_ratio is None
    assert result.cross_section_median_return is None


def test_field_level_coverage_prevents_sparse_indicator_rescaling() -> None:
    memberships = [member(f"US.T{index}") for index in range(5)]
    observations = [
        breadth(
            f"US.T{index}",
            return_1=0.01,
            above_ma20=True if index == 0 else None,
        )
        for index in range(5)
    ]

    result = compute_market_breadth(
        memberships,
        observations,
        as_of=AS_OF,
        universe_version="universe-v1",
        minimum_coverage=0.80,
    )

    assert result.partial is False
    assert result.advance_ratio == pytest.approx(1.0)
    assert result.ma_participation is None


def test_observation_after_cutoff_is_not_used_to_fill_breadth() -> None:
    memberships = [member("US.AAPL")]
    future = breadth("US.AAPL", return_1=0.10).model_copy(
        update={
            "as_of": AS_OF + timedelta(days=1),
            "source_available_at": AS_OF + timedelta(days=1),
        }
    )
    result = compute_market_breadth(
        memberships,
        [future],
        as_of=AS_OF,
        universe_version="universe-v1",
        minimum_coverage=0.80,
    )
    assert result.constituent_count == 0
    assert result.partial is True

    stale = breadth("US.AAPL", return_1=0.10).model_copy(
        update={
            "as_of": AS_OF - timedelta(days=1),
            "source_available_at": AS_OF - timedelta(days=1),
        }
    )
    stale_result = compute_market_breadth(
        memberships,
        [stale],
        as_of=AS_OF,
        universe_version="universe-v1",
        minimum_coverage=0.80,
    )
    assert stale_result.constituent_count == 0


def returns(
    symbol: str,
    values: list[float],
    *,
    missing_index: int | None = None,
) -> list[ReturnObservation]:
    output = []
    for index, value in enumerate(values):
        if index == missing_index:
            continue
        observed = AS_OF - timedelta(days=len(values) - 1 - index)
        output.append(
            ReturnObservation(
                symbol=symbol,
                session_date=observed.date(),
                return_1=value,
                source_available_at=observed,
                ingested_at=observed + timedelta(minutes=1),
                dataset_revision="dataset-r1",
            )
        )
    return output


def mapping(*, start: datetime = AS_OF - timedelta(days=100)) -> SectorMapping:
    return SectorMapping(
        symbol="US.AAPL",
        sector_etf="US.XLK",
        mapping_version="sector-map-v1",
        effective_from=start,
        effective_to=None,
    )


def test_relative_strength_and_beta_use_only_aligned_past_dates() -> None:
    values = [0.01, -0.01, 0.02, 0.00, 0.01]
    observations = (
        returns("US.AAPL", values)
        + returns("US.SPY", [0.005, -0.005, 0.01, 0.0, 0.005])
        + returns("US.XLK", [0.008, -0.004, 0.015, 0.0, 0.007], missing_index=1)
    )

    result = compute_relative_context(
        "US.AAPL",
        observations,
        [mapping()],
        as_of=AS_OF,
        mapping_version="sector-map-v1",
        benchmark_symbol="US.SPY",
        lookback_sessions=5,
        minimum_aligned_sessions=3,
        expected_sessions=tuple(
            (AS_OF - timedelta(days=index)).date() for index in range(4, -1, -1)
        ),
        anchor_session=AS_OF.date(),
    )

    assert result.aligned_session_count == 4
    assert result.partial is False
    assert result.market_relative_strength is not None
    assert result.sector_relative_strength is not None
    assert result.market_beta is not None


def test_future_sector_mapping_cannot_be_used_historically() -> None:
    observations = (
        returns("US.AAPL", [0.01] * 3)
        + returns("US.SPY", [0.01] * 3)
        + returns("US.XLK", [0.01] * 3)
    )
    with pytest.raises(ContextInputError, match="active sector mapping"):
        compute_relative_context(
            "US.AAPL",
            observations,
            [mapping(start=AS_OF + timedelta(days=1))],
            as_of=AS_OF,
            mapping_version="sector-map-v1",
            benchmark_symbol="US.SPY",
            lookback_sessions=3,
            minimum_aligned_sessions=3,
            expected_sessions=tuple(
                (AS_OF - timedelta(days=index)).date()
                for index in range(2, -1, -1)
            ),
            anchor_session=AS_OF.date(),
        )


def test_relative_context_rejects_expected_window_ending_after_anchor() -> None:
    observations = (
        returns("US.AAPL", [0.01] * 3)
        + returns("US.SPY", [0.01] * 3)
        + returns("US.XLK", [0.01] * 3)
    )
    expected = tuple(
        (AS_OF - timedelta(days=index)).date()
        for index in range(1, -2, -1)
    )

    with pytest.raises(ContextInputError, match="end at the anchor"):
        compute_relative_context(
            "US.AAPL",
            observations,
            [mapping()],
            as_of=AS_OF,
            mapping_version="sector-map-v1",
            benchmark_symbol="US.SPY",
            lookback_sessions=3,
            minimum_aligned_sessions=3,
            expected_sessions=expected,
            anchor_session=AS_OF.date(),
        )
