from io import StringIO
import json
from pathlib import Path

from stock_prediction.cli import main


def test_history_pilot_requires_both_confirmation_gates() -> None:
    calls = 0

    def runner(_args):
        nonlocal calls
        calls += 1
        return {"status": "ok"}

    stderr = StringIO()
    code = main(
        [
            "m2-history-pilot",
            "--symbol",
            "US.SPY",
            "--timeframe",
            "1d",
            "--start",
            "2026-07-01",
            "--end",
            "2026-07-10",
        ],
        m2_runner=runner,
        environ={},
        stderr=stderr,
    )

    assert code == 2
    assert calls == 0
    assert stderr.getvalue() == "error_code=history_pilot_confirmation_required\n"


def test_history_pilot_with_both_gates_emits_only_runner_summary() -> None:
    stdout = StringIO()

    code = main(
        [
            "m2-history-pilot",
            "--symbol",
            "US.SPY",
            "--timeframe",
            "1d",
            "--start",
            "2026-07-01",
            "--end",
            "2026-07-10",
            "--confirm-quota-use",
        ],
        m2_runner=lambda _args: {"status": "ok", "rows": 10},
        environ={"RUN_OPEND_HISTORY_PILOT": "1"},
        stdout=stdout,
    )

    assert code == 0
    assert json.loads(stdout.getvalue()) == {"rows": 10, "status": "ok"}


def test_offline_demo_verify_reports_and_rebuild_are_operational(
    tmp_path: Path,
) -> None:
    work_root = tmp_path / "demo"
    demo_stdout = StringIO()

    assert (
        main(
            ["m2-offline-demo", "--work-root", str(work_root)],
            stdout=demo_stdout,
        )
        == 0
    )
    demo = json.loads(demo_stdout.getvalue())
    assert demo["task_status"] == "committed"
    assert demo["raw_rows"] == 7
    assert demo["canonical_rows"] == 7
    assert demo["adjusted_rows"] == 7

    raw_stdout = StringIO()
    assert (
        main(
            ["m2-verify-raw", "--raw-root", str(work_root / "raw")],
            stdout=raw_stdout,
        )
        == 0
    )
    assert json.loads(raw_stdout.getvalue())["committed_rows"] == 7

    quality_stdout = StringIO()
    assert (
        main(
            [
                "m2-quality-report",
                "--catalog",
                str(work_root / "catalog.duckdb"),
            ],
            stdout=quality_stdout,
        )
        == 0
    )
    assert json.loads(quality_stdout.getvalue())["datasets"][0]["input_rows"] == 7

    task_stdout = StringIO()
    assert (
        main(
            [
                "m2-task-status",
                "--state-db",
                str(work_root / "state.duckdb"),
            ],
            stdout=task_stdout,
        )
        == 0
    )
    assert json.loads(task_stdout.getvalue())["tasks"][0]["task_status"] == (
        "committed"
    )

    fresh_catalog = work_root / "fresh.duckdb"
    rebuild_stdout = StringIO()
    assert (
        main(
            [
                "m2-rebuild",
                "--raw-root",
                str(work_root / "raw"),
                "--catalog",
                str(fresh_catalog),
            ],
            stdout=rebuild_stdout,
        )
        == 0
    )
    rebuilt = json.loads(rebuild_stdout.getvalue())
    assert rebuilt["raw_rows"] == 7
    assert rebuilt["canonical_rows"] == 7


def test_m2_command_failure_is_sanitized(tmp_path: Path) -> None:
    stdout = StringIO()
    stderr = StringIO()

    code = main(
        ["m2-verify-raw", "--raw-root", str(tmp_path / "missing")],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "error_code=m2_command_failed\n"
