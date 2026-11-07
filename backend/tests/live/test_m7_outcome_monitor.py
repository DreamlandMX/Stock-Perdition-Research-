from __future__ import annotations

from datetime import UTC, date, datetime
import json
from pathlib import Path

import pandas as pd

from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_orchestration import M7OrchestrationStore
from stock_prediction.live.m7_outcome_monitor import (
    M7OutcomeComponentReport,
    M7WeeklyOutcomeMonitor,
    _evaluate_direction_outcomes,
    _evaluate_range_outcomes,
)
from stock_prediction.live.m7_promotion import M7ForecastBundleComponent
from stock_prediction.universe.seed import SEED_UNIVERSE


def _report(
    *, relative_degradation: float, calibration_error: float
) -> M7OutcomeComponentReport:
    return M7OutcomeComponentReport.build(
        bundle_id="direction",
        package_sha256="a" * 64,
        reference_brier=0.20,
        current_brier=0.20 * (1.0 + relative_degradation),
        calibration_error=calibration_error,
        sample_count=1500,
        mature_sample_keys_hash="b" * 64,
    )


def test_weekly_monitor_records_exact_mature_evidence_idempotently(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    report = _report(relative_degradation=0.06, calibration_error=0.04)
    monitor = M7WeeklyOutcomeMonitor(
        paths,
        evaluation_provider=lambda _week_ending: (report,),
    )

    first = monitor.run(
        week_ending=date(2026, 8, 21),
        generated_at=datetime(2026, 8, 21, 22, tzinfo=UTC),
    )
    second = monitor.run(
        week_ending=date(2026, 8, 21),
        generated_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
    )

    assert first == second
    assert first is not None
    assert first.labels_mature is True
    assert first.breach is True
    assert first.relative_brier_degradation == 0.06
    assert (
        M7OrchestrationStore(paths).drift_decision().consecutive_mature_weekly_breaches
        == 1
    )
    detail = json.loads(first.detail_path.read_text(encoding="utf-8"))
    assert detail["components"][0]["mature_sample_keys_hash"] == "b" * 64
    assert detail["components"][0]["reference_package_sha256"] == "a" * 64


def test_weekly_monitor_does_not_count_immature_labels_as_breach(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    monitor = M7WeeklyOutcomeMonitor(
        paths,
        evaluation_provider=lambda _week_ending: (),
    )

    result = monitor.run(
        week_ending=date(2026, 8, 21),
        generated_at=datetime(2026, 8, 21, 22, tzinfo=UTC),
    )

    assert result is not None
    assert result.labels_mature is False
    assert result.breach is False


def test_outcome_evaluator_uses_exact_mature_sample_keys_and_symbol_equal_metrics() -> (
    None
):
    direction_component = M7ForecastBundleComponent(
        bundle_id="direction",
        package_id="direction",
        package_sha256="a" * 64,
        training_run_id="run",
        blind_window_id="window",
        blind_report_hash="b" * 64,
        previous_incumbent_id=None,
        verified_range_5d_anchor_hash=None,
    )
    range_component = M7ForecastBundleComponent(
        bundle_id="range_exit_20",
        package_id="range",
        package_sha256="c" * 64,
        training_run_id="run",
        blind_window_id="window",
        blind_report_hash="d" * 64,
        previous_incumbent_id=None,
        verified_range_5d_anchor_hash="e" * 64,
    )
    anchor = date(2026, 7, 1)
    direction_rows = []
    range_rows = []
    label_rows = []
    for symbol in SEED_UNIVERSE:
        label_rows.append(
            {
                "symbol": symbol,
                "session_date": anchor,
                "direction_1": 1,
                "direction_5": 1,
                "direction_20": 1,
                "range_exit_event_20": 1,
            }
        )
        for horizon in (1, 5, 20):
            for outcome in ("down", "flat", "up"):
                direction_rows.append(
                    {
                        "symbol": symbol,
                        "anchor_session": anchor,
                        "horizon": horizon,
                        "outcome": outcome,
                        "probability": 1.0 if outcome == "up" else 0.0,
                    }
                )
        range_rows.append(
            {
                "symbol": symbol,
                "anchor_session": anchor,
                "horizon": 20,
                "outcome": "exit_by",
                "probability": 1.0,
            }
        )
    labels = pd.DataFrame(label_rows)

    direction = _evaluate_direction_outcomes(
        pd.DataFrame(direction_rows),
        labels,
        component=direction_component,
        reference_brier=0.2,
    )
    range_report = _evaluate_range_outcomes(
        pd.DataFrame(range_rows),
        labels,
        component=range_component,
        reference_brier=0.2,
    )

    assert direction is not None
    assert direction.current_brier == 0.0
    assert direction.calibration_error == 0.0
    assert direction.sample_count == 300
    assert range_report is not None
    assert range_report.current_brier == 0.0
    assert range_report.calibration_error == 0.0
    assert range_report.sample_count == 100
