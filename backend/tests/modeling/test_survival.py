from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from stock_prediction.modeling.contracts import (
    EvaluationRole,
    EvaluationScope,
    EvaluationWindow,
    TaskName,
)
from stock_prediction.modeling.survival import (
    DiscreteHazardConfig,
    DiscreteHazardTrainer,
    PersonPeriodEvidence,
    PersonPeriodRow,
    RangeExitObservation,
    SurvivalInputError,
    build_person_period_rows,
    cumulative_incidence,
)
from stock_prediction.modeling.training import (
    ClassificationTrainingData,
    TrainingPartitionEvidence,
)


def test_right_censored_range_event_has_no_fake_positive() -> None:
    observation = RangeExitObservation(
        observation_id="range-aapl-001",
        observed_days=7,
        event_day=None,
        right_censored=True,
    )
    rows = build_person_period_rows((observation,), max_horizon=20)

    assert len(rows) == 7
    assert all(row.event == 0 for row in rows)
    assert rows[-1].censored is True
    assert all(row.censored is False for row in rows[:-1])


def test_observed_event_stops_at_event_day_with_one_positive() -> None:
    observation = RangeExitObservation(
        observation_id="range-msft-001",
        observed_days=20,
        event_day=4,
        right_censored=False,
    )
    rows = build_person_period_rows((observation,), max_horizon=20)

    assert tuple(row.day for row in rows) == (1, 2, 3, 4)
    assert tuple(row.event for row in rows) == (0, 0, 0, 1)
    assert all(row.censored is False for row in rows)


def test_fully_observed_non_event_has_no_censor_marker() -> None:
    observation = RangeExitObservation(
        observation_id="range-spy-001",
        observed_days=20,
        event_day=None,
        right_censored=False,
    )
    rows = build_person_period_rows((observation,), max_horizon=20)

    assert len(rows) == 20
    assert all(row.event == 0 and row.censored is False for row in rows)


def test_short_non_event_must_be_right_censored() -> None:
    observation = RangeExitObservation(
        observation_id="range-short-001",
        observed_days=3,
        event_day=None,
        right_censored=False,
    )
    with pytest.raises(SurvivalInputError, match="right-censored"):
        build_person_period_rows((observation,), max_horizon=20)


def test_person_period_evidence_rejects_illegal_risk_sets() -> None:
    short_rows = tuple(
        PersonPeriodRow(
            observation_id="short", day=day, event=0, censored=False
        )
        for day in range(1, 4)
    )
    with pytest.raises(ValueError, match="event or censoring"):
        PersonPeriodEvidence.from_rows(short_rows, horizon=20)

    too_long = tuple(
        PersonPeriodRow(
            observation_id="long", day=day, event=0, censored=False
        )
        for day in range(1, 7)
    )
    with pytest.raises(ValueError, match="exceeds horizon"):
        PersonPeriodEvidence.from_rows(too_long, horizon=5)


@pytest.mark.parametrize(
    "observation",
    [
        RangeExitObservation.model_construct(
            observation_id="bad-event-after-observation",
            observed_days=3,
            event_day=4,
            right_censored=False,
        ),
        RangeExitObservation.model_construct(
            observation_id="bad-censored-event",
            observed_days=3,
            event_day=2,
            right_censored=True,
        ),
    ],
)
def test_person_period_builder_revalidates_observation_contract(
    observation: RangeExitObservation,
) -> None:
    with pytest.raises(SurvivalInputError, match="observation"):
        build_person_period_rows((observation,), max_horizon=20)


def test_cumulative_incidence_is_monotone_and_hand_calculated() -> None:
    incidence = cumulative_incidence((0.10, 0.20, 0.30))

    assert incidence == pytest.approx((0.10, 0.28, 0.496))
    assert np.all(np.diff(incidence) >= 0)


