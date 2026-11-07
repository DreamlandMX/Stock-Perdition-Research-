from __future__ import annotations

from datetime import UTC, date, datetime
import json
from pathlib import Path

from fastapi.testclient import TestClient

from stock_prediction.api.app import create_app
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_audit import M7AuditLog
from stock_prediction.live.m7_orchestration import (
    M7DailyDriftEvidence,
    M7OrchestrationStore,
)
from stock_prediction.live.m7_status import build_m7_lifecycle_status
from stock_prediction.modeling.report import write_immutable_json


def test_m7_status_is_explicitly_unavailable_before_first_targeted_run(
    tmp_path: Path,
) -> None:
    status = build_m7_lifecycle_status(LivePaths.from_workspace(tmp_path))

    assert status.source_status == "unavailable"
    assert status.recipe_id == "candidate-recipe-seed100-targeted-v10"
    assert status.latest_partial_run_id is None
    assert status.partial_training_summary is None
    assert status.executor_configured is False
    assert [row.bundle_id for row in status.capabilities] == [
        "direction",
        "range_exit_20",
    ]
    assert all(row.promotion_allowed is False for row in status.capabilities)
    assert all(row.blind_required_anchors == 77 for row in status.capabilities)
    assert "不可作为正式预测或交易信号" in status.disclaimer


def test_m7_status_exposes_actionable_latest_drift_inputs(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    M7OrchestrationStore(paths).record_daily_evidence(
        M7DailyDriftEvidence(
            session=date(2026, 8, 25),
            eligible=True,
            core_feature_psi_fraction_above_020=0.25,
            prediction_distribution_shifted=True,
            evidence_hash="a" * 64,
        )
    )
    feature_psi = {f"feature_{index}": 0.0 for index in range(20)}
    feature_psi["feature_0"] = 0.31
    write_immutable_json(
        paths.models / "m7-drift" / "daily" / "2026-08-25" / "direction.json",
        {
            "schema_version": "m7-daily-drift-component-v1",
            "session": "2026-08-25",
            "observed_at": "2026-08-25T22:00:00Z",
            "bundle_id": "direction",
            "package_id": "direction-package",
            "package_sha256": "b" * 64,
            "reference_hash": "c" * 64,
            "report": {
                "feature_warning": True,
                "prediction_distribution_shifted": True,
                "core_feature_psi_fraction_above_020": 0.25,
                "missingness_rate": 0.01,
                "coverage_rate": 0.99,
                "ood_rate": 0.04,
                "feature_psi": feature_psi,
                "prediction_psi": {"direction_h1_up": 0.27},
            },
        },
        pretty=False,
    )

    status = build_m7_lifecycle_status(paths)

    assert status.drift.last_updated_at == datetime(2026, 8, 25, 22, tzinfo=UTC)
    assert status.drift.affected_scopes == ("direction",)
    assert status.drift.affected_inputs == (
        "feature:feature_0",
        "prediction:direction_h1_up",
    )
    assert status.drift.retryability == "automatic"
    assert status.drift.feature_warning is True
    assert status.drift.prediction_warning is True
    assert status.drift.coverage_rate == 0.99


def test_m7_status_joins_latest_report_blind_progress_drift_and_audit(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    run_id = "m7-run-001"
    window_id = "promotion-seed100-aaaaaaaaaaaa-v1"
    report = {
        "schema_version": "m7-targeted-training-report-v1",
        "run_id": run_id,
        "generated_at": "2026-08-24T16:00:00Z",
        "candidate_recipe_version": "candidate-recipe-seed100-targeted-v7",
        "bundle_results": {
            "direction": {
                "bundle_id": "direction",
                "development_state": "passed",
                "shadow_state": "frozen",
                "blind_state": "collecting",
                "promotion_state": "blind_collecting",
                "package_id": "m7-direction-001",
                "package_sha256": "a" * 64,
                "blind_window_id": window_id,
                "incumbent_id": "direction-v1",
            }
        },
        "m7_sections": {
            "baseline_deltas": {
                "status": "completed",
                "reason": None,
                "payload": [
                    {
                        "bundle_id": "direction",
                        "payload": {
                            "1": {
                                "status": "failed",
                                "metrics": {"candidate_brier_mean": 0.64},
                            }
                        },
                    }
                ],
            },
            "explanation_manifest": {
                "status": "not_run",
                "reason": "development_gate_failed",
                "payload": None,
            },
        },
    }
    report_path = paths.models / "runs" / run_id / "training-report.json"
    write_immutable_json(report_path, report, pretty=False)
    write_immutable_json(
        paths.models / "LATEST.json",
        {
            "schema_version": "m7-latest-training-pointer-v1",
            "run_id": run_id,
            "relative_path": f"runs/{run_id}/training-report.json",
        },
        pretty=False,
    )
    blind = paths.models / "m7-blind" / window_id
    blind.mkdir(parents=True)
    (blind / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "m7-blind-ledger-v1",
                "window_id": window_id,
                "bundle_id": "direction",
                "package_sha256": "a" * 64,
                "registration_hash": "b" * 64,
                "scheduled_sessions": [f"2026-09-{day:02d}" for day in range(1, 10)],
            }
        ),
        encoding="utf-8",
    )
    for day in range(1, 4):
        target = blind / "predictions" / f"2026-09-{day:02d}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
    M7AuditLog(paths).append(
        event_type="shadow_frozen",
        recorded_at=datetime(2026, 8, 24, 16, tzinfo=UTC),
        actor="model-training-worker",
        run_id=run_id,
        bundle_id="direction",
        package_sha256="a" * 64,
        detail={"package_id": "m7-direction-001"},
    )

    status = build_m7_lifecycle_status(paths)

    direction = status.capabilities[0]
    assert status.source_status == "partial"
    assert status.latest_training_run_id == run_id
    assert direction.package_id == "m7-direction-001"
    assert direction.blind_available_anchors == 3
    assert direction.promotion_allowed is False
    assert status.recent_events[0].event_type == "shadow_frozen"
    assert "m7_blind_manifest_invalid" in status.errors
    assert (
        direction.evidence_sections["baseline_deltas"].payload["1"]["metrics"][
            "candidate_brier_mean"
        ]
        == 0.64
    )
    assert direction.evidence_sections["explanation_manifest"].status == "not_run"


