"""Hash-chained M7 lifecycle audit events for Research and Health."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
from typing import Literal, Self, TypeAlias

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from stock_prediction.contracts.operations import ModelCapabilityBundle
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.live.config import LivePaths
from stock_prediction.modeling.report import deterministic_hash


M7AuditEventType = Literal[
    "training_queued",
    "training_started",
    "training_cancelled",
    "training_completed",
    "training_failed",
    "shadow_frozen",
    "blind_started",
    "blind_completed",
    "blind_invalidated",
    "candidate_rejected",
    "manual_approval",
    "promotion_completed",
    "drift_warning",
    "drift_confirmed",
    "integrity_failure",
    "automatic_rollback",
    "retention_deleted",
]
AuditScalar: TypeAlias = str | int | float | bool | None


class M7AuditError(RuntimeError):
    """The append-only audit ledger is malformed or has been changed."""


class M7AuditEvent(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    schema_version: Literal["m7-audit-event-v1"] = "m7-audit-event-v1"
    event_id: str = Field(pattern=r"^m7-audit-[0-9]{8}$")
    event_type: M7AuditEventType
    recorded_at: UtcDatetime
    actor: str = Field(min_length=1, max_length=128)
    run_id: str | None
    bundle_id: ModelCapabilityBundle | None
    package_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    detail: dict[str, AuditScalar]
    previous_event_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        expected = deterministic_hash(
            self.model_dump(mode="json", exclude={"event_hash"})
        )
        if self.event_hash != expected:
            raise ValueError("M7 audit event hash mismatch")
        return self


class M7AuditLog:
    def __init__(self, paths: LivePaths) -> None:
        self.path = paths.models / "m7-audit" / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.path.parent / ".audit.lock"))

    def append(
        self,
        *,
        event_type: M7AuditEventType,
        recorded_at: datetime,
        actor: str,
        run_id: str | None,
        bundle_id: ModelCapabilityBundle | None,
        package_sha256: str | None,
        detail: Mapping[str, AuditScalar],
    ) -> M7AuditEvent:
        with self._lock:
            events = self._events_unlocked()
            return self._append_unlocked(
                events,
                event_type=event_type,
                recorded_at=recorded_at,
                actor=actor,
                run_id=run_id,
                bundle_id=bundle_id,
                package_sha256=package_sha256,
                detail=detail,
            )

    def append_once(
        self,
        *,
        event_type: M7AuditEventType,
        recorded_at: datetime,
        actor: str,
        run_id: str | None,
        bundle_id: ModelCapabilityBundle | None,
        package_sha256: str | None,
        detail: Mapping[str, AuditScalar],
    ) -> M7AuditEvent:
        """Append one semantic event, or return its existing durable record."""

        checked_detail = dict(detail)
        with self._lock:
            events = self._events_unlocked()
            existing = next(
                (
                    event
                    for event in reversed(events)
                    if event.event_type == event_type
                    and event.actor == actor
                    and event.run_id == run_id
                    and event.bundle_id == bundle_id
                    and event.package_sha256 == package_sha256
                    and event.detail == checked_detail
                ),
                None,
            )
            if existing is not None:
                return existing
            return self._append_unlocked(
                events,
                event_type=event_type,
                recorded_at=recorded_at,
                actor=actor,
                run_id=run_id,
                bundle_id=bundle_id,
                package_sha256=package_sha256,
                detail=checked_detail,
            )

    def events(self) -> tuple[M7AuditEvent, ...]:
        with self._lock:
            return self._events_unlocked()

    def _events_unlocked(self) -> tuple[M7AuditEvent, ...]:
        if not self.path.exists():
            return ()
        events: list[M7AuditEvent] = []
        previous: str | None = None
        try:
            for index, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                event = M7AuditEvent.model_validate_json(line, strict=True)
                if (
                    event.event_id != f"m7-audit-{index:08d}"
                    or event.previous_event_hash != previous
                ):
                    raise M7AuditError("m7_audit_ledger_invalid")
                events.append(event)
                previous = event.event_hash
        except (OSError, ValidationError, ValueError) as error:
            raise M7AuditError("m7_audit_ledger_invalid") from error
        return tuple(events)

    def _append_unlocked(
        self,
        events: tuple[M7AuditEvent, ...],
        *,
        event_type: M7AuditEventType,
        recorded_at: datetime,
        actor: str,
        run_id: str | None,
        bundle_id: ModelCapabilityBundle | None,
        package_sha256: str | None,
        detail: Mapping[str, AuditScalar],
    ) -> M7AuditEvent:
        base: dict[str, object] = {
            "schema_version": "m7-audit-event-v1",
            "event_id": f"m7-audit-{len(events) + 1:08d}",
            "event_type": event_type,
            "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
            "actor": actor,
            "run_id": run_id,
            "bundle_id": bundle_id,
            "package_sha256": package_sha256,
            "detail": dict(detail),
            "previous_event_hash": None if not events else events[-1].event_hash,
        }
        event = M7AuditEvent.model_validate_json(
            json.dumps({**base, "event_hash": deterministic_hash(base)}),
            strict=True,
        )
        with self.path.open("ab") as stream:
            stream.write((event.model_dump_json() + "\n").encode("utf-8"))
            stream.flush()
        return event


__all__ = ["M7AuditError", "M7AuditEvent", "M7AuditEventType", "M7AuditLog"]
