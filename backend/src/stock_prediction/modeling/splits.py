"""Purged expanding walk-forward manifests over immutable M3 datasets."""

from __future__ import annotations

from bisect import bisect_left
from calendar import monthrange
from collections.abc import Sequence
from datetime import date

from pydantic import ValidationError

from stock_prediction.contracts.research import CalendarSource, Horizon
from stock_prediction.modeling.contracts import (
    EvaluationRole,
    EvaluationScope,
    EvaluationWindow,
    OuterFold,
    M7FitComponent,
    M7FittedObjectEvidence,
    M7FoldPartitions,
    M7PartitionRole,
    M7SequenceWindowEvidence,
    SampleKey,
    SplitManifest,
    WalkForwardSpec,
    hash_m7_sample_keys,
)
from stock_prediction.research.calendar import TradingCalendarArtifact
from stock_prediction.research.dataset import (
    ResearchDataset,
    ResearchSample,
    compute_research_dataset_input_hash,
)


class SplitBuildError(ValueError):
    """Dataset, calendar, or split evidence cannot form an auditable manifest."""


_M7_FIT_ROLE_BY_COMPONENT = {
    M7FitComponent.SCALER: M7PartitionRole.TRAIN,
    M7FitComponent.IMPUTER: M7PartitionRole.TRAIN,
    M7FitComponent.WINSOR_LIMITS: M7PartitionRole.TRAIN,
    M7FitComponent.CATEGORY_VOCABULARY: M7PartitionRole.TRAIN,
    M7FitComponent.LABEL_THRESHOLDS: M7PartitionRole.TRAIN,
    M7FitComponent.CLASS_WEIGHTS: M7PartitionRole.TRAIN,
    M7FitComponent.MODEL: M7PartitionRole.TRAIN,
    M7FitComponent.EARLY_STOPPING: M7PartitionRole.SELECTION,
    M7FitComponent.CALIBRATOR: M7PartitionRole.CALIBRATION,
    M7FitComponent.BLEND_WEIGHTS: M7PartitionRole.CALIBRATION,
}


def build_m7_fold_partitions(
    *,
    fold_id: str,
    split_version: str,
    dataset_input_hash: str,
    calendar_sessions: Sequence[date],
    source_sample_keys: Sequence[SampleKey],
    outer_test_start: date,
    training_start: date,
) -> M7FoldPartitions:
    """Build exact 116/20/116/20/126 M7 partitions from a verified calendar."""

    sessions = tuple(calendar_sessions)
    if not sessions or tuple(sorted(set(sessions))) != sessions:
        raise SplitBuildError("m7_calendar_sessions_not_canonical")
    try:
        test_position = sessions.index(outer_test_start)
        training_position = sessions.index(training_start)
    except ValueError as error:
        raise SplitBuildError("m7_partition_boundary_outside_calendar") from error
    validation_start = test_position - 20 - 252
    if validation_start <= training_position or test_position + 126 > len(sessions):
        raise SplitBuildError("insufficient_m7_geometry")
    train_sessions = sessions[training_position:validation_start]
    selection_sessions = sessions[validation_start : validation_start + 116]
    calibration_purge_sessions = sessions[
        validation_start + 116 : validation_start + 136
    ]
    calibration_sessions = sessions[validation_start + 136 : validation_start + 252]
    outer_embargo_sessions = sessions[test_position - 20 : test_position]
    outer_test_sessions = sessions[test_position : test_position + 126]
    keys = tuple(source_sample_keys)
    if not keys or len(set(keys)) != len(keys):
        raise SplitBuildError("m7_source_sample_keys_invalid")
    if tuple(sorted(keys, key=lambda key: (key[1], key[0], key[2]))) != keys:
        raise SplitBuildError("m7_source_sample_keys_not_canonical")
    geometry_sessions = (
        *train_sessions,
        *selection_sessions,
        *calibration_purge_sessions,
        *calibration_sessions,
        *outer_embargo_sessions,
        *outer_test_sessions,
    )
    _require_m7_nominal_coverage(keys, geometry_sessions)

    def keys_for(partition_sessions: tuple[date, ...]) -> tuple[SampleKey, ...]:
        allowed = set(partition_sessions)
        return tuple(key for key in keys if key[1].date() in allowed)

    try:
        return M7FoldPartitions(
            fold_id=fold_id,
            split_version=split_version,
            dataset_input_hash=dataset_input_hash,
            train_sessions=train_sessions,
            selection_sessions=selection_sessions,
            calibration_purge_sessions=calibration_purge_sessions,
            calibration_sessions=calibration_sessions,
            outer_embargo_sessions=outer_embargo_sessions,
            outer_test_sessions=outer_test_sessions,
            train_sample_keys=keys_for(train_sessions),
            selection_sample_keys=keys_for(selection_sessions),
            calibration_sample_keys=keys_for(calibration_sessions),
            outer_test_sample_keys=keys_for(outer_test_sessions),
        )
    except ValidationError as error:
        raise SplitBuildError(f"m7_partition_contract_invalid:{error}") from error


