from datetime import date

import pytest

from stock_prediction.live.publication import _task_evaluation_summary


def test_summary_preserves_worst_fold_and_uses_sample_weights() -> None:
    folds = [
        {
            "fold_id": "a",
            "sample_count": 10,
            "test_start": "2024-01-02",
            "test_end": "2024-06-28",
            "cqr_coverage": 0.71,
            "cqr_average_width": 0.1,
            "candidate_pinball": 0.01,
            "baseline_pinball": 0.02,
        },
        {
            "fold_id": "b",
            "sample_count": 30,
            "test_start": "2024-07-01",
            "test_end": "2024-12-31",
            "cqr_coverage": 0.83,
            "cqr_average_width": 0.2,
            "candidate_pinball": 0.03,
            "baseline_pinball": 0.04,
        },
    ]
    result = _task_evaluation_summary({"fold_count": 2, "folds": folds})
    assert result["evaluation_sample_count"] == 40
    assert result["evaluation_start"] == date(2024, 1, 2)
    assert result["evaluation_end"] == date(2024, 12, 31)
    assert result["worst_coverage"] == 0.71
    assert result["average_interval_width"] == pytest.approx(0.175)
    assert result["average_pinball_loss"] == pytest.approx(0.025)
    assert result["baseline_pinball_loss"] == pytest.approx(0.035)
    assert _task_evaluation_summary({"fold_count": 3, "folds": folds}) == {}
    assert _task_evaluation_summary({"fold_count": 2, "folds": [folds[0]] * 2}) == {}


def test_summary_never_fills_missing_dates_or_invalid_metrics_from_other_folds() -> None:
    result = _task_evaluation_summary(
        {
            "fold_count": 1,
            "folds": [
                {
                    "fold_id": "a",
                    "sample_count": 10,
                    "cqr_coverage": float("nan"),
                    "cqr_average_width": -0.1,
                    "candidate_pinball": 0.01,
                }
            ],
        }
    )
    assert result == {"evaluation_sample_count": 10, "average_pinball_loss": 0.01}
    assert _task_evaluation_summary({"fold_count": 4}) == {}
