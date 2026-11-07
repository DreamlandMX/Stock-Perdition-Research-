"""Strict M4 model, trial, and evaluation governance contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from enum import StrEnum
import hashlib
import json
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic_core import to_jsonable_python

from stock_prediction.contracts.market_data import Identifier, UsSymbol, VersionId
from stock_prediction.contracts.research import CalendarSource, Horizon, Sha256
from stock_prediction.contracts.time import UtcDatetime


Description = Annotated[str, StringConstraints(min_length=3, max_length=1024)]
SampleKey = tuple[UsSymbol, UtcDatetime, Horizon]


def hash_m7_sample_keys(sample_keys: Sequence[SampleKey]) -> str:
    return hashlib.sha256(
        json.dumps(
            [(key[0], key[1].isoformat(), key[2]) for key in sample_keys],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _canonical_hash(value: object) -> str:
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(
            to_jsonable_python(_hash_ready(value)),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _hash_ready(value: object) -> object:
    if isinstance(value, BaseModel):
        return _hash_ready(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {str(key): _hash_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_hash_ready(item) for item in value]
    return value


class TaskName(StrEnum):
    FORECAST_DIRECTION = "forecast_direction"
    EXECUTABLE_DIRECTION = "executable_direction"
    BREAKOUT = "breakout"
    RANGE_EXIT_SURVIVAL = "range_exit_survival"
    CAPITAL_GAIN_RETURN = "capital_gain_return"
    TOTAL_RETURN = "total_return"
    EXECUTABLE_RETURN = "executable_return"
    FORECAST_MFE = "forecast_mfe"
    FORECAST_MAE = "forecast_mae"
    EXECUTABLE_MFE = "executable_mfe"
    EXECUTABLE_MAE = "executable_mae"


class EvaluationRole(StrEnum):
    TRAIN = "train"
    CALIBRATION = "calibration"
    SELECTION = "selection"
    OUTER_TEST = "outer_test"
    LOCKBOX = "lockbox"
    FORWARD_SHADOW = "forward_shadow"


class EvaluationScope(StrEnum):
    PRODUCTION = "production"
    RESEARCH_ONLY = "research_only"
    SYNTHETIC_OFFLINE = "synthetic_offline"


class TrialStatus(StrEnum):
    REGISTERED = "registered"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"
    REJECTED = "rejected"


class ModelLifecycleStatus(StrEnum):
    DRAFT = "draft"
    TRAINED = "trained"
    EVALUATED = "evaluated"
    REJECTED = "rejected"
    CHALLENGER = "challenger"
    SHADOW = "shadow"
    CHAMPION = "champion"
    RETIRED = "retired"
    ROLLED_BACK = "rolled_back"


class M7PartitionRole(StrEnum):
    TRAIN = "train"
    SELECTION = "selection"
    CALIBRATION = "calibration"
    OUTER_TEST = "outer_test"


class M7FitComponent(StrEnum):
    SCALER = "scaler"
    IMPUTER = "imputer"
    WINSOR_LIMITS = "winsor_limits"
    CATEGORY_VOCABULARY = "category_vocabulary"
    LABEL_THRESHOLDS = "label_thresholds"
    CLASS_WEIGHTS = "class_weights"
    MODEL = "model"
    EARLY_STOPPING = "early_stopping"
    CALIBRATOR = "calibrator"
    BLEND_WEIGHTS = "blend_weights"


class WalkForwardSpec(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    train_years: int = Field(ge=1)
    validation_months: int = Field(ge=2)
    test_months: int = Field(ge=1)
    step_months: int = Field(ge=1)
    embargo_sessions: int = Field(ge=1)
    minimum_outer_folds: int = Field(ge=1)
    evaluation_scope: EvaluationScope

    @model_validator(mode="after")
    def validate_geometry(self) -> "WalkForwardSpec":
        if self.validation_months % 2 != 0:
            raise ValueError("validation_months must split into equal time halves")
        if self.step_months != self.test_months:
            raise ValueError(
                "step_months must equal test_months for independent outer tests"
            )
        return self


class OuterFold(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    fold_id: Identifier
    fold_index: int = Field(ge=0)
    split_version: VersionId
    dataset_input_hash: Sha256
    calendar_hash: Sha256
    train_start_session: date
    train_end_session: date
    calibration_start_session: date
    calibration_end_session: date
    selection_start_session: date
    selection_end_session: date
    test_start_session: date
    test_end_session: date
    train_sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    calibration_sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    selection_sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    test_sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    purged_sample_keys: tuple[SampleKey, ...]
    embargoed_sample_keys: tuple[SampleKey, ...]
    train_anchor_sessions: tuple[date, ...] = Field(min_length=1)
    calibration_anchor_sessions: tuple[date, ...] = Field(min_length=1)
    selection_anchor_sessions: tuple[date, ...] = Field(min_length=1)
    test_anchor_sessions: tuple[date, ...] = Field(min_length=1)
    embargo_sessions: tuple[date, ...] = Field(min_length=1)
    boundary_embargo_sessions: Mapping[Identifier, tuple[date, ...]]
    evaluation_window: EvaluationWindow
    fold_hash: Sha256 = "0" * 64

    @model_validator(mode="before")
    @classmethod
    def bind_fold_hash(cls, values: Any) -> Any:
        if not isinstance(values, Mapping):
            return values
        payload = dict(values)
        supplied = payload.pop("fold_hash", None)
        expected = _canonical_hash(payload)
        if supplied is not None and supplied != expected:
            raise ValueError("fold_hash does not match fold evidence")
        payload["fold_hash"] = expected
        return payload

    @model_validator(mode="after")
    def validate_fold(self) -> "OuterFold":
        expected_fold_hash = _canonical_hash(
            self.model_dump(mode="python", exclude={"fold_hash"})
        )
        if self.fold_hash != expected_fold_hash:
            raise ValueError("fold_hash does not match fold evidence")
        if not (
            self.train_start_session
            <= self.train_end_session
            < self.test_start_session
            <= self.test_end_session
        ):
            raise ValueError("train and test session boundaries are invalid")
        validation_boundaries = (
            self.calibration_start_session,
            self.calibration_end_session,
            self.selection_start_session,
            self.selection_end_session,
            self.test_start_session,
            self.test_end_session,
        )
        if tuple(sorted(validation_boundaries)) != validation_boundaries or len(
            set(validation_boundaries)
        ) != len(validation_boundaries):
            raise ValueError("validation and test boundaries must be strictly ordered")
        if self.train_start_session >= self.calibration_start_session:
            raise ValueError("training must begin before calibration")
        partitions = (
            self.train_sample_keys,
            self.calibration_sample_keys,
            self.selection_sample_keys,
            self.test_sample_keys,
        )
        flattened = tuple(key for partition in partitions for key in partition)
        if len(set(flattened)) != len(flattened):
            raise ValueError("fold sample partitions must be disjoint")
        partition_evidence = (
            (
                "train",
                self.train_sample_keys,
                self.train_anchor_sessions,
                self.train_start_session,
                self.train_end_session,
            ),
            (
                "calibration",
                self.calibration_sample_keys,
                self.calibration_anchor_sessions,
                self.calibration_start_session,
                self.calibration_end_session,
            ),
            (
                "selection",
                self.selection_sample_keys,
                self.selection_anchor_sessions,
                self.selection_start_session,
                self.selection_end_session,
            ),
            (
                "test",
                self.test_sample_keys,
                self.test_anchor_sessions,
                self.test_start_session,
                self.test_end_session,
            ),
        )
        for (
            name,
            keys,
            anchor_sessions,
            start_session,
            end_session,
        ) in partition_evidence:
            expected_sessions = tuple(sorted({key[1].date() for key in keys}))
            if anchor_sessions != expected_sessions:
                raise ValueError(f"{name} anchor sessions do not match sample keys")
            if not all(
                start_session <= session <= end_session for session in anchor_sessions
            ):
                raise ValueError(f"{name} anchors fall outside nominal boundaries")
        for name, keys in (
            ("purged", self.purged_sample_keys),
            ("embargoed", self.embargoed_sample_keys),
        ):
            if len(set(keys)) != len(keys):
                raise ValueError(f"{name} sample keys must be unique")
            if set(keys) & set(flattened):
                raise ValueError(f"{name} samples cannot remain in a partition")
        if set(self.purged_sample_keys) & set(self.embargoed_sample_keys):
            raise ValueError("purged and embargoed samples must be disjoint")
        expected_boundary_names = {
            "train_to_calibration",
            "calibration_to_selection",
            "selection_to_test",
        }
        actual_boundary_names = set(self.boundary_embargo_sessions)
        if not expected_boundary_names.issubset(actual_boundary_names) or not (
            actual_boundary_names
            <= expected_boundary_names
            | {
                "outer_test_embargo",
                "test_to_next_outer",
                "test_to_lockbox",
            }
        ):
            raise ValueError("fold boundary embargo names are incomplete")
        for sessions in self.boundary_embargo_sessions.values():
            if tuple(sorted(set(sessions))) != sessions:
                raise ValueError("boundary embargo sessions must be ordered and unique")
        outer_embargo = self.boundary_embargo_sessions.get(
            "outer_test_embargo",
            self.boundary_embargo_sessions["selection_to_test"],
        )
        if self.embargo_sessions != outer_embargo:
            raise ValueError("outer embargo summary does not match test boundary")
        for boundary_name, retained_sessions in (
            ("train_to_calibration", self.train_anchor_sessions),
            ("calibration_to_selection", self.calibration_anchor_sessions),
            ("selection_to_test", self.selection_anchor_sessions),
        ):
            if set(self.boundary_embargo_sessions[boundary_name]) & set(
                retained_sessions
            ):
                raise ValueError("embargo sessions cannot retain prior-window anchors")
        if "outer_test_embargo" in self.boundary_embargo_sessions and set(
            self.boundary_embargo_sessions["outer_test_embargo"]
        ) & (
            set(self.train_anchor_sessions)
            | set(self.calibration_anchor_sessions)
            | set(self.selection_anchor_sessions)
        ):
            raise ValueError("outer-test embargo cannot retain decision anchors")
        for boundary_name in ("test_to_next_outer", "test_to_lockbox"):
            if boundary_name in self.boundary_embargo_sessions and set(
                self.boundary_embargo_sessions[boundary_name]
            ) & set(self.test_anchor_sessions):
                raise ValueError("test-boundary embargo cannot retain test anchors")
        if self.evaluation_window.role is not EvaluationRole.OUTER_TEST:
            raise ValueError("outer fold evaluation window must be an outer test")
        if (
            self.evaluation_window.start_session != self.test_start_session
            or self.evaluation_window.end_session != self.test_end_session
            or self.evaluation_window.split_version != self.split_version
            or self.evaluation_window.dataset_input_hash != self.dataset_input_hash
            or self.evaluation_window.calendar_hash != self.calendar_hash
        ):
            raise ValueError("outer evaluation window evidence does not match fold")
        object.__setattr__(
            self,
            "boundary_embargo_sessions",
            MappingProxyType(dict(self.boundary_embargo_sessions)),
        )
        return self

    @field_validator("boundary_embargo_sessions", mode="before")
    @classmethod
    def thaw_boundary_embargo(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return dict(value)
        return value

    @field_serializer("boundary_embargo_sessions")
    def serialize_boundary_embargo(
        self, value: Mapping[str, tuple[date, ...]]
    ) -> dict[str, tuple[date, ...]]:
        return dict(value)


class SplitManifest(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    split_version: VersionId
    spec: WalkForwardSpec
    dataset_id: Identifier
    dataset_input_hash: Sha256
    dataset_revision: VersionId
    calendar_version: VersionId
    calendar_hash: Sha256
    calendar_source: CalendarSource
    calendar_sessions: tuple[date, ...] = Field(min_length=1)
    source_sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    required_horizons: tuple[Horizon, ...] = Field(min_length=1)
    outer_folds: tuple[OuterFold, ...] = Field(min_length=1)
    lockbox_window: EvaluationWindow | None
    lockbox_sample_keys: tuple[SampleKey, ...]
    manifest_hash: Sha256 = "0" * 64

    @model_validator(mode="before")
    @classmethod
    def bind_manifest_hash(cls, values: Any) -> Any:
        if not isinstance(values, Mapping):
            return values
        payload = dict(values)
        supplied = payload.pop("manifest_hash", None)
        expected = _canonical_hash(payload)
        if supplied is not None and supplied != expected:
            raise ValueError("manifest_hash does not match split evidence")
        payload["manifest_hash"] = expected
        return payload

    @model_validator(mode="after")
    def validate_manifest(self) -> "SplitManifest":
        expected_manifest_hash = _canonical_hash(
            self.model_dump(mode="python", exclude={"manifest_hash"})
        )
        if self.manifest_hash != expected_manifest_hash:
            raise ValueError("manifest_hash does not match split evidence")
        if len(self.outer_folds) < self.spec.minimum_outer_folds:
            raise ValueError("manifest has fewer than minimum outer folds")
        if tuple(sorted(set(self.calendar_sessions))) != self.calendar_sessions:
            raise ValueError("manifest calendar sessions must be ordered and unique")
        if len(set(self.source_sample_keys)) != len(self.source_sample_keys):
            raise ValueError("manifest source sample keys must be unique")
        if (
            tuple(
                sorted(
                    self.source_sample_keys,
                    key=lambda key: (key[1], key[0], key[2]),
                )
            )
            != self.source_sample_keys
        ):
            raise ValueError("manifest source sample keys must use canonical order")
        source_horizons = tuple(sorted({key[2] for key in self.source_sample_keys}))
        if self.required_horizons != source_horizons:
            raise ValueError("required horizons do not match source sample keys")
        calendar_set = set(self.calendar_sessions)
        if any(key[1].date() not in calendar_set for key in self.source_sample_keys):
            raise ValueError("source sample key falls outside manifest calendar")
        source_key_set = set(self.source_sample_keys)
        if tuple(fold.fold_index for fold in self.outer_folds) != tuple(
            range(len(self.outer_folds))
        ):
            raise ValueError("outer fold indexes must be contiguous")
        reference_fold = self.outer_folds[0]
        fixed_decision_fields = (
            "train_start_session",
            "train_end_session",
            "calibration_start_session",
            "calibration_end_session",
            "selection_start_session",
            "selection_end_session",
            "train_sample_keys",
            "calibration_sample_keys",
            "selection_sample_keys",
            "train_anchor_sessions",
            "calibration_anchor_sessions",
            "selection_anchor_sessions",
        )
        prior_outer_keys: set[SampleKey] = set()
        for fold in self.outer_folds:
            if (
                fold.split_version != self.split_version
                or fold.dataset_input_hash != self.dataset_input_hash
                or fold.calendar_hash != self.calendar_hash
            ):
                raise ValueError("outer fold evidence does not match manifest")
            fold_keys = (
                set(fold.train_sample_keys)
                | set(fold.calibration_sample_keys)
                | set(fold.selection_sample_keys)
                | set(fold.test_sample_keys)
                | set(fold.purged_sample_keys)
                | set(fold.embargoed_sample_keys)
            )
            if not fold_keys.issubset(source_key_set):
                raise ValueError("outer fold references a non-source sample key")
            removed_keys = set(fold.purged_sample_keys) | set(
                fold.embargoed_sample_keys
            )
            nominal_partitions = (
                (
                    "train",
                    fold.train_start_session,
                    fold.train_end_session,
                    set(fold.train_sample_keys),
                ),
                (
                    "calibration",
                    fold.calibration_start_session,
                    fold.calibration_end_session,
                    set(fold.calibration_sample_keys),
                ),
                (
                    "selection",
                    fold.selection_start_session,
                    fold.selection_end_session,
                    set(fold.selection_sample_keys),
                ),
                (
                    "test",
                    fold.test_start_session,
                    fold.test_end_session,
                    set(fold.test_sample_keys),
                ),
            )
            nominal_source_union: set[SampleKey] = set()
            for (
                partition_name,
                start_session,
                end_session,
                retained_keys,
            ) in nominal_partitions:
                nominal_source_keys = {
                    key
                    for key in self.source_sample_keys
                    if start_session <= key[1].date() <= end_session
                }
                nominal_source_union.update(nominal_source_keys)
                if retained_keys | (removed_keys & nominal_source_keys) != (
                    nominal_source_keys
                ):
                    raise ValueError(
                        f"{partition_name} source-key classification is incomplete"
                    )
            if removed_keys - nominal_source_union:
                raise ValueError("removed sample key lies outside nominal partitions")
            if any(
                getattr(fold, field_name) != getattr(reference_fold, field_name)
                for field_name in fixed_decision_fields
            ):
                raise ValueError(
                    "training, calibration, and selection evidence must remain fixed"
                )
            if fold.evaluation_window.scope is not self.spec.evaluation_scope:
                raise ValueError("outer fold scope does not match manifest")
            if any(
                len(sessions) != self.spec.embargo_sessions
                for sessions in fold.boundary_embargo_sessions.values()
            ):
                raise ValueError(
                    "outer fold embargo count does not match specification"
                )
            forbidden_reuse = prior_outer_keys & (
                set(fold.train_sample_keys)
                | set(fold.calibration_sample_keys)
                | set(fold.selection_sample_keys)
                | set(fold.test_sample_keys)
            )
            if forbidden_reuse:
                raise ValueError(
                    "prior outer-test samples cannot enter later fold partitions"
                )
            prior_outer_keys.update(fold.test_sample_keys)
        for previous, following in zip(
            self.outer_folds,
            self.outer_folds[1:],
            strict=False,
        ):
            if previous.test_end_session >= following.test_start_session:
                raise ValueError("outer test windows must be independent and ordered")
            if "test_to_next_outer" not in previous.boundary_embargo_sessions:
                raise ValueError("outer fold lacks next-test embargo evidence")
        if "test_to_next_outer" in self.outer_folds[-1].boundary_embargo_sessions:
            raise ValueError("final outer fold cannot reference a missing next test")
        if self.lockbox_window is not None:
            if self.lockbox_window.role is not EvaluationRole.LOCKBOX:
                raise ValueError("reserved window must be a lockbox")
            if self.lockbox_window.scope is not self.spec.evaluation_scope:
                raise ValueError("lockbox scope does not match manifest")
            if (
                self.lockbox_window.split_version != self.split_version
                or self.lockbox_window.dataset_input_hash != self.dataset_input_hash
                or self.lockbox_window.calendar_hash != self.calendar_hash
            ):
                raise ValueError("lockbox evidence does not match manifest")
            if (
                self.lockbox_window.start_session
                <= self.outer_folds[-1].test_end_session
            ):
                raise ValueError("lockbox must follow all outer test windows")
            if "test_to_lockbox" not in self.outer_folds[-1].boundary_embargo_sessions:
                raise ValueError("final outer fold lacks lockbox embargo evidence")
            expected_lockbox_keys = tuple(
                key
                for key in self.source_sample_keys
                if self.lockbox_window.start_session
                <= key[1].date()
                <= self.lockbox_window.end_session
            )
            if self.lockbox_sample_keys != expected_lockbox_keys:
                raise ValueError("lockbox sample keys are not the exact source subset")
            if not self.lockbox_sample_keys:
                raise ValueError("lockbox window requires exact sample keys")
            if len(set(self.lockbox_sample_keys)) != len(self.lockbox_sample_keys):
                raise ValueError("lockbox sample keys must be unique")
            if any(
                not self.lockbox_window.start_session
                <= key[1].date()
                <= self.lockbox_window.end_session
                for key in self.lockbox_sample_keys
            ):
                raise ValueError("lockbox sample key falls outside reserved window")
            expected_lockbox_sessions = tuple(
                session
                for session in self.calendar_sessions
                if self.lockbox_window.start_session
                <= session
                <= self.lockbox_window.end_session
            )
            for horizon in self.required_horizons:
                actual_sessions = tuple(
                    sorted(
                        {
                            key[1].date()
                            for key in self.lockbox_sample_keys
                            if key[2] == horizon
                        }
                    )
                )
                if actual_sessions != expected_lockbox_sessions:
                    raise ValueError(
                        "lockbox horizon coverage does not match nominal sessions"
                    )
        elif self.lockbox_sample_keys:
            raise ValueError("lockbox sample keys require a lockbox window")
        elif any(
            "test_to_lockbox" in fold.boundary_embargo_sessions
            for fold in self.outer_folds
        ):
            raise ValueError("lockbox embargo evidence requires a lockbox window")
        return self


class EvaluationWindow(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    window_id: Identifier
    role: EvaluationRole
    scope: EvaluationScope
    start_session: date
    end_session: date
    split_version: VersionId
    dataset_input_hash: Sha256
    calendar_hash: Sha256
    consumed_at: UtcDatetime | None
    consumption_reason: Identifier | None

    @model_validator(mode="after")
    def validate_window(self) -> "EvaluationWindow":
        if self.start_session > self.end_session:
            raise ValueError("evaluation window is reversed")
        if (self.consumed_at is None) != (self.consumption_reason is None):
            raise ValueError("evaluation window consumption fields are atomic")
        if self.consumed_at is not None and self.consumed_at.date() < self.end_session:
            raise ValueError("evaluation window cannot be consumed before window ends")
        return self


class BacktestTrial(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    trial_id: Identifier
    registered_at: UtcDatetime
    owner: Identifier
    hypothesis: Description
    task: TaskName
    horizon: Horizon
    model_version: VersionId
    feature_set_version: VersionId
    threshold_version: VersionId
    label_version: VersionId
    split_version: VersionId
    dataset_input_hash: Sha256
    calendar_hash: Sha256
    evaluation_window_id: Identifier
    evaluation_window: EvaluationWindow
    primary_metrics: tuple[Identifier, ...] = Field(min_length=1)
    secondary_metrics: tuple[Identifier, ...]
    frozen_parameters: Mapping[str, JsonValue]
    parent_trial_id: Identifier | None
    status: TrialStatus
    result: Mapping[Identifier, float] | None
    stop_reason: Description | None
    touched_lockbox: bool
    consumed_at: UtcDatetime | None

    @model_validator(mode="after")
    def validate_trial_state(self) -> "BacktestTrial":
        if len(set(self.primary_metrics)) != len(self.primary_metrics) or len(
            set(self.secondary_metrics)
        ) != len(self.secondary_metrics):
            raise ValueError("primary and secondary metric names must be unique")
        if set(self.primary_metrics) & set(self.secondary_metrics):
            raise ValueError("primary and secondary metrics must not overlap")
        if self.evaluation_window_id != self.evaluation_window.window_id:
            raise ValueError("trial and evaluation window IDs must match")
        if self.split_version != self.evaluation_window.split_version:
            raise ValueError("trial and evaluation window split versions must match")
        if self.dataset_input_hash != self.evaluation_window.dataset_input_hash:
            raise ValueError("trial and evaluation window dataset hashes must match")
        if self.calendar_hash != self.evaluation_window.calendar_hash:
            raise ValueError("trial and evaluation window calendar hashes must match")
        if (
            self.status is TrialStatus.REGISTERED
            and self.evaluation_window.consumed_at is not None
        ):
            raise ValueError(
                "registered trial cannot bind a consumed evaluation window"
            )
        if self.status is TrialStatus.REGISTERED and any(
            value is not None
            for value in (self.result, self.stop_reason, self.consumed_at)
        ):
            raise ValueError("registered trial cannot contain result state")
        if self.status is TrialStatus.REGISTERED and self.touched_lockbox:
            raise ValueError("registered trial cannot claim lockbox access")
        if self.touched_lockbox != (self.consumed_at is not None):
            raise ValueError("lockbox access and consumed_at must agree")
        if self.status is TrialStatus.SUCCEEDED and not self.result:
            raise ValueError("succeeded trial requires a result")
        if (
            self.status
            in {
                TrialStatus.FAILED,
                TrialStatus.ABORTED,
                TrialStatus.REJECTED,
            }
            and self.stop_reason is None
        ):
            raise ValueError("unsuccessful trial requires a stop reason")
        object.__setattr__(
            self,
            "frozen_parameters",
            _freeze_json(dict(self.frozen_parameters)),
        )
        if self.result is not None:
            object.__setattr__(self, "result", _freeze_json(dict(self.result)))
        return self

    @field_serializer("frozen_parameters", "result")
    def serialize_frozen_json(self, value: object) -> object:
        return _thaw_json(value)


class TrialFinished(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    trial_id: Identifier
    evaluation_window_id: Identifier
    status: TrialStatus
    result: Mapping[Identifier, float] | None
    stop_reason: Description | None
    finished_at: UtcDatetime
    touched_lockbox: bool
    consumed_at: UtcDatetime | None
    consumed_window: EvaluationWindow | None

    @model_validator(mode="after")
    def validate_finish(self) -> "TrialFinished":
        if self.status not in {
            TrialStatus.SUCCEEDED,
            TrialStatus.FAILED,
            TrialStatus.ABORTED,
            TrialStatus.REJECTED,
        }:
            raise ValueError("trial finish requires terminal status")
        if self.status is TrialStatus.SUCCEEDED and not self.result:
            raise ValueError("succeeded trial requires a result")
        if self.status is not TrialStatus.SUCCEEDED and self.stop_reason is None:
            raise ValueError("unsuccessful trial requires a stop reason")
        lockbox_evidence_present = (
            self.consumed_at is not None and self.consumed_window is not None
        )
        if self.touched_lockbox != lockbox_evidence_present:
            raise ValueError("lockbox access and consumed evidence must agree")
        if (self.consumed_at is None) != (self.consumed_window is None):
            raise ValueError("consumed_at and consumed_window must be atomic")
        if self.consumed_window is not None:
            if self.consumed_window.role is not EvaluationRole.LOCKBOX:
                raise ValueError("trial consumed window must be a lockbox")
            if self.consumed_window.consumed_at != self.consumed_at:
                raise ValueError("trial and window consumption times must match")
            if self.consumed_at is not None and self.consumed_at > self.finished_at:
                raise ValueError("lockbox consumption cannot occur after trial finish")
        if self.result is not None:
            object.__setattr__(self, "result", _freeze_json(dict(self.result)))
        return self

    @field_serializer("result")
    def serialize_result(self, value: object) -> object:
        return _thaw_json(value)


class ModelRun(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    run_id: Identifier
    model_version: VersionId
    task: TaskName
    horizon: Horizon
    asset_group: Identifier
    split_manifest_hash: Sha256
    dataset_input_hash: Sha256
    universe_version: VersionId
    feature_set_version: VersionId
    label_version: VersionId
    threshold_version: VersionId
    transformer_input_hash: Sha256
    parameters: Mapping[str, JsonValue]
    seed: int = Field(ge=0)
    dependencies: Mapping[Identifier, VersionId]
    fold_metrics: Mapping[Identifier, Mapping[Identifier, float]]
    aggregate_metrics: Mapping[Identifier, float]
    calibration_metrics: Mapping[Identifier, float]
    coverage_metrics: Mapping[Identifier, float]
    latency_ms: float = Field(ge=0)
    status: ModelLifecycleStatus
    created_at: UtcDatetime
    evaluation_window_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_run_state(self) -> "ModelRun":
        if len(set(self.evaluation_window_ids)) != len(self.evaluation_window_ids):
            raise ValueError("evaluation window IDs must be unique")
        if (
            self.status
            in {
                ModelLifecycleStatus.EVALUATED,
                ModelLifecycleStatus.REJECTED,
                ModelLifecycleStatus.CHALLENGER,
                ModelLifecycleStatus.SHADOW,
                ModelLifecycleStatus.CHAMPION,
                ModelLifecycleStatus.RETIRED,
                ModelLifecycleStatus.ROLLED_BACK,
            }
            and not self.fold_metrics
        ):
            raise ValueError("evaluated model run requires fold metrics")
        if (
            self.status
            in {
                ModelLifecycleStatus.EVALUATED,
                ModelLifecycleStatus.REJECTED,
                ModelLifecycleStatus.CHALLENGER,
                ModelLifecycleStatus.SHADOW,
                ModelLifecycleStatus.CHAMPION,
                ModelLifecycleStatus.RETIRED,
                ModelLifecycleStatus.ROLLED_BACK,
            }
            and not self.aggregate_metrics
        ):
            raise ValueError("evaluated model run requires aggregate metrics")
        if not set(self.fold_metrics).issubset(self.evaluation_window_ids):
            raise ValueError("fold metrics reference an unknown evaluation window")
        for name in (
            "parameters",
            "dependencies",
            "fold_metrics",
            "aggregate_metrics",
            "calibration_metrics",
            "coverage_metrics",
        ):
            object.__setattr__(self, name, _freeze_json(dict(getattr(self, name))))
        return self

    @field_serializer(
        "parameters",
        "dependencies",
        "fold_metrics",
        "aggregate_metrics",
        "calibration_metrics",
        "coverage_metrics",
    )
    def serialize_run_mapping(self, value: object) -> object:
        return _thaw_json(value)


class M7FoldPartitions(BaseModel):
    """Exact M7 validation/calibration geometry beside the legacy split contract."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    fold_id: Identifier
    split_version: VersionId
    dataset_input_hash: Sha256
    train_sessions: tuple[date, ...] = Field(min_length=1)
    selection_sessions: tuple[date, ...] = Field(min_length=116, max_length=116)
    calibration_purge_sessions: tuple[date, ...] = Field(min_length=20, max_length=20)
    calibration_sessions: tuple[date, ...] = Field(min_length=116, max_length=116)
    outer_embargo_sessions: tuple[date, ...] = Field(min_length=20, max_length=20)
    outer_test_sessions: tuple[date, ...] = Field(min_length=126, max_length=126)
    train_sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    selection_sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    calibration_sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    outer_test_sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    partition_hash: Sha256 = "0" * 64

    @model_validator(mode="before")
    @classmethod
    def bind_partition_hash(cls, values: Any) -> Any:
        if not isinstance(values, Mapping):
            return values
        payload = dict(values)
        supplied = payload.pop("partition_hash", None)
        expected = _canonical_hash(payload)
        if supplied is not None and supplied != expected:
            raise ValueError("partition_hash does not match M7 fold evidence")
        payload["partition_hash"] = expected
        return payload

    @model_validator(mode="after")
    def validate_geometry(self) -> "M7FoldPartitions":
        session_groups = (
            self.train_sessions,
            self.selection_sessions,
            self.calibration_purge_sessions,
            self.calibration_sessions,
            self.outer_embargo_sessions,
            self.outer_test_sessions,
        )
        if any(tuple(sorted(set(group))) != group for group in session_groups):
            raise ValueError("M7 partition sessions must be ordered and unique")
        flattened_sessions = tuple(
            session for group in session_groups for session in group
        )
        if tuple(sorted(flattened_sessions)) != flattened_sessions or len(
            set(flattened_sessions)
        ) != len(flattened_sessions):
            raise ValueError("M7 partition session groups overlap or are out of order")
        key_groups = (
            self.train_sample_keys,
            self.selection_sample_keys,
            self.calibration_sample_keys,
            self.outer_test_sample_keys,
        )
        flattened_keys = tuple(key for group in key_groups for key in group)
        if len(set(flattened_keys)) != len(flattened_keys):
            raise ValueError("M7 partition sample keys must be disjoint")
        for role, keys, sessions in (
            ("train", self.train_sample_keys, self.train_sessions),
            ("selection", self.selection_sample_keys, self.selection_sessions),
            ("calibration", self.calibration_sample_keys, self.calibration_sessions),
            ("outer_test", self.outer_test_sample_keys, self.outer_test_sessions),
        ):
            if any(key[1].date() not in set(sessions) for key in keys):
                raise ValueError(f"M7 {role} sample key is outside its sessions")
        expected_hash = _canonical_hash(
            self.model_dump(mode="python", exclude={"partition_hash"})
        )
        if self.partition_hash != expected_hash:
            raise ValueError("partition_hash does not match M7 fold evidence")
        return self