def bind_m7_fit_evidence(
    partitions: M7FoldPartitions,
    *,
    component: M7FitComponent,
    sample_keys: Sequence[SampleKey],
) -> M7FittedObjectEvidence:
    """Bind a fitted object to the one partition its semantic role permits."""

    role = _M7_FIT_ROLE_BY_COMPONENT[component]
    expected_by_role = {
        M7PartitionRole.TRAIN: partitions.train_sample_keys,
        M7PartitionRole.SELECTION: partitions.selection_sample_keys,
        M7PartitionRole.CALIBRATION: partitions.calibration_sample_keys,
        M7PartitionRole.OUTER_TEST: partitions.outer_test_sample_keys,
    }
    supplied = tuple(sample_keys)
    expected = expected_by_role[role]
    forbidden = set(supplied) & set(
        key
        for other_role, keys in expected_by_role.items()
        if other_role is not role
        for key in keys
    )
    if forbidden:
        raise SplitBuildError("m7_fit_uses_forbidden_partition")
    if supplied != expected:
        raise SplitBuildError("m7_fit_requires_exact_partition")
    try:
        return M7FittedObjectEvidence(
            component=component,
            fit_role=role,
            partition_hash=partitions.partition_hash,
            sample_keys=supplied,
            sample_keys_hash=hash_m7_sample_keys(supplied),
        )
    except ValidationError as error:
        raise SplitBuildError(f"m7_fit_evidence_invalid:{error}") from error


def bind_m7_horizon_fit_evidence(
    partitions: M7FoldPartitions,
    *,
    component: M7FitComponent,
    horizon: int,
    sample_keys: Sequence[SampleKey],
) -> M7FittedObjectEvidence:
    """Bind a horizon-local fit to the complete allowed partition slice."""

    if horizon not in (1, 5, 20):
        raise SplitBuildError("m7_fit_horizon_invalid")
    role = _M7_FIT_ROLE_BY_COMPONENT[component]
    complete_by_role = {
        M7PartitionRole.TRAIN: partitions.train_sample_keys,
        M7PartitionRole.SELECTION: partitions.selection_sample_keys,
        M7PartitionRole.CALIBRATION: partitions.calibration_sample_keys,
        M7PartitionRole.OUTER_TEST: partitions.outer_test_sample_keys,
    }
    expected_by_role = {
        partition_role: tuple(key for key in keys if key[2] == horizon)
        for partition_role, keys in complete_by_role.items()
    }
    supplied = tuple(sample_keys)
    expected = expected_by_role[role]
    forbidden = set(supplied) & set(
        key
        for other_role, keys in expected_by_role.items()
        if other_role is not role
        for key in keys
    )
    if forbidden:
        raise SplitBuildError("m7_fit_uses_forbidden_partition")
    if supplied != expected:
        raise SplitBuildError("m7_fit_requires_exact_horizon_partition")
    try:
        return M7FittedObjectEvidence(
            component=component,
            fit_role=role,
            partition_hash=partitions.partition_hash,
            sample_keys=supplied,
            sample_keys_hash=hash_m7_sample_keys(supplied),
        )
    except ValidationError as error:
        raise SplitBuildError(f"m7_fit_evidence_invalid:{error}") from error


