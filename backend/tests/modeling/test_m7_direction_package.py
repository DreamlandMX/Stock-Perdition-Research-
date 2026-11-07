from __future__ import annotations

from pathlib import Path
from datetime import UTC, date, datetime, timedelta
import pickle

from catboost import CatBoostClassifier
import numpy as np
import pandas as pd
import pytest
import torch

from stock_prediction.features.m7 import M7_CORE_V2_COLUMNS
from stock_prediction.modeling.m7_candidates import (
    M7CatBoostCandidate,
    M7QuantileCDFCandidate,
)
from stock_prediction.modeling.m7_direction_package import (
    M7DirectionFoldEnsemble,
    M7DirectionFrozenFold,
    M7DirectionFrozenMember,
    M7DirectionPublicationIdentity,
    freeze_m7_direction_candidate,
    load_m7_direction_deep_candidate,
    load_m7_direction_package,
    load_m7_direction_tree_candidate,
    write_m7_direction_tree_candidate,
)
from stock_prediction.modeling.m7_deep_models import DLinearDirectionModel
from stock_prediction.modeling.m7_calibration import (
    M7DirectionCalibrator,
    M7FiniteBlend,
)
from stock_prediction.modeling.m7_development import M7DirectionFoldSelection
from stock_prediction.modeling.report import file_sha256
from stock_prediction.modeling.m7_package import publish_m7_package


def test_catboost_tree_artifact_reloads_with_frozen_preprocessing(
    tmp_path: Path,
) -> None:
    train = pd.DataFrame(
        {
            "signal": [-2.0, -1.0, -0.2, 0.0, 0.3, 1.0, 2.0, 3.0, -3.0],
            "sector": [
                "tech",
                "tech",
                "finance",
                "finance",
                "tech",
                "finance",
                "tech",
                "finance",
                "tech",
            ],
        }
    )
    labels = np.asarray(
        ["down", "down", "flat", "flat", "flat", "up", "up", "up", "down"]
    )
    model = CatBoostClassifier(
        loss_function="MultiClass",
        iterations=5,
        depth=2,
        random_seed=17,
        allow_writing_files=False,
        verbose=False,
    )
    model.fit(train, labels, cat_features=["sector"])
    candidate = M7CatBoostCandidate(
        configuration_id="m7-catboost_multiclass-01",
        model=model,
        feature_names=("signal", "sector"),
        categorical_feature_names=("sector",),
        class_order=("down", "flat", "up"),
        model_class_order=tuple(str(value) for value in model.classes_),
        best_iteration=5,
        selection_brier=0.2,
        selection_metric="multiclass_brier",
        boosting_type="Ordered",
        thread_count=1,
        seed=17,
        promotion_eligible=True,
        ineligibility_reason=None,
    )
    artifact_path = tmp_path / "candidate.pkl"

    written = write_m7_direction_tree_candidate(
        artifact_path,
        candidate=candidate,
        training_frame=train,
    )
    loaded = load_m7_direction_tree_candidate(written.source_path)
    inference = pd.DataFrame(
        {
            "signal": [np.nan, 1.5, -1.5],
            "sector": ["unknown", "tech", None],
            "atr_pct_20": [0.02, 0.02, 0.02],
        }
    )
    probabilities = loaded.predict(inference, horizon=5)

    assert written.category == "model"
    assert probabilities.shape == (3, 3)
    assert np.all(np.isfinite(probabilities))
    assert np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-12)
    assert loaded.configuration_id == candidate.configuration_id
    assert loaded.family == "catboost_multiclass"


