from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

import stock_prediction.modeling.lightgbm_models as lightgbm_module
from stock_prediction.modeling.contracts import (
    EvaluationRole,
    EvaluationScope,
    EvaluationWindow,
    TaskName,
)
from stock_prediction.modeling.lightgbm_models import (
    ClassifierConfig,
    ClassifierArtifact,
    LightGbmClassifierTrainer,
    LightGbmQuantileTrainer,
    ModelInputError,
    QuantileConfig,
    explain_tree_prediction,
)
from stock_prediction.modeling.training import (
    ClassificationTrainingData,
    ClassificationTuningFold,
    InnerFoldObjective,
    OptunaBudget,
    RegressionTrainingData,
    TrainingPartitionEvidence,
    run_bounded_optuna,
)


FEATURE_NAMES = ("momentum", "compression")
START = datetime(2020, 1, 2, 21, tzinfo=UTC)


def _sample_keys(count: int, horizon: int = 5):
    return tuple(
        ("US.AAPL", START + timedelta(days=index), horizon)
        for index in range(count)
    )


def _partition(
    sample_keys,
    *,
    task: TaskName = TaskName.FORECAST_DIRECTION,
    horizon: int = 5,
    role: EvaluationRole = EvaluationRole.TRAIN,
) -> TrainingPartitionEvidence:
    window = EvaluationWindow(
        window_id=f"split-v1.{role.value}",
        role=role,
        scope=EvaluationScope.SYNTHETIC_OFFLINE,
        start_session=sample_keys[0][1].date(),
        end_session=sample_keys[-1][1].date(),
        split_version="split-v1",
        dataset_input_hash="a" * 64,
        calendar_hash="b" * 64,
        consumed_at=None,
        consumption_reason=None,
    )
    return TrainingPartitionEvidence.register(
        partition_id=f"partition-{task.value}-{horizon}",
        task=task,
        horizon=horizon,
        evaluation_window=window,
        registered_sample_keys=sample_keys,
        split_manifest_hash="c" * 64,
        source_fold_hash="d" * 64,
    )


def _classification_data() -> ClassificationTrainingData:
    features = np.array(
        [
            [-2.0, 0.1],
            [-1.7, 0.2],
            [-1.3, 0.2],
            [-0.9, 0.3],
            [-0.5, 0.4],
            [-0.2, 0.5],
            [0.2, 0.5],
            [0.5, 0.4],
            [0.9, 0.3],
            [1.3, 0.2],
            [1.7, 0.2],
            [2.0, 0.1],
        ],
        dtype=np.float64,
    )
    labels = ("down",) * 6 + ("up",) * 6
    keys = _sample_keys(len(labels))
    return ClassificationTrainingData.build(
        features,
        labels,
        sample_keys=keys,
        feature_names=FEATURE_NAMES,
        dataset_input_hash="a" * 64,
        partition=_partition(keys),
    )


def _classifier_config() -> ClassifierConfig:
    return ClassifierConfig(
        task=TaskName.FORECAST_DIRECTION,
        horizon=5,
        model_version="lgbm-direction-v1",
        asset_group="all",
        class_order=("down", "up"),
        seed=17,
        num_threads=1,
        num_boost_round=15,
        learning_rate=0.1,
        num_leaves=7,
        min_data_in_leaf=2,
    )


def test_classifier_is_deterministic_and_preserves_class_order() -> None:
    training = _classification_data()
    first = LightGbmClassifierTrainer(_classifier_config()).fit(training)
    second = LightGbmClassifierTrainer(_classifier_config()).fit(training)
    before = first.model_dump_json()
    probabilities = first.predict_proba(
        training.features(),
        feature_names=FEATURE_NAMES,
        expected_artifact_hash=first.artifact_hash,
    )

    assert first.class_order == ("down", "up")
    assert first.model_hash == second.model_hash
    assert probabilities == pytest.approx(
        second.predict_proba(
            training.features(),
            feature_names=FEATURE_NAMES,
            expected_artifact_hash=second.artifact_hash,
        )
    )
    assert np.sum(probabilities, axis=1) == pytest.approx(np.ones(len(probabilities)))
    assert first.model_dump_json() == before
    assert probabilities.flags.writeable is False

    with pytest.raises(ModelInputError, match="registered identity"):
        first.predict_proba(
            training.features(),
            feature_names=FEATURE_NAMES,
            expected_artifact_hash="0" * 64,
        )


def test_classifier_weights_and_schema_are_bound_to_training_artifact() -> None:
    training = _classification_data()
    artifact = LightGbmClassifierTrainer(_classifier_config()).fit(training)

    assert artifact.class_counts == (6, 6)
    assert artifact.class_weights == pytest.approx((1.0, 1.0))
    assert artifact.training_input_hash == training.input_hash
    with pytest.raises(ModelInputError, match="feature order"):
        artifact.predict_proba(
            training.features(),
            feature_names=("compression", "momentum"),
            expected_artifact_hash=artifact.artifact_hash,
        )


