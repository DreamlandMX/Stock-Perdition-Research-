from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.tests.fixtures.research_snapshot_factory import write_valid_snapshot
from stock_prediction.contracts.operations import UpdateOperation, UpdateOperationStatus
from stock_prediction.api.runtime import (
    RuntimeConfigurationError,
    create_runtime_app,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.model_operations import ModelTrainingOperationStore


def test_runtime_without_snapshot_root_fails_closed() -> None:
    with pytest.raises(RuntimeConfigurationError, match="unavailable"):
        create_runtime_app({})


def test_runtime_allows_explicit_unavailable_read_only_shell() -> None:
    with TestClient(
        create_runtime_app({"PREDICTION_STOCK_ALLOW_UNAVAILABLE": "1"})
    ) as client:
        response = client.get("/api/v1/overview")
    assert response.status_code == 503
    assert response.json()["error_code"] == "research_snapshot_unavailable"


def test_runtime_serves_only_explicit_absolute_snapshot_root(tmp_path: Path) -> None:
    root = tmp_path / "research"
    write_valid_snapshot(root)
    with TestClient(
        create_runtime_app({"PREDICTION_STOCK_RESEARCH_ROOT": str(root.resolve())})
    ) as client:
        response = client.get("/api/v1/overview?snapshot_id=snapshot-20260710-r1")
    assert response.status_code == 200
    assert response.json()["resolved_snapshot"]["snapshot_id"] == (
        "snapshot-20260710-r1"
    )
    assert response.json()["data"]["top_opportunities"]


def test_runtime_rejects_relative_or_missing_snapshot_root(tmp_path: Path) -> None:
    with pytest.raises(RuntimeConfigurationError, match="invalid"):
        create_runtime_app({"PREDICTION_STOCK_RESEARCH_ROOT": "relative/research"})
    with pytest.raises(RuntimeConfigurationError, match="unavailable"):
        create_runtime_app(
            {"PREDICTION_STOCK_RESEARCH_ROOT": str((tmp_path / "missing").resolve())}
        )


def test_runtime_partial_result_uses_the_training_coordinator(tmp_path: Path) -> None:
    research_root = tmp_path / "research"
    write_valid_snapshot(research_root)
    paths = LivePaths.from_workspace(tmp_path)
    now = datetime.now(UTC)
    operation = UpdateOperation(
        operation_id="model-training-runtime-partial",
        operation_type="model_training",
        status=UpdateOperationStatus.QUEUED,
        progress_percent=0,
        requested_bundles=("direction",),
        created_at=now,
        updated_at=now,
    )
    ModelTrainingOperationStore(paths).save(operation)

    with TestClient(
        create_runtime_app(
            {
                "PREDICTION_STOCK_RESEARCH_ROOT": str(research_root.resolve()),
                "PREDICTION_STOCK_LIVE_ROOT": str(paths.root.resolve()),
            }
        )
    ) as client:
        response = client.get(
            "/api/v1/operations/model-training/"
            "model-training-runtime-partial/partial-result",
            params={"symbol": "US.AAPL"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "availability": "unavailable",
        "reason": "m7_partial_no_valid_fold",
    }
