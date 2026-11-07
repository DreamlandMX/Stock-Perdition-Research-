"""Latest-vintage live features, rule states, and forward research labels.

Default production history is retrospective, not strict historical PIT.
Use ``load_live_observations_as_of`` for an explicit read-only catalog vintage;
per-anchor feature replay and label-vintage policy remain caller responsibilities.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from stock_prediction.live.config import (
    ALPACA_SIP_DATASET_REVISION,
    ALPACA_SIP_FEATURE_SET_VERSION,
    ALPACA_IEX_YAHOO_DATASET_REVISION,
    ALPACA_IEX_YAHOO_FEATURE_SET_VERSION,
    LIVE_FEATURE_SET_VERSION,
    LivePaths,
)
from stock_prediction.live.adjustments import (
    build_point_in_time_adjusted_bars,
    window_has_quarantine,
)
from stock_prediction.live.artifact_references import (
    ArtifactReference,
    ArtifactReferenceError,
    build_artifact_reference,
    resolve_artifact_reference,
)
from stock_prediction.features.m7 import (
    M7FeatureBuildResult,
    build_m7_daily_features,
)
from stock_prediction.universe.seed import SEED_UNIVERSE_VERSION
from stock_prediction.live.universe_scope import LiveUniverseSpec, resolve_live_universe
from stock_prediction.live.observations import load_live_observations_as_of


LIVE_FEATURE_ARTIFACT_SCHEMA_VERSION = "live-feature-artifact-v1"
M7_FEATURE_ARTIFACT_SCHEMA_VERSION = "m7-feature-artifact-v1"


def build_m7_feature_frame(
    source: pd.DataFrame,
    *,
    dependency_hashes: Mapping[str, str],
    intraday_extension: pd.DataFrame | None = None,
) -> M7FeatureBuildResult:
    """Expose the targeted v5 builder without changing the legacy build path."""

    return build_m7_daily_features(
        source,
        dependency_hashes=dependency_hashes,
        intraday_extension=intraday_extension,
    )


@dataclass(frozen=True, slots=True)
class LiveFeatureArtifact:
    universe_version: str
    feature_set_version: str
    dataset_revision: str
    input_hash: str
    generated_at: datetime
    as_of: datetime
    row_count: int
    path: Path
    reference: ArtifactReference

    def as_dict(self) -> dict[str, object]:
        return {
            "universe_version": self.universe_version,
            "feature_set_version": self.feature_set_version,
            "dataset_revision": self.dataset_revision,
            "input_hash": self.input_hash,
            "generated_at": self.generated_at.isoformat(),
            "as_of": self.as_of.isoformat(),
            "row_count": self.row_count,
            "artifact_reference": self.reference.as_dict(),
        }


class LiveFeatureBuilder:
    def __init__(
        self, paths: LivePaths, *, universe: LiveUniverseSpec | None = None
    ) -> None:
        self.paths = paths
        self.universe = universe or resolve_live_universe(paths)
        paths.ensure()

    def build(self) -> LiveFeatureArtifact:
        dataset_revision, feature_set_version = _artifact_versions(self.paths)
        with duckdb.connect(str(self.paths.catalog)) as connection:
            daily = connection.execute(
                """
                SELECT symbol, session_date, bar_start, source_available_at,
                       open, high, low, close, volume, turnover
                FROM canonical_market_bars
                WHERE timeframe = '1d' AND symbol IN (SELECT * FROM UNNEST(?))
                ORDER BY symbol, session_date
                """,
                [list(self.universe.symbols)],
            ).fetch_df()
            rehab = connection.execute(
                """
                SELECT symbol, ex_div_date, split_ratio, per_cash_div,
                       special_dividend, forward_adj_factor_a,
                       forward_adj_factor_b
                FROM latest_rehab_observations
                ORDER BY symbol, ex_div_date
                """
            ).fetch_df()
            intraday = connection.execute(
                """
                SELECT symbol, session_date,
                       MAX(high) / NULLIF(MIN(low), 0) - 1 AS intraday_range,
                       SUM(volume) AS intraday_volume,
                       arg_min(open, bar_start) AS session_open,
                       arg_max(close, bar_start) AS session_close,
                       MAX(source_available_at) AS intraday_available_at
                FROM canonical_market_bars
                WHERE timeframe = '60m' AND symbol IN (SELECT * FROM UNNEST(?))
                GROUP BY symbol, session_date
                """,
                [list(self.universe.symbols)],
            ).fetch_df()
            evidence = connection.execute(
                """
                SELECT 'market_bar' AS source_type, batch_id AS source_id, sha256
                FROM raw_bar_files
                UNION ALL
                SELECT 'rehab', snapshot_id, sha256 FROM rehab_snapshots
                ORDER BY source_type, source_id
                """
            ).fetchall()
        if daily.empty:
            raise RuntimeError("live_daily_bars_unavailable")
        daily = build_point_in_time_adjusted_bars(daily, rehab)
        frames = [_build_symbol_features(frame) for _, frame in daily.groupby("symbol")]
        features = pd.concat(frames, ignore_index=True)
        features = _add_market_and_relative_features(features, etfs=self.universe.etfs)
        if not intraday.empty:
            intraday["session_date"] = pd.to_datetime(intraday["session_date"]).dt.date
            intraday["intraday_return"] = (
                intraday["session_close"] / intraday["session_open"] - 1.0
            )
            features = features.merge(
                intraday[
                    [
                        "symbol",
                        "session_date",
                        "intraday_range",
                        "intraday_volume",
                        "intraday_return",
                        "intraday_available_at",
                    ]
                ],
                how="left",
                on=["symbol", "session_date"],
                validate="one_to_one",
            )
        else:
            features["intraday_range"] = np.nan
            features["intraday_volume"] = np.nan
            features["intraday_return"] = np.nan
            features["intraday_available_at"] = pd.NaT
        features["intraday_available"] = features["intraday_range"].notna()
        features["feature_set_version"] = feature_set_version
        features["dataset_revision"] = dataset_revision
        features.sort_values(["symbol", "session_date"], inplace=True)

        input_hash = hashlib.sha256(
            json.dumps(
                [
                    self.universe.version,
                    dataset_revision,
                    feature_set_version,
                    "pit-adjustment-and-label-policy-v8-zero-width-channel",
                    evidence,
                ],
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        directory = self.paths.features / feature_set_version / input_hash[:16]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "features.parquet"
        table = pa.Table.from_pandas(features, preserve_index=False)
        table = table.replace_schema_metadata(
            {
                **(table.schema.metadata or {}),
                b"observation_policy": b"latest_revision_retrospective",
                b"strict_historical_pit": b"false",
            }
        )
        if not target.exists():
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            pq.write_table(table, temporary, compression="zstd", use_dictionary=True)
            os.replace(temporary, target)
        generated_at = datetime.now(UTC)
        as_of_value = pd.to_datetime(features["source_available_at"], utc=True).max()
        as_of = as_of_value.to_pydatetime()
        artifact = LiveFeatureArtifact(
            universe_version=self.universe.version,
            feature_set_version=feature_set_version,
            dataset_revision=dataset_revision,
            input_hash=input_hash,
            generated_at=generated_at,
            as_of=as_of,
            row_count=len(features),
            path=target.resolve(),
            reference=build_artifact_reference(
                artifact_root=self.paths.features,
                artifact_id=input_hash,
                target=target,
                schema_version=LIVE_FEATURE_ARTIFACT_SCHEMA_VERSION,
                created_at=generated_at,
            ),
        )
        manifest = directory / "artifact.json"
        payload = artifact.as_dict()
        if manifest.exists():
            artifact = _load_feature_artifact_file(self.paths, manifest)
            if artifact.input_hash != input_hash or artifact.path != target.resolve():
                raise RuntimeError("live_feature_artifact_identity_invalid")
            payload = artifact.as_dict()
        else:
            _write_json_atomic(manifest, payload)
        pointer = self.paths.features / "LATEST.json"
        _write_json_atomic(pointer, payload)
        self._register(artifact)
        return artifact

    def _register(self, artifact: LiveFeatureArtifact) -> None:
        with duckdb.connect(str(self.paths.catalog)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feature_artifacts (
                    input_hash VARCHAR PRIMARY KEY,
                    universe_version VARCHAR,
                    feature_set_version VARCHAR NOT NULL,
                    dataset_revision VARCHAR NOT NULL,
                    generated_at TIMESTAMPTZ NOT NULL,
                    as_of TIMESTAMPTZ NOT NULL,
                    row_count BIGINT NOT NULL,
                    path VARCHAR NOT NULL
                )
                """
            )
            connection.execute(
                """
                ALTER TABLE feature_artifacts
                ADD COLUMN IF NOT EXISTS universe_version VARCHAR
                """
            )
            connection.execute(
                """
                INSERT INTO feature_artifacts
                (input_hash, universe_version, feature_set_version, dataset_revision,
                 generated_at, as_of, row_count, path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (input_hash) DO UPDATE SET
                    universe_version = excluded.universe_version,
                    feature_set_version = excluded.feature_set_version,
                    dataset_revision = excluded.dataset_revision,
                    generated_at = excluded.generated_at,
                    as_of = excluded.as_of,
                    row_count = excluded.row_count,
                    path = excluded.path
                """,
                [
                    artifact.input_hash,
                    artifact.universe_version,
                    artifact.feature_set_version,
                    artifact.dataset_revision,
                    artifact.generated_at,
                    artifact.as_of,
                    artifact.row_count,
                    artifact.reference.relative_path,
                ],
            )
            escaped = str(artifact.path).replace("'", "''")
            connection.execute(
                f"""
                CREATE OR REPLACE VIEW latest_live_features AS
                SELECT * FROM read_parquet('{escaped}')
                """
            )


