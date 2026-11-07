from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from stock_prediction.modeling.baselines import (
    BaselineInputError,
    ChannelAtrRuleBaseline,
    HistoricalFrequencyBaseline,
    HistoricalQuantileBaseline,
    LinearQuantileBaseline,
    LogisticBaseline,
)
from stock_prediction.modeling.contracts import TaskName


TRAIN_START = datetime(2018, 1, 2, 21, tzinfo=UTC)
TRAIN_END = datetime(2023, 12, 29, 21, tzinfo=UTC)
DATASET_HASH = "a" * 64


def _evidence() -> dict[str, object]:
    return {
        "task": TaskName.FORECAST_DIRECTION,
        "horizon": 5,
        "training_start": TRAIN_START,
        "training_end": TRAIN_END,
        "dataset_input_hash": DATASET_HASH,
        "seed": 17,
    }


def test_historical_frequency_baseline_is_train_only_and_immutable() -> None:
    fitted = HistoricalFrequencyBaseline.fit(
        ("up", "up", "down", "flat"),
        classes=("down", "flat", "up"),
        **_evidence(),
    )
    before = fitted.model_dump_json()
    probabilities = fitted.predict_proba(({}, {}, {}))

    assert probabilities.shape == (3, 3)
    assert probabilities[0] == pytest.approx((0.25, 0.25, 0.5))
    assert fitted.model_dump_json() == before
    assert probabilities.flags.writeable is False


def test_frequency_baseline_rejects_unknown_labels_and_invalid_classes() -> None:
    with pytest.raises(BaselineInputError, match="unknown"):
        HistoricalFrequencyBaseline.fit(
            ("up", "other"), classes=("down", "up"), **_evidence()
        )
    with pytest.raises(BaselineInputError, match="classes"):
        HistoricalFrequencyBaseline.fit(
            ("up",), classes=("up", "up"), **_evidence()
        )


def test_channel_atr_rule_baseline_has_explicit_three_way_behavior() -> None:
    baseline = ChannelAtrRuleBaseline.create(
        classes=("down", "flat", "up"),
        channel_buffer=0.1,
        **_evidence(),
    )
    probabilities = baseline.predict_proba(
        (
            {"channel_position": -1.2, "atr_breakout_score": -1.5},
            {"channel_position": 0.1, "atr_breakout_score": 0.0},
            {"channel_position": 1.2, "atr_breakout_score": 1.5},
        )
    )

    assert tuple(np.argmax(probabilities, axis=1)) == (0, 1, 2)
    assert np.sum(probabilities, axis=1) == pytest.approx((1.0, 1.0, 1.0))
    with pytest.raises(BaselineInputError, match="channel_position"):
        baseline.predict_proba(({"atr_breakout_score": 0.0},))


def test_logistic_baseline_is_deterministic_and_uses_frozen_schema() -> None:
    features = np.array(
        [
            [-2.0, -1.0],
            [-1.0, -0.5],
            [1.0, 0.5],
            [2.0, 1.0],
        ],
        dtype=np.float64,
    )
    labels = ("down", "down", "up", "up")
    fitted = LogisticBaseline.fit(
        features,
        labels,
        feature_names=("momentum", "relative_strength"),
        classes=("down", "up"),
        c=1.0,
        max_iter=500,
        **_evidence(),
    )
    repeated = LogisticBaseline.fit(
        features,
        labels,
        feature_names=("momentum", "relative_strength"),
        classes=("down", "up"),
        c=1.0,
        max_iter=500,
        **_evidence(),
    )

    probabilities = fitted.predict_proba(
        features, feature_names=("momentum", "relative_strength")
    )
    assert probabilities[:, 1] == pytest.approx(
        repeated.predict_proba(
            features, feature_names=("momentum", "relative_strength")
        )[:, 1]
    )
    assert probabilities[0, 0] > probabilities[0, 1]
    assert probabilities[-1, 1] > probabilities[-1, 0]
    assert fitted.model_dump_json() == repeated.model_dump_json()
    with pytest.raises(BaselineInputError, match="feature schema"):
        fitted.predict_proba(
            np.ones((2, 3), dtype=np.float64),
            feature_names=("momentum", "relative_strength", "extra"),
        )
    with pytest.raises(BaselineInputError, match="feature order"):
        fitted.predict_proba(
            features,
            feature_names=("relative_strength", "momentum"),
        )


