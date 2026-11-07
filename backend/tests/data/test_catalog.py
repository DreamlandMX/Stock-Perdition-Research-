from datetime import date
from pathlib import Path

import pytest

from stock_prediction.contracts.ingestion import DataQualityReport
from stock_prediction.contracts.market_data import MarketBar, MarketSession, Timeframe
from stock_prediction.data.catalog import CatalogConflictError, DuckDbCatalog
from stock_prediction.data.raw_store import RawBatchStore


SHA = "c" * 64


def committed_batch(
    root: Path,
    *,
    batch_id: str,
    close: float = 624.0,
    created_at: str = "2026-07-10T20:01:00Z",
    committed_at: str = "2026-07-10T20:01:01Z",
):
    store = RawBatchStore(root)
    row = MarketBar(
        symbol="US.SPY",
        timeframe=Timeframe.DAY_1,
        session=MarketSession.REGULAR,
        session_date=date(2026, 7, 10),
        bar_start="2026-07-10T13:30:00Z",
        open=620.0,
        high=max(625.0, close),
        low=618.0,
        close=close,
        volume=50_000_000,
        turnover=31_100_000_000.0,
        source_available_at="2026-07-10T20:00:05Z",
        ingested_at=created_at,
        raw_batch_id=batch_id,
        schema_version="market-bar-v1",
        dataset_revision="dataset-r1",
        revision_no=1,
    )
    staged = store.stage_market_bars(
        [row],
        task_id=f"task-{batch_id}",
        batch_id=batch_id,
        request_start=date(2026, 7, 10),
        request_end=date(2026, 7, 10),
        request_fingerprint=SHA,
        opend_version="1007",
        sdk_version="10.8.6808",
        created_at=created_at,
    )
    report = DataQualityReport(
        report_id=f"quality-{batch_id}",
        batch_id=batch_id,
        checked_at=created_at,
        input_rows=1,
        valid_rows=1,
        quarantined_rows=0,
        quality_score=1.0,
        quarantined=False,
        issues=[],
    )
    manifest = store.commit(
        staged, quality_report=report, committed_at=committed_at
    )
    return store, manifest


def test_catalog_registration_is_transactional_and_idempotent(tmp_path: Path) -> None:
    store, manifest = committed_batch(tmp_path / "raw", batch_id="batch-1")
    catalog = DuckDbCatalog(tmp_path / "catalog.duckdb")

    assert catalog.register_committed_batch(store, manifest) is True
    assert catalog.register_committed_batch(store, manifest) is False

    assert catalog.registered_batch_count() == 1
    assert catalog.manifests() == [manifest]
    summary = catalog.quality_summary()
    assert summary == [
        {
            "dataset_revision": "dataset-r1",
            "batch_count": 1,
            "input_rows": 1,
            "valid_rows": 1,
            "quarantined_rows": 0,
            "average_quality_score": 1.0,
        }
    ]


def test_catalog_rejects_same_batch_id_with_different_evidence(tmp_path: Path) -> None:
    first_store, first = committed_batch(tmp_path / "raw-1", batch_id="batch-1")
    second_store, second = committed_batch(
        tmp_path / "raw-2", batch_id="batch-1", close=630.0
    )
    catalog = DuckDbCatalog(tmp_path / "catalog.duckdb")
    catalog.register_committed_batch(first_store, first)

    with pytest.raises(CatalogConflictError, match="batch ID"):
        catalog.register_committed_batch(second_store, second)

    assert catalog.registered_batch_count() == 1


def test_catalog_can_rehydrate_registration_from_cold_layer(tmp_path: Path) -> None:
    store, manifest = committed_batch(tmp_path / "raw", batch_id="batch-1")
    catalog = DuckDbCatalog(tmp_path / "fresh.duckdb")

    registered = catalog.rehydrate_from_raw(store)

    assert registered == 1
    assert catalog.manifests() == [manifest]