def _artifact_versions(paths: LivePaths) -> tuple[str, str]:
    if paths.dataset_revision == ALPACA_IEX_YAHOO_DATASET_REVISION:
        return (
            ALPACA_IEX_YAHOO_DATASET_REVISION,
            ALPACA_IEX_YAHOO_FEATURE_SET_VERSION,
        )
    if paths.dataset_revision == ALPACA_SIP_DATASET_REVISION:
        return ALPACA_SIP_DATASET_REVISION, ALPACA_SIP_FEATURE_SET_VERSION
    return paths.dataset_revision, LIVE_FEATURE_SET_VERSION


def load_latest_feature_artifact(paths: LivePaths) -> LiveFeatureArtifact:
    pointer = paths.features / "LATEST.json"
    if not pointer.is_file():
        raise FileNotFoundError("live feature artifact is unavailable")
    return _load_feature_artifact_file(paths, pointer)


def rebuild_latest_feature_pointer(paths: LivePaths) -> LiveFeatureArtifact:
    valid: dict[tuple[str, str], LiveFeatureArtifact] = {}
    legacy_candidates: list[tuple[datetime, Path, Mapping[str, object]]] = []
    for manifest in sorted(paths.features.rglob("artifact.json")):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and "path" in payload:
                generated_at = datetime.fromisoformat(str(payload["generated_at"]))
                if generated_at.tzinfo is None or generated_at.utcoffset() is None:
                    raise ValueError("legacy generated_at must be timezone-aware")
                legacy_candidates.append((generated_at, manifest, payload))
                continue
            artifact = _load_feature_artifact_file(paths, manifest)
        except (OSError, json.JSONDecodeError, RuntimeError):
            continue
        except (KeyError, TypeError, ValueError):
            continue
        valid[(artifact.input_hash, artifact.reference.relative_path)] = artifact
    if not valid:
        if not legacy_candidates:
            raise RuntimeError("live_feature_latest_unavailable")
        newest_legacy_at = max(row[0] for row in legacy_candidates)
        newest_legacy = [row for row in legacy_candidates if row[0] == newest_legacy_at]
        if len(newest_legacy) != 1:
            raise RuntimeError("live_feature_latest_ambiguous")
        _generated_at, manifest, payload = newest_legacy[0]
        recovered = _recover_copied_legacy_feature_artifact(paths, manifest, payload)
        _write_json_atomic(paths.features / "LATEST.json", recovered.as_dict())
        return recovered
    newest_generated_at = max(artifact.generated_at for artifact in valid.values())
    newest = [
        artifact
        for artifact in valid.values()
        if artifact.generated_at == newest_generated_at
    ]
    if len(newest) != 1:
        raise RuntimeError("live_feature_latest_ambiguous")
    selected = newest[0]
    _write_json_atomic(paths.features / "LATEST.json", selected.as_dict())
    return selected


