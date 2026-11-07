"""Leakage-filtered exact nearest-neighbor evidence for M4 forecasts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Self

import duckdb
import numpy as np
import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator
from pydantic_core import to_jsonable_python

from stock_prediction.contracts.market_data import Identifier
from stock_prediction.contracts.research import Horizon, Sha256
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.modeling.contracts import EvaluationRole, SampleKey, TaskName
from stock_prediction.modeling.training import TrainingPartitionEvidence


class SimilarCaseInputError(ValueError):
    """Similar-case data, query, or artifact evidence is invalid."""


class DistanceConfig(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    feature_names: tuple[Identifier, ...] = Field(min_length=1)
    feature_weights: tuple[float, ...] = Field(min_length=1)
    minimum_independent_cases: int = Field(ge=1)
    distance_version: Identifier

    @model_validator(mode="after")
    def validate_schema(self) -> Self:
        if len(self.feature_names) != len(self.feature_weights):
            raise ValueError("distance feature names and weights must align")
        if len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("distance feature names must be unique")
        if any(weight <= 0 for weight in self.feature_weights):
            raise ValueError("distance feature weights must be positive")
        return self


class SimilarCaseRow(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    case_id: Identifier
    sample_key: SampleKey
    label_end: UtcDatetime
    label_observed_at: UtcDatetime
    feature_values: tuple[float, ...] = Field(min_length=1)
    range_event_id: Identifier | None
    independence_group_id: Identifier
    asset_group: Identifier
    data_quality_pass: bool
    forward_return: float
    direction_up: bool
    breakout_up: bool
    mae: float

    @model_validator(mode="after")
    def validate_label_window(self) -> Self:
        if self.label_end <= self.sample_key[1]:
            raise ValueError("similar-case label window must follow as-of")
        if self.label_observed_at < self.label_end:
            raise ValueError("similar-case label cannot be observed before label end")
        return self


class SimilarCaseQuery(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    sample_key: SampleKey
    label_end: UtcDatetime
    feature_values: tuple[float, ...] = Field(min_length=1)
    range_event_id: Identifier | None
    asset_group: Identifier
    data_quality_pass: bool

    @model_validator(mode="after")
    def validate_label_window(self) -> Self:
        if self.label_end <= self.sample_key[1]:
            raise ValueError("similar-case query label window must follow as-of")
        return self


class SimilarCaseResult(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    case_ids: tuple[Identifier, ...]
    distances: tuple[float, ...]
    effective_independent_count: int = Field(ge=0)
    insufficient_evidence: bool
    return_quantiles: tuple[float, float, float] | None
    win_rate: float | None = Field(default=None, ge=0, le=1)
    breakout_rate: float | None = Field(default=None, ge=0, le=1)
    worst_mae_case_id: Identifier | None

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        if len(self.case_ids) != len(self.distances):
            raise ValueError("case IDs and distances must align")
        if self.effective_independent_count != len(self.case_ids):
            raise ValueError("effective case count is inconsistent")
        if self.insufficient_evidence and self.win_rate is not None:
            raise ValueError("insufficient evidence cannot publish a win rate")
        return self


class SimilarCaseArtifact(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    config: DistanceConfig
    task: TaskName
    horizon: Horizon
    dataset_input_hash: Sha256
    training_partition_hash: Sha256
    rows: tuple[SimilarCaseRow, ...] = Field(min_length=1)
    means: tuple[float, ...] = Field(min_length=1)
    scales: tuple[float, ...] = Field(min_length=1)
    artifact_hash: Sha256

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        if not (
            len(self.config.feature_names)
            == len(self.means)
            == len(self.scales)
        ):
            raise ValueError("similar-case scaling schema is inconsistent")
        if any(scale <= 0 for scale in self.scales):
            raise ValueError("similar-case scales must be positive")
        if any(len(row.feature_values) != len(self.means) for row in self.rows):
            raise ValueError("similar-case row feature schema is inconsistent")
        if len({row.case_id for row in self.rows}) != len(self.rows):
            raise ValueError("similar-case IDs must be unique")
        if self.artifact_hash != _content_hash(
            self.model_dump(mode="python", exclude={"artifact_hash"})
        ):
            raise ValueError("similar-case artifact hash is invalid")
        return self

    def query(
        self,
        query: SimilarCaseQuery,
        *,
        cutoff: UtcDatetime,
        top_k: int,
        expected_artifact_hash: str,
    ) -> SimilarCaseResult:
        artifact = _revalidate_artifact(self, expected_artifact_hash)
        try:
            checked_query = SimilarCaseQuery.model_validate(
                query.model_dump(mode="python"), strict=True
            )
        except (AttributeError, ValidationError) as error:
            raise SimilarCaseInputError("similar-case query is invalid") from error
        if top_k < 1:
            raise SimilarCaseInputError("top_k must be positive")
        try:
            checked_cutoff = TypeAdapter(UtcDatetime).validate_python(
                cutoff, strict=True
            )
        except ValidationError as error:
            raise SimilarCaseInputError("similar-case cutoff is invalid") from error
        if checked_cutoff < checked_query.sample_key[1]:
            raise SimilarCaseInputError("cutoff cannot precede query as-of")
        if checked_query.sample_key[2] != artifact.horizon:
            raise SimilarCaseInputError("query horizon does not match artifact")
        if len(checked_query.feature_values) != len(artifact.means):
            raise SimilarCaseInputError("query feature schema does not match artifact")
        if not checked_query.data_quality_pass:
            return _empty_result()

        eligible = _eligible_indices(artifact.rows, checked_query, checked_cutoff)
        if not eligible:
            return _empty_result()
        matrix = np.asarray(
            [artifact.rows[index].feature_values for index in eligible],
            dtype=np.float64,
        )
        scale = np.asarray(artifact.scales, dtype=np.float64)
        weights = np.asarray(artifact.config.feature_weights, dtype=np.float64)
        target = np.asarray(checked_query.feature_values, dtype=np.float64)
        distances = np.sqrt(
            np.sum(np.square((matrix - target) / scale) * weights, axis=1)
        )
        ordered = sorted(
            zip(eligible, distances, strict=True),
            key=lambda item: (float(item[1]), artifact.rows[item[0]].case_id),
        )
        selected: list[tuple[int, float]] = []
        groups: set[str] = set()
        for index, distance in ordered:
            group = artifact.rows[index].independence_group_id
            if group in groups:
                continue
            groups.add(group)
            selected.append((index, float(distance)))
            if len(selected) == top_k:
                break
        chosen = tuple(artifact.rows[index] for index, _ in selected)
        count = len(chosen)
        insufficient = count < artifact.config.minimum_independent_cases
        returns = np.asarray([row.forward_return for row in chosen], dtype=np.float64)
        quantiles = (
            tuple(float(value) for value in np.quantile(returns, (0.1, 0.5, 0.9)))
            if count
            else None
        )
        worst = min(chosen, key=lambda row: (row.mae, row.case_id)) if chosen else None
        return SimilarCaseResult(
            case_ids=tuple(row.case_id for row in chosen),
            distances=tuple(distance for _, distance in selected),
            effective_independent_count=count,
            insufficient_evidence=insufficient,
            return_quantiles=quantiles,  # type: ignore[arg-type]
            win_rate=(
                float(np.mean([row.direction_up for row in chosen]))
                if not insufficient
                else None
            ),
            breakout_rate=(
                float(np.mean([row.breakout_up for row in chosen]))
                if not insufficient
                else None
            ),
            worst_mae_case_id=worst.case_id if worst is not None else None,
        )


class SimilarCaseIndex:
    @staticmethod
    def fit(
        rows: Sequence[SimilarCaseRow],
        *,
        partition: TrainingPartitionEvidence,
        config: DistanceConfig,
    ) -> SimilarCaseArtifact:
        try:
            checked_partition = TrainingPartitionEvidence.model_validate(
                partition.model_dump(mode="python"), strict=True
            )
            checked_config = DistanceConfig.model_validate(
                config.model_dump(mode="python"), strict=True
            )
            checked_rows = tuple(
                SimilarCaseRow.model_validate(row.model_dump(mode="python"), strict=True)
                for row in rows
            )
        except (AttributeError, ValidationError) as error:
            raise SimilarCaseInputError("similar-case fit evidence is invalid") from error
        if checked_partition.evaluation_window.role is not EvaluationRole.TRAIN:
            raise SimilarCaseInputError("similar-case scaling requires a TRAIN partition")
        if tuple(row.sample_key for row in checked_rows) != checked_partition.registered_sample_keys:
            raise SimilarCaseInputError("similar-case rows do not match TRAIN partition")
        if any(row.sample_key[2] != checked_partition.horizon for row in checked_rows):
            raise SimilarCaseInputError("similar-case row horizon is inconsistent")
        if any(
            row.label_observed_at.date()
            > checked_partition.evaluation_window.end_session
            for row in checked_rows
        ):
            raise SimilarCaseInputError(
                "similar-case label is not mature within TRAIN window"
            )
        if any(len(row.feature_values) != len(checked_config.feature_names) for row in checked_rows):
            raise SimilarCaseInputError("similar-case feature schema is inconsistent")
        scaling_rows = tuple(row for row in checked_rows if row.data_quality_pass)
        if not scaling_rows:
            raise SimilarCaseInputError("similar-case scaling has no quality-passing rows")
        matrix = np.asarray([row.feature_values for row in scaling_rows], dtype=np.float64)
        means = np.mean(matrix, axis=0)
        scales = np.std(matrix, axis=0)
        scales = np.where(scales > 1e-12, scales, 1.0)
        payload: dict[str, object] = {
            "config": checked_config,
            "task": checked_partition.task,
            "horizon": checked_partition.horizon,
            "dataset_input_hash": checked_partition.evaluation_window.dataset_input_hash,
            "training_partition_hash": checked_partition.partition_hash,
            "rows": checked_rows,
            "means": tuple(float(value) for value in means),
            "scales": tuple(float(value) for value in scales),
        }
        payload["artifact_hash"] = _content_hash(payload)
        return SimilarCaseArtifact.model_validate(payload, strict=True)


def _eligible_indices(
    rows: Sequence[SimilarCaseRow], query: SimilarCaseQuery, cutoff: UtcDatetime
) -> tuple[int, ...]:
    table = pa.table(
        {
            "row_index": tuple(range(len(rows))),
            "as_of": tuple(row.sample_key[1] for row in rows),
            "label_end": tuple(row.label_end for row in rows),
            "label_observed_at": tuple(row.label_observed_at for row in rows),
            "range_event_id": tuple(row.range_event_id for row in rows),
            "asset_group": tuple(row.asset_group for row in rows),
            "quality": tuple(row.data_quality_pass for row in rows),
        }
    )
    connection = duckdb.connect(":memory:")
    try:
        connection.register("case_rows", table)
        result = connection.execute(
            """
            SELECT row_index
            FROM case_rows
            WHERE as_of < ?
              AND as_of < ?
              AND label_end < ?
              AND label_end <= ?
              AND label_observed_at <= ?
              AND label_observed_at <= ?
              AND asset_group = ?
              AND quality = TRUE
              AND (? IS NULL OR range_event_id IS NULL OR range_event_id <> ?)
            ORDER BY row_index
            """,
            (
                cutoff,
                query.sample_key[1],
                query.sample_key[1],
                cutoff,
                query.sample_key[1],
                cutoff,
                query.asset_group,
                query.range_event_id,
                query.range_event_id,
            ),
        ).fetchall()
    finally:
        connection.close()
    return tuple(int(row[0]) for row in result)


def _revalidate_artifact(
    artifact: SimilarCaseArtifact, expected_artifact_hash: str
) -> SimilarCaseArtifact:
    try:
        checked = SimilarCaseArtifact.model_validate(
            artifact.model_dump(mode="python"), strict=True
        )
    except (AttributeError, ValidationError) as error:
        raise SimilarCaseInputError("similar-case artifact failed revalidation") from error
    if checked.artifact_hash != expected_artifact_hash:
        raise SimilarCaseInputError("similar-case artifact identity is not registered")
    return checked


def _empty_result() -> SimilarCaseResult:
    return SimilarCaseResult(
        case_ids=(),
        distances=(),
        effective_independent_count=0,
        insufficient_evidence=True,
        return_quantiles=None,
        win_rate=None,
        breakout_rate=None,
        worst_mae_case_id=None,
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
