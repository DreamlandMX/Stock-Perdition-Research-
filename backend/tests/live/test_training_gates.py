from __future__ import annotations

from datetime import UTC, date, datetime
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import pytest

from stock_prediction.live.config import LivePaths
from stock_prediction.live.training import (
    _class_share_summary,
    _cqr_adjustment_vector,
    _execution_cost_metrics,
    _execution_gate_summary,
    _freeze_development_labels,
    _gate_failure_codes,
    _group_cqr_adjustments,
    _registered_candidate_matrix,
    _survival_share_summary,
    attach_m7_training_report_sections,
    candidate_training_report_fingerprint,
    load_latest_training_report,
    write_latest_training_pointer,
)
from stock_prediction.modeling.report import M7_REPORT_SECTION_NAMES
from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_UNIVERSE204_V1,
    CANDIDATE_RECIPE_V1,
    CANDIDATE_RECIPE_V2,
)


def _write_training_report(
    paths: LivePaths,
    *,
    run_id: str,
    generated_at: datetime,
) -> Path:
    report_path = paths.models / "runs" / run_id / "training-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "generated_at": generated_at.isoformat(),
                "universe_version": "m1-seed-2026-07-11-v1",
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    return report_path


def test_latest_training_report_pointer_is_portable_and_hash_verified(
    tmp_path: Path,
) -> None:
    original_paths = LivePaths.from_workspace(tmp_path / "original")
    original_paths.ensure()
    run_id = "run-seed100-v4"
    report_path = _write_training_report(
        original_paths,
        run_id=run_id,
        generated_at=datetime(2026, 8, 24, 4, tzinfo=UTC),
    )
    write_latest_training_pointer(original_paths, report_path, run_id)
    copied_paths = LivePaths.from_workspace(tmp_path / "copied")
    shutil.copytree(original_paths.models, copied_paths.models)

    loaded = load_latest_training_report(copied_paths)

    assert loaded["run_id"] == run_id
    pointer_text = (copied_paths.models / "LATEST-TRAINING.json").read_text(
        encoding="utf-8"
    )
    assert str((tmp_path / "original").resolve()) not in pointer_text
    copied_report = copied_paths.models / "runs" / run_id / "training-report.json"
    copied_report.write_text('{"run_id":"tampered"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="training_report_hash_mismatch"):
        load_latest_training_report(copied_paths)


def test_latest_training_report_never_falls_back_to_older_valid_report(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    _write_training_report(
        paths,
        run_id="run-older-valid",
        generated_at=datetime(2026, 8, 23, 4, tzinfo=UTC),
    )
    latest_path = _write_training_report(
        paths,
        run_id="run-latest-corrupt",
        generated_at=datetime(2026, 8, 24, 4, tzinfo=UTC),
    )
    write_latest_training_pointer(paths, latest_path, "run-latest-corrupt")
    latest_path.write_text("corrupt", encoding="utf-8")

    with pytest.raises(RuntimeError, match="training_report_hash_mismatch"):
        load_latest_training_report(paths)


def test_m7_report_sections_are_attached_without_mutating_legacy_report() -> None:
    report = {"run_id": "run-m7", "tasks": []}
    completed = {
        name: {"evidence": name}
        for name in M7_REPORT_SECTION_NAMES
        if name != "window_consumption"
    }

    attached = attach_m7_training_report_sections(
        report,
        completed_sections=completed,
        failed_sections={},
        not_run_sections={"window_consumption": "blind_window_not_complete"},
    )

    assert "m7_sections" not in report
    assert attached["tasks"] == []
    assert tuple(attached["m7_sections"]) == M7_REPORT_SECTION_NAMES
    assert attached["m7_sections"]["window_consumption"] == {
        "status": "not_run",
        "reason": "blind_window_not_complete",
        "payload": None,
    }


def test_classification_label_health_requires_every_class_at_two_percent() -> None:
    passing = pd.Series([-1] * 2 + [0] * 96 + [1] * 2)
    failing = pd.Series([-1] + [0] * 97 + [1] * 2)

    assert _class_share_summary(passing, "boundary")["passed"] is True
    assert _class_share_summary(failing, "below-boundary")["passed"] is False


def test_range_exit_requires_event_and_censored_share_at_five_percent() -> None:
    passing = pd.Series([1.0] * 5 + [0.0] * 95)
    failing = pd.Series([1.0] * 4 + [0.0] * 96)

    assert _survival_share_summary(passing, "boundary")["passed"] is True
    assert _survival_share_summary(failing, "below-boundary")["passed"] is False


def test_prediction_gate_failure_codes_do_not_include_execution_economics() -> None:
    failures = _gate_failure_codes(
        statistical_gate=False,
        calibration_gate=True,
        stability_gate=False,
        promotion_gate=False,
        label_health={"passed": False},
    )

    assert failures == [
        "label_degenerate",
        "statistical_gate_failed",
        "stability_gate_failed",
        "promotion_window_failed",
    ]
    assert all("cost" not in failure for failure in failures)


def test_development_gate_does_not_report_an_unevaluated_promotion_failure() -> None:
    failures = _gate_failure_codes(
        statistical_gate=True,
        calibration_gate=True,
        stability_gate=True,
        promotion_gate=None,
    )

    assert failures == []


def test_registered_budget_freezes_all_17_tasks_and_parallelism() -> None:
    matrix = _registered_candidate_matrix()

    assert matrix["random_seed"] == 17
    assert matrix["maximum_parallel_trials"] == 4
    assert matrix["threads_per_trial"] == 4
    assert len(matrix["tasks"]) == 17
    assert {row["candidate_count"] for row in matrix["tasks"]} == {12, 16}
    direction = next(
        row for row in matrix["tasks"] if row["task"] == "forecast_direction"
    )
    assert direction["families"] == {
        "class_balanced_lgbm": 4,
        "market_state_fusion": 2,
        "elastic_net_multiclass": 4,
        "threshold_decomposition": 6,
    }


def test_second_recipe_expands_only_direction_search_and_is_versioned() -> None:
    first = _registered_candidate_matrix(CANDIDATE_RECIPE_V1)
    second = _registered_candidate_matrix(CANDIDATE_RECIPE_V2)

    assert first["candidate_generation"] == 1
    assert second["candidate_generation"] == 2
    assert first != second
    directions = [row for row in second["tasks"] if row["task"] == "forecast_direction"]
    assert [row["candidate_count"] for row in directions] == [24, 24, 24]
    assert directions[0]["families"]["linear_nonlinear_probability_blend"] == 6
    unchanged = [row for row in second["tasks"] if row["task"] != "forecast_direction"]
    assert {row["candidate_count"] for row in unchanged} == {12, 16}


def test_universe_204_recipe_is_a_new_generation_with_the_expanded_search() -> None:
    matrix = _registered_candidate_matrix(CANDIDATE_RECIPE_UNIVERSE204_V1)

    assert matrix["candidate_recipe_version"] == "candidate-recipe-universe204-v1"
    assert matrix["candidate_generation"] == 3
    directions = [row for row in matrix["tasks"] if row["task"] == "forecast_direction"]
    assert [row["candidate_count"] for row in directions] == [24, 24, 24]


def test_candidate_fingerprint_is_scoped_by_universe_version() -> None:
    report = {
        "development_data_hash": "development-hash",
        "candidate_recipe_version": CANDIDATE_RECIPE_UNIVERSE204_V1,
        "candidate_matrix": _registered_candidate_matrix(
            CANDIDATE_RECIPE_UNIVERSE204_V1
        ),
        "universe_version": "universe-204-v1",
    }

    first = candidate_training_report_fingerprint(report)
    second = candidate_training_report_fingerprint(
        {**report, "universe_version": "universe-204-v2"}
    )

    assert first is not None
    assert second is not None
    assert first != second


def test_cqr_uses_stock_and_etf_calibration_with_pooled_fallback() -> None:
    calibration = pd.DataFrame({"is_etf": [0.0] * 60 + [1.0] * 60})
    nonconformity = np.asarray([0.01] * 60 + [0.04] * 60)

    adjustments = _group_cqr_adjustments(calibration, nonconformity)
    applied = _cqr_adjustment_vector(
        pd.DataFrame({"is_etf": [0.0, 1.0]}),
        adjustments,
    )

    assert adjustments["stock"] == 0.01
    assert adjustments["etf"] == 0.04
    assert applied.tolist() == [0.01, 0.04]


def test_v2_cqr_uses_asset_and_lagged_volatility_with_ordered_fallback() -> None:
    calibration = pd.DataFrame(
        {
            "is_etf": [0.0] * 120 + [1.0] * 120,
            "volatility_20": np.tile(
                np.concatenate((np.full(60, 0.10), np.full(60, 0.30))),
                2,
            ),
        }
    )
    nonconformity = np.concatenate(
        (
            np.full(60, 0.01),
            np.full(60, 0.02),
            np.full(60, 0.03),
            np.full(60, 0.04),
        )
    )

    adjustments = _group_cqr_adjustments(
        calibration,
        nonconformity,
        include_volatility_state=True,
    )
    applied = _cqr_adjustment_vector(
        pd.DataFrame(
            {
                "is_etf": [0.0, 0.0, 1.0, 1.0],
                "volatility_20": [0.10, 0.30, 0.10, 0.30],
            }
        ),
        adjustments,
    )

    assert applied.tolist() == [0.01, 0.02, 0.03, 0.04]


def test_development_labels_never_use_future_observations_after_cutoff() -> None:
    sessions = pd.bdate_range("2026-05-28", "2026-06-29").date
    frame = pd.DataFrame(
        {
            "session_date": sessions,
            "direction_1": 1.0,
            "direction_5": 1.0,
            "direction_20": 1.0,
            "return_fwd_1": 0.01,
            "return_fwd_5": 0.02,
            "return_fwd_20": 0.03,
        }
    )

    frozen = _freeze_development_labels(frame)

    assert frozen["direction_1"].isna().sum() == 1
    assert frozen["direction_5"].isna().sum() == 5
    assert frozen["direction_20"].isna().sum() == 20
    assert frozen["return_fwd_20"].isna().sum() == 20
    assert frozen.loc[frozen["direction_20"].notna(), "session_date"].max() < date(
        2026, 6, 30
    )


def test_execution_gate_checks_all_costs_concentration_and_capacity() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["US.A", "US.B", "US.C", "US.D"],
            "turnover": [10_000_000.0] * 4,
            "return_fwd_5": [0.003] * 4,
        }
    )
    probabilities = np.asarray([[0.02, 0.08, 0.90]] * 4)
    metrics = _execution_cost_metrics(
        frame,
        probabilities,
        np.asarray([-1, 0, 1]),
        5,
    )
    row = {
        "execution_cost_sensitivity": metrics,
        "task": "forecast_direction",
        "horizon": 5,
    }
    tasks = [
        {
            **row,
            "folds": [row, row, row, row],
            "promotion_window": {"execution_cost_sensitivity": metrics},
        }
    ]

    summary = _execution_gate_summary(tasks)

    assert metrics["net_returns"]["5"] > metrics["net_returns"]["20"] > 0
    assert metrics["maximum_symbol_concentration"] == 0.25
    assert metrics["capacity_notional_proxy"] > 0
    assert summary["development_windows_passed"] == 4
    assert summary["promotion_window_passed"] is True
    assert summary["ready"] is True