def build_m7_sequence_window_evidence(
    partitions: M7FoldPartitions,
    *,
    sample_key: SampleKey,
    input_sessions: Sequence[date],
    sequence_length: int,
) -> M7SequenceWindowEvidence:
    retained = {
        *partitions.train_sample_keys,
        *partitions.selection_sample_keys,
        *partitions.calibration_sample_keys,
        *partitions.outer_test_sample_keys,
    }
    if sample_key not in retained:
        raise SplitBuildError("m7_sequence_sample_key_not_retained")
    sessions = tuple(input_sessions)
    if not sessions or sessions[-1] != sample_key[1].date():
        raise SplitBuildError("m7_sequence_must_end_at_anchor")
    if len(sessions) != sequence_length:
        raise SplitBuildError("m7_sequence_length_mismatch")
    try:
        return M7SequenceWindowEvidence(
            partition_hash=partitions.partition_hash,
            sample_key=sample_key,
            sequence_length=sequence_length,
            input_sessions=sessions,
        )
    except ValidationError as error:
        raise SplitBuildError(f"m7_sequence_evidence_invalid:{error}") from error


def _require_m7_nominal_coverage(
    sample_keys: Sequence[SampleKey],
    expected_sessions: Sequence[date],
) -> None:
    by_session_horizon = {(key[1].date(), key[2]) for key in sample_keys}
    missing = [
        (session, horizon)
        for session in expected_sessions
        for horizon in (1, 5, 20)
        if (session, horizon) not in by_session_horizon
    ]
    if missing:
        raise SplitBuildError("m7_nominal_partition_incomplete")


