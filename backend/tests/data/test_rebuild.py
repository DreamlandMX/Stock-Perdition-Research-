from pathlib import Path

import pytest

from stock_prediction.data.catalog import DuckDbCatalog
from stock_prediction.data.rebuild import MarketDataRebuilder

from .test_catalog import committed_batch


def test_rebuild_assigns_revisions_and_collapses_exact_repeats(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    store, first = committed_batch(
        raw_root,
        batch_id="batch-1",
        created_at="2026-07-10T20:01:00Z",
        committed_at="2026-07-10T20:01:01Z",
    )
    _, repeated = committed_batch(
        raw_root,
        batch_id="batch-2",
        created_at="2026-07-10T20:02:00Z",
        committed_at="2026-07-10T20:02:01Z",
    )
    _, changed = committed_batch(
        raw_root,
        batch_id="batch-3",
        close=630.0,
        created_at="2026-07-10T20:03:00Z",
        committed_at="2026-07-10T20:03:01Z",
    )
    catalog = DuckDbCatalog(tmp_path / "catalog.duckdb")
    for manifest in (first, repeated, changed):
        catalog.register_committed_batch(store, manifest)

    result = MarketDataRebuilder(catalog, store).rebuild()

    assert result.raw_rows == 3
    assert result.observation_revisions == 2
    assert result.canonical_rows == 1
    observations = catalog.query_dicts(
        """
        SELECT revision_no, close, first_seen_at, last_seen_at, revision_reason
        FROM market_bar_observations
        ORDER BY revision_no
        """
    )
    assert [row["revision_no"] for row in observations] == [1, 2]
    assert [row["close"] for row in observations] == [624.0, 630.0]
    assert observations[0]["revision_reason"] == "initial_observation"
    assert observations[0]["first_seen_at"] != observations[0]["last_seen_at"]
    assert observations[1]["revision_reason"] == "source_value_changed"
    canonical = catalog.query_dicts(
        "SELECT close, revision_no, rebuild_id FROM market_bars_canonical"
    )
    assert canonical == [
        {"close": 630.0, "revision_no": 2, "rebuild_id": result.rebuild_id}
    ]


def test_rebuild_is_deterministic_for_the_same_manifest_set(tmp_path: Path) -> None:
    store, manifest = committed_batch(tmp_path / "raw", batch_id="batch-1")
    catalog = DuckDbCatalog(tmp_path / "catalog.duckdb")
    catalog.register_committed_batch(store, manifest)
    rebuilder = MarketDataRebuilder(catalog, store)

    first = rebuilder.rebuild()
    first_rows = catalog.query_dicts(
        "SELECT * FROM market_bars_canonical ORDER BY symbol, bar_start"
    )
    second = rebuilder.rebuild()
    second_rows = catalog.query_dicts(
        "SELECT * FROM market_bars_canonical ORDER BY symbol, bar_start"
    )

    assert second == first
    assert second_rows == first_rows


def test_fresh_duckdb_rebuilds_from_committed_cold_evidence(tmp_path: Path) -> None:
    store, manifest = committed_batch(tmp_path / "raw", batch_id="batch-1")
    first_catalog = DuckDbCatalog(tmp_path / "first.duckdb")
    first_catalog.register_committed_batch(store, manifest)
    expected = MarketDataRebuilder(first_catalog, store).rebuild()
    expected_rows = first_catalog.query_dicts("SELECT * FROM market_bars_canonical")

    fresh_catalog = DuckDbCatalog(tmp_path / "fresh.duckdb")
    assert fresh_catalog.rehydrate_from_raw(store) == 1
    rebuilt = MarketDataRebuilder(fresh_catalog, store).rebuild()

    assert rebuilt == expected
    assert fresh_catalog.query_dicts("SELECT * FROM market_bars_canonical") == expected_rows


def test_failed_rebuild_rolls_back_derived_tables_and_run_registration(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    store, first = committed_batch(raw_root, batch_id="batch-1")
    catalog = DuckDbCatalog(tmp_path / "catalog.duckdb")
    catalog.register_committed_batch(store, first)
    initial = MarketDataRebuilder(catalog, store).rebuild()
    initial_rows = catalog.query_dicts(
        "SELECT close, revision_no FROM market_bars_canonical"
    )
    _, changed = committed_batch(
        raw_root,
        batch_id="batch-2",
        close=630.0,
        created_at="2026-07-10T20:03:00Z",
        committed_at="2026-07-10T20:03:01Z",
    )
    catalog.register_committed_batch(store, changed)

    def fail(phase: str) -> None:
        if phase == "derived_tables_replaced":
            raise RuntimeError("simulated rebuild crash")

    with pytest.raises(RuntimeError, match="simulated rebuild crash"):
        MarketDataRebuilder(catalog, store, phase_hook=fail).rebuild()

    assert catalog.query_dicts(
        "SELECT close, revision_no FROM market_bars_canonical"
    ) == initial_rows
    assert catalog.query_dicts(
        "SELECT rebuild_id FROM rebuild_runs ORDER BY rebuild_id"
    ) == [{"rebuild_id": initial.rebuild_id}]
