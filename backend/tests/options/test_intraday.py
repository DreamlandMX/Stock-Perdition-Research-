from datetime import UTC, datetime, timedelta

import pytest

from stock_prediction.options.intraday import (
    OptionPulseStore,
    intraday_cone,
    normalize_bars,
    verified_schedule,
)


NOW = datetime(2026, 10, 13, 15, tzinfo=UTC)


def test_adapter_accepts_successful_subscription_without_payload():
    from stock_prediction.opend.futu_adapter import FutuQuoteContext

    class Raw:
        def subscribe(self, *args, **kwargs):
            return (0, None)

        def get_cur_kline(self, *args, **kwargs):
            return (0, "bars")

        def close(self):
            pass

    with FutuQuoteContext(quote_context_factory=lambda **kwargs: Raw()) as context:
        assert context.get_current_5m("US.META").value == "bars"


def test_pulse_adapter_connects_async_with_a_bounded_query_wait():
    from stock_prediction.opend.futu_adapter import FutuQuoteContext

    arguments = {}

    class Raw:
        def set_sync_query_connect_timeout(self, timeout):
            arguments["timeout"] = timeout

        def close(self):
            pass

    def factory(**kwargs):
        arguments.update(kwargs)
        return Raw()

    with FutuQuoteContext(quote_context_factory=factory, connect_timeout_seconds=5):
        assert arguments["is_async_connect"] is True
        assert arguments["timeout"] == 5


def test_leases_are_symbol_scoped_expire_and_share_collection(tmp_path):
    store = OptionPulseStore(tmp_path)
    first = store.acquire("US.META", NOW)
    second = store.acquire("US.META", NOW)
    assert store.active_symbols(NOW) == ("US.META",)
    assert store.renew("US.AAPL", first.session_id, NOW) is None
    store.release("US.META", first.session_id)
    assert store.active_symbols(NOW + timedelta(seconds=89)) == ("US.META",)
    assert store.active_symbols(NOW + timedelta(seconds=90)) == ()
    assert (
        store.renew("US.META", second.session_id, NOW + timedelta(seconds=91)) is None
    )
    with pytest.raises(ValueError):
        store.acquire("../../escape", NOW)


@pytest.mark.parametrize(
    "day,kind,close", [("2026-10-13", "WHOLE", 20), ("2026-11-27", "MORNING", 18)]
)
def test_verified_calendar_handles_dst_and_half_days(day, kind, close):
    schedule = verified_schedule(day, [{"time": day, "trade_date_type": kind}])
    assert schedule.close_at.hour == close
    cone = intraday_cone(100, 0.3, schedule.open_at, schedule)
    assert cone[-1].at == schedule.close_at
    assert cone[-1].upper80 > cone[-1].upper68 > 100
    later = intraday_cone(100, 0.3, schedule.close_at - timedelta(minutes=5), schedule)
    assert later[-1].upper80 < cone[-1].upper80
    assert not intraday_cone(100, 0.3, schedule.close_at, schedule)
    assert not intraday_cone(100, None, schedule.open_at, schedule)
    with pytest.raises(ValueError):
        verified_schedule(day, [])


def test_bars_exclude_future_other_sessions_and_label_forming():
    schedule = verified_schedule(
        "2026-10-13", [{"time": "2026-10-13", "trade_date_type": "WHOLE"}]
    )
    rows = [
        {"time_key": at, "open": 100, "close": 101, "low": 99, "high": 102}
        for at in [
            "2026-10-12 10:00:00",
            "2026-10-13 10:55:00",
            "2026-10-13 11:05:00",
            "2026-10-13 11:10:00",
        ]
    ]
    bars = normalize_bars(rows, NOW + timedelta(minutes=2), schedule)
    assert len(bars) == 2
    assert not bars[0].forming and bars[1].forming
    rows[1]["high"] = 98
    with pytest.raises(ValueError):
        normalize_bars(rows, NOW, schedule)


def test_pulse_get_never_creates_lease_and_post_renew_release(tmp_path):
    from fastapi.testclient import TestClient
    from stock_prediction.api.app import create_app
    from stock_prediction.options.store import OptionSnapshotStore

    options = OptionSnapshotStore(tmp_path)
    client = TestClient(create_app(options_store=options, clock=lambda: NOW))
    url = "/api/v1/instruments/US.META/options/pulse"
    assert client.get(url, params={"mode": "live"}).status_code == 200
    assert OptionPulseStore(tmp_path / "_pulse").active_symbols(NOW) == ()
    response = client.post(url + "/sessions")
    assert response.status_code == 201
    session_id = response.json()["session_id"]
    assert client.put(url + "/sessions/" + session_id).status_code == 200
    assert (
        client.put(url.replace("META", "AAPL") + "/sessions/" + session_id).status_code
        == 404
    )
    assert client.delete(url + "/sessions/" + session_id).status_code == 204
    assert client.post(url.replace("META", "NOT_IN_UNIVERSE") + "/sessions").status_code == 422


