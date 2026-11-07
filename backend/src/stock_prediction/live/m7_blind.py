"""Append-only, forward-only M7 shadow prediction and mature-label ledger."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Literal, Self

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from stock_prediction.contracts.operations import ModelCapabilityBundle
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_audit import M7AuditLog
from stock_prediction.live.promotion_windows import PromotionWindowRegistry
from stock_prediction.modeling.m7_blind_gates import M7BlindGateDecision
from stock_prediction.modeling.report import canonical_json_bytes, write_immutable_json


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class M7BlindLedgerError(RuntimeError):
    """Blind evidence is late, duplicated, mutated, or inconsistent."""


class M7BlindLedgerManifest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["m7-blind-ledger-v1"] = "m7-blind-ledger-v1"
    window_id: str = Field(min_length=1)
    bundle_id: ModelCapabilityBundle
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    incumbent_id: str | None = Field(default=None, min_length=1)
    range_five_day_anchor_id: str | None = Field(default=None, min_length=1)
    registration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scheduled_sessions: tuple[date, ...] = Field(min_length=97)
    nominal_end_session: date
    required_available_anchors: Literal[77] = 77
    label_horizon_sessions: Literal[20] = 20
    created_at: UtcDatetime
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if tuple(sorted(set(self.scheduled_sessions))) != self.scheduled_sessions:
            raise ValueError("M7 blind scheduled sessions must be ordered and unique")
        if self.nominal_end_session not in self.scheduled_sessions:
            raise ValueError("M7 blind nominal end is outside scheduled sessions")
        expected = _hash_payload(
            self.model_dump(
                mode="json",
                exclude={"manifest_hash"},
                exclude_none=True,
            )
        )
        if self.manifest_hash != expected:
            raise ValueError("M7 blind manifest hash mismatch")
        return self


class M7BlindLedgerStatus(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    state: Literal[
        "collecting",
        "label_maturing",
        "ready_for_review",
        "consumed",
        "invalidated",
    ]
    available_anchor_count: int = Field(ge=0, le=77)
    missing_anchor_count: int = Field(ge=0)
    mature_label_count: int = Field(ge=0, le=77)
    next_scheduled_session: date | None
    last_available_anchor: date | None
    terminal_reason: str | None


class M7BlindWindowLedger:
    def __init__(self, directory: Path, *, audit: M7AuditLog | None = None) -> None:
        self.directory = directory.resolve()
        self._lock = FileLock(str(self.directory / ".ledger.lock"))
        self.audit = audit
        self.manifest = self._load_manifest()

    def record_prediction(
        self,
        *,
        anchor_session: date,
        generated_at: datetime,
        prediction_artifact_hash: str,
    ) -> Path:
        if _SHA256.fullmatch(prediction_artifact_hash) is None:
            raise M7BlindLedgerError("m7_blind_prediction_hash_invalid")
        return self._record_prediction_event(
            anchor_session=anchor_session,
            recorded_at=generated_at,
            status="available",
            artifact_hash=prediction_artifact_hash,
            reason=None,
        )

    def record_missing_prediction(
        self,
        *,
        anchor_session: date,
        recorded_at: datetime,
        reason: str,
    ) -> Path:
        if not reason.strip():
            raise M7BlindLedgerError("m7_blind_missing_reason_required")
        return self._record_prediction_event(
            anchor_session=anchor_session,
            recorded_at=recorded_at,
            status="missing",
            artifact_hash=None,
            reason=reason,
        )

    def record_mature_label(
        self,
        *,
        anchor_session: date,
        matured_at: datetime,
        label_artifact_hash: str,
        sample_keys_hash: str,
        sample_count: int,
    ) -> Path:
        if (
            _SHA256.fullmatch(label_artifact_hash) is None
            or _SHA256.fullmatch(sample_keys_hash) is None
            or sample_count < 1
        ):
            raise M7BlindLedgerError("m7_blind_label_identity_invalid")
        with self._lock:
            self._require_active()
            predictions = self._prediction_events()
            available = {
                date.fromisoformat(str(event["anchor_session"])): event
                for event in predictions
                if event["status"] == "available"
            }
            if anchor_session not in available:
                raise M7BlindLedgerError("m7_blind_label_requires_available_prediction")
            position = self.manifest.scheduled_sessions.index(anchor_session)
            maturity_position = position + self.manifest.label_horizon_sessions
            if maturity_position >= len(self.manifest.scheduled_sessions):
                raise M7BlindLedgerError("m7_blind_maturity_calendar_incomplete")
            maturity_session = self.manifest.scheduled_sessions[maturity_position]
            _require_utc(matured_at)
            if matured_at.date() < maturity_session:
                raise M7BlindLedgerError("m7_blind_label_not_mature")
            target = self.directory / "labels" / f"{anchor_session.isoformat()}.json"
            if target.exists():
                raise M7BlindLedgerError("m7_blind_label_already_recorded")
            payload: dict[str, object] = {
                "schema_version": "m7-blind-label-v1",
                "window_id": self.manifest.window_id,
                "package_sha256": self.manifest.package_sha256,
                "anchor_session": anchor_session.isoformat(),
                "maturity_session": maturity_session.isoformat(),
                "matured_at": matured_at.isoformat(),
                "label_artifact_hash": label_artifact_hash,
                "sample_keys_hash": sample_keys_hash,
                "sample_count": sample_count,
            }
            payload["event_hash"] = _hash_payload(payload)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_immutable_json(target, payload, pretty=False)
            return target

    def invalidate(self, *, reason: str) -> Path:
        if not reason.strip():
            raise M7BlindLedgerError("m7_blind_invalidation_reason_required")
        target = self._write_terminal(
            "invalidated", reason=reason, blind_report_hash=None
        )
        if self.audit is not None:
            self.audit.append(
                event_type="blind_invalidated",
                recorded_at=datetime.now(self.manifest.created_at.tzinfo),
                actor="blind-worker",
                run_id=None,
                bundle_id=self.manifest.bundle_id,
                package_sha256=self.manifest.package_sha256,
                detail={"window_id": self.manifest.window_id, "reason": reason},
            )
        return target

    def consume(self, *, blind_report_hash: str) -> Path:
        if _SHA256.fullmatch(blind_report_hash) is None:
            raise M7BlindLedgerError("m7_blind_report_hash_invalid")
        if (self.directory / "terminal.json").exists():
            raise M7BlindLedgerError("m7_blind_already_terminal")
        if self.status().state != "ready_for_review":
            raise M7BlindLedgerError("m7_blind_not_ready_for_consumption")
        return self._write_terminal(
            "consumed",
            reason="blind_report_consumed",
            blind_report_hash=blind_report_hash,
        )

    def publish_review_report(
        self,
        *,
        decision: M7BlindGateDecision,
        generated_at: datetime,
        evidence: Mapping[str, object] | None = None,
    ) -> tuple[Path, str]:
        """Publish one content-addressed decision and consume this window."""

        _require_utc(generated_at)
        if decision.bundle_id != self.manifest.bundle_id:
            raise M7BlindLedgerError("m7_blind_report_bundle_mismatch")
        with self._lock:
            if (self.directory / "terminal.json").exists():
                raise M7BlindLedgerError("m7_blind_already_terminal")
            status = self.status()
            if status.state != "ready_for_review":
                raise M7BlindLedgerError("m7_blind_not_ready_for_consumption")
            payload: dict[str, object] = {
                "schema_version": "m7-blind-report-v1",
                "window_id": self.manifest.window_id,
                "bundle_id": self.manifest.bundle_id,
                "package_sha256": self.manifest.package_sha256,
                "manifest_hash": self.manifest.manifest_hash,
                "registration_hash": self.manifest.registration_hash,
                "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
                "evidence_scope": "blind_complete",
                "available_anchor_count": status.available_anchor_count,
                "mature_label_count": status.mature_label_count,
                "decision": decision.model_dump(mode="json"),
            }
            if evidence is not None:
                payload["evidence"] = dict(evidence)
            report_hash = hashlib.sha256(
                canonical_json_bytes(payload, pretty=False)
            ).hexdigest()
            target = self.directory / "reports" / f"{report_hash}.json"
            written_hash = write_immutable_json(target, payload, pretty=False)
            if written_hash != report_hash:
                raise M7BlindLedgerError("m7_blind_report_hash_mismatch")
            self._write_terminal_unlocked(
                "consumed",
                reason=f"blind_report_{decision.status}",
                blind_report_hash=report_hash,
            )
            if self.audit is not None:
                self.audit.append(
                    event_type="blind_completed",
                    recorded_at=generated_at,
                    actor="blind-worker",
                    run_id=None,
                    bundle_id=self.manifest.bundle_id,
                    package_sha256=self.manifest.package_sha256,
                    detail={
                        "window_id": self.manifest.window_id,
                        "report_hash": report_hash,
                        "decision": decision.status,
                    },
                )
            return target, report_hash

    def status(self) -> M7BlindLedgerStatus:
        terminal = self._terminal_event()
        predictions = self._prediction_events()
        labels = self._label_events()
        available_sessions = tuple(
            date.fromisoformat(str(event["anchor_session"]))
            for event in predictions
            if event["status"] == "available"
        )
        missing_count = sum(event["status"] == "missing" for event in predictions)
        next_session = (
            self.manifest.scheduled_sessions[len(predictions)]
            if len(predictions) < len(self.manifest.scheduled_sessions)
            and len(available_sessions) < self.manifest.required_available_anchors
            else None
        )
        if terminal is not None:
            state = str(terminal["status"])
            reason = str(terminal["reason"])
        elif len(available_sessions) < self.manifest.required_available_anchors:
            state = "collecting"
            reason = None
        elif len(labels) < self.manifest.required_available_anchors:
            state = "label_maturing"
            reason = None
        else:
            state = "ready_for_review"
            reason = None
        return M7BlindLedgerStatus.model_validate(
            {
                "state": state,
                "available_anchor_count": len(available_sessions),
                "missing_anchor_count": missing_count,
                "mature_label_count": len(labels),
                "next_scheduled_session": next_session,
                "last_available_anchor": (
                    available_sessions[-1] if available_sessions else None
                ),
                "terminal_reason": reason,
            },
            strict=True,
        )

    def _record_prediction_event(
        self,
        *,
        anchor_session: date,
        recorded_at: datetime,
        status: Literal["available", "missing"],
        artifact_hash: str | None,
        reason: str | None,
    ) -> Path:
        _require_utc(recorded_at)
        if recorded_at.date() != anchor_session:
            raise M7BlindLedgerError("m7_blind_realtime_timestamp_invalid")
        with self._lock:
            self._require_active()
            current = self.status()
            if (
                current.available_anchor_count
                >= self.manifest.required_available_anchors
            ):
                raise M7BlindLedgerError("m7_blind_collection_complete")
            if current.next_scheduled_session != anchor_session:
                raise M7BlindLedgerError("m7_blind_requires_next_scheduled_session")
            target = (
                self.directory / "predictions" / f"{anchor_session.isoformat()}.json"
            )
            if target.exists():
                raise M7BlindLedgerError("m7_blind_prediction_already_recorded")
            payload: dict[str, object] = {
                "schema_version": "m7-blind-prediction-v1",
                "window_id": self.manifest.window_id,
                "bundle_id": self.manifest.bundle_id,
                "package_sha256": self.manifest.package_sha256,
                "anchor_session": anchor_session.isoformat(),
                "recorded_at": recorded_at.isoformat(),
                "status": status,
                "prediction_artifact_hash": artifact_hash,
                "reason": reason,
            }
            payload["event_hash"] = _hash_payload(payload)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_immutable_json(target, payload, pretty=False)
            return target

    def _write_terminal(
        self,
        status: Literal["consumed", "invalidated"],
        *,
        reason: str,
        blind_report_hash: str | None,
    ) -> Path:
        with self._lock:
            return self._write_terminal_unlocked(
                status,
                reason=reason,
                blind_report_hash=blind_report_hash,
            )

    def _write_terminal_unlocked(
        self,
        status: Literal["consumed", "invalidated"],
        *,
        reason: str,
        blind_report_hash: str | None,
    ) -> Path:
        target = self.directory / "terminal.json"
        if target.exists():
            raise M7BlindLedgerError("m7_blind_already_terminal")
        payload: dict[str, object] = {
            "schema_version": "m7-blind-terminal-v1",
            "window_id": self.manifest.window_id,
            "package_sha256": self.manifest.package_sha256,
            "status": status,
            "reason": reason,
            "blind_report_hash": blind_report_hash,
            "recorded_at": datetime.now(self.manifest.created_at.tzinfo).isoformat(),
        }
        payload["event_hash"] = _hash_payload(payload)
        write_immutable_json(target, payload, pretty=False)
        return target

    def _require_active(self) -> None:
        if (self.directory / "terminal.json").exists():
            raise M7BlindLedgerError("m7_blind_ledger_terminal")

    def _load_manifest(self) -> M7BlindLedgerManifest:
        try:
            return M7BlindLedgerManifest.model_validate_json(
                (self.directory / "manifest.json").read_text(encoding="utf-8"),
                strict=True,
            )
        except (OSError, ValidationError) as error:
            raise M7BlindLedgerError("m7_blind_manifest_invalid") from error

    def _prediction_events(self) -> tuple[dict[str, object], ...]:
        return self._events("predictions", "m7-blind-prediction-v1")

    def _label_events(self) -> tuple[dict[str, object], ...]:
        return self._events("labels", "m7-blind-label-v1")

    def _terminal_event(self) -> dict[str, object] | None:
        target = self.directory / "terminal.json"
        if not target.is_file():
            return None
        return self._load_event(target, "m7-blind-terminal-v1")

    def _events(self, directory: str, schema: str) -> tuple[dict[str, object], ...]:
        root = self.directory / directory
        if not root.is_dir():
            return ()
        return tuple(
            self._load_event(path, schema) for path in sorted(root.glob("*.json"))
        )

    def _load_event(self, path: Path, schema: str) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise M7BlindLedgerError("m7_blind_event_invalid") from error
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != schema
            or payload.get("window_id") != self.manifest.window_id
            or payload.get("package_sha256") != self.manifest.package_sha256
            or not isinstance(payload.get("event_hash"), str)
        ):
            raise M7BlindLedgerError("m7_blind_event_invalid")
        supplied_hash = str(payload.pop("event_hash"))
        expected = _hash_payload(payload)
        payload["event_hash"] = supplied_hash
        if supplied_hash != expected:
            raise M7BlindLedgerError("m7_blind_event_invalid")
        return payload


def register_m7_blind_ledger(
    paths: LivePaths,
    *,
    window_id: str,
    bundle_id: ModelCapabilityBundle,
    package_sha256: str,
    incumbent_id: str | None = None,
    range_five_day_anchor_id: str | None = None,
    scheduled_sessions: Sequence[date],
    registration_path: Path,
) -> M7BlindWindowLedger:
    registered = PromotionWindowRegistry(paths).load(window_id)
    if registered.package_sha256 != package_sha256:
        raise M7BlindLedgerError("m7_blind_package_registration_mismatch")
    try:
        registration_payload = json.loads(registration_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M7BlindLedgerError("m7_blind_registration_invalid") from error
    if registration_payload.get("registration_hash") != registered.registration_hash:
        raise M7BlindLedgerError("m7_blind_registration_hash_mismatch")
    sessions = tuple(scheduled_sessions)
    if (
        len(sessions) < 97
        or tuple(sorted(set(sessions))) != sessions
        or sessions[0] != registered.definition.start
    ):
        raise M7BlindLedgerError("m7_blind_schedule_invalid")
    directory = paths.models / "m7-blind" / window_id
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        ledger = M7BlindWindowLedger(directory, audit=M7AuditLog(paths))
        if (
            ledger.manifest.bundle_id != bundle_id
            or ledger.manifest.package_sha256 != package_sha256
            or ledger.manifest.incumbent_id != incumbent_id
            or ledger.manifest.range_five_day_anchor_id
            != range_five_day_anchor_id
            or ledger.manifest.scheduled_sessions != sessions
        ):
            raise M7BlindLedgerError("m7_blind_registration_conflict")
        return ledger
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": "m7-blind-ledger-v1",
        "window_id": window_id,
        "bundle_id": bundle_id,
        "package_sha256": package_sha256,
        "registration_hash": registered.registration_hash,
        "scheduled_sessions": tuple(session.isoformat() for session in sessions),
        "nominal_end_session": registered.definition.end.isoformat(),
        "required_available_anchors": 77,
        "label_horizon_sessions": 20,
        "created_at": (
            registered.frozen_at.isoformat().replace("+00:00", "Z")
            if registered.frozen_at
            else None
        ),
    }
    if incumbent_id is not None:
        payload["incumbent_id"] = incumbent_id
    if range_five_day_anchor_id is not None:
        payload["range_five_day_anchor_id"] = range_five_day_anchor_id
    payload["manifest_hash"] = _hash_payload(payload)
    write_immutable_json(manifest_path, payload, pretty=False)
    audit = M7AuditLog(paths)
    audit.append(
        event_type="blind_started",
        recorded_at=registered.frozen_at or datetime.now(UTC),
        actor="blind-worker",
        run_id=None,
        bundle_id=bundle_id,
        package_sha256=package_sha256,
        detail={"window_id": window_id},
    )
    return M7BlindWindowLedger(directory, audit=audit)


def _require_utc(value: datetime) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise M7BlindLedgerError("m7_blind_timestamp_must_be_utc")


def _hash_payload(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "M7BlindLedgerError",
    "M7BlindLedgerManifest",
    "M7BlindLedgerStatus",
    "M7BlindWindowLedger",
    "register_m7_blind_ledger",
]
