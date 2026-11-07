from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

import numpy as np
import pytest

from stock_prediction.modeling.calibration import (
    CalibrationInputError,
    CalibrationPartitionEvidence,
    CqrCalibrator,
    PlattScaler,
    TemperatureScaler,
    rearrange_quantiles,
)
from stock_prediction.modeling.contracts import (
    EvaluationRole,
    EvaluationScope,
    EvaluationWindow,
    TaskName,
)
from stock_prediction.modeling.lightgbm_models import (
    ClassifierConfig,
    LightGbmClassifierTrainer,
    LightGbmQuantileTrainer,
    QuantileConfig,
)
from stock_prediction.modeling.training import (
    ClassificationTrainingData,
    RegressionTrainingData,
    TrainingPartitionEvidence,
)


def _window(role: EvaluationRole = EvaluationRole.CALIBRATION) -> EvaluationWindow:
    return EvaluationWindow(
        window_id=f"split-v1.{role.value}",
        role=role,
        scope=EvaluationScope.SYNTHETIC_OFFLINE,
        start_session=date(2022, 1, 3),
        end_session=date(2022, 6, 30),
        split_version="split-v1",
        dataset_input_hash="a" * 64,
        calendar_hash="b" * 64,
        consumed_at=None,
        consumption_reason=None,
    )


def _partition(
    count: int,
    role: EvaluationRole = EvaluationRole.CALIBRATION,
    task: TaskName = TaskName.FORECAST_DIRECTION,
):
    keys = tuple(
        ("US.AAPL", datetime(2022, 1, 3, 21, tzinfo=UTC) + timedelta(days=index), 5)
        for index in range(count)
    )
    return (
        CalibrationPartitionEvidence.register(
            partition_id=f"split-v1.{role.value}.h5",
            task=task,
            horizon=5,
            asset_group="all",
            evaluation_window=_window(role),
            registered_sample_keys=keys,
            split_manifest_hash="d" * 64,
            source_fold_hash="e" * 64,
        ),
        keys,
    )


@lru_cache(maxsize=1)
def _source_classifier():
    keys = tuple(
        ("US.AAPL", datetime(2020, 1, 2, 21, tzinfo=UTC) + timedelta(days=index), 5)
        for index in range(6)
    )
    window = EvaluationWindow(
        window_id="split-v1.train",
        role=EvaluationRole.TRAIN,
        scope=EvaluationScope.SYNTHETIC_OFFLINE,
        start_session=keys[0][1].date(),
        end_session=keys[-1][1].date(),
        split_version="split-v1",
        dataset_input_hash="a" * 64,
        calendar_hash="b" * 64,
        consumed_at=None,
        consumption_reason=None,
    )
    partition = TrainingPartitionEvidence.register(
        partition_id="direction-h5-train",
        task=TaskName.FORECAST_DIRECTION,
        horizon=5,
        evaluation_window=window,
        registered_sample_keys=keys,
        split_manifest_hash="d" * 64,
        source_fold_hash="e" * 64,
    )
    features = np.column_stack((np.linspace(-1, 1, 6), np.linspace(0.1, 0.6, 6)))
    training = ClassificationTrainingData.build(
        features,
        ("down", "down", "down", "up", "up", "up"),
        sample_keys=keys,
        feature_names=("momentum", "compression"),
        dataset_input_hash="a" * 64,
        partition=partition,
    )
    return LightGbmClassifierTrainer(
        ClassifierConfig(
            task=TaskName.FORECAST_DIRECTION,
            horizon=5,
            model_version="direction-source-v1",
            asset_group="all",
            class_order=("down", "up"),
            seed=17,
            num_threads=1,
            num_boost_round=5,
            learning_rate=0.1,
            num_leaves=5,
            min_data_in_leaf=1,
        )
    ).fit(training)


