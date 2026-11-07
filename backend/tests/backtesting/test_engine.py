from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from stock_prediction.backtesting.costs import CostScenario
from stock_prediction.backtesting.engine import (
    BacktestConfigError,
    ExecutableBar,
    LongCashBacktester,
    StrategySpec,
    UniverseMembership,
    VerifiedMarketSession,
    VerifiedMarketCalendar,
    WeeklySignal,
)


def _cost() -> CostScenario:
    return CostScenario(
        half_spread_bps=2.0,
        liquidity_bps=3.0,
        commission_bps=1.0,
        regulatory_fee_bps=0.5,
        max_adv_participation=0.10,
        scenario_version="base-v1",
    )


def _strategy() -> StrategySpec:
    market_calendar = _calendar()
    return StrategySpec(
        horizon=5,
        top_k=2,
        initial_capital=100_000.0,
        single_stock_cap=0.10,
        industry_stock_cap=0.25,
        single_etf_cap=0.10,
        total_etf_cap=0.20,
        broad_etf_allowlist=("US.SPY", "US.QQQ"),
        cash_annual_yield=0.04,
        one_vintage=True,
        rebalance_rule="weekly_last_full_session",
        entry_rule="next_verified_open",
        exit_rule="fixed_horizon_close",
        cost_scenario=_cost(),
        strategy_version="long-cash-v1",
        universe_version="universe-v1",
        adjustment_revision="adjustment-v1",
        adjustment_input_hash="a" * 64,
        adjustment_mode="point_in_time_split_adjusted",
        calendar_version=market_calendar.calendar_version,
        calendar_hash=market_calendar.calendar_hash,
        minimum_executable_up_probability=0.60,
        score_definition="executable-up-probability",
        required_signal_version="signal-v1",
        model_evidence_hash="b" * 64,
        calibration_evidence_hash="c" * 64,
        dataset_input_hash="d" * 64,
        post_entry_missing_exit_rule="next_verified_close_then_total_loss",
        maximum_exit_delay_sessions=5,
    )


def _calendar() -> VerifiedMarketCalendar:
    anchor = VerifiedMarketSession(
        session_date=date(2024, 1, 5),
        session_open_at=datetime(2024, 1, 5, 14, 30, tzinfo=UTC),
        session_close_at=datetime(2024, 1, 5, 21, tzinfo=UTC),
        half_day=False,
    )
    future = tuple(
        VerifiedMarketSession(
            session_date=date(2024, 1, 8) + timedelta(days=index),
            session_open_at=datetime(2024, 1, 8 + index, 14, 30, tzinfo=UTC),
            session_close_at=datetime(2024, 1, 8 + index, 21, tzinfo=UTC),
            half_day=False,
        )
        for index in range(5)
    )
    return VerifiedMarketCalendar.create(
        calendar_version="nyse-calendar-v1",
        sessions=(anchor, *future),
        complete_through=date(2024, 1, 14),
    )


def _signal(anchor_at: datetime) -> WeeklySignal:
    return WeeklySignal(
        symbol="US.AAPL",
        anchor_at=anchor_at,
        score=0.8,
        executable_up_probability=0.8,
        abstain=False,
        data_quality_pass=True,
        liquidity_eligible=True,
        score_definition="executable-up-probability",
        signal_version="signal-v1",
        model_evidence_hash="b" * 64,
        calibration_evidence_hash="c" * 64,
        dataset_input_hash="d" * 64,
    )


def _session(day: date, *, half_day: bool = False) -> VerifiedMarketSession:
    close_hour = 18 if half_day else 21
    return VerifiedMarketSession(
        session_date=day,
        session_open_at=datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC),
        session_close_at=datetime(day.year, day.month, day.day, close_hour, tzinfo=UTC),
        half_day=half_day,
    )


def _calendar_artifact(
    days: tuple[date, ...], *, complete_through: date
) -> VerifiedMarketCalendar:
    return VerifiedMarketCalendar.create(
        calendar_version="nyse-calendar-v1",
        sessions=tuple(_session(day) for day in days),
        complete_through=complete_through,
    )


