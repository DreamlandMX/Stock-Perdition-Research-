"""Training-window-only fitting of frozen regime thresholds."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import date

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from stock_prediction.contracts.market_data import Identifier, UsSymbol, VersionId
from stock_prediction.contracts.research import CalendarSource, Sha256
from stock_prediction.contracts.time import UtcDatetime


MINIMUM_GROUP_SAMPLES = 20


class ThresholdFitRow(BaseModel):
    """Past-only inputs that are eligible to fit one asset-group threshold set."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    asset_group: Identifier
    as_of: date
    source_max_available_at: UtcDatetime
    dataset_revision: VersionId
    feature_set_version: VersionId
    calendar_version: VersionId
    calendar_hash: Sha256
    calendar_source: CalendarSource
    channel_width: float = Field(ge=0)
    efficiency_ratio20: float = Field(ge=0, le=1)
    absolute_normalized_slope20: float = Field(ge=0)
    realized_volatility20: float = Field(ge=0)


class RegimeThresholdTrainingSet(BaseModel):
    """Explicit fit-only container; rows outside this window cannot be constructed."""

    model_config = ConfigDict(strict=True, frozen=True)

    train_start: date
    train_end: date
    rows: tuple[ThresholdFitRow, ...] = Field(min_length=1)
    dataset_revision: VersionId
    feature_set_version: VersionId
    calendar_version: VersionId
    calendar_hash: Sha256
    calendar_source: CalendarSource

    @model_validator(mode="after")
    def validate_training_window(self) -> "RegimeThresholdTrainingSet":
        if self.train_start > self.train_end:
            raise ValueError("training window is invalid")
        if any(
            not self.train_start <= row.as_of <= self.train_end for row in self.rows
        ):
            raise ValueError("all rows must belong to the explicit training window")
        if any(
            row.dataset_revision != self.dataset_revision
            or row.feature_set_version != self.feature_set_version
            or row.calendar_version != self.calendar_version
            or row.calendar_hash != self.calendar_hash
            or row.calendar_source is not self.calendar_source
            for row in self.rows
        ):
            raise ValueError("threshold training row lineage does not match container")
        keys = [(row.symbol, row.asset_group, row.as_of) for row in self.rows]
        if len(set(keys)) != len(keys):
            raise ValueError("threshold training sample keys must be unique")
        return self