def build_walk_forward_manifest(
    dataset: ResearchDataset,
    calendar: TradingCalendarArtifact,
    spec: WalkForwardSpec,
    split_version: str,
    *,
    lockbox_start: date | None = None,
) -> SplitManifest:
    """Build deterministic expanding folds with purge and boundary embargoes."""

    validated_dataset = _validate_dataset(dataset)
    validated_calendar = _validate_calendar(calendar)
    validated_spec = _validate_spec(spec)
    _validate_evidence(validated_dataset, validated_calendar, validated_spec)

    samples = validated_dataset.samples
    required_horizons: tuple[Horizon, ...] = tuple(
        sorted({sample.horizon for sample in samples})
    )
    sessions = validated_calendar.sessions
    session_set = set(sessions)
    _validate_samples(validated_dataset, session_set)
    first_anchor_session = samples[0].as_of.date()
    last_anchor_session = samples[-1].as_of.date()
    lockbox_session = (
        _session_on_or_after(sessions, lockbox_start)
        if lockbox_start is not None
        else None
    )
    if lockbox_start is not None and lockbox_session is None:
        raise SplitBuildError("lockbox start is outside the verified calendar")

    validation_half_months = validated_spec.validation_months // 2
    first_calibration_date = _add_months(
        first_anchor_session, validated_spec.train_years * 12
    )
    first_test_start = _session_on_or_after(
        sessions,
        _add_months(first_calibration_date, validated_spec.validation_months),
    )
    if first_test_start is None:
        raise SplitBuildError("history ends before the first outer test")
    fixed_test_following = tuple(
        sample for sample in samples if sample.as_of.date() >= first_test_start
    )
    if not fixed_test_following:
        raise SplitBuildError("first outer test has no samples")
    fixed_selection_embargo = _preceding_sessions(
        sessions,
        first_test_start,
        validated_spec.embargo_sessions,
    )
    folds: list[OuterFold] = []
    fold_index = 0
    while True:
        calibration_boundary_date = first_calibration_date
        calibration_start = _session_on_or_after(
            sessions,
            calibration_boundary_date,
        )
        if calibration_start is None:
            break
        selection_start = _session_on_or_after(
            sessions,
            _add_months(calibration_boundary_date, validation_half_months),
        )
        test_start = _session_on_or_after(
            sessions,
            _add_months(
                calibration_boundary_date,
                validated_spec.validation_months
                + fold_index * validated_spec.step_months,
            ),
        )
        test_end_exclusive = _session_on_or_after(
            sessions,
            _add_months(
                calibration_boundary_date,
                validated_spec.validation_months
                + (fold_index + 1) * validated_spec.test_months,
            ),
        )
        if None in (selection_start, test_start, test_end_exclusive):
            break
        assert selection_start is not None
        assert test_start is not None
        assert test_end_exclusive is not None
        test_end_position = bisect_left(sessions, test_end_exclusive) - 1
        if test_end_position < 0:
            break
        nominal_test_end = sessions[test_end_position]
        if last_anchor_session < nominal_test_end:
            break
        if lockbox_session is not None and (
            test_start >= lockbox_session or nominal_test_end >= lockbox_session
        ):
            break

        base_train_candidates = _samples_between(
            samples, first_anchor_session, calibration_start
        )
        calibration_candidates = _samples_between(
            samples, calibration_start, selection_start
        )
        selection_candidates = _samples_between(
            samples, selection_start, first_test_start
        )
        test_candidates = _samples_between(samples, test_start, test_end_exclusive)
        if not all(
            (
                base_train_candidates,
                calibration_candidates,
                selection_candidates,
                test_candidates,
            )
        ):
            break

        _require_session_coverage(
            base_train_candidates,
            _sessions_between(sessions, first_anchor_session, calibration_start),
            "base training",
            required_horizons,
        )
        _require_session_coverage(
            calibration_candidates,
            _sessions_between(sessions, calibration_start, selection_start),
            "calibration",
            required_horizons,
        )
        _require_session_coverage(
            selection_candidates,
            _sessions_between(sessions, selection_start, first_test_start),
            "selection",
            required_horizons,
        )
        _require_session_coverage(
            test_candidates,
            _sessions_between(sessions, test_start, test_end_exclusive),
            "outer test",
            required_horizons,
        )

        train_embargo = _preceding_sessions(
            sessions, calibration_start, validated_spec.embargo_sessions
        )
        calibration_embargo = _preceding_sessions(
            sessions, selection_start, validated_spec.embargo_sessions
        )
        outer_test_embargo = _preceding_sessions(
            sessions, test_start, validated_spec.embargo_sessions
        )
        base_train, base_train_purged, base_train_embargoed = _purge_and_embargo(
            base_train_candidates,
            calibration_candidates,
            train_embargo,
        )
        train = base_train
        calibration_rows, calibration_purged, calibration_embargoed = (
            _purge_and_embargo(
                calibration_candidates,
                selection_candidates,
                calibration_embargo,
            )
        )
        selection_rows, selection_purged, selection_embargoed = _purge_and_embargo(
            selection_candidates,
            fixed_test_following,
            fixed_selection_embargo,
        )
        test_rows = test_candidates
        test_boundary_purged: tuple[ResearchSample, ...] = ()
        test_boundary_embargoed: tuple[ResearchSample, ...] = ()
        test_boundary_embargo: tuple[date, ...] | None = None
        test_boundary_name: str | None = None
        next_outer_start = _session_on_or_after(
            sessions,
            _add_months(
                first_calibration_date,
                validated_spec.validation_months
                + (fold_index + 1) * validated_spec.step_months,
            ),
        )
        next_outer_end_exclusive = _session_on_or_after(
            sessions,
            _add_months(
                first_calibration_date,
                validated_spec.validation_months
                + (fold_index + 2) * validated_spec.test_months,
            ),
        )
        next_outer_complete = False
        if next_outer_start is not None and next_outer_end_exclusive is not None:
            next_outer_end = _session_before(sessions, next_outer_end_exclusive)
            next_outer_complete = last_anchor_session >= next_outer_end and (
                lockbox_session is None or next_outer_end < lockbox_session
            )
        following_start: date | None = None
        if next_outer_complete:
            following_start = next_outer_start
            test_boundary_name = "test_to_next_outer"
        elif lockbox_session is not None:
            following_start = lockbox_session
            test_boundary_name = "test_to_lockbox"
        if following_start is not None:
            following_samples = tuple(
                sample for sample in samples if sample.as_of.date() >= following_start
            )
            if not following_samples:
                raise SplitBuildError("following evaluation window has no samples")
            test_boundary_embargo = _preceding_sessions(
                sessions,
                following_start,
                validated_spec.embargo_sessions,
            )
            (
                test_rows,
                test_boundary_purged,
                test_boundary_embargoed,
            ) = _purge_and_embargo(
                test_candidates,
                following_samples,
                test_boundary_embargo,
            )
        if not all((train, calibration_rows, selection_rows, test_rows)):
            raise SplitBuildError("purge or embargo emptied a required fold partition")

        fold_id = f"outer-{fold_index + 1:02d}"
        evaluation_window = EvaluationWindow(
            window_id=f"{split_version}.{fold_id}.test",
            role=EvaluationRole.OUTER_TEST,
            scope=validated_spec.evaluation_scope,
            start_session=test_start,
            end_session=nominal_test_end,
            split_version=split_version,
            dataset_input_hash=validated_dataset.input_hash,
            calendar_hash=validated_calendar.input_hash,
            consumed_at=None,
            consumption_reason=None,
        )
        boundary_embargo_sessions = {
            "train_to_calibration": train_embargo,
            "calibration_to_selection": calibration_embargo,
            "selection_to_test": fixed_selection_embargo,
        }
        if fold_index > 0:
            boundary_embargo_sessions["outer_test_embargo"] = outer_test_embargo
        if test_boundary_embargo is not None and test_boundary_name is not None:
            boundary_embargo_sessions[test_boundary_name] = test_boundary_embargo
        folds.append(
            OuterFold(
                fold_id=fold_id,
                fold_index=fold_index,
                split_version=split_version,
                dataset_input_hash=validated_dataset.input_hash,
                calendar_hash=validated_calendar.input_hash,
                train_start_session=first_anchor_session,
                train_end_session=_session_before(
                    sessions,
                    calibration_start,
                ),
                calibration_start_session=calibration_start,
                calibration_end_session=_session_before(sessions, selection_start),
                selection_start_session=selection_start,
                selection_end_session=_session_before(sessions, first_test_start),
                test_start_session=evaluation_window.start_session,
                test_end_session=evaluation_window.end_session,
                train_sample_keys=_keys(train),
                calibration_sample_keys=_keys(calibration_rows),
                selection_sample_keys=_keys(selection_rows),
                test_sample_keys=_keys(test_rows),
                purged_sample_keys=_unique_keys(
                    base_train_purged
                    + calibration_purged
                    + selection_purged
                    + test_boundary_purged
                ),
                embargoed_sample_keys=_unique_keys(
                    base_train_embargoed
                    + calibration_embargoed
                    + selection_embargoed
                    + test_boundary_embargoed
                ),
                train_anchor_sessions=_anchor_sessions(train),
                calibration_anchor_sessions=_anchor_sessions(calibration_rows),
                selection_anchor_sessions=_anchor_sessions(selection_rows),
                test_anchor_sessions=_anchor_sessions(test_rows),
                embargo_sessions=outer_test_embargo,
                boundary_embargo_sessions=boundary_embargo_sessions,
                evaluation_window=evaluation_window,
            )
        )
        fold_index += 1

    if len(folds) < validated_spec.minimum_outer_folds:
        raise SplitBuildError(
            "history cannot form the required minimum number of outer folds"
        )

    lockbox_window = _build_lockbox_window(
        validated_dataset,
        validated_calendar,
        validated_spec,
        split_version,
        lockbox_session,
        last_anchor_session,
        folds,
    )
    lockbox_samples = (
        tuple(
            sample
            for sample in samples
            if lockbox_session is not None and sample.as_of.date() >= lockbox_session
        )
        if lockbox_window is not None
        else ()
    )
    if lockbox_window is not None:
        _require_session_coverage(
            lockbox_samples,
            tuple(
                session
                for session in sessions
                if lockbox_window.start_session <= session <= lockbox_window.end_session
            ),
            "lockbox",
            required_horizons,
        )
    try:
        return SplitManifest(
            split_version=split_version,
            spec=validated_spec,
            dataset_id=validated_dataset.dataset_id,
            dataset_input_hash=validated_dataset.input_hash,
            dataset_revision=validated_dataset.dataset_revision,
            calendar_version=validated_calendar.calendar_version,
            calendar_hash=validated_calendar.input_hash,
            calendar_source=validated_calendar.source,
            calendar_sessions=sessions,
            source_sample_keys=_keys(samples),
            required_horizons=required_horizons,
            outer_folds=tuple(folds),
            lockbox_window=lockbox_window,
            lockbox_sample_keys=_keys(lockbox_samples),
        )
    except ValidationError as error:
        raise SplitBuildError("split manifest contract is invalid") from error


