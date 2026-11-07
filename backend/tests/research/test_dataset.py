from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stock_prediction.contracts.research import (
    BreakoutEvent,
    CalendarSource,
    DirectionLabel,
    FalseBreakoutResolution,
    FeatureVector,
    HorizonTarget,
    LabelBundle,
    RegimeLabel,
    hash_feature_values,
)
from stock_prediction.features.transforms import (
    TransformInputRow,
    TransformerTrainingSet,
    apply_transformer,
    fit_transformer,
)
from stock_prediction.research.dataset import (
    DatasetAssemblyError,
    FeatureArtifactEvidence,
    assemble_research_dataset,
    compute_feature_values_hash,
)


ANCHOR = datetime(2026, 7, 10, 20, tzinfo=UTC)


def target(horizon: int) -> HorizonTarget:
    return HorizonTarget(
        horizon=horizon,
        forecast_direction=DirectionLabel.UP,
        executable_direction=DirectionLabel.UP,
        capital_gain_return=0.02,
        total_return=0.021,
        executable_return=0.015,
        forecast_mfe=0.03,
        forecast_mae=-0.01,
        executable_mfe=0.025,
        executable_mae=-0.012,
        breakout_event=BreakoutEvent.NONE,
        breakout_event_at=None,
        range_exit=None if horizon == 1 else False,
        range_exit_at=None,
        label_end_at=ANCHOR + timedelta(days=horizon),
        resolved=True,
    )


def bundle(**updates: object) -> LabelBundle:
    values: dict[str, object] = {
        "symbol": "US.AAPL",
        "anchor_at": ANCHOR,
        "anchor_state": RegimeLabel.RANGE,
        "frozen_channel_high": 110.0,
        "frozen_channel_low": 90.0,
        "frozen_atr20": 4.0,
        "atr_buffer_multiplier": 0.25,
        "false_breakout_resolution": FalseBreakoutResolution.NOT_APPLICABLE,
        "false_breakout_event_at": None,
        "targets": (target(1), target(5), target(20)),
        "label_version": "labels-v1",
        "threshold_version": "threshold-v1",
        "dataset_revision": "dataset-r1",
        "adjustment_revision": "adjustment-r1",
        "feature_set_version": "core-v1",
        "calendar_version": "test-calendar-v1",
        "calendar_hash": "b" * 64,
        "calendar_source": CalendarSource.SYNTHETIC_OFFLINE,
    }
    values.update(updates)
    return LabelBundle(**values)


def feature(horizon: int, **updates: object) -> FeatureVector:
    values: dict[str, object] = {
        "symbol": "US.AAPL",
        "as_of": ANCHOR,
        "horizon": horizon,
        "values": {"return_20": 0.02, "state": "range"},
        "feature_set_version": "core-v1",
        "universe_version": "universe-v1",
        "dataset_revision": "dataset-r1",
        "adjustment_revision": "adjustment-r1",
        "threshold_version": "threshold-v1",
        "calendar_version": "test-calendar-v1",
        "calendar_hash": "b" * 64,
        "calendar_source": CalendarSource.SYNTHETIC_OFFLINE,
        "feature_lineage_hash": "f" * 64,
        "feature_content_hash": "c" * 64,
        "feature_values_hash": "0" * 64,
        "source_max_available_at": ANCHOR,
        "source_max_ingested_at": ANCHOR + timedelta(minutes=1),
        "data_quality_score": 1.0,
        "missing_flags": {},
        "ood_inputs": {"return_20": 0.02},
    }
    values.update(updates)
    draft = FeatureVector(**values)
    return FeatureVector.model_validate(
        {**draft.model_dump(), "feature_values_hash": compute_feature_values_hash(draft)},
        strict=True,
    )


def artifact_evidence(features: list[FeatureVector]) -> dict[str, FeatureArtifactEvidence]:
    return {
        "f" * 64: FeatureArtifactEvidence(
            symbol=features[0].symbol,
            as_of_start=min(item.as_of for item in features),
            as_of_end=max(item.as_of for item in features),
            horizon=None,
            input_hash="f" * 64,
            content_hash="c" * 64,
            feature_values_hashes=tuple(
                sorted({item.feature_values_hash for item in features})
            ),
        )
    }


