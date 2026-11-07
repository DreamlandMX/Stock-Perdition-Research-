from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_orchestration import (
    M7DailyDriftEvidence,
    M7OrchestrationStore,
    M7WeeklyOutcomeEvidence,
    build_monthly_training_intent,
)


def test_monthly_intent_is_first_following_saturday_at_0200_et() -> None:
    intent = build_monthly_training_intent(
        last_completed_session=date(2026, 7, 31),
        universe_version="m1-seed-2026-07-11-v1",
        evidence_hash="a" * 64,
    )

    assert intent.eligible_at == datetime(
        2026, 8, 1, 2, tzinfo=ZoneInfo("America/New_York")
    ).astimezone(UTC)
    assert intent.trigger == "scheduled"
    assert intent.bundles == ("direction", "range_exit_20")


def test_drift_requires_five_daily_warnings_and_two_mature_weekly_breaches(
    tmp_path: Path,
) -> None:
    store = M7OrchestrationStore(LivePaths.from_workspace(tmp_path))
    for day in range(3, 8):
        store.record_daily_evidence(
            M7DailyDriftEvidence(
                session=date(2026, 8, day),
                eligible=True,
                core_feature_psi_fraction_above_020=0.25,
                prediction_distribution_shifted=False,
                evidence_hash=f"{day}" * 64,
            )
        )

    review = store.drift_decision()
    assert review.state == "review_required"
    assert review.consecutive_daily_warning_sessions == 5
    assert review.consecutive_mature_weekly_breaches == 0

    store.record_weekly_evidence(
        M7WeeklyOutcomeEvidence(
            week_ending=date(2026, 8, 7),
            labels_mature=True,
            relative_brier_degradation=0.051,
            calibration_error=0.04,
            evidence_hash="a" * 64,
        )
    )
    assert store.drift_decision().state == "review_required"
    store.record_weekly_evidence(
        M7WeeklyOutcomeEvidence(
            week_ending=date(2026, 8, 14),
            labels_mature=True,
            relative_brier_degradation=0.01,
            calibration_error=0.051,
            evidence_hash="b" * 64,
        )
    )

    authorized = store.drift_decision()
    assert authorized.state == "training_authorized"
    assert authorized.consecutive_mature_weekly_breaches == 2


def test_warning_or_outcome_break_resets_consecutive_count(tmp_path: Path) -> None:
    store = M7OrchestrationStore(LivePaths.from_workspace(tmp_path))
    for day, warning in zip(
        range(3, 9), (True, True, False, True, True, True), strict=True
    ):
        store.record_daily_evidence(
            M7DailyDriftEvidence(
                session=date(2026, 8, day),
                eligible=True,
                core_feature_psi_fraction_above_020=0.25 if warning else 0.10,
                prediction_distribution_shifted=False,
                evidence_hash=f"{day}" * 64,
            )
        )
    for week, breach in ((7, True), (14, False), (21, True)):
        store.record_weekly_evidence(
            M7WeeklyOutcomeEvidence(
                week_ending=date(2026, 8, week),
                labels_mature=True,
                relative_brier_degradation=0.06 if breach else 0.01,
                calibration_error=0.03,
                evidence_hash=f"{week % 10}" * 64,
            )
        )

    decision = store.drift_decision()
    assert decision.state == "warning"
    assert decision.consecutive_daily_warning_sessions == 3
    assert decision.consecutive_mature_weekly_breaches == 1


def test_queue_claim_deduplicates_exact_intent(tmp_path: Path) -> None:
    store = M7OrchestrationStore(LivePaths.from_workspace(tmp_path))
    intent = build_monthly_training_intent(
        last_completed_session=date(2026, 7, 31),
        universe_version="m1-seed-2026-07-11-v1",
        evidence_hash="a" * 64,
    )
    submissions: list[str] = []

    first = store.queue_once(
        intent, lambda row: submissions.append(row.intent_id) or "op-1"
    )
    second = store.queue_once(
        intent, lambda row: submissions.append(row.intent_id) or "op-2"
    )

    assert first.created is True
    assert second.created is False
    assert second.operation_id == "op-1"
    assert submissions == [intent.intent_id]
