from __future__ import annotations

import os
from pathlib import Path

import pytest
import pyarrow as pa

from backend.tests.fixtures.research_snapshot_factory import (
    GENERATED_AT,
    make_snapshot_tables,
    resolved_snapshot,
)
from stock_prediction.serving.manifest import ResearchSnapshotManifest
from stock_prediction.serving.materialize import (
    MaterializationError,
    materialize_research_snapshot,
)
from stock_prediction.serving.repository import SnapshotVersionConflict


def test_materialization_publishes_complete_content_checked_snapshot(
    tmp_path: Path,
) -> None:
    snapshot = resolved_snapshot()
    result = materialize_research_snapshot(
        tmp_path,
        snapshot=snapshot,
        generated_at=GENERATED_AT,
        tables=make_snapshot_tables(snapshot),
        champion_exists=True,
    )
    assert result.published_path == tmp_path / snapshot.snapshot_id
    assert result.reason is None
    assert not tmp_path.joinpath(f"{snapshot.snapshot_id}.staging").exists()
    manifest = ResearchSnapshotManifest.model_validate_json(
        result.published_path.joinpath("manifest.json").read_bytes(), strict=True
    )
    assert manifest.snapshot_id == snapshot.snapshot_id
    assert sum(table.row_count for table in manifest.tables) > 100


def test_publication_rename_is_atomic_and_manifest_is_written_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stock_prediction.serving import materialize

    snapshot = resolved_snapshot()
    original_replace = os.replace
    observed: list[bool] = []

    def observe_replace(source: str | Path, target: str | Path) -> None:
        source_path = Path(source)
        target_path = Path(target)
        assert source_path.name.endswith(".staging")
        assert not target_path.exists()
        assert source_path.joinpath("manifest.json").is_file()
        observed.append(True)
        original_replace(source, target)

    monkeypatch.setattr(materialize.os, "replace", observe_replace)
    materialize_research_snapshot(
        tmp_path,
        snapshot=snapshot,
        generated_at=GENERATED_AT,
        tables=make_snapshot_tables(snapshot),
        champion_exists=True,
    )
    assert observed == [True]


def test_failed_validation_exposes_no_partial_or_staging_directory(
    tmp_path: Path,
) -> None:
    snapshot = resolved_snapshot()
    tables = make_snapshot_tables(snapshot)
    tables.pop("screen.parquet")
    with pytest.raises(MaterializationError, match="required research tables"):
        materialize_research_snapshot(
            tmp_path,
            snapshot=snapshot,
            generated_at=GENERATED_AT,
            tables=tables,
            champion_exists=True,
        )
    assert not tmp_path.joinpath(snapshot.snapshot_id).exists()
    assert not tmp_path.joinpath(f"{snapshot.snapshot_id}.staging").exists()


def test_no_champion_rejects_a_forecast_ready_snapshot_fixture(
    tmp_path: Path,
) -> None:
    snapshot = resolved_snapshot()
    with pytest.raises(
        MaterializationError,
        match="champion availability must agree with snapshot prediction readiness",
    ):
        materialize_research_snapshot(
            tmp_path,
            snapshot=snapshot,
            generated_at=GENERATED_AT,
            tables=make_snapshot_tables(snapshot),
            champion_exists=False,
        )
    assert list(tmp_path.glob("*")) == []


def test_materialization_rejects_duplicate_snapshot_identity(tmp_path: Path) -> None:
    snapshot = resolved_snapshot()
    arguments = {
        "snapshot": snapshot,
        "generated_at": GENERATED_AT,
        "tables": make_snapshot_tables(snapshot),
        "champion_exists": True,
    }
    materialize_research_snapshot(tmp_path, **arguments)
    with pytest.raises(SnapshotVersionConflict, match="already published"):
        materialize_research_snapshot(tmp_path, **arguments)


@pytest.mark.parametrize(
    "snapshot_id",
    (
        "snapshot-hidden.staging",
        "snapshot-trailing.",
        "snapshot-trailing ",
        "snapshot:invalid",
        "CON",
        "con.txt",
        "COM1.snapshot",
        "LPT9",
    ),
)
def test_materialization_rejects_windows_unsafe_snapshot_ids(
    tmp_path: Path, snapshot_id: str
) -> None:
    snapshot = resolved_snapshot().model_copy(update={"snapshot_id": snapshot_id})
    tables = make_snapshot_tables(resolved_snapshot())
    with pytest.raises(MaterializationError, match="snapshot"):
        materialize_research_snapshot(
            tmp_path,
            snapshot=snapshot,
            generated_at=GENERATED_AT,
            tables=tables,
            champion_exists=True,
        )


