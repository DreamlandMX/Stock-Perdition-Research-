from datetime import UTC

import pytest
from pydantic import ValidationError

from stock_prediction.contracts.health import (
    Completeness,
    ComponentHealth,
    Connectivity,
    Freshness,
    HealthResponse,
    Readiness,
)


def test_health_dimensions_are_independent() -> None:
    component = ComponentHealth(
        component="opend",
        connectivity=Connectivity.DISCONNECTED,
        freshness=Freshness.UNKNOWN,
        completeness=Completeness.UNKNOWN,
        readiness=Readiness.BLOCKED,
    )

    assert component.readiness is Readiness.BLOCKED


def test_health_enum_values_are_stable() -> None:
    assert [value.value for value in Connectivity] == [
        "connected",
        "disconnected",
        "unknown",
        "not_applicable",
    ]
    assert [value.value for value in Freshness] == [
        "current",
        "delayed",
        "stale",
        "unknown",
        "not_applicable",
    ]
    assert [value.value for value in Completeness] == [
        "complete",
        "partial",
        "empty",
        "unknown",
        "not_applicable",
    ]
    assert [value.value for value in Readiness] == ["ready", "degraded", "blocked"]


def test_health_response_normalizes_generated_at_to_utc() -> None:
    response = HealthResponse(
        request_id="request-1",
        generated_at="2026-07-10T18:00:00-04:00",
        service_status=Readiness.READY,
        data_status=Readiness.DEGRADED,
        components=[],
    )

    assert response.generated_at.isoformat() == "2026-07-10T22:00:00+00:00"
    assert response.generated_at.tzinfo is UTC


def test_health_response_rejects_naive_generated_at() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        HealthResponse(
            request_id="request-1",
            generated_at="2026-07-10T22:00:00",
            service_status=Readiness.READY,
            data_status=Readiness.READY,
            components=[],
        )


def test_health_contracts_use_strict_validation() -> None:
    with pytest.raises(ValidationError):
        ComponentHealth(
            component=1,
            connectivity=Connectivity.CONNECTED,
            freshness=Freshness.CURRENT,
            completeness=Completeness.COMPLETE,
            readiness=Readiness.READY,
        )