def _quantile_candidate(
    *,
    mapping_policy: str | None = None,
    v9_execution: bool = False,
) -> M7QuantileCDFCandidate:
    values = {
        "configuration_id": "m7-quantile_cdf-01",
        "models": (),
        "quantile_levels": (0.10, 0.25, 0.50, 0.75, 0.90),
        "feature_names": ("signal",),
        "class_order": ("down", "flat", "up"),
        "best_iteration": 1,
        "selection_brier": 0.2,
        "selection_metric": "multiclass_brier",
        "seed": 38,
        "promotion_eligible": True,
        "ineligibility_reason": None,
    }
    if mapping_policy is not None:
        values["quantile_mapping_policy"] = mapping_policy
    if v9_execution:
        values.update(
            fit_execution_policy="lightgbm_threadpool_ordered_v1",
            fit_parallel_workers=4,
            selection_execution_policy="joblib_loky_ordered_chunks_v1",
            selection_parallel_workers=8,
            selection_brier_vector=(0.3, 0.2),
        )
    return M7QuantileCDFCandidate(**values)  # type: ignore[arg-type]


def test_v8_quantile_artifact_records_and_reloads_mapping_policy(
    tmp_path: Path,
) -> None:
    candidate = _quantile_candidate(mapping_policy="scipy_isotonic_compact_interp_v1")
    artifact = write_m7_direction_tree_candidate(
        tmp_path / "quantile-v8.pkl",
        candidate=candidate,
        training_frame=pd.DataFrame({"signal": [0.0, 1.0]}),
    )

    loaded = load_m7_direction_tree_candidate(artifact.source_path)

    assert artifact.schema_version == "m7-direction-tree-candidate-v2"
    assert loaded.quantile_mapping_policy == "scipy_isotonic_compact_interp_v1"


@pytest.mark.parametrize(
    ("mutation", "replacement"),
    (
        ("absent", None),
        ("unknown", "unknown"),
        ("mismatched", "sklearn_isotonic_unique_interp_v1"),
    ),
)
def test_v8_quantile_artifact_fails_closed_for_invalid_mapping_policy(
    tmp_path: Path,
    mutation: str,
    replacement: str | None,
) -> None:
    path = tmp_path / f"quantile-v8-{mutation}.pkl"
    write_m7_direction_tree_candidate(
        path,
        candidate=_quantile_candidate(
            mapping_policy="scipy_isotonic_compact_interp_v1"
        ),
        training_frame=pd.DataFrame({"signal": [0.0, 1.0]}),
    )
    with path.open("rb") as stream:
        payload = pickle.load(stream)
    if mutation == "absent":
        payload.pop("quantile_mapping_policy")
    else:
        payload["quantile_mapping_policy"] = replacement
    path.unlink()
    with path.open("xb") as stream:
        pickle.dump(payload, stream, protocol=5)

    with pytest.raises(ValueError, match="m7_direction_tree_artifact_invalid"):
        load_m7_direction_tree_candidate(path)


