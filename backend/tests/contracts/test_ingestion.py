from datetime import UTC, date

import pytest
from pydantic import ValidationError

from stock_prediction.contracts.ingestion import (
    BatchCommitState,
    DataQualityReport,
    IngestionPriority,
    IngestionStatus,
    IngestionTask,
    QualityIssue,
    QualitySeverity,
    RawBatchManifest,
)
from stock_prediction.contracts.market_data import Timeframe


SHA = "a" * 64


def valid_manifest(**overrides: object) -> RawBatchManifest:
    values: dict[str, object] = {
        "batch_id": "batch-spy-1d-20260710",
        "task_id": "task-spy-1d-20260710",
        "dataset": "market_bars",
        "market": "US",
        "symbol": "US.SPY",
        "timeframe": Timeframe.DAY_1,
        "schema_version": "market-bar-v1",
        "dataset_revision": "dataset-r1",
        "request_start": date(2026, 7, 10),
        "request_end": date(2026, 7, 10),
        "request_fingerprint": SHA,
        "row_count": 1,
        "min_event_at": "2026-07-10T13:30:00Z",
        "max_event_at": "2026-07-10T13:30:00Z",
        "sha256": SHA,
        "quality_report_sha256": SHA,
        "relative_data_path": (
            "market_bars/US/1d/US.SPY/2026/07/batch-spy-1d-20260710/data.parquet"
        ),
        "opend_version": "1007",
        "sdk_version": "10.8.6808",
        "created_at": "2026-07-10T20:01:00Z",
        "commit_state": BatchCommitState.COMMITTED,
        "committed_at": "2026-07-10T20:01:01Z",
    }
    values.update(overrides)
    return RawBatchManifest(**values)


def test_manifest_round_trip_and_utc_normalization() -> None:
    manifest = valid_manifest()

    restored = RawBatchManifest.model_validate_json(manifest.model_dump_json())

    assert restored == manifest
    assert manifest.created_at.tzinfo is UTC
    assert manifest.committed_at is not None
    assert manifest.committed_at.tzinfo is UTC


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("relative_data_path", "../escape.parquet", "relative_data_path"),
        ("relative_data_path", "C:/data/file.parquet", "relative_data_path"),
        ("sha256", "bad", "sha256"),
        ("request_fingerprint", "bad", "request_fingerprint"),
        ("row_count", 0, "row_count"),
    ],
)
def test_manifest_rejects_unsafe_or_incomplete_evidence(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        valid_manifest(**{field: value})


def test_committed_manifest_requires_commit_time_and_valid_bounds() -> None:
    with pytest.raises(ValidationError, match="committed_at"):
        valid_manifest(committed_at=None)
    with pytest.raises(ValidationError, match="request_start"):
        valid_manifest(
            request_start=date(2026, 7, 11), request_end=date(2026, 7, 10)
        )
    with pytest.raises(ValidationError, match="min_event_at"):
        valid_manifest(
            min_event_at="2026-07-10T14:30:00Z",
            max_event_at="2026-07-10T13:30:00Z",
        )


def valid_task(**overrides: object) -> IngestionTask:
    values: dict[str, object] = {
        "task_id": "task-spy-1d-20260710",
        "dataset": "market_bars",
        "market": "US",
        "symbol": "US.SPY",
        "timeframe": Timeframe.DAY_1,
        "requested_start": date(2026, 7, 1),
        "requested_end": date(2026, 7, 10),
        "priority": IngestionPriority.HIGH,
        "attempt": 0,
        "status": IngestionStatus.PLANNED,
        "error_class": None,
        "next_retry_at": None,
        "committed_batch_id": None,
        "committed_watermark": None,
        "created_at": "2026-07-10T20:00:00Z",
        "updated_at": "2026-07-10T20:00:00Z",
    }
    values.update(overrides)
    return IngestionTask(**values)


def test_task_enums_are_stable_and_planned_task_has_no_progress() -> None:
    task = valid_task()

    assert [item.value for item in IngestionPriority] == ["high", "medium", "low"]
    assert [item.value for item in IngestionStatus] == [
        "planned",
        "capability_check",
        "fetching",
        "retrying",
        "staged",
        "committed",
        "blocked",
        "failed",
        "quarantined",
    ]
    assert task.committed_watermark is None


def test_retry_and_commit_states_require_their_evidence() -> None:
    with pytest.raises(ValidationError, match="retrying"):
        valid_task(status=IngestionStatus.RETRYING)
    with pytest.raises(ValidationError, match="committed"):
        valid_task(status=IngestionStatus.COMMITTED)

    committed = valid_task(
        status=IngestionStatus.COMMITTED,
        committed_batch_id="batch-spy-1d-20260710",
        committed_watermark="2026-07-10T20:00:00Z",
    )
    assert committed.committed_watermark is not None
    assert committed.committed_watermark.tzinfo is UTC


def test_quality_report_quarantines_any_hard_error() -> None:
    issue = QualityIssue(
        code="duplicate_natural_key",
        severity=QualitySeverity.ERROR,
        message="duplicate bar key",
        affected_rows=2,
    )

    report = DataQualityReport(
        report_id="quality-batch-1",
        batch_id="batch-1",
        checked_at="2026-07-10T20:02:00Z",
        input_rows=10,
        valid_rows=8,
        quarantined_rows=2,
        quality_score=0.8,
        quarantined=True,
        issues=[issue],
    )

    assert report.quarantined is True
    with pytest.raises(ValidationError, match="quarantined"):
        DataQualityReport(
            **{**report.model_dump(), "quarantined": False},
        )


def test_quality_report_row_reconciliation_is_exact() -> None:
    with pytest.raises(ValidationError, match="input_rows"):
        DataQualityReport(
            report_id="quality-batch-1",
            batch_id="batch-1",
            checked_at="2026-07-10T20:02:00Z",
            input_rows=10,
            valid_rows=9,
            quarantined_rows=2,
            quality_score=0.8,
            quarantined=True,
            issues=[
                QualityIssue(
                    code="bad_ohlc",
                    severity=QualitySeverity.ERROR,
                    message="invalid prices",
                    affected_rows=2,
                )
            ],
        )
