"""Versioned market-data contracts shared by ingestion and research layers."""

from datetime import date
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from stock_prediction.contracts.time import UtcDatetime

Identifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
VersionId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
UsSymbol = Annotated[
    str,
    StringConstraints(
        min_length=4,
        max_length=32,
        pattern=r"^US\.[A-Z0-9][A-Z0-9.-]*$",
    ),
]


class Timeframe(StrEnum):
    DAY_1 = "1d"
    MINUTE_60 = "60m"


class MarketSession(StrEnum):
    REGULAR = "regular"
    PRE = "pre"
    POST = "post"


class CorporateActionType(StrEnum):
    SPLIT = "split"
    CASH_DIVIDEND = "cash_dividend"


class AdjustmentKnowledge(StrEnum):
    POINT_IN_TIME = "point_in_time"
    RETROSPECTIVE = "retrospective"


class MarketBar(BaseModel):
    """One normalized but unadjusted market bar from an immutable raw batch."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    timeframe: Timeframe
    session: MarketSession
    session_date: date
    bar_start: UtcDatetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)
    turnover: float | None = Field(default=None, ge=0)
    source_available_at: UtcDatetime
    ingested_at: UtcDatetime
    raw_batch_id: Identifier
    schema_version: VersionId
    dataset_revision: VersionId
    revision_no: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_observation_order(self) -> "MarketBar":
        if self.source_available_at > self.ingested_at:
            raise ValueError("source_available_at must not be after ingested_at")
        return self

    @property
    def natural_key(self) -> tuple[str, str, str, str]:
        return (
            self.symbol,
            self.timeframe.value,
            self.session.value,
            self.bar_start.isoformat(),
        )


class CorporateActionObservation(BaseModel):
    """One source observation used to construct an adjustment revision."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    observation_id: Identifier
    event_id: Identifier
    revision_no: int = Field(ge=1)
    symbol: UsSymbol
    action_type: CorporateActionType
    ex_date: date
    split_ratio: float | None = Field(default=None, gt=0)
    cash_amount: float | None = Field(default=None, gt=0)
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    knowledge: AdjustmentKnowledge
    source_available_at: UtcDatetime
    ingested_at: UtcDatetime
    raw_batch_id: Identifier
    schema_version: VersionId
    dataset_revision: VersionId

    @model_validator(mode="after")
    def validate_action(self) -> "CorporateActionObservation":
        if self.source_available_at > self.ingested_at:
            raise ValueError("source_available_at must not be after ingested_at")
        if self.action_type is CorporateActionType.SPLIT and self.split_ratio is None:
            raise ValueError("split_ratio is required for a split")
        if (
            self.action_type is CorporateActionType.CASH_DIVIDEND
            and self.cash_amount is None
        ):
            raise ValueError("cash_amount is required for a cash dividend")
        return self

    @property
    def point_in_time_eligible(self) -> bool:
        return self.knowledge is AdjustmentKnowledge.POINT_IN_TIME