def _recover_copied_legacy_feature_artifact(
    paths: LivePaths,
    manifest: Path,
    payload: Mapping[str, object],
) -> LiveFeatureArtifact:
    """Re-index a copied legacy artifact without trusting its absolute path."""

    try:
        input_hash = str(payload["input_hash"])
        generated_at = datetime.fromisoformat(str(payload["generated_at"]))
        as_of = datetime.fromisoformat(str(payload["as_of"]))
        row_count_value = payload["row_count"]
        feature_set_version = str(payload["feature_set_version"])
        dataset_revision = str(payload["dataset_revision"])
        legacy_path = Path(str(payload["path"]))
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("live_feature_artifact_manifest_invalid") from error
    if not isinstance(row_count_value, int) or isinstance(row_count_value, bool):
        raise RuntimeError("live_feature_artifact_manifest_invalid")
    row_count = row_count_value
    target = manifest.parent / "features.parquet"
    if (
        len(input_hash) != 64
        or any(character not in "0123456789abcdef" for character in input_hash)
        or manifest.parent.name != input_hash[:16]
        or manifest.parent.parent.name != feature_set_version
        or legacy_path.name != target.name
        or generated_at.tzinfo is None
        or generated_at.utcoffset() is None
        or as_of.tzinfo is None
        or as_of.utcoffset() is None
        or row_count < 0
        or not target.is_file()
        or target.is_symlink()
    ):
        raise RuntimeError("live_feature_artifact_path_invalid")
    try:
        actual_rows = pq.ParquetFile(target).metadata.num_rows
    except (OSError, ValueError) as error:
        raise RuntimeError("live_feature_artifact_manifest_invalid") from error
    if actual_rows != row_count:
        raise RuntimeError("live_feature_artifact_identity_invalid")
    reference = build_artifact_reference(
        artifact_root=paths.features,
        artifact_id=input_hash,
        target=target,
        schema_version=LIVE_FEATURE_ARTIFACT_SCHEMA_VERSION,
        created_at=generated_at,
    )
    return LiveFeatureArtifact(
        universe_version=str(payload.get("universe_version", SEED_UNIVERSE_VERSION)),
        feature_set_version=feature_set_version,
        dataset_revision=dataset_revision,
        input_hash=input_hash,
        generated_at=generated_at,
        as_of=as_of,
        row_count=row_count,
        path=target.resolve(),
        reference=reference,
    )


