"""Append-only JSONL trial and evaluation-window governance ledger."""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from filelock import FileLock
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from stock_prediction.contracts.market_data import Identifier
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.modeling.contracts import (
    BacktestTrial,
    EvaluationRole,
    EvaluationWindow,
    TrialFinished,
    TrialStatus,
)


class TrialLedgerError(RuntimeError):
    """Ledger history or requested state transition is invalid."""


class LedgerRecordType(StrEnum):
    TRIAL_REGISTERED = "trial_registered"
    TRIAL_FINISHED = "trial_finished"
    WINDOW_CONSUMED = "window_consumed"


class TrialRegisteredRecord(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    record_type: Literal[LedgerRecordType.TRIAL_REGISTERED]
    recorded_at: UtcDatetime
    record_id: Identifier
    payload: BacktestTrial

    @model_validator(mode="after")
    def validate_envelope(self) -> "TrialRegisteredRecord":
        if self.record_id != self.payload.trial_id:
            raise ValueError("trial registration record ID does not match payload")
        if self.recorded_at != self.payload.registered_at:
            raise ValueError("trial registration time does not match payload")
        return self


class TrialFinishedRecord(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    record_type: Literal[LedgerRecordType.TRIAL_FINISHED]
    recorded_at: UtcDatetime
    record_id: Identifier
    payload: TrialFinished

    @model_validator(mode="after")
    def validate_envelope(self) -> "TrialFinishedRecord":
        if self.record_id != self.payload.trial_id:
            raise ValueError("trial finish record ID does not match payload")
        if self.recorded_at != self.payload.finished_at:
            raise ValueError("trial finish time does not match payload")
        return self


class WindowConsumedRecord(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    record_type: Literal[LedgerRecordType.WINDOW_CONSUMED]
    recorded_at: UtcDatetime
    record_id: Identifier
    payload: EvaluationWindow

    @model_validator(mode="after")
    def validate_envelope(self) -> "WindowConsumedRecord":
        if self.record_id != self.payload.window_id:
            raise ValueError("window consumption record ID does not match payload")
        if self.recorded_at != self.payload.consumed_at:
            raise ValueError("window consumption time does not match payload")
        return self


LedgerRecord = Annotated[
    TrialRegisteredRecord | TrialFinishedRecord | WindowConsumedRecord,
    Field(discriminator="record_type"),
]
_RECORD_ADAPTER: TypeAdapter[LedgerRecord] = TypeAdapter(LedgerRecord)


class JsonlTrialLedger:
    """Serialize and replay every transition under a process-safe append lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(path.with_suffix(path.suffix + ".lock")))

    def read_records(self) -> tuple[LedgerRecord, ...]:
        with self._lock:
            return self._read_unlocked()

    def register(self, trial: BacktestTrial) -> TrialRegisteredRecord:
        try:
            validated = BacktestTrial.model_validate(
                trial.model_dump(mode="python"), strict=True
            )
        except ValidationError as error:
            raise TrialLedgerError("registered trial contract is invalid") from error
        if validated.status is not TrialStatus.REGISTERED:
            raise TrialLedgerError("trial registration must have registered status")
        with self._lock:
            records = self._read_unlocked()
            if self._trial_registration(records, validated.trial_id) is not None:
                raise TrialLedgerError("duplicate trial registration")
            for existing in self._registrations_for_window(
                records, validated.evaluation_window_id
            ):
                self._validate_window_definition(
                    existing.payload.evaluation_window,
                    validated.evaluation_window,
                )
            if self._window_consumption(
                records, validated.evaluation_window_id
            ) is not None:
                raise TrialLedgerError(
                    "trial references a consumed evaluation window"
                )
            if validated.parent_trial_id is not None:
                if self._trial_registration(
                    records, validated.parent_trial_id
                ) is None:
                    raise TrialLedgerError("parent trial must already be registered")
                parent_finish = self._trial_finish(
                    records, validated.parent_trial_id
                )
                if parent_finish is None:
                    raise TrialLedgerError("parent trial must be finished")
                if validated.registered_at < parent_finish.recorded_at:
                    raise TrialLedgerError(
                        "derived trial registration cannot precede parent result"
                    )
            record = TrialRegisteredRecord(
                record_type=LedgerRecordType.TRIAL_REGISTERED,
                recorded_at=validated.registered_at,
                record_id=validated.trial_id,
                payload=validated,
            )
            self._append_unlocked(record)
            return record

    def finish(
        self,
        trial_id: str,
        *,
        status: TrialStatus,
        result: dict[str, float] | None,
        finished_at: UtcDatetime,
        stop_reason: str | None,
        lockbox_window: EvaluationWindow | None = None,
        consumed_at: UtcDatetime | None = None,
        lockbox_reason: str = "trial_evaluation",
    ) -> TrialFinishedRecord:
        if status is TrialStatus.SUCCEEDED and not result:
            raise TrialLedgerError("succeeded trial requires a result")
        if status in {
            TrialStatus.FAILED,
            TrialStatus.ABORTED,
            TrialStatus.REJECTED,
        } and not stop_reason:
            raise TrialLedgerError("failed or aborted trial requires a stop reason")
        with self._lock:
            records = self._read_unlocked()
            registration = self._trial_registration(records, trial_id)
            if registration is None:
                raise TrialLedgerError("trial must be registered before result")
            if self._trial_finish(records, trial_id) is not None:
                raise TrialLedgerError("trial is already finished")
            self._validate_result_metrics(registration.payload, status, result)
            if (
                registration.payload.evaluation_window.role
                is EvaluationRole.LOCKBOX
                and result is not None
                and lockbox_window is None
            ):
                raise TrialLedgerError(
                    "lockbox trial result requires atomic lockbox consumption"
                )
            consumed_window: EvaluationWindow | None = None
            if lockbox_window is not None:
                try:
                    candidate = EvaluationWindow.model_validate(
                        lockbox_window.model_dump(mode="python"), strict=True
                    )
                except ValidationError as error:
                    raise TrialLedgerError("lockbox window contract is invalid") from error
                if candidate.consumed_at is not None:
                    raise TrialLedgerError("evaluation window is already consumed")
                if candidate.role is not EvaluationRole.LOCKBOX:
                    raise TrialLedgerError("atomic trial consumption requires a lockbox")
                self._validate_window_identity(registration.payload, candidate)
                if self._window_consumption(records, candidate.window_id) is not None:
                    raise TrialLedgerError("evaluation window is already consumed")
                if consumed_at is None:
                    raise TrialLedgerError("lockbox consumption time is required")
                for bound_trial in self._registrations_for_window(
                    records, candidate.window_id
                ):
                    if consumed_at < bound_trial.recorded_at:
                        raise TrialLedgerError(
                            "lockbox consumption cannot precede trial registration"
                        )
                try:
                    consumed_window = EvaluationWindow.model_validate(
                        {
                            **candidate.model_dump(),
                            "consumed_at": consumed_at,
                            "consumption_reason": lockbox_reason,
                        },
                        strict=True,
                    )
                except ValidationError as error:
                    raise TrialLedgerError("lockbox consumption is invalid") from error
            elif consumed_at is not None:
                raise TrialLedgerError(
                    "consumed_at requires an atomic lockbox finish"
                )
            if consumed_at is not None and consumed_at > finished_at:
                raise TrialLedgerError(
                    "lockbox consumption cannot occur after trial finish"
                )
            try:
                finished = TrialFinished(
                    trial_id=trial_id,
                    evaluation_window_id=registration.payload.evaluation_window_id,
                    status=status,
                    result=result,
                    stop_reason=stop_reason,
                    finished_at=finished_at,
                    touched_lockbox=consumed_window is not None,
                    consumed_at=consumed_at,
                    consumed_window=consumed_window,
                )
            except ValidationError as error:
                raise TrialLedgerError("trial result contract is invalid or non-finite") from error
            if finished.finished_at < registration.recorded_at:
                raise TrialLedgerError("trial finish cannot precede registration")
            self._validate_finished_trial(
                registration.payload,
                finished,
                self._window_consumption(records, finished.evaluation_window_id),
            )
            record = TrialFinishedRecord(
                record_type=LedgerRecordType.TRIAL_FINISHED,
                recorded_at=finished.finished_at,
                record_id=trial_id,
                payload=finished,
            )
            self._append_unlocked(record)
            return record

    def consume_window(
        self,
        window: EvaluationWindow,
        *,
        consumed_at: UtcDatetime,
        reason: str,
    ) -> EvaluationWindow:
        try:
            validated = EvaluationWindow.model_validate(
                window.model_dump(mode="python"), strict=True
            )
        except ValidationError as error:
            raise TrialLedgerError("evaluation window contract is invalid") from error
        if validated.consumed_at is not None:
            raise TrialLedgerError("evaluation window is already consumed")
        if validated.role is EvaluationRole.LOCKBOX:
            raise TrialLedgerError(
                "lockbox consumption must be atomic with trial finish"
            )
        with self._lock:
            records = self._read_unlocked()
            if self._window_consumption(records, validated.window_id) is not None:
                raise TrialLedgerError("evaluation window is already consumed")
            for registration in self._registrations_for_window(
                records, validated.window_id
            ):
                self._validate_window_definition(
                    registration.payload.evaluation_window,
                    validated,
                )
                if consumed_at < registration.recorded_at:
                    raise TrialLedgerError(
                        "window consumption cannot precede trial registration"
                    )
            try:
                consumed = EvaluationWindow.model_validate(
                    {
                        **validated.model_dump(),
                        "consumed_at": consumed_at,
                        "consumption_reason": reason,
                    },
                    strict=True,
                )
            except ValidationError as error:
                raise TrialLedgerError(
                    "evaluation window consumption is invalid"
                ) from error
            record = WindowConsumedRecord(
                record_type=LedgerRecordType.WINDOW_CONSUMED,
                recorded_at=consumed_at,
                record_id=consumed.window_id,
                payload=consumed,
            )
            self._append_unlocked(record)
            return consumed

    def _read_unlocked(self) -> tuple[LedgerRecord, ...]:
        if not self.path.exists():
            return ()
        records: list[LedgerRecord] = []
        try:
            with self.path.open("rb") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.endswith(b"\n"):
                        raise TrialLedgerError(
                            f"ledger has a corrupt partial record at line {line_number}"
                        )
                    records.append(_RECORD_ADAPTER.validate_json(line, strict=True))
        except (OSError, ValidationError, ValueError) as error:
            raise TrialLedgerError("ledger contains a corrupt record") from error
        try:
            self._replay(tuple(records))
        except TrialLedgerError as error:
            raise TrialLedgerError("ledger contains a corrupt state history") from error
        return tuple(records)

    def _append_unlocked(self, record: LedgerRecord) -> None:
        payload = _RECORD_ADAPTER.dump_json(record) + b"\n"
        with self.path.open("ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def _replay(self, records: tuple[LedgerRecord, ...]) -> None:
        registrations: dict[str, TrialRegisteredRecord] = {}
        finishes: dict[str, TrialFinishedRecord] = {}
        consumptions: dict[str, EvaluationWindow] = {}
        canonical_windows: dict[str, EvaluationWindow] = {}
        for record in records:
            if isinstance(record, TrialRegisteredRecord):
                trial = record.payload
                if trial.trial_id in registrations:
                    raise TrialLedgerError("duplicate trial registration")
                if trial.evaluation_window_id in consumptions:
                    raise TrialLedgerError(
                        "trial references a consumed evaluation window"
                    )
                canonical = canonical_windows.get(trial.evaluation_window_id)
                if canonical is not None:
                    self._validate_window_definition(
                        canonical,
                        trial.evaluation_window,
                    )
                if trial.parent_trial_id is not None:
                    parent = finishes.get(trial.parent_trial_id)
                    if parent is None:
                        raise TrialLedgerError("parent trial is not finished")
                    if trial.registered_at < parent.recorded_at:
                        raise TrialLedgerError(
                            "derived trial predates parent result"
                        )
                registrations[trial.trial_id] = record
                canonical_windows.setdefault(
                    trial.evaluation_window_id,
                    trial.evaluation_window,
                )
            elif isinstance(record, WindowConsumedRecord):
                if record.record_id in consumptions:
                    raise TrialLedgerError("duplicate window consumption")
                if record.payload.role is EvaluationRole.LOCKBOX:
                    raise TrialLedgerError(
                        "lockbox consumption is not atomic with trial finish"
                    )
                canonical = canonical_windows.get(record.record_id)
                if canonical is not None:
                    self._validate_window_definition(canonical, record.payload)
                for registration in registrations.values():
                    if registration.payload.evaluation_window_id != record.record_id:
                        continue
                    if record.recorded_at < registration.recorded_at:
                        raise TrialLedgerError(
                            "window consumption predates trial registration"
                        )
                canonical_windows.setdefault(record.record_id, record.payload)
                consumptions[record.record_id] = record.payload
            else:
                finished = record.payload
                registered = registrations.get(finished.trial_id)
                if registered is None:
                    raise TrialLedgerError("trial finish precedes registration")
                if finished.trial_id in finishes:
                    raise TrialLedgerError("duplicate trial finish")
                if (
                    finished.evaluation_window_id
                    != registered.payload.evaluation_window_id
                ):
                    raise TrialLedgerError("trial finish window does not match registration")
                if finished.finished_at < registered.recorded_at:
                    raise TrialLedgerError("trial finish predates registration")
                existing_consumption = consumptions.get(
                    finished.evaluation_window_id
                )
                if finished.consumed_at is not None:
                    for bound_trial in registrations.values():
                        if (
                            bound_trial.payload.evaluation_window_id
                            != finished.evaluation_window_id
                        ):
                            continue
                        if finished.consumed_at < bound_trial.recorded_at:
                            raise TrialLedgerError(
                                "lockbox consumption predates trial registration"
                            )
                self._validate_finished_trial(
                    registered.payload,
                    finished,
                    existing_consumption,
                )
                if finished.consumed_window is not None:
                    consumptions[finished.evaluation_window_id] = (
                        finished.consumed_window
                    )
                finishes[finished.trial_id] = record

    @staticmethod
    def _validate_window_identity(
        trial: BacktestTrial,
        window: EvaluationWindow,
    ) -> None:
        registered_window = trial.evaluation_window
        JsonlTrialLedger._validate_window_definition(registered_window, window)
        if trial.evaluation_window_id != registered_window.window_id:
            raise TrialLedgerError("trial and evaluation window IDs do not match")
        if trial.split_version != registered_window.split_version:
            raise TrialLedgerError("trial and evaluation window split versions differ")
        if trial.dataset_input_hash != registered_window.dataset_input_hash:
            raise TrialLedgerError("trial and evaluation window dataset hashes differ")
        if trial.calendar_hash != registered_window.calendar_hash:
            raise TrialLedgerError("trial and evaluation window calendar hashes differ")

    @staticmethod
    def _validate_window_definition(
        expected: EvaluationWindow,
        actual: EvaluationWindow,
    ) -> None:
        if expected.window_id != actual.window_id:
            raise TrialLedgerError("evaluation window IDs differ")
        if expected.role is not actual.role:
            raise TrialLedgerError("evaluation window roles differ")
        if expected.scope is not actual.scope:
            raise TrialLedgerError("evaluation window scopes differ")
        if (
            expected.start_session != actual.start_session
            or expected.end_session != actual.end_session
        ):
            raise TrialLedgerError("evaluation window dates differ")
        if expected.split_version != actual.split_version:
            raise TrialLedgerError("evaluation window split versions differ")
        if expected.dataset_input_hash != actual.dataset_input_hash:
            raise TrialLedgerError("evaluation window dataset hashes differ")
        if expected.calendar_hash != actual.calendar_hash:
            raise TrialLedgerError("evaluation window calendar hashes differ")

    @classmethod
    def _validate_finished_trial(
        cls,
        trial: BacktestTrial,
        finished: TrialFinished,
        existing_consumption: EvaluationWindow | None,
    ) -> None:
        cls._validate_result_metrics(trial, finished.status, finished.result)
        if (
            trial.evaluation_window.role is EvaluationRole.LOCKBOX
            and finished.result is not None
            and finished.consumed_window is None
        ):
            raise TrialLedgerError(
                "lockbox trial result is missing atomic access evidence"
            )
        if finished.consumed_window is not None:
            cls._validate_window_identity(trial, finished.consumed_window)
            if existing_consumption is not None:
                raise TrialLedgerError("evaluation window is already consumed")
            if finished.consumed_at is not None and (
                finished.consumed_at > finished.finished_at
            ):
                raise TrialLedgerError(
                    "lockbox consumption cannot occur after trial finish"
                )
            if (
                finished.consumed_at is not None
                and finished.consumed_at < trial.registered_at
            ):
                raise TrialLedgerError(
                    "lockbox consumption cannot precede trial registration"
                )
        elif (
            existing_consumption is not None
            and existing_consumption.role is EvaluationRole.LOCKBOX
            and finished.result is not None
        ):
            raise TrialLedgerError(
                "lockbox trial result is missing atomic access evidence"
            )

    @staticmethod
    def _validate_result_metrics(
        trial: BacktestTrial,
        status: TrialStatus,
        result: Mapping[str, float] | None,
    ) -> None:
        result_names = set(result or {})
        declared = set(trial.primary_metrics) | set(trial.secondary_metrics)
        unregistered = result_names - declared
        if unregistered:
            raise TrialLedgerError("trial result contains an unregistered metric")
        if status is TrialStatus.SUCCEEDED and not set(
            trial.primary_metrics
        ).issubset(result_names):
            raise TrialLedgerError("trial result is missing a primary metric")
        if status is TrialStatus.SUCCEEDED and result_names != declared:
            raise TrialLedgerError("trial result is missing a declared metric")

    @staticmethod
    def _trial_registration(
        records: tuple[LedgerRecord, ...], trial_id: str
    ) -> TrialRegisteredRecord | None:
        return next(
            (
                record
                for record in records
                if isinstance(record, TrialRegisteredRecord)
                and record.record_id == trial_id
            ),
            None,
        )

    @staticmethod
    def _trial_finish(
        records: tuple[LedgerRecord, ...], trial_id: str
    ) -> TrialFinishedRecord | None:
        return next(
            (
                record
                for record in records
                if isinstance(record, TrialFinishedRecord)
                and record.record_id == trial_id
            ),
            None,
        )

    @staticmethod
    def _registrations_for_window(
        records: tuple[LedgerRecord, ...],
        window_id: str,
    ) -> tuple[TrialRegisteredRecord, ...]:
        return tuple(
            record
            for record in records
            if isinstance(record, TrialRegisteredRecord)
            and record.payload.evaluation_window_id == window_id
        )

    @staticmethod
    def _window_consumption(
        records: tuple[LedgerRecord, ...], window_id: str
    ) -> EvaluationWindow | None:
        return next(
            (
                record.payload
                for record in records
                if isinstance(record, WindowConsumedRecord)
                and record.record_id == window_id
            ),
            next(
                (
                    record.payload.consumed_window
                    for record in records
                    if isinstance(record, TrialFinishedRecord)
                    and record.payload.consumed_window is not None
                    and record.payload.consumed_window.window_id == window_id
                ),
                None,
            ),
        )