def _validate_dataset(dataset: ResearchDataset) -> ResearchDataset:
    try:
        return ResearchDataset.model_validate(
            dataset.model_dump(mode="python"), strict=True
        )
    except ValidationError as error:
        raise SplitBuildError("research dataset contract is invalid") from error


def _validate_calendar(calendar: TradingCalendarArtifact) -> TradingCalendarArtifact:
    try:
        return TradingCalendarArtifact.model_validate(
            calendar.model_dump(mode="python"), strict=True
        )
    except ValidationError as error:
        raise SplitBuildError("trading calendar contract is invalid") from error


def _validate_spec(spec: WalkForwardSpec) -> WalkForwardSpec:
    try:
        return WalkForwardSpec.model_validate(
            spec.model_dump(mode="python"), strict=True
        )
    except ValidationError as error:
        raise SplitBuildError("walk-forward specification is invalid") from error


def _validate_evidence(
    dataset: ResearchDataset,
    calendar: TradingCalendarArtifact,
    spec: WalkForwardSpec,
) -> None:
    if dataset.calendar_hash != calendar.input_hash:
        raise SplitBuildError("dataset calendar hash does not match calendar evidence")
    if dataset.calendar_version != calendar.calendar_version:
        raise SplitBuildError(
            "dataset calendar version does not match calendar evidence"
        )
    if dataset.calendar_source is not calendar.source:
        raise SplitBuildError(
            "dataset calendar source does not match calendar evidence"
        )
    if dataset.dataset_revision != calendar.dataset_revision:
        raise SplitBuildError("dataset revision does not match calendar evidence")
    if (
        spec.evaluation_scope is EvaluationScope.PRODUCTION
        and calendar.source is not CalendarSource.OPEND_VERIFIED
    ):
        raise SplitBuildError(
            "production evaluation requires a verified OpenD calendar"
        )


