from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from stock_prediction.modeling.contracts import (
    BacktestTrial,
    EvaluationRole,
    EvaluationScope,
    EvaluationWindow,
    TaskName,
    TrialStatus,
)
from stock_prediction.modeling.ledger import JsonlTrialLedger, TrialLedgerError


NOW = datetime(2026, 7, 12, 15, tzinfo=UTC)


def window() -> EvaluationWindow:
    return EvaluationWindow(
        window_id="outer-fold-1-test",
        role=EvaluationRole.OUTER_TEST,
        scope=EvaluationScope.SYNTHETIC_OFFLINE,
        start_session=date(2024, 1, 2),
        end_session=date(2024, 6, 28),
        split_version="split-v1",
        dataset_input_hash="a" * 64,
        calendar_hash="b" * 64,
        consumed_at=None,
        consumption_reason=None,
    )


def trial(
    trial_id: str = "trial-direction-h5-001",
    *,
    evaluation_window: EvaluationWindow | None = None,
) -> BacktestTrial:
    bound_window = evaluation_window or window()
    return BacktestTrial(
        trial_id=trial_id,
        registered_at=NOW,
        owner="local-researcher",
        hypothesis="LightGBM improves calibrated direction Brier skill.",
        task=TaskName.FORECAST_DIRECTION,
        horizon=5,
        model_version="lgbm-direction-v1",
        feature_set_version="core-v1",
        threshold_version="threshold-v1",
        label_version="label-v1",
        split_version=bound_window.split_version,
        dataset_input_hash=bound_window.dataset_input_hash,
        calendar_hash=bound_window.calendar_hash,
        evaluation_window_id=bound_window.window_id,
        evaluation_window=bound_window,
        primary_metrics=("brier_skill",),
        secondary_metrics=(),
        frozen_parameters={"seed": 17},
        parent_trial_id=None,
        status=TrialStatus.REGISTERED,
        result=None,
        stop_reason=None,
        touched_lockbox=False,
        consumed_at=None,
    )


def test_trial_must_be_registered_before_result(tmp_path: Path) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")

    with pytest.raises(TrialLedgerError, match="registered"):
        ledger.finish(
            "trial-direction-h5-001",
            status=TrialStatus.SUCCEEDED,
            result={"brier_skill": 0.02},
            finished_at=NOW,
            stop_reason=None,
        )


def test_registration_and_result_are_append_only_and_unique(tmp_path: Path) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    registered = trial()
    ledger.register(registered)
    ledger.finish(
        registered.trial_id,
        status=TrialStatus.SUCCEEDED,
        result={"brier_skill": 0.02},
        finished_at=NOW,
        stop_reason=None,
    )

    records = ledger.read_records()
    assert [record.record_type.value for record in records] == [
        "trial_registered",
        "trial_finished",
    ]
    assert records[0].payload.status is TrialStatus.REGISTERED
    with pytest.raises(TrialLedgerError, match="duplicate"):
        ledger.register(registered)
    with pytest.raises(TrialLedgerError, match="already finished"):
        ledger.finish(
            registered.trial_id,
            status=TrialStatus.FAILED,
            result=None,
            finished_at=NOW,
            stop_reason="retry after seeing result",
        )


def test_consumed_window_cannot_be_reused_or_mutated(tmp_path: Path) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    consumed = ledger.consume_window(
        window(), consumed_at=NOW, reason="promotion_review"
    )

    assert consumed.consumed_at == NOW
    with pytest.raises(TrialLedgerError, match="already consumed"):
        ledger.consume_window(window(), consumed_at=NOW, reason="retune")
    with pytest.raises(TrialLedgerError, match="consumed evaluation window"):
        ledger.register(trial("trial-after-consumption"))


def test_window_id_has_one_canonical_definition(tmp_path: Path) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    ledger.register(trial("trial-canonical-window"))
    conflicting = window().model_copy(
        update={"scope": EvaluationScope.RESEARCH_ONLY}
    )

    with pytest.raises(TrialLedgerError, match="scopes differ"):
        ledger.register(
            trial("trial-conflicting-window", evaluation_window=conflicting)
        )


def test_window_consumption_matches_canonical_definition(tmp_path: Path) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    ledger.register(trial())
    conflicting = window().model_copy(update={"split_version": "split-v2"})

    with pytest.raises(TrialLedgerError, match="split versions differ"):
        ledger.consume_window(
            conflicting,
            consumed_at=NOW,
            reason="conflicting_definition",
        )


def test_window_consumption_cannot_precede_referencing_registration(
    tmp_path: Path,
) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    ledger.register(trial())

    with pytest.raises(TrialLedgerError, match="precede trial registration"):
        ledger.consume_window(
            window(),
            consumed_at=NOW - timedelta(hours=1),
            reason="premature_consumption",
        )