def fitted_transformer(*, lineage_hash: str = "f" * 64):
    training_feature = feature(5)
    values_hash = hash_feature_values({"return_20": 0.02, "state": "range"})
    rows = (
        TransformInputRow(
            symbol=training_feature.symbol,
            as_of=training_feature.as_of,
            horizon=5,
            adjustment_revision="adjustment-r1",
            threshold_version="threshold-v1",
            feature_lineage_hash=lineage_hash,
            feature_content_hash="c" * 64,
            feature_values_hash=values_hash,
            numeric={"return_20": 0.02},
            categorical={"state": "range"},
        ),
    )
    return fit_transformer(
        TransformerTrainingSet(
            train_start=ANCHOR,
            train_end=ANCHOR,
            rows=rows,
            feature_set_version="core-v1",
            dataset_revision="dataset-r1",
            adjustment_revision="adjustment-r1",
            threshold_version="threshold-v1",
        ),
        transformer_version="transform-v1",
        fitted_at=ANCHOR + timedelta(days=1),
    )


def transformed(features: list[FeatureVector]):
    fitted = fitted_transformer()
    rows = [
        apply_transformer(
            fitted,
            TransformInputRow(
                symbol=item.symbol,
                as_of=item.as_of,
                horizon=item.horizon,
                adjustment_revision=item.adjustment_revision,
                threshold_version=item.threshold_version,
                feature_lineage_hash=item.feature_lineage_hash,
                feature_content_hash=item.feature_content_hash,
                feature_values_hash=item.feature_values_hash,
                numeric={"return_20": 0.02},
                categorical={"state": "range"},
            ),
        )
        for item in features
    ]
    return fitted, rows


def test_assembles_versioned_samples_for_exact_1_5_20_targets() -> None:
    features = [feature(horizon) for horizon in (1, 5, 20)]
    transformer, transformed_rows = transformed(features)

    dataset = assemble_research_dataset(
        features,
        [bundle()],
        transformed_rows,
        transformer,
        eligible_adjustment_revisions={"adjustment-r1"},
        eligible_feature_artifacts=artifact_evidence(features),
    )

    assert [sample.horizon for sample in dataset.samples] == [1, 5, 20]
    assert dataset.feature_set_version == "core-v1"
    assert dataset.label_version == "labels-v1"
    assert dataset.transformer_version == "transform-v1"
    assert dataset.transformer_input_hash == transformer.input_hash
    assert (
        dataset.transformer_manifest_hash
        == transformer.training_lineage_manifest_hash
    )
    assert dataset.samples[2].label_start_at == ANCHOR
    assert dataset.samples[2].label_end_at == ANCHOR + timedelta(days=20)


@pytest.mark.parametrize(
    ("changed_feature", "changed_bundle", "match"),
    [
        ({"dataset_revision": "dataset-r2"}, {}, "dataset revision"),
        ({"feature_set_version": "core-v2"}, {}, "feature set"),
        ({"adjustment_revision": "adjustment-r2"}, {}, "adjustment revision"),
        ({"threshold_version": "threshold-v2"}, {}, "threshold version"),
        ({"calendar_hash": "c" * 64}, {}, "calendar evidence"),
        ({}, {"adjustment_revision": "adjustment-r2"}, "adjustment revision"),
    ],
)
def test_rejects_version_and_adjustment_mismatches(
    changed_feature: dict[str, object],
    changed_bundle: dict[str, object],
    match: str,
) -> None:
    features = [feature(5, **changed_feature)]
    transformer, transformed_rows = transformed([feature(5)])
    with pytest.raises(DatasetAssemblyError, match=match):
        assemble_research_dataset(
            features,
            [bundle(**changed_bundle)],
            transformed_rows,
            transformer,
            eligible_adjustment_revisions={"adjustment-r1"},
            eligible_feature_artifacts=artifact_evidence(features),
        )


