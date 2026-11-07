"""Stable and sanitized error mappings for the research API."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.tests.api.test_research_routes import (
    GENERATED_AT,
    SnapshotStore,
    live_components,
    make_research_client,
)
from backend.tests.fixtures.research_snapshot_factory import (
    write_valid_snapshot,
)
from stock_prediction.api.app import create_app
from stock_prediction.serving.repository import (
    DuckDbResearchRepository,
    SnapshotIntegrityError,
    SnapshotNotFound,
    SnapshotVersionConflict,
)


@pytest.mark.parametrize(
    ("path", "status_code", "error_code"),
    (
        (
            "/api/v1/instruments/BAD/forecast",
            404,
            "unknown_symbol",
        ),
        (
            "/api/v1/instruments/US.UNKNOWN/forecast",
            404,
            "not_in_universe",
        ),
        (
            "/api/v1/overview?snapshot_id=snapshot-missing",
            503,
            "research_snapshot_unavailable",
        ),
        (
            "/api/v1/overview?snapshot_id=snapshot-20260710-r1&model_version=model-conflict",
            409,
            "snapshot_version_conflict",
        ),
        (
            "/api/v1/instruments?page=999",
            422,
            "validation_error",
        ),
    ),
)
def test_repository_and_symbol_failures_have_exact_public_mappings(
    tmp_path: Path, path: str, status_code: int, error_code: str
) -> None:
    response = make_research_client(tmp_path).get(path)

    assert response.status_code == status_code
    assert response.json()["error_code"] == error_code
    assert response.json()["request_id"] == "request-research"


def test_corrupt_snapshot_maps_to_sanitized_integrity_error(tmp_path: Path) -> None:
    snapshot = write_valid_snapshot(tmp_path)
    snapshot.joinpath("screen.parquet").write_text(
        "SELECT secret FROM C:/users/account/private.db",
        encoding="utf-8",
    )
    app = create_app(
        capability_store=SnapshotStore(),
        research_repository=DuckDbResearchRepository(tmp_path),
        state_provider=live_components,
        clock=lambda: GENERATED_AT,
        request_id_factory=lambda: "request-corrupt",
    )

    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/overview")

    assert response.status_code == 503
    assert response.json() == {
        "request_id": "request-corrupt",
        "generated_at": "2026-07-10T20:03:00Z",
        "data_status": "blocked",
        "error_code": "snapshot_integrity_error",
        "message": "The research snapshot failed integrity verification",
        "retryable": True,
        "affected_scope": ["research_snapshot"],
    }
    lowered = response.text.lower()
    assert "select" not in lowered
    assert "private.db" not in lowered
    assert "account" not in lowered


class RaisingRepository:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def resolve_snapshot(self, _query: object) -> object:
        raise self.error


@pytest.mark.parametrize(
    ("error", "status_code", "error_code"),
    (
        (
            SnapshotNotFound("SQL SELECT * FROM account at C:/private"),
            503,
            "research_snapshot_unavailable",
        ),
        (
            SnapshotVersionConflict("model mismatch /private/model.bin"),
            409,
            "snapshot_version_conflict",
        ),
        (
            SnapshotIntegrityError("duckdb corruption SELECT account_number"),
            503,
            "snapshot_integrity_error",
        ),
    ),
)
def test_typed_repository_errors_never_expose_internal_details(
    error: Exception, status_code: int, error_code: str
) -> None:
    app = create_app(
        capability_store=SnapshotStore(),
        research_repository=RaisingRepository(error),  # type: ignore[arg-type]
        state_provider=live_components,
        clock=lambda: GENERATED_AT,
        request_id_factory=lambda: "request-sanitized",
    )

    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/overview")

    assert response.status_code == status_code
    assert response.json()["error_code"] == error_code
    lowered = response.text.lower()
    assert "select" not in lowered
    assert "private" not in lowered
    assert "account" not in lowered


def test_default_repository_is_explicitly_unavailable() -> None:
    app = create_app(
        capability_store=SnapshotStore(),
        state_provider=live_components,
        clock=lambda: GENERATED_AT,
        request_id_factory=lambda: "request-default-repository",
    )

    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/overview")

    assert response.status_code == 503
    assert response.json()["error_code"] == "research_snapshot_unavailable"
