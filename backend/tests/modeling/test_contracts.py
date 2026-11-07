from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from stock_prediction.modeling.contracts import (
    BacktestTrial,
    EvaluationRole,
    EvaluationScope,
    EvaluationWindow,
    ModelLifecycleStatus,
    ModelRun,
    TaskName,
    TrialStatus,
)


NOW = datetime(2026, 7, 12, 15, tzinfo=UTC)


def evaluation_window(**updates: object) -> EvaluationWindow:
    values: dict[str, object] = {
        "window_id": "outer-fold-1-test",
        "role": EvaluationRole.OUTER_TEST,
        "scope": EvaluationScope.SYNTHETIC_OFFLINE,
        "start_session": date(2024, 1, 2),
        "end_session": date(2024, 6, 28),
        "split_version": "split-v1",
        "dataset_input_hash": "a" * 64,
        "calendar_hash": "b" * 64,
        "consumed_at": None,
        "consumption_reason": None,
    }
    values.update(updates)
    return EvaluationWindow(**values)


def trial(**updates: object) -> BacktestTrial:
    values: dict[str, object] = {
        "trial_id": "trial-direction-h5-001",
        "registered_at": NOW,
        "owner": "local-researcher",
        "hypothesis": "LightGBM improves calibrated direction Brier skill.",
        "task": TaskName.FORECAST_DIRECTION,
        "horizon": 5,
        "model_version": "lgbm-direction-v1",
        "feature_set_version": "core-v1",
        "threshold_version": "threshold-v1",
        "label_version": "label-v1",
        "split_version": "split-v1",
        "dataset_input_hash": "a" * 64,
        "calendar_hash": "b" * 64,
        "evaluation_window_id": "outer-fold-1-test",
        "evaluation_window": evaluation_window(),
        "primary_metrics": ("brier_skill",),
        "secondary_metrics": ("pr_auc", "ece"),
        "frozen_parameters": {"num_leaves": 31, "learning_rate": 0.05},
        "parent_trial_id": None,
        "status": TrialStatus.REGISTERED,
        "result": None,
        "stop_reason": None,
        "touched_lockbox": False,
        "consumed_at": None,
    }
    values.update(updates)
    return BacktestTrial(**values)


def test_evaluation_window_consumption_fields_are_atomic() -> None:
    with pytest.raises(ValidationError, match="consumption"):
        evaluation_window(consumed_at=NOW)
    with pytest.raises(ValidationError, match="reversed"):
        evaluation_window(
            start_session=date(2024, 7, 1),
            end_session=date(2024, 6, 28),
        )
    with pytest.raises(ValidationError, match="window ends"):
        evaluation_window(
            consumed_at=datetime(2023, 12, 31, tzinfo=UTC),
            consumption_reason="premature_review",
        )


def test_trial_registration_cannot_pretend_to_have_result_or_lockbox_consumption() -> None:
    with pytest.raises(ValidationError, match="registered trial"):
        trial(result={"brier_skill": 0.01})
    with pytest.raises(ValidationError, match="lockbox"):
        trial(touched_lockbox=True, consumed_at=None)


def test_trial_registration_binds_the_complete_evaluation_window() -> None:
    with pytest.raises(ValidationError, match="window IDs"):
        trial(evaluation_window=evaluation_window(window_id="different-window"))
    with pytest.raises(ValidationError, match="consumed evaluation window"):
        trial(
            evaluation_window=evaluation_window(
                consumed_at=NOW,
                consumption_reason="prior_evaluation",
            )
        )


def test_trial_metric_names_are_unique_and_disjoint() -> None:
    with pytest.raises(ValidationError, match="unique"):
        trial(primary_metrics=("brier_skill", "brier_skill"))
    with pytest.raises(ValidationError, match="overlap"):
        trial(secondary_metrics=("brier_skill",))


def test_trial_parameters_are_deeply_immutable() -> None:
    registered = trial()

    with pytest.raises(TypeError):
        registered.frozen_parameters["num_leaves"] = 63

    nested = trial(frozen_parameters={"grid": [1, 2]})
    assert nested.frozen_parameters["grid"] == (1, 2)
    assert BacktestTrial.model_validate_json(nested.model_dump_json()) == nested


def test_model_run_binds_folds_dependencies_metrics_and_lifecycle() -> None:
    run = ModelRun(
        run_id="run-direction-h5-001",
        model_version="lgbm-direction-v1",
        task=TaskName.FORECAST_DIRECTION,
        horizon=5,
        asset_group="all",
        split_manifest_hash="c" * 64,
        dataset_input_hash="a" * 64,
        universe_version="universe-v1",
        feature_set_version="core-v1",
        label_version="label-v1",
        threshold_version="threshold-v1",
        transformer_input_hash="d" * 64,
        parameters={"seed": 17},
        seed=17,
        dependencies={"lightgbm": "4.6.0"},
        fold_metrics={"outer-fold-1-test": {"brier_skill": 0.02}},
        aggregate_metrics={"median_brier_skill": 0.02},
        calibration_metrics={"ece": 0.03},
        coverage_metrics={},
        latency_ms=15.0,
        status=ModelLifecycleStatus.EVALUATED,
        created_at=NOW,
        evaluation_window_ids=("outer-fold-1-test",),
    )

    assert ModelRun.model_validate_json(run.model_dump_json()) == run
    with pytest.raises(ValidationError, match="fold metrics"):
        ModelRun(**{**run.model_dump(), "fold_metrics": {}})
    with pytest.raises(ValidationError, match="evaluation window"):
        ModelRun(
            **{
                **run.model_dump(),
                "fold_metrics": {"unregistered-window": {"brier_skill": 0.02}},
            }
        )
