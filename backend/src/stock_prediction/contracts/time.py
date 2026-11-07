"""Time contracts shared across the platform."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict


def _normalize_utc(value: object) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("value must be a valid ISO 8601 datetime") from error

    if not isinstance(value, datetime):
        raise ValueError("value must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("value must be timezone-aware")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, BeforeValidator(_normalize_utc)]


class SourceTimestamp(BaseModel):
    """Source availability and local ingestion timestamps for one observation."""

    model_config = ConfigDict(strict=True)

    source_available_at: UtcDatetime
    ingested_at: UtcDatetime
