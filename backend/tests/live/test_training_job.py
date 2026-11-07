from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime

import pytest


def _research_job(tmp_path):
    from stock_prediction.live.config import LivePaths
    from stock_prediction.live.training import write_latest_training_pointer
    from stock_prediction.operations.registry import LocalModelRegistry

    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    directory = tmp_path / "var/training-jobs/training-test"
    models = directory / "artifacts/main-models"
    report_path = models / "runs/main-training-test/training-report.json"
    report_path.parent.mkdir(parents=True)
    feature = {
        "feature_set_version": "features-v1",
        "universe_version": "universe-v1",
        "input_hash": "a" * 64,
        "as_of": "2026-09-14T20:00:00+00:00",
    }
    (paths.features / "LATEST.json").write_text(json.dumps(feature))
    (directory / "job.json").write_text(
        json.dumps(
            {
                "run_id": "training-test",
                "state": "completed",
                "phase": "completed",
                "progress_percent": 60,
                "failed_tasks": 1,
                "completed_at": "2026-09-28T14:15:40+00:00",
                "identity": {"feature_pointer": feature},
            }
        )
    )
    artifact = directory / "production-package.pkl"
    artifact.write_bytes(b"local-model")
    registry = LocalModelRegistry(models / "registry")
    registry.register_release(
        model_version="test-model",
        task="forecast_bundle",
        horizon=20,
        calibration_version="test-cal",
        source_decision_id="main-training-test",
        promotion_eligible=False,
        artifacts={"production-package.pkl": artifact},
        registered_at=datetime.now(UTC),
    )
    report = {
        **feature,
        "feature_input_hash": feature["input_hash"],
        "run_id": "main-training-test",
        "model_version": "test-model",
        "development_data_cutoff": "2026-06-29",
        "component_releases": [],
        "tasks": [{"task": "forecast_direction", "horizon": 1, "gate_passed": False}],
    }
    report_path.write_text(json.dumps(report))
    (directory / "main-report.json").write_text(json.dumps(report))
    (directory / "m7-report.json").write_text(
        json.dumps(
            {
                "run_id": "m7-training-test",
                "full_iterations": True,
                "timeout_seconds": None,
                "candidate_failures": [],
                "deep_trials": 73,
                "gate": {"status": "failed", "failed_gates": ["horizon_1"]},
            }
        )
    )
    write_latest_training_pointer(
        replace(paths, models=models), report_path, "main-training-test"
    )
    return paths, directory


def test_research_source_is_pinned_verified_and_fails_closed(tmp_path):
    from stock_prediction.live import training_job

    paths, directory = _research_job(tmp_path)
    assert training_job.resolve_research_paths(paths) == paths
    selected = training_job.resolve_research_paths(paths, "training-test")
    assert selected.models == directory / "artifacts/main-models"
    pointer = paths.root / "status/research-source.json"
    pointer.write_text(
        json.dumps(
            {
                "run_id": "training-test",
                "main_report_sha256": training_job.file_sha256(
                    directory / "main-report.json"
                ),
                "m7_report_sha256": training_job.file_sha256(
                    directory / "m7-report.json"
                ),
            }
        )
    )
    assert training_job.resolve_research_paths(paths).models == selected.models
    (directory / "main-report.json").write_text("{}")
    with pytest.raises(RuntimeError, match="hash_mismatch"):
        training_job.resolve_research_paths(paths)