@lru_cache(maxsize=1)
def _source_multiclass_classifier():
    keys = tuple(
        ("US.AAPL", datetime(2020, 2, 3, 21, tzinfo=UTC) + timedelta(days=index), 5)
        for index in range(9)
    )
    window = EvaluationWindow(
        window_id="split-v1.multiclass-train",
        role=EvaluationRole.TRAIN,
        scope=EvaluationScope.SYNTHETIC_OFFLINE,
        start_session=keys[0][1].date(),
        end_session=keys[-1][1].date(),
        split_version="split-v1",
        dataset_input_hash="a" * 64,
        calendar_hash="b" * 64,
        consumed_at=None,
        consumption_reason=None,
    )
    partition = TrainingPartitionEvidence.register(
        partition_id="direction-multiclass-h5-train",
        task=TaskName.FORECAST_DIRECTION,
        horizon=5,
        evaluation_window=window,
        registered_sample_keys=keys,
        split_manifest_hash="d" * 64,
        source_fold_hash="e" * 64,
    )
    features = np.column_stack((np.linspace(-1, 1, 9), np.linspace(0.1, 0.9, 9)))
    training = ClassificationTrainingData.build(
        features,
        ("up", "up", "up", "flat", "flat", "flat", "down", "down", "down"),
        sample_keys=keys,
        feature_names=("momentum", "compression"),
        dataset_input_hash="a" * 64,
        partition=partition,
    )
    return LightGbmClassifierTrainer(
        ClassifierConfig(
            task=TaskName.FORECAST_DIRECTION,
            horizon=5,
            model_version="direction-multiclass-source-v1",
            asset_group="all",
            class_order=("up", "flat", "down"),
            seed=17,
            num_threads=1,
            num_boost_round=5,
            learning_rate=0.1,
            num_leaves=5,
            min_data_in_leaf=1,
        )
    ).fit(training)


@lru_cache(maxsize=1)
def _source_quantiles():
    keys = tuple(
        ("US.AAPL", datetime(2020, 1, 2, 21, tzinfo=UTC) + timedelta(days=index), 5)
        for index in range(10)
    )
    window = EvaluationWindow(
        window_id="split-v1.return-train",
        role=EvaluationRole.TRAIN,
        scope=EvaluationScope.SYNTHETIC_OFFLINE,
        start_session=keys[0][1].date(),
        end_session=keys[-1][1].date(),
        split_version="split-v1",
        dataset_input_hash="a" * 64,
        calendar_hash="b" * 64,
        consumed_at=None,
        consumption_reason=None,
    )
    partition = TrainingPartitionEvidence.register(
        partition_id="return-h5-train",
        task=TaskName.CAPITAL_GAIN_RETURN,
        horizon=5,
        evaluation_window=window,
        registered_sample_keys=keys,
        split_manifest_hash="d" * 64,
        source_fold_hash="e" * 64,
    )
    features = np.column_stack((np.linspace(-1, 1, 10), np.linspace(0.1, 0.9, 10)))
    training = RegressionTrainingData.build(
        features,
        np.linspace(-0.05, 0.05, 10),
        sample_keys=keys,
        feature_names=("momentum", "compression"),
        dataset_input_hash="a" * 64,
        partition=partition,
    )
    return LightGbmQuantileTrainer(
        QuantileConfig(
            task=TaskName.CAPITAL_GAIN_RETURN,
            horizon=5,
            model_version="return-source-v1",
            asset_group="all",
            seed=17,
            num_threads=1,
            num_boost_round=5,
            learning_rate=0.1,
            num_leaves=5,
            min_data_in_leaf=1,
        )
    ).fit(training)
def test_temperature_scaler_is_immutable_and_returns_simplex() -> None:
    logits = np.array(
        [[2.0, 0.0, -1.0], [0.0, 2.0, -1.0], [-1.0, 0.0, 2.0], [1.0, 0.5, 0.0]],
        dtype=np.float64,
    )
    labels = ("up", "flat", "down", "up")
    partition, keys = _partition(len(labels))
    source = _source_multiclass_classifier()
    scaler = TemperatureScaler.fit(
        logits,
        labels,
        class_order=("up", "flat", "down"),
        calibration_partition=partition,
        sample_keys=keys,
        source_model=source,
        expected_source_model_artifact_hash=source.artifact_hash,
    )
    before = scaler.model_dump_json()
    probabilities = scaler.apply(
        logits,
        expected_artifact_hash=scaler.artifact_hash,
        source_model_artifact_hash=source.artifact_hash,
    )

    assert probabilities.shape == logits.shape
    assert probabilities.sum(axis=1) == pytest.approx(np.ones(len(logits)))
    assert np.all((probabilities >= 0) & (probabilities <= 1))
    assert probabilities.flags.writeable is False
    assert scaler.model_dump_json() == before


