"""Explicit, sanitized command-line entry points for the research platform."""

import argparse
from collections import Counter
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date
from io import StringIO
import json
import logging
from pathlib import Path
from collections.abc import Mapping
import os
import sys
from typing import Protocol, TextIO

from stock_prediction.capabilities.store import JsonCapabilityStore
from stock_prediction.contracts.capability import CapabilitySnapshot, CapabilityStatus


class ProbeRunner(Protocol):
    """Small command boundary for the OpenD capability probe."""

    def probe(self) -> CapabilitySnapshot: ...


class SnapshotWriter(Protocol):
    """Small command boundary for capability persistence."""

    def save(self, snapshot: CapabilitySnapshot) -> Path: ...


@dataclass(frozen=True, slots=True)
class ProbeRun:
    """Safe values produced by one persisted capability probe."""

    snapshot: CapabilitySnapshot
    output_path: Path
    status_counts: dict[CapabilityStatus, int]
    core_ready: bool


def _disable_sdk_console_output() -> None:
    """Silence raw futu diagnostics for the remaining CLI process lifetime."""

    logging.getLogger("FTConsoleLog").disabled = True


def run_probe(
    *,
    probe: ProbeRunner | None = None,
    store: SnapshotWriter | None = None,
) -> ProbeRun:
    """Run and persist the fixed M1 probe without exposing raw SDK responses."""

    _disable_sdk_console_output()
    if probe is None:
        # Keep the futu dependency outside module import so the CLI can place
        # SDK import-time diagnostics behind its sanitizing output boundary.
        from stock_prediction.opend.probe import CapabilityProbe

        active_probe: ProbeRunner = CapabilityProbe()
    else:
        active_probe = probe
    active_store = store or JsonCapabilityStore()
    snapshot = active_probe.probe()
    output_path = active_store.save(snapshot)
    counts = Counter(row.status for row in snapshot.capabilities)
    global_rows = [
        row for row in snapshot.capabilities if row.interface == "get_global_state"
    ]
    core_ready = bool(global_rows) and all(
        row.status is CapabilityStatus.AVAILABLE for row in global_rows
    )
    return ProbeRun(
        snapshot=snapshot,
        output_path=output_path,
        status_counts={status: counts[status] for status in CapabilityStatus},
        core_ready=core_ready,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the deliberately small command surface."""

    parser = argparse.ArgumentParser(prog="stock-prediction")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "probe-opend",
        help="persist a safe, read-only OpenD capability snapshot",
    )
    subparsers.add_parser(
        "benchmark-m1",
        help="run the fully offline M1 performance benchmark",
    )
    history_parser = subparsers.add_parser(
        "probe-history-coverage",
        help="measure minimal quota-sensitive history coverage",
    )
    history_parser.add_argument("--confirm-quota-use", action="store_true")
    verify_parser = subparsers.add_parser(
        "m2-verify-raw", help="verify immutable M2 raw batches and checksums"
    )
    verify_parser.add_argument("--raw-root", type=Path, required=True)
    quality_parser = subparsers.add_parser(
        "m2-quality-report", help="summarize registered M2 quality evidence"
    )
    quality_parser.add_argument("--catalog", type=Path, required=True)
    task_parser = subparsers.add_parser(
        "m2-task-status", help="show persisted M2 ingestion task status"
    )
    task_parser.add_argument("--state-db", type=Path, required=True)
    rebuild_parser = subparsers.add_parser(
        "m2-rebuild", help="rebuild DuckDB canonical bars from cold evidence"
    )
    rebuild_parser.add_argument("--raw-root", type=Path, required=True)
    rebuild_parser.add_argument("--catalog", type=Path, required=True)
    demo_parser = subparsers.add_parser(
        "m2-offline-demo", help="run a synthetic no-OpenD M2 end-to-end demo"
    )
    demo_parser.add_argument("--work-root", type=Path, required=True)
    m3_demo_parser = subparsers.add_parser(
        "m3-offline-demo",
        help="build and audit a synthetic no-OpenD M3 research dataset",
    )
    m3_demo_parser.add_argument("--work-root", type=Path, required=True)
    m4_demo_parser = subparsers.add_parser(
        "m4-offline-demo",
        help="run deterministic no-OpenD M4 validation and backtesting",
    )
    m4_demo_parser.add_argument("--work-root", type=Path, required=True)
    m6_demo_parser = subparsers.add_parser(
        "m6-offline-demo",
        help="run deterministic M6 lifecycle, recovery, drift, and SLO drills",
    )
    m6_demo_parser.add_argument("--work-root", type=Path, required=True)
    registry_status_parser = subparsers.add_parser(
        "m6-registry-status", help="show the hash-verified model lifecycle state"
    )
    registry_status_parser.add_argument("--registry-root", type=Path, required=True)
    registry_status_parser.add_argument("--task", required=True)
    registry_status_parser.add_argument(
        "--horizon", type=int, choices=(1, 5, 20), required=True
    )
    promote_parser = subparsers.add_parser(
        "m6-promote", help="promote one registered release after frozen gates pass"
    )
    promote_parser.add_argument("--registry-root", type=Path, required=True)
    promote_parser.add_argument("--model-version", required=True)
    promote_parser.add_argument("--reason", required=True)
    rollback_parser = subparsers.add_parser(
        "m6-rollback", help="roll back to a hash-verified prior champion"
    )
    rollback_parser.add_argument("--registry-root", type=Path, required=True)
    rollback_parser.add_argument("--task", required=True)
    rollback_parser.add_argument(
        "--horizon", type=int, choices=(1, 5, 20), required=True
    )
    rollback_parser.add_argument("--target-version")
    rollback_parser.add_argument("--reason", required=True)
    backup_parser = subparsers.add_parser(
        "m6-backup", help="create a checksum-bound raw and model backup"
    )
    backup_parser.add_argument("--backup-root", type=Path, required=True)
    backup_parser.add_argument("--backup-id", required=True)
    backup_parser.add_argument("--raw-root", type=Path, required=True)
    backup_parser.add_argument("--registry-root", type=Path, required=True)
    verify_backup_parser = subparsers.add_parser(
        "m6-verify-backup", help="verify one backup manifest and every payload hash"
    )
    verify_backup_parser.add_argument("--backup-root", type=Path, required=True)
    verify_backup_parser.add_argument("--backup-id", required=True)
    restore_parser = subparsers.add_parser(
        "m6-restore", help="restore one verified backup into a new empty target"
    )
    restore_parser.add_argument("--backup-root", type=Path, required=True)
    restore_parser.add_argument("--backup-id", required=True)
    restore_parser.add_argument("--target-root", type=Path, required=True)
    pilot_parser = subparsers.add_parser(
        "m2-history-pilot", help="run one explicitly bounded live OpenD history pilot"
    )
    pilot_parser.add_argument("--symbol", required=True)
    pilot_parser.add_argument("--timeframe", choices=("1d", "60m"), required=True)
    pilot_parser.add_argument("--start", type=date.fromisoformat, required=True)
    pilot_parser.add_argument("--end", type=date.fromisoformat, required=True)
    pilot_parser.add_argument("--confirm-quota-use", action="store_true")
    for command, help_text in (
        ("live-preflight", "validate the selected market-data provider"),
        ("live-refresh", "incrementally refresh the active live dataset"),
        ("live-build-features", "build versioned live features and rule states"),
        ("live-train", "run registered live walk-forward model evaluation"),
        ("live-publish", "publish an immutable live research snapshot"),
        ("live-run-once", "run one refresh, build, inference, and publish cycle"),
        ("live-scheduler", "run the local live-data scheduler"),
    ):
        live_parser = subparsers.add_parser(command, help=help_text)
        live_parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
        live_parser.add_argument("--dataset-revision")
        if command == "live-preflight":
            live_parser.add_argument(
                "--provider",
                choices=("opend", "alpaca-sip", "alpaca-iex"),
                default="opend",
            )
    bootstrap_parser = subparsers.add_parser(
        "live-bootstrap", help="initialize the active OpenD dataset"
    )
    bootstrap_parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    bootstrap_parser.add_argument("--dataset-revision")
    bootstrap_parser.add_argument(
        "--provider",
        choices=("opend", "alpaca-sip", "alpaca-iex"),
        default="opend",
    )
    bootstrap_parser.add_argument("--confirm-quota-use", action="store_true")
    bootstrap_parser.add_argument("--confirm-history-use", action="store_true")
    bootstrap_parser.add_argument("--allow-zero-reserve", action="store_true")
    expansion_parser = subparsers.add_parser(
        "live-expand-universe",
        help="backfill only missing members of the active versioned universe",
    )
    expansion_parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    expansion_parser.add_argument("--dataset-revision")
    expansion_parser.add_argument("--confirm-quota-use", action="store_true")
    expansion_parser.add_argument("--allow-zero-reserve", action="store_true")
    validation_parser = subparsers.add_parser(
        "live-validate-dataset", help="validate a staged live dataset"
    )
    validation_parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    validation_parser.add_argument("--dataset-revision", required=True)
    activation_parser = subparsers.add_parser(
        "live-activate-dataset", help="atomically activate a validated dataset"
    )
    activation_parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    activation_parser.add_argument("--dataset-revision", required=True)
    activation_parser.add_argument("--confirm-activation", action="store_true")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    probe: ProbeRunner | None = None,
    store: SnapshotWriter | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    benchmark_runner: Callable[[], dict[str, object]] | None = None,
    history_coverage_runner: Callable[[], dict[str, object]] | None = None,
    m2_runner: Callable[[argparse.Namespace], dict[str, object]] | None = None,
    m3_runner: Callable[[argparse.Namespace], dict[str, object]] | None = None,
    m4_runner: Callable[[argparse.Namespace], dict[str, object]] | None = None,
    m6_runner: Callable[[argparse.Namespace], dict[str, object]] | None = None,
    live_runner: Callable[[argparse.Namespace], dict[str, object]] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Run a command and return a process exit code."""

    args = build_parser().parse_args(argv)
    live_commands = {
        "live-preflight",
        "live-bootstrap",
        "live-refresh",
        "live-build-features",
        "live-train",
        "live-publish",
        "live-run-once",
        "live-scheduler",
        "live-expand-universe",
        "live-validate-dataset",
        "live-activate-dataset",
    }
    if args.command in live_commands:
        environment = os.environ if environ is None else environ
        output = stdout or sys.stdout
        error_output = stderr or sys.stderr
        if args.command == "live-bootstrap":
            if args.provider in {"alpaca-sip", "alpaca-iex"}:
                if not args.confirm_history_use:
                    print(
                        "error_code=live_bootstrap_confirmation_required",
                        file=error_output,
                    )
                    return 2
            elif (
                not args.confirm_quota_use
                or not args.allow_zero_reserve
                or environment.get("RUN_OPEND_LIVE_BOOTSTRAP") != "1"
            ):
                print(
                    "error_code=live_bootstrap_confirmation_required",
                    file=error_output,
                )
                return 2
        if args.command == "live-activate-dataset" and not args.confirm_activation:
            print(
                "error_code=live_activate_dataset_confirmation_required",
                file=error_output,
            )
            return 2
        if args.command == "live-expand-universe" and (
            not args.confirm_quota_use
            or environment.get("RUN_OPEND_UNIVERSE_EXPANSION") != "1"
        ):
            print(
                "error_code=live_expand_universe_confirmation_required",
                file=error_output,
            )
            return 2
        _disable_sdk_console_output()
        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                if live_runner is None:
                    from stock_prediction.live.commands import run_live_command

                    result = run_live_command(args)
                else:
                    result = live_runner(args)
                payload = json.dumps(
                    result, allow_nan=False, separators=(",", ":"), sort_keys=True
                )
        except (Exception, SystemExit):
            error_code = args.command.replace("-", "_") + "_failed"
            print(f"error_code={error_code}", file=error_output)
            return 2
        print(payload, file=output)
        return 0
    m2_commands = {
        "m2-verify-raw",
        "m2-quality-report",
        "m2-task-status",
        "m2-rebuild",
        "m2-offline-demo",
        "m2-history-pilot",
    }
    if args.command in m2_commands:
        environment = os.environ if environ is None else environ
        output = stdout or sys.stdout
        error_output = stderr or sys.stderr
        if args.command == "m2-history-pilot" and (
            not args.confirm_quota_use
            or environment.get("RUN_OPEND_HISTORY_PILOT") != "1"
        ):
            print(
                "error_code=history_pilot_confirmation_required",
                file=error_output,
            )
            return 2
        _disable_sdk_console_output()
        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                if m2_runner is None:
                    from stock_prediction.data.report import run_m2_command

                    result = run_m2_command(args)
                else:
                    result = m2_runner(args)
                payload = json.dumps(
                    result, allow_nan=False, separators=(",", ":"), sort_keys=True
                )
        except (Exception, SystemExit):
            error_code = (
                "history_pilot_failed"
                if args.command == "m2-history-pilot"
                else "m2_command_failed"
            )
            print(f"error_code={error_code}", file=error_output)
            return 2
        print(payload, file=output)
        return 0
    if args.command == "m3-offline-demo":
        output = stdout or sys.stdout
        error_output = stderr or sys.stderr
        _disable_sdk_console_output()
        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                if m3_runner is None:
                    from stock_prediction.research.report import run_m3_command

                    result = run_m3_command(args)
                else:
                    result = m3_runner(args)
                payload = json.dumps(
                    result, allow_nan=False, separators=(",", ":"), sort_keys=True
                )
        except (Exception, SystemExit):
            print("error_code=m3_command_failed", file=error_output)
            return 2
        print(payload, file=output)
        return 0
    if args.command == "m4-offline-demo":
        output = stdout or sys.stdout
        error_output = stderr or sys.stderr
        _disable_sdk_console_output()
        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                if m4_runner is None:
                    from stock_prediction.modeling.pipeline import run_m4_command

                    result = run_m4_command(args)
                else:
                    result = m4_runner(args)
                payload = json.dumps(
                    result, allow_nan=False, separators=(",", ":"), sort_keys=True
                )
        except (Exception, SystemExit):
            print("error_code=m4_command_failed", file=error_output)
            return 2
        print(payload, file=output)
        return 0
    m6_commands = {
        "m6-offline-demo",
        "m6-registry-status",
        "m6-promote",
        "m6-rollback",
        "m6-backup",
        "m6-verify-backup",
        "m6-restore",
    }
    if args.command in m6_commands:
        output = stdout or sys.stdout
        error_output = stderr or sys.stderr
        _disable_sdk_console_output()
        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                if m6_runner is None:
                    from stock_prediction.operations.report import run_m6_command

                    result = run_m6_command(args)
                else:
                    result = m6_runner(args)
                payload = json.dumps(
                    result, allow_nan=False, separators=(",", ":"), sort_keys=True
                )
        except (Exception, SystemExit):
            print("error_code=m6_command_failed", file=error_output)
            return 2
        print(payload, file=output)
        return 0
    if args.command == "probe-history-coverage":
        environment = os.environ if environ is None else environ
        error_output = stderr or sys.stderr
        if (
            not args.confirm_quota_use
            or environment.get("RUN_OPEND_HISTORY_COVERAGE") != "1"
        ):
            print(
                "error_code=history_coverage_confirmation_required", file=error_output
            )
            return 2
        output = stdout or sys.stdout
        _disable_sdk_console_output()
        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                if history_coverage_runner is None:
                    from stock_prediction.opend.history_coverage import (
                        run_and_save_history_coverage,
                    )

                    result = run_and_save_history_coverage()
                else:
                    result = history_coverage_runner()
                payload = json.dumps(
                    result, allow_nan=False, separators=(",", ":"), sort_keys=True
                )
        except (Exception, SystemExit):
            print("error_code=history_coverage_failed", file=error_output)
            return 2
        print(payload, file=output)
        return 0
    if args.command == "benchmark-m1":
        output = stdout or sys.stdout
        error_output = stderr or sys.stderr
        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                if benchmark_runner is None:
                    from stock_prediction.benchmarks.m1 import run_m1_benchmark

                    result = run_m1_benchmark()
                else:
                    result = benchmark_runner()
                payload = json.dumps(
                    result, allow_nan=False, separators=(",", ":"), sort_keys=True
                )
        except (Exception, SystemExit):
            print('{"error_code":"benchmark_failed"}', file=error_output)
            return 2
        print(payload, file=output)
        return 0

    if args.command != "probe-opend":
        build_parser().error("unsupported command")

    output = stdout or sys.stdout
    error_output = stderr or sys.stderr
    try:
        # The SDK can print directly during import, connection, and close.
        # Capture both process streams for the complete untrusted boundary;
        # only the stable summary below is emitted after success.
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            run = run_probe(probe=probe, store=store)
    except (Exception, SystemExit):
        print("error_code=probe_failed", file=error_output)
        return 2

    counts = run.status_counts
    print(f"snapshot_id={run.snapshot.snapshot_id}", file=output)
    print(
        "status_counts="
        f"available:{counts[CapabilityStatus.AVAILABLE]} "
        f"denied:{counts[CapabilityStatus.DENIED]} "
        f"unavailable:{counts[CapabilityStatus.UNAVAILABLE]} "
        f"unknown:{counts[CapabilityStatus.UNKNOWN]}",
        file=output,
    )
    print(f"opend_version={run.snapshot.opend_version}", file=output)
    print(f"sdk_version={run.snapshot.sdk_version}", file=output)
    print(f"output_path={run.output_path}", file=output)
    return 0 if run.core_ready else 1
