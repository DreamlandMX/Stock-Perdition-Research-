from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from stock_prediction.contracts.research import CalendarSource
from stock_prediction.research.calendar import build_trading_calendar


def test_calendar_hash_binds_source_versions_references_and_sessions() -> None:
    artifact = build_trading_calendar(
        sessions=(date(2026, 7, 2), date(2026, 7, 6)),
        calendar_version="us-calendar-v1",
        session_rule_version="us-session-v1",
        dataset_revision="dataset-r1",
        source=CalendarSource.OPEND_VERIFIED,
        source_reference_ids=("raw-opend-trading-days-1",),
        generated_at=datetime(2026, 7, 1, 12, tzinfo=UTC),
    )

    assert len(artifact.input_hash) == 64
    assert artifact.sessions == (date(2026, 7, 2), date(2026, 7, 6))
    with pytest.raises(ValidationError, match="input_hash"):
        artifact.model_validate(
            {
                **artifact.model_dump(),
                "sessions": (date(2026, 7, 2), date(2026, 7, 3)),
            },
            strict=True,
        )


def test_verified_opend_calendar_requires_source_reference() -> None:
    with pytest.raises(ValidationError, match="source reference"):
        build_trading_calendar(
            sessions=(date(2026, 7, 2),),
            calendar_version="us-calendar-v1",
            session_rule_version="us-session-v1",
            dataset_revision="dataset-r1",
            source=CalendarSource.OPEND_VERIFIED,
            source_reference_ids=(),
            generated_at=datetime(2026, 7, 1, 12, tzinfo=UTC),
        )


def test_calendar_sessions_must_be_unique_and_ordered() -> None:
    with pytest.raises(ValidationError, match="ordered"):
        build_trading_calendar(
            sessions=(date(2026, 7, 6), date(2026, 7, 2)),
            calendar_version="offline-calendar-v1",
            session_rule_version="offline-session-v1",
            dataset_revision="dataset-r1",
            source=CalendarSource.SYNTHETIC_OFFLINE,
            source_reference_ids=("synthetic-weekday-generator-v1",),
            generated_at=datetime(2026, 7, 1, 12, tzinfo=UTC),
        )