def _strategy_for(
    market_calendar: VerifiedMarketCalendar, **updates: object
) -> StrategySpec:
    values = _strategy().model_dump(mode="python")
    horizon = int(updates.get("horizon", values["horizon"]))
    values.update(
        {
            "calendar_version": market_calendar.calendar_version,
            "calendar_hash": market_calendar.calendar_hash,
            "horizon": horizon,
            "rebalance_rule": {
                1: "daily_close",
                5: "weekly_last_full_session",
                20: "month_end_full_session",
            }[horizon],
            **updates,
        }
    )
    return StrategySpec.model_validate(values, strict=True)


def _membership(
    symbol: str,
    *,
    asset_type: str = "stock",
    industry: str = "technology",
) -> UniverseMembership:
    return UniverseMembership.model_validate(
        {
            "symbol": symbol,
            "universe_version": "universe-v1",
            "effective_from": datetime(2024, 1, 1, tzinfo=UTC),
            "effective_to": None,
            "observed_at": datetime(2024, 1, 1, tzinfo=UTC),
            "point_in_time_eligible": True,
            "asset_type": asset_type,
            "industry": industry,
            "eligible": True,
        },
        strict=True,
    )


def _bar(
    symbol: str,
    market_session: VerifiedMarketSession,
    *,
    open_price: float = 100.0,
    close_price: float = 101.0,
    average_daily_dollar_volume: float = 10_000_000.0,
    cash_dividend: float = 0.0,
    cumulative_split_factor: float = 1.0,
    close_at: datetime | None = None,
) -> ExecutableBar:
    return ExecutableBar(
        symbol=symbol,
        session_date=market_session.session_date,
        session_open_at=market_session.session_open_at,
        session_close_at=close_at or market_session.session_close_at,
        open_price=open_price,
        close_price=close_price,
        average_daily_dollar_volume=average_daily_dollar_volume,
        cash_dividend=cash_dividend,
        verified=True,
        adjustment_revision="adjustment-v1",
        adjustment_input_hash="a" * 64,
        point_in_time_eligible=True,
        adjustment_mode="point_in_time_split_adjusted",
        cumulative_split_factor=cumulative_split_factor,
        dividend_basis="split_adjusted_per_share",
    )


def _signal_for(
    symbol: str, anchor_at: datetime, *, score: float, **updates: object
) -> WeeklySignal:
    values = _signal(anchor_at).model_dump(mode="python")
    values.update({"symbol": symbol, "score": score, **updates})
    return WeeklySignal.model_validate(values, strict=True)


def _weekdays_after(start: date, count: int) -> tuple[date, ...]:
    days: list[date] = []
    candidate = start + timedelta(days=1)
    while len(days) < count:
        if candidate.weekday() < 5:
            days.append(candidate)
        candidate += timedelta(days=1)
    return tuple(days)


def test_entry_uses_next_verified_open_and_never_anchor_close() -> None:
    signal_at = datetime(2024, 1, 5, 21, tzinfo=UTC)
    entry_at = datetime(2024, 1, 8, 14, 30, tzinfo=UTC)
    exit_at = datetime(2024, 1, 12, 21, tzinfo=UTC)
    bars = tuple(
        ExecutableBar(
            symbol="US.AAPL",
            session_date=date(2024, 1, 8) + timedelta(days=index),
            session_open_at=entry_at + timedelta(days=index),
            session_close_at=datetime(2024, 1, 8 + index, 21, tzinfo=UTC),
            open_price=101.0 + index,
            close_price=107.0 + index,
            average_daily_dollar_volume=10_000_000.0,
            cash_dividend=0.25 if index == 4 else 0.0,
            verified=True,
            adjustment_revision="adjustment-v1",
            adjustment_input_hash="a" * 64,
            point_in_time_eligible=True,
            adjustment_mode="point_in_time_split_adjusted",
            cumulative_split_factor=1.0,
            dividend_basis="split_adjusted_per_share",
        )
        for index in range(5)
    )
    result = LongCashBacktester().run(
        predictions=(_signal(signal_at),),
        executable_bars=bars,
        universe_history=(UniverseMembership(symbol="US.AAPL", universe_version="universe-v1", effective_from=datetime(2024, 1, 1, tzinfo=UTC), effective_to=None, observed_at=datetime(2024, 1, 1, tzinfo=UTC), point_in_time_eligible=True, asset_type="stock", industry="technology", eligible=True),),
        strategy=_strategy(),
        market_calendar=_calendar(),
    )

    assert result.trades[0].entry_at == entry_at
    assert result.trades[0].entry_price == 101.0
    assert result.trades[0].exit_at == exit_at
    assert result.trades[0].gross_return == pytest.approx((111.0 + 0.25) / 101.0 - 1)
    assert result.trades[0].net_return < result.trades[0].gross_return
    assert result.cash_weight >= 0.80
    assert result.model_evidence_hash == "b" * 64
    assert result.calibration_evidence_hash == "c" * 64
    assert "model_evidence_hash" in WeeklySignal.model_fields
    assert "calibration_evidence_hash" in WeeklySignal.model_fields
    assert "model_evidence_hash" in StrategySpec.model_fields
    assert "calibration_evidence_hash" in StrategySpec.model_fields
    assert "model_artifact_hash" not in type(result).model_fields
    assert "calibration_artifact_hash" not in type(result).model_fields


