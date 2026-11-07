"""Manual, evidence-bound compare-and-swap promotion for M7 bundles."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Literal, Self
from uuid import uuid4

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from stock_prediction.contracts.operations import (
    ModelCapabilityBundle,
    ModelPromotionRequest,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_audit import M7AuditLog
from stock_prediction.live.m7_blind_review import resolve_verified_m7_range_anchor
from stock_prediction.modeling.m7_blind_gates import M7BlindGateDecision
from stock_prediction.modeling.m7_package import (
    M7PackageError,
    M7PackageManifest,
    M7RuntimeCompatibility,
    load_m7_package,
)
from stock_prediction.modeling.report import (
    deterministic_hash,
    file_sha256,
    write_immutable_json,
)
from stock_prediction.operations.registry import LocalModelRegistry


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PRESERVED_BASELINE_RUN_ID = "live-train-20260814T211738Z-r3-a7569373"
_PRESERVED_COMPONENT_KEYS = frozenset(
    {
        *(
            (task, horizon)
            for task in ("breakout", "return_fwd", "mfe", "mae")
            for horizon in (1, 5, 20)
        ),
        ("range_exit", 5),
    }
)
_TARGETED_COMPONENT_KEYS = frozenset(
    {
        *(("forecast_direction", horizon) for horizon in (1, 5, 20)),
        ("range_exit", 20),
    }
)


class M7PromotionError(RuntimeError):
    """Manual approval evidence is stale, incomplete, or incompatible."""


class M7PreservedLegacyEvidence(BaseModel):
    """Hash-bound identity of the 13 passing task releases M7 must not retrain."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    source_run_id: str = Field(min_length=1)
    report_relative_path: str = Field(min_length=1)
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    component_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    component_count: Literal[13] = 13
    universe_version: str = Field(min_length=1)
    dataset_revision: str = Field(min_length=1)
    feature_set_version: str = Field(min_length=1)
    label_version: str = Field(min_length=1)


