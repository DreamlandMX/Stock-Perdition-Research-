"""Loadable M7 direction candidate artifacts with frozen preprocessing."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from datetime import datetime
import json
from pathlib import Path
from pathlib import PurePosixPath
import pickle
import time
from typing import Any, Literal, cast

import numpy as np
import pandas as pd
import psutil  # type: ignore[import-untyped]
import torch
from torch import nn

from stock_prediction.modeling.m7_candidates import (
    M7CatBoostCandidate,
    M7QuantileCDFCandidate,
    M7_QUANTILE_FIT_POLICY_SERIAL,
    M7_QUANTILE_FIT_POLICY_THREADPOOL,
    M7_QUANTILE_MAPPING_POLICY_REFERENCE,
    M7_QUANTILE_MAPPING_POLICY_SCIPY_COMPACT,
    M7_QUANTILE_SELECTION_POLICY_LOKY,
    M7_QUANTILE_SELECTION_POLICY_SERIAL,
    predict_m7_catboost,
    predict_m7_quantile_cdf,
)
from stock_prediction.modeling.m7_calibration import (
    M7BlendCandidate,
    M7DirectionCalibrator,
    M7FiniteBlend,
)
from stock_prediction.modeling.m7_development import M7DirectionFoldSelection
from stock_prediction.modeling.m7_drift import build_m7_drift_reference
from stock_prediction.modeling.m7_package import (
    M7PackageArtifactInput,
    M7PackageBuildSpec,
    detect_m7_runtime_compatibility,
    load_m7_package,
)
from stock_prediction.modeling.m7_deep_models import (
    DLinearDirectionModel,
    PatchTSTDirectionModel,
)
from stock_prediction.modeling.m7_explanations import (
    M7InferenceSLAReport,
    integrated_gradients_up_minus_down,
)
from stock_prediction.modeling.report import (
    deterministic_hash,
    file_sha256,
    write_immutable_json,
)


@dataclass(frozen=True, slots=True)
class M7DirectionFrozenMember:
    candidate_id: str
    family: str
    artifact: M7PackageArtifactInput
    model_kind: Literal["tree", "deep"]


@dataclass(frozen=True, slots=True)
class M7DirectionFrozenFold:
    fold_id: str
    horizon: int
    selection: M7DirectionFoldSelection | M7DirectionServingSelection
    members: tuple[M7DirectionFrozenMember, ...]


@dataclass(frozen=True, slots=True)
class M7DirectionServingSelection:
    classes: tuple[str, str, str]
    blend: M7FiniteBlend
    calibrators: Mapping[str, M7DirectionCalibrator]


@dataclass(frozen=True, slots=True)
class M7DirectionPublicationIdentity:
    package_id: str
    code_commit: str
    universe_version: str
    dataset_revision: str
    feature_set_version: str
    feature_input_hash: str
    label_version: str
    split_manifest_hash: str
    candidate_matrix_hash: str
    dependency_lock_hash: str
    created_at: datetime
    recipe_id: str = "candidate-recipe-seed100-targeted-v9"
    recipe_generation: int = 9


@dataclass(frozen=True, slots=True)
class M7DirectionPublication:
    build_spec: M7PackageBuildSpec
    artifacts: tuple[M7PackageArtifactInput, ...]
    sla: M7InferenceSLAReport
    runtime_observations: dict[str, object]


@dataclass(frozen=True, slots=True)
class M7DirectionFoldEnsemble:
    folds: tuple[M7DirectionFrozenFold, ...]
    required_folds: int = 4

    def predict(self, frame: pd.DataFrame, *, horizon: int) -> np.ndarray:
        if horizon not in {1, 5, 20}:
            raise ValueError("m7_direction_inference_horizon_invalid")
        selected = tuple(fold for fold in self.folds if fold.horizon == horizon)
        if (
            self.required_folds < 1
            or len(selected) != self.required_folds
            or len({fold.fold_id for fold in selected}) != self.required_folds
        ):
            raise ValueError("m7_direction_fold_ensemble_incomplete")
        fold_probabilities: list[np.ndarray] = []
        for fold in selected:
            if {member.candidate_id for member in fold.members} != set(
                fold.selection.blend.member_ids
            ):
                raise ValueError("m7_direction_fold_member_mismatch")
            calibrated: list[M7BlendCandidate] = []
            for member in fold.members:
                if member.model_kind == "tree":
                    loaded: Any = load_m7_direction_tree_candidate(
                        member.artifact.source_path
                    )
                    inference_frame = _latest_tree_inference_frame(frame)
                else:
                    loaded = load_m7_direction_deep_candidate(
                        member.artifact.source_path
                    )
                    inference_frame = frame
                raw = np.asarray(
                    loaded.predict(inference_frame, horizon=horizon), dtype=float
                )
                calibrator = fold.selection.calibrators.get(member.candidate_id)
                if calibrator is None:
                    raise ValueError("m7_direction_fold_calibrator_missing")
                calibrated.append(
                    M7BlendCandidate(
                        candidate_id=member.candidate_id,
                        family=member.family,
                        probabilities=calibrator.apply(
                            np.log(np.clip(raw, 1e-12, 1.0))
                        ),
                    )
                )
            fold_probabilities.append(fold.selection.blend.apply(calibrated))
        probabilities = np.asarray(
            np.mean(np.stack(fold_probabilities), axis=0), dtype=np.float64
        )
        if (
            probabilities.shape != (100, 3)
            or not np.all(np.isfinite(probabilities))
            or not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-9)
        ):
            raise ValueError("m7_direction_fold_ensemble_output_invalid")
        return probabilities


def _latest_tree_inference_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if {"symbol", "session_date"} - set(frame.columns):
        if len(frame) == 100:
            return frame.reset_index(drop=True)
        raise ValueError("m7_direction_tree_inference_identity_missing")
    sessions = pd.to_datetime(frame["session_date"], errors="coerce").dt.date
    if sessions.isna().any():
        raise ValueError("m7_direction_tree_inference_session_invalid")
    latest_session = max(sessions)
    selected = frame.loc[sessions == latest_session].copy()
    if "eligible" in selected:
        selected = selected[selected["eligible"].fillna(False).astype(bool)]
    selected.sort_values("symbol", inplace=True, kind="stable")
    if len(selected) != 100 or selected["symbol"].astype(str).nunique() != 100:
        raise ValueError("m7_direction_tree_inference_seed100_invalid")
    return selected.reset_index(drop=True)


def freeze_m7_direction_candidate(
    output_directory: Path,
    *,
    identity: M7DirectionPublicationIdentity,
    label_path: Path,
    label_schema_version: str,
    label_content_hash: str,
    frozen_folds: Mapping[int, tuple[M7DirectionFrozenFold, ...]],
    latest_seed100_history: pd.DataFrame,
    training_wall_seconds: float,
    training_cpu_seconds: float,
    peak_rss_bytes: int,
    peak_private_bytes: int,
    peak_gpu_memory_bytes: int,
    budget_seconds: float = 300.0,
) -> M7DirectionPublication:
    """Write, reload, benchmark, and explain all three frozen direction horizons."""

    if set(frozen_folds) != {1, 5, 20} or any(
        len(frozen_folds[horizon]) != 4 for horizon in (1, 5, 20)
    ):
        raise ValueError("m7_direction_package_folds_incomplete")
    if budget_seconds <= 0:
        raise ValueError("m7_direction_seed100_sla_budget_invalid")
    folds = tuple(fold for horizon in (1, 5, 20) for fold in frozen_folds[horizon])
    model_artifacts = tuple(
        member.artifact for fold in folds for member in fold.members
    )
    if len({artifact.artifact_id for artifact in model_artifacts}) != len(
        model_artifacts
    ):
        raise ValueError("m7_direction_package_model_artifacts_duplicate")
    output_directory.mkdir(parents=True, exist_ok=True)
    calibration_path = output_directory / "direction-calibration.json"
    explanation_path = output_directory / "direction-explanation.json"
    drift_reference_path = output_directory / "direction-drift-reference.json"
    write_immutable_json(
        calibration_path,
        {
            "schema_version": "m7-direction-calibration-ensemble-v1",
            "folds": [_frozen_fold_payload(fold) for fold in folds],
        },
        pretty=False,
    )
    ensemble = M7DirectionFoldEnsemble(folds)
    process = psutil.Process()
    cpu_started = time.process_time()
    inference_started = time.monotonic()
    predictions = {
        horizon: ensemble.predict(latest_seed100_history, horizon=horizon)
        for horizon in (1, 5, 20)
    }
    inference_finished = time.monotonic()
    if any(
        values.shape != (100, 3)
        or not np.all(np.isfinite(values))
        or not np.allclose(values.sum(axis=1), 1.0, atol=1e-9)
        for values in predictions.values()
    ):
        raise ValueError("m7_direction_seed100_probability_artifact_invalid")
    explanation_started = time.monotonic()
    explanation_payload = _build_explanation_probe(
        ensemble,
        folds=folds,
        history=latest_seed100_history,
        predictions=predictions,
    )
    write_immutable_json(explanation_path, explanation_payload, pretty=False)
    drift_reference = build_m7_drift_reference(
        latest_seed100_history,
        prediction_values={
            f"direction_h{horizon}_{class_name}": predictions[horizon][:, class_index]
            for horizon in (1, 5, 20)
            for class_index, class_name in enumerate(("down", "flat", "up"))
        },
        source_identity_hash=identity.feature_input_hash,
    )
    write_immutable_json(
        drift_reference_path,
        drift_reference.model_dump(mode="json"),
        pretty=False,
    )
    explanation_finished = time.monotonic()
    inference_seconds = inference_finished - inference_started
    explanation_seconds = explanation_finished - explanation_started
    total_seconds = explanation_finished - inference_started
    sla = M7InferenceSLAReport(
        inference_seconds=inference_seconds,
        explanation_seconds=explanation_seconds,
        total_seconds=total_seconds,
        seed100_row_count=100,
        inference_sla_passed=inference_seconds <= budget_seconds,
        explanation_sla_passed=total_seconds <= budget_seconds,
        probability_artifact_valid=True,
        explanation_availability="available",
        warning=None,
        budget_seconds=budget_seconds,
    )
    calibration_artifact = _modeling_artifact(
        "direction-calibration-ensemble",
        "calibration",
        calibration_path,
        "m7-direction-calibration-ensemble-v1",
    )
    explanation_artifact = _modeling_artifact(
        "direction-explanation-contract",
        "explanation",
        explanation_path,
        "m7-direction-explanation-contract-v1",
    )
    drift_reference_artifact = _modeling_artifact(
        "direction-drift-reference",
        "explanation",
        drift_reference_path,
        "m7-drift-reference-v1",
    )
    label_artifact = M7PackageArtifactInput(
        artifact_id="direction-label-thresholds",
        category="label_threshold",
        source_path=label_path,
        schema_version=label_schema_version,
        content_hash=label_content_hash,
    )
    build_spec = M7PackageBuildSpec(
        package_id=identity.package_id,
        bundle_id="direction",
        recipe_id=identity.recipe_id,
        recipe_generation=identity.recipe_generation,
        code_commit=identity.code_commit,
        universe_version=identity.universe_version,
        dataset_revision=identity.dataset_revision,
        feature_set_version=identity.feature_set_version,
        feature_input_hash=identity.feature_input_hash,
        label_version=identity.label_version,
        split_manifest_hash=identity.split_manifest_hash,
        candidate_matrix_hash=identity.candidate_matrix_hash,
        dependency_lock_hash=identity.dependency_lock_hash,
        runtime_compatibility=detect_m7_runtime_compatibility(),
        evidence_scope="optimization_reused",
        blind_window_id=None,
        created_at=identity.created_at,
    )
    current_memory = process.memory_info()
    runtime_observations: dict[str, object] = {
        "wall_seconds": training_wall_seconds + total_seconds,
        "cpu_seconds": training_cpu_seconds + (time.process_time() - cpu_started),
        "peak_rss_bytes": max(peak_rss_bytes, int(current_memory.rss)),
        "peak_private_bytes": max(
            peak_private_bytes, int(getattr(current_memory, "private", 0))
        ),
        "peak_gpu_memory_bytes": peak_gpu_memory_bytes,
        "gpu_device": "cpu",
        "inference_device": "cpu",
        "inference_seconds": inference_seconds,
        "explanation_seconds": explanation_seconds,
        "seed100_row_count": 100,
        "deep_integrated_gradients_steps": 32,
    }
    return M7DirectionPublication(
        build_spec=build_spec,
        artifacts=(
            label_artifact,
            *model_artifacts,
            calibration_artifact,
            explanation_artifact,
            drift_reference_artifact,
        ),
        sla=sla,
        runtime_observations=runtime_observations,
    )


def load_m7_direction_package(package_directory: Path) -> M7DirectionFoldEnsemble:
    """Verify a published direction package and reconstruct its serving ensemble."""

    manifest = load_m7_package(package_directory.parent, package_directory.name)
    if manifest.bundle_id != "direction":
        raise ValueError("m7_direction_package_bundle_mismatch")
    calibration_entries = [
        row
        for row in manifest.calibration_artifacts
        if row.schema_version == "m7-direction-calibration-ensemble-v1"
    ]
    if len(calibration_entries) != 1:
        raise ValueError("m7_direction_package_calibration_invalid")
    calibration_path = package_directory / Path(
        *PurePosixPath(calibration_entries[0].relative_path).parts
    )
    try:
        payload = json.loads(calibration_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "m7-direction-calibration-ensemble-v1":
            raise ValueError
        fold_rows = payload["folds"]
        model_entries = {row.artifact_id: row for row in manifest.model_artifacts}
        folds: list[M7DirectionFrozenFold] = []
        for row in fold_rows:
            blend_row = row["blend"]
            blend = M7FiniteBlend(
                member_ids=tuple(str(value) for value in blend_row["member_ids"]),
                weights=tuple(float(value) for value in blend_row["weights"]),
                eligible_candidate_ids=tuple(
                    str(value) for value in blend_row["eligible_candidate_ids"]
                ),
                fit_evidence_hash=str(blend_row["fit_evidence_hash"]),
                calibration_brier=float(blend_row["calibration_brier"]),
                blend_hash=str(blend_row["blend_hash"]),
            )
            calibrators = {
                str(candidate_id): M7DirectionCalibrator(
                    method=value["method"],
                    scales=_float_triple(value["scales"]),
                    biases=_float_triple(value["biases"]),
                    fit_evidence_hash=str(value["fit_evidence_hash"]),
                    oof_fold_hash=str(value["oof_fold_hash"]),
                    raw_brier=float(value["raw_brier"]),
                    calibrated_brier=float(value["calibrated_brier"]),
                    calibrator_hash=str(value["calibrator_hash"]),
                )
                for candidate_id, value in row["calibrators"].items()
            }
            members: list[M7DirectionFrozenMember] = []
            for member_row in row["members"]:
                artifact_id = str(member_row["artifact_id"])
                entry = model_entries[artifact_id]
                source_path = package_directory / Path(
                    *PurePosixPath(entry.relative_path).parts
                )
                members.append(
                    M7DirectionFrozenMember(
                        candidate_id=str(member_row["candidate_id"]),
                        family=str(member_row["family"]),
                        artifact=M7PackageArtifactInput(
                            artifact_id=entry.artifact_id,
                            category="model",
                            source_path=source_path,
                            schema_version=entry.schema_version,
                            content_hash=entry.content_hash,
                        ),
                        model_kind=member_row["model_kind"],
                    )
                )
            classes = tuple(str(value) for value in row["classes"])
            if classes != ("down", "flat", "up"):
                raise ValueError
            folds.append(
                M7DirectionFrozenFold(
                    fold_id=str(row["fold_id"]),
                    horizon=int(row["horizon"]),
                    selection=M7DirectionServingSelection(
                        classes=("down", "flat", "up"),
                        blend=blend,
                        calibrators=calibrators,
                    ),
                    members=tuple(members),
                )
            )
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise ValueError("m7_direction_package_calibration_invalid") from error
    return M7DirectionFoldEnsemble(tuple(folds))


def _build_explanation_probe(
    ensemble: M7DirectionFoldEnsemble,
    *,
    folds: tuple[M7DirectionFrozenFold, ...],
    history: pd.DataFrame,
    predictions: Mapping[int, np.ndarray],
) -> dict[str, object]:
    latest_session = pd.to_datetime(history["session_date"]).dt.date.max()
    latest = history[
        pd.to_datetime(history["session_date"]).dt.date == latest_session
    ].sort_values("symbol", kind="stable")
    if len(latest) != 100:
        raise ValueError("m7_direction_explanation_seed100_incomplete")
    candidate_features: set[str] = set()
    deep_ig: list[dict[str, object]] = []
    seen_deep: set[tuple[str, int]] = set()
    for fold in folds:
        for member in fold.members:
            if member.model_kind == "tree":
                loaded = load_m7_direction_tree_candidate(member.artifact.source_path)
                candidate_features.update(loaded.candidate.feature_names)
            elif (member.artifact.artifact_id, fold.horizon) not in seen_deep:
                loaded_deep = load_m7_direction_deep_candidate(
                    member.artifact.source_path
                )
                candidate_features.update(loaded_deep.feature_names)
                zero = loaded_deep.integrated_gradients(
                    history, horizon=fold.horizon, baseline="zero"
                )
                median_ig = loaded_deep.integrated_gradients(
                    history, horizon=fold.horizon, baseline="median"
                )
                deep_ig.append(
                    {
                        "artifact_id": member.artifact.artifact_id,
                        "horizon": fold.horizon,
                        "steps": 32,
                        "zero_mean_absolute": float(np.mean(np.abs(zero))),
                        "median_mean_absolute": float(np.mean(np.abs(median_ig))),
                    }
                )
                seen_deep.add((member.artifact.artifact_id, fold.horizon))
    numeric = [
        name
        for name in sorted(candidate_features)
        if name in history and pd.api.types.is_numeric_dtype(history[name])
    ]
    ranked = sorted(
        numeric,
        key=lambda name: (
            -float(pd.to_numeric(latest[name], errors="coerce").var() or 0.0),
            name,
        ),
    )[:5]
    if not ranked:
        raise ValueError("m7_direction_explanation_features_unavailable")
    zero_contributions: dict[int, np.ndarray] = {}
    median_contributions: dict[int, np.ndarray] = {}
    for horizon in (1, 5, 20):
        base_target = predictions[horizon][:, 2] - predictions[horizon][:, 0]
        zero_rows: list[np.ndarray] = []
        median_rows: list[np.ndarray] = []
        for name in ranked:
            zero_frame = history.copy()
            zero_frame[name] = 0.0
            median_frame = history.copy()
            feature_median = float(
                pd.to_numeric(history[name], errors="coerce").median()
            )
            if not np.isfinite(feature_median):
                feature_median = 0.0
            median_frame[name] = feature_median
            zero_prediction = ensemble.predict(zero_frame, horizon=horizon)
            median_prediction = ensemble.predict(median_frame, horizon=horizon)
            zero_rows.append(
                base_target - (zero_prediction[:, 2] - zero_prediction[:, 0])
            )
            median_rows.append(
                base_target - (median_prediction[:, 2] - median_prediction[:, 0])
            )
        zero_contributions[horizon] = np.column_stack(zero_rows)
        median_contributions[horizon] = np.column_stack(median_rows)
    prediction_rows: list[dict[str, object]] = []
    for horizon in (1, 5, 20):
        zero = zero_contributions[horizon]
        median = median_contributions[horizon]
        averaged = (zero + median) / 2.0
        stable = (np.sign(zero) == np.sign(median)) & (np.sign(zero) != 0)
        for row_index, symbol in enumerate(latest["symbol"].astype(str)):
            indexes = [
                index for index in range(len(ranked)) if bool(stable[row_index, index])
            ]
            positive = sorted(
                (index for index in indexes if averaged[row_index, index] > 0),
                key=lambda index: (
                    -abs(float(averaged[row_index, index])),
                    ranked[index],
                ),
            )[:5]
            negative = sorted(
                (index for index in indexes if averaged[row_index, index] < 0),
                key=lambda index: (
                    -abs(float(averaged[row_index, index])),
                    ranked[index],
                ),
            )[:5]
            prediction_rows.append(
                {
                    "symbol": symbol,
                    "horizon": horizon,
                    "status": "available" if indexes else "explanation_unstable",
                    "top_positive": [
                        {
                            "feature_name": ranked[index],
                            "contribution": float(averaged[row_index, index]),
                        }
                        for index in positive
                    ],
                    "top_negative": [
                        {
                            "feature_name": ranked[index],
                            "contribution": float(averaged[row_index, index]),
                        }
                        for index in negative
                    ],
                    "stable_factor_count": len(indexes),
                }
            )
    baseline_hash = deterministic_hash(
        {
            "features": ranked,
            "zero": [0.0 for _name in ranked],
            "median": [
                float(pd.to_numeric(history[name], errors="coerce").median())
                for name in ranked
            ],
        }
    )
    return {
        "schema_version": "m7-direction-explanation-contract-v1",
        "method": "dual_baseline_ensemble_sensitivity-v1",
        "target": "up_relative_to_down",
        "feature_names": ranked,
        "baseline_artifact_sha256": baseline_hash,
        "prediction_probe": prediction_rows,
        "deep_integrated_gradients": deep_ig,
        "deep_integrated_gradients_steps": 32,
        "non_causal_caveat": True,
        "caveat": "Associational explanation only; not a causal claim.",
    }


def build_m7_direction_shadow_explanations(
    ensemble: M7DirectionFoldEnsemble,
    *,
    history: pd.DataFrame,
    predictions: Mapping[int, np.ndarray],
) -> dict[str, object]:
    """Generate the governed dual-baseline explanation payload for one shadow day."""

    return _build_explanation_probe(
        ensemble,
        folds=ensemble.folds,
        history=history,
        predictions=predictions,
    )


def _frozen_fold_payload(fold: M7DirectionFrozenFold) -> dict[str, object]:
    return {
        "fold_id": fold.fold_id,
        "horizon": fold.horizon,
        "classes": list(fold.selection.classes),
        "blend": {
            "member_ids": list(fold.selection.blend.member_ids),
            "weights": list(fold.selection.blend.weights),
            "eligible_candidate_ids": list(fold.selection.blend.eligible_candidate_ids),
            "fit_evidence_hash": fold.selection.blend.fit_evidence_hash,
            "calibration_brier": fold.selection.blend.calibration_brier,
            "blend_hash": fold.selection.blend.blend_hash,
        },
        "calibrators": {
            candidate_id: {
                "method": calibrator.method,
                "scales": list(calibrator.scales),
                "biases": list(calibrator.biases),
                "calibrator_hash": calibrator.calibrator_hash,
                "fit_evidence_hash": calibrator.fit_evidence_hash,
                "oof_fold_hash": calibrator.oof_fold_hash,
                "raw_brier": calibrator.raw_brier,
                "calibrated_brier": calibrator.calibrated_brier,
            }
            for candidate_id, calibrator in fold.selection.calibrators.items()
            if candidate_id in fold.selection.blend.member_ids
        },
        "members": [
            {
                "candidate_id": member.candidate_id,
                "family": member.family,
                "artifact_id": member.artifact.artifact_id,
                "model_kind": member.model_kind,
            }
            for member in fold.members
        ],
    }


def _modeling_artifact(
    artifact_id: str, category: str, path: Path, schema_version: str
) -> M7PackageArtifactInput:
    return M7PackageArtifactInput.model_validate(
        {
            "artifact_id": artifact_id,
            "category": category,
            "source_path": path,
            "schema_version": schema_version,
            "content_hash": file_sha256(path),
        },
        strict=True,
    )


@dataclass(frozen=True, slots=True)
class M7LoadedDirectionTreeCandidate:
    candidate: M7CatBoostCandidate | M7QuantileCDFCandidate
    numeric_medians: dict[str, float]
    categorical_vocabularies: dict[str, frozenset[str]]
    quantile_mapping_policy: str
    fit_execution_policy: str
    fit_parallel_workers: int
    selection_execution_policy: str
    selection_parallel_workers: int

    @property
    def configuration_id(self) -> str:
        return self.candidate.configuration_id

    @property
    def family(self) -> str:
        if isinstance(self.candidate, M7CatBoostCandidate):
            return "catboost_multiclass"
        return "quantile_cdf"

    def predict(self, frame: pd.DataFrame, *, horizon: int) -> np.ndarray:
        if horizon not in {1, 5, 20}:
            raise ValueError("m7_direction_inference_horizon_invalid")
        names = self.candidate.feature_names
        if set(names) - set(frame.columns):
            raise ValueError("m7_direction_inference_features_missing")
        transformed = frame.loc[:, names].copy()
        for name, median in self.numeric_medians.items():
            transformed[name] = pd.to_numeric(
                transformed[name], errors="coerce"
            ).fillna(median)
        for name, vocabulary in self.categorical_vocabularies.items():
            values = transformed[name].fillna("unavailable").astype(str)
            transformed[name] = values.where(values.isin(vocabulary), "unavailable")
        if isinstance(self.candidate, M7CatBoostCandidate):
            return predict_m7_catboost(self.candidate, transformed)
        if "atr_pct_20" not in frame:
            raise ValueError("m7_direction_threshold_feature_missing")
        thresholds = np.maximum(
            0.002,
            0.25
            * pd.to_numeric(frame["atr_pct_20"], errors="coerce").to_numpy(
                dtype=np.float64
            )
            * np.sqrt(float(horizon)),
        )
        if not np.all(np.isfinite(thresholds)):
            raise ValueError("m7_direction_threshold_invalid")
        return predict_m7_quantile_cdf(
            self.candidate,
            transformed,
            direction_thresholds=thresholds,
            mapping_policy=self.quantile_mapping_policy,
        )


@dataclass(frozen=True, slots=True)
class M7LoadedDirectionDeepCandidate:
    configuration_id: str
    family: str
    model: nn.Module
    feature_names: tuple[str, ...]
    feature_medians: dict[str, float]
    sequence_length: int

    def predict(self, frame: pd.DataFrame, *, horizon: int) -> np.ndarray:
        if horizon not in {1, 5, 20}:
            raise ValueError("m7_direction_inference_horizon_invalid")
        sequences = self._latest_sequences(frame)
        self.model.eval()
        with torch.no_grad():
            logits = self.model(sequences)
            if not isinstance(logits, dict) or horizon not in logits:
                raise ValueError("m7_direction_deep_output_invalid")
            probabilities = torch.softmax(logits[horizon], dim=1).cpu().numpy()
        if probabilities.shape != (100, 3) or not np.all(np.isfinite(probabilities)):
            raise ValueError("m7_direction_deep_output_invalid")
        return probabilities

    def _latest_sequences(self, frame: pd.DataFrame) -> torch.Tensor:
        required = {"symbol", "session_date", *self.feature_names}
        if required - set(frame.columns):
            raise ValueError("m7_direction_deep_inference_features_missing")
        source = frame.loc[:, tuple(required)].copy()
        source["session_date"] = pd.to_datetime(source["session_date"]).dt.date
        source.sort_values(["symbol", "session_date"], inplace=True, kind="stable")
        latest_session = max(source["session_date"])
        windows: list[np.ndarray] = []
        for _symbol, group in source.groupby("symbol", sort=True):
            if group.iloc[-1]["session_date"] != latest_session:
                continue
            values = group.loc[:, self.feature_names].copy()
            for name, median in self.feature_medians.items():
                values[name] = pd.to_numeric(values[name], errors="coerce").fillna(
                    median
                )
            if len(values) < self.sequence_length:
                raise ValueError("m7_direction_deep_history_insufficient")
            window = values.iloc[-self.sequence_length :].to_numpy(dtype=np.float32)
            if not np.all(np.isfinite(window)):
                raise ValueError("m7_direction_deep_input_invalid")
            windows.append(window)
        if len(windows) != 100:
            raise ValueError("m7_direction_deep_seed100_incomplete")
        return torch.as_tensor(np.stack(windows), dtype=torch.float32)

    def integrated_gradients(
        self, frame: pd.DataFrame, *, horizon: int, baseline: Literal["zero", "median"]
    ) -> np.ndarray:
        values = self._latest_sequences(frame)
        if baseline == "zero":
            reference = torch.zeros_like(values)
        elif baseline == "median":
            medians = torch.as_tensor(
                [self.feature_medians[name] for name in self.feature_names],
                dtype=torch.float32,
            )
            reference = medians.view(1, 1, -1).expand_as(values).clone()
        else:
            raise ValueError("m7_direction_deep_baseline_invalid")

        def head(features: torch.Tensor) -> torch.Tensor:
            output = self.model(features)
            if not isinstance(output, dict) or horizon not in output:
                raise ValueError("m7_direction_deep_output_invalid")
            selected = output[horizon]
            if not isinstance(selected, torch.Tensor):
                raise ValueError("m7_direction_deep_output_invalid")
            return selected

        attribution = integrated_gradients_up_minus_down(
            head,
            values,
            reference,
            steps=32,
        )
        return np.asarray(
            attribution.detach().cpu().numpy().sum(axis=1), dtype=np.float64
        )


def write_m7_direction_tree_candidate(
    path: Path,
    *,
    candidate: M7CatBoostCandidate | M7QuantileCDFCandidate,
    training_frame: pd.DataFrame,
    artifact_id: str | None = None,
) -> M7PackageArtifactInput:
    names = candidate.feature_names
    if set(names) - set(training_frame.columns):
        raise ValueError("m7_direction_training_features_missing")
    categorical = (
        set(candidate.categorical_feature_names)
        if isinstance(candidate, M7CatBoostCandidate)
        else set()
    )
    numeric = tuple(name for name in names if name not in categorical)
    medians = {
        name: float(pd.to_numeric(training_frame[name], errors="coerce").median())
        for name in numeric
    }
    medians = {
        name: value if np.isfinite(value) else 0.0 for name, value in medians.items()
    }
    vocabularies = {
        name: frozenset(
            {
                *training_frame[name].dropna().astype(str),
                "unavailable",
            }
        )
        for name in categorical
    }
    schema_version = "m7-direction-tree-candidate-v1"
    quantile_mapping_policy: str | None = None
    fit_execution_policy = M7_QUANTILE_FIT_POLICY_SERIAL
    fit_parallel_workers = 1
    selection_execution_policy = M7_QUANTILE_SELECTION_POLICY_SERIAL
    selection_parallel_workers = 1
    if isinstance(candidate, M7QuantileCDFCandidate):
        quantile_mapping_policy = getattr(
            candidate,
            "quantile_mapping_policy",
            M7_QUANTILE_MAPPING_POLICY_REFERENCE,
        )
        fit_execution_policy = getattr(
            candidate,
            "fit_execution_policy",
            M7_QUANTILE_FIT_POLICY_SERIAL,
        )
        fit_parallel_workers = getattr(candidate, "fit_parallel_workers", 1)
        selection_execution_policy = getattr(
            candidate,
            "selection_execution_policy",
            M7_QUANTILE_SELECTION_POLICY_SERIAL,
        )
        selection_parallel_workers = getattr(
            candidate,
            "selection_parallel_workers",
            1,
        )
        serial_execution = (
            fit_execution_policy == M7_QUANTILE_FIT_POLICY_SERIAL
            and fit_parallel_workers == 1
            and selection_execution_policy == M7_QUANTILE_SELECTION_POLICY_SERIAL
            and selection_parallel_workers == 1
        )
        v9_execution = (
            fit_execution_policy == M7_QUANTILE_FIT_POLICY_THREADPOOL
            and fit_parallel_workers == 4
            and selection_execution_policy == M7_QUANTILE_SELECTION_POLICY_LOKY
            and selection_parallel_workers == 8
        )
        if (
            quantile_mapping_policy == M7_QUANTILE_MAPPING_POLICY_SCIPY_COMPACT
            and v9_execution
        ):
            schema_version = "m7-direction-tree-candidate-v3"
        elif (
            quantile_mapping_policy == M7_QUANTILE_MAPPING_POLICY_SCIPY_COMPACT
            and serial_execution
        ):
            schema_version = "m7-direction-tree-candidate-v2"
        elif quantile_mapping_policy != M7_QUANTILE_MAPPING_POLICY_REFERENCE:
            raise ValueError("m7_direction_tree_artifact_policy_invalid")
        elif not serial_execution:
            raise ValueError("m7_direction_tree_artifact_policy_invalid")
    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "candidate": candidate,
        "numeric_medians": medians,
        "categorical_vocabularies": vocabularies,
    }
    if schema_version in {
        "m7-direction-tree-candidate-v2",
        "m7-direction-tree-candidate-v3",
    }:
        payload["quantile_mapping_policy"] = quantile_mapping_policy
    if schema_version == "m7-direction-tree-candidate-v3":
        payload.update(
            fit_execution_policy=fit_execution_policy,
            fit_parallel_workers=fit_parallel_workers,
            selection_execution_policy=selection_execution_policy,
            selection_parallel_workers=selection_parallel_workers,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            with path.open("rb") as stream:
                existing = pickle.load(stream)  # noqa: S301 - internal resume artifact
            existing_candidate = existing["candidate"]
            if (
                existing.get("schema_version") != schema_version
                or existing_candidate.configuration_id != candidate.configuration_id
            ):
                raise ValueError("m7_direction_tree_artifact_resume_mismatch")
        except (OSError, KeyError, AttributeError, pickle.PickleError) as error:
            raise ValueError("m7_direction_tree_artifact_resume_mismatch") from error
        return M7PackageArtifactInput(
            artifact_id=artifact_id or candidate.configuration_id,
            category="model",
            source_path=path,
            schema_version=schema_version,
            content_hash=file_sha256(path),
        )
    try:
        with path.open("xb") as stream:
            pickle.dump(payload, stream, protocol=5)
    except (OSError, pickle.PickleError) as error:
        raise ValueError("m7_direction_tree_artifact_write_failed") from error
    return M7PackageArtifactInput(
        artifact_id=artifact_id or candidate.configuration_id,
        category="model",
        source_path=path,
        schema_version=schema_version,
        content_hash=file_sha256(path),
    )


def load_m7_direction_tree_candidate(path: Path) -> M7LoadedDirectionTreeCandidate:
    try:
        with path.open("rb") as stream:
            payload = pickle.load(stream)  # noqa: S301 - hash-verified internal package
        candidate = payload["candidate"]
        medians = payload["numeric_medians"]
        vocabularies = payload["categorical_vocabularies"]
        schema_version = payload.get("schema_version")
        fit_execution_policy = M7_QUANTILE_FIT_POLICY_SERIAL
        fit_parallel_workers = 1
        selection_execution_policy = M7_QUANTILE_SELECTION_POLICY_SERIAL
        selection_parallel_workers = 1
        if (
            not isinstance(candidate, (M7CatBoostCandidate, M7QuantileCDFCandidate))
            or not isinstance(medians, dict)
            or not isinstance(vocabularies, dict)
        ):
            raise ValueError
        if isinstance(candidate, M7CatBoostCandidate):
            if schema_version != "m7-direction-tree-candidate-v1":
                raise ValueError
            quantile_mapping_policy = M7_QUANTILE_MAPPING_POLICY_REFERENCE
        elif schema_version == "m7-direction-tree-candidate-v1":
            quantile_mapping_policy = getattr(
                candidate,
                "quantile_mapping_policy",
                M7_QUANTILE_MAPPING_POLICY_REFERENCE,
            )
            if (
                quantile_mapping_policy != M7_QUANTILE_MAPPING_POLICY_REFERENCE
                or getattr(
                    candidate,
                    "fit_execution_policy",
                    M7_QUANTILE_FIT_POLICY_SERIAL,
                )
                != M7_QUANTILE_FIT_POLICY_SERIAL
                or getattr(candidate, "fit_parallel_workers", 1) != 1
                or getattr(
                    candidate,
                    "selection_execution_policy",
                    M7_QUANTILE_SELECTION_POLICY_SERIAL,
                )
                != M7_QUANTILE_SELECTION_POLICY_SERIAL
                or getattr(candidate, "selection_parallel_workers", 1) != 1
            ):
                raise ValueError
        elif schema_version == "m7-direction-tree-candidate-v2":
            quantile_mapping_policy = payload.get("quantile_mapping_policy")
            if (
                quantile_mapping_policy != M7_QUANTILE_MAPPING_POLICY_SCIPY_COMPACT
                or not hasattr(candidate, "quantile_mapping_policy")
                or candidate.quantile_mapping_policy != quantile_mapping_policy
                or getattr(
                    candidate,
                    "fit_execution_policy",
                    M7_QUANTILE_FIT_POLICY_SERIAL,
                )
                != M7_QUANTILE_FIT_POLICY_SERIAL
                or getattr(candidate, "fit_parallel_workers", 1) != 1
                or getattr(
                    candidate,
                    "selection_execution_policy",
                    M7_QUANTILE_SELECTION_POLICY_SERIAL,
                )
                != M7_QUANTILE_SELECTION_POLICY_SERIAL
                or getattr(candidate, "selection_parallel_workers", 1) != 1
            ):
                raise ValueError
        elif schema_version == "m7-direction-tree-candidate-v3":
            quantile_mapping_policy = payload.get("quantile_mapping_policy")
            fit_execution_policy = payload.get("fit_execution_policy")
            fit_parallel_workers = payload.get("fit_parallel_workers")
            selection_execution_policy = payload.get("selection_execution_policy")
            selection_parallel_workers = payload.get("selection_parallel_workers")
            if (
                quantile_mapping_policy != M7_QUANTILE_MAPPING_POLICY_SCIPY_COMPACT
                or fit_execution_policy != M7_QUANTILE_FIT_POLICY_THREADPOOL
                or fit_parallel_workers != 4
                or selection_execution_policy != M7_QUANTILE_SELECTION_POLICY_LOKY
                or selection_parallel_workers != 8
                or not isinstance(candidate, M7QuantileCDFCandidate)
                or candidate.quantile_mapping_policy != quantile_mapping_policy
                or candidate.fit_execution_policy != fit_execution_policy
                or candidate.fit_parallel_workers != fit_parallel_workers
                or candidate.selection_execution_policy != selection_execution_policy
                or candidate.selection_parallel_workers != selection_parallel_workers
            ):
                raise ValueError
        else:
            raise ValueError
    except (KeyError, OSError, TypeError, ValueError, pickle.PickleError) as error:
        raise ValueError("m7_direction_tree_artifact_invalid") from error
    return M7LoadedDirectionTreeCandidate(
        candidate=candidate,
        numeric_medians={str(name): float(value) for name, value in medians.items()},
        categorical_vocabularies={
            str(name): frozenset(str(value) for value in values)
            for name, values in vocabularies.items()
        },
        quantile_mapping_policy=quantile_mapping_policy,
        fit_execution_policy=fit_execution_policy,
        fit_parallel_workers=fit_parallel_workers,
        selection_execution_policy=selection_execution_policy,
        selection_parallel_workers=selection_parallel_workers,
    )


def load_m7_direction_deep_candidate(path: Path) -> M7LoadedDirectionDeepCandidate:
    try:
        payload = torch.load(path, map_location="cpu", weights_only=True)
        if payload.get("schema_version") != "m7-deep-model-state-v1":
            raise ValueError
        configuration = payload["configuration"]
        configuration_id = str(configuration["configuration_id"])
        family = str(configuration["family"])
        raw_parameters = configuration["parameters"]
        if not isinstance(raw_parameters, Mapping):
            raise ValueError
        parameters = {str(name): value for name, value in raw_parameters.items()}
        feature_names = tuple(str(value) for value in payload["feature_names"])
        medians = {
            str(name): float(value)
            for name, value in payload["feature_medians"].items()
        }
        sequence_length = int(payload["sequence_length"])
        model = _deep_model(
            family,
            parameters=parameters,
            feature_count=len(feature_names),
        )
        model.load_state_dict(payload["state_dict"], strict=True)
    except (KeyError, OSError, TypeError, ValueError, RuntimeError) as error:
        raise ValueError("m7_direction_deep_artifact_invalid") from error
    if sequence_length != _required_int(parameters, "sequence_length"):
        raise ValueError("m7_direction_deep_artifact_invalid")
    return M7LoadedDirectionDeepCandidate(
        configuration_id=configuration_id,
        family=family,
        model=model,
        feature_names=feature_names,
        feature_medians=medians,
        sequence_length=sequence_length,
    )


def _deep_model(
    family: str, *, parameters: dict[str, object], feature_count: int
) -> nn.Module:
    if family == "dlinear":
        return DLinearDirectionModel(
            sequence_length=_required_int(parameters, "sequence_length"),
            feature_count=feature_count,
            hidden_size=_required_int(parameters, "hidden_size"),
            moving_average=_required_int(parameters, "moving_average", default=7),
        )
    if family == "patchtst":
        return PatchTSTDirectionModel(
            sequence_length=_required_int(parameters, "sequence_length"),
            feature_count=feature_count,
            patch_length=_required_int(parameters, "patch_length"),
            d_model=_required_int(parameters, "d_model"),
            encoder_layers=_required_int(parameters, "encoder_layers"),
            attention_heads=_required_int(parameters, "attention_heads"),
            dropout=_required_float(parameters, "dropout"),
        )
    raise ValueError("m7_direction_deep_family_invalid")


def _required_int(
    parameters: Mapping[str, object], name: str, *, default: int | None = None
) -> int:
    value = parameters.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("m7_direction_deep_parameter_invalid")
    return value


def _required_float(parameters: Mapping[str, object], name: str) -> float:
    value = parameters.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("m7_direction_deep_parameter_invalid")
    return float(value)


def _float_triple(values: object) -> tuple[float, float, float]:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise ValueError("m7_direction_calibrator_vector_invalid")
    return cast(tuple[float, float, float], tuple(float(value) for value in values))


__all__ = [
    "M7DirectionFoldEnsemble",
    "M7DirectionFrozenFold",
    "M7DirectionFrozenMember",
    "M7DirectionPublication",
    "M7DirectionPublicationIdentity",
    "M7DirectionServingSelection",
    "M7LoadedDirectionDeepCandidate",
    "M7LoadedDirectionTreeCandidate",
    "load_m7_direction_tree_candidate",
    "load_m7_direction_deep_candidate",
    "load_m7_direction_package",
    "freeze_m7_direction_candidate",
    "build_m7_direction_shadow_explanations",
    "write_m7_direction_tree_candidate",
]
