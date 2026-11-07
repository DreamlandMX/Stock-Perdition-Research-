from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_integrity import (
    M7IntegrityMonitor,
    M7ServingObservation,
)
from stock_prediction.live.m7_promotion import (
    M7ForecastBundleComponent,
    M7ForecastBundleManifest,
    M7PreservedLegacyEvidence,
    load_active_m7_forecast_bundle,
)
from stock_prediction.modeling.report import deterministic_hash, write_immutable_json


def _manifest(
    *, package_id: str, package_hash: str, previous_hash: str | None
) -> M7ForecastBundleManifest:
    preserved = M7PreservedLegacyEvidence(
        source_run_id="live-train-20260814T211738Z-r3-a7569373",
        report_relative_path=(
            "models/runs/live-train-20260814T211738Z-r3-a7569373/training-report.json"
        ),
        report_sha256="7" * 64,
        component_set_sha256="8" * 64,
        component_count=13,
        universe_version="m1-seed-2026-07-11-v1",
        dataset_revision="opend-live-market-bars-pit-adjusted-v2",
        feature_set_version="live-daily-features-v2",
        label_version="live-labels-v2",
    )
    component = M7ForecastBundleComponent(
        bundle_id="direction",
        package_id=package_id,
        package_sha256=package_hash,
        training_run_id="run-1",
        blind_window_id="window-1",
        blind_report_hash="b" * 64,
        previous_incumbent_id="legacy-v1",
    )
    base = {
        "schema_version": "m7-forecast-bundle-v1",
        "previous_bundle_sha256": previous_hash,
        "promoted_bundle_id": "direction",
        "components": (component.model_dump(mode="json"),),
        "preserved_legacy_component_count": 13,
        "preserved_legacy_evidence": preserved.model_dump(mode="json"),
        "compatibility_signature": "c" * 64,
        "approved_by": "research-owner",
        "promoted_at": "2026-12-02T15:00:00Z",
    }
    bundle_hash = deterministic_hash(base)
    return M7ForecastBundleManifest.model_validate(
        {
            **base,
            "forecast_bundle_id": f"m7-forecast-{bundle_hash[:16]}",
            "bundle_sha256": bundle_hash,
        },
        strict=True,
    )


def _activate(
    paths: LivePaths, manifests: tuple[M7ForecastBundleManifest, ...]
) -> None:
    root = paths.models / "m7-forecast-bundles"
    for manifest in manifests:
        write_immutable_json(
            root / "manifests" / f"{manifest.bundle_sha256}.json",
            manifest.model_dump(mode="json"),
            pretty=False,
        )
    active = manifests[-1]
    write_immutable_json(
        root / "ACTIVE.json",
        {
            "schema_version": "m7-active-forecast-bundle-v1",
            "forecast_bundle_id": active.forecast_bundle_id,
            "bundle_sha256": active.bundle_sha256,
            "previous_bundle_sha256": active.previous_bundle_sha256,
        },
        pretty=False,
    )


def test_integrity_failure_restores_exact_previous_pointer_and_records_incident(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    previous = _manifest(
        package_id="direction-v1", package_hash="1" * 64, previous_hash=None
    )
    failed = _manifest(
        package_id="direction-v2",
        package_hash="2" * 64,
        previous_hash=previous.bundle_sha256,
    )
    _activate(paths, (previous, failed))

    monitor = M7IntegrityMonitor(
        paths,
        manifest_verifier=lambda _manifest: (_ for _ in ()).throw(
            RuntimeError("model_hash_mismatch")
        ),
    )
    result = monitor.verify_active(observed_at=datetime(2026, 12, 3, tzinfo=UTC))

    assert result is not None
    assert result.action == "rolled_back"
    assert result.restored_bundle_sha256 == previous.bundle_sha256
    assert load_active_m7_forecast_bundle(paths) == previous
    incident = json.loads(result.incident_path.read_text(encoding="utf-8"))
    assert incident["failure_class"] == "integrity"
    assert incident["failed_bundle_sha256"] == failed.bundle_sha256


def test_performance_drift_is_alert_only_and_never_changes_pointer(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    active = _manifest(
        package_id="direction-v1", package_hash="1" * 64, previous_hash=None
    )
    _activate(paths, (active,))
    monitor = M7IntegrityMonitor(paths, manifest_verifier=lambda _manifest: None)

    event = monitor.record_performance_drift(
        bundle_id="direction",
        reason="relative_brier_degradation_above_5_percent",
        observed_at=datetime(2026, 12, 3, tzinfo=UTC),
    )

    assert load_active_m7_forecast_bundle(paths) == active
    assert json.loads(event.read_text("utf-8"))["action"] == "alert_only"


def test_invalid_probability_observation_triggers_integrity_rollback(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    previous = _manifest(
        package_id="direction-v1", package_hash="1" * 64, previous_hash=None
    )
    active = _manifest(
        package_id="direction-v2",
        package_hash="2" * 64,
        previous_hash=previous.bundle_sha256,
    )
    _activate(paths, (previous, active))
    monitor = M7IntegrityMonitor(paths, manifest_verifier=lambda _manifest: None)
    observation = M7ServingObservation(
        bundle_id="direction",
        package_sha256="2" * 64,
        universe_version="m1-seed-2026-07-11-v1",
        expected_symbol_count=100,
        observed_symbol_count=100,
        direction_probabilities=((0.2, 0.2, 0.2),) * 100,
        range_probability_5d=None,
        range_probability_20d=None,
        prediction_artifact_hash="d" * 64,
    )

    result = monitor.verify_observation(
        observation, observed_at=datetime(2026, 12, 3, tzinfo=UTC)
    )

    assert result is not None
    assert result.action == "rolled_back"
    assert load_active_m7_forecast_bundle(paths) == previous
