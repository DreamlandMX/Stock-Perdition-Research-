from datetime import UTC

import pytest
from pydantic import ValidationError

from stock_prediction.contracts.capability import (
    CapabilitySnapshot,
    CapabilityStatus,
    CompatibilityStatus,
    DataCapability,
    PointInTimeStatus,
    TrainingEligibility,
)


def test_capability_key_is_not_global_boolean() -> None:
    capability = DataCapability(
        interface="get_market_snapshot",
        market="US",
        asset_type="etf",
        timeframe=None,
        status=CapabilityStatus.AVAILABLE,
        point_in_time_status=PointInTimeStatus.FORWARD_ONLY,
        probed_at="2026-07-10T22:00:00Z",
    )

    assert capability.interface == "get_market_snapshot"
    assert capability.asset_type == "etf"


def test_capability_enum_values_are_stable() -> None:
    assert [value.value for value in CapabilityStatus] == [
        "available",
        "denied",
        "unavailable",
        "unknown",
    ]
    assert [value.value for value in PointInTimeStatus] == [
        "proven",
        "forward_only",
        "unproven",
        "not_applicable",
    ]


def test_capability_times_are_normalized_to_utc() -> None:
    capability = DataCapability(
        interface="get_market_snapshot",
        market="US",
        asset_type="etf",
        timeframe=None,
        status=CapabilityStatus.AVAILABLE,
        point_in_time_status=PointInTimeStatus.FORWARD_ONLY,
        probed_at="2026-07-10T18:00:00-04:00",
    )
    snapshot = CapabilitySnapshot(
        snapshot_id="snapshot-1",
        probed_at="2026-07-10T18:00:00-04:00",
        opend_version="9.4.5208",
        sdk_version="10.8.6808",
        capabilities=[capability],
    )

    assert capability.probed_at.tzinfo is UTC
    assert snapshot.probed_at.tzinfo is UTC
    assert snapshot.probed_at.isoformat() == "2026-07-10T22:00:00+00:00"


@pytest.mark.parametrize("field", ["capability", "snapshot"])
def test_capability_times_reject_naive_values(field: str) -> None:
    capability_time = (
        "2026-07-10T22:00:00" if field == "capability" else "2026-07-10T22:00:00Z"
    )
    snapshot_time = (
        "2026-07-10T22:00:00" if field == "snapshot" else "2026-07-10T22:00:00Z"
    )

    with pytest.raises(ValidationError, match="timezone-aware"):
        capability = DataCapability(
            interface="get_market_snapshot",
            market="US",
            asset_type="etf",
            timeframe=None,
            status=CapabilityStatus.AVAILABLE,
            point_in_time_status=PointInTimeStatus.FORWARD_ONLY,
            probed_at=capability_time,
        )
        CapabilitySnapshot(
            snapshot_id="snapshot-1",
            probed_at=snapshot_time,
            opend_version="9.4.5208",
            sdk_version="10.8.6808",
            capabilities=[capability],
        )


def test_capability_snapshot_requires_at_least_one_row() -> None:
    with pytest.raises(ValidationError):
        CapabilitySnapshot(
            snapshot_id="snapshot-1",
            probed_at="2026-07-10T22:00:00Z",
            opend_version="9.4.5208",
            sdk_version="10.8.6808",
            capabilities=[],
        )


def test_capability_contracts_use_strict_validation() -> None:
    with pytest.raises(ValidationError):
        DataCapability(
            interface=1,
            market="US",
            asset_type="etf",
            timeframe=None,
            status=CapabilityStatus.AVAILABLE,
            point_in_time_status=PointInTimeStatus.FORWARD_ONLY,
            probed_at="2026-07-10T22:00:00Z",
        )


def test_capability_serializes_explicit_unknown_governance_defaults() -> None:
    capability = DataCapability(
        interface="future_interface",
        market="US",
        asset_type="stock",
        timeframe=None,
        status=CapabilityStatus.UNKNOWN,
        point_in_time_status=PointInTimeStatus.UNPROVEN,
        probed_at="2026-07-11T12:00:00Z",
    )

    payload = capability.model_dump(mode="json")
    assert payload["permission_scope"] == "unknown"
    assert payload["quota_total"] is None
    assert payload["quota_used"] is None
    assert payload["quota_remaining"] is None
    assert payload["quota_reset_at"] is None
    assert payload["rate_limit_calls"] is None
    assert payload["rate_limit_window"] is None
    assert payload["safety_budget"] is None
    assert payload["earliest_available_at"] is None
    assert payload["latest_available_at"] is None
    assert payload["training_eligibility"] == "unknown"
    assert payload["training_eligibility_reason"] == "capability_not_probed"
    assert payload["degradation_policy"] == "unknown"
    assert payload["last_error_class"] is None


