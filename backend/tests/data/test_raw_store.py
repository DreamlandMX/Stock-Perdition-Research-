from datetime import date
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from stock_prediction.contracts.ingestion import (
    BatchCommitState,
    DataQualityReport,
    QualityIssue,
    QualitySeverity,
)
from stock_prediction.contracts.market_data import MarketBar, MarketSession, Timeframe
from stock_prediction.data.raw_store import (
    RawBatchConflictError,
    RawBatchIntegrityError,
    RawBatchStore,
)


SHA = "b" * 64


def bar(*, close: float = 624.0, batch_id: str = "batch-spy-20260710") -> MarketBar:
    return MarketBar(
        symbol="US.SPY",
        timeframe=Timeframe.DAY_1,
        session=MarketSession.REGULAR,
        session_date=date(2026, 7, 10),
        bar_start="2026-07-10T13:30:00Z",
        open=620.0,
        high=max(625.0, close),
        low=618.0,
        close=close,
        volume=50_000_000,
        turnover=31_100_000_000.0,
        source_available_at="2026-07-10T20:00:05Z",
        ingested_at="2026-07-10T20:01:00Z",
        raw_batch_id=batch_id,
        schema_version="market-bar-v1",
        dataset_revision="dataset-r1",
        revision_no=1,
    )


def stage(
    store: RawBatchStore,
    rows: list[MarketBar],
    *,
    created_at: str = "2026-07-10T20:01:00Z",
):
    return store.stage_market_bars(
        rows,
        task_id="task-spy-20260710",
        batch_id=rows[0].raw_batch_id,
        request_start=date(2026, 7, 10),
        request_end=date(2026, 7, 10),
        request_fingerprint=SHA,
        opend_version="1007",
        sdk_version="10.8.6808",
        created_at=created_at,
    )


def clean_report(batch_id: str) -> DataQualityReport:
    return DataQualityReport(
        report_id=f"quality-{batch_id}",
        batch_id=batch_id,
        checked_at="2026-07-10T20:01:00Z",
        input_rows=1,
        valid_rows=1,
        quarantined_rows=0,
        quality_score=1.0,
        quarantined=False,
        issues=[],
    )


def failed_report(batch_id: str) -> DataQualityReport:
    return DataQualityReport(
        report_id=f"quality-{batch_id}",
        batch_id=batch_id,
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


def test_stage_is_not_visible_until_atomic_commit(tmp_path: Path) -> None:
    store = RawBatchStore(tmp_path)

    staged = stage(store, [bar()])

    assert staged.manifest.commit_state is BatchCommitState.STAGED
    assert staged.data_path.exists()
    assert staged.manifest_path.exists()
    assert store.committed_manifests() == []

    committed = store.commit(
        staged,
        quality_report=clean_report(staged.manifest.batch_id),
        committed_at="2026-07-10T20:01:01Z",
    )

    assert committed.commit_state is BatchCommitState.COMMITTED
    assert store.verify(committed) == committed
    assert store.quality_report(committed) == clean_report(committed.batch_id)
    assert store.committed_manifests() == [committed]
    assert not staged.staging_path.exists()


def test_parquet_schema_is_deterministic_and_timezone_aware(tmp_path: Path) -> None:
    staged = stage(RawBatchStore(tmp_path), [bar()])

    table = pq.read_table(staged.data_path)

    assert table.schema.names == [
        "symbol",
        "timeframe",
        "session",
        "session_date",
        "bar_start",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
        "source_available_at",
        "ingested_at",
        "raw_batch_id",
        "schema_version",
        "dataset_revision",
        "revision_no",
    ]
    assert str(table.schema.field("bar_start").type) == "timestamp[us, tz=UTC]"
    assert str(table.schema.field("session_date").type) == "date32[day]"


def test_same_batch_is_idempotent_but_conflicting_reuse_fails(tmp_path: Path) -> None:
    store = RawBatchStore(tmp_path)
    committed = store.commit(
        stage(store, [bar()]),
        quality_report=clean_report("batch-spy-20260710"),
        committed_at="2026-07-10T20:01:01Z",
    )

    repeated = stage(store, [bar()])
    repeated_commit = store.commit(
        repeated,
        quality_report=clean_report("batch-spy-20260710"),
        committed_at="2026-07-10T20:02:00Z",
    )

    assert repeated.already_committed is True
    assert repeated_commit == committed
    with pytest.raises(RawBatchConflictError, match="batch ID"):
        stage(store, [bar(close=623.5)])


def test_batch_id_is_globally_unique_across_storage_partitions(tmp_path: Path) -> None:
    store = RawBatchStore(tmp_path)
    committed = store.commit(
        stage(store, [bar()]),
        quality_report=clean_report("batch-spy-20260710"),
        committed_at="2026-07-10T20:01:01Z",
    )
    other_symbol = MarketBar(
        **{**bar().model_dump(), "symbol": "US.QQQ"}
    )

    with pytest.raises(RawBatchConflictError, match="batch ID"):
        stage(store, [other_symbol])

    assert store.find_committed(committed.batch_id) == committed
    assert len(store.committed_manifests()) == 1


def test_verify_detects_data_tampering(tmp_path: Path) -> None:
    store = RawBatchStore(tmp_path)
    committed = store.commit(
        stage(store, [bar()]),
        quality_report=clean_report("batch-spy-20260710"),
        committed_at="2026-07-10T20:01:01Z",
    )
    data_path = store.data_path(committed)
    data_path.write_bytes(data_path.read_bytes() + b"tampered")

    with pytest.raises(RawBatchIntegrityError, match="checksum"):
        store.verify(committed)


def test_verify_detects_quality_report_tampering(tmp_path: Path) -> None:
    store = RawBatchStore(tmp_path)
    committed = store.commit(
        stage(store, [bar()]),
        quality_report=clean_report("batch-spy-20260710"),
        committed_at="2026-07-10T20:01:01Z",
    )
    quality_path = store.data_path(committed).parent / "quality-report.json"
    payload = json.loads(quality_path.read_text(encoding="utf-8"))
    payload["quality_score"] = 0.9
    quality_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RawBatchIntegrityError, match="quality report checksum"):
        store.verify(committed)


