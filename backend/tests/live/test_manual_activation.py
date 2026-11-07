from datetime import UTC, datetime
import json
from pathlib import Path
import pickle
from types import SimpleNamespace

import pytest

from stock_prediction.live.config import LivePaths
from stock_prediction.live.training import write_latest_training_pointer
from stock_prediction.operations.registry import LocalModelRegistry


@pytest.mark.parametrize("failure", [None, "corrupt", "switch", "publish"])
def test_manual_activation_is_complete_truthful_and_reversible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    from stock_prediction.live import manual_activation as module

    paths = LivePaths.from_workspace(tmp_path)
    candidate_version = "live-model-20261103002021-r3-fe3e7930"
    paths.ensure()
    registry = LocalModelRegistry(paths.models / "registry")
    keys = [
        (task, horizon)
        for horizon in (1, 5, 20)
        for task in ("forecast_direction", "breakout", "return_fwd", "mfe", "mae")
    ] + [("range_exit", horizon) for horizon in (5, 20)]
    run = paths.models / "runs" / "run-1"
    run.mkdir(parents=True)
    rows = []
    for index, (task, horizon) in enumerate([("forecast_bundle", 20), *keys]):
        version = candidate_version if index == 0 else f"candidate-{task}-{horizon}"
        name = "production-package.pkl" if index == 0 else "component-package.pkl"
        package_path = run / f"package-{index}.pkl"
        package_path.write_bytes(
            pickle.dumps(
                {
                    "model_version": version,
                    "feature_set_version": "features-v1",
                    "universe_version": "universe-v1",
                    "features": ("return_1",),
                    "gate_metrics": {"gate_passed": False},
                }
            )
        )
        release = registry.register_release(
            model_version=version,
            task=task,
            horizon=horizon,
            calibration_version="cal-v1",
            source_decision_id="run-1",
            promotion_eligible=False,
            artifacts={name: package_path},
            registered_at=datetime.now(UTC),
        )
        rows.append(
            {
                "task": task,
                "horizon": horizon,
                "model_version": version,
                "release_hash": release.manifest_hash,
            }
        )
    report = {
        "run_id": "run-1",
        "model_version": candidate_version,
        "feature_set_version": "features-v1",
        "universe_version": "universe-v1",
        "feature_input_hash": "f" * 64,
        "tasks": [
            {"task": t, "horizon": h, "gate_passed": i >= 4}
            for i, (t, h) in enumerate(keys)
        ],
        "component_releases": rows[1:],
        "development_all_gates_passed": False,
        "data_quality_summary": {"quarantined_rows": 42},
    }
    report_path = run / "training-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    write_latest_training_pointer(paths, report_path, "run-1")
    before_report = report_path.read_bytes()
    monkeypatch.setattr(
        module,
        "load_latest_feature_artifact",
        lambda _p: SimpleNamespace(
            feature_set_version="features-v1",
            universe_version="universe-v1",
            input_hash="f" * 64,
        ),
    )
    if failure == "corrupt":
        source = (
            paths.models
            / "registry"
            / "releases"
            / candidate_version
            / "artifacts"
            / "production-package.pkl"
        )
        source.write_bytes(b"corrupt")
    original_promote = LocalModelRegistry.promote

    def promote(self, version, **kwargs):
        if failure == "switch" and version.endswith("-mfe-1"):
            raise OSError("injected switch failure")
        return original_promote(self, version, **kwargs)

    monkeypatch.setattr(LocalModelRegistry, "promote", promote)

    def publish():
        version = registry.snapshot("forecast_bundle", 20).champion_version
        assert version is not None
        published = paths.research / "manual-test-snapshot"
        published.mkdir(parents=True)
        (published / "manifest.json").write_text(json.dumps({"model_version": version}))
        if failure == "publish":
            raise OSError("injected publication failure")
        return {
            "snapshot_id": published.name,
            "prediction_readiness": "forecast_ready",
            "research_readiness": "manual_unverified",
        }

    monkeypatch.setattr(
        module, "LiveSnapshotPublisher", lambda _p: SimpleNamespace(publish=publish)
    )
    if failure:
        with pytest.raises((RuntimeError, OSError)):
            module.activate_manual_champions(
                paths,
                expected_candidate_version=candidate_version,
                approval_reason="explicit test approval",
            )
        assert all(
            registry.snapshot(t, h).champion_version is None
            for t, h in [("forecast_bundle", 20), *keys]
        )
        if failure == "publish":
            assert not (paths.research / "manual-test-snapshot").exists()
            assert list(
                (paths.models / "manual-activations").glob(
                    "*/failed-snapshots/manual-test-snapshot/manifest.json"
                )
            )
    else:
        result = module.activate_manual_champions(
            paths,
            expected_candidate_version=candidate_version,
            approval_reason="explicit test approval",
        )
        assert result["status"] == "activated"
        assert len(result["releases"]) == 18
        assert all(len(row["model_version"]) <= 64 for row in result["releases"])
        repeated = module.activate_manual_champions(
            paths,
            expected_candidate_version=candidate_version,
            approval_reason="same explicit approval",
        )
        assert repeated == result
        for task, horizon in [("forecast_bundle", 20), *keys]:
            version = registry.snapshot(task, horizon).champion_version
            release = registry.verify_release(version)
            assert release.promotion_eligible
            assert any(
                a.relative_path.endswith("manual-decision.json")
                for a in release.artifacts
            )
    assert report_path.read_bytes() == before_report