def _validate_samples(dataset: ResearchDataset, sessions: set[date]) -> None:
    if dataset.sample_count != len(dataset.samples):
        raise SplitBuildError("dataset sample_count does not match samples")
    if not dataset.samples:
        raise SplitBuildError("research dataset has no samples")
    if dataset.input_hash != compute_research_dataset_input_hash(dataset.samples):
        raise SplitBuildError("research dataset input hash does not match samples")
    if dataset.dataset_id != f"research-{dataset.input_hash[:24]}":
        raise SplitBuildError("research dataset ID does not match input hash")
    keys = _keys(dataset.samples)
    if len(set(keys)) != len(keys):
        raise SplitBuildError("research sample keys must be unique")
    if tuple(sorted(keys, key=lambda item: (item[1], item[0], item[2]))) != keys:
        raise SplitBuildError("research samples must use canonical time order")
    for sample in dataset.samples:
        if (
            sample.as_of.date() not in sessions
            or sample.label_end_at.date() not in sessions
        ):
            raise SplitBuildError(
                "sample time is outside the verified trading calendar"
            )
        if sample.label_end_at != sample.target.label_end_at:
            raise SplitBuildError("sample and target label_end_at differ")
        if sample.horizon != sample.target.horizon:
            raise SplitBuildError("sample and target horizons differ")
        if not sample.as_of <= sample.label_start_at < sample.label_end_at:
            raise SplitBuildError("sample label window is invalid")
        if (
            sample.calendar_version != dataset.calendar_version
            or sample.calendar_hash != dataset.calendar_hash
            or sample.calendar_source is not dataset.calendar_source
            or sample.dataset_revision != dataset.dataset_revision
            or sample.feature_set_version != dataset.feature_set_version
            or sample.label_version != dataset.label_version
            or sample.threshold_version != dataset.threshold_version
            or sample.transformer_version != dataset.transformer_version
            or sample.transformer_input_hash != dataset.transformer_input_hash
            or sample.transformer_manifest_hash != dataset.transformer_manifest_hash
            or sample.universe_version != dataset.universe_version
        ):
            raise SplitBuildError("research sample mixes dataset version evidence")


