from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from stock_prediction.modeling.m7_economics import (
    M7EconomicSeriesMetrics,
    evaluate_m7_direction_economics,
    m7_economic_bundle_guardrail_passed,
    m7_economic_horizon_is_catastrophic,
)


def _market() -> pd.DataFrame:
    sessions = [date(2024, 1, 2) + timedelta(days=index) for index in range(50)]
    rows: list[dict[str, object]] = []
    for symbol, drift in (("US.A", 0.02), ("US.B", -0.02), ("US.SPY", 0.005)):
        for index, session in enumerate(sessions):
            open_price = 100.0 * (1.0 + drift) ** index
            rows.append(
                {
                    "symbol": symbol,
                    "session_date": session,
                    "source_available_at": datetime.combine(
                        session, datetime.min.time(), tzinfo=UTC
                    ),
                    "open": open_price,
                    "close": open_price * (1.0 + drift),
                    "volume": 1_000_000.0,
                    "eligible": True,
                }
            )
    return pd.DataFrame(rows)


def _evidence(probability: tuple[float, float, float]):
    anchors = tuple(
        datetime.combine(value, datetime.min.time(), tzinfo=UTC)
        for value in (date(2024, 1, 25), date(2024, 1, 26), date(2024, 1, 27))
    )
    return {
        horizon: (
            tuple(
                (symbol, anchor, horizon)
                for anchor in anchors
                for symbol in ("US.A", "US.B")
            ),
            np.tile(np.asarray(probability), (len(anchors) * 2, 1)),
        )
        for horizon in (1, 5, 20)
    }


def test_economic_diagnostic_uses_next_open_caps_costs_and_short_filter() -> None:
    diagnostic = evaluate_m7_direction_economics(
        _market(),
        candidate_probabilities=_evidence((0.1, 0.2, 0.7)),
        incumbent_probabilities=_evidence((0.7, 0.2, 0.1)),
    )

    one_day = diagnostic.horizons[1]
    assert one_day.candidate.trade_count == 6
    assert one_day.candidate.average_gross_exposure == pytest.approx(0.1)
    assert one_day.candidate.average_turnover == pytest.approx(0.2)
    assert one_day.candidate.net_return < one_day.candidate.gross_return
    assert one_day.candidate.cost_sensitivity[20] < (
        one_day.candidate.cost_sensitivity[10]
    )
    assert one_day.spy.cohort_count == 3
    assert one_day.seed100_equal_weight.cohort_count == 3
    assert diagnostic.evidence_scope == "research_only"


def test_catastrophic_guardrail_requires_two_horizons_at_exact_boundaries() -> None:
    incumbent = M7EconomicSeriesMetrics(
        gross_return=0.0,
        net_return=0.0,
        annualized_sharpe=1.0,
        maximum_drawdown=-0.20,
        average_turnover=0.0,
        average_gross_exposure=0.0,
        trade_count=0,
        cohort_count=0,
        capacity_proxy_usd=0.0,
        cost_sensitivity={0: 0.0},
    )
    at_boundary = replace(
        incumbent,
        annualized_sharpe=0.75,
        maximum_drawdown=-0.25,
    )
    beyond_sharpe = replace(at_boundary, annualized_sharpe=0.749)
    beyond_both = replace(beyond_sharpe, maximum_drawdown=-0.251)

    assert m7_economic_horizon_is_catastrophic(at_boundary, incumbent) is False
    assert m7_economic_horizon_is_catastrophic(beyond_sharpe, incumbent) is True
    assert m7_economic_horizon_is_catastrophic(beyond_both, incumbent) is True
    assert m7_economic_bundle_guardrail_passed((True, False, False)) is True
    assert m7_economic_bundle_guardrail_passed((True, True, False)) is False


def test_maximum_drawdown_includes_loss_from_initial_capital() -> None:
    market = _market()
    market.loc[market["symbol"] == "US.A", "close"] = (
        market.loc[market["symbol"] == "US.A", "open"] * 0.98
    )

    diagnostic = evaluate_m7_direction_economics(
        market,
        candidate_probabilities=_evidence((0.1, 0.2, 0.7)),
        incumbent_probabilities=_evidence((0.1, 0.2, 0.7)),
    )

    assert diagnostic.horizons[1].candidate.maximum_drawdown < 0.0


def test_short_signal_is_excluded_below_price_and_liquidity_thresholds() -> None:
    market = _market()
    market.loc[market["symbol"] == "US.B", "close"] = 4.0
    market.loc[market["symbol"] == "US.B", "open"] = 4.0
    market.loc[market["symbol"] == "US.B", "volume"] = 100.0

    diagnostic = evaluate_m7_direction_economics(
        market,
        candidate_probabilities=_evidence((0.7, 0.2, 0.1)),
        incumbent_probabilities=_evidence((0.7, 0.2, 0.1)),
    )

    assert diagnostic.horizons[1].candidate.trade_count == 3