def test_v9_quantile_artifact_records_and_reloads_execution_policy(
    tmp_path: Path,
) -> None:
    candidate = _quantile_candidate(
        mapping_policy="scipy_isotonic_compact_interp_v1",
        v9_execution=True,
    )
    artifact = write_m7_direction_tree_candidate(
        tmp_path / "quantile-v9.pkl",
        candidate=candidate,
        training_frame=pd.DataFrame({"signal": [0.0, 1.0]}),
    )

    loaded = load_m7_direction_tree_candidate(artifact.source_path)

    assert artifact.schema_version == "m7-direction-tree-candidate-v3"
    assert loaded.quantile_mapping_policy == "scipy_isotonic_compact_interp_v1"
    assert loaded.fit_execution_policy == "lightgbm_threadpool_ordered_v1"
    assert loaded.fit_parallel_workers == 4
    assert loaded.selection_execution_policy == "joblib_loky_ordered_chunks_v1"
    assert loaded.selection_parallel_workers == 8


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("fit_execution_policy", None),
        ("fit_execution_policy", "unknown"),
        ("fit_parallel_workers", 3),
        ("selection_execution_policy", "unknown"),
        ("selection_parallel_workers", 7),
    ),
)
def test_v9_quantile_artifact_fails_closed_for_invalid_execution_policy(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    path = tmp_path / f"quantile-v9-{field}.pkl"
    write_m7_direction_tree_candidate(
        path,
        candidate=_quantile_candidate(
            mapping_policy="scipy_isotonic_compact_interp_v1",
            v9_execution=True,
        ),
        training_frame=pd.DataFrame({"signal": [0.0, 1.0]}),
    )
    with path.open("rb") as stream:
        payload = pickle.load(stream)
    if replacement is None:
        payload.pop(field)
    else:
        payload[field] = replacement
    path.unlink()
    with path.open("xb") as stream:
        pickle.dump(payload, stream, protocol=5)

    with pytest.raises(ValueError, match="m7_direction_tree_artifact_invalid"):
        load_m7_direction_tree_candidate(path)


def test_legacy_quantile_artifact_loads_with_reference_mapping_policy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "quantile-v7.pkl"
    with path.open("wb") as stream:
        pickle.dump(
            {
                "schema_version": "m7-direction-tree-candidate-v1",
                "candidate": _quantile_candidate(),
                "numeric_medians": {"signal": 0.0},
                "categorical_vocabularies": {},
            },
            stream,
            protocol=5,
        )

    loaded = load_m7_direction_tree_candidate(path)

    assert loaded.quantile_mapping_policy == "sklearn_isotonic_unique_interp_v1"


def test_deep_artifact_reloads_and_predicts_latest_seed100_sequences(
    tmp_path: Path,
) -> None:
    model = DLinearDirectionModel(
        sequence_length=4,
        feature_count=2,
        hidden_size=8,
        moving_average=3,
    )
    artifact_path = tmp_path / "deep.pt"
    torch.save(
        {
            "schema_version": "m7-deep-model-state-v1",
            "configuration": {
                "configuration_id": "m7-dlinear-test",
                "family": "dlinear",
                "parameters": {
                    "sequence_length": 4,
                    "hidden_size": 8,
                    "moving_average": 3,
                },
            },
            "feature_names": ("signal", "context"),
            "feature_medians": {"signal": 0.0, "context": 0.0},
            "sequence_length": 4,
            "state_dict": model.state_dict(),
        },
        artifact_path,
    )
    rows: list[dict[str, object]] = []
    start = date(2026, 8, 18)
    for symbol_index in range(100):
        for offset in range(4):
            rows.append(
                {
                    "symbol": f"US.S{symbol_index:03d}",
                    "session_date": start + timedelta(days=offset),
                    "signal": symbol_index / 100 + offset / 10,
                    "context": offset / 20,
                }
            )
    loaded = load_m7_direction_deep_candidate(artifact_path)

    probabilities = loaded.predict(pd.DataFrame(rows), horizon=20)

    assert loaded.configuration_id == "m7-dlinear-test"
    assert loaded.family == "dlinear"
    assert probabilities.shape == (100, 3)
    assert np.all(np.isfinite(probabilities))
    assert np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-7)