def test_cumulative_incidence_rejects_invalid_hazards() -> None:
    with pytest.raises(SurvivalInputError, match="hazard"):
        cumulative_incidence((0.1, float("nan")))
    with pytest.raises(SurvivalInputError, match="hazard"):
        cumulative_incidence((0.1, 1.1))
    with pytest.raises(SurvivalInputError, match="hazard"):
        cumulative_incidence(())


def test_discrete_hazard_model_returns_daily_hazard_and_cumulative_exit() -> None:
    days = np.tile(np.arange(1, 6, dtype=np.float64), 4)
    compression = np.repeat((0.1, 0.2, 0.8, 0.9), 5)
    features = np.column_stack((days, compression))
    labels = tuple(
        "exit" if day == 5 and group >= 2 else "no_exit"
        for group in range(4)
        for day in range(1, 6)
    )
    start = datetime(2020, 1, 2, 21, tzinfo=UTC)
    keys = tuple(
        ("US.AAPL", start + timedelta(days=index), 5)
        for index in range(len(labels))
    )
    window = EvaluationWindow(
        window_id="split-v1.train",
        role=EvaluationRole.TRAIN,
        scope=EvaluationScope.SYNTHETIC_OFFLINE,
        start_session=keys[0][1].date(),
        end_session=keys[-1][1].date(),
        split_version="split-v1",
        dataset_input_hash="b" * 64,
        calendar_hash="c" * 64,
        consumed_at=None,
        consumption_reason=None,
    )
    partition = TrainingPartitionEvidence.register(
        partition_id="range-exit-train-5",
        task=TaskName.RANGE_EXIT_SURVIVAL,
        horizon=5,
        evaluation_window=window,
        registered_sample_keys=keys,
        split_manifest_hash="d" * 64,
        source_fold_hash="e" * 64,
    )
    training = ClassificationTrainingData.build(
        features,
        labels,
        sample_keys=keys,
        feature_names=("day_at_risk", "compression"),
        dataset_input_hash="b" * 64,
        partition=partition,
    )
    observations = tuple(
        RangeExitObservation(
            observation_id=f"range-{group}",
            observed_days=5,
            event_day=5 if group >= 2 else None,
            right_censored=False,
        )
        for group in range(4)
    )
    person_period = PersonPeriodEvidence.from_rows(
        build_person_period_rows(observations, max_horizon=5), horizon=5
    )
    artifact = DiscreteHazardTrainer(
        DiscreteHazardConfig(
            horizon=5,
            model_version="range-exit-v1",
            asset_group="all",
            seed=19,
            num_threads=1,
            num_boost_round=10,
            learning_rate=0.1,
            num_leaves=5,
            min_data_in_leaf=2,
        )
    ).fit(training, person_period_evidence=person_period)
    forecast = artifact.predict_exit_curve(
        features[:5],
        feature_names=("day_at_risk", "compression"),
        expected_artifact_hash=artifact.artifact_hash,
    )

    assert len(forecast.daily_hazards) == 5
    assert len(forecast.cumulative_exit_probabilities) == 5
    assert all(0 <= value <= 1 for value in forecast.daily_hazards)
    assert np.all(np.diff(forecast.cumulative_exit_probabilities) >= 0)
    assert forecast.horizon == 5

    with pytest.raises(SurvivalInputError, match="ordered"):
        artifact.predict_exit_curve(
            features[:5][::-1],
            feature_names=("day_at_risk", "compression"),
            expected_artifact_hash=artifact.artifact_hash,
        )

    tampered = artifact.model_copy(update={"person_period_hash": "0" * 64})
    with pytest.raises(SurvivalInputError, match="artifact"):
        tampered.predict_exit_curve(
            features[:5],
            feature_names=("day_at_risk", "compression"),
            expected_artifact_hash=artifact.artifact_hash,
        )


def test_range_exit_survival_rejects_one_day_horizon() -> None:
    with pytest.raises(ValueError, match="horizon"):
        DiscreteHazardConfig(
            horizon=1,
            model_version="invalid-range-exit-v1",
            asset_group="all",
            seed=19,
        )
