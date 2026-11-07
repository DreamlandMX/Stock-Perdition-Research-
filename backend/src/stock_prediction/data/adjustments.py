"""Versioned split/cash-dividend adjustment without point-in-time ambiguity."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from datetime import date, datetime
from enum import StrEnum

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from stock_prediction.contracts.market_data import (
    AdjustmentKnowledge,
    CorporateActionObservation,
    CorporateActionType,
    Identifier,
    MarketBar,
    MarketSession,
    Timeframe,
    UsSymbol,
    VersionId,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.data.catalog import DuckDbCatalog
from stock_prediction.sql_assets import load_sql_asset


class AdjustmentMode(StrEnum):
    POINT_IN_TIME = "point_in_time"
    RESEARCH_RETROSPECTIVE = "research_retrospective"


class AdjustmentError(RuntimeError):
    """Base class for adjustment-layer failures."""


class AdjustmentUnavailableError(AdjustmentError):
    """Required point-in-time adjustment evidence is unavailable."""


class AdjustmentConflictError(AdjustmentError):
    """An adjustment revision already owns different evidence."""


class AdjustedMarketBar(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)

    symbol: UsSymbol
    timeframe: Timeframe
    session: MarketSession
    session_date: date
    bar_start: UtcDatetime
    raw_open: float = Field(gt=0)
    raw_high: float = Field(gt=0)
    raw_low: float = Field(gt=0)
    raw_close: float = Field(gt=0)
    split_factor: float = Field(gt=0)
    adjusted_open: float = Field(gt=0)
    adjusted_high: float = Field(gt=0)
    adjusted_low: float = Field(gt=0)
    adjusted_close: float = Field(gt=0)
    adjusted_volume: float = Field(ge=0)
    capital_gain_return: float | None = None
    cash_distribution: float = Field(ge=0)
    total_return_index: float = Field(gt=0)
    adjustment_revision: VersionId
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class AdjustedSeries(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    adjustment_revision: VersionId
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: AdjustmentMode
    as_of: UtcDatetime
    point_in_time_eligible: bool
    included_action_ids: tuple[Identifier, ...]
    rows: tuple[AdjustedMarketBar, ...]


class AdjustmentEngine:
    """Create capital-gain and total-return series from raw unadjusted bars."""

    def adjust(
        self,
        bars: Sequence[MarketBar],
        actions: Sequence[CorporateActionObservation],
        *,
        mode: AdjustmentMode,
        as_of: UtcDatetime | str,
        adjustment_revision: str,
    ) -> AdjustedSeries:
        if not bars:
            raise AdjustmentUnavailableError("adjustment requires at least one market bar")
        ordered_bars = sorted(bars, key=lambda bar: bar.bar_start)
        partition = {
            (bar.symbol, bar.timeframe, bar.session) for bar in ordered_bars
        }
        if len(partition) != 1 or len({bar.natural_key for bar in ordered_bars}) != len(
            ordered_bars
        ):
            raise AdjustmentUnavailableError(
                "adjustment input must be one unique symbol/timeframe/session partition"
            )
        cutoff = TypeAdapter(UtcDatetime).validate_python(as_of)
        if any(bar.ingested_at > cutoff for bar in ordered_bars):
            raise AdjustmentUnavailableError("market bars were not observable at as_of")
        symbol = ordered_bars[0].symbol
        first_date = ordered_bars[0].session_date
        anchor_date = ordered_bars[-1].session_date
        candidates = sorted(
            [
                item
                for item in actions
                if first_date <= item.ex_date <= anchor_date
            ],
            key=lambda item: (item.ex_date, item.observation_id),
        )
        if any(item.symbol != symbol for item in candidates):
            raise AdjustmentUnavailableError(
                "corporate action symbol does not match market bars"
            )
        if len({item.observation_id for item in candidates}) != len(candidates):
            raise AdjustmentUnavailableError(
                "corporate action observation IDs must be unique"
            )
        relevant = _canonical_actions(candidates, cutoff)
        for item in relevant:
            if (
                mode is AdjustmentMode.POINT_IN_TIME
                and item.knowledge is not AdjustmentKnowledge.POINT_IN_TIME
            ):
                raise AdjustmentUnavailableError(
                    "retrospective rehab is not point-in-time training evidence"
                )

        evidence = {
            "bars": [bar.model_dump(mode="json") for bar in ordered_bars],
            "actions": [item.model_dump(mode="json") for item in relevant],
            "mode": mode.value,
            "as_of": cutoff.isoformat(),
        }
        input_hash = hashlib.sha256(
            json.dumps(
                evidence,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        split_actions = [
            item
            for item in relevant
            if item.action_type is CorporateActionType.SPLIT
        ]
        dividends: dict[date, float] = {}
        for item in relevant:
            if item.action_type is CorporateActionType.CASH_DIVIDEND:
                assert item.cash_amount is not None
                dividends[item.ex_date] = dividends.get(item.ex_date, 0.0) + item.cash_amount

        adjusted_rows: list[AdjustedMarketBar] = []
        previous_adjusted_close: float | None = None
        previous_session_date: date | None = None
        total_return_index = 1.0
        for bar in ordered_bars:
            future_split_ratio = math.prod(
                item.split_ratio
                for item in split_actions
                if item.ex_date > bar.session_date and item.split_ratio is not None
            )
            split_factor = 1.0 / future_split_ratio
            adjusted_open = bar.open * split_factor
            adjusted_high = bar.high * split_factor
            adjusted_low = bar.low * split_factor
            adjusted_close = bar.close * split_factor
            adjusted_volume = bar.volume / split_factor
            cash_distribution = dividends.get(bar.session_date, 0.0) * split_factor
            if previous_adjusted_close is None:
                capital_gain_return = None
            else:
                capital_gain_return = adjusted_close / previous_adjusted_close - 1.0
                distribution_for_period = (
                    cash_distribution
                    if previous_session_date != bar.session_date
                    else 0.0
                )
                total_return_index *= (
                    adjusted_close + distribution_for_period
                ) / previous_adjusted_close
            adjusted_rows.append(
                AdjustedMarketBar(
                    symbol=bar.symbol,
                    timeframe=bar.timeframe,
                    session=bar.session,
                    session_date=bar.session_date,
                    bar_start=bar.bar_start,
                    raw_open=bar.open,
                    raw_high=bar.high,
                    raw_low=bar.low,
                    raw_close=bar.close,
                    split_factor=split_factor,
                    adjusted_open=adjusted_open,
                    adjusted_high=adjusted_high,
                    adjusted_low=adjusted_low,
                    adjusted_close=adjusted_close,
                    adjusted_volume=adjusted_volume,
                    capital_gain_return=capital_gain_return,
                    cash_distribution=cash_distribution,
                    total_return_index=total_return_index,
                    adjustment_revision=adjustment_revision,
                    input_hash=input_hash,
                )
            )
            previous_adjusted_close = adjusted_close
            previous_session_date = bar.session_date

        point_in_time_eligible = all(
            item.knowledge is AdjustmentKnowledge.POINT_IN_TIME for item in relevant
        )
        return AdjustedSeries(
            adjustment_revision=adjustment_revision,
            input_hash=input_hash,
            mode=mode,
            as_of=cutoff,
            point_in_time_eligible=point_in_time_eligible,
            included_action_ids=tuple(item.observation_id for item in relevant),
            rows=tuple(adjusted_rows),
        )


class AdjustedSeriesStore:
    """Register immutable adjustment artifacts in the local DuckDB catalog."""

    def __init__(self, catalog: DuckDbCatalog) -> None:
        self.catalog = catalog
        with self.catalog.connect() as connection:
            connection.execute(load_sql_asset("m2/adjusted_market_bars.sql"))

    def register(self, series: AdjustedSeries) -> bool:
        artifact_json = series.model_dump_json()
        with self.catalog.connect() as connection:
            existing = connection.execute(
                """
                SELECT input_hash, row_count, artifact_json::VARCHAR
                FROM adjustment_artifacts WHERE adjustment_revision = ?
                """,
                [series.adjustment_revision],
            ).fetchone()
            expected = (series.input_hash, len(series.rows), artifact_json)
            if existing is not None:
                if tuple(existing) != expected:
                    raise AdjustmentConflictError(
                        "adjustment revision already owns different evidence"
                    )
                return False
            table = pa.Table.from_pylist(
                [
                    {
                        "adjustment_revision": row.adjustment_revision,
                        "input_hash": row.input_hash,
                        "point_in_time_eligible": series.point_in_time_eligible,
                        "symbol": row.symbol,
                        "timeframe": row.timeframe.value,
                        "session": row.session.value,
                        "session_date": row.session_date,
                        "bar_start": row.bar_start,
                        "raw_open": row.raw_open,
                        "raw_high": row.raw_high,
                        "raw_low": row.raw_low,
                        "raw_close": row.raw_close,
                        "split_factor": row.split_factor,
                        "adjusted_open": row.adjusted_open,
                        "adjusted_high": row.adjusted_high,
                        "adjusted_low": row.adjusted_low,
                        "adjusted_close": row.adjusted_close,
                        "adjusted_volume": row.adjusted_volume,
                        "capital_gain_return": row.capital_gain_return,
                        "cash_distribution": row.cash_distribution,
                        "total_return_index": row.total_return_index,
                    }
                    for row in series.rows
                ]
            )
            connection.execute("BEGIN TRANSACTION")
            connection.register("adjusted_market_bars_input", table)
            try:
                connection.execute(
                    "INSERT INTO adjusted_market_bars SELECT * FROM adjusted_market_bars_input"
                )
                connection.execute(
                    """
                    INSERT INTO adjustment_artifacts
                    VALUES (?, ?, ?, ?, ?, ?, ?::JSON)
                    """,
                    [
                        series.adjustment_revision,
                        series.input_hash,
                        series.mode.value,
                        series.as_of,
                        series.point_in_time_eligible,
                        len(series.rows),
                        artifact_json,
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.unregister("adjusted_market_bars_input")
        return True


def _canonical_actions(
    actions: Sequence[CorporateActionObservation], as_of: datetime
) -> list[CorporateActionObservation]:
    grouped: dict[str, list[CorporateActionObservation]] = {}
    for item in actions:
        grouped.setdefault(item.event_id, []).append(item)
    selected: list[CorporateActionObservation] = []
    for event_id, revisions in grouped.items():
        stable_keys = {
            (item.symbol, item.action_type, item.ex_date, item.currency)
            for item in revisions
        }
        if len(stable_keys) != 1:
            raise AdjustmentUnavailableError(
                f"corporate action event {event_id} changes stable identity"
            )
        revision_numbers = [item.revision_no for item in revisions]
        if len(set(revision_numbers)) != len(revision_numbers):
            raise AdjustmentUnavailableError(
                f"corporate action event {event_id} has ambiguous revisions"
            )
        visible = [
            item
            for item in revisions
            if item.source_available_at <= as_of and item.ingested_at <= as_of
        ]
        if not visible:
            raise AdjustmentUnavailableError(
                "required corporate action was not observable at as_of"
            )
        selected.append(max(visible, key=lambda item: item.revision_no))
    selected.sort(key=lambda item: (item.ex_date, item.event_id))
    return selected
