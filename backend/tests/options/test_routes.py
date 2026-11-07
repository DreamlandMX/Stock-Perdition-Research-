from fastapi.testclient import TestClient
import pytest

from stock_prediction.api.app import create_app
from stock_prediction.options.store import OptionSnapshotStore
from backend.tests.options.test_structure import NOW, contract, snapshot


@pytest.mark.parametrize("endpoint_name", ["board", "experiments"])
def test_full_options_reads_reuse_batched_stock_history_instead_of_per_symbol_queries(
    tmp_path,
    endpoint_name,
):
    from types import SimpleNamespace
    from stock_prediction.api.options_router import (
        build_options_router,
        OptionOverviewParameters,
    )
    from stock_prediction.contracts.serving import InstrumentHistory
    from backend.tests.options.test_analytics import NOW, chain

    stock = SimpleNamespace(
        snapshot_id="stock", as_of=NOW, model_dump_json=lambda: "stock"
    )
    calls = []

    class Repository:
        def resolve_snapshot(self, query):
            return stock

        def history(self, *args):
            raise AssertionError("board should reuse batched histories")

        def history_batch(self, snapshot, symbols, query):
            calls.append((snapshot.snapshot_id, symbols, query.adjustment))
            return SimpleNamespace(
                items=tuple(
                    SimpleNamespace(
                        symbol=symbol,
                        history=InstrumentHistory(
                            symbol=symbol,
                            timeframe="1d",
                            adjustment=query.adjustment,
                            bars=(),
                            events=(),
                            gaps=(),
                        ),
                    )
                    for symbol in symbols
                )
            )

    store = OptionSnapshotStore(tmp_path)
    store.save(chain())
    router = build_options_router(
        store=store,
        clock=lambda: NOW,
        request_id_factory=lambda: "batch",
        repository=Repository(),
    )
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == f"/api/v1/options/{endpoint_name}"
    )
    if endpoint_name == "board":
        response = endpoint(OptionOverviewParameters())
        assert any(
            item.symbol == "US.AAPL" and item.snapshot_id for item in response.items
        )
    else:
        assert endpoint().resolved_snapshot_id == "stock"
    assert 0 < len(calls) <= 8
    assert all(
        len(symbols) <= 64 and identity == "stock" for identity, symbols, _ in calls
    )


def test_structure_map_reads_raw_turnover_without_rewriting_snapshot(tmp_path):
    from hashlib import sha256

    store = OptionSnapshotStore(tmp_path)
    row = contract().model_copy(update={"volume": 10})
    path = store.save(
        snapshot((row,)),
        raw={
            "quotes-0": [
                {
                    "code": row.contract_id,
                    "turnover": 1000,
                    "high_price": 1.1,
                    "low_price": 0.9,
                }
            ]
        },
    )
    before = sha256(path.read_bytes()).hexdigest()
    client = TestClient(create_app(options_store=store, clock=lambda: NOW))
    url = "/api/v1/instruments/US.AAPL/options/structure-map"
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["cells"][0]["call"]["turnover"] == 1000
    assert data["snapshot"]["contracts"][0]["turnover"] == 1000
    assert sha256(path.read_bytes()).hexdigest() == before
    assert client.get(url, params={"snapshot_id": "missing"}).status_code == 404
    assert client.get(url, params={"dte_min": 10, "dte_max": 5}).status_code == 422
    assert (
        client.get(url, params={"as_of": "2026-10-01T20:00:00Z"}).json()["data"][
            "snapshot"
        ]
        is None
    )


