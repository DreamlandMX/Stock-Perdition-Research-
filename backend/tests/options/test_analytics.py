from datetime import UTC, date, datetime, timedelta
from math import sqrt

import pytest

from stock_prediction.contracts.options import (
    OptionChainSnapshot,
    OptionContract,
    OptionStockReturn,
)
from stock_prediction.options.analytics import build_option_analytics
from stock_prediction.options.structure import build_option_structure_features

NOW = datetime(2026, 10, 9, 20, tzinfo=UTC)


def chain(at=NOW, identity="current", oi=100):
    rows = []
    for days, iv in ((28, 0.2), (56, 0.3)):
        for side, strike, delta in (
            ("call", 100, 0.5),
            ("put", 100, -0.5),
            ("call", 110, 0.25),
            ("put", 90, -0.25),
        ):
            rows.append(
                OptionContract(
                    contract_id=f"{days}-{side}-{strike}",
                    underlying="US.AAPL",
                    side=side,
                    strike=strike,
                    expiration=NOW.date() + timedelta(days=days),
                    multiplier=100,
                    standard=True,
                    open_interest=oi,
                    volume=10,
                    oi_date=at.date(),
                    iv=iv + (0.05 if strike == 90 else 0),
                    delta=delta,
                    delta_units="signed_unit_v1",
                    gamma=0.01,
                    greek_units="per_usd_v1",
                    greeks_at=at,
                    quote_at=at,
                    observed_at=at,
                    source_available_at=at,
                    ingested_at=at,
                    underlying_price=100,
                    underlying_at=at,
                    bid=1,
                    ask=1.2,
                )
            )
    return OptionChainSnapshot(
        snapshot_id=identity,
        raw_batch_id=identity,
        symbol="US.AAPL",
        captured_at=at,
        market_as_of=at,
        source_available_at=at,
        underlying_price=100,
        underlying_at=at,
        expected_contract_ids=tuple(r.contract_id for r in rows),
        contracts=tuple(rows),
        complete=True,
        discovery_complete=True,
    )


def test_analytics_numeric_features_and_same_identity_history():
    prior = chain(NOW - timedelta(days=1), "prior", 50)
    result = build_option_analytics(chain(), history=(prior,), as_of=NOW)
    assert result.values["atm_iv_30d"] == pytest.approx(0.2)
    assert result.values["iv_term_slope_per_day"] == pytest.approx(0.1 / 28)
    assert result.values["put_call_skew_25delta"] == pytest.approx(0.05)
    assert result.values["volume_oi_ratio"] == pytest.approx(0.1)
    assert result.values["oi_change_fraction"] == pytest.approx(1)
    assert result.values["oi_strike_hhi"] == pytest.approx(0.375)
    assert result.values["gamma_near_30d_share"] == pytest.approx(0.5)
    assert len(result.iv_terms) == 2 and len(result.history) == 6
    assert result.source_snapshot_ids == ("prior", "current")
    assert (
        build_option_structure_features(chain(), as_of=NOW).feature_version
        == "option-numeric-v1"
    )
    assert (
        build_option_structure_features(
            chain(), as_of=NOW, history=(prior,), feature_version="option-numeric-v2"
        ).values["oi_change_fraction"]
        == 1
    )


def test_unknown_delta_units_and_unproven_oi_remain_missing():
    s = chain()
    rows = tuple(
        r.model_copy(update={"delta_units": "unknown", "oi_date": None})
        for r in s.contracts
    )
    result = build_option_analytics(s.model_copy(update={"contracts": rows}), as_of=NOW)
    assert result.values["put_call_skew_25delta"] is None
    assert result.values["oi_strike_hhi"] is None
    assert result.values["gamma_near_30d_share"] is None
    assert (
        "delta_units_or_25delta_legs_missing"
        in result.missing_reasons["put_call_skew_25delta"]
    )


def test_future_snapshot_and_unmatched_history_cannot_enter_analytics():
    result = build_option_analytics(chain(), as_of=NOW - timedelta(seconds=1))
    assert result.status == "unavailable" and result.iv_terms == ()
    assert all(v is None for v in result.values.values())
    changed = chain(NOW - timedelta(days=1), "prior").model_copy(
        update={"strike_min_ratio": 0.8}
    )
    result = build_option_analytics(chain(), history=(changed,), as_of=NOW)
    assert result.values["oi_change_fraction"] is None
    assert result.history == ()


def test_realized_volatility_has_explicit_annualization_and_cutoff():
    returns = tuple(
        OptionStockReturn(
            symbol="US.AAPL",
            session=(NOW - timedelta(days=30 - i)).date(),
            log_return=0.01 if i % 2 else -0.01,
            observed_at=NOW - timedelta(days=30 - i),
            source_available_at=NOW - timedelta(days=30 - i),
            annualization_sessions=252,
        )
        for i in range(20)
    )
    future = returns[0].model_copy(
        update={
            "session": date(2026, 10, 11),
            "observed_at": NOW + timedelta(days=2),
            "source_available_at": NOW + timedelta(days=2),
            "log_return": 1,
        }
    )
    result = build_option_analytics(
        chain(), stock_returns=(*returns, future), as_of=NOW
    )
    rv = sqrt(20 * 0.01**2 / 19 * 252)
    assert result.values["realized_volatility_20sessions"] == pytest.approx(rv)
    assert result.values["iv_minus_realized_20sessions"] == pytest.approx(0.2 - rv)
    duplicate = (*returns, returns[0])
    assert (
        build_option_analytics(chain(), stock_returns=duplicate).values[
            "realized_volatility_20sessions"
        ]
        is None
    )


def test_missing_quote_time_and_partial_contracts_do_not_claim_full_coverage():
    s = chain()
    rows = tuple(
        r.model_copy(update={"quote_at": None, "observed_at": None})
        for r in s.contracts
    )
    result = build_option_analytics(s.model_copy(update={"contracts": rows}))
    assert result.values["mean_relative_spread"] is None
    partial = s.model_copy(update={"complete": False, "contracts": s.contracts[:-1]})
    result = build_option_analytics(partial)
    assert result.values["put_call_oi"] is None
    assert result.values["contract_coverage_ratio"] == pytest.approx(7 / 8)
    assert result.status == "partial"


def test_term_slope_requires_same_moneyness_and_history_same_capture_clock():
    s = chain()
    rows = tuple(
        r.model_copy(update={"strike": 101})
        if r.strike == 100 and r.expiration == NOW.date() + timedelta(days=56)
        else r
        for r in s.contracts
    )
    result = build_option_analytics(s.model_copy(update={"contracts": rows}))
    assert result.values["iv_term_slope_per_day"] is None
    shifted = chain(NOW - timedelta(days=1, hours=1), "shifted")
    assert build_option_analytics(s, history=(shifted,)).history == ()


def test_delta_validation_and_missing_gamma_scale_are_conservative():
    s = chain()
    with pytest.raises(ValueError, match="signed Delta"):
        OptionContract.model_validate(s.contracts[0].model_dump() | {"delta": 50})
    rows = tuple(r.model_copy(update={"multiplier": None}) for r in s.contracts)
    result = build_option_analytics(s.model_copy(update={"contracts": rows}))
    assert result.values["gamma_strike_hhi"] is None
    assert result.values["gamma_valid_ratio"] == 0
