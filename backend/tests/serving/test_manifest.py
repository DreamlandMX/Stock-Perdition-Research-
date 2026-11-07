from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pytest
from pydantic import ValidationError

from stock_prediction.contracts.serving import ResolvedSnapshot
from stock_prediction.serving.manifest import (
    EXPECTED_RESEARCH_TABLES,
    RESEARCH_TABLE_SCHEMAS,
    ResearchSnapshotManifest,
    SnapshotTable,
    parquet_schema_hash,
)
from stock_prediction.serving.repository import SnapshotQuery


def snapshot() -> ResolvedSnapshot:
    return ResolvedSnapshot(
        snapshot_id="snapshot-20260710-r1",
        revision_no=1,
        as_of=datetime(2026, 7, 10, 20, tzinfo=UTC),
        dataset_revision="dataset-r1",
        feature_set_version="features-v1",
        universe_version="universe-v1",
        model_version="model-v1",
        calibration_version="calibration-v1",
    )


def tables() -> tuple[SnapshotTable, ...]:
    return tuple(
        SnapshotTable(
            path=path,
            schema_hash=parquet_schema_hash(RESEARCH_TABLE_SCHEMAS[path]),
            content_hash=f"{index:064x}",
            row_count=index,
        )
        for index, path in enumerate(EXPECTED_RESEARCH_TABLES, start=1)
    )


def manifest() -> ResearchSnapshotManifest:
    return ResearchSnapshotManifest.create(
        snapshot=snapshot(),
        generated_at=datetime(2026, 7, 10, 20, 2, tzinfo=UTC),
        tables=tables(),
    )


def test_manifest_is_frozen_published_and_content_hashed() -> None:
    value = manifest()
    assert value.status == "published"
    assert value.verify_manifest_hash() is None
    with pytest.raises(ValidationError, match="frozen"):
        value.revision_no = 2

    with pytest.raises(ValidationError, match="manifest hash"):
        ResearchSnapshotManifest.model_validate(
            {**value.model_dump(), "revision_no": 2}, strict=True
        )


def test_backtest_schema_exposes_exact_identity_columns() -> None:
    schema = RESEARCH_TABLE_SCHEMAS["backtests.parquet"]

    assert schema.names == [
        "model_version",
        "strategy_version",
        "universe_version",
        "split_version",
        "task",
        "horizon",
        "asset_group",
        "evaluation_window_id",
        "cost_scenario",
        "payload_json",
    ]
    assert schema.field("horizon").type == pa.int8()
    assert all(not field.nullable for field in schema)


def test_manifest_rejects_unpublished_missing_and_duplicate_tables() -> None:
    value = manifest()
    payload = value.model_dump()
    with pytest.raises(ValidationError, match="published"):
        ResearchSnapshotManifest.model_validate(
            {**payload, "status": "staging"}, strict=True
        )
    with pytest.raises(ValidationError, match="required research tables"):
        ResearchSnapshotManifest.create(
            snapshot=snapshot(),
            generated_at=value.generated_at,
            tables=value.tables[:-1],
        )
    with pytest.raises(ValidationError, match="unique"):
        ResearchSnapshotManifest.create(
            snapshot=snapshot(),
            generated_at=value.generated_at,
            tables=(*value.tables[:-1], value.tables[0]),
        )


def test_manifest_rejects_non_utc_timestamps() -> None:
    value = manifest()
    for field in ("as_of", "generated_at"):
        with pytest.raises(ValidationError, match="UTC"):
            ResearchSnapshotManifest.model_validate(
                {
                    **value.model_dump(mode="json"),
                    field: "2026-07-10T21:00:00+01:00",
                },
                strict=False,
            )


def test_snapshot_table_rejects_traversal_and_nested_paths() -> None:
    for unsafe in ("../screen.parquet", "subdir/screen.parquet", "C:/screen.parquet"):
        with pytest.raises(ValidationError, match="safe relative filename"):
            SnapshotTable(
                path=unsafe,
                schema_hash="a" * 64,
                content_hash="b" * 64,
                row_count=1,
            )


@pytest.mark.parametrize(
    "snapshot_id",
    (
        "snapshot-hidden.staging",
        "snapshot-trailing.",
        "snapshot-trailing ",
        "snapshot:invalid",
        "CON",
        "con.txt",
        "PRN",
        "AUX.json",
        "NUL",
        "COM1",
        "com9.snapshot",
        "LPT1",
        "lpt9.parquet",
    ),
)
def test_manifest_and_query_reject_windows_unsafe_snapshot_ids(
    snapshot_id: str,
) -> None:
    unsafe = snapshot().model_copy(update={"snapshot_id": snapshot_id})
    with pytest.raises(ValidationError, match="snapshot"):
        ResearchSnapshotManifest.create(
            snapshot=unsafe,
            generated_at=datetime(2026, 7, 10, 20, 2, tzinfo=UTC),
            tables=tables(),
        )
    with pytest.raises(ValidationError, match="snapshot"):
        SnapshotQuery(snapshot_id=snapshot_id)


def test_repository_path_confinement_rejects_resolved_escape(tmp_path: Path) -> None:
    from stock_prediction.serving import manifest as manifest_module

    root = tmp_path / "repository"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="repository root"):
        manifest_module.resolve_repository_path(root, outside)
