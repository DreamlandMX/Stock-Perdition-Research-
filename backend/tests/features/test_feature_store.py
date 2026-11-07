"""Feature artifact store tests with a globally unique pytest module name."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import os

import pytest
import pyarrow as pa

import stock_prediction.features.store as store_module

from stock_prediction.contracts.research import ArtifactStatus, CalendarSource
from stock_prediction.features.core import build_core_feature_table
from stock_prediction.features.dag import FeatureCacheKey, FeatureInvalidation
from stock_prediction.features.store import (
    FeatureArtifactConflictError,
    FeatureArtifactCorruptError,
    FeatureArtifactInvalidatedError,
    FeatureArtifactStore,
)
from stock_prediction.research.primitives import MarketPrimitives


AS_OF = datetime(2026, 7, 10, 20, tzinfo=UTC)


def primitive() -> MarketPrimitives:
    return MarketPrimitives(
        symbol="US.AAPL",
        asset_group="stock",
        session_date=date(2026, 7, 10),
        source_max_available_at=AS_OF,
        source_max_ingested_at=AS_OF + timedelta(minutes=1),
        dataset_revision="dataset-r1",
        adjustment_revision="adjustment-r1",
        feature_set_version="core-v1",
        calendar_version="test-calendar-v1",
        calendar_hash="b" * 64,
        calendar_source=CalendarSource.SYNTHETIC_OFFLINE,
        anchor_close=100.0,
        return_1=0.01,
        return_5=0.03,
        return_20=0.08,
        prior_high_20=110.0,
        prior_low_20=90.0,
        atr20=4.0,
        channel_width20=0.20,
        channel_position20=0.50,
        efficiency_ratio20=0.40,
        normalized_slope20=0.002,
        trend_r2_20=0.60,
        realized_volatility20=0.25,
        downside_volatility20=0.15,
        max_drawdown20=-0.05,
        max_up_move20=0.03,
        ma20=98.0,
        ma50=95.0,
        distance_ma20=100.0 / 98.0 - 1.0,
        distance_ma50=100.0 / 95.0 - 1.0,
        bollinger_bandwidth20=0.10,
        atr_percent20=0.04,
        volume_ratio20=1.20,
        turnover_ratio20=1.10,
        data_coverage20=1.0,
        missing_session_count20=0,
        valid_core=True,
    )


def key() -> FeatureCacheKey:
    return FeatureCacheKey(
        node_name="core",
        dataset_revision="dataset-r1",
        feature_set_version="core-v1",
        symbol="US.AAPL",
        as_of=AS_OF,
        horizon_dependent=False,
        horizon=None,
    )


def table():
    return build_core_feature_table(
        [primitive()],
        feature_set_version="core-v1",
        universe_version="universe-v1",
    )


def table_with_values_hash():
    return table().append_column(
        "feature_values_hash", pa.array(["d" * 64])
    )


def test_round_trip_and_idempotent_cache_hit(tmp_path: Path) -> None:
    store = FeatureArtifactStore(tmp_path)
    first = store.put(
        key(),
        table(),
        input_hash="a" * 64,
        dependency_hashes={"primitives": "b" * 64},
        source_max_available_at=AS_OF,
        generated_at=AS_OF + timedelta(days=1),
    )
    second = store.put(
        key(),
        table(),
        input_hash="a" * 64,
        dependency_hashes={"primitives": "b" * 64},
        source_max_available_at=AS_OF,
        generated_at=AS_OF,
    )
    loaded_table, loaded_artifact = store.get(key())

    assert first == second == loaded_artifact
    assert first.status is ArtifactStatus.CURRENT
    assert len(first.content_hash) == 64
    assert loaded_table.equals(table())


def test_same_cache_key_cannot_be_silently_overwritten(tmp_path: Path) -> None:
    store = FeatureArtifactStore(tmp_path)
    store.put(
        key(),
        table(),
        input_hash="a" * 64,
        dependency_hashes={"primitives": "b" * 64},
        source_max_available_at=AS_OF,
        generated_at=AS_OF,
    )

    with pytest.raises(FeatureArtifactConflictError, match="already exists"):
        store.put(
            key(),
            table(),
            input_hash="c" * 64,
            dependency_hashes={"primitives": "b" * 64},
            source_max_available_at=AS_OF,
            generated_at=AS_OF,
        )


def test_corrupted_parquet_is_rejected_by_content_hash(tmp_path: Path) -> None:
    store = FeatureArtifactStore(tmp_path)
    store.put(
        key(),
        table(),
        input_hash="a" * 64,
        dependency_hashes={"primitives": "b" * 64},
        source_max_available_at=AS_OF,
        generated_at=AS_OF,
    )
    parquet_path = next(tmp_path.rglob("artifact.parquet"))
    parquet_path.write_bytes(parquet_path.read_bytes() + b"corruption")

    with pytest.raises(FeatureArtifactCorruptError, match="content hash"):
        store.get(key())


def test_metadata_cannot_claim_values_hash_absent_from_parquet(tmp_path: Path) -> None:
    store = FeatureArtifactStore(tmp_path)
    store.put(
        key(),
        table_with_values_hash(),
        input_hash="a" * 64,
        dependency_hashes={"primitives": "b" * 64},
        source_max_available_at=AS_OF,
        generated_at=AS_OF,
    )
    metadata_path = next(tmp_path.rglob("artifact.json"))
    payload = json.loads(metadata_path.read_text("utf-8"))
    payload["artifact"]["feature_values_hashes"] = ["e" * 64]
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FeatureArtifactCorruptError, match="values hash"):
        store.get(key())


def test_metadata_cannot_understate_stored_source_maxima(tmp_path: Path) -> None:
    store = FeatureArtifactStore(tmp_path)
    store.put(
        key(),
        table(),
        input_hash="a" * 64,
        dependency_hashes={"primitives": "b" * 64},
        source_max_available_at=AS_OF,
        generated_at=AS_OF,
    )
    metadata_path = next(tmp_path.rglob("artifact.json"))
    payload = json.loads(metadata_path.read_text("utf-8"))
    payload["artifact"]["source_max_ingested_at"] = (
        AS_OF + timedelta(minutes=2)
    ).isoformat().replace("+00:00", "Z")
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FeatureArtifactCorruptError, match="source maxima"):
        store.get(key())


def test_invalidation_is_append_only_and_blocks_cache_reads(tmp_path: Path) -> None:
    store = FeatureArtifactStore(tmp_path)
    artifact = store.put(
        key(),
        table(),
        input_hash="a" * 64,
        dependency_hashes={"primitives": "b" * 64},
        source_max_available_at=AS_OF,
        generated_at=AS_OF,
    )

    invalidated = store.invalidate(
        key(),
        affected_start=AS_OF,
        affected_end=AS_OF,
        reason="upstream_revision",
    )

    assert artifact.status is ArtifactStatus.CURRENT
    assert invalidated.status is ArtifactStatus.INVALIDATED
    with pytest.raises(FeatureArtifactInvalidatedError, match="invalidated"):
        store.get(key())
    with pytest.raises(FeatureArtifactConflictError, match="invalidation"):
        store.invalidate(
            key(),
            affected_start=AS_OF,
            affected_end=AS_OF,
            reason="different_reason",
        )
    with pytest.raises(FeatureArtifactInvalidatedError, match="cannot be reused"):
        store.put(
            key(),
            table(),
            input_hash="a" * 64,
            dependency_hashes={"primitives": "b" * 64},
            source_max_available_at=AS_OF,
            generated_at=AS_OF + timedelta(days=2),
        )


def test_metadata_cannot_understate_table_source_time(tmp_path: Path) -> None:
    store = FeatureArtifactStore(tmp_path)
    with pytest.raises(FeatureArtifactConflictError, match="source_max"):
        store.put(
            key(),
            table(),
            input_hash="a" * 64,
            dependency_hashes={"primitives": "b" * 64},
            source_max_available_at=AS_OF - timedelta(seconds=1),
            generated_at=AS_OF,
        )


def test_partial_staging_directory_is_rebuilt_and_promoted_atomically(
    tmp_path: Path,
) -> None:
    store = FeatureArtifactStore(tmp_path)
    staging = (
        tmp_path
        / "artifacts"
        / key().cache_id[:2]
        / f"{key().cache_id}.staging"
    )
    staging.mkdir(parents=True)
    (staging / "artifact.parquet").write_bytes(b"partial")

    artifact = store.put(
        key(),
        table(),
        input_hash="a" * 64,
        dependency_hashes={"primitives": "b" * 64},
        source_max_available_at=AS_OF,
        generated_at=AS_OF,
    )

    assert artifact.status is ArtifactStatus.CURRENT
    assert not staging.exists()
    assert store.get(key())[1] == artifact


def test_normal_get_recovers_complete_staging_artifact(tmp_path: Path) -> None:
    store = FeatureArtifactStore(tmp_path)
    artifact = store.put(
        key(),
        table(),
        input_hash="a" * 64,
        dependency_hashes={"primitives": "b" * 64},
        source_max_available_at=AS_OF,
        generated_at=AS_OF,
    )
    final = tmp_path / "artifacts" / key().cache_id[:2] / key().cache_id
    staging = final.with_name(f"{final.name}.staging")
    final.rename(staging)

    loaded_table, loaded_artifact = store.get(key())

    assert final.exists()
    assert not staging.exists()
    assert loaded_table.equals(table())
    assert loaded_artifact == artifact


def test_normal_get_retries_transient_lock_when_cleaning_stale_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FeatureArtifactStore(tmp_path)
    artifact = store.put(
        key(),
        table(),
        input_hash="a" * 64,
        dependency_hashes={"primitives": "b" * 64},
        source_max_available_at=AS_OF,
        generated_at=AS_OF,
    )
    final = tmp_path / "artifacts" / key().cache_id[:2] / key().cache_id
    staging = final.with_name(f"{final.name}.staging")
    staging.mkdir()
    attempts = 0
    real_rmtree = store_module.shutil.rmtree

    def flaky_rmtree(path: Path) -> None:
        nonlocal attempts
        if path == staging and attempts == 0:
            attempts += 1
            raise PermissionError(5, "transient directory lock")
        real_rmtree(path)

    monkeypatch.setattr(store_module.shutil, "rmtree", flaky_rmtree)

    assert store.get(key())[1] == artifact
    assert attempts == 1
    assert not staging.exists()


def test_atomic_directory_promotion_retries_transient_windows_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_replace = os.replace
    attempts = 0

    def flaky_replace(source: Path, destination: Path) -> None:
        nonlocal attempts
        if str(source).endswith(".staging") and attempts == 0:
            attempts += 1
            raise PermissionError(5, "transient directory lock")
        real_replace(source, destination)

    monkeypatch.setattr(store_module.os, "replace", flaky_replace)
    store = FeatureArtifactStore(tmp_path)

    artifact = store.put(
        key(),
        table(),
        input_hash="a" * 64,
        dependency_hashes={"primitives": "b" * 64},
        source_max_available_at=AS_OF,
        generated_at=AS_OF,
    )

    assert attempts == 1
    assert artifact.status is ArtifactStatus.CURRENT
    assert store.get(key())[1] == artifact


def test_dag_invalidation_resolves_to_exact_store_keys(tmp_path: Path) -> None:
    store = FeatureArtifactStore(tmp_path)
    store.put(
        key(),
        table(),
        input_hash="a" * 64,
        dependency_hashes={"primitives": "b" * 64},
        source_max_available_at=AS_OF,
        generated_at=AS_OF,
    )
    invalidation = FeatureInvalidation(
        node_name="core",
        symbol="US.AAPL",
        dataset_revision="dataset-r1",
        feature_set_version="core-v1",
        affected_start=AS_OF.date(),
        affected_end=AS_OF.date(),
        horizons=None,
    )

    [artifact] = store.apply_invalidation(
        invalidation, reason="upstream_revision"
    )

    assert artifact.status is ArtifactStatus.INVALIDATED
    with pytest.raises(FeatureArtifactInvalidatedError):
        store.get(key())


def test_dag_invalidation_recovers_complete_staging_artifact(tmp_path: Path) -> None:
    store = FeatureArtifactStore(tmp_path)
    store.put(
        key(),
        table(),
        input_hash="a" * 64,
        dependency_hashes={"primitives": "b" * 64},
        source_max_available_at=AS_OF,
        generated_at=AS_OF,
    )
    final = tmp_path / "artifacts" / key().cache_id[:2] / key().cache_id
    staging = final.with_name(f"{final.name}.staging")
    final.rename(staging)
    invalidation = FeatureInvalidation(
        node_name="core",
        symbol="US.AAPL",
        dataset_revision="dataset-r1",
        feature_set_version="core-v1",
        affected_start=AS_OF.date(),
        affected_end=AS_OF.date(),
        horizons=None,
    )

    [artifact] = store.apply_invalidation(
        invalidation, reason="upstream_revision"
    )

    assert final.exists()
    assert not staging.exists()
    assert artifact.status is ArtifactStatus.INVALIDATED
