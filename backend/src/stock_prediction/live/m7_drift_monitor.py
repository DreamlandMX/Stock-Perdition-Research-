"""Automatic daily drift evidence for active M7 forecast components."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
import json
from pathlib import Path, PurePosixPath
from typing import Self

from filelock import FileLock
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_prediction.live.config import LivePaths
from stock_prediction.live.features import load_latest_feature_artifact
from stock_prediction.live.m7_audit import M7AuditLog
from stock_prediction.live.m7_executor import prepare_m7_feature_data
from stock_prediction.live.m7_orchestration import (
    M7DailyDriftEvidence,
    M7OrchestrationStore,
)
from stock_prediction.live.m7_promotion import (
    M7ForecastBundleComponent,
    load_active_m7_forecast_bundle,
)
from stock_prediction.modeling.m7_direction_package import (
    load_m7_direction_package,
)
from stock_prediction.modeling.m7_drift import (
    M7DriftReference,
    evaluate_m7_daily_drift,
)
from stock_prediction.modeling.m7_package import load_m7_package
from stock_prediction.modeling.m7_range_package import load_m7_range_package
from stock_prediction.modeling.report import (
    deterministic_hash,
    file_sha256,
    write_immutable_json,
)
from stock_prediction.universe.seed import SEED_UNIVERSE


class M7DriftMonitorError(RuntimeError):
    """Daily drift evidence cannot be computed or persisted safely."""


class M7DailyDriftRunResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: str = "m7-daily-drift-run-v1"
    session: date
    warning: bool
    component_report_hashes: dict[str, str]
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        expected = deterministic_hash(
            self.model_dump(mode="json", exclude={"evidence_hash"})
        )
        if self.evidence_hash != expected:
            raise ValueError("m7_daily_drift_run_hash_invalid")
        return self


ComponentProvider = Callable[[], Sequence[M7ForecastBundleComponent]]
ReferenceProvider = Callable[[M7ForecastBundleComponent], M7DriftReference]
ObservationProvider = Callable[
    [M7ForecastBundleComponent],
    tuple[date, pd.DataFrame, Mapping[str, Sequence[float] | np.ndarray]],
]


class M7DriftMonitor:
    """Compute package-bound PSI evidence and update the governed drift streak."""

    def __init__(
        self,
        paths: LivePaths,
        *,
        component_provider: ComponentProvider | None = None,
        reference_provider: ReferenceProvider | None = None,
        observation_provider: ObservationProvider | None = None,
    ) -> None:
        self.paths = paths
        self.root = paths.models / "m7-drift"
        self.root.mkdir(parents=True, exist_ok=True)
        self.component_provider = component_provider or self._active_components
        self.reference_provider = reference_provider or self._load_reference
        self.observation_provider = observation_provider or self._observe_component
        self._lock = FileLock(str(self.root / ".daily-drift.lock"))

    def run(
        self, *, generated_at: datetime | None = None
    ) -> M7DailyDriftRunResult | None:
        recorded_at = generated_at or datetime.now(UTC)
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise M7DriftMonitorError("m7_drift_generated_at_invalid")
        with self._lock:
            components = tuple(self.component_provider())
            if not components:
                return None
            if len({row.bundle_id for row in components}) != len(components):
                raise M7DriftMonitorError("m7_drift_components_duplicate")

            sessions: set[date] = set()
            report_hashes: dict[str, str] = {}
            reports = []
            for component in sorted(components, key=lambda row: row.bundle_id):
                reference = self.reference_provider(component)
                session, frame, predictions = self.observation_provider(component)
                sessions.add(session)
                report = evaluate_m7_daily_drift(
                    reference,
                    frame,
                    prediction_values=predictions,
                    reference_package_sha256=component.package_sha256,
                )
                payload = {
                    "schema_version": "m7-daily-drift-component-v1",
                    "session": session.isoformat(),
                    "observed_at": recorded_at.astimezone(UTC)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "bundle_id": component.bundle_id,
                    "package_id": component.package_id,
                    "package_sha256": component.package_sha256,
                    "reference_hash": reference.reference_hash,
                    "report": report.model_dump(mode="json"),
                }
                target = (
                    self.root
                    / "daily"
                    / session.isoformat()
                    / f"{component.bundle_id}.json"
                )
                report_hashes[component.bundle_id] = self._write_or_validate(
                    target, payload
                )
                reports.append(report)

            if len(sessions) != 1:
                raise M7DriftMonitorError("m7_drift_component_sessions_mixed")
            session = next(iter(sessions))
            evidence_payload = {
                "session": session.isoformat(),
                "components": report_hashes,
            }
            evidence = M7DailyDriftEvidence(
                session=session,
                eligible=all(report.coverage_rate >= 0.95 for report in reports),
                core_feature_psi_fraction_above_020=max(
                    report.core_feature_psi_fraction_above_020 for report in reports
                ),
                prediction_distribution_shifted=any(
                    report.prediction_distribution_shifted for report in reports
                ),
                evidence_hash=deterministic_hash(evidence_payload),
            )
            evidence_target = (
                self.paths.root
                / "m7-orchestration"
                / "daily"
                / f"{session.isoformat()}.json"
            )
            created = not evidence_target.is_file()
            store = M7OrchestrationStore(self.paths)
            store.record_daily_evidence(evidence)
            decision = store.drift_decision()
            if created and evidence.warning:
                M7AuditLog(self.paths).append(
                    event_type=(
                        "drift_confirmed"
                        if decision.state in {"review_required", "training_authorized"}
                        else "drift_warning"
                    ),
                    recorded_at=recorded_at.astimezone(UTC),
                    actor="m7-drift-monitor",
                    run_id=None,
                    bundle_id=None,
                    package_sha256=None,
                    detail={
                        "session": session.isoformat(),
                        "state": decision.state,
                        "evidence_hash": evidence.evidence_hash,
                    },
                )
            base = {
                "schema_version": "m7-daily-drift-run-v1",
                "session": session.isoformat(),
                "warning": evidence.warning,
                "component_report_hashes": report_hashes,
            }
            return M7DailyDriftRunResult(
                session=session,
                warning=evidence.warning,
                component_report_hashes=report_hashes,
                evidence_hash=deterministic_hash(base),
            )

    def _active_components(self) -> Sequence[M7ForecastBundleComponent]:
        active = load_active_m7_forecast_bundle(self.paths)
        return () if active is None else active.components

    def _load_reference(self, component: M7ForecastBundleComponent) -> M7DriftReference:
        package_root = self.paths.models / "m7-packages"
        manifest = load_m7_package(package_root, component.package_id)
        if (
            manifest.package_sha256 != component.package_sha256
            or manifest.bundle_id != component.bundle_id
        ):
            raise M7DriftMonitorError("m7_drift_package_identity_mismatch")
        entries = tuple(
            row
            for row in manifest.explanation_artifacts
            if row.schema_version == "m7-drift-reference-v1"
        )
        if len(entries) != 1:
            raise M7DriftMonitorError("m7_drift_reference_artifact_invalid")
        path = (
            package_root
            / component.package_id
            / Path(*PurePosixPath(entries[0].relative_path).parts)
        )
        try:
            reference = M7DriftReference.model_validate_json(
                path.read_text(encoding="utf-8"), strict=True
            )
        except (OSError, ValueError) as error:
            raise M7DriftMonitorError("m7_drift_reference_invalid") from error
        if reference.source_identity_hash != manifest.feature_input_hash:
            raise M7DriftMonitorError("m7_drift_reference_source_mismatch")
        return reference

    def _observe_component(
        self, component: M7ForecastBundleComponent
    ) -> tuple[date, pd.DataFrame, Mapping[str, np.ndarray]]:
        try:
            artifact = load_latest_feature_artifact(self.paths)
            source = pq.read_table(artifact.path, columns=["session_date"]).to_pandas()
            session = max(pd.to_datetime(source["session_date"]).dt.date)
            prepared = prepare_m7_feature_data(self.paths, development_cutoff=session)
        except (OSError, RuntimeError, ValueError) as error:
            raise M7DriftMonitorError("m7_drift_observation_unavailable") from error
        eligible = prepared.frame[prepared.frame["eligible"].fillna(False).astype(bool)]
        latest = eligible[eligible["session_date"] == session].copy()
        latest.sort_values("symbol", inplace=True, kind="stable")
        if len(latest) != 100 or set(latest["symbol"].astype(str)) != set(
            SEED_UNIVERSE
        ):
            raise M7DriftMonitorError("m7_drift_seed100_incomplete")
        package_directory = self.paths.models / "m7-packages" / component.package_id
        if component.bundle_id == "direction":
            direction_ensemble = load_m7_direction_package(package_directory)
            predictions: dict[str, np.ndarray] = {}
            for horizon in (1, 5, 20):
                values = direction_ensemble.predict(prepared.frame, horizon=horizon)
                for index, label in enumerate(("down", "flat", "up")):
                    predictions[f"direction_h{horizon}_{label}"] = values[:, index]
            return session, latest.reset_index(drop=True), predictions
        range_ensemble = load_m7_range_package(package_directory)
        values = range_ensemble.predict(latest.reset_index(drop=True))
        return (
            session,
            latest.reset_index(drop=True),
            {
                "range_exit_by_1": values[:, 0],
                "range_exit_by_5": values[:, 1],
                "range_exit_by_20": values[:, 2],
            },
        )

    @staticmethod
    def _write_or_validate(target: Path, payload: object) -> str:
        if target.is_file():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise M7DriftMonitorError("m7_drift_report_invalid") from error
            comparable = payload
            if isinstance(existing, dict) and isinstance(payload, dict):
                comparable = {**payload, "observed_at": existing.get("observed_at")}
            if existing != comparable:
                raise M7DriftMonitorError("m7_drift_report_conflict")
            return file_sha256(target)
        return write_immutable_json(target, payload, pretty=False)


__all__ = [
    "M7DailyDriftRunResult",
    "M7DriftMonitor",
    "M7DriftMonitorError",
]
