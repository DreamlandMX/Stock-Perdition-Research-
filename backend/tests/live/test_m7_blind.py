from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path

import pandas as pd
import pytest

from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_blind import (
    M7BlindLedgerError,
    M7BlindWindowLedger,
    register_m7_blind_ledger,
)
from stock_prediction.live.promotion_windows import (
    PromotionPackageBinding,
    PromotionWindowRegistry,
    build_seed100_promotion_window,
)
from stock_prediction.modeling.m7_blind_gates import M7BlindGateDecision


def _sessions(count: int = 120) -> tuple[date, ...]:
    return tuple(value.date() for value in pd.bdate_range("2026-08-25", periods=count))


def _ledger(tmp_path: Path) -> M7BlindWindowLedger:
    paths = LivePaths.from_workspace(tmp_path)
    sessions = _sessions()
    package = PromotionPackageBinding(
        package_sha256="a" * 64,
        universe_version="m1-seed-2026-07-11-v1",
        frozen_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
    )
    definition = build_seed100_promotion_window(package, list(sessions))
    registration = PromotionWindowRegistry(paths).register(definition)
    return register_m7_blind_ledger(
        paths,
        window_id=definition.window_id,
        bundle_id="direction",
        package_sha256=package.package_sha256,
        scheduled_sessions=sessions[1:],
        registration_path=registration,
    )


def _at_close(session: date) -> datetime:
    return datetime.combine(session, time(21), tzinfo=UTC)


def test_blind_ledger_is_forward_only_and_missing_anchor_extends_collection(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    sessions = _sessions()[1:]

    with pytest.raises(M7BlindLedgerError, match="next_scheduled_session"):
        ledger.record_prediction(
            anchor_session=sessions[1],
            generated_at=_at_close(sessions[1]),
            prediction_artifact_hash="b" * 64,
        )
    ledger.record_missing_prediction(
        anchor_session=sessions[0],
        recorded_at=_at_close(sessions[0]),
        reason="daily_data_not_ready",
    )
    for session in sessions[1:78]:
        ledger.record_prediction(
            anchor_session=session,
            generated_at=_at_close(session),
            prediction_artifact_hash="b" * 64,
        )

    status = ledger.status()
    assert status.state == "label_maturing"
    assert status.available_anchor_count == 77
    assert status.missing_anchor_count == 1
    assert status.last_available_anchor == sessions[77]
    assert status.last_available_anchor > ledger.manifest.nominal_end_session
    with pytest.raises(M7BlindLedgerError, match="collection_complete"):
        ledger.record_prediction(
            anchor_session=sessions[78],
            generated_at=_at_close(sessions[78]),
            prediction_artifact_hash="c" * 64,
        )


def test_blind_labels_require_available_prediction_and_exact_maturity(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    sessions = _sessions()[1:]
    for session in sessions[:77]:
        ledger.record_prediction(
            anchor_session=session,
            generated_at=_at_close(session),
            prediction_artifact_hash="b" * 64,
        )

    with pytest.raises(M7BlindLedgerError, match="label_not_mature"):
        ledger.record_mature_label(
            anchor_session=sessions[0],
            matured_at=_at_close(sessions[19]),
            label_artifact_hash="c" * 64,
            sample_keys_hash="d" * 64,
            sample_count=100,
        )
    for index, session in enumerate(sessions[:77]):
        ledger.record_mature_label(
            anchor_session=session,
            matured_at=_at_close(sessions[index + 20]),
            label_artifact_hash="c" * 64,
            sample_keys_hash="d" * 64,
            sample_count=100,
        )

    assert ledger.status().state == "ready_for_review"
    with pytest.raises(M7BlindLedgerError, match="already_recorded"):
        ledger.record_mature_label(
            anchor_session=sessions[0],
            matured_at=_at_close(sessions[20]),
            label_artifact_hash="c" * 64,
            sample_keys_hash="d" * 64,
            sample_count=100,
        )
    decision = M7BlindGateDecision(
        bundle_id="direction",
        status="awaiting_manual_approval",
        failed_gates=(),
        metrics={"evidence_scope": "blind_complete"},
        per_horizon={"1": {}, "5": {}, "20": {}},
    )
    report, report_hash = ledger.publish_review_report(
        decision=decision,
        generated_at=_at_close(sessions[96]),
    )

    assert report.name == f"{report_hash}.json"
    assert ledger.status().state == "consumed"
    with pytest.raises(M7BlindLedgerError, match="already_terminal"):
        ledger.publish_review_report(
            decision=decision,
            generated_at=_at_close(sessions[96]),
        )


def test_blind_ledger_consumption_and_invalidation_are_terminal(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.invalidate(reason="package_semantics_changed")

    assert ledger.status().state == "invalidated"
    with pytest.raises(M7BlindLedgerError, match="terminal"):
        ledger.record_missing_prediction(
            anchor_session=_sessions()[1],
            recorded_at=_at_close(_sessions()[1]),
            reason="late",
        )
    with pytest.raises(M7BlindLedgerError, match="already_terminal"):
        ledger.consume(blind_report_hash="e" * 64)


def test_blind_ledger_rejects_retroactive_timestamp_and_tampering(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    session = _sessions()[1]
    with pytest.raises(M7BlindLedgerError, match="realtime_timestamp"):
        ledger.record_prediction(
            anchor_session=session,
            generated_at=_at_close(_sessions()[2]),
            prediction_artifact_hash="b" * 64,
        )
    ledger.record_prediction(
        anchor_session=session,
        generated_at=_at_close(session),
        prediction_artifact_hash="b" * 64,
    )
    event = next((ledger.directory / "predictions").glob("*.json"))
    event.write_text("{}", encoding="utf-8")
    with pytest.raises(M7BlindLedgerError, match="event_invalid"):
        ledger.status()