def test_premium_heatmap_api_returns_dollars_and_keeps_existing_oi(tmp_path):
    row = contract(bid=1.0, ask=3.0).model_copy(update={"volume": 10})
    store = OptionSnapshotStore(tmp_path)
    store.save(snapshot((row,)))
    client = TestClient(create_app(options_store=store, clock=lambda: NOW))
    response = client.get(
        "/api/v1/instruments/US.AAPL/options/heatmap", params={"metric": "premium"}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["unit"] == "USD"
    assert data["aggregation"] == "sum"
    assert data["total"] == data["cells"][0]["value"] == 2000
    assert (
        client.get(
            "/api/v1/instruments/US.AAPL/options/heatmap", params={"metric": "oi"}
        ).json()["data"]["total"]
        == 1000
    )


def test_read_routes_resolve_fixed_option_snapshot(tmp_path):
    store = OptionSnapshotStore(tmp_path)
    store.save(snapshot())
    client = TestClient(create_app(options_store=store, clock=lambda: NOW))
    result = client.get(
        "/api/v1/instruments/US.AAPL/options/heatmap",
        params={"snapshot_id": "sample-1", "metric": "gamma"},
    )

    assert result.status_code == 200
    assert result.json()["data"]["total"] == 200000
    assert result.json()["data"]["prediction_status"] == "research_unpublished"
    assert (
        client.get(
            "/api/v1/instruments/US.AAPL/options/heatmap",
            params={"snapshot_id": "missing"},
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/v1/instruments/US.AAPL/options/heatmap", params={"dte_min": 91}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/instruments/US.AAPL/options/heatmap",
            params={"dte_min": 20, "dte_max": 10},
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/instruments/US.AAPL/options/heatmap",
            params={"as_of": "2026-10-01T20:00:00Z"},
        ).json()["data"]["snapshot"]
        is None
    )


def test_header_returns_metadata_without_building_the_chain(tmp_path, monkeypatch):
    import stock_prediction.api.options_router as routes

    store = OptionSnapshotStore(tmp_path)
    store.save(snapshot())

    def reject_chain(*args, **kwargs):
        raise AssertionError("header must not build a chain")

    monkeypatch.setattr(routes, "build_option_heatmap", reject_chain)
    client = TestClient(create_app(options_store=store, clock=lambda: NOW))
    result = client.get("/api/v1/instruments/US.AAPL/options/header")
    assert result.status_code == 200
    body = result.json()
    assert body["data_status"] == "available"
    assert body["resolved_snapshot_id"] == "sample-1"
    assert body["data"]["contract_count"] == 1
    assert body["data"]["complete"] is True
    assert set(body["data"]) == {
        "symbol",
        "snapshot_id",
        "market_as_of",
        "captured_at",
        "complete",
        "contract_count",
        "reasons",
    }
    assert (
        client.get("/api/v1/instruments/US.NOT_IN_UNIVERSE/options/header").json()[
            "data_status"
        ]
        == "unsupported"
    )
    assert (
        client.get(
            "/api/v1/instruments/US.AAPL/options/header",
            params={"as_of": "2026-10-01T20:00:00Z"},
        ).json()["data_status"]
        == "unavailable"
    )
    assert (
        client.get(
            "/api/v1/instruments/US.AAPL/options/header",
            params={"snapshot_id": "missing"},
        ).status_code
        == 404
    )


def test_board_derived_cache_singleflights_and_detects_replaced_inputs(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    import stock_prediction.api.options_router as routes
    from backend.tests.options.test_analytics import chain

    store = OptionSnapshotStore(tmp_path)
    saved = chain()
    path = store.save(saved)
    build = routes.build_option_board_item
    calls = []
    now = [NOW]

    def counted(*args, **kwargs):
        calls.append(args[0].underlying_price)
        return build(*args, **kwargs)

    monkeypatch.setattr(routes, "build_option_board_item", counted)
    router = routes.build_options_router(
        store=store, clock=lambda: now[0], request_id_factory=lambda: str(now[0])
    )
    endpoint = next(
        r.endpoint for r in router.routes if r.path == "/api/v1/options/board"
    )
    parameters = routes.OptionOverviewParameters(symbol="US.AAPL", as_of=NOW)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = tuple(pool.map(lambda _: endpoint(parameters), range(4)))
    assert responses[0].items[0].spot == 100
    assert len(calls) == 1
    now[0] += timedelta(minutes=1)
    repeat = endpoint(parameters)
    assert repeat.generated_at == now[0]
    assert repeat.request_id != responses[0].request_id
    assert len(calls) == 1
    path.write_text(
        saved.model_copy(update={"underlying_price": 101}).model_dump_json(),
        encoding="utf-8",
    )
    assert endpoint(parameters).items[0].spot == 101
    assert len(calls) == 2


def test_overview_reuses_derived_items_but_keeps_cutoffs_separate(
    tmp_path, monkeypatch
):
    from datetime import timedelta
    import stock_prediction.api.options_router as routes
    from backend.tests.options.test_analytics import chain

    store = OptionSnapshotStore(tmp_path)
    store.save(chain())
    build = routes.build_option_overview_item
    calls = []

    def counted(*args, **kwargs):
        calls.append(kwargs["at"])
        return build(*args, **kwargs)

    monkeypatch.setattr(routes, "build_option_overview_item", counted)
    router = routes.build_options_router(
        store=store, clock=lambda: NOW, request_id_factory=lambda: "test"
    )
    endpoint = next(
        r.endpoint for r in router.routes if r.path == "/api/v1/options/overview"
    )
    parameters = routes.OptionOverviewParameters(symbol="US.AAPL", as_of=NOW)
    first, repeat = endpoint(parameters), endpoint(parameters)
    assert first.items == repeat.items
    assert len(calls) == 1
    later = endpoint(
        parameters.model_copy(update={"as_of": NOW + timedelta(minutes=1)})
    )
    assert later.items[0].analytics.as_of == NOW + timedelta(minutes=1)
    assert len(calls) == 2
    path = tmp_path / "US.AAPL" / "current.json"
    path.write_text(
        chain()
        .model_copy(update={"captured_at": NOW + timedelta(hours=1)})
        .model_dump_json(),
        encoding="utf-8",
    )
    assert endpoint(parameters).items[0].data_status == "unavailable"
    assert endpoint(parameters).items[0].data_status == "unavailable"
    assert len(calls) == 4


def test_no_data_and_unsupported_are_explicit_without_io(tmp_path):
    client = TestClient(create_app(options_store=OptionSnapshotStore(tmp_path)))
    result = client.get("/api/v1/instruments/US.AAPL/options/summary")
    assert result.status_code == 200
    assert result.json()["data"]["status"] == "unavailable"
    assert not tmp_path.exists() or not list(tmp_path.iterdir())
    assert (
        client.get("/api/v1/instruments/US.NOT_IN_UNIVERSE/options/heatmap").json()[
            "data"
        ]["status"]
        == "unsupported"
    )


def test_analytics_reads_only_saved_snapshot_and_preserves_explicit_missing_fields(
    tmp_path,
):
    store = OptionSnapshotStore(tmp_path)
    store.save(snapshot())
    client = TestClient(create_app(options_store=store, clock=lambda: NOW))
    result = client.get(
        "/api/v1/instruments/US.AAPL/options/analytics",
        params={"snapshot_id": "sample-1"},
    )
    assert result.status_code == 200
    assert result.json()["data"]["snapshot_id"] == "sample-1"
    assert result.json()["data"]["feature_version"] == "option-numeric-v2"
    assert (
        client.get("/api/v1/instruments/US.NOT_IN_UNIVERSE/options/analytics").json()[
            "data"
        ]
        is None
    )


def test_forecast_exposes_actual_stock_baseline_version_without_candidate_promotion(
    tmp_path,
):
    from backend.tests.api.test_research_routes import make_research_app

    root = tmp_path / "research"
    client = TestClient(
        make_research_app(root, options_store=OptionSnapshotStore(tmp_path / "options"))
    )
    baseline = client.get("/api/v1/instruments/US.AAPL/forecast").json()
    result = client.get("/api/v1/instruments/US.AAPL/options/forecast")
    assert result.status_code == 200
    data = result.json()["data"]
    assert data["candidate"]["promoted"] is False
    assert data["model_version"] == baseline["data"]["bundle"]["model_version"]
    assert data["status"] == "baseline_fallback"
    assert [h["horizon_days"] for h in data["horizons"]] == [1, 5, 20]
