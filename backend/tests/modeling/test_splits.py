from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from pydantic import ValidationError

from stock_prediction.contracts.research import (
    BreakoutEvent,
    CalendarSource,
    DirectionLabel,
    HorizonTarget,
)
from stock_prediction.modeling.contracts import EvaluationRole, EvaluationScope
from stock_prediction.modeling.splits import (
    SplitBuildError,
    WalkForwardSpec,
    build_walk_forward_manifest,
)
from stock_prediction.research.calendar import (
    TradingCalendarArtifact,
    build_trading_calendar,
)
from stock_prediction.research.dataset import (
    ResearchDataset,
    ResearchSample,
    compute_research_dataset_input_hash,
)


def _at_close(session: date) -> datetime:
    return datetime.combine(session, time(20), tzinfo=UTC)


def _weekdays(start: date, end: date) -> tuple[date, ...]:
    sessions: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            sessions.append(current)
        current += timedelta(days=1)
    return tuple(sessions)


def _calendar() -> TradingCalendarArtifact:
    return build_trading_calendar(
        sessions=_weekdays(date(2015, 1, 2), date(2022, 8, 5)),
        calendar_version="offline-calendar-v1",
        session_rule_version="weekday-test-v1",
        dataset_revision="dataset-r1",
        source=CalendarSource.SYNTHETIC_OFFLINE,
        source_reference_ids=("weekday-fixture-v1",),
        generated_at=datetime(2026, 7, 12, 12, tzinfo=UTC),
    )


def _sample(
    session: date,
    label_end_session: date,
    *,
    calendar: TradingCalendarArtifact,
) -> ResearchSample:
    anchor = _at_close(session)
    label_end = _at_close(label_end_session)
    target = HorizonTarget(
        horizon=20,
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
        range_exit=False,
        range_exit_at=None,
        label_end_at=label_end,
        resolved=True,
    )
    return ResearchSample(
        symbol="US.AAPL",
        as_of=anchor,
        horizon=20,
        numeric_features={"return_20": 0.01},
        categorical_features={"state": "range"},
        transform_flags={},
        ood_score=0.0,
        target=target,
        feature_set_version="core-v1",
        label_version="labels-v1",
        threshold_version="threshold-v1",
        calendar_version=calendar.calendar_version,
        calendar_hash=calendar.input_hash,
        calendar_source=calendar.source,
        transformer_version="transformer-v1",
        transformer_input_hash="d" * 64,
        transformer_manifest_hash="e" * 64,
        universe_version="universe-v1",
        dataset_revision="dataset-r1",
        adjustment_revision="adjustment-r1",
        feature_lineage_hash="f" * 64,
        feature_content_hash="a" * 64,
        feature_values_hash="b" * 64,
        feature_source_max_available_at=anchor,
        feature_source_max_ingested_at=anchor + timedelta(minutes=1),
        data_quality_score=1.0,
        label_start_at=anchor + timedelta(seconds=1),
        label_end_at=label_end,
    )


def _dataset(calendar: TradingCalendarArtifact) -> ResearchDataset:
    sessions = calendar.sessions
    last_anchor = sessions.index(date(2022, 7, 1))
    samples = tuple(
        _sample(session, sessions[index + 20], calendar=calendar)
        for index, session in enumerate(sessions[: last_anchor + 1])
    )
    input_hash = compute_research_dataset_input_hash(samples)
    return ResearchDataset(
        dataset_id=f"research-{input_hash[:24]}",
        input_hash=input_hash,
        feature_set_version="core-v1",
        label_version="labels-v1",
        threshold_version="threshold-v1",
        transformer_version="transformer-v1",
        transformer_input_hash="d" * 64,
        transformer_manifest_hash="e" * 64,
        universe_version="universe-v1",
        dataset_revision="dataset-r1",
        calendar_version=calendar.calendar_version,
        calendar_hash=calendar.input_hash,
        calendar_source=calendar.source,
        sample_count=len(samples),
        samples=samples,
    )


@pytest.fixture(scope="module")
def research_inputs() -> tuple[ResearchDataset, TradingCalendarArtifact]:
    calendar = _calendar()
    return _dataset(calendar), calendar


