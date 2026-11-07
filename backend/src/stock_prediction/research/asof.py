"""Point-in-time selection of versioned research observations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from pydantic import TypeAdapter

from stock_prediction.contracts.research import (
    FundamentalObservation,
    PointInTimeEligibility,
)
from stock_prediction.contracts.time import UtcDatetime


class AsOfSelectionError(ValueError):
    """Observation history is ambiguous and cannot be selected safely."""


class MissingObservationReason(StrEnum):
    """Why an event has no historically usable value at the sample cutoff."""

    NOT_YET_AVAILABLE = "not_yet_available"
    FORWARD_ONLY = "forward_only"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True)
class AsOfObservationSelection:
    """Selected event revision or an explicit point-in-time missing value."""

    event_id: str
    symbol: str
    metric: str
    report_period: date | None
    as_of: datetime
    observation: FundamentalObservation | None
    missing_reason: MissingObservationReason | None
    visible_revision_count: int
    local_ingestion_lag_seconds: float | None

    def __post_init__(self) -> None:
        if (self.observation is None) == (self.missing_reason is None):
            raise ValueError(
                "selection must contain either an observation or a missing reason"
            )


def select_observations_as_of(
    observations: Sequence[FundamentalObservation],
    as_of: UtcDatetime | str,
) -> tuple[AsOfObservationSelection, ...]:
    """Select one source-visible revision per event without using ingestion time.

    `source_available_at` and `published_at` describe what the market could have
    known. `ingested_at` is deliberately retained only as local-lag evidence, so a
    later local collection does not rewrite the historical source visibility rule.
    """

    cutoff = TypeAdapter(UtcDatetime).validate_python(as_of)
    grouped: dict[str, list[FundamentalObservation]] = {}
    observation_ids: set[str] = set()
    for item in observations:
        if item.observation_id in observation_ids:
            raise AsOfSelectionError(
                f"duplicate observation_id {item.observation_id}"
            )
        observation_ids.add(item.observation_id)
        grouped.setdefault(item.event_id, []).append(item)

    selected: list[AsOfObservationSelection] = []
    for event_id, revisions in grouped.items():
        _validate_revision_history(event_id, revisions)
        exemplar = revisions[0]
        visible = [
            item
            for item in revisions
            if item.point_in_time_eligibility is PointInTimeEligibility.ELIGIBLE
            and item.published_at is not None
            and item.published_at <= cutoff
            and item.source_available_at <= cutoff
        ]

        if visible:
            chosen = max(
                visible,
                key=lambda item: (item.source_available_at, item.revision_no),
            )
            selected.append(
                AsOfObservationSelection(
                    event_id=event_id,
                    symbol=chosen.symbol,
                    metric=chosen.metric,
                    report_period=chosen.report_period,
                    as_of=cutoff,
                    observation=chosen,
                    missing_reason=None,
                    visible_revision_count=len(visible),
                    local_ingestion_lag_seconds=(
                        chosen.ingested_at - chosen.source_available_at
                    ).total_seconds(),
                )
            )
            continue

        selected.append(
            AsOfObservationSelection(
                event_id=event_id,
                symbol=exemplar.symbol,
                metric=exemplar.metric,
                report_period=exemplar.report_period,
                as_of=cutoff,
                observation=None,
                missing_reason=_missing_reason(revisions),
                visible_revision_count=0,
                local_ingestion_lag_seconds=None,
            )
        )

    return tuple(
        sorted(
            selected,
            key=lambda item: (
                item.symbol,
                item.metric,
                item.report_period or date.min,
                item.event_id,
            ),
        )
    )


def _validate_revision_history(
    event_id: str, revisions: Sequence[FundamentalObservation]
) -> None:
    stable_identities = {
        (item.symbol, item.metric, item.report_period, item.unit) for item in revisions
    }
    if len(stable_identities) != 1:
        raise AsOfSelectionError(f"event {event_id} changes stable identity")

    revision_numbers = [item.revision_no for item in revisions]
    if len(set(revision_numbers)) != len(revision_numbers):
        raise AsOfSelectionError(f"event {event_id} has duplicate revision numbers")

    source_times = [item.source_available_at for item in revisions]
    if len(set(source_times)) != len(source_times):
        raise AsOfSelectionError(f"event {event_id} has ambiguous source time")
    chronological = sorted(revisions, key=lambda item: item.revision_no)
    if any(
        current.source_available_at <= previous.source_available_at
        for previous, current in zip(
            chronological, chronological[1:], strict=False
        )
    ):
        raise AsOfSelectionError(f"event {event_id} has reversed revision chronology")


def _missing_reason(
    revisions: Sequence[FundamentalObservation],
) -> MissingObservationReason:
    eligibility = {item.point_in_time_eligibility for item in revisions}
    if PointInTimeEligibility.ELIGIBLE in eligibility:
        return MissingObservationReason.NOT_YET_AVAILABLE
    if PointInTimeEligibility.FORWARD_ONLY in eligibility:
        return MissingObservationReason.FORWARD_ONLY
    return MissingObservationReason.INELIGIBLE
