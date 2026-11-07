"""Deterministic DuckDB rebuild from verified immutable Parquet batches."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from stock_prediction.data.catalog import CatalogConflictError, DuckDbCatalog
from stock_prediction.data.raw_store import RawBatchIntegrityError, RawBatchStore
from stock_prediction.sql_assets import load_sql_asset


@dataclass(frozen=True, slots=True)
class RebuildResult:
    rebuild_id: str
    manifest_set_hash: str
    raw_rows: int
    observation_revisions: int
    canonical_rows: int


class MarketDataRebuilder:
    """Recreate normalized revision history and canonical bars from cold evidence."""

    def __init__(
        self,
        catalog: DuckDbCatalog,
        store: RawBatchStore,
        *,
        phase_hook: Callable[[str], None] = lambda _phase: None,
    ) -> None:
        self.catalog = catalog
        self.store = store
        self.phase_hook = phase_hook

    def rebuild(self) -> RebuildResult:
        manifests = self.catalog.manifests()
        if not manifests:
            raise RawBatchIntegrityError("no committed market-bar batches to rebuild")
        tables: list[pa.Table] = []
        evidence: list[dict[str, object]] = []
        for manifest in manifests:
            self.store.verify(manifest)
            report = self.store.quality_report(manifest)
            table = pq.read_table(self.store.data_path(manifest))
            batch_ids = pc.unique(table.column("raw_batch_id")).to_pylist()
            revisions = pc.unique(table.column("dataset_revision")).to_pylist()
            if batch_ids != [manifest.batch_id] or revisions != [
                manifest.dataset_revision
            ]:
                raise RawBatchIntegrityError(
                    "Parquet lineage does not match its committed manifest"
                )
            table = table.append_column(
                "manifest_committed_at",
                pa.array(
                    [manifest.committed_at] * table.num_rows,
                    type=pa.timestamp("us", tz="UTC"),
                ),
            ).append_column(
                "manifest_batch_id",
                pa.array([manifest.batch_id] * table.num_rows, type=pa.string()),
            )
            tables.append(table)
            evidence.append(
                {
                    "manifest": manifest.model_dump(mode="json"),
                    "quality_report": report.model_dump(mode="json"),
                }
            )

        manifest_set_hash = hashlib.sha256(
            json.dumps(
                evidence,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        rebuild_id = f"rebuild-{manifest_set_hash[:24]}"
        input_table = pa.concat_tables(tables).append_column(
            "rebuild_id",
            pa.array([rebuild_id] * sum(table.num_rows for table in tables)),
        )
        with self.catalog.connect() as connection:
            connection.register("raw_market_bars_input", input_table)
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(load_sql_asset("m2/normalized_market_bars.sql"))
                self.phase_hook("derived_tables_replaced")
                raw_rows = _count(connection, "raw_market_bars_input")
                observation_revisions = _count(
                    connection, "market_bar_observations"
                )
                canonical_rows = _count(connection, "market_bars_canonical")
                existing = connection.execute(
                    """
                    SELECT manifest_set_hash, raw_rows, observation_revisions,
                           canonical_rows
                    FROM rebuild_runs WHERE rebuild_id = ?
                    """,
                    [rebuild_id],
                ).fetchone()
                values = (
                    manifest_set_hash,
                    raw_rows,
                    observation_revisions,
                    canonical_rows,
                )
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO rebuild_runs (
                            rebuild_id, manifest_set_hash, raw_rows,
                            observation_revisions, canonical_rows
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        [rebuild_id, *values],
                    )
                elif tuple(existing) != values:
                    raise CatalogConflictError(
                        "rebuild ID already owns different derived evidence"
                    )
                self.phase_hook("rebuild_registered")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.unregister("raw_market_bars_input")
        return RebuildResult(
            rebuild_id=rebuild_id,
            manifest_set_hash=manifest_set_hash,
            raw_rows=raw_rows,
            observation_revisions=observation_revisions,
            canonical_rows=canonical_rows,
        )


def _count(connection: duckdb.DuckDBPyConnection, relation: str) -> int:
    if relation not in {
        "raw_market_bars_input",
        "market_bar_observations",
        "market_bars_canonical",
    }:
        raise ValueError("unsupported rebuild relation")
    row = connection.execute(f"SELECT COUNT(*) FROM {relation}").fetchone()
    if row is None:
        raise RawBatchIntegrityError("rebuild count query returned no result")
    return int(row[0])