def _spec(**updates: object) -> WalkForwardSpec:
    values: dict[str, object] = {
        "train_years": 5,
        "validation_months": 12,
        "test_months": 6,
        "step_months": 6,
        "embargo_sessions": 20,
        "minimum_outer_folds": 3,
        "evaluation_scope": EvaluationScope.SYNTHETIC_OFFLINE,
    }
    values.update(updates)
    return WalkForwardSpec(**values)


def test_outer_test_step_cannot_overlap_or_gap() -> None:
    with pytest.raises(ValidationError, match="step_months"):
        _spec(step_months=3)
    with pytest.raises(ValidationError, match="step_months"):
        _spec(step_months=9)


def test_builds_three_expanding_folds_with_fixed_validation_order(
    research_inputs: tuple[ResearchDataset, TradingCalendarArtifact],
) -> None:
    dataset, calendar = research_inputs
    manifest = build_walk_forward_manifest(
        dataset, calendar, _spec(), split_version="split-v1"
    )

    assert len(manifest.outer_folds) == 3
    first, second, third = manifest.outer_folds
    assert first.train_start_session == date(2015, 1, 2)
    assert first.calibration_start_session == date(2020, 1, 2)
    assert first.selection_start_session == date(2020, 7, 2)
    assert first.test_start_session == date(2021, 1, 4)
    assert first.test_end_session == date(2021, 7, 1)
    assert first.calibration_end_session < first.selection_start_session
    assert first.selection_end_session < first.test_start_session
    assert second.train_sample_keys == first.train_sample_keys
    assert second.calibration_sample_keys == first.calibration_sample_keys
    assert second.selection_sample_keys == first.selection_sample_keys
    assert third.calibration_sample_keys == first.calibration_sample_keys
    prior_outer = set(first.test_sample_keys)
    assert prior_outer.isdisjoint(second.train_sample_keys)
    assert prior_outer.isdisjoint(second.calibration_sample_keys)
    assert prior_outer.isdisjoint(second.selection_sample_keys)
    assert prior_outer.isdisjoint(second.test_sample_keys)
    assert third.test_end_session == date(2022, 7, 1)
    assert all(fold.dataset_input_hash == dataset.input_hash for fold in manifest.outer_folds)
    assert len(manifest.manifest_hash) == 64
    by_key = {
        (sample.symbol, sample.as_of, sample.horizon): sample
        for sample in dataset.samples
    }
    for previous, following in zip(
        manifest.outer_folds,
        manifest.outer_folds[1:],
        strict=False,
    ):
        next_start_at = _at_close(following.test_start_session)
        assert all(
            by_key[key].label_end_at < next_start_at
            for key in previous.test_sample_keys
        )
        assert (
            len(previous.boundary_embargo_sessions["test_to_next_outer"])
            == 20
        )


def test_purge_uses_label_end_instead_of_anchor_only(
    research_inputs: tuple[ResearchDataset, TradingCalendarArtifact],
) -> None:
    dataset, calendar = research_inputs
    first = build_walk_forward_manifest(
        dataset, calendar, _spec(), split_version="split-v1"
    ).outer_folds[0]
    overlapping = next(
        sample
        for sample in reversed(dataset.samples)
        if sample.as_of < _at_close(first.calibration_start_session)
        and sample.label_end_at >= _at_close(first.calibration_start_session)
    )
    key = (overlapping.symbol, overlapping.as_of, overlapping.horizon)

    assert key in first.purged_sample_keys
    assert key not in first.train_sample_keys
    by_key = {
        (sample.symbol, sample.as_of, sample.horizon): sample
        for sample in dataset.samples
    }
    assert all(
        by_key[sample_key].label_end_at
        < _at_close(first.calibration_start_session)
        for sample_key in first.train_sample_keys
    )


