"""Fold-local numeric, categorical, and OOD transformations."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from stock_prediction.contracts.market_data import UsSymbol, VersionId
from stock_prediction.contracts.research import Horizon, Sha256, hash_feature_values
from stock_prediction.contracts.time import UtcDatetime


MISSING_CATEGORY = "__MISSING__"
UNKNOWN_CATEGORY = "__UNKNOWN__"


class TransformInputRow(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    as_of: UtcDatetime
    horizon: Horizon
    adjustment_revision: VersionId
    threshold_version: VersionId
    feature_lineage_hash: Sha256
    feature_content_hash: Sha256
    feature_values_hash: Sha256
    numeric: dict[str, float | None]
    categorical: dict[str, str | None]

    @model_validator(mode="after")
    def validate_namespaces(self) -> "TransformInputRow":
        if set(self.numeric) & set(self.categorical):
            raise ValueError("numeric and categorical feature names overlap")
        if any(
            value in {MISSING_CATEGORY, UNKNOWN_CATEGORY}
            for value in self.categorical.values()
            if value is not None
        ):
            raise ValueError("categorical value uses a reserved token")
        if hash_feature_values({**self.numeric, **self.categorical}) != self.feature_values_hash:
            raise ValueError("transform values do not match feature_values_hash")
        return self

    @property
    def sample_key(self) -> tuple[str, object, int]:
        return (self.symbol, self.as_of, self.horizon)

    @property
    def input_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.model_dump(mode="json"),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


class TransformerTrainingSet(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    train_start: UtcDatetime
    train_end: UtcDatetime
    rows: tuple[TransformInputRow, ...] = Field(min_length=1)
    feature_set_version: VersionId
    dataset_revision: VersionId
    adjustment_revision: VersionId
    threshold_version: VersionId

    @model_validator(mode="after")
    def validate_training_rows(self) -> "TransformerTrainingSet":
        if self.train_start > self.train_end:
            raise ValueError("transformer training window is reversed")
        if any(
            not self.train_start <= row.as_of <= self.train_end for row in self.rows
        ):
            raise ValueError("all rows must belong to the explicit training window")
        first_numeric = set(self.rows[0].numeric)
        first_categorical = set(self.rows[0].categorical)
        if any(
            set(row.numeric) != first_numeric
            or set(row.categorical) != first_categorical
            for row in self.rows[1:]
        ):
            raise ValueError("training rows must have identical feature schema")
        keys = [row.sample_key for row in self.rows]
        if len(set(keys)) != len(keys):
            raise ValueError("transformer training sample keys must be unique")
        if any(
            row.adjustment_revision != self.adjustment_revision
            or row.threshold_version != self.threshold_version
            for row in self.rows
        ):
            raise ValueError("training row lineage versions do not match container")
        return self


class NumericTransformSpec(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    median: float | None
    q25: float | None
    q75: float | None
    robust_scale: float | None = Field(default=None, gt=0)
    winsor_low: float | None
    winsor_high: float | None
    observed_count: int = Field(ge=0)
    all_missing: bool


class TrainingFeatureEvidence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    symbol: UsSymbol
    as_of: UtcDatetime
    horizon: Horizon
    feature_lineage_hash: Sha256
    feature_content_hash: Sha256
    feature_values_hash: Sha256


class TransformerArtifact(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    transformer_version: VersionId
    feature_set_version: VersionId
    dataset_revision: VersionId
    adjustment_revision: VersionId
    threshold_version: VersionId
    train_start: UtcDatetime
    train_end: UtcDatetime
    fitted_at: UtcDatetime
    input_hash: Sha256
    training_lineage_hashes: tuple[Sha256, ...]
    training_feature_evidence: tuple[TrainingFeatureEvidence, ...]
    training_lineage_manifest_hash: Sha256
    numeric_specs: dict[str, NumericTransformSpec]
    categorical_levels: dict[str, tuple[str, ...]]
    numeric_schema: tuple[str, ...]
    categorical_schema: tuple[str, ...]
    winsor_quantiles: tuple[float, float]
    ood_method: VersionId
    sample_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_artifact(self) -> "TransformerArtifact":
        if self.fitted_at < self.train_end:
            raise ValueError("transformer fitted_at precedes training window")
        if set(self.numeric_specs) != set(self.numeric_schema):
            raise ValueError("numeric specs do not match numeric schema")
        if set(self.categorical_levels) != set(self.categorical_schema):
            raise ValueError("categorical levels do not match categorical schema")
        expected_lineages = tuple(
            sorted(
                {
                    item.feature_lineage_hash
                    for item in self.training_feature_evidence
                }
            )
        )
        expected_manifest = hashlib.sha256(
            json.dumps(
                [
                    item.model_dump(mode="json")
                    for item in self.training_feature_evidence
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if (
            not self.training_feature_evidence
            or self.training_lineage_hashes != expected_lineages
            or expected_manifest != self.training_lineage_manifest_hash
        ):
            raise ValueError("transformer training lineage manifest is invalid")
        return self


class TransformedFeatureRow(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    as_of: UtcDatetime
    horizon: Horizon
    transformer_version: VersionId
    feature_set_version: VersionId
    dataset_revision: VersionId
    adjustment_revision: VersionId
    threshold_version: VersionId
    feature_lineage_hash: Sha256
    input_hash: Sha256
    numeric: dict[str, float | None]
    categorical: dict[str, str]
    flags: dict[str, bool]
    ood_score: float = Field(ge=0)

    @property
    def sample_key(self) -> tuple[str, object, int]:
        return (self.symbol, self.as_of, self.horizon)


def fit_transformer(
    training: TransformerTrainingSet,
    *,
    transformer_version: str,
    fitted_at: UtcDatetime | str,
) -> TransformerArtifact:
    """Fit all learned preprocessing state from an explicit training container."""

    fitted_timestamp = TypeAdapter(UtcDatetime).validate_python(fitted_at)
    ordered = sorted(training.rows, key=lambda row: row.sample_key)
    numeric_schema = tuple(sorted(ordered[0].numeric))
    categorical_schema = tuple(sorted(ordered[0].categorical))
    numeric_specs = {
        name: _fit_numeric([row.numeric[name] for row in ordered])
        for name in numeric_schema
    }
    categorical_levels = {
        name: tuple(
            sorted(
                {
                    value
                    for row in ordered
                    if (value := row.categorical[name]) is not None
                }
            )
        )
        for name in categorical_schema
    }
    evidence = {
        "train_start": training.train_start.isoformat(),
        "train_end": training.train_end.isoformat(),
        "feature_set_version": training.feature_set_version,
        "dataset_revision": training.dataset_revision,
        "rows": [row.model_dump(mode="json") for row in ordered],
        "winsor_quantiles": [0.01, 0.99],
    }
    input_hash = hashlib.sha256(
        json.dumps(
            evidence,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    training_feature_evidence = tuple(
        TrainingFeatureEvidence(
            symbol=symbol,
            as_of=as_of,
            horizon=horizon,
            feature_lineage_hash=lineage,
            feature_content_hash=content,
            feature_values_hash=values,
        )
        for symbol, as_of, horizon, lineage, content, values in sorted(
            {
                (
                    row.symbol,
                    row.as_of,
                    row.horizon,
                    row.feature_lineage_hash,
                    row.feature_content_hash,
                    row.feature_values_hash,
                )
                for row in ordered
            }
        )
    )
    training_lineages = tuple(
        sorted({item.feature_lineage_hash for item in training_feature_evidence})
    )
    lineage_manifest_hash = hashlib.sha256(
        json.dumps(
            [item.model_dump(mode="json") for item in training_feature_evidence],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return TransformerArtifact(
        transformer_version=transformer_version,
        feature_set_version=training.feature_set_version,
        dataset_revision=training.dataset_revision,
        adjustment_revision=training.adjustment_revision,
        threshold_version=training.threshold_version,
        train_start=training.train_start,
        train_end=training.train_end,
        fitted_at=fitted_timestamp,
        input_hash=input_hash,
        training_lineage_hashes=training_lineages,
        training_feature_evidence=training_feature_evidence,
        training_lineage_manifest_hash=lineage_manifest_hash,
        numeric_specs=numeric_specs,
        categorical_levels=categorical_levels,
        numeric_schema=numeric_schema,
        categorical_schema=categorical_schema,
        winsor_quantiles=(0.01, 0.99),
        ood_method="robust-rms-v1",
        sample_count=len(ordered),
    )


def apply_transformer(
    artifact: TransformerArtifact,
    row: TransformInputRow,
) -> TransformedFeatureRow:
    """Apply frozen preprocessing without mutating or extending fitted state."""

    if row.adjustment_revision != artifact.adjustment_revision:
        raise ValueError("transform row adjustment revision does not match artifact")
    if row.threshold_version != artifact.threshold_version:
        raise ValueError("transform row threshold version does not match artifact")
    if set(row.numeric) != set(artifact.numeric_schema):
        raise ValueError("transform input numeric schema does not match artifact")
    if set(row.categorical) != set(artifact.categorical_schema):
        raise ValueError("transform input categorical schema does not match artifact")

    numeric: dict[str, float | None] = {}
    categorical: dict[str, str] = {}
    flags: dict[str, bool] = {}
    ood_components: list[float] = []
    for name in artifact.numeric_schema:
        value = row.numeric[name]
        spec = artifact.numeric_specs[name]
        flags[f"{name}__missing"] = value is None
        flags[f"{name}__unusable"] = spec.all_missing
        if spec.all_missing:
            numeric[name] = None
            flags[f"{name}__extreme"] = False
            continue
        assert spec.median is not None
        assert spec.robust_scale is not None
        assert spec.winsor_low is not None
        assert spec.winsor_high is not None
        raw = spec.median if value is None else value
        extreme = raw < spec.winsor_low or raw > spec.winsor_high
        flags[f"{name}__extreme"] = extreme
        clipped = min(max(raw, spec.winsor_low), spec.winsor_high)
        numeric[name] = (clipped - spec.median) / spec.robust_scale
        if value is not None:
            ood_components.append(abs(value - spec.median) / spec.robust_scale)

    for name in artifact.categorical_schema:
        categorical_value = row.categorical[name]
        levels = artifact.categorical_levels[name]
        missing = categorical_value is None
        unknown = categorical_value is not None and categorical_value not in levels
        flags[f"{name}__missing"] = missing
        flags[f"{name}__unknown"] = unknown
        if categorical_value is None:
            categorical[name] = MISSING_CATEGORY
        elif unknown:
            categorical[name] = UNKNOWN_CATEGORY
        else:
            categorical[name] = categorical_value
    ood_score = (
        math.sqrt(sum(value * value for value in ood_components) / len(ood_components))
        if ood_components
        else 0.0
    )
    return TransformedFeatureRow(
        symbol=row.symbol,
        as_of=row.as_of,
        horizon=row.horizon,
        transformer_version=artifact.transformer_version,
        feature_set_version=artifact.feature_set_version,
        dataset_revision=artifact.dataset_revision,
        adjustment_revision=artifact.adjustment_revision,
        threshold_version=artifact.threshold_version,
        feature_lineage_hash=row.feature_lineage_hash,
        input_hash=row.input_hash,
        numeric=numeric,
        categorical=categorical,
        flags=flags,
        ood_score=ood_score,
    )


def _fit_numeric(values: Sequence[float | None]) -> NumericTransformSpec:
    observed = [value for value in values if value is not None]
    if not observed:
        return NumericTransformSpec(
            median=None,
            q25=None,
            q75=None,
            robust_scale=None,
            winsor_low=None,
            winsor_high=None,
            observed_count=0,
            all_missing=True,
        )
    array = np.asarray(observed, dtype=np.float64)
    median = float(np.quantile(array, 0.50))
    q25 = float(np.quantile(array, 0.25))
    q75 = float(np.quantile(array, 0.75))
    scale = q75 - q25
    if scale <= 1e-12:
        scale = 1.0
    return NumericTransformSpec(
        median=median,
        q25=q25,
        q75=q75,
        robust_scale=scale,
        winsor_low=float(np.quantile(array, 0.01)),
        winsor_high=float(np.quantile(array, 0.99)),
        observed_count=len(observed),
        all_missing=False,
    )