def test_m7_status_marks_manual_approval_only_from_consumed_blind_report(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    run_id = "m7-run-002"
    window_id = "promotion-seed100-bbbbbbbbbbbb-v1"
    report = {
        "schema_version": "m7-targeted-training-report-v1",
        "run_id": run_id,
        "generated_at": "2026-08-24T16:00:00Z",
        "candidate_recipe_version": "candidate-recipe-seed100-targeted-v7",
        "bundle_results": {
            "direction": {
                "bundle_id": "direction",
                "development_state": "passed",
                "shadow_state": "frozen",
                "blind_state": "collecting",
                "promotion_state": "blind_collecting",
                "package_id": "m7-direction-002",
                "package_sha256": "c" * 64,
                "blind_window_id": window_id,
                "incumbent_id": None,
            }
        },
    }
    write_immutable_json(
        paths.models / "runs" / run_id / "training-report.json",
        report,
        pretty=False,
    )
    blind = paths.models / "m7-blind" / window_id
    reports = blind / "reports"
    reports.mkdir(parents=True)
    review_payload = {
        "schema_version": "m7-blind-report-v1",
        "window_id": window_id,
        "bundle_id": "direction",
        "package_sha256": "c" * 64,
        "available_anchor_count": 77,
        "mature_label_count": 77,
        "decision": {
            "status": "awaiting_manual_approval",
        },
        "evidence": {
            "schema_version": "m7-blind-review-evidence-v1",
            "non_causal_caveat": True,
            "reliability": [
                {
                    "horizon": 1,
                    "class_name": "up",
                    "model": "candidate",
                    "bin_index": 6,
                    "lower_bound": 0.6,
                    "upper_bound": 0.7,
                    "mean_prediction": 0.64,
                    "observed_frequency": 0.62,
                    "sample_count": 88,
                }
            ],
        },
    }
    pending_report = reports / "pending.json"
    report_hash = write_immutable_json(pending_report, review_payload, pretty=False)
    pending_report.replace(reports / f"{report_hash}.json")
    (blind / "terminal.json").write_text(
        json.dumps(
            {
                "status": "consumed",
                "blind_report_hash": report_hash,
                "reason": "blind_report_awaiting_manual_approval",
            }
        ),
        encoding="utf-8",
    )

    status = build_m7_lifecycle_status(paths)

    direction = status.capabilities[0]
    assert direction.blind_state == "consumed"
    assert direction.promotion_state == "awaiting_manual_approval"
    assert direction.promotion_allowed is True
    assert direction.blind_available_anchors == 77
    assert direction.blind_mature_labels == 77
    assert direction.blind_report_hash == report_hash
    assert direction.evidence_sections["blind_report"].status == "completed"
    assert (
        direction.evidence_sections["blind_report"].payload["reliability"][0][
            "sample_count"
        ]
        == 88
    )


def test_m7_status_rejects_tampered_consumed_blind_report(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    run_id = "m7-run-003"
    window_id = "promotion-seed100-cccccccccccc-v1"
    package_hash = "e" * 64
    write_immutable_json(
        paths.models / "runs" / run_id / "training-report.json",
        {
            "schema_version": "m7-targeted-training-report-v1",
            "run_id": run_id,
            "generated_at": "2026-08-24T16:00:00Z",
            "candidate_recipe_version": "candidate-recipe-seed100-targeted-v7",
            "bundle_results": {
                "direction": {
                    "bundle_id": "direction",
                    "development_state": "passed",
                    "shadow_state": "frozen",
                    "blind_state": "collecting",
                    "promotion_state": "blind_collecting",
                    "package_sha256": package_hash,
                    "blind_window_id": window_id,
                }
            },
        },
        pretty=False,
    )
    blind = paths.models / "m7-blind" / window_id
    reports = blind / "reports"
    reports.mkdir(parents=True)
    report_hash = "f" * 64
    (reports / f"{report_hash}.json").write_text(
        json.dumps(
            {
                "schema_version": "m7-blind-report-v1",
                "window_id": window_id,
                "bundle_id": "direction",
                "package_sha256": package_hash,
                "decision": {"status": "awaiting_manual_approval"},
                "evidence": {"reliability": []},
            }
        ),
        encoding="utf-8",
    )
    (blind / "terminal.json").write_text(
        json.dumps(
            {
                "status": "consumed",
                "blind_report_hash": report_hash,
                "reason": "blind_report_awaiting_manual_approval",
            }
        ),
        encoding="utf-8",
    )

    status = build_m7_lifecycle_status(paths)

    direction = status.capabilities[0]
    assert direction.promotion_allowed is False
    assert direction.blind_report_hash is None
    assert "blind_report" not in direction.evidence_sections
    assert "m7_blind_report_integrity_invalid" in status.errors


def test_m7_status_api_returns_explicit_read_only_contract(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)

    with TestClient(
        create_app(m7_status_provider=lambda: build_m7_lifecycle_status(paths))
    ) as client:
        response = client.get("/api/v1/operations/m7-status")

    assert response.status_code == 200
    assert response.json()["schema_version"] == "m7-lifecycle-status-v1"
    assert response.json()["capabilities"][0]["bundle_id"] == "direction"


def test_m7_status_api_fails_closed_when_runtime_provider_is_absent() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/operations/m7-status")

    assert response.status_code == 503
    assert response.json()["error_code"] == "m7_status_unavailable"
