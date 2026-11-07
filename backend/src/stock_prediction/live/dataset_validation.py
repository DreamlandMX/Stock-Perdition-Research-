"""Fail-closed staged dataset validation and immutable evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time
import json
import os
from pathlib import Path
from statistics import median
from typing import Any
from uuid import uuid4

import duckdb

from stock_prediction.live.config import LivePaths, OPEND_EXPANDED_DATASET_REVISION
from stock_prediction.live.storage import LiveStorage


class DatasetValidationError(RuntimeError):
    pass


M7_MIN_SYMBOL_COVERAGE = 0.90
M7_MIN_HORIZON_COVERAGE = 0.95


def m7_coverage_failures(
    *,
    symbol_coverage: Mapping[str, float],
    horizon_coverage: Mapping[int, float],
) -> tuple[str, ...]:
    """Return deterministic M7 coverage failure codes without changing scope."""

    failures: list[str] = []
    if any(value < M7_MIN_SYMBOL_COVERAGE for value in symbol_coverage.values()):
        failures.append("symbol_coverage_below_0_90")
    failures.extend(
        f"horizon_{horizon}_coverage_below_0_95"
        for horizon, value in sorted(horizon_coverage.items())
        if value < M7_MIN_HORIZON_COVERAGE
    )
    return tuple(failures)


def validate_live_dataset(
    paths: LivePaths,
    *,
    symbols: Sequence[str],
    expected_cutoff: date,
    provider_comparison: dict[str, object] | None = None,
    required_daily_start: date | None = None,
    required_intraday_start: date | None = None,
) -> dict[str, object]:
    if not paths.catalog.is_file():
        raise DatasetValidationError("dataset_catalog_unavailable")
    expected = tuple(dict.fromkeys(symbols))
    opend_volume: dict[str, object] | None = None
    with duckdb.connect(str(paths.catalog)) as connection:
        coverage = connection.execute(
            """
            SELECT symbol, timeframe, MIN(session_date), MAX(session_date)
            FROM canonical_market_bars
            WHERE symbol IN (SELECT * FROM UNNEST(?::VARCHAR[]))
            GROUP BY symbol, timeframe
            """,
            [list(expected)],
        ).fetchall()
        invalid_row = connection.execute(
            """
                SELECT COUNT(*) FROM canonical_market_bars
                WHERE high < GREATEST(open, close)
                   OR low > LEAST(open, close)
                   OR low <= 0 OR volume < 0
                """
        ).fetchone()
        duplicate_row = connection.execute(
            """
                SELECT COUNT(*) FROM (
                    SELECT symbol, timeframe, session, bar_start, COUNT(*) count
                    FROM canonical_market_bars
                    GROUP BY ALL HAVING count > 1
                )
                """
        ).fetchone()
        volume_rows = connection.execute(
            """
            WITH daily AS (
                SELECT symbol, session_date, MAX(volume) AS daily_volume
                FROM canonical_market_bars
                WHERE timeframe = '1d'
                GROUP BY symbol, session_date
            ), intraday AS (
                SELECT symbol, session_date, SUM(volume) AS intraday_volume
                FROM canonical_market_bars
                WHERE timeframe = '60m'
                GROUP BY symbol, session_date
            )
            SELECT ABS(intraday_volume / NULLIF(daily_volume, 0) - 1.0)
            FROM daily JOIN intraday USING (symbol, session_date)
            WHERE daily_volume > 0
            """
        ).fetchall()
        if paths.dataset_revision == OPEND_EXPANDED_DATASET_REVISION:
            opend_volume = _validate_opend_volume(
                connection, paths, expected, expected_cutoff
            )
        provenance_row: tuple[Any, ...] | None = (0, 0, 0)
        if paths.dataset_revision.startswith("alpaca-iex-yahoo-volume-"):
            provenance_row = connection.execute(
                """
                SELECT COUNT(*) FILTER (
                           WHERE timeframe='1d' AND volume_source='yahoo'
                       ),
                       COUNT(*) FILTER (
                           WHERE timeframe='60m'
                             AND volume_source='yahoo-scaled-by-iex-shape'
                             AND volume_estimated
                       ),
                       COUNT(*) FILTER (
                           WHERE price_source NOT LIKE 'alpaca%'
                              OR volume_source NOT LIKE 'yahoo%'
                       )
                FROM volume_provenance
                """
            ).fetchone()
        invalid_ohlc = int(invalid_row[0]) if invalid_row is not None else 0
        duplicates = int(duplicate_row[0]) if duplicate_row is not None else 0
        yahoo_starts: dict[str, date] = {}
        daily_history_coverage_ratio = 1.0
        if paths.dataset_revision.startswith("alpaca-iex-yahoo-volume-"):
            yahoo_rows = connection.execute(
                """
                SELECT symbol, MIN(session_date)
                FROM yahoo_volume_evidence
                WHERE symbol IN (SELECT * FROM UNNEST(?::VARCHAR[]))
                GROUP BY symbol
                """,
                [list(expected)],
            ).fetchall()
            yahoo_starts = {str(symbol): first for symbol, first in yahoo_rows}
            overlap = connection.execute(
                """
                WITH expected_rows AS (
                    SELECT symbol, session_date
                    FROM yahoo_volume_evidence
                    WHERE symbol IN (SELECT * FROM UNNEST(?::VARCHAR[]))
                      AND session_date >= ? AND session_date <= ?
                ), candidate_rows AS (
                    SELECT symbol, session_date
                    FROM canonical_market_bars WHERE timeframe='1d'
                )
                SELECT COUNT(candidate_rows.symbol), COUNT(expected_rows.symbol)
                FROM expected_rows
                LEFT JOIN candidate_rows USING (symbol, session_date)
                """,
                [list(expected), required_daily_start or date.min, expected_cutoff],
            ).fetchone()
            if overlap is not None and int(overlap[1]) > 0:
                daily_history_coverage_ratio = int(overlap[0]) / int(overlap[1])
    minima = {
        (str(symbol), str(timeframe)): first
        for symbol, timeframe, first, _last in coverage
    }
    maxima = {
        (str(symbol), str(timeframe)): last
        for symbol, timeframe, _first, last in coverage
    }
    daily = sum(
        maxima.get((symbol, "1d"), date.min) >= expected_cutoff for symbol in expected
    )
    intraday = sum(
        maxima.get((symbol, "60m"), date.min) >= expected_cutoff for symbol in expected
    )
    daily_start_complete = (
        sum(
            minima.get((symbol, "1d"), date.max)
            <= (
                max(required_daily_start, yahoo_starts.get(symbol, date.max))
                if paths.dataset_revision.startswith("alpaca-iex-yahoo-volume-")
                else required_daily_start
            )
            for symbol in expected
        )
        if required_daily_start is not None
        else len(expected)
    )
    intraday_start_complete = (
        sum(
            minima.get((symbol, "60m"), date.max) <= required_intraday_start
            for symbol in expected
        )
        if required_intraday_start is not None
        else len(expected)
    )
    volume_differences = sorted(float(row[0]) for row in volume_rows)
    middle = len(volume_differences) // 2
    intraday_daily_volume_median = (
        volume_differences[middle]
        if len(volume_differences) % 2
        else (
            (volume_differences[middle - 1] + volume_differences[middle]) / 2
            if volume_differences
            else float("inf")
        )
    )
    provenance_daily = int(provenance_row[0]) if provenance_row is not None else 0
    provenance_intraday = int(provenance_row[1]) if provenance_row is not None else 0
    provenance_invalid = int(provenance_row[2]) if provenance_row is not None else 0
    failed_gates: list[str] = []
    if daily != len(expected):
        failed_gates.append("daily_current_coverage")
    if intraday != len(expected):
        failed_gates.append("intraday_current_coverage")
    if daily_start_complete != len(expected):
        failed_gates.append("daily_history_start")
    if daily_history_coverage_ratio < 0.995:
        failed_gates.append("daily_history_coverage")
    if intraday_start_complete != len(expected):
        failed_gates.append("intraday_history_start")
    if invalid_ohlc:
        failed_gates.append("ohlc_invariants")
    if duplicates:
        failed_gates.append("duplicate_natural_keys")
    if opend_volume is not None:
        if opend_volume.get("status") != "pass":
            failed_gates.append("opend_same_session_volume")
        if opend_volume.get("incomplete_sessions") != 0:
            failed_gates.append("intraday_bar_completeness")
    elif intraday_daily_volume_median > 0.02:
        failed_gates.append("intraday_daily_volume_consistency")
    if paths.dataset_revision.startswith("alpaca-iex-yahoo-volume-") and (
        provenance_daily == 0 or provenance_intraday == 0 or provenance_invalid
    ):
        failed_gates.append("volume_provenance")
    if provider_comparison is not None and provider_comparison.get("status") != "pass":
        failed_gates.append("provider_comparison")
    if failed_gates:
        _write_evidence(
            paths.root / "validation-failure.json",
            {
                "status": "fail",
                "decision": "abstain",
                "dataset_revision": paths.dataset_revision,
                "validated_at": datetime.now(UTC).isoformat(),
                "failed_gates": failed_gates,
                "symbol_count": len(expected),
                "daily_symbols_complete": daily,
                "intraday_symbols_complete": intraday,
                "daily_start_symbols_complete": daily_start_complete,
                "daily_history_coverage_ratio": daily_history_coverage_ratio,
                "intraday_start_symbols_complete": intraday_start_complete,
                "required_daily_start": (
                    required_daily_start.isoformat() if required_daily_start else None
                ),
                "required_intraday_start": (
                    required_intraday_start.isoformat()
                    if required_intraday_start
                    else None
                ),
                "provider_comparison": provider_comparison,
                "opend_volume_validation": opend_volume,
            },
        )
        raise DatasetValidationError("dataset_coverage_incomplete")
    payload: dict[str, object] = {
        "status": "pass",
        "dataset_revision": paths.dataset_revision,
        "validated_at": datetime.now(UTC).isoformat(),
        "expected_cutoff": expected_cutoff.isoformat(),
        "symbol_count": len(expected),
        "daily_symbols_complete": daily,
        "intraday_symbols_complete": intraday,
        "daily_start_symbols_complete": daily_start_complete,
        "daily_history_coverage_ratio": daily_history_coverage_ratio,
        "intraday_start_symbols_complete": intraday_start_complete,
        "required_daily_start": (
            required_daily_start.isoformat() if required_daily_start else None
        ),
        "required_intraday_start": (
            required_intraday_start.isoformat() if required_intraday_start else None
        ),
        "invalid_ohlc_rows": invalid_ohlc,
        "duplicate_natural_keys": duplicates,
        "intraday_daily_volume_diff_median": intraday_daily_volume_median,
        "intraday_daily_volume_comparable": opend_volume is None,
        "opend_volume_validation": opend_volume,
        "provider_comparison_status": (
            provider_comparison.get("status")
            if provider_comparison is not None
            else "not_run"
        ),
        "provider_comparison": provider_comparison,
        "yahoo_daily_provenance_rows": provenance_daily,
        "yahoo_scaled_intraday_provenance_rows": provenance_intraday,
        "invalid_volume_provenance_rows": provenance_invalid,
    }
    target = paths.root / "validation.json"
    _write_evidence(target, payload)
    return payload


def _validate_opend_volume(
    connection: duckdb.DuckDBPyConnection,
    paths: LivePaths,
    symbols: Sequence[str],
    cutoff: date,
) -> dict[str, object]:
    """Compare like sessions and reject missing regular bars independently."""
    calendar = LiveStorage(paths).trading_days(date(2018, 1, 1), cutoff)
    sessions = connection.execute(
        """
        SELECT d.symbol, d.session_date,
               LIST(CAST(timezone('America/New_York', i.bar_start) AS TIME)
                    ORDER BY i.bar_start) FILTER (WHERE i.bar_start IS NOT NULL)
        FROM canonical_market_bars d
        LEFT JOIN canonical_market_bars i
          ON i.symbol=d.symbol AND i.session_date=d.session_date AND i.timeframe='60m'
        WHERE d.timeframe='1d' AND d.symbol IN (SELECT * FROM UNNEST(?::VARCHAR[]))
          AND d.session_date BETWEEN
              (SELECT MIN(session_date) FROM canonical_market_bars WHERE timeframe='60m') AND ?
        GROUP BY d.symbol, d.session_date
        """,
        [list(symbols), cutoff],
    ).fetchall()
    accepted_gaps = set()
    try:
        gaps = json.loads(
            (paths.root / "native-source-gap-evidence.json").read_text("utf-8")
        )
        if (
            gaps["dataset_revision"] == paths.dataset_revision
            and gaps["provider"] == "opend"
            and gaps["decision"] == "user_accepted_source_gap"
            and {q["timeframe"] for q in gaps["queries"]} == {"60m", "1m"}
            and all(q["sdk_status"] == 0 and q["rows"] == 0 for q in gaps["queries"])
        ):
            accepted_gaps = {
                (gaps["symbol"], date.fromisoformat(day))
                for day in gaps["source_missing_sessions"]
                if gaps["requested_start"] <= day <= gaps["requested_end"]
                and date.fromisoformat(day) < cutoff
            }
    except (OSError, ValueError, TypeError, KeyError):
        pass
    retained_gaps = [
        {"symbol": symbol, "session_date": day.isoformat()}
        for symbol, day, starts in sessions
        if starts is None
        and (symbol, day) in accepted_gaps
        and calendar.get(day) in {"WHOLE", "MORNING"}
    ]
    incomplete = sum(
        calendar.get(day) not in {"WHOLE", "MORNING"}
        or starts
        != [
            time(hour, 30)
            for hour in range(9, 13 if calendar.get(day) == "MORNING" else 16)
        ]
        for symbol, day, starts in sessions
        if not (
            starts is None
            and (symbol, day) in accepted_gaps
            and calendar.get(day) in {"WHOLE", "MORNING"}
        )
    )
    result = {
        "status": "fail",
        "session_scope": "regular-plus-pre-post",
        "regular_sessions_checked": len(sessions),
        "incomplete_sessions": incomplete,
        "retained_source_gaps": retained_gaps,
        "warning": "historical_source_gaps_retained" if retained_gaps else None,
    }
    daily = connection.execute(
        """
        SELECT symbol, session_date, volume FROM canonical_market_bars
        WHERE timeframe='1d' AND symbol IN (SELECT * FROM UNNEST(?::VARCHAR[]))
          AND session_date IN (
              SELECT DISTINCT session_date FROM canonical_market_bars
              WHERE timeframe='1d' AND session_date <= ? ORDER BY session_date DESC LIMIT 20
          )
        """,
        [list(symbols), cutoff],
    ).fetchall()
    expected = {(symbol, day): volume for symbol, day, volume in daily}
    try:
        evidence = json.loads(
            (paths.root / "full-session-volume-sample.json").read_text(encoding="utf-8")
        )
        if (
            evidence["dataset_revision"] != paths.dataset_revision
            or evidence["cutoff"] != cutoff.isoformat()
            or evidence["session_scope"] != "regular-plus-pre-post"
            or evidence["failed_symbols"]
        ):
            raise ValueError
        sample = {}
        for row in evidence["rows"]:
            key = (row["symbol"], date.fromisoformat(row["session_date"]))
            if key in sample or type(row["volume"]) is not int or row["volume"] < 0:
                raise ValueError
            sample[key] = row["volume"]
        if sample.keys() != expected.keys() or {s for s, _d in sample} != set(symbols):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError):
        return {**result, "reason": "volume_sample_missing_invalid_or_stale"}
    differences = {symbol: [] for symbol in symbols}
    for key, volume in expected.items():
        differences[key[0]].append(
            abs(sample[key] / volume - 1) if volume else float(sample[key] != 0)
        )
    medians = {symbol: median(values) for symbol, values in differences.items()}
    maximum = max(medians.values())
    return {
        **result,
        "status": "pass" if maximum <= 0.02 else "fail",
        "sampled_symbols": len(medians),
        "sampled_sessions": len(sample),
        "maximum_symbol_volume_diff_median": maximum,
        "symbol_volume_diff_medians": medians,
    }


def _write_evidence(target: Path, payload: dict[str, object]) -> None:
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, target)


def read_validation(paths: LivePaths) -> dict[str, object] | None:
    try:
        payload = json.loads((paths.root / "validation.json").read_text("utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


__all__ = [
    "DatasetValidationError",
    "M7_MIN_HORIZON_COVERAGE",
    "M7_MIN_SYMBOL_COVERAGE",
    "m7_coverage_failures",
    "read_validation",
    "validate_live_dataset",
]
