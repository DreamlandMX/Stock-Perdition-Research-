from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from stock_prediction.contracts.ingestion import (
    DataQualityReport,
    IngestionStatus,
    QualityIssue,
    QualitySeverity,
)
from stock_prediction.data.catalog import DuckDbCatalog
from stock_prediction.data.raw_store import RawBatchStore
from stock_prediction.ingestion.service import IngestionCommitService
from stock_prediction.ingestion.state import (
    CommitPhase,
    IngestionStateConflict,
    IngestionStateStore,
)

from data.test_raw_store import bar, clean_report, stage
from .test_state import NOW, task


WATERMARK = datetime(2026, 7, 10, 13, 30, tzinfo=UTC)


def setup_staged(
    tmp_path: Path,
    *,
    batch_id: str = "batch-spy-20260710",
    previous_watermark: datetime | None = None,
):
    raw_store = RawBatchStore(tmp_path / "raw")
    staged = stage(raw_store, [bar(batch_id=batch_id)])
    state = IngestionStateStore(tmp_path / "state.duckdb")
    initial = task().model_copy(
        update={
            "task_id": staged.manifest.task_id,
            "committed_batch_id": "previous-batch"
            if previous_watermark is not None
            else None,
            "committed_watermark": previous_watermark,
        }
    )
    state.create(initial)
    state.transition(initial.task_id, IngestionStatus.CAPABILITY_CHECK, updated_at=NOW)
    state.transition(initial.task_id, IngestionStatus.FETCHING, updated_at=NOW)
    state.transition(
        initial.task_id,
        IngestionStatus.STAGED,
        updated_at=NOW,
        staged_batch_id=batch_id,
    )
    catalog = DuckDbCatalog(tmp_path / "catalog.duckdb")
    return raw_store, staged, state, catalog, initial.task_id


def test_commit_service_publishes_all_layers_then_advances_watermark(
    tmp_path: Path,
) -> None:
    raw_store, staged, state, catalog, task_id = setup_staged(tmp_path)
    service = IngestionCommitService(state, raw_store, catalog)

    committed_task = service.commit_staged(
        task_id=task_id,
        staged=staged,
        quality_report=clean_report(staged.manifest.batch_id),
        next_page_token=None,
        committed_at="2026-07-10T20:01:01Z",
    )

    assert committed_task.status is IngestionStatus.COMMITTED
    assert committed_task.committed_watermark == WATERMARK
    assert raw_store.find_committed(staged.manifest.batch_id) is not None
    assert catalog.registered_batch_count() == 1
    assert state.pending_commits() == []


@pytest.mark.parametrize("failure_phase", ["prepared", "raw_published", "catalog_published"])
def test_commit_journal_recovers_every_cross_storage_crash_boundary(
    tmp_path: Path, failure_phase: str
) -> None:
    raw_store, staged, state, catalog, task_id = setup_staged(tmp_path)

    def fail(phase: str) -> None:
        if phase == failure_phase:
            raise RuntimeError("simulated process crash")

    service = IngestionCommitService(state, raw_store, catalog, phase_hook=fail)
    with pytest.raises(RuntimeError, match="simulated process crash"):
        service.commit_staged(
            task_id=task_id,
            staged=staged,
            quality_report=clean_report(staged.manifest.batch_id),
            next_page_token=None,
            committed_at="2026-07-10T20:01:01Z",
        )

    assert state.get(task_id).committed_watermark is None
    recovered = IngestionCommitService(state, raw_store, catalog).recover_pending()

    assert [item.status for item in recovered] == [IngestionStatus.COMMITTED]
    assert state.get(task_id).committed_watermark == WATERMARK
    assert raw_store.find_committed(staged.manifest.batch_id) is not None
    assert catalog.registered_batch_count() == 1
    assert state.pending_commits() == []
    assert IngestionCommitService(state, raw_store, catalog).recover_pending() == ()


def test_intermediate_page_commits_watermark_and_resumes_from_next_token(
    tmp_path: Path,
) -> None:
    raw_store, staged, state, catalog, task_id = setup_staged(tmp_path)

    resumed = IngestionCommitService(state, raw_store, catalog).commit_staged(
        task_id=task_id,
        staged=staged,
        quality_report=clean_report(staged.manifest.batch_id),
        next_page_token="safe-next-token",
        committed_at="2026-07-10T20:01:01Z",
    )

    assert resumed.status is IngestionStatus.FETCHING
    assert resumed.resume_page_token == "safe-next-token"
    assert resumed.committed_watermark == WATERMARK


def test_quality_failure_is_quarantined_without_catalog_or_watermark(
    tmp_path: Path,
) -> None:
    raw_store, staged, state, catalog, task_id = setup_staged(tmp_path)
    failed_report = DataQualityReport(
        report_id=f"quality-{staged.manifest.batch_id}",
        batch_id=staged.manifest.batch_id,
        checked_at="2026-07-10T20:01:00Z",
        input_rows=1,
        valid_rows=0,
        quarantined_rows=1,
        quality_score=0.0,
        quarantined=True,
        issues=[
            QualityIssue(
                code="invalid_ohlc",
                severity=QualitySeverity.ERROR,
                message="OHLC relations are inconsistent",
                affected_rows=1,
            )
        ],
    )

    quarantined = IngestionCommitService(state, raw_store, catalog).commit_staged(
        task_id=task_id,
        staged=staged,
        quality_report=failed_report,
        next_page_token=None,
        committed_at="2026-07-10T20:01:01Z",
    )

    assert quarantined.status is IngestionStatus.QUARANTINED
    assert quarantined.committed_watermark is None
    assert raw_store.find_committed(staged.manifest.batch_id) is None
    assert catalog.registered_batch_count() == 0
    assert state.pending_commits() == []


