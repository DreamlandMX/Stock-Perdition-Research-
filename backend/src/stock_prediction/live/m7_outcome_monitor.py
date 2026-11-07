"""Weekly mature-label performance drift evidence for M7 Champions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
import json
from pathlib import Path
from typing import Literal, Self

from filelock import FileLock
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_prediction.contracts.operations import ModelCapabilityBundle
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_orchestration import (
    M7OrchestrationStore,
    M7WeeklyOutcomeEvidence,
)
from stock_prediction.live.m7_integrity import M7IntegrityMonitor
from stock_prediction.live.m7_formal_inference import _resolve_prediction_path
from stock_prediction.live.features import load_latest_feature_artifact
from stock_prediction.live.m7_promotion import (
    M7ForecastBundleComponent,
    load_active_m7_forecast_bundle,
)
from stock_prediction.modeling.m7_metrics import (
    adaptive_classwise_ece,
    direction_brier_report,
    range_exit_calibration_report,
)
from stock_prediction.modeling.report import (
    deterministic_hash,
    file_sha256,
    write_immutable_json,
)
from stock_prediction.universe.seed import SEED_UNIVERSE


class M7OutcomeMonitorError(RuntimeError):
    """Weekly outcome evidence is malformed or conflicts with prior evidence."""


class M7OutcomeComponentReport(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    schema_version: Literal["m7-outcome-component-v1"] = "m7-outcome-component-v1"
    bundle_id: ModelCapabilityBundle
    reference_package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_brier: float = Field(gt=0)
    current_brier: float = Field(ge=0)
    relative_brier_degradation: float
    calibration_error: float = Field(ge=0, le=1)
    sample_count: int = Field(ge=1)
    mature_sample_keys_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def build(
        cls,
        *,
        bundle_id: ModelCapabilityBundle,
        package_sha256: str,
        reference_brier: float,
        current_brier: float,
        calibration_error: float,
        sample_count: int,
        mature_sample_keys_hash: str,
    ) -> M7OutcomeComponentReport:
        relative = round((current_brier - reference_brier) / reference_brier, 12)
        base = {
            "schema_version": "m7-outcome-component-v1",
            "bundle_id": bundle_id,
            "reference_package_sha256": package_sha256,
            "reference_brier": reference_brier,
            "current_brier": current_brier,
            "relative_brier_degradation": relative,
            "calibration_error": calibration_error,
            "sample_count": sample_count,
            "mature_sample_keys_hash": mature_sample_keys_hash,
        }
        return cls.model_validate(
            {**base, "evidence_hash": deterministic_hash(base)}, strict=True
        )

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        expected_relative = round(
            (self.current_brier - self.reference_brier) / self.reference_brier, 12
        )
        if self.relative_brier_degradation != expected_relative:
            raise ValueError("m7_outcome_relative_brier_invalid")
        expected = deterministic_hash(
            self.model_dump(mode="json", exclude={"evidence_hash"})
        )
        if self.evidence_hash != expected:
            raise ValueError("m7_outcome_component_hash_invalid")
        return self


class M7WeeklyOutcomeRunResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    week_ending: date
    labels_mature: bool
    breach: bool
    relative_brier_degradation: float
    calibration_error: float = Field(ge=0, le=1)
    detail_path: Path
    detail_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


EvaluationProvider = Callable[[date], Sequence[M7OutcomeComponentReport]]


class M7WeeklyOutcomeMonitor:
    """Persist weekly performance evidence without changing Champion state."""

    def __init__(
        self,
        paths: LivePaths,
        *,
        evaluation_provider: EvaluationProvider | None = None,
    ) -> None:
        self.paths = paths
        self.root = paths.models / "m7-drift" / "weekly"
        self.root.mkdir(parents=True, exist_ok=True)
        self.evaluation_provider = evaluation_provider or self._evaluate_current
        self._lock = FileLock(str(self.root / ".weekly-outcome.lock"))

    def run(
        self,
        *,
        week_ending: date,
        generated_at: datetime | None = None,
    ) -> M7WeeklyOutcomeRunResult | None:
        recorded_at = generated_at or datetime.now(UTC)
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise M7OutcomeMonitorError("m7_outcome_generated_at_invalid")
        with self._lock:
            reports = tuple(self.evaluation_provider(week_ending))
            if len({row.bundle_id for row in reports}) != len(reports):
                raise M7OutcomeMonitorError("m7_outcome_components_duplicate")
            labels_mature = bool(reports)
            relative = max(
                (row.relative_brier_degradation for row in reports), default=0.0
            )
            calibration = max((row.calibration_error for row in reports), default=0.0)
            detail = {
                "schema_version": "m7-weekly-outcome-report-v1",
                "week_ending": week_ending.isoformat(),
                "observed_at": recorded_at.astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "labels_mature": labels_mature,
                "components": [row.model_dump(mode="json") for row in reports],
                "relative_brier_degradation": relative,
                "calibration_error": calibration,
            }
            target = self.root / f"{week_ending.isoformat()}.json"
            created = not target.is_file()
            detail_hash = self._write_or_validate(target, detail)
            evidence = M7WeeklyOutcomeEvidence(
                week_ending=week_ending,
                labels_mature=labels_mature,
                relative_brier_degradation=relative,
                calibration_error=calibration,
                evidence_hash=detail_hash,
            )
            M7OrchestrationStore(self.paths).record_weekly_evidence(evidence)
            if created:
                integrity = M7IntegrityMonitor(self.paths)
                for report in reports:
                    if (
                        report.relative_brier_degradation > 0.05
                        or report.calibration_error > 0.05
                    ):
                        integrity.record_performance_drift(
                            bundle_id=report.bundle_id,
                            reason=(
                                "weekly_outcome_breach:"
                                f"relative_brier={report.relative_brier_degradation:.6f},"
                                f"ece={report.calibration_error:.6f}"
                            ),
                            observed_at=recorded_at.astimezone(UTC),
                        )
            return M7WeeklyOutcomeRunResult(
                week_ending=week_ending,
                labels_mature=labels_mature,
                breach=evidence.breach,
                relative_brier_degradation=relative,
                calibration_error=calibration,
                detail_path=target,
                detail_hash=detail_hash,
            )

    def _evaluate_current(
        self, week_ending: date
    ) -> Sequence[M7OutcomeComponentReport]:
        active = load_active_m7_forecast_bundle(self.paths)
        if active is None:
            return ()
        prediction_frames = self._formal_prediction_frames(week_ending)
        if not prediction_frames:
            return ()
        predictions = pd.concat(prediction_frames, ignore_index=True)
        try:
            artifact = load_latest_feature_artifact(self.paths)
            labels = pq.read_table(artifact.path).to_pandas()
        except (OSError, RuntimeError, ValueError) as error:
            raise M7OutcomeMonitorError("m7_outcome_labels_unavailable") from error
        labels["session_date"] = pd.to_datetime(labels["session_date"]).dt.date
        reports: list[M7OutcomeComponentReport] = []
        for component in active.components:
            selected = predictions[
                (predictions["bundle_id"] == component.bundle_id)
                & (predictions["package_sha256"] == component.package_sha256)
            ].copy()
            if selected.empty:
                continue
            keys = ["symbol", "anchor_session", "horizon", "outcome"]
            if (selected.groupby(keys)["probability"].nunique(dropna=False) > 1).any():
                raise M7OutcomeMonitorError("m7_outcome_prediction_conflict")
            selected.drop_duplicates(keys, keep="last", inplace=True)
            reference_brier = self._reference_brier(component)
            report = (
                _evaluate_direction_outcomes(
                    selected,
                    labels,
                    component=component,
                    reference_brier=reference_brier,
                )
                if component.bundle_id == "direction"
                else _evaluate_range_outcomes(
                    selected,
                    labels,
                    component=component,
                    reference_brier=reference_brier,
                )
            )
            if report is not None:
                reports.append(report)
        if reports and len(reports) != len(active.components):
            return ()
        return tuple(reports)

    def _formal_prediction_frames(self, week_ending: date) -> list[pd.DataFrame]:
        rows: list[tuple[date, Path]] = []
        for manifest_path in (self.paths.models / "m7-formal" / "predictions").glob(
            "*/*.manifest.json"
        ):
            try:
                session = date.fromisoformat(manifest_path.parent.name)
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                prediction = _resolve_prediction_path(
                    self.paths, str(payload["prediction_path"])
                )
                if (
                    session <= week_ending
                    and file_sha256(prediction) == payload["prediction_sha256"]
                ):
                    rows.append((session, prediction))
            except (
                OSError,
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as error:
                raise M7OutcomeMonitorError(
                    "m7_outcome_formal_manifest_invalid"
                ) from error
        selected_sessions = set(session for session, _path in sorted(rows)[-20:])
        return [
            pq.read_table(path).to_pandas()
            for session, path in rows
            if session in selected_sessions
        ]

    def _reference_brier(self, component: M7ForecastBundleComponent) -> float:
        target = (
            self.paths.models
            / "m7-blind"
            / component.blind_window_id
            / "reports"
            / f"{component.blind_report_hash}.json"
        )
        try:
            if file_sha256(target) != component.blind_report_hash:
                raise ValueError
            payload = json.loads(target.read_text(encoding="utf-8"))
            if payload.get("package_sha256") != component.package_sha256:
                raise ValueError
            metrics = payload["decision"]["metrics"]
            field = (
                "candidate_macro_brier"
                if component.bundle_id == "direction"
                else "candidate_brier"
            )
            value = float(metrics[field])
            if not np.isfinite(value) or value <= 0:
                raise ValueError
            return value
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise M7OutcomeMonitorError("m7_outcome_reference_invalid") from error

    @staticmethod
    def _write_or_validate(target: Path, payload: object) -> str:
        if target.is_file():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise M7OutcomeMonitorError("m7_outcome_report_invalid") from error
            comparable = payload
            if isinstance(existing, dict) and isinstance(payload, dict):
                comparable = {**payload, "observed_at": existing.get("observed_at")}
            if existing != comparable:
                raise M7OutcomeMonitorError("m7_outcome_report_conflict")
            return file_sha256(target)
        return write_immutable_json(target, payload, pretty=False)


def _evaluate_direction_outcomes(
    predictions: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    component: M7ForecastBundleComponent,
    reference_brier: float,
) -> M7OutcomeComponentReport | None:
    frame = predictions.copy()
    frame["anchor_session"] = pd.to_datetime(frame["anchor_session"]).dt.date
    pivot = frame.pivot(
        index=["symbol", "anchor_session", "horizon"],
        columns="outcome",
        values="probability",
    ).reset_index()
    reports: list[tuple[float, float]] = []
    mature_keys: list[tuple[str, str, int]] = []
    sample_count = 0
    for horizon in (1, 5, 20):
        selected = pivot[pivot["horizon"] == horizon].merge(
            labels[["symbol", "session_date", f"direction_{horizon}"]],
            left_on=["symbol", "anchor_session"],
            right_on=["symbol", "session_date"],
            how="inner",
            validate="many_to_one",
        )
        selected = selected[selected[f"direction_{horizon}"].notna()].copy()
        if len(selected) < 100 or set(selected["symbol"]) != set(SEED_UNIVERSE):
            return None
        label_values = tuple(
            _direction_label(value) for value in selected[f"direction_{horizon}"]
        )
        probabilities = selected[["down", "flat", "up"]].to_numpy(dtype=float)
        brier = direction_brier_report(
            symbols=tuple(selected["symbol"].astype(str)),
            labels=label_values,
            probabilities=probabilities,
            classes=("down", "flat", "up"),
        ).symbol_equal_brier
        ece = adaptive_classwise_ece(
            symbols=tuple(selected["symbol"].astype(str)),
            labels=label_values,
            probabilities=probabilities,
            classes=("down", "flat", "up"),
            minimum_bin_count=5,
        ).macro_ece
        reports.append((brier, ece))
        sample_count += len(selected)
        mature_keys.extend(
            (str(row.symbol), row.anchor_session.isoformat(), horizon)
            for row in selected.itertuples(index=False)
        )
    return M7OutcomeComponentReport.build(
        bundle_id="direction",
        package_sha256=component.package_sha256,
        reference_brier=reference_brier,
        current_brier=float(np.mean([row[0] for row in reports])),
        calibration_error=max(row[1] for row in reports),
        sample_count=sample_count,
        mature_sample_keys_hash=deterministic_hash(sorted(mature_keys)),
    )


def _evaluate_range_outcomes(
    predictions: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    component: M7ForecastBundleComponent,
    reference_brier: float,
) -> M7OutcomeComponentReport | None:
    selected = predictions[
        (predictions["horizon"] == 20) & (predictions["outcome"] == "exit_by")
    ].copy()
    selected["anchor_session"] = pd.to_datetime(selected["anchor_session"]).dt.date
    selected = selected.merge(
        labels[["symbol", "session_date", "range_exit_event_20"]],
        left_on=["symbol", "anchor_session"],
        right_on=["symbol", "session_date"],
        how="inner",
        validate="many_to_one",
    )
    selected = selected[selected["range_exit_event_20"].notna()].copy()
    if len(selected) < 100 or set(selected["symbol"]) != set(SEED_UNIVERSE):
        return None
    observed = selected["range_exit_event_20"].astype(int).to_numpy()
    probabilities = selected["probability"].to_numpy(dtype=float)
    losses = np.square(probabilities - observed)
    symbol_brier = (
        pd.DataFrame({"symbol": selected["symbol"].astype(str), "loss": losses})
        .groupby("symbol")["loss"]
        .mean()
    )
    calibration = range_exit_calibration_report(
        symbols=tuple(selected["symbol"].astype(str)),
        observed=tuple(int(value) for value in observed),
        cumulative_probability=tuple(float(value) for value in probabilities),
        minimum_bin_count=5,
    ).calibration_error
    mature_keys = sorted(
        (str(row.symbol), row.anchor_session.isoformat(), 20)
        for row in selected.itertuples(index=False)
    )
    return M7OutcomeComponentReport.build(
        bundle_id="range_exit_20",
        package_sha256=component.package_sha256,
        reference_brier=reference_brier,
        current_brier=float(symbol_brier.mean()),
        calibration_error=calibration,
        sample_count=len(selected),
        mature_sample_keys_hash=deterministic_hash(mature_keys),
    )


def _direction_label(value: object) -> str:
    mapping: dict[object, str] = {
        -1: "down",
        0: "flat",
        1: "up",
        "-1": "down",
        "0": "flat",
        "1": "up",
        "down": "down",
        "flat": "flat",
        "up": "up",
    }
    try:
        return mapping[value]
    except (KeyError, TypeError) as error:
        raise M7OutcomeMonitorError("m7_outcome_direction_label_invalid") from error


__all__ = [
    "M7OutcomeComponentReport",
    "M7OutcomeMonitorError",
    "M7WeeklyOutcomeMonitor",
    "M7WeeklyOutcomeRunResult",
]
