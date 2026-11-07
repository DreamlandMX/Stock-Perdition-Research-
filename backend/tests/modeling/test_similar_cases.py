from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stock_prediction.modeling.contracts import (
    EvaluationRole,
    EvaluationScope,
    EvaluationWindow,
    TaskName,
)
from stock_prediction.modeling.similar_cases import (
    DistanceConfig,
    SimilarCaseIndex,
    SimilarCaseInputError,
    SimilarCaseQuery,
    SimilarCaseRow,
)
from stock_prediction.modeling.training import TrainingPartitionEvidence


BASE = datetime(2022, 1, 3, 21, tzinfo=UTC)


def _row(
    name: str,
    day: int,
    *,
    values: tuple[float, float],
    label_days: int = 5,
    event: str | None = None,
    group: str | None = None,
    asset_group: str = "stock",
    quality: bool = True,
    forward_return: float = 0.02,
    mae: float = -0.03,
) -> SimilarCaseRow:
    as_of = BASE + timedelta(days=day)
    label_end = as_of + timedelta(days=label_days)
    return SimilarCaseRow(
        case_id=name,
        sample_key=("US.AAPL", as_of, 5),
        label_end=label_end,
        label_observed_at=label_end + timedelta(minutes=1),
        feature_values=values,
        range_event_id=event,
        independence_group_id=group or name,
        asset_group=asset_group,
        data_quality_pass=quality,
        forward_return=forward_return,
        direction_up=forward_return > 0,
        breakout_up=forward_return > 0.03,
        mae=mae,
    )


def _artifact(rows: tuple[SimilarCaseRow, ...]):
    keys = tuple(row.sample_key for row in rows)
    window = EvaluationWindow(
        window_id="split-v1.train",
        role=EvaluationRole.TRAIN,
        scope=EvaluationScope.SYNTHETIC_OFFLINE,
        start_session=min(key[1].date() for key in keys),
        end_session=max(row.label_observed_at.date() for row in rows),
        split_version="split-v1",
        dataset_input_hash="a" * 64,
        calendar_hash="b" * 64,
        consumed_at=None,
        consumption_reason=None,
    )
    partition = TrainingPartitionEvidence.register(
        partition_id="similar-cases-h5-train",
        task=TaskName.CAPITAL_GAIN_RETURN,
        horizon=5,
        evaluation_window=window,
        registered_sample_keys=keys,
        split_manifest_hash="c" * 64,
        source_fold_hash="d" * 64,
    )
    return SimilarCaseIndex.fit(
        rows,
        partition=partition,
        config=DistanceConfig(
            feature_names=("volatility", "compression"),
            feature_weights=(1.0, 2.0),
            minimum_independent_cases=3,
            distance_version="distance-v1",
        ),
    )


def test_query_excludes_future_overlap_event_quality_and_asset_mismatch() -> None:
    rows = (
        _row("valid-1", 0, values=(0.2, 0.3), label_days=2),
        _row("overlap", 8, values=(0.2, 0.3), label_days=8),
        _row("same-event", 1, values=(0.2, 0.3), label_days=2, event="range-q"),
        _row("bad-quality", 2, values=(0.2, 0.3), label_days=2, quality=False),
        _row("etf", 3, values=(0.2, 0.3), label_days=2, asset_group="etf"),
        _row("future", 20, values=(0.2, 0.3), label_days=2),
    )
    artifact = _artifact(rows)
    query_time = BASE + timedelta(days=10)
    result = artifact.query(
        SimilarCaseQuery(
            sample_key=("US.MSFT", query_time, 5),
            label_end=query_time + timedelta(days=5),
            feature_values=(0.2, 0.3),
            range_event_id="range-q",
            asset_group="stock",
            data_quality_pass=True,
        ),
        cutoff=query_time,
        top_k=20,
        expected_artifact_hash=artifact.artifact_hash,
    )

    assert result.case_ids == ("valid-1",)
    assert result.insufficient_evidence is True
    assert result.win_rate is None


