from datetime import UTC

import pytest
from pydantic import ValidationError

from stock_prediction.contracts.time import SourceTimestamp


def test_source_and_ingestion_times_are_distinct() -> None:
    value = SourceTimestamp(
        source_available_at="2026-07-10T20:00:00Z",
        ingested_at="2026-07-10T20:00:02Z",
    )

    assert value.ingested_at > value.source_available_at


def test_source_times_are_normalized_to_utc() -> None:
    value = SourceTimestamp(
        source_available_at="2026-07-10T16:00:00-04:00",
        ingested_at="2026-07-10T16:00:02-04:00",
    )

    assert value.source_available_at.tzinfo is UTC
    assert value.ingested_at.tzinfo is UTC
    assert value.source_available_at.isoformat() == "2026-07-10T20:00:00+00:00"


@pytest.mark.parametrize("field", ["source_available_at", "ingested_at"])
def test_source_times_reject_naive_values(field: str) -> None:
    values = {
        "source_available_at": "2026-07-10T20:00:00Z",
        "ingested_at": "2026-07-10T20:00:02Z",
    }
    values[field] = "2026-07-10T20:00:00"

    with pytest.raises(ValidationError, match="timezone-aware"):
        SourceTimestamp(**values)
