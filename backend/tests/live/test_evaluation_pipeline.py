from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from stock_prediction.live.quantiles import finite_sample_cqr
from stock_prediction.live.training import (
    _ordered_predictions,
    _volatility_quantile_baseline,
    split_validation_selection_calibration,
)


def test_evaluation_partition_and_numerical_policy():
    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(252)]
    frame = pd.DataFrame({"session_date": dates * 2})
    selection, calibration = split_validation_selection_calibration(frame)
    assert selection.session_date.nunique() == 116
    assert calibration.session_date.nunique() == 116
    assert selection.session_date.max() == dates[115]
    assert calibration.session_date.min() == dates[136]
    with pytest.raises(ValueError, match="sessions_insufficient"):
        split_validation_selection_calibration(frame.iloc[:27])

    scores = np.arange(9, dtype=float)
    assert finite_sample_cqr(scores) == 7  # ceil(10 * .8), eighth order statistic
    with pytest.raises(ValueError, match="too_small"):
        finite_sample_cqr(np.zeros(3))
    raw = {
        0.1: np.array([0.3, -0.2]),
        0.5: np.array([-0.1, 0.2]),
        0.9: np.array([0.1, 0.1]),
    }
    ordered = _ordered_predictions(raw, "mfe")
    np.testing.assert_allclose(ordered[0.1], [0, 0])
    np.testing.assert_allclose(ordered[0.9], [0.3, 0.2])
    ordered = _ordered_predictions(raw, "mae")
    np.testing.assert_allclose(ordered[0.9], [0, 0])

    train = pd.DataFrame({"volatility_20": [0.01, 0.02], "return_fwd_5": [0.02, 0.04]})
    test = pd.DataFrame({"volatility_20": [0.03, np.nan], "return_fwd_5": [999, -999]})
    result = _volatility_quantile_baseline(train, test, "return_fwd_5", (0.1, 0.5, 0.9))
    np.testing.assert_allclose(result[0.5], [0.06, 0.03])
    test["return_fwd_5"] = 0
    np.testing.assert_allclose(
        _volatility_quantile_baseline(train, test, "return_fwd_5", (0.5,))[0.5],
        result[0.5],
    )


def test_deployment_uses_selected_recipe_and_serves_evaluated_quantiles(monkeypatch):
    from stock_prediction.live import training
    from stock_prediction.live.inference import _quantile_entry_prediction

    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(600)]
    frame = pd.DataFrame(0.01, index=range(600), columns=training._FEATURES)
    frame["session_date"] = dates
    frame["mfe_5"] = 0.02
    levels = (0.1, 0.25, 0.5, 0.75, 0.9)
    values = dict(zip(levels, (0.04, -0.01, 0.03, 0.01, 0.02), strict=True))
    fitted = []

    def select(train, selection, target):
        assert train.session_date.max() < selection.session_date.min()
        assert selection.session_date.nunique() == 116
        return {"n_estimators": 7}, "test-selected"

    class Model:
        def __init__(self, *, alpha, n_estimators, **kwargs):
            assert n_estimators == 7
            self.alpha = alpha

        def fit(self, x, y):
            fitted.append(len(x))

        def predict(self, x):
            return np.full(len(x), values[self.alpha])

    monkeypatch.setattr(training, "_select_quantile_candidate", select)
    monkeypatch.setattr(training, "LGBMRegressor", Model)
    package = training._fit_deployment_quantiles(frame, "mfe_5", levels)
    assert package["selected_candidate"] == "test-selected"
    assert fitted == [328] * 5
    assert package["partition_evidence"]["calibration_sample_count"] == 116
    served, adjustment = _quantile_entry_prediction(package, frame.iloc[:1])
    evaluated = training._ordered_predictions(
        {level: np.array([value]) for level, value in values.items()}, "mfe"
    )
    np.testing.assert_allclose(served, [evaluated[level][0] for level in levels])
    assert adjustment == 0


