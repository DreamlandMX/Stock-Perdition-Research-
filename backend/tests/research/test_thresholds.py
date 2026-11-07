from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from stock_prediction.contracts.research import CalendarSource
from stock_prediction.research.thresholds import (
    RegimeThresholdTrainingSet,
    ThresholdFitRow,
    apply_regime_thresholds,
    fit_regime_thresholds,
)


TRAIN_START = date(2025, 1, 1)
TRAIN_END = date(2025, 6, 30)
FITTED_AT = datetime(2025, 7, 1, tzinfo=UTC)


def fit_row(index: int, *, asset_group: str) -> ThresholdFitRow:
    return ThresholdFitRow(
        symbol="US.SPY" if asset_group == "etf" else "US.AAPL",
        asset_group=asset_group,
        as_of=TRAIN_START + timedelta(days=index),
        source_max_available_at=datetime.combine(
            TRAIN_START + timedelta(days=index),
            datetime.min.time(),
            tzinfo=UTC,
        )
        + timedelta(hours=21),
        dataset_revision="dataset-r1",
        feature_set_version="features-v1",
        calendar_version="test-calendar-v1",
        calendar_hash="b" * 64,
        calendar_source=CalendarSource.SYNTHETIC_OFFLINE,
        channel_width=0.01 + index * 0.001,
        efficiency_ratio20=index / 29,
        absolute_normalized_slope20=index * 0.0001,
        realized_volatility20=0.10 + index * 0.01,
    )


def training_set(rows: tuple[ThresholdFitRow, ...]) -> RegimeThresholdTrainingSet:
    return RegimeThresholdTrainingSet(
        train_start=TRAIN_START,
        train_end=TRAIN_END,
        rows=rows,
        dataset_revision="dataset-r1",
        feature_set_version="features-v1",
        calendar_version="test-calendar-v1",
        calendar_hash="b" * 64,
        calendar_source=CalendarSource.SYNTHETIC_OFFLINE,
    )


def test_training_container_structurally_rejects_rows_outside_window() -> None:
    future = fit_row(0, asset_group="stock").model_copy(
        update={"as_of": date(2025, 7, 1)}
    )

    with pytest.raises(ValidationError, match="explicit training window"):
        training_set((future,))


def test_training_container_rejects_row_feature_or_calendar_lineage_mismatch() -> None:
    wrong = fit_row(0, asset_group="stock").model_copy(
        update={"feature_set_version": "features-v2"}
    )

    with pytest.raises(ValidationError, match="row lineage"):
        training_set((wrong,))


def test_fits_separate_deterministic_asset_group_artifacts() -> None:
    stock = tuple(fit_row(index, asset_group="stock") for index in range(30))
    etf = tuple(fit_row(index, asset_group="etf") for index in range(30))

    first = fit_regime_thresholds(
        training_set(tuple(reversed(stock + etf))),
        threshold_version="regime-thresholds-v1",
        fitted_at=FITTED_AT,
    )
    second = fit_regime_thresholds(
        training_set(stock + etf),
        threshold_version="regime-thresholds-v1",
        fitted_at=FITTED_AT,
    )

    assert [artifact.asset_group for artifact in first] == ["etf", "stock"]
    assert first == second
    assert len({artifact.input_hash for artifact in first}) == 2
    assert all(artifact.sample_count == 30 for artifact in first)


def test_frozen_thresholds_apply_deterministically_after_training_window() -> None:
    rows = tuple(fit_row(index, asset_group="stock") for index in range(30))
    [artifact] = fit_regime_thresholds(
        training_set(rows),
        threshold_version="regime-thresholds-v1",
        fitted_at=FITTED_AT,
    )
    future = ThresholdFitRow(
        symbol="US.MSFT",
        asset_group="stock",
        as_of=date(2026, 7, 10),
        source_max_available_at=datetime(2026, 7, 10, 21, tzinfo=UTC),
        dataset_revision="dataset-r1",
        feature_set_version="features-v1",
        calendar_version="test-calendar-v1",
        calendar_hash="b" * 64,
        calendar_source=CalendarSource.SYNTHETIC_OFFLINE,
        channel_width=0.005,
        efficiency_ratio20=0.95,
        absolute_normalized_slope20=0.00001,
        realized_volatility20=0.60,
    )

    first = apply_regime_thresholds(artifact, future)
    second = apply_regime_thresholds(artifact, future)

    assert first == second
    assert first.compressed is True
    assert first.efficient_trend is True
    assert first.flat_slope is True
    assert first.high_volatility is True


def test_fit_refuses_underfilled_groups_and_apply_refuses_wrong_group() -> None:
    rows = tuple(fit_row(index, asset_group="stock") for index in range(19))
    with pytest.raises(ValueError, match="at least 20"):
        fit_regime_thresholds(
            training_set(rows),
            threshold_version="regime-thresholds-v1",
            fitted_at=FITTED_AT,
        )

    enough = tuple(fit_row(index, asset_group="stock") for index in range(20))
    [artifact] = fit_regime_thresholds(
        training_set(enough),
        threshold_version="regime-thresholds-v1",
        fitted_at=FITTED_AT,
    )
    with pytest.raises(ValueError, match="asset group"):
        apply_regime_thresholds(artifact, fit_row(1, asset_group="etf"))


def test_fitted_at_must_follow_latest_source_availability() -> None:
    rows = tuple(fit_row(index, asset_group="stock") for index in range(20))
    with pytest.raises(ValueError, match="source availability"):
        fit_regime_thresholds(
            training_set(rows),
            threshold_version="regime-thresholds-v1",
            fitted_at=rows[-1].source_max_available_at - timedelta(seconds=1),
        )
