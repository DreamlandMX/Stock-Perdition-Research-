from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pandas as pd
import pytest

from stock_prediction.live.candidate_locks import (
    create_candidate_lock,
    file_sha256,
    latest_candidate_lock,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.promotion_windows import (
    NEXT_PROMOTION_WINDOW,
    PromotionPackageBinding,
    PromotionWindowRegistry,
    build_seed100_promotion_window,
)
from stock_prediction.universe.seed import SEED_UNIVERSE_VERSION


def test_candidate_lock_is_created_only_for_complete_development(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    run = paths.models / "runs" / "run-1"
    run.mkdir(parents=True)
    package = run / "production-package.pkl"
    report_path = run / "training-report.json"
    matrix = run / "candidate-matrix.json"
    package.write_bytes(b"locked-package")
    matrix.write_text('{"tasks":[]}', encoding="utf-8")
    report = {
        "candidate_matrix_hash": "matrix-hash",
        "dataset_revision": "bars-r1",
        "development_all_gates_passed": True,
        "development_data_cutoff": "2026-06-29",
        "development_data_hash": "development-hash",
        "feature_set_version": "features-v1",
        "universe_version": "live-expanded-2026-08-10-v1",
        "model_version": "candidate-v1",
        "run_id": "run-1",
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")

    created = create_candidate_lock(
        paths,
        report=report,
        report_path=report_path,
        package_path=package,
        candidate_matrix_path=matrix,
    )

    assert created["development_data_cutoff"] == "2026-06-29"
    assert created["universe_version"] == "live-expanded-2026-08-10-v1"
    assert created["promotion_window_id"] == (
        "promotion-20260811-20261127-universe204-v1"
    )
    assert latest_candidate_lock(paths) == created


def test_tampered_candidate_package_invalidates_the_lock(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    run = paths.models / "runs" / "run-1"
    run.mkdir(parents=True)
    package = run / "production-package.pkl"
    report_path = run / "training-report.json"
    matrix = run / "candidate-matrix.json"
    package.write_bytes(b"locked-package")
    matrix.write_text("{}", encoding="utf-8")
    report = {
        "candidate_matrix_hash": "matrix-hash",
        "dataset_revision": "bars-r1",
        "development_all_gates_passed": True,
        "development_data_cutoff": "2026-06-29",
        "development_data_hash": "development-hash",
        "feature_set_version": "features-v1",
        "universe_version": "live-expanded-2026-08-10-v1",
        "model_version": "candidate-v1",
        "run_id": "run-1",
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    create_candidate_lock(
        paths,
        report=report,
        report_path=report_path,
        package_path=package,
        candidate_matrix_path=matrix,
    )
    package.write_bytes(b"tampered")

    assert latest_candidate_lock(paths) is None


def test_seed100_candidate_lock_requires_its_package_bound_window(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    run = paths.models / "runs" / "run-seed100"
    run.mkdir(parents=True)
    package = run / "production-package.pkl"
    report_path = run / "training-report.json"
    matrix = run / "candidate-matrix.json"
    package.write_bytes(b"seed100-package")
    matrix.write_text("{}", encoding="utf-8")
    frozen_at = datetime(2026, 8, 24, 20, tzinfo=UTC)
    binding = PromotionPackageBinding(
        package_sha256=file_sha256(package),
        universe_version=SEED_UNIVERSE_VERSION,
        frozen_at=frozen_at,
    )
    sessions = [
        timestamp.date() for timestamp in pd.bdate_range("2026-08-25", periods=100)
    ]
    definition = build_seed100_promotion_window(binding, sessions)
    PromotionWindowRegistry(paths).register(definition)
    report = {
        "candidate_matrix_hash": "matrix-hash",
        "dataset_revision": "bars-r1",
        "development_all_gates_passed": True,
        "development_data_cutoff": "2026-08-24",
        "development_data_hash": "development-hash",
        "feature_set_version": "features-v5",
        "universe_version": SEED_UNIVERSE_VERSION,
        "model_version": "seed100-candidate-v1",
        "run_id": "run-seed100",
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")

    created = create_candidate_lock(
        paths,
        report=report,
        report_path=report_path,
        package_path=package,
        candidate_matrix_path=matrix,
        promotion_window_id=definition.window_id,
        frozen_at=frozen_at,
    )

    assert created["promotion_window_id"] == definition.window_id
    assert created["package_hash"] == binding.package_sha256

    with pytest.raises(RuntimeError, match="universe_mismatch"):
        create_candidate_lock(
            paths,
            report={**report, "model_version": "seed100-wrong-window-v1"},
            report_path=report_path,
            package_path=package,
            candidate_matrix_path=matrix,
            promotion_window_id=NEXT_PROMOTION_WINDOW.window_id,
            frozen_at=frozen_at,
        )
