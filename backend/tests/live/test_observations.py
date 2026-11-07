from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq
import pytest

from stock_prediction.live.adjustments import build_point_in_time_adjusted_bars
from stock_prediction.live.config import LivePaths
from stock_prediction.live.features import LiveFeatureBuilder, load_live_observations_as_of
from stock_prediction.live.storage import LiveStorage


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


@pytest.fixture
def catalog(tmp_path: Path) -> LivePaths:
    paths = LivePaths.from_workspace(tmp_path)
    storage = LiveStorage(paths)
    with duckdb.connect(str(paths.catalog)) as connection:
        for timeframe in ("1d", "60m"):
            for session_date, close, ingested, batch in (
                ("2020-01-02", 100, "2026-01-01", "old"),
                ("2020-01-03", 50, "2026-01-01", "old"),
                ("2020-01-03", 55, "2026-02-01", "correction"),
            ):
                connection.execute(
                    """
                    INSERT INTO market_bar_observations
                    (symbol, timeframe, session, session_date, bar_start,
                     open, high, low, close, volume, turnover,
                     source_available_at, ingested_at, raw_batch_id,
                     schema_version, dataset_revision, revision_no)
                    VALUES ('US.SPY', ?, 'regular', ?, ?, ?, ?, ?, ?, 100, 10000,
                            ?, ?, ?, 'market-bar-v1', 'test', 1)
                    """,
                    [timeframe, session_date, _at(session_date), close, close,
                     close, close, _at(session_date + "T21:00:00"),
                     _at(ingested), batch],
                )
    for observed, factor in (("2026-01-01", 0.5), ("2026-02-01", 0.25)):
        storage.write_rehab_snapshot(
            symbol="US.SPY",
            frame=pd.DataFrame([{
                "ex_div_date": "2020-01-03", "split_ratio": factor,
                "forward_adj_factorA": factor, "forward_adj_factorB": 0.0,
            }]),
            observed_at=_at(observed), dataset_revision=paths.dataset_revision,
        )
    return paths


def test_cutoff_selects_old_bar_and_whole_rehab_revision(catalog: LivePaths) -> None:
    before = catalog.catalog.read_bytes()
    old = load_live_observations_as_of(
        catalog, symbols=("US.SPY",), cutoff=_at("2026-01-01")
    )
    new = load_live_observations_as_of(
        catalog, symbols=("US.SPY",), cutoff=_at("2026-02-01")
    )
    for timeframe in ("1d", "60m"):
        assert old.bars.loc[old.bars.timeframe.eq(timeframe), "close"].tolist() == [100, 50]
        assert new.bars.loc[new.bars.timeframe.eq(timeframe), "close"].tolist() == [100, 55]
    assert old.rehab.forward_adj_factor_a.tolist() == [0.5]
    assert new.rehab.forward_adj_factor_a.tolist() == [0.25]
    assert old.rehab.observed_at.iloc[0] == _at("2026-01-01")
    assert old.rehab.raw_batch_id.iloc[0] == old.rehab_snapshots.snapshot_id.iloc[0]
    adjusted = build_point_in_time_adjusted_bars(
        old.bars[old.bars.timeframe.eq("1d")], old.rehab
    )
    assert adjusted.close.tolist() == [100, 100]
    assert old.diagnostics["known_bar_keys_without_visible_revision"] == {}
    assert old.diagnostics["strict_historical_pit"] is False
    assert catalog.catalog.read_bytes() == before
    assert not (catalog.features / "LATEST.json").exists()


def test_backfill_has_no_observations_before_collection(catalog: LivePaths) -> None:
    result = load_live_observations_as_of(
        catalog, symbols=("US.SPY", "US.MISSING"), cutoff=_at("2020-01-04")
    )
    assert result.bars.empty and result.rehab.empty and result.rehab_snapshots.empty
    assert result.diagnostics["known_bar_keys_without_visible_revision"] == {"1d": 2, "60m": 2}
    assert result.diagnostics["symbols_without_visible_daily_bars"] == ["US.MISSING", "US.SPY"]
    assert result.diagnostics["symbols_without_visible_rehab_snapshot"] == ["US.MISSING", "US.SPY"]


def test_source_availability_also_gates_a_revision(catalog: LivePaths) -> None:
    with duckdb.connect(str(catalog.catalog)) as connection:
        connection.execute(
            "UPDATE market_bar_observations SET source_available_at = ? WHERE raw_batch_id = 'correction'",
            [_at("2026-04-01")],
        )
    result = load_live_observations_as_of(
        catalog, symbols=("US.SPY",), cutoff=_at("2026-03-01")
    )
    assert set(result.bars.raw_batch_id) == {"old"}


def test_empty_snapshot_removes_old_actions(catalog: LivePaths) -> None:
    LiveStorage(catalog).write_rehab_snapshot(
        symbol="US.SPY", frame=pd.DataFrame(), observed_at=_at("2026-03-01"),
        dataset_revision=catalog.dataset_revision,
    )
    result = load_live_observations_as_of(
        catalog, symbols=("US.SPY",), cutoff=_at("2026-03-01")
    )
    assert result.rehab.empty
    assert result.rehab_snapshots.row_count.tolist() == [0]
    assert result.diagnostics["symbols_without_visible_rehab_snapshot"] == []


def test_incomplete_snapshot_fails_closed(catalog: LivePaths) -> None:
    with duckdb.connect(str(catalog.catalog)) as connection:
        connection.execute("UPDATE rehab_snapshots SET row_count = 2")
    with pytest.raises(ValueError, match="incomplete_or_inconsistent"):
        load_live_observations_as_of(
            catalog, symbols=("US.SPY",), cutoff=_at("2026-02-01")
        )


@pytest.mark.parametrize("cutoff", [datetime(2026, 1, 1), pd.NaT])
def test_invalid_cutoff_rejected_before_opening_catalog(tmp_path: Path, cutoff: datetime) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        load_live_observations_as_of(paths, symbols=("US.SPY",), cutoff=cutoff)
    assert not paths.root.exists()


def test_default_build_retains_backfill_and_discloses_retrospective_history(catalog: LivePaths) -> None:
    artifact = LiveFeatureBuilder(catalog).build()
    table = pq.read_table(artifact.path)
    assert table.num_rows == 2
    assert table.column("raw_close").to_pylist() == [100, 55]
    assert table.schema.metadata[b"observation_policy"] == b"latest_revision_retrospective"
    assert table.schema.metadata[b"strict_historical_pit"] == b"false"
    assert b"pandas" in table.schema.metadata
