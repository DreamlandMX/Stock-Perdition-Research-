from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stock_prediction.modeling.confidence import (
    ConfidenceEvidence,
    ConfidenceGate,
    ConfidenceLevel,
    ConfidencePolicy,
    HighConfidenceOosEvidence,
    OosWindowEvidence,
    RejectionReason,
)
from stock_prediction.modeling.contracts import (
    EvaluationRole,
    EvaluationScope,
    EvaluationWindow,
    TaskName,
)


def test_rejection_vocabulary_includes_serving_hard_gates() -> None:
    assert RejectionReason.NO_CHAMPION_CANDIDATE.value == "no_champion_candidate"
    assert (
        RejectionReason.CORPORATE_ACTION_UNRESOLVED.value
        == "corporate_action_unresolved"
    )
    assert (
        RejectionReason.FEATURE_ARTIFACT_MISMATCH.value == "feature_artifact_mismatch"
    )


def _policy() -> ConfidencePolicy:
    return ConfidencePolicy.create(
        max_data_age_seconds=900,
        max_missing_rate=0.05,
        max_ood_score=3.0,
        max_interval_width=0.20,
        max_calibration_error=0.08,
        min_calibration_samples=100,
        max_model_disagreement=0.15,
        min_similar_cases=10,
        high_confidence_data_age_seconds=300,
        high_confidence_missing_rate=0.01,
        high_confidence_ood_score=1.0,
        high_confidence_interval_width=0.08,
        high_confidence_calibration_error=0.03,
        high_confidence_model_disagreement=0.05,
        high_confidence_similar_cases=30,
        high_confidence_calibration_samples=300,
        policy_version="confidence-v1",
    )


def _evidence(**updates: object) -> ConfidenceEvidence:
    policy = _policy()
    windows = []
    for index, role in enumerate(
        (EvaluationRole.OUTER_TEST, EvaluationRole.FORWARD_SHADOW)
    ):
        start = datetime(2023 + index, 1, 3, 21, tzinfo=UTC)
        keys = tuple(
            ("US.AAPL", start + timedelta(days=offset), 5)
            for offset in range(15)
        )
        window = EvaluationWindow(
            window_id=f"split-v1.oos-{index}",
            role=role,
            scope=EvaluationScope.SYNTHETIC_OFFLINE,
            start_session=keys[0][1].date(),
            end_session=keys[-1][1].date(),
            split_version="split-v1",
            dataset_input_hash="a" * 64,
            calendar_hash="b" * 64,
            consumed_at=None,
            consumption_reason=None,
        )
        windows.append(
            OosWindowEvidence.register(
                evaluation_window=window,
                sample_keys=keys,
            )
        )
    oos = HighConfidenceOosEvidence.create(
        task=TaskName.FORECAST_DIRECTION,
        horizon=5,
        model_artifact_hash="d" * 64,
        calibration_artifact_hash="e" * 64,
        policy_hash=policy.policy_hash,
        evaluation_windows=tuple(windows),
        brier_skill_improvement=0.03,
        ranking_ic_improvement=0.01,
        interval_width_reduction_at_same_coverage=0.02,
        target_coverage=0.80,
        empirical_coverage_delta=-0.01,
        sample_count=30,
    )
    values: dict[str, object] = {
        "data_age_seconds": 60,
        "missing_rate": 0.0,
        "schema_match": True,
        "ood_score": 0.5,
        "interval_width": 0.05,
        "calibration_error": 0.02,
        "calibration_sample_count": 500,
        "model_disagreement": 0.03,
        "similar_case_count": 50,
        "data_health_version": "health-v1",
        "feature_set_version": "features-v1",
        "model_version": "model-v1",
        "calibration_version": "cal-v1",
        "task": TaskName.FORECAST_DIRECTION,
        "horizon": 5,
        "model_artifact_hash": "d" * 64,
        "calibration_artifact_hash": "e" * 64,
        "high_confidence_oos": oos,
    }
    values.update(updates)
    return ConfidenceEvidence.model_validate(values, strict=True)


def _gate() -> ConfidenceGate:
    policy = _policy()
    return ConfidenceGate(policy, expected_policy_hash=policy.policy_hash)


def _evaluate(evidence: ConfidenceEvidence):
    expected = (
        evidence.high_confidence_oos.evidence_hash
        if evidence.high_confidence_oos is not None
        else None
    )
    return _gate().evaluate(
        evidence, expected_oos_evidence_hash=expected
    )


