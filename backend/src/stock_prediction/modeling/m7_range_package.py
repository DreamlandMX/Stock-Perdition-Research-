"""Loadable fold-ensemble artifacts for M7 range-exit-20 shadow inference."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path, PurePosixPath
import time
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import psutil  # type: ignore[import-untyped]

from stock_prediction.modeling.m7_calibration import M7RangeCalibrator
from stock_prediction.modeling.m7_drift import build_m7_drift_reference
from stock_prediction.modeling.m7_explanations import M7InferenceSLAReport
from stock_prediction.modeling.m7_package import (
    M7PackageArtifactInput,
    M7PackageBuildSpec,
    detect_m7_runtime_compatibility,
    load_m7_package,
)
from stock_prediction.modeling.report import file_sha256, write_immutable_json


@dataclass(frozen=True, slots=True)
class M7WrittenRangeArtifacts:
    artifacts: tuple[M7PackageArtifactInput, ...]
    model_path: Path
    calibration_path: Path
    explanation_path: Path


@dataclass(frozen=True, slots=True)
class M7RangePublicationIdentity:
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
class M7RangePublication:
    build_spec: M7PackageBuildSpec
    artifacts: tuple[M7PackageArtifactInput, ...]
    sla: M7InferenceSLAReport
    runtime_observations: dict[str, object]


@dataclass(frozen=True, slots=True)
class M7RangeFoldEnsemble:
    models: tuple[lgb.Booster, ...]
    calibrators: tuple[M7RangeCalibrator, ...]
    feature_names: tuple[str, ...]

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        required = tuple(name for name in self.feature_names if name != "elapsed_day")
        if set(required) - set(frame.columns):
            raise ValueError("m7_range_inference_features_missing")
        if len(self.models) != len(self.calibrators) or not self.models:
            raise ValueError("m7_range_ensemble_invalid")
        fold_curves: list[np.ndarray] = []
        for model, calibrator in zip(self.models, self.calibrators, strict=True):
            survival = np.ones(len(frame), dtype=np.float64)
            selected: list[np.ndarray] = []
            for elapsed_day in range(1, 21):
                values = frame.loc[:, required].copy()
                values["elapsed_day"] = float(elapsed_day)
                hazard = np.asarray(
                    model.predict(values.loc[:, self.feature_names]),
                    dtype=np.float64,
                )
                if hazard.shape != (len(frame),) or not np.all(np.isfinite(hazard)):
                    raise ValueError("m7_range_inference_output_invalid")
                survival *= 1.0 - np.clip(hazard, 0.0, 1.0)
                if elapsed_day in {1, 5, 20}:
                    selected.append(1.0 - survival.copy())
            fold_curves.append(calibrator.apply(np.column_stack(selected)))
        probabilities = np.mean(np.stack(fold_curves), axis=0)
        if not np.all(np.isfinite(probabilities)):
            raise ValueError("m7_range_inference_output_invalid")
        return np.maximum.accumulate(np.clip(probabilities, 0.0, 1.0), axis=1)


def write_m7_range_package_artifacts(
    output_directory: Path,
    *,
    models: tuple[Any, ...],
    calibrators: tuple[M7RangeCalibrator, ...],
    feature_names: tuple[str, ...],
) -> M7WrittenRangeArtifacts:
    if len(models) != 4 or len(calibrators) != 4:
        raise ValueError("m7_range_package_requires_four_folds")
    if not feature_names or "elapsed_day" not in feature_names:
        raise ValueError("m7_range_package_feature_contract_invalid")
    output_directory.mkdir(parents=True, exist_ok=True)
    model_path = output_directory / "range-fold-ensemble.json"
    calibration_path = output_directory / "range-calibration.json"
    explanation_path = output_directory / "range-explanation.json"
    model_rows: list[dict[str, object]] = []
    importances: list[np.ndarray] = []
    for fold_index, model in enumerate(models, 1):
        booster = getattr(model, "booster_", None)
        if booster is None:
            raise ValueError("m7_range_package_model_unfitted")
        model_rows.append(
            {"fold_id": f"fold-{fold_index}", "model_text": booster.model_to_string()}
        )
        importances.append(
            np.asarray(booster.feature_importance(importance_type="gain"), dtype=float)
        )
    write_immutable_json(
        model_path,
        {
            "schema_version": "m7-range-fold-ensemble-v1",
            "feature_names": list(feature_names),
            "models": model_rows,
        },
        pretty=False,
    )
    write_immutable_json(
        calibration_path,
        {
            "schema_version": "m7-range-calibration-ensemble-v1",
            "calibrators": [_calibrator_payload(value) for value in calibrators],
        },
        pretty=False,
    )
    mean_importance = np.mean(np.stack(importances), axis=0)
    total = float(mean_importance.sum())
    normalized = mean_importance / total if total > 0 else mean_importance
    write_immutable_json(
        explanation_path,
        {
            "schema_version": "m7-range-global-explanation-v1",
            "feature_names": list(feature_names),
            "global_gain_importance": {
                name: float(value)
                for name, value in zip(feature_names, normalized, strict=True)
            },
            "non_causal_caveat": True,
        },
        pretty=False,
    )
    artifacts = (
        _artifact(
            "range-fold-ensemble", "model", model_path, "m7-range-fold-ensemble-v1"
        ),
        _artifact(
            "range-calibration-ensemble",
            "calibration",
            calibration_path,
            "m7-range-calibration-ensemble-v1",
        ),
        _artifact(
            "range-global-explanation",
            "explanation",
            explanation_path,
            "m7-range-global-explanation-v1",
        ),
    )
    return M7WrittenRangeArtifacts(
        artifacts=artifacts,
        model_path=model_path,
        calibration_path=calibration_path,
        explanation_path=explanation_path,
    )


def load_m7_range_ensemble(
    model_path: Path, calibration_path: Path
) -> M7RangeFoldEnsemble:
    try:
        model_payload = json.loads(model_path.read_text(encoding="utf-8"))
        calibration_payload = json.loads(calibration_path.read_text(encoding="utf-8"))
        if (
            model_payload.get("schema_version") != "m7-range-fold-ensemble-v1"
            or calibration_payload.get("schema_version")
            != "m7-range-calibration-ensemble-v1"
        ):
            raise ValueError
        feature_names = tuple(str(value) for value in model_payload["feature_names"])
        models = tuple(
            lgb.Booster(model_str=str(row["model_text"]))
            for row in model_payload["models"]
        )
        calibrators = tuple(
            M7RangeCalibrator(**row) for row in calibration_payload["calibrators"]
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("m7_range_package_invalid") from error
    if len(models) != 4 or len(calibrators) != 4:
        raise ValueError("m7_range_package_invalid")
    return M7RangeFoldEnsemble(
        models=models,
        calibrators=calibrators,
        feature_names=feature_names,
    )


def load_m7_range_package(package_directory: Path) -> M7RangeFoldEnsemble:
    manifest = load_m7_package(package_directory.parent, package_directory.name)
    if manifest.bundle_id != "range_exit_20":
        raise ValueError("m7_range_package_bundle_mismatch")
    model_entries = [
        row
        for row in manifest.model_artifacts
        if row.schema_version == "m7-range-fold-ensemble-v1"
    ]
    calibration_entries = [
        row
        for row in manifest.calibration_artifacts
        if row.schema_version == "m7-range-calibration-ensemble-v1"
    ]
    if len(model_entries) != 1 or len(calibration_entries) != 1:
        raise ValueError("m7_range_package_artifacts_invalid")
    model_path = package_directory / Path(
        *PurePosixPath(model_entries[0].relative_path).parts
    )
    calibration_path = package_directory / Path(
        *PurePosixPath(calibration_entries[0].relative_path).parts
    )
    return load_m7_range_ensemble(model_path, calibration_path)


def freeze_m7_range_candidate(
    output_directory: Path,
    *,
    identity: M7RangePublicationIdentity,
    label_path: Path,
    label_schema_version: str,
    label_content_hash: str,
    models: tuple[Any, ...],
    calibrators: tuple[M7RangeCalibrator, ...],
    feature_names: tuple[str, ...],
    latest_seed100_frame: pd.DataFrame,
    training_reference_frame: pd.DataFrame,
    training_wall_seconds: float,
    training_cpu_seconds: float,
    budget_seconds: float = 300.0,
) -> M7RangePublication:
    """Write, reload, and benchmark the exact range ensemble before publication."""

    if len(latest_seed100_frame) != 100:
        raise ValueError("m7_range_seed100_sla_rows_invalid")
    if budget_seconds <= 0:
        raise ValueError("m7_range_seed100_sla_budget_invalid")
    written = write_m7_range_package_artifacts(
        output_directory,
        models=models,
        calibrators=calibrators,
        feature_names=feature_names,
    )
    ensemble = load_m7_range_ensemble(written.model_path, written.calibration_path)
    process = psutil.Process()
    inference_cpu_started = time.process_time()
    inference_started = time.monotonic()
    probabilities = ensemble.predict(latest_seed100_frame)
    inference_finished = time.monotonic()
    probability_valid = bool(
        probabilities.shape == (100, 3)
        and np.all(np.isfinite(probabilities))
        and np.all((0.0 <= probabilities) & (probabilities <= 1.0))
        and np.all(np.diff(probabilities, axis=1) >= -1e-12)
    )
    if not probability_valid:
        raise ValueError("m7_range_seed100_probability_artifact_invalid")
    drift_reference_path = output_directory / "range-drift-reference.json"
    drift_reference = build_m7_drift_reference(
        training_reference_frame,
        prediction_values={
            "range_exit_by_1": probabilities[:, 0],
            "range_exit_by_5": probabilities[:, 1],
            "range_exit_by_20": probabilities[:, 2],
        },
        source_identity_hash=identity.feature_input_hash,
    )
    write_immutable_json(
        drift_reference_path,
        drift_reference.model_dump(mode="json"),
        pretty=False,
    )
    explanation_started = time.monotonic()
    try:
        explanation = json.loads(written.explanation_path.read_text(encoding="utf-8"))
        if explanation.get("non_causal_caveat") is not True or not explanation.get(
            "global_gain_importance"
        ):
            raise ValueError
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("m7_range_explanation_artifact_invalid") from error
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
    label_artifact = M7PackageArtifactInput(
        artifact_id="range-label-thresholds",
        category="label_threshold",
        source_path=label_path,
        schema_version=label_schema_version,
        content_hash=label_content_hash,
    )
    drift_reference_artifact = _artifact(
        "range-drift-reference",
        "explanation",
        drift_reference_path,
        "m7-drift-reference-v1",
    )
    build_spec = M7PackageBuildSpec(
        package_id=identity.package_id,
        bundle_id="range_exit_20",
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
    runtime_observations: dict[str, object] = {
        "wall_seconds": training_wall_seconds + total_seconds,
        "cpu_seconds": training_cpu_seconds
        + (time.process_time() - inference_cpu_started),
        "peak_rss_bytes": process.memory_info().rss,
        "peak_private_bytes": getattr(process.memory_info(), "private", 0),
        "peak_gpu_memory_bytes": 0,
        "gpu_device": "cpu",
        "inference_device": "cpu",
        "inference_seconds": inference_seconds,
        "explanation_seconds": explanation_seconds,
        "seed100_row_count": 100,
    }
    return M7RangePublication(
        build_spec=build_spec,
        artifacts=(label_artifact, *written.artifacts, drift_reference_artifact),
        sla=sla,
        runtime_observations=runtime_observations,
    )


def _calibrator_payload(value: M7RangeCalibrator) -> dict[str, object]:
    return {
        "method": value.method,
        "parameters": value.parameters,
        "x_thresholds": value.x_thresholds,
        "y_thresholds": value.y_thresholds,
        "fit_evidence_hash": value.fit_evidence_hash,
        "oof_fold_hash": value.oof_fold_hash,
        "raw_brier": value.raw_brier,
        "calibrated_brier": value.calibrated_brier,
        "calibrator_hash": value.calibrator_hash,
    }


def _artifact(
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


__all__ = [
    "M7RangeFoldEnsemble",
    "M7RangePublication",
    "M7RangePublicationIdentity",
    "M7WrittenRangeArtifacts",
    "freeze_m7_range_candidate",
    "load_m7_range_ensemble",
    "load_m7_range_package",
    "write_m7_range_package_artifacts",
]
