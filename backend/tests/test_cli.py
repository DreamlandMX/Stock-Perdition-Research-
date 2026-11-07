"""Tests for the safe, explicit OpenD probe command."""

from datetime import UTC, datetime
from io import StringIO
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import tomllib
import pytest

from stock_prediction.contracts.capability import (
    CapabilitySnapshot,
    CapabilityStatus,
    DataCapability,
    PointInTimeStatus,
)


PROBED_AT = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def make_snapshot(
    *, global_status: CapabilityStatus = CapabilityStatus.AVAILABLE
) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        snapshot_id="safe-snapshot-id",
        probed_at=PROBED_AT,
        opend_version="10.8.6808",
        sdk_version="10.8.6808",
        capabilities=[
            DataCapability(
                interface="get_global_state",
                market="GLOBAL",
                asset_type="system",
                timeframe=None,
                status=global_status,
                point_in_time_status=PointInTimeStatus.NOT_APPLICABLE,
                probed_at=PROBED_AT,
            ),
            DataCapability(
                interface="request_history_kline",
                market="US",
                asset_type="stock",
                timeframe="1d",
                status=CapabilityStatus.UNKNOWN,
                point_in_time_status=PointInTimeStatus.UNPROVEN,
                probed_at=PROBED_AT,
            ),
        ],
    )