def test_each_boundary_has_twenty_verified_embargo_sessions(
    research_inputs: tuple[ResearchDataset, TradingCalendarArtifact],
) -> None:
    dataset, calendar = research_inputs
    first = build_walk_forward_manifest(
        dataset, calendar, _spec(), split_version="split-v1"
    ).outer_folds[0]

    assert len(first.embargo_sessions) == 20
    assert set(first.embargo_sessions).issubset(calendar.sessions)
    assert set(first.embargo_sessions).isdisjoint(first.train_anchor_sessions)
    assert set(first.boundary_embargo_sessions) == {
        "train_to_calibration",
        "calibration_to_selection",
        "selection_to_test",
        "test_to_next_outer",
    }
    assert all(
        len(sessions) == 20
        for sessions in first.boundary_embargo_sessions.values()
    )
    retained = {
        "train_to_calibration": set(first.train_anchor_sessions),
        "calibration_to_selection": set(first.calibration_anchor_sessions),
        "selection_to_test": set(first.selection_anchor_sessions),
        "test_to_next_outer": set(first.test_anchor_sessions),
    }
    assert all(
        set(first.boundary_embargo_sessions[name]).isdisjoint(retained[name])
        for name in retained
    )


def test_manifest_rejects_insufficient_history() -> None:
    calendar = build_trading_calendar(
        sessions=_weekdays(date(2020, 1, 2), date(2022, 8, 5)),
        calendar_version="short-calendar-v1",
        session_rule_version="weekday-test-v1",
        dataset_revision="dataset-r1",
        source=CalendarSource.SYNTHETIC_OFFLINE,
        source_reference_ids=("short-weekday-fixture-v1",),
        generated_at=datetime(2026, 7, 12, 12, tzinfo=UTC),
    )
    dataset = _dataset(calendar)

    with pytest.raises(SplitBuildError, match="outer folds|history"):
        build_walk_forward_manifest(
            dataset, calendar, _spec(), split_version="split-v1"
        )


def test_production_rejects_synthetic_calendar(
    research_inputs: tuple[ResearchDataset, TradingCalendarArtifact],
) -> None:
    dataset, calendar = research_inputs

    with pytest.raises(SplitBuildError, match="verified OpenD calendar"):
        build_walk_forward_manifest(
            dataset,
            calendar,
            _spec(evaluation_scope=EvaluationScope.PRODUCTION),
            split_version="split-v1",
        )


def test_dataset_and_calendar_evidence_must_match(
    research_inputs: tuple[ResearchDataset, TradingCalendarArtifact],
) -> None:
    dataset, calendar = research_inputs
    mismatched = dataset.model_copy(update={"calendar_hash": "0" * 64})

    with pytest.raises(SplitBuildError, match="calendar hash"):
        build_walk_forward_manifest(
            mismatched, calendar, _spec(), split_version="split-v1"
        )


def test_dataset_content_must_match_its_input_hash(
    research_inputs: tuple[ResearchDataset, TradingCalendarArtifact],
) -> None:
    dataset, calendar = research_inputs
    first = dataset.samples[0]
    altered_first = first.model_copy(
        update={"numeric_features": {"return_20": 99.0}}
    )
    tampered = dataset.model_copy(
        update={"samples": (altered_first, *dataset.samples[1:])}
    )

    with pytest.raises(SplitBuildError, match="input hash"):
        build_walk_forward_manifest(
            tampered, calendar, _spec(), split_version="split-v1"
        )


def test_sparse_partition_cannot_shrink_nominal_window_silently(
    research_inputs: tuple[ResearchDataset, TradingCalendarArtifact],
) -> None:
    dataset, calendar = research_inputs
    sparse_samples = tuple(
        sample
        for sample in dataset.samples
        if not (date(2020, 1, 1) <= sample.as_of.date() <= date(2020, 1, 31))
    )
    sparse_hash = compute_research_dataset_input_hash(sparse_samples)
    sparse = dataset.model_copy(
        update={
            "dataset_id": f"research-{sparse_hash[:24]}",
            "input_hash": sparse_hash,
            "sample_count": len(sparse_samples),
            "samples": sparse_samples,
        }
    )

    with pytest.raises(SplitBuildError, match="nominal calendar session"):
        build_walk_forward_manifest(
            sparse, calendar, _spec(), split_version="split-v1"
        )