def test_oos_archive_preserves_rows_and_refuses_immature_or_overwrite(tmp_path):
    import hashlib
    import pyarrow.parquet as pq
    from stock_prediction.live.training import _write_fold_evidence

    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(8)]
    history = pd.DataFrame(
        {"symbol": ["US.TEST"] * 8, "session_date": dates, "is_etf": 1.0}
    )
    test = history.iloc[[1, 0]]
    values = {"actual": np.array([0.02, -0.01]), "candidate_q50": np.array([0.01, 0.0])}
    evidence = _write_fold_evidence(tmp_path, history, test, "return_fwd", 5, 1, values)
    path = tmp_path / "return_fwd-5-fold-1.parquet"
    assert evidence["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    rows = pq.read_table(path).to_pandas()
    assert rows.session_date.tolist() == [dates[1], dates[0]]
    assert rows.target_end_date.tolist() == [dates[6], dates[5]]
    np.testing.assert_array_equal(rows.actual, values["actual"])
    assert _write_fold_evidence(
        tmp_path, history, test, "return_fwd", 5, 1, values
    ) == evidence
    changed = {**values, "candidate_q50": np.array([0.02, 0.0])}
    with pytest.raises(ValueError, match="oos_resume_evidence_mismatch"):
        _write_fold_evidence(tmp_path, history, test, "return_fwd", 5, 1, changed)
    with pytest.raises(ValueError, match="immature"):
        _write_fold_evidence(
            tmp_path, history, history.iloc[-2:], "return_fwd", 5, 2, values
        )
    assert not (tmp_path / "return_fwd-5-fold-2.parquet").exists()


def test_quantile_task_wires_fold_archive_and_baselines(monkeypatch, tmp_path):
    from stock_prediction.live import training
    from stock_prediction.live.config import LivePaths
    import pyarrow.parquet as pq

    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(700)]
    frame = pd.DataFrame(0.01, index=range(700), columns=training._FEATURES)
    frame["session_date"] = dates
    frame["symbol"] = "US.TEST"
    frame["return_fwd_5"] = np.linspace(-0.02, 0.02, len(frame))
    monkeypatch.setattr(
        training,
        "_walk_forward_folds",
        lambda _: [(dates[:200], dates[220:472], dates[492:618])],
    )
    monkeypatch.setattr(
        training, "_select_quantile_candidate", lambda *args: ({}, "fixed")
    )

    class Model:
        def __init__(self, *, alpha, **kwargs):
            self.alpha = alpha

        def fit(self, x, y):
            self.value = float(y.quantile(self.alpha))

        def predict(self, x):
            return np.full(len(x), self.value)

    monkeypatch.setattr(training, "LGBMRegressor", Model)
    trainer = training.LiveModelTrainer(LivePaths.from_workspace(tmp_path))
    result, package = trainer._quantile_task(
        frame,
        5,
        "return_fwd",
        recipe_version=training.CANDIDATE_RECIPE_V1,
        oos_directory=tmp_path / "oos",
    )
    assert result["fold_count"] == 1 and result["gate_passed"] is False
    fold = result["folds"][0]
    rows = pq.read_table(tmp_path / fold["oos_evidence"]["path"]).to_pandas()
    assert len(rows) == fold["sample_count"] == 126
    assert fold["selection_sample_count"] == fold["calibration_sample_count"] == 116
    assert (
        package["evaluation_pipeline_version"] == training.EVALUATION_PIPELINE_VERSION
    )
    assert "volatility_q50" in rows and "calibrated_lower" in rows


@pytest.mark.parametrize("task", ["forecast_direction", "breakout", "range_exit"])
def test_classification_and_exit_archive_reproduces_fold_brier(
    monkeypatch, tmp_path, task
):
    from stock_prediction.live import training
    from stock_prediction.live.config import LivePaths
    import pyarrow.parquet as pq

    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(700)]
    frame = pd.DataFrame(0.01, index=range(700), columns=training._FEATURES)
    frame["session_date"], frame["symbol"], frame["turnover"] = dates, "US.TEST", 1000
    frame["return_fwd_5"] = 0.01
    frame["direction_5"] = frame["breakout_direction_5"] = np.arange(700) % 3 - 1
    frame["range_exit_event_5"] = np.arange(700) % 2
    frame["range_exit_day_5"] = 5
    monkeypatch.setattr(
        training,
        "_walk_forward_folds",
        lambda _: [(dates[:200], dates[220:472], dates[492:618])],
    )

    class Model:
        classes_ = np.array([-1, 0, 1])

        def predict_proba(self, x):
            return np.tile([0.3, 0.3, 0.4], (len(x), 1))

    monkeypatch.setattr(
        training, "_select_classifier_candidate", lambda *a, **k: (Model(), "fixed")
    )
    monkeypatch.setattr(
        training, "_fit_deployment_classifier", lambda *a, **k: (None, None)
    )
    monkeypatch.setattr(
        training, "_select_survival_candidate", lambda *a, **k: (Model(), "fixed")
    )
    monkeypatch.setattr(training, "_fit_deployment_survival", lambda *a, **k: None)
    trainer = training.LiveModelTrainer(LivePaths.from_workspace(tmp_path))
    if task == "range_exit":
        result, _ = trainer._survival_task(
            frame,
            5,
            recipe_version=training.CANDIDATE_RECIPE_V1,
            oos_directory=tmp_path / "oos",
        )
    else:
        result, _, _ = trainer._classification_task(
            frame,
            5,
            target_prefix="direction"
            if task == "forecast_direction"
            else "breakout_direction",
            task_name=task,
            recipe_version=training.CANDIDATE_RECIPE_V1,
            oos_directory=tmp_path / "oos",
        )
    fold = result["folds"][0]
    rows = pq.read_table(tmp_path / fold["oos_evidence"]["path"]).to_pandas()
    assert len(rows) == fold["sample_count"] == 126
    if task == "range_exit":
        assert fold["time_dependent_brier"] == pytest.approx(
            np.square(rows.candidate_probability - rows.actual).mean()
        )
    else:
        probs = rows[[f"candidate_p_{label}" for label in (-1, 0, 1)]].to_numpy()
        assert fold["candidate_brier"] == pytest.approx(
            np.square(probs - np.eye(3)[rows.actual.to_numpy() + 1]).sum(axis=1).mean()
        )