def test_missing_next_open_is_failed_fill_not_anchor_close_fallback() -> None:
    signal_at = datetime(2024, 1, 5, 21, tzinfo=UTC)
    result = LongCashBacktester().run(
        predictions=(_signal(signal_at),),
        executable_bars=(),
        universe_history=(UniverseMembership(symbol="US.AAPL", universe_version="universe-v1", effective_from=datetime(2024, 1, 1, tzinfo=UTC), effective_to=None, observed_at=datetime(2024, 1, 1, tzinfo=UTC), point_in_time_eligible=True, asset_type="stock", industry="technology", eligible=True),),
        strategy=_strategy(),
        market_calendar=_calendar(),
    )

    assert result.trades == ()
    assert result.failed_fills == 1
    assert result.cash_weight == 1.0


def test_one_vintage_and_nonzero_cost_are_mandatory() -> None:
    strategy = _strategy().model_copy(update={"one_vintage": False})
    with pytest.raises(BacktestConfigError, match="one vintage"):
        LongCashBacktester().run(
            (), (), (), strategy, market_calendar=_calendar()
        )


def test_retrospective_universe_or_adjustment_evidence_is_rejected() -> None:
    membership = UniverseMembership(
        symbol="US.AAPL",
        universe_version="universe-v1",
        effective_from=datetime(2024, 1, 1, tzinfo=UTC),
        effective_to=None,
        observed_at=datetime(2024, 1, 1, tzinfo=UTC),
        point_in_time_eligible=False,
        asset_type="stock",
        industry="technology",
        eligible=True,
    )
    with pytest.raises(BacktestConfigError, match="point-in-time universe"):
        LongCashBacktester().run(
            (), (), (membership,), _strategy(), market_calendar=_calendar()
        )

    session = _calendar().sessions[1]
    bar = ExecutableBar(
        symbol="US.AAPL",
        session_date=session.session_date,
        session_open_at=session.session_open_at,
        session_close_at=session.session_close_at,
        open_price=100.0,
        close_price=101.0,
        average_daily_dollar_volume=1_000_000.0,
        cash_dividend=0.0,
        verified=True,
        adjustment_revision="adjustment-v1",
        adjustment_input_hash="a" * 64,
        point_in_time_eligible=False,
        adjustment_mode="point_in_time_split_adjusted",
        cumulative_split_factor=1.0,
        dividend_basis="split_adjusted_per_share",
    )
    with pytest.raises(BacktestConfigError, match="point-in-time adjustment"):
        LongCashBacktester().run(
            (), (bar,), (), _strategy(), market_calendar=_calendar()
        )


def test_missing_scheduled_exit_uses_only_next_verified_close_within_delay() -> None:
    days = tuple(
        date(2024, 1, day) for day in (5, 8, 9, 10, 11, 12, 15, 16)
    )
    market_calendar = _calendar_artifact(days, complete_through=date(2024, 1, 21))
    strategy = _strategy_for(market_calendar, maximum_exit_delay_sessions=2)
    sessions = market_calendar.sessions
    bars = (
        _bar("US.AAPL", sessions[1]),
        _bar("US.AAPL", sessions[6], close_price=110.0),
    )

    result = LongCashBacktester().run(
        (_signal(sessions[0].session_close_at),),
        bars,
        (_membership("US.AAPL"),),
        strategy,
        market_calendar=market_calendar,
    )

    assert result.trades[0].exit_resolution == "delayed_close"
    assert result.trades[0].scheduled_exit_at == sessions[5].session_close_at
    assert result.trades[0].exit_at == sessions[6].session_close_at