class M7ForecastBundleComponent(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    bundle_id: ModelCapabilityBundle
    package_id: str = Field(min_length=1)
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    training_run_id: str = Field(min_length=1)
    blind_window_id: str = Field(min_length=1)
    blind_report_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_incumbent_id: str | None
    verified_range_5d_anchor_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def validate_range_anchor(self) -> Self:
        if (self.bundle_id == "range_exit_20") != (
            self.verified_range_5d_anchor_hash is not None
        ):
            raise ValueError("M7 range component requires its verified 5d anchor")
        return self


class M7ForecastBundleManifest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["m7-forecast-bundle-v1"] = "m7-forecast-bundle-v1"
    forecast_bundle_id: str = Field(min_length=1)
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_bundle_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    promoted_bundle_id: ModelCapabilityBundle
    components: tuple[M7ForecastBundleComponent, ...] = Field(min_length=1)
    preserved_legacy_component_count: Literal[13] = 13
    preserved_legacy_evidence: M7PreservedLegacyEvidence
    compatibility_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_by: str = Field(min_length=1, max_length=128)
    promoted_at: UtcDatetime

    @property
    def component(self) -> M7ForecastBundleComponent:
        return next(
            item
            for item in self.components
            if item.bundle_id == self.promoted_bundle_id
        )

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if len({item.bundle_id for item in self.components}) != len(self.components):
            raise ValueError("M7 forecast components must be unique")
        if self.promoted_bundle_id not in {item.bundle_id for item in self.components}:
            raise ValueError("M7 promoted component is missing")
        if self.preserved_legacy_component_count != (
            self.preserved_legacy_evidence.component_count
        ):
            raise ValueError("M7 preserved legacy component count mismatch")
        base = self.model_dump(
            mode="json", exclude={"bundle_sha256", "forecast_bundle_id"}
        )
        if deterministic_hash(base) != self.bundle_sha256:
            raise ValueError("M7 forecast bundle hash mismatch")
        if self.forecast_bundle_id != f"m7-forecast-{self.bundle_sha256[:16]}":
            raise ValueError("M7 forecast bundle identity mismatch")
        return self


IncumbentProvider = Callable[[ModelCapabilityBundle], str | None]
BlindReportResolver = Callable[[str], Path]
PreservedEvidenceProvider = Callable[[], M7PreservedLegacyEvidence]


class M7PromotionService:
    """Revalidate the reviewed package and atomically switch its bundle pointer."""

    def __init__(
        self,
        paths: LivePaths,
        *,
        current_runtime: M7RuntimeCompatibility,
        incumbent_provider: IncumbentProvider,
        blind_report_resolver: BlindReportResolver | None = None,
        preserved_evidence_provider: PreservedEvidenceProvider | None = None,
    ) -> None:
        self.paths = paths
        self.current_runtime = current_runtime
        self.incumbent_provider = incumbent_provider
        self.root = paths.models / "m7-forecast-bundles"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.root / ".promotion.lock"))
        self.blind_report_resolver = blind_report_resolver or (
            lambda window_id: _resolve_blind_report(paths, window_id)
        )
        self.preserved_evidence_provider = preserved_evidence_provider or (
            lambda: load_m7_preserved_legacy_evidence(paths)
        )

    def promote(
        self,
        request: ModelPromotionRequest,
        *,
        promoted_at: datetime,
    ) -> M7ForecastBundleManifest:
        if promoted_at.tzinfo is None or promoted_at.utcoffset() is None:
            raise M7PromotionError("m7_promotion_timestamp_invalid")
        with self._lock:
            report_row = self._load_training_binding(request)
            package_id = _required_text(report_row, "package_id")
            try:
                package = load_m7_package(
                    self.paths.models / "m7-packages",
                    package_id,
                    current_runtime=self.current_runtime,
                )
            except M7PackageError as error:
                raise M7PromotionError(
                    f"m7_promotion_package_invalid:{error}"
                ) from error
            self._validate_package(request, package)
            window_id = _required_text(report_row, "blind_window_id")
            blind_path = self.blind_report_resolver(window_id)
            blind_hash, decision = self._validate_blind_report(
                blind_path,
                request=request,
                window_id=window_id,
                expected_range_anchor_id=(
                    _optional_text(report_row, "range_five_day_anchor_id")
                    if request.bundle_id == "range_exit_20"
                    else None
                ),
            )
            active = load_active_m7_forecast_bundle(self.paths)
            preserved_evidence = self.preserved_evidence_provider()
            if (
                active is not None
                and active.preserved_legacy_evidence != preserved_evidence
            ):
                raise M7PromotionError("m7_promotion_preserved_evidence_changed")
            existing = (
                next(
                    (
                        item
                        for item in active.components
                        if item.bundle_id == request.bundle_id
                    ),
                    None,
                )
                if active is not None
                else None
            )
            actual_incumbent = (
                existing.package_id
                if existing is not None
                else self.incumbent_provider(request.bundle_id)
            )
            if actual_incumbent != request.expected_incumbent_id:
                raise M7PromotionError("m7_promotion_incumbent_conflict")
            range_anchor = (
                _range_anchor(decision)
                if request.bundle_id == "range_exit_20"
                else None
            )
            component = M7ForecastBundleComponent(
                bundle_id=request.bundle_id,
                package_id=package.package_id,
                package_sha256=package.package_sha256,
                training_run_id=request.training_run_id,
                blind_window_id=window_id,
                blind_report_hash=blind_hash,
                previous_incumbent_id=actual_incumbent,
                verified_range_5d_anchor_hash=range_anchor,
            )
            components = tuple(
                sorted(
                    (
                        *(
                            item
                            for item in (() if active is None else active.components)
                            if item.bundle_id != request.bundle_id
                        ),
                        component,
                    ),
                    key=lambda item: item.bundle_id,
                )
            )
            packages = tuple(
                package
                if item.package_id == package.package_id
                else load_m7_package(
                    self.paths.models / "m7-packages",
                    item.package_id,
                    current_runtime=self.current_runtime,
                )
                for item in components
            )
            signature = _compatibility_signature(packages)
            base: dict[str, object] = {
                "schema_version": "m7-forecast-bundle-v1",
                "forecast_bundle_id": "pending",
                "previous_bundle_sha256": (
                    None if active is None else active.bundle_sha256
                ),
                "promoted_bundle_id": request.bundle_id,
                "components": tuple(
                    item.model_dump(mode="json") for item in components
                ),
                "preserved_legacy_component_count": 13,
                "preserved_legacy_evidence": preserved_evidence.model_dump(mode="json"),
                "compatibility_signature": signature,
                "approved_by": request.approved_by,
                "promoted_at": promoted_at.isoformat().replace("+00:00", "Z"),
            }
            hash_payload = {
                name: value
                for name, value in base.items()
                if name != "forecast_bundle_id"
            }
            bundle_hash = deterministic_hash(hash_payload)
            base["forecast_bundle_id"] = f"m7-forecast-{bundle_hash[:16]}"
            manifest = M7ForecastBundleManifest.model_validate(
                {**base, "bundle_sha256": bundle_hash}, strict=True
            )
            audit = M7AuditLog(self.paths)
            audit.append(
                event_type="manual_approval",
                recorded_at=promoted_at,
                actor=request.approved_by,
                run_id=request.training_run_id,
                bundle_id=request.bundle_id,
                package_sha256=package.package_sha256,
                detail={
                    "blind_report_hash": blind_hash,
                    "expected_incumbent_id": actual_incumbent,
                },
            )
            target = self.root / "manifests" / f"{bundle_hash}.json"
            write_immutable_json(target, manifest.model_dump(mode="json"), pretty=False)
            _replace_active_pointer(self.root, manifest)
            write_immutable_json(
                self.root / "promotions" / f"{bundle_hash}.json",
                {
                    "schema_version": "m7-promotion-audit-v1",
                    "bundle_sha256": bundle_hash,
                    "approved_by": request.approved_by,
                    "promoted_at": promoted_at.isoformat().replace("+00:00", "Z"),
                    "old_incumbent_id": actual_incumbent,
                    "new_package_id": package.package_id,
                    "blind_report_hash": blind_hash,
                },
                pretty=False,
            )
            audit.append(
                event_type="promotion_completed",
                recorded_at=promoted_at,
                actor=request.approved_by,
                run_id=request.training_run_id,
                bundle_id=request.bundle_id,
                package_sha256=package.package_sha256,
                detail={
                    "forecast_bundle_id": manifest.forecast_bundle_id,
                    "bundle_sha256": manifest.bundle_sha256,
                },
            )
            return manifest

    def _load_training_binding(
        self, request: ModelPromotionRequest
    ) -> dict[str, object]:
        if _SAFE_ID.fullmatch(request.training_run_id) is None:
            raise M7PromotionError("m7_promotion_training_run_id_invalid")
        target = (
            self.paths.models
            / "runs"
            / request.training_run_id
            / "training-report.json"
        )
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            row = payload["bundle_results"][request.bundle_id]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise M7PromotionError("m7_promotion_training_binding_invalid") from error
        if not isinstance(row, dict):
            raise M7PromotionError("m7_promotion_training_binding_invalid")
        if row.get("package_sha256") != request.expected_package_sha256:
            raise M7PromotionError("m7_promotion_package_hash_mismatch")
        if row.get("incumbent_id") != request.expected_incumbent_id:
            raise M7PromotionError("m7_promotion_incumbent_conflict")
        return row

    def _validate_package(
        self,
        request: ModelPromotionRequest,
        package: M7PackageManifest,
    ) -> None:
        if (
            package.bundle_id != request.bundle_id
            or package.package_sha256 != request.expected_package_sha256
        ):
            raise M7PromotionError("m7_promotion_package_identity_mismatch")
        if package.bundle_id == "direction":
            horizons = {
                match.group(1)
                for artifact in package.model_artifacts
                if (
                    match := re.search(
                        r"(?:^|[-_])h(1|5|20)(?:d)?(?:$|[-_])", artifact.artifact_id
                    )
                )
            }
            if horizons != {"1", "5", "20"}:
                raise M7PromotionError("m7_promotion_direction_horizons_incomplete")

    def _validate_blind_report(
        self,
        path: Path,
        *,
        request: ModelPromotionRequest,
        window_id: str,
        expected_range_anchor_id: str | None,
    ) -> tuple[str, M7BlindGateDecision]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            decision = M7BlindGateDecision.model_validate_json(
                json.dumps(payload["decision"]), strict=True
            )
        except (OSError, json.JSONDecodeError, KeyError, ValidationError) as error:
            raise M7PromotionError("m7_promotion_blind_report_invalid") from error
        if payload.get("package_sha256") != request.expected_package_sha256:
            raise M7PromotionError("m7_promotion_blind_package_mismatch")
        if (
            payload.get("schema_version") != "m7-blind-report-v1"
            or payload.get("window_id") != window_id
            or payload.get("bundle_id") != request.bundle_id
            or payload.get("evidence_scope") != "blind_complete"
            or payload.get("available_anchor_count") != 77
            or payload.get("mature_label_count") != 77
            or decision.bundle_id != request.bundle_id
            or decision.status != "awaiting_manual_approval"
        ):
            raise M7PromotionError("m7_promotion_blind_report_not_approved")
        if request.bundle_id == "range_exit_20":
            evidence = payload.get("evidence")
            report_anchor_id = (
                evidence.get("range_five_day_anchor_id")
                if isinstance(evidence, dict)
                else None
            )
            anchor_hash, anchor_verified = resolve_verified_m7_range_anchor(
                self.paths,
                expected_range_anchor_id,
            )
            if (
                not anchor_verified
                or report_anchor_id != expected_range_anchor_id
                or decision.metrics.get("five_day_anchor_hash") != anchor_hash
            ):
                raise M7PromotionError("m7_promotion_range_5d_anchor_conflict")
        return file_sha256(path), decision


