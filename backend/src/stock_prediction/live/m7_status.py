"""Side-effect-free aggregation of M7 lifecycle evidence for local UI clients."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from stock_prediction.contracts.m7_status import (
    M7CapabilityLifecycleStatus,
    M7DriftLifecycleStatus,
    M7LifecycleAuditEvent,
    M7LifecycleEvidenceSection,
    M7LifecycleStatus,
    M7PartialTrainingSummary,
)
from stock_prediction.contracts.operations import ModelCapabilityBundle
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_audit import M7AuditError, M7AuditLog
from stock_prediction.live.m7_blind import M7BlindLedgerError, M7BlindWindowLedger
from stock_prediction.live.m7_orchestration import M7OrchestrationStore
from stock_prediction.live.m7_promotion import (
    M7PromotionError,
    load_active_m7_forecast_bundle,
)
from stock_prediction.live.m7_partial_research import M7PartialFoldManifest
from stock_prediction.modeling.m7_package import M7PackageError, load_m7_package
from stock_prediction.modeling.report import file_sha256


_RECIPE_ID: Literal["candidate-recipe-seed100-targeted-v10"] = (
    "candidate-recipe-seed100-targeted-v10"
)
_DISCLAIMER = "实验 Challenger；前瞻盲测未完成前不可晋升，不可作为正式预测或交易信号。"


@dataclass(frozen=True, slots=True)
class _BlindSnapshot:
    blind_state: str
    promotion_state: str
    available_anchors: int = 0
    mature_labels: int = 0
    missing_anchors: int = 0
    next_scheduled_session: date | None = None
    last_available_anchor: date | None = None
    report_hash: str | None = None
    report_evidence: dict[str, Any] | None = None


def build_m7_lifecycle_status(
    paths: LivePaths,
    *,
    executor_configured: bool = False,
) -> M7LifecycleStatus:
    from stock_prediction.live.training_job import independent_training_summary

    errors: list[str] = []
    report = _load_latest_report(paths, errors)
    partial = _load_latest_partial(paths, errors)
    active_hashes = _active_package_hashes(paths, errors)
    capabilities = tuple(
        _capability_status(paths, bundle, report, active_hashes, errors)
        for bundle in ("direction", "range_exit_20")
    )
    events = _recent_events(paths, errors)
    drift = _drift_status(paths, errors)
    has_evidence = report is not None or partial is not None or bool(events) or bool(active_hashes)
    if errors or has_evidence:
        source_status: Literal["ready", "partial", "unavailable"] = (
            "partial" if errors or not _lifecycle_complete(capabilities) else "ready"
        )
    else:
        source_status = "unavailable"
    generated_at = None
    run_id = None
    if report is not None:
        run_id = _text(report.get("run_id"))
        generated_at = _timestamp(report.get("generated_at"), errors)
    return M7LifecycleStatus(
        source_status=source_status,
        recipe_id=_RECIPE_ID,
        latest_training_run_id=run_id,
        latest_partial_run_id=None if partial is None else partial.run_id,
        partial_training_summary=partial,
        independent_training_summary=independent_training_summary(paths),
        latest_generated_at=generated_at,
        executor_configured=executor_configured,
        disclaimer=_DISCLAIMER,
        capabilities=capabilities,
        drift=drift,
        recent_events=events,
        errors=tuple(dict.fromkeys(errors)),
    )


def _load_latest_partial(
    paths: LivePaths,
    errors: list[str],
) -> M7PartialTrainingSummary | None:
    candidates: list[tuple[datetime, M7PartialFoldManifest]] = []
    for path in paths.models.glob("runs/*/partial-research/*/*.json"):
        try:
            manifest = M7PartialFoldManifest.model_validate_json(
                path.read_text(encoding="utf-8"), strict=True
            )
        except (OSError, ValueError):
            errors.append("m7_partial_manifest_invalid")
            continue
        candidates.append((manifest.generated_at, manifest))
    if not candidates:
        return None
    _, latest = max(candidates, key=lambda row: (row[0], row[1].run_id, row[1].bundle_id))
    same = [
        manifest
        for _generated, manifest in candidates
        if manifest.run_id == latest.run_id and manifest.bundle_id == latest.bundle_id
    ]
    operation = _partial_operation(paths, latest.run_id)
    return M7PartialTrainingSummary(
        run_id=latest.run_id,
        recipe_id=latest.recipe_id,
        bundle_id=latest.bundle_id,
        operation_status=_text(operation.get("status")) or "unknown",
        current_stage=_text(operation.get("current_stage")),
        successful_trials=0,
        failed_trials=0,
        valid_folds=len({manifest.fold_id for manifest in same}),
        training_devices=tuple(
            sorted({device for manifest in same for device in manifest.training_devices})
        ),
        error_code=_text(operation.get("error_code")),
        generated_at=latest.generated_at,
    )


def _partial_operation(paths: LivePaths, run_id: str) -> dict[str, Any]:
    path = paths.root / "operations" / "model-training" / f"{run_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_latest_report(paths: LivePaths, errors: list[str]) -> dict[str, Any] | None:
    candidates: list[tuple[datetime, Path, dict[str, Any]]] = []
    for path in paths.models.glob("runs/*/training-report.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != "m7-targeted-training-report-v1"
        ):
            continue
        generated = _timestamp(payload.get("generated_at"), errors)
        candidates.append(
            (generated or datetime.min.replace(tzinfo=UTC), path, payload)
        )
    if not candidates:
        return None
    return max(candidates, key=lambda row: (row[0], row[1].as_posix()))[2]


def _active_package_hashes(paths: LivePaths, errors: list[str]) -> set[str]:
    try:
        active = load_active_m7_forecast_bundle(paths)
    except M7PromotionError:
        errors.append("m7_active_forecast_bundle_invalid")
        return set()
    return (
        set() if active is None else {row.package_sha256 for row in active.components}
    )


def _capability_status(
    paths: LivePaths,
    bundle: ModelCapabilityBundle,
    report: dict[str, Any] | None,
    active_hashes: set[str],
    errors: list[str],
) -> M7CapabilityLifecycleStatus:
    row: dict[str, Any] = {}
    if report is not None and isinstance(report.get("bundle_results"), dict):
        candidate = report["bundle_results"].get(bundle)
        if isinstance(candidate, dict):
            row = candidate
    package_hash = _text(row.get("package_sha256"))
    package_id = _text(row.get("package_id"))
    package = _package_evidence(paths, package_id, errors)
    window_id = _text(row.get("blind_window_id"))
    blind = _BlindSnapshot(
        blind_state=_text(row.get("blind_state")) or "not_started",
        promotion_state=_text(row.get("promotion_state")) or "not_eligible",
    )
    if window_id is not None:
        blind = _blind_status(
            paths,
            window_id=window_id,
            bundle=bundle,
            package_sha256=package_hash,
            current_blind_state=blind.blind_state,
            current_promotion_state=blind.promotion_state,
            errors=errors,
        )
    promotion_allowed = blind.promotion_state == "awaiting_manual_approval"
    block_reasons: list[str] = []
    if not package_hash:
        block_reasons.append("m7_shadow_package_unavailable")
    if blind.available_anchors < 77:
        block_reasons.append("m7_blind_anchor_count_incomplete")
    if blind.mature_labels < 77:
        block_reasons.append("m7_blind_labels_incomplete")
    if not promotion_allowed:
        block_reasons.append("m7_manual_approval_unavailable")
    evidence_sections = _bundle_evidence_sections(report, bundle, errors)
    if blind.report_evidence is not None:
        evidence_sections["blind_report"] = M7LifecycleEvidenceSection(
            status="completed",
            reason=None,
            payload=blind.report_evidence,
        )
    return M7CapabilityLifecycleStatus(
        bundle_id=bundle,
        development_state=_text(row.get("development_state")) or "not_started",
        shadow_state=_text(row.get("shadow_state")) or "not_frozen",
        blind_state=blind.blind_state,
        promotion_state=blind.promotion_state,
        package_id=package_id,
        package_sha256=package_hash,
        code_commit=package.get("code_commit"),
        universe_version=package.get("universe_version"),
        dataset_revision=package.get("dataset_revision"),
        feature_set_version=package.get("feature_set_version"),
        feature_input_hash=package.get("feature_input_hash"),
        label_version=package.get("label_version"),
        split_manifest_hash=package.get("split_manifest_hash"),
        candidate_matrix_hash=package.get("candidate_matrix_hash"),
        dependency_lock_hash=package.get("dependency_lock_hash"),
        model_components=tuple(package.get("model_components", ())),
        blend_weights=_blend_weights(row.get("gate")),
        blind_window_id=window_id,
        incumbent_id=_text(row.get("incumbent_id")),
        blind_available_anchors=blind.available_anchors,
        blind_mature_labels=blind.mature_labels,
        blind_missing_anchors=blind.missing_anchors,
        blind_next_scheduled_session=blind.next_scheduled_session,
        blind_last_available_anchor=blind.last_available_anchor,
        blind_report_hash=blind.report_hash,
        promotion_allowed=promotion_allowed,
        is_active_champion=package_hash is not None and package_hash in active_hashes,
        block_reasons=tuple(block_reasons),
        evidence_sections=evidence_sections,
    )


def _bundle_evidence_sections(
    report: dict[str, Any] | None,
    bundle: ModelCapabilityBundle,
    errors: list[str],
) -> dict[str, M7LifecycleEvidenceSection]:
    if report is None:
        return {}
    raw_sections = report.get("m7_sections")
    if not isinstance(raw_sections, dict):
        return {}
    output: dict[str, M7LifecycleEvidenceSection] = {}
    for name in (
        "feature_ablation",
        "calibration_selection",
        "baseline_deltas",
        "bootstrap_results",
        "determinism_results",
        "resource_observations",
        "economic_diagnostics",
        "explanation_manifest",
    ):
        section = raw_sections.get(name)
        if not isinstance(section, dict):
            continue
        status = section.get("status")
        reason = section.get("reason")
        payload = section.get("payload")
        if status == "completed" and isinstance(payload, list):
            bundle_payload = next(
                (
                    row.get("payload")
                    for row in payload
                    if isinstance(row, dict) and row.get("bundle_id") == bundle
                ),
                None,
            )
            if bundle_payload is None:
                continue
            payload = bundle_payload
        try:
            output[name] = M7LifecycleEvidenceSection.model_validate(
                {
                    "status": status,
                    "reason": reason,
                    "payload": payload,
                },
                strict=True,
            )
        except ValidationError:
            errors.append(f"m7_{name}_section_invalid")
    return output


def _package_evidence(
    paths: LivePaths,
    package_id: str | None,
    errors: list[str],
) -> dict[str, Any]:
    if package_id is None:
        return {}
    try:
        manifest = load_m7_package(paths.models / "m7-packages", package_id)
    except M7PackageError:
        errors.append("m7_package_integrity_invalid")
        return {}
    return {
        "code_commit": manifest.code_commit,
        "universe_version": manifest.universe_version,
        "dataset_revision": manifest.dataset_revision,
        "feature_set_version": manifest.feature_set_version,
        "feature_input_hash": manifest.feature_input_hash,
        "label_version": manifest.label_version,
        "split_manifest_hash": manifest.split_manifest_hash,
        "candidate_matrix_hash": manifest.candidate_matrix_hash,
        "dependency_lock_hash": manifest.dependency_lock_hash,
        "model_components": tuple(
            item.artifact_id for item in manifest.model_artifacts
        ),
    }


def _blend_weights(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    weights = value.get("blend_weights")
    if not isinstance(weights, dict):
        return ()
    return tuple(
        f"{name}={weight}"
        for name, weight in sorted(weights.items())
        if isinstance(name, str) and isinstance(weight, (int, float))
    )


def _blind_status(
    paths: LivePaths,
    *,
    window_id: str,
    bundle: ModelCapabilityBundle,
    package_sha256: str | None,
    current_blind_state: str,
    current_promotion_state: str,
    errors: list[str],
) -> _BlindSnapshot:
    directory = paths.models / "m7-blind" / window_id
    missing = 0
    next_session = None
    last_anchor = None
    try:
        status = M7BlindWindowLedger(directory).status()
        blind_state: str = status.state
        anchors = status.available_anchor_count
        labels = status.mature_label_count
        missing = status.missing_anchor_count
        next_session = status.next_scheduled_session
        last_anchor = status.last_available_anchor
    except M7BlindLedgerError:
        errors.append("m7_blind_manifest_invalid")
        blind_state = current_blind_state
        anchors = min(77, len(tuple((directory / "predictions").glob("*.json"))))
        labels = min(77, len(tuple((directory / "labels").glob("*.json"))))
    promotion_state = current_promotion_state
    report_hash: str | None = None
    report_evidence: dict[str, Any] | None = None
    terminal = _json_object(directory / "terminal.json")
    if terminal is not None and terminal.get("status") == "consumed":
        blind_state = "consumed"
        requested_report_hash = _text(terminal.get("blind_report_hash"))
        review_path = (
            None
            if requested_report_hash is None
            else directory / "reports" / f"{requested_report_hash}.json"
        )
        review = None if review_path is None else _json_object(review_path)
        integrity_valid = bool(
            requested_report_hash is not None
            and review_path is not None
            and review is not None
            and file_sha256(review_path) == requested_report_hash
            and review.get("schema_version") == "m7-blind-report-v1"
            and review.get("window_id") == window_id
            and review.get("bundle_id") == bundle
            and package_sha256 is not None
            and review.get("package_sha256") == package_sha256
        )
        if not integrity_valid:
            errors.append("m7_blind_report_integrity_invalid")
            review = None
        else:
            report_hash = requested_report_hash
        decision = None if review is None else review.get("decision")
        if isinstance(decision, dict):
            promotion_state = _text(decision.get("status")) or promotion_state
            assert review is not None
            anchors = int(review.get("available_anchor_count", anchors))
            labels = int(review.get("mature_label_count", labels))
            evidence = review.get("evidence")
            if isinstance(evidence, dict):
                report_evidence = evidence
            else:
                errors.append("m7_blind_report_evidence_invalid")
    elif terminal is not None and terminal.get("status") == "invalidated":
        blind_state = "invalidated"
        promotion_state = "rejected"
    return _BlindSnapshot(
        blind_state=blind_state,
        promotion_state=promotion_state,
        available_anchors=min(77, anchors),
        mature_labels=min(77, labels),
        missing_anchors=missing,
        next_scheduled_session=next_session,
        last_available_anchor=last_anchor,
        report_hash=report_hash,
        report_evidence=report_evidence,
    )


def _recent_events(
    paths: LivePaths, errors: list[str]
) -> tuple[M7LifecycleAuditEvent, ...]:
    target = paths.models / "m7-audit" / "events.jsonl"
    if not target.is_file():
        return ()
    try:
        events = M7AuditLog(paths).events()[-20:]
    except M7AuditError:
        errors.append("m7_audit_ledger_invalid")
        return ()
    return tuple(
        M7LifecycleAuditEvent(
            event_id=event.event_id,
            event_type=event.event_type,
            recorded_at=event.recorded_at,
            actor=event.actor,
            run_id=event.run_id,
            bundle_id=event.bundle_id,
            detail=event.detail,
        )
        for event in reversed(events)
    )


def _drift_status(paths: LivePaths, errors: list[str]) -> M7DriftLifecycleStatus:
    root = paths.root / "m7-orchestration"
    if not root.is_dir():
        return M7DriftLifecycleStatus(
            state="none",
            consecutive_daily_warning_sessions=0,
            consecutive_mature_weekly_breaches=0,
            reason="no_drift_evidence",
        )
    try:
        decision = M7OrchestrationStore(paths).drift_decision()
    except (OSError, ValidationError, ValueError):
        errors.append("m7_drift_evidence_invalid")
        return M7DriftLifecycleStatus(
            state="none",
            consecutive_daily_warning_sessions=0,
            consecutive_mature_weekly_breaches=0,
            reason="drift_evidence_invalid",
        )
    evidence = _latest_drift_evidence(paths, errors)
    retryability: Literal["automatic", "manual", "not_applicable"] = (
        "not_applicable" if decision.state == "none" else "automatic"
    )
    action = {
        "none": "continue_monitoring",
        "warning": "investigate_inputs_and_wait_for_confirmation",
        "review_required": "review_mature_outcomes",
        "training_authorized": "review_queued_challenger_training",
    }[decision.state]
    return M7DriftLifecycleStatus(
        state=decision.state,
        consecutive_daily_warning_sessions=decision.consecutive_daily_warning_sessions,
        consecutive_mature_weekly_breaches=decision.consecutive_mature_weekly_breaches,
        reason=decision.reason,
        last_updated_at=evidence["last_updated_at"],
        affected_scopes=tuple(evidence["affected_scopes"]),
        affected_inputs=tuple(evidence["affected_inputs"]),
        retryability=retryability,
        recommended_action=action,
        feature_warning=evidence["feature_warning"],
        prediction_warning=evidence["prediction_warning"],
        outcome_warning=evidence["outcome_warning"],
        core_feature_psi_fraction_above_020=evidence[
            "core_feature_psi_fraction_above_020"
        ],
        missingness_rate=evidence["missingness_rate"],
        coverage_rate=evidence["coverage_rate"],
        ood_rate=evidence["ood_rate"],
    )


def _latest_drift_evidence(paths: LivePaths, errors: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "last_updated_at": None,
        "affected_scopes": [],
        "affected_inputs": [],
        "feature_warning": None,
        "prediction_warning": None,
        "outcome_warning": None,
        "core_feature_psi_fraction_above_020": None,
        "missingness_rate": None,
        "coverage_rate": None,
        "ood_rate": None,
    }
    daily_root = paths.models / "m7-drift" / "daily"
    daily_directories = sorted(
        (path for path in daily_root.glob("*") if path.is_dir()), reverse=True
    )
    if daily_directories:
        rows: list[dict[str, Any]] = []
        try:
            for path in sorted(daily_directories[0].glob("*.json")):
                payload = json.loads(path.read_text(encoding="utf-8"))
                report = payload["report"]
                if payload.get(
                    "schema_version"
                ) != "m7-daily-drift-component-v1" or not isinstance(report, dict):
                    raise ValueError
                rows.append(payload)
            if rows:
                reports = [row["report"] for row in rows]
                result["feature_warning"] = any(
                    report.get("feature_warning") is True for report in reports
                )
                result["prediction_warning"] = any(
                    report.get("prediction_distribution_shifted") is True
                    for report in reports
                )
                result["core_feature_psi_fraction_above_020"] = max(
                    float(report["core_feature_psi_fraction_above_020"])
                    for report in reports
                )
                result["missingness_rate"] = max(
                    float(report["missingness_rate"]) for report in reports
                )
                result["coverage_rate"] = min(
                    float(report["coverage_rate"]) for report in reports
                )
                result["ood_rate"] = max(
                    float(report["ood_rate"]) for report in reports
                )
                for row in rows:
                    bundle = _text(row.get("bundle_id"))
                    if bundle is not None:
                        result["affected_scopes"].append(bundle)
                    report = row["report"]
                    result["affected_inputs"].extend(
                        f"feature:{name}"
                        for name, value in report["feature_psi"].items()
                        if float(value) > 0.20
                    )
                    result["affected_inputs"].extend(
                        f"prediction:{name}"
                        for name, value in report["prediction_psi"].items()
                        if float(value) > 0.20
                    )
                observed = tuple(
                    value
                    for row in rows
                    if (value := _optional_utc(row.get("observed_at"))) is not None
                )
                result["last_updated_at"] = (
                    max(observed)
                    if observed
                    else datetime.combine(
                        date.fromisoformat(daily_directories[0].name),
                        datetime.min.time(),
                        tzinfo=UTC,
                    )
                )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            errors.append("m7_daily_drift_detail_invalid")
    weekly_files = sorted(
        (paths.models / "m7-drift" / "weekly").glob("*.json"), reverse=True
    )
    if weekly_files:
        try:
            weekly = json.loads(weekly_files[0].read_text(encoding="utf-8"))
            components = weekly["components"]
            if not isinstance(components, list):
                raise ValueError
            result["outcome_warning"] = any(
                float(row["relative_brier_degradation"]) > 0.05
                or float(row["calibration_error"]) > 0.05
                for row in components
            )
            result["affected_inputs"].extend(
                f"outcome:{row['bundle_id']}"
                for row in components
                if float(row["relative_brier_degradation"]) > 0.05
                or float(row["calibration_error"]) > 0.05
            )
            weekly_at = _optional_utc(weekly.get("observed_at"))
            if weekly_at is not None and (
                result["last_updated_at"] is None
                or weekly_at > result["last_updated_at"]
            ):
                result["last_updated_at"] = weekly_at
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            errors.append("m7_weekly_drift_detail_invalid")
    result["affected_scopes"] = list(dict.fromkeys(result["affected_scopes"]))
    result["affected_inputs"] = list(dict.fromkeys(result["affected_inputs"]))
    return result


def _optional_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _lifecycle_complete(rows: tuple[M7CapabilityLifecycleStatus, ...]) -> bool:
    return all(row.is_active_champion for row in rows)


def _json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _timestamp(value: object, errors: list[str]) -> datetime | None:
    if not isinstance(value, str):
        errors.append("m7_training_generated_at_invalid")
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        errors.append("m7_training_generated_at_invalid")
        return None


__all__ = ["build_m7_lifecycle_status"]
