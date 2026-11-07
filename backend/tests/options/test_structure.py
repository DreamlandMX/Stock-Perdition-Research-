from datetime import UTC, date, datetime, timedelta

import pytest

from stock_prediction.contracts.options import OptionChainSnapshot, OptionContract
from stock_prediction.options.structure import (
    build_option_heatmap,
    build_option_structure_features,
)
from stock_prediction.options.store import OptionSnapshotStore

NOW = datetime(2026, 10, 9, 20, tzinfo=UTC)


def test_structure_map_model_derivatives_and_reported_money():
    from math import erf, log, sqrt
    from stock_prediction.options.structure_map import build_option_structure_map

    call = contract().model_copy(
        update={
            "turnover": 1000,
            "volume": 10,
            "session_low": 0.9,
            "session_high": 1.1,
            "oi_date": None,
        }
    )
    put = contract("US.AAPL261016P100000").model_copy(
        update={
            "side": "put",
            "open_interest": 500,
            "turnover": 2000,
            "volume": 20,
            "session_low": 0.9,
            "session_high": 1.1,
        }
    )
    result = build_option_structure_map(
        snapshot((call, put)),
        dte_min=1,
        dte_max=90,
        strike_min_ratio=0.7,
        strike_max_ratio=1.3,
    )
    cell = result.cells[0]
    t = 7 / 365

    def delta(s, sigma):
        return (
            1
            + erf((log(s / 100) + sigma * sigma * t / 2) / (sigma * sqrt(t)) / sqrt(2))
        ) / 2

    gamma = (delta(100.001, 0.2) - delta(99.999, 0.2)) / 0.002
    vanna = (delta(100, 0.200001) - delta(100, 0.199999)) / 0.000002
    assert cell.call.gex == pytest.approx(gamma * 1000 * 100 * 100**2 * 0.01, rel=1e-6)
    assert cell.call.vex == pytest.approx(vanna * 1000 * 100 * 100 * 0.01, rel=1e-6)
    assert cell.put.gex == pytest.approx(-cell.call.gex / 2)
    assert result.total.call.turnover == 1000
    assert result.total.put.turnover == 2000
    assert result.windows[14].rankings[0].turnover == 3000
    assert result.windows[5].rankings == ()
    assert result.call_walls == result.put_walls == (100,)
    assert "oi_date_unproven" in cell.call.reasons
    # The fall DST transition adds one actual hour to this seven-calendar-day term.
    at = NOW + timedelta(days=21)
    dated = call.model_copy(
        update={
            "expiration": date(2026, 11, 6),
            "observed_at": at,
            "source_available_at": at,
            "ingested_at": at,
            "underlying_at": at,
        }
    )
    t = (7 * 24 + 1) / (365 * 24)
    gamma = (delta(100.001, 0.2) - delta(99.999, 0.2)) / 0.002
    dated_map = build_option_structure_map(
        snapshot((call,)).model_copy(
            update={
                "contracts": (dated,),
                "captured_at": at,
                "source_available_at": at,
                "underlying_at": at,
            }
        ),
        dte_min=1,
        dte_max=90,
        strike_min_ratio=0.7,
        strike_max_ratio=1.3,
    )
    assert dated_map.cells[0].call.gex == pytest.approx(
        gamma * 1000 * 100 * 100**2 * 0.01, rel=1e-6
    )


def test_structure_map_excludes_conflicting_turnover_and_keeps_missing_distinct():
    from stock_prediction.options.structure_map import build_option_structure_map

    bad = contract().model_copy(
        update={
            "turnover": 103379970,
            "volume": 2610,
            "session_low": 20.4,
            "session_high": 21.91,
            "iv": None,
        }
    )
    zero = contract("US.AAPL261016C110000").model_copy(
        update={"strike": 110, "turnover": 0, "volume": 0, "open_interest": 0}
    )
    result = build_option_structure_map(
        snapshot((bad, zero)),
        dte_min=1,
        dte_max=90,
        strike_min_ratio=0.7,
        strike_max_ratio=1.3,
    )
    assert result.cells[0].call.turnover is None
    assert result.cells[0].call.gex is None
    assert "turnover_session_range_conflict" in result.cells[0].call.reasons
    assert result.cells[1].call.turnover == result.cells[1].call.gex == 0
    assert result.turnover_coverage.valid == 1
    assert result.turnover_coverage.excluded == 1
    assert result.windows[90].rankings == ()
    assert result.call_walls == ()


