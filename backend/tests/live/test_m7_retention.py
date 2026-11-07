from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_retention import M7RetentionManager
from stock_prediction.modeling.report import write_immutable_json


def _failed_trial(
    paths: LivePaths, *, trial_id: str, created_at: datetime
) -> tuple[Path, Path]:
    root = paths.models / "m7-failed-trials" / trial_id
    checkpoint = root / "checkpoints" / "model.ckpt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"large-failed-checkpoint")
    metadata = root / "metadata.json"
    write_immutable_json(
        metadata,
        {
            "schema_version": "m7-failed-trial-v1",
            "trial_id": trial_id,
            "status": "failed",
            "created_at": created_at.isoformat().replace("+00:00", "Z"),
            "checkpoint_relative_paths": ["checkpoints/model.ckpt"],
            "parameters": {"seed": 17},
            "metrics": {"reason": "oom"},
        },
        pretty=False,
    )
    return metadata, checkpoint


def test_retention_deletes_only_old_unreferenced_failed_checkpoint(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    now = datetime(2026, 12, 1, tzinfo=UTC)
    metadata, checkpoint = _failed_trial(
        paths, trial_id="trial-old", created_at=now - timedelta(days=91)
    )

    result = M7RetentionManager(paths).cleanup_failed_checkpoints(now=now)

    assert result.deleted_checkpoints == (checkpoint.resolve(),)
    assert metadata.is_file()
    assert not checkpoint.exists()
    assert result.deletion_records[0].is_file()


def test_retention_preserves_referenced_and_younger_checkpoints(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    now = datetime(2026, 12, 1, tzinfo=UTC)
    _, referenced = _failed_trial(
        paths, trial_id="trial-referenced", created_at=now - timedelta(days=120)
    )
    _, young = _failed_trial(
        paths, trial_id="trial-young", created_at=now - timedelta(days=20)
    )
    write_immutable_json(
        paths.models / "runs" / "run-1" / "training-report.json",
        {
            "checkpoint_reference": (
                "m7-failed-trials/trial-referenced/checkpoints/model.ckpt"
            )
        },
        pretty=False,
    )

    result = M7RetentionManager(paths).cleanup_failed_checkpoints(now=now)

    assert result.deleted_checkpoints == ()
    assert referenced.is_file()
    assert young.is_file()


def test_retention_never_touches_shadow_blind_champion_or_audit(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    protected = (
        paths.models / "m7-packages" / "shadow-1" / "model.bin",
        paths.models / "m7-blind" / "window-1" / "prediction.json",
        paths.models / "m7-forecast-bundles" / "ACTIVE.json",
        paths.models / "m7-audit" / "events.jsonl",
    )
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"created_at": "2020-01-01"}), encoding="utf-8")

    M7RetentionManager(paths).cleanup_failed_checkpoints(
        now=datetime(2026, 12, 1, tzinfo=UTC)
    )

    assert all(path.is_file() for path in protected)