def test_quarantine_journal_recovers_after_raw_move(tmp_path: Path) -> None:
    raw_store, staged, state, catalog, task_id = setup_staged(tmp_path)
    failed_report = DataQualityReport(
        report_id=f"quality-{staged.manifest.batch_id}",
        batch_id=staged.manifest.batch_id,
        checked_at="2026-07-10T20:01:00Z",
        input_rows=1,
        valid_rows=0,
        quarantined_rows=1,
        quality_score=0.0,
        quarantined=True,
        issues=[
            QualityIssue(
                code="invalid_ohlc",
                severity=QualitySeverity.ERROR,
                message="OHLC relations are inconsistent",
                affected_rows=1,
            )
        ],
    )

    def fail(phase: str) -> None:
        if phase == "quarantine_published":
            raise RuntimeError("simulated process crash")

    with pytest.raises(RuntimeError, match="simulated process crash"):
        IngestionCommitService(
            state, raw_store, catalog, phase_hook=fail
        ).commit_staged(
            task_id=task_id,
            staged=staged,
            quality_report=failed_report,
            next_page_token=None,
            committed_at="2026-07-10T20:01:01Z",
        )

    assert state.get(task_id).status is IngestionStatus.STAGED
    recovered = IngestionCommitService(state, raw_store, catalog).recover_pending()
    assert [item.status for item in recovered] == [IngestionStatus.QUARANTINED]
    assert state.get(task_id).committed_watermark is None
    assert catalog.registered_batch_count() == 0


def test_journal_phase_is_done_after_success(tmp_path: Path) -> None:
    raw_store, staged, state, catalog, task_id = setup_staged(tmp_path)
    service = IngestionCommitService(state, raw_store, catalog)

    service.commit_staged(
        task_id=task_id,
        staged=staged,
        quality_report=clean_report(staged.manifest.batch_id),
        next_page_token=None,
        committed_at="2026-07-10T20:01:01Z",
    )

    assert state.commit_entry(staged.manifest.batch_id).phase is CommitPhase.DONE


def test_mismatched_quality_evidence_never_creates_a_journal(tmp_path: Path) -> None:
    raw_store, staged, state, catalog, task_id = setup_staged(tmp_path)
    mismatched = clean_report("different-batch")

    with pytest.raises(IngestionStateConflict, match="quality evidence"):
        IngestionCommitService(state, raw_store, catalog).commit_staged(
            task_id=task_id,
            staged=staged,
            quality_report=mismatched,
            next_page_token=None,
            committed_at="2026-07-10T20:01:01Z",
        )

    assert state.pending_commits() == []
    assert state.get(task_id).status is IngestionStatus.STAGED


def test_service_derives_watermark_and_refuses_regression(tmp_path: Path) -> None:
    raw_store, staged, state, catalog, task_id = setup_staged(
        tmp_path,
        previous_watermark=datetime(2026, 7, 10, 14, 30, tzinfo=UTC),
    )

    with pytest.raises(IngestionStateConflict, match="backwards"):
        IngestionCommitService(state, raw_store, catalog).commit_staged(
            task_id=task_id,
            staged=staged,
            quality_report=clean_report(staged.manifest.batch_id),
            next_page_token=None,
            committed_at="2026-07-10T20:01:01Z",
        )

    assert state.pending_commits() == []
    assert catalog.registered_batch_count() == 0


def test_recovery_refuses_a_published_task_with_the_wrong_page_cursor(
    tmp_path: Path,
) -> None:
    raw_store, staged, state, catalog, task_id = setup_staged(tmp_path)

    def fail(phase: str) -> None:
        if phase == "task_published":
            raise RuntimeError("simulated process crash")

    with pytest.raises(RuntimeError, match="simulated process crash"):
        IngestionCommitService(
            state, raw_store, catalog, phase_hook=fail
        ).commit_staged(
            task_id=task_id,
            staged=staged,
            quality_report=clean_report(staged.manifest.batch_id),
            next_page_token="safe-next-token",
            committed_at="2026-07-10T20:01:01Z",
        )
    published = state.get(task_id)
    corrupted = published.model_copy(update={"resume_page_token": "wrong-token"})
    with duckdb.connect(str(state.path)) as connection:
        connection.execute(
            "UPDATE ingestion_tasks SET task_json = ?::JSON WHERE task_id = ?",
            [corrupted.model_dump_json(), task_id],
        )

    with pytest.raises(IngestionStateConflict, match="conflicts"):
        IngestionCommitService(state, raw_store, catalog).recover_pending()


def test_cleanup_cannot_delete_a_batch_after_commit_journal_is_prepared(
    tmp_path: Path,
) -> None:
    raw_store, staged, state, catalog, task_id = setup_staged(tmp_path)

    def fail(phase: str) -> None:
        if phase == "prepared":
            raise RuntimeError("simulated process crash")

    with pytest.raises(RuntimeError, match="simulated process crash"):
        IngestionCommitService(
            state, raw_store, catalog, phase_hook=fail
        ).commit_staged(
            task_id=task_id,
            staged=staged,
            quality_report=clean_report(staged.manifest.batch_id),
            next_page_token=None,
            committed_at="2026-07-10T20:01:01Z",
        )

    removed = raw_store.cleanup_staging(
        created_before="2026-07-11T00:00:00Z",
        is_batch_protected=lambda batch_id: batch_id
        in state.protected_batch_ids(),
    )
    assert removed == []
    assert staged.staging_path.exists()
    assert IngestionCommitService(
        state, raw_store, catalog
    ).recover_pending()[0].status is IngestionStatus.COMMITTED
