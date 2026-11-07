"""Explicit, audited activation of a complete unverified model bundle."""

from datetime import UTC, datetime
import json
import pickle
from typing import Any
from uuid import uuid4

from stock_prediction.live.config import LivePaths
from stock_prediction.live.features import load_latest_feature_artifact
from stock_prediction.live.locking import LiveWriterLease
from stock_prediction.live.publication import LiveSnapshotPublisher
from stock_prediction.live.promotion_windows import _atomic_json
from stock_prediction.live.training import load_latest_training_report
from stock_prediction.operations.registry import LocalModelRegistry


def activate_manual_champions(
    paths: LivePaths,
    *,
    expected_candidate_version: str,
    approval_reason: str,
) -> dict[str, Any]:
    if not approval_reason.strip():
        raise ValueError("manual_activation_approval_required")
    with LiveWriterLease(paths, task_id="manual-champion-activation", timeout=0):
        report = load_latest_training_report(paths)
        if report["model_version"] != expected_candidate_version:
            raise RuntimeError("manual_activation_candidate_changed")
        expected_tasks = {
            (task, horizon)
            for task in ("forecast_direction", "breakout", "return_fwd", "mfe", "mae")
            for horizon in (1, 5, 20)
        } | {("range_exit", horizon) for horizon in (5, 20)}
        components = report["component_releases"]
        if (
            len(components) != 17
            or {(row["task"], row["horizon"]) for row in components} != expected_tasks
            or len(report["tasks"]) != 17
            or {(row["task"], row["horizon"]) for row in report["tasks"]}
            != expected_tasks
        ):
            raise RuntimeError("manual_activation_task_set_incomplete")
        registry = LocalModelRegistry(paths.models / "registry")
        active = registry.snapshot("forecast_bundle", 20).champion_version
        if active is not None:
            active_release = registry.verify_release(active)
            if active_release.source_decision_id.startswith("manual-activation-"):
                result_path = (
                    paths.models
                    / "manual-activations"
                    / active_release.source_decision_id
                    / "result.json"
                )
                if result_path.is_file():
                    previous_result: dict[str, Any] = json.loads(
                        result_path.read_text(encoding="utf-8")
                    )
                    if (
                        previous_result.get("source_candidate_version")
                        == expected_candidate_version
                    ):
                        if (
                            len(previous_result["releases"]) == 18
                            and {
                                (r["task"], r["horizon"])
                                for r in previous_result["releases"]
                            }
                            == expected_tasks | {("forecast_bundle", 20)}
                            and all(
                                registry.snapshot(
                                    r["task"], r["horizon"]
                                ).champion_version
                                == r["model_version"]
                                for r in previous_result["releases"]
                            )
                        ):
                            return previous_result
                        raise RuntimeError("manual_activation_incumbent_changed")
        artifact = load_latest_feature_artifact(paths)
        if report["feature_input_hash"] != artifact.input_hash:
            raise RuntimeError("manual_activation_feature_input_changed")
        sources = [
            {
                "task": "forecast_bundle",
                "horizon": 20,
                "model_version": expected_candidate_version,
            },
            *components,
        ]
        prepared = []
        for source in sources:
            release = registry.verify_release(source["model_version"])
            if (release.task, release.horizon) != (source["task"], source["horizon"]):
                raise RuntimeError("manual_activation_source_task_mismatch")
            name = (
                "production-package.pkl"
                if release.task == "forecast_bundle"
                else "component-package.pkl"
            )
            member = next(
                a for a in release.artifacts if a.relative_path.endswith(name)
            )
            source_path = (
                paths.models
                / "registry"
                / "releases"
                / release.model_version
                / member.relative_path
            )
            with source_path.open("rb") as handle:
                package = pickle.load(handle)  # noqa: S301 - verified registry hash
            if not isinstance(package, dict) or not package.get("features"):
                raise RuntimeError("manual_activation_package_invalid")
            if (
                package.get("feature_set_version") != artifact.feature_set_version
                or package.get("universe_version") != artifact.universe_version
            ):
                raise RuntimeError("manual_activation_feature_contract_mismatch")
            old = registry.snapshot(release.task, release.horizon)
            if old.champion_version is None and old.prior_champion_versions:
                raise RuntimeError("manual_activation_inactive_history_requires_review")
            prepared.append((release, name, package, old.champion_version))

        now = datetime.now(UTC)
        suffix = uuid4().hex[:8]
        activation_id = f"manual-activation-{now:%Y%m%dT%H%M%SZ}-{suffix}"
        directory = paths.models / "manual-activations" / activation_id
        directory.mkdir(parents=True)
        bundle_version = f"manual-{now:%Y%m%d%H%M%S}-{suffix}"
        versions = {
            (r.task, r.horizon): bundle_version
            if r.task == "forecast_bundle"
            else f"{bundle_version}-{r.task}-{r.horizon}"
            for r, _, _, _ in prepared
        }
        decision = {
            "activation_id": activation_id,
            "activation_basis": "manual_override",
            "approved_by": "local_user",
            "approval_reason": approval_reason,
            "approved_at": now.isoformat(),
            "source_candidate_version": expected_candidate_version,
            "feature_input_hash": artifact.input_hash,
            "passed_tasks": sum(bool(r["gate_passed"]) for r in report["tasks"]),
            "total_tasks": 17,
            "data_quality_summary": report.get("data_quality_summary"),
            "quality_gates_overridden": True,
            "independent_evaluation_passed": False,
            "sources": [
                {
                    "task": r.task,
                    "horizon": r.horizon,
                    "source_model_version": r.model_version,
                    "source_manifest_hash": r.manifest_hash,
                    "model_version": versions[(r.task, r.horizon)],
                    "previous_champion_version": previous,
                }
                for r, _, _, previous in prepared
            ],
        }
        decision_path = directory / "manual-decision.json"
        _atomic_json(decision_path, decision)
        before_snapshots = {p.name for p in paths.research.iterdir() if p.is_dir()}
        registered: list[dict[str, Any]] = []
        try:
            for release, name, package, previous in prepared:
                version = versions[(release.task, release.horizon)]
                package.update(
                    activation_basis="manual_override",
                    manual_activation_id=activation_id,
                    source_candidate_version=expected_candidate_version,
                    model_version=version,
                )
                if release.task == "forecast_bundle":
                    package["manual_component_versions"] = {
                        key: value
                        for key, value in versions.items()
                        if key[0] != "forecast_bundle"
                    }
                elif "bundle_model_version" in package:
                    package["bundle_model_version"] = bundle_version
                package_path = directory / f"{release.task}-{release.horizon}.pkl"
                package_path.write_bytes(pickle.dumps(package, protocol=5))
                new_release = registry.register_release(
                    model_version=version,
                    task=release.task,
                    horizon=release.horizon,
                    calibration_version=release.calibration_version,
                    source_decision_id=activation_id,
                    promotion_eligible=True,
                    artifacts={
                        name: package_path,
                        "manual-decision.json": decision_path,
                    },
                    registered_at=now,
                )
                registry.verify_release(version)
                registered.append(
                    {
                        "task": release.task,
                        "horizon": release.horizon,
                        "model_version": version,
                        "manifest_hash": new_release.manifest_hash,
                        "previous_champion_version": previous,
                    }
                )
            for row in registered:
                registry.promote(
                    row["model_version"],
                    reason=f"explicit manual approval: {activation_id}",
                    recorded_at=now,
                )
            publication = LiveSnapshotPublisher(paths).publish()
            if (
                publication["prediction_readiness"] != "forecast_ready"
                or publication["research_readiness"] != "manual_unverified"
            ):
                raise RuntimeError("manual_activation_publication_status_invalid")
            if not all(
                registry.snapshot(r["task"], r["horizon"]).champion_version
                == r["model_version"]
                for r in registered
            ):
                raise RuntimeError("manual_activation_incomplete")
            result = {
                "status": "activated",
                "activation_id": activation_id,
                "source_candidate_version": expected_candidate_version,
                "model_version": bundle_version,
                "snapshot_id": publication["snapshot_id"],
                "activation_basis": "manual_override",
                "releases": registered,
            }
            _atomic_json(directory / "result.json", result)
            return result
        except Exception as error:
            rollback_errors: list[Exception] = []
            for snapshot_path in paths.research.iterdir():
                if not snapshot_path.is_dir() or snapshot_path.name in before_snapshots:
                    continue
                manifest_path = snapshot_path / "manifest.json"
                if not manifest_path.is_file():
                    continue
                try:
                    if (
                        json.loads(manifest_path.read_text(encoding="utf-8")).get(
                            "model_version"
                        )
                        != bundle_version
                    ):
                        continue
                    destination = directory / "failed-snapshots" / snapshot_path.name
                    if not snapshot_path.resolve().is_relative_to(
                        paths.research.resolve()
                    ) or not destination.resolve().is_relative_to(directory.resolve()):
                        raise RuntimeError("manual_activation_rollback_path_invalid")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    snapshot_path.rename(destination)
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
            for release, _, _, previous in reversed(prepared):
                try:
                    current = registry.snapshot(
                        release.task, release.horizon
                    ).champion_version
                    if current != versions[(release.task, release.horizon)]:
                        continue
                    if previous is None:
                        registry.deactivate_champion(
                            task=release.task,
                            horizon=release.horizon,
                            reason=f"rollback failed manual activation {activation_id}",
                            recorded_at=datetime.now(UTC),
                        )
                    else:
                        registry.rollback(
                            task=release.task,
                            horizon=release.horizon,
                            target_version=previous,
                            reason=f"rollback failed manual activation {activation_id}",
                            recorded_at=datetime.now(UTC),
                        )
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
            _atomic_json(
                directory / "failure.json",
                {
                    "error": str(error),
                    "rollback_errors": [str(e) for e in rollback_errors],
                },
            )
            if rollback_errors:
                raise ExceptionGroup(
                    "manual activation and rollback failed", [error, *rollback_errors]
                )
            raise