class FakeProbe:
    def __init__(self, snapshot: CapabilitySnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0
        self.errors = ("account=123456 credential=super-secret raw-body",)

    def probe(self) -> CapabilitySnapshot:
        self.calls += 1
        return self.snapshot


class NoisySdkProbe(FakeProbe):
    def probe(self) -> CapabilitySnapshot:
        logging.getLogger("FTConsoleLog").info(
            "raw connection=998877 account=123456 credential=super-secret"
        )
        return super().probe()


class LateNoisySdkProbe(FakeProbe):
    def __init__(self, snapshot: CapabilitySnapshot) -> None:
        super().__init__(snapshot)
        self.release = Event()
        self.finished = Event()

    def probe(self) -> CapabilitySnapshot:
        def log_after_probe_returns() -> None:
            self.release.wait(timeout=5)
            logging.getLogger("FTConsoleLog").info(
                "late raw connection=887766 account=654321"
            )
            self.finished.set()

        Thread(target=log_after_probe_returns, daemon=True).start()
        return super().probe()


class FakeStore:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.saved: list[CapabilitySnapshot] = []

    def save(self, snapshot: CapabilitySnapshot) -> Path:
        self.saved.append(snapshot)
        return self.output_path


class FailingProbe:
    def probe(self) -> CapabilitySnapshot:
        raise RuntimeError("account=123456 credential=super-secret raw-body")


class FailingStore(FakeStore):
    def save(self, snapshot: CapabilitySnapshot) -> Path:
        raise RuntimeError("account=654321 credential=another-secret raw-body")


def test_probe_command_saves_once_and_prints_only_safe_summary(tmp_path: Path) -> None:
    from stock_prediction.cli import main

    snapshot = make_snapshot()
    probe = FakeProbe(snapshot)
    store = FakeStore(tmp_path / "safe-snapshot.json")
    stdout = StringIO()

    exit_code = main(
        ["probe-opend"], probe=probe, store=store, stdout=stdout
    )

    assert exit_code == 0
    assert probe.calls == 1
    assert store.saved == [snapshot]
    assert stdout.getvalue().splitlines() == [
        "snapshot_id=safe-snapshot-id",
        "status_counts=available:1 denied:0 unavailable:0 unknown:1",
        "opend_version=10.8.6808",
        "sdk_version=10.8.6808",
        f"output_path={store.output_path}",
    ]
    assert "123456" not in stdout.getvalue()
    assert "super-secret" not in stdout.getvalue()
    assert "raw-body" not in stdout.getvalue()


def test_probe_command_exits_nonzero_for_core_readiness_failure(
    tmp_path: Path,
) -> None:
    from stock_prediction.cli import main

    snapshot = make_snapshot(global_status=CapabilityStatus.UNAVAILABLE)

    exit_code = main(
        ["probe-opend"],
        probe=FakeProbe(snapshot),
        store=FakeStore(tmp_path / "offline.json"),
        stdout=StringIO(),
    )

    assert exit_code == 1


def test_probe_command_suppresses_raw_sdk_console_logs(tmp_path: Path) -> None:
    from stock_prediction.cli import main

    stdout = StringIO()
    logger = logging.getLogger("FTConsoleLog")
    original_handlers = logger.handlers[:]
    original_level = logger.level
    original_disabled = logger.disabled
    logger.handlers = [logging.StreamHandler(stdout)]
    logger.setLevel(logging.INFO)
    logger.disabled = False
    try:
        exit_code = main(
            ["probe-opend"],
            probe=NoisySdkProbe(make_snapshot()),
            store=FakeStore(tmp_path / "safe.json"),
            stdout=stdout,
        )
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.disabled = original_disabled

    assert exit_code == 0
    assert "998877" not in stdout.getvalue()
    assert "123456" not in stdout.getvalue()
    assert "super-secret" not in stdout.getvalue()


def test_probe_command_keeps_late_sdk_disconnect_logs_suppressed(
    tmp_path: Path,
) -> None:
    from stock_prediction.cli import main

    stdout = StringIO()
    logger = logging.getLogger("FTConsoleLog")
    original_handlers = logger.handlers[:]
    original_level = logger.level
    original_disabled = logger.disabled
    logger.handlers = [logging.StreamHandler(stdout)]
    logger.setLevel(logging.INFO)
    logger.disabled = False
    probe = LateNoisySdkProbe(make_snapshot())
    try:
        exit_code = main(
            ["probe-opend"],
            probe=probe,
            store=FakeStore(tmp_path / "safe.json"),
            stdout=stdout,
        )
        probe.release.set()
        assert probe.finished.wait(timeout=5)
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.disabled = original_disabled

    assert exit_code == 0
    assert "887766" not in stdout.getvalue()
    assert "654321" not in stdout.getvalue()


def test_default_probe_command_does_not_accept_history_arguments() -> None:
    from stock_prediction.cli import build_parser

    help_text = build_parser().format_help()

    assert "account" not in help_text.casefold()
    assert "trade" not in help_text.casefold()
    with pytest.raises(SystemExit):
        build_parser().parse_args(["probe-opend", "--confirm-quota-use"])


def test_probe_command_sanitizes_probe_and_store_failures(tmp_path: Path) -> None:
    from stock_prediction.cli import main

    for probe, store in (
        (FailingProbe(), FakeStore(tmp_path / "unused.json")),
        (FakeProbe(make_snapshot()), FailingStore(tmp_path / "unused.json")),
    ):
        stdout = StringIO()
        stderr = StringIO()

        exit_code = main(
            ["probe-opend"],
            probe=probe,
            store=store,
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == "error_code=probe_failed\n"
        assert "account" not in stderr.getvalue()
        assert "secret" not in stderr.getvalue()
        assert "raw-body" not in stderr.getvalue()


def test_console_process_suppresses_import_time_sdk_output(tmp_path: Path) -> None:
    fake_sdk = tmp_path / "futu.py"
    fake_sdk.write_text(
        'print("account=112233 credential=import-secret raw-body")\n'
        'raise SystemExit("account=445566 credential=raised-secret")\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    source_root = str(Path("backend/src").resolve())
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), source_root, env.get("PYTHONPATH", "")]
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from stock_prediction.cli import main; "
                "raise SystemExit(main(['probe-opend']))"
            ),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "error_code=probe_failed\n"
    assert "112233" not in result.stdout + result.stderr
    assert "import-secret" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_console_entrypoint_and_powershell_wrapper_delegate_to_cli() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    wrapper = Path("scripts/probe-opend.ps1").read_text(encoding="utf-8")

    assert project["project"]["scripts"]["stock-prediction"] == (
        "stock_prediction.cli:main"
    )
    assert "uv run stock-prediction probe-opend" in wrapper
    assert "request_history_kline" not in wrapper
    assert "OpenQuoteContext" not in wrapper