def test_failed_trial_requires_stop_reason_and_is_retained(tmp_path: Path) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    ledger.register(trial())

    with pytest.raises(TrialLedgerError, match="stop reason"):
        ledger.finish(
            trial().trial_id,
            status=TrialStatus.FAILED,
            result=None,
            finished_at=NOW,
            stop_reason=None,
        )
    ledger.finish(
        trial().trial_id,
        status=TrialStatus.FAILED,
        result=None,
        finished_at=NOW,
        stop_reason="insufficient class support",
    )
    assert ledger.read_records()[-1].payload.stop_reason == (
        "insufficient class support"
    )


def test_trial_cannot_finish_before_its_registration_time(tmp_path: Path) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    ledger.register(trial())

    with pytest.raises(TrialLedgerError, match="precede registration"):
        ledger.finish(
            trial().trial_id,
            status=TrialStatus.SUCCEEDED,
            result={"brier_skill": 0.02},
            finished_at=datetime(2026, 7, 12, 14, 59, tzinfo=UTC),
            stop_reason=None,
        )


def test_derived_trial_requires_completed_parent_and_later_registration(
    tmp_path: Path,
) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    parent = trial("trial-parent")
    child = trial("trial-child").model_copy(
        update={"parent_trial_id": parent.trial_id}
    )
    ledger.register(parent)

    with pytest.raises(TrialLedgerError, match="parent trial must be finished"):
        ledger.register(child)
    ledger.finish(
        parent.trial_id,
        status=TrialStatus.SUCCEEDED,
        result={"brier_skill": 0.02},
        finished_at=NOW,
        stop_reason=None,
    )
    ledger.register(child)
    assert ledger.read_records()[-1].record_id == child.trial_id


def test_corrupt_or_partial_jsonl_is_not_silently_ignored(tmp_path: Path) -> None:
    path = tmp_path / "trials.jsonl"
    path.write_text('{"record_type":"trial_registered"', encoding="utf-8")

    with pytest.raises(TrialLedgerError, match="corrupt"):
        JsonlTrialLedger(path).read_records()


def test_semantically_invalid_payload_and_state_history_are_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trials.jsonl"
    path.write_text(
        '{"record_type":"trial_registered","recorded_at":"2026-07-12T15:00:00Z",'
        '"record_id":"trial-bad","payload":{}}\n',
        encoding="utf-8",
    )

    with pytest.raises(TrialLedgerError, match="corrupt"):
        JsonlTrialLedger(path).read_records()


def test_valid_records_reordered_into_finish_before_registration_are_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trials.jsonl"
    ledger = JsonlTrialLedger(path)
    ledger.register(trial())
    ledger.finish(
        trial().trial_id,
        status=TrialStatus.SUCCEEDED,
        result={"brier_skill": 0.02},
        finished_at=NOW,
        stop_reason=None,
    )
    registration, finish = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(finish + registration)

    with pytest.raises(TrialLedgerError, match="corrupt state"):
        ledger.read_records()


def test_finish_rejects_non_finite_result_instead_of_serializing_null(
    tmp_path: Path,
) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    ledger.register(trial())

    with pytest.raises((TrialLedgerError, ValueError), match="finite|result"):
        ledger.finish(
            trial().trial_id,
            status=TrialStatus.SUCCEEDED,
            result={"brier_skill": float("nan")},
            finished_at=NOW,
            stop_reason=None,
        )


def test_register_revalidates_model_copy_bypass(tmp_path: Path) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    bypassed = trial().model_copy(update={"result": {"brier_skill": 1.0}})

    with pytest.raises(TrialLedgerError, match="registered trial"):
        ledger.register(bypassed)


def test_lockbox_finish_is_linked_to_consumed_window(tmp_path: Path) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    lockbox = window().model_copy(update={"role": EvaluationRole.LOCKBOX})
    lockbox_trial = trial(evaluation_window=lockbox)
    ledger.register(lockbox_trial)
    ledger.finish(
        lockbox_trial.trial_id,
        status=TrialStatus.SUCCEEDED,
        result={"brier_skill": 0.02},
        finished_at=NOW,
        stop_reason=None,
        lockbox_window=lockbox,
        consumed_at=NOW,
    )

    payload = ledger.read_records()[-1].payload
    assert payload.touched_lockbox is True
    assert payload.consumed_at == NOW
    assert payload.consumed_window is not None
    assert payload.consumed_window.split_version == lockbox_trial.split_version


