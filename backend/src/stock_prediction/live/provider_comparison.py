"""Read-only legacy/new-provider overlap diagnostics for migration evidence."""

from __future__ import annotations

from collections.abc import Sequence
import duckdb
import numpy as np
import pandas as pd

from stock_prediction.live.config import LivePaths


def compare_provider_frames(
    opend: pd.DataFrame, candidate: pd.DataFrame
) -> dict[str, object]:
    keys = ["symbol", "session_date"]
    left = opend.copy().sort_values(keys)
    right = candidate.copy().sort_values(keys)
    merged = left.merge(right, on=keys, suffixes=("_opend", "_candidate"))
    denominator = max(len(left), len(right), 1)
    coverage = len(merged) / denominator
    merged["return_opend"] = merged.groupby("symbol")["close_opend"].pct_change()
    merged["return_candidate"] = merged.groupby("symbol")["close_candidate"].pct_change()
    valid_returns = merged[["return_opend", "return_candidate"]].dropna()
    correlation = (
        float(valid_returns.corr().iloc[0, 1])
        if len(valid_returns) >= 2
        else float("nan")
    )
    close_bps = (
        (merged["close_candidate"] / merged["close_opend"] - 1.0).abs() * 10_000
    )
    volume_diff = (
        (merged["volume_candidate"] / merged["volume_opend"] - 1.0)
        .abs()
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    metrics = {
        "coverage_ratio": float(coverage),
        "return_correlation": correlation,
        "close_diff_bps_median": float(close_bps.median()),
        "close_diff_bps_p99": float(close_bps.quantile(0.99)),
        "volume_diff_median": float(volume_diff.median()),
        "volume_diff_p99": float(volume_diff.quantile(0.99)),
    }
    gates = {
        "coverage_ratio": metrics["coverage_ratio"] >= 0.995,
        "return_correlation": metrics["return_correlation"] >= 0.999,
        "close_diff_bps_median": metrics["close_diff_bps_median"] <= 5.0,
        "close_diff_bps_p99": metrics["close_diff_bps_p99"] <= 50.0,
        "volume_diff_median": metrics["volume_diff_median"] <= 0.02,
        "volume_diff_p99": metrics["volume_diff_p99"] <= 0.10,
    }
    failed = [name for name, passed in gates.items() if not passed]
    return {"status": "pass" if not failed else "fail", **metrics, "failed_gates": failed}


def compare_provider_catalogs(
    opend_paths: LivePaths, sip_paths: LivePaths, *, symbols: Sequence[str]
) -> dict[str, object]:
    if not opend_paths.catalog.is_file() or not sip_paths.catalog.is_file():
        return {
            "status": "fail",
            "failed_gates": ["provider_catalog_unavailable"],
        }
    query = """
        SELECT symbol, session_date, close, volume
        FROM canonical_market_bars
        WHERE timeframe='1d' AND symbol IN (
            SELECT * FROM UNNEST(?::VARCHAR[])
        )
        ORDER BY symbol, session_date
    """
    with duckdb.connect(str(opend_paths.catalog), read_only=True) as connection:
        opend = connection.execute(query, [list(symbols)]).fetch_df()
    with duckdb.connect(str(sip_paths.catalog), read_only=True) as connection:
        sip = connection.execute(query, [list(symbols)]).fetch_df()
    return compare_provider_frames(opend, sip)


__all__ = ["compare_provider_catalogs", "compare_provider_frames"]
