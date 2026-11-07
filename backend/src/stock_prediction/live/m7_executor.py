"""Real seed-100 data preparation for the governed M7 bundle executor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from stock_prediction.features.m7 import M7_CORE_V2_COLUMNS, M7FeatureBuildResult
from stock_prediction.live.artifact_references import (
    ArtifactReference,
    build_artifact_reference,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.features import (
    LiveFeatureArtifact,
    build_m7_feature_frame,
    load_latest_feature_artifact,
)
from stock_prediction.modeling.contracts import M7FoldPartitions, SampleKey
from stock_prediction.modeling.m7_dataset import (
    M7DatasetManifest,
    M7EligibilityInputRow,
    build_m7_eligibility_manifest,
)
from stock_prediction.modeling.m7_recipe import M7_LABEL_VERSION
from stock_prediction.modeling.report import deterministic_hash, write_immutable_json
from stock_prediction.modeling.splits import build_m7_fold_partitions
from stock_prediction.universe.seed import (
    SEED_ETFS,
    SEED_UNIVERSE,
    SEED_UNIVERSE_VERSION,
)


M7_ASSET_TYPE_VERSION = "seed100-asset-type-v1"
M7_SECTOR_MAPPING_VERSION = "seed100-sector-map-v1"
_M7_REQUIRED_LABEL_COLUMNS = (
    *(f"direction_{horizon}" for horizon in (1, 5, 20)),
    *(f"return_fwd_{horizon}" for horizon in (1, 5, 20)),
    *(f"range_exit_event_{horizon}" for horizon in (5, 20)),
    *(f"range_exit_day_{horizon}" for horizon in (5, 20)),
)


class M7BundleExecutionError(RuntimeError):
    """The governed M7 executor cannot safely prepare or run a bundle."""


@dataclass(frozen=True, slots=True)
class M7PreparedFeatureData:
    source_artifact: LiveFeatureArtifact
    feature_result: M7FeatureBuildResult
    frame: pd.DataFrame
    development_cutoff: date
    dependency_hashes: Mapping[str, str]
    label_version: str = M7_LABEL_VERSION


@dataclass(frozen=True, slots=True)
class M7MaterializedDevelopmentDataset:
    manifest: M7DatasetManifest
    feature_path: Path
    label_path: Path
    eligibility_path: Path
    manifest_path: Path
    feature_reference: ArtifactReference
    label_reference: ArtifactReference


@dataclass(frozen=True, slots=True)
class M7DevelopmentSplits:
    folds: tuple[M7FoldPartitions, M7FoldPartitions, M7FoldPartitions, M7FoldPartitions]
    split_manifest_hash: str


def prepare_m7_feature_data(
    paths: LivePaths,
    *,
    development_cutoff: date,
) -> M7PreparedFeatureData:
    """Build the exact v5 feature frame from the governed live adjusted artifact."""

    try:
        artifact = load_latest_feature_artifact(paths)
        source = pq.read_table(artifact.path).to_pandas()
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        raise M7BundleExecutionError(
            "m7_source_feature_artifact_unavailable"
        ) from error
    if artifact.universe_version != SEED_UNIVERSE_VERSION:
        raise M7BundleExecutionError("m7_source_universe_mismatch")
    missing = {
        "symbol",
        "session_date",
        "source_available_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "dataset_revision",
        *_M7_REQUIRED_LABEL_COLUMNS,
    } - set(source.columns)
    if missing:
        raise M7BundleExecutionError(
            "m7_source_columns_missing:" + ",".join(sorted(missing))
        )
    source["session_date"] = pd.to_datetime(source["session_date"]).dt.date
    source = source[source["session_date"] <= development_cutoff].copy()
    if source.empty:
        raise M7BundleExecutionError("m7_development_data_unavailable")
    if set(source["symbol"]) != set(SEED_UNIVERSE):
        raise M7BundleExecutionError("m7_source_symbols_must_equal_seed100")
    if source["dataset_revision"].nunique(dropna=False) != 1:
        raise M7BundleExecutionError("m7_source_dataset_revision_mixed")
    source = source.loc[
        :,
        [
            "symbol",
            "session_date",
            "source_available_at",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "dataset_revision",
            *(name for name in _M7_REQUIRED_LABEL_COLUMNS),
            *(["quality_quarantined"] if "quality_quarantined" in source else []),
        ],
    ].copy()
    source["eligible"] = ~source.get(
        "quality_quarantined",
        pd.Series(False, index=source.index),
    ).fillna(True).astype(bool)
    source["asset_type"] = np.where(source["symbol"].isin(SEED_ETFS), "etf", "stock")
    source["asset_type_version"] = M7_ASSET_TYPE_VERSION
    source["sector"] = source["symbol"].map(_seed100_sector)
    source["sector_mapping_version"] = M7_SECTOR_MAPPING_VERSION
    source["universe_version"] = SEED_UNIVERSE_VERSION
    dependencies = _dependency_hashes(artifact, source)
    try:
        feature_result = build_m7_feature_frame(
            source,
            dependency_hashes=dependencies,
        )
    except ValueError as error:
        raise M7BundleExecutionError(f"m7_v5_feature_build_failed:{error}") from error
    frame = _freeze_development_labels(
        feature_result.frame,
        development_cutoff=development_cutoff,
    )
    return M7PreparedFeatureData(
        source_artifact=artifact,
        feature_result=feature_result,
        frame=frame,
        development_cutoff=development_cutoff,
        dependency_hashes=dependencies,
    )


def materialize_m7_development_dataset(
    paths: LivePaths,
    *,
    run_id: str,
    prepared: M7PreparedFeatureData,
) -> M7MaterializedDevelopmentDataset:
    """Persist v5 data and its complete row-level eligibility lineage once."""

    run_root = paths.models / "runs" / run_id / "executor" / "dataset"
    run_root.mkdir(parents=True, exist_ok=True)
    identity_path = run_root / "resume-identity.json"
    identity = {
        "source_input_hash": prepared.source_artifact.input_hash,
        "feature_set_version": prepared.feature_result.feature_set_version,
        "dataset_revision": prepared.feature_result.dataset_revision,
        "dependency_hashes": dict(prepared.dependency_hashes),
    }
    if identity_path.exists():
        try:
            saved_identity = json.loads(identity_path.read_text(encoding="utf-8"))
            created_at = datetime.fromisoformat(saved_identity.pop("created_at"))
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
            raise M7BundleExecutionError("m7_resume_identity_invalid") from error
        if saved_identity != identity:
            raise M7BundleExecutionError("m7_resume_identity_mismatch")
    else:
        created_at = datetime.now(UTC)
        write_immutable_json(
            identity_path,
            {**identity, "created_at": created_at.isoformat()},
        )
    frame = prepared.frame
    sessions = tuple(sorted(set(frame["session_date"])))
    if len(sessions) <= 272:
        raise M7BundleExecutionError("m7_development_history_insufficient")
    anchor_sessions = sessions[252:-20]
    if not anchor_sessions:
        raise M7BundleExecutionError("m7_development_anchor_sessions_unavailable")

    feature_path = run_root / "v5-features.parquet"
    _write_parquet_immutable(feature_path, frame)
    feature_reference = build_artifact_reference(
        artifact_root=paths.root,
        artifact_id=f"{run_id}-v5-features",
        target=feature_path,
        schema_version="m7-v5-feature-frame-v1",
        created_at=created_at,
    )
    calendar_path = run_root / "calendar.json"
    _write_json_idempotent(
        calendar_path,
        {
            "schema_version": "m7-calendar-evidence-v1",
            "sessions": [value.isoformat() for value in sessions],
            "dependency_hash": prepared.dependency_hashes["calendar"],
        },
    )
    calendar_reference = build_artifact_reference(
        artifact_root=paths.root,
        artifact_id=f"{run_id}-calendar",
        target=calendar_path,
        schema_version="m7-calendar-evidence-v1",
        created_at=created_at,
    )
    adjusted_reference = build_artifact_reference(
        artifact_root=paths.root,
        artifact_id=f"{run_id}-adjusted-bars",
        target=prepared.source_artifact.path,
        schema_version="m7-adjusted-bar-source-v1",
        created_at=prepared.source_artifact.generated_at,
    )

    anchor_frame = frame[frame["session_date"].isin(anchor_sessions)].copy()
    label_frame = anchor_frame.loc[
        :,
        [
            "symbol",
            "session_date",
            "atr_pct_20",
            *_M7_REQUIRED_LABEL_COLUMNS,
        ],
    ].copy()
    for horizon in (1, 5, 20):
        label_frame[f"direction_threshold_{horizon}"] = np.maximum(
            0.002,
            0.25 * label_frame["atr_pct_20"] * np.sqrt(float(horizon)),
        )
    label_path = run_root / "labels-and-thresholds.parquet"
    _write_parquet_immutable(label_path, label_frame)
    label_reference = build_artifact_reference(
        artifact_root=paths.root,
        artifact_id=f"{run_id}-labels-thresholds",
        target=label_path,
        schema_version="m7-label-threshold-evidence-v1",
        created_at=created_at,
    )

    feature_schema_valid = set(M7_CORE_V2_COLUMNS).issubset(frame.columns)
    session_counts = anchor_frame.groupby("session_date")["symbol"].nunique().to_dict()
    source_rows: list[M7EligibilityInputRow] = []
    for row in anchor_frame.itertuples(index=False):
        anchor = row.session_date
        missing_core = tuple(
            sorted(name for name in M7_CORE_V2_COLUMNS if pd.isna(getattr(row, name)))
        )
        available_at = pd.Timestamp(row.source_available_at)
        pit_valid = available_at.date() <= anchor
        for horizon in (1, 5, 20):
            source_rows.append(
                M7EligibilityInputRow(
                    symbol=row.symbol,
                    anchor_session=anchor,
                    horizon=horizon,  # type: ignore[arg-type]
                    universe_version=SEED_UNIVERSE_VERSION,
                    dataset_revision=prepared.feature_result.dataset_revision,
                    feature_set_version=prepared.feature_result.feature_set_version,
                    label_version=M7_LABEL_VERSION,
                    adjusted_bar_reference=adjusted_reference,
                    calendar_reference=calendar_reference,
                    feature_reference=feature_reference,
                    label_reference=label_reference,
                    pit_lineage_valid=pit_valid,
                    adjustment_verified=bool(row.eligible),
                    calendar_complete=session_counts.get(anchor) == len(SEED_UNIVERSE),
                    feature_schema_valid=feature_schema_valid,
                    label_window_complete=pd.notna(
                        getattr(row, f"direction_{horizon}")
                    ),
                    missing_core_features=missing_core,
                )
            )
    manifest = build_m7_eligibility_manifest(
        source_rows,
        expected_symbols=SEED_UNIVERSE,
        expected_anchor_sessions=anchor_sessions,
        label_threshold_artifacts=(label_reference,),
    )
    eligibility_path = run_root / "eligibility.parquet"
    _write_parquet_immutable(
        eligibility_path,
        pd.DataFrame(row.canonical_payload() for row in manifest.rows),
    )
    eligibility_reference = build_artifact_reference(
        artifact_root=paths.root,
        artifact_id=f"{run_id}-eligibility",
        target=eligibility_path,
        schema_version="m7-eligibility-rows-v1",
        created_at=created_at,
    )
    manifest_path = run_root / "dataset-manifest.json"
    _write_json_idempotent(
        manifest_path,
        {
            "schema_version": manifest.schema_version,
            "dataset_id": manifest.dataset_id,
            "manifest_hash": manifest.manifest_hash,
            "universe_version": manifest.universe_version,
            "dataset_revision": manifest.dataset_revision,
            "feature_set_version": manifest.feature_set_version,
            "label_version": manifest.label_version,
            "symbol_count": manifest.symbol_count,
            "anchor_session_count": len(manifest.anchor_sessions),
            "expected_row_count": manifest.expected_row_count,
            "eligible_row_count": manifest.eligible_row_count,
            "symbol_coverage": dict(manifest.symbol_coverage),
            "horizon_coverage": dict(manifest.horizon_coverage),
            "exclusion_counts": dict(manifest.exclusion_counts),
            "gate_passed": manifest.gate_passed,
            "failure_codes": list(manifest.failure_codes),
            "eligibility_artifact": eligibility_reference.as_dict(),
            "label_threshold_artifacts": [label_reference.as_dict()],
        },
    )
    return M7MaterializedDevelopmentDataset(
        manifest=manifest,
        feature_path=feature_path,
        label_path=label_path,
        eligibility_path=eligibility_path,
        manifest_path=manifest_path,
        feature_reference=feature_reference,
        label_reference=label_reference,
    )


def build_m7_development_folds(
    manifest: M7DatasetManifest,
) -> M7DevelopmentSplits:
    """Select the latest four non-overlapping 126-session outer tests."""

    sessions = manifest.anchor_sessions
    required_test_sessions = 4 * 126
    if not manifest.gate_passed or len(sessions) <= 272 + required_test_sessions:
        raise M7BundleExecutionError("m7_four_fold_history_insufficient")
    keys: tuple[SampleKey, ...] = tuple(
        sorted(
            (
                (
                    row.symbol,
                    datetime.combine(
                        row.anchor_session,
                        datetime.min.time(),
                        tzinfo=UTC,
                    ),
                    row.horizon,
                )
                for row in manifest.rows
                if row.eligible
            ),
            key=lambda key: (key[1], key[0], key[2]),
        )
    )
    first_test_position = len(sessions) - required_test_sessions
    folds = tuple(
        build_m7_fold_partitions(
            fold_id=f"m7-outer-{index + 1:02d}",
            split_version="m7-purged-four-fold-v1",
            dataset_input_hash=manifest.manifest_hash,
            calendar_sessions=sessions,
            source_sample_keys=keys,
            outer_test_start=sessions[first_test_position + index * 126],
            training_start=sessions[0],
        )
        for index in range(4)
    )
    typed_folds = (folds[0], folds[1], folds[2], folds[3])
    return M7DevelopmentSplits(
        folds=typed_folds,
        split_manifest_hash=deterministic_hash(
            {
                "schema_version": "m7-split-manifest-v1",
                "folds": [fold.model_dump(mode="json") for fold in typed_folds],
            }
        ),
    )


def _dependency_hashes(
    artifact: LiveFeatureArtifact,
    source: pd.DataFrame,
) -> dict[str, str]:
    sessions = tuple(sorted(set(source["session_date"])))
    classification = tuple(
        (symbol, "etf" if symbol in SEED_ETFS else "stock", _seed100_sector(symbol))
        for symbol in SEED_UNIVERSE
    )
    return {
        "adjusted_bars": artifact.reference.sha256,
        "calendar": _hash_json([value.isoformat() for value in sessions]),
        "classification": _hash_json(
            {
                "asset_type_version": M7_ASSET_TYPE_VERSION,
                "sector_mapping_version": M7_SECTOR_MAPPING_VERSION,
                "rows": classification,
            }
        ),
        "universe": _hash_json(
            {"version": SEED_UNIVERSE_VERSION, "symbols": SEED_UNIVERSE}
        ),
    }


def _freeze_development_labels(
    frame: pd.DataFrame,
    *,
    development_cutoff: date,
) -> pd.DataFrame:
    development = frame[frame["session_date"] <= development_cutoff].copy()
    sessions = tuple(sorted(set(development["session_date"])))
    for horizon in (1, 5, 20):
        if len(sessions) <= horizon:
            raise M7BundleExecutionError("m7_development_sessions_insufficient")
        mature_anchor_through = sessions[-(horizon + 1)]
        targets = [
            name
            for name in (
                f"direction_{horizon}",
                f"return_fwd_{horizon}",
                f"range_exit_event_{horizon}",
                f"range_exit_day_{horizon}",
            )
            if name in development
        ]
        development.loc[
            development["session_date"] > mature_anchor_through,
            targets,
        ] = np.nan
    return development.sort_values(
        ["session_date", "symbol"], kind="stable"
    ).reset_index(drop=True)


def _seed100_sector(symbol: str) -> str:
    if symbol in SEED_ETFS:
        return "etf"
    ticker = symbol.removeprefix("US.")
    groups = (
        (
            "technology",
            "AAPL MSFT NVDA GOOGL META AVGO ORCL CRM AMD INTC QCOM MU AMAT LRCX KLAC TXN CSCO NFLX NOW",
        ),
        (
            "consumer",
            "AMZN TSLA WMT COST HD MCD NKE SBUX LOW TGT DIS BKNG CMG PG KO PEP",
        ),
        ("financials", "JPM BAC WFC GS MS C SCHW BLK AXP V MA COF"),
        ("healthcare", "LLY UNH JNJ ABBV MRK TMO AMGN GILD ISRG PFE BMY MDT ABT"),
        ("industrials", "GE CAT BA RTX LMT DE UPS FDX ETN HON"),
        ("energy_materials", "XOM CVX COP SLB EOG NEE LIN FCX PLD"),
        ("communication", "T"),
    )
    for sector, members in groups:
        if ticker in members.split():
            return sector
    raise M7BundleExecutionError(f"m7_seed100_sector_missing:{symbol}")


def _hash_json(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_parquet_immutable(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = pa.Table.from_pandas(frame.reset_index(drop=True), preserve_index=False)
    if path.exists():
        try:
            if not pq.read_table(path).equals(expected, check_metadata=False):
                raise M7BundleExecutionError(
                    "m7_executor_artifact_resume_mismatch"
                )
        except (OSError, pa.ArrowException) as error:
            raise M7BundleExecutionError("m7_executor_artifact_resume_mismatch") from error
        return
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        pq.write_table(
            expected,
            temporary,
            compression="zstd",
            use_dictionary=True,
        )
        os.replace(temporary, path)
    except OSError as error:
        raise M7BundleExecutionError("m7_executor_artifact_write_failed") from error
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_idempotent(path: Path, payload: object) -> None:
    if path.exists():
        try:
            canonical = json.loads(
                json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
            )
            if json.loads(path.read_text(encoding="utf-8")) != canonical:
                raise M7BundleExecutionError("m7_executor_artifact_resume_mismatch")
        except (OSError, json.JSONDecodeError) as error:
            raise M7BundleExecutionError("m7_executor_artifact_resume_mismatch") from error
        return
    write_immutable_json(path, payload)


__all__ = [
    "M7_ASSET_TYPE_VERSION",
    "M7_SECTOR_MAPPING_VERSION",
    "M7BundleExecutionError",
    "M7DevelopmentSplits",
    "M7MaterializedDevelopmentDataset",
    "M7PreparedFeatureData",
    "build_m7_development_folds",
    "materialize_m7_development_dataset",
    "prepare_m7_feature_data",
]
