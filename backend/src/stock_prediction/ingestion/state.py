"""Durable ingestion state machine and event ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

import duckdb
from pydantic import (
    BaseModel,
    ConfigDict,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from stock_prediction.contracts.ingestion import (
    DataQualityReport,
    IngestionStatus,
    IngestionTask,
)
from stock_prediction.contracts.market_data import Identifier
from stock_prediction.contracts.time import UtcDatetime


class IngestionStateError(RuntimeError):
    """Base class for sanitized state-store failures."""


class IngestionStateConflict(IngestionStateError):
    """Persisted task evidence conflicts with a requested operation."""


class InvalidIngestionTransition(IngestionStateError):
    """The requested state edge is not in the versioned state graph."""


@dataclass(frozen=True, slots=True)
class IngestionEvent:
    event_id: int
    task_id: str
    from_status: IngestionStatus
    to_status: IngestionStatus
    state_version: int
    occurred_at: datetime


class CommitPhase(StrEnum):
    PREPARED = "prepared"
    RAW_COMMITTED = "raw_committed"
    CATALOG_REGISTERED = "catalog_registered"
    QUARANTINED = "quarantined"
    DONE = "done"


class CommitJournalEntry(BaseModel):
    """Recovery evidence for one cross-storage batch publication."""

    model_config = ConfigDict(strict=True, frozen=True)

    task_id: Identifier
    batch_id: Identifier
    phase: CommitPhase
    quality_report: DataQualityReport
    committed_watermark: UtcDatetime
    next_page_token: str | None = None
    committed_at: UtcDatetime
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @model_validator(mode="after")
    def validate_commit_evidence(self) -> "CommitJournalEntry":
        if self.quality_report.batch_id != self.batch_id:
            raise ValueError("quality report does not match commit batch")
        if self.committed_watermark > self.committed_at:
            raise ValueError("committed watermark must not be after commit time")
        if self.updated_at < self.created_at:
            raise ValueError("journal update must not be before creation")
        return self


_LEGAL_TRANSITIONS: dict[IngestionStatus, frozenset[IngestionStatus]] = {
    IngestionStatus.PLANNED: frozenset({IngestionStatus.CAPABILITY_CHECK}),
    IngestionStatus.CAPABILITY_CHECK: frozenset(
        {IngestionStatus.BLOCKED, IngestionStatus.FETCHING}
    ),
    IngestionStatus.FETCHING: frozenset(
        {
            IngestionStatus.RETRYING,
            IngestionStatus.FAILED,
            IngestionStatus.STAGED,
        }
    ),
    IngestionStatus.RETRYING: frozenset(
        {IngestionStatus.FETCHING, IngestionStatus.FAILED}
    ),
    IngestionStatus.STAGED: frozenset(
        {
            IngestionStatus.QUARANTINED,
            IngestionStatus.COMMITTED,
            IngestionStatus.FETCHING,
        }
    ),
    IngestionStatus.BLOCKED: frozenset({IngestionStatus.PLANNED}),
    IngestionStatus.FAILED: frozenset({IngestionStatus.PLANNED}),
    IngestionStatus.QUARANTINED: frozenset({IngestionStatus.PLANNED}),
    IngestionStatus.COMMITTED: frozenset(),
}


class IngestionStateStore:
    """Persist tasks as versioned Pydantic JSON plus an append-only event ledger."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                SET TimeZone = 'UTC';
                CREATE TABLE IF NOT EXISTS ingestion_tasks (
                    task_id VARCHAR PRIMARY KEY,
                    status VARCHAR NOT NULL,
                    priority VARCHAR NOT NULL,
                    state_version BIGINT NOT NULL,
                    next_retry_at TIMESTAMPTZ,
                    task_json JSON NOT NULL
                );
                CREATE SEQUENCE IF NOT EXISTS ingestion_event_id START 1;
                CREATE TABLE IF NOT EXISTS ingestion_events (
                    event_id BIGINT PRIMARY KEY DEFAULT nextval('ingestion_event_id'),
                    task_id VARCHAR NOT NULL,
                    from_status VARCHAR NOT NULL,
                    to_status VARCHAR NOT NULL,
                    state_version BIGINT NOT NULL,
                    occurred_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE IF NOT EXISTS commit_journal (
                    batch_id VARCHAR PRIMARY KEY,
                    task_id VARCHAR NOT NULL,
                    phase VARCHAR NOT NULL,
                    journal_json JSON NOT NULL
                );
                """
            )

    def create(self, task: IngestionTask) -> bool:
        task_json = task.model_dump_json()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT task_json::VARCHAR FROM ingestion_tasks WHERE task_id = ?",
                [task.task_id],
            ).fetchone()
            if existing is not None:
                if existing[0] != task_json:
                    raise IngestionStateConflict(
                        "task ID already owns different state evidence"
                    )
                return False
            connection.execute(
                """
                INSERT INTO ingestion_tasks
                VALUES (?, ?, ?, ?, ?, ?::JSON)
                """,
                [
                    task.task_id,
                    task.status.value,
                    task.priority.value,
                    task.state_version,
                    task.next_retry_at,
                    task_json,
                ],
            )
        return True

    def get(self, task_id: str) -> IngestionTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT task_json::VARCHAR FROM ingestion_tasks WHERE task_id = ?",
                [task_id],
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return IngestionTask.model_validate_json(row[0])

    def list_tasks(self) -> list[IngestionTask]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT task_json::VARCHAR FROM ingestion_tasks
                ORDER BY
                    CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                    task_id
                """
            ).fetchall()
        return [IngestionTask.model_validate_json(row[0]) for row in rows]

    def transition(
        self,
        task_id: str,
        to_status: IngestionStatus,
        *,
        updated_at: UtcDatetime | str,
        error_class: str | None = None,
        next_retry_at: UtcDatetime | str | None = None,
        staged_batch_id: str | None = None,
        committed_batch_id: str | None = None,
        committed_watermark: UtcDatetime | str | None = None,
        resume_page_token: str | None = None,
    ) -> IngestionTask:
        updated = TypeAdapter(UtcDatetime).validate_python(updated_at)
        with self._connect() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                current = self._get_on(connection, task_id)
                if to_status not in _LEGAL_TRANSITIONS[current.status]:
                    raise InvalidIngestionTransition(
                        f"{current.status.value} cannot transition to {to_status.value}"
                    )
                attempt = current.attempt
                if (
                    to_status is IngestionStatus.FETCHING
                    and current.status is not IngestionStatus.STAGED
                ):
                    attempt += 1
                retry_at = (
                    TypeAdapter(UtcDatetime).validate_python(next_retry_at)
                    if next_retry_at is not None
                    else None
                )
                watermark = (
                    TypeAdapter(UtcDatetime).validate_python(committed_watermark)
                    if committed_watermark is not None
                    else current.committed_watermark
                )
                if (
                    current.committed_watermark is not None
                    and watermark is not None
                    and watermark < current.committed_watermark
                ):
                    raise IngestionStateConflict(
                        "committed watermark must be monotonic"
                    )
                latest_batch = committed_batch_id or current.committed_batch_id
                next_token = current.resume_page_token
                if current.status is IngestionStatus.STAGED and to_status in {
                    IngestionStatus.FETCHING,
                    IngestionStatus.COMMITTED,
                }:
                    next_token = (
                        resume_page_token
                        if to_status is IngestionStatus.FETCHING
                        else None
                    )
                values = current.model_dump()
                values.update(
                    {
                        "attempt": attempt,
                        "state_version": current.state_version + 1,
                        "status": to_status,
                        "error_class": error_class
                        if to_status
                        in {
                            IngestionStatus.RETRYING,
                            IngestionStatus.BLOCKED,
                            IngestionStatus.FAILED,
                            IngestionStatus.QUARANTINED,
                        }
                        else None,
                        "next_retry_at": retry_at
                        if to_status is IngestionStatus.RETRYING
                        else None,
                        "staged_batch_id": staged_batch_id
                        if to_status is IngestionStatus.STAGED
                        else None,
                        "committed_batch_id": latest_batch,
                        "committed_watermark": watermark,
                        "resume_page_token": next_token,
                        "updated_at": updated,
                    }
                )
                try:
                    changed = IngestionTask(**values)
                except ValidationError as error:
                    raise IngestionStateConflict(
                        "transition evidence is incomplete"
                    ) from error
                updated_row = connection.execute(
                    """
                    UPDATE ingestion_tasks
                    SET status = ?, priority = ?, state_version = ?,
                        next_retry_at = ?, task_json = ?::JSON
                    WHERE task_id = ? AND state_version = ?
                    RETURNING task_id
                    """,
                    [
                        changed.status.value,
                        changed.priority.value,
                        changed.state_version,
                        changed.next_retry_at,
                        changed.model_dump_json(),
                        task_id,
                        current.state_version,
                    ],
                ).fetchone()
                if updated_row is None:
                    raise IngestionStateConflict(
                        "task state changed during transition"
                    )
                connection.execute(
                    """
                    INSERT INTO ingestion_events (
                        task_id, from_status, to_status, state_version, occurred_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        task_id,
                        current.status.value,
                        changed.status.value,
                        changed.state_version,
                        updated,
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return changed

    def claim(
        self,
        task_id: str,
        *,
        owner: str,
        now: UtcDatetime | str,
        ttl: timedelta,
    ) -> IngestionTask:
        current_time = TypeAdapter(UtcDatetime).validate_python(now)
        if ttl <= timedelta(0):
            raise ValueError("lease TTL must be positive")
        with self._connect() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                current = self._get_on(connection, task_id)
                if (
                    current.lease_owner is not None
                    and current.lease_owner != owner
                    and current.lease_expires_at is not None
                    and current.lease_expires_at > current_time
                ):
                    raise IngestionStateConflict("task is already leased")
                values = current.model_dump()
                values.update(
                    {
                        "lease_owner": owner,
                        "lease_expires_at": current_time + ttl,
                        "state_version": current.state_version + 1,
                        "updated_at": current_time,
                    }
                )
                claimed = IngestionTask(**values)
                self._replace_on(connection, current, claimed)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return claimed

    def events(self, task_id: str) -> list[IngestionEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, task_id, from_status, to_status,
                       state_version, occurred_at
                FROM ingestion_events WHERE task_id = ? ORDER BY event_id
                """,
                [task_id],
            ).fetchall()
        return [
            IngestionEvent(
                event_id=int(row[0]),
                task_id=str(row[1]),
                from_status=IngestionStatus(row[2]),
                to_status=IngestionStatus(row[3]),
                state_version=int(row[4]),
                occurred_at=row[5],
            )
            for row in rows
        ]

    def prepare_commit(
        self,
        *,
        task_id: str,
        batch_id: str,
        quality_report: DataQualityReport,
        committed_watermark: UtcDatetime | str,
        next_page_token: str | None,
        committed_at: UtcDatetime | str,
        created_at: UtcDatetime | str,
    ) -> CommitJournalEntry:
        timestamp = TypeAdapter(UtcDatetime).validate_python(created_at)
        entry = CommitJournalEntry(
            task_id=task_id,
            batch_id=batch_id,
            phase=CommitPhase.PREPARED,
            quality_report=quality_report,
            committed_watermark=TypeAdapter(UtcDatetime).validate_python(
                committed_watermark
            ),
            next_page_token=next_page_token,
            committed_at=TypeAdapter(UtcDatetime).validate_python(committed_at),
            created_at=timestamp,
            updated_at=timestamp,
        )
        payload = entry.model_dump_json()
        with self._connect() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                existing = connection.execute(
                    "SELECT journal_json::VARCHAR FROM commit_journal WHERE batch_id = ?",
                    [batch_id],
                ).fetchone()
                if existing is not None:
                    persisted = CommitJournalEntry.model_validate_json(existing[0])
                    if _journal_evidence(entry) != _journal_evidence(persisted):
                        raise IngestionStateConflict(
                            "batch ID already owns different commit journal evidence"
                        )
                    connection.execute("ROLLBACK")
                    return persisted
                current = self._get_on(connection, task_id)
                if (
                    current.status is not IngestionStatus.STAGED
                    or current.staged_batch_id != batch_id
                ):
                    raise IngestionStateConflict(
                        "commit journal requires the matching staged task"
                    )
                if (
                    current.committed_watermark is not None
                    and entry.committed_watermark < current.committed_watermark
                ):
                    raise IngestionStateConflict(
                        "commit journal watermark would move task backwards"
                    )
                connection.execute(
                    "INSERT INTO commit_journal VALUES (?, ?, ?, ?::JSON)",
                    [batch_id, task_id, entry.phase.value, payload],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return entry

    def advance_commit(
        self,
        batch_id: str,
        *,
        expected_phase: CommitPhase,
        to_phase: CommitPhase,
        updated_at: UtcDatetime | str,
    ) -> CommitJournalEntry:
        legal = {
            CommitPhase.PREPARED: {
                CommitPhase.RAW_COMMITTED,
                CommitPhase.QUARANTINED,
            },
            CommitPhase.RAW_COMMITTED: {CommitPhase.CATALOG_REGISTERED},
            CommitPhase.CATALOG_REGISTERED: {CommitPhase.DONE},
            CommitPhase.QUARANTINED: {CommitPhase.DONE},
        }
        if to_phase not in legal.get(expected_phase, set()):
            raise InvalidIngestionTransition(
                f"{expected_phase.value} cannot advance to {to_phase.value}"
            )
        with self._connect() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                row = connection.execute(
                    "SELECT journal_json::VARCHAR FROM commit_journal WHERE batch_id = ?",
                    [batch_id],
                ).fetchone()
                if row is None:
                    raise KeyError(batch_id)
                current = CommitJournalEntry.model_validate_json(row[0])
                if current.phase is not expected_phase:
                    raise IngestionStateConflict("commit journal phase changed")
                changed = CommitJournalEntry(
                    **{
                        **current.model_dump(),
                        "phase": to_phase,
                        "updated_at": TypeAdapter(UtcDatetime).validate_python(
                            updated_at
                        ),
                    }
                )
                updated_row = connection.execute(
                    """
                    UPDATE commit_journal SET phase = ?, journal_json = ?::JSON
                    WHERE batch_id = ? AND phase = ?
                    RETURNING batch_id
                    """,
                    [
                        changed.phase.value,
                        changed.model_dump_json(),
                        batch_id,
                        expected_phase.value,
                    ],
                ).fetchone()
                if updated_row is None:
                    raise IngestionStateConflict(
                        "commit journal changed during phase advance"
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return changed

    def pending_commits(self) -> list[CommitJournalEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT journal_json::VARCHAR FROM commit_journal
                WHERE phase <> ? ORDER BY task_id, batch_id
                """,
                [CommitPhase.DONE.value],
            ).fetchall()
        return [CommitJournalEntry.model_validate_json(row[0]) for row in rows]

    def protected_batch_ids(self) -> frozenset[str]:
        return frozenset(entry.batch_id for entry in self.pending_commits())

    def commit_entry(self, batch_id: str) -> CommitJournalEntry:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT journal_json::VARCHAR FROM commit_journal WHERE batch_id = ?",
                [batch_id],
            ).fetchone()
        if row is None:
            raise KeyError(batch_id)
        return CommitJournalEntry.model_validate_json(row[0])

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.path))

    @staticmethod
    def _get_on(
        connection: duckdb.DuckDBPyConnection, task_id: str
    ) -> IngestionTask:
        row = connection.execute(
            "SELECT task_json::VARCHAR FROM ingestion_tasks WHERE task_id = ?",
            [task_id],
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return IngestionTask.model_validate_json(row[0])

    @staticmethod
    def _replace_on(
        connection: duckdb.DuckDBPyConnection,
        current: IngestionTask,
        changed: IngestionTask,
    ) -> None:
        updated_row = connection.execute(
            """
            UPDATE ingestion_tasks
            SET status = ?, priority = ?, state_version = ?,
                next_retry_at = ?, task_json = ?::JSON
            WHERE task_id = ? AND state_version = ?
            RETURNING task_id
            """,
            [
                changed.status.value,
                changed.priority.value,
                changed.state_version,
                changed.next_retry_at,
                changed.model_dump_json(),
                current.task_id,
                current.state_version,
            ],
        ).fetchone()
        if updated_row is None:
            raise IngestionStateConflict("task state changed during lease update")


def _journal_evidence(entry: CommitJournalEntry) -> dict[str, object]:
    return entry.model_dump(exclude={"phase", "created_at", "updated_at"})
