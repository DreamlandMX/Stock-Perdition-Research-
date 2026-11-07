"""Governed M7 targeted-bundle training publication orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
import re

from stock_prediction.contracts.operations import ModelCapabilityBundle
from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V9,
    CANDIDATE_RECIPE_SEED100_TARGETED_V10,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_audit import M7AuditLog
from stock_prediction.live.m7_blind import register_m7_blind_ledger
from stock_prediction.live.promotion_windows import (
    PromotionPackageBinding,
    PromotionWindowRegistry,
    build_seed100_promotion_window,
)
from stock_prediction.live.training import write_latest_training_pointer
from stock_prediction.modeling.m7_package import (
    M7PackageArtifactInput,
    M7PackageBuildSpec,
    publish_m7_package,
)
from stock_prediction.modeling.report import (
    M7_REPORT_SECTION_NAMES,
    build_m7_training_report_sections,
    deterministic_hash,
    write_immutable_json,
)


ProgressCallback = Callable[[str, int, str], None]
BundleExecutor = Callable[[ModelCapabilityBundle], "M7BundleExecutionResult"]
CancellationCheck = Callable[[], bool]
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class M7TargetedTrainingCancelled(RuntimeError):
    """Cancellation became effective at a governed atomic boundary."""


@dataclass(frozen=True, slots=True)
class M7BundleExecutionResult:
    bundle_id: ModelCapabilityBundle
    development_gate_passed: bool
    gate_payload: Mapping[str, object]
    package_build_spec: M7PackageBuildSpec | None
    artifacts: tuple[M7PackageArtifactInput, ...]
    eligible_sessions: tuple[date, ...]
    incumbent_id: str | None
    report_payloads: Mapping[str, object]
    runtime_observations: Mapping[str, object]
    range_five_day_anchor_id: str | None = None

    def __post_init__(self) -> None:
        if not self.gate_payload:
            raise ValueError("m7_bundle_gate_payload_required")
        expected_gate_status = "passed" if self.development_gate_passed else "failed"
        if self.gate_payload.get("status") != expected_gate_status:
            raise ValueError("m7_bundle_gate_status_mismatch")
        unknown_sections = set(self.report_payloads) - set(M7_REPORT_SECTION_NAMES)
        if unknown_sections:
            raise ValueError("m7_bundle_report_section_unknown")
        if self.development_gate_passed:
            determinism = self.report_payloads.get("determinism_results")
            if (
                self.package_build_spec is None
                or self.package_build_spec.bundle_id != self.bundle_id
                or not self.artifacts
                or not self.eligible_sessions
                or not self.runtime_observations
                or not isinstance(determinism, Mapping)
                or determinism.get("passed") is not True
            ):
                raise ValueError("m7_passing_bundle_publication_inputs_incomplete")
        elif (
            self.package_build_spec is not None
            or self.artifacts
            or self.eligible_sessions
        ):
            raise ValueError("m7_failed_bundle_cannot_publish_shadow")
        if self.development_gate_passed:
            _validate_resource_observations(self.runtime_observations)
            if self.bundle_id == "range_exit_20" and not self.range_five_day_anchor_id:
                raise ValueError("m7_range_five_day_anchor_required")


class TargetedModelTrainer:
    """Freeze development-passing M7 bundles without switching the Champion."""

    def __init__(
        self,
        paths: LivePaths,
        *,
        run_id: str,
        bundles: Sequence[ModelCapabilityBundle],
        recipe_version: str,
        bundle_executor: BundleExecutor | None = None,
        cancellation_requested: CancellationCheck | None = None,
    ) -> None:
        selected = tuple(bundles)
        if _SAFE_RUN_ID.fullmatch(run_id) is None:
            raise ValueError("m7_targeted_run_id_invalid")
        if recipe_version not in {
            CANDIDATE_RECIPE_SEED100_TARGETED_V9,
            CANDIDATE_RECIPE_SEED100_TARGETED_V10,
        }:
            raise ValueError("m7_targeted_recipe_invalid")
        if not selected or len(set(selected)) != len(selected):
            raise ValueError("m7_targeted_bundles_must_be_nonempty_and_unique")
        self.paths = paths
        self.run_id = run_id
        self.bundles = selected
        self.recipe_version = recipe_version
        self.bundle_executor = bundle_executor or _unconfigured_bundle_executor
        self.cancellation_requested = cancellation_requested or (lambda: False)
        self.audit = M7AuditLog(paths)

    def train(self, *, progress_callback: ProgressCallback) -> dict[str, object]:
        self.paths.ensure()
        self.audit.append(
            event_type="training_started",
            recorded_at=datetime.now(UTC),
            actor="model-training-worker",
            run_id=self.run_id,
            bundle_id=None,
            package_sha256=None,
            detail={"trigger": "targeted", "recipe_id": self.recipe_version},
        )
        bundle_results: dict[str, dict[str, object]] = {}
        report_payloads: dict[str, list[object]] = {}
        passed_packages: list[dict[str, object]] = []
        completed: list[ModelCapabilityBundle] = []
        bundle_count = len(self.bundles)
        for index, bundle in enumerate(self.bundles):
            progress_callback(
                "candidate_search",
                10 + int(index / bundle_count * 50),
                f"正在执行 M7 {bundle} 注册候选矩阵",
            )
            result = self.bundle_executor(bundle)
            if result.bundle_id != bundle:
                raise RuntimeError("m7_targeted_bundle_identity_mismatch")
            self._check_cancellation()
            for section, payload in result.report_payloads.items():
                report_payloads.setdefault(section, []).append(
                    {"bundle_id": bundle, "payload": payload}
                )
            if result.runtime_observations:
                report_payloads.setdefault("resource_observations", []).append(
                    {
                        "bundle_id": bundle,
                        "payload": dict(result.runtime_observations),
                    }
                )
            row: dict[str, object]
            if result.development_gate_passed:
                assert result.package_build_spec is not None
                if result.package_build_spec.recipe_id != self.recipe_version:
                    raise RuntimeError("m7_targeted_package_recipe_mismatch")
                progress_callback(
                    "oos_validation",
                    62 + int(index / bundle_count * 15),
                    f"{bundle} 开发门禁通过，正在冻结不可变 shadow 包",
                )
                published = publish_m7_package(
                    self.paths.models / "m7-packages",
                    build_spec=result.package_build_spec,
                    artifacts=result.artifacts,
                    runtime_observations=result.runtime_observations,
                )
                self._check_cancellation()
                definition = build_seed100_promotion_window(
                    PromotionPackageBinding(
                        package_sha256=published.manifest.package_sha256,
                        universe_version=published.manifest.universe_version,
                        frozen_at=published.manifest.created_at,
                    ),
                    list(result.eligible_sessions),
                )
                registration = PromotionWindowRegistry(self.paths).register(definition)
                scheduled_sessions = tuple(
                    session
                    for session in result.eligible_sessions
                    if session >= definition.start
                )
                register_m7_blind_ledger(
                    self.paths,
                    window_id=definition.window_id,
                    bundle_id=bundle,
                    package_sha256=published.manifest.package_sha256,
                    incumbent_id=result.incumbent_id,
                    range_five_day_anchor_id=result.range_five_day_anchor_id,
                    scheduled_sessions=scheduled_sessions,
                    registration_path=registration,
                )
                self._check_cancellation()
                row = {
                    "bundle_id": bundle,
                    "development_state": "passed",
                    "shadow_state": "frozen",
                    "blind_state": "collecting",
                    "promotion_state": "blind_collecting",
                    "package_id": published.manifest.package_id,
                    "package_sha256": published.manifest.package_sha256,
                    "blind_window_id": definition.window_id,
                    "incumbent_id": result.incumbent_id,
                    "range_five_day_anchor_id": result.range_five_day_anchor_id,
                    "gate": dict(result.gate_payload),
                }
                passed_packages.append(row)
                self.audit.append(
                    event_type="shadow_frozen",
                    recorded_at=datetime.now(UTC),
                    actor="model-training-worker",
                    run_id=self.run_id,
                    bundle_id=bundle,
                    package_sha256=published.manifest.package_sha256,
                    detail={
                        "package_id": published.manifest.package_id,
                        "blind_window_id": definition.window_id,
                    },
                )
            else:
                row = {
                    "bundle_id": bundle,
                    "development_state": "failed",
                    "shadow_state": "not_frozen",
                    "blind_state": "not_started",
                    "promotion_state": "kept_current",
                    "package_id": None,
                    "package_sha256": None,
                    "blind_window_id": None,
                    "incumbent_id": result.incumbent_id,
                    "range_five_day_anchor_id": result.range_five_day_anchor_id,
                    "gate": dict(result.gate_payload),
                }
                self.audit.append(
                    event_type="candidate_rejected",
                    recorded_at=datetime.now(UTC),
                    actor="model-training-worker",
                    run_id=self.run_id,
                    bundle_id=bundle,
                    package_sha256=None,
                    detail={"reason": "development_gate_failed"},
                )
            bundle_results[bundle] = row
            completed.append(bundle)
        self._check_cancellation()
        progress_callback("promotion_window", 86, "正在写入 M7 不可变训练报告")
        sections = _report_sections(bundle_results, report_payloads, passed_packages)
        generated_at = datetime.now(UTC)
        report: dict[str, object] = {
            "schema_version": "m7-targeted-training-report-v1",
            "run_id": self.run_id,
            "generated_at": generated_at.isoformat(),
            "candidate_recipe_version": self.recipe_version,
            "requested_bundles": list(self.bundles),
            "completed_bundles": list(completed),
            "bundle_results": bundle_results,
            "evidence_scope": "optimization_reused",
            "automatic_promotion": (
                "awaiting_manual_approval" if passed_packages else "kept_current"
            ),
            "m7_sections": sections,
        }
        report_path = self.paths.models / "runs" / self.run_id / "training-report.json"
        report_hash = write_immutable_json(report_path, report, pretty=False)
        self._check_cancellation()
        write_latest_training_pointer(self.paths, report_path, self.run_id)
        self.audit.append(
            event_type="training_completed",
            recorded_at=generated_at,
            actor="model-training-worker",
            run_id=self.run_id,
            bundle_id=None,
            package_sha256=None,
            detail={
                "completed_bundle_count": len(completed),
                "published_package_count": len(passed_packages),
            },
        )
        package_ids = [str(row["package_id"]) for row in passed_packages]
        package_hashes = [str(row["package_sha256"]) for row in passed_packages]
        window_ids = [str(row["blind_window_id"]) for row in passed_packages]
        fingerprint = deterministic_hash(
            {
                "recipe": self.recipe_version,
                "bundles": self.bundles,
                "package_hashes": package_hashes,
                "report_hash": report_hash,
            }
        )
        return {
            "run_id": self.run_id,
            "candidate_fingerprint": fingerprint,
            "development_all_gates_passed": len(passed_packages) == len(self.bundles),
            "automatic_promotion": report["automatic_promotion"],
            "model_version": f"m7-targeted-{self.run_id}",
            "completed_bundles": list(completed),
            "bundle_results": bundle_results,
            "evidence_scope": "optimization_reused",
            "shadow_package_id": package_ids[0] if len(package_ids) == 1 else None,
            "blind_window_id": window_ids[0] if len(window_ids) == 1 else None,
            "package_sha256s": package_hashes,
            "training_report_hash": report_hash,
        }

    def _check_cancellation(self) -> None:
        if self.cancellation_requested():
            raise M7TargetedTrainingCancelled("m7_targeted_training_cancelled")


def _validate_resource_observations(observations: Mapping[str, object]) -> None:
    wall_seconds = observations.get("wall_seconds")
    cpu_seconds = observations.get("cpu_seconds")
    peak_rss = observations.get("peak_rss_bytes")
    peak_gpu = observations.get("peak_gpu_memory_bytes")
    gpu_device = observations.get("gpu_device")
    if (
        not isinstance(wall_seconds, (int, float))
        or isinstance(wall_seconds, bool)
        or wall_seconds < 0
        or wall_seconds > 24 * 60 * 60
        or not isinstance(cpu_seconds, (int, float))
        or isinstance(cpu_seconds, bool)
        or cpu_seconds < 0
        or not isinstance(peak_rss, int)
        or isinstance(peak_rss, bool)
        or peak_rss < 0
        or peak_rss > 24 * 1024**3
        or not isinstance(peak_gpu, int)
        or isinstance(peak_gpu, bool)
        or peak_gpu < 0
        or peak_gpu > 10 * 1024**3
        or not isinstance(gpu_device, str)
        or not gpu_device.strip()
    ):
        raise ValueError("m7_resource_observations_outside_hard_limits")


def _report_sections(
    bundle_results: Mapping[str, Mapping[str, object]],
    report_payloads: Mapping[str, Sequence[object]],
    passed_packages: Sequence[Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    completed: dict[str, object] = {
        "bundle_results": dict(bundle_results),
        "evidence_scope": {"scope": "optimization_reused"},
        "artifact_pointer_policy": {
            "policy": "content_addressed_relative_paths",
            "latest_is_index_only": True,
        },
    }
    completed.update({name: list(values) for name, values in report_payloads.items()})
    failed: dict[str, str] = {}
    not_run: dict[str, str] = {}
    if passed_packages:
        completed["shadow_status"] = {
            "packages": [dict(value) for value in passed_packages]
        }
        completed["window_registration"] = {
            "windows": [str(value["blind_window_id"]) for value in passed_packages]
        }
        completed.setdefault(
            "resource_observations",
            {
                "stored_separately_by_hash": True,
                "package_count": len(passed_packages),
            },
        )
    else:
        failed["shadow_status"] = "development_gate_failed"
        not_run["window_registration"] = "no_development_passing_package"
        not_run["resource_observations"] = "no_package_published"
    defaults = {
        "feature_ablation": "bundle_executor_did_not_emit_feature_ablation",
        "calibration_selection": "bundle_executor_did_not_emit_calibration_selection",
        "baseline_deltas": "bundle_executor_did_not_emit_baseline_deltas",
        "bootstrap_results": "bundle_executor_did_not_emit_bootstrap_results",
        "determinism_results": "bundle_executor_did_not_emit_determinism_results",
        "window_consumption": "blind_window_not_complete",
        "drift_evidence": "mature_outcome_drift_not_available",
        "economic_diagnostics": "bundle_executor_did_not_emit_economic_diagnostics",
        "explanation_manifest": "bundle_executor_did_not_emit_explanation_manifest",
    }
    for name, reason in defaults.items():
        if name not in completed and name not in failed:
            not_run[name] = reason
    return build_m7_training_report_sections(
        completed_sections=completed,
        failed_sections=failed,
        not_run_sections=not_run,
    )


def _unconfigured_bundle_executor(
    bundle: ModelCapabilityBundle,
) -> M7BundleExecutionResult:
    raise RuntimeError(f"m7_targeted_bundle_executor_unconfigured:{bundle}")


__all__ = [
    "M7BundleExecutionResult",
    "M7TargetedTrainingCancelled",
    "TargetedModelTrainer",
]
