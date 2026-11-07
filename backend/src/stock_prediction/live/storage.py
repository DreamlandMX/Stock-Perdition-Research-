"""Durable page watermarks, immutable raw files, and the live DuckDB catalog."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from stock_prediction.contracts.market_data import MarketBar, Timeframe
from stock_prediction.data.raw_store import MARKET_BAR_SCHEMA
from stock_prediction.live.config import LivePaths
from stock_prediction.market_data.ports import ProviderBar
from stock_prediction.yahoo.volume import YahooVolumeObservation

_PAGINATION_CHAIN_MIGRATION = "history_page_chains_v1"


@dataclass(frozen=True, slots=True)
class LiveTaskState:
    task_id: str
    status: str
    last_page_index: int
    next_page_token: str | None
    watermark: datetime | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class RawPagePart:
    batch_id: str
    path: Path
    sha256: str
    row_count: int
    min_bar_at: datetime
    max_bar_at: datetime


@dataclass(frozen=True, slots=True)
class RehabSnapshot:
    snapshot_id: str
    symbol: str
    observed_at: datetime
    row_count: int
    path: Path
    sha256: str


class LiveStorage:
    """Own live ingestion metadata and normalized observation storage."""

    def __init__(self, paths: LivePaths) -> None:
        self.paths = paths
        paths.ensure()
        self._initialize_ingestion()
        self._initialize_catalog()

    def task(self, task_id: str) -> LiveTaskState | None:
        with self._ingestion_connection() as connection:
            row = connection.execute(
                """
                SELECT task_id, status, last_page_index, next_page_token,
                       watermark, error_code
                FROM history_tasks WHERE task_id = ?
                """,
                [task_id],
            ).fetchone()
        if row is None:
            return None
        return LiveTaskState(
            task_id=str(row[0]),
            status=str(row[1]),
            last_page_index=int(row[2]),
            next_page_token=str(row[3]) if row[3] is not None else None,
            watermark=row[4],
            error_code=str(row[5]) if row[5] is not None else None,
        )

    def ensure_task(
        self,
        *,
        task_id: str,
        symbol: str,
        timeframe: Timeframe,
        start: date,
        end: date,
        priority: str,
        dataset_revision: str,
    ) -> LiveTaskState:
        now = datetime.now(UTC)
        with self._ingestion_connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO history_tasks
                (task_id, symbol, timeframe, requested_start, requested_end,
                 priority, status, last_page_index, next_page_token, watermark,
                 dataset_revision, attempt_count, error_code, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'planned', 0, NULL, NULL, ?, 0, NULL, ?)
                """,
                [
                    task_id,
                    symbol,
                    timeframe.value,
                    start,
                    end,
                    priority,
                    dataset_revision,
                    now,
                ],
            )
        state = self.task(task_id)
        if state is None:
            raise RuntimeError("live_task_registration_failed")
        return state

    def mark_fetching(self, task_id: str) -> None:
        with self._ingestion_connection() as connection:
            connection.execute(
                """
                UPDATE history_tasks
                SET status = 'fetching', attempt_count = attempt_count + 1,
                    error_code = NULL, updated_at = ?
                WHERE task_id = ? AND status <> 'committed'
                """,
                [datetime.now(UTC), task_id],
            )

    def reset_pagination(self, task_id: str) -> None:
        """Restart an expired OpenD page-token chain without deleting raw evidence."""

        with self._ingestion_connection() as connection:
            connection.execute(
                """
                UPDATE history_tasks
                SET status = 'planned', pagination_chain = pagination_chain + 1,
                    last_page_index = 0,
                    next_page_token = NULL, watermark = NULL,
                    error_code = NULL, updated_at = ?
                WHERE task_id = ? AND status <> 'committed'
                """,
                [datetime.now(UTC), task_id],
            )

    def committed_page_tokens(self, task_id: str) -> tuple[str, ...]:
        """Return nonterminal tokens from the task's active pagination chain."""

        with self._ingestion_connection() as connection:
            rows = connection.execute(
                """
                SELECT page.next_page_token
                FROM history_page_chains AS page
                JOIN history_tasks AS task USING (task_id)
                WHERE page.task_id = ?
                  AND page.pagination_chain = task.pagination_chain
                  AND page.next_page_token IS NOT NULL
                ORDER BY page.page_index
                """,
                [task_id],
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def commit_page(
        self,
        *,
        task_id: str,
        page_index: int,
        next_page_token: str | None,
        parts: Sequence[RawPagePart],
    ) -> None:
        if not parts:
            raise ValueError("a committed live page requires raw evidence")
        committed_at = datetime.now(UTC)
        watermark = max(part.max_bar_at for part in parts)
        page_id = f"{task_id}-p{page_index:05d}"
        payload = json.dumps(
            [
                {
                    "batch_id": part.batch_id,
                    "path": part.path.relative_to(self.paths.raw).as_posix(),
                    "sha256": part.sha256,
                    "row_count": part.row_count,
                    "min_bar_at": part.min_bar_at.isoformat(),
                    "max_bar_at": part.max_bar_at.isoformat(),
                }
                for part in parts
            ],
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._ingestion_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                chain_row = connection.execute(
                    "SELECT pagination_chain FROM history_tasks WHERE task_id = ?",
                    [task_id],
                ).fetchone()
                if chain_row is None:
                    raise RuntimeError("live_task_registration_failed")
                pagination_chain = int(chain_row[0])
                chain_page = connection.execute(
                    """
                    SELECT next_page_token
                    FROM history_page_chains
                    WHERE task_id = ? AND pagination_chain = ? AND page_index = ?
                    """,
                    [task_id, pagination_chain, page_index],
                ).fetchone()
                if chain_page is not None and chain_page[0] != next_page_token:
                    raise RuntimeError("live_page_pagination_conflict")
                existing = connection.execute(
                    "SELECT parts_json::VARCHAR FROM history_pages WHERE page_id = ?",
                    [page_id],
                ).fetchone()
                if existing is not None and existing[0] != payload:
                    raise RuntimeError("live_page_evidence_conflict")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO history_pages
                    VALUES (?, ?, ?, ?, ?, ?::JSON, ?)
                    """,
                    [
                        page_id,
                        task_id,
                        page_index,
                        next_page_token,
                        watermark,
                        payload,
                        committed_at,
                    ],
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO history_page_chains
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        task_id,
                        pagination_chain,
                        page_index,
                        next_page_token,
                        watermark,
                        committed_at,
                    ],
                )
                connection.execute(
                    """
                    UPDATE history_tasks
                    SET status = ?, last_page_index = ?, next_page_token = ?,
                        watermark = ?, error_code = NULL, updated_at = ?
                    WHERE task_id = ? AND last_page_index <= ?
                    """,
                    [
                        "committed" if next_page_token is None else "fetching",
                        page_index,
                        next_page_token,
                        watermark,
                        committed_at,
                        task_id,
                        page_index,
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def mark_failed(self, task_id: str, *, status: str, error_code: str) -> None:
        if status not in {"retry", "blocked", "quarantined"}:
            raise ValueError("unsupported live failure status")
        with self._ingestion_connection() as connection:
            connection.execute(
                """
                UPDATE history_tasks
                SET status = ?, error_code = ?, updated_at = ? WHERE task_id = ?
                """,
                [status, error_code, datetime.now(UTC), task_id],
            )

    def complete_empty_task(self, task_id: str) -> None:
        with self._ingestion_connection() as connection:
            connection.execute(
                """
                UPDATE history_tasks
                SET status = 'committed', next_page_token = NULL,
                    error_code = NULL, updated_at = ? WHERE task_id = ?
                """,
                [datetime.now(UTC), task_id],
            )

    def write_page(
        self,
        *,
        task_id: str,
        page_index: int,
        rows: Sequence[MarketBar],
    ) -> tuple[RawPagePart, ...]:
        parts = self.prepare_page(
            task_id=task_id,
            page_index=page_index,
            rows=rows,
        )
        self.register_catalog_parts(parts)
        return parts

    def prepare_page(
        self,
        *,
        task_id: str,
        page_index: int,
        rows: Sequence[MarketBar],
    ) -> tuple[RawPagePart, ...]:
        """Persist immutable Parquet parts without opening a catalog transaction."""

        grouped: dict[tuple[int, int], list[MarketBar]] = defaultdict(list)
        for row in rows:
            grouped[(row.session_date.year, row.session_date.month)].append(row)
        parts: list[RawPagePart] = []
        for (year, month), values in sorted(grouped.items()):
            batch_id = f"{task_id}-p{page_index:05d}-{year:04d}{month:02d}"
            page_rows = [
                row.model_copy(update={"raw_batch_id": batch_id}) for row in values
            ]
            directory = (
                self.paths.raw
                / "market_bars"
                / page_rows[0].timeframe.value
                / page_rows[0].symbol
                / f"{year:04d}"
                / f"{month:02d}"
            )
            directory.mkdir(parents=True, exist_ok=True)
            physical_id = hashlib.sha256(batch_id.encode("utf-8")).hexdigest()[:24]
            path = directory / f"{physical_id}.parquet"
            table = _market_bar_table(page_rows)
            if path.exists():
                persisted = pq.read_table(path)
                if not persisted.schema.equals(MARKET_BAR_SCHEMA, check_metadata=True):
                    raise RuntimeError("immutable_raw_batch_schema_conflict")
                table = persisted
            else:
                temporary = path.with_name(f".{physical_id}.tmp")
                pq.write_table(
                    table,
                    temporary,
                    compression="zstd",
                    use_dictionary=True,
                    write_statistics=True,
                    coerce_timestamps="us",
                )
                os.replace(temporary, path)
            digest = _sha256(path)
            persisted_starts = table.column("bar_start").to_pylist()
            parts.append(
                RawPagePart(
                    batch_id=batch_id,
                    path=path,
                    sha256=digest,
                    row_count=table.num_rows,
                    min_bar_at=min(persisted_starts),
                    max_bar_at=max(persisted_starts),
                )
            )
        return tuple(parts)

    def register_catalog_parts(self, parts: Sequence[RawPagePart]) -> None:
        """Register a whole provider batch in one DuckDB transaction."""

        self._register_catalog_parts(parts)

    def record_calendar(
        self,
        *,
        sessions: Sequence[tuple[date, str]],
        source_hash: str,
        dataset_revision: str,
    ) -> None:
        with self._ingestion_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                for session_date, day_type in sessions:
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO trading_days
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        [
                            session_date,
                            day_type,
                            source_hash,
                            dataset_revision,
                            datetime.now(UTC),
                        ],
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def record_volume_provenance(
        self, *, rows: Sequence[ProviderBar], task_id: str
    ) -> None:
        if not rows:
            return
        table = pa.Table.from_pylist(
            [
                {
                    "symbol": row.symbol,
                    "timeframe": row.timeframe.value,
                    "session_date": row.session_date,
                    "bar_start": row.bar_start,
                    "price_source": row.price_source,
                    "volume_source": row.volume_source,
                    "raw_volume": row.raw_volume,
                    "final_volume": row.volume,
                    "volume_scale": row.volume_scale,
                    "volume_estimated": row.volume_estimated,
                    "volume_observed_at": row.volume_observed_at,
                }
                for row in rows
            ]
        )
        with self._catalog_connection() as connection:
            connection.register("incoming_volume_provenance", table)
            connection.execute(
                """
                INSERT OR REPLACE INTO volume_provenance
                SELECT * FROM incoming_volume_provenance
                """
            )

    def record_yahoo_volume_evidence(
        self, observations: Sequence[YahooVolumeObservation]
    ) -> None:
        if not observations:
            return
        table = pa.Table.from_pylist(
            [
                {
                    "symbol": row.symbol,
                    "session_date": row.session_date,
                    "volume": row.volume,
                    "observed_at": row.observed_at,
                    "available_at": row.available_at,
                }
                for row in observations
            ]
        )
        with self._catalog_connection() as connection:
            connection.register("incoming_yahoo_volume_evidence", table)
            connection.execute(
                """
                INSERT OR REPLACE INTO yahoo_volume_evidence
                SELECT * FROM incoming_yahoo_volume_evidence
                """
            )

    def trading_days(self, start: date, end: date) -> dict[date, str]:
        with self._ingestion_connection() as connection:
            rows = connection.execute(
                """
                SELECT session_date, day_type FROM trading_days
                WHERE session_date BETWEEN ? AND ? ORDER BY session_date
                """,
                [start, end],
            ).fetchall()
        return {row[0]: str(row[1]) for row in rows}

    def write_rehab_snapshot(
        self,
        *,
        symbol: str,
        frame: object,
        observed_at: datetime,
        dataset_revision: str,
    ) -> RehabSnapshot:
        """Persist one immutable retrospective OpenD rehab observation."""

        if not isinstance(frame, pd.DataFrame):
            raise TypeError("OpenD rehab response must be a pandas DataFrame")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("rehab observed_at must be timezone-aware")
        raw = frame.copy()
        raw.insert(0, "symbol", symbol)
        raw["observed_at"] = observed_at
        raw["dataset_revision"] = dataset_revision
        table = pa.Table.from_pandas(raw, preserve_index=False)
        content = table.to_pydict()
        content_hash = hashlib.sha256(
            json.dumps(
                content, default=str, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        snapshot_id = (
            f"rehab-{symbol.replace('.', '-')}-{observed_at:%Y%m%dT%H%M%SZ}-"
            f"{content_hash[:12]}"
        )
        directory = (
            self.paths.raw
            / "rehab"
            / symbol
            / f"{observed_at:%Y}"
            / f"{observed_at:%m}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        physical_id = hashlib.sha256(snapshot_id.encode("utf-8")).hexdigest()[:24]
        path = directory / f"{physical_id}.parquet"
        if not path.exists():
            temporary = path.with_name(f".{physical_id}.tmp")
            pq.write_table(table, temporary, compression="zstd", use_dictionary=True)
            os.replace(temporary, path)
        digest = _sha256(path)
        rows = _normalize_rehab_rows(symbol, frame, observed_at, snapshot_id)
        normalized = pa.Table.from_pylist(rows, schema=REHAB_OBSERVATION_SCHEMA)
        with self._catalog_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                existing = connection.execute(
                    "SELECT sha256 FROM rehab_snapshots WHERE snapshot_id = ?",
                    [snapshot_id],
                ).fetchone()
                if existing is not None and existing[0] != digest:
                    raise RuntimeError("immutable_rehab_snapshot_conflict")
                connection.execute(
                    "INSERT OR IGNORE INTO rehab_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        snapshot_id,
                        symbol,
                        observed_at,
                        len(rows),
                        str(path),
                        digest,
                        dataset_revision,
                    ],
                )
                if normalized.num_rows:
                    connection.register("rehab_input", normalized)
                    connection.execute(
                        "INSERT OR IGNORE INTO rehab_observations SELECT * FROM rehab_input"
                    )
                    connection.unregister("rehab_input")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return RehabSnapshot(
            snapshot_id=snapshot_id,
            symbol=symbol,
            observed_at=observed_at,
            row_count=len(rows),
            path=path,
            sha256=digest,
        )

    def latest_watermark(self, symbol: str, timeframe: Timeframe) -> datetime | None:
        with self._catalog_connection() as connection:
            row = connection.execute(
                """
                SELECT MAX(bar_start) FROM market_bar_observations
                WHERE symbol = ? AND timeframe = ?
                """,
                [symbol, timeframe.value],
            ).fetchone()
        return row[0] if row is not None else None

    def coverage(self) -> list[dict[str, object]]:
        with self._catalog_connection() as connection:
            rows = connection.execute(
                """
                SELECT symbol, timeframe, COUNT(*) AS row_count,
                       MIN(session_date) AS min_session_date,
                       MAX(session_date) AS max_session_date,
                       MAX(ingested_at) AS last_ingested_at
                FROM canonical_market_bars
                GROUP BY symbol, timeframe ORDER BY symbol, timeframe
                """
            ).fetchall()
        return [
            {
                "symbol": row[0],
                "timeframe": row[1],
                "row_count": int(row[2]),
                "min_session_date": row[3].isoformat(),
                "max_session_date": row[4].isoformat(),
                "last_ingested_at": row[5].isoformat(),
            }
            for row in rows
        ]

    def common_daily_cutoff(self, symbols: Sequence[str]) -> tuple[date | None, int]:
        """Return the last daily session completed by every requested symbol."""

        expected = tuple(dict.fromkeys(symbols))
        if not expected:
            return None, 0
        with self._catalog_connection() as connection:
            rows = connection.execute(
                """
                SELECT symbol, MAX(session_date) AS max_session_date
                FROM canonical_market_bars
                WHERE timeframe = '1d' AND symbol IN (
                    SELECT * FROM UNNEST(?::VARCHAR[])
                )
                GROUP BY symbol
                """,
                [list(expected)],
            ).fetchall()
        maxima = {str(row[0]): row[1] for row in rows if row[1] is not None}
        completed = len(maxima)
        if completed != len(expected):
            return None, completed
        return min(maxima.values()), completed

    def lag_sessions(self, actual: date | None, expected: date) -> int | None:
        if actual is None:
            return None
        if actual >= expected:
            return 0
        with self._ingestion_connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM trading_days
                WHERE session_date > ? AND session_date <= ?
                """,
                [actual, expected],
            ).fetchone()
        return int(row[0]) if row is not None else None

    def latest_task_states(self) -> list[dict[str, object]]:
        with self._ingestion_connection() as connection:
            rows = connection.execute(
                """
                SELECT symbol, timeframe, status, watermark, error_code, updated_at
                FROM history_tasks
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY symbol, timeframe ORDER BY updated_at DESC
                ) = 1
                ORDER BY symbol, timeframe
                """
            ).fetchall()
        return [
            {
                "symbol": str(row[0]),
                "timeframe": str(row[1]),
                "status": str(row[2]),
                "watermark": row[3],
                "error_code": str(row[4]) if row[4] is not None else None,
                "updated_at": row[5],
            }
            for row in rows
        ]

    def rehab_coverage(self) -> list[dict[str, object]]:
        with self._catalog_connection() as connection:
            rows = connection.execute(
                """
                SELECT symbol, MAX(observed_at) AS observed_at,
                       COUNT(DISTINCT raw_batch_id) AS snapshot_count,
                       COUNT(*) AS action_count
                FROM rehab_observations
                GROUP BY symbol ORDER BY symbol
                """
            ).fetchall()
            empty_rows = connection.execute(
                """
                SELECT symbol, MAX(observed_at), COUNT(*)
                FROM rehab_snapshots GROUP BY symbol ORDER BY symbol
                """
            ).fetchall()
        actions = {str(row[0]): int(row[3]) for row in rows}
        return [
            {
                "symbol": str(row[0]),
                "observed_at": row[1],
                "snapshot_count": int(row[2]),
                "action_count": actions.get(str(row[0]), 0),
            }
            for row in empty_rows
        ]

    def record_health(self, payload: dict[str, object]) -> None:
        observed_at = datetime.now(UTC)
        persisted = {
            "observed_at": observed_at.isoformat(),
            "payload": payload,
        }
        with self._ingestion_connection() as connection:
            connection.execute(
                """
                INSERT INTO opend_health VALUES (?, ?::JSON)
                """,
                [observed_at, json.dumps(payload, separators=(",", ":"))],
            )
        _atomic_json(self.paths.health_mirror, persisted)

    def latest_health(self) -> dict[str, object] | None:
        try:
            mirrored = json.loads(self.paths.health_mirror.read_text(encoding="utf-8"))
            payload = mirrored.get("payload")
            if isinstance(payload, dict):
                return cast(dict[str, object], payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
        with self._ingestion_connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json::VARCHAR FROM opend_health
                ORDER BY observed_at DESC LIMIT 1
                """
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def record_instrument_metadata(
        self,
        rows: Sequence[dict[str, object]],
        *,
        observed_at: datetime,
        provider: str = "opend",
    ) -> None:
        """Persist one atomic, forward-only provider metadata observation."""

        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("instrument metadata observed_at must be timezone-aware")
        _atomic_json(
            self.paths.instrument_metadata,
            {
                "metadata_version": (
                    f"{provider}-static-{observed_at:%Y%m%dT%H%M%SZ}"
                ),
                "observed_at": observed_at.astimezone(UTC).isoformat(),
                "rows": list(rows),
            },
        )

    def latest_instrument_metadata(self) -> dict[str, dict[str, object]]:
        """Return the latest validated metadata keyed by symbol."""

        try:
            payload = json.loads(
                self.paths.instrument_metadata.read_text(encoding="utf-8")
            )
            observed_at = payload["observed_at"]
            metadata_version = payload["metadata_version"]
            rows = payload["rows"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return {}
        if (
            not isinstance(observed_at, str)
            or not isinstance(metadata_version, str)
            or not isinstance(rows, list)
        ):
            return {}
        result: dict[str, dict[str, object]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = row.get("symbol")
            name = row.get("name")
            if not isinstance(symbol, str) or not isinstance(name, str) or not name:
                continue
            result[symbol] = {
                **row,
                "observed_at": observed_at,
                "metadata_version": metadata_version,
            }
        return result

    def _register_catalog_parts(self, parts: Sequence[RawPagePart]) -> None:
        if not parts:
            return
        registered_at = datetime.now(UTC)
        metadata = pa.Table.from_pylist(
            [
                {
                    "batch_id": part.batch_id,
                    "path": str(part.path),
                    "sha256": part.sha256,
                    "registered_at": registered_at,
                }
                for part in parts
            ]
        )
        with self._catalog_connection() as connection:
            connection.register("incoming_raw_bar_files", metadata)
            connection.execute("BEGIN TRANSACTION")
            try:
                conflict = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM raw_bar_files existing
                    JOIN incoming_raw_bar_files incoming USING (batch_id)
                    WHERE existing.sha256 <> incoming.sha256
                    """
                ).fetchone()
                if conflict is not None and int(conflict[0]) > 0:
                    raise RuntimeError("catalog_raw_batch_conflict")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO raw_bar_files
                    SELECT * FROM incoming_raw_bar_files
                    """
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO market_bar_observations
                    SELECT * FROM read_parquet(?)
                    """,
                    [[str(part.path) for part in parts]],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def _initialize_ingestion(self) -> None:
        with self._ingestion_connection() as connection:
            existing_task_columns = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'history_tasks'
                    """
                ).fetchall()
            }
            connection.execute(
                """
                SET TimeZone = 'UTC';
                CREATE TABLE IF NOT EXISTS history_tasks (
                    task_id VARCHAR PRIMARY KEY,
                    symbol VARCHAR NOT NULL,
                    timeframe VARCHAR NOT NULL,
                    requested_start DATE NOT NULL,
                    requested_end DATE NOT NULL,
                    priority VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    last_page_index INTEGER NOT NULL,
                    next_page_token VARCHAR,
                    watermark TIMESTAMPTZ,
                    pagination_chain INTEGER NOT NULL DEFAULT 0,
                    dataset_revision VARCHAR NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    error_code VARCHAR,
                    updated_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history_pages (
                    page_id VARCHAR PRIMARY KEY,
                    task_id VARCHAR NOT NULL,
                    page_index INTEGER NOT NULL,
                    next_page_token VARCHAR,
                    watermark TIMESTAMPTZ NOT NULL,
                    parts_json JSON NOT NULL,
                    committed_at TIMESTAMPTZ NOT NULL,
                    UNIQUE(task_id, page_index)
                );
                CREATE TABLE IF NOT EXISTS history_page_chains (
                    task_id VARCHAR NOT NULL,
                    pagination_chain INTEGER NOT NULL,
                    page_index INTEGER NOT NULL,
                    next_page_token VARCHAR,
                    watermark TIMESTAMPTZ NOT NULL,
                    committed_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY(task_id, pagination_chain, page_index)
                );
                CREATE TABLE IF NOT EXISTS ingestion_schema_migrations (
                    migration_name VARCHAR PRIMARY KEY,
                    completed_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trading_days (
                    session_date DATE PRIMARY KEY,
                    day_type VARCHAR NOT NULL,
                    source_hash VARCHAR NOT NULL,
                    dataset_revision VARCHAR NOT NULL,
                    observed_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE IF NOT EXISTS opend_health (
                    observed_at TIMESTAMPTZ NOT NULL,
                    payload_json JSON NOT NULL
                );
                """
            )
            connection.execute(
                """
                ALTER TABLE history_tasks
                ADD COLUMN IF NOT EXISTS pagination_chain INTEGER DEFAULT 0
                """
            )
            connection.execute(
                """
                UPDATE history_tasks
                SET pagination_chain = 0
                WHERE pagination_chain IS NULL
                """
            )
            migration_complete = (
                connection.execute(
                    """
                    SELECT 1
                    FROM ingestion_schema_migrations
                    WHERE migration_name = ?
                    """,
                    [_PAGINATION_CHAIN_MIGRATION],
                ).fetchone()
                is not None
            )
            if existing_task_columns and not migration_complete:
                connection.execute("BEGIN TRANSACTION")
                try:
                    connection.execute(
                        """
                        UPDATE history_tasks
                        SET status = 'planned',
                            pagination_chain = COALESCE(pagination_chain, 0) + 1,
                            last_page_index = 0,
                            next_page_token = NULL,
                            watermark = NULL,
                            error_code = 'legacy_pagination_chain_unverifiable',
                            updated_at = ?
                        WHERE status <> 'committed'
                          AND (
                            last_page_index > 0
                            OR next_page_token IS NOT NULL
                            OR watermark IS NOT NULL
                          )
                        """,
                        [datetime.now(UTC)],
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO history_page_chains
                        SELECT task_id, 0, page_index, next_page_token, watermark, committed_at
                        FROM history_pages
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO ingestion_schema_migrations VALUES (?, ?)
                        """,
                        [_PAGINATION_CHAIN_MIGRATION, datetime.now(UTC)],
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
            elif not migration_complete:
                connection.execute(
                    """
                    INSERT INTO ingestion_schema_migrations VALUES (?, ?)
                    """,
                    [_PAGINATION_CHAIN_MIGRATION, datetime.now(UTC)],
                )

    def _initialize_catalog(self) -> None:
        columns = ",\n".join(
            f"{field.name} {_duckdb_type(field.type)}"
            + (" NOT NULL" if not field.nullable else "")
            for field in MARKET_BAR_SCHEMA
        )
        with self._catalog_connection() as connection:
            connection.execute("SET TimeZone = 'UTC'")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_bar_files (
                    batch_id VARCHAR PRIMARY KEY,
                    path VARCHAR NOT NULL,
                    sha256 VARCHAR NOT NULL,
                    registered_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS volume_provenance (
                    symbol VARCHAR NOT NULL,
                    timeframe VARCHAR NOT NULL,
                    session_date DATE NOT NULL,
                    bar_start TIMESTAMPTZ NOT NULL,
                    price_source VARCHAR NOT NULL,
                    volume_source VARCHAR NOT NULL,
                    raw_volume BIGINT,
                    final_volume BIGINT NOT NULL,
                    volume_scale DOUBLE,
                    volume_estimated BOOLEAN NOT NULL,
                    volume_observed_at TIMESTAMPTZ,
                    PRIMARY KEY(symbol, timeframe, bar_start)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS yahoo_volume_evidence (
                    symbol VARCHAR NOT NULL,
                    session_date DATE NOT NULL,
                    volume BIGINT NOT NULL,
                    observed_at TIMESTAMPTZ NOT NULL,
                    available_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY(symbol, session_date)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rehab_snapshots (
                    snapshot_id VARCHAR PRIMARY KEY,
                    symbol VARCHAR NOT NULL,
                    observed_at TIMESTAMPTZ NOT NULL,
                    row_count BIGINT NOT NULL,
                    path VARCHAR NOT NULL,
                    sha256 VARCHAR NOT NULL,
                    dataset_revision VARCHAR NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rehab_observations (
                    symbol VARCHAR NOT NULL,
                    ex_div_date DATE NOT NULL,
                    split_ratio DOUBLE,
                    per_cash_div DOUBLE,
                    special_dividend DOUBLE,
                    forward_adj_factor_a DOUBLE,
                    forward_adj_factor_b DOUBLE,
                    backward_adj_factor_a DOUBLE,
                    backward_adj_factor_b DOUBLE,
                    observed_at TIMESTAMPTZ NOT NULL,
                    raw_batch_id VARCHAR NOT NULL,
                    PRIMARY KEY(symbol, ex_div_date, observed_at)
                );
                CREATE OR REPLACE VIEW latest_rehab_observations AS
                SELECT * EXCLUDE(snapshot_rank) FROM (
                    SELECT *, DENSE_RANK() OVER (
                        PARTITION BY symbol ORDER BY observed_at DESC
                    ) AS snapshot_rank
                    FROM rehab_observations
                ) WHERE snapshot_rank = 1;
                """
            )
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS market_bar_observations (
                    {columns},
                    PRIMARY KEY(symbol, timeframe, session, bar_start, raw_batch_id)
                )
                """
            )
            connection.execute(
                """
                CREATE OR REPLACE VIEW canonical_market_bars AS
                SELECT * EXCLUDE(row_rank) FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY symbol, timeframe, session, bar_start
                        ORDER BY ingested_at DESC, raw_batch_id DESC
                    ) AS row_rank
                    FROM market_bar_observations
                ) WHERE row_rank = 1
                """
            )

    def _ingestion_connection(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.paths.ingestion))

    def _catalog_connection(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.paths.catalog))


def _market_bar_table(rows: Sequence[MarketBar]) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "symbol": row.symbol,
                "timeframe": row.timeframe.value,
                "session": row.session.value,
                "session_date": row.session_date,
                "bar_start": row.bar_start,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "turnover": row.turnover,
                "source_available_at": row.source_available_at,
                "ingested_at": row.ingested_at,
                "raw_batch_id": row.raw_batch_id,
                "schema_version": row.schema_version,
                "dataset_revision": row.dataset_revision,
                "revision_no": row.revision_no,
            }
            for row in rows
        ],
        schema=MARKET_BAR_SCHEMA,
    )


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)




REHAB_OBSERVATION_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("ex_div_date", pa.date32(), nullable=False),
        pa.field("split_ratio", pa.float64()),
        pa.field("per_cash_div", pa.float64()),
        pa.field("special_dividend", pa.float64()),
        pa.field("forward_adj_factor_a", pa.float64()),
        pa.field("forward_adj_factor_b", pa.float64()),
        pa.field("backward_adj_factor_a", pa.float64()),
        pa.field("backward_adj_factor_b", pa.float64()),
        pa.field("observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("raw_batch_id", pa.string(), nullable=False),
    ]
)


