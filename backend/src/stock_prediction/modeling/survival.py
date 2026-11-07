"""Discrete-time survival utilities for range-exit forecasts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from typing import Literal, Self

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import to_jsonable_python

from stock_prediction.contracts.market_data import Identifier, VersionId
from stock_prediction.contracts.research import Sha256
from stock_prediction.modeling.contracts import TaskName
from stock_prediction.modeling.lightgbm_models import (
    ClassifierArtifact,
    ClassifierConfig,
    LightGbmClassifierTrainer,
)
from stock_prediction.modeling.training import ClassificationTrainingData, ModelInputError


class SurvivalInputError(ValueError):
    """Survival evidence is incomplete, inconsistent, or numerically invalid."""


class RangeExitObservation(BaseModel):
    """Observed portion of one range event, including right-censoring evidence."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    observation_id: Identifier
    observed_days: int = Field(ge=1, le=20)
    event_day: int | None = Field(default=None, ge=1, le=20)
    right_censored: bool

    @model_validator(mode="after")
    def validate_observation(self) -> "RangeExitObservation":
        if self.event_day is not None and self.event_day > self.observed_days:
            raise ValueError("event_day cannot follow the observed period")
        if self.right_censored and self.event_day is not None:
            raise ValueError("a right-censored observation cannot contain an event")
        return self


class PersonPeriodRow(BaseModel):
    """One at-risk day used by a discrete-time hazard model."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    observation_id: Identifier
    day: int = Field(ge=1, le=20)
    event: int = Field(ge=0, le=1)
    censored: bool


class DiscreteHazardConfig(BaseModel):
    """CPU-only configuration for 5/20-day range-exit hazards."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    horizon: Literal[5, 20]
    model_version: VersionId
    asset_group: Identifier
    seed: int = Field(default=17, ge=0)
    num_threads: int = Field(default=12, ge=1, le=16)
    num_boost_round: int = Field(default=200, ge=1, le=5000)
    learning_rate: float = Field(default=0.05, gt=0, le=1)
    num_leaves: int = Field(default=31, ge=2, le=1024)
    min_data_in_leaf: int = Field(default=20, ge=1)
    day_feature_name: Identifier = "day_at_risk"


