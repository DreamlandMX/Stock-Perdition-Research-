from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from stock_prediction.api.app import create_app
from stock_prediction.contracts.operations import (
    CandidateTrainingRequest,
    CapabilityReadiness,
    CandidateTrainingAction,
    EodWorkerState,
    ModelTrainingReadiness,
    ModelTrainingReadinessState,
    UpdateOperationStage,
    UpdateOperationStatus,
)
from pydantic import ValidationError
from stock_prediction.live.artifact_references import ArtifactReference
from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_V2,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_cuda import M7CudaPreflightError
from stock_prediction.live.features import LiveFeatureArtifact
from stock_prediction.live.ingestion import LivePreflightResult
from stock_prediction.live.locking import LiveWriterLease
from stock_prediction.live.m7_audit import M7AuditLog
from stock_prediction.live.operation_store import EodOperationStore
from stock_prediction.live.model_operations import (
    CandidateRecipeExhaustedError,
    CandidateRecipeVersionMismatchError,
    ModelTrainingCoordinator,
    ModelTrainingOperationStore,
    PromotionWindowAlreadyConsumedError,
)
from stock_prediction.live.promotion_windows import PromotionWindowNotReadyError
from stock_prediction.live.promotion_operations import (
    ModelPromotionCoordinator,
    ModelPromotionOperationStore,
)
from stock_prediction.live import scheduler as live_scheduler
from stock_prediction.live.operations import EodUpdateCoordinator
from stock_prediction.live.training import write_latest_training_pointer
from stock_prediction.universe.seed import SEED_UNIVERSE, SEED_UNIVERSE_VERSION


_ACTIVE_COUNT = len(SEED_UNIVERSE)


def test_targeted_training_request_and_capability_contracts_are_strict() -> None:
    request = CandidateTrainingRequest(
        bundles=("direction", "range_exit_20"),
        trigger="manual",
    )
    readiness = CapabilityReadiness(
        bundle_id="direction",
        development_state="passed",
        shadow_state="frozen",
        blind_state="collecting",
        promotion_state="blocked",
        passed_tasks=3,
        total_tasks=3,
        block_reasons=("blind_window_incomplete",),
    )

    assert request.bundles == ("direction", "range_exit_20")
    assert request.trigger == "manual"
    assert readiness.bundle_id == "direction"
    with pytest.raises(ValidationError, match="unique"):
        CandidateTrainingRequest(
            bundles=("direction", "direction"),
            trigger="manual",
        )


def test_cancellation_status_values_are_stable() -> None:
    assert UpdateOperationStatus.CANCEL_REQUESTED.value == "cancel_requested"
    assert UpdateOperationStatus.CANCELLED.value == "cancelled"


