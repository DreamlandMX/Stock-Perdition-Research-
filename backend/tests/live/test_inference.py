from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import pickle

import numpy as np
import pandas as pd
import pytest

from stock_prediction.contracts.serving import (
    PredictionReadiness,
    ResearchReadiness,
    ResolvedSnapshot,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.inference import LiveInferenceEngine
from stock_prediction.operations.disk import DiskSpaceGuard
from stock_prediction.operations.registry import LocalModelRegistry


NOW = datetime(2026, 7, 28, 20, tzinfo=UTC)


def test_batch_quantiles_keep_per_row_cqr_groups_and_missing_values():
    from stock_prediction.live.inference import _batch_entry, _quantile_entry_prediction

    values = pd.DataFrame({"feature_a": [np.nan, 2.0], "is_etf": [0.0, 1.0]})
    entry = {
        "models": {
            alpha: _ConstantQuantileModel(value)
            for alpha, value in zip(
                (0.1, 0.25, 0.5, 0.75, 0.9), (0.2, -0.1, 0.0, 0.1, 0.3), strict=True
            )
        },
        "cqr_adjustment": 0.03,
        "cqr_adjustments": {"stock": 0.02, "etf": 0.07},
    }
    batched = _batch_entry(entry, values, kind="quantile")
    for index in values.index:
        assert _quantile_entry_prediction(
            batched, values.loc[[index]]
        ) == _quantile_entry_prediction(entry, values.loc[[index]])


class _ConstantQuantileModel:
    def __init__(self, value: float) -> None:
        self.value = value

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), self.value, dtype=float)


def test_similar_cases_do_not_convert_full_feature_history_to_object_rows(monkeypatch):
    from stock_prediction.live.inference import _similar_cases

    history = pd.DataFrame(
        {
            "symbol": ["US.SPY"] * 25,
            "session_date": pd.date_range("2020-01-01", periods=25, freq="30D"),
            "feature_a": np.arange(25, dtype=float),
            "return_fwd_20": np.arange(25, dtype=float) / 100,
            "mae_20": np.full(25, -0.1),
            "regime": ["range"] * 25,
            "breakout_direction_20": np.zeros(25, dtype=int),
        }
    )
    row = pd.Series({"session_date": pd.Timestamp("2026-11-04"), "feature_a": 0.0})
    args = (
        "US.SPY",
        row,
        history,
        ("feature_a",),
        {"feature_a": 0.0},
        {"feature_a": 1.0},
    )
    expected = _similar_cases(*args)
    assert expected[0].effective_independent_count == 20
    prepared = {}
    assert _similar_cases(*args, prepared=prepared) == expected
    other_row = row.copy()
    other_row["feature_a"] = 20.0
    other_args = (
        "US.SPY",
        other_row,
        history,
        ("feature_a",),
        {"feature_a": 0.0},
        {"feature_a": 1.0},
    )
    assert _similar_cases(*other_args, prepared=prepared) == _similar_cases(*other_args)
    assert len(prepared) == 1
    assert "feature_a" not in next(iter(prepared.values()))[0].columns
    other_row["session_date"] = pd.Timestamp("2020-07-01")
    assert _similar_cases(*other_args, prepared=prepared) == _similar_cases(*other_args)
    assert len(prepared) == 1

    def reject_iterrows(*args, **kwargs):
        raise AssertionError(
            "Full feature history must not be converted to object rows"
        )

    monkeypatch.setattr(pd.DataFrame, "iterrows", reject_iterrows)
    assert _similar_cases(*args) == expected


class _CountingClassifier:
    classes_ = np.asarray((-1, 0, 1))

    def __init__(self):
        self.calls = 0
        self.n_jobs = 4

    def set_params(self, **params):
        self.n_jobs = params["n_jobs"]

    def predict_proba(self, frame):
        self.calls += 1
        up = 0.2 + frame["feature_a"].to_numpy() * 0.01
        if "elapsed_day" in frame:
            up += frame["elapsed_day"].to_numpy() * 0.001
        return np.column_stack((up, 0.5 - up, np.full(len(frame), 0.5)))


def test_batch_classifier_matches_scalar_temperature_and_calls_model_once():
    from stock_prediction.live.inference import (
        _batch_entry,
        _classifier_entry_probability,
    )

    model = _CountingClassifier()
    values = pd.DataFrame({"feature_a": [1.0, np.nan, 3.0]}, index=[7, 2, 9]).fillna(0)
    entry = {"model": model, "temperature": 1.7}
    expected = [
        _classifier_entry_probability(entry, values.loc[[index]])
        for index in values.index
    ]
    model.calls = 0
    batched = _batch_entry(entry, values, kind="classifier")
    for index, expected_row in zip(values.index, expected, strict=True):
        assert _classifier_entry_probability(
            batched, values.loc[[index]]
        ) == pytest.approx(expected_row)
    assert model.calls == 1


