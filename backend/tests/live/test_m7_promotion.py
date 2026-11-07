from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from stock_prediction.contracts.operations import ModelPromotionRequest
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_promotion import (
    M7PreservedLegacyEvidence,
    M7PromotionError,
    M7PromotionService,
    load_active_m7_forecast_bundle,
    load_m7_preserved_legacy_evidence,
)
from stock_prediction.modeling.m7_package import (
    M7PackageArtifactInput,
    M7PackageBuildSpec,
    M7RuntimeCompatibility,
    publish_m7_package,
)
from stock_prediction.modeling.report import write_immutable_json
from stock_prediction.operations.registry import LocalModelRegistry


RUNTIME = M7RuntimeCompatibility(
    python_version="3.11",
    platform="windows",
    architecture="x86_64",
    numpy_version="2.4.2",
    scikit_learn_version="1.9.0",
    catboost_version="1.2.10",
    torch_version="2.8.0",
    inference_devices=("cpu",),
)


def _preserved_evidence() -> M7PreservedLegacyEvidence:
    return M7PreservedLegacyEvidence(
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


def _write_preserved_baseline(paths: LivePaths, tmp_path: Path) -> Path:
    preserved_keys = {
        *(
            (task, horizon)
            for task in ("breakout", "return_fwd", "mfe", "mae")
            for horizon in (1, 5, 20)
        ),
        ("range_exit", 5),
    }
    target_keys = {
        *(("forecast_direction", horizon) for horizon in (1, 5, 20)),
        ("range_exit", 20),
    }
    registry = LocalModelRegistry(paths.models / "registry")
    tasks: list[dict[str, object]] = []
    releases: list[dict[str, object]] = []
    for task, horizon in sorted(preserved_keys | target_keys):
        passed = (task, horizon) in preserved_keys
        model_version = f"legacy-{task.replace('_', '-')}-h{horizon}"
        release_hash = "0" * 64
        if passed:
            artifact = tmp_path / f"{model_version}.bin"
            artifact.write_bytes(model_version.encode("utf-8"))
            registered = registry.register_release(
                model_version=model_version,
                task=task,
                horizon=horizon,
                calibration_version="legacy-cal-v1",
                source_decision_id="legacy-run",
                promotion_eligible=False,
                artifacts={"component-package.bin": artifact},
                registered_at=datetime(2026, 8, 14, tzinfo=UTC),
            )
            release_hash = registered.manifest_hash
        tasks.append({"task": task, "horizon": horizon, "gate_passed": passed})
        releases.append(
            {
                "task": task,
                "horizon": horizon,
                "model_version": model_version,
                "calibration_version": "legacy-cal-v1",
                "gate_passed": passed,
                "lifecycle_status": "challenger",
                "previous_champion_version": None,
                "release_hash": release_hash,
            }
        )
    report = {
        "run_id": "live-train-20260814T211738Z-r3-a7569373",
        "universe_version": "m1-seed-2026-07-11-v1",
        "dataset_revision": "opend-live-market-bars-pit-adjusted-v2",
        "feature_set_version": "live-daily-features-v2",
        "label_version": "live-labels-v2",
        "tasks": tasks,
        "component_releases": releases,
    }
    target = (
        paths.models
        / "runs"
        / "live-train-20260814T211738Z-r3-a7569373"
        / "training-report.json"
    )
    write_immutable_json(target, report, pretty=False)
    return target


def test_preserved_legacy_evidence_binds_exact_verified_thirteen(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    target = _write_preserved_baseline(paths, tmp_path)

    evidence = load_m7_preserved_legacy_evidence(paths)

    assert evidence.component_count == 13
    assert evidence.report_relative_path.endswith("training-report.json")
    assert evidence.report_sha256 != evidence.component_set_sha256
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["component_releases"][0]["release_hash"] = "f" * 64
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(M7PromotionError, match="release_mismatch"):
        load_m7_preserved_legacy_evidence(paths)


def _publish_direction(paths: LivePaths, tmp_path: Path) -> tuple[str, str]:
    source = tmp_path / "source"
    source.mkdir()
    values = {
        "thresholds": ("label_threshold", b"thresholds"),
        "model-h1": ("model", b"one"),
        "model-h5": ("model", b"five"),
        "model-h20": ("model", b"twenty"),
        "calibration": ("calibration", b"calibration"),
        "explanation": ("explanation", b"explanation"),
    }
    artifacts = []
    for artifact_id, (category, payload) in values.items():
        path = source / f"{artifact_id}.bin"
        path.write_bytes(payload)
        artifacts.append(
            M7PackageArtifactInput(
                artifact_id=artifact_id,
                category=category,
                source_path=path,
                schema_version="m7-test-v1",
            )
        )
    published = publish_m7_package(
        paths.models / "m7-packages",
        build_spec=M7PackageBuildSpec(
            package_id="m7-direction-shadow-001",
            bundle_id="direction",
            recipe_id="candidate-recipe-seed100-targeted-v4",
            recipe_generation=4,
            code_commit="a" * 40,
            universe_version="m1-seed-2026-07-11-v1",
            dataset_revision="opend-live-market-bars-pit-adjusted-v2",
            feature_set_version="live-daily-features-v5-seed100",
            feature_input_hash="b" * 64,
            label_version="live-labels-v2",
            split_manifest_hash="c" * 64,
            candidate_matrix_hash="d" * 64,
            dependency_lock_hash="e" * 64,
            runtime_compatibility=RUNTIME,
            evidence_scope="optimization_reused",
            blind_window_id=None,
            created_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
        ),
        artifacts=tuple(artifacts),
    )
    return published.manifest.package_id, published.manifest.package_sha256


def _publish_range(paths: LivePaths, tmp_path: Path) -> tuple[str, str]:
    source = tmp_path / "range-source"
    source.mkdir()
    artifacts = []
    for artifact_id, category in (
        ("thresholds", "label_threshold"),
        ("model-h20", "model"),
        ("calibration", "calibration"),
        ("explanation", "explanation"),
    ):
        path = source / f"{artifact_id}.bin"
        path.write_bytes(artifact_id.encode("utf-8"))
        artifacts.append(
            M7PackageArtifactInput(
                artifact_id=artifact_id,
                category=category,
                source_path=path,
                schema_version="m7-test-v1",
            )
        )
    published = publish_m7_package(
        paths.models / "m7-packages",
        build_spec=M7PackageBuildSpec(
            package_id="m7-range-shadow-001",
            bundle_id="range_exit_20",
            recipe_id="candidate-recipe-seed100-targeted-v4",
            recipe_generation=4,
            code_commit="a" * 40,
            universe_version="m1-seed-2026-07-11-v1",
            dataset_revision="opend-live-market-bars-pit-adjusted-v2",
            feature_set_version="live-daily-features-v5-seed100",
            feature_input_hash="b" * 64,
            label_version="live-labels-v2",
            split_manifest_hash="c" * 64,
            candidate_matrix_hash="d" * 64,
            dependency_lock_hash="e" * 64,
            runtime_compatibility=RUNTIME,
            evidence_scope="optimization_reused",
            blind_window_id=None,
            created_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
        ),
        artifacts=tuple(artifacts),
    )
    return published.manifest.package_id, published.manifest.package_sha256


def _evidence(
    paths: LivePaths, *, package_id: str, package_hash: str
) -> tuple[Path, ModelPromotionRequest]:
    run_id = "run-targeted-vnext-1"
    window_id = "promotion-seed100-aaaaaaaaaaaa-v1"
    report = {
        "schema_version": "m7-targeted-training-report-v1",
        "run_id": run_id,
        "bundle_results": {
            "direction": {
                "bundle_id": "direction",
                "package_id": package_id,
                "package_sha256": package_hash,
                "blind_window_id": window_id,
                "incumbent_id": "direction-incumbent-v1",
            }
        },
    }
    report_path = paths.models / "runs" / run_id / "training-report.json"
    write_immutable_json(report_path, report, pretty=False)
    blind = {
        "schema_version": "m7-blind-report-v1",
        "window_id": window_id,
        "bundle_id": "direction",
        "package_sha256": package_hash,
        "manifest_hash": "f" * 64,
        "registration_hash": "1" * 64,
        "generated_at": "2026-12-01T21:00:00Z",
        "evidence_scope": "blind_complete",
        "available_anchor_count": 77,
        "mature_label_count": 77,
        "decision": {
            "bundle_id": "direction",
            "status": "awaiting_manual_approval",
            "failed_gates": [],
            "metrics": {"evidence_scope": "blind_complete"},
            "per_horizon": {"1": {}, "5": {}, "20": {}},
        },
    }
    blind_path = tmp_blind = paths.models / "test-evidence" / "blind.json"
    write_immutable_json(tmp_blind, blind, pretty=False)
    request = ModelPromotionRequest(
        training_run_id=run_id,
        bundle_id="direction",
        expected_package_sha256=package_hash,
        expected_incumbent_id="direction-incumbent-v1",
        approved_by="research-owner",
    )
    return blind_path, request


def test_manual_promotion_validates_evidence_and_cas_switches_pointer(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    package_id, package_hash = _publish_direction(paths, tmp_path)
    blind_path, request = _evidence(
        paths, package_id=package_id, package_hash=package_hash
    )
    incumbent = {"value": "direction-incumbent-v1"}
    service = M7PromotionService(
        paths,
        current_runtime=RUNTIME,
        incumbent_provider=lambda _bundle: incumbent["value"],
        blind_report_resolver=lambda _window: blind_path,
        preserved_evidence_provider=_preserved_evidence,
    )

    result = service.promote(
        request,
        promoted_at=datetime(2026, 12, 2, 15, tzinfo=UTC),
    )
    active = load_active_m7_forecast_bundle(paths)

    assert result.component.package_id == package_id
    assert result.approved_by == "research-owner"
    assert active is not None
    assert active.bundle_sha256 == result.bundle_sha256
    assert active.components[0].bundle_id == "direction"
    assert active.preserved_legacy_component_count == 13
    assert active.preserved_legacy_evidence == _preserved_evidence()


def test_manual_promotion_rejects_stale_incumbent_without_pointer_change(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    package_id, package_hash = _publish_direction(paths, tmp_path)
    blind_path, request = _evidence(
        paths, package_id=package_id, package_hash=package_hash
    )
    service = M7PromotionService(
        paths,
        current_runtime=RUNTIME,
        incumbent_provider=lambda _bundle: "direction-incumbent-v2",
        blind_report_resolver=lambda _window: blind_path,
        preserved_evidence_provider=_preserved_evidence,
    )

    with pytest.raises(M7PromotionError, match="incumbent_conflict"):
        service.promote(request, promoted_at=datetime.now(UTC))

    assert load_active_m7_forecast_bundle(paths) is None


def test_manual_promotion_rejects_tampered_blind_report(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    package_id, package_hash = _publish_direction(paths, tmp_path)
    blind_path, request = _evidence(
        paths, package_id=package_id, package_hash=package_hash
    )
    payload = json.loads(blind_path.read_text(encoding="utf-8"))
    payload["package_sha256"] = "0" * 64
    blind_path.write_text(json.dumps(payload), encoding="utf-8")
    service = M7PromotionService(
        paths,
        current_runtime=RUNTIME,
        incumbent_provider=lambda _bundle: "direction-incumbent-v1",
        blind_report_resolver=lambda _window: blind_path,
        preserved_evidence_provider=_preserved_evidence,
    )

    with pytest.raises(M7PromotionError, match="blind_package_mismatch"):
        service.promote(request, promoted_at=datetime.now(UTC))


def test_range_promotion_rejects_five_day_anchor_changed_after_blind_review(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    package_id, package_hash = _publish_range(paths, tmp_path)
    registry = LocalModelRegistry(paths.models / "registry")
    anchor_releases = []
    for index in (1, 2):
        artifact = tmp_path / f"anchor-{index}.bin"
        artifact.write_bytes(f"anchor-{index}".encode("utf-8"))
        release = registry.register_release(
            model_version=f"range-anchor-v{index}",
            task="range_exit",
            horizon=5,
            calibration_version=f"anchor-cal-v{index}",
            source_decision_id=f"anchor-decision-v{index}",
            promotion_eligible=True,
            artifacts={"component.bin": artifact},
            registered_at=datetime(2026, 8, 20 + index, tzinfo=UTC),
        )
        anchor_releases.append(release)
    registry.promote(
        "range-anchor-v1",
        reason="blind anchor",
        recorded_at=datetime(2026, 8, 23, tzinfo=UTC),
    )
    run_id = "run-targeted-range-v1"
    window_id = "promotion-seed100-bbbbbbbbbbbb-v1"
    report_path = paths.models / "runs" / run_id / "training-report.json"
    write_immutable_json(
        report_path,
        {
            "run_id": run_id,
            "bundle_results": {
                "range_exit_20": {
                    "bundle_id": "range_exit_20",
                    "package_id": package_id,
                    "package_sha256": package_hash,
                    "blind_window_id": window_id,
                    "incumbent_id": None,
                    "range_five_day_anchor_id": "range-anchor-v1",
                }
            },
        },
        pretty=False,
    )
    blind_path = paths.models / "test-evidence" / "range-blind.json"
    write_immutable_json(
        blind_path,
        {
            "schema_version": "m7-blind-report-v1",
            "window_id": window_id,
            "bundle_id": "range_exit_20",
            "package_sha256": package_hash,
            "manifest_hash": "f" * 64,
            "registration_hash": "1" * 64,
            "generated_at": "2026-12-01T21:00:00Z",
            "evidence_scope": "blind_complete",
            "available_anchor_count": 77,
            "mature_label_count": 77,
            "evidence": {"range_five_day_anchor_id": "range-anchor-v1"},
            "decision": {
                "bundle_id": "range_exit_20",
                "status": "awaiting_manual_approval",
                "failed_gates": [],
                "metrics": {
                    "evidence_scope": "blind_complete",
                    "five_day_anchor_hash": anchor_releases[0].manifest_hash,
                },
                "per_horizon": {"20": {}},
            },
        },
        pretty=False,
    )
    registry.promote(
        "range-anchor-v2",
        reason="anchor changed after review",
        recorded_at=datetime(2026, 12, 2, tzinfo=UTC),
    )
    request = ModelPromotionRequest(
        training_run_id=run_id,
        bundle_id="range_exit_20",
        expected_package_sha256=package_hash,
        expected_incumbent_id=None,
        approved_by="research-owner",
    )
    service = M7PromotionService(
        paths,
        current_runtime=RUNTIME,
        incumbent_provider=lambda _bundle: None,
        blind_report_resolver=lambda _window: blind_path,
        preserved_evidence_provider=_preserved_evidence,
    )

    with pytest.raises(M7PromotionError, match="range_5d_anchor_conflict"):
        service.promote(request, promoted_at=datetime(2026, 12, 2, 15, tzinfo=UTC))

    assert load_active_m7_forecast_bundle(paths) is None