def _purge_and_embargo(
    previous: Sequence[ResearchSample],
    following: Sequence[ResearchSample],
    embargo_sessions: tuple[date, ...],
) -> tuple[
    tuple[ResearchSample, ...],
    tuple[ResearchSample, ...],
    tuple[ResearchSample, ...],
]:
    next_start_at = min(item.as_of for item in following)
    embargo_set = set(embargo_sessions)
    retained: list[ResearchSample] = []
    purged: list[ResearchSample] = []
    embargoed: list[ResearchSample] = []
    for sample in previous:
        if sample.label_end_at >= next_start_at:
            purged.append(sample)
        elif sample.as_of.date() in embargo_set:
            embargoed.append(sample)
        else:
            retained.append(sample)
    return tuple(retained), tuple(purged), tuple(embargoed)


def _samples_between(
    samples: Sequence[ResearchSample],
    start: date,
    end_exclusive: date,
) -> tuple[ResearchSample, ...]:
    return tuple(
        sample for sample in samples if start <= sample.as_of.date() < end_exclusive
    )


def _keys(samples: Sequence[ResearchSample]) -> tuple[SampleKey, ...]:
    return tuple((item.symbol, item.as_of, item.horizon) for item in samples)


def _unique_keys(samples: Sequence[ResearchSample]) -> tuple[SampleKey, ...]:
    return tuple(dict.fromkeys(_keys(samples)))


def _anchor_sessions(samples: Sequence[ResearchSample]) -> tuple[date, ...]:
    return tuple(sorted({item.as_of.date() for item in samples}))


def _require_session_coverage(
    samples: Sequence[ResearchSample],
    expected_sessions: tuple[date, ...],
    partition_name: str,
    required_horizons: tuple[Horizon, ...],
) -> None:
    for horizon in required_horizons:
        horizon_samples = tuple(
            sample for sample in samples if sample.horizon == horizon
        )
        if _anchor_sessions(horizon_samples) != expected_sessions:
            raise SplitBuildError(
                f"{partition_name} horizon {horizon} does not cover every "
                "nominal calendar session"
            )


def _sessions_between(
    sessions: tuple[date, ...],
    start: date,
    end_exclusive: date,
) -> tuple[date, ...]:
    return sessions[bisect_left(sessions, start) : bisect_left(sessions, end_exclusive)]


def _session_before(sessions: tuple[date, ...], boundary: date) -> date:
    position = bisect_left(sessions, boundary)
    if position == 0:
        raise SplitBuildError("calendar has no session before a fold boundary")
    return sessions[position - 1]


def _preceding_sessions(
    sessions: tuple[date, ...],
    boundary: date,
    count: int,
) -> tuple[date, ...]:
    position = bisect_left(sessions, boundary)
    if position < count:
        raise SplitBuildError("history is too short for the requested embargo")
    return sessions[position - count : position]


def _session_on_or_after(
    sessions: tuple[date, ...],
    target: date | None,
) -> date | None:
    if target is None:
        return None
    position = bisect_left(sessions, target)
    return sessions[position] if position < len(sessions) else None


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def _build_lockbox_window(
    dataset: ResearchDataset,
    calendar: TradingCalendarArtifact,
    spec: WalkForwardSpec,
    split_version: str,
    lockbox_start: date | None,
    last_anchor_session: date,
    folds: Sequence[OuterFold],
) -> EvaluationWindow | None:
    if lockbox_start is None:
        return None
    if lockbox_start > last_anchor_session:
        raise SplitBuildError("lockbox starts after the available sample history")
    if folds and lockbox_start <= folds[-1].test_end_session:
        raise SplitBuildError("lockbox overlaps an outer test window")
    return EvaluationWindow(
        window_id=f"{split_version}.lockbox",
        role=EvaluationRole.LOCKBOX,
        scope=spec.evaluation_scope,
        start_session=lockbox_start,
        end_session=last_anchor_session,
        split_version=split_version,
        dataset_input_hash=dataset.input_hash,
        calendar_hash=calendar.input_hash,
        consumed_at=None,
        consumption_reason=None,
    )