def _normalize_rehab_rows(
    symbol: str,
    frame: pd.DataFrame,
    observed_at: datetime,
    snapshot_id: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for raw in frame.to_dict(orient="records"):
        parsed = pd.to_datetime(raw.get("ex_div_date"), errors="coerce")
        if pd.isna(parsed):
            raise ValueError("OpenD rehab row has no ex-dividend date")
        rows.append(
            {
                "symbol": symbol,
                "ex_div_date": parsed.date(),
                "split_ratio": _optional_float(raw.get("split_ratio")),
                "per_cash_div": _optional_float(raw.get("per_cash_div")),
                "special_dividend": _optional_float(raw.get("special_dividend")),
                "forward_adj_factor_a": _optional_float(raw.get("forward_adj_factorA")),
                "forward_adj_factor_b": _optional_float(raw.get("forward_adj_factorB")),
                "backward_adj_factor_a": _optional_float(
                    raw.get("backward_adj_factorA")
                ),
                "backward_adj_factor_b": _optional_float(
                    raw.get("backward_adj_factorB")
                ),
                "observed_at": observed_at,
                "raw_batch_id": snapshot_id,
            }
        )
    return rows


def _optional_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    converted = float(cast(Any, value))
    if not (float("-inf") < converted < float("inf")):
        raise ValueError("OpenD rehab value must be finite")
    return converted


def _duckdb_type(value: pa.DataType) -> str:
    if pa.types.is_string(value):
        return "VARCHAR"
    if pa.types.is_date32(value):
        return "DATE"
    if pa.types.is_timestamp(value):
        return "TIMESTAMPTZ"
    if pa.types.is_float64(value):
        return "DOUBLE"
    if pa.types.is_int64(value):
        return "BIGINT"
    if pa.types.is_int32(value):
        return "INTEGER"
    raise TypeError(f"unsupported market-bar Arrow type: {value}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def task_id_for(*, symbol: str, timeframe: Timeframe, start: date, end: date) -> str:
    payload = f"{symbol}|{timeframe.value}|{start.isoformat()}|{end.isoformat()}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"live-{symbol.replace('.', '-')}-{timeframe.value}-{digest}"


__all__ = [
    "LiveStorage",
    "LiveTaskState",
    "RawPagePart",
    "RehabSnapshot",
    "task_id_for",
]