def test_classifier_rejects_training_keys_from_another_horizon() -> None:
    source = _classification_data()
    wrong_horizon = ClassificationTrainingData.build(
        source.features(),
        source.labels,
        sample_keys=(keys := _sample_keys(len(source.labels), horizon=20)),
        feature_names=FEATURE_NAMES,
        dataset_input_hash="a" * 64,
        partition=_partition(keys, horizon=20),
    )

    with pytest.raises(ModelInputError, match="horizon"):
        LightGbmClassifierTrainer(_classifier_config()).fit(wrong_horizon)


def test_training_partition_rejects_outer_test_and_exact_key_mismatch() -> None:
    keys = _sample_keys(4)
    with pytest.raises(ValueError, match="TRAIN"):
        _partition(keys, role=EvaluationRole.OUTER_TEST)

    registered = _partition(keys)
    with pytest.raises(ModelInputError, match="exactly match"):
        ClassificationTrainingData.build(
            np.ones((3, 2), dtype=np.float64),
            ("down", "up", "up"),
            sample_keys=keys[:3],
            feature_names=FEATURE_NAMES,
            dataset_input_hash="a" * 64,
            partition=registered,
        )


def test_trainer_revalidates_partition_after_model_copy_rehash() -> None:
    valid = _classification_data()
    outer_keys = tuple(
        (symbol, as_of + timedelta(days=500), horizon)
        for symbol, as_of, horizon in valid.sample_keys
    )
    tampered = valid.model_copy(update={"sample_keys": outer_keys})
    tampered = tampered.model_copy(update={"input_hash": tampered.compute_hash()})

    with pytest.raises(ModelInputError, match="training evidence"):
        LightGbmClassifierTrainer(_classifier_config()).fit(tampered)


def test_quantile_artifact_contains_exact_five_alphas() -> None:
    features = np.column_stack(
        (np.linspace(-2, 2, 30), np.linspace(0.1, 0.9, 30))
    )
    outcomes = 0.03 * features[:, 0] + 0.01 * features[:, 1]
    keys = _sample_keys(len(outcomes))
    training = RegressionTrainingData.build(
        features,
        outcomes,
        sample_keys=keys,
        feature_names=FEATURE_NAMES,
        dataset_input_hash="a" * 64,
        partition=_partition(keys, task=TaskName.CAPITAL_GAIN_RETURN),
    )
    config = QuantileConfig(
        task=TaskName.CAPITAL_GAIN_RETURN,
        horizon=5,
        model_version="lgbm-return-v1",
        asset_group="all",
        seed=17,
        num_threads=1,
        num_boost_round=12,
        learning_rate=0.1,
        num_leaves=7,
        min_data_in_leaf=2,
    )
    artifact = LightGbmQuantileTrainer(config).fit(training)
    predictions = artifact.predict(
        training.features(),
        feature_names=FEATURE_NAMES,
        expected_artifact_hash=artifact.artifact_hash,
    )

    assert artifact.quantiles == (0.10, 0.25, 0.50, 0.75, 0.90)
    assert len(artifact.model_hashes) == 5
    assert predictions.shape == (30, 5)
    assert np.all(np.isfinite(predictions))
    assert predictions.flags.writeable is False


def test_native_contributions_sum_to_raw_binary_score() -> None:
    training = _classification_data()
    artifact = LightGbmClassifierTrainer(_classifier_config()).fit(training)
    explanation = explain_tree_prediction(
        artifact,
        training.features()[0],
        feature_names=FEATURE_NAMES,
        class_name="up",
        top_n=5,
        expected_artifact_hash=artifact.artifact_hash,
    )

    assert explanation.non_causal is True
    assert explanation.model_version == artifact.model_version
    assert explanation.contribution_sum == pytest.approx(
        explanation.raw_score, abs=1e-8
    )
    assert len(explanation.top_contributions) <= 5
    assert all(item.feature_name in FEATURE_NAMES for item in explanation.top_contributions)


