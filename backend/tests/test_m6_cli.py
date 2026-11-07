from __future__ import annotations

from io import StringIO
import json
from pathlib import Path

import pytest

from stock_prediction.cli import main


def test_m6_offline_demo_closes_lifecycle_recovery_drift_and_slo(
    tmp_path: Path,
) -> None:
    stdout = StringIO()
    stderr = StringIO()
    root = tmp_path / "m6"

    exit_code = main(
        ["m6-offline-demo", "--work-root", str(root)],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    summary = json.loads(stdout.getvalue())
    assert summary["all_drills_passed"] is True
    assert summary["all_local_slos_passed"] is True
    assert summary["recovered_champion"] == "model-v1"
    report = json.loads((root / "m6-acceptance.json").read_text(encoding="utf-8"))
    assert report["production_champion_claimed"] is False
    assert len(report["drills"]) == 5
    assert {item["family"] for item in report["drift"]["metrics"]} == {
        "data",
        "feature",
        "calibration",
        "concept",
        "interval",
    }
    interval = next(
        item for item in report["drift"]["metrics"] if item["family"] == "interval"
    )
    assert interval["level"] == "insufficient_samples"
    assert report["backup"]["restored_champion"] == "model-v2"
    assert report["slo"]["workload"]["symbols"] == 100
    log = (root / "operations.jsonl").read_text(encoding="utf-8").casefold()
    assert not any(
        marker in log
        for marker in ("password", "credential", "account_id", "authorization")
    )


def test_m6_cli_failure_is_sanitized(tmp_path: Path) -> None:
    stdout = StringIO()
    stderr = StringIO()

    def fail(_args: object) -> dict[str, object]:
        raise RuntimeError("account secret should never escape")

    exit_code = main(
        ["m6-offline-demo", "--work-root", str(tmp_path / "m6")],
        m6_runner=fail,  # type: ignore[arg-type]
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "error_code=m6_command_failed\n"


@pytest.mark.parametrize(
    ("arguments", "expected_command"),
    (
        (
            [
                "m6-registry-status",
                "--registry-root",
                "models",
                "--task",
                "direction",
                "--horizon",
                "5",
            ],
            "m6-registry-status",
        ),
        (
            [
                "m6-promote",
                "--registry-root",
                "models",
                "--model-version",
                "model-v1",
                "--reason",
                "approved",
            ],
            "m6-promote",
        ),
        (
            [
                "m6-rollback",
                "--registry-root",
                "models",
                "--task",
                "direction",
                "--horizon",
                "5",
                "--reason",
                "integrity failure",
            ],
            "m6-rollback",
        ),
        (
            [
                "m6-backup",
                "--backup-root",
                "backups",
                "--backup-id",
                "backup-v1",
                "--raw-root",
                "raw",
                "--registry-root",
                "models",
            ],
            "m6-backup",
        ),
        (
            [
                "m6-verify-backup",
                "--backup-root",
                "backups",
                "--backup-id",
                "backup-v1",
            ],
            "m6-verify-backup",
        ),
        (
            [
                "m6-restore",
                "--backup-root",
                "backups",
                "--backup-id",
                "backup-v1",
                "--target-root",
                "restore",
            ],
            "m6-restore",
        ),
    ),
)
def test_m6_operator_commands_use_one_sanitized_dispatch_boundary(
    arguments: list[str], expected_command: str
) -> None:
    stdout = StringIO()
    stderr = StringIO()

    exit_code = main(
        arguments,
        m6_runner=lambda args: {"command": args.command},
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert json.loads(stdout.getvalue()) == {"command": expected_command}
