from __future__ import annotations

from datetime import UTC, date, datetime
from dataclasses import replace
import json
from pathlib import Path

import pandas as pd
import pytest

from stock_prediction.contracts.operations import ModelTrainingReadinessState
from stock_prediction.live.config import LivePaths
from stock_prediction.live.promotion_windows import (
    NEXT_PROMOTION_WINDOW,
    PromotionPackageBinding,
    PromotionWindowRegistry,
    build_seed100_promotion_window,
    ensure_promotion_window_registered,
    model_training_readiness,
    registration_path,
)
from stock_prediction.universe.seed import SEED_UNIVERSE_VERSION


def _market_days(start: str, end: str) -> list[date]:
    holidays = {
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    }
    return [
        timestamp.date()
        for timestamp in pd.bdate_range(start, end)
        if timestamp.date() not in holidays
    ]


def test_preregistered_window_is_frozen_and_hash_protected(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)

    target = ensure_promotion_window_registered(paths)
    first = json.loads(target.read_text(encoding="utf-8"))

    assert target == registration_path(paths)
    assert first["window_id"] == "promotion-20260811-20261127-universe204-v1"
    assert first["start"] == "2026-08-11"
    assert first["end"] == "2026-11-27"
    assert first["label_complete_through"] == "2026-12-28"
    assert first["universe_size"] == 204
    assert first["universe_version"] == "live-expanded-2026-08-10-v1"
    assert len(first["registration_hash"]) == 64
    assert ensure_promotion_window_registered(paths) == target

    first["end"] = "2026-10-15"
    target.write_text(json.dumps(first), encoding="utf-8")
    with pytest.raises(RuntimeError, match="registration_conflict"):
        ensure_promotion_window_registered(paths)


