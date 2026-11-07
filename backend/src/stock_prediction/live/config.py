"""Frozen paths and versions for the local live-data runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from uuid import uuid4

LIVE_DATASET_REVISION = "opend-live-market-bars-pit-adjusted-v2"
OPEND_EXPANDED_DATASET_REVISION = "opend-expanded-live-market-bars-pit-adjusted-v1"
LIVE_SCHEMA_VERSION = "market-bar-v1"
LIVE_FEATURE_SET_VERSION = "live-daily-features-v2"
LIVE_CALENDAR_VERSION = "opend-us-calendar-v1"
LIVE_SESSION_RULE_VERSION = "us-regular-session-v1"
ALPACA_SIP_DATASET_REVISION = "alpaca-sip-live-market-bars-pit-adjusted-v1"
ALPACA_SIP_FEATURE_SET_VERSION = "live-daily-features-v3"
ALPACA_SIP_CALENDAR_VERSION = "alpaca-us-calendar-v1"
ALPACA_SIP_SESSION_RULE_VERSION = "us-regular-session-0930-v2"
ALPACA_IEX_YAHOO_DATASET_REVISION = (
    "alpaca-iex-yahoo-volume-live-market-bars-pit-adjusted-v1"
)
ALPACA_IEX_YAHOO_FEATURE_SET_VERSION = "live-daily-features-v4"
ALPACA_IEX_YAHOO_CALENDAR_VERSION = "alpaca-us-calendar-v1"
ALPACA_IEX_YAHOO_SESSION_RULE_VERSION = "us-regular-session-0930-v2"
ALPACA_IEX_YAHOO_VOLUME_POLICY_VERSION = (
    "yahoo-daily-scaled-by-iex-intraday-shape-v1"
)


@dataclass(frozen=True, slots=True)
class LivePaths:
    root: Path
    raw: Path
    catalog: Path
    ingestion: Path
    features: Path
    models: Path
    research: Path
    dataset_revision: str = LIVE_DATASET_REVISION

    @property
    def health_mirror(self) -> Path:
        """Atomic, read-only health handoff consumed by the API process."""

        return self.root / "status" / "opend-health.json"

    @property
    def market_data_health_mirror(self) -> Path:
        """Provider-neutral alias; legacy filename remains readable."""

        return self.health_mirror

    @property
    def instrument_metadata(self) -> Path:
        """Forward-only OpenD instrument metadata captured by the writer."""

        return self.root / "status" / "instrument-metadata.json"

    @property
    def writer_lock(self) -> Path:
        """Single cross-process lock for all live OpenD/DuckDB writers."""

        return self.root / "operations" / ".pipeline.lock"

    @property
    def writer_lock_owner(self) -> Path:
        """Human-readable owner and heartbeat for the live writer lease."""

        return self.root / "operations" / "pipeline-owner.json"

    @classmethod
    def from_workspace(cls, workspace: Path) -> "LivePaths":
        resolved = workspace.resolve()
        live = resolved / "var" / "live"
        return cls(
            root=live,
            raw=live / "raw",
            catalog=live / "catalog.duckdb",
            ingestion=live / "ingestion.duckdb",
            features=live / "features",
            models=live / "models",
            research=resolved / "var" / "research",
            dataset_revision=LIVE_DATASET_REVISION,
        )

    @classmethod
    def for_dataset(cls, workspace: Path, dataset_revision: str) -> "LivePaths":
        resolved = workspace.resolve()
        live = resolved / "var" / "live-datasets" / dataset_revision
        return cls(
            root=live,
            raw=live / "raw",
            catalog=live / "catalog.duckdb",
            ingestion=live / "ingestion.duckdb",
            features=live / "features",
            models=live / "models",
            research=resolved / "var" / "research",
            dataset_revision=dataset_revision,
        )

    @classmethod
    def active(cls, workspace: Path) -> "LivePaths":
        revision = read_active_dataset(workspace)
        if revision is None:
            return cls.from_workspace(workspace)
        return cls.for_dataset(workspace, revision)

    def ensure(self) -> None:
        for path in (
            self.root,
            self.root / "status",
            self.root / "operations",
            self.raw,
            self.features,
            self.models,
            self.research,
        ):
                path.mkdir(parents=True, exist_ok=True)


def read_active_dataset(workspace: Path) -> str | None:
    pointer = workspace.resolve() / "var" / "live-active.json"
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        revision = payload["dataset_revision"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return revision if isinstance(revision, str) and revision else None


def activate_live_dataset(
    workspace: Path, *, dataset_revision: str
) -> dict[str, object]:
    staged = LivePaths.for_dataset(workspace, dataset_revision)
    if not staged.root.is_dir():
        raise RuntimeError("dataset_root_unavailable")
    try:
        validation = json.loads(
            (staged.root / "validation.json").read_text(encoding="utf-8")
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("dataset_validation_required") from None
    if (
        not isinstance(validation, dict)
        or validation.get("status") != "pass"
        or validation.get("dataset_revision") != dataset_revision
        or (
            dataset_revision
            in {ALPACA_SIP_DATASET_REVISION, ALPACA_IEX_YAHOO_DATASET_REVISION}
            and validation.get("provider_comparison_status") != "pass"
        )
    ):
        raise RuntimeError("dataset_validation_required")
    activated_at = datetime.now(UTC)
    payload: dict[str, object] = {
        "dataset_revision": dataset_revision,
        "activated_at": activated_at.isoformat(),
    }
    pointer = workspace.resolve() / "var" / "live-active.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    temporary = pointer.with_name(f".{pointer.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, pointer)
    return payload


__all__ = [
    "ALPACA_SIP_CALENDAR_VERSION",
    "ALPACA_SIP_DATASET_REVISION",
    "ALPACA_SIP_FEATURE_SET_VERSION",
    "ALPACA_SIP_SESSION_RULE_VERSION",
    "ALPACA_IEX_YAHOO_CALENDAR_VERSION",
    "ALPACA_IEX_YAHOO_DATASET_REVISION",
    "ALPACA_IEX_YAHOO_FEATURE_SET_VERSION",
    "ALPACA_IEX_YAHOO_SESSION_RULE_VERSION",
    "ALPACA_IEX_YAHOO_VOLUME_POLICY_VERSION",
    "LIVE_CALENDAR_VERSION",
    "LIVE_DATASET_REVISION",
    "OPEND_EXPANDED_DATASET_REVISION",
    "LIVE_FEATURE_SET_VERSION",
    "LIVE_SCHEMA_VERSION",
    "LIVE_SESSION_RULE_VERSION",
    "LivePaths",
    "activate_live_dataset",
    "read_active_dataset",
]
