"""Market-bar schema, time, session, coverage, and value quality gates."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence, Set
from datetime import date
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from stock_prediction.contracts.ingestion import (
    DataQualityReport,
    QualityIssue,
    QualitySeverity,
)
from stock_prediction.contracts.market_data import MarketBar, MarketSession, Timeframe
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.data.sessions import SessionSchedule, UsSessionRules

_NEW_YORK = ZoneInfo("America/New_York")


class MarketBarQualityGate:
    """Produce a deterministic batch-level quality decision."""

    def __init__(
        self,
        session_rules: UsSessionRules,
        *,
        rule_version: str = "market-quality-v1",
        unexplained_jump_ratio: float = 0.5,
    ) -> None:
        if not 0 < unexplained_jump_ratio <= 5:
            raise ValueError("unexplained_jump_ratio is outside the supported range")
        self.session_rules = session_rules
        self.rule_version = rule_version
        self.unexplained_jump_ratio = unexplained_jump_ratio

    def evaluate(
        self,
        rows: Sequence[MarketBar],
        *,
        schedules: Mapping[date, SessionSchedule],
        checked_at: UtcDatetime | str,
        batch_id: str | None = None,
        previous_close_by_symbol: Mapping[str, float] | None = None,
        explained_action_dates: Set[date] | None = None,
    ) -> DataQualityReport:
        checked = TypeAdapter(UtcDatetime).validate_python(checked_at)
        resolved_batch_id = batch_id or (rows[0].raw_batch_id if rows else None)
        if resolved_batch_id is None:
            raise ValueError("batch_id is required for an empty response")
        findings: dict[str, tuple[QualitySeverity, str, int]] = {}

        def record(
            code: str,
            severity: QualitySeverity,
            message: str,
            affected_rows: int,
        ) -> None:
            existing = findings.get(code)
            if existing is None:
                findings[code] = (severity, message, affected_rows)
            else:
                findings[code] = (
                    severity,
                    message,
                    existing[2] + affected_rows,
                )

        if not rows:
            record(
                "empty_batch",
                QualitySeverity.ERROR,
                "source response contains no market bars",
                0,
            )
            return self._report(resolved_batch_id, checked, 0, findings)

        wrong_batch = sum(row.raw_batch_id != resolved_batch_id for row in rows)
        if wrong_batch:
            record(
                "mixed_batch_lineage",
                QualitySeverity.ERROR,
                "rows do not share the requested raw batch ID",
                wrong_batch,
            )
        partitions = {
            (row.symbol, row.timeframe, row.schema_version, row.dataset_revision)
            for row in rows
        }
        if len(partitions) != 1:
            record(
                "mixed_batch_partition",
                QualitySeverity.ERROR,
                "rows cross a symbol, timeframe, schema, or dataset partition",
                len(rows),
            )

        natural_keys = [row.natural_key for row in rows]
        duplicates = sum(
            count for count in Counter(natural_keys).values() if count > 1
        )
        if duplicates:
            record(
                "duplicate_natural_key",
                QualitySeverity.ERROR,
                "natural keys are duplicated inside the batch",
                duplicates,
            )
        starts = [row.bar_start for row in rows]
        if starts != sorted(starts):
            record(
                "out_of_order_bar",
                QualitySeverity.ERROR,
                "market bars are not ordered by UTC bar start",
                len(rows),
            )

        missing_turnover = 0
        for row in rows:
            if not (
                row.low <= min(row.open, row.close)
                and row.high >= max(row.open, row.close)
                and row.low <= row.high
            ):
                record(
                    "invalid_ohlc",
                    QualitySeverity.ERROR,
                    "OHLC relations are inconsistent",
                    1,
                )
            if row.bar_start > checked:
                record(
                    "future_bar",
                    QualitySeverity.ERROR,
                    "bar start is later than the quality-check time",
                    1,
                )
            if row.bar_start.astimezone(_NEW_YORK).date() != row.session_date:
                record(
                    "session_date_mismatch",
                    QualitySeverity.ERROR,
                    "UTC bar start maps to a different New York session date",
                    1,
                )
            if row.session_date not in schedules:
                record(
                    "missing_session_schedule",
                    QualitySeverity.ERROR,
                    "bar date has no verified OpenD-backed session schedule",
                    1,
                )
            if row.turnover is None:
                missing_turnover += 1
        if missing_turnover:
            record(
                "missing_turnover",
                QualitySeverity.WARNING,
                "turnover is unavailable for part of the batch",
                missing_turnover,
            )

        timeframe = rows[0].timeframe
        regular_by_date: dict[date, list[MarketBar]] = defaultdict(list)
        for row in rows:
            if row.session is MarketSession.REGULAR:
                regular_by_date[row.session_date].append(row)
        for session_date, schedule in schedules.items():
            actual = regular_by_date.get(session_date, [])
            if timeframe is Timeframe.DAY_1:
                if len(actual) == 0:
                    record(
                        "missing_expected_bar",
                        QualitySeverity.ERROR,
                        "daily session is missing its expected bar",
                        1,
                    )
                elif len(actual) > 1:
                    record(
                        "unexpected_bar",
                        QualitySeverity.ERROR,
                        "daily session contains more than one regular bar",
                        len(actual) - 1,
                    )
                continue
            expected = set(
                self.session_rules.expected_bar_starts(schedule, timeframe)
            )
            actual_starts = {row.bar_start for row in actual}
            missing = expected - actual_starts
            unexpected = actual_starts - expected
            if missing:
                record(
                    "missing_expected_bar",
                    QualitySeverity.ERROR,
                    "regular session is missing exchange-aligned bars",
                    len(missing),
                )
            if unexpected:
                record(
                    "unexpected_bar",
                    QualitySeverity.ERROR,
                    "regular session contains bars outside expected windows",
                    len(unexpected),
                )

        previous_closes = previous_close_by_symbol or {}
        explained_dates = explained_action_dates or set()
        first_by_symbol: dict[str, MarketBar] = {}
        for row in sorted(rows, key=lambda item: item.bar_start):
            first_by_symbol.setdefault(row.symbol, row)
        for symbol, row in first_by_symbol.items():
            previous = previous_closes.get(symbol)
            if (
                previous is not None
                and previous > 0
                and abs(row.open / previous - 1) >= self.unexplained_jump_ratio
                and row.session_date not in explained_dates
            ):
                record(
                    "unexplained_price_jump",
                    QualitySeverity.ERROR,
                    "opening price jump lacks a matching corporate action",
                    1,
                )

        return self._report(resolved_batch_id, checked, len(rows), findings)

    def _report(
        self,
        batch_id: str,
        checked_at: UtcDatetime,
        input_rows: int,
        findings: Mapping[str, tuple[QualitySeverity, str, int]],
    ) -> DataQualityReport:
        issues = [
            QualityIssue(
                code=code,
                severity=severity,
                message=message,
                affected_rows=affected,
            )
            for code, (severity, message, affected) in sorted(findings.items())
        ]
        quarantined = any(
            issue.severity is QualitySeverity.ERROR for issue in issues
        )
        warning_count = sum(
            issue.affected_rows
            for issue in issues
            if issue.severity is QualitySeverity.WARNING
        )
        quality_score = (
            0.0 if quarantined else max(0.5, 1.0 - 0.05 * warning_count)
        )
        return DataQualityReport(
            report_id=f"quality-{batch_id}",
            batch_id=batch_id,
            quality_rule_version=self.rule_version,
            session_rule_version=self.session_rules.version,
            checked_at=checked_at,
            input_rows=input_rows,
            valid_rows=0 if quarantined else input_rows,
            quarantined_rows=input_rows if quarantined else 0,
            quality_score=quality_score,
            quarantined=quarantined,
            issues=issues,
        )
