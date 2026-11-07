from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stock_prediction.features.intraday import (
    HourlyBar,
    IntradaySessionSpec,
    aggregate_intraday_session,
)


def spec(*, winter: bool = False, half_day: bool = False) -> IntradaySessionSpec:
    opening_hour = 14 if winter else 13
    market_open = datetime(2026, 1 if winter else 7, 10, opening_hour, 30, tzinfo=UTC)
    close = market_open + (timedelta(hours=3, minutes=30) if half_day else timedelta(hours=6, minutes=30))
    return IntradaySessionSpec(
        symbol="US.AAPL",
        session_date=market_open.date(),
        market_open_at=market_open,
        market_close_at=close,
        dataset_revision="dataset-r1",
        adjustment_revision="adjustment-r1",
    )


def bars(
    session: IntradaySessionSpec,
    *,
    count: int,
    incomplete_last: bool = False,
) -> list[HourlyBar]:
    output = []
    for index in range(count):
        start = session.market_open_at + timedelta(hours=index)
        opening = 100.0 + index
        close = opening + 1.0
        output.append(
            HourlyBar(
                symbol=session.symbol,
                session_date=session.session_date,
                window_start=start,
                window_end=start + timedelta(hours=1),
                adjusted_open=opening,
                adjusted_high=close + 0.5,
                adjusted_low=opening - 0.5,
                adjusted_close=close,
                adjusted_volume=100_000.0 * (index + 1),
                source_available_at=start + timedelta(hours=1, seconds=1),
                ingested_at=start + timedelta(hours=1, minutes=1),
                complete_window=not (incomplete_last and index == count - 1),
                dataset_revision=session.dataset_revision,
                adjustment_revision=session.adjustment_revision,
            )
        )
    return output


def test_regular_session_uses_six_complete_hours_and_excludes_final_half_hour() -> None:
    session = spec()
    inputs = bars(session, count=6)
    # The 15:30-16:00 ET stub is not a complete 60-minute feature window.
    inputs.append(
        HourlyBar(
            symbol=session.symbol,
            session_date=session.session_date,
            window_start=session.market_close_at - timedelta(minutes=30),
            window_end=session.market_close_at,
            adjusted_open=106.0,
            adjusted_high=107.0,
            adjusted_low=105.0,
            adjusted_close=106.5,
            adjusted_volume=50_000.0,
            source_available_at=session.market_close_at,
            ingested_at=session.market_close_at + timedelta(minutes=1),
            complete_window=False,
            dataset_revision=session.dataset_revision,
            adjustment_revision=session.adjustment_revision,
        )
    )

    result = aggregate_intraday_session(
        inputs,
        session,
        as_of=session.market_close_at,
        median_volume_by_hour={index: 100_000.0 for index in range(6)},
    )

    assert result.expected_hour_count == 6
    assert result.complete_hour_count == 6
    assert result.partial is False
    assert result.first_hour_return == pytest.approx(0.01)
    assert result.last_hour_return == pytest.approx(1 / 105)
    assert result.relative_hourly_volume == pytest.approx(3.5)


def test_incomplete_expected_final_hour_is_partial_and_not_relabelled_as_last() -> None:
    session = spec()
    result = aggregate_intraday_session(
        bars(session, count=6, incomplete_last=True),
        session,
        as_of=session.market_close_at,
    )

    assert result.expected_hour_count == 6
    assert result.complete_hour_count == 5
    assert result.partial is True
    assert result.last_hour_return is None


@pytest.mark.parametrize(
    ("session", "expected"),
    [(spec(winter=True), 6), (spec(winter=True, half_day=True), 3)],
)
def test_explicit_session_boundaries_handle_dst_and_half_days(
    session: IntradaySessionSpec, expected: int
) -> None:
    result = aggregate_intraday_session(
        bars(session, count=expected),
        session,
        as_of=session.market_close_at,
    )

    assert result.expected_hour_count == expected
    assert result.complete_hour_count == expected
    assert result.partial is False


def test_future_windows_do_not_change_features_available_at_sample_cutoff() -> None:
    session = spec()
    all_bars = bars(session, count=6)
    cutoff = session.market_open_at + timedelta(hours=3)
    baseline = aggregate_intraday_session(all_bars[:3], session, as_of=cutoff)
    future_shock = all_bars[3].model_copy(
        update={
            "adjusted_high": 1_000.0,
            "adjusted_close": 999.0,
            "source_available_at": all_bars[3].window_end,
        }
    )
    with_future = aggregate_intraday_session(
        all_bars[:3] + [future_shock] + all_bars[4:],
        session,
        as_of=cutoff,
    )

    assert baseline == with_future
    assert baseline.expected_hour_count == 3