_M7_FIT_ROLES: dict[M7FitComponent, M7PartitionRole] = {
    M7FitComponent.SCALER: M7PartitionRole.TRAIN,
    M7FitComponent.IMPUTER: M7PartitionRole.TRAIN,
    M7FitComponent.WINSOR_LIMITS: M7PartitionRole.TRAIN,
    M7FitComponent.CATEGORY_VOCABULARY: M7PartitionRole.TRAIN,
    M7FitComponent.LABEL_THRESHOLDS: M7PartitionRole.TRAIN,
    M7FitComponent.CLASS_WEIGHTS: M7PartitionRole.TRAIN,
    M7FitComponent.MODEL: M7PartitionRole.TRAIN,
    M7FitComponent.EARLY_STOPPING: M7PartitionRole.SELECTION,
    M7FitComponent.CALIBRATOR: M7PartitionRole.CALIBRATION,
    M7FitComponent.BLEND_WEIGHTS: M7PartitionRole.CALIBRATION,
}


class M7FittedObjectEvidence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    component: M7FitComponent
    fit_role: M7PartitionRole
    partition_hash: Sha256
    sample_keys: tuple[SampleKey, ...] = Field(min_length=1)
    sample_keys_hash: Sha256

    @model_validator(mode="after")
    def validate_fit_role_and_hash(self) -> "M7FittedObjectEvidence":
        if self.fit_role is not _M7_FIT_ROLES[self.component]:
            raise ValueError("M7 fitted component uses a forbidden partition")
        expected = hash_m7_sample_keys(self.sample_keys)
        if self.sample_keys_hash != expected:
            raise ValueError("M7 fitted sample key hash does not match")
        return self


