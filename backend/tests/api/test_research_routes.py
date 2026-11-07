"""Integration tests for the frozen read-only research API surface."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pyarrow as pa
import pytest

from backend.tests.fixtures.research_snapshot_factory import (
    GENERATED_AT as SNAPSHOT_GENERATED_AT,
    SYMBOLS,
    make_snapshot_tables,
    resolved_snapshot,
    write_valid_snapshot,
)
from stock_prediction.api.app import create_app
from stock_prediction.options.store import OptionSnapshotStore
from stock_prediction.contracts.capability import (
    CapabilitySnapshot,
    CapabilityStatus,
    DataCapability,
    PointInTimeStatus,
)
from stock_prediction.contracts.health import (
    Completeness,
    ComponentHealth,
    Connectivity,
    Freshness,
    Readiness,
)
from stock_prediction.contracts.m7_status import M7LifecycleStatus
from stock_prediction.serving.repository import DuckDbResearchRepository
from stock_prediction.serving.materialize import materialize_research_snapshot


GENERATED_AT = datetime(2026, 7, 10, 20, 3, tzinfo=UTC)
EXPECTED_RESEARCH_GET_PATHS = {
    "/api/v1/health",
    "/api/v1/overview",
    "/api/v1/snapshot-context",
    "/api/v1/history/batch",
    "/api/v1/instruments",
    "/api/v1/instruments/{symbol}",
    "/api/v1/screen",
    "/api/v1/instruments/{symbol}/forecast",
    "/api/v1/instruments/{symbol}/history",
    "/api/v1/instruments/{symbol}/options/heatmap",
    "/api/v1/instruments/{symbol}/options/summary",
    "/api/v1/instruments/{symbol}/options/header",
    "/api/v1/instruments/{symbol}/options/analytics",
    "/api/v1/instruments/{symbol}/options/forecast",
    "/api/v1/instruments/{symbol}/options/monitor",
    "/api/v1/instruments/{symbol}/options/pulse",
    "/api/v1/instruments/{symbol}/options/structure-map",
    "/api/v1/options/board",
    "/api/v1/options/overview",
    "/api/v1/options/collection",
    "/api/v1/options/experiments",
    "/api/v1/backtests",
    "/api/v1/models",
    "/api/v1/data-health",
    "/api/v1/data-capabilities",
    "/api/v1/universes",
}
EXPECTED_OPERATION_GET_PATHS = {
    "/api/v1/options/refresh/worker",
    "/api/v1/options/refresh/latest",
    "/api/v1/options/refresh/{operation_id}",
    "/api/v1/operations/m7-status",
    "/api/v1/operations/eod-update/latest",
    "/api/v1/operations/eod-update/worker",
    "/api/v1/operations/model-training/latest",
    "/api/v1/operations/model-training/{operation_id}/partial-result",
    "/api/v1/operations/model-training/readiness",
    "/api/v1/operations/model-training/worker",
    "/api/v1/operations/model-promotion/latest",
    "/api/v1/operations/model-promotion/worker",
    "/api/v1/operations/{operation_id}",
}
EXPECTED_POST_PATHS = {
    "/api/v1/options/refresh",
    "/api/v1/instruments/{symbol}/options/pulse/sessions",
    "/api/v1/operations/eod-update",
    "/api/v1/operations/model-training",
    "/api/v1/operations/model-promotion",
    "/api/v1/operations/{operation_id}/cancel",
}
EXPECTED_GET_PATHS = EXPECTED_RESEARCH_GET_PATHS | EXPECTED_OPERATION_GET_PATHS
RESEARCH_PATHS = (
    "/api/v1/overview",
    "/api/v1/instruments",
    "/api/v1/screen",
    "/api/v1/instruments/US.AAPL/forecast?include=evidence,similar_cases",
    "/api/v1/instruments/US.AAPL/history",
    "/api/v1/backtests",
    "/api/v1/models",
    "/api/v1/data-health",
    "/api/v1/universes",
)


def test_lightweight_context_and_batch_match_fixed_snapshot_reads(
    tmp_path: Path,
) -> None:
    client = make_research_client(tmp_path)
    context = client.get("/api/v1/snapshot-context", params={"symbols": "US.AAPL"})
    assert context.status_code == 200, context.text
    body = context.json()
    assert body["data"]["symbol_count"] == len(SYMBOLS)
    assert body["data"]["instruments"][0]["symbol"] == "US.AAPL"
    identity = body["resolved_snapshot"]["snapshot_id"]
    batch = client.get(
        "/api/v1/history/batch",
        params=[
            ("snapshot_id", identity),
            ("symbols", "US.AAPL"),
            ("symbols", "US.MISSING"),
        ],
    )
    assert batch.status_code == 200, batch.text
    single = client.get(
        "/api/v1/instruments/US.AAPL/history", params={"snapshot_id": identity}
    )
    assert batch.json()["resolved_snapshot"] == single.json()["resolved_snapshot"]
    assert batch.json()["data"]["items"][0]["history"] == single.json()["data"]
    assert batch.json()["data"]["items"][1]["error_code"] == "symbol_not_in_universe"
    assert client.get("/api/v1/history/batch").status_code == 422
    assert (
        client.get(
            "/api/v1/history/batch", params=[("symbols", "US.AAPL")] * 65
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/snapshot-context", params={"snapshot_id": "missing"}
        ).status_code
        == 503
    )


class SnapshotStore:
    """Side-effect-free capability store used by API integration tests."""

    def __init__(self) -> None:
        self.snapshot = CapabilitySnapshot(
            snapshot_id="capability-fixture",
            probed_at=GENERATED_AT,
            opend_version="10.7.3218",
            sdk_version="10.8.6808",
            capabilities=[
                DataCapability(
                    interface="get_global_state",
                    market="GLOBAL",
                    asset_type="system",
                    timeframe=None,
                    status=CapabilityStatus.AVAILABLE,
                    point_in_time_status=PointInTimeStatus.NOT_APPLICABLE,
                    probed_at=GENERATED_AT,
                )
            ],
        )

    def load_latest(self) -> CapabilitySnapshot:
        return self.snapshot


def live_components() -> list[ComponentHealth]:
    return [
        ComponentHealth(
            component="research_snapshot",
            connectivity=Connectivity.CONNECTED,
            freshness=Freshness.CURRENT,
            completeness=Completeness.COMPLETE,
            readiness=Readiness.READY,
        )
    ]


def offline_components() -> list[ComponentHealth]:
    return [
        ComponentHealth(
            component="research_snapshot",
            connectivity=Connectivity.DISCONNECTED,
            freshness=Freshness.UNKNOWN,
            completeness=Completeness.UNKNOWN,
            readiness=Readiness.BLOCKED,
        )
    ]


def make_research_app(
    root: Path,
    *,
    state_provider: Callable[[], list[ComponentHealth]] = live_components,
    clock: Callable[[], datetime] = lambda: GENERATED_AT,
    request_id: str = "request-research",
    m7_status_provider: Callable[[], M7LifecycleStatus] | None = None,
    options_store: OptionSnapshotStore | None = None,
) -> FastAPI:
    write_valid_snapshot(root)
    return create_app(
        options_store=options_store
        if options_store is not None
        else OptionSnapshotStore(root.parent / "options"),
        capability_store=SnapshotStore(),
        research_repository=DuckDbResearchRepository(root),
        state_provider=state_provider,
        clock=clock,
        request_id_factory=lambda: request_id,
        m7_status_provider=m7_status_provider,
    )


def make_research_client(
    root: Path,
    *,
    state_provider: Callable[[], list[ComponentHealth]] = live_components,
    clock: Callable[[], datetime] = lambda: GENERATED_AT,
    request_id: str = "request-research",
) -> TestClient:
    return TestClient(
        make_research_app(
            root,
            state_provider=state_provider,
            clock=clock,
            request_id=request_id,
        ),
        raise_server_exceptions=False,
    )


def make_custom_forecast_client(
    root: Path,
    forecast_rows: list[dict[str, object]],
    *,
    clock: Callable[[], datetime] = lambda: GENERATED_AT,
) -> TestClient:
    snapshot = resolved_snapshot()
    tables = make_snapshot_tables(snapshot)
    tables["forecasts.parquet"] = pa.Table.from_pylist(
        forecast_rows,
        schema=tables["forecasts.parquet"].schema,
    )
    materialize_research_snapshot(
        root,
        snapshot=snapshot,
        generated_at=SNAPSHOT_GENERATED_AT,
        tables=tables,
        champion_exists=True,
    )
    return TestClient(
        create_app(
            capability_store=SnapshotStore(),
            research_repository=DuckDbResearchRepository(root),
            state_provider=live_components,
            clock=clock,
            request_id_factory=lambda: "request-custom-forecast",
        ),
        raise_server_exceptions=False,
    )


def make_all_unavailable_forecast_rows() -> list[dict[str, object]]:
    tables = make_snapshot_tables()
    rows = tables["forecasts.parquet"].to_pylist()
    unavailable = next(row for row in rows if row["availability"] == "unavailable")
    unavailable_payload = json.loads(unavailable["payload_json"])
    forecast_rows: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        payload = dict(unavailable_payload)
        payload["symbol"] = symbol
        forecast_rows.append(
            {
                "symbol": symbol,
                "availability": "unavailable",
                "payload_json": json.dumps(payload),
            }
        )
    return forecast_rows


def tree_fingerprint(root: Path) -> tuple[tuple[str, int, int], ...]:
    if not root.exists():
        return ()
    return tuple(
        sorted(
            (
                path.relative_to(root).as_posix(),
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def test_api_exposes_only_declared_research_and_explicit_operation_routes(tmp_path: Path) -> None:
    schema = make_research_app(tmp_path).openapi()
    get_paths = {path for path, methods in schema["paths"].items() if "get" in methods}
    post_paths = {
        path for path, methods in schema["paths"].items() if "post" in methods
    }
    assert get_paths == EXPECTED_GET_PATHS
    assert post_paths == EXPECTED_POST_PATHS
    session_path = "/api/v1/instruments/{symbol}/options/pulse/sessions/{session_id}"
    assert set(schema["paths"][session_path]) == {"put", "delete"}
    assert all(
        set(methods) <= {"get", "post"}
        for path, methods in schema["paths"].items()
        if path != session_path
    )


def test_backtest_openapi_exposes_every_exact_identity_as_get_only(
    tmp_path: Path,
) -> None:
    operation = make_research_app(tmp_path).openapi()["paths"]["/api/v1/backtests"]
    parameter_names = {
        parameter["name"] for parameter in operation["get"]["parameters"]
    }

    assert {
        "split_version",
        "task",
        "horizon",
        "asset_group",
        "fold",
    } <= parameter_names
    assert set(operation) <= {"get"}


def test_backtest_exact_identity_is_replayable_and_missing_identity_is_404(
    tmp_path: Path,
) -> None:
    client = make_research_client(tmp_path)
    query = (
        "task=forecast_direction"
        "&horizon=5"
        "&asset_group=us_equity"
        "&fold=outer-1"
        "&split_version=split-v1"
    )

    selected = client.get(f"/api/v1/backtests?{query}")
    missing = client.get(
        f"/api/v1/backtests?{query.replace('split-v1', 'split-missing')}"
    )

    assert selected.status_code == 200, selected.text
    assert selected.json()["data"]["split_version"] == "split-v1"
    assert [fold["fold_id"] for fold in selected.json()["data"]["fold_metrics"]] == [
        "outer-1"
    ]
    assert missing.status_code == 404
    assert missing.json() == {
        "request_id": "request-research",
        "generated_at": "2026-07-10T20:03:00Z",
        "data_status": "blocked",
        "error_code": "backtest_not_found",
        "message": "No matching backtest evidence is available",
        "retryable": False,
        "affected_scope": ["backtest"],
    }


@pytest.mark.parametrize("path", RESEARCH_PATHS)
def test_every_research_route_returns_the_common_snapshot_envelope(
    tmp_path: Path, path: str
) -> None:
    response = make_research_client(tmp_path).get(path)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request_id"] == "request-research"
    assert body["generated_at"] == "2026-07-10T20:03:00Z"
    assert body["data_status"] == "ready"
    assert body["serving_status"] == "live"
    assert body["effective_abstain"] is False
    assert body["evaluated_at"] == body["generated_at"]
    assert body["exclusion_reason"] == []
    assert body["resolved_snapshot"] == {
        "snapshot_id": "snapshot-20260710-r1",
        "revision_no": 1,
        "as_of": "2026-07-10T20:00:00Z",
        "dataset_revision": "dataset-r1",
        "feature_set_version": "features-v1",
        "universe_version": "universe-v1",
        "model_version": "model-v1",
        "calibration_version": "calibration-v1",
        "prediction_readiness": "forecast_ready",
        "research_readiness": "full_verified",
    }
    assert "data" in body


@pytest.mark.parametrize("horizon", (1, 5, 20))
def test_forecast_accepts_only_frozen_horizons(tmp_path: Path, horizon: int) -> None:
    response = make_research_client(tmp_path).get(
        f"/api/v1/instruments/US.AAPL/forecast?horizon={horizon}"
    )
    assert response.status_code == 200
    assert response.json()["data"]["selected_horizon"]["horizon"] == horizon


def test_screen_bases_and_default_direction_have_server_query_semantics(
    tmp_path: Path,
) -> None:
    client = make_research_client(tmp_path)

    executable = client.get(
        "/api/v1/screen?prediction_basis=executable&return_basis=executable"
    ).json()["data"]
    forecast = client.get(
        "/api/v1/screen?prediction_basis=forecast&return_basis=executable"
    ).json()["data"]
    capital_gain = client.get(
        "/api/v1/screen?prediction_basis=executable&return_basis=capital_gain"
    ).json()["data"]
    ascending = client.get("/api/v1/screen?sort=default&direction=asc").json()["data"]

    assert executable["prediction_basis"] == "executable"
    assert executable["return_basis"] == "executable"
    assert executable["items"][0]["symbol"] == "US.AMZN"
    assert forecast["prediction_basis"] == "forecast"
    assert forecast["items"][0]["symbol"] == "US.AAPL"
    assert capital_gain["return_basis"] == "capital_gain"
    assert capital_gain["items"][0]["symbol"] == "US.MSFT"
    assert ascending["items"][0]["symbol"] != executable["items"][0]["symbol"]


def test_screen_thresholds_are_server_authoritative_and_composable(
    tmp_path: Path,
) -> None:
    client = make_research_client(tmp_path)

    response = client.get(
        "/api/v1/screen"
        "?prediction_basis=executable"
        "&return_basis=executable"
        "&min_probability=0.90"
        "&max_q10=-0.045"
        "&confidence=medium"
        "&limit=100"
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [row["symbol"] for row in data["items"]] == ["US.AMZN", "US.MSFT"]
    assert data["total"] == 2


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/screen?horizon=2",
        "/api/v1/screen?prediction_basis=raw_model",
        "/api/v1/screen?return_basis=price",
        "/api/v1/screen?return_basis=total_return",
        "/api/v1/screen?sort=sql;drop",
        "/api/v1/screen?direction=sideways",
        "/api/v1/screen?min_probability=-0.1",
        "/api/v1/screen?min_probability=1.1",
        "/api/v1/screen?max_q10=-1.01",
        "/api/v1/screen?max_q10=1.01",
        "/api/v1/screen?confidence=unavailable",
        "/api/v1/instruments?page=0",
        "/api/v1/instruments?limit=0",
        "/api/v1/instruments?limit=101",
        "/api/v1/overview?as_of=2026-07-10T20:00:00",
        "/api/v1/instruments/US.AAPL/history?timeframe=5m",
        "/api/v1/instruments/US.AAPL/history?adjustment=forward",
        "/api/v1/instruments/US.AAPL/history?from=2026-07-11T00:00:00Z&to=2026-07-10T00:00:00Z",
        "/api/v1/instruments/US.AAPL/forecast?include=evidence,secrets",
        "/api/v1/models?unexpected=value",
        "/api/v1/data-capabilities?unexpected=value",
        "/api/v1/health?unexpected=value",
    ),
)
def test_strict_queries_reject_values_outside_the_public_contract(
    tmp_path: Path, path: str
) -> None:
    response = make_research_client(tmp_path).get(path)

    assert response.status_code == 422
    assert response.json() == {
        "request_id": "request-research",
        "generated_at": "2026-07-10T20:03:00Z",
        "data_status": "blocked",
        "error_code": "validation_error",
        "message": "The request parameters are invalid",
        "retryable": False,
        "affected_scope": ["request"],
    }


def test_forecast_include_controls_expanded_evidence(tmp_path: Path) -> None:
    client = make_research_client(tmp_path)

    compact = client.get("/api/v1/instruments/US.AAPL/forecast").json()["data"]
    expanded = client.get(
        "/api/v1/instruments/US.AAPL/forecast?include=evidence,similar_cases"
    ).json()["data"]

    assert compact["availability"] == "available"
    assert compact["evidence"] is None
    assert compact["similar_cases"] == []
    assert expanded["evidence"] == expanded["bundle"]["evidence"]
    assert expanded["similar_cases"] == []


def test_forecast_unavailable_is_hard_abstained_in_envelope_and_data(
    tmp_path: Path,
) -> None:
    response = make_research_client(tmp_path).get(
        "/api/v1/instruments/US.PRE/forecast?horizon=20"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["serving_status"] == "unavailable"
    assert body["effective_abstain"] is True
    assert body["exclusion_reason"] == ["unavailable"]
    assert body["data"]["availability"] == "unavailable"
    assert body["data"]["serving_status"] == "unavailable"
    assert body["data"]["effective_abstain"] is True
    assert body["data"]["requested_horizon"] == 20


def test_dynamic_overlay_excludes_current_rankings_but_preserves_exact_history(
    tmp_path: Path,
) -> None:
    client = make_research_client(tmp_path, state_provider=offline_components)

    current_screen = client.get("/api/v1/screen").json()
    current_overview = client.get("/api/v1/overview").json()
    forecast = client.get("/api/v1/instruments/US.AAPL/forecast").json()
    exact = client.get("/api/v1/screen?snapshot_id=snapshot-20260710-r1").json()

    assert current_screen["serving_status"] == "offline"
    assert current_screen["effective_abstain"] is True
    assert current_screen["data"]["items"] == []
    assert current_overview["data"]["top_opportunities"] == []
    assert forecast["data"]["serving"] == {
        "serving_status": "offline",
        "effective_abstain": True,
        "evaluated_at": "2026-07-10T20:03:00Z",
        "exclusion_reason": ["offline"],
    }
    assert exact["data"]["items"]
    assert exact["resolved_snapshot"]["snapshot_id"] == "snapshot-20260710-r1"


def test_valid_until_excludes_current_rows_before_paging_and_preserves_history(
    tmp_path: Path,
) -> None:
    future = GENERATED_AT + timedelta(days=4)
    client = make_research_client(tmp_path, clock=lambda: future)
    snapshot = tmp_path / "snapshot-20260710-r1"
    before = tree_fingerprint(snapshot)

    current_screen = client.get("/api/v1/screen?limit=1").json()
    current_overview = client.get("/api/v1/overview").json()
    beyond_recomputed_total = client.get("/api/v1/screen?page=2&limit=1")
    exact_screen = client.get(
        "/api/v1/screen?snapshot_id=snapshot-20260710-r1&limit=1"
    ).json()
    exact_overview = client.get(
        "/api/v1/overview?snapshot_id=snapshot-20260710-r1"
    ).json()

    assert current_screen["serving_status"] == "stale"
    assert current_screen["effective_abstain"] is True
    assert current_screen["exclusion_reason"] == ["stale"]
    assert current_screen["data"]["items"] == []
    assert current_screen["data"]["total"] == 0
    assert current_overview["serving_status"] == "stale"
    assert current_overview["data"]["top_opportunities"] == []
    assert beyond_recomputed_total.status_code == 422
    assert exact_screen["serving_status"] == "stale"
    assert exact_screen["effective_abstain"] is True
    assert exact_screen["data"]["items"]
    assert exact_screen["data"]["total"] > 0
    assert exact_overview["serving_status"] == "stale"
    assert exact_overview["effective_abstain"] is True
    assert exact_overview["data"]["top_opportunities"]
    assert tree_fingerprint(snapshot) == before


def test_snapshot_envelope_does_not_inherit_earliest_symbol_abstention(
    tmp_path: Path,
) -> None:
    tables = make_snapshot_tables()
    forecast_rows = tables["forecasts.parquet"].to_pylist()
    for row in forecast_rows:
        if row["symbol"] != "US.POST":
            continue
        payload = json.loads(row["payload_json"])
        payload["bundle"]["valid_until"] = "2026-07-11T20:02:00Z"
        row["payload_json"] = json.dumps(payload)
    client = make_custom_forecast_client(tmp_path, forecast_rows)

    screen = client.get("/api/v1/screen").json()
    overview = client.get("/api/v1/overview").json()

    assert screen["serving_status"] == "live"
    assert screen["effective_abstain"] is False
    assert screen["data"]["items"]
    assert overview["serving_status"] == "live"
    assert overview["effective_abstain"] is False
    assert overview["data"]["top_opportunities"]


def test_all_unavailable_snapshot_is_successful_and_preserves_exact_history(
    tmp_path: Path,
) -> None:
    forecast_rows = make_all_unavailable_forecast_rows()
    assert len(forecast_rows) == 100
    client = make_custom_forecast_client(tmp_path, forecast_rows)
    snapshot = tmp_path / "snapshot-20260710-r1"
    before = tree_fingerprint(snapshot)

    current_screen_response = client.get("/api/v1/screen")
    current_overview_response = client.get("/api/v1/overview")
    exact_screen_response = client.get(
        "/api/v1/screen?snapshot_id=snapshot-20260710-r1"
    )

    assert current_screen_response.status_code == 200
    current_screen = current_screen_response.json()
    assert current_screen["serving_status"] == "unavailable"
    assert current_screen["effective_abstain"] is True
    assert current_screen["exclusion_reason"] == ["unavailable"]
    assert current_screen["data"]["items"] == []
    assert current_screen["data"]["total"] == 0
    assert current_overview_response.status_code == 200
    current_overview = current_overview_response.json()
    assert current_overview["serving_status"] == "unavailable"
    assert current_overview["effective_abstain"] is True
    assert current_overview["data"]["top_opportunities"] == []
    assert exact_screen_response.status_code == 200
    exact_screen = exact_screen_response.json()
    assert exact_screen["serving_status"] == "unavailable"
    assert exact_screen["effective_abstain"] is True
    assert exact_screen["data"]["items"]
    assert exact_screen["data"]["total"] > 0
    assert tree_fingerprint(snapshot) == before


def test_corrupt_forecast_coverage_maps_all_serving_reads_to_integrity_error(
    tmp_path: Path,
) -> None:
    forecast_rows = make_all_unavailable_forecast_rows()[:-1]
    client = make_custom_forecast_client(tmp_path, forecast_rows)

    for path in (
        "/api/v1/overview",
        "/api/v1/screen",
        "/api/v1/instruments/US.AAPL/forecast",
    ):
        response = client.get(path)
        assert response.status_code == 503
        assert response.json()["error_code"] == "snapshot_integrity_error"


def test_forecast_uses_one_request_time_for_overlay_and_envelope(
    tmp_path: Path,
) -> None:
    calls = 0

    def advancing_clock() -> datetime:
        nonlocal calls
        value = GENERATED_AT + timedelta(seconds=calls)
        calls += 1
        return value

    response = make_research_client(tmp_path, clock=advancing_clock).get(
        "/api/v1/instruments/US.AAPL/forecast"
    )

    assert response.status_code == 200
    body = response.json()
    assert calls == 1
    assert body["generated_at"] == body["evaluated_at"]
    assert body["generated_at"] == body["data"]["serving"]["evaluated_at"]


def test_every_get_is_deterministic_and_does_not_call_live_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a GET request must not invoke a live side effect")

    client = make_research_client(tmp_path / "snapshots")
    monkeypatch.setattr("stock_prediction.opend.probe.CapabilityProbe.probe", fail)
    monkeypatch.setattr(
        "stock_prediction.options.collector.OptionCollector.collect", fail
    )
    monkeypatch.setattr(
        "stock_prediction.serving.materialize.materialize_research_snapshot", fail
    )
    monkeypatch.setattr("stock_prediction.modeling.pipeline.run_m4_command", fail)
    monkeypatch.setattr("stock_prediction.modeling.pipeline.offline_demo", fail)
    var_root = Path("var")
    before = tree_fingerprint(var_root)

    paths = (
        "/api/v1/health",
        *RESEARCH_PATHS,
        "/api/v1/instruments/US.AAPL/options/heatmap",
        "/api/v1/instruments/US.AAPL/options/summary",
        "/api/v1/data-capabilities",
    )
    first = {path: client.get(path).json() for path in paths}
    second = {path: client.get(path).json() for path in paths}

    assert first == second
    assert tree_fingerprint(var_root) == before
    assert all("account" not in json.dumps(body).lower() for body in first.values())
