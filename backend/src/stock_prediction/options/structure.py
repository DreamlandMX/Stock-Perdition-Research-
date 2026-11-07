"""Metric-specific aggregation and conservative, availability-gated features."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from math import isfinite, fsum
from zoneinfo import ZoneInfo

from stock_prediction.contracts.options import (
    OptionCell,
    OptionChainSnapshot,
    OptionContract,
    OptionCoverage,
    OptionHeatmapReadModel,
    OptionMetric,
    OptionHeatmapSide,
    OptionStructureFeatures,
    OptionStockReturn,
)

UNITS = {
    "oi": "contracts",
    "volume": "contracts_session_to_snapshot",
    "iv": "annualized_decimal",
    "gamma": "USD_per_1pct_move",
    "spread": "USD",
    "premium": "USD",
}


def metric_value(row: OptionContract, metric: OptionMetric) -> float | None:
    if metric == "premium":
        if (
            row.volume is None
            or row.multiplier is None
            or row.bid is None
            or row.ask is None
            or row.bid <= 0
            or row.ask < row.bid
        ):
            return None
        value = row.volume * (row.bid / 2 + row.ask / 2) * row.multiplier
        return value if isfinite(value) else None
    if metric == "oi":
        return row.open_interest
    if metric == "volume":
        return row.volume
    if metric == "iv":
        return row.iv
    if metric == "spread":
        if row.bid is None or row.ask is None or row.bid == 0 or row.ask < row.bid:
            return None
        return row.ask - row.bid
    if (
        row.gamma is None
        or row.open_interest is None
        or row.multiplier is None
        or row.underlying_price is None
        or row.greek_units != "per_usd_v1"
    ):
        return None
    greek_time = row.greeks_at or row.observed_at
    if greek_time is None or row.underlying_at != greek_time:
        return None
    try:
        value = (
            row.gamma
            * row.open_interest
            * row.multiplier
            * row.underlying_price**2
            * 0.01
        )
    except OverflowError:
        return None
    return value if isfinite(value) else None


def _aggregate(values: list[float], additive: bool) -> float | None:
    if not values:
        return None
    try:
        value = (
            fsum(values) if additive else fsum(value / len(values) for value in values)
        )
    except OverflowError:
        return None
    return value if isfinite(value) else None


def build_option_heatmap(
    snapshot: OptionChainSnapshot,
    *,
    metric: OptionMetric,
    side: OptionHeatmapSide,
    dte_min: int = 1,
    dte_max: int = 90,
    strike_min_ratio: float = 0.7,
    strike_max_ratio: float = 1.3,
) -> OptionHeatmapReadModel:
    if metric not in UNITS or side not in ("call", "put", "net"):
        raise ValueError("invalid option metric or side")
    if side == "net":
        call = build_option_heatmap(
            snapshot,
            metric=metric,
            side="call",
            dte_min=dte_min,
            dte_max=dte_max,
            strike_min_ratio=strike_min_ratio,
            strike_max_ratio=strike_max_ratio,
        )
        put = build_option_heatmap(
            snapshot,
            metric=metric,
            side="put",
            dte_min=dte_min,
            dte_max=dte_max,
            strike_min_ratio=strike_min_ratio,
            strike_max_ratio=strike_max_ratio,
        )
        calls = {(c.strike, c.expiration): c for c in call.cells}
        puts = {(c.strike, c.expiration): c for c in put.cells}
        cells = []
        for strike, expiration in sorted(calls.keys() | puts.keys()):
            legs = [mapping.get((strike, expiration)) for mapping in (calls, puts)]
            valid_legs = all(
                c is not None and c.value is not None and c.missing_count == 0
                for c in legs
            )
            value = (
                legs[0].value - legs[1].value if valid_legs and metric != "iv" else None
            )
            cells.append(
                OptionCell(
                    strike=strike,
                    expiration=expiration,
                    value=value,
                    contract_ids=tuple(
                        identity
                        for c in legs
                        if c is not None
                        for identity in c.contract_ids
                    ),
                    valid_count=sum(c.valid_count for c in legs if c is not None)
                    if value is not None
                    else 0,
                    missing_count=sum(c.missing_count for c in legs if c is not None)
                    + sum(c is None for c in legs),
                    excluded_count=sum(c.excluded_count for c in legs if c is not None),
                )
            )
        available = [c.value for c in cells if c.value is not None]

        def summarize(attribute: str) -> dict[str, float | None]:
            keys = {str(getattr(c, attribute)) for c in cells}
            return {
                k: _aggregate(
                    [
                        c.value
                        for c in cells
                        if str(getattr(c, attribute)) == k and c.value is not None
                    ],
                    metric != "spread",
                )
                for k in keys
            }

        return call.model_copy(
            update={
                "side": "net",
                "cells": tuple(cells),
                "strikes": tuple(sorted({c.strike for c in cells})),
                "expirations": tuple(sorted({c.expiration for c in cells})),
                "status": "available"
                if snapshot.complete and available and len(available) == len(cells)
                else "partial"
                if available
                else "unavailable",
                "aggregation": "call_minus_put_sum"
                if metric != "spread"
                else "call_minus_put_arithmetic_mean",
                "total": _aggregate(available, True)
                if metric in ("oi", "volume", "gamma", "premium")
                else None,
                "coverage": OptionCoverage(
                    expected=call.coverage.expected + put.coverage.expected,
                    valid=sum(c.valid_count for c in cells),
                    missing=sum(c.missing_count for c in cells),
                    excluded=sum(c.excluded_count for c in cells),
                    discovery_complete=call.coverage.discovery_complete
                    and put.coverage.discovery_complete,
                ),
                "strike_summary": summarize("strike"),
                "expiry_summary": summarize("expiration"),
                "reasons": tuple(
                    dict.fromkeys(
                        (
                            *call.reasons,
                            *put.reasons,
                            *(() if available else ("metric_unavailable",)),
                        )
                    )
                ),
            }
        )
    today = (
        (snapshot.market_as_of or snapshot.captured_at)
        .astimezone(ZoneInfo("America/New_York"))
        .date()
    )
    grouped: dict[tuple[float, date], list[OptionContract]] = defaultdict(list)
    for row in snapshot.contracts:
        dte = (row.expiration - today).days
        if row.side != side or not dte_min <= dte <= dte_max:
            continue
        if (
            snapshot.underlying_price is not None
            and not strike_min_ratio
            <= row.strike / snapshot.underlying_price
            <= strike_max_ratio
        ):
            continue
        grouped[(row.strike, row.expiration)].append(row)
    cells = []
    additive = metric in ("oi", "volume", "gamma", "premium")
    market_at = snapshot.market_as_of or snapshot.underlying_at
    by_strike: dict[str, list[float]] = defaultdict(list)
    by_expiry: dict[str, list[float]] = defaultdict(list)
    valid = missing = excluded = 0
    overflow = False
    for (strike, expiration), rows in sorted(grouped.items()):
        values = [
            v
            for row in rows
            if row.standard is True
            and (v := metric_value(row, metric)) is not None
            and (
                metric != "premium"
                or (
                    market_at is not None
                    and row.observed_at is not None
                    and row.observed_at <= market_at
                    and (row.quote_at or row.observed_at) <= market_at
                    and row.source_available_at <= snapshot.source_available_at
                    and (
                        snapshot.time_evidence == "historical_verified"
                        or row.ingested_at <= snapshot.source_available_at
                    )
                )
            )
        ]
        excluded_count = sum(row.standard is not True for row in rows)
        missing_count = len(rows) - len(values) - excluded_count
        value = _aggregate(values, additive)
        overflow = overflow or bool(values and value is None)
        cells.append(
            OptionCell(
                strike=strike,
                expiration=expiration,
                value=value,
                contract_ids=tuple(row.contract_id for row in rows),
                valid_count=len(values),
                missing_count=missing_count,
                excluded_count=excluded_count,
            )
        )
        valid += len(values)
        missing += missing_count
        excluded += excluded_count
        if values:
            by_strike[str(strike)].extend(values)
            by_expiry[str(expiration)].extend(values)

    total = (
        _aggregate([c.value for c in cells if c.value is not None], True)
        if additive and not overflow
        else None
    )
    overflow = overflow or bool(additive and valid and total is None)
    reasons = list(snapshot.errors)
    if overflow:
        reasons.append("metric_numeric_overflow")
    if snapshot.underlying_price is None:
        reasons.append("underlying_price_missing_range_unfiltered")
    if not valid:
        reasons.append("metric_unavailable")
    return OptionHeatmapReadModel(
        symbol=snapshot.symbol,
        snapshot=snapshot,
        status="available"
        if snapshot.complete and missing == 0 and valid and not overflow
        else "partial"
        if valid
        else "unavailable",
        metric=metric,
        side=side,
        unit=UNITS[metric],
        aggregation="sum" if additive else "arithmetic_mean_not_sum",
        cells=tuple(cells),
        strikes=tuple(sorted({c.strike for c in cells})),
        expirations=tuple(sorted({c.expiration for c in cells})),
        coverage=OptionCoverage(
            expected=valid + missing + excluded,
            valid=valid,
            missing=missing,
            excluded=excluded,
            discovery_complete=snapshot.discovery_complete
            and len(snapshot.contracts) == len(snapshot.expected_contract_ids),
        ),
        total=total,
        strike_summary={k: _aggregate(v, additive) for k, v in by_strike.items()},
        expiry_summary={k: _aggregate(v, additive) for k, v in by_expiry.items()},
        dte_min=dte_min,
        dte_max=dte_max,
        strike_min_ratio=strike_min_ratio,
        strike_max_ratio=strike_max_ratio,
        reasons=tuple(reasons),
    )


def build_option_structure_features(
    snapshot: OptionChainSnapshot,
    *,
    as_of: datetime,
    history: Sequence[OptionChainSnapshot] = (),
    stock_returns: Sequence[OptionStockReturn] = (),
    feature_version: str = "option-numeric-v1",
) -> OptionStructureFeatures:
    if as_of.tzinfo is None:
        raise ValueError("as_of requires timezone")
    if feature_version == "option-numeric-v2":
        from stock_prediction.options.analytics import build_option_analytics

        analytics = build_option_analytics(
            snapshot, history=history, stock_returns=stock_returns, as_of=as_of
        )
        blockers = list(analytics.reasons)
        if analytics.values["call_oi"] is None or analytics.values["put_oi"] is None:
            blockers.append("oi_field_time_or_coverage_unproven")
        return OptionStructureFeatures(
            symbol=snapshot.symbol,
            as_of=as_of,
            snapshot_id=snapshot.snapshot_id,
            feature_version=feature_version,
            available=not blockers,
            values=analytics.values,
            reasons=tuple(dict.fromkeys(blockers)),
        )
    if feature_version != "option-numeric-v1":
        raise ValueError("unknown option feature version")
    values: dict[str, float | None] = {
        "call_oi": None,
        "put_oi": None,
        "put_call_oi": None,
        "call_volume": None,
        "put_volume": None,
        "put_call_volume": None,
        "call_gamma": None,
        "put_gamma": None,
    }
    reasons = []
    if snapshot.source_available_at > as_of:
        reasons.append("snapshot_not_available")
    if any(
        at is not None and at > as_of
        for at in (snapshot.market_as_of, snapshot.underlying_at)
    ):
        reasons.append("snapshot_future_market_time")
    if not snapshot.complete:
        reasons.append("incomplete_snapshot")
    if snapshot.underlying_price is None:
        reasons.append("underlying_price_unavailable")
    market_day = (
        (snapshot.market_as_of or snapshot.captured_at)
        .astimezone(ZoneInfo("America/New_York"))
        .date()
    )
    for side in ("call", "put"):
        rows = [
            r
            for r in snapshot.contracts
            if r.side == side
            and r.standard is True
            and snapshot.dte_min <= (r.expiration - market_day).days <= snapshot.dte_max
            and snapshot.underlying_price is not None
            and snapshot.strike_min_ratio
            <= r.strike / snapshot.underlying_price
            <= snapshot.strike_max_ratio
        ]
        for metric in ("oi", "volume", "gamma"):
            eligible = [
                r
                for r in rows
                if r.source_available_at <= as_of
                and r.observed_at is not None
                and r.observed_at <= as_of
                and (
                    snapshot.time_evidence == "historical_verified"
                    or r.ingested_at <= as_of
                )
                and (
                    metric not in ("oi", "gamma")
                    or r.oi_date is not None
                    and r.oi_date <= as_of.date()
                )
            ]
            if (
                not reasons
                and rows
                and len(eligible) == len(rows)
                and all(metric_value(r, metric) is not None for r in eligible)
            ):
                values[f"{side}_{metric}"] = _aggregate(
                    [metric_value(r, metric) or 0 for r in eligible], True
                )
    for metric in ("oi", "volume"):
        denominator = values[f"call_{metric}"]
        numerator = values[f"put_{metric}"]
        if denominator is not None and denominator > 0 and numerator is not None:
            values[f"put_call_{metric}"] = numerator / denominator
    if any(v is not None and not isfinite(v) for v in values.values()):
        raise ValueError("nonfinite option features")
    if values["call_oi"] is None or values["put_oi"] is None:
        reasons.append("oi_field_time_or_coverage_unproven")
    return OptionStructureFeatures(
        symbol=snapshot.symbol,
        as_of=as_of,
        snapshot_id=snapshot.snapshot_id,
        available=not reasons,
        values=values,
        reasons=tuple(reasons),
    )
