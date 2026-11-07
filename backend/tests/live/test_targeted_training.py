from __future__ import annotations

from datetime import UTC, date, datetime
from dataclasses import replace
import json
from pathlib import Path

import pandas as pd
import pytest

from stock_prediction.live.config import LivePaths
from stock_prediction.live.promotion_windows import PromotionWindowRegistry
from stock_prediction.live.targeted_training import (
    M7BundleExecutionResult,
    M7TargetedTrainingCancelled,
    TargetedModelTrainer,
)
from stock_prediction.live.training import load_latest_training_report
from stock_prediction.modeling.m7_package import (
    M7PackageArtifactInput,
    M7PackageBuildSpec,
    M7RuntimeCompatibility,
    load_m7_package,
)
from stock_prediction.modeling.report import M7_REPORT_SECTION_NAMES


def _artifact_inputs(tmp_path: Path, bundle: str):
    source = tmp_path / f"sources-{bundle}"
    source.mkdir(parents=True)
    categories = (
        ("threshold", "label_threshold"),
        ("model-h1", "model"),
        ("model-h5", "model"),
        ("model-h20", "model"),
        ("calibration", "calibration"),
        ("explanation", "explanation"),
    )
    rows = []
    for artifact_id, category in categories:
        path = source / f"{artifact_id}.bin"
        path.write_bytes(f"{bundle}:{artifact_id}".encode())
        rows.append(
            M7PackageArtifactInput(
                artifact_id=f"{bundle}-{artifact_id}",
                category=category,
                source_path=path,
                schema_version="m7-test-v1",
            )
        )
    return tuple(rows)


def _build_spec(bundle: str) -> M7PackageBuildSpec:
    return M7PackageBuildSpec(
        package_id=f"m7-{bundle}-shadow-001",
        bundle_id=bundle,
        recipe_id="candidate-recipe-seed100-targeted-v9",
        recipe_generation=9,
        code_commit="a" * 40,
        universe_version="m1-seed-2026-07-11-v1",
        dataset_revision="opend-live-market-bars-pit-adjusted-v2",
        feature_set_version="live-daily-features-v5-seed100",
        feature_input_hash="b" * 64,
        label_version="live-labels-v2",
        split_manifest_hash="c" * 64,
        candidate_matrix_hash="d" * 64,
        dependency_lock_hash="e" * 64,
        runtime_compatibility=M7RuntimeCompatibility(
            python_version="3.11",
            platform="windows",
            architecture="x86_64",
            numpy_version="2.4.2",
            scikit_learn_version="1.9.0",
            catboost_version="1.2.10",
            torch_version="2.8.0",
            inference_devices=("cpu",),
        ),
        evidence_scope="optimization_reused",
        blind_window_id=None,
        created_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
    )


def _eligible_sessions() -> tuple[date, ...]:
    return tuple(value.date() for value in pd.bdate_range("2026-08-25", periods=110))


def _passing_result(
    tmp_path: Path, bundle: str = "direction"
) -> M7BundleExecutionResult:
    return M7BundleExecutionResult(
        bundle_id=bundle,
        development_gate_passed=True,
        gate_payload={"status": "passed", "failed_gates": []},
        package_build_spec=_build_spec(bundle),
        artifacts=_artifact_inputs(tmp_path, bundle),
        eligible_sessions=_eligible_sessions(),
        incumbent_id="incumbent-v1",
        report_payloads={
            "feature_ablation": {"status": "complete"},
            "calibration_selection": {"method": "vector_scaling"},
            "baseline_deltas": {"macro_brier_delta": -0.01},
            "bootstrap_results": {"upper_90": -0.001},
            "determinism_results": {"passed": True},
            "economic_diagnostics": {"guardrail_passed": True},
            "explanation_manifest": {"status": "available"},
        },
        runtime_observations={
            "wall_seconds": 4.0,
            "cpu_seconds": 3.5,
            "peak_rss_bytes": 4096,
            "peak_gpu_memory_bytes": 0,
            "gpu_device": "none",
        },
        range_five_day_anchor_id=(
            "range-exit-5d-incumbent-v1" if bundle == "range_exit_20" else None
        ),
    )


