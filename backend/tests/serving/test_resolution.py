from __future__ import annotations

from datetime import UTC, datetime

import pytest

from stock_prediction.contracts.serving import ResolvedSnapshot
from stock_prediction.serving.manifest import (
    EXPECTED_RESEARCH_TABLES,
    RESEARCH_TABLE_SCHEMAS,
    ResearchSnapshotManifest,
    SnapshotTable,
    parquet_schema_hash,
)
from stock_prediction.serving.repository import (
    SnapshotNotFound,
    SnapshotQuery,
    SnapshotVersionConflict,
)
from stock_prediction.serving.resolution import (
    FutureSnapshotQuery,
    SnapshotRevisionAmbiguity,
    resolve_snapshot_query,
)


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def manifest(
    snapshot_id: str,
    *,
    as_of: str,
    generated_at: str,
    revision_no: int = 1,
    dataset_revision: str = "dataset-r1",
    feature_set_version: str = "features-v1",
    universe_version: str = "universe-v1",
    model_version: str = "model-v1",
    calibration_version: str = "calibration-v1",
) -> ResearchSnapshotManifest:
    snapshot = ResolvedSnapshot(
        snapshot_id=snapshot_id,
        revision_no=revision_no,
        as_of=utc(as_of),
        dataset_revision=dataset_revision,
        feature_set_version=feature_set_version,
        universe_version=universe_version,
        model_version=model_version,
        calibration_version=calibration_version,
    )
    tables = tuple(
        SnapshotTable(
            path=path,
            schema_hash=parquet_schema_hash(RESEARCH_TABLE_SCHEMAS[path]),
            content_hash=f"{index:064x}",
            row_count=index,
        )
        for index, path in enumerate(EXPECTED_RESEARCH_TABLES, start=1)
    )
    return ResearchSnapshotManifest.create(
        snapshot=snapshot,
        generated_at=utc(generated_at),
        tables=tables,
    )


def test_exact_snapshot_id_has_precedence_over_as_of_selection() -> None:
    older = manifest(
        "snapshot-20260709-r1",
        as_of="2026-07-09T20:00:00Z",
        generated_at="2026-07-09T20:05:00Z",
    )
    latest = manifest(
        "snapshot-20260710-r1",
        as_of="2026-07-10T20:00:00Z",
        generated_at="2026-07-10T20:05:00Z",
    )

    resolved = resolve_snapshot_query(
        SnapshotQuery(snapshot_id=older.snapshot_id),
        (latest, older),
        evaluated_at=utc("2026-07-13T20:00:00Z"),
    )

    assert resolved == older.resolved_snapshot()


def test_as_of_resolution_never_sees_a_revision_before_publication() -> None:
    first = manifest(
        "snapshot-20260710-r1",
        as_of="2026-07-10T20:00:00Z",
        generated_at="2026-07-10T20:05:00Z",
    )
    correction = manifest(
        "snapshot-20260710-r2",
        as_of="2026-07-10T20:00:00Z",
        generated_at="2026-07-10T20:10:00Z",
        revision_no=2,
        model_version="model-v2",
    )

    resolved = resolve_snapshot_query(
        SnapshotQuery(as_of=utc("2026-07-10T20:06:00Z")),
        (correction, first),
        evaluated_at=utc("2026-07-13T20:00:00Z"),
    )

    assert resolved == first.resolved_snapshot()


def test_latest_resolution_ignores_snapshots_not_yet_published() -> None:
    current = manifest(
        "snapshot-current",
        as_of="2026-07-10T20:00:00Z",
        generated_at="2026-07-10T20:05:00Z",
    )
    future = manifest(
        "snapshot-future",
        as_of="2026-07-14T20:00:00Z",
        generated_at="2026-07-14T20:05:00Z",
    )

    resolved = resolve_snapshot_query(
        SnapshotQuery(),
        (future, current),
        evaluated_at=utc("2026-07-13T20:00:00Z"),
    )

    assert resolved == current.resolved_snapshot()


