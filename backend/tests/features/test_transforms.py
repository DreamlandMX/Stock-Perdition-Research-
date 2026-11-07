from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from stock_prediction.contracts.research import hash_feature_values
from stock_prediction.features.transforms import (
    TransformInputRow,
    TransformerTrainingSet,
    apply_transformer,
    fit_transformer,
)


TRAIN_START = datetime(2025, 1, 1, tzinfo=UTC)
TRAIN_END = datetime(2025, 12, 31, tzinfo=UTC)


def row(
    index: int,
    *,
    numeric: dict[str, float | None] | None = None,
    categorical: dict[str, str | None] | None = None,
) -> TransformInputRow:
    numeric_values = (
        numeric
        if numeric is not None
        else {"return_20": float(index), "all_missing": None}
    )
    categorical_values = (
        categorical
        if categorical is not None
        else {"state": "range" if index % 2 == 0 else "trend"}
    )
    return TransformInputRow(
        symbol=f"US.T{index}",
        as_of=TRAIN_START + timedelta(days=index),
        horizon=5,
        adjustment_revision="adjustment-r1",
        threshold_version="threshold-v1",
        feature_lineage_hash="f" * 64,
        feature_content_hash="c" * 64,
        feature_values_hash=hash_feature_values(
            {**numeric_values, **categorical_values}
        ),
        numeric=numeric_values,
        categorical=categorical_values,
    )


def training(rows: tuple[TransformInputRow, ...]) -> TransformerTrainingSet:
    return TransformerTrainingSet(
        train_start=TRAIN_START,
        train_end=TRAIN_END,
        rows=rows,
        feature_set_version="core-v1",
        dataset_revision="dataset-r1",
        adjustment_revision="adjustment-r1",
        threshold_version="threshold-v1",
    )


def artifact():
    return fit_transformer(
        training(tuple(row(index) for index in range(20))),
        transformer_version="transform-v1",
        fitted_at="2026-01-01T00:00:00Z",
    )


def test_fit_is_deterministic_and_validation_outlier_cannot_mutate_artifact() -> None:
    fitted = artifact()
    before = fitted.model_dump_json()
    outlier = row(
        100,
        numeric={"return_20": 1_000_000.0, "all_missing": None},
    ).model_copy(update={"as_of": datetime(2026, 7, 10, tzinfo=UTC)})

    transformed = apply_transformer(fitted, outlier)

    assert fitted.model_dump_json() == before
    assert transformed.flags["return_20__extreme"] is True
    assert transformed.numeric["return_20"] is not None
    assert transformed.ood_score > 1.0


def test_missing_unknown_and_all_missing_columns_remain_explicit() -> None:
    fitted = artifact()
    sample = row(
        100,
        numeric={"return_20": None, "all_missing": None},
        categorical={"state": "new_state"},
    ).model_copy(update={"as_of": datetime(2026, 7, 10, tzinfo=UTC)})

    transformed = apply_transformer(fitted, sample)

    assert transformed.numeric["return_20"] == pytest.approx(0.0)
    assert transformed.flags["return_20__missing"] is True
    assert transformed.numeric["all_missing"] is None
    assert transformed.flags["all_missing__unusable"] is True
    assert transformed.categorical["state"] == "__UNKNOWN__"
    assert transformed.flags["state__unknown"] is True


def test_schema_drift_and_rows_outside_explicit_training_window_are_rejected() -> None:
    fitted = artifact()
    drift = row(100, numeric={"different": 1.0, "all_missing": None})
    with pytest.raises(ValueError, match="numeric schema"):
        apply_transformer(fitted, drift)

    future = row(0).model_copy(
        update={"as_of": datetime(2026, 1, 1, tzinfo=UTC)}
    )
    with pytest.raises(ValidationError, match="explicit training window"):
        training((future,))


def test_training_rows_must_share_exact_numeric_and_categorical_schema() -> None:
    inconsistent = row(1, categorical={"other": "x"})
    with pytest.raises(ValidationError, match="identical feature schema"):
        training((row(0), inconsistent))


def test_transform_input_values_must_match_bound_feature_values_hash() -> None:
    valid = row(0)

    with pytest.raises(ValidationError, match="feature_values_hash"):
        TransformInputRow.model_validate(
            {**valid.model_dump(), "feature_values_hash": "e" * 64},
            strict=True,
        )
