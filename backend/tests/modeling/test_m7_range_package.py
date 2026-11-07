from __future__ import annotations

import json
from pathlib import Path
from datetime import UTC, datetime

from lightgbm import LGBMClassifier
import numpy as np
import pandas as pd

from stock_prediction.features.m7 import M7_CORE_V2_COLUMNS
from stock_prediction.modeling.m7_calibration import M7RangeCalibrator
from stock_prediction.modeling.m7_range_package import (
    M7RangePublicationIdentity,
    freeze_m7_range_candidate,
    load_m7_range_ensemble,
    load_m7_range_package,
    write_m7_range_package_artifacts,
)
from stock_prediction.modeling.report import file_sha256
from stock_prediction.modeling.m7_package import publish_m7_package


def _model(seed: int) -> LGBMClassifier:
    features = pd.DataFrame(
        {
            "signal": np.linspace(-1.0, 1.0, 40),
            "elapsed_day": np.tile(np.arange(1, 5), 10),
        }
    )
    labels = (features["signal"] + features["elapsed_day"] / 10 > 0).astype(int)
    model = LGBMClassifier(
        n_estimators=5,
        num_leaves=4,
        min_child_samples=2,
        random_state=seed,
        verbosity=-1,
    )
    model.fit(features, labels)
    return model


def _identity_calibrator() -> M7RangeCalibrator:
    return M7RangeCalibrator(
        method="identity",
        parameters=((), (), ()),
        x_thresholds=((), (), ()),
        y_thresholds=((), (), ()),
        fit_evidence_hash="fit-evidence",
        oof_fold_hash="oof-evidence",
        raw_brier=0.1,
        calibrated_brier=0.1,
    )


def test_written_range_ensemble_loads_and_replays_monotonic_probabilities(
    tmp_path: Path,
) -> None:
    written = write_m7_range_package_artifacts(
        tmp_path,
        models=(_model(1), _model(2), _model(3), _model(4)),
        calibrators=tuple(_identity_calibrator() for _ in range(4)),
        feature_names=("signal", "elapsed_day"),
    )

    assert {item.category for item in written.artifacts} == {
        "model",
        "calibration",
        "explanation",
    }
    ensemble = load_m7_range_ensemble(written.model_path, written.calibration_path)
    frame = pd.DataFrame({"signal": np.linspace(-0.8, 0.8, 100)})
    probabilities = ensemble.predict(frame)

    assert probabilities.shape == (100, 3)
    assert np.all(np.isfinite(probabilities))
    assert np.all((0 <= probabilities) & (probabilities <= 1))
    assert np.all(np.diff(probabilities, axis=1) >= -1e-12)
    explanation = json.loads(written.explanation_path.read_text(encoding="utf-8"))
    assert explanation["non_causal_caveat"] is True
    assert explanation["feature_names"] == ["signal", "elapsed_day"]


def test_range_ensemble_rejects_missing_inference_columns(tmp_path: Path) -> None:
    written = write_m7_range_package_artifacts(
        tmp_path,
        models=tuple(_model(seed) for seed in range(4)),
        calibrators=tuple(_identity_calibrator() for _ in range(4)),
        feature_names=("signal", "elapsed_day"),
    )
    ensemble = load_m7_range_ensemble(written.model_path, written.calibration_path)

    try:
        ensemble.predict(pd.DataFrame({"other": np.zeros(100)}))
    except ValueError as error:
        assert str(error) == "m7_range_inference_features_missing"
    else:
        raise AssertionError("missing feature contract was accepted")


def test_freeze_range_candidate_builds_complete_package_inputs_and_seed100_sla(
    tmp_path: Path,
) -> None:
    label_path = tmp_path / "labels.parquet"
    label_path.write_bytes(b"governed-label-thresholds")
    identity = M7RangePublicationIdentity(
        package_id="m7-range-run-001",
        code_commit="1" * 40,
        universe_version="m1-seed-2026-07-11-v1",
        dataset_revision="dataset-v1",
        feature_set_version="features-v5",
        feature_input_hash="2" * 64,
        label_version="labels-v1",
        split_manifest_hash="3" * 64,
        candidate_matrix_hash="4" * 64,
        dependency_lock_hash="5" * 64,
        created_at=datetime(2026, 8, 25, 16, tzinfo=UTC),
    )

    latest = pd.DataFrame({"signal": np.linspace(-0.8, 0.8, 100)})
    reference = latest.copy()
    reference["eligible"] = True
    reference["regime"] = np.resize(np.asarray(["trend_up", "range"]), 100)
    for feature_index, feature_name in enumerate(M7_CORE_V2_COLUMNS):
        reference[feature_name] = (
            np.linspace(-1.0, 1.0, 100) + feature_index / 100
        )

    publication = freeze_m7_range_candidate(
        tmp_path / "publication",
        identity=identity,
        label_path=label_path,
        label_schema_version="m7-label-thresholds-v1",
        label_content_hash=file_sha256(label_path),
        models=tuple(_model(seed) for seed in range(4)),
        calibrators=tuple(_identity_calibrator() for _ in range(4)),
        feature_names=("signal", "elapsed_day"),
        latest_seed100_frame=latest,
        training_reference_frame=reference,
        training_wall_seconds=12.5,
        training_cpu_seconds=10.0,
    )

    assert publication.build_spec.bundle_id == "range_exit_20"
    assert publication.build_spec.blind_window_id is None
    assert len(publication.artifacts) == 5
    assert {artifact.category for artifact in publication.artifacts} == {
        "label_threshold",
        "model",
        "calibration",
        "explanation",
    }
    assert publication.sla.seed100_row_count == 100
    assert publication.sla.inference_sla_passed is True
    assert publication.sla.explanation_sla_passed is True
    assert publication.runtime_observations["inference_device"] == "cpu"
    assert publication.runtime_observations["peak_gpu_memory_bytes"] == 0
    assert any(
        row.schema_version == "m7-drift-reference-v1"
        for row in publication.artifacts
    )
    published = publish_m7_package(
        tmp_path / "m7-packages",
        build_spec=publication.build_spec,
        artifacts=publication.artifacts,
        runtime_observations=publication.runtime_observations,
    )
    ensemble = load_m7_range_package(published.package_directory)
    replay = ensemble.predict(
        pd.DataFrame({"signal": np.linspace(-0.8, 0.8, 100)})
    )
    assert replay.shape == (100, 3)
    assert np.all(np.diff(replay, axis=1) >= -1e-12)
