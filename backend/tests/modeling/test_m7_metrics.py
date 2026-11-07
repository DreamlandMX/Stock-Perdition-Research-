from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from stock_prediction.modeling.m7_metrics import (
    M7MetricInputError,
    adaptive_classwise_ece,
    compare_direction_brier,
    compute_m7_training_weights,
    date_block_bootstrap_delta_brier,
    direction_brier_report,
    range_exit_calibration_report,
)


CLASSES = ("down", "flat", "up")


def test_training_weights_follow_exact_symbol_recency_and_class_formula() -> None:
    sessions = (date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6))
    report = compute_m7_training_weights(
        symbols=("US.A", "US.A", "US.B"),
        sessions=sessions,
        labels=("down", "down", "up"),
        training_cutoff=sessions[-1],
    )

    assert report.symbol_weights == pytest.approx((0.75, 0.75, 1.5))
    assert report.recency_weights == pytest.approx(
        (2 ** (-2 / 756), 2 ** (-1 / 756), 1.0)
    )
    assert report.class_weights == pytest.approx((0.75, 0.75, 1.5))
    assert np.mean(report.combined_weights) == pytest.approx(1.0)
    assert all(value > 0 for value in report.combined_weights)


def test_class_weights_are_fit_locally_and_clipped() -> None:
    labels = ("down",) * 11 + ("up",)
    sessions = tuple(date(2026, 1, 2) + timedelta(days=index) for index in range(12))

    report = compute_m7_training_weights(
        symbols=("US.A",) * 12,
        sessions=sessions,
        labels=labels,
        training_cutoff=sessions[-1],
    )

    assert report.class_weights[-1] == pytest.approx(5.0)
    assert report.class_weights[0] == pytest.approx(12 / 22)


def test_symbol_equal_brier_differs_from_row_equal_on_imbalanced_symbols() -> None:
    probabilities = np.array(
        [
            [1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=float,
    )
    report = direction_brier_report(
        symbols=("US.A", "US.A", "US.B"),
        labels=("down", "up", "up"),
        probabilities=probabilities,
        classes=CLASSES,
    )

    assert report.row_equal_brier == pytest.approx(2 / 3)
    assert report.per_symbol_brier["US.A"] == pytest.approx(1.0)
    assert report.per_symbol_brier["US.B"] == pytest.approx(0.0)
    assert report.symbol_equal_brier == pytest.approx(0.5)


def test_candidate_and_incumbent_use_identical_sample_keys() -> None:
    keys = (
        ("US.A", date(2026, 1, 2), 5),
        ("US.B", date(2026, 1, 2), 5),
    )
    labels = ("down", "up")
    candidate = np.array([[0.8, 0.1, 0.1], [0.1, 0.1, 0.8]])
    incumbent = np.array([[0.7, 0.2, 0.1], [0.2, 0.2, 0.6]])

    report = compare_direction_brier(
        candidate_sample_keys=keys,
        incumbent_sample_keys=keys,
        labels=labels,
        candidate_probabilities=candidate,
        incumbent_probabilities=incumbent,
        classes=CLASSES,
    )

    assert report.candidate.symbol_equal_brier < report.incumbent.symbol_equal_brier
    assert report.relative_improvement > 0
    with pytest.raises(M7MetricInputError, match="sample_keys_mismatch"):
        compare_direction_brier(
            candidate_sample_keys=keys,
            incumbent_sample_keys=tuple(reversed(keys)),
            labels=labels,
            candidate_probabilities=candidate,
            incumbent_probabilities=incumbent,
            classes=CLASSES,
        )


def test_adaptive_classwise_ece_reports_ten_equal_mass_bins_and_decomposition() -> None:
    symbols = tuple(f"US.{index}" for index in range(30))
    labels = tuple(CLASSES[index % 3] for index in range(30))
    probabilities = np.full((30, 3), 0.1, dtype=float)
    for index, label in enumerate(labels):
        probabilities[index, CLASSES.index(label)] = 0.8

    report = adaptive_classwise_ece(
        symbols=symbols,
        labels=labels,
        probabilities=probabilities,
        classes=CLASSES,
        n_bins=10,
    )

    assert report.macro_ece == pytest.approx(0.12)
    assert set(report.per_class_ece) == set(CLASSES)
    assert all(len(report.reliability_bins[name]) == 10 for name in CLASSES)
    assert all(
        sum(item.count for item in report.reliability_bins[name]) == 30
        for name in CLASSES
    )
    assert all(name in report.brier_decomposition for name in CLASSES)


def test_range_exit_calibration_keeps_empty_and_underpopulated_bins() -> None:
    report = range_exit_calibration_report(
        symbols=("US.A", "US.A", "US.B", "US.B"),
        observed=(0, 0, 1, 1),
        cumulative_probability=(0.1, 0.2, 0.8, 0.9),
        n_bins=10,
        minimum_bin_count=2,
    )

    assert len(report.bins) == 10
    assert sum(item.count for item in report.bins) == 4
    assert any(item.status == "empty" for item in report.bins)
    assert any(item.status == "underpopulated" for item in report.bins)
    assert report.calibration_error == pytest.approx(0.15)


def test_five_session_date_block_bootstrap_is_seeded_and_one_sided() -> None:
    sessions = tuple(date(2026, 1, 2) + timedelta(days=index) for index in range(12))
    sample_keys = tuple(
        (symbol, session, 5) for session in sessions for symbol in ("US.A", "US.B")
    )
    labels = tuple("up" for _ in sample_keys)
    candidate = np.tile(np.array([[0.1, 0.1, 0.8]]), (len(sample_keys), 1))
    incumbent = np.tile(np.array([[0.2, 0.2, 0.6]]), (len(sample_keys), 1))

    first = date_block_bootstrap_delta_brier(
        sample_keys=sample_keys,
        labels=labels,
        candidate_probabilities=candidate,
        incumbent_probabilities=incumbent,
        classes=CLASSES,
        n_draws=10_000,
        block_length=5,
        seed=17,
    )
    second = date_block_bootstrap_delta_brier(
        sample_keys=sample_keys,
        labels=labels,
        candidate_probabilities=candidate,
        incumbent_probabilities=incumbent,
        classes=CLASSES,
        n_draws=10_000,
        block_length=5,
        seed=17,
    )

    assert first == second
    assert first.point_estimate == pytest.approx(-0.18)
    assert first.one_sided_upper_90 == pytest.approx(-0.18)
    assert first.one_sided_upper_90 <= 0
    assert first.block_length == 5
    assert first.n_draws == 10_000


@pytest.mark.parametrize(
    "probabilities",
    [
        np.array([[0.2, 0.2, 0.2]]),
        np.array([[np.nan, 0.0, 1.0]]),
        np.array([[-0.1, 0.1, 1.0]]),
    ],
)
def test_direction_metrics_reject_invalid_probabilities(
    probabilities: np.ndarray,
) -> None:
    with pytest.raises(M7MetricInputError, match="probabilities"):
        direction_brier_report(
            symbols=("US.A",),
            labels=("up",),
            probabilities=probabilities,
            classes=CLASSES,
        )
