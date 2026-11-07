"""Explicitly gated, bounded live history adapter pilot."""

from __future__ import annotations

from datetime import date
import math

from stock_prediction.capabilities.store import JsonCapabilityStore
from stock_prediction.contracts.capability import CapabilityStatus
from stock_prediction.contracts.market_data import Timeframe
from stock_prediction.opend.futu_adapter import FutuQuoteContext
from stock_prediction.opend.history import HistoryReadRequest, OpenDHistoryReader
from stock_prediction.universe.seed import SEED_ETFS, SEED_STOCKS


def run_bounded_history_pilot(
    *,
    symbol: str,
    timeframe: Timeframe,
    start: date,
    end: date,
    capability_store: JsonCapabilityStore | None = None,
) -> dict[str, object]:
    if symbol not in {*SEED_ETFS, *SEED_STOCKS}:
        raise ValueError("pilot symbol must belong to the frozen seed universe")
    if start > end or (end - start).days > 31 or (start.year, start.month) != (
        end.year,
        end.month,
    ):
        raise ValueError("pilot range must be at most 31 days inside one month")
    snapshot = (capability_store or JsonCapabilityStore()).load_latest()
    if snapshot is None:
        raise RuntimeError("capability snapshot is required")
    asset_type = "etf" if symbol in SEED_ETFS else "stock"
    history_rows = [
        row
        for row in snapshot.capabilities
        if row.interface == "request_history_kline"
        and row.market == "US"
        and row.asset_type == asset_type
        and row.timeframe == timeframe.value
    ]
    quota_rows = [
        row
        for row in snapshot.capabilities
        if row.interface == "get_history_kl_quota"
    ]
    if (
        len(history_rows) != 1
        or history_rows[0].status is not CapabilityStatus.AVAILABLE
        or len(quota_rows) != 1
        or quota_rows[0].quota_total is None
        or quota_rows[0].quota_remaining is None
    ):
        raise RuntimeError("required history capability is unavailable")
    reserve = math.ceil(quota_rows[0].quota_total * 0.05)
    if quota_rows[0].quota_remaining <= reserve:
        raise RuntimeError("history quota reserve would be violated")
    request = HistoryReadRequest(
        code=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        max_count=1000,
        batch_id=(
            f"pilot-{symbol.replace('.', '-')}-{timeframe.value}-{start.isoformat()}"
        ),
        schema_version="market-bar-v1",
        dataset_revision=f"pilot-{snapshot.snapshot_id}",
    )
    pages = OpenDHistoryReader(
        context_factory=lambda: FutuQuoteContext()
    ).read(request)
    rows = [row for page in pages for row in page.rows]
    min_bar_start = min((row.bar_start for row in rows), default=None)
    max_bar_start = max((row.bar_start for row in rows), default=None)
    return {
        "status": "ok",
        "snapshot_id": snapshot.snapshot_id,
        "symbol": symbol,
        "timeframe": timeframe.value,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "pages": len(pages),
        "rows": len(rows),
        "min_bar_start": min_bar_start.isoformat()
        if min_bar_start is not None
        else None,
        "max_bar_start": max_bar_start.isoformat()
        if max_bar_start is not None
        else None,
    }