def test_minimum_outer_folds_are_enforced_per_horizon(
    research_inputs: tuple[ResearchDataset, TradingCalendarArtifact],
) -> None:
    dataset, calendar = research_inputs
    early_horizon_one = tuple(
        sample.model_copy(
            update={
                "horizon": 1,
                "target": sample.target.model_copy(
                    update={"horizon": 1, "range_exit": None}
                ),
            }
        )
        for sample in dataset.samples[:100]
    )
    mixed_samples = tuple(
        sorted(
            (*dataset.samples, *early_horizon_one),
            key=lambda sample: (sample.as_of, sample.symbol, sample.horizon),
        )
    )
    mixed_hash = compute_research_dataset_input_hash(mixed_samples)
    mixed = dataset.model_copy(
        update={
            "dataset_id": f"research-{mixed_hash[:24]}",
            "input_hash": mixed_hash,
            "sample_count": len(mixed_samples),
            "samples": mixed_samples,
        }
    )

    with pytest.raises(SplitBuildError, match="horizon 1"):
        build_walk_forward_manifest(
            mixed, calendar, _spec(), split_version="split-v1"
        )


def test_lockbox_coverage_is_enforced_per_horizon(
    research_inputs: tuple[ResearchDataset, TradingCalendarArtifact],
) -> None:
    dataset, calendar = research_inputs
    horizon_one = tuple(
        sample.model_copy(
            update={
                "horizon": 1,
                "target": sample.target.model_copy(
                    update={"horizon": 1, "range_exit": None}
                ),
            }
        )
        for sample in dataset.samples
        if sample.as_of.date() <= date(2021, 7, 1)
    )
    mixed_samples = tuple(
        sorted(
            (*dataset.samples, *horizon_one),
            key=lambda sample: (sample.as_of, sample.symbol, sample.horizon),
        )
    )
    mixed_hash = compute_research_dataset_input_hash(mixed_samples)
    mixed = dataset.model_copy(
        update={
            "dataset_id": f"research-{mixed_hash[:24]}",
            "input_hash": mixed_hash,
            "sample_count": len(mixed_samples),
            "samples": mixed_samples,
        }
    )

    with pytest.raises(SplitBuildError, match="lockbox horizon 1"):
        build_walk_forward_manifest(
            mixed,
            calendar,
            _spec(minimum_outer_folds=1),
            split_version="split-v1",
            lockbox_start=date(2021, 7, 2),
        )


def test_manifest_is_deterministic_and_binds_split_version(
    research_inputs: tuple[ResearchDataset, TradingCalendarArtifact],
) -> None:
    dataset, calendar = research_inputs
    first = build_walk_forward_manifest(
        dataset, calendar, _spec(), split_version="split-v1"
    )
    repeated = build_walk_forward_manifest(
        dataset, calendar, _spec(), split_version="split-v1"
    )
    changed = build_walk_forward_manifest(
        dataset, calendar, _spec(), split_version="split-v2"
    )

    assert repeated == first
    assert changed.manifest_hash != first.manifest_hash


