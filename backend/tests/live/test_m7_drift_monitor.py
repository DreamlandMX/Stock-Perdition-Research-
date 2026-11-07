from __future__ import annotations

from datetime import UTC, date, datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd

from stock_prediction.features.m7 import M7_CORE_V2_COLUMNS
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_drift_monitor import M7DriftMonitor
from stock_prediction.live.m7_orchestration import M7OrchestrationStore
from stock_prediction.live.m7_promotion import M7ForecastBundleComponent
from stock_prediction.modeling.m7_drift import build_m7_drift_reference


def _frame(*, shift: float = 0.0) -> pd.DataFrame:
    rows = 100
    frame = pd.DataFrame(
        {
            name: np.linspace(-1.0, 1.0, rows) + (shift if index < 5 else 0.0)
            for index, name in enumerate(M7_CORE_V2_COLUMNS)
        }
    )
    frame["eligible"] = True
    frame["regime"] = np.resize(np.asarray(["trend_up", "range"]), rows)
    return frame


def test_daily_monitor_records_real_psi_evidence_once_and_updates_streak(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    component = M7ForecastBundleComponent(
        bundle_id="direction",
        package_id="m7-direction-active",
        package_sha256="a" * 64,
        training_run_id="m7-run-001",
        blind_window_id="blind-001",
        blind_report_hash="b" * 64,
        previous_incumbent_id="legacy-direction-v1",
        verified_range_5d_anchor_hash=None,
    )
    reference = build_m7_drift_reference(
        _frame(),
        prediction_values={"direction_h1_up": np.linspace(0.05, 0.95, 100)},
        source_identity_hash="c" * 64,
    )
    current = _frame(shift=4.0)
    monitor = M7DriftMonitor(
        paths,
        component_provider=lambda: (component,),
        reference_provider=lambda _component: reference,
        observation_provider=lambda _component: (
            date(2026, 8, 25),
            current,
            {"direction_h1_up": np.linspace(0.60, 0.99, 100)},
        ),
    )

    first = monitor.run(generated_at=datetime(2026, 8, 25, 22, tzinfo=UTC))
    second = monitor.run(generated_at=datetime(2026, 8, 25, 23, tzinfo=UTC))

    assert first is not None
    assert second == first
    assert first.session == date(2026, 8, 25)
    assert first.warning is True
    assert first.component_report_hashes == {
        "direction": first.component_report_hashes["direction"]
    }
    decision = M7OrchestrationStore(paths).drift_decision()
    assert decision.state == "warning"
    assert decision.consecutive_daily_warning_sessions == 1
    detail_path = paths.models / "m7-drift" / "daily" / "2026-08-25" / "direction.json"
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    assert detail["report"]["feature_warning"] is True
    assert detail["report"]["prediction_distribution_shifted"] is True
