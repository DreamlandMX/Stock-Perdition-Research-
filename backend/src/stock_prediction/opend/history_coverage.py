"""Explicitly authorized, minimal OpenD history coverage measurement."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from stock_prediction.capabilities.store import JsonCapabilityStore
from stock_prediction.contracts.capability import (
    CapabilitySnapshot,
    CapabilityStatus,
    DataCapability,
    PointInTimeStatus,
)
from stock_prediction.opend.futu_adapter import FutuQuoteContext
from stock_prediction.opend.ports import ReadOnlyQuoteContext, SdkCallResult


class HistoryCoverageError(RuntimeError):
    """Fixed, sanitized coverage failure."""


class SnapshotStore(Protocol):
    def save(self, snapshot: CapabilitySnapshot) -> Path: ...


@dataclass(frozen=True, slots=True)
class HistoryCoverageResult:
    snapshot: CapabilitySnapshot
    output_path: Path
    rows_updated: int
    quota_remaining_before: int
    quota_remaining_after: int

    def safe_summary(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot.snapshot_id,
            "rows_updated": self.rows_updated,
            "quota_remaining_before": self.quota_remaining_before,
            "quota_remaining_after": self.quota_remaining_after,
            "output_path": str(self.output_path),
        }


class HistoryContext(ReadOnlyQuoteContext, Protocol):
    def request_history_kline(
        self, *, code: str, timeframe: str, start: str, end: str, max_count: int
    ) -> SdkCallResult[Any]: ...


class HistoryCoverageProbe:
    def __init__(
        self,
        *,
        context_factory: Callable[..., HistoryContext] = FutuQuoteContext,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        snapshot_id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._context_factory = context_factory
        self._clock = clock
        self._snapshot_id_factory = snapshot_id_factory

    def run(
        self, snapshot: CapabilitySnapshot, store: SnapshotStore
    ) -> HistoryCoverageResult:
        expected_keys = {
            (asset_type, timeframe)
            for asset_type in ("stock", "etf")
            for timeframe in ("1d", "60m")
        }
        history_keys = [
            (row.asset_type, row.timeframe)
            for row in snapshot.capabilities
            if row.interface == "request_history_kline"
        ]
        if len(history_keys) != 4 or set(history_keys) != expected_keys:
            raise HistoryCoverageError("history_coverage_failed")
        quota_rows = [
            row
            for row in snapshot.capabilities
            if row.interface == "get_history_kl_quota"
        ]
        if len(quota_rows) != 1:
            raise HistoryCoverageError("history_coverage_failed")

        measured_at = self._clock().astimezone(UTC)
        coverage_end = measured_at.date().isoformat()
        earliest_start = "1970-01-01"
        latest_start = (measured_at.date() - timedelta(days=30)).isoformat()
        try:
            with self._context_factory(host="127.0.0.1", port=11111) as context:
                before = self._quota(context.get_history_kl_quota(False))
                if before[1] < 2:
                    raise HistoryCoverageError("history_coverage_failed")
                coverage: dict[tuple[str, str], tuple[datetime, datetime]] = {}
                for asset_type, code in (("stock", "US.AAPL"), ("etf", "US.SPY")):
                    for timeframe in ("1d", "60m"):
                        earliest = self._history_time(
                            context.request_history_kline(
                                code=code,
                                timeframe=timeframe,
                                start=earliest_start,
                                end=coverage_end,
                                max_count=1,
                            ),
                            earliest=True,
                            expected_code=code,
                            start=earliest_start,
                            end=coverage_end,
                        )
                        latest = self._history_time(
                            context.request_history_kline(
                                code=code,
                                timeframe=timeframe,
                                start=latest_start,
                                end=coverage_end,
                                max_count=1000,
                            ),
                            earliest=False,
                            expected_code=code,
                            start=latest_start,
                            end=coverage_end,
                        )
                        coverage[(asset_type, timeframe)] = (earliest, latest)
                after = self._quota(context.get_history_kl_quota(False))
        except HistoryCoverageError:
            raise
        except Exception as exception:
            raise HistoryCoverageError("history_coverage_failed") from exception

        updated_rows: list[DataCapability] = []
        rows_updated = 0
        for row in snapshot.capabilities:
            key = (row.asset_type, row.timeframe or "")
            if row.interface == "request_history_kline" and key in coverage:
                earliest, latest = coverage[key]
                updated_rows.append(
                    DataCapability.model_validate(
                        {
                            **row.model_dump(),
                            "status": CapabilityStatus.AVAILABLE,
                            "point_in_time_status": PointInTimeStatus.PROVEN,
                            "probed_at": measured_at,
                            "earliest_available_at": earliest,
                            "latest_available_at": latest,
                            "last_error_class": None,
                        }
                    )
                )
                rows_updated += 1
            elif row.interface == "get_history_kl_quota":
                updated_rows.append(
                    DataCapability.model_validate(
                        {
                            **row.model_dump(),
                            "status": CapabilityStatus.AVAILABLE,
                            "point_in_time_status": PointInTimeStatus.NOT_APPLICABLE,
                            "quota_total": after[0] + after[1],
                            "quota_used": after[0],
                            "quota_remaining": after[1],
                            "probed_at": measured_at,
                            "last_error_class": None,
                        }
                    )
                )
            else:
                updated_rows.append(row)
        if len(coverage) != 4 or rows_updated != 4:
            raise HistoryCoverageError("history_coverage_failed")
        updated = CapabilitySnapshot(
            **{
                **snapshot.model_dump(),
                "snapshot_id": self._snapshot_id_factory(),
                "probed_at": measured_at,
                "capabilities": updated_rows,
            }
        )
        output_path = store.save(updated)
        return HistoryCoverageResult(
            updated, output_path, rows_updated, before[1], after[1]
        )

    @staticmethod
    def _quota(result: SdkCallResult[tuple[int, int, list[object]]]) -> tuple[int, int]:
        if not result.is_ok or result.value is None:
            raise HistoryCoverageError("history_coverage_failed")
        used, remaining, _detail = result.value
        if (
            isinstance(used, bool)
            or not isinstance(used, int)
            or used < 0
            or isinstance(remaining, bool)
            or not isinstance(remaining, int)
            or remaining < 0
            or not isinstance(_detail, list)
        ):
            raise HistoryCoverageError("history_coverage_failed")
        return used, remaining

    @staticmethod
    def _history_time(
        result: SdkCallResult[Any],
        *,
        earliest: bool,
        expected_code: str,
        start: str,
        end: str,
    ) -> datetime:
        if not result.is_ok or not isinstance(result.value, pd.DataFrame):
            raise HistoryCoverageError("history_coverage_failed")
        frame = result.value
        if frame.empty or not {"code", "time_key"} <= set(frame.columns):
            raise HistoryCoverageError("history_coverage_failed")
        codes = frame["code"].tolist()
        if not codes or any(code != expected_code for code in codes):
            raise HistoryCoverageError("history_coverage_failed")
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        market_zone = ZoneInfo("America/New_York")
        raw_values = frame["time_key"].tolist()
        if not raw_values or any(not isinstance(value, str) for value in raw_values):
            raise HistoryCoverageError("history_coverage_failed")
        values: list[datetime] = []
        for value in raw_values:
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                raise HistoryCoverageError("history_coverage_failed") from None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=market_zone)
            market_time = parsed.astimezone(market_zone)
            if not start_date <= market_time.date() <= end_date:
                raise HistoryCoverageError("history_coverage_failed")
            values.append(parsed.astimezone(UTC))
        selected = min(values) if earliest else max(values)
        return selected


def run_and_save_history_coverage() -> dict[str, object]:
    store = JsonCapabilityStore()
    snapshot = store.load_latest()
    if snapshot is None:
        raise HistoryCoverageError("history_coverage_failed")
    return HistoryCoverageProbe().run(snapshot, store).safe_summary()