def test_structure_map_quotes_after_underlying_are_not_false_stale():
    from stock_prediction.options.structure_map import build_option_structure_map

    observed = NOW + timedelta(seconds=30)
    available = NOW + timedelta(minutes=1)
    row = contract().model_copy(
        update={
            "observed_at": observed,
            "source_available_at": available,
            "ingested_at": available,
            "volume": 10,
            "turnover": 1000,
        }
    )
    batch = snapshot().model_copy(
        update={
            "contracts": (row,),
            "captured_at": available,
            "source_available_at": available,
        }
    )
    result = build_option_structure_map(
        batch, dte_min=1, dte_max=90, strike_min_ratio=0.7, strike_max_ratio=1.3
    )
    assert result.cells[0].call.turnover == 1000
    assert result.cells[0].call.gex is not None
    assert result.model_at == result.market_at == observed
    empty_batch = batch.model_copy(
        update={"contracts": (), "expected_contract_ids": (), "complete": False}
    )
    assert (
        build_option_structure_map(
            empty_batch,
            dte_min=1,
            dte_max=90,
            strike_min_ratio=0.7,
            strike_max_ratio=1.3,
        ).cells
        == ()
    )


def contract(code="US.AAPL261016C100000", **changes):
    return OptionContract(
        contract_id=code,
        underlying="US.AAPL",
        side="call",
        strike=100.0,
        expiration=date(2026, 10, 16),
        multiplier=100.0,
        standard=True,
        open_interest=1000.0,
        volume=0.0,
        iv=0.2,
        gamma=0.02,
        observed_at=NOW,
        source_available_at=NOW,
        ingested_at=NOW,
        underlying_price=100.0,
        underlying_at=NOW,
        oi_date=date(2026, 10, 8),
        greek_units="per_usd_v1",
        **changes,
    )


def snapshot(records=None, **changes):
    rows = records or (contract(),)
    return OptionChainSnapshot(
        snapshot_id="sample-1",
        raw_batch_id="sample-1",
        symbol="US.AAPL",
        captured_at=NOW,
        source_available_at=NOW,
        underlying_price=100.0,
        underlying_at=NOW,
        expected_contract_ids=tuple(c.contract_id for c in rows),
        contracts=tuple(rows),
        discovery_complete=True,
        complete=True,
        **changes,
    )


def test_gamma_and_volume_zero_are_not_missing():
    s = snapshot()
    heat = build_option_heatmap(s, metric="gamma", side="call")
    assert heat.cells[0].value == 200000.0
    assert heat.total == 200000.0
    assert heat.coverage.valid == 1
    assert build_option_heatmap(s, metric="volume", side="call").cells[0].value == 0


def test_premium_uses_actual_multiplier_and_sums_matching_cells():
    call = contract(bid=1.0, ask=3.0, quote_at=NOW).model_copy(
        update={"volume": 10, "multiplier": 50}
    )
    other = call.model_copy(update={"contract_id": "other", "strike": 105})
    put = call.model_copy(update={"contract_id": "put", "side": "put", "volume": 4})
    heat = build_option_heatmap(
        snapshot((call, other, put)), metric="premium", side="call"
    )
    assert heat.unit == "USD"
    assert heat.total == 2000
    assert heat.strike_summary == {"100.0": 1000, "105.0": 1000}
    assert heat.expiry_summary == {"2026-10-16": 2000}
    net = build_option_heatmap(snapshot((call, put)), metric="premium", side="net")
    assert net.cells[0].value == net.total == 600
    zero = call.model_copy(update={"volume": 0})
    assert (
        build_option_heatmap(snapshot((zero,)), metric="premium", side="call").total
        == 0
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"bid": None},
        {"ask": None},
        {"bid": 0},
        {"bid": 4},
        {"volume": None},
        {"multiplier": None},
        {
            "quote_at": NOW + timedelta(seconds=1),
            "source_available_at": NOW + timedelta(seconds=1),
            "ingested_at": NOW + timedelta(seconds=1),
        },
        {
            "observed_at": NOW + timedelta(seconds=1),
            "source_available_at": NOW + timedelta(seconds=1),
            "ingested_at": NOW + timedelta(seconds=1),
        },
        {"quote_at": None, "observed_at": None},
        {"ingested_at": NOW + timedelta(seconds=1)},
        {"volume": 1e308, "ask": 1e308},
    ],
)
def test_premium_invalid_inputs_are_missing_not_zero(changes):
    row = contract(bid=1.0, ask=3.0, quote_at=NOW).model_copy(update=changes)
    batch = snapshot().model_copy(
        update={
            "contracts": (row,),
            "source_available_at": changes.get("source_available_at", NOW),
        }
    )
    heat = build_option_heatmap(batch, metric="premium", side="call")
    assert heat.cells[0].value is None
    assert heat.total is None
    assert heat.coverage.missing == 1