def test_rejects_future_feature_source_and_invalid_label_window_even_if_models_bypassed() -> None:
    base = feature(5)
    future_source = base.model_copy(
        update={"source_max_available_at": ANCHOR + timedelta(seconds=1)}
    )
    transformer, transformed_rows = transformed([base])
    with pytest.raises(DatasetAssemblyError, match="future source"):
        assemble_research_dataset(
            [future_source],
            [bundle()],
            transformed_rows,
            transformer,
            eligible_adjustment_revisions={"adjustment-r1"},
            eligible_feature_artifacts=artifact_evidence([base]),
        )

    bad_target = target(5).model_copy(
        update={"label_end_at": ANCHOR - timedelta(seconds=1)}
    )
    bad_bundle = bundle().model_copy(
        update={"targets": (target(1), bad_target, target(20))}
    )
    with pytest.raises(DatasetAssemblyError, match="label window"):
        assemble_research_dataset(
            [base],
            [bad_bundle],
            transformed_rows,
            transformer,
            eligible_adjustment_revisions={"adjustment-r1"},
            eligible_feature_artifacts=artifact_evidence([base]),
        )

    bad_event_target = target(5).model_copy(
        update={
            "breakout_event": BreakoutEvent.UP,
            "breakout_event_at": ANCHOR - timedelta(seconds=1),
        }
    )
    bad_event_bundle = bundle().model_copy(
        update={"targets": (target(1), bad_event_target, target(20))}
    )
    with pytest.raises(DatasetAssemblyError, match="label event"):
        assemble_research_dataset(
            [base],
            [bad_event_bundle],
            transformed_rows,
            transformer,
            eligible_adjustment_revisions={"adjustment-r1"},
            eligible_feature_artifacts=artifact_evidence([base]),
        )


def test_rejects_missing_or_wrong_transformed_sample_identity() -> None:
    base = feature(5)
    transformer, transformed_rows = transformed([base])
    wrong = transformed_rows[0].model_copy(update={"horizon": 20})

    with pytest.raises(DatasetAssemblyError, match="transformed feature"):
        assemble_research_dataset(
            [base],
            [bundle()],
            [wrong],
            transformer,
            eligible_adjustment_revisions={"adjustment-r1"},
            eligible_feature_artifacts=artifact_evidence([base]),
        )

    with pytest.raises(DatasetAssemblyError, match="lineage hash"):
        assemble_research_dataset(
            [base],
            [bundle()],
            transformed_rows,
            transformer,
            eligible_adjustment_revisions={"adjustment-r1"},
            eligible_feature_artifacts={},
        )


def test_rejects_feature_values_changed_after_artifact_materialization() -> None:
    base = feature(5)
    tampered = base.model_copy(update={"values": {"return_20": 0.99, "state": "range"}})
    transformer = fitted_transformer()
    transformed_row = transformed([base])[1][0]

    with pytest.raises(DatasetAssemblyError, match="row hash"):
        assemble_research_dataset(
            [tampered],
            [bundle()],
            [transformed_row],
            transformer,
            eligible_adjustment_revisions={"adjustment-r1"},
            eligible_feature_artifacts=artifact_evidence([base]),
        )


def test_rejects_transformer_fitted_from_unapproved_feature_lineage() -> None:
    base = feature(5)
    transformer = fitted_transformer(lineage_hash="e" * 64)
    transformed_row = apply_transformer(
        transformer,
        TransformInputRow(
            symbol=base.symbol,
            as_of=base.as_of,
            horizon=base.horizon,
            adjustment_revision=base.adjustment_revision,
            threshold_version=base.threshold_version,
            feature_lineage_hash=base.feature_lineage_hash,
            feature_content_hash=base.feature_content_hash,
            feature_values_hash=base.feature_values_hash,
            numeric={"return_20": 0.02},
            categorical={"state": "range"},
        ),
    )

    with pytest.raises(DatasetAssemblyError, match="training lineage"):
        assemble_research_dataset(
            [base],
            [bundle()],
            [transformed_row],
            transformer,
            eligible_adjustment_revisions={"adjustment-r1"},
            eligible_feature_artifacts=artifact_evidence([base]),
        )