def test_batch_survival_matches_scalar_curve_and_terminal_probability():
    from stock_prediction.live.inference import (
        _batch_entry,
        _survival_curve,
        _survival_component_probability,
    )

    model = _CountingClassifier()
    values = pd.DataFrame({"feature_a": [1.0, 3.0]}, index=[7, 9])
    entry = {"model": model}
    expected = [
        _survival_curve(
            {"survival": {5: entry}}, 5, values.loc[[index]], ("feature_a",)
        )
        for index in values.index
    ]
    terminal = [
        _survival_component_probability(entry, 5, values.loc[[index]], ("feature_a",))
        for index in values.index
    ]
    model.calls = 0
    batched = _batch_entry(entry, values, kind="survival", horizon=5)
    for index, curve, probability in zip(values.index, expected, terminal, strict=True):
        assert (
            _survival_curve(
                {"survival": {5: batched}}, 5, values.loc[[index]], ("feature_a",)
            )
            == curve
        )
        assert _survival_component_probability(
            batched, 5, values.loc[[index]], ("feature_a",)
        ) == pytest.approx(probability)
    assert model.calls == 1


def test_thread_limits_reach_nested_models_and_shared_references():
    from types import SimpleNamespace
    from stock_prediction.live.inference import (
        _limit_inference_threads,
        _limit_component_threads,
    )

    inner = _CountingClassifier()
    wrapper = SimpleNamespace(
        linear=SimpleNamespace(model=inner), nonlinear=SimpleNamespace(model=inner)
    )
    _limit_inference_threads(
        {"classifiers": {("forecast_direction", 1): {"model": wrapper}}}
    )
    assert inner.n_jobs == 1
    inner.n_jobs = 4
    _limit_component_threads("forecast_direction", {"model": wrapper})
    assert inner.n_jobs == 1


def _registry(root: Path) -> LocalModelRegistry:
    return LocalModelRegistry(
        root,
        disk_guard=DiskSpaceGuard(
            minimum_free_bytes=0,
            minimum_free_ratio=0,
            usage=lambda _path: (1_000_000, 0, 1_000_000),
        ),
    )


def _component_package(
    path: Path,
    *,
    model_values: tuple[float, float, float, float, float],
    task: str = "return_fwd",
    horizon: int = 20,
    gate_passed: bool | None = None,
) -> Path:
    gate_metrics: dict[str, object] = {
        "fold_count": 4,
        "pinball_wins": 4,
        "coverage_windows_passed": 4,
    }
    if gate_passed is not None:
        gate_metrics["gate_passed"] = gate_passed
    package = {
        "schema_version": "research-component-v1",
        "task": task,
        "horizon": horizon,
        "features": ("feature_a",),
        "feature_set_version": "features-v1",
        "universe_version": "universe-v1",
        "feature_distribution": {
            "mean": {"feature_a": 0.0},
            "std": {"feature_a": 1.0},
        },
        "gate_metrics": gate_metrics,
        "payload": {
            "models": {
                alpha: _ConstantQuantileModel(value)
                for alpha, value in zip(
                    (0.10, 0.25, 0.50, 0.75, 0.90),
                    model_values,
                    strict=True,
                )
            },
            "cqr_adjustment": 0.01,
        },
    }
    with path.open("wb") as handle:
        pickle.dump(package, handle)
    return path


