from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pytest

from stock_prediction.modeling.metrics import (
    MetricInputError,
    calibration_report,
    date_block_bootstrap_ic,
    empirical_interval_diagnostics,
    fit_class_frequency_reference,
    hash_sample_keys,
    hash_training_labels,
    mean_pinball_loss,
    median_absolute_error,
    multiclass_brier_score,
    multiclass_brier_skill,
    multiclass_log_loss,
    one_vs_rest_pr_auc,
    quantile_crossing_rate,
    spearman_ic_by_date,
)
from stock_prediction.modeling.contracts import (
    EvaluationRole,
    EvaluationScope,
    EvaluationWindow,
    TaskName,
)


CLASSES = ("down", "up")
Y_TRUE = ("up", "down")
PROBABILITIES = np.array([[0.25, 0.75], [0.60, 0.40]], dtype=np.float64)
TRAIN_KEYS = (
    ("US.AAPL", datetime(2023, 12, 20, 21, tzinfo=UTC), 5),
    ("US.MSFT", datetime(2023, 12, 21, 21, tzinfo=UTC), 5),
    ("US.NVDA", datetime(2023, 12, 22, 21, tzinfo=UTC), 5),
    ("US.AMZN", datetime(2023, 12, 26, 21, tzinfo=UTC), 5),
)


def _training_window(role: EvaluationRole = EvaluationRole.TRAIN) -> EvaluationWindow:
    return EvaluationWindow(
        window_id="split-v1.train",
        role=role,
        scope=EvaluationScope.SYNTHETIC_OFFLINE,
        start_session=date(2018, 1, 2),
        end_session=date(2023, 12, 29),
        split_version="split-v1",
        dataset_input_hash="a" * 64,
        calendar_hash="b" * 64,
        consumed_at=None,
        consumption_reason=None,
    )


def test_brier_score_and_skill_use_train_frequency_reference() -> None:
    reference = fit_class_frequency_reference(
        ("down", "down", "down", "up"),
        sample_keys=TRAIN_KEYS,
        classes=CLASSES,
        task=TaskName.FORECAST_DIRECTION,
        horizon=5,
        training_window=_training_window(),
    )
    score = multiclass_brier_score(Y_TRUE, PROBABILITIES, classes=CLASSES)
    skill = multiclass_brier_skill(
        Y_TRUE,
        PROBABILITIES,
        classes=CLASSES,
        reference=reference,
        task=TaskName.FORECAST_DIRECTION,
        horizon=5,
        dataset_input_hash="a" * 64,
        training_window_id="split-v1.train",
        training_sample_keys_hash=hash_sample_keys(TRAIN_KEYS),
        training_labels_hash=hash_training_labels(
            TRAIN_KEYS, ("down", "down", "down", "up")
        ),
    )

    assert score == pytest.approx(0.2225)
    assert skill == pytest.approx(0.644)


def test_brier_reference_rejects_evaluation_window_frequencies() -> None:
    with pytest.raises(MetricInputError, match="TRAIN window"):
        fit_class_frequency_reference(
            Y_TRUE,
            sample_keys=TRAIN_KEYS[:2],
            classes=CLASSES,
            task=TaskName.FORECAST_DIRECTION,
            horizon=5,
            training_window=_training_window(EvaluationRole.OUTER_TEST),
        )


def test_brier_reference_revalidates_tampered_frequency_counts() -> None:
    reference = fit_class_frequency_reference(
        ("down", "down", "down", "up"),
        sample_keys=TRAIN_KEYS,
        classes=CLASSES,
        task=TaskName.FORECAST_DIRECTION,
        horizon=5,
        training_window=_training_window(),
    ).model_copy(update={"class_counts": (1, 3)})

    with pytest.raises(MetricInputError, match="reference artifact"):
        multiclass_brier_skill(
            Y_TRUE,
            PROBABILITIES,
            classes=CLASSES,
            reference=reference,
            task=TaskName.FORECAST_DIRECTION,
            horizon=5,
            dataset_input_hash="a" * 64,
            training_window_id="split-v1.train",
            training_sample_keys_hash=hash_sample_keys(TRAIN_KEYS),
            training_labels_hash=hash_training_labels(
                TRAIN_KEYS, ("down", "down", "down", "up")
            ),
        )


def test_brier_reference_rejects_labels_keyed_outside_training_window() -> None:
    outer_keys = (
        ("US.AAPL", datetime(2024, 1, 2, 21, tzinfo=UTC), 5),
        ("US.MSFT", datetime(2024, 1, 3, 21, tzinfo=UTC), 5),
    )
    with pytest.raises(MetricInputError, match="outside TRAIN window"):
        fit_class_frequency_reference(
            Y_TRUE,
            sample_keys=outer_keys,
            classes=CLASSES,
            task=TaskName.FORECAST_DIRECTION,
            horizon=5,
            training_window=_training_window(),
        )