def load_active_m7_forecast_bundle(
    paths: LivePaths,
) -> M7ForecastBundleManifest | None:
    root = paths.models / "m7-forecast-bundles"
    pointer = root / "ACTIVE.json"
    if not pointer.is_file():
        return None
    try:
        binding = json.loads(pointer.read_text(encoding="utf-8"))
        bundle_hash = binding["bundle_sha256"]
        manifest_path = root / "manifests" / f"{bundle_hash}.json"
        manifest = M7ForecastBundleManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8"), strict=True
        )
    except (OSError, json.JSONDecodeError, KeyError, ValidationError) as error:
        raise M7PromotionError("m7_active_forecast_bundle_invalid") from error
    if binding.get("forecast_bundle_id") != manifest.forecast_bundle_id:
        raise M7PromotionError("m7_active_forecast_pointer_mismatch")
    return manifest


def load_m7_preserved_legacy_evidence(
    paths: LivePaths,
) -> M7PreservedLegacyEvidence:
    """Verify and bind the authoritative 13-of-17 baseline component evidence."""

    target = paths.models / "runs" / _PRESERVED_BASELINE_RUN_ID / "training-report.json"
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M7PromotionError("m7_preserved_baseline_report_invalid") from error
    if not isinstance(payload, dict) or payload.get("run_id") != (
        _PRESERVED_BASELINE_RUN_ID
    ):
        raise M7PromotionError("m7_preserved_baseline_identity_invalid")
    tasks = payload.get("tasks")
    releases = payload.get("component_releases")
    if not isinstance(tasks, list) or not isinstance(releases, list):
        raise M7PromotionError("m7_preserved_baseline_components_invalid")

    task_rows = {
        (row.get("task"), row.get("horizon")): row
        for row in tasks
        if isinstance(row, dict)
        and isinstance(row.get("task"), str)
        and row.get("horizon") in (1, 5, 20)
    }
    if len(task_rows) != 17 or set(task_rows) != (
        _PRESERVED_COMPONENT_KEYS | _TARGETED_COMPONENT_KEYS
    ):
        raise M7PromotionError("m7_preserved_baseline_task_matrix_invalid")
    passed = {key for key, row in task_rows.items() if row.get("gate_passed") is True}
    if passed != _PRESERVED_COMPONENT_KEYS:
        raise M7PromotionError("m7_preserved_baseline_gate_set_invalid")

    release_rows = {
        (row.get("task"), row.get("horizon")): row
        for row in releases
        if isinstance(row, dict)
        and isinstance(row.get("task"), str)
        and row.get("horizon") in (1, 5, 20)
    }
    if set(release_rows) != set(task_rows):
        raise M7PromotionError("m7_preserved_baseline_release_matrix_invalid")
    registry = LocalModelRegistry(paths.models / "registry")
    canonical: list[dict[str, object]] = []
    for task, horizon in sorted(_PRESERVED_COMPONENT_KEYS):
        row = release_rows[(task, horizon)]
        model_version = row.get("model_version")
        release_hash = row.get("release_hash")
        calibration_version = row.get("calibration_version")
        if (
            row.get("gate_passed") is not True
            or not isinstance(model_version, str)
            or re.fullmatch(r"[0-9a-f]{64}", str(release_hash)) is None
            or not isinstance(calibration_version, str)
        ):
            raise M7PromotionError("m7_preserved_baseline_release_invalid")
        try:
            release = registry.verify_release(model_version)
        except RuntimeError as error:
            raise M7PromotionError(
                "m7_preserved_baseline_release_unverifiable"
            ) from error
        if (
            release.task != task
            or release.horizon != horizon
            or release.calibration_version != calibration_version
            or release.manifest_hash != release_hash
        ):
            raise M7PromotionError("m7_preserved_baseline_release_mismatch")
        canonical.append(
            {
                "task": task,
                "horizon": horizon,
                "model_version": model_version,
                "calibration_version": calibration_version,
                "release_hash": release_hash,
                "previous_champion_version": row.get("previous_champion_version"),
                "lifecycle_status": row.get("lifecycle_status"),
            }
        )
    required_identity = {
        "universe_version": "m1-seed-2026-07-11-v1",
        "dataset_revision": "opend-live-market-bars-pit-adjusted-v2",
        "feature_set_version": "live-daily-features-v2",
        "label_version": "live-labels-v2",
    }
    if any(payload.get(name) != value for name, value in required_identity.items()):
        raise M7PromotionError("m7_preserved_baseline_contract_invalid")
    try:
        relative_path = target.resolve(strict=True).relative_to(
            paths.root.resolve(strict=True)
        )
    except (OSError, ValueError) as error:
        raise M7PromotionError("m7_preserved_baseline_path_invalid") from error
    return M7PreservedLegacyEvidence(
        source_run_id=_PRESERVED_BASELINE_RUN_ID,
        report_relative_path=relative_path.as_posix(),
        report_sha256=file_sha256(target),
        component_set_sha256=deterministic_hash(canonical),
        component_count=13,
        **required_identity,
    )


