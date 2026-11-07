"""Dependency interfaces and safe defaults for the read-only API."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
from typing import Protocol

from stock_prediction.contracts.capability import CapabilitySnapshot, CapabilityStatus
from stock_prediction.contracts.health import (
    Completeness,
    ComponentHealth,
    Connectivity,
    Freshness,
    Readiness,
)
from stock_prediction.serving.repository import ResearchReadRepository


class CapabilityStore(Protocol):
    """Read surface required by the API."""

    def load_latest(self) -> CapabilitySnapshot | None: ...


ComponentStateProvider = Callable[[], list[ComponentHealth]]

CURRENT_SNAPSHOT_MAX_AGE = timedelta(hours=24)
DELAYED_SNAPSHOT_MAX_AGE = timedelta(hours=48)


def unavailable_component_states() -> list[ComponentHealth]:
    """Return a side-effect-free state when no external monitor is injected."""

    return [
        ComponentHealth(
            component="opend",
            connectivity=Connectivity.DISCONNECTED,
            freshness=Freshness.UNKNOWN,
            completeness=Completeness.UNKNOWN,
            readiness=Readiness.BLOCKED,
        )
    ]


def persisted_component_state_provider(
    store: CapabilityStore,
    clock: Callable[[], datetime],
) -> ComponentStateProvider:
    """Resolve OpenD health from the latest snapshot without probing or writing."""

    def provide() -> list[ComponentHealth]:
        try:
            snapshot = store.load_latest()
            if snapshot is None:
                return unavailable_component_states()
            return [_component_from_snapshot(snapshot, clock())]
        except Exception:
            return unavailable_component_states()

    return provide


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def live_component_state_provider(
    health_mirror_path: Path,
    clock: Callable[[], datetime],
) -> ComponentStateProvider:
    """Read the scheduler-owned atomic mirror without opening DuckDB or OpenD."""

    resolved = health_mirror_path.resolve()

    def provide() -> list[ComponentHealth]:
        if not resolved.is_file():
            return unavailable_component_states()
        try:
            document = json.loads(resolved.read_text(encoding="utf-8"))
            observed_at = _utc_datetime(document.get("observed_at"))
            payload = document.get("payload")
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return unavailable_component_states()
        if observed_at is None or not isinstance(payload, dict):
            return unavailable_component_states()
        age = clock() - observed_at
        provider_name = (
            payload["provider"] if isinstance(payload.get("provider"), str) else "opend"
        )
        connected = payload.get("connection_status") == "connected" or payload.get(
            "status"
        ) in {"ok", "ready"}
        freshness = (
            Freshness.CURRENT
            if age <= timedelta(minutes=2)
            else Freshness.DELAYED
            if age <= timedelta(hours=24)
            else Freshness.STALE
        )
        blockers = payload.get("blockers")
        degraded = isinstance(blockers, list) and bool(blockers)
        lag_sessions = _non_negative_int(payload.get("lag_sessions"))
        if lag_sessions is not None and lag_sessions > 0:
            freshness = Freshness.DELAYED if lag_sessions == 1 else Freshness.STALE
            degraded = True
        return [
            ComponentHealth(
                component="market_data",
                connectivity=(
                    Connectivity.CONNECTED if connected else Connectivity.DISCONNECTED
                ),
                freshness=freshness,
                completeness=(
                    Completeness.PARTIAL if degraded else Completeness.COMPLETE
                ),
                readiness=(
                    Readiness.READY
                    if connected and not degraded and freshness is Freshness.CURRENT
                    else Readiness.DEGRADED
                ),
                last_success_at=observed_at,
                data_cutoff=(
                    date.fromisoformat(payload["data_cutoff"])
                    if isinstance(payload.get("data_cutoff"), str)
                    else None
                ),
                expected_cutoff=(
                    date.fromisoformat(payload["expected_cutoff"])
                    if isinstance(payload.get("expected_cutoff"), str)
                    else None
                ),
                snapshot_cutoff=(
                    date.fromisoformat(payload["snapshot_cutoff"])
                    if isinstance(payload.get("snapshot_cutoff"), str)
                    else None
                ),
                lag_sessions=lag_sessions,
                last_pipeline_success_at=_utc_datetime(
                    payload.get("last_pipeline_success_at")
                ),
                daily_symbols_complete=_non_negative_int(
                    payload.get("daily_symbols_complete")
                ),
                daily_symbols_total=_non_negative_int(
                    payload.get("daily_symbols_total")
                ),
                provider=provider_name,
                feed=(
                    payload["feed"] if isinstance(payload.get("feed"), str) else None
                ),
                provider_version=(
                    payload["provider_version"]
                    if isinstance(payload.get("provider_version"), str)
                    else None
                ),
                delay_minutes=_non_negative_int(payload.get("delay_minutes")),
                opend_version=(
                    payload["opend_version"]
                    if isinstance(payload.get("opend_version"), str)
                    else None
                ),
                quota_total=_non_negative_int(payload.get("history_quota_total")),
                quota_used=_non_negative_int(payload.get("history_quota_used")),
                quota_remaining=_non_negative_int(
                    payload.get("history_quota_remaining")
                ),
            )
        ]

    return provide


def _component_from_snapshot(
    snapshot: CapabilitySnapshot,
    now: datetime,
) -> ComponentHealth:
    global_statuses = {
        row.status
        for row in snapshot.capabilities
        if row.interface == "get_global_state"
    }
    global_ready = CapabilityStatus.AVAILABLE in global_statuses
    if global_ready or CapabilityStatus.DENIED in global_statuses:
        connectivity = Connectivity.CONNECTED
    elif CapabilityStatus.UNAVAILABLE in global_statuses:
        connectivity = Connectivity.DISCONNECTED
    else:
        connectivity = Connectivity.UNKNOWN

    age = now - snapshot.probed_at
    if age < timedelta(0):
        freshness = Freshness.UNKNOWN
    elif age <= CURRENT_SNAPSHOT_MAX_AGE:
        freshness = Freshness.CURRENT
    elif age <= DELAYED_SNAPSHOT_MAX_AGE:
        freshness = Freshness.DELAYED
    else:
        freshness = Freshness.STALE

    statuses = {row.status for row in snapshot.capabilities}
    if statuses == {CapabilityStatus.AVAILABLE}:
        completeness = Completeness.COMPLETE
    elif CapabilityStatus.AVAILABLE in statuses:
        completeness = Completeness.PARTIAL
    elif statuses <= {
        CapabilityStatus.DENIED,
        CapabilityStatus.UNAVAILABLE,
    }:
        completeness = Completeness.EMPTY
    else:
        completeness = Completeness.UNKNOWN

    if not global_ready:
        readiness = Readiness.BLOCKED
    elif (
        connectivity is Connectivity.CONNECTED
        and freshness is Freshness.CURRENT
        and completeness is Completeness.COMPLETE
    ):
        readiness = Readiness.READY
    else:
        readiness = Readiness.DEGRADED

    return ComponentHealth(
        component="opend",
        connectivity=connectivity,
        freshness=freshness,
        completeness=completeness,
        readiness=readiness,
    )


__all__ = [
    "CapabilityStore",
    "ComponentStateProvider",
    "ResearchReadRepository",
    "persisted_component_state_provider",
    "live_component_state_provider",
    "unavailable_component_states",
]
