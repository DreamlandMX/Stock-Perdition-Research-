"""Multi-reason abstention and confidence policy for M4 forecasts."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import to_jsonable_python

from stock_prediction.contracts.market_data import VersionId
from stock_prediction.contracts.research import Horizon, Sha256
from stock_prediction.modeling.contracts import (
    EvaluationRole,
    EvaluationWindow,
    SampleKey,
    TaskName,
)


class RejectionReason(StrEnum):
    NO_CHAMPION_CANDIDATE = "no_champion_candidate"
    CORPORATE_ACTION_UNRESOLVED = "corporate_action_unresolved"
    FEATURE_ARTIFACT_MISMATCH = "feature_artifact_mismatch"
    STALE_DATA = "stale_data"
    DATA_QUALITY = "data_quality"
    SCHEMA_MISMATCH = "schema_mismatch"
    OOD = "out_of_distribution"
    INTERVAL_TOO_WIDE = "interval_too_wide"
    POOR_CALIBRATION = "poor_calibration"
    INSUFFICIENT_CALIBRATION = "insufficient_calibration"
    MODEL_DISAGREEMENT = "model_disagreement"
    INSUFFICIENT_SIMILAR_CASES = "insufficient_similar_cases"


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNAVAILABLE = "unavailable"


class ConfidencePolicy(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    max_data_age_seconds: int = Field(ge=0)
    max_missing_rate: float = Field(ge=0, le=1)
    max_ood_score: float = Field(ge=0)
    max_interval_width: float = Field(gt=0)
    max_calibration_error: float = Field(ge=0, le=1)
    min_calibration_samples: int = Field(ge=1)
    max_model_disagreement: float = Field(ge=0, le=1)
    min_similar_cases: int = Field(ge=0)
    high_confidence_data_age_seconds: int = Field(ge=0)
    high_confidence_missing_rate: float = Field(ge=0, le=1)
    high_confidence_ood_score: float = Field(ge=0)
    high_confidence_interval_width: float = Field(gt=0)
    high_confidence_calibration_error: float = Field(ge=0, le=1)
    high_confidence_model_disagreement: float = Field(ge=0, le=1)
    high_confidence_similar_cases: int = Field(ge=0)
    high_confidence_calibration_samples: int = Field(ge=1)
    policy_version: VersionId
    policy_hash: Sha256

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        if self.high_confidence_data_age_seconds > self.max_data_age_seconds:
            raise ValueError("high-confidence freshness threshold exceeds gate threshold")
        if self.high_confidence_missing_rate > self.max_missing_rate:
            raise ValueError("high-confidence missing-rate threshold exceeds gate threshold")
        if self.high_confidence_ood_score > self.max_ood_score:
            raise ValueError("high-confidence OOD threshold exceeds gate threshold")
        if self.high_confidence_interval_width > self.max_interval_width:
            raise ValueError("high-confidence interval threshold exceeds gate threshold")
        if self.high_confidence_calibration_error > self.max_calibration_error:
            raise ValueError("high-confidence calibration threshold exceeds gate threshold")
        if self.high_confidence_model_disagreement > self.max_model_disagreement:
            raise ValueError("high-confidence disagreement threshold exceeds gate threshold")
        if self.high_confidence_similar_cases < self.min_similar_cases:
            raise ValueError("high-confidence case count is below gate minimum")
        if self.high_confidence_calibration_samples < self.min_calibration_samples:
            raise ValueError("high-confidence calibration count is below gate minimum")
        if self.policy_hash != _content_hash(
            self.model_dump(mode="python", exclude={"policy_hash"})
        ):
            raise ValueError("confidence policy hash is invalid")
        return self

    @classmethod
    def create(cls, **values: Any) -> "ConfidencePolicy":
        payload = dict(values)
        if "policy_hash" in payload:
            raise ValueError("policy_hash is assigned by ConfidencePolicy.create")
        payload["policy_hash"] = _content_hash(payload)
        return cls.model_validate(payload, strict=True)


class OosWindowEvidence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    window_hash: Sha256
    evaluation_window: EvaluationWindow
    sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    sample_keys_hash: Sha256

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.evaluation_window.role not in {
            EvaluationRole.OUTER_TEST,
            EvaluationRole.FORWARD_SHADOW,
        }:
            raise ValueError("high-confidence evidence requires independent OOS windows")
        if len(set(self.sample_keys)) != len(self.sample_keys):
            raise ValueError("OOS window sample keys must be unique")
        if any(
            not self.evaluation_window.start_session
            <= key[1].date()
            <= self.evaluation_window.end_session
            for key in self.sample_keys
        ):
            raise ValueError("OOS sample falls outside evaluation window")
        if self.window_hash != _content_hash(self.evaluation_window):
            raise ValueError("OOS evaluation window hash is invalid")
        if self.sample_keys_hash != _sample_keys_hash(self.sample_keys):
            raise ValueError("OOS sample key hash is invalid")
        return self

    @classmethod
    def register(
        cls,
        *,
        evaluation_window: EvaluationWindow,
        sample_keys: tuple[SampleKey, ...],
    ) -> "OosWindowEvidence":
        return cls(
            window_hash=_content_hash(evaluation_window),
            evaluation_window=evaluation_window,
            sample_keys=sample_keys,
            sample_keys_hash=_sample_keys_hash(sample_keys),
        )


class HighConfidenceOosEvidence(BaseModel):
    """Independent-window evidence that the high-confidence cohort adds value."""

    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    task: TaskName
    horizon: Horizon
    model_artifact_hash: Sha256
    calibration_artifact_hash: Sha256
    policy_hash: Sha256
    evaluation_windows: tuple[OosWindowEvidence, ...] = Field(min_length=2)
    brier_skill_improvement: float
    ranking_ic_improvement: float
    interval_width_reduction_at_same_coverage: float
    target_coverage: float = Field(gt=0, lt=1)
    empirical_coverage_delta: float = Field(ge=-1, le=1)
    sample_count: int = Field(ge=30)
    evidence_hash: Sha256

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        window_hashes = tuple(window.window_hash for window in self.evaluation_windows)
        if len(set(window_hashes)) != len(window_hashes):
            raise ValueError("high-confidence OOS windows must be unique")
        sample_hashes = tuple(
            window.sample_keys_hash for window in self.evaluation_windows
        )
        if len(set(sample_hashes)) != len(sample_hashes):
            raise ValueError("high-confidence OOS sample sets must be unique")
        seen_keys: set[SampleKey] = set()
        for window in self.evaluation_windows:
            if any(key[2] != self.horizon for key in window.sample_keys):
                raise ValueError("high-confidence OOS sample horizon is inconsistent")
            overlap = seen_keys & set(window.sample_keys)
            if overlap:
                raise ValueError("high-confidence OOS sample sets must be disjoint")
            seen_keys.update(window.sample_keys)
        if self.sample_count != len(seen_keys):
            raise ValueError(
                "high-confidence sample count must match exact OOS sample keys"
            )
        width_is_better = (
            self.interval_width_reduction_at_same_coverage > 0
            and abs(self.empirical_coverage_delta) <= 0.05
        )
        if (
            self.brier_skill_improvement <= 0
            and self.ranking_ic_improvement <= 0
            and not width_is_better
        ):
            raise ValueError("high-confidence cohort has no OOS improvement")
        if self.evidence_hash != _content_hash(
            self.model_dump(mode="python", exclude={"evidence_hash"})
        ):
            raise ValueError("high-confidence OOS evidence hash is invalid")
        return self

    @classmethod
    def create(cls, **values: Any) -> "HighConfidenceOosEvidence":
        payload = dict(values)
        if "evidence_hash" in payload:
            raise ValueError("evidence_hash is assigned by create")
        payload["evidence_hash"] = _content_hash(payload)
        return cls.model_validate(payload, strict=True)


class ConfidenceEvidence(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    data_age_seconds: int = Field(ge=0)
    missing_rate: float = Field(ge=0, le=1)
    schema_match: bool
    ood_score: float = Field(ge=0)
    interval_width: float = Field(ge=0)
    calibration_error: float = Field(ge=0, le=1)
    calibration_sample_count: int = Field(ge=0)
    model_disagreement: float = Field(ge=0, le=1)
    similar_case_count: int = Field(ge=0)
    data_health_version: VersionId
    feature_set_version: VersionId
    model_version: VersionId
    calibration_version: VersionId
    task: TaskName
    horizon: Horizon
    model_artifact_hash: Sha256
    calibration_artifact_hash: Sha256
    high_confidence_oos: HighConfidenceOosEvidence | None


class ConfidenceDecision(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    level: ConfidenceLevel
    abstain: bool
    reasons: tuple[RejectionReason, ...]
    policy_version: VersionId
    data_health_version: VersionId
    feature_set_version: VersionId
    model_version: VersionId
    calibration_version: VersionId
    policy_hash: Sha256
    high_confidence_oos_evidence_hash: Sha256 | None

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.abstain != bool(self.reasons):
            raise ValueError("abstention and rejection reasons must agree")
        if self.abstain != (self.level is ConfidenceLevel.UNAVAILABLE):
            raise ValueError("abstention must use unavailable confidence")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("rejection reasons must be unique")
        return self


class ConfidenceGate:
    __slots__ = ("__expected_policy_hash", "__policy")

    def __init__(
        self, policy: ConfidencePolicy, *, expected_policy_hash: str
    ) -> None:
        checked = ConfidencePolicy.model_validate(
            policy.model_dump(mode="python"), strict=True
        )
        if checked.policy_hash != expected_policy_hash:
            raise ValueError("confidence policy does not match registered identity")
        self.__policy = checked
        self.__expected_policy_hash = expected_policy_hash

    @property
    def policy(self) -> ConfidencePolicy:
        return self.__policy

    def evaluate(
        self,
        evidence: ConfidenceEvidence,
        *,
        expected_oos_evidence_hash: str | None,
    ) -> ConfidenceDecision:
        checked = ConfidenceEvidence.model_validate(
            evidence.model_dump(mode="python"), strict=True
        )
        policy = ConfidencePolicy.model_validate(
            self.__policy.model_dump(mode="python"), strict=True
        )
        if policy.policy_hash != self.__expected_policy_hash:
            raise ValueError("confidence policy does not match registered identity")
        if checked.high_confidence_oos is not None:
            oos = HighConfidenceOosEvidence.model_validate(
                checked.high_confidence_oos.model_dump(mode="python"), strict=True
            )
            if oos.evidence_hash != expected_oos_evidence_hash:
                raise ValueError(
                    "high-confidence OOS evidence does not match registered identity"
                )
            if (
                oos.task != checked.task
                or oos.horizon != checked.horizon
                or oos.model_artifact_hash != checked.model_artifact_hash
                or oos.calibration_artifact_hash
                != checked.calibration_artifact_hash
                or oos.policy_hash != policy.policy_hash
            ):
                raise ValueError("high-confidence OOS evidence identity is inconsistent")
        elif expected_oos_evidence_hash is not None:
            raise ValueError("unexpected high-confidence OOS evidence identity")
        reasons: list[RejectionReason] = []
        if checked.data_age_seconds > policy.max_data_age_seconds:
            reasons.append(RejectionReason.STALE_DATA)
        if checked.missing_rate > policy.max_missing_rate:
            reasons.append(RejectionReason.DATA_QUALITY)
        if not checked.schema_match:
            reasons.append(RejectionReason.SCHEMA_MISMATCH)
        if checked.ood_score > policy.max_ood_score:
            reasons.append(RejectionReason.OOD)
        if checked.interval_width > policy.max_interval_width:
            reasons.append(RejectionReason.INTERVAL_TOO_WIDE)
        if checked.calibration_error > policy.max_calibration_error:
            reasons.append(RejectionReason.POOR_CALIBRATION)
        if checked.calibration_sample_count < policy.min_calibration_samples:
            reasons.append(RejectionReason.INSUFFICIENT_CALIBRATION)
        if checked.model_disagreement > policy.max_model_disagreement:
            reasons.append(RejectionReason.MODEL_DISAGREEMENT)
        if checked.similar_case_count < policy.min_similar_cases:
            reasons.append(RejectionReason.INSUFFICIENT_SIMILAR_CASES)

        if reasons:
            level = ConfidenceLevel.UNAVAILABLE
        elif (
            checked.data_age_seconds <= policy.high_confidence_data_age_seconds
            and checked.missing_rate <= policy.high_confidence_missing_rate
            and checked.ood_score <= policy.high_confidence_ood_score
            and checked.interval_width <= policy.high_confidence_interval_width
            and checked.calibration_error
            <= policy.high_confidence_calibration_error
            and checked.model_disagreement
            <= policy.high_confidence_model_disagreement
            and checked.similar_case_count
            >= policy.high_confidence_similar_cases
            and checked.calibration_sample_count
            >= policy.high_confidence_calibration_samples
            and checked.high_confidence_oos is not None
        ):
            level = ConfidenceLevel.HIGH
        elif (
            checked.ood_score <= (policy.max_ood_score + policy.high_confidence_ood_score) / 2
            and checked.interval_width
            <= (policy.max_interval_width + policy.high_confidence_interval_width) / 2
        ):
            level = ConfidenceLevel.MEDIUM
        else:
            level = ConfidenceLevel.LOW
        return ConfidenceDecision(
            level=level,
            abstain=bool(reasons),
            reasons=tuple(reasons),
            policy_version=policy.policy_version,
            data_health_version=checked.data_health_version,
            feature_set_version=checked.feature_set_version,
            model_version=checked.model_version,
            calibration_version=checked.calibration_version,
            policy_hash=policy.policy_hash,
            high_confidence_oos_evidence_hash=(
                checked.high_confidence_oos.evidence_hash
                if checked.high_confidence_oos is not None
                else None
            ),
        )


def _content_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            to_jsonable_python(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sample_keys_hash(sample_keys: tuple[SampleKey, ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            tuple((key[0], key[1].isoformat(), key[2]) for key in sample_keys),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