def test_staging_cleanup_never_removes_committed_evidence(tmp_path: Path) -> None:
    store = RawBatchStore(tmp_path)
    committed = store.commit(
        stage(store, [bar(batch_id="batch-committed")]),
        quality_report=clean_report("batch-committed"),
        committed_at="2026-07-10T20:01:01Z",
    )
    abandoned = stage(store, [bar(batch_id="batch-abandoned")])
    protected = stage(store, [bar(batch_id="batch-protected")])
    active = stage(
        store,
        [bar(batch_id="batch-active")],
        created_at="2026-07-12T00:00:00Z",
    )

    removed = store.cleanup_staging(
        created_before="2026-07-11T00:00:00Z",
        is_batch_protected=lambda batch_id: batch_id == "batch-protected",
    )

    assert removed == ["batch-abandoned"]
    assert not abandoned.staging_path.exists()
    assert protected.staging_path.exists()
    assert active.staging_path.exists()
    assert store.verify(committed) == committed


def test_commit_rejects_missing_or_mismatched_quality_evidence(tmp_path: Path) -> None:
    store = RawBatchStore(tmp_path)
    staged = stage(store, [bar()])

    with pytest.raises(RawBatchIntegrityError, match="quality report"):
        store.commit(
            staged,
            quality_report=clean_report("different-batch"),
            committed_at="2026-07-10T20:01:01Z",
        )

    assert staged.staging_path.exists()


def test_commit_recovers_after_directory_publish_before_manifest_promotion(
    tmp_path: Path,
) -> None:
    def crash(phase: str) -> None:
        if phase == "commit_directory_published":
            raise RuntimeError("simulated process crash")

    store = RawBatchStore(tmp_path, phase_hook=crash)
    staged = stage(store, [bar()])
    with pytest.raises(RuntimeError, match="simulated process crash"):
        store.commit(
            staged,
            quality_report=clean_report(staged.manifest.batch_id),
            committed_at="2026-07-10T20:01:01Z",
        )

    recovered_store = RawBatchStore(tmp_path)
    recovered = recovered_store.find_committed(staged.manifest.batch_id)
    assert recovered is not None
    assert recovered.commit_state is BatchCommitState.COMMITTED
    assert recovered_store.verify(recovered) == recovered


def test_idempotent_stage_recovers_pending_manifest_without_lock_deadlock(
    tmp_path: Path,
) -> None:
    def crash(phase: str) -> None:
        if phase == "commit_directory_published":
            raise RuntimeError("simulated process crash")

    store = RawBatchStore(tmp_path, phase_hook=crash)
    staged = stage(store, [bar()])
    with pytest.raises(RuntimeError, match="simulated process crash"):
        store.commit(
            staged,
            quality_report=clean_report(staged.manifest.batch_id),
            committed_at="2026-07-10T20:01:01Z",
        )

    repeated = stage(RawBatchStore(tmp_path), [bar()])
    assert repeated.already_committed is True
    assert repeated.manifest.commit_state is BatchCommitState.COMMITTED


def test_quarantine_recovers_after_directory_publish_before_manifest_promotion(
    tmp_path: Path,
) -> None:
    def crash(phase: str) -> None:
        if phase == "quarantine_directory_published":
            raise RuntimeError("simulated process crash")

    store = RawBatchStore(tmp_path, phase_hook=crash)
    staged = stage(store, [bar()])
    with pytest.raises(RuntimeError, match="simulated process crash"):
        store.quarantine(
            staged, quality_report=failed_report(staged.manifest.batch_id)
        )

    recovered_store = RawBatchStore(tmp_path)
    recovered = recovered_store.find_quarantined(staged.manifest.batch_id)
    assert recovered is not None
    assert recovered.commit_state is BatchCommitState.QUARANTINED
    assert recovered_store.verify_quarantined(recovered) == recovered


def test_batch_requires_one_partition_and_matching_batch_id(tmp_path: Path) -> None:
    store = RawBatchStore(tmp_path)
    different_month = MarketBar(
        **{
            **bar().model_dump(),
            "session_date": date(2026, 8, 3),
            "bar_start": "2026-08-03T13:30:00Z",
        }
    )

    with pytest.raises(RawBatchIntegrityError, match="partition"):
        stage(store, [bar(), different_month])
    with pytest.raises(RawBatchIntegrityError, match="raw_batch_id"):
        store.stage_market_bars(
            [bar()],
            task_id="task-spy-20260710",
            batch_id="different-batch-id",
            request_start=date(2026, 7, 10),
            request_end=date(2026, 7, 10),
            request_fingerprint=SHA,
            opend_version="1007",
            sdk_version="10.8.6808",
            created_at="2026-07-10T20:01:00Z",
        )


def test_store_rejects_unsafe_batch_identifier_before_writing(tmp_path: Path) -> None:
    store = RawBatchStore(tmp_path)

    with pytest.raises(RawBatchIntegrityError, match="metadata"):
        store.stage_market_bars(
            [bar()],
            task_id="task-spy-20260710",
            batch_id="../escape",
            request_start=date(2026, 7, 10),
            request_end=date(2026, 7, 10),
            request_fingerprint=SHA,
            opend_version="1007",
            sdk_version="10.8.6808",
            created_at="2026-07-10T20:01:00Z",
        )
    assert list(tmp_path.rglob("*.parquet")) == []