def test_benchmark_command_prints_one_safe_json_document() -> None:
    from stock_prediction.cli import main

    stdout = StringIO()
    stderr = StringIO()
    payload = {
        "schema_version": "m1-benchmark-v1",
        "measurements": {},
        "safe": True,
    }

    exit_code = main(
        ["benchmark-m1"],
        benchmark_runner=lambda: payload,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == payload
    assert stdout.getvalue().count("\n") == 1
    assert stderr.getvalue() == ""


def test_benchmark_command_sanitizes_failures() -> None:
    from stock_prediction.cli import main

    def fail_benchmark() -> dict[str, object]:
        print("raw account=123456 credential=secret")
        raise RuntimeError("raw benchmark traceback account=123456")

    stdout = StringIO()
    stderr = StringIO()

    exit_code = main(
        ["benchmark-m1"],
        benchmark_runner=fail_benchmark,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == '{"error_code":"benchmark_failed"}\n'


def test_history_coverage_requires_both_confirmation_gates() -> None:
    from stock_prediction.cli import main

    calls = 0

    def runner() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"snapshot_id": "safe"}

    for argv, environment in (
        (["probe-history-coverage"], {"RUN_OPEND_HISTORY_COVERAGE": "1"}),
        (["probe-history-coverage", "--confirm-quota-use"], {}),
    ):
        stderr = StringIO()
        assert main(
            argv,
            history_coverage_runner=runner,
            environ=environment,
            stderr=stderr,
        ) == 2
        assert stderr.getvalue() == "error_code=history_coverage_confirmation_required\n"
    assert calls == 0


def test_history_coverage_runs_only_with_both_gates_and_prints_safe_json() -> None:
    from stock_prediction.cli import main

    stdout = StringIO()
    exit_code = main(
        ["probe-history-coverage", "--confirm-quota-use"],
        history_coverage_runner=lambda: {"snapshot_id": "safe", "rows_updated": 4},
        environ={"RUN_OPEND_HISTORY_COVERAGE": "1"},
        stdout=stdout,
    )
    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {"rows_updated": 4, "snapshot_id": "safe"}


def test_history_coverage_sanitizes_runner_failure() -> None:
    from stock_prediction.cli import main

    def fail() -> dict[str, object]:
        print("raw account=123 credential=secret")
        raise RuntimeError("raw history response")

    stdout = StringIO()
    stderr = StringIO()
    exit_code = main(
        ["probe-history-coverage", "--confirm-quota-use"],
        history_coverage_runner=fail,
        environ={"RUN_OPEND_HISTORY_COVERAGE": "1"},
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "error_code=history_coverage_failed\n"


def test_history_coverage_disables_prebound_futu_console_logger() -> None:
    from stock_prediction.cli import main

    stdout = StringIO()
    logger = logging.getLogger("FTConsoleLog")
    original_handlers = logger.handlers[:]
    original_level = logger.level
    original_disabled = logger.disabled
    logger.handlers = [logging.StreamHandler(stdout)]
    logger.setLevel(logging.INFO)
    logger.disabled = False

    def noisy_runner() -> dict[str, object]:
        logger.info("raw account=123 credential=secret")
        return {"snapshot_id": "safe", "rows_updated": 4}

    try:
        exit_code = main(
            ["probe-history-coverage", "--confirm-quota-use"],
            history_coverage_runner=noisy_runner,
            environ={"RUN_OPEND_HISTORY_COVERAGE": "1"},
            stdout=stdout,
        )
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.disabled = original_disabled

    assert exit_code == 0
    assert "account=123" not in stdout.getvalue()
    assert "credential=secret" not in stdout.getvalue()