def test_training_data_and_artifacts_reject_non_finite_or_tampered_inputs() -> None:
    with pytest.raises(ModelInputError, match="finite"):
        keys = _sample_keys(2)
        ClassificationTrainingData.build(
            np.array([[0.0, np.nan], [1.0, 2.0]]),
            ("down", "up"),
            sample_keys=keys,
            feature_names=FEATURE_NAMES,
            dataset_input_hash="a" * 64,
            partition=_partition(keys),
        )

    valid = LightGbmClassifierTrainer(_classifier_config()).fit(
        _classification_data()
    )
    artifact = valid.model_copy(update={"model_hash": "0" * 64})
    with pytest.raises(ModelInputError, match="artifact"):
        artifact.predict_proba(
            _classification_data().features(),
            feature_names=FEATURE_NAMES,
            expected_artifact_hash=valid.artifact_hash,
        )

    metadata_tamper = LightGbmClassifierTrainer(_classifier_config()).fit(
        _classification_data()
    ).model_copy(update={"model_version": "lgbm-direction-v2"})
    with pytest.raises(ModelInputError, match="artifact"):
        metadata_tamper.predict_proba(
            _classification_data().features(),
            feature_names=FEATURE_NAMES,
            expected_artifact_hash=valid.artifact_hash,
        )


def test_classifier_rejects_rehashed_quantile_booster_substitution() -> None:
    classifier = LightGbmClassifierTrainer(_classifier_config()).fit(
        _classification_data()
    )
    features = np.column_stack(
        (np.linspace(-2, 2, 30), np.linspace(0.1, 0.9, 30))
    )
    keys = _sample_keys(len(features))
    regression = RegressionTrainingData.build(
        features,
        0.03 * features[:, 0],
        sample_keys=keys,
        feature_names=FEATURE_NAMES,
        dataset_input_hash="a" * 64,
        partition=_partition(keys, task=TaskName.CAPITAL_GAIN_RETURN),
    )
    quantiles = LightGbmQuantileTrainer(
        QuantileConfig(
            task=TaskName.CAPITAL_GAIN_RETURN,
            horizon=5,
            model_version="lgbm-return-v1",
            asset_group="all",
            seed=17,
            num_threads=1,
            num_boost_round=5,
            learning_rate=0.1,
            num_leaves=5,
            min_data_in_leaf=2,
        )
    ).fit(regression)
    payload = classifier.model_dump(mode="python", exclude={"artifact_hash"})
    payload["model_text"] = quantiles.model_texts[0]
    payload["model_hash"] = quantiles.model_hashes[0]
    payload["artifact_hash"] = lightgbm_module._artifact_hash(payload)

    with pytest.raises(ValueError, match="objective"):
        ClassifierArtifact.model_validate(payload, strict=True)


def test_optuna_budget_is_inner_fold_bounded_and_calibration_first() -> None:
    training = _classification_data()
    folds = []
    for index in range(2):
        keys = tuple(
            (
                "US.AAPL",
                START + timedelta(days=100 + index * 20 + row),
                5,
            )
            for row in range(len(training.labels))
        )
        window = EvaluationWindow(
            window_id=f"split-v1.inner-{index:02d}.calibration",
            role=EvaluationRole.CALIBRATION,
            scope=EvaluationScope.SYNTHETIC_OFFLINE,
            start_session=keys[0][1].date(),
            end_session=keys[-1][1].date(),
            split_version="split-v1",
            dataset_input_hash="a" * 64,
            calendar_hash="b" * 64,
            consumed_at=None,
            consumption_reason=None,
        )
        folds.append(
            ClassificationTuningFold.build(
                fold_id=f"inner-{index:02d}",
                training_data=training,
                calibration_window=window,
                calibration_features=training.features(),
                calibration_labels=training.labels,
                calibration_sample_keys=keys,
            )
        )
    inner_folds = tuple(fold.view for fold in folds)
    duplicate_keys = folds[0].model_copy(
        update={
            "calibration_sample_keys": (
                folds[0].calibration_sample_keys[0],
            )
            * len(folds[0].calibration_sample_keys)
        }
    )
    with pytest.raises(ValueError, match="unique"):
        ClassificationTuningFold.model_validate(
            duplicate_keys.model_dump(mode="python"), strict=True
        )
    budget = OptunaBudget(
        trial_count=3,
        parallel_trials=2,
        threads_per_trial=2,
        inner_folds=inner_folds,
        seed=23,
    )

    result = run_bounded_optuna(budget, folds, base_config=_classifier_config())

    assert result.trial_count == 3
    assert result.parallel_trials == 2
    assert result.inner_fold_ids == budget.inner_fold_ids
    assert result.inner_fold_hashes == budget.inner_fold_hashes
    assert len(result.trials) == 3
    assert all(trial.primary_metric == "brier" for trial in result.trials)


def test_optuna_objective_cannot_optimize_portfolio_return_alone() -> None:
    with pytest.raises(ValueError, match="primary_metric"):
        InnerFoldObjective(
            primary_metric="portfolio_return",
            primary_value=0.2,
            ranking_metric="ic",
            ranking_value=0.1,
            evaluated_fold_hashes=("1" * 64, "2" * 64),
            threads_used=4,
        )
