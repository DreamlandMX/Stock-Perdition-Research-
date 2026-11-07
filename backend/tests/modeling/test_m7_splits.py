from __future__ import annotations

from datetime import UTC, date, datetime, time

import pandas as pd
import pytest

from stock_prediction.modeling.contracts import M7FitComponent, M7PartitionRole
from stock_prediction.modeling.splits import (
    SplitBuildError,
    bind_m7_fit_evidence,
    bind_m7_horizon_fit_evidence,
    build_m7_fold_partitions,
    build_m7_sequence_window_evidence,
)


def _calendar(count: int = 700) -> tuple[date, ...]:
    return tuple(value.date() for value in pd.bdate_range("2022-01-03", periods=count))


def _at_close(session: date) -> datetime:
    return datetime.combine(session, time(21), tzinfo=UTC)


def _sample_keys(sessions: tuple[date, ...]):
    return tuple(
        (symbol, _at_close(session), horizon)
        for session in sessions
        for symbol in ("US.AAPL", "US.SPY")
        for horizon in (1, 5, 20)
    )


def _partitions():
    calendar = _calendar()
    return build_m7_fold_partitions(
        fold_id="m7-fold-0",
        split_version="m7-split-v1",
        dataset_input_hash="a" * 64,
        calendar_sessions=calendar,
        source_sample_keys=_sample_keys(calendar),
        outer_test_start=calendar[550],
        training_start=calendar[20],
    )


def test_m7_fold_uses_exact_116_20_116_20_126_geometry() -> None:
    partitions = _partitions()

    assert len(partitions.selection_sessions) == 116
    assert len(partitions.calibration_purge_sessions) == 20
    assert len(partitions.calibration_sessions) == 116
    assert len(partitions.outer_embargo_sessions) == 20
    assert len(partitions.outer_test_sessions) == 126
    assert partitions.selection_sessions[-1] < partitions.calibration_purge_sessions[0]
    assert (
        partitions.calibration_purge_sessions[-1] < partitions.calibration_sessions[0]
    )
    assert partitions.calibration_sessions[-1] < partitions.outer_embargo_sessions[0]
    assert partitions.outer_embargo_sessions[-1] < partitions.outer_test_sessions[0]
    assert len(partitions.partition_hash) == 64


def test_fit_evidence_is_bound_to_the_only_allowed_partition() -> None:
    partitions = _partitions()
    scaler = bind_m7_fit_evidence(
        partitions,
        component=M7FitComponent.SCALER,
        sample_keys=partitions.train_sample_keys,
    )
    early_stopping = bind_m7_fit_evidence(
        partitions,
        component=M7FitComponent.EARLY_STOPPING,
        sample_keys=partitions.selection_sample_keys,
    )
    calibrator = bind_m7_fit_evidence(
        partitions,
        component=M7FitComponent.CALIBRATOR,
        sample_keys=partitions.calibration_sample_keys,
    )

    assert scaler.fit_role is M7PartitionRole.TRAIN
    assert early_stopping.fit_role is M7PartitionRole.SELECTION
    assert calibrator.fit_role is M7PartitionRole.CALIBRATION
    assert scaler.partition_hash == partitions.partition_hash

    with pytest.raises(SplitBuildError, match="forbidden_partition"):
        bind_m7_fit_evidence(
            partitions,
            component=M7FitComponent.CLASS_WEIGHTS,
            sample_keys=partitions.selection_sample_keys,
        )
    with pytest.raises(SplitBuildError, match="exact_partition"):
        bind_m7_fit_evidence(
            partitions,
            component=M7FitComponent.BLEND_WEIGHTS,
            sample_keys=partitions.calibration_sample_keys[:-1],
        )


def test_horizon_fit_evidence_requires_the_complete_allowed_horizon_slice() -> None:
    partitions = _partitions()
    expected = tuple(key for key in partitions.calibration_sample_keys if key[2] == 20)

    evidence = bind_m7_horizon_fit_evidence(
        partitions,
        component=M7FitComponent.CALIBRATOR,
        horizon=20,
        sample_keys=expected,
    )

    assert evidence.sample_keys == expected
    assert evidence.fit_role is M7PartitionRole.CALIBRATION
    with pytest.raises(SplitBuildError, match="exact_horizon_partition"):
        bind_m7_horizon_fit_evidence(
            partitions,
            component=M7FitComponent.BLEND_WEIGHTS,
            horizon=20,
            sample_keys=expected[:-1],
        )


def test_purge_and_outer_embargo_keys_are_never_retained() -> None:
    partitions = _partitions()
    retained_sessions = {
        key[1].date()
        for key in (
            *partitions.train_sample_keys,
            *partitions.selection_sample_keys,
            *partitions.calibration_sample_keys,
            *partitions.outer_test_sample_keys,
        )
    }

    assert retained_sessions.isdisjoint(partitions.calibration_purge_sessions)
    assert retained_sessions.isdisjoint(partitions.outer_embargo_sessions)


def test_sparse_nominal_partition_fails_instead_of_shrinking() -> None:
    calendar = _calendar()
    keys = tuple(
        key
        for key in _sample_keys(calendar)
        if not (key[1].date() == calendar[400] and key[2] == 20)
    )

    with pytest.raises(SplitBuildError, match="nominal_partition_incomplete"):
        build_m7_fold_partitions(
            fold_id="m7-fold-0",
            split_version="m7-split-v1",
            dataset_input_hash="a" * 64,
            calendar_sessions=calendar,
            source_sample_keys=keys,
            outer_test_start=calendar[550],
            training_start=calendar[20],
        )


def test_sequence_window_ends_at_anchor_and_preserves_sample_identity() -> None:
    partitions = _partitions()
    sample_key = partitions.selection_sample_keys[80]
    calendar = _calendar()
    anchor_position = calendar.index(sample_key[1].date())
    input_sessions = calendar[anchor_position - 63 : anchor_position + 1]

    evidence = build_m7_sequence_window_evidence(
        partitions,
        sample_key=sample_key,
        input_sessions=input_sessions,
        sequence_length=64,
    )

    assert evidence.sample_key == sample_key
    assert evidence.input_sessions[-1] == sample_key[1].date()
    assert evidence.sequence_length == 64
    assert len(evidence.window_hash) == 64

    with pytest.raises(SplitBuildError, match="must_end_at_anchor"):
        build_m7_sequence_window_evidence(
            partitions,
            sample_key=sample_key,
            input_sessions=calendar[anchor_position - 62 : anchor_position + 2],
            sequence_length=64,
        )


def test_geometry_requires_enough_history_and_test_sessions() -> None:
    calendar = _calendar(300)
    with pytest.raises(SplitBuildError, match="insufficient_m7_geometry"):
        build_m7_fold_partitions(
            fold_id="m7-fold-0",
            split_version="m7-split-v1",
            dataset_input_hash="a" * 64,
            calendar_sessions=calendar,
            source_sample_keys=_sample_keys(calendar),
            outer_test_start=calendar[200],
            training_start=calendar[0],
        )