def _load_feature_artifact_file(
    paths: LivePaths,
    source: Path,
) -> LiveFeatureArtifact:
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("live_feature_artifact_manifest_invalid") from error
    artifact = _load_feature_artifact_payload(paths, payload)
    if (
        source.name == "artifact.json"
        and artifact.path.parent != source.parent.resolve()
    ):
        raise RuntimeError("live_feature_artifact_identity_invalid")
    return artifact


def _load_feature_artifact_payload(
    paths: LivePaths,
    payload: object,
) -> LiveFeatureArtifact:
    if not isinstance(payload, Mapping):
        raise RuntimeError("live_feature_artifact_manifest_invalid")
    if "path" in payload:
        raise RuntimeError("live_feature_artifact_path_invalid")
    reference_payload = payload.get("artifact_reference")
    if not isinstance(reference_payload, Mapping):
        raise RuntimeError("live_feature_artifact_reference_invalid")
    try:
        reference = ArtifactReference.from_dict(reference_payload)
    except ArtifactReferenceError as error:
        raise _feature_reference_error(error) from error
    if reference.schema_version != LIVE_FEATURE_ARTIFACT_SCHEMA_VERSION:
        raise RuntimeError("live_feature_artifact_schema_invalid")
    try:
        input_hash = str(payload["input_hash"])
        generated_at = datetime.fromisoformat(str(payload["generated_at"]))
        as_of = datetime.fromisoformat(str(payload["as_of"]))
        row_count = int(payload["row_count"])
        feature_set_version = str(payload["feature_set_version"])
        dataset_revision = str(payload["dataset_revision"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("live_feature_artifact_manifest_invalid") from error
    if (
        reference.artifact_id != input_hash
        or reference.created_at != generated_at
        or generated_at.tzinfo is None
        or generated_at.utcoffset() is None
        or as_of.tzinfo is None
        or as_of.utcoffset() is None
        or row_count < 0
    ):
        raise RuntimeError("live_feature_artifact_identity_invalid")
    try:
        path = resolve_artifact_reference(paths.features, reference)
    except ArtifactReferenceError as error:
        raise _feature_reference_error(error) from error
    return LiveFeatureArtifact(
        universe_version=str(payload.get("universe_version", SEED_UNIVERSE_VERSION)),
        feature_set_version=feature_set_version,
        dataset_revision=dataset_revision,
        input_hash=input_hash,
        generated_at=generated_at,
        as_of=as_of,
        row_count=row_count,
        path=path,
        reference=reference,
    )


def _feature_reference_error(error: ArtifactReferenceError) -> RuntimeError:
    code = str(error)
    if code in {"artifact_path_invalid", "artifact_path_outside_root"}:
        return RuntimeError("live_feature_artifact_path_invalid")
    if code == "artifact_hash_mismatch":
        return RuntimeError("live_feature_artifact_hash_mismatch")
    if code == "artifact_missing":
        return RuntimeError("live_feature_artifact_missing")
    return RuntimeError("live_feature_artifact_reference_invalid")


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _build_symbol_features(source: pd.DataFrame) -> pd.DataFrame:
    frame = source.copy().sort_values("session_date").reset_index(drop=True)
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    volume = frame["volume"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["return_1"] = close.pct_change(1)
    frame["return_5"] = close.pct_change(5)
    frame["return_20"] = close.pct_change(20)
    frame["return_60"] = close.pct_change(60)
    frame["volatility_5"] = frame["return_1"].rolling(5).std()
    frame["volatility_20"] = frame["return_1"].rolling(20).std()
    frame["volatility_60"] = frame["return_1"].rolling(60).std()
    frame["atr_20"] = true_range.rolling(20).mean()
    frame["atr_pct_20"] = frame["atr_20"] / close
    frame["channel_high_20"] = high.shift(1).rolling(20).max()
    frame["channel_low_20"] = low.shift(1).rolling(20).min()
    frame["channel_width_20"] = frame["channel_high_20"] / frame["channel_low_20"] - 1.0
    frame["trend_slope_20"] = close / close.shift(20) - 1.0
    frame["ma_distance_20"] = close / close.rolling(20).mean() - 1.0
    frame["ma_distance_50"] = close / close.rolling(50).mean() - 1.0
    frame["ma_distance_200"] = close / close.rolling(200).mean() - 1.0
    frame["gap_1"] = frame["open"].astype(float) / previous_close - 1.0
    frame["channel_position_20"] = (close - frame["channel_low_20"]) / (
        frame["channel_high_20"] - frame["channel_low_20"]
    ).replace(0.0, np.nan)
    frame["volume_z_20"] = (volume - volume.shift(1).rolling(20).mean()) / volume.shift(
        1
    ).rolling(20).std()
    frame["volume_price_anomaly_20"] = frame["volume_z_20"] * frame["return_1"]
    compression_threshold = (
        frame["channel_width_20"].shift(1).rolling(252).quantile(0.25)
    )
    frame["channel_compressed"] = frame["channel_width_20"] <= compression_threshold
    atr_buffer = 0.25 * frame["atr_20"]
    frame["breakout_up_rule"] = close > frame["channel_high_20"] + atr_buffer
    frame["breakout_down_rule"] = close < frame["channel_low_20"] - atr_buffer
    enough = (
        frame[["atr_pct_20", "channel_width_20", "trend_slope_20"]].notna().all(axis=1)
    )
    trend_threshold = 0.75 * frame["atr_pct_20"]
    regime = np.select(
        [
            ~enough,
            frame["breakout_up_rule"] | frame["breakout_down_rule"],
            frame["trend_slope_20"] > trend_threshold,
            frame["trend_slope_20"] < -trend_threshold,
            frame["channel_compressed"]
            | (frame["trend_slope_20"].abs() <= trend_threshold),
        ],
        ["invalid", "transition", "uptrend", "downtrend", "range"],
        default="transition",
    )
    frame["regime"] = regime
    for horizon in (1, 5, 20):
        future_return = close.shift(-horizon) / close - 1.0
        threshold = np.maximum(
            0.002,
            0.25 * frame["atr_pct_20"] * np.sqrt(float(horizon)),
        )
        frame[f"return_fwd_{horizon}"] = future_return
        frame[f"direction_{horizon}"] = np.select(
            [future_return > threshold, future_return < -threshold],
            [1, -1],
            default=0,
        ).astype(float)
        frame.loc[future_return.isna(), f"direction_{horizon}"] = np.nan
        future_highs = pd.concat(
            [high.shift(-step) for step in range(1, horizon + 1)], axis=1
        )
        future_lows = pd.concat(
            [low.shift(-step) for step in range(1, horizon + 1)], axis=1
        )
        frame[f"mfe_{horizon}"] = future_highs.max(axis=1) / close - 1.0
        frame[f"mae_{horizon}"] = future_lows.min(axis=1) / close - 1.0
        has_full_window = high.shift(-horizon).notna() & low.shift(-horizon).notna()
        (
            breakout_direction,
            breakout_day,
            breakout_ambiguous,
        ) = _first_breakout(
            future_highs,
            future_lows,
            upper=frame["channel_high_20"] + atr_buffer,
            lower=frame["channel_low_20"] - atr_buffer,
        )
        frame[f"breakout_direction_{horizon}"] = breakout_direction
        frame[f"breakout_day_{horizon}"] = breakout_day
        frame[f"breakout_ambiguous_{horizon}"] = breakout_ambiguous
        quarantine_in_window = window_has_quarantine(
            frame["quality_quarantined"],
            horizon=horizon,
        )
        invalid_label = (
            ~has_full_window
            | quarantine_in_window
            | frame["quality_quarantined"].astype(bool)
        )
        frame.loc[
            invalid_label,
            [
                f"return_fwd_{horizon}",
                f"direction_{horizon}",
                f"mfe_{horizon}",
                f"mae_{horizon}",
                f"breakout_direction_{horizon}",
                f"breakout_day_{horizon}",
            ],
        ] = np.nan
        frame.loc[
            frame[f"breakout_ambiguous_{horizon}"],
            [f"breakout_direction_{horizon}", f"breakout_day_{horizon}"],
        ] = np.nan
    for horizon in (5, 20):
        future_up_exit = pd.concat(
            [frame["breakout_up_rule"].shift(-step) for step in range(1, horizon + 1)],
            axis=1,
        ).fillna(False)
        future_down_exit = pd.concat(
            [
                frame["breakout_down_rule"].shift(-step)
                for step in range(1, horizon + 1)
            ],
            axis=1,
        ).fillna(False)
        future_up_exit.columns = range(horizon)
        future_down_exit.columns = range(horizon)
        confirmed_strong_trend = (
            frame["trend_slope_20"].abs() > 4.0 * frame["atr_pct_20"]
        )
        future_strong_trend = pd.concat(
            [confirmed_strong_trend.shift(-step) for step in range(1, horizon + 1)],
            axis=1,
        ).fillna(False)
        future_strong_trend.columns = range(horizon)
        full_window = close.shift(-horizon).notna()
        future_confirmed_exit = future_up_exit | future_down_exit | future_strong_trend
        exit_confirmations = future_confirmed_exit & future_confirmed_exit.shift(
            1, axis=1, fill_value=False
        )
        observed = exit_confirmations.any(axis=1).to_numpy(dtype=bool)
        first_exit = exit_confirmations.to_numpy(dtype=bool).argmax(axis=1) + 1
        frame[f"range_exit_day_{horizon}"] = np.where(
            observed, first_exit, horizon
        ).astype(float)
        frame[f"range_exit_event_{horizon}"] = observed.astype(float)
        frame.loc[
            ~full_window
            | (frame["regime"] != "range")
            | frame["quality_quarantined"].astype(bool)
            | window_has_quarantine(frame["quality_quarantined"], horizon=horizon),
            [f"range_exit_day_{horizon}", f"range_exit_event_{horizon}"],
        ] = np.nan
    frame["source_available_at"] = pd.to_datetime(
        frame["source_available_at"], utc=True
    )
    return frame


def _first_breakout(
    future_highs: pd.DataFrame,
    future_lows: pd.DataFrame,
    *,
    upper: pd.Series,
    lower: pd.Series,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    up_hits = future_highs.gt(upper, axis=0).to_numpy(dtype=bool)
    down_hits = future_lows.lt(lower, axis=0).to_numpy(dtype=bool)
    up_any = up_hits.any(axis=1)
    down_any = down_hits.any(axis=1)
    up_day = np.where(up_any, up_hits.argmax(axis=1) + 1, np.inf)
    down_day = np.where(down_any, down_hits.argmax(axis=1) + 1, np.inf)
    ambiguous = np.isfinite(up_day) & (up_day == down_day)
    direction = np.where(
        ambiguous,
        np.nan,
        np.where(up_day < down_day, 1.0, np.where(down_day < up_day, -1.0, 0.0)),
    )
    event_day = np.where(
        ambiguous | (~np.isfinite(np.minimum(up_day, down_day))),
        np.nan,
        np.minimum(up_day, down_day),
    )
    return direction, event_day, ambiguous


def _add_market_and_relative_features(
    frame: pd.DataFrame, *, etfs: tuple[str, ...]
) -> pd.DataFrame:
    result = frame.copy().sort_values(["symbol", "session_date"])
    result["is_etf"] = result["symbol"].isin(etfs).astype(float)
    daily_breadth = (
        result.groupby("session_date")["return_1"]
        .apply(lambda values: float((values > 0).mean()))
        .sort_index()
        .shift(1)
    )
    result["market_breadth_up_lag1"] = result["session_date"].map(daily_breadth)

    proxy = result[result["symbol"].eq("US.SPY")][
        ["session_date", "return_1", "return_20", "return_60"]
    ].rename(
        columns={
            "return_1": "spy_return_1",
            "return_20": "spy_return_20",
            "return_60": "spy_return_60",
        }
    )
    result = result.merge(proxy, on="session_date", how="left", validate="many_to_one")
    result["relative_spy_20"] = result["return_20"] - result["spy_return_20"]
    result["relative_spy_60"] = result["return_60"] - result["spy_return_60"]

    groups: list[pd.DataFrame] = []
    for _, symbol_frame in result.groupby("symbol", sort=False):
        values = symbol_frame.copy().sort_values("session_date")
        stock_return = values["return_1"].shift(1)
        spy_return = values["spy_return_1"].shift(1)
        covariance = stock_return.rolling(60, min_periods=40).cov(spy_return)
        proxy_variance = spy_return.rolling(60, min_periods=40).var()
        values["rolling_beta_spy_60_lag1"] = covariance / proxy_variance
        values["rolling_corr_spy_60_lag1"] = stock_return.rolling(
            60, min_periods=40
        ).corr(spy_return)
        groups.append(values)
    return pd.concat(groups, ignore_index=True).sort_values(["symbol", "session_date"])


__all__ = [
    "M7_FEATURE_ARTIFACT_SCHEMA_VERSION",
    "LIVE_FEATURE_ARTIFACT_SCHEMA_VERSION",
    "LiveFeatureArtifact",
    "LiveFeatureBuilder",
    "build_m7_feature_frame",
    "load_latest_feature_artifact",
    "load_live_observations_as_of",
    "rebuild_latest_feature_pointer",
]
