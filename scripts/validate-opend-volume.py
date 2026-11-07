"""Collect resumable full-session volume samples; never alter canonical bars."""

from datetime import UTC, datetime
import json
from pathlib import Path
import time

import duckdb
import pandas as pd

from stock_prediction.ingestion.scheduler import EndpointRateLimiter
from stock_prediction.live.config import LivePaths, OPEND_EXPANDED_DATASET_REVISION
from stock_prediction.live.locking import LiveWriterLease
from stock_prediction.live.universe_scope import resolve_live_universe
from stock_prediction.opend.futu_adapter import FutuQuoteContext


def main() -> None:
    paths = LivePaths.for_dataset(
        Path(__file__).resolve().parents[1], OPEND_EXPANDED_DATASET_REVISION
    )
    symbols = resolve_live_universe(paths).symbols
    with LiveWriterLease(paths, task_id="full-session-volume-sample"):
        with duckdb.connect(str(paths.catalog), read_only=True) as connection:
            days = connection.execute(
                "SELECT DISTINCT session_date FROM canonical_market_bars WHERE timeframe='1d' ORDER BY session_date DESC LIMIT 20"
            ).fetchall()
        start, cutoff = days[-1][0], days[0][0]
        directory = paths.root / "volume-sample" / cutoff.isoformat()
        directory.mkdir(parents=True, exist_ok=True)
        limiter = EndpointRateLimiter(
            {"request_history_kline": (30, 30.0)}, safety_ratio=0.8
        )
        failed = []
        samples = []
        with FutuQuoteContext() as context:
            for index, symbol in enumerate(symbols):
                target = directory / f"{symbol}.parquet"
                if target.exists():
                    frame = pd.read_parquet(target)
                else:
                    for attempt in range(3):
                        while not limiter.allow(
                            "request_history_kline", now=time.monotonic()
                        ):
                            time.sleep(0.25)
                        result = context.request_history_kline_page(
                            code=symbol,
                            timeframe="60m",
                            start=start.isoformat(),
                            end=cutoff.isoformat(),
                            max_count=1000,
                            page_token=None,
                            extended_time=True,
                        )
                        if result.is_ok:
                            break
                        time.sleep(2**attempt)
                    if not result.is_ok or result.value.next_page_token is not None:
                        failed.append(symbol)
                        continue
                    frame = result.value.frame
                    if (
                        not isinstance(frame, pd.DataFrame)
                        or frame.empty
                        or set(frame.code) != {symbol}
                    ):
                        failed.append(symbol)
                        continue
                    frame.to_parquet(target, index=False)
                volumes = frame.groupby(frame.time_key.str.slice(0, 10)).volume.sum()
                for day, volume in volumes.items():
                    if not float(volume).is_integer() or volume < 0:
                        raise ValueError("invalid_native_volume")
                    samples.append(
                        {"symbol": symbol, "session_date": day, "volume": int(volume)}
                    )
                if (index + 1) % 24 == 0:
                    print(f"sampled {index + 1}/{len(symbols)}", flush=True)
        payload = {
            "dataset_revision": paths.dataset_revision,
            "start": start.isoformat(),
            "cutoff": cutoff.isoformat(),
            "observed_at": datetime.now(UTC).isoformat(),
            "session_scope": "regular-plus-pre-post",
            "failed_symbols": failed,
            "rows": samples,
        }
        target = paths.root / "full-session-volume-sample.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(target)
        print(
            json.dumps(
                {
                    "sampled_symbols": len(symbols) - len(failed),
                    "failed_symbols": failed,
                    "sessions": len(samples),
                }
            ),
            flush=True,
        )
        if failed:
            raise RuntimeError("volume_sample_incomplete_rerun_to_resume")


if __name__ == "__main__":
    main()