def test_exact_resolution_is_not_affected_by_a_future_duplicate_identity() -> None:
    current = manifest(
        "snapshot-exact",
        as_of="2026-07-10T20:00:00Z",
        generated_at="2026-07-10T20:05:00Z",
    )
    future_duplicate = manifest(
        "snapshot-exact",
        as_of="2026-07-14T20:00:00Z",
        generated_at="2026-07-14T20:05:00Z",
    )

    resolved = resolve_snapshot_query(
        SnapshotQuery(snapshot_id="snapshot-exact"),
        (future_duplicate, current),
        evaluated_at=utc("2026-07-13T20:00:00Z"),
    )

    assert resolved == current.resolved_snapshot()


def test_future_as_of_uses_a_stable_typed_exception() -> None:
    with pytest.raises(FutureSnapshotQuery, match="future"):
        resolve_snapshot_query(
            SnapshotQuery(as_of=utc("2026-07-14T20:00:00Z")),
            (),
            evaluated_at=utc("2026-07-13T20:00:00Z"),
        )


def test_duplicate_revision_is_rejected_as_ambiguous() -> None:
    first = manifest(
        "snapshot-a",
        as_of="2026-07-10T20:00:00Z",
        generated_at="2026-07-10T20:05:00Z",
    )
    duplicate = manifest(
        "snapshot-b",
        as_of="2026-07-10T20:00:00Z",
        generated_at="2026-07-10T20:06:00Z",
        model_version="model-v2",
    )

    with pytest.raises(SnapshotRevisionAmbiguity, match="duplicate revision"):
        resolve_snapshot_query(
            SnapshotQuery(),
            (duplicate, first),
            evaluated_at=utc("2026-07-13T20:00:00Z"),
        )


@pytest.mark.parametrize(
    ("field", "requested"),
    (
        ("dataset_revision", "dataset-conflict"),
        ("feature_set_version", "features-conflict"),
        ("universe_version", "universe-conflict"),
        ("model_version", "model-conflict"),
        ("calibration_version", "calibration-conflict"),
    ),
)
def test_exact_snapshot_requires_every_version_filter_to_match(
    field: str, requested: str
) -> None:
    published = manifest(
        "snapshot-exact",
        as_of="2026-07-10T20:00:00Z",
        generated_at="2026-07-10T20:05:00Z",
    )
    query = SnapshotQuery.model_validate(
        {"snapshot_id": published.snapshot_id, field: requested}, strict=True
    )

    with pytest.raises(SnapshotVersionConflict, match=field):
        resolve_snapshot_query(
            query,
            (published,),
            evaluated_at=utc("2026-07-13T20:00:00Z"),
        )


def test_non_exact_version_filters_select_only_a_fully_matching_snapshot() -> None:
    first = manifest(
        "snapshot-model-v1",
        as_of="2026-07-09T20:00:00Z",
        generated_at="2026-07-09T20:05:00Z",
    )
    second = manifest(
        "snapshot-model-v2",
        as_of="2026-07-10T20:00:00Z",
        generated_at="2026-07-10T20:05:00Z",
        dataset_revision="dataset-r2",
        feature_set_version="features-v2",
        universe_version="universe-v2",
        model_version="model-v2",
        calibration_version="calibration-v2",
    )

    resolved = resolve_snapshot_query(
        SnapshotQuery(
            dataset_revision="dataset-r1",
            feature_set_version="features-v1",
            universe_version="universe-v1",
            model_version="model-v1",
            calibration_version="calibration-v1",
        ),
        (second, first),
        evaluated_at=utc("2026-07-13T20:00:00Z"),
    )

    assert resolved == first.resolved_snapshot()
    with pytest.raises(SnapshotNotFound):
        resolve_snapshot_query(
            SnapshotQuery(model_version="missing-model"),
            (second, first),
            evaluated_at=utc("2026-07-13T20:00:00Z"),
        )