def test_turnover_missing_is_not_estimated_and_fixed_time_does_not_read_future(
    tmp_path,
):
    from fastapi.testclient import TestClient
    from stock_prediction.api.app import create_app
    from stock_prediction.options.store import OptionSnapshotStore
    from backend.tests.options.test_analytics import chain

    store = OptionSnapshotStore(tmp_path)
    original = chain(at=NOW, identity="before")
    rows = [
        r.model_copy(update={"turnover": 1000, "session_low": 0.9, "session_high": 1.2})
        for r in original.contracts
    ]
    rows[0] = rows[0].model_copy(update={"turnover": None})
    store.save(original.model_copy(update={"contracts": tuple(rows)}))
    store.save(chain(at=NOW + timedelta(minutes=1), identity="future"))
    client = TestClient(
        create_app(options_store=store, clock=lambda: NOW + timedelta(minutes=2))
    )
    result = client.get(
        "/api/v1/instruments/US.AAPL/options/pulse", params={"as_of": NOW.isoformat()}
    ).json()
    assert result["snapshot_id"] == "before"
    assert result["total_turnover"] == 7000
    assert result["missing_turnover_contracts"] == 1
    assert result["data_status"] == "partial"
    assert (
        client.post(
            "/api/v1/instruments/US.AAPL/options/pulse/sessions",
            headers={"origin": "https://untrusted.example"},
        ).status_code
        == 403
    )


def test_worker_deduplicates_rounds_preserves_success_on_failure_and_stops(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import pandas as pd
    import stock_prediction.options.intraday as module
    from stock_prediction.live.config import LivePaths
    from stock_prediction.opend.ports import SdkCallResult
    from stock_prediction.options.store import OptionSnapshotStore
    from backend.tests.options.test_analytics import chain

    at = [NOW]
    calls = []
    failed = [False]
    options = OptionSnapshotStore(tmp_path / "var" / "options")
    pulse = OptionPulseStore(options.root / "_pulse")
    lease = pulse.acquire("US.AAPL", NOW)
    pulse.acquire("US.AAPL", NOW)

    class Context:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request_trading_days(self, **kwargs):
            return SdkCallResult.success(
                [{"time": "2026-10-13", "trade_date_type": "WHOLE"}]
            )

        def get_current_5m(self, symbol):
            return SdkCallResult.success(
                pd.DataFrame(
                    [
                        {
                            "time_key": "2026-10-13 11:05:00",
                            "open": 100,
                            "close": 101,
                            "low": 99,
                            "high": 102,
                        }
                    ]
                )
            )

    def collect(store, **kwargs):
        calls.append(kwargs["symbols"])
        if failed[0]:
            return SimpleNamespace(status="failed", reason="rate_limited")
        store.save(chain(at=at[0], identity=f"round-{len(calls)}"))
        return SimpleNamespace(status="completed")

    monkeypatch.setattr(module, "run_option_collection", collect)
    paths = LivePaths.from_workspace(tmp_path)

    def tick():
        module.run_intraday_tick(paths, context_factory=Context, clock=lambda: at[0])

    tick()
    assert len(calls) == 1
    assert pulse.latest_batch("US.AAPL").snapshot_id == "round-1"
    tick()
    assert len(calls) == 1
    at[0] += timedelta(seconds=60)
    pulse.renew("US.AAPL", lease.session_id, at[0])
    failed[0] = True
    tick()
    assert pulse.state("US.AAPL")["reason"] == "rate_limited"
    assert pulse.latest_batch("US.AAPL").snapshot_id == "round-1"
    at[0] += timedelta(seconds=90)
    tick()
    assert len(calls) == 2


def test_collector_deadline_fails_before_another_provider_request(tmp_path):
    from stock_prediction.options.collector import OptionCollector
    from stock_prediction.options.store import OptionSnapshotStore

    collector = OptionCollector(OptionSnapshotStore(tmp_path), timeout_seconds=0)
    with pytest.raises(TimeoutError, match="collection_timeout"):
        collector._call("snapshot", lambda: pytest.fail("must not call provider"))


def test_live_market_cutoff_accepts_later_quotes_and_preserves_stock_time():
    from backend.tests.options.test_analytics import chain
    from stock_prediction.options.intraday import pulse_from_snapshot

    later = NOW + timedelta(seconds=20)
    data = chain(at=later).model_copy(
        update={"underlying_at": NOW, "market_as_of": None}
    )
    before = pulse_from_snapshot(data, later, mode="live")
    assert before.iv30 is None
    after = pulse_from_snapshot(
        data.model_copy(update={"market_as_of": later}), later, mode="live"
    )
    assert after.iv30 == pytest.approx(0.2)
    assert after.put_call_volume == 1
    assert after.market_at == NOW
