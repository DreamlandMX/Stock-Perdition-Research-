"""Recoverable publication of staged batches across raw storage and DuckDB."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from stock_prediction.contracts.ingestion import (
    DataQualityReport,
    IngestionStatus,
    IngestionTask,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.data.catalog import DuckDbCatalog
from stock_prediction.data.raw_store import RawBatchStore, StagedRawBatch
from stock_prediction.ingestion.state import (
    CommitJournalEntry,
    CommitPhase,
    IngestionStateConflict,
    IngestionStateStore,
)


class IngestionCommitService:
    """Use a write-ahead journal to make cross-storage publication recoverable."""

    def __init__(
        self,
        state: IngestionStateStore,
        raw_store: RawBatchStore,
        catalog: DuckDbCatalog,
        *,
        phase_hook: Callable[[str], None] = lambda _phase: None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.state = state
        self.raw_store = raw_store
        self.catalog = catalog
        self.phase_hook = phase_hook
        self.clock = clock

    def commit_staged(
        self,
        *,
        task_id: str,
        staged: StagedRawBatch,
        quality_report: DataQualityReport,
        next_page_token: str | None,
        committed_at: UtcDatetime | str,
    ) -> IngestionTask:
        if staged.manifest.task_id != task_id:
            raise IngestionStateConflict(
                "staged manifest does not belong to the requested task"
            )
        if (
            quality_report.batch_id != staged.manifest.batch_id
            or quality_report.input_rows != staged.manifest.row_count
        ):
            raise IngestionStateConflict(
                "quality evidence does not match the staged batch"
            )
        with self.raw_store.batch_lock(staged.manifest.batch_id):
            if not staged.already_committed and not staged.staging_path.is_dir():
                raise IngestionStateConflict("staged evidence disappeared before journal")
            current_task = self.state.get(task_id)
            if (
                current_task.committed_watermark is not None
                and staged.manifest.max_event_at < current_task.committed_watermark
            ):
                raise IngestionStateConflict(
                    "staged watermark would move the task backwards"
                )
            journal = self.state.prepare_commit(
                task_id=task_id,
                batch_id=staged.manifest.batch_id,
                quality_report=quality_report,
                committed_watermark=staged.manifest.max_event_at,
                next_page_token=next_page_token,
                committed_at=committed_at,
                created_at=self.clock(),
            )
        self.phase_hook("prepared")
        return self._resume(journal)

    def recover_pending(self) -> tuple[IngestionTask, ...]:
        return tuple(self._resume(entry) for entry in self.state.pending_commits())

    def _resume(self, entry: CommitJournalEntry) -> IngestionTask:
        current = entry
        if current.phase is CommitPhase.PREPARED and current.quality_report.quarantined:
            quarantined = self.raw_store.find_quarantined(current.batch_id)
            if quarantined is None:
                staged = self.raw_store.staged_batch(current.batch_id)
                self.raw_store.quarantine(
                    staged, quality_report=current.quality_report
                )
            elif self.raw_store.quality_report(
                quarantined
            ) != current.quality_report:
                raise IngestionStateConflict(
                    "quarantined quality evidence conflicts with the journal"
                )
            self.phase_hook("quarantine_published")
            current = self.state.advance_commit(
                current.batch_id,
                expected_phase=CommitPhase.PREPARED,
                to_phase=CommitPhase.QUARANTINED,
                updated_at=self.clock(),
            )
            self.phase_hook("quarantined")
        if current.phase is CommitPhase.QUARANTINED:
            task = self.state.get(current.task_id)
            if task.status is IngestionStatus.STAGED:
                task = self.state.transition(
                    current.task_id,
                    IngestionStatus.QUARANTINED,
                    updated_at=self.clock(),
                    error_class="quality_failed",
                )
            elif task.status is not IngestionStatus.QUARANTINED:
                raise IngestionStateConflict(
                    "task state conflicts with quarantined evidence"
                )
            self.phase_hook("quarantine_task_published")
            self.state.advance_commit(
                current.batch_id,
                expected_phase=CommitPhase.QUARANTINED,
                to_phase=CommitPhase.DONE,
                updated_at=self.clock(),
            )
            self.phase_hook("done")
            return task
        if current.phase is CommitPhase.PREPARED:
            manifest = self.raw_store.find_committed(current.batch_id)
            if manifest is None:
                staged = self.raw_store.staged_batch(current.batch_id)
                manifest = self.raw_store.commit(
                    staged,
                    quality_report=current.quality_report,
                    committed_at=current.committed_at,
                )
            elif self.raw_store.quality_report(manifest) != current.quality_report:
                raise IngestionStateConflict(
                    "committed quality evidence conflicts with the journal"
                )
            self.phase_hook("raw_published")
            current = self.state.advance_commit(
                current.batch_id,
                expected_phase=CommitPhase.PREPARED,
                to_phase=CommitPhase.RAW_COMMITTED,
                updated_at=self.clock(),
            )
            self.phase_hook("raw_committed")
        if current.phase is CommitPhase.RAW_COMMITTED:
            manifest = self.raw_store.find_committed(current.batch_id)
            if manifest is None:
                raise IngestionStateConflict(
                    "journal says raw committed but evidence is missing"
                )
            self.catalog.register_committed_batch(self.raw_store, manifest)
            self.phase_hook("catalog_published")
            current = self.state.advance_commit(
                current.batch_id,
                expected_phase=CommitPhase.RAW_COMMITTED,
                to_phase=CommitPhase.CATALOG_REGISTERED,
                updated_at=self.clock(),
            )
            self.phase_hook("catalog_registered")
        if current.phase is CommitPhase.CATALOG_REGISTERED:
            task = self.state.get(current.task_id)
            target = (
                IngestionStatus.COMMITTED
                if current.next_page_token is None
                else IngestionStatus.FETCHING
            )
            if task.status is IngestionStatus.STAGED:
                task = self.state.transition(
                    current.task_id,
                    target,
                    updated_at=self.clock(),
                    committed_batch_id=current.batch_id,
                    committed_watermark=current.committed_watermark,
                    resume_page_token=current.next_page_token,
                )
            elif not (
                task.status is target
                and task.committed_batch_id == current.batch_id
                and task.committed_watermark == current.committed_watermark
                and task.resume_page_token == current.next_page_token
            ):
                raise IngestionStateConflict(
                    "task state conflicts with catalog-registered commit"
                )
            self.phase_hook("task_published")
            current = self.state.advance_commit(
                current.batch_id,
                expected_phase=CommitPhase.CATALOG_REGISTERED,
                to_phase=CommitPhase.DONE,
                updated_at=self.clock(),
            )
            self.phase_hook("done")
            return task
        if current.phase is CommitPhase.DONE:
            return self.state.get(current.task_id)
        raise IngestionStateConflict("unsupported commit journal phase")
