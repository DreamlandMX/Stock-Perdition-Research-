from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import shutil

import duckdb
import pandas as pd
import pytest

from stock_prediction.live.artifact_references import build_artifact_reference
from stock_prediction.live.config import (
    ALPACA_SIP_DATASET_REVISION,
    ALPACA_SIP_FEATURE_SET_VERSION,
    LivePaths,
)
from stock_prediction.live.features import (
    LIVE_FEATURE_ARTIFACT_SCHEMA_VERSION,
    LiveFeatureArtifact,
    LiveFeatureBuilder,
    _add_market_and_relative_features,
    _artifact_versions,
    _build_symbol_features,
    load_latest_feature_artifact,
    rebuild_latest_feature_pointer,
)
from stock_prediction.universe.active import ACTIVE_ETFS


NOW = datetime(2026, 8, 24, 4, tzinfo=UTC)


@pytest.mark.parametrize("gap_close", [9.0, 11.0])
def test_zero_width_channel_position_is_missing(gap_close: float) -> None:
    close = [10.0] * 42
    close[20] = gap_close
    source = pd.DataFrame(
        {
            "session_date": pd.date_range("2020-05-01", periods=len(close)),
            "source_available_at": pd.date_range(
                "2020-05-01", periods=len(close), tz="UTC"
            ),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": range(100, 100 + len(close)),
            "quality_quarantined": False,
        }
    )

    result = _build_symbol_features(source)

    assert pd.isna(result.loc[20, "channel_position_20"])
    expected = (10.0 - min(10.0, gap_close)) / abs(10.0 - gap_close)
    assert result.loc[21, "channel_position_20"] == pytest.approx(expected)


def _write_feature_manifest(
    paths: LivePaths,
    *,
    input_hash: str,
    generated_at: datetime = NOW,
    corrupt_after_reference: bool = False,
) -> LiveFeatureArtifact:
    directory = paths.features / "live-features-v5" / input_hash[:16]
    target = directory / "features.parquet"
    directory.mkdir(parents=True, exist_ok=True)
    target.write_bytes(f"feature-payload-{input_hash}".encode())
    reference = build_artifact_reference(
        artifact_root=paths.features,
        artifact_id=input_hash,
        target=target,
        schema_version=LIVE_FEATURE_ARTIFACT_SCHEMA_VERSION,
        created_at=generated_at,
    )
    artifact = LiveFeatureArtifact(
        universe_version="seed100-v1",
        feature_set_version="live-features-v5",
        dataset_revision="dataset-v5",
        input_hash=input_hash,
        generated_at=generated_at,
        as_of=generated_at - timedelta(hours=1),
        row_count=100,
        path=target.resolve(),
        reference=reference,
    )
    (directory / "artifact.json").write_text(
        json.dumps(artifact.as_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if corrupt_after_reference:
        target.write_bytes(b"corrupt")
    return artifact


def test_country_etfs_are_marked_as_etfs_in_live_features() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["US.SPY", "US.EWJ", "US.AAPL"],
            "session_date": [date(2026, 8, 11)] * 3,
            "return_1": [0.01, 0.02, -0.01],
            "return_20": [0.03, 0.04, 0.01],
            "return_60": [0.05, 0.06, 0.02],
        }
    )

    result = _add_market_and_relative_features(frame, etfs=ACTIVE_ETFS).set_index(
        "symbol"
    )

    assert result.loc["US.SPY", "is_etf"] == 1.0
    assert result.loc["US.EWJ", "is_etf"] == 1.0
    assert result.loc["US.AAPL", "is_etf"] == 0.0


def test_alpaca_staged_paths_select_v3_artifact_versions(tmp_path: Path) -> None:
    dataset, feature_set = _artifact_versions(
        LivePaths.for_dataset(tmp_path, ALPACA_SIP_DATASET_REVISION)
    )

    assert dataset == ALPACA_SIP_DATASET_REVISION
    assert feature_set == ALPACA_SIP_FEATURE_SET_VERSION