def test_stale_or_schema_mismatch_always_abstains_with_all_reasons() -> None:
    decision = _evaluate(
        _evidence(
            data_age_seconds=3600,
            missing_rate=0.20,
            schema_match=False,
            ood_score=4.0,
        )
    )

    assert decision.abstain is True
    assert decision.level is ConfidenceLevel.UNAVAILABLE
    assert set(decision.reasons) >= {
        RejectionReason.STALE_DATA,
        RejectionReason.DATA_QUALITY,
        RejectionReason.SCHEMA_MISMATCH,
        RejectionReason.OOD,
    }


def test_high_confidence_requires_every_high_quality_condition() -> None:
    high = _evaluate(_evidence())
    medium = _evaluate(_evidence(interval_width=0.10))
    stale_for_high = _evaluate(
        _evidence(data_age_seconds=600)
    )

    assert high.abstain is False
    assert high.level is ConfidenceLevel.HIGH
    assert high.reasons == ()
    assert medium.abstain is False
    assert medium.level is ConfidenceLevel.MEDIUM
    assert stale_for_high.level is not ConfidenceLevel.HIGH


def test_interval_calibration_disagreement_and_cases_all_gate_prediction() -> None:
    decision = _evaluate(
        _evidence(
            interval_width=0.30,
            calibration_error=0.20,
            calibration_sample_count=20,
            model_disagreement=0.30,
            similar_case_count=3,
        )
    )

    assert decision.abstain is True
    assert set(decision.reasons) == {
        RejectionReason.INTERVAL_TOO_WIDE,
        RejectionReason.POOR_CALIBRATION,
        RejectionReason.INSUFFICIENT_CALIBRATION,
        RejectionReason.MODEL_DISAGREEMENT,
        RejectionReason.INSUFFICIENT_SIMILAR_CASES,
    }


def test_confidence_contract_rejects_non_finite_evidence() -> None:
    with pytest.raises(ValueError):
        _evidence(ood_score=float("nan"))


def test_policy_identity_is_immutable_and_revalidated() -> None:
    policy = _policy()
    gate = ConfidenceGate(policy, expected_policy_hash=policy.policy_hash)
    tampered = policy.model_copy(update={"max_data_age_seconds": 999999})

    with pytest.raises(AttributeError):
        gate.policy = tampered  # type: ignore[misc]
    with pytest.raises(ValueError, match="policy hash"):
        ConfidenceGate(tampered, expected_policy_hash=policy.policy_hash)


def test_high_confidence_requires_independent_oos_validation() -> None:
    decision = _evaluate(
        _evidence(
            high_confidence_oos=None,
        )
    )

    assert decision.abstain is False
    assert decision.level is not ConfidenceLevel.HIGH


def test_high_confidence_oos_identity_cannot_cross_models() -> None:
    evidence = _evidence()
    mismatched = evidence.model_copy(
        update={"model_artifact_hash": "f" * 64}
    )

    with pytest.raises(ValueError, match="identity is inconsistent"):
        _gate().evaluate(
            mismatched,
            expected_oos_evidence_hash=evidence.high_confidence_oos.evidence_hash
            if evidence.high_confidence_oos is not None
            else None,
        )
    with pytest.raises(ValueError, match="registered identity"):
        _gate().evaluate(
            evidence,
            expected_oos_evidence_hash="0" * 64,
        )


def test_high_confidence_windows_cannot_reuse_same_samples() -> None:
    evidence = _evidence()
    assert evidence.high_confidence_oos is not None
    first, second = evidence.high_confidence_oos.evaluation_windows
    overlapping_window = second.evaluation_window.model_copy(
        update={
            "start_session": first.evaluation_window.start_session,
            "end_session": first.evaluation_window.end_session,
        }
    )
    duplicate_samples = OosWindowEvidence.register(
        evaluation_window=overlapping_window,
        sample_keys=first.sample_keys,
    )
    values = evidence.high_confidence_oos.model_dump(
        mode="python", exclude={"evidence_hash"}
    )
    values["evaluation_windows"] = (first, duplicate_samples)

    with pytest.raises(ValueError, match="sample sets"):
        HighConfidenceOosEvidence.create(**values)

    wrong_count = evidence.high_confidence_oos.model_dump(
        mode="python", exclude={"evidence_hash"}
    )
    wrong_count["sample_count"] = 31
    with pytest.raises(ValueError, match="sample count"):
        HighConfidenceOosEvidence.create(**wrong_count)