def test_direction_fold_ensemble_applies_frozen_calibration_blend_and_fold_mean(
    tmp_path: Path,
) -> None:
    train = pd.DataFrame(
        {
            "signal": [-2.0, -1.0, -0.2, 0.0, 0.3, 1.0, 2.0, 3.0, -3.0],
            "sector": [
                "tech",
                "tech",
                "finance",
                "finance",
                "tech",
                "finance",
                "tech",
                "finance",
                "tech",
            ],
        }
    )
    labels = np.asarray(
        ["down", "down", "flat", "flat", "flat", "up", "up", "up", "down"]
    )
    model = CatBoostClassifier(
        loss_function="MultiClass",
        iterations=5,
        depth=2,
        random_seed=17,
        allow_writing_files=False,
        verbose=False,
    )
    model.fit(train, labels, cat_features=["sector"])
    candidate = M7CatBoostCandidate(
        configuration_id="m7-catboost_multiclass-01",
        model=model,
        feature_names=("signal", "sector"),
        categorical_feature_names=("sector",),
        class_order=("down", "flat", "up"),
        model_class_order=tuple(str(value) for value in model.classes_),
        best_iteration=5,
        selection_brier=0.2,
        selection_metric="multiclass_brier",
        boosting_type="Ordered",
        thread_count=1,
        seed=17,
        promotion_eligible=True,
        ineligibility_reason=None,
    )
    calibrator = M7DirectionCalibrator(
        method="identity",
        scales=(1.0, 1.0, 1.0),
        biases=(0.0, 0.0, 0.0),
        fit_evidence_hash="fit",
        oof_fold_hash="oof",
        raw_brier=0.2,
        calibrated_brier=0.2,
    )
    blend = M7FiniteBlend(
        member_ids=(candidate.configuration_id,),
        weights=(1.0,),
        eligible_candidate_ids=(candidate.configuration_id,),
        fit_evidence_hash="blend-fit",
        calibration_brier=0.2,
    )
    folds: list[M7DirectionFrozenFold] = []
    for fold_index in range(1, 5):
        artifact = write_m7_direction_tree_candidate(
            tmp_path / f"fold-{fold_index}.pkl",
            candidate=candidate,
            training_frame=train,
            artifact_id=f"fold-{fold_index}-candidate",
        )
        selection = M7DirectionFoldSelection(
            classes=("down", "flat", "up"),
            calibration_sample_keys=(("US.A", date(2026, 1, 2), 5),),
            candidate_ids=(candidate.configuration_id,),
            blend=blend,
            calibrators={candidate.configuration_id: calibrator},
        )
        folds.append(
            M7DirectionFrozenFold(
                fold_id=f"fold-{fold_index}",
                horizon=5,
                selection=selection,
                members=(
                    M7DirectionFrozenMember(
                        candidate_id=candidate.configuration_id,
                        family="catboost_multiclass",
                        artifact=artifact,
                        model_kind="tree",
                    ),
                ),
            )
        )
    ensemble = M7DirectionFoldEnsemble(tuple(folds))
    frame = pd.DataFrame(
        {
            "signal": np.linspace(-1.5, 1.5, 100),
            "sector": ["tech"] * 100,
            "atr_pct_20": [0.02] * 100,
        }
    )

    probabilities = ensemble.predict(frame, horizon=5)

    assert probabilities.shape == (100, 3)
    assert np.all(np.isfinite(probabilities))
    assert np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-12)


