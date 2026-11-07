from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from stock_prediction.api.app import create_app
from stock_prediction.contracts.options import OPTION_SYMBOLS
from stock_prediction.live.config import LivePaths
from stock_prediction.options.store import OptionSnapshotStore

NOW = datetime(2026, 10, 15, 3, 50, tzinfo=UTC)


def test_refresh_is_explicit_durable_and_deduplicated(tmp_path):
    from stock_prediction.options.refresh import OptionRefreshStore

    options = OptionSnapshotStore(tmp_path / "var/options")
    jobs = OptionRefreshStore(options.root)
    jobs.heartbeat(NOW)
    client = TestClient(create_app(options_store=options, clock=lambda: NOW))
    base = "/api/v1/options/refresh"
    assert client.get(base + "/latest").json() is None
    assert client.get(base + "/worker").json()["state"] == "ready"
    first = client.post(base, json={"symbol": "US.ORCL"})
    assert first.status_code == 202
    identity = first.json()["operation_id"]
    assert (
        client.post(base, json={"symbol": "US.ORCL"}).json()["operation_id"] == identity
    )
    assert client.post(base, json={}).status_code == 409
    assert OptionRefreshStore(options.root).get(identity).items[0].symbol == "US.ORCL"
    assert client.get(base + "/" + identity).json()["status"] == "queued"
    assert client.post(base, json={"symbol": "US.NOT_IN_UNIVERSE"}).status_code == 422
    assert client.post(base, json={"symbol": ""}).status_code == 422
    assert (
        client.post(
            base, json={}, headers={"origin": "https://other.example"}
        ).status_code
        == 403
    )


def test_all_continues_after_failure_and_never_requests_stock_bars(
    tmp_path, monkeypatch
):
    import stock_prediction.options.refresh as module
    from backend.tests.options.test_analytics import chain

    options = OptionSnapshotStore(tmp_path / "var/options")
    jobs = module.OptionRefreshStore(options.root)
    operation = jobs.submit(None, NOW)
    calls = []
    limiters = []

    def collect(store, **kwargs):
        symbol = kwargs["symbols"][0]
        calls.append(symbol)
        limiters.append(kwargs["limiter"])
        assert kwargs["intraday"] is True
        assert kwargs["refresh_monitor"] is (symbol == OPTION_SYMBOLS[-1])
        if symbol == "US.QQQ":
            return SimpleNamespace(status="failed", reason="permission_denied")
        snapshot = chain(at=NOW, identity="manual-" + symbol.replace(".", "-"))
        store.save(
            snapshot.model_copy(
                update={
                    "symbol": symbol,
                    "contracts": tuple(
                        r.model_copy(update={"underlying": symbol})
                        for r in snapshot.contracts
                    ),
                }
            )
        )
        return SimpleNamespace(status="completed", reason=None)

    monkeypatch.setattr(module, "run_option_collection", collect)
    paths = LivePaths.from_workspace(tmp_path)
    for _ in OPTION_SYMBOLS:
        module.run_option_refresh_tick(paths, clock=lambda: NOW)
    final = jobs.get(operation.operation_id)
    assert calls == list(OPTION_SYMBOLS)
    assert len({id(limiter) for limiter in limiters}) == 1
    assert final.status == "partial"
    assert sum(i.status == "completed" for i in final.items) == len(OPTION_SYMBOLS) - 1
    assert final.items[1].reason == "permission_denied"
    assert not paths.ingestion.exists()
    module.run_option_refresh_tick(paths, clock=lambda: NOW)
    assert len(calls) == len(OPTION_SYMBOLS)


def test_busy_writer_waits_and_interrupted_worker_does_not_repeat_collection(tmp_path):
    from stock_prediction.options.refresh import (
        OptionRefreshStore,
        run_option_refresh_tick,
    )
    from stock_prediction.live.locking import LiveWriterLease

    paths = LivePaths.from_workspace(tmp_path)
    jobs = OptionRefreshStore(tmp_path / "var/options")
    operation = jobs.submit("US.ORCL", NOW)
    with LiveWriterLease(paths, task_id="test", timeout=0):
        run_option_refresh_tick(paths, clock=lambda: NOW)
        assert jobs.latest().status == "queued"
    jobs.save(
        operation.model_copy(
            update={
                "status": "running",
                "items": (operation.items[0].model_copy(update={"status": "running"}),),
            }
        )
    )
    run_option_refresh_tick(paths, clock=lambda: NOW)
    assert jobs.latest().status == "failed"
    assert jobs.latest().items[0].reason == "worker_interrupted"


def test_failed_collection_preserves_success_and_offline_worker_rejects_submit(
    tmp_path, monkeypatch
):
    import stock_prediction.options.refresh as module
    from backend.tests.options.test_analytics import chain

    options = OptionSnapshotStore(tmp_path / "var/options")
    options.save(chain(at=NOW, identity="old-success"))
    jobs = module.OptionRefreshStore(options.root)
    client = TestClient(create_app(options_store=options, clock=lambda: NOW))
    assert client.post("/api/v1/options/refresh", json={}).status_code == 503
    jobs.submit("US.AAPL", NOW)
    monkeypatch.setattr(
        module,
        "run_option_collection",
        lambda *args, **kwargs: SimpleNamespace(
            status="failed", reason="connection_failed"
        ),
    )
    module.run_option_refresh_tick(
        LivePaths.from_workspace(tmp_path), clock=lambda: NOW
    )
    assert jobs.latest().status == "failed"
    assert options.load("US.AAPL").snapshot_id == "old-success"


def test_latest_pulse_is_independent_of_stock_cutoff_and_preserves_history(tmp_path):
    from backend.tests.options.test_analytics import chain
    from datetime import timedelta

    options = OptionSnapshotStore(tmp_path)
    options.save(chain(at=NOW - timedelta(days=1), identity="old"))
    options.save(chain(at=NOW, identity="new"))

    class Repository:
        def resolve_snapshot(self, query):
            return SimpleNamespace(
                snapshot_id="stock-old", as_of=NOW - timedelta(days=1)
            )

        def history(self, *args):
            raise RuntimeError("stock bars unavailable")

    client = TestClient(
        create_app(
            options_store=options, research_repository=Repository(), clock=lambda: NOW
        )
    )
    url = "/api/v1/instruments/US.AAPL/options/pulse"
    latest = client.get(url, params={"mode": "latest"})
    assert latest.status_code == 200
    assert latest.json()["snapshot_id"] == "new"
    assert latest.json()["timeframe"] == "1d"
    assert latest.json()["stock_batch_id"] == "stock-old"
    assert latest.json()["bars"] == []
    old = client.get(
        url, params={"mode": "daily", "as_of": (NOW - timedelta(days=1)).isoformat()}
    )
    assert old.json()["snapshot_id"] == "old"