def test_manual_failed_champion_is_not_verified_research(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    engine = LiveInferenceEngine(paths)
    engine.registry = _registry(paths.models / "registry")
    source = _component_package(
        tmp_path / "manual.pkl",
        model_values=(-0.05, -0.02, 0.01, 0.03, 0.06),
        gate_passed=False,
    )
    with source.open("rb") as handle:
        package = pickle.load(handle)
    package["activation_basis"] = "manual_override"
    source.write_bytes(pickle.dumps(package))
    engine.registry.register_release(
        model_version="manual-component-v1",
        task="return_fwd",
        horizon=20,
        calibration_version="cal-v1",
        source_decision_id="manual-activation-test",
        promotion_eligible=True,
        artifacts={"component-package.pkl": source},
        registered_at=NOW,
    )
    engine.registry.promote(
        "manual-component-v1", reason="explicit approval", recorded_at=NOW
    )
    assert engine.predict_verified_research(_snapshot(), _frame()) == {"US.SPY": ()}


def test_failed_new_candidate_keeps_last_promoted_task_component(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    engine = LiveInferenceEngine(paths)
    engine.registry = _registry(paths.models / "registry")

    promoted_package = _component_package(
        tmp_path / "promoted.pkl",
        model_values=(-0.05, -0.02, 0.01, 0.03, 0.06),
    )
    engine.registry.register_release(
        model_version="return20-promoted",
        task="return_fwd",
        horizon=20,
        calibration_version="return20-cal-v1",
        source_decision_id="eval-promoted",
        promotion_eligible=True,
        artifacts={"component-package.pkl": promoted_package},
        registered_at=NOW,
    )
    engine.registry.promote(
        "return20-promoted",
        reason="task gate passed",
        recorded_at=NOW + timedelta(seconds=1),
    )

    failed_package = _component_package(
        tmp_path / "failed.pkl",
        model_values=(-0.30, -0.20, -0.10, 0.0, 0.10),
    )
    engine.registry.register_release(
        model_version="return20-failed",
        task="return_fwd",
        horizon=20,
        calibration_version="return20-cal-v2",
        source_decision_id="eval-failed",
        promotion_eligible=False,
        artifacts={"component-package.pkl": failed_package},
        registered_at=NOW + timedelta(minutes=1),
    )
    engine.registry.mark_challenger(
        "return20-failed",
        reason="latest candidate failed the task gate",
        recorded_at=NOW + timedelta(minutes=1, seconds=1),
    )

    snapshot = ResolvedSnapshot(
        snapshot_id="snapshot-20260728-r1",
        revision_no=1,
        as_of=NOW,
        dataset_revision="dataset-r1",
        feature_set_version="features-v1",
        universe_version="universe-v1",
        model_version=None,
        calibration_version=None,
        prediction_readiness=PredictionReadiness.DATA_ONLY,
        research_readiness=ResearchReadiness.RULES_ONLY,
    )
    frame = pd.DataFrame(
        [
            {
                "symbol": "US.SPY",
                "session_date": NOW.date(),
                "close": 844.03,
                "raw_close": 740.86,
                "regime": "range",
                "feature_a": 0.2,
            }
        ]
    )

    predictions = engine.predict_verified_research(snapshot, frame)["US.SPY"]

    assert len(predictions) == 1
    assert predictions[0].model_version == "return20-promoted"
    assert predictions[0].evaluation_run_id == "eval-promoted"
    assert predictions[0].reference_price == 740.86
    assert predictions[0].quantiles is not None
    assert predictions[0].quantiles.q50 == 0.01
    assert engine.verified_component_versions() == {
        ("return_fwd", 20): "return20-promoted"
    }


def test_component_from_another_universe_is_not_published_as_verified(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    engine = LiveInferenceEngine(paths)
    engine.registry = _registry(paths.models / "registry")
    package_path = _component_package(
        tmp_path / "legacy.pkl",
        model_values=(-0.05, -0.02, 0.01, 0.03, 0.06),
    )
    with package_path.open("rb") as handle:
        package = pickle.load(handle)
    package["universe_version"] = "legacy-universe"
    with package_path.open("wb") as handle:
        pickle.dump(package, handle)
    engine.registry.register_release(
        model_version="legacy-return20",
        task="return_fwd",
        horizon=20,
        calibration_version="legacy-cal",
        source_decision_id="legacy-eval",
        promotion_eligible=True,
        artifacts={"component-package.pkl": package_path},
        registered_at=NOW,
    )
    engine.registry.promote(
        "legacy-return20", reason="legacy", recorded_at=NOW + timedelta(seconds=1)
    )
    snapshot = ResolvedSnapshot(
        snapshot_id="snapshot-active",
        revision_no=1,
        as_of=NOW,
        dataset_revision="dataset-r1",
        feature_set_version="features-v1",
        universe_version="active-universe",
        prediction_readiness=PredictionReadiness.DATA_ONLY,
        research_readiness=ResearchReadiness.RULES_ONLY,
        model_version=None,
        calibration_version=None,
    )
    frame = pd.DataFrame(
        [{"symbol": "US.SPY", "session_date": NOW.date(), "feature_a": 0.2}]
    )

    assert engine.predict_verified_research(snapshot, frame) == {"US.SPY": ()}


def _snapshot() -> ResolvedSnapshot:
    return ResolvedSnapshot(
        snapshot_id="snapshot-20260728-r1",
        revision_no=1,
        as_of=NOW,
        dataset_revision="dataset-r1",
        feature_set_version="features-v1",
        universe_version="universe-v1",
        model_version=None,
        calibration_version=None,
        prediction_readiness=PredictionReadiness.DATA_ONLY,
        research_readiness=ResearchReadiness.RULES_ONLY,
    )


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "US.SPY",
                "session_date": NOW.date(),
                "close": 844.03,
                "raw_close": 740.86,
                "regime": "range",
                "feature_a": 0.2,
            }
        ]
    )