def test_premium_observation_time_fallback_retains_partial_coverage():
    valid = contract(bid=1.0, ask=3.0).model_copy(update={"volume": 10})
    missing = valid.model_copy(update={"contract_id": "missing", "ask": None})
    heat = build_option_heatmap(
        snapshot((valid, missing)), metric="premium", side="call"
    )
    assert heat.total == 2000
    assert heat.status == "partial"
    assert heat.coverage.valid == heat.coverage.missing == 1


def test_iv_is_mean_and_adjusted_contracts_are_excluded():
    a = contract()
    b = a.model_copy(update={"contract_id": "other", "iv": 0.4})
    c = a.model_copy(update={"contract_id": "adjusted", "standard": False})
    heat = build_option_heatmap(snapshot((a, b, c)), metric="iv", side="call")
    assert heat.cells[0].value == pytest.approx(0.3)
    assert heat.total is None
    assert heat.coverage.excluded == 1
    assert heat.coverage.expected == 3


def test_missing_oi_is_not_zero_and_future_inputs_are_rejected():
    a = contract().model_copy(update={"open_interest": None})
    heat = build_option_heatmap(snapshot((a,)), metric="oi", side="call")
    assert heat.cells[0].value is None
    assert heat.coverage.missing == 1
    features = build_option_structure_features(
        snapshot(), as_of=NOW - timedelta(seconds=1)
    )
    assert features.available is False
    assert "snapshot_not_available" in features.reasons


def test_store_keeps_complete_pointer_after_partial_and_rejects_conflict(tmp_path):
    store = OptionSnapshotStore(tmp_path)
    s = snapshot()
    store.save(s)
    store.save(s)
    partial = s.model_copy(
        update={
            "snapshot_id": "partial",
            "complete": False,
            "captured_at": NOW + timedelta(minutes=1),
        }
    )
    store.save(partial)
    assert store.load("US.AAPL").snapshot_id == "sample-1"
    assert store.latest_attempt("US.AAPL").snapshot_id == "partial"
    with pytest.raises(ValueError, match="conflict"):
        store.save(s.model_copy(update={"underlying_price": 101.0}))


def test_store_reuses_validated_snapshots_but_detects_changes_and_deletion(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    store = OptionSnapshotStore(tmp_path)
    path = store.save(snapshot())
    validate = OptionChainSnapshot.model_validate_json
    parses = []

    def counted(payload):
        parses.append(payload)
        return validate(payload)

    monkeypatch.setattr(OptionChainSnapshot, "model_validate_json", counted)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = tuple(pool.map(lambda _: store.load("US.AAPL"), range(8)))
    assert all(item.underlying_price == 100 for item in results)
    assert len(parses) == 1
    changed = snapshot().model_copy(update={"underlying_price": 101})
    path.write_text(changed.model_dump_json(), encoding="utf-8")
    assert store.load("US.AAPL").underlying_price == 101
    assert len(parses) == 2
    import os

    information = path.stat()
    path.write_text(
        changed.model_copy(update={"underlying_price": 102}).model_dump_json(),
        encoding="utf-8",
    )
    os.utime(path, ns=(information.st_atime_ns, information.st_mtime_ns))
    assert store.load("US.AAPL").underlying_price == 102
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        store.load("US.AAPL")
    path.unlink()
    with pytest.raises(FileNotFoundError):
        store.load("US.AAPL")


def test_historical_feature_export_requires_field_time_evidence():
    a = contract().model_copy(update={"oi_date": None, "observed_at": None})
    f = build_option_structure_features(snapshot((a,)), as_of=NOW)
    assert f.values["call_oi"] is None
    assert f.available is False


def test_nonfinite_values_and_duplicate_identity_are_rejected():
    with pytest.raises(ValueError):
        contract().model_copy(update={"iv": float("inf")}).__class__.model_validate(
            contract().model_dump() | {"iv": float("inf")}
        )
    with pytest.raises(ValueError):
        snapshot((contract(), contract()))


def test_historical_import_cannot_self_assert_evidence_or_leak_field_times():
    with pytest.raises(ValueError, match="historical options require"):
        snapshot(time_evidence="historical_verified")
    with pytest.raises(ValueError, match="field time"):
        OptionContract.model_validate(
            contract().model_dump() | {"greeks_at": NOW + timedelta(seconds=1)}
        )


def test_missing_identity_never_claims_complete_denominator():
    s = snapshot().model_copy(
        update={
            "expected_contract_ids": ("missing", contract().contract_id),
            "complete": False,
        }
    )
    assert (
        build_option_heatmap(s, metric="oi", side="call").coverage.discovery_complete
        is False
    )