def test_historical_quantile_baseline_fits_only_training_outcomes() -> None:
    fitted = HistoricalQuantileBaseline.fit(
        (0.0, 1.0, 2.0, 3.0),
        quantiles=(0.1, 0.5, 0.9),
        **_evidence(),
    )
    before = fitted.model_dump_json()
    predictions = fitted.predict(2)

    assert predictions[0] == pytest.approx((0.3, 1.5, 2.7))
    assert predictions[1] == pytest.approx(predictions[0])
    assert fitted.model_dump_json() == before
    assert predictions.flags.writeable is False


def test_linear_quantile_baseline_binds_features_and_returns_finite_matrix() -> None:
    features = np.array(
        [[-2.0], [-1.0], [0.0], [1.0], [2.0], [3.0]], dtype=np.float64
    )
    outcomes = np.array([-2.1, -1.0, 0.1, 1.1, 1.9, 3.2], dtype=np.float64)
    fitted = LinearQuantileBaseline.fit(
        features,
        outcomes,
        feature_names=("momentum",),
        quantiles=(0.1, 0.5, 0.9),
        alpha=0.01,
        **_evidence(),
    )
    before = fitted.model_dump_json()
    predictions = fitted.predict(
        np.array([[-0.5], [2.5]], dtype=np.float64),
        feature_names=("momentum",),
    )

    assert predictions.shape == (2, 3)
    assert np.all(np.isfinite(predictions))
    assert fitted.feature_names == ("momentum",)
    assert fitted.model_dump_json() == before
    with pytest.raises(BaselineInputError, match="feature schema"):
        fitted.predict(
            np.ones((2, 2), dtype=np.float64),
            feature_names=("momentum", "extra"),
        )


def test_prediction_revalidates_model_copy_bypass() -> None:
    frequency = HistoricalFrequencyBaseline.fit(
        ("down", "up"), classes=("down", "up"), **_evidence()
    ).model_copy(update={"class_probabilities": (2.0, -1.0)})
    with pytest.raises(BaselineInputError, match="artifact"):
        frequency.predict_proba(({},))

    quantile = HistoricalQuantileBaseline.fit(
        (0.0, 1.0), quantiles=(0.5,), **_evidence()
    ).model_copy(update={"fitted_quantiles": (float("nan"),)})
    with pytest.raises(BaselineInputError, match="artifact"):
        quantile.predict(1)


@pytest.mark.parametrize(
    "quantiles",
    [(0.5, 0.1), (0.5, 0.5), (0.0, 0.5)],
)
def test_quantile_baselines_reject_invalid_quantile_orders(
    quantiles: tuple[float, ...],
) -> None:
    with pytest.raises(BaselineInputError, match="quantile"):
        HistoricalQuantileBaseline.fit(
            (0.0, 1.0), quantiles=quantiles, **_evidence()
        )


def test_all_fitters_reject_non_finite_training_data() -> None:
    with pytest.raises(BaselineInputError, match="finite"):
        LogisticBaseline.fit(
            np.array([[0.0], [np.nan]]),
            ("down", "up"),
            feature_names=("x",),
            classes=("down", "up"),
            c=1.0,
            max_iter=100,
            **_evidence(),
        )
    with pytest.raises(BaselineInputError, match="finite"):
        HistoricalQuantileBaseline.fit(
            (0.0, float("inf")), quantiles=(0.5,), **_evidence()
        )
