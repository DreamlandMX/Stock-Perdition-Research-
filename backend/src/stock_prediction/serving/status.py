"""Dynamic serving state evaluated over immutable forecast bundles."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from pydantic import TypeAdapter

from stock_prediction.contracts.health import (
    Completeness,
    ComponentHealth,
    Connectivity,
    Freshness,
    Readiness,
)
from stock_prediction.contracts.serving import (
    HARD_ABSTAIN_SERVING_STATUSES,
    ForecastBundle,
    ServingOverlay,
    ServingStatus,
    SnapshotServingReference,
)
from stock_prediction.contracts.time import UtcDatetime


class ServingEvaluationTimeError(ValueError):
    """A dynamic overlay was requested before its immutable bundle existed."""


class ServingExclusionReason(StrEnum):
    """Stable dynamic reasons in descending serving-policy priority."""

    UNAVAILABLE = "unavailable"
    OFFLINE = "offline"
    STALE = "stale"


def evaluate_snapshot_status(
    bundle: ForecastBundle,
    *,
    evaluated_at: datetime | str,
    component_states: Sequence[ComponentHealth],
) -> ServingStatus:
    """Evaluate the strongest current status without modifying the bundle."""

    evaluated = _validated_evaluation_time(bundle, evaluated_at)
    status, _ = _evaluate(bundle, evaluated, component_states)
    return status


def overlay_forecast(
    bundle: ForecastBundle,
    *,
    evaluated_at: datetime | str,
    component_states: Sequence[ComponentHealth],
) -> ServingOverlay:
    """Return current serving metadata alongside an immutable forecast record."""

    evaluated = _validated_evaluation_time(bundle, evaluated_at)
    status, reasons = _evaluate(bundle, evaluated, component_states)
    return ServingOverlay(
        serving_status=status,
        effective_abstain=(bundle.abstain or status in HARD_ABSTAIN_SERVING_STATUSES),
        evaluated_at=evaluated,
        exclusion_reason=reasons,
    )


def overlay_snapshot(
    reference: SnapshotServingReference,
    *,
    evaluated_at: datetime | str,
    component_states: Sequence[ComponentHealth],
) -> ServingOverlay:
    """Evaluate snapshot availability and validity without symbol-level state."""

    evaluated = TypeAdapter(UtcDatetime).validate_python(evaluated_at)
    if evaluated < reference.generated_at:
        raise ServingEvaluationTimeError(
            "evaluated_at must not precede snapshot generation"
        )
    unavailable = not reference.has_available_forecasts or _has_unavailable_state(
        component_states
    )
    offline = any(
        state.connectivity is Connectivity.DISCONNECTED for state in component_states
    )
    stale = (
        reference.valid_until is not None and evaluated >= reference.valid_until
    ) or any(state.freshness is Freshness.STALE for state in component_states)
    partial = any(
        state.completeness is Completeness.PARTIAL
        or state.readiness is Readiness.DEGRADED
        for state in component_states
    )
    delayed = any(state.freshness is Freshness.DELAYED for state in component_states)
    if unavailable:
        status = ServingStatus.UNAVAILABLE
    elif offline:
        status = ServingStatus.OFFLINE
    elif stale:
        status = ServingStatus.STALE
    elif partial:
        status = ServingStatus.PARTIAL
    elif delayed:
        status = ServingStatus.DELAYED
    else:
        status = ServingStatus.LIVE
    reasons = tuple(
        reason
        for reason, applies in (
            (ServingExclusionReason.UNAVAILABLE, unavailable),
            (ServingExclusionReason.OFFLINE, offline),
            (ServingExclusionReason.STALE, stale),
        )
        if applies
    )
    return ServingOverlay(
        serving_status=status,
        effective_abstain=status in HARD_ABSTAIN_SERVING_STATUSES,
        evaluated_at=evaluated,
        exclusion_reason=reasons,
    )


def _validated_evaluation_time(
    bundle: ForecastBundle, evaluated_at: datetime | str
) -> datetime:
    evaluated = TypeAdapter(UtcDatetime).validate_python(evaluated_at)
    if evaluated < bundle.generated_at:
        raise ServingEvaluationTimeError(
            "evaluated_at must not precede bundle generated_at"
        )
    return evaluated


def _evaluate(
    bundle: ForecastBundle,
    evaluated_at: datetime,
    component_states: Sequence[ComponentHealth],
) -> tuple[ServingStatus, tuple[ServingExclusionReason, ...]]:
    unavailable = _has_unavailable_state(component_states)
    offline = any(
        state.connectivity is Connectivity.DISCONNECTED for state in component_states
    )
    stale = evaluated_at >= bundle.valid_until or any(
        state.freshness is Freshness.STALE for state in component_states
    )
    partial = bundle.data_quality_status == "partial" or any(
        state.completeness is Completeness.PARTIAL
        or state.readiness is Readiness.DEGRADED
        for state in component_states
    )
    delayed = any(state.freshness is Freshness.DELAYED for state in component_states)

    if unavailable:
        status = ServingStatus.UNAVAILABLE
    elif offline:
        status = ServingStatus.OFFLINE
    elif stale:
        status = ServingStatus.STALE
    elif partial:
        status = ServingStatus.PARTIAL
    elif delayed:
        status = ServingStatus.DELAYED
    else:
        status = ServingStatus.LIVE

    reasons = tuple(
        reason
        for reason, applies in (
            (ServingExclusionReason.UNAVAILABLE, unavailable),
            (ServingExclusionReason.OFFLINE, offline),
            (ServingExclusionReason.STALE, stale),
        )
        if applies
    )
    return status, reasons


def _has_unavailable_state(
    component_states: Sequence[ComponentHealth],
) -> bool:
    if not component_states:
        return True
    return any(
        state.completeness is Completeness.EMPTY
        or state.connectivity is Connectivity.UNKNOWN
        or (
            state.connectivity is not Connectivity.DISCONNECTED
            and (
                state.readiness is Readiness.BLOCKED
                or state.freshness is Freshness.UNKNOWN
                or state.completeness is Completeness.UNKNOWN
            )
        )
        for state in component_states
    )


__all__ = [
    "ServingEvaluationTimeError",
    "ServingExclusionReason",
    "evaluate_snapshot_status",
    "overlay_forecast",
    "overlay_snapshot",
]
