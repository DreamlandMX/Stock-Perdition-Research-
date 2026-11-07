"""Forward-only daily inference for frozen M7 shadow packages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path
import time
from typing import Mapping
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from stock_prediction.contracts.operations import ModelCapabilityBundle
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_blind import M7BlindWindowLedger
from stock_prediction.live.m7_executor import prepare_m7_feature_data
from stock_prediction.modeling.m7_direction_package import (
    build_m7_direction_shadow_explanations,
    load_m7_direction_package,
)
from stock_prediction.modeling.m7_package import (
    M7PackageManifest,
    load_m7_package,
)
from stock_prediction.modeling.m7_range_package import load_m7_range_package
from stock_prediction.modeling.report import (
    deterministic_hash,
    file_sha256,
    write_immutable_json,
)
from stock_prediction.serving.manifest import ResearchSnapshotManifest
from stock_prediction.universe.seed import SEED_UNIVERSE


@dataclass(frozen=True, slots=True)
class M7ResolvedShadowPackage:
    package_directory: Path
    manifest: M7PackageManifest


@dataclass(frozen=True, slots=True)
class M7IncumbentPrediction:
    direction: tuple[float, float, float] | None
    range_exit: float | None


@dataclass(frozen=True, slots=True)
class M7IncumbentSnapshot:
    incumbent_id: str | None
    snapshot_id: str | None
    manifest_hash: str | None
    predictions: Mapping[tuple[str, int], M7IncumbentPrediction]


@dataclass(frozen=True, slots=True)
class M7ShadowRunResult:
    window_id: str
    bundle_id: ModelCapabilityBundle
    anchor_session: date
    status: str
    prediction_artifact_hash: str | None
    prediction_path: Path | None
    explanation_path: Path | None
    manifest_path: Path | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class M7ShadowLabelResult:
    window_id: str
    bundle_id: ModelCapabilityBundle
    anchor_session: date
    status: str
    label_artifact_hash: str
    sample_keys_hash: str
    label_path: Path


class M7ShadowInferenceRunner:
    def __init__(self, paths: LivePaths) -> None:
        self.paths = paths

    def run_due(
        self,
        *,
        generated_at: datetime,
        incumbent_snapshot_path: Path | None = None,
    ) -> tuple[M7ShadowRunResult, ...]:
        _require_utc(generated_at)
        root = self.paths.models / "m7-blind"
        if not root.is_dir():
            return ()
        results: list[M7ShadowRunResult] = []
        for manifest_path in sorted(root.glob("*/manifest.json")):
            ledger = M7BlindWindowLedger(manifest_path.parent)
            status = ledger.status()
            if status.state != "collecting":
                continue
            anchor = status.next_scheduled_session
            if anchor != generated_at.date():
                continue
            results.append(
                self._run_one(
                    ledger,
                    anchor=anchor,
                    generated_at=generated_at,
                    incumbent_snapshot_path=incumbent_snapshot_path,
                )
            )
        return tuple(results)

    def run_mature_labels(
        self,
        *,
        generated_at: datetime,
    ) -> tuple[M7ShadowLabelResult, ...]:
        """Materialize every available prediction whose 20-session label matured."""

        _require_utc(generated_at)
        root = self.paths.models / "m7-blind"
        if not root.is_dir():
            return ()
        results: list[M7ShadowLabelResult] = []
        for ledger_manifest in sorted(root.glob("*/manifest.json")):
            ledger = M7BlindWindowLedger(ledger_manifest.parent)
            status = ledger.status()
            if status.state in {"consumed", "invalidated"}:
                continue
            existing_labels = {
                path.stem for path in (ledger.directory / "labels").glob("*.json")
            }
            due_anchors = _due_label_anchors(
                ledger,
                generated_at=generated_at,
                existing_labels=existing_labels,
            )
            if not due_anchors:
                continue
            prepared = prepare_m7_feature_data(
                self.paths,
                development_cutoff=generated_at.date(),
            )
            if prepared.source_artifact.as_of.date() != generated_at.date():
                raise RuntimeError("m7_shadow_label_snapshot_not_current")
            for anchor in due_anchors:
                results.append(
                    _materialize_mature_label(
                        ledger,
                        prepared_frame=prepared.frame,
                        source_input_hash=prepared.source_artifact.input_hash,
                        anchor=anchor,
                        generated_at=generated_at,
                    )
                )
        return tuple(results)

    def _run_one(
        self,
        ledger: M7BlindWindowLedger,
        *,
        anchor: date,
        generated_at: datetime,
        incumbent_snapshot_path: Path | None = None,
    ) -> M7ShadowRunResult:
        artifact_root = ledger.directory / "artifacts" / anchor.isoformat()
        manifest_path = artifact_root / "manifest.json"
        if manifest_path.is_file():
            artifact_hash = _verify_existing_artifact_manifest(
                manifest_path,
                ledger=ledger,
                anchor=anchor,
            )
            ledger.record_prediction(
                anchor_session=anchor,
                generated_at=generated_at,
                prediction_artifact_hash=artifact_hash,
            )
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            return M7ShadowRunResult(
                window_id=ledger.manifest.window_id,
                bundle_id=ledger.manifest.bundle_id,
                anchor_session=anchor,
                status="available",
                prediction_artifact_hash=artifact_hash,
                prediction_path=artifact_root / str(payload["prediction_file"]),
                explanation_path=artifact_root / str(payload["explanation_file"]),
                manifest_path=manifest_path,
                reason=None,
            )
        if artifact_root.exists() and any(artifact_root.iterdir()):
            return self._record_missing(
                ledger,
                anchor=anchor,
                generated_at=generated_at,
                reason="m7_shadow_partial_artifact",
            )
        started = time.monotonic()
        try:
            package = _resolve_package(
                self.paths,
                package_sha256=ledger.manifest.package_sha256,
                bundle_id=ledger.manifest.bundle_id,
            )
            prepared = prepare_m7_feature_data(
                self.paths,
                development_cutoff=anchor,
            )
            if prepared.source_artifact.as_of.date() != anchor:
                raise RuntimeError("m7_shadow_feature_snapshot_not_current")
            latest = prepared.frame[
                (prepared.frame["session_date"] == anchor)
                & prepared.frame["eligible"].astype(bool)
            ].copy()
            latest.sort_values("symbol", inplace=True, kind="stable")
            if (
                len(latest) != 100
                or set(latest["symbol"].astype(str)) != set(SEED_UNIVERSE)
            ):
                raise RuntimeError("m7_shadow_seed100_not_ready")
            incumbent = _load_incumbent_predictions(
                incumbent_snapshot_path,
                ledger=ledger,
                anchor=anchor,
            )
            if ledger.manifest.bundle_id == "direction":
                direction_ensemble = load_m7_direction_package(
                    package.package_directory
                )
                predictions = {
                    horizon: direction_ensemble.predict(
                        prepared.frame, horizon=horizon
                    )
                    for horizon in (1, 5, 20)
                }
                rows = _direction_rows(
                    latest,
                    predictions=predictions,
                    package=package,
                    window_id=ledger.manifest.window_id,
                    anchor=anchor,
                    generated_at=generated_at,
                    incumbent=incumbent,
                )
                explanations = build_m7_direction_shadow_explanations(
                    direction_ensemble,
                    history=prepared.frame,
                    predictions=predictions,
                )
            else:
                range_ensemble = load_m7_range_package(package.package_directory)
                probabilities = range_ensemble.predict(latest)
                rows = _range_rows(
                    latest,
                    probabilities=probabilities,
                    package=package,
                    window_id=ledger.manifest.window_id,
                    anchor=anchor,
                    generated_at=generated_at,
                    incumbent=incumbent,
                )
                explanations = {
                    "schema_version": "m7-shadow-range-explanations-v1",
                    "status": "global_model_importance_available",
                    "prediction_count": 100,
                    "non_causal_caveat": True,
                    "caveat": "Associational explanation only; not a causal claim.",
                }
            prediction_path = artifact_root / "predictions.parquet"
            explanation_path = artifact_root / "explanations.json"
            _write_parquet_immutable(prediction_path, rows)
            write_immutable_json(explanation_path, explanations, pretty=False)
            total_seconds = time.monotonic() - started
            manifest_payload: dict[str, object] = {
                "schema_version": "m7-shadow-artifact-manifest-v1",
                "window_id": ledger.manifest.window_id,
                "bundle_id": ledger.manifest.bundle_id,
                "package_id": package.manifest.package_id,
                "package_sha256": package.manifest.package_sha256,
                "anchor_session": anchor.isoformat(),
                "generated_at": generated_at.isoformat(),
                "feature_input_hash": prepared.source_artifact.input_hash,
                "prediction_file": prediction_path.name,
                "prediction_sha256": file_sha256(prediction_path),
                "explanation_file": explanation_path.name,
                "explanation_sha256": file_sha256(explanation_path),
                "evidence_scope": "shadow_forward",
                "formal_eligible": False,
                "prediction_and_explanation_seconds": total_seconds,
                "inference_sla_budget_seconds": 300.0,
                "inference_sla_passed": total_seconds <= 300.0,
                "incumbent_id": ledger.manifest.incumbent_id,
                "incumbent_snapshot_id": incumbent.snapshot_id,
                "incumbent_snapshot_manifest_hash": incumbent.manifest_hash,
            }
            artifact_hash = write_immutable_json(
                manifest_path, manifest_payload, pretty=False
            )
            ledger.record_prediction(
                anchor_session=anchor,
                generated_at=generated_at,
                prediction_artifact_hash=artifact_hash,
            )
            return M7ShadowRunResult(
                window_id=ledger.manifest.window_id,
                bundle_id=ledger.manifest.bundle_id,
                anchor_session=anchor,
                status="available",
                prediction_artifact_hash=artifact_hash,
                prediction_path=prediction_path,
                explanation_path=explanation_path,
                manifest_path=manifest_path,
                reason=None,
            )
        except Exception as error:
            reason = str(error).strip() or type(error).__name__
            return self._record_missing(
                ledger,
                anchor=anchor,
                generated_at=generated_at,
                reason=reason[:200],
            )

    def _record_missing(
        self,
        ledger: M7BlindWindowLedger,
        *,
        anchor: date,
        generated_at: datetime,
        reason: str,
    ) -> M7ShadowRunResult:
        ledger.record_missing_prediction(
            anchor_session=anchor,
            recorded_at=generated_at,
            reason=reason,
        )
        return M7ShadowRunResult(
            window_id=ledger.manifest.window_id,
            bundle_id=ledger.manifest.bundle_id,
            anchor_session=anchor,
            status="missing",
            prediction_artifact_hash=None,
            prediction_path=None,
            explanation_path=None,
            manifest_path=None,
            reason=reason,
        )


def _resolve_package(
    paths: LivePaths,
    *,
    package_sha256: str,
    bundle_id: ModelCapabilityBundle,
) -> M7ResolvedShadowPackage:
    root = paths.models / "m7-packages"
    matches: list[M7ResolvedShadowPackage] = []
    if root.is_dir():
        for directory in sorted(
            path
            for path in root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ):
            manifest = load_m7_package(root, directory.name)
            if (
                manifest.package_sha256 == package_sha256
                and manifest.bundle_id == bundle_id
            ):
                matches.append(
                    M7ResolvedShadowPackage(
                        package_directory=directory,
                        manifest=manifest,
                    )
                )
    if len(matches) != 1:
        raise RuntimeError("m7_shadow_package_unavailable")
    return matches[0]


def _load_incumbent_predictions(
    snapshot_path: Path | None,
    *,
    ledger: M7BlindWindowLedger,
    anchor: date,
) -> M7IncumbentSnapshot:
    unavailable = M7IncumbentSnapshot(
        incumbent_id=ledger.manifest.incumbent_id,
        snapshot_id=None,
        manifest_hash=None,
        predictions={},
    )
    if snapshot_path is None or ledger.manifest.incumbent_id is None:
        return unavailable
    try:
        manifest = ResearchSnapshotManifest.model_validate_json(
            (snapshot_path / "manifest.json").read_bytes(), strict=True
        )
    except (OSError, ValueError) as error:
        raise RuntimeError("m7_shadow_incumbent_manifest_invalid") from error
    if (
        manifest.as_of.date() != anchor
        or manifest.model_version != ledger.manifest.incumbent_id
        or manifest.prediction_readiness.value != "forecast_ready"
    ):
        return unavailable
    table_entry = manifest.table("forecasts.parquet")
    forecast_path = snapshot_path / table_entry.path
    if (
        not forecast_path.is_file()
        or file_sha256(forecast_path) != table_entry.content_hash
    ):
        raise RuntimeError("m7_shadow_incumbent_artifact_invalid")
    frame = pq.read_table(forecast_path).to_pandas()
    if len(frame) != 100 or set(frame["symbol"].astype(str)) != set(SEED_UNIVERSE):
        raise RuntimeError("m7_shadow_incumbent_seed100_invalid")
    predictions: dict[tuple[str, int], M7IncumbentPrediction] = {}
    for row in frame.itertuples(index=False):
        if str(row.availability) != "available":
            continue
        try:
            payload = json.loads(str(row.payload_json))
            bundle = payload["bundle"]
            symbol = str(bundle["symbol"])
            if (
                symbol != str(row.symbol)
                or bundle["model_version"] != ledger.manifest.incumbent_id
                or datetime.fromisoformat(
                    str(bundle["as_of"]).replace("Z", "+00:00")
                ).date()
                != anchor
            ):
                raise RuntimeError("m7_shadow_incumbent_identity_invalid")
            for horizon_row in bundle["horizons"]:
                horizon = int(horizon_row["horizon"])
                direction = horizon_row["forecast_direction_probabilities"]
                probabilities = (
                    float(direction["down"]),
                    float(direction["flat"]),
                    float(direction["up"]),
                )
                if (
                    not np.all(np.isfinite(probabilities))
                    or not np.allclose(sum(probabilities), 1.0, atol=1e-9)
                ):
                    raise RuntimeError("m7_shadow_incumbent_probability_invalid")
                range_value = horizon_row.get("range_exit_probability")
                predictions[(symbol, horizon)] = M7IncumbentPrediction(
                    direction=probabilities,
                    range_exit=(
                        float(range_value) if range_value is not None else None
                    ),
                )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("m7_shadow_incumbent_payload_invalid") from error
    return M7IncumbentSnapshot(
        incumbent_id=ledger.manifest.incumbent_id,
        snapshot_id=str(manifest.snapshot_id),
        manifest_hash=manifest.manifest_hash,
        predictions=predictions,
    )


def _due_label_anchors(
    ledger: M7BlindWindowLedger,
    *,
    generated_at: datetime,
    existing_labels: set[str],
) -> tuple[date, ...]:
    due: list[date] = []
    prediction_root = ledger.directory / "predictions"
    if not prediction_root.is_dir():
        return ()
    for path in sorted(prediction_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "available" or path.stem in existing_labels:
            continue
        anchor = date.fromisoformat(path.stem)
        position = ledger.manifest.scheduled_sessions.index(anchor)
        maturity = ledger.manifest.scheduled_sessions[
            position + ledger.manifest.label_horizon_sessions
        ]
        if maturity <= generated_at.date():
            due.append(anchor)
    return tuple(due)


def _materialize_mature_label(
    ledger: M7BlindWindowLedger,
    *,
    prepared_frame: pd.DataFrame,
    source_input_hash: str,
    anchor: date,
    generated_at: datetime,
) -> M7ShadowLabelResult:
    artifact_root = ledger.directory / "artifacts" / anchor.isoformat()
    prediction_manifest = artifact_root / "manifest.json"
    _verify_existing_artifact_manifest(
        prediction_manifest,
        ledger=ledger,
        anchor=anchor,
    )
    manifest_payload = json.loads(prediction_manifest.read_text(encoding="utf-8"))
    prediction_path = artifact_root / str(manifest_payload["prediction_file"])
    predictions = pq.read_table(prediction_path).to_pandas()
    label_path = artifact_root / "labels.parquet"
    if label_path.is_file():
        labels = pq.read_table(label_path).to_pandas()
    else:
        labels = _build_label_rows(
            ledger,
            predictions=predictions,
            prepared_frame=prepared_frame,
            source_input_hash=source_input_hash,
            anchor=anchor,
            generated_at=generated_at,
        )
        _write_parquet_immutable(label_path, labels)
    sample_keys = _sample_keys(labels, anchor=anchor)
    if sample_keys != _sample_keys(predictions, anchor=anchor):
        raise RuntimeError("m7_shadow_label_sample_keys_mismatch")
    sample_keys_hash = deterministic_hash(sample_keys)
    label_hash = file_sha256(label_path)
    ledger.record_mature_label(
        anchor_session=anchor,
        matured_at=generated_at,
        label_artifact_hash=label_hash,
        sample_keys_hash=sample_keys_hash,
        sample_count=len(labels),
    )
    return M7ShadowLabelResult(
        window_id=ledger.manifest.window_id,
        bundle_id=ledger.manifest.bundle_id,
        anchor_session=anchor,
        status="available",
        label_artifact_hash=label_hash,
        sample_keys_hash=sample_keys_hash,
        label_path=label_path,
    )


def _build_label_rows(
    ledger: M7BlindWindowLedger,
    *,
    predictions: pd.DataFrame,
    prepared_frame: pd.DataFrame,
    source_input_hash: str,
    anchor: date,
    generated_at: datetime,
) -> pd.DataFrame:
    expected_horizons = {1, 5, 20} if ledger.manifest.bundle_id == "direction" else {20}
    expected_count = 300 if ledger.manifest.bundle_id == "direction" else 100
    if (
        len(predictions) != expected_count
        or set(predictions["symbol"].astype(str)) != set(SEED_UNIVERSE)
        or set(predictions["horizon"].astype(int)) != expected_horizons
        or set(predictions["anchor_session"].astype(str)) != {anchor.isoformat()}
        or set(predictions["package_sha256"].astype(str))
        != {ledger.manifest.package_sha256}
    ):
        raise RuntimeError("m7_shadow_label_prediction_identity_invalid")
    source = prepared_frame[prepared_frame["session_date"] == anchor].copy()
    source.sort_values("symbol", inplace=True, kind="stable")
    if len(source) != 100 or set(source["symbol"].astype(str)) != set(SEED_UNIVERSE):
        raise RuntimeError("m7_shadow_label_seed100_unavailable")
    source_by_symbol = source.set_index("symbol")
    if not source_by_symbol.index.is_unique:
        raise RuntimeError("m7_shadow_label_seed100_unavailable")
    rows: list[dict[str, object]] = []
    direction_names = {-1: "down", 0: "flat", 1: "up"}
    for prediction in predictions.itertuples(index=False):
        symbol = str(prediction.symbol)
        horizon = int(prediction.horizon)
        row: dict[str, object] = {
            "window_id": ledger.manifest.window_id,
            "package_sha256": ledger.manifest.package_sha256,
            "bundle_id": ledger.manifest.bundle_id,
            "symbol": symbol,
            "anchor_session": anchor,
            "horizon": horizon,
            "matured_at": generated_at,
            "label_source_input_hash": source_input_hash,
        }
        if ledger.manifest.bundle_id == "direction":
            value = source_by_symbol.at[symbol, f"direction_{horizon}"]
            if pd.isna(value):
                observed: object = None
            else:
                integer = int(value)
                if float(value) != float(integer) or integer not in direction_names:
                    raise RuntimeError("m7_shadow_direction_label_invalid")
                observed = direction_names[integer]
            row["observed_label"] = observed
            row["label_available"] = observed is not None
            realized_return = source_by_symbol.at[
                symbol, f"return_fwd_{horizon}"
            ]
            if pd.isna(realized_return):
                row["realized_return"] = None
            elif not np.isfinite(float(realized_return)):
                raise RuntimeError("m7_shadow_direction_return_invalid")
            else:
                row["realized_return"] = float(realized_return)
        else:
            observed_value = source_by_symbol.at[symbol, "range_exit_event_20"]
            event_day = source_by_symbol.at[symbol, "range_exit_day_20"]
            if pd.isna(observed_value) or pd.isna(event_day):
                row["observed_event"] = None
                row["event_day"] = None
                row["label_available"] = False
            else:
                observed_integer = int(observed_value)
                event_day_integer = int(event_day)
                if observed_integer not in {0, 1} or not 1 <= event_day_integer <= 20:
                    raise RuntimeError("m7_shadow_range_label_invalid")
                row["observed_event"] = observed_integer
                row["event_day"] = event_day_integer
                row["label_available"] = True
        rows.append(row)
    return pd.DataFrame(rows)


def _sample_keys(frame: pd.DataFrame, *, anchor: date) -> list[list[object]]:
    required = {"symbol", "anchor_session", "horizon"}
    if required - set(frame.columns):
        raise RuntimeError("m7_shadow_label_sample_keys_invalid")
    key_tuples = [
        (str(row.symbol), anchor.isoformat(), int(row.horizon))
        for row in frame.itertuples(index=False)
    ]
    if (
        len(set(key_tuples)) != len(key_tuples)
        or set(frame["anchor_session"].astype(str)) != {anchor.isoformat()}
    ):
        raise RuntimeError("m7_shadow_label_sample_keys_invalid")
    return [[symbol, session, horizon] for symbol, session, horizon in key_tuples]


def _direction_rows(
    latest: pd.DataFrame,
    *,
    predictions: dict[int, np.ndarray],
    package: M7ResolvedShadowPackage,
    window_id: str,
    anchor: date,
    generated_at: datetime,
    incumbent: M7IncumbentSnapshot,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    symbols = tuple(latest["symbol"].astype(str))
    for horizon in (1, 5, 20):
        values = np.asarray(predictions[horizon], dtype=np.float64)
        if (
            values.shape != (100, 3)
            or not np.all(np.isfinite(values))
            or np.any(values < 0.0)
            or np.any(values > 1.0)
            or not np.allclose(values.sum(axis=1), 1.0, atol=1e-9)
        ):
            raise RuntimeError("m7_shadow_direction_output_invalid")
        for index, symbol in enumerate(symbols):
            incumbent_row = incumbent.predictions.get((symbol, horizon))
            incumbent_probabilities = (
                incumbent_row.direction
                if incumbent_row is not None
                and incumbent_row.direction is not None
                else None
            )
            rows.append(
                {
                    "window_id": window_id,
                    "package_id": package.manifest.package_id,
                    "package_sha256": package.manifest.package_sha256,
                    "bundle_id": "direction",
                    "symbol": symbol,
                    "anchor_session": anchor,
                    "horizon": horizon,
                    "probability_down": float(values[index, 0]),
                    "probability_flat": float(values[index, 1]),
                    "probability_up": float(values[index, 2]),
                    "incumbent_id": incumbent.incumbent_id,
                    "incumbent_available": incumbent_probabilities is not None,
                    "incumbent_probability_down": (
                        incumbent_probabilities[0]
                        if incumbent_probabilities is not None
                        else None
                    ),
                    "incumbent_probability_flat": (
                        incumbent_probabilities[1]
                        if incumbent_probabilities is not None
                        else None
                    ),
                    "incumbent_probability_up": (
                        incumbent_probabilities[2]
                        if incumbent_probabilities is not None
                        else None
                    ),
                    "generated_at": generated_at,
                    "evidence_scope": "shadow_forward",
                    "formal_eligible": False,
                }
            )
    return pd.DataFrame(rows)


def _range_rows(
    latest: pd.DataFrame,
    *,
    probabilities: np.ndarray,
    package: M7ResolvedShadowPackage,
    window_id: str,
    anchor: date,
    generated_at: datetime,
    incumbent: M7IncumbentSnapshot,
) -> pd.DataFrame:
    values = np.asarray(probabilities, dtype=np.float64)
    if (
        values.shape != (100, 3)
        or not np.all(np.isfinite(values))
        or np.any(values < 0.0)
        or np.any(values > 1.0)
        or np.any(np.diff(values, axis=1) < -1e-12)
    ):
        raise RuntimeError("m7_shadow_range_output_invalid")
    rows: list[dict[str, object]] = []
    for index, symbol in enumerate(latest["symbol"].astype(str)):
        incumbent_row = incumbent.predictions.get((symbol, 20))
        incumbent_probability = (
            incumbent_row.range_exit if incumbent_row is not None else None
        )
        rows.append(
            {
                "window_id": window_id,
                "package_id": package.manifest.package_id,
                "package_sha256": package.manifest.package_sha256,
                "bundle_id": "range_exit_20",
                "symbol": symbol,
                "anchor_session": anchor,
                "horizon": 20,
                "range_task_eligible": str(latest.iloc[index]["regime"]) == "range",
                "probability_exit_by_1": float(values[index, 0]),
                "probability_exit_by_5": float(values[index, 1]),
                "probability_exit_by_20": float(values[index, 2]),
                "incumbent_id": incumbent.incumbent_id,
                "incumbent_available": incumbent_probability is not None,
                "incumbent_probability_exit_by_20": incumbent_probability,
                "generated_at": generated_at,
                "evidence_scope": "shadow_forward",
                "formal_eligible": False,
            }
        )
    return pd.DataFrame(rows)


def _write_parquet_immutable(path: Path, frame: pd.DataFrame) -> None:
    if path.exists():
        raise RuntimeError("m7_shadow_prediction_artifact_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        pq.write_table(
            pa.Table.from_pandas(frame, preserve_index=False),
            temporary,
            compression="zstd",
            write_statistics=True,
            coerce_timestamps="us",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _verify_existing_artifact_manifest(
    manifest_path: Path,
    *,
    ledger: M7BlindWindowLedger,
    anchor: date,
) -> str:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        prediction_path = manifest_path.parent / str(payload["prediction_file"])
        explanation_path = manifest_path.parent / str(payload["explanation_file"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("m7_shadow_artifact_manifest_invalid") from error
    if (
        payload.get("schema_version") != "m7-shadow-artifact-manifest-v1"
        or payload.get("window_id") != ledger.manifest.window_id
        or payload.get("package_sha256") != ledger.manifest.package_sha256
        or payload.get("anchor_session") != anchor.isoformat()
        or not prediction_path.is_file()
        or not explanation_path.is_file()
        or file_sha256(prediction_path) != payload.get("prediction_sha256")
        or file_sha256(explanation_path) != payload.get("explanation_sha256")
    ):
        raise RuntimeError("m7_shadow_artifact_manifest_invalid")
    return file_sha256(manifest_path)


def _require_utc(value: datetime) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError("m7_shadow_generated_at_must_be_utc")


__all__ = [
    "M7IncumbentPrediction",
    "M7IncumbentSnapshot",
    "M7ResolvedShadowPackage",
    "M7ShadowInferenceRunner",
    "M7ShadowLabelResult",
    "M7ShadowRunResult",
]
