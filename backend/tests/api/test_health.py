"""Tests for the side-effect-free health endpoint."""

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

import stock_prediction.api.app as app_module
from stock_prediction.api.app import create_app
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


GENERATED_AT = datetime(2026, 7, 10, 22, 30, tzinfo=UTC)


class FailOnStoreUse:
    def save(self, _snapshot: object) -> None:
        raise AssertionError("health must not write capability snapshots")

    def load_latest(self) -> None:
        raise AssertionError("health must not read capability snapshots")


class SnapshotStore:
    def __init__(self, snapshot: CapabilitySnapshot | None) -> None:
        self.snapshot = snapshot
        self.load_calls = 0

    def load_latest(self) -> CapabilitySnapshot | None:
        self.load_calls += 1
        return self.snapshot


def available_snapshot(
    global_status: CapabilityStatus = CapabilityStatus.AVAILABLE,
) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        snapshot_id="health-snapshot",
        probed_at=GENERATED_AT,
        opend_version="1007",
        sdk_version="10.8.6808",
        capabilities=[
            DataCapability(
                interface="get_global_state",
                market="GLOBAL",
                asset_type="system",
                timeframe=None,
                status=global_status,
                point_in_time_status=PointInTimeStatus.NOT_APPLICABLE,
                probed_at=GENERATED_AT,
            ),
            DataCapability(
                interface="request_history_kline",
                market="US",
                asset_type="stock",
                timeframe="1d",
                status=CapabilityStatus.UNKNOWN,
                point_in_time_status=PointInTimeStatus.UNPROVEN,
                probed_at=GENERATED_AT,
            ),
        ],
    )


def disconnected_opend() -> list[ComponentHealth]:
    return [
        ComponentHealth(
            component="opend",
            connectivity=Connectivity.DISCONNECTED,
            freshness=Freshness.UNKNOWN,
            completeness=Completeness.UNKNOWN,
            readiness=Readiness.BLOCKED,
        )
    ]


def test_component_health_preserves_live_provider_evidence() -> None:
    component = ComponentHealth(
        component="market_data",
        connectivity=Connectivity.CONNECTED,
        freshness=Freshness.CURRENT,
        completeness=Completeness.COMPLETE,
        readiness=Readiness.READY,
        last_success_at=GENERATED_AT,
        data_cutoff=date(2026, 7, 10),
        expected_cutoff=date(2026, 7, 10),
        snapshot_cutoff=date(2026, 7, 10),
        lag_sessions=0,
        provider="opend",
        quota_remaining=0,
    )

    assert component.model_dump(mode="json", exclude_none=True) == {
        "component": "market_data",
        "connectivity": "connected",
        "freshness": "current",
        "completeness": "complete",
        "readiness": "ready",
        "last_success_at": "2026-07-10T22:30:00Z",
        "data_cutoff": "2026-07-10",
        "expected_cutoff": "2026-07-10",
        "snapshot_cutoff": "2026-07-10",
        "lag_sessions": 0,
        "provider": "opend",
        "quota_remaining": 0,
    }


def test_health_is_200_and_reports_disconnected_opend_without_store_use(
    tmp_path: Path,
) -> None:
    app = create_app(
        capability_store=FailOnStoreUse(),
        state_provider=disconnected_opend,
        clock=lambda: GENERATED_AT,
        request_id_factory=lambda: "request-health",
    )

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "request_id": "request-health",
        "generated_at": "2026-07-10T22:30:00Z",
        "service_status": "degraded",
        "data_status": "blocked",
        "components": [
            {
                "component": "opend",
                "connectivity": "disconnected",
                "freshness": "unknown",
                "completeness": "unknown",
                "readiness": "blocked",
            }
        ],
    }
    assert not any(tmp_path.iterdir())


def test_health_does_not_call_capability_probe(monkeypatch: object) -> None:
    def fail_probe(_self: object) -> None:
        raise AssertionError("health must not call CapabilityProbe")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "stock_prediction.opend.probe.CapabilityProbe.probe", fail_probe
    )
    app = create_app(
        capability_store=FailOnStoreUse(),
        state_provider=disconnected_opend,
        clock=lambda: GENERATED_AT,
        request_id_factory=lambda: "request-no-probe",
    )

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200


def test_health_does_not_resolve_a_research_snapshot() -> None:
    class FailResearchRepository:
        def resolve_snapshot(self, _query: object) -> None:
            raise AssertionError("health must not resolve research snapshots")

    app = create_app(
        capability_store=FailOnStoreUse(),
        research_repository=FailResearchRepository(),  # type: ignore[arg-type]
        state_provider=disconnected_opend,
        clock=lambda: GENERATED_AT,
        request_id_factory=lambda: "request-health-no-research",
    )

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200


