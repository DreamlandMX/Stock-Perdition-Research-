from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
import json
from pathlib import Path
import pickle
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_prediction.live.candidate_locks import create_candidate_lock
from stock_prediction.live.config import LivePaths
from stock_prediction.live.promotion import LiveModelPromoter
from stock_prediction.live.promotion_windows import (
    NEXT_PROMOTION_WINDOW,
    PromotionWindowRegistry,
    ledger_path,
    registration_path,
)
from stock_prediction.operations.registry import LocalModelRegistry


@pytest.mark.parametrize("holdout_passed", [False, True])
def test_mature_window_preserves_quality_gates_and_original_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, holdout_passed: bool
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    run = paths.models / "runs" / "candidate"
    (run / "components").mkdir(parents=True)
    keys = [
        (task, horizon)
        for horizon in (1, 5, 20)
        for task in ("forecast_direction", "breakout", "return_fwd", "mfe", "mae")
    ] + [("range_exit", horizon) for horizon in (5, 20)]
    report = {
        "model_version": "candidate-v1",
        "run_id": "run-v1",
        "calibration_version": "cal-v1",
        "development_all_gates_passed": True,
        "development_data_cutoff": "2026-06-29",
        "development_data_hash": "development-hash",
        "dataset_revision": "bars-v1",
        "feature_set_version": "features-v1",
        "universe_version": NEXT_PROMOTION_WINDOW.universe_version,
        "candidate_matrix_hash": "matrix-hash",
        "tasks": [{"task": t, "horizon": h, "gate_passed": True} for t, h in keys],
        "component_releases": [{"task": t, "horizon": h} for t, h in keys],
    }
    package_path = run / "production-package.pkl"
    package_path.write_bytes(pickle.dumps({}))
    for task, horizon in keys:
        (run / "components" / f"{task}-{horizon}-package.pkl").write_bytes(b"model")
    report_path = run / "training-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    matrix = run / "candidate-matrix.json"
    matrix.write_text("{}", encoding="utf-8")
    frozen_at = datetime(2026, 11, 2, 23, tzinfo=UTC)
    lock = create_candidate_lock(
        paths,
        report=report,
        report_path=report_path,
        package_path=package_path,
        candidate_matrix_path=matrix,
        frozen_at=frozen_at,
    )
    original_registration = registration_path(paths).read_bytes()
    window = replace(
        NEXT_PROMOTION_WINDOW,
        window_id="mature-holdout-20260811-20261005-v1",
        end=date(2026, 10, 5),
        label_complete_through=date(2026, 11, 2),
        required_anchor_sessions=39,
        package_sha256=lock["package_hash"],
        frozen_at=frozen_at,
    )
    windows = PromotionWindowRegistry(paths)
    windows.register(window)
    sessions = [
        day.date()
        for day in pd.bdate_range("2026-08-11", "2026-11-02")
        if day.date() != date(2026, 9, 7)
    ]
    feature_path = tmp_path / "features.parquet"
    pd.DataFrame({"session_date": [date(2026, 6, 1), *sessions]}).to_parquet(
        feature_path
    )
    monkeypatch.setattr(
        "stock_prediction.live.promotion_windows._full_universe_sessions",
        lambda _paths, required_size=None: sessions,
    )
    monkeypatch.setattr(
        "stock_prediction.live.promotion.load_latest_feature_artifact",
        lambda _paths: SimpleNamespace(path=feature_path, input_hash="feature-hash"),
    )

    def evaluate(_package, _development, frame, task, horizon):
        assert frame["session_date"].min() == window.start
        assert frame["session_date"].max() == window.end
        assert len(frame) == 39
        return {
            "task": task,
            "horizon": horizon,
            "gate_passed": holdout_passed
            or (task, horizon) != ("forecast_direction", 1),
        }

    monkeypatch.setattr(
        "stock_prediction.live.promotion._blind_classification", evaluate
    )
    monkeypatch.setattr("stock_prediction.live.promotion._blind_quantile", evaluate)
    monkeypatch.setattr(
        "stock_prediction.live.promotion._blind_survival",
        lambda package, development, frame, horizon: evaluate(
            package, development, frame, "range_exit", horizon
        ),
    )

    with pytest.raises(RuntimeError, match="promotion_window_not_ready"):
        LiveModelPromoter(paths).promote()
    monkeypatch.setattr(
        "stock_prediction.live.promotion_windows._full_universe_sessions",
        lambda _paths, required_size=None: sessions[:-1],
    )
    with pytest.raises(RuntimeError, match="promotion_window_not_ready"):
        LiveModelPromoter(paths).promote(evaluation_window=window)
    monkeypatch.setattr(
        "stock_prediction.live.promotion_windows._full_universe_sessions",
        lambda _paths, required_size=None: sessions,
    )
    with pytest.raises(RuntimeError, match="retrospective_promotion_window_invalid"):
        LiveModelPromoter(paths).promote(
            evaluation_window=replace(window, start=date(2026, 6, 29))
        )
    wrong_package = replace(
        window, window_id="wrong-package-window", package_sha256="f" * 64
    )
    windows.register(wrong_package)
    with pytest.raises(RuntimeError, match="promotion_window_package_mismatch"):
        LiveModelPromoter(paths).promote(evaluation_window=wrong_package)

    result = LiveModelPromoter(paths).promote(evaluation_window=window)

    assert result["evaluation_scope"] == "retrospective_holdout"
    assert result["all_gates_passed"] is holdout_passed
    assert result["window_id"] == window.window_id
    assert result["feature_input_hash"] == "feature-hash"
    assert (
        result["window_registration_hash"]
        == windows.load(window.window_id).registration_hash
    )
    assert len(result["tasks"]) == 17
    assert sum(row["gate_passed"] for row in result["tasks"]) == (
        17 if holdout_passed else 16
    )
    assert registration_path(paths).read_bytes() == original_registration
    assert not ledger_path(paths).exists()
    registry = LocalModelRegistry(paths.models / "registry")
    for task, horizon in [("forecast_bundle", 20), *keys]:
        assert (
            registry.snapshot(task, horizon).champion_version is not None
        ) is holdout_passed
    with pytest.raises(RuntimeError, match="promotion_window_not_ready"):
        LiveModelPromoter(paths).promote(evaluation_window=window)
    alias = replace(window, window_id="renamed-same-history-window")
    windows.register(alias)
    with pytest.raises(RuntimeError, match="promotion_window_not_ready"):
        LiveModelPromoter(paths).promote(evaluation_window=alias)
