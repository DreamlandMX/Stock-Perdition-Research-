from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.tests.contracts.test_serving import valid_forecast_bundle_payload
from stock_prediction.contracts.health import (
    Completeness,
    ComponentHealth,
    Connectivity,
    Freshness,
    Readiness,
)
from stock_prediction.contracts.serving import ForecastBundle, ServingStatus
from stock_prediction.modeling.confidence import ConfidenceLevel, RejectionReason
from stock_prediction.serving.status import (
    ServingEvaluationTimeError,
    ServingExclusionReason,
    evaluate_snapshot_status,
    overlay_forecast,
)


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def bundle(*, abstain: bool = False) -> ForecastBundle:
    payload = valid_forecast_bundle_payload()
    if abstain:
        payload.update(
            abstain=True,
            abstain_reasons=(RejectionReason.OOD,),
            confidence_level=ConfidenceLevel.UNAVAILABLE,
            confidence_score=0.0,
        )
    return ForecastBundle.model_validate(payload, strict=True)


def component(
    *,
    name: str = "market_data",
    connectivity: Connectivity = Connectivity.CONNECTED,
    freshness: Freshness = Freshness.CURRENT,
    completeness: Completeness = Completeness.COMPLETE,
    readiness: Readiness = Readiness.READY,
) -> ComponentHealth:
    return ComponentHealth(
        component=name,
        connectivity=connectivity,
        freshness=freshness,
        completeness=completeness,
        readiness=readiness,
    )


def test_valid_until_is_exclusive_and_marks_the_bundle_stale() -> None:
    value = bundle()

    status = evaluate_snapshot_status(
        value,
        evaluated_at=value.valid_until,
        component_states=(component(),),
    )

    assert status is ServingStatus.STALE


def test_status_precedence_is_unavailable_offline_stale_partial_delayed_live() -> None:
    value = bundle()
    states = (
        component(name="unavailable", completeness=Completeness.EMPTY),
        component(
            name="offline",
            connectivity=Connectivity.DISCONNECTED,
            freshness=Freshness.UNKNOWN,
            completeness=Completeness.UNKNOWN,
            readiness=Readiness.BLOCKED,
        ),
        component(name="stale", freshness=Freshness.STALE),
        component(name="partial", completeness=Completeness.PARTIAL),
        component(name="delayed", freshness=Freshness.DELAYED),
    )

    assert evaluate_snapshot_status(
        value,
        evaluated_at=utc("2026-07-12T20:00:00Z"),
        component_states=states,
    ) is ServingStatus.UNAVAILABLE
    assert evaluate_snapshot_status(
        value,
        evaluated_at=utc("2026-07-12T20:00:00Z"),
        component_states=states[1:],
    ) is ServingStatus.OFFLINE
    assert evaluate_snapshot_status(
        value,
        evaluated_at=utc("2026-07-12T20:00:00Z"),
        component_states=states[2:],
    ) is ServingStatus.STALE
    assert evaluate_snapshot_status(
        value,
        evaluated_at=utc("2026-07-12T20:00:00Z"),
        component_states=states[3:],
    ) is ServingStatus.PARTIAL
    assert evaluate_snapshot_status(
        value,
        evaluated_at=utc("2026-07-12T20:00:00Z"),
        component_states=states[4:],
    ) is ServingStatus.DELAYED
    assert evaluate_snapshot_status(
        value,
        evaluated_at=utc("2026-07-12T20:00:00Z"),
        component_states=(component(),),
    ) is ServingStatus.LIVE


def test_stale_bundle_remains_immutable_and_is_effectively_abstained() -> None:
    value = bundle()
    before = value.model_dump(mode="json")
    offline = component(
        connectivity=Connectivity.DISCONNECTED,
        freshness=Freshness.UNKNOWN,
        completeness=Completeness.UNKNOWN,
        readiness=Readiness.BLOCKED,
    )

    overlay = overlay_forecast(
        value,
        evaluated_at=utc("2026-07-13T20:00:00Z"),
        component_states=(offline,),
    )

    assert overlay.serving_status is ServingStatus.OFFLINE
    assert overlay.effective_abstain is True
    assert ServingExclusionReason.OFFLINE in overlay.exclusion_reason
    assert ServingExclusionReason.STALE in overlay.exclusion_reason
    assert value.model_dump(mode="json") == before


def test_exclusion_reasons_are_unique_and_sorted_by_policy_order() -> None:
    value = bundle()
    states = (
        component(name="empty-a", completeness=Completeness.EMPTY),
        component(name="empty-b", completeness=Completeness.EMPTY),
        component(
            name="offline-a",
            connectivity=Connectivity.DISCONNECTED,
            freshness=Freshness.UNKNOWN,
            completeness=Completeness.UNKNOWN,
            readiness=Readiness.BLOCKED,
        ),
        component(
            name="offline-b", connectivity=Connectivity.DISCONNECTED
        ),
    )

    overlay = overlay_forecast(
        value,
        evaluated_at=value.valid_until,
        component_states=states,
    )

    assert overlay.exclusion_reason == (
        ServingExclusionReason.UNAVAILABLE,
        ServingExclusionReason.OFFLINE,
        ServingExclusionReason.STALE,
    )


def test_original_abstention_remains_effective_during_live_service() -> None:
    value = bundle(abstain=True)

    overlay = overlay_forecast(
        value,
        evaluated_at=utc("2026-07-12T20:00:00Z"),
        component_states=(component(),),
    )

    assert overlay.serving_status is ServingStatus.LIVE
    assert overlay.effective_abstain is True
    assert overlay.exclusion_reason == ()


def test_evaluation_before_bundle_generation_is_rejected() -> None:
    value = bundle()

    with pytest.raises(ServingEvaluationTimeError, match="generated_at"):
        overlay_forecast(
            value,
            evaluated_at=utc("2026-07-10T20:01:00Z"),
            component_states=(component(),),
        )
