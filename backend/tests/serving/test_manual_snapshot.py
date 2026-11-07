from datetime import UTC, datetime

from backend.tests.serving.test_manifest import snapshot, tables
from stock_prediction.contracts.serving import ResearchReadiness, ResolvedSnapshot
from stock_prediction.serving.manifest import ResearchSnapshotManifest
from stock_prediction.live.publication import _model_rows
from stock_prediction.operations.registry import LocalModelRegistry


def test_manual_snapshot_round_trip_does_not_claim_quality_verification() -> None:
    value = ResolvedSnapshot.model_validate(
        {
            **snapshot().model_dump(mode="python"),
            "activation_basis": "manual_override",
            "research_readiness": ResearchReadiness.MANUAL_UNVERIFIED,
        },
        strict=True,
    )
    manifest = ResearchSnapshotManifest.create(
        snapshot=value,
        generated_at=datetime(2026, 7, 10, 20, 2, tzinfo=UTC),
        tables=tables(),
    )
    loaded = ResearchSnapshotManifest.model_validate_json(manifest.model_dump_json())
    assert (
        loaded.resolved_snapshot().research_readiness
        is ResearchReadiness.MANUAL_UNVERIFIED
    )
    assert loaded.resolved_snapshot().activation_basis == "manual_override"
    assert loaded.verify_manifest_hash() is None


def test_automatic_snapshot_keeps_legacy_manifest_shape() -> None:
    value = ResearchSnapshotManifest.create(
        snapshot=snapshot(),
        generated_at=datetime(2026, 7, 10, 20, 2, tzinfo=UTC),
        tables=tables(),
    )
    assert "activation_basis" not in value.model_dump(mode="json")
    assert (
        value.resolved_snapshot().research_readiness is ResearchReadiness.FULL_VERIFIED
    )


def test_manual_component_keeps_its_failed_quality_result(tmp_path) -> None:
    value = ResolvedSnapshot.model_validate(
        {
            **snapshot().model_dump(mode="python"),
            "activation_basis": "manual_override",
            "research_readiness": ResearchReadiness.MANUAL_UNVERIFIED,
        }
    )
    lifecycle = LocalModelRegistry(tmp_path / "registry").snapshot(
        "forecast_bundle", 20
    )
    rows = _model_rows(
        value,
        lifecycle,
        {
            "model_version": "candidate-v1",
            "run_id": "run-v1",
            "calibration_version": "cal-v1",
            "component_releases": [],
            "tasks": [
                {
                    "task": "forecast_direction",
                    "horizon": 1,
                    "gate_passed": False,
                    "statistical_gate_passed": False,
                }
            ],
        },
        manual_components={("forecast_direction", 1): "manual-component-v1"},
    )
    assert rows[0].champion_version == "manual-component-v1"
    assert rows[0].status == "champion"
    assert rows[0].gate_passed is False
    assert rows[0].statistical_gate_passed is False