def test_research_source_rejects_incomplete_incompatible_and_missing_models(tmp_path):
    from stock_prediction.live import training_job

    paths, directory = _research_job(tmp_path)
    with pytest.raises(RuntimeError):
        training_job.resolve_research_paths(paths, "../training-test")
    job_path = directory / "job.json"
    job = json.loads(job_path.read_text())
    job_path.write_text(json.dumps({**job, "state": "running"}))
    with pytest.raises(RuntimeError, match="not_completed"):
        training_job.resolve_research_paths(paths, "training-test")
    job_path.write_text(json.dumps(job))
    feature_path = paths.features / "LATEST.json"
    feature = json.loads(feature_path.read_text())
    feature_path.write_text(json.dumps({**feature, "universe_version": "other"}))
    with pytest.raises(RuntimeError, match="contract_mismatch"):
        training_job.resolve_research_paths(paths, "training-test")
    feature_path.write_text(json.dumps(feature))
    (
        directory
        / "artifacts/main-models/registry/releases/test-model/artifacts/production-package.pkl"
    ).unlink()
    with pytest.raises(RuntimeError):
        training_job.resolve_research_paths(paths, "training-test")


def test_research_publish_failure_keeps_pointer_and_completion_summary(
    tmp_path, monkeypatch
):
    from stock_prediction.live import training_job
    from stock_prediction.live.publication import LiveSnapshotPublisher

    paths, directory = _research_job(tmp_path)
    pointer = paths.root / "status/research-source.json"
    pointer.write_text('{"run_id":"old-source"}')
    before = pointer.read_bytes()

    def fail(*_args, **_kwargs):
        raise RuntimeError("publication_failed")

    monkeypatch.setattr(LiveSnapshotPublisher, "publish", fail)
    with pytest.raises(RuntimeError, match="publication_failed"):
        training_job.publish_research_job(tmp_path, "training-test")
    assert pointer.read_bytes() == before
    pointer.unlink()
    summary = training_job.independent_training_summary(paths)
    assert summary.state == "completed"
    assert summary.main_total_tasks == 1
    assert summary.m7_gate_status == "failed"
    assert summary.selected_for_research is False
    assert summary.completed_at == datetime(2026, 9, 28, 14, 15, 40, tzinfo=UTC)