class RegimeThresholdValues(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    channel_width_q25: float = Field(ge=0)
    channel_width_q75: float = Field(ge=0)
    efficiency_ratio_q25: float = Field(ge=0, le=1)
    efficiency_ratio_q75: float = Field(ge=0, le=1)
    absolute_slope_q25: float = Field(ge=0)
    absolute_slope_q75: float = Field(ge=0)
    realized_volatility_q25: float = Field(ge=0)
    realized_volatility_q75: float = Field(ge=0)


class RegimeThresholdArtifact(BaseModel):
    """Frozen per-group thresholds with reproducible fit evidence."""

    model_config = ConfigDict(strict=True, frozen=True)

    threshold_version: VersionId
    asset_group: Identifier
    train_start: date
    train_end: date
    dataset_revision: VersionId
    feature_set_version: VersionId
    calendar_version: VersionId
    calendar_hash: Sha256
    calendar_source: CalendarSource
    quantile_method: Identifier
    values: RegimeThresholdValues
    input_hash: Sha256
    source_max_available_at: UtcDatetime
    sample_count: int = Field(ge=MINIMUM_GROUP_SAMPLES)
    fitted_at: UtcDatetime

    @model_validator(mode="after")
    def validate_fit_time(self) -> "RegimeThresholdArtifact":
        if self.fitted_at < self.source_max_available_at:
            raise ValueError("fitted_at must not precede source availability")
        return self


class AppliedRegimeThresholds(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    threshold_version: VersionId
    asset_group: Identifier
    compressed: bool
    efficient_trend: bool
    flat_slope: bool
    high_volatility: bool


def fit_regime_thresholds(
    training: RegimeThresholdTrainingSet,
    *,
    threshold_version: str,
    fitted_at: UtcDatetime | str,
) -> tuple[RegimeThresholdArtifact, ...]:
    """Fit independent asset-group quantiles from an already bounded train set."""

    fitted_timestamp = TypeAdapter(UtcDatetime).validate_python(fitted_at)
    grouped: defaultdict[str, list[ThresholdFitRow]] = defaultdict(list)
    for row in training.rows:
        grouped[row.asset_group].append(row)

    artifacts: list[RegimeThresholdArtifact] = []
    for asset_group in sorted(grouped):
        rows = sorted(
            grouped[asset_group], key=lambda row: (row.as_of, row.symbol)
        )
        if len(rows) < MINIMUM_GROUP_SAMPLES:
            raise ValueError(
                f"asset group {asset_group} requires at least "
                f"{MINIMUM_GROUP_SAMPLES} training rows"
            )
        evidence = {
            "asset_group": asset_group,
            "dataset_revision": training.dataset_revision,
            "feature_set_version": training.feature_set_version,
            "train_start": training.train_start.isoformat(),
            "train_end": training.train_end.isoformat(),
            "rows": [row.model_dump(mode="json") for row in rows],
        }
        input_hash = hashlib.sha256(
            json.dumps(
                evidence,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        artifacts.append(
            RegimeThresholdArtifact(
                threshold_version=threshold_version,
                asset_group=asset_group,
                train_start=training.train_start,
                train_end=training.train_end,
                dataset_revision=training.dataset_revision,
                feature_set_version=training.feature_set_version,
                calendar_version=training.calendar_version,
                calendar_hash=training.calendar_hash,
                calendar_source=training.calendar_source,
                quantile_method="linear",
                values=_fit_values(rows),
                input_hash=input_hash,
                source_max_available_at=max(
                    row.source_max_available_at for row in rows
                ),
                sample_count=len(rows),
                fitted_at=fitted_timestamp,
            )
        )
    return tuple(artifacts)


def apply_regime_thresholds(
    artifact: RegimeThresholdArtifact,
    row: ThresholdFitRow,
) -> AppliedRegimeThresholds:
    """Apply frozen values to any later row without refitting or mutation."""

    if artifact.asset_group != row.asset_group:
        raise ValueError("threshold artifact asset group does not match row")
    if (
        artifact.dataset_revision != row.dataset_revision
        or artifact.feature_set_version != row.feature_set_version
        or artifact.calendar_version != row.calendar_version
        or artifact.calendar_hash != row.calendar_hash
        or artifact.calendar_source is not row.calendar_source
    ):
        raise ValueError("threshold artifact lineage does not match row")
    if artifact.fitted_at > row.source_max_available_at:
        raise ValueError("threshold artifact was not available for row")
    values = artifact.values
    return AppliedRegimeThresholds(
        threshold_version=artifact.threshold_version,
        asset_group=artifact.asset_group,
        compressed=row.channel_width <= values.channel_width_q25,
        efficient_trend=(
            row.efficiency_ratio20 >= values.efficiency_ratio_q75
        ),
        flat_slope=(
            row.absolute_normalized_slope20 <= values.absolute_slope_q25
        ),
        high_volatility=(
            row.realized_volatility20 >= values.realized_volatility_q75
        ),
    )


def _fit_values(rows: Sequence[ThresholdFitRow]) -> RegimeThresholdValues:
    return RegimeThresholdValues(
        channel_width_q25=_quantile([row.channel_width for row in rows], 0.25),
        channel_width_q75=_quantile([row.channel_width for row in rows], 0.75),
        efficiency_ratio_q25=_quantile(
            [row.efficiency_ratio20 for row in rows], 0.25
        ),
        efficiency_ratio_q75=_quantile(
            [row.efficiency_ratio20 for row in rows], 0.75
        ),
        absolute_slope_q25=_quantile(
            [row.absolute_normalized_slope20 for row in rows], 0.25
        ),
        absolute_slope_q75=_quantile(
            [row.absolute_normalized_slope20 for row in rows], 0.75
        ),
        realized_volatility_q25=_quantile(
            [row.realized_volatility20 for row in rows], 0.25
        ),
        realized_volatility_q75=_quantile(
            [row.realized_volatility20 for row in rows], 0.75
        ),
    )


def _quantile(values: Sequence[float], quantile: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=np.float64), quantile))
