"""DuckDB catalog for committed raw evidence and rebuild metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from stock_prediction.contracts.ingestion import RawBatchManifest
from stock_prediction.data.raw_store import RawBatchStore
from stock_prediction.sql_assets import load_sql_asset


class CatalogError(RuntimeError):
    """Base class for sanitized catalog failures."""


class CatalogConflictError(CatalogError):
    """A natural identifier already owns different catalog evidence."""


class DuckDbCatalog:
    """Small local catalog whose derived tables can be rebuilt from cold evidence."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                """
                SET TimeZone = 'UTC';
                CREATE TABLE IF NOT EXISTS batch_manifests (
                    batch_id VARCHAR PRIMARY KEY,
                    dataset VARCHAR NOT NULL,
                    dataset_revision VARCHAR NOT NULL,
                    committed_at TIMESTAMPTZ NOT NULL,
                    sha256 VARCHAR NOT NULL,
                    manifest_json JSON NOT NULL
                );
                CREATE TABLE IF NOT EXISTS quality_reports (
                    batch_id VARCHAR PRIMARY KEY,
                    report_id VARCHAR NOT NULL,
                    input_rows BIGINT NOT NULL,
                    valid_rows BIGINT NOT NULL,
                    quarantined_rows BIGINT NOT NULL,
                    quality_score DOUBLE NOT NULL,
                    report_json JSON NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rebuild_runs (
                    rebuild_id VARCHAR PRIMARY KEY,
                    manifest_set_hash VARCHAR NOT NULL,
                    raw_rows BIGINT NOT NULL,
                    observation_revisions BIGINT NOT NULL,
                    canonical_rows BIGINT NOT NULL,
                    completed_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
                );
                """
            )
            connection.execute(load_sql_asset("m2/data_quality_summary.sql"))

    def connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.path))

    def register_committed_batch(
        self, store: RawBatchStore, manifest: RawBatchManifest
    ) -> bool:
        store.verify(manifest)
        quality = store.quality_report(manifest)
        manifest_json = manifest.model_dump_json()
        quality_json = quality.model_dump_json()
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT manifest_json::VARCHAR,
                       (SELECT report_json::VARCHAR FROM quality_reports WHERE batch_id = ?)
                FROM batch_manifests
                WHERE batch_id = ?
                """,
                [manifest.batch_id, manifest.batch_id],
            ).fetchone()
            if existing is not None:
                if existing[0] != manifest_json or existing[1] != quality_json:
                    raise CatalogConflictError(
                        "batch ID already owns different catalog evidence"
                    )
                return False
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """
                    INSERT INTO batch_manifests
                    VALUES (?, ?, ?, ?, ?, ?::JSON)
                    """,
                    [
                        manifest.batch_id,
                        manifest.dataset,
                        manifest.dataset_revision,
                        manifest.committed_at,
                        manifest.sha256,
                        manifest_json,
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO quality_reports
                    VALUES (?, ?, ?, ?, ?, ?, ?::JSON)
                    """,
                    [
                        quality.batch_id,
                        quality.report_id,
                        quality.input_rows,
                        quality.valid_rows,
                        quality.quarantined_rows,
                        quality.quality_score,
                        quality_json,
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return True

    def rehydrate_from_raw(self, store: RawBatchStore) -> int:
        return sum(
            self.register_committed_batch(store, manifest)
            for manifest in store.committed_manifests()
        )

    def registered_batch_count(self) -> int:
        with self.connect() as connection:
            value = connection.execute(
                "SELECT COUNT(*) FROM batch_manifests"
            ).fetchone()
        return int(value[0]) if value is not None else 0

    def manifests(self) -> list[RawBatchManifest]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT manifest_json::VARCHAR
                FROM batch_manifests
                ORDER BY committed_at, batch_id
                """
            ).fetchall()
        return [RawBatchManifest.model_validate_json(row[0]) for row in rows]

    def quality_summary(self) -> list[dict[str, object]]:
        return self.query_dicts(
            """
            SELECT dataset_revision, batch_count, input_rows, valid_rows,
                   quarantined_rows, average_quality_score
            FROM data_quality_summary
            """
        )

    def query_dicts(
        self, query: str, parameters: list[object] | None = None
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            result = connection.execute(query, parameters or [])
            columns = [description[0] for description in result.description]
            rows = result.fetchall()
        return [dict(zip(columns, row, strict=True)) for row in rows]