def test_research_publication_uses_selected_models_and_is_idempotent(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from datetime import date
    from stock_prediction.live import publication, training_job
    from stock_prediction.live.universe_scope import LiveUniverseSpec
    from stock_prediction.serving.manifest import (
        EXPECTED_RESEARCH_TABLES,
        RESEARCH_TABLE_SCHEMAS,
        parquet_schema_hash,
        ResearchSnapshotManifest,
        SnapshotTable,
    )

    paths, directory = _research_job(tmp_path)
    selected = training_job.resolve_research_paths(paths, "training-test")
    universe = LiveUniverseSpec(
        "universe-v1", ("US.TEST",), (), date(2026, 1, 1), "test"
    )
    monkeypatch.setattr(
        publication,
        "load_latest_feature_artifact",
        lambda _: SimpleNamespace(
            as_of=datetime(2026, 9, 14, 20, tzinfo=UTC),
            input_hash="a" * 64,
            feature_set_version="features-v1",
        ),
    )
    observed = []

    def tables(_self, _snapshot, **kwargs):
        observed.append(
            (
                kwargs["inference"].paths.models,
                kwargs["research_inference"].paths.models,
            )
        )
        return {}

    monkeypatch.setattr(publication.LiveSnapshotPublisher, "_tables", tables)

    def materialize(root, *, snapshot, generated_at, **kwargs):
        target = root / snapshot.snapshot_id
        target.mkdir()
        manifest = ResearchSnapshotManifest.create(
            snapshot=snapshot,
            generated_at=generated_at,
            tables=tuple(
                SnapshotTable(
                    path=name,
                    schema_hash=parquet_schema_hash(RESEARCH_TABLE_SCHEMAS[name]),
                    content_hash="b" * 64,
                    row_count=0,
                )
                for name in EXPECTED_RESEARCH_TABLES
            ),
        )
        (target / "manifest.json").write_text(manifest.model_dump_json())
        return SimpleNamespace(published_path=target)

    monkeypatch.setattr(publication, "materialize_research_snapshot", materialize)
    publisher = publication.LiveSnapshotPublisher(
        paths, universe=universe, research_paths=selected
    )
    first = publisher.publish()
    assert first["status"] == "published"
    assert "-research-" in first["snapshot_id"]
    assert observed == [(paths.models, selected.models)]
    second = publisher.publish()
    assert second["status"] == "unchanged"
    assert second["snapshot_id"] == first["snapshot_id"]
    assert len(observed) == 1


def test_research_selection_preserves_previous_source_and_missing_report_is_unavailable(
    tmp_path, monkeypatch
):
    from stock_prediction.live import training_job
    from stock_prediction.live.publication import LiveSnapshotPublisher

    paths, directory = _research_job(tmp_path)
    pointer = paths.root / "status/research-source.json"
    original = b'{"run_id":"old-source"}'
    pointer.write_bytes(original)
    monkeypatch.setattr(
        LiveSnapshotPublisher, "publish", lambda self: {"status": "published"}
    )
    training_job.publish_research_job(tmp_path, "training-test")
    assert pointer.with_name("research-source.previous.json").read_bytes() == original
    assert training_job.independent_training_summary(paths).selected_for_research
    training_job.publish_research_job(tmp_path, "training-test")
    assert pointer.with_name("research-source.previous.json").read_bytes() == original
    (directory / "main-report.json").unlink()
    with pytest.raises(RuntimeError, match="report_missing"):
        training_job.resolve_research_paths(paths, "training-test")
    assert (
        training_job.independent_training_summary(paths).source_status == "unavailable"
    )


def test_job_start_status_and_resume_are_identity_safe(
    tmp_path: Path, monkeypatch
) -> None:
    from stock_prediction.live import training_job

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "uv.lock").write_text("lock", encoding="utf-8")
    feature = (
        workspace / "var" / "live" / "features" / "set" / "hash" / "features.parquet"
    )
    feature.parent.mkdir(parents=True)
    feature.write_bytes(b"features")
    (workspace / "var" / "live" / "features" / "LATEST.json").write_text(
        json.dumps(
            {"artifact_reference": {"sha256": "a" * 64}, "input_hash": "b" * 64}
        ),
        encoding="utf-8",
    )
    launched: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        training_job,
        "_launch_worker",
        lambda command, **_: launched.append(tuple(command)) or 4321,
    )
    monkeypatch.setattr(
        training_job,
        "_process_identity",
        lambda pid: (
            {"create_time": 1.0, "cmdline": list(launched[0])} if launched else None
        ),
    )

    started = training_job.start_job(workspace)
    assert started["state"] == "running"
    assert started["pid"] == 4321
    assert launched and launched[0][0].endswith("python.exe")
    with pytest.raises(RuntimeError, match="already_running"):
        training_job.start_job(workspace)

    monkeypatch.setattr(training_job, "_process_identity", lambda pid: None)
    status = training_job.job_status(workspace, str(started["run_id"]))
    assert status["state"] == "interrupted"

    (workspace / "uv.lock").write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="identity_changed"):
        training_job.resume_job(workspace, str(started["run_id"]))


def test_job_stop_sets_cooperative_cancel_marker(tmp_path: Path) -> None:
    from stock_prediction.live import training_job

    workspace = tmp_path / "workspace"
    run = workspace / "var" / "training-jobs" / "job-1"
    run.mkdir(parents=True)
    (run / "job.json").write_text(
        json.dumps(
            {"run_id": "job-1", "workspace": str(workspace), "state": "running"}
        ),
        encoding="utf-8",
    )

    result = training_job.stop_job(workspace, "job-1")
    assert result["state"] == "interrupted"
    assert (run / "cancel.requested").is_file()


def test_detached_worker_keeps_running_after_launcher_returns(tmp_path: Path) -> None:
    from stock_prediction.live.training_job import _launch_worker

    marker = tmp_path / "finished.txt"
    _launch_worker(
        [
            sys.executable,
            "-c",
            (
                "import pathlib,time; time.sleep(0.5); "
                f"pathlib.Path({str(marker)!r}).write_text('done')"
            ),
        ],
        cwd=tmp_path,
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
    )
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.read_text(encoding="utf-8") == "done"