def _write_m7_promotion_report(
    paths: LivePaths,
    *,
    package_sha256: str = "a" * 64,
    incumbent_id: str | None = "direction-incumbent-v1",
) -> None:
    run_id = "run-targeted-vnext-1"
    report_path = paths.models / "runs" / run_id / "training-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "bundle_results": {
                    "direction": {
                        "promotion_state": "awaiting_manual_approval",
                        "package_id": "direction-package-v1",
                        "package_sha256": package_sha256,
                        "incumbent_id": incumbent_id,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    write_latest_training_pointer(paths, report_path, run_id)


def test_targeted_training_api_accepts_manual_bundles_and_legacy_no_body(
    tmp_path: Path,
) -> None:
    legacy = ModelTrainingOperationStore(
        LivePaths.from_workspace(tmp_path / "legacy"),
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    targeted = ModelTrainingOperationStore(
        LivePaths.from_workspace(tmp_path / "targeted"),
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    legacy.heartbeat()
    targeted.heartbeat()

    with TestClient(create_app(model_training_controller=legacy)) as client:
        legacy_response = client.post("/api/v1/operations/model-training")
    with TestClient(create_app(model_training_controller=targeted)) as client:
        targeted_response = client.post(
            "/api/v1/operations/model-training",
            json={"bundles": ["direction"], "trigger": "manual"},
        )

    assert legacy_response.status_code == 202
    assert legacy_response.json().get("requested_bundles") is None
    assert targeted_response.status_code == 202
    assert targeted_response.json()["requested_bundles"] == ["direction"]


def test_training_api_rejects_duplicate_bundles_and_external_internal_triggers(
    tmp_path: Path,
) -> None:
    store = ModelTrainingOperationStore(
        LivePaths.from_workspace(tmp_path),
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    store.heartbeat()
    with TestClient(create_app(model_training_controller=store)) as client:
        duplicate = client.post(
            "/api/v1/operations/model-training",
            json={"bundles": ["direction", "direction"], "trigger": "manual"},
        )
        scheduled = client.post(
            "/api/v1/operations/model-training",
            json={"bundles": ["direction"], "trigger": "scheduled"},
        )
        drift = client.post(
            "/api/v1/operations/model-training",
            json={"bundles": ["range_exit_20"], "trigger": "drift"},
        )

    assert duplicate.status_code == 422
    assert scheduled.status_code == 403
    assert scheduled.json()["error_code"] == "training_trigger_unauthorized"
    assert drift.status_code == 403
    assert store.latest() is None


def test_v10_range_requires_a_terminal_direction_operation(tmp_path: Path) -> None:
    store = ModelTrainingOperationStore(
        LivePaths.from_workspace(tmp_path),
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    store.heartbeat()

    with pytest.raises(
        CandidateRecipeVersionMismatchError,
        match="m7_range_phase_requires_terminal_direction",
    ):
        store.submit(bundles=("range_exit_20",))

    direction, created = store.submit(bundles=("direction",))
    assert created is True
    store.update(direction.operation_id, status=UpdateOperationStatus.FAILED)

    range_operation, created = store.submit(bundles=("range_exit_20",))

    assert created is True
    assert range_operation.requested_bundles == ("range_exit_20",)


def test_cancel_operation_is_idempotent_and_respects_atomic_boundary(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    store = ModelTrainingOperationStore(
        paths,
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    store.heartbeat()
    queued, _ = store.submit()
    with TestClient(create_app(model_training_controller=store)) as client:
        cancelled = client.post(f"/api/v1/operations/{queued.operation_id}/cancel")
        repeated = client.post(f"/api/v1/operations/{queued.operation_id}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert repeated.json()["status"] == "cancelled"
    assert cancelled.json()["cancelled_at"] is not None

    running, _ = store.submit()
    store.update(running.operation_id, status=UpdateOperationStatus.RUNNING)
    with TestClient(create_app(model_training_controller=store)) as client:
        requested = client.post(f"/api/v1/operations/{running.operation_id}/cancel")
        requested_again = client.post(
            f"/api/v1/operations/{running.operation_id}/cancel"
        )
    assert requested.json()["status"] == "cancel_requested"
    assert requested_again.json()["status"] == "cancel_requested"
    assert requested.json()["cancel_requested_at"] is not None


def test_cancel_terminal_operation_returns_conflict(tmp_path: Path) -> None:
    store = ModelTrainingOperationStore(
        LivePaths.from_workspace(tmp_path),
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    store.heartbeat()
    queued, _ = store.submit()
    store.update(
        queued.operation_id,
        status=UpdateOperationStatus.FAILED,
        finished_at=datetime.now(UTC),
    )

    with TestClient(create_app(model_training_controller=store)) as client:
        response = client.post(f"/api/v1/operations/{queued.operation_id}/cancel")

    assert response.status_code == 409
    assert response.json()["error_code"] == "operation_not_cancellable"


def test_m7_promotion_requires_exact_body_hash_and_incumbent(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    _write_m7_promotion_report(paths)
    incumbent = {"value": "direction-incumbent-v1"}
    store = ModelPromotionOperationStore(
        paths,
        readiness_provider=_readiness_provider(),
        incumbent_provider=lambda _paths, _bundle: incumbent["value"],
    )
    store.heartbeat()

    with TestClient(create_app(model_promotion_controller=store)) as client:
        missing = client.post("/api/v1/operations/model-promotion")
        bad_hash = client.post(
            "/api/v1/operations/model-promotion",
            json={
                "training_run_id": "run-targeted-vnext-1",
                "bundle_id": "direction",
                "expected_package_sha256": "b" * 64,
                "expected_incumbent_id": "direction-incumbent-v1",
            },
        )
        incumbent["value"] = "direction-incumbent-v2"
        stale_incumbent = client.post(
            "/api/v1/operations/model-promotion",
            json={
                "training_run_id": "run-targeted-vnext-1",
                "bundle_id": "direction",
                "expected_package_sha256": "a" * 64,
                "expected_incumbent_id": "direction-incumbent-v1",
            },
        )
        incumbent["value"] = "direction-incumbent-v1"
        accepted = client.post(
            "/api/v1/operations/model-promotion",
            json={
                "training_run_id": "run-targeted-vnext-1",
                "bundle_id": "direction",
                "expected_package_sha256": "a" * 64,
                "expected_incumbent_id": "direction-incumbent-v1",
            },
        )

    assert missing.status_code == 409
    assert missing.json()["error_code"] == "model_promotion_request_required"
    assert bad_hash.status_code == 409
    assert bad_hash.json()["error_code"] == "model_package_hash_mismatch"
    assert stale_incumbent.status_code == 409
    assert stale_incumbent.json()["error_code"] == "model_incumbent_conflict"
    assert accepted.status_code == 202
    assert accepted.json()["requested_bundles"] == ["direction"]
    assert accepted.json()["automatic_promotion"] == "awaiting_manual_approval"


def _training_readiness(
    state: ModelTrainingReadinessState = ModelTrainingReadinessState.READY,
) -> ModelTrainingReadiness:
    return ModelTrainingReadiness(
        state=state,
        window_id="promotion-20260630-20261016-v1",
        window_start=date(2026, 6, 30),
        window_end=date(2026, 10, 16),
        label_complete_through=date(2026, 11, 13),
        automatic_training_at=datetime(2026, 11, 14, 15, tzinfo=UTC),
        data_cutoff=date(2026, 11, 13),
        anchor_sessions_collected=77
        if state is not ModelTrainingReadinessState.COLLECTING
        else 20,
        required_anchor_sessions=77,
        fully_labeled_sessions=77
        if state is not ModelTrainingReadinessState.COLLECTING
        else 0,
        required_fully_labeled_sessions=77,
        label_horizon=20,
        can_submit=state is ModelTrainingReadinessState.READY,
        detail=f"promotion_window_{state.value}",
    )


def _readiness_provider(
    state: ModelTrainingReadinessState = ModelTrainingReadinessState.READY,
):
    readiness = _training_readiness(state)
    return lambda _paths: readiness


def _fingerprint_provider(_paths: LivePaths) -> str:
    return "candidate-data-and-config-v1"


def _versioned_fingerprint_provider(
    _paths: LivePaths,
    recipe_version: str,
) -> str:
    return f"candidate-data-and-config:{recipe_version}"


def test_operation_submission_is_durable_and_single_instance(tmp_path: Path) -> None:
    store = EodOperationStore(LivePaths.from_workspace(tmp_path))

    first, created = store.submit()
    second, duplicate_created = store.submit()

    assert created is True
    assert duplicate_created is False
    assert second.operation_id == first.operation_id
    assert store.get(first.operation_id) == first
    assert store.latest() == first


def test_model_training_submission_reuses_the_active_standard_run(
    tmp_path: Path,
) -> None:
    store = ModelTrainingOperationStore(
        LivePaths.from_workspace(tmp_path),
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )

    first, created = store.submit()
    duplicate, duplicate_created = store.submit()

    assert created is True
    assert duplicate_created is False
    assert duplicate.operation_id == first.operation_id
    assert duplicate.operation_type == "model_training"


def test_model_training_submission_reuses_matching_completed_history(
    tmp_path: Path,
) -> None:
    store = ModelTrainingOperationStore(
        LivePaths.from_workspace(tmp_path),
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    queued, _ = store.submit()
    finished_at = datetime(2026, 7, 29, 16, tzinfo=UTC)
    completed = queued.model_copy(
        update={
            "status": UpdateOperationStatus.SUCCEEDED_DATA_ONLY,
            "progress_percent": 100,
            "finished_at": finished_at,
            "updated_at": finished_at,
        }
    )
    store.save(completed)
    interrupted = completed.model_copy(
        update={
            "operation_id": "model-training-interrupted",
            "status": UpdateOperationStatus.FAILED,
            "data_hash": "different-candidate-fingerprint",
            "finished_at": finished_at + timedelta(minutes=1),
            "updated_at": finished_at + timedelta(minutes=1),
        }
    )
    store.save(interrupted)

    reused, created = store.submit()

    assert created is False
    assert reused.operation_id == completed.operation_id
    assert store.latest() == completed


def test_second_recipe_creates_once_then_reuses_active_and_completed(
    tmp_path: Path,
) -> None:
    base = _training_readiness(ModelTrainingReadinessState.COLLECTING)
    available = base.model_copy(
        update={
            "candidate_training_action": CandidateTrainingAction.AVAILABLE,
            "latest_candidate_recipe_version": "candidate-recipe-v1",
            "next_candidate_recipe_version": CANDIDATE_RECIPE_V2,
        }
    )
    exhausted = available.model_copy(
        update={
            "can_submit_candidate_training": False,
            "candidate_training_action": CandidateTrainingAction.EXHAUSTED,
            "candidate_training_block_reason": (
                "no_additional_candidate_recipe_registered"
            ),
            "latest_candidate_recipe_version": CANDIDATE_RECIPE_V2,
            "next_candidate_recipe_version": None,
        }
    )
    readiness = {"value": available}
    store = ModelTrainingOperationStore(
        LivePaths.from_workspace(tmp_path),
        readiness_provider=lambda _paths: readiness["value"],
        fingerprint_provider=_versioned_fingerprint_provider,
    )

    queued, created = store.submit(candidate_recipe_version=CANDIDATE_RECIPE_V2)
    active, active_created = store.submit(candidate_recipe_version=CANDIDATE_RECIPE_V2)

    assert created is True
    assert queued.candidate_generation == 2
    assert queued.submission_disposition == "created"
    assert active_created is False
    assert active.operation_id == queued.operation_id
    assert active.submission_disposition == "reused_active"

    finished_at = datetime(2026, 7, 29, 18, tzinfo=UTC)
    store.save(
        queued.model_copy(
            update={
                "status": UpdateOperationStatus.SUCCEEDED_DATA_ONLY,
                "progress_percent": 100,
                "finished_at": finished_at,
                "updated_at": finished_at,
            }
        )
    )
    readiness["value"] = exhausted
    completed, completed_created = store.submit(
        candidate_recipe_version=CANDIDATE_RECIPE_V2
    )

    assert completed_created is False
    assert completed.operation_id == queued.operation_id
    assert completed.no_change is True
    assert completed.submission_disposition == "reused_completed"
    with pytest.raises(CandidateRecipeExhaustedError):
        store.submit()


def test_v10_cuda_preflight_fails_before_the_targeted_executor_runs(
    tmp_path: Path,
) -> None:
    started: list[str] = []

    def reject_cuda() -> object:
        raise M7CudaPreflightError("m7_cuda_preflight_failed")

    coordinator = ModelTrainingCoordinator(
        LivePaths.from_workspace(tmp_path),
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
        cuda_preflight=reject_cuda,
        targeted_trainer_factory=lambda _operation, _cancelled: started.append(
            "started"
        ),  # type: ignore[arg-type]
    )
    queued, _ = coordinator.submit(
        candidate_recipe_version="candidate-recipe-seed100-targeted-v10",
        bundles=("direction",),
    )

    failed = coordinator.run_pending()

    assert failed is not None
    assert failed.status is UpdateOperationStatus.FAILED
    assert failed.current_stage is UpdateOperationStage.RUNTIME_PREFLIGHT
    assert failed.error_code == "m7_cuda_preflight_failed"
    assert failed.automatic_promotion == "kept_current"
    assert started == []
    assert queued.operation_id == failed.operation_id


def test_partial_result_endpoint_returns_a_stable_unavailable_response(
    tmp_path: Path,
) -> None:
    coordinator = ModelTrainingCoordinator(
        LivePaths.from_workspace(tmp_path),
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
        cuda_preflight=lambda: object(),  # type: ignore[arg-type]
    )
    queued, _ = coordinator.submit(
        candidate_recipe_version="candidate-recipe-seed100-targeted-v10",
        bundles=("direction",),
    )

    with TestClient(create_app(model_training_controller=coordinator)) as client:
        response = client.get(
            f"/api/v1/operations/model-training/{queued.operation_id}/partial-result",
            params={"symbol": "US.AAPL"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "availability": "unavailable",
        "reason": "m7_partial_no_valid_fold",
    }


def test_model_training_api_rejects_new_work_when_worker_is_offline(
    tmp_path: Path,
) -> None:
    store = ModelTrainingOperationStore(
        LivePaths.from_workspace(tmp_path),
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    with TestClient(create_app(model_training_controller=store)) as client:
        worker = client.get("/api/v1/operations/model-training/worker")
        submitted = client.post("/api/v1/operations/model-training")

    assert worker.status_code == 200
    assert worker.json()["state"] == "offline"
    assert submitted.status_code == 503
    assert submitted.json()["error_code"] == "model_worker_unavailable"
    assert store.latest() is None


def test_candidate_training_remains_available_after_promotion_window_is_consumed(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    store = ModelTrainingOperationStore(
        paths,
        readiness_provider=_readiness_provider(ModelTrainingReadinessState.CONSUMED),
        fingerprint_provider=_fingerprint_provider,
    )

    queued, created = store.submit()

    store.heartbeat()
    with TestClient(create_app(model_training_controller=store)) as client:
        submitted = client.post("/api/v1/operations/model-training")

    assert created is True
    assert submitted.status_code == 202
    assert submitted.json()["operation_id"] == queued.operation_id


def test_candidate_training_submits_while_blind_window_is_collecting(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    store = ModelTrainingOperationStore(
        paths,
        readiness_provider=_readiness_provider(ModelTrainingReadinessState.COLLECTING),
        fingerprint_provider=_fingerprint_provider,
    )
    queued, created = store.submit()

    store.heartbeat()
    with TestClient(create_app(model_training_controller=store)) as client:
        readiness = client.get("/api/v1/operations/model-training/readiness")
        submitted = client.post("/api/v1/operations/model-training")

    assert readiness.status_code == 200
    assert readiness.json()["state"] == "collecting"
    assert readiness.json()["anchor_sessions_collected"] == 20
    assert readiness.json()["fully_labeled_sessions"] == 0
    assert created is True
    assert submitted.status_code == 202
    assert submitted.json()["operation_id"] == queued.operation_id


def test_model_promotion_rejects_collecting_window(tmp_path: Path) -> None:
    store = ModelPromotionOperationStore(
        LivePaths.from_workspace(tmp_path),
        readiness_provider=_readiness_provider(ModelTrainingReadinessState.COLLECTING),
    )
    with pytest.raises(PromotionWindowNotReadyError):
        store.submit()


def test_model_promotion_rejects_consumed_window(tmp_path: Path) -> None:
    store = ModelPromotionOperationStore(
        LivePaths.from_workspace(tmp_path),
        readiness_provider=_readiness_provider(ModelTrainingReadinessState.CONSUMED),
    )
    with pytest.raises(PromotionWindowAlreadyConsumedError):
        store.submit()


def test_generic_operation_route_reads_model_training_tasks(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    eod_store = EodOperationStore(paths)
    training_store = ModelTrainingOperationStore(
        paths,
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    operation, _created = training_store.submit()

    with TestClient(
        create_app(
            operation_controller=eod_store,
            model_training_controller=training_store,
        )
    ) as client:
        response = client.get(f"/api/v1/operations/{operation.operation_id}")

    assert response.status_code == 200
    assert response.json()["operation_id"] == operation.operation_id
    assert response.json()["operation_type"] == "model_training"


class _TrainingStub:
    def __init__(self, *, promoted: bool = False) -> None:
        self.promoted = promoted

    def train(self, *, progress_callback):
        progress_callback("label_health", 10, "标签健康通过")
        progress_callback("candidate_search", 35, "候选搜索完成")
        progress_callback("oos_validation", 65, "开发窗口验证完成")
        progress_callback("promotion_window", 85, "晋升窗口完成")
        return {
            "all_gates_passed": self.promoted,
            "development_all_gates_passed": self.promoted,
            "automatic_promotion": "promoted" if self.promoted else "kept_current",
            "component_releases": (
                [
                    {
                        "gate_passed": True,
                        "horizon": 5,
                        "previous_champion_version": None,
                        "task": "return_quantiles",
                    }
                ]
                if self.promoted
                else []
            ),
            "model_version": "formal-bundle-v1",
            "previous_champion_version": None,
        }


class _PublisherStub:
    def publish(self, *, progress=None):
        return {
            "prediction_readiness": "data_only",
            "snapshot_id": "LIVE-STUB",
        }


def test_explicit_bundles_delegate_to_targeted_trainer_and_legacy_remains_separate(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    captured = {}

    class _TargetedStub:
        def train(self, *, progress_callback):
            progress_callback("candidate_search", 35, "M7 候选完成")
            return {
                "candidate_fingerprint": "m7-targeted-fingerprint",
                "development_all_gates_passed": True,
                "automatic_promotion": "awaiting_manual_approval",
                "model_version": "m7-direction-shadow-v1",
                "completed_bundles": ["direction"],
                "shadow_package_id": "m7-direction-shadow-001",
                "blind_window_id": "promotion-m7-direction-001",
                "evidence_scope": "optimization_reused",
            }

    def targeted_factory(operation, cancellation_requested):
        captured["recipe"] = operation.candidate_recipe_version
        captured["bundles"] = operation.requested_bundles
        captured["cancel"] = cancellation_requested()
        return _TargetedStub()

    coordinator = ModelTrainingCoordinator(
        paths,
        targeted_trainer_factory=targeted_factory,  # type: ignore[arg-type]
        publisher_factory=lambda: _PublisherStub(),
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
        cuda_preflight=lambda: object(),  # type: ignore[arg-type]
    )
    queued, _ = coordinator.submit(bundles=("direction",), trigger="manual")

    completed = coordinator.run_pending()

    assert queued.candidate_recipe_version == "candidate-recipe-seed100-targeted-v10"
    assert captured == {
        "recipe": "candidate-recipe-seed100-targeted-v10",
        "bundles": ("direction",),
        "cancel": False,
    }
    assert completed is not None
    assert completed.completed_bundles == ("direction",)
    assert completed.shadow_package_id == "m7-direction-shadow-001"
    assert completed.blind_window_id == "promotion-m7-direction-001"
    assert completed.automatic_promotion == "awaiting_manual_approval"


def test_running_targeted_training_cancels_after_report_atomic_boundary(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    coordinator: ModelTrainingCoordinator

    class _CancellingTrainer:
        def train(self, *, progress_callback):
            progress_callback("candidate_search", 35, "候选搜索完成")
            current = coordinator.latest()
            assert current is not None
            coordinator.request_cancel(current.operation_id)
            return {
                "candidate_fingerprint": "targeted-fingerprint",
                "development_all_gates_passed": True,
                "automatic_promotion": "kept_current",
                "model_version": "direction-shadow-v1",
            }

    coordinator = ModelTrainingCoordinator(
        paths,
        trainer_factory=lambda: _CancellingTrainer(),  # type: ignore[arg-type]
        publisher_factory=lambda: _PublisherStub(),
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
        cuda_preflight=lambda: object(),  # type: ignore[arg-type]
    )
    queued, _ = coordinator.submit(bundles=("direction",), trigger="manual")

    cancelled = coordinator.run_pending()

    assert cancelled is not None
    assert cancelled.operation_id == queued.operation_id
    assert cancelled.status is UpdateOperationStatus.CANCELLED
    assert cancelled.cancel_requested_at is not None
    assert cancelled.cancelled_at is not None
    assert cancelled.finished_at is not None
    assert not cancelled.completed_bundles
    assert cancelled.automatic_promotion == "kept_current"
    assert coordinator.run_pending() is None


def test_reconcile_interrupted_running_targeted_training_fails_closed_once(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    coordinator = ModelTrainingCoordinator(
        paths,
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    queued, _ = coordinator.submit(bundles=("direction",), trigger="manual")
    coordinator.store.update(
        queued.operation_id,
        status=UpdateOperationStatus.RUNNING,
        stage=UpdateOperationStage.CANDIDATE_SEARCH,
        progress_percent=10,
        phase_status="running",
        phase_detail="正在执行 M7 direction 注册候选矩阵",
        started_at=datetime(2026, 8, 28, 23, 23, tzinfo=UTC),
        active_trial_id="m7-outer-03.m7-dlinear-06",
    )

    recovered = coordinator.reconcile_interrupted_operation()
    repeated = coordinator.reconcile_interrupted_operation()

    assert recovered is not None
    assert recovered.status is UpdateOperationStatus.FAILED
    assert recovered.current_stage is UpdateOperationStage.CANDIDATE_SEARCH
    assert recovered.progress_percent == 10
    assert recovered.error_code == "model_training_worker_process_tree_lost"
    assert recovered.diagnostic_detail == "model_training_worker_process_tree_lost"
    assert recovered.finished_at is not None
    assert recovered.active_trial_id is None
    assert recovered.automatic_promotion == "kept_current"
    assert recovered.completed_bundles in {None, ()}
    assert repeated == recovered
    failed_phase = recovered.phases[-1]
    assert failed_phase.stage is UpdateOperationStage.CANDIDATE_SEARCH
    assert failed_phase.status == "failed"
    assert failed_phase.detail == "model_training_worker_process_tree_lost"
    events = M7AuditLog(paths).events()
    assert len(events) == 1
    assert events[0].event_type == "training_failed"
    assert events[0].run_id == queued.operation_id
    assert events[0].detail == {
        "reason": "model_training_worker_process_tree_lost",
        "stage": "candidate_search",
    }


def test_reconcile_interrupted_cancel_request_finishes_cancellation_once(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    coordinator = ModelTrainingCoordinator(
        paths,
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    queued, _ = coordinator.submit(bundles=("direction",), trigger="manual")
    coordinator.store.update(
        queued.operation_id,
        status=UpdateOperationStatus.RUNNING,
        stage=UpdateOperationStage.CANDIDATE_SEARCH,
        progress_percent=10,
        phase_status="running",
        phase_detail="正在执行 M7 direction 注册候选矩阵",
        started_at=datetime(2026, 8, 28, 23, 23, tzinfo=UTC),
    )
    coordinator.request_cancel(queued.operation_id)

    recovered = coordinator.reconcile_interrupted_operation()
    repeated = coordinator.reconcile_interrupted_operation()

    assert recovered is not None
    assert recovered.status is UpdateOperationStatus.CANCELLED
    assert recovered.cancelled_at is not None
    assert recovered.finished_at is not None
    assert repeated == recovered
    events = M7AuditLog(paths).events()
    assert len(events) == 1
    assert events[0].event_type == "training_cancelled"
    assert events[0].run_id == queued.operation_id


@pytest.mark.parametrize(
    "status",
    [UpdateOperationStatus.RUNNING, UpdateOperationStatus.CANCEL_REQUESTED],
)
def test_reconcile_interrupted_training_preserves_operation_while_writer_lease_is_live(
    tmp_path: Path,
    status: UpdateOperationStatus,
) -> None:
    """Catch startup reconciliation stealing a genuinely live training operation."""

    paths = LivePaths.from_workspace(tmp_path)
    coordinator = ModelTrainingCoordinator(
        paths,
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    queued, _ = coordinator.submit(bundles=("direction",), trigger="manual")
    coordinator.store.update(
        queued.operation_id,
        status=UpdateOperationStatus.RUNNING,
        stage=UpdateOperationStage.CANDIDATE_SEARCH,
        progress_percent=10,
        phase_status="running",
        phase_detail="正在执行 M7 direction 注册候选矩阵",
        started_at=datetime(2026, 8, 28, 23, 23, tzinfo=UTC),
    )
    if status is UpdateOperationStatus.CANCEL_REQUESTED:
        coordinator.request_cancel(queued.operation_id)
    expected = coordinator.latest()

    with LiveWriterLease(paths, task_id=queued.operation_id, timeout=0):
        recovered = coordinator.reconcile_interrupted_operation()

    assert recovered == expected
    assert coordinator.latest() == expected
    assert M7AuditLog(paths).events() == ()


def test_reconcile_interrupted_training_leaves_queued_operation_untouched(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    coordinator = ModelTrainingCoordinator(
        paths,
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    queued, _ = coordinator.submit(bundles=("direction",), trigger="manual")

    recovered = coordinator.reconcile_interrupted_operation()

    assert recovered == queued
    assert recovered.status is UpdateOperationStatus.QUEUED
    assert M7AuditLog(paths).events() == ()


def test_scheduler_worker_initialization_reconciles_training_before_ready(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    eod = EodUpdateCoordinator(paths)
    training = ModelTrainingCoordinator(
        paths,
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    promotion = ModelPromotionCoordinator(paths)
    queued, _ = training.submit(bundles=("direction",), trigger="manual")
    training.store.update(
        queued.operation_id,
        status=UpdateOperationStatus.RUNNING,
        stage=UpdateOperationStage.CANDIDATE_SEARCH,
        progress_percent=10,
        phase_status="running",
        phase_detail="正在执行 M7 direction 注册候选矩阵",
        started_at=datetime(2026, 8, 28, 23, 23, tzinfo=UTC),
    )

    live_scheduler._initialize_worker_state(eod, training, promotion)

    latest = training.latest()
    assert latest is not None
    assert latest.status is UpdateOperationStatus.FAILED
    assert eod.store.worker_status().state is EodWorkerState.READY
    assert training.store.worker_status().state is EodWorkerState.READY
    assert promotion.store.worker_status().state is EodWorkerState.READY


def test_scheduler_shutdown_reconciles_interrupted_training_before_offline(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    eod = EodUpdateCoordinator(paths)
    training = ModelTrainingCoordinator(
        paths,
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    promotion = ModelPromotionCoordinator(paths)
    queued, _ = training.submit(bundles=("direction",), trigger="manual")
    training.store.update(
        queued.operation_id,
        status=UpdateOperationStatus.RUNNING,
        stage=UpdateOperationStage.CANDIDATE_SEARCH,
        progress_percent=10,
        phase_status="running",
        phase_detail="正在执行 M7 direction 注册候选矩阵",
        started_at=datetime(2026, 8, 28, 23, 23, tzinfo=UTC),
    )

    live_scheduler._shutdown_worker_state(eod, training, promotion)

    latest = training.latest()
    assert latest is not None
    assert latest.status is UpdateOperationStatus.FAILED
    assert latest.error_code == "model_training_worker_process_tree_lost"
    assert eod.store.worker_status().state is EodWorkerState.OFFLINE
    assert training.store.worker_status().state is EodWorkerState.OFFLINE
    assert promotion.store.worker_status().state is EodWorkerState.OFFLINE


def test_standard_training_reports_completed_stages_and_publishes_snapshot(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    coordinator = ModelTrainingCoordinator(
        paths,
        trainer_factory=lambda: _TrainingStub(),
        publisher_factory=lambda: _PublisherStub(),
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    queued, _ = coordinator.submit()

    completed = coordinator.run_pending()

    assert completed is not None
    assert completed.operation_id == queued.operation_id
    assert completed.status is UpdateOperationStatus.SUCCEEDED_DATA_ONLY
    assert completed.progress_percent == 100
    phase_statuses = {phase.stage: phase.status for phase in completed.phases}
    assert phase_statuses[UpdateOperationStage.DATA_REPAIR] == "completed"
    assert phase_statuses[UpdateOperationStage.LABEL_HEALTH] == "completed"
    assert phase_statuses[UpdateOperationStage.CANDIDATE_SEARCH] == "completed"
    assert phase_statuses[UpdateOperationStage.OOS_VALIDATION] == "completed"
    assert phase_statuses[UpdateOperationStage.PROMOTION_WINDOW] == "completed"
    assert phase_statuses[UpdateOperationStage.CHAMPION_PUBLICATION] == "completed"
    assert (
        phase_statuses[UpdateOperationStage.FINAL_SNAPSHOT_PUBLICATION] == "completed"
    )


def test_standard_training_rolls_back_first_promotion_when_publication_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[str, str, int]] = []

    class _RegistryStub:
        def __init__(self, _root: Path) -> None:
            pass

        def deactivate_champion(
            self,
            *,
            task: str,
            horizon: int,
            reason: str,
            recorded_at: datetime,
        ) -> None:
            del reason, recorded_at
            calls.append(("deactivate", task, horizon))

        def rollback(self, **_kwargs) -> None:
            raise AssertionError("first promotion should deactivate, not roll back")

    class _FailingPublisher:
        def publish(self, *, progress=None):
            raise RuntimeError("snapshot write failed")

    monkeypatch.setattr(
        "stock_prediction.live.model_operations.LocalModelRegistry",
        _RegistryStub,
    )
    coordinator = ModelTrainingCoordinator(
        LivePaths.from_workspace(tmp_path),
        trainer_factory=lambda: _TrainingStub(promoted=True),
        publisher_factory=lambda: _FailingPublisher(),
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    coordinator.submit()

    failed = coordinator.run_pending()

    assert failed is not None
    assert failed.status is UpdateOperationStatus.FAILED
    assert ("deactivate", "return_quantiles", 5) in calls
    assert ("deactivate", "forecast_bundle", 20) in calls


def test_scheduler_submission_reuses_success_completed_after_daily_cutoff(
    tmp_path: Path,
) -> None:
    store = EodOperationStore(LivePaths.from_workspace(tmp_path))
    queued, _ = store.submit()
    finished_at = datetime(2026, 7, 28, 20, 30, tzinfo=UTC)
    completed = queued.model_copy(
        update={
            "status": UpdateOperationStatus.SUCCEEDED_DATA_ONLY,
            "progress_percent": 100,
            "target_session": date(2026, 7, 28),
            "actual_cutoff": date(2026, 7, 28),
            "finished_at": finished_at,
            "updated_at": finished_at,
        }
    )
    store.save(completed)

    reused, created = store.submit(
        completed_not_before=datetime(2026, 7, 28, 20, 20, tzinfo=UTC),
    )

    assert created is False
    assert reused.operation_id == completed.operation_id


def test_operation_api_returns_202_and_restores_latest(tmp_path: Path) -> None:
    store = EodOperationStore(LivePaths.from_workspace(tmp_path))
    store.heartbeat()
    with TestClient(create_app(operation_controller=store)) as client:
        submitted = client.post("/api/v1/operations/eod-update")
        latest = client.get("/api/v1/operations/eod-update/latest")

    assert submitted.status_code == 202
    assert latest.status_code == 200
    assert latest.json()["operation_id"] == submitted.json()["operation_id"]


def test_operation_api_rejects_new_queue_when_worker_is_offline(
    tmp_path: Path,
) -> None:
    store = EodOperationStore(LivePaths.from_workspace(tmp_path))
    with TestClient(create_app(operation_controller=store)) as client:
        worker = client.get("/api/v1/operations/eod-update/worker")
        submitted = client.post("/api/v1/operations/eod-update")

    assert worker.status_code == 200
    assert worker.json()["state"] == "offline"
    assert submitted.status_code == 503
    assert submitted.json()["error_code"] == "eod_worker_unavailable"
    assert store.latest() is None


def test_worker_heartbeat_transitions_from_ready_to_stale_and_recovers(
    tmp_path: Path,
) -> None:
    store = EodOperationStore(LivePaths.from_workspace(tmp_path))
    heartbeat_at = datetime(2026, 7, 28, 22, 15, tzinfo=UTC)

    store.heartbeat(now=heartbeat_at)

    assert store.worker_status(now=heartbeat_at).state is EodWorkerState.READY
    assert (
        store.worker_status(now=heartbeat_at + timedelta(seconds=11)).state
        is EodWorkerState.STALE
    )
    assert (
        store.heartbeat(now=heartbeat_at + timedelta(seconds=12)).state
        is EodWorkerState.READY
    )


def test_latest_operation_is_nullable_when_queue_has_no_prior_run() -> None:
    with TestClient(create_app(operation_controller=None)) as client:
        latest = client.get("/api/v1/operations/eod-update/latest")
        worker = client.get("/api/v1/operations/eod-update/worker")
        submitted = client.post("/api/v1/operations/eod-update")

    assert latest.status_code == 200
    assert latest.json() is None
    assert worker.status_code == 200
    assert worker.json()["state"] == "offline"
    assert submitted.status_code == 503


def test_repeated_running_progress_preserves_phase_start_time(tmp_path: Path) -> None:
    store = EodOperationStore(LivePaths.from_workspace(tmp_path))
    operation, _ = store.submit()
    first = store.update(
        operation.operation_id,
        status=UpdateOperationStatus.RUNNING,
        stage=UpdateOperationStage.INCREMENTAL_COLLECTION,
        progress_percent=12,
        phase_status="running",
        phase_detail="1d 采集 10/100",
    )
    second = store.update(
        operation.operation_id,
        status=UpdateOperationStatus.RUNNING,
        stage=UpdateOperationStage.INCREMENTAL_COLLECTION,
        progress_percent=18,
        phase_status="running",
        phase_detail="1d 采集 55/100",
    )

    assert first.phases[0].started_at == second.phases[0].started_at
    assert second.phases[0].detail == "1d 采集 55/100"


def test_targeted_stage_persists_active_trial_and_latest_candidate_detail(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    coordinator = ModelTrainingCoordinator(
        paths,
        readiness_provider=_readiness_provider(),
        fingerprint_provider=_fingerprint_provider,
    )
    operation, _ = coordinator.submit(bundles=("direction",), trigger="manual")

    coordinator._stage(
        operation.operation_id,
        UpdateOperationStage.CANDIDATE_SEARCH,
        12,
        "候选 13/280 · outer 1/4 · H1 · CPU CatBoost",
        active_trial_id="m7-outer-01.m7-catboost_multiclass-01.h1",
    )

    current = coordinator.latest()
    assert current is not None
    assert current.progress_percent == 12
    assert current.active_trial_id == "m7-outer-01.m7-catboost_multiclass-01.h1"
    assert current.phases[-1].detail == "候选 13/280 · outer 1/4 · H1 · CPU CatBoost"


class _Service:
    def __init__(self, end: date = date(2026, 7, 24)) -> None:
        self.end = end
        self.refresh_calls = 0

    def preflight(self) -> LivePreflightResult:
        return LivePreflightResult(
            ready=True,
            universe_version=SEED_UNIVERSE_VERSION,
            symbol_count=_ACTIVE_COUNT,
            daily_start=date(2018, 1, 1),
            intraday_start=date(2024, 7, 24),
            end=self.end,
            opend_version="10.8.6808",
            history_quota_total=_ACTIVE_COUNT,
            history_quota_used=_ACTIVE_COUNT,
            history_quota_remaining=0,
            new_history_symbols=0,
            subscription_total=_ACTIVE_COUNT,
            subscription_used=0,
            subscription_remaining=_ACTIVE_COUNT,
            disk_free_bytes=10_000_000_000,
            existing_daily_symbols=_ACTIVE_COUNT,
            existing_intraday_symbols=_ACTIVE_COUNT,
            blockers=(),
            trading_days=((date(2026, 7, 24), "WHOLE"),),
        )

    def refresh(self, progress_callback: object = None) -> dict[str, object]:
        self.refresh_calls += 1
        return {"tasks_committed": 200, "tasks_failed": 0}


class _Storage:
    def common_daily_cutoff(self, _symbols: object) -> tuple[date, int]:
        return date(2026, 7, 24), _ACTIVE_COUNT

    def coverage(self) -> list[dict[str, object]]:
        return [{"symbol": symbol, "timeframe": "60m"} for symbol in SEED_UNIVERSE]

    def latest_health(self) -> dict[str, object]:
        return {"connection_status": "connected"}

    def lag_sessions(self, _actual: date | None, _expected: date) -> int:
        return 0

    def record_health(self, payload: dict[str, object]) -> None:
        self.health = payload


class _SequenceStorage(_Storage):
    def __init__(self, cutoffs: list[tuple[date | None, int]]) -> None:
        self.cutoffs = cutoffs
        self.cutoff_calls = 0

    def common_daily_cutoff(self, _symbols: object) -> tuple[date | None, int]:
        index = min(self.cutoff_calls, len(self.cutoffs) - 1)
        self.cutoff_calls += 1
        return self.cutoffs[index]


class _AdvancingClock:
    def __init__(self, value: datetime) -> None:
        self.value = value
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += timedelta(seconds=seconds)


class _FailingRefreshService(_Service):
    def refresh(self, progress_callback: object = None) -> dict[str, object]:
        del progress_callback
        self.refresh_calls += 1
        raise RuntimeError("opend_refresh_failed")


class _Features:
    def __init__(self, path: Path) -> None:
        self.path = path

    def build(self) -> LiveFeatureArtifact:
        now = datetime(2026, 7, 24, 20, tzinfo=UTC)
        return LiveFeatureArtifact(
            universe_version=SEED_UNIVERSE_VERSION,
            feature_set_version="live-features-v1",
            dataset_revision="opend-live-market-bars-v1",
            input_hash="a" * 64,
            generated_at=now,
            as_of=now,
            row_count=_ACTIVE_COUNT,
            path=self.path,
            reference=ArtifactReference(
                artifact_id="a" * 64,
                relative_path=self.path.name,
                sha256="b" * 64,
                schema_version="live-feature-artifact-v1",
                created_at=now,
            ),
        )


@pytest.mark.parametrize(
    "stage, lower, upper",
    [
        (UpdateOperationStage.MARKET_SNAPSHOT_PUBLICATION, 50, 54),
        (UpdateOperationStage.FINAL_SNAPSHOT_PUBLICATION, 90, 98),
    ],
)
def test_publication_reports_real_work_without_finishing_early(
    tmp_path, monkeypatch, stage, lower, upper
):
    from types import SimpleNamespace
    from itertools import count

    writes = []
    ticks = count(0, 2)
    monkeypatch.setattr(
        "stock_prediction.live.operations.time.monotonic", lambda: next(ticks)
    )

    class Publisher:
        def publish(self, *, progress):
            for step in (
                "prepare",
                "formal",
                "verified",
                "candidate",
                "tables",
                "materialize",
            ):
                for done in range(4):
                    progress(step, done, 3)
            return {"snapshot_id": "isolated-snapshot"}

    coordinator = EodUpdateCoordinator(
        LivePaths.from_workspace(tmp_path), publisher_factory=Publisher
    )
    coordinator.store = SimpleNamespace(
        update=lambda operation_id, **fields: writes.append(fields)
    )
    assert coordinator._publish("test-operation", stage) == {
        "snapshot_id": "isolated-snapshot"
    }
    percentages = [write["progress_percent"] for write in writes]
    assert percentages == sorted(percentages)
    assert percentages[0] == lower
    assert percentages[-1] == upper
    assert len(set(percentages)) > 1
    assert all(write["phase_status"] == "running" for write in writes)
    assert any(
        write["phase_detail"] == "snapshot_publication:candidate:2/3"
        for write in writes
    )


class _Publisher:
    def publish(self, *, progress=None) -> dict[str, object]:
        return {
            "prediction_readiness": "data_only",
            "snapshot_id": "live-20260724-data",
            "path": "live-20260724-data",
        }


class _Trainer:
    def train(self) -> dict[str, object]:
        return {
            "run_id": "run-1",
            "production_gate_complete": True,
            "all_gates_passed": False,
            "automatic_promotion": "kept_current",
        }


class _ShadowRunner:
    def __init__(self) -> None:
        self.generated_at: list[datetime] = []
        self.matured_at: list[datetime] = []
        self.snapshot_paths: list[Path] = []

    def run_due(
        self,
        *,
        generated_at: datetime,
        incumbent_snapshot_path: Path,
    ) -> tuple[object, ...]:
        self.generated_at.append(generated_at)
        self.snapshot_paths.append(incumbent_snapshot_path)
        return ()

    def run_mature_labels(self, *, generated_at: datetime) -> tuple[object, ...]:
        self.matured_at.append(generated_at)
        return ()


class _DriftRunner:
    def __init__(self) -> None:
        self.generated_at: list[datetime] = []

    def run(self, *, generated_at: datetime) -> object:
        self.generated_at.append(generated_at)
        return object()


class _FormalRunner:
    def __init__(self) -> None:
        self.generated_at: list[datetime] = []

    def run(self, *, generated_at: datetime) -> object:
        self.generated_at.append(generated_at)
        return object()


def test_coordinator_runs_shadow_inference_after_final_publication(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    shadow = _ShadowRunner()
    drift = _DriftRunner()
    formal = _FormalRunner()
    now = datetime(2026, 7, 24, 21, tzinfo=UTC)
    coordinator = EodUpdateCoordinator(
        paths,
        service_factory=_Service,
        feature_factory=lambda: _Features(tmp_path / "features.parquet"),
        publisher_factory=_Publisher,
        trainer_factory=_Trainer,
        shadow_factory=lambda: shadow,
        drift_factory=lambda: drift,
        formal_factory=lambda: formal,
        clock=lambda: now,
    )
    coordinator.storage = _Storage()  # type: ignore[assignment]
    coordinator.submit()

    completed = coordinator.run_pending()

    assert completed is not None
    assert completed.status is UpdateOperationStatus.SUCCEEDED_DATA_ONLY
    assert shadow.generated_at == [now]
    assert shadow.snapshot_paths == [Path("live-20260724-data")]
    assert shadow.matured_at == [now]
    assert drift.generated_at == [now]
    assert formal.generated_at == [now]


def test_coordinator_completes_data_only_when_model_gate_fails(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    coordinator = EodUpdateCoordinator(
        paths,
        service_factory=_Service,
        feature_factory=lambda: _Features(tmp_path / "features.parquet"),
        publisher_factory=_Publisher,
        trainer_factory=_Trainer,
    )
    coordinator.storage = _Storage()  # type: ignore[assignment]
    submitted = coordinator.submit()

    completed = coordinator.run_pending()

    assert completed is not None
    assert completed.operation_id == submitted.operation_id
    assert completed.status is UpdateOperationStatus.SUCCEEDED_DATA_ONLY
    assert completed.progress_percent == 100
    assert len(completed.phases) == 10
    assert completed.automatic_promotion == "kept_current"


def test_coordinator_reuses_unchanged_hash_without_model_only_stages(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    coordinator = EodUpdateCoordinator(
        paths,
        service_factory=_Service,
        feature_factory=lambda: _Features(tmp_path / "features.parquet"),
        publisher_factory=_Publisher,
        trainer_factory=_Trainer,
    )
    coordinator.storage = _Storage()  # type: ignore[assignment]
    coordinator.submit()
    first = coordinator.run_pending()
    second_submitted = coordinator.submit()

    second = coordinator.run_pending()

    assert first is not None
    assert second is not None
    assert second.operation_id == second_submitted.operation_id
    assert second.operation_id != first.operation_id
    assert second.status is UpdateOperationStatus.SUCCEEDED_DATA_ONLY
    assert second.no_change is True
    assert second.progress_percent == 100
    assert second.snapshot_id == first.snapshot_id
    assert second.error_code is None
    assert [phase.stage for phase in second.phases] == [
        UpdateOperationStage.OPEND_PREFLIGHT,
        UpdateOperationStage.INCREMENTAL_COLLECTION,
        UpdateOperationStage.DATA_QUALITY,
        UpdateOperationStage.FEATURE_BUILD,
        UpdateOperationStage.MARKET_SNAPSHOT_PUBLICATION,
        UpdateOperationStage.MODEL_TRAINING,
        UpdateOperationStage.PROMOTION_GATE,
        UpdateOperationStage.CHAMPION_SWITCH,
        UpdateOperationStage.BATCH_INFERENCE,
        UpdateOperationStage.FINAL_SNAPSHOT_PUBLICATION,
    ]


@pytest.mark.parametrize(
    "first_incomplete",
    [
        (date(2026, 7, 23), _ACTIVE_COUNT),
        (date(2026, 7, 24), _ACTIVE_COUNT - 1),
    ],
)
def test_coordinator_retries_incomplete_daily_data_and_recovers(
    tmp_path: Path,
    first_incomplete: tuple[date, int],
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    service = _Service()
    clock = _AdvancingClock(datetime(2026, 7, 24, 20, 11, tzinfo=UTC))
    coordinator = EodUpdateCoordinator(
        paths,
        service_factory=lambda: service,
        feature_factory=lambda: _Features(tmp_path / "features.parquet"),
        publisher_factory=_Publisher,
        trainer_factory=_Trainer,
        clock=clock,
        sleeper=clock.sleep,
    )
    coordinator.storage = _SequenceStorage(  # type: ignore[assignment]
        [
            (date(2026, 7, 23), _ACTIVE_COUNT),
            first_incomplete,
            (date(2026, 7, 24), _ACTIVE_COUNT),
        ]
    )
    coordinator.submit()

    completed = coordinator.run_pending()

    assert completed is not None
    assert completed.status is UpdateOperationStatus.SUCCEEDED_DATA_ONLY
    assert completed.previous_cutoff == date(2026, 7, 23)
    assert completed.actual_cutoff == date(2026, 7, 24)
    assert completed.session_advanced is True
    assert completed.retry_attempt == 1
    assert completed.retry_deadline_at == datetime(2026, 7, 24, 20, 26, tzinfo=UTC)
    assert completed.next_retry_at is None
    assert clock.sleeps == [60.0]
    assert service.refresh_calls == 2


def test_coordinator_times_out_daily_retry_after_fifteen_minutes(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    service = _Service()
    clock = _AdvancingClock(datetime(2026, 7, 24, 20, 11, tzinfo=UTC))
    coordinator = EodUpdateCoordinator(
        paths,
        service_factory=lambda: service,
        clock=clock,
        sleeper=clock.sleep,
    )
    coordinator.storage = _SequenceStorage(  # type: ignore[assignment]
        [
            (date(2026, 7, 23), _ACTIVE_COUNT),
            (date(2026, 7, 23), _ACTIVE_COUNT),
        ]
    )
    coordinator.submit()

    failed = coordinator.run_pending()

    assert failed is not None
    assert failed.status is UpdateOperationStatus.FAILED
    assert failed.error_code == "daily_cutoff_incomplete"
    assert failed.previous_cutoff == date(2026, 7, 23)
    assert failed.actual_cutoff == date(2026, 7, 23)
    assert failed.retry_attempt == 15
    assert failed.retry_deadline_at == datetime(2026, 7, 24, 20, 26, tzinfo=UTC)
    assert failed.next_retry_at is None
    assert len(clock.sleeps) == 15
    assert service.refresh_calls == 16


def test_coordinator_fails_non_retryable_error_immediately(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    service = _FailingRefreshService()
    clock = _AdvancingClock(datetime(2026, 7, 24, 20, 11, tzinfo=UTC))
    coordinator = EodUpdateCoordinator(
        paths,
        service_factory=lambda: service,
        clock=clock,
        sleeper=clock.sleep,
    )
    coordinator.storage = _Storage()  # type: ignore[assignment]
    coordinator.submit()

    failed = coordinator.run_pending()

    assert failed is not None
    assert failed.status is UpdateOperationStatus.FAILED
    assert failed.error_code == "opend_refresh_failed"
    assert failed.retry_attempt == 0
    assert failed.retry_deadline_at is None
    assert clock.sleeps == []
    assert service.refresh_calls == 1


@pytest.mark.parametrize(
    ("previous_cutoff", "expected_advanced"),
    [
        (date(2026, 7, 30), True),
        (date(2026, 7, 31), False),
    ],
)
def test_coordinator_reports_whether_the_trading_session_advanced(
    tmp_path: Path,
    previous_cutoff: date,
    expected_advanced: bool,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    service = _Service(end=date(2026, 7, 31))
    coordinator = EodUpdateCoordinator(
        paths,
        service_factory=lambda: service,
        feature_factory=lambda: _Features(tmp_path / "features.parquet"),
        publisher_factory=_Publisher,
        trainer_factory=_Trainer,
    )
    coordinator.storage = _SequenceStorage(  # type: ignore[assignment]
        [
            (previous_cutoff, _ACTIVE_COUNT),
            (date(2026, 7, 31), _ACTIVE_COUNT),
        ]
    )
    coordinator.submit()

    completed = coordinator.run_pending()

    assert completed is not None
    assert completed.status is UpdateOperationStatus.SUCCEEDED_DATA_ONLY
    assert completed.previous_cutoff == previous_cutoff
    assert completed.actual_cutoff == date(2026, 7, 31)
    assert completed.session_advanced is expected_advanced
