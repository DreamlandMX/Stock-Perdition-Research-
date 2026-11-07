from __future__ import annotations

from datetime import UTC, datetime, time
import json
from pathlib import Path

import pandas as pd
import pytest

from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_blind import register_m7_blind_ledger
from stock_prediction.live.m7_blind_review import (
    M7BlindReviewRunner,
    evaluate_m7_direction_blind_frames,
    evaluate_m7_range_blind_frames,
    resolve_verified_m7_range_anchor,
)
from stock_prediction.live.promotion_windows import (
    PromotionPackageBinding,
    PromotionWindowRegistry,
    build_seed100_promotion_window,
)
from stock_prediction.modeling.m7_blind_gates import M7BlindGateDecision
from stock_prediction.operations.registry import LocalModelRegistry
from stock_prediction.universe.seed import SEED_UNIVERSE


def test_range_anchor_resolution_requires_current_hash_verified_champion(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    registry = LocalModelRegistry(paths.models / "registry")
    releases = []
    for index in (1, 2):
        artifact = tmp_path / f"range-five-{index}.bin"
        artifact.write_bytes(f"range-five-{index}".encode("utf-8"))
        release = registry.register_release(
            model_version=f"range-five-v{index}",
            task="range_exit",
            horizon=5,
            calibration_version=f"range-cal-v{index}",
            source_decision_id=f"range-decision-v{index}",
            promotion_eligible=True,
            artifacts={"component.bin": artifact},
            registered_at=datetime(2026, 8, 20 + index, tzinfo=UTC),
        )
        releases.append(release)
    registry.promote(
        "range-five-v1",
        reason="verified five-day anchor",
        recorded_at=datetime(2026, 8, 23, tzinfo=UTC),
    )

    anchor_hash, verified = resolve_verified_m7_range_anchor(paths, "range-five-v1")

    assert verified is True
    assert anchor_hash == releases[0].manifest_hash
    registry.promote(
        "range-five-v2",
        reason="anchor changed",
        recorded_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    stale_hash, stale_verified = resolve_verified_m7_range_anchor(
        paths, "range-five-v1"
    )
    assert stale_verified is False
    assert stale_hash == releases[0].manifest_hash


def _direction_blind_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    anchors = tuple(value.date() for value in pd.bdate_range("2026-08-25", periods=77))
    prediction_rows: list[dict[str, object]] = []
    label_rows: list[dict[str, object]] = []
    labels = ("down", "flat", "up")
    for anchor in anchors:
        for symbol_index, symbol in enumerate(SEED_UNIVERSE):
            observed = labels[symbol_index % 3]
            candidate = {
                "down": (0.99, 0.005, 0.005),
                "flat": (0.005, 0.99, 0.005),
                "up": (0.005, 0.005, 0.99),
            }[observed]
            for horizon in (1, 5, 20):
                key = {
                    "symbol": symbol,
                    "anchor_session": anchor,
                    "horizon": horizon,
                }
                prediction_rows.append(
                    {
                        **key,
                        "probability_down": candidate[0],
                        "probability_flat": candidate[1],
                        "probability_up": candidate[2],
                        "incumbent_available": True,
                        "incumbent_probability_down": 1 / 3,
                        "incumbent_probability_flat": 1 / 3,
                        "incumbent_probability_up": 1 / 3,
                    }
                )
                label_rows.append(
                    {
                        **key,
                        "observed_label": observed,
                        "label_available": True,
                    }
                )
    return pd.DataFrame(prediction_rows), pd.DataFrame(label_rows)


def test_direction_blind_review_passes_exact_77_by_seed100_evidence() -> None:
    predictions, labels = _direction_blind_frames()

    decision = evaluate_m7_direction_blind_frames(
        predictions,
        labels,
        inference_sla_passed=True,
        economic_guardrail_passed=True,
        n_bootstrap=200,
    )

    assert decision.status == "awaiting_manual_approval"
    assert decision.failed_gates == ()
    assert decision.metrics["available_anchor_count"] == 77
    assert decision.metrics["mature_sample_count"] == 23_100
    assert decision.metrics["economic_catastrophic_guardrail"] is True


def test_direction_blind_review_rejects_missing_frozen_incumbent_rows() -> None:
    predictions, labels = _direction_blind_frames()
    predictions.loc[predictions.index[0], "incumbent_available"] = False

    decision = evaluate_m7_direction_blind_frames(
        predictions,
        labels,
        inference_sla_passed=True,
        economic_guardrail_passed=True,
        n_bootstrap=50,
    )

    assert decision.status == "rejected"
    assert "direction_incumbent_coverage" in decision.failed_gates


def _range_blind_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    anchors = tuple(value.date() for value in pd.bdate_range("2026-08-25", periods=77))
    prediction_rows: list[dict[str, object]] = []
    label_rows: list[dict[str, object]] = []
    for anchor in anchors:
        for symbol_index, symbol in enumerate(SEED_UNIVERSE):
            observed = symbol_index % 2
            prediction_rows.append(
                {
                    "symbol": symbol,
                    "anchor_session": anchor,
                    "horizon": 20,
                    "range_task_eligible": True,
                    "probability_exit_by_5": 0.10 if observed else 0.005,
                    "probability_exit_by_20": 0.99 if observed else 0.01,
                    "incumbent_available": True,
                    "incumbent_probability_exit_by_20": 0.80,
                }
            )
            label_rows.append(
                {
                    "symbol": symbol,
                    "anchor_session": anchor,
                    "horizon": 20,
                    "observed_event": observed,
                    "event_day": 5 if observed else 20,
                    "label_available": True,
                }
            )
    return pd.DataFrame(prediction_rows), pd.DataFrame(label_rows)


def test_range_blind_review_passes_monotone_calibrated_seed100_evidence() -> None:
    predictions, labels = _range_blind_frames()

    decision = evaluate_m7_range_blind_frames(
        predictions,
        labels,
        inference_sla_passed=True,
        five_day_anchor_hash="a" * 64,
        five_day_anchor_verified=True,
        n_bootstrap=200,
    )

    assert decision.status == "awaiting_manual_approval"
    assert decision.failed_gates == ()
    assert decision.metrics["available_anchor_count"] == 77
    assert decision.metrics["mature_sample_count"] == 7_700


def test_range_blind_review_rejects_unbound_five_day_anchor() -> None:
    predictions, labels = _range_blind_frames()

    decision = evaluate_m7_range_blind_frames(
        predictions,
        labels,
        inference_sla_passed=True,
        five_day_anchor_hash="a" * 64,
        five_day_anchor_verified=False,
        n_bootstrap=50,
    )

    assert decision.status == "rejected"
    assert "range_five_day_anchor_unverified" in decision.failed_gates


def test_ready_direction_window_publishes_one_immutable_review_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    sessions = tuple(
        value.date() for value in pd.bdate_range("2026-08-25", periods=120)
    )
    package = PromotionPackageBinding(
        package_sha256="a" * 64,
        universe_version="m1-seed-2026-07-11-v1",
        frozen_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
    )
    definition = build_seed100_promotion_window(package, list(sessions))
    registration = PromotionWindowRegistry(paths).register(definition)
    ledger = register_m7_blind_ledger(
        paths,
        window_id=definition.window_id,
        bundle_id="direction",
        package_sha256=package.package_sha256,
        incumbent_id="incumbent-v1",
        scheduled_sessions=sessions[1:],
        registration_path=registration,
    )
    scheduled = sessions[1:]
    for session in scheduled[:77]:
        ledger.record_prediction(
            anchor_session=session,
            generated_at=datetime.combine(session, time(21), tzinfo=UTC),
            prediction_artifact_hash="b" * 64,
        )
    for index, session in enumerate(scheduled[:77]):
        ledger.record_mature_label(
            anchor_session=session,
            matured_at=datetime.combine(scheduled[index + 20], time(21), tzinfo=UTC),
            label_artifact_hash="c" * 64,
            sample_keys_hash="d" * 64,
            sample_count=300,
        )
    predictions, labels = _direction_blind_frames()
    monkeypatch.setattr(
        "stock_prediction.live.m7_blind_review._load_blind_window",
        lambda _ledger: (predictions, labels, True),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_blind_review._direction_economics",
        lambda *_args, **_kwargs: None,
    )
    decision = M7BlindGateDecision(
        bundle_id="direction",
        status="awaiting_manual_approval",
        failed_gates=(),
        metrics={"evidence_scope": "blind_complete"},
        per_horizon={"1": {}, "5": {}, "20": {}},
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_blind_review.evaluate_m7_direction_blind_frames",
        lambda *_args, **_kwargs: decision,
    )

    results = M7BlindReviewRunner(paths).run_ready(
        generated_at=datetime.combine(scheduled[96], time(21), tzinfo=UTC),
        n_bootstrap=1,
    )

    assert len(results) == 1
    assert results[0].decision_status == "awaiting_manual_approval"
    assert results[0].report_path.is_file()
    report = json.loads(results[0].report_path.read_text(encoding="utf-8"))
    evidence = report["evidence"]
    assert evidence["schema_version"] == "m7-blind-review-evidence-v1"
    assert evidence["incumbent_id"] == "incumbent-v1"
    assert evidence["prediction_row_count"] == 23_100
    assert evidence["label_row_count"] == 23_100
    assert evidence["non_causal_caveat"] is True
    reliability = evidence["reliability"]
    assert len(reliability) == 27
    assert {
        (row["horizon"], row["class_name"], row["model"]) for row in reliability
    } == {
        (horizon, class_name, model)
        for horizon in (1, 5, 20)
        for class_name in ("down", "flat", "up")
        for model in ("candidate", "incumbent")
    }
    assert all(row["sample_count"] > 0 for row in reliability)
    assert ledger.status().state == "consumed"