def test_exit_after_maximum_calendar_delay_is_total_loss_not_late_fill() -> None:
    days = tuple(
        date(2024, 1, day) for day in (5, 8, 9, 10, 11, 12, 15, 16)
    )
    market_calendar = _calendar_artifact(days, complete_through=date(2024, 1, 21))
    strategy = _strategy_for(market_calendar, maximum_exit_delay_sessions=1)
    sessions = market_calendar.sessions
    bars = (
        _bar("US.AAPL", sessions[1]),
        _bar("US.AAPL", sessions[7], close_price=110.0),
    )

    result = LongCashBacktester().run(
        (_signal(sessions[0].session_close_at),),
        bars,
        (_membership("US.AAPL"),),
        strategy,
        market_calendar=market_calendar,
    )

    trade = result.trades[0]
    assert trade.exit_resolution == "total_loss"
    assert trade.exit_price == 0.0
    assert trade.gross_return == -1.0
    assert trade.exit_at == sessions[6].session_close_at
    assert result.failed_fills == 0


def test_wrong_scheduled_close_timestamp_is_not_accepted_as_execution() -> None:
    market_calendar = _calendar()
    entry_session = market_calendar.sessions[1]
    exit_session = market_calendar.sessions[-1]
    wrong_close = exit_session.session_close_at - timedelta(minutes=1)
    bars = (
        _bar("US.AAPL", entry_session),
        _bar("US.AAPL", exit_session, close_at=wrong_close),
    )

    result = LongCashBacktester().run(
        (_signal(market_calendar.sessions[0].session_close_at),),
        bars,
        (_membership("US.AAPL"),),
        _strategy_for(market_calendar, maximum_exit_delay_sessions=0),
        market_calendar=market_calendar,
    )

    assert result.trades[0].exit_resolution == "total_loss"


def test_missing_intermediate_bars_do_not_cancel_an_entered_trade() -> None:
    market_calendar = _calendar()
    sessions = market_calendar.sessions
    result = LongCashBacktester().run(
        (_signal(sessions[0].session_close_at),),
        (_bar("US.AAPL", sessions[1]), _bar("US.AAPL", sessions[-1])),
        (_membership("US.AAPL"),),
        _strategy_for(market_calendar),
        market_calendar=market_calendar,
    )

    assert len(result.trades) == 1
    assert result.trades[0].exit_resolution == "scheduled_close"


def test_split_adjusted_prices_keep_trade_value_continuous() -> None:
    market_calendar = _calendar()
    sessions = market_calendar.sessions
    result = LongCashBacktester().run(
        (_signal(sessions[0].session_close_at),),
        (
            _bar(
                "US.AAPL",
                sessions[1],
                open_price=100.0,
                close_price=100.0,
                cumulative_split_factor=1.0,
            ),
            _bar(
                "US.AAPL",
                sessions[-1],
                open_price=105.0,
                close_price=105.0,
                cumulative_split_factor=2.0,
            ),
        ),
        (_membership("US.AAPL"),),
        _strategy_for(market_calendar),
        market_calendar=market_calendar,
    )

    assert result.trades[0].gross_return == pytest.approx(0.05)


def test_half_day_is_not_used_for_entry_or_holding_period() -> None:
    friday = _session(date(2024, 1, 5))
    monday_half_day = _session(date(2024, 1, 8), half_day=True)
    tuesday = _session(date(2024, 1, 9))
    market_calendar = VerifiedMarketCalendar.create(
        calendar_version="nyse-calendar-v1",
        sessions=(friday, monday_half_day, tuesday),
        complete_through=date(2024, 1, 9),
    )
    result = LongCashBacktester().run(
        (_signal(friday.session_close_at),),
        (_bar("US.AAPL", tuesday),),
        (_membership("US.AAPL"),),
        _strategy_for(market_calendar, horizon=1),
        market_calendar=market_calendar,
    )

    assert result.trades[0].entry_at == tuesday.session_open_at
    assert result.trades[0].exit_at == tuesday.session_close_at


def test_truncated_week_calendar_cannot_certify_weekly_anchor() -> None:
    friday = _session(date(2024, 1, 5))
    market_calendar = VerifiedMarketCalendar.create(
        calendar_version="nyse-calendar-v1",
        sessions=(friday,),
        complete_through=date(2024, 1, 5),
    )

    with pytest.raises(BacktestConfigError, match="rebalance rule"):
        LongCashBacktester().run(
            (_signal(friday.session_close_at),),
            (),
            (_membership("US.AAPL"),),
            _strategy_for(market_calendar),
            market_calendar=market_calendar,
        )


