"""Leakage-audited assembly of transformed features and future targets."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence, Set

from pydantic import BaseModel, ConfigDict, Field

from stock_prediction.contracts.market_data import UsSymbol, VersionId
from stock_prediction.contracts.research import (
    CalendarSource,
    FeatureVector,
    Horizon,
    HorizonTarget,
    LabelBundle,
    Sha256,
    hash_feature_values,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.features.transforms import (
    TransformInputRow,
    TransformedFeatureRow,
    TransformerArtifact,
)


class DatasetAssemblyError(ValueError):
    """Research sample identity, time, or version evidence is inconsistent."""


class FeatureArtifactEvidence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    symbol: UsSymbol
    as_of_start: UtcDatetime
    as_of_end: UtcDatetime
    horizon: Horizon | None
    input_hash: Sha256
    content_hash: Sha256
    feature_values_hashes: tuple[Sha256, ...] = Field(min_length=1)


class ResearchSample(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    as_of: UtcDatetime
    horizon: Horizon
    numeric_features: dict[str, float | None]
    categorical_features: dict[str, str]
    transform_flags: dict[str, bool]
    ood_score: float = Field(ge=0)
    target: HorizonTarget
    feature_set_version: VersionId
    label_version: VersionId
    threshold_version: VersionId
    calendar_version: VersionId
    calendar_hash: Sha256
    calendar_source: CalendarSource
    transformer_version: VersionId
    transformer_input_hash: Sha256
    transformer_manifest_hash: Sha256
    universe_version: VersionId
    dataset_revision: VersionId
    adjustment_revision: VersionId
    feature_lineage_hash: Sha256
    feature_content_hash: Sha256
    feature_values_hash: Sha256
    feature_source_max_available_at: UtcDatetime
    feature_source_max_ingested_at: UtcDatetime
    data_quality_score: float = Field(ge=0, le=1)
    label_start_at: UtcDatetime
    label_end_at: UtcDatetime


class ResearchDataset(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    dataset_id: str = Field(pattern=r"^research-[0-9a-f]{24}$")
    input_hash: Sha256
    feature_set_version: VersionId
    label_version: VersionId
    threshold_version: VersionId
    transformer_version: VersionId
    transformer_input_hash: Sha256
    transformer_manifest_hash: Sha256
    universe_version: VersionId
    dataset_revision: VersionId
    calendar_version: VersionId
    calendar_hash: Sha256
    calendar_source: CalendarSource
    sample_count: int = Field(ge=1)
    samples: tuple[ResearchSample, ...]


def compute_research_dataset_input_hash(
    samples: Sequence[ResearchSample],
) -> str:
    """Hash the exact ordered M3 sample evidence used by downstream manifests."""

    return hashlib.sha256(
        json.dumps(
            [item.model_dump(mode="json") for item in samples],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def assemble_research_dataset(
    features: Sequence[FeatureVector],
    labels: Sequence[LabelBundle],
    transformed_rows: Sequence[TransformedFeatureRow],
    transformer: TransformerArtifact,
    *,
    eligible_adjustment_revisions: Set[str],
    eligible_feature_artifacts: Mapping[str, FeatureArtifactEvidence],
) -> ResearchDataset:
    """Join exact sample keys and reject every time/version incompatibility."""

    if not features:
        raise DatasetAssemblyError("research dataset requires feature rows")
    feature_keys = [_feature_key(item) for item in features]
    if len(set(feature_keys)) != len(feature_keys):
        raise DatasetAssemblyError("feature sample keys must be unique")
    label_keys = [(item.symbol, item.anchor_at) for item in labels]
    if len(set(label_keys)) != len(label_keys):
        raise DatasetAssemblyError("label bundle keys must be unique")
    transformed_keys = [item.sample_key for item in transformed_rows]
    if len(set(transformed_keys)) != len(transformed_keys):
        raise DatasetAssemblyError("transformed feature keys must be unique")

    label_map = {key: item for key, item in zip(label_keys, labels, strict=True)}
    transformed_map = {
        key: item for key, item in zip(transformed_keys, transformed_rows, strict=True)
    }
    samples: list[ResearchSample] = []
    for feature in features:
        key = _feature_key(feature)
        label = label_map.get((feature.symbol, feature.as_of))
        if label is None:
            raise DatasetAssemblyError("feature row has no matching label bundle")
        transformed = transformed_map.get(key)
        if transformed is None:
            raise DatasetAssemblyError("feature row has no matching transformed feature")
        _validate_versions(feature, label, transformed, transformer)
        if label.adjustment_revision not in eligible_adjustment_revisions:
            raise DatasetAssemblyError("adjustment revision is not training eligible")
        if feature.source_max_available_at > feature.as_of:
            raise DatasetAssemblyError("feature contains a future source timestamp")
        evidence = eligible_feature_artifacts.get(feature.feature_lineage_hash)
        if evidence is None:
            raise DatasetAssemblyError("feature lineage hash is not training eligible")
        if evidence.input_hash != feature.feature_lineage_hash:
            raise DatasetAssemblyError("feature artifact evidence key is inconsistent")
        if evidence.content_hash != feature.feature_content_hash:
            raise DatasetAssemblyError("feature content hash does not match artifact")
        if feature.feature_values_hash not in evidence.feature_values_hashes:
            raise DatasetAssemblyError("feature values hash is absent from artifact")
        if compute_feature_values_hash(feature) != feature.feature_values_hash:
            raise DatasetAssemblyError("feature values do not match their row hash")
        target = next(
            (item for item in label.targets if item.horizon == feature.horizon),
            None,
        )
        if target is None:
            raise DatasetAssemblyError("feature horizon has no matching label target")
        _validate_label_window(feature, label, target)
        expected_transform_input = _transform_input(feature, transformer)
        if transformed.input_hash != expected_transform_input.input_hash:
            raise DatasetAssemblyError("transformed feature input hash mismatch")
        samples.append(
            ResearchSample(
                symbol=feature.symbol,
                as_of=feature.as_of,
                horizon=feature.horizon,
                numeric_features=transformed.numeric,
                categorical_features=transformed.categorical,
                transform_flags=transformed.flags,
                ood_score=transformed.ood_score,
                target=target,
                feature_set_version=feature.feature_set_version,
                label_version=label.label_version,
                threshold_version=label.threshold_version,
                calendar_version=feature.calendar_version,
                calendar_hash=feature.calendar_hash,
                calendar_source=feature.calendar_source,
                transformer_version=transformer.transformer_version,
                transformer_input_hash=transformer.input_hash,
                transformer_manifest_hash=transformer.training_lineage_manifest_hash,
                universe_version=feature.universe_version,
                dataset_revision=feature.dataset_revision,
                adjustment_revision=label.adjustment_revision,
                feature_lineage_hash=feature.feature_lineage_hash,
                feature_content_hash=feature.feature_content_hash,
                feature_values_hash=feature.feature_values_hash,
                feature_source_max_available_at=feature.source_max_available_at,
                feature_source_max_ingested_at=feature.source_max_ingested_at,
                data_quality_score=feature.data_quality_score,
                label_start_at=feature.as_of,
                label_end_at=target.label_end_at,
            )
        )

    ordered = tuple(sorted(samples, key=lambda item: (item.as_of, item.symbol, item.horizon)))
    dimensions: dict[str, set[str]] = {
        "feature_set_version": {item.feature_set_version for item in ordered},
        "label_version": {item.label_version for item in ordered},
        "threshold_version": {item.threshold_version for item in ordered},
        "transformer_version": {item.transformer_version for item in ordered},
        "transformer_input_hash": {item.transformer_input_hash for item in ordered},
        "transformer_manifest_hash": {
            item.transformer_manifest_hash for item in ordered
        },
        "universe_version": {item.universe_version for item in ordered},
        "dataset_revision": {item.dataset_revision for item in ordered},
        "calendar_version": {item.calendar_version for item in ordered},
        "calendar_hash": {item.calendar_hash for item in ordered},
    }
    calendar_sources = {item.calendar_source for item in ordered}
    if any(len(values) != 1 for values in dimensions.values()) or len(calendar_sources) != 1:
        raise DatasetAssemblyError("research dataset mixes version dimensions")
    for training_evidence in transformer.training_feature_evidence:
        eligible = eligible_feature_artifacts.get(
            training_evidence.feature_lineage_hash
        )
        if eligible is None:
            raise DatasetAssemblyError(
                "transformer training lineage is not in eligible artifacts"
            )
        if (
            training_evidence.symbol != eligible.symbol
            or not eligible.as_of_start
            <= training_evidence.as_of
            <= eligible.as_of_end
            or (
                eligible.horizon is not None
                and training_evidence.horizon != eligible.horizon
            )
        ):
            raise DatasetAssemblyError(
                "transformer training feature identity is not eligible"
            )
        if (
            eligible.content_hash != training_evidence.feature_content_hash
            or training_evidence.feature_values_hash
            not in eligible.feature_values_hashes
        ):
            raise DatasetAssemblyError(
                "transformer training feature evidence is not eligible"
            )
    input_hash = compute_research_dataset_input_hash(ordered)
    values = {name: next(iter(items)) for name, items in dimensions.items()}
    return ResearchDataset(
        dataset_id=f"research-{input_hash[:24]}",
        input_hash=input_hash,
        sample_count=len(ordered),
        samples=ordered,
        calendar_source=next(iter(calendar_sources)),
        **values,
    )


def _validate_versions(
    feature: FeatureVector,
    label: LabelBundle,
    transformed: TransformedFeatureRow,
    transformer: TransformerArtifact,
) -> None:
    if feature.feature_set_version != transformer.feature_set_version:
        raise DatasetAssemblyError("feature set does not match transformer")
    if feature.feature_set_version != label.feature_set_version:
        raise DatasetAssemblyError("feature set does not match label bundle")
    if feature.dataset_revision != label.dataset_revision:
        raise DatasetAssemblyError("feature and label dataset revisions do not match")
    if feature.dataset_revision != transformer.dataset_revision:
        raise DatasetAssemblyError("feature dataset revision does not match transformer")
    if feature.adjustment_revision != label.adjustment_revision:
        raise DatasetAssemblyError("feature and label adjustment revisions do not match")
    if feature.adjustment_revision != transformer.adjustment_revision:
        raise DatasetAssemblyError("feature adjustment revision does not match transformer")
    if feature.threshold_version != label.threshold_version:
        raise DatasetAssemblyError("feature and label threshold versions do not match")
    if feature.threshold_version != transformer.threshold_version:
        raise DatasetAssemblyError("feature threshold version does not match transformer")
    if (
        feature.calendar_version != label.calendar_version
        or feature.calendar_hash != label.calendar_hash
        or feature.calendar_source is not label.calendar_source
    ):
        raise DatasetAssemblyError("feature and label calendar evidence does not match")
    if transformed.feature_set_version != feature.feature_set_version:
        raise DatasetAssemblyError("transformed feature set does not match feature row")
    if transformed.dataset_revision != feature.dataset_revision:
        raise DatasetAssemblyError("transformed dataset revision does not match feature row")
    if transformed.transformer_version != transformer.transformer_version:
        raise DatasetAssemblyError("transformed row uses the wrong transformer version")
    if transformed.adjustment_revision != feature.adjustment_revision:
        raise DatasetAssemblyError("transformed adjustment revision does not match feature")
    if transformed.threshold_version != feature.threshold_version:
        raise DatasetAssemblyError("transformed threshold version does not match feature")
    if transformed.feature_lineage_hash != feature.feature_lineage_hash:
        raise DatasetAssemblyError("transformed feature lineage does not match feature")


def _validate_label_window(
    feature: FeatureVector,
    label: LabelBundle,
    target: HorizonTarget,
) -> None:
    if target.label_end_at <= feature.as_of:
        raise DatasetAssemblyError("label window must begin after feature anchor")
    for event_at in (target.breakout_event_at, target.range_exit_at):
        if event_at is not None and not feature.as_of < event_at <= target.label_end_at:
            raise DatasetAssemblyError("label event falls outside label window")
    if (
        label.false_breakout_event_at is not None
        and label.false_breakout_event_at <= feature.as_of
    ):
        raise DatasetAssemblyError("false-breakout event falls outside label window")


def _transform_input(
    feature: FeatureVector, transformer: TransformerArtifact
) -> TransformInputRow:
    expected_names = set(transformer.numeric_schema) | set(
        transformer.categorical_schema
    )
    if set(feature.values) != expected_names:
        raise DatasetAssemblyError("feature values do not match transformer schema")
    numeric: dict[str, float | None] = {}
    categorical: dict[str, str | None] = {}
    for name in transformer.numeric_schema:
        value = feature.values[name]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise DatasetAssemblyError("numeric feature has a non-numeric value")
        numeric[name] = None if value is None else float(value)
    for name in transformer.categorical_schema:
        value = feature.values[name]
        if value is not None and not isinstance(value, str):
            raise DatasetAssemblyError("categorical feature has a non-string value")
        categorical[name] = value
    return TransformInputRow(
        symbol=feature.symbol,
        as_of=feature.as_of,
        horizon=feature.horizon,
        adjustment_revision=feature.adjustment_revision,
        threshold_version=feature.threshold_version,
        feature_lineage_hash=feature.feature_lineage_hash,
        feature_content_hash=feature.feature_content_hash,
        feature_values_hash=feature.feature_values_hash,
        numeric=numeric,
        categorical=categorical,
    )


def _feature_key(feature: FeatureVector) -> tuple[str, object, int]:
    return (feature.symbol, feature.as_of, feature.horizon)


def compute_feature_values_hash(feature: FeatureVector) -> str:
    """Bind exact untransformed values; identity is bound by the other hashes."""

    return hash_feature_values(feature.values)