def test_materialization_rejects_wrong_indexed_arrow_type(tmp_path: Path) -> None:
    snapshot = resolved_snapshot()
    tables = make_snapshot_tables(snapshot)
    overview = tables["overview.parquet"]
    tables["overview.parquet"] = overview.set_column(
        0, "horizon", pa.array(["5"], type=pa.string())
    )
    with pytest.raises(MaterializationError, match="overview.parquet schema"):
        materialize_research_snapshot(
            tmp_path,
            snapshot=snapshot,
            generated_at=GENERATED_AT,
            tables=tables,
            champion_exists=True,
        )


def test_materialization_rejects_wrong_payload_type_and_nullability(
    tmp_path: Path,
) -> None:
    snapshot = resolved_snapshot()
    for suffix, replacement in (
        (
            "type",
            pa.Table.from_arrays(
                [pa.array([5], type=pa.int8()), pa.array([1], type=pa.int64())],
                schema=pa.schema(
                    [
                        pa.field("horizon", pa.int8(), nullable=False),
                        pa.field("payload_json", pa.int64(), nullable=False),
                    ]
                ),
            ),
        ),
        (
            "nullability",
            pa.Table.from_arrays(
                [
                    pa.array([5], type=pa.int8()),
                    pa.array(
                        make_snapshot_tables(snapshot)["overview.parquet"]
                        .column("payload_json")
                        .to_pylist(),
                        type=pa.string(),
                    ),
                ],
                schema=pa.schema(
                    [
                        pa.field("horizon", pa.int8(), nullable=False),
                        pa.field("payload_json", pa.string(), nullable=True),
                    ]
                ),
            ),
        ),
    ):
        root = tmp_path / suffix
        tables = make_snapshot_tables(snapshot)
        tables["overview.parquet"] = replacement
        with pytest.raises(MaterializationError, match="overview.parquet schema"):
            materialize_research_snapshot(
                root,
                snapshot=snapshot,
                generated_at=GENERATED_AT,
                tables=tables,
                champion_exists=True,
            )


def test_materialization_rejects_null_data_in_non_nullable_payload_before_staging(
    tmp_path: Path,
) -> None:
    snapshot = resolved_snapshot()
    tables = make_snapshot_tables(snapshot)
    tables["overview.parquet"] = pa.Table.from_arrays(
        [
            pa.array([5], type=pa.int8()),
            pa.array([None], type=pa.string()),
        ],
        schema=pa.schema(
            [
                pa.field("horizon", pa.int8(), nullable=False),
                pa.field("payload_json", pa.string(), nullable=False),
            ]
        ),
    )
    with pytest.raises(MaterializationError, match="non-nullable"):
        materialize_research_snapshot(
            tmp_path,
            snapshot=snapshot,
            generated_at=GENERATED_AT,
            tables=tables,
            champion_exists=True,
        )
    assert not tmp_path.joinpath(snapshot.snapshot_id).exists()
    assert not tmp_path.joinpath(f"{snapshot.snapshot_id}.staging").exists()


def test_materialization_rejects_missing_extra_and_reordered_columns(
    tmp_path: Path,
) -> None:
    snapshot = resolved_snapshot()
    valid = make_snapshot_tables(snapshot)["overview.parquet"]
    variants = (
        valid.select(["payload_json"]),
        valid.append_column("unexpected", pa.array([1], type=pa.int64())),
        valid.select(["payload_json", "horizon"]),
    )
    for index, replacement in enumerate(variants):
        root = tmp_path / str(index)
        tables = make_snapshot_tables(snapshot)
        tables["overview.parquet"] = replacement
        with pytest.raises(MaterializationError, match="overview.parquet schema"):
            materialize_research_snapshot(
                root,
                snapshot=snapshot,
                generated_at=GENERATED_AT,
                tables=tables,
                champion_exists=True,
            )
