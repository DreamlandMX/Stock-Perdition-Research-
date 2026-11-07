"""Allowlisted structured logging that rejects sensitive operational fields."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from io import TextIOBase
import json


class UnsafeLogField(ValueError):
    """A caller attempted to emit a field outside the safe log contract."""


_SAFE_FIELDS = frozenset(
    {
        "component",
        "count",
        "dataset_revision",
        "duration_ms",
        "error_class",
        "horizon",
        "model_version",
        "request_id",
        "snapshot_id",
        "status",
        "symbol",
        "task_id",
        "universe_version",
    }
)
_SENSITIVE_MARKERS = (
    "account",
    "authorization",
    "cookie",
    "credential",
    "key",
    "page_token",
    "password",
    "raw_response",
    "secret",
    "token",
)


def safe_log_payload(
    event: str,
    fields: Mapping[str, str | int | float | bool | None | Enum],
    *,
    timestamp: datetime | None = None,
) -> dict[str, str | int | float | bool | None]:
    if not event or len(event) > 128:
        raise UnsafeLogField("event is invalid")
    output: dict[str, str | int | float | bool | None] = {
        "event": event,
        "timestamp": (timestamp or datetime.now(UTC)).astimezone(UTC).isoformat(),
    }
    for name, value in fields.items():
        normalized = name.casefold()
        if any(marker in normalized for marker in _SENSITIVE_MARKERS):
            raise UnsafeLogField("sensitive log field is forbidden")
        if name not in _SAFE_FIELDS:
            raise UnsafeLogField("log field is not allowlisted")
        if isinstance(value, Enum):
            output[name] = str(value.value)
        elif value is None or isinstance(value, (str, int, float, bool)):
            output[name] = value
        else:
            raise UnsafeLogField("log field value is not scalar")
    return output


class SafeJsonLogger:
    def __init__(self, stream: TextIOBase) -> None:
        self.stream = stream

    def emit(
        self,
        event: str,
        fields: Mapping[str, str | int | float | bool | None | Enum],
        *,
        timestamp: datetime | None = None,
    ) -> None:
        payload = safe_log_payload(event, fields, timestamp=timestamp)
        self.stream.write(
            json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True)
            + "\n"
        )
        self.stream.flush()