def test_log_loss_and_pr_auc_match_primary_library_definitions() -> None:
    assert multiclass_log_loss(
        Y_TRUE, PROBABILITIES, classes=CLASSES
    ) == pytest.approx((-np.log(0.75) - np.log(0.60)) / 2)
    report = one_vs_rest_pr_auc(Y_TRUE, PROBABILITIES, classes=CLASSES)

    assert report.per_class["down"] == pytest.approx(1.0)
    assert report.per_class["up"] == pytest.approx(1.0)
    assert report.macro_average == pytest.approx(1.0)


def test_calibration_report_keeps_count_bearing_bins() -> None:
    report = calibration_report(
        ("up", "up", "down", "down"),
        np.array(
            [[0.1, 0.9], [0.4, 0.6], [0.7, 0.3], [0.55, 0.45]],
            dtype=np.float64,
        ),
        classes=CLASSES,
        n_bins=5,
    )

    assert sum(item.count for item in report.bins) == 4
    assert report.ece == pytest.approx(0.3125)
    assert report.mce == pytest.approx(0.45)
    assert any(item.count == 0 for item in report.bins)


@pytest.mark.parametrize(
    "probabilities",
    [
        np.array([[0.2, 0.2]], dtype=np.float64),
        np.array([[np.nan, np.nan]], dtype=np.float64),
        np.array([[-0.1, 1.1]], dtype=np.float64),
    ],
)
def test_classification_metrics_reject_invalid_probability_simplex(
    probabilities: np.ndarray,
) -> None:
    with pytest.raises(MetricInputError, match="probabilit"):
        multiclass_brier_score(("up",), probabilities, classes=CLASSES)


def test_classification_metrics_reject_unknown_or_degenerate_classes() -> None:
    with pytest.raises(MetricInputError, match="unknown class"):
        multiclass_brier_score(("flat",), np.array([[0.5, 0.5]]), classes=CLASSES)
    with pytest.raises(MetricInputError, match="positive and negative"):
        one_vs_rest_pr_auc(
            ("up", "up"),
            np.array([[0.4, 0.6], [0.3, 0.7]]),
            classes=CLASSES,
        )


def test_pinball_interval_crossing_and_median_metrics_are_hand_calculated() -> None:
    assert mean_pinball_loss(
        (0.0, 2.0), (1.0, 1.0), quantile=0.5
    ) == pytest.approx(0.5)
    interval = empirical_interval_diagnostics(
        (0.0, 1.0, 2.0),
        lower=(-1.0, 0.0, 1.0),
        upper=(1.0, 1.0, 1.5),
        target_coverage=0.8,
    )
    assert interval.empirical_coverage == pytest.approx(2 / 3)
    assert interval.average_width == pytest.approx(3.5 / 3)
    assert interval.coverage_error == pytest.approx((2 / 3) - 0.8)
    assert quantile_crossing_rate(
        np.array([[0.0, 1.0, 2.0], [0.0, 2.0, 1.0]]),
        quantiles=(0.1, 0.5, 0.9),
    ) == pytest.approx(0.5)
    assert median_absolute_error((0.0, 2.0), (1.0, 1.0)) == pytest.approx(1.0)


def test_distribution_metrics_reject_crossed_bounds_and_non_finite_values() -> None:
    with pytest.raises(MetricInputError, match="lower"):
        empirical_interval_diagnostics(
            (1.0,), lower=(2.0,), upper=(1.0,), target_coverage=0.8
        )
    with pytest.raises(MetricInputError, match="finite"):
        mean_pinball_loss((1.0,), (float("nan"),), quantile=0.5)


def test_spearman_ic_is_grouped_by_date_before_aggregation() -> None:
    dates = (
        date(2026, 1, 2),
        date(2026, 1, 2),
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 5),
        date(2026, 1, 5),
    )
    outcomes = (1.0, 2.0, 3.0, 1.0, 2.0, 3.0)
    scores = (1.0, 2.0, 3.0, 3.0, 2.0, 1.0)
    daily = spearman_ic_by_date(dates, outcomes, scores)
    interval = date_block_bootstrap_ic(
        daily,
        n_bootstrap=200,
        confidence=0.95,
        seed=17,
    )

    assert tuple(item.ic for item in daily) == pytest.approx((1.0, -1.0))
    assert interval.point_estimate == pytest.approx(0.0)
    assert -1.0 <= interval.lower <= interval.upper <= 1.0
    assert interval.seed == 17
    assert interval.n_dates == 2


def test_spearman_ic_rejects_empty_or_constant_cross_sections() -> None:
    with pytest.raises(MetricInputError, match="cross-section"):
        spearman_ic_by_date((date(2026, 1, 2),), (1.0,), (1.0,))
    with pytest.raises(MetricInputError, match="constant"):
        spearman_ic_by_date(
            (date(2026, 1, 2), date(2026, 1, 2)),
            (1.0, 1.0),
            (0.2, 0.3),
        )


def test_date_block_bootstrap_rejects_duplicate_sessions() -> None:
    duplicated = spearman_ic_by_date(
        (date(2026, 1, 2), date(2026, 1, 2)),
        (1.0, 2.0),
        (1.0, 2.0),
    )[0]
    with pytest.raises(MetricInputError, match="duplicate session"):
        date_block_bootstrap_ic(
            (duplicated, duplicated),
            n_bootstrap=10,
            confidence=0.95,
            seed=17,
        )