def test_calibrators_reject_non_calibration_window_and_wrong_source_model() -> None:
    source = _source_classifier()
    with pytest.raises(CalibrationInputError, match="CALIBRATION"):
        partition, keys = _partition(4, EvaluationRole.OUTER_TEST)
        PlattScaler.fit(
            (-2.0, -1.0, 1.0, 2.0),
            (0, 0, 1, 1),
            calibration_partition=partition,
            sample_keys=keys,
            source_model=source,
            expected_source_model_artifact_hash=source.artifact_hash,
        )

    partition, keys = _partition(4)
    fitted = PlattScaler.fit(
        (-2.0, -1.0, 1.0, 2.0),
        (0, 0, 1, 1),
        calibration_partition=partition,
        sample_keys=keys,
        source_model=source,
        expected_source_model_artifact_hash=source.artifact_hash,
    )
    with pytest.raises(CalibrationInputError, match="exactly match"):
        PlattScaler.fit(
            (-2.0, -1.0, 1.0, 2.0),
            (0, 0, 1, 1),
            calibration_partition=partition,
            sample_keys=tuple(reversed(keys)),
            source_model=source,
            expected_source_model_artifact_hash=source.artifact_hash,
        )
    with pytest.raises(CalibrationInputError, match="source model"):
        fitted.apply(
            (-0.5, 0.5),
            expected_artifact_hash=fitted.artifact_hash,
            source_model_artifact_hash="d" * 64,
        )


def test_outer_residual_cannot_update_cqr() -> None:
    calibration_y = np.array([0.5, 1.1, 1.2, 1.3, 2.0])
    q10 = np.zeros(5)
    q90 = np.ones(5)
    partition, keys = _partition(5, task=TaskName.CAPITAL_GAIN_RETURN)
    source = _source_quantiles()
    fitted = CqrCalibrator.fit(
        calibration_y,
        q10,
        q90,
        target_coverage=0.80,
        calibration_partition=partition,
        sample_keys=keys,
        source_model=source,
        expected_source_model_artifact_hash=source.artifact_hash,
    )
    before = fitted.model_dump_json()
    interval = fitted.apply(
        (0.0, 0.2),
        (1.0, 1.2),
        expected_artifact_hash=fitted.artifact_hash,
        source_model_artifact_hash=source.artifact_hash,
    )

    assert fitted.finite_sample_rank == 5
    assert fitted.conformal_score == pytest.approx(1.0)
    assert interval.lower == pytest.approx((-1.0, -0.8))
    assert interval.upper == pytest.approx((2.0, 2.2))
    assert fitted.model_dump_json() == before


def test_cqr_rejects_unachievable_small_sample_coverage() -> None:
    partition, keys = _partition(2, task=TaskName.CAPITAL_GAIN_RETURN)
    source = _source_quantiles()
    with pytest.raises(CalibrationInputError, match="too small"):
        CqrCalibrator.fit(
            (0.0, 0.1),
            (-0.1, 0.0),
            (0.1, 0.2),
            target_coverage=0.95,
            calibration_partition=partition,
            sample_keys=keys,
            source_model=source,
            expected_source_model_artifact_hash=source.artifact_hash,
        )


def test_calibration_rejects_source_task_mismatch() -> None:
    partition, keys = _partition(4, task=TaskName.BREAKOUT)
    source = _source_classifier()
    with pytest.raises(CalibrationInputError, match="task, horizon, or asset"):
        PlattScaler.fit(
            (-2.0, -1.0, 1.0, 2.0),
            (0, 0, 1, 1),
            calibration_partition=partition,
            sample_keys=keys,
            source_model=source,
            expected_source_model_artifact_hash=source.artifact_hash,
        )


def test_quantile_rearrangement_is_ordered_and_reports_crossing() -> None:
    raw = np.array(
        [[0.4, 0.2, 0.3, 0.5, 0.1], [0.1, 0.2, 0.3, 0.4, 0.5]],
        dtype=np.float64,
    )
    result = rearrange_quantiles(raw)

    assert result.quantiles[0] == pytest.approx((0.1, 0.2, 0.3, 0.4, 0.5))
    assert result.crossing_row_count == 1
    assert result.crossing_rate == pytest.approx(0.5)
    assert result.maximum_violation == pytest.approx(0.4)


def test_calibration_rejects_non_finite_and_wrong_expected_identity() -> None:
    source = _source_classifier()
    with pytest.raises(CalibrationInputError, match="finite"):
        partition, keys = _partition(2)
        TemperatureScaler.fit(
            np.array([[0.0, np.nan], [1.0, 0.0]]),
            ("down", "up"),
            class_order=("down", "up"),
            calibration_partition=partition,
            sample_keys=keys,
            source_model=source,
            expected_source_model_artifact_hash=source.artifact_hash,
        )

    partition, keys = _partition(4)
    fitted = PlattScaler.fit(
        (-2.0, -1.0, 1.0, 2.0),
        (0, 0, 1, 1),
        calibration_partition=partition,
        sample_keys=keys,
        source_model=source,
        expected_source_model_artifact_hash=source.artifact_hash,
    )
    with pytest.raises(CalibrationInputError, match="registered identity"):
        fitted.apply(
            (-0.5, 0.5),
            expected_artifact_hash="0" * 64,
            source_model_artifact_hash=source.artifact_hash,
        )
