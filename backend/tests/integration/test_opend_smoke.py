"""Opt-in smoke test against the loopback-only moomoo OpenD service."""

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import warnings

import pandas as pd  # type: ignore[import-untyped]
import pytest


RUN_INTEGRATION = os.environ.get("RUN_OPEND_INTEGRATION") == "1"
RUN_HISTORY = RUN_INTEGRATION and os.environ.get("RUN_OPEND_HISTORY_SMOKE") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION,
        reason="set RUN_OPEND_INTEGRATION=1 to use local OpenD",
    ),
]


def _assert_market_rows(frame: object, required_columns: set[str]) -> pd.DataFrame:
    assert isinstance(frame, pd.DataFrame)
    assert not frame.empty
    assert required_columns <= set(frame.columns)
    assert frame[list(required_columns)].notna().all().all()
    return frame


def test_loopback_opend_vertical_slice(tmp_path: Path) -> None:
    """Persist a probe and validate snapshot data; history is separately gated."""

    from futu import AuType, KLType, OpenQuoteContext, RET_OK

    from stock_prediction.capabilities.store import JsonCapabilityStore
    from stock_prediction.cli import run_probe

    run = run_probe(store=JsonCapabilityStore(tmp_path / "capabilities"))
    assert run.output_path.exists()
    assert run.core_ready

    context = None
    try:
        context = OpenQuoteContext(host="127.0.0.1", port=11111)
        ret, snapshot = context.get_market_snapshot(["US.SPY", "US.AAPL"])
        assert ret == RET_OK, "OpenD snapshot request failed"
        snapshot_frame = _assert_market_rows(
            snapshot,
            {
                "code",
                "update_time",
                "open_price",
                "high_price",
                "low_price",
                "last_price",
                "volume",
            },
        )
        assert set(snapshot_frame["code"]) >= {"US.SPY", "US.AAPL"}

        if RUN_HISTORY:
            quota_ret, quota = context.get_history_kl_quota(get_detail=False)
            assert quota_ret == RET_OK, "OpenD history quota read failed"
            assert isinstance(quota, tuple) and len(quota) == 3
            assert quota[1] > 0, "OpenD historical quota is exhausted"
            warnings.warn(
                "Each newly requested historical symbol consumes seven-day quota.",
                stacklevel=1,
            )
            end = datetime.now(UTC).date()
            start = end - timedelta(days=10)
            history_ret, history, _page_key = context.request_history_kline(
                code="US.AAPL",
                start=start.isoformat(),
                end=end.isoformat(),
                ktype=KLType.K_DAY,
                autype=AuType.QFQ,
                max_count=1,
            )
            assert history_ret == RET_OK, "OpenD historical request failed"
            history_frame = _assert_market_rows(
                history,
                {"code", "time_key", "open", "high", "low", "close", "volume"},
            )
            assert set(history_frame["code"]) == {"US.AAPL"}
    finally:
        if context is not None:
            context.close()