def test_quota_and_rate_limit_governance_is_consistent() -> None:
    capability = DataCapability(
        interface="query_subscription",
        market="US",
        asset_type="realtime_quote",
        timeframe=None,
        status=CapabilityStatus.AVAILABLE,
        point_in_time_status=PointInTimeStatus.NOT_APPLICABLE,
        probed_at="2026-07-11T12:00:00Z",
        quota_total=300,
        quota_used=2,
        quota_remaining=298,
        rate_limit_calls=100,
        rate_limit_window="30s",
        safety_budget=80,
    )

    assert capability.quota_total == 300
    assert capability.safety_budget == 80

    with pytest.raises(ValidationError, match="quota_total"):
        capability.model_copy(update={"quota_remaining": 297}).model_validate(
            {**capability.model_dump(), "quota_remaining": 297}
        )
    with pytest.raises(ValidationError, match="80%"):
        DataCapability.model_validate(
            {**capability.model_dump(), "safety_budget": 81}
        )


@pytest.mark.parametrize(
    ("status", "pit", "expected"),
    [
        (CapabilityStatus.AVAILABLE, PointInTimeStatus.PROVEN, TrainingEligibility.ELIGIBLE),
        (CapabilityStatus.AVAILABLE, PointInTimeStatus.FORWARD_ONLY, TrainingEligibility.INELIGIBLE),
        (CapabilityStatus.UNKNOWN, PointInTimeStatus.UNPROVEN, TrainingEligibility.UNKNOWN),
    ],
)
def test_training_eligibility_is_derived_from_status_and_point_in_time(
    status: CapabilityStatus,
    pit: PointInTimeStatus,
    expected: TrainingEligibility,
) -> None:
    capability = DataCapability(
        interface="governed",
        market="US",
        asset_type="stock",
        timeframe=None,
        status=status,
        point_in_time_status=pit,
        probed_at="2026-07-11T12:00:00Z",
    )
    assert capability.training_eligibility is expected


def test_snapshot_defaults_compatibility_to_unknown_for_old_fixtures() -> None:
    capability = DataCapability(
        interface="governed",
        market="US",
        asset_type="stock",
        timeframe=None,
        status=CapabilityStatus.UNKNOWN,
        point_in_time_status=PointInTimeStatus.UNPROVEN,
        probed_at="2026-07-11T12:00:00Z",
    )
    snapshot = CapabilitySnapshot(
        snapshot_id="legacy-fixture",
        probed_at="2026-07-11T12:00:00Z",
        opend_version="1007",
        sdk_version="10.8.6808",
        capabilities=[capability],
    )
    assert snapshot.compatibility_status is CompatibilityStatus.UNKNOWN


def test_capability_is_frozen_so_derived_training_policy_cannot_go_stale() -> None:
    capability = DataCapability(
        interface="governed",
        market="US",
        asset_type="stock",
        timeframe=None,
        status=CapabilityStatus.UNKNOWN,
        point_in_time_status=PointInTimeStatus.UNPROVEN,
        probed_at="2026-07-11T12:00:00Z",
    )

    with pytest.raises(ValidationError, match="frozen"):
        capability.status = CapabilityStatus.AVAILABLE


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("opend_version", "1007\naccount=123"),
        ("sdk_version", "10.8.6808\rcredential=secret"),
        ("opend_version", "x" * 33),
    ],
)
def test_snapshot_rejects_unsafe_version_strings(field: str, value: str) -> None:
    capability = DataCapability(
        interface="governed",
        market="US",
        asset_type="stock",
        timeframe=None,
        status=CapabilityStatus.UNKNOWN,
        point_in_time_status=PointInTimeStatus.UNPROVEN,
        probed_at="2026-07-11T12:00:00Z",
    )
    payload = {
        "snapshot_id": "safe-id",
        "probed_at": "2026-07-11T12:00:00Z",
        "opend_version": "1007",
        "sdk_version": "10.8.6808",
        "capabilities": [capability],
        field: value,
    }

    with pytest.raises(ValidationError):
        CapabilitySnapshot.model_validate(payload)