def test_survival_calibration_selection_uses_dates_not_symbol_row_order(monkeypatch):
    from stock_prediction.live import training

    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(116)]
    frame = pd.DataFrame(
        {"session_date": dates + dates[::-1], "range_exit_event_20": np.arange(232) % 2}
    )
    raw = np.array([dates.index(day) / 116 for day in frame.session_date])
    seen = []

    class Platt:
        def __init__(self, **kwargs):
            pass

        def fit(self, x, y):
            seen.append(x[:, 0].copy())

        def predict_proba(self, x):
            return np.tile([0.5, 0.5], (len(x), 1))

    monkeypatch.setattr(training, "_cumulative_exit_probability", lambda *a: raw)
    monkeypatch.setattr(training, "LogisticRegression", Platt)
    monkeypatch.setattr(training, "_refit_survival_calibrator", lambda *a: None)
    training._fit_survival_calibrator(object(), frame, 20, [], compare_variants=True)
    assert len(seen) == 1
    assert len(seen[0]) == 96  # first 48 dates for both symbols
    assert seen[0].max() == 47 / 116


def test_survival_deployment_keeps_selected_model_and_separate_calibration(monkeypatch):
    from stock_prediction.live import training

    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(700)]
    frame = pd.DataFrame(0.01, index=range(700), columns=training._FEATURES)
    frame["session_date"] = dates
    frame["range_exit_event_5"] = np.arange(700) % 2
    frame["range_exit_day_5"] = 5
    selected = object()

    def select(person_period, selection, horizon):
        assert person_period.session_date.max() == dates[427]
        assert selection.session_date.min() == dates[448]
        assert selection.session_date.max() == dates[563]
        return selected, "selected-hazard"

    def calibrate(model, calibration, *args, **kwargs):
        assert model is selected
        assert calibration.session_date.min() == dates[584]
        assert calibration.session_date.max() == dates[-1]
        return None

    monkeypatch.setattr(training, "_select_survival_candidate", select)
    monkeypatch.setattr(training, "_fit_survival_calibrator", calibrate)
    package = training._fit_deployment_survival(frame, 5)
    assert package["model"] is selected
    assert package["selected_candidate"] == "selected-hazard"


@pytest.mark.parametrize("candidate", [None, "fixed-classifier"])
def test_classifier_deployment_always_uses_separate_calibration(monkeypatch, candidate):
    from stock_prediction.live import training

    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(700)]
    frame = pd.DataFrame(0.01, index=range(700), columns=training._FEATURES)
    frame["session_date"] = dates
    frame["direction_5"] = np.arange(700) % 3 - 1
    frame[training._FEATURES[0]] = np.arange(700)

    class Model:
        classes_ = np.array([-1, 0, 1])

        def predict_proba(self, rows):
            assert rows[training._FEATURES[0]].tolist() == list(range(584, 700))
            return np.tile([0.3, 0.3, 0.4], (len(rows), 1))

    model = Model()

    def select(train, selection, target, **kwargs):
        assert train.session_date.max() == dates[427]
        assert selection.session_date.tolist() == dates[448:564]
        return model, "fixed-classifier"

    def fit(train, target, selected_candidate, **kwargs):
        assert selected_candidate == "fixed-classifier"
        assert train.session_date.max() == dates[427]
        return model

    monkeypatch.setattr(training, "_select_classifier_candidate", select)
    monkeypatch.setattr(training, "_fit_classifier_candidate_id", fit)
    monkeypatch.setattr(training, "_fit_temperature", lambda *args: 1.0)
    fitted, temperature = training._fit_deployment_classifier(
        frame, "direction_5", selected_candidate=candidate
    )
    assert fitted is model and temperature == 1.0