def test_freeze_direction_candidate_builds_loadable_three_horizon_package_and_sla(
    tmp_path: Path,
) -> None:
    train = pd.DataFrame(
        {
            "signal": [-2.0, -1.0, -0.2, 0.0, 0.3, 1.0, 2.0, 3.0, -3.0],
            "sector": [
                "tech",
                "tech",
                "finance",
                "finance",
                "tech",
                "finance",
                "tech",
                "finance",
                "tech",
            ],
        }
    )
    labels = np.asarray(
        ["down", "down", "flat", "flat", "flat", "up", "up", "up", "down"]
    )
    model = CatBoostClassifier(
        loss_function="MultiClass",
        iterations=5,
        depth=2,
        random_seed=17,
        allow_writing_files=False,
        verbose=False,
    )
    model.fit(train, labels, cat_features=["sector"])
    candidate = M7CatBoostCandidate(
        configuration_id="m7-catboost_multiclass-01",
        model=model,
        feature_names=("signal", "sector"),
        categorical_feature_names=("sector",),
        class_order=("down", "flat", "up"),
        model_class_order=tuple(str(value) for value in model.classes_),
        best_iteration=5,
        selection_brier=0.2,
        selection_metric="multiclass_brier",
        boosting_type="Ordered",
        thread_count=1,
        seed=17,
        promotion_eligible=True,
        ineligibility_reason=None,
    )
    base_artifact = write_m7_direction_tree_candidate(
        tmp_path / "base-candidate.pkl",
        candidate=candidate,
        training_frame=train,
        artifact_id="base-candidate",
    )
    calibrator = M7DirectionCalibrator(
        method="identity",
        scales=(1.0, 1.0, 1.0),
        biases=(0.0, 0.0, 0.0),
        fit_evidence_hash="fit",
        oof_fold_hash="oof",
        raw_brier=0.2,
        calibrated_brier=0.2,
    )
    blend = M7FiniteBlend(
        member_ids=(candidate.configuration_id,),
        weights=(1.0,),
        eligible_candidate_ids=(candidate.configuration_id,),
        fit_evidence_hash="blend-fit",
        calibration_brier=0.2,
    )
    frozen: dict[int, tuple[M7DirectionFrozenFold, ...]] = {}
    for horizon in (1, 5, 20):
        rows: list[M7DirectionFrozenFold] = []
        for fold_index in range(1, 5):
            selection = M7DirectionFoldSelection(
                classes=("down", "flat", "up"),
                calibration_sample_keys=(("US.A", date(2026, 1, 2), horizon),),
                candidate_ids=(candidate.configuration_id,),
                blend=blend,
                calibrators={candidate.configuration_id: calibrator},
            )
            rows.append(
                M7DirectionFrozenFold(
                    fold_id=f"fold-{fold_index}",
                    horizon=horizon,
                    selection=selection,
                    members=(
                        M7DirectionFrozenMember(
                            candidate_id=candidate.configuration_id,
                            family="catboost_multiclass",
                            artifact=base_artifact.model_copy(
                                update={"artifact_id": f"h{horizon}-fold-{fold_index}"}
                            ),
                            model_kind="tree",
                        ),
                    ),
                )
            )
        frozen[horizon] = tuple(rows)
    label_path = tmp_path / "labels.parquet"
    label_path.write_bytes(b"governed-labels")
    identity = M7DirectionPublicationIdentity(
        package_id="m7-direction-run-001",
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
    latest = pd.DataFrame(
        {
            "symbol": [f"US.S{index:03d}" for index in range(100)],
            "session_date": [date(2026, 8, 25)] * 100,
            "signal": np.linspace(-1.5, 1.5, 100),
            "sector": ["tech"] * 100,
            "atr_pct_20": [0.02] * 100,
        }
    )
    for feature_index, feature_name in enumerate(M7_CORE_V2_COLUMNS):
        if feature_name not in latest:
            latest[feature_name] = np.linspace(-1.0, 1.0, 100) + feature_index / 100

    publication = freeze_m7_direction_candidate(
        tmp_path / "publication",
        identity=identity,
        label_path=label_path,
        label_schema_version="m7-label-thresholds-v1",
        label_content_hash=file_sha256(label_path),
        frozen_folds=frozen,
        latest_seed100_history=latest,
        training_wall_seconds=12.0,
        training_cpu_seconds=10.0,
        peak_rss_bytes=1024,
        peak_private_bytes=2048,
        peak_gpu_memory_bytes=0,
    )

    assert publication.build_spec.bundle_id == "direction"
    assert len([row for row in publication.artifacts if row.category == "model"]) == 12
    assert any(
        row.schema_version == "m7-drift-reference-v1" for row in publication.artifacts
    )
    assert publication.sla.inference_sla_passed is True
    assert publication.sla.explanation_sla_passed is True
    assert publication.runtime_observations["inference_device"] == "cpu"
    published = publish_m7_package(
        tmp_path / "m7-packages",
        build_spec=publication.build_spec,
        artifacts=publication.artifacts,
        runtime_observations=publication.runtime_observations,
    )
    reloaded = load_m7_direction_package(published.package_directory)
    replay = reloaded.predict(latest, horizon=1)
    assert replay.shape == (100, 3)
    assert np.allclose(replay.sum(axis=1), 1.0, atol=1e-12)
    prior = latest.copy()
    prior["session_date"] = date(2026, 8, 24)
    history_replay = reloaded.predict(
        pd.concat([prior, latest], ignore_index=True), horizon=1
    )
    assert history_replay.shape == (100, 3)
    assert np.allclose(history_replay, replay, atol=1e-12)