def test_etf_limit_skips_whole_candidate_and_backfills_next_stock() -> None:
    market_calendar = _calendar()
    sessions = market_calendar.sessions
    anchor_at = sessions[0].session_close_at
    symbols = ("US.SPY", "US.QQQ", "US.DIA", "US.AAPL")
    signals = tuple(
        _signal_for(symbol, anchor_at, score=0.9 - index * 0.1)
        for index, symbol in enumerate(symbols)
    )
    memberships = tuple(
        _membership(
            symbol,
            asset_type="etf" if symbol != "US.AAPL" else "stock",
            industry="broad" if symbol != "US.AAPL" else "technology",
        )
        for symbol in symbols
    )
    bars = tuple(
        bar
        for symbol in symbols
        for bar in (
            _bar(symbol, sessions[1]),
            _bar(symbol, sessions[-1]),
        )
    )
    strategy = _strategy_for(
        market_calendar,
        top_k=3,
        broad_etf_allowlist=("US.SPY", "US.QQQ", "US.DIA"),
    )

    result = LongCashBacktester().run(
        signals,
        bars,
        memberships,
        strategy,
        market_calendar=market_calendar,
    )

    assert tuple(trade.symbol for trade in result.trades) == (
        "US.SPY",
        "US.QQQ",
        "US.AAPL",
    )
    assert all(trade.portfolio_weight == pytest.approx(0.10) for trade in result.trades)
    assert result.cash_weight == pytest.approx(0.70)


def test_abstained_signal_is_rejected_and_never_traded() -> None:
    market_calendar = _calendar()
    sessions = market_calendar.sessions
    result = LongCashBacktester().run(
        (_signal_for("US.AAPL", sessions[0].session_close_at, score=0.9, abstain=True),),
        (_bar("US.AAPL", sessions[1]), _bar("US.AAPL", sessions[-1])),
        (_membership("US.AAPL"),),
        _strategy_for(market_calendar),
        market_calendar=market_calendar,
    )

    assert result.trades == ()
    assert result.rejected_signals == 1


def test_rejected_signal_cannot_bypass_frozen_evidence_identity() -> None:
    market_calendar = _calendar()
    anchor_at = market_calendar.sessions[0].session_close_at
    signal = _signal_for(
        "US.AAPL",
        anchor_at,
        score=0.9,
        abstain=True,
        dataset_input_hash="e" * 64,
    )

    with pytest.raises(BacktestConfigError, match="signal evidence"):
        LongCashBacktester().run(
            (signal,),
            (),
            (_membership("US.AAPL"),),
            _strategy_for(market_calendar),
            market_calendar=market_calendar,
        )


def test_missing_point_in_time_universe_forces_research_only_result() -> None:
    market_calendar = _calendar()
    sessions = market_calendar.sessions
    result = LongCashBacktester().run(
        (_signal(sessions[0].session_close_at),),
        (_bar("US.AAPL", sessions[1]), _bar("US.AAPL", sessions[-1])),
        (),
        _strategy_for(market_calendar),
        market_calendar=market_calendar,
    )

    assert result.economic_evidence_scope == "research_only"
    assert "missing_point_in_time_universe" in result.research_only_reasons


def test_explicit_research_only_strategy_remains_research_only() -> None:
    market_calendar = _calendar()
    sessions = market_calendar.sessions
    result = LongCashBacktester().run(
        (_signal(sessions[0].session_close_at),),
        (_bar("US.AAPL", sessions[1]), _bar("US.AAPL", sessions[-1])),
        (_membership("US.AAPL"),),
        _strategy_for(market_calendar, economic_evidence_scope="research_only"),
        market_calendar=market_calendar,
    )

    assert result.economic_evidence_scope == "research_only"
    assert "strategy_declared_research_only" in result.research_only_reasons


def test_missing_exit_requires_complete_delay_calendar_before_total_loss() -> None:
    market_calendar = _calendar()
    sessions = market_calendar.sessions
    with pytest.raises(BacktestConfigError, match="exit delay coverage"):
        LongCashBacktester().run(
            (_signal(sessions[0].session_close_at),),
            (_bar("US.AAPL", sessions[1]),),
            (_membership("US.AAPL"),),
            _strategy_for(market_calendar, maximum_exit_delay_sessions=1),
            market_calendar=market_calendar,
        )


