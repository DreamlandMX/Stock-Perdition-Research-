from datetime import UTC, datetime

from fastapi.testclient import TestClient

from stock_prediction.api.app import create_app
from stock_prediction.contracts.options import OPTION_SYMBOLS
from stock_prediction.options.refresh import OptionRefreshStore
from stock_prediction.options.store import OptionSnapshotStore
from stock_prediction.universe.active import ACTIVE_UNIVERSE

NOW = datetime(2026, 10, 30, 20, tzinfo=UTC)


def test_supported_scope_is_governed_universe_and_automatic_scope_stays_ten():
    from stock_prediction.contracts.options import AUTOMATIC_OPTION_SYMBOLS

    assert set(OPTION_SYMBOLS) == set(ACTIVE_UNIVERSE)
    assert len(OPTION_SYMBOLS) == len(set(OPTION_SYMBOLS))
    assert AUTOMATIC_OPTION_SYMBOLS == (
        "US.SPY",
        "US.QQQ",
        "US.AAPL",
        "US.MSFT",
        "US.AMZN",
        "US.GOOGL",
        "US.META",
        "US.NVDA",
        "US.TSLA",
        "US.ORCL",
    )
    assert set(AUTOMATIC_OPTION_SYMBOLS) <= set(OPTION_SYMBOLS)


def test_scheduled_collection_explicitly_passes_only_the_original_ten(
    tmp_path, monkeypatch
):
    from contextlib import nullcontext
    from types import SimpleNamespace
    import stock_prediction.options.scheduled_collection as module
    from stock_prediction.contracts.options import AUTOMATIC_OPTION_SYMBOLS
    from stock_prediction.live.config import LivePaths
    from stock_prediction.opend.ports import SdkCallResult

    captured = {}

    def collect(store, **kwargs):
        captured.update(kwargs)
        return "collected"

    monkeypatch.setattr(module, "run_option_collection", collect)
    context = SimpleNamespace(
        request_trading_days=lambda **kwargs: SdkCallResult.success(
            [
                {"time": "2026-10-30", "trade_date_type": "WHOLE"},
            ]
        )
    )
    result = module.run_scheduled_option_collection(
        LivePaths.from_workspace(tmp_path),
        context_factory=lambda: nullcontext(context),
        clock=lambda: NOW.replace(hour=19, minute=40),
    )
    assert result == "collected"
    assert captured["symbols"] == AUTOMATIC_OPTION_SYMBOLS


def test_expanded_symbol_can_queue_and_save_and_all_is_deduplicated(tmp_path):
    from backend.tests.options.test_analytics import chain

    store = OptionSnapshotStore(tmp_path)
    jobs = OptionRefreshStore(store.root)
    jobs.heartbeat(NOW)
    client = TestClient(create_app(options_store=store, clock=lambda: NOW))
    assert (
        client.post("/api/v1/options/refresh", json={"symbol": "US.TSM"}).status_code
        == 202
    )
    snapshot = chain(at=NOW, identity="expanded-tsm")
    snapshot = snapshot.model_copy(
        update={
            "symbol": "US.TSM",
            "contracts": tuple(
                c.model_copy(update={"underlying": "US.TSM"})
                for c in snapshot.contracts
            ),
        }
    )
    store.save(snapshot)
    assert store.load("US.TSM").snapshot_id == "expanded-tsm"
    assert (
        client.get("/api/v1/options/board", params={"symbol": "US.TSM"}).json()[
            "items"
        ][0]["snapshot_id"]
        == "expanded-tsm"
    )
    assert (
        client.post(
            "/api/v1/options/refresh", json={"symbol": "US.NOT_IN_UNIVERSE"}
        ).status_code
        == 422
    )
    jobs.save(jobs.latest().model_copy(update={"status": "completed"}))
    operation = jobs.submit(None, NOW)
    assert {i.symbol for i in operation.items} == set(ACTIVE_UNIVERSE)
    assert jobs.submit("US.TSM", NOW).operation_id == operation.operation_id


def test_default_board_includes_every_symbol_without_fabricating_data(tmp_path):
    client = TestClient(
        create_app(options_store=OptionSnapshotStore(tmp_path), clock=lambda: NOW)
    )
    for endpoint in ("board", "overview"):
        result = client.get("/api/v1/options/" + endpoint)
        assert result.status_code == 200
        items = result.json()["items"]
        assert {item["symbol"] for item in items} == set(ACTIVE_UNIVERSE)
        assert all(item["snapshot_id"] is None for item in items)
        assert all(item["data_status"] == "unavailable" for item in items)


def test_failed_attempt_reasons_are_visible_without_leaking_future_failures(tmp_path):
    from datetime import timedelta
    from stock_prediction.contracts.options import OptionChainSnapshot

    store = OptionSnapshotStore(tmp_path)
    for index, reason in enumerate(("no_option_contracts", "permission_denied")):
        at = NOW + timedelta(hours=index)
        store.save(
            OptionChainSnapshot(
                symbol="US.TSM",
                snapshot_id=f"failed-{index}",
                raw_batch_id=f"failed-{index}",
                captured_at=at,
                source_available_at=at,
                sdk_version="test",
                errors=(reason,),
            )
        )
    client = TestClient(
        create_app(options_store=store, clock=lambda: NOW + timedelta(hours=2))
    )
    for endpoint in ("board", "overview"):
        latest = client.get(
            "/api/v1/options/" + endpoint, params={"symbol": "US.TSM"}
        ).json()["items"][0]
        assert "permission_denied" in latest["reasons"]
        old = client.get(
            "/api/v1/options/" + endpoint,
            params={"symbol": "US.TSM", "as_of": NOW.isoformat()},
        ).json()["items"][0]
        assert "no_option_contracts" in old["reasons"]
        assert "permission_denied" not in old["reasons"]
    pulse = client.get(
        "/api/v1/instruments/US.TSM/options/pulse", params={"mode": "latest"}
    ).json()
    assert "permission_denied" in pulse["reasons"]
