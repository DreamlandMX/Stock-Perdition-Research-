"""Monthly and confirmed-drift training intent orchestration for M7."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
import json
from pathlib import Path
from typing import Literal, Self, TypeVar
from zoneinfo import ZoneInfo

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_prediction.contracts.operations import (
    ModelCapabilityBundle,
    TrainingTrigger,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V9,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_audit import M7AuditLog
from stock_prediction.modeling.report import deterministic_hash, write_immutable_json


class M7OrchestrationError(RuntimeError):
    """A schedule or drift intent conflicts with immutable evidence."""


EvidenceRow = TypeVar("EvidenceRow")


class M7DailyDriftEvidence(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    session: date
    eligible: bool
    core_feature_psi_fraction_above_020: float = Field(ge=0, le=1)
    prediction_distribution_shifted: bool
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def warning(self) -> bool:
        return self.eligible and (
            self.core_feature_psi_fraction_above_020 >= 0.20
            or self.prediction_distribution_shifted
        )


class M7WeeklyOutcomeEvidence(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    week_ending: date
    labels_mature: bool
    relative_brier_degradation: float
    calibration_error: float = Field(ge=0, le=1)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def breach(self) -> bool:
        return self.labels_mature and (
            self.relative_brier_degradation > 0.05 or self.calibration_error > 0.05
        )


class M7DriftDecision(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    state: Literal["none", "warning", "review_required", "training_authorized"]
    consecutive_daily_warning_sessions: int = Field(ge=0)
    consecutive_mature_weekly_breaches: int = Field(ge=0)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str


class M7TrainingIntent(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["m7-training-intent-v1"] = "m7-training-intent-v1"
    intent_id: str = Field(min_length=1)
    trigger: TrainingTrigger
    recipe_id: Literal["candidate-recipe-seed100-targeted-v9"]
    data_cutoff: date
    universe_version: str = Field(min_length=1)
    bundles: tuple[ModelCapabilityBundle, ...]
    eligible_at: UtcDatetime
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_intent(self) -> Self:
        if self.bundles != ("direction", "range_exit_20"):
            raise ValueError("M7 automatic intent requires both capability bundles")
        payload = self.model_dump(mode="json", exclude={"intent_id"})
        if self.intent_id != f"m7-intent-{deterministic_hash(payload)[:20]}":
            raise ValueError("M7 training intent identity mismatch")
        return self


class M7QueueReceipt(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    intent_id: str
    operation_id: str
    created: bool


IntentSubmitter = Callable[[M7TrainingIntent], str]


def build_monthly_training_intent(
    *,
    last_completed_session: date,
    universe_version: str,
    evidence_hash: str,
) -> M7TrainingIntent:
    local_zone = ZoneInfo("America/New_York")
    next_day = last_completed_session + timedelta(days=1)
    while next_day.weekday() != 5:
        next_day += timedelta(days=1)
    eligible_at = datetime.combine(next_day, time(2), tzinfo=local_zone).astimezone(UTC)
    return _build_intent(
        trigger="scheduled",
        data_cutoff=last_completed_session,
        universe_version=universe_version,
        eligible_at=eligible_at,
        evidence_hash=evidence_hash,
    )


class M7OrchestrationStore:
    def __init__(self, paths: LivePaths) -> None:
        self.paths = paths
        self.root = paths.root / "m7-orchestration"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.root / ".orchestration.lock"))

    def record_daily_evidence(self, evidence: M7DailyDriftEvidence) -> Path:
        return self._record(
            self.root / "daily" / f"{evidence.session.isoformat()}.json",
            evidence.model_dump(mode="json"),
        )

    def record_weekly_evidence(self, evidence: M7WeeklyOutcomeEvidence) -> Path:
        return self._record(
            self.root / "weekly" / f"{evidence.week_ending.isoformat()}.json",
            evidence.model_dump(mode="json"),
        )

    def drift_decision(self) -> M7DriftDecision:
        daily = self._load_daily()
        weekly = self._load_weekly()
        daily_count = _consecutive_suffix(daily, lambda row: row.warning)
        mature = tuple(row for row in weekly if row.labels_mature)
        weekly_count = _consecutive_suffix(mature, lambda row: row.breach)
        if daily_count >= 5 and weekly_count >= 2:
            state: Literal[
                "none", "warning", "review_required", "training_authorized"
            ] = "training_authorized"
            reason = "five_daily_warnings_and_two_mature_weekly_breaches"
        elif daily_count >= 5:
            state = "review_required"
            reason = "daily_drift_confirmed_outcomes_not_yet_confirmed"
        elif daily_count > 0:
            state = "warning"
            reason = "daily_drift_warning_streak_incomplete"
        else:
            state = "none"
            reason = "no_consecutive_drift_warning"
        evidence_hash = deterministic_hash(
            {
                "daily": tuple(row.model_dump(mode="json") for row in daily),
                "weekly": tuple(row.model_dump(mode="json") for row in weekly),
            }
        )
        return M7DriftDecision(
            state=state,
            consecutive_daily_warning_sessions=daily_count,
            consecutive_mature_weekly_breaches=weekly_count,
            evidence_hash=evidence_hash,
            reason=reason,
        )

    def build_drift_training_intent(
        self,
        *,
        data_cutoff: date,
        universe_version: str,
        eligible_at: datetime,
    ) -> M7TrainingIntent:
        decision = self.drift_decision()
        if decision.state != "training_authorized":
            raise M7OrchestrationError("m7_drift_training_not_authorized")
        return _build_intent(
            trigger="drift",
            data_cutoff=data_cutoff,
            universe_version=universe_version,
            eligible_at=eligible_at,
            evidence_hash=decision.evidence_hash,
        )

    def queue_once(
        self,
        intent: M7TrainingIntent,
        submitter: IntentSubmitter,
    ) -> M7QueueReceipt:
        with self._lock:
            target = self.root / "queue-receipts" / f"{intent.intent_id}.json"
            if target.is_file():
                try:
                    payload = json.loads(target.read_text(encoding="utf-8"))
                    operation_id = str(payload["operation_id"])
                except (OSError, json.JSONDecodeError, KeyError) as error:
                    raise M7OrchestrationError("m7_queue_receipt_invalid") from error
                return M7QueueReceipt(
                    intent_id=intent.intent_id,
                    operation_id=operation_id,
                    created=False,
                )
            operation_id = submitter(intent)
            if not operation_id:
                raise M7OrchestrationError("m7_training_submitter_returned_empty_id")
            write_immutable_json(
                target,
                {
                    "schema_version": "m7-queue-receipt-v1",
                    "intent": intent.model_dump(mode="json"),
                    "operation_id": operation_id,
                },
                pretty=False,
            )
            M7AuditLog(self.paths).append(
                event_type="training_queued",
                recorded_at=datetime.now(UTC),
                actor="m7-orchestrator",
                run_id=operation_id,
                bundle_id=None,
                package_sha256=None,
                detail={
                    "intent_id": intent.intent_id,
                    "trigger": intent.trigger,
                    "data_cutoff": intent.data_cutoff.isoformat(),
                },
            )
            return M7QueueReceipt(
                intent_id=intent.intent_id,
                operation_id=operation_id,
                created=True,
            )

    def _record(self, target: Path, payload: dict[str, object]) -> Path:
        with self._lock:
            if target.is_file():
                try:
                    existing = json.loads(target.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise M7OrchestrationError("m7_drift_evidence_invalid") from error
                if existing != payload:
                    raise M7OrchestrationError("m7_drift_evidence_conflict")
                return target
            write_immutable_json(target, payload, pretty=False)
            return target

    def _load_daily(self) -> tuple[M7DailyDriftEvidence, ...]:
        return tuple(
            M7DailyDriftEvidence.model_validate_json(
                path.read_text("utf-8"), strict=True
            )
            for path in sorted((self.root / "daily").glob("*.json"))
        )

    def _load_weekly(self) -> tuple[M7WeeklyOutcomeEvidence, ...]:
        return tuple(
            M7WeeklyOutcomeEvidence.model_validate_json(
                path.read_text("utf-8"), strict=True
            )
            for path in sorted((self.root / "weekly").glob("*.json"))
        )


def _build_intent(
    *,
    trigger: TrainingTrigger,
    data_cutoff: date,
    universe_version: str,
    eligible_at: datetime,
    evidence_hash: str,
) -> M7TrainingIntent:
    base = {
        "schema_version": "m7-training-intent-v1",
        "trigger": trigger,
        "recipe_id": CANDIDATE_RECIPE_SEED100_TARGETED_V9,
        "data_cutoff": data_cutoff.isoformat(),
        "universe_version": universe_version,
        "bundles": ("direction", "range_exit_20"),
        "eligible_at": eligible_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "evidence_hash": evidence_hash,
    }
    intent_id = f"m7-intent-{deterministic_hash(base)[:20]}"
    return M7TrainingIntent.model_validate_json(
        json.dumps({**base, "intent_id": intent_id}), strict=True
    )


def _consecutive_suffix(
    rows: Sequence[EvidenceRow], predicate: Callable[[EvidenceRow], bool]
) -> int:
    count = 0
    for row in reversed(rows):
        if not predicate(row):
            break
        count += 1
    return count


__all__ = [
    "M7DailyDriftEvidence",
    "M7DriftDecision",
    "M7OrchestrationError",
    "M7OrchestrationStore",
    "M7QueueReceipt",
    "M7TrainingIntent",
    "M7WeeklyOutcomeEvidence",
    "build_monthly_training_intent",
]