def test_latest_feature_pointer_is_portable_across_workspace_copy(
    tmp_path: Path,
) -> None:
    original_workspace = tmp_path / "original"
    original_paths = LivePaths.from_workspace(original_workspace)
    original_paths.ensure()
    artifact = _write_feature_manifest(original_paths, input_hash="a" * 64)
    (original_paths.features / "LATEST.json").write_text(
        json.dumps(artifact.as_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    copied_workspace = tmp_path / "copied"
    shutil.copytree(original_workspace, copied_workspace)

    loaded = load_latest_feature_artifact(LivePaths.from_workspace(copied_workspace))

    assert (
        loaded.path
        == (
            copied_workspace
            / "var/live/features/live-features-v5"
            / ("a" * 16)
            / "features.parquet"
        ).resolve()
    )
    assert loaded.reference.relative_path.endswith("features.parquet")
    assert str(original_workspace.resolve()) not in json.dumps(loaded.as_dict())


def test_latest_feature_pointer_rejects_legacy_absolute_path(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path / "workspace")
    paths.ensure()
    outside = tmp_path / "old-worktree" / "features.parquet"
    outside.parent.mkdir()
    outside.write_bytes(b"legacy")
    (paths.features / "LATEST.json").write_text(
        json.dumps(
            {
                "input_hash": "a" * 64,
                "path": str(outside.resolve()),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="live_feature_artifact_path_invalid"):
        load_latest_feature_artifact(paths)


def test_latest_feature_pointer_can_be_rebuilt_from_valid_manifest(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    expected = _write_feature_manifest(paths, input_hash="b" * 64)

    rebuilt = rebuild_latest_feature_pointer(paths)

    assert rebuilt.input_hash == expected.input_hash
    assert load_latest_feature_artifact(paths) == rebuilt


def test_latest_feature_pointer_rebuild_recovers_copied_legacy_manifest(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path / "copied-worktree")
    paths.ensure()
    input_hash = "9" * 64
    directory = paths.features / "live-daily-features-v2" / input_hash[:16]
    directory.mkdir(parents=True)
    target = directory / "features.parquet"
    pd.DataFrame({"symbol": ["US.AAPL"]}).to_parquet(target, index=False)
    manifest = {
        "universe_version": "m1-seed-2026-07-11-v1",
        "feature_set_version": "live-daily-features-v2",
        "dataset_revision": "opend-live-market-bars-pit-adjusted-v2",
        "input_hash": input_hash,
        "generated_at": NOW.isoformat(),
        "as_of": (NOW - timedelta(hours=1)).isoformat(),
        "row_count": 1,
        "path": str(
            (
                tmp_path
                / "old-worktree/var/live/features/live-daily-features-v2"
                / input_hash[:16]
                / "features.parquet"
            ).resolve()
        ),
    }
    (directory / "artifact.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    rebuilt = rebuild_latest_feature_pointer(paths)

    assert rebuilt.path == target.resolve()
    assert rebuilt.input_hash == input_hash
    assert "path" not in json.loads((paths.features / "LATEST.json").read_text())
    assert load_latest_feature_artifact(paths) == rebuilt


def test_latest_feature_pointer_rebuild_rejects_ambiguous_newest_manifests(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    _write_feature_manifest(paths, input_hash="c" * 64)
    _write_feature_manifest(paths, input_hash="d" * 64)

    with pytest.raises(RuntimeError, match="live_feature_latest_ambiguous"):
        rebuild_latest_feature_pointer(paths)


def test_latest_feature_pointer_rebuild_never_selects_corrupt_hash(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    expected = _write_feature_manifest(
        paths,
        input_hash="e" * 64,
        generated_at=NOW - timedelta(hours=1),
    )
    _write_feature_manifest(
        paths,
        input_hash="f" * 64,
        generated_at=NOW,
        corrupt_after_reference=True,
    )

    rebuilt = rebuild_latest_feature_pointer(paths)

    assert rebuilt.input_hash == expected.input_hash


def test_feature_catalog_stores_relative_path_but_view_reads_resolved_file(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    input_hash = "1" * 64
    directory = paths.features / "live-features-v5" / input_hash[:16]
    target = directory / "features.parquet"
    directory.mkdir(parents=True)
    pd.DataFrame({"symbol": ["US.AAPL"]}).to_parquet(target, index=False)
    reference = build_artifact_reference(
        artifact_root=paths.features,
        artifact_id=input_hash,
        target=target,
        schema_version=LIVE_FEATURE_ARTIFACT_SCHEMA_VERSION,
        created_at=NOW,
    )
    artifact = LiveFeatureArtifact(
        universe_version="seed100-v1",
        feature_set_version="live-features-v5",
        dataset_revision="dataset-v5",
        input_hash=input_hash,
        generated_at=NOW,
        as_of=NOW - timedelta(hours=1),
        row_count=1,
        path=target.resolve(),
        reference=reference,
    )
    builder = object.__new__(LiveFeatureBuilder)
    builder.paths = paths

    builder._register(artifact)

    with duckdb.connect(str(paths.catalog), read_only=True) as connection:
        stored_path = connection.execute(
            "SELECT path FROM feature_artifacts WHERE input_hash = ?",
            [input_hash],
        ).fetchone()
        symbols = connection.execute(
            "SELECT symbol FROM latest_live_features"
        ).fetchall()
    assert stored_path == (reference.relative_path,)
    assert symbols == [("US.AAPL",)]
