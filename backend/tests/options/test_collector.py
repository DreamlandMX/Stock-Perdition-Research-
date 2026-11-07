from datetime import UTC, date, datetime

from stock_prediction.options.collector import expiration_windows, normalize_contract


def test_windows_cover_90_days_without_overlap():
    windows = expiration_windows(date(2026, 10, 10), 1, 90)
    assert windows == [
        ("2026-10-11", "2026-11-09"),
        ("2026-11-10", "2026-12-09"),
        ("2026-12-10", "2027-01-08"),
    ]


def test_futu_percent_iv_and_metadata_multiplier_are_explicit():
    now = datetime(2026, 10, 9, 20, tzinfo=UTC)
    chain = {
        "code": "US.AAPL261016C100000",
        "stock_owner": "US.AAPL",
        "option_type": "CALL",
        "strike_price": 100,
        "strike_time": "2026-10-16",
        "option_standard_type": "STANDARD",
        "lot_size": 10,
    }
    data = {
        "option_valid": True,
        "option_implied_volatility": 20,
        "option_open_interest": 0,
        "volume": 0,
        "option_gamma": 0.02,
        "update_time": "2026-10-09 16:00:00",
        "bid_price": 2,
        "ask_price": 1,
    }
    row = normalize_contract(chain, data, symbol="US.AAPL", now=now)
    assert row.iv == 0.2
    assert row.multiplier == 10
    assert row.open_interest == 0
    assert row.bid is None and row.ask is None
    assert "crossed_quote" in row.quality_flags
    assert row.oi_date is None and row.greeks_at is None


def test_invalid_option_fields_do_not_become_real_zeros():
    now = datetime(2026, 10, 9, 20, tzinfo=UTC)
    chain = {
        "code": "US.AAPL261016P100000",
        "stock_owner": "US.AAPL",
        "option_type": "PUT",
        "strike_price": 100,
        "strike_time": "2026-10-16",
    }
    row = normalize_contract(
        chain,
        {
            "option_valid": False,
            "option_open_interest": 0,
            "option_implied_volatility": 0,
        },
        symbol="US.AAPL",
        now=now,
    )
    assert row.open_interest is None and row.iv is None and row.standard is None


def test_offline_connection_records_sanitized_failures_and_preserves_old_snapshot(
    tmp_path,
):
    from stock_prediction.options.collector import OptionCollector
    from stock_prediction.options.store import OptionSnapshotStore
    from backend.tests.options.test_structure import snapshot, NOW

    store = OptionSnapshotStore(tmp_path)
    store.save(snapshot())

    def offline():
        raise ConnectionError("private account detail")

    results = OptionCollector(
        store, context_factory=offline, clock=lambda: NOW
    ).collect(["US.AAPL"])
    assert results[0].errors == ("connection_failed",)
    assert "private" not in results[0].model_dump_json()
    assert store.load("US.AAPL").snapshot_id == "sample-1"


def test_permission_denied_is_not_retried(tmp_path):
    from stock_prediction.options.collector import OptionCollector
    from stock_prediction.options.store import OptionSnapshotStore
    from stock_prediction.opend.ports import SdkCallResult
    from stock_prediction.opend.errors import classify_sdk_error

    calls = []

    def denied():
        calls.append(1)
        return SdkCallResult.failure(classify_sdk_error("no permission"))

    result = OptionCollector(OptionSnapshotStore(tmp_path))._call("chain", denied)
    assert not result.is_ok
    assert len(calls) == 1


def test_expanded_symbol_without_contracts_is_not_a_successful_empty_snapshot(tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    import pandas as pd
    from stock_prediction.options.collector import OptionCollector
    from stock_prediction.options.store import OptionSnapshotStore
    from stock_prediction.opend.ports import SdkCallResult

    now = datetime(2026, 10, 30, 20, tzinfo=UTC)
    context = SimpleNamespace(
        get_global_state=lambda: SdkCallResult.success({}),
        get_option_expiration_date=lambda symbol: SdkCallResult.success(
            pd.DataFrame(columns=["strike_time"])
        ),
        get_market_snapshot=lambda codes: SdkCallResult.success(
            pd.DataFrame(
                [
                    {
                        "code": "US.TSM",
                        "last_price": 100,
                        "update_time": now.isoformat(),
                    }
                ]
            )
        ),
        get_option_chain=lambda *args, **kwargs: SdkCallResult.success(
            pd.DataFrame(
                columns=[
                    "code",
                    "stock_owner",
                    "option_type",
                    "strike_price",
                    "strike_time",
                ]
            )
        ),
    )
    store = OptionSnapshotStore(tmp_path)
    (result,) = OptionCollector(
        store, context_factory=lambda: nullcontext(context), clock=lambda: now
    ).collect(["US.TSM"])
    assert result.errors == ("no_option_contracts",)
    assert not result.complete
    assert store.load("US.TSM").complete is False
    assert not (tmp_path / "US.TSM" / "latest-complete.json").exists()
    assert store.latest_attempt("US.TSM").errors == ("no_option_contracts",)


def test_collection_timeout_is_saved_as_an_attempt_without_replacing_success(
    tmp_path, monkeypatch
):
    from datetime import timedelta
    from backend.tests.options.test_analytics import chain, NOW
    import stock_prediction.options.collector as module
    from stock_prediction.options.store import OptionSnapshotStore

    store = OptionSnapshotStore(tmp_path)
    store.save(chain(at=NOW, identity="old-success"))

    def timeout(*args, **kwargs):
        raise TimeoutError("collection_timeout")

    monkeypatch.setattr(module.OptionCollector, "collect", timeout)
    result = module.run_option_collection(
        store,
        symbols=("US.AAPL",),
        clock=lambda: NOW + timedelta(seconds=1),
        refresh_monitor=False,
    )
    assert result.reason == "collection_timeout"
    assert store.latest_attempt("US.AAPL").errors == ("collection_timeout",)
    assert store.load("US.AAPL").snapshot_id == "old-success"


def test_eligible_complete_pointer_avoids_scanning_history_but_old_cutoff_stays_pinned(
    tmp_path, monkeypatch
):
    from datetime import timedelta
    from backend.tests.options.test_analytics import chain, NOW
    from stock_prediction.options.store import OptionSnapshotStore

    store = OptionSnapshotStore(tmp_path)
    store.save(chain(at=NOW, identity="old"))
    store.save(chain(at=NOW + timedelta(days=1), identity="new"))
    snapshots = store.snapshots

    def unexpected_history(*args):
        raise AssertionError("latest eligible complete pointer scanned all history")

    monkeypatch.setattr(store, "snapshots", unexpected_history)
    assert store.load("US.AAPL", as_of=NOW + timedelta(days=2)).snapshot_id == "new"
    monkeypatch.setattr(store, "snapshots", snapshots)
    assert store.load("US.AAPL", as_of=NOW).snapshot_id == "old"
