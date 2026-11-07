from __future__ import annotations

from argparse import Namespace
from datetime import date
import json
from pathlib import Path

from stock_prediction.live.commands import run_live_command
from stock_prediction.live.config import ALPACA_SIP_DATASET_REVISION, LivePaths
from stock_prediction.live.storage import LiveStorage


def _args(tmp_path: Path, command: str, **values: object) -> Namespace:
    return Namespace(
        command=command,
        workspace_root=tmp_path,
        provider=values.get("provider", "alpaca-sip"),
        dataset_revision=values.get(
            "dataset_revision", ALPACA_SIP_DATASET_REVISION
        ),
        allow_zero_reserve=False,
    )


def test_validation_command_targets_staged_dataset(tmp_path: Path, monkeypatch: object) -> None:
    captured: dict[str, object] = {}

    def validate(paths: LivePaths, **kwargs: object) -> dict[str, object]:
        captured["root"] = paths.root
        return {"status": "pass"}

    monkeypatch.setattr("stock_prediction.live.commands.validate_live_dataset", validate)
    LiveStorage(LivePaths.for_dataset(tmp_path, ALPACA_SIP_DATASET_REVISION)).record_calendar(
        sessions=((date(2026, 8, 12), "WHOLE"),),
        source_hash="test",
        dataset_revision=ALPACA_SIP_DATASET_REVISION,
    )
    result = run_live_command(_args(tmp_path, "live-validate-dataset"))

    assert result == {"status": "pass"}
    assert captured["root"] == LivePaths.for_dataset(
        tmp_path, ALPACA_SIP_DATASET_REVISION
    ).root


def test_activation_command_requires_real_validation_file(tmp_path: Path) -> None:
    paths = LivePaths.for_dataset(tmp_path, ALPACA_SIP_DATASET_REVISION)
    paths.ensure()
    (paths.root / "validation.json").write_text(
        json.dumps(
                {
                    "status": "pass",
                    "dataset_revision": ALPACA_SIP_DATASET_REVISION,
                    "provider_comparison_status": "pass",
                }
        ),
        encoding="utf-8",
    )

    result = run_live_command(_args(tmp_path, "live-activate-dataset"))

    assert result["dataset_revision"] == ALPACA_SIP_DATASET_REVISION
