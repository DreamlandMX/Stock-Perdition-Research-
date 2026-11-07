"""Read local observation vintages; never infer knowledge from an event date."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import duckdb
import pandas as pd

from stock_prediction.live.config import LivePaths


@dataclass(frozen=True)
class LiveObservationSelection:
    bars: pd.DataFrame
    rehab: pd.DataFrame
    rehab_snapshots: pd.DataFrame
    diagnostics: dict[str, object]


def load_live_observations_as_of(
    paths: LivePaths, *, symbols: tuple[str, ...], cutoff: datetime
) -> LiveObservationSelection:
    """Read daily/hourly revisions locally observed and available by ``cutoff``.

    This is one catalog vintage, NOT per-row historical PIT feature/label replay.
    Backfilled bars remain unavailable before ingestion even if their bar date
    and source availability are years earlier. Missing observations are returned
    explicitly, without latest-view fallback. Diagnostics cover known catalog
    keys only, not missing sessions, unknown events, or universe survivorship.
    Callers must inspect coverage before constructing features or labels.

    Rehab selection uses whole snapshots, including empty snapshots/deletions.
    ``observed_at`` is local collection time, not a provider publication time.
    No paths, tables, artifacts, or latest pointers are created or updated.
    """
    if pd.isna(cutoff) or cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("observation cutoff must be timezone-aware")
    cutoff = cutoff.astimezone(UTC)
    if not symbols:
        raise ValueError("observation symbols must not be empty")
    scope = list(dict.fromkeys(symbols))
    with duckdb.connect(str(paths.catalog), read_only=True) as connection:
        bars = connection.execute(
            """
            SELECT * FROM market_bar_observations
            WHERE symbol IN (SELECT * FROM UNNEST(?))
              AND timeframe IN ('1d', '60m')
              AND source_available_at <= ? AND ingested_at <= ?
              AND bar_start <= ?
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol, timeframe, session, bar_start
                ORDER BY ingested_at DESC, raw_batch_id DESC
            ) = 1
            ORDER BY symbol, timeframe, session, bar_start
            """,
            [scope, cutoff, cutoff, cutoff],
        ).fetch_df()
        missing_bars = connection.execute(
            """
            SELECT timeframe, COUNT(*) FROM (
                SELECT symbol, timeframe, session, bar_start
                FROM market_bar_observations
                WHERE symbol IN (SELECT * FROM UNNEST(?))
                  AND timeframe IN ('1d', '60m') AND bar_start <= ?
                GROUP BY symbol, timeframe, session, bar_start
                HAVING NOT COALESCE(BOOL_OR(
                    source_available_at <= ? AND ingested_at <= ?
                ), FALSE)
            ) GROUP BY timeframe ORDER BY timeframe
            """,
            [scope, cutoff, cutoff, cutoff],
        ).fetchall()
        snapshots = connection.execute(
            """
            SELECT * FROM rehab_snapshots
            WHERE symbol IN (SELECT * FROM UNNEST(?)) AND observed_at <= ?
            QUALIFY DENSE_RANK() OVER (
                PARTITION BY symbol ORDER BY observed_at DESC
            ) = 1
            ORDER BY symbol
            """,
            [scope, cutoff],
        ).fetch_df()
        if snapshots["symbol"].duplicated().any():
            raise ValueError("ambiguous_rehab_snapshot_at_cutoff")
        rehab = connection.execute(
            """
            SELECT * FROM rehab_observations
            WHERE raw_batch_id IN (SELECT * FROM UNNEST(?))
            ORDER BY symbol, ex_div_date
            """,
            [snapshots["snapshot_id"].tolist()],
        ).fetch_df()
    for snapshot in snapshots.itertuples(index=False):
        rows = rehab[rehab["raw_batch_id"].eq(snapshot.snapshot_id)]
        if (
            len(rows) != snapshot.row_count
            or not rows["symbol"].eq(snapshot.symbol).all()
            or not rows["observed_at"].eq(snapshot.observed_at).all()
            or rows["ex_div_date"].duplicated().any()
        ):
            raise ValueError("incomplete_or_inconsistent_rehab_snapshot")
    return LiveObservationSelection(
        bars=bars,
        rehab=rehab,
        rehab_snapshots=snapshots,
        diagnostics={
            "observation_policy": "local_observation_cutoff",
            "observation_cutoff": cutoff.isoformat(),
            "strict_historical_pit": False,
            "known_bar_keys_without_visible_revision": dict(missing_bars),
            "symbols_without_visible_daily_bars": sorted(
                set(scope) - set(bars.loc[bars["timeframe"].eq("1d"), "symbol"])
            ),
            "symbols_without_visible_rehab_snapshot": sorted(
                set(scope) - set(snapshots["symbol"])
            ),
            "coverage_basis": "known_catalog_keys_only",
        },
    )