def test_gate_passed_challenger_without_champion_publishes_return_fwd_for_all_horizons(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    engine = LiveInferenceEngine(paths)
    engine.registry = _registry(paths.models / "registry")
    expected_q50 = {1: 0.003, 5: 0.011, 20: 0.024}
    for horizon, q50 in expected_q50.items():
        package_path = _component_package(
            tmp_path / f"return{horizon}-passed.pkl",
            model_values=(-0.05, -0.02, q50, 0.03, 0.06),
            horizon=horizon,
            gate_passed=True,
        )
        version = f"return{horizon}-challenger"
        engine.registry.register_release(
            model_version=version,
            task="return_fwd",
            horizon=horizon,
            calibration_version=f"return{horizon}-cal",
            source_decision_id=f"eval-{horizon}",
            promotion_eligible=False,
            artifacts={"component-package.pkl": package_path},
            registered_at=NOW,
        )
        engine.registry.mark_challenger(
            version,
            reason="development component passed the task gate",
            recorded_at=NOW + timedelta(seconds=horizon),
        )

    predictions = engine.predict_verified_research(_snapshot(), _frame())["US.SPY"]
    by_horizon = {item.horizon: item for item in predictions}

    assert set(by_horizon) == {1, 5, 20}
    for horizon, q50 in expected_q50.items():
        item = by_horizon[horizon]
        assert item.task == "return_fwd"
        assert item.model_version == f"return{horizon}-challenger"
        assert item.quantiles is not None
        assert item.quantiles.q50 == q50
    assert engine.verified_component_versions() == {}


def test_failed_challenger_without_champion_is_not_published_as_verified(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    engine = LiveInferenceEngine(paths)
    engine.registry = _registry(paths.models / "registry")
    package_path = _component_package(
        tmp_path / "return20-failed.pkl",
        model_values=(-0.30, -0.20, -0.10, 0.0, 0.10),
        gate_passed=False,
    )
    engine.registry.register_release(
        model_version="return20-failed",
        task="return_fwd",
        horizon=20,
        calibration_version="return20-cal-failed",
        source_decision_id="eval-failed",
        promotion_eligible=False,
        artifacts={"component-package.pkl": package_path},
        registered_at=NOW,
    )
    engine.registry.mark_challenger(
        "return20-failed",
        reason="latest candidate failed the task gate",
        recorded_at=NOW + timedelta(seconds=1),
    )

    assert engine.predict_verified_research(_snapshot(), _frame()) == {"US.SPY": ()}
    assert engine.verified_component_versions() == {}


def test_mismatched_champion_falls_back_to_gate_passed_challenger(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    engine = LiveInferenceEngine(paths)
    engine.registry = _registry(paths.models / "registry")
    stale = _component_package(
        tmp_path / "stale.pkl",
        model_values=(-0.30, -0.20, -0.10, 0.0, 0.10),
        gate_passed=True,
    )
    with stale.open("rb") as handle:
        package = pickle.load(handle)
    package["universe_version"] = "legacy-universe"
    with stale.open("wb") as handle:
        pickle.dump(package, handle)
    engine.registry.register_release(
        model_version="return20-stale",
        task="return_fwd",
        horizon=20,
        calibration_version="stale-cal",
        source_decision_id="eval-stale",
        promotion_eligible=True,
        artifacts={"component-package.pkl": stale},
        registered_at=NOW,
    )
    engine.registry.promote(
        "return20-stale",
        reason="legacy champion",
        recorded_at=NOW + timedelta(seconds=1),
    )
    current = _component_package(
        tmp_path / "current.pkl",
        model_values=(-0.05, -0.02, 0.024, 0.03, 0.06),
        gate_passed=True,
    )
    engine.registry.register_release(
        model_version="return20-current",
        task="return_fwd",
        horizon=20,
        calibration_version="current-cal",
        source_decision_id="eval-current",
        promotion_eligible=False,
        artifacts={"component-package.pkl": current},
        registered_at=NOW + timedelta(minutes=1),
    )
    engine.registry.mark_challenger(
        "return20-current",
        reason="development component passed the task gate",
        recorded_at=NOW + timedelta(minutes=1, seconds=1),
    )

    predictions = engine.predict_verified_research(_snapshot(), _frame())["US.SPY"]
    assert len(predictions) == 1
    assert predictions[0].model_version == "return20-current"
    assert predictions[0].quantiles is not None
    assert predictions[0].quantiles.q50 == 0.024