def test_default_health_resolves_latest_persisted_snapshot_without_probe(
    monkeypatch: object,
) -> None:
    def fail_probe(_self: object) -> None:
        raise AssertionError("health must not call CapabilityProbe")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "stock_prediction.opend.probe.CapabilityProbe.probe", fail_probe
    )
    store = SnapshotStore(available_snapshot())
    app = create_app(
        capability_store=store,
        clock=lambda: GENERATED_AT,
        request_id_factory=lambda: "request-persisted-health",
    )

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert store.load_calls == 1
    assert response.json() == {
        "request_id": "request-persisted-health",
        "generated_at": "2026-07-10T22:30:00Z",
        "service_status": "degraded",
        "data_status": "degraded",
        "components": [
            {
                "component": "opend",
                "connectivity": "connected",
                "freshness": "current",
                "completeness": "partial",
                "readiness": "degraded",
            }
        ],
    }


@pytest.mark.parametrize("snapshot", [None])
def test_default_health_fails_safe_when_snapshot_is_missing(
    snapshot: CapabilitySnapshot | None,
) -> None:
    store = SnapshotStore(snapshot)
    app = create_app(
        capability_store=store,
        clock=lambda: GENERATED_AT,
        request_id_factory=lambda: "request-missing-snapshot",
    )

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert store.load_calls == 1
    assert response.json()["components"][0]["connectivity"] == "disconnected"
    assert response.json()["data_status"] == "blocked"


def test_default_health_keeps_connectivity_separate_from_quote_login() -> None:
    store = SnapshotStore(available_snapshot(CapabilityStatus.DENIED))
    app = create_app(
        capability_store=store,
        clock=lambda: GENERATED_AT,
        request_id_factory=lambda: "request-denied-snapshot",
    )

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["components"] == [
        {
            "component": "opend",
            "connectivity": "connected",
            "freshness": "current",
            "completeness": "unknown",
            "readiness": "blocked",
        }
    ]
    assert response.json()["service_status"] == "degraded"
    assert response.json()["data_status"] == "blocked"


def test_health_remains_200_and_sanitized_when_state_provider_fails() -> None:
    def broken_state_provider() -> list[ComponentHealth]:
        raise RuntimeError("raw futu account 123456 and socket details")

    app = create_app(
        capability_store=FailOnStoreUse(),
        state_provider=broken_state_provider,
        clock=lambda: GENERATED_AT,
        request_id_factory=lambda: "request-fallback",
    )

    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["service_status"] == "degraded"
    assert response.json()["data_status"] == "blocked"
    assert response.json()["components"][0]["connectivity"] == "disconnected"
    assert "123456" not in response.text
    assert "socket" not in response.text


def assert_safe_fallback(response: object) -> None:
    status_code = getattr(response, "status_code")
    body = getattr(response, "json")()
    assert status_code == 200
    assert body["service_status"] == "degraded"
    assert body["data_status"] == "blocked"
    assert body["components"] == [
        {
            "component": "opend",
            "connectivity": "disconnected",
            "freshness": "unknown",
            "completeness": "unknown",
            "readiness": "blocked",
        }
    ]


@pytest.mark.parametrize("provider_result", [None, [], ["malformed"]])
def test_health_falls_back_for_none_empty_or_malformed_component_states(
    provider_result: object,
) -> None:
    app = create_app(
        capability_store=FailOnStoreUse(),
        state_provider=lambda: provider_result,  # type: ignore[arg-type,return-value]
        clock=lambda: GENERATED_AT,
        request_id_factory=lambda: "request-malformed-provider",
    )

    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/health")

    assert_safe_fallback(response)


def test_health_uses_safe_utc_and_uuid_fallbacks_when_factories_raise() -> None:
    def broken_clock() -> datetime:
        raise RuntimeError("clock failed")

    def broken_request_id() -> str:
        raise RuntimeError("request id failed")

    app = create_app(
        capability_store=FailOnStoreUse(),
        state_provider=disconnected_opend,
        clock=broken_clock,
        request_id_factory=broken_request_id,
    )

    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/health")

    assert_safe_fallback(response)
    body = response.json()
    UUID(body["request_id"])
    generated_at = datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00"))
    assert generated_at.tzinfo is UTC


def test_health_stays_200_if_health_response_construction_fails(
    monkeypatch: object,
) -> None:
    app = create_app(
        capability_store=FailOnStoreUse(),
        state_provider=disconnected_opend,
        clock=lambda: GENERATED_AT,
        request_id_factory=lambda: "request-response-failure",
    )

    def fail_response_construction(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("response construction failed")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module, "_health_content", fail_response_construction
    )

    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/health")

    assert_safe_fallback(response)
    body = response.json()
    UUID(body["request_id"])
    assert body["request_id"] != "request-response-failure"
    generated_at = datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00"))
    assert generated_at.tzinfo is UTC


def test_documented_api_command_binds_only_to_loopback() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "-File .\\scripts\\start-local.ps1" in readme
    assert "127.0.0.1:18766" in readme
    assert "127.0.0.1:18765" in readme
    start_script = Path("scripts/start-local.ps1").read_text(encoding="utf-8")
    assert '"--host", "127.0.0.1"' in start_script
    assert "--host 0.0.0.0" not in readme