class M7SequenceWindowEvidence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    partition_hash: Sha256
    sample_key: SampleKey
    sequence_length: int = Field(ge=1)
    input_sessions: tuple[date, ...] = Field(min_length=1)
    window_hash: Sha256 = "0" * 64

    @model_validator(mode="before")
    @classmethod
    def bind_window_hash(cls, values: Any) -> Any:
        if not isinstance(values, Mapping):
            return values
        payload = dict(values)
        supplied = payload.pop("window_hash", None)
        expected = _canonical_hash(payload)
        if supplied is not None and supplied != expected:
            raise ValueError("M7 sequence window hash does not match")
        payload["window_hash"] = expected
        return payload

    @model_validator(mode="after")
    def validate_window(self) -> "M7SequenceWindowEvidence":
        if len(self.input_sessions) != self.sequence_length:
            raise ValueError("M7 sequence session count does not match length")
        if tuple(sorted(set(self.input_sessions))) != self.input_sessions:
            raise ValueError("M7 sequence sessions must be ordered and unique")
        if self.input_sessions[-1] != self.sample_key[1].date():
            raise ValueError("M7 sequence must end at its anchor")
        expected = _canonical_hash(
            self.model_dump(mode="python", exclude={"window_hash"})
        )
        if self.window_hash != expected:
            raise ValueError("M7 sequence window hash does not match")
        return self


OuterFold.model_rebuild()
SplitManifest.model_rebuild()
