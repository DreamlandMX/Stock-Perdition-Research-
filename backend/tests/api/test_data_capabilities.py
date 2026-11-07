"""Tests for the persisted data-capability endpoint."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from stock_prediction.api.app import create_app
from stock_prediction.capabilities.store import JsonCapabilityStore
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
PROBED_AT = datetime(2026, 7, 10, 22, 0, tzinfo=UTC)
CURRENT_HEALTH = [
    ComponentHealth(
        component="opend",
        connectivity=Connectivity.CONNECTED,
        freshness=Freshness.CURRENT,
        completeness=Completeness.PARTIAL,
        readiness=Readiness.DEGRADED,
    )
]


def make_snapshot() -> CapabilitySnapshot:
    return CapabilitySnapshot(
        snapshot_id="snapshot-api",
        probed_at=PROBED_AT,
        opend_version="10.7.3218",
        sdk_version="10.8.6808",
        capabilities=[
            DataCapability(
                interface="get_market_snapshot",
                market="US",
                asset_type="stock",
                timeframe=None,
                status=CapabilityStatus.AVAILABLE,
                point_in_time_status=PointInTimeStatus.FORWARD_ONLY,
                probed_at=PROBED_AT,
            )
        ],
    )


def make_client(store: object, request_id: str = "request-capabilities") -> TestClient:
    app = create_app(
        capability_store=store,
        state_provider=lambda: CURRENT_HEALTH,
        clock=lambda: GENERATED_AT,
        request_id_factory=lambda: request_id,
    )
    return TestClient(app, raise_server_exceptions=False)


def test_data_capabilities_returns_latest_snapshot_without_writing(
    tmp_path: Path,
) -> None:
    store = JsonCapabilityStore(tmp_path)
    snapshot = make_snapshot()
    store.save(snapshot)
    files_before = {path: path.read_bytes() for path in tmp_path.iterdir()}

    response = make_client(store).get("/api/v1/data-capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "request_id": "request-capabilities",
        "generated_at": "2026-07-10T22:30:00Z",
        "data_status": "ready",
        "snapshot": snapshot.model_dump(mode="json"),
        "current_health": [
            component.model_dump(mode="json") for component in CURRENT_HEALTH
        ],
        "allowance_status": "available",
    }
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == files_before


def test_data_capabilities_returns_typed_unavailable_when_store_is_empty(
    tmp_path: Path,
) -> None:
    response = make_client(JsonCapabilityStore(tmp_path), "request-empty").get(
        "/api/v1/data-capabilities"
    )

    assert response.status_code == 503
    assert response.json() == {
        "request_id": "request-empty",
        "generated_at": "2026-07-10T22:30:00Z",
        "data_status": "blocked",
        "error_code": "capabilities_unavailable",
        "message": "No capability snapshot is available",
        "retryable": True,
        "affected_scope": ["data_capabilities"],
    }
    assert not any(tmp_path.iterdir())


def test_data_capabilities_sanitizes_store_failures() -> None:
    class BrokenStore:
        def load_latest(self) -> None:
            raise RuntimeError("raw futu account 123456 and socket details")

    response = make_client(BrokenStore(), "request-error").get(
        "/api/v1/data-capabilities"
    )

    assert response.status_code == 503
    assert response.json() == {
        "request_id": "request-error",
        "generated_at": "2026-07-10T22:30:00Z",
        "data_status": "blocked",
        "error_code": "capabilities_unavailable",
        "message": "Capability snapshots could not be read",
        "retryable": True,
        "affected_scope": ["data_capabilities"],
    }
    assert "123456" not in response.text
    assert "socket" not in response.text


def test_unknown_route_uses_stable_error_envelope() -> None:
    response = make_client(JsonCapabilityStore(), "request-not-found").get(
        "/api/v1/does-not-exist"
    )

    assert response.status_code == 404
    assert response.json() == {
        "request_id": "request-not-found",
        "generated_at": "2026-07-10T22:30:00Z",
        "data_status": "blocked",
        "error_code": "not_found",
        "message": "Resource not found",
        "retryable": False,
        "affected_scope": ["api"],
    }