def test_lockbox_trial_cannot_succeed_without_atomic_consumption(
    tmp_path: Path,
) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    lockbox = window().model_copy(update={"role": EvaluationRole.LOCKBOX})
    registered = trial(evaluation_window=lockbox)
    ledger.register(registered)

    with pytest.raises(TrialLedgerError, match="atomic lockbox"):
        ledger.finish(
            registered.trial_id,
            status=TrialStatus.SUCCEEDED,
            result={"brier_skill": 0.02},
            finished_at=NOW,
            stop_reason=None,
        )


def test_lockbox_consumption_must_be_atomic_with_finish(tmp_path: Path) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    lockbox = window().model_copy(update={"role": EvaluationRole.LOCKBOX})

    with pytest.raises(TrialLedgerError, match="atomic"):
        ledger.consume_window(lockbox, consumed_at=NOW, reason="release_review")


def test_atomic_finish_rejects_a_previously_consumed_lockbox(
    tmp_path: Path,
) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    consumed_lockbox = window().model_copy(
        update={
            "role": EvaluationRole.LOCKBOX,
            "consumed_at": NOW - timedelta(minutes=10),
            "consumption_reason": "prior_evaluation",
        }
    )
    registered = trial(
        evaluation_window=consumed_lockbox.model_copy(
            update={"consumed_at": None, "consumption_reason": None}
        )
    )
    ledger.register(registered)

    with pytest.raises(TrialLedgerError, match="already consumed"):
        ledger.finish(
            registered.trial_id,
            status=TrialStatus.SUCCEEDED,
            result={"brier_skill": 0.02},
            finished_at=NOW,
            stop_reason=None,
            lockbox_window=consumed_lockbox,
            consumed_at=NOW,
        )


def test_lockbox_consumption_cannot_precede_trial_registration(
    tmp_path: Path,
) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    lockbox = window().model_copy(update={"role": EvaluationRole.LOCKBOX})
    registered = trial(evaluation_window=lockbox)
    ledger.register(registered)

    with pytest.raises(TrialLedgerError, match="precede trial registration"):
        ledger.finish(
            registered.trial_id,
            status=TrialStatus.SUCCEEDED,
            result={"brier_skill": 0.02},
            finished_at=NOW,
            stop_reason=None,
            lockbox_window=lockbox,
            consumed_at=NOW - timedelta(hours=1),
        )


def test_atomic_lockbox_consumption_follows_every_referencing_registration(
    tmp_path: Path,
) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    lockbox = window().model_copy(update={"role": EvaluationRole.LOCKBOX})
    consumer = trial("trial-consumer", evaluation_window=lockbox)
    future_registration = trial(
        "trial-future-registration", evaluation_window=lockbox
    ).model_copy(update={"registered_at": NOW + timedelta(hours=1)})
    ledger.register(consumer)
    ledger.register(future_registration)

    with pytest.raises(TrialLedgerError, match="precede trial registration"):
        ledger.finish(
            consumer.trial_id,
            status=TrialStatus.SUCCEEDED,
            result={"brier_skill": 0.02},
            finished_at=NOW + timedelta(minutes=30),
            stop_reason=None,
            lockbox_window=lockbox,
            consumed_at=NOW + timedelta(minutes=30),
        )


def test_lockbox_finish_rejects_future_consumption_and_split_mismatch(
    tmp_path: Path,
) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    lockbox = window().model_copy(update={"role": EvaluationRole.LOCKBOX})
    registered = trial(evaluation_window=lockbox)
    ledger.register(registered)

    with pytest.raises(TrialLedgerError, match="split"):
        ledger.finish(
            registered.trial_id,
            status=TrialStatus.SUCCEEDED,
            result={"brier_skill": 0.02},
            finished_at=NOW,
            stop_reason=None,
            lockbox_window=lockbox.model_copy(update={"split_version": "split-v2"}),
            consumed_at=NOW,
        )
    with pytest.raises(TrialLedgerError, match="after trial finish"):
        ledger.finish(
            registered.trial_id,
            status=TrialStatus.SUCCEEDED,
            result={"brier_skill": 0.02},
            finished_at=NOW,
            stop_reason=None,
            lockbox_window=lockbox,
            consumed_at=datetime(2026, 7, 12, 15, 1, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("dataset_input_hash", "c" * 64, "dataset"),
        ("calendar_hash", "d" * 64, "calendar"),
        ("scope", EvaluationScope.RESEARCH_ONLY, "scopes"),
        ("start_session", date(2024, 1, 3), "dates"),
    ],
)
def test_lockbox_finish_rejects_data_identity_mismatch(
    tmp_path: Path,
    field: str,
    replacement: object,
    message: str,
) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    lockbox = window().model_copy(update={"role": EvaluationRole.LOCKBOX})
    registered = trial(evaluation_window=lockbox)
    ledger.register(registered)

    with pytest.raises(TrialLedgerError, match=message):
        ledger.finish(
            registered.trial_id,
            status=TrialStatus.SUCCEEDED,
            result={"brier_skill": 0.02},
            finished_at=NOW,
            stop_reason=None,
            lockbox_window=lockbox.model_copy(update={field: replacement}),
            consumed_at=NOW,
        )