def _compatibility_signature(packages: tuple[M7PackageManifest, ...]) -> str:
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
    if len(contracts) != 1:
        raise M7PromotionError("m7_promotion_capability_contract_incompatible")
    return deterministic_hash(next(iter(contracts)))


def _range_anchor(decision: M7BlindGateDecision) -> str:
    value = decision.metrics.get("five_day_anchor_hash")
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise M7PromotionError("m7_promotion_range_5d_anchor_unverified")
    return value


def _resolve_blind_report(paths: LivePaths, window_id: str) -> Path:
    directory = paths.models / "m7-blind" / window_id
    try:
        terminal = json.loads((directory / "terminal.json").read_text("utf-8"))
        report_hash = terminal["blind_report_hash"]
    except (OSError, json.JSONDecodeError, KeyError) as error:
        raise M7PromotionError("m7_promotion_blind_window_unconsumed") from error
    target = directory / "reports" / f"{report_hash}.json"
    if not target.is_file() or file_sha256(target) != report_hash:
        raise M7PromotionError("m7_promotion_blind_report_hash_invalid")
    return target


def _replace_active_pointer(root: Path, manifest: M7ForecastBundleManifest) -> None:
    pointer = root / "ACTIVE.json"
    temporary = root / f".ACTIVE.{uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(
            {
                "schema_version": "m7-active-forecast-bundle-v1",
                "forecast_bundle_id": manifest.forecast_bundle_id,
                "bundle_sha256": manifest.bundle_sha256,
                "previous_bundle_sha256": manifest.previous_bundle_sha256,
            },
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, pointer)


def _required_text(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise M7PromotionError("m7_promotion_training_binding_invalid")
    return value


def _optional_text(payload: dict[str, object], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise M7PromotionError("m7_promotion_training_binding_invalid")
    return value


__all__ = [
    "M7ForecastBundleComponent",
    "M7ForecastBundleManifest",
    "M7PreservedLegacyEvidence",
    "M7PromotionError",
    "M7PromotionService",
    "load_active_m7_forecast_bundle",
    "load_m7_preserved_legacy_evidence",
]