class PersonPeriodEvidence(BaseModel):
    """Hashed output of the censor-aware person-period expansion."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    horizon: Literal[5, 20]
    rows: tuple[PersonPeriodRow, ...] = Field(min_length=1)
    rows_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_rows(self) -> Self:
        if self.rows_hash != _person_period_hash(self.rows):
            raise ValueError("person-period evidence hash is invalid")
        grouped: dict[str, list[PersonPeriodRow]] = {}
        for row in self.rows:
            grouped.setdefault(row.observation_id, []).append(row)
        for rows in grouped.values():
            if tuple(row.day for row in rows) != tuple(range(1, len(rows) + 1)):
                raise ValueError("person-period days must be consecutive from one")
            if sum(row.event for row in rows) > 1:
                raise ValueError("person-period observation has multiple events")
            if any(row.event for row in rows[:-1]):
                raise ValueError("person-period event must terminate an observation")
            if any(row.censored for row in rows[:-1]):
                raise ValueError("censor marker must terminate an observation")
            if rows[-1].event and rows[-1].censored:
                raise ValueError("event and censor marker are mutually exclusive")
            if rows[-1].day > self.horizon:
                raise ValueError("person-period observation exceeds horizon")
            if (
                rows[-1].day < self.horizon
                and not rows[-1].event
                and not rows[-1].censored
            ):
                raise ValueError(
                    "short person-period observation must end in event or censoring"
                )
            if rows[-1].day == self.horizon and rows[-1].censored:
                raise ValueError("horizon-complete observation is not right-censored")
        return self

    @classmethod
    def from_rows(
        cls, rows: Sequence[PersonPeriodRow], *, horizon: Literal[5, 20]
    ) -> "PersonPeriodEvidence":
        row_tuple = tuple(rows)
        return cls(
            horizon=horizon,
            rows=row_tuple,
            rows_hash=_person_period_hash(row_tuple),
        )


class ExitProbabilityCurve(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    horizon: Literal[5, 20]
    daily_hazards: tuple[float, ...]
    cumulative_exit_probabilities: tuple[float, ...]

    @model_validator(mode="after")
    def validate_curve(self) -> Self:
        if len(self.daily_hazards) != self.horizon or len(
            self.cumulative_exit_probabilities
        ) != self.horizon:
            raise ValueError("exit curve must contain exactly one value per horizon day")
        if any(not 0 <= value <= 1 for value in self.daily_hazards):
            raise ValueError("daily hazards must be probabilities")
        if tuple(sorted(self.cumulative_exit_probabilities)) != (
            self.cumulative_exit_probabilities
        ):
            raise ValueError("cumulative exit probability must be monotone")
        return self


class DiscreteHazardArtifact(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    horizon: Literal[5, 20]
    classifier: ClassifierArtifact
    person_period_hash: Sha256
    day_feature_name: Identifier
    artifact_hash: Sha256

    @model_validator(mode="after")
    def validate_classifier(self) -> Self:
        if self.classifier.task != TaskName.RANGE_EXIT_SURVIVAL:
            raise ValueError("hazard artifact must use the range-exit task")
        if self.classifier.horizon != self.horizon:
            raise ValueError("hazard artifact horizon is inconsistent")
        if self.classifier.class_order != ("no_exit", "exit"):
            raise ValueError("hazard artifact class order is invalid")
        if self.artifact_hash != _hazard_artifact_hash(
            self.model_dump(mode="python", exclude={"artifact_hash"})
        ):
            raise ValueError("hazard artifact hash is invalid")
        return self

    def predict_exit_curve(
        self,
        features: object,
        *,
        feature_names: Sequence[str],
        expected_artifact_hash: str,
    ) -> ExitProbabilityCurve:
        try:
            artifact = DiscreteHazardArtifact.model_validate(
                self.model_dump(mode="python"), strict=True
            )
        except ValidationError as error:
            raise SurvivalInputError("hazard artifact failed strict revalidation") from error
        if artifact.artifact_hash != expected_artifact_hash:
            raise SurvivalInputError("hazard artifact does not match registered identity")
        probabilities = artifact.classifier.predict_proba(
            features,
            feature_names=feature_names,
            expected_artifact_hash=artifact.classifier.artifact_hash,
        )
        if len(probabilities) != artifact.horizon:
            raise SurvivalInputError(
                "hazard inference requires one at-risk row for every horizon day"
            )
        matrix = np.asarray(features, dtype=np.float64)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        day_index = tuple(feature_names).index(artifact.day_feature_name)
        expected_days = np.arange(1, artifact.horizon + 1, dtype=np.float64)
        if not np.array_equal(matrix[:, day_index], expected_days):
            raise SurvivalInputError(
                "hazard inference day-at-risk must be ordered from one through horizon"
            )
        hazards = tuple(float(value) for value in probabilities[:, 1])
        return ExitProbabilityCurve(
            horizon=artifact.horizon,
            daily_hazards=hazards,
            cumulative_exit_probabilities=cumulative_incidence(hazards),
        )


class DiscreteHazardTrainer:
    def __init__(self, config: DiscreteHazardConfig) -> None:
        self.config = DiscreteHazardConfig.model_validate(
            config.model_dump(mode="python"), strict=True
        )

    def fit(
        self,
        data: ClassificationTrainingData,
        *,
        person_period_evidence: PersonPeriodEvidence,
    ) -> DiscreteHazardArtifact:
        try:
            evidence = PersonPeriodEvidence.model_validate(
                person_period_evidence.model_dump(mode="python"), strict=True
            )
        except (AttributeError, ValidationError) as error:
            raise SurvivalInputError("person-period evidence is invalid") from error
        if evidence.horizon != self.config.horizon:
            raise SurvivalInputError("person-period horizon does not match config")
        if len(evidence.rows) != len(data.labels):
            raise SurvivalInputError("person-period rows do not align with training data")
        if self.config.day_feature_name not in data.feature_names:
            raise SurvivalInputError("day-at-risk feature is missing")
        day_index = data.feature_names.index(self.config.day_feature_name)
        expected_days = tuple(float(row.day) for row in evidence.rows)
        actual_days = tuple(row[day_index] for row in data.feature_rows)
        if actual_days != expected_days:
            raise SurvivalInputError("training day-at-risk values do not match evidence")
        expected_labels = tuple(
            "exit" if row.event else "no_exit" for row in evidence.rows
        )
        if data.labels != expected_labels:
            raise SurvivalInputError("training labels do not match censor-aware evidence")
        try:
            classifier = LightGbmClassifierTrainer(
                ClassifierConfig(
                    task=TaskName.RANGE_EXIT_SURVIVAL,
                    horizon=self.config.horizon,
                    model_version=self.config.model_version,
                    asset_group=self.config.asset_group,
                    class_order=("no_exit", "exit"),
                    seed=self.config.seed,
                    num_threads=self.config.num_threads,
                    num_boost_round=self.config.num_boost_round,
                    learning_rate=self.config.learning_rate,
                    num_leaves=self.config.num_leaves,
                    min_data_in_leaf=self.config.min_data_in_leaf,
                )
            ).fit(data)
        except ModelInputError as error:
            raise SurvivalInputError("discrete hazard training failed") from error
        payload = {
            "horizon": self.config.horizon,
            "classifier": classifier,
            "person_period_hash": evidence.rows_hash,
            "day_feature_name": self.config.day_feature_name,
        }
        return DiscreteHazardArtifact.model_validate(
            {
                **payload,
                "artifact_hash": _hazard_artifact_hash(payload),
            },
            strict=True,
        )


def build_person_period_rows(
    observations: Sequence[RangeExitObservation], *, max_horizon: int
) -> tuple[PersonPeriodRow, ...]:
    """Expand event observations without inventing positives after censoring."""

    if max_horizon not in {5, 20}:
        raise SurvivalInputError("max_horizon must be 5 or 20 trading days")
    if not observations:
        raise SurvivalInputError("at least one observation is required")

    rows: list[PersonPeriodRow] = []
    identifiers: set[str] = set()
    for raw in observations:
        try:
            observation = RangeExitObservation.model_validate(
                raw.model_dump(mode="python"), strict=True
            )
        except (AttributeError, ValidationError) as error:
            raise SurvivalInputError("observation contract is invalid") from error
        if observation.observation_id in identifiers:
            raise SurvivalInputError("observation identifiers must be unique")
        identifiers.add(observation.observation_id)
        if observation.observed_days > max_horizon:
            raise SurvivalInputError("observation exceeds max_horizon")
        if observation.right_censored and observation.observed_days >= max_horizon:
            raise SurvivalInputError(
                "right-censored observation must end before max_horizon"
            )
        if (
            observation.event_day is None
            and not observation.right_censored
            and observation.observed_days != max_horizon
        ):
            raise SurvivalInputError(
                "short non-event observation must be explicitly right-censored"
            )
        terminal_day = observation.event_day or observation.observed_days
        for day in range(1, terminal_day + 1):
            rows.append(
                PersonPeriodRow(
                    observation_id=observation.observation_id,
                    day=day,
                    event=int(observation.event_day == day),
                    censored=bool(
                        observation.right_censored and day == terminal_day
                    ),
                )
            )
    return tuple(rows)


def cumulative_incidence(hazards: Sequence[float]) -> tuple[float, ...]:
    """Convert conditional daily hazards into cumulative exit probabilities."""

    if not hazards:
        raise SurvivalInputError("hazard sequence cannot be empty")
    survival = 1.0
    incidence: list[float] = []
    for value in hazards:
        hazard = float(value)
        if not math.isfinite(hazard) or not 0.0 <= hazard <= 1.0:
            raise SurvivalInputError("hazard values must be finite probabilities")
        survival *= 1.0 - hazard
        incidence.append(1.0 - survival)
    return tuple(incidence)


def _person_period_hash(rows: Sequence[PersonPeriodRow]) -> str:
    return hashlib.sha256(
        json.dumps(
            to_jsonable_python(tuple(rows)),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _hazard_artifact_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            to_jsonable_python(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
