"""Versioned trading-calendar artifacts backed by explicit source evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from stock_prediction.contracts.market_data import Identifier, VersionId
from stock_prediction.contracts.research import CalendarSource, Sha256
from stock_prediction.contracts.time import UtcDatetime


class TradingCalendarArtifact(BaseModel):
    """Immutable ordered US trading dates plus their source and rule identity."""

    model_config = ConfigDict(strict=True, frozen=True)

    market: str = Field(pattern=r"^US$")
    calendar_version: VersionId
    session_rule_version: VersionId
    dataset_revision: VersionId
    source: CalendarSource
    source_reference_ids: tuple[Identifier, ...]
    sessions: tuple[date, ...] = Field(min_length=1)
    input_hash: Sha256
    generated_at: UtcDatetime

    @model_validator(mode="after")
    def validate_evidence(self) -> "TradingCalendarArtifact":
        if tuple(sorted(set(self.sessions))) != self.sessions:
            raise ValueError("calendar sessions must be unique and ordered")
        if not self.source_reference_ids:
            raise ValueError("calendar requires at least one source reference")
        if self.input_hash != _calendar_hash(self.model_dump(mode="json")):
            raise ValueError("calendar input_hash does not match its evidence")
        return self


def build_trading_calendar(
    *,
    sessions: Sequence[date],
    calendar_version: str,
    session_rule_version: str,
    dataset_revision: str,
    source: CalendarSource,
    source_reference_ids: Sequence[str],
    generated_at: UtcDatetime | str,
) -> TradingCalendarArtifact:
    """Build an artifact whose hash changes with any source or session evidence."""

    payload: dict[str, Any] = {
        "market": "US",
        "calendar_version": calendar_version,
        "session_rule_version": session_rule_version,
        "dataset_revision": dataset_revision,
        "source": source,
        "source_reference_ids": tuple(source_reference_ids),
        "sessions": tuple(sessions),
        "generated_at": TypeAdapter(UtcDatetime).validate_python(generated_at),
    }
    return TradingCalendarArtifact(
        **payload,
        input_hash=_calendar_hash(payload),
    )


def _calendar_hash(payload: dict[str, Any]) -> str:
    evidence = {
        name: value
        for name, value in payload.items()
        if name not in {"input_hash", "generated_at"}
    }
    return hashlib.sha256(
        json.dumps(
            evidence,
            default=_json_default,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, CalendarSource):
        return value.value
    raise TypeError(f"unsupported calendar evidence type: {type(value).__name__}")