def test_cost_and_capacity_sensitivity_are_monotonic() -> None:
    market_calendar = _calendar()
    sessions = market_calendar.sessions
    result = LongCashBacktester().run(
        (_signal(sessions[0].session_close_at),),
        (
            _bar(
                "US.AAPL",
                sessions[1],
                average_daily_dollar_volume=100_000.0,
            ),
            _bar(
                "US.AAPL",
                sessions[-1],
                average_daily_dollar_volume=100_000.0,
            ),
        ),
        (_membership("US.AAPL"),),
        _strategy_for(market_calendar),
        market_calendar=market_calendar,
    )

    sensitivity_returns = [item.net_portfolio_return for item in result.cost_sensitivity]
    sensitivity_costs = [item.estimated_total_cost for item in result.cost_sensitivity]
    capacity_counts = [item.capacity_exceeded_count for item in result.capacity_sensitivity]
    assert sensitivity_returns == sorted(sensitivity_returns, reverse=True)
    assert sensitivity_costs == sorted(sensitivity_costs)
    assert capacity_counts == sorted(capacity_counts)
    assert result.turnover == pytest.approx((10_000.0 + 10_100.0) / 200_000.0)


def test_one_day_horizon_enters_next_full_open_and_exits_same_close() -> None:
    anchor_day = date(2024, 1, 5)
    exit_day = date(2024, 1, 8)
    market_calendar = _calendar_artifact(
        (anchor_day, exit_day), complete_through=exit_day
    )
    anchor_session, exit_session = market_calendar.sessions

    result = LongCashBacktester().run(
        (_signal(anchor_session.session_close_at),),
        (_bar("US.AAPL", exit_session),),
        (_membership("US.AAPL"),),
        _strategy_for(market_calendar, horizon=1),
        market_calendar=market_calendar,
    )

    assert result.trades[0].entry_at == exit_session.session_open_at
    assert result.trades[0].scheduled_exit_at == exit_session.session_close_at
    assert result.trades[0].exit_at == exit_session.session_close_at


def test_twenty_day_horizon_exits_on_twentieth_full_session() -> None:
    anchor_day = date(2024, 1, 31)
    future_days = _weekdays_after(anchor_day, 20)
    market_calendar = _calendar_artifact(
        (anchor_day, *future_days), complete_through=future_days[-1]
    )
    anchor_session = market_calendar.sessions[0]
    entry_session = market_calendar.sessions[1]
    exit_session = market_calendar.sessions[-1]

    result = LongCashBacktester().run(
        (_signal(anchor_session.session_close_at),),
        (
            _bar("US.AAPL", entry_session),
            _bar("US.AAPL", exit_session),
        ),
        (_membership("US.AAPL"),),
        _strategy_for(market_calendar, horizon=20),
        market_calendar=market_calendar,
    )

    assert result.trades[0].entry_at == entry_session.session_open_at
    assert result.trades[0].scheduled_exit_at == exit_session.session_close_at
    assert result.trades[0].exit_at == exit_session.session_close_at


def test_industry_cap_skips_whole_candidate_and_backfills_other_industry() -> None:
    market_calendar = _calendar()
    sessions = market_calendar.sessions
    anchor_at = sessions[0].session_close_at
    symbols = ("US.AAPL", "US.MSFT", "US.NVDA", "US.XOM")
    signals = tuple(
        _signal_for(symbol, anchor_at, score=0.9 - index * 0.1)
        for index, symbol in enumerate(symbols)
    )
    memberships = tuple(
        _membership(
            symbol,
            industry="energy" if symbol == "US.XOM" else "technology",
        )
        for symbol in symbols
    )
    bars = tuple(
        bar
        for symbol in symbols
        for bar in (
            _bar(symbol, sessions[1]),
            _bar(symbol, sessions[-1]),
        )
    )

    result = LongCashBacktester().run(
        signals,
        bars,
        memberships,
        _strategy_for(market_calendar, top_k=10),
        market_calendar=market_calendar,
    )

    assert tuple(trade.symbol for trade in result.trades) == (
        "US.AAPL",
        "US.MSFT",
        "US.XOM",
    )
    assert sum(
        trade.portfolio_weight
        for trade in result.trades
        if trade.symbol in {"US.AAPL", "US.MSFT", "US.NVDA"}
    ) == pytest.approx(0.20)