def test_neighbor_group_keeps_nearest_and_ties_are_deterministic() -> None:
    rows = (
        _row("a", 0, values=(0.0, 0.0), label_days=1, group="cluster-1"),
        _row("b", 1, values=(0.1, 0.1), label_days=1, group="cluster-1"),
        _row("c", 2, values=(0.2, 0.2), label_days=1, group="cluster-2", forward_return=-0.01),
        _row("d", 3, values=(0.2, 0.2), label_days=1, group="cluster-3", forward_return=0.04, mae=-0.10),
    )
    artifact = _artifact(rows)
    query_time = BASE + timedelta(days=20)
    result = artifact.query(
        SimilarCaseQuery(
            sample_key=("US.MSFT", query_time, 5),
            label_end=query_time + timedelta(days=5),
            feature_values=(0.1, 0.1),
            range_event_id=None,
            asset_group="stock",
            data_quality_pass=True,
        ),
        cutoff=query_time,
        top_k=10,
        expected_artifact_hash=artifact.artifact_hash,
    )

    assert result.case_ids == ("b", "c", "d")
    assert result.effective_independent_count == 3
    assert result.insufficient_evidence is False
    assert result.win_rate == pytest.approx(2 / 3)
    assert result.worst_mae_case_id == "d"
    assert result.return_quantiles[0] <= result.return_quantiles[1] <= result.return_quantiles[2]


def test_fit_rejects_non_train_partition_and_tampered_artifact() -> None:
    rows = (
        _row("a", 0, values=(0.0, 0.0), label_days=1),
        _row("b", 1, values=(0.1, 0.1), label_days=1),
        _row("c", 2, values=(0.2, 0.2), label_days=1),
    )
    artifact = _artifact(rows)
    tampered = artifact.model_copy(update={"scales": (999.0, 999.0)})
    query_time = BASE + timedelta(days=20)
    query = SimilarCaseQuery(
        sample_key=("US.MSFT", query_time, 5),
        label_end=query_time + timedelta(days=5),
        feature_values=(0.1, 0.1),
        range_event_id=None,
        asset_group="stock",
        data_quality_pass=True,
    )
    with pytest.raises(SimilarCaseInputError, match="artifact"):
        tampered.query(
            query,
            cutoff=query_time,
            top_k=3,
            expected_artifact_hash=artifact.artifact_hash,
        )
    with pytest.raises(SimilarCaseInputError, match="cutoff"):
        artifact.query(
            query,
            cutoff=query_time - timedelta(days=1),
            top_k=3,
            expected_artifact_hash=artifact.artifact_hash,
        )


def test_fit_rejects_label_that_matures_after_train_window() -> None:
    rows = (
        _row("a", 0, values=(0.0, 0.0), label_days=5),
        _row("b", 1, values=(0.1, 0.1), label_days=5),
        _row("c", 2, values=(0.2, 0.2), label_days=5),
    )
    keys = tuple(row.sample_key for row in rows)
    window = EvaluationWindow(
        window_id="split-v1.immature-train",
        role=EvaluationRole.TRAIN,
        scope=EvaluationScope.SYNTHETIC_OFFLINE,
        start_session=keys[0][1].date(),
        end_session=keys[-1][1].date(),
        split_version="split-v1",
        dataset_input_hash="a" * 64,
        calendar_hash="b" * 64,
        consumed_at=None,
        consumption_reason=None,
    )
    partition = TrainingPartitionEvidence.register(
        partition_id="similar-immature-h5",
        task=TaskName.CAPITAL_GAIN_RETURN,
        horizon=5,
        evaluation_window=window,
        registered_sample_keys=keys,
        split_manifest_hash="c" * 64,
        source_fold_hash="d" * 64,
    )
    with pytest.raises(SimilarCaseInputError, match="not mature"):
        SimilarCaseIndex.fit(
            rows,
            partition=partition,
            config=DistanceConfig(
                feature_names=("volatility", "compression"),
                feature_weights=(1.0, 1.0),
                minimum_independent_cases=3,
                distance_version="distance-v1",
            ),
        )
