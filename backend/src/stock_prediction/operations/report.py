"""Deterministic offline M6 acceptance drill and evidence report."""

from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from stock_prediction.benchmarks.m6 import run_m6_benchmark
from stock_prediction.modeling.report import file_sha256, write_immutable_json
from stock_prediction.opend.errors import OpenDError, OpenDErrorClass
from stock_prediction.opend.ports import SdkCallResult
from stock_prediction.operations.backup import LocalBackupStore
from stock_prediction.operations.contracts import (
    DriftFamily,
    DriftReport,
    DrillStatus,
    RecoveryDrill,
)
from stock_prediction.operations.disk import DiskSpaceGuard, InsufficientDiskSpace
from stock_prediction.operations.drift import (
    assess_target_deviation,
    assess_upper_bound,
    brier_score,
    build_drift_report,
    population_stability_index,
)
from stock_prediction.operations.reconnect import ResilientQuoteSession
from stock_prediction.operations.registry import LocalModelRegistry
from stock_prediction.operations.safe_logging import SafeJsonLogger
from stock_prediction.operations.schema_guard import (
    SchemaMismatch,
    require_exact_schema,
)


_START = datetime(2026, 7, 21, 16, 0, tzinfo=UTC)


def run_m6_command(args: Namespace) -> dict[str, object]:
    if args.command != "m6-offline-demo":
        from stock_prediction.operations.commands import run_operator_command

        return run_operator_command(args)
    root = Path(args.work_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("M6 work root must be empty")
    root.mkdir(parents=True, exist_ok=True)
    test_guard = DiskSpaceGuard(
        minimum_free_bytes=0,
        minimum_free_ratio=0,
        usage=lambda _path: (10_000_000, 0, 10_000_000),
    )

    raw_root = root / "raw"
    raw_root.mkdir()
    (raw_root / "daily-bars.parquet").write_bytes(b"immutable-raw-fixture")
    source_root = root / "release-sources"
    source_root.mkdir()
    first_source = source_root / "model-v1.bin"
    second_source = source_root / "model-v2.bin"
    first_source.write_bytes(b"stable-model-v1")
    second_source.write_bytes(b"candidate-model-v2")

    registry = LocalModelRegistry(root / "models", disk_guard=test_guard)
    registry.register_release(
        model_version="model-v1",
        task="direction",
        horizon=5,
        calibration_version="cal-v1",
        source_decision_id="decision-v1",
        promotion_eligible=True,
        artifacts={"model.bin": first_source},
        registered_at=_START,
    )
    registry.mark_challenger(
        "model-v1",
        reason="initial shadow validation",
        recorded_at=_START + timedelta(seconds=1),
    )
    registry.promote(
        "model-v1",
        reason="initial stable champion",
        recorded_at=_START + timedelta(seconds=2),
    )
    registry.register_release(
        model_version="model-v2",
        task="direction",
        horizon=5,
        calibration_version="cal-v2",
        source_decision_id="decision-v2",
        promotion_eligible=True,
        artifacts={"model.bin": second_source},
        registered_at=_START + timedelta(seconds=3),
    )
    registry.mark_challenger(
        "model-v2",
        reason="independent shadow validation",
        recorded_at=_START + timedelta(seconds=4),
    )
    promoted = registry.promote(
        "model-v2",
        reason="all frozen promotion gates passed",
        recorded_at=_START + timedelta(seconds=5),
    )

    backups = LocalBackupStore(root / "backups", disk_guard=test_guard)
    backup = backups.create(
        backup_id="backup-20260721-v1",
        sources={"raw": raw_root, "models": root / "models"},
        created_at=_START + timedelta(seconds=6),
    )
    backup_manifest = backups.verify(backup)
    restored = backups.restore(backup, target=root / "restored")
    restored_registry = LocalModelRegistry(restored / "models", disk_guard=test_guard)
    restored_snapshot = restored_registry.snapshot("direction", 5)
    restored_registry.verify_release("model-v2")

    active_artifact = (
        root / "models" / "releases" / "model-v2" / "artifacts" / "model.bin"
    )
    active_artifact.unlink()
    recovered = registry.recover_champion(
        task="direction", horizon=5, recorded_at=_START + timedelta(seconds=7)
    )

    drift = _drift_report()
    drills = (
        _drill(
            "drill-model-rollback",
            "model-artifact-missing",
            recovered.champion_version == "model-v1",
            "missing active artifact triggered rollback to hash-verified model-v1",
            7,
        ),
        _schema_drill(),
        _disk_drill(root),
        _reconnect_drill(),
        _drill(
            "drill-backup-restore",
            "raw-model-backup-restore",
            restored_snapshot.champion_version == "model-v2",
            "checksum-verified backup restored raw evidence and the pre-fault model registry",
            11,
        ),
    )
    benchmark = run_m6_benchmark(root / "slo-benchmark")

    log_path = root / "operations.jsonl"
    with log_path.open("x", encoding="utf-8", newline="\n") as stream:
        logger = SafeJsonLogger(stream)
        logger.emit(
            "m6_drills_completed",
            {
                "component": "operations",
                "status": "passed",
                "count": len(drills),
                "model_version": recovered.champion_version,
            },
            timestamp=_START + timedelta(seconds=12),
        )

    report: dict[str, object] = {
        "schema_version": "m6-acceptance-v1",
        "generated_at": (_START + timedelta(seconds=13))
        .isoformat()
        .replace("+00:00", "Z"),
        "scope": "deterministic_offline_operational_acceptance",
        "production_champion_claimed": False,
        "lifecycle": {
            "promoted_champion": promoted.champion_version,
            "recovered_champion": recovered.champion_version,
            "rejected_after_recovery": list(recovered.rejected_versions),
            "event_count": len(registry.events()),
            "ledger_head_hash": recovered.ledger_head_hash,
        },
        "drift": drift.model_dump(mode="json"),
        "backup": {
            "backup_id": backup_manifest.backup_id,
            "item_count": len(backup_manifest.items),
            "manifest_hash": backup_manifest.manifest_hash,
            "restored_champion": restored_snapshot.champion_version,
        },
        "drills": [item.model_dump(mode="json") for item in drills],
        "slo": benchmark,
        "api_slo_reference": "docs/reports/m5-performance-report.md",
        "safe_log": {
            "relative_path": "operations.jsonl",
            "sha256": file_sha256(log_path),
        },
        "all_drills_passed": all(item.status is DrillStatus.PASSED for item in drills),
        "all_local_slos_passed": benchmark["all_passed"],
    }
    report_path = root / "m6-acceptance.json"
    report_hash = write_immutable_json(report_path, report)
    return {
        "report_path": report_path.relative_to(root).as_posix(),
        "report_sha256": report_hash,
        "all_drills_passed": report["all_drills_passed"],
        "all_local_slos_passed": report["all_local_slos_passed"],
        "recovered_champion": recovered.champion_version,
        "drift_overall": drift.overall_level.value,
    }


def _drift_report() -> DriftReport:
    reference = [float(value) for value in range(100)]
    current = [float(value) + 4.0 for value in range(100)]
    data_psi = population_stability_index(reference, current)
    reference_brier = brier_score([0.2, 0.8] * 50, [0, 1] * 50)
    current_brier = brier_score([0.35, 0.65] * 50, [0, 1] * 50)
    metrics = (
        assess_upper_bound(
            metric_id="data-psi",
            family=DriftFamily.DATA,
            metric_name="psi",
            value=data_psi,
            sample_count=100,
            minimum_sample_size=50,
            warning_threshold=0.10,
            critical_threshold=0.25,
            units="ratio",
            detail="100-symbol daily input distribution",
        ),
        assess_upper_bound(
            metric_id="feature-psi",
            family=DriftFamily.FEATURE,
            metric_name="psi",
            value=0.12,
            sample_count=100,
            minimum_sample_size=50,
            warning_threshold=0.10,
            critical_threshold=0.25,
            units="ratio",
            detail="rolling core feature distribution",
        ),
        assess_upper_bound(
            metric_id="calibration-ece",
            family=DriftFamily.CALIBRATION,
            metric_name="ece",
            value=0.03,
            sample_count=100,
            minimum_sample_size=50,
            warning_threshold=0.05,
            critical_threshold=0.10,
            units="probability",
            detail="rolling probability calibration error",
        ),
        assess_upper_bound(
            metric_id="concept-brier-gap",
            family=DriftFamily.CONCEPT,
            metric_name="brier-gap",
            value=current_brier - reference_brier,
            sample_count=100,
            minimum_sample_size=50,
            warning_threshold=0.03,
            critical_threshold=0.08,
            units="score",
            detail="current Brier minus registered reference Brier",
        ),
        assess_target_deviation(
            metric_id="interval-coverage-gap",
            family=DriftFamily.INTERVAL,
            metric_name="coverage-gap",
            observed=0.65,
            target=0.80,
            sample_count=20,
            minimum_sample_size=50,
            warning_threshold=0.05,
            critical_threshold=0.10,
            units="probability",
            detail="sample count remains below the interval alert minimum",
        ),
    )
    return build_drift_report(
        report_id="drift-20260721-v1",
        as_of=_START,
        model_version="model-v2",
        dataset_revision="dataset-v1",
        metrics=metrics,
    )


def _schema_drill() -> RecoveryDrill:
    passed = False
    try:
        require_exact_schema(
            {"symbol": "VARCHAR", "close": "DOUBLE"},
            {"symbol": "VARCHAR", "close": "VARCHAR"},
        )
    except SchemaMismatch:
        passed = True
    return _drill(
        "drill-schema-gate",
        "schema-change",
        passed,
        "changed close type was rejected before publication",
        8,
    )


def _disk_drill(root: Path) -> RecoveryDrill:
    guard = DiskSpaceGuard(
        minimum_free_bytes=100,
        minimum_free_ratio=0,
        usage=lambda _path: (1_000, 950, 50),
    )
    passed = False
    try:
        guard.require(root, planned_bytes=1)
    except InsufficientDiskSpace:
        passed = True
    return _drill(
        "drill-disk-reserve",
        "disk-insufficient",
        passed,
        "write was refused before the configured disk reserve was crossed",
        9,
    )


class _FakeQuoteContext:
    def __init__(self, result: SdkCallResult[str]) -> None:
        self.result = result

    def request(self) -> SdkCallResult[str]:
        return self.result

    def close(self) -> None:
        return None


def _reconnect_drill() -> RecoveryDrill:
    failure = _FakeQuoteContext(
        SdkCallResult.failure(
            OpenDError(
                OpenDErrorClass.CONNECTION_FAILED,
                "OpenD connection failed",
                retryable=True,
            )
        )
    )
    success = _FakeQuoteContext(SdkCallResult.success("continued-from-watermark"))
    contexts = iter([failure, success])
    with ResilientQuoteSession(
        lambda: next(contexts), delays=(0,), sleeper=lambda _delay: None
    ) as session:
        result = session.call(
            lambda context: cast(_FakeQuoteContext, context).request()
        )
    passed = result.value == "continued-from-watermark" and len(session.attempts) == 1
    return _drill(
        "drill-opend-reconnect",
        "opend-disconnect",
        passed,
        "retryable disconnect recreated the read-only context once; caller watermark stayed external",
        10,
    )


def _drill(
    drill_id: str,
    scenario: str,
    passed: bool,
    evidence: str,
    second: int,
) -> RecoveryDrill:
    started = _START + timedelta(seconds=second)
    return RecoveryDrill(
        drill_id=drill_id,
        scenario=scenario,
        status=DrillStatus.PASSED if passed else DrillStatus.FAILED,
        started_at=started,
        finished_at=started + timedelta(milliseconds=1),
        evidence=evidence,
    )