def test_fold_and_manifest_hashes_reject_tampering_and_nested_bypass(
    research_inputs: tuple[ResearchDataset, TradingCalendarArtifact],
) -> None:
    dataset, calendar = research_inputs
    manifest = build_walk_forward_manifest(
        dataset, calendar, _spec(), split_version="split-v1"
    )
    first = manifest.outer_folds[0]

    with pytest.raises(ValidationError, match="fold_hash"):
        type(first).model_validate(
            {**first.model_dump(), "test_end_session": date(2021, 6, 30)},
            strict=True,
        )

    inconsistent_fold = first.model_dump(exclude={"fold_hash"})
    inconsistent_fold["train_anchor_sessions"] = first.train_anchor_sessions[1:]
    with pytest.raises(ValidationError, match="anchor sessions"):
        type(first).model_validate(inconsistent_fold, strict=True)

    inconsistent_manifest = manifest.model_dump(exclude={"manifest_hash"})
    inconsistent_manifest["spec"] = _spec(embargo_sessions=19)
    with pytest.raises(ValidationError, match="embargo count"):
        type(manifest).model_validate(inconsistent_manifest, strict=True)
    with pytest.raises(ValidationError, match="manifest_hash"):
        type(manifest).model_validate(
            {**manifest.model_dump(), "dataset_revision": "dataset-r2"},
            strict=True,
        )

    bypassed = first.model_copy(
        update={"test_sample_keys": first.test_sample_keys[1:]}
    )
    with pytest.raises(ValidationError, match="fold_hash"):
        type(manifest).model_validate(
            {
                **manifest.model_dump(exclude={"manifest_hash", "outer_folds"}),
                "outer_folds": (bypassed, *manifest.outer_folds[1:]),
            },
            strict=True,
        )

    second = manifest.outer_folds[1]
    inconsistent_second_payload = second.model_dump(exclude={"fold_hash"})
    inconsistent_second_payload["calibration_sample_keys"] = (
        second.calibration_sample_keys[1:]
    )
    inconsistent_second_payload["calibration_anchor_sessions"] = (
        second.calibration_anchor_sessions[1:]
    )
    inconsistent_second = type(second).model_validate(
        inconsistent_second_payload, strict=True
    )
    with pytest.raises(ValidationError, match="classification is incomplete"):
        type(manifest).model_validate(
            {
                **manifest.model_dump(exclude={"manifest_hash", "outer_folds"}),
                "outer_folds": (
                    manifest.outer_folds[0],
                    inconsistent_second,
                    *manifest.outer_folds[2:],
                ),
            },
            strict=True,
        )

    consistently_omitted_folds = []
    for fold in manifest.outer_folds:
        fold_payload = fold.model_dump(exclude={"fold_hash"})
        fold_payload["calibration_sample_keys"] = fold.calibration_sample_keys[1:]
        fold_payload["calibration_anchor_sessions"] = (
            fold.calibration_anchor_sessions[1:]
        )
        consistently_omitted_folds.append(
            type(fold).model_validate(fold_payload, strict=True)
        )
    with pytest.raises(ValidationError, match="classification is incomplete"):
        type(manifest).model_validate(
            {
                **manifest.model_dump(exclude={"manifest_hash", "outer_folds"}),
                "outer_folds": tuple(consistently_omitted_folds),
            },
            strict=True,
        )


def test_lockbox_start_reserves_a_separate_unconsumed_window(
    research_inputs: tuple[ResearchDataset, TradingCalendarArtifact],
) -> None:
    dataset, calendar = research_inputs
    manifest = build_walk_forward_manifest(
        dataset,
        calendar,
        _spec(minimum_outer_folds=2),
        split_version="split-v1",
        lockbox_start=date(2022, 1, 3),
    )

    assert len(manifest.outer_folds) == 2
    assert manifest.lockbox_window is not None
    assert manifest.lockbox_window.role is EvaluationRole.LOCKBOX
    assert manifest.lockbox_window.start_session == date(2022, 1, 3)
    assert manifest.lockbox_window.consumed_at is None
    assert manifest.lockbox_sample_keys
    final_test = manifest.outer_folds[-1]
    by_key = {
        (sample.symbol, sample.as_of, sample.horizon): sample
        for sample in dataset.samples
    }
    lockbox_start_at = _at_close(manifest.lockbox_window.start_session)
    assert all(
        by_key[key].label_end_at < lockbox_start_at
        for key in final_test.test_sample_keys
    )
    assert len(final_test.boundary_embargo_sessions["test_to_lockbox"]) == 20

    final_payload = final_test.model_dump(exclude={"fold_hash"})
    boundary_evidence = dict(final_test.boundary_embargo_sessions)
    boundary_evidence.pop("test_to_lockbox")
    final_payload["boundary_embargo_sessions"] = boundary_evidence
    missing_boundary = type(final_test).model_validate(final_payload, strict=True)
    with pytest.raises(ValidationError, match="lockbox embargo evidence"):
        type(manifest).model_validate(
            {
                **manifest.model_dump(exclude={"manifest_hash", "outer_folds"}),
                "outer_folds": (*manifest.outer_folds[:-1], missing_boundary),
            },
            strict=True,
        )

    missing_key_payload = manifest.model_dump(exclude={"manifest_hash"})
    missing_key_payload["lockbox_sample_keys"] = manifest.lockbox_sample_keys[:-1]
    with pytest.raises(ValidationError, match="exact source subset"):
        type(manifest).model_validate(missing_key_payload, strict=True)