def test_registry_preserves_legacy_bytes_and_registers_distinct_seed100_window(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    legacy_path = ensure_promotion_window_registered(paths)
    legacy_bytes = legacy_path.read_bytes()
    package = PromotionPackageBinding(
        package_sha256="a" * 64,
        universe_version=SEED_UNIVERSE_VERSION,
        frozen_at=datetime(2026, 8, 24, 20, tzinfo=UTC),
    )
    future_sessions = [
        timestamp.date() for timestamp in pd.bdate_range("2026-08-25", periods=100)
    ]
    definition = build_seed100_promotion_window(package, future_sessions)
    registry = PromotionWindowRegistry(paths)

    seed_path = registry.register(definition)

    assert legacy_path.read_bytes() == legacy_bytes
    assert seed_path != legacy_path
    registered = registry.load(definition.window_id)
    assert registered.package_sha256 == package.package_sha256
    assert registered.universe_version == SEED_UNIVERSE_VERSION
    assert registered.definition.start == future_sessions[1]
    assert registered.definition.end == future_sessions[77]
    assert registered.definition.label_complete_through == future_sessions[97]
    assert (
        registered.registration_hash
        != json.loads(legacy_bytes.decode("utf-8"))["registration_hash"]
    )

    conflicting = replace(definition, end=future_sessions[78])
    with pytest.raises(RuntimeError, match="registration_conflict"):
        registry.register(conflicting)


def test_package_bound_registry_readiness_and_consumption_are_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    package = PromotionPackageBinding(
        package_sha256="b" * 64,
        universe_version=SEED_UNIVERSE_VERSION,
        frozen_at=datetime(2026, 8, 24, 20, tzinfo=UTC),
    )
    sessions = [
        timestamp.date() for timestamp in pd.bdate_range("2026-08-25", periods=100)
    ]
    definition = build_seed100_promotion_window(package, sessions)
    registry = PromotionWindowRegistry(paths)
    registry.register(definition)
    monkeypatch.setattr(
        "stock_prediction.live.promotion_windows._full_universe_sessions",
        lambda _paths, required_size=None: sessions[:98],
    )

    readiness = registry.readiness(definition.window_id, package)

    assert readiness.state is ModelTrainingReadinessState.READY
    assert readiness.window_id == definition.window_id
    assert readiness.anchor_sessions_collected == 77
    assert readiness.fully_labeled_sessions == 77
    consumed_path = registry.consume(
        definition.window_id,
        package_sha256=package.package_sha256,
        reason="manual_promotion_approved",
    )
    assert consumed_path.is_file()
    assert (
        registry.readiness(definition.window_id, package).state
        is ModelTrainingReadinessState.CONSUMED
    )
    with pytest.raises(RuntimeError, match="already_consumed"):
        registry.consume(
            definition.window_id,
            package_sha256=package.package_sha256,
            reason="reuse_attempt",
        )


def test_legacy_universe204_window_cannot_authorize_seed100_package(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    registry = PromotionWindowRegistry(paths)
    registry.register(NEXT_PROMOTION_WINDOW)
    seed_package = PromotionPackageBinding(
        package_sha256="c" * 64,
        universe_version=SEED_UNIVERSE_VERSION,
        frozen_at=datetime(2026, 8, 24, 20, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="universe_mismatch"):
        registry.readiness(NEXT_PROMOTION_WINDOW.window_id, seed_package)


def test_readiness_reports_current_20_of_77_anchor_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    current_sessions = _market_days("2026-08-11", "2026-09-08")
    assert len(current_sessions) == 20
    monkeypatch.setattr(
        "stock_prediction.live.promotion_windows._full_universe_sessions",
        lambda _paths: current_sessions,
    )

    readiness = model_training_readiness(paths)

    assert readiness.state is ModelTrainingReadinessState.COLLECTING
    assert readiness.anchor_sessions_collected == 20
    assert readiness.required_anchor_sessions == 77
    assert readiness.fully_labeled_sessions == 0
    assert readiness.data_cutoff == date(2026, 9, 8)
    assert readiness.can_submit is False
    assert readiness.detail == "collecting_anchor_sessions"


def test_readiness_unlocks_after_all_anchors_have_20_future_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    complete_sessions = _market_days("2026-08-11", "2026-12-28")
    anchors = [
        session
        for session in complete_sessions
        if NEXT_PROMOTION_WINDOW.start <= session <= NEXT_PROMOTION_WINDOW.end
    ]
    assert len(anchors) == 77
    monkeypatch.setattr(
        "stock_prediction.live.promotion_windows._full_universe_sessions",
        lambda _paths: complete_sessions,
    )
    monkeypatch.setattr(
        "stock_prediction.live.candidate_locks.latest_candidate_lock",
        lambda _paths: {
            "candidate_model_version": "candidate-17-of-17",
            "created_at": "2026-07-29T16:00:00+00:00",
            "universe_version": "live-expanded-2026-08-10-v1",
        },
    )

    readiness = model_training_readiness(paths)

    assert readiness.state is ModelTrainingReadinessState.READY
    assert readiness.anchor_sessions_collected == 77
    assert readiness.fully_labeled_sessions == 77
    assert readiness.data_cutoff == date(2026, 12, 28)
    assert readiness.can_submit is True
    assert readiness.automatic_training_at.isoformat() == "2026-12-29T15:00:00+00:00"


def test_complete_blind_window_still_requires_a_frozen_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    complete_sessions = _market_days("2026-08-11", "2026-12-28")
    monkeypatch.setattr(
        "stock_prediction.live.promotion_windows._full_universe_sessions",
        lambda _paths: complete_sessions,
    )

    readiness = model_training_readiness(paths)

    assert readiness.state is ModelTrainingReadinessState.READY
    assert readiness.anchor_sessions_collected == 77
    assert readiness.fully_labeled_sessions == 77
    assert readiness.frozen_candidate_version is None
    assert readiness.can_submit_promotion is False
    assert readiness.can_submit is False


def test_legacy_candidate_lock_does_not_block_first_universe_204_recipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    monkeypatch.setattr(
        "stock_prediction.live.promotion_windows._full_universe_sessions",
        lambda _paths: [],
    )
    monkeypatch.setattr(
        "stock_prediction.live.candidate_locks.latest_candidate_lock",
        lambda _paths: {
            "candidate_model_version": "legacy-candidate",
            "created_at": "2026-07-29T16:00:00+00:00",
            "universe_version": "m1-seed-2026-07-11-v1",
        },
    )

    readiness = model_training_readiness(paths)

    assert readiness.frozen_candidate_version is None
    assert readiness.next_candidate_recipe_version == (
        "candidate-recipe-universe204-v1"
    )
    assert readiness.can_submit_candidate_training is True