def test_passing_targeted_bundle_freezes_shadow_registers_window_and_writes_report(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    trainer = TargetedModelTrainer(
        paths,
        run_id="run-m7-direction-001",
        bundles=("direction",),
        recipe_version="candidate-recipe-seed100-targeted-v9",
        bundle_executor=lambda bundle: _passing_result(tmp_path, bundle),
    )
    progress = []

    result = trainer.train(progress_callback=lambda *args: progress.append(args))

    assert result["development_all_gates_passed"] is True
    assert result["automatic_promotion"] == "awaiting_manual_approval"
    assert result["completed_bundles"] == ["direction"]
    bundle = result["bundle_results"]["direction"]
    assert bundle["development_state"] == "passed"
    assert bundle["shadow_state"] == "frozen"
    assert bundle["promotion_state"] == "blind_collecting"
    assert len(bundle["package_sha256"]) == 64
    assert bundle["blind_window_id"].startswith("promotion-")
    manifest = load_m7_package(paths.models / "m7-packages", bundle["package_id"])
    assert manifest.package_sha256 == bundle["package_sha256"]
    registered = PromotionWindowRegistry(paths).load(bundle["blind_window_id"])
    assert registered.package_sha256 == manifest.package_sha256
    blind_manifest = (
        paths.models / "m7-blind" / bundle["blind_window_id"] / "manifest.json"
    )
    assert blind_manifest.is_file()
    blind_payload = json.loads(blind_manifest.read_text(encoding="utf-8"))
    assert blind_payload["bundle_id"] == "direction"
    assert blind_payload["package_sha256"] == manifest.package_sha256
    assert blind_payload["incumbent_id"] == "incumbent-v1"
    report = load_latest_training_report(paths)
    assert report["run_id"] == "run-m7-direction-001"
    assert set(report["m7_sections"]) == set(M7_REPORT_SECTION_NAMES)
    assert report["m7_sections"]["drift_evidence"]["status"] == "not_run"
    assert report["m7_sections"]["window_consumption"]["reason"]
    assert progress
    assert not (paths.models / "registry").exists()


def test_failed_targeted_bundle_writes_challenger_report_without_shadow(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)

    def execute(bundle: str) -> M7BundleExecutionResult:
        return M7BundleExecutionResult(
            bundle_id=bundle,
            development_gate_passed=False,
            gate_payload={"status": "failed", "failed_gates": ["horizon_20"]},
            package_build_spec=None,
            artifacts=(),
            eligible_sessions=(),
            incumbent_id="incumbent-v1",
            report_payloads={},
            runtime_observations={},
        )

    result = TargetedModelTrainer(
        paths,
        run_id="run-m7-failed-001",
        bundles=("direction",),
        recipe_version="candidate-recipe-seed100-targeted-v9",
        bundle_executor=execute,
    ).train(progress_callback=lambda *_: None)

    bundle = result["bundle_results"]["direction"]
    assert result["development_all_gates_passed"] is False
    assert result["automatic_promotion"] == "kept_current"
    assert bundle["development_state"] == "failed"
    assert bundle["shadow_state"] == "not_frozen"
    assert bundle["package_id"] is None
    assert not (paths.models / "m7-packages").exists()
    report = load_latest_training_report(paths)
    assert report["m7_sections"]["shadow_status"]["status"] == "failed"
    assert report["m7_sections"]["shadow_status"]["reason"] == "development_gate_failed"


def test_failed_direction_does_not_block_passing_range_shadow(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)

    def execute(bundle: str) -> M7BundleExecutionResult:
        if bundle == "range_exit_20":
            return _passing_result(tmp_path, bundle)
        return M7BundleExecutionResult(
            bundle_id="direction",
            development_gate_passed=False,
            gate_payload={"status": "failed", "failed_gates": ["horizon_20"]},
            package_build_spec=None,
            artifacts=(),
            eligible_sessions=(),
            incumbent_id="direction-incumbent-v1",
            report_payloads={},
            runtime_observations={},
        )

    result = TargetedModelTrainer(
        paths,
        run_id="run-m7-mixed-001",
        bundles=("direction", "range_exit_20"),
        recipe_version="candidate-recipe-seed100-targeted-v9",
        bundle_executor=execute,
    ).train(progress_callback=lambda *_: None)

    assert result["development_all_gates_passed"] is False
    assert result["automatic_promotion"] == "awaiting_manual_approval"
    assert result["bundle_results"]["direction"]["shadow_state"] == "not_frozen"
    assert result["bundle_results"]["range_exit_20"]["shadow_state"] == "frozen"
    assert (
        load_m7_package(
            paths.models / "m7-packages",
            "m7-range_exit_20-shadow-001",
        ).bundle_id
        == "range_exit_20"
    )


def test_cancellation_after_bundle_boundary_cannot_freeze_or_publish_report(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    cancel = False

    def execute(bundle: str) -> M7BundleExecutionResult:
        nonlocal cancel
        result = _passing_result(tmp_path, bundle)
        cancel = True
        return result

    trainer = TargetedModelTrainer(
        paths,
        run_id="run-m7-cancelled-001",
        bundles=("direction",),
        recipe_version="candidate-recipe-seed100-targeted-v9",
        bundle_executor=execute,
        cancellation_requested=lambda: cancel,
    )

    with pytest.raises(M7TargetedTrainingCancelled):
        trainer.train(progress_callback=lambda *_: None)

    assert not (paths.models / "m7-packages").exists()
    assert not (paths.models / "LATEST-TRAINING.json").exists()


def test_targeted_trainer_rejects_wrong_recipe_duplicate_bundles_and_mismatched_result(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    with pytest.raises(ValueError, match="recipe"):
        TargetedModelTrainer(
            paths,
            run_id="run-bad",
            bundles=("direction",),
            recipe_version="candidate-recipe-v1",
            bundle_executor=lambda bundle: _passing_result(tmp_path, bundle),
        )
    with pytest.raises(ValueError, match="unique"):
        TargetedModelTrainer(
            paths,
            run_id="run-bad",
            bundles=("direction", "direction"),
            recipe_version="candidate-recipe-seed100-targeted-v9",
            bundle_executor=lambda bundle: _passing_result(tmp_path, bundle),
        )

    trainer = TargetedModelTrainer(
        paths,
        run_id="run-mismatch",
        bundles=("direction",),
        recipe_version="candidate-recipe-seed100-targeted-v9",
        bundle_executor=lambda _: _passing_result(tmp_path, "range_exit_20"),
    )
    with pytest.raises(RuntimeError, match="bundle_identity_mismatch"):
        trainer.train(progress_callback=lambda *_: None)


def test_training_report_is_canonical_json(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    TargetedModelTrainer(
        paths,
        run_id="run-m7-canonical-001",
        bundles=("direction",),
        recipe_version="candidate-recipe-seed100-targeted-v9",
        bundle_executor=lambda bundle: _passing_result(tmp_path, bundle),
    ).train(progress_callback=lambda *_: None)
    report_path = (
        paths.models / "runs" / "run-m7-canonical-001" / "training-report.json"
    )
    text = report_path.read_text("utf-8")
    assert (
        text
        == json.dumps(
            json.loads(text),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def test_targeted_training_allows_runs_longer_than_24_hours(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    trainer = TargetedModelTrainer(
        paths,
        run_id="run-m7-timeout-001",
        bundles=("direction",),
        recipe_version="candidate-recipe-seed100-targeted-v9",
        bundle_executor=lambda bundle: _passing_result(tmp_path, bundle),
    )

    result = trainer.train(progress_callback=lambda *_: None)

    assert result["completed_bundles"] == ["direction"]
    assert (paths.models / "runs" / "run-m7-timeout-001" / "training-report.json").is_file()


def test_passing_bundle_rejects_resource_observation_over_hard_limit(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="resource"):
        replace(
            _passing_result(tmp_path),
            runtime_observations={
                "wall_seconds": 4.0,
                "cpu_seconds": 3.5,
                "peak_rss_bytes": 24 * 1024**3 + 1,
                "peak_gpu_memory_bytes": 0,
                "gpu_device": "none",
            },
        )