def test_rejects_transformer_values_not_bound_to_eligible_artifact_rows() -> None:
    base = feature(5)
    malicious_rows = (
        TransformInputRow(
            symbol=base.symbol,
            as_of=base.as_of,
            horizon=5,
            adjustment_revision="adjustment-r1",
            threshold_version="threshold-v1",
            feature_lineage_hash=base.feature_lineage_hash,
            feature_content_hash=base.feature_content_hash,
            feature_values_hash=hash_feature_values(
                {"return_20": 999.0, "state": "range"}
            ),
            numeric={"return_20": 999.0},
            categorical={"state": "range"},
        ),
    )
    transformer = fit_transformer(
        TransformerTrainingSet(
            train_start=base.as_of,
            train_end=base.as_of,
            rows=malicious_rows,
            feature_set_version="core-v1",
            dataset_revision="dataset-r1",
            adjustment_revision="adjustment-r1",
            threshold_version="threshold-v1",
        ),
        transformer_version="transform-v1",
        fitted_at=base.as_of + timedelta(days=1),
    )
    transformed_row = apply_transformer(
        transformer,
        TransformInputRow(
            symbol=base.symbol,
            as_of=base.as_of,
            horizon=base.horizon,
            adjustment_revision=base.adjustment_revision,
            threshold_version=base.threshold_version,
            feature_lineage_hash=base.feature_lineage_hash,
            feature_content_hash=base.feature_content_hash,
            feature_values_hash=base.feature_values_hash,
            numeric={"return_20": 0.02},
            categorical={"state": "range"},
        ),
    )

    with pytest.raises(DatasetAssemblyError, match="training feature evidence"):
        assemble_research_dataset(
            [base],
            [bundle()],
            [transformed_row],
            transformer,
            eligible_adjustment_revisions={"adjustment-r1"},
            eligible_feature_artifacts=artifact_evidence([base]),
        )


def test_rejects_transformer_training_identity_copied_from_another_artifact() -> None:
    base = feature(5)
    copied_rows = tuple(
        TransformInputRow(
            symbol=f"US.UNRELATED{index}",
            as_of=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=index),
            horizon=5,
            adjustment_revision="adjustment-r1",
            threshold_version="threshold-v1",
            feature_lineage_hash=base.feature_lineage_hash,
            feature_content_hash=base.feature_content_hash,
            feature_values_hash=base.feature_values_hash,
            numeric={"return_20": 0.02},
            categorical={"state": "range"},
        )
        for index in range(20)
    )
    transformer = fit_transformer(
        TransformerTrainingSet(
            train_start=datetime(2025, 1, 1, tzinfo=UTC),
            train_end=datetime(2025, 12, 31, tzinfo=UTC),
            rows=copied_rows,
            feature_set_version="core-v1",
            dataset_revision="dataset-r1",
            adjustment_revision="adjustment-r1",
            threshold_version="threshold-v1",
        ),
        transformer_version="transform-v1",
        fitted_at="2026-01-01T00:00:00Z",
    )
    transformed_row = apply_transformer(
        transformer,
        TransformInputRow(
            symbol=base.symbol,
            as_of=base.as_of,
            horizon=base.horizon,
            adjustment_revision=base.adjustment_revision,
            threshold_version=base.threshold_version,
            feature_lineage_hash=base.feature_lineage_hash,
            feature_content_hash=base.feature_content_hash,
            feature_values_hash=base.feature_values_hash,
            numeric={"return_20": 0.02},
            categorical={"state": "range"},
        ),
    )

    with pytest.raises(DatasetAssemblyError, match="training feature identity"):
        assemble_research_dataset(
            [base],
            [bundle()],
            [transformed_row],
            transformer,
            eligible_adjustment_revisions={"adjustment-r1"},
            eligible_feature_artifacts=artifact_evidence([base]),
        )
