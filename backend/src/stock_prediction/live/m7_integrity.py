"""Integrity-only verification and automatic rollback for active M7 bundles."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
import math
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from stock_prediction.contracts.operations import ModelCapabilityBundle
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_audit import M7AuditLog
from stock_prediction.live.m7_promotion import (
    M7ForecastBundleManifest,
    M7PromotionError,
    load_active_m7_forecast_bundle,
    load_m7_preserved_legacy_evidence,
)
from stock_prediction.modeling.m7_package import (
    M7PackageManifest,
    detect_m7_runtime_compatibility,
    load_m7_package,
)
from stock_prediction.modeling.report import deterministic_hash, write_immutable_json


class M7ServingObservation(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    bundle_id: ModelCapabilityBundle
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    universe_version: str = Field(min_length=1)
    expected_symbol_count: Literal[100]
    observed_symbol_count: int = Field(ge=0)
    direction_probabilities: tuple[tuple[float, float, float], ...] | None
    range_probability_5d: tuple[float, ...] | None
    range_probability_20d: tuple[float, ...] | None
    prediction_artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class M7IntegrityResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    action: Literal["healthy", "rolled_back", "deactivated"]
    failed_bundle_sha256: str | None
    restored_bundle_sha256: str | None
    incident_path: Path


ManifestVerifier = Callable[[M7ForecastBundleManifest], None]


class M7IntegrityMonitor:
    def __init__(
        self,
        paths: LivePaths,
        *,
        manifest_verifier: ManifestVerifier | None = None,
    ) -> None:
        self.paths = paths
        self.root = paths.models / "m7-forecast-bundles"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.root / ".promotion.lock"))
        self.manifest_verifier = manifest_verifier or self._verify_manifest_packages

    def verify_active(self, *, observed_at: datetime) -> M7IntegrityResult | None:
        pointer = self._pointer_binding()
        if pointer is None:
            return None
        failed_hash = str(pointer["bundle_sha256"])
        previous_hash = pointer.get("previous_bundle_sha256")
        try:
            active = load_active_m7_forecast_bundle(self.paths)
            if active is None:
                return None
            self.manifest_verifier(active)
        except Exception as error:
            return self._rollback(
                failed_bundle_sha256=failed_hash,
                previous_bundle_sha256=(
                    str(previous_hash) if isinstance(previous_hash, str) else None
                ),
                reason=f"active_bundle_integrity_failure:{error}",
                observed_at=observed_at,
            )
        healthy = self._write_event(
            event_type="integrity_check",
            failure_class="none",
            action="healthy",
            bundle_id=active.promoted_bundle_id,
            failed_bundle_sha256=None,
            restored_bundle_sha256=active.bundle_sha256,
            reason="active_bundle_integrity_verified",
            observed_at=observed_at,
            directory="integrity-checks",
        )
        return M7IntegrityResult(
            action="healthy",
            failed_bundle_sha256=None,
            restored_bundle_sha256=active.bundle_sha256,
            incident_path=healthy,
        )

    def verify_observation(
        self,
        observation: M7ServingObservation,
        *,
        observed_at: datetime,
    ) -> M7IntegrityResult | None:
        active = load_active_m7_forecast_bundle(self.paths)
        if active is None:
            return None
        component = next(
            (
                item
                for item in active.components
                if item.bundle_id == observation.bundle_id
            ),
            None,
        )
        failure = _observation_failure(
            observation,
            component_hash=(None if component is None else component.package_sha256),
        )
        if failure is None:
            return None
        return self._rollback(
            failed_bundle_sha256=active.bundle_sha256,
            previous_bundle_sha256=active.previous_bundle_sha256,
            reason=f"serving_integrity_failure:{failure}",
            observed_at=observed_at,
        )

    def record_performance_drift(
        self,
        *,
        bundle_id: ModelCapabilityBundle,
        reason: str,
        observed_at: datetime,
    ) -> Path:
        if not reason.strip():
            raise ValueError("m7_performance_drift_reason_required")
        active = load_active_m7_forecast_bundle(self.paths)
        return self._write_event(
            event_type="performance_drift",
            failure_class="performance",
            action="alert_only",
            bundle_id=bundle_id,
            failed_bundle_sha256=(None if active is None else active.bundle_sha256),
            restored_bundle_sha256=None,
            reason=reason,
            observed_at=observed_at,
            directory="drift-alerts",
        )

    def _rollback(
        self,
        *,
        failed_bundle_sha256: str,
        previous_bundle_sha256: str | None,
        reason: str,
        observed_at: datetime,
    ) -> M7IntegrityResult:
        with self._lock:
            pointer = self._pointer_binding()
            if pointer is None or pointer.get("bundle_sha256") != failed_bundle_sha256:
                raise M7PromotionError("m7_integrity_rollback_cas_conflict")
            restored: M7ForecastBundleManifest | None = None
            if previous_bundle_sha256 is not None:
                restored = _load_manifest(self.root, previous_bundle_sha256)
                _replace_pointer(self.root, restored)
                action: Literal["rolled_back", "deactivated"] = "rolled_back"
            else:
                (self.root / "ACTIVE.json").unlink()
                action = "deactivated"
            incident = self._write_event(
                event_type="automatic_rollback",
                failure_class="integrity",
                action=action,
                bundle_id=None,
                failed_bundle_sha256=failed_bundle_sha256,
                restored_bundle_sha256=(
                    None if restored is None else restored.bundle_sha256
                ),
                reason=reason,
                observed_at=observed_at,
                directory="incidents",
            )
            return M7IntegrityResult(
                action=action,
                failed_bundle_sha256=failed_bundle_sha256,
                restored_bundle_sha256=(
                    None if restored is None else restored.bundle_sha256
                ),
                incident_path=incident,
            )

    def _verify_manifest_packages(self, manifest: M7ForecastBundleManifest) -> None:
        if load_m7_preserved_legacy_evidence(self.paths) != (
            manifest.preserved_legacy_evidence
        ):
            raise M7PromotionError("m7_integrity_preserved_evidence_mismatch")
        runtime = detect_m7_runtime_compatibility()
        packages = tuple(
            load_m7_package(
                self.paths.models / "m7-packages",
                component.package_id,
                current_runtime=runtime,
            )
            for component in manifest.components
        )
        for component, package in zip(manifest.components, packages, strict=True):
            if (
                component.bundle_id != package.bundle_id
                or component.package_sha256 != package.package_sha256
            ):
                raise M7PromotionError("m7_integrity_component_identity_mismatch")
        _verify_compatible_packages(packages, manifest.compatibility_signature)

    def _pointer_binding(self) -> dict[str, object] | None:
        pointer = self.root / "ACTIVE.json"
        if not pointer.is_file():
            return None
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise M7PromotionError("m7_integrity_active_pointer_invalid") from error
        if not isinstance(payload, dict) or not isinstance(
            payload.get("bundle_sha256"), str
        ):
            raise M7PromotionError("m7_integrity_active_pointer_invalid")
        return payload

    def _write_event(
        self,
        *,
        event_type: str,
        failure_class: str,
        action: str,
        bundle_id: ModelCapabilityBundle | None,
        failed_bundle_sha256: str | None,
        restored_bundle_sha256: str | None,
        reason: str,
        observed_at: datetime,
        directory: str,
    ) -> Path:
        payload = {
            "schema_version": "m7-integrity-event-v1",
            "event_type": event_type,
            "failure_class": failure_class,
            "action": action,
            "bundle_id": bundle_id,
            "failed_bundle_sha256": failed_bundle_sha256,
            "restored_bundle_sha256": restored_bundle_sha256,
            "reason": reason,
            "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        }
        identity = deterministic_hash(payload)
        target = self.root / directory / f"{identity}.json"
        if target.is_file():
            return target
        write_immutable_json(target, payload, pretty=False)
        if event_type == "performance_drift":
            M7AuditLog(self.paths).append(
                event_type="drift_warning",
                recorded_at=observed_at,
                actor="integrity-monitor",
                run_id=None,
                bundle_id=bundle_id,
                package_sha256=None,
                detail={"reason": reason, "action": "alert_only"},
            )
        elif event_type == "automatic_rollback":
            audit = M7AuditLog(self.paths)
            audit.append(
                event_type="integrity_failure",
                recorded_at=observed_at,
                actor="integrity-monitor",
                run_id=None,
                bundle_id=bundle_id,
                package_sha256=None,
                detail={"reason": reason, "failed_bundle": failed_bundle_sha256},
            )
            audit.append(
                event_type="automatic_rollback",
                recorded_at=observed_at,
                actor="integrity-monitor",
                run_id=None,
                bundle_id=bundle_id,
                package_sha256=None,
                detail={
                    "failed_bundle": failed_bundle_sha256,
                    "restored_bundle": restored_bundle_sha256,
                },
            )
        return target


def _observation_failure(
    observation: M7ServingObservation,
    *,
    component_hash: str | None,
) -> str | None:
    if component_hash != observation.package_sha256:
        return "package_identity"
    if observation.universe_version != "m1-seed-2026-07-11-v1":
        return "universe_scope"
    if observation.observed_symbol_count != observation.expected_symbol_count:
        return "symbol_count"
    if observation.bundle_id == "direction":
        rows = observation.direction_probabilities
        if (
            rows is None
            or len(rows) != observation.expected_symbol_count
            or observation.range_probability_5d is not None
            or observation.range_probability_20d is not None
        ):
            return "direction_shape"
        if any(
            any(not math.isfinite(value) or value < 0 or value > 1 for value in row)
            or abs(sum(row) - 1.0) > 1e-8
            for row in rows
        ):
            return "direction_probability"
        return None
    short = observation.range_probability_5d
    long = observation.range_probability_20d
    if (
        observation.direction_probabilities is not None
        or short is None
        or long is None
        or len(short) != observation.expected_symbol_count
        or len(long) != observation.expected_symbol_count
    ):
        return "range_shape"
    if any(
        not math.isfinite(p5)
        or not math.isfinite(p20)
        or p5 < 0
        or p20 > 1
        or p5 > p20 + 1e-12
        for p5, p20 in zip(short, long, strict=True)
    ):
        return "range_probability"
    return None


def _verify_compatible_packages(
    packages: tuple[M7PackageManifest, ...], expected_signature: str
) -> None:
    contracts = {
        (
            package.universe_version,
            package.dataset_revision,
            package.feature_set_version,
            package.label_version,
            package.split_manifest_hash,
        )
        for package in packages
    }
    if len(contracts) != 1 or deterministic_hash(next(iter(contracts))) != (
        expected_signature
    ):
        raise M7PromotionError("m7_integrity_capability_contract_mismatch")


def _load_manifest(root: Path, bundle_hash: str) -> M7ForecastBundleManifest:
    try:
        return M7ForecastBundleManifest.model_validate_json(
            (root / "manifests" / f"{bundle_hash}.json").read_text("utf-8"),
            strict=True,
        )
    except (OSError, ValueError) as error:
        raise M7PromotionError("m7_integrity_previous_bundle_invalid") from error


def _replace_pointer(root: Path, manifest: M7ForecastBundleManifest) -> None:
    payload = {
        "schema_version": "m7-active-forecast-bundle-v1",
        "forecast_bundle_id": manifest.forecast_bundle_id,
        "bundle_sha256": manifest.bundle_sha256,
        "previous_bundle_sha256": manifest.previous_bundle_sha256,
    }
    temporary = root / f".ACTIVE.integrity.{uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, root / "ACTIVE.json")


__all__ = ["M7IntegrityMonitor", "M7IntegrityResult", "M7ServingObservation"]