def test_lockbox_evidence_is_attributed_only_to_consuming_trial(
    tmp_path: Path,
) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    lockbox = window().model_copy(update={"role": EvaluationRole.LOCKBOX})
    consumer = trial("trial-lockbox-consumer", evaluation_window=lockbox)
    independent_abort = trial(
        "trial-independent-abort", evaluation_window=lockbox
    )
    ledger.register(consumer)
    ledger.register(independent_abort)

    ledger.finish(
        consumer.trial_id,
        status=TrialStatus.SUCCEEDED,
        result={"brier_skill": 0.02},
        finished_at=NOW,
        stop_reason=None,
        lockbox_window=lockbox,
        consumed_at=NOW,
    )
    ledger.finish(
        independent_abort.trial_id,
        status=TrialStatus.ABORTED,
        result=None,
        finished_at=NOW + timedelta(minutes=1),
        stop_reason="cancelled before evaluation",
    )

    finishes = [
        record.payload
        for record in ledger.read_records()
        if record.record_type.value == "trial_finished"
    ]
    assert finishes[0].touched_lockbox is True
    assert finishes[0].consumed_window is not None
    assert finishes[1].touched_lockbox is False
    assert finishes[1].consumed_window is None


def test_non_successful_lockbox_result_still_consumes_the_window(
    tmp_path: Path,
) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    lockbox = window().model_copy(update={"role": EvaluationRole.LOCKBOX})
    rejected = trial("trial-rejected", evaluation_window=lockbox)
    later = trial("trial-later", evaluation_window=lockbox)
    ledger.register(rejected)
    ledger.register(later)

    with pytest.raises(TrialLedgerError, match="atomic lockbox"):
        ledger.finish(
            rejected.trial_id,
            status=TrialStatus.REJECTED,
            result={"brier_skill": 0.02},
            finished_at=NOW,
            stop_reason="promotion gate failed",
        )
    ledger.finish(
        rejected.trial_id,
        status=TrialStatus.REJECTED,
        result={"brier_skill": 0.02},
        finished_at=NOW,
        stop_reason="promotion gate failed",
        lockbox_window=lockbox,
        consumed_at=NOW,
    )
    with pytest.raises(TrialLedgerError, match="already consumed"):
        ledger.finish(
            later.trial_id,
            status=TrialStatus.SUCCEEDED,
            result={"brier_skill": 0.03},
            finished_at=NOW + timedelta(minutes=1),
            stop_reason=None,
            lockbox_window=lockbox,
            consumed_at=NOW + timedelta(minutes=1),
        )


@pytest.mark.parametrize("invalid_value", [None, "good", True])
def test_success_metrics_must_be_finite_numbers(
    tmp_path: Path,
    invalid_value: object,
) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    registered = trial()
    ledger.register(registered)

    with pytest.raises(TrialLedgerError, match="contract|finite|numeric"):
        ledger.finish(
            registered.trial_id,
            status=TrialStatus.SUCCEEDED,
            result={"brier_skill": invalid_value},  # type: ignore[dict-item]
            finished_at=NOW,
            stop_reason=None,
        )


def test_success_result_must_match_preregistered_metrics(tmp_path: Path) -> None:
    ledger = JsonlTrialLedger(tmp_path / "trials.jsonl")
    registered = trial().model_copy(update={"secondary_metrics": ("pr_auc",)})
    ledger.register(registered)

    with pytest.raises(TrialLedgerError, match="primary metric"):
        ledger.finish(
            registered.trial_id,
            status=TrialStatus.SUCCEEDED,
            result={"pr_auc": 0.5},
            finished_at=NOW,
            stop_reason=None,
        )
    with pytest.raises(TrialLedgerError, match="declared metric"):
        ledger.finish(
            registered.trial_id,
            status=TrialStatus.SUCCEEDED,
            result={"brier_skill": 0.02},
            finished_at=NOW,
            stop_reason=None,
        )
    with pytest.raises(TrialLedgerError, match="unregistered metric"):
        ledger.finish(
            registered.trial_id,
            status=TrialStatus.SUCCEEDED,
            result={"brier_skill": 0.02, "after_the_fact": 1.0},
            finished_at=NOW,
            stop_reason=None,
        )
