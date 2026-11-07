"""Research sensitivities and vendor-reported turnover from one frozen batch."""

from collections import defaultdict
from datetime import datetime, time
from math import exp, fsum, isfinite, log, pi, sqrt
from typing import Any, Literal
from zoneinfo import ZoneInfo

from stock_prediction.contracts.options import (
    OptionChainSnapshot,
    OptionContract,
    OptionCoverage,
    OptionMapCell,
    OptionMapGroup,
    OptionMapRanking,
    OptionMapSideValues,
    OptionMapWindow,
    OptionStructureMapReadModel,
)

NEW_YORK = ZoneInfo("America/New_York")


def enrich_turnover(
    snapshot: OptionChainSnapshot, raw: dict[str, Any]
) -> OptionChainSnapshot:
    quotes = {}
    for name, batch in raw.items():
        if name.startswith("quotes-") and isinstance(batch, list):
            for quote in batch:
                if isinstance(quote, dict) and isinstance(quote.get("code"), str):
                    if quote["code"] in quotes:
                        raise ValueError("duplicate raw option quote")
                    quotes[quote["code"]] = quote
    rows = []
    for row in snapshot.contracts:
        quote = quotes.get(row.contract_id, {})
        fields = {}
        for field, source in (
            ("turnover", "turnover"),
            ("session_low", "low_price"),
            ("session_high", "high_price"),
        ):
            value = quote.get(source)
            if (
                getattr(row, field) is None
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and isfinite(value)
                and value >= 0
            ):
                fields[field] = value
        rows.append(OptionContract.model_validate(row.model_dump() | fields))
    return snapshot.model_copy(update={"contracts": tuple(rows)})


def _sum(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    try:
        value = fsum(present)
        return value if isfinite(value) else None
    except OverflowError:
        return None


def _side(rows: list[OptionMapSideValues]) -> OptionMapSideValues:
    metrics = {
        key: _sum([getattr(r, key) for r in rows]) for key in ("gex", "vex", "turnover")
    }
    reasons = tuple(dict.fromkeys(reason for r in rows for reason in r.reasons))
    if any(
        metrics[key] is None and any(getattr(r, key) is not None for r in rows)
        for key in metrics
    ):
        reasons += ("metric_numeric_overflow",)
    return OptionMapSideValues(
        gex=metrics["gex"],
        vex=metrics["vex"],
        turnover=metrics["turnover"],
        **{
            key: sum(getattr(r, key) for r in rows)
            for key in (
                "contracts",
                "exposure_valid",
                "turnover_valid",
                "exposure_excluded",
                "turnover_excluded",
            )
        },
        reasons=reasons,
    )


def _group(cells: list[OptionMapCell]) -> OptionMapGroup:
    return OptionMapGroup(
        call=_side([c.call for c in cells]), put=_side([c.put for c in cells])
    )


def _coverage(group: OptionMapGroup, metric: str, complete: bool) -> OptionCoverage:
    rows = (group.call, group.put)
    expected = sum(r.contracts for r in rows)
    valid = sum(getattr(r, f"{metric}_valid") for r in rows)
    excluded = sum(getattr(r, f"{metric}_excluded") for r in rows)
    return OptionCoverage(
        expected=expected,
        valid=valid,
        excluded=excluded,
        missing=expected - valid - excluded,
        discovery_complete=complete,
    )


def _values(
    row: OptionContract, snapshot: OptionChainSnapshot, at: datetime
) -> OptionMapSideValues:
    reasons = []
    if row.standard is not True:
        return OptionMapSideValues(
            contracts=1,
            exposure_excluded=1,
            turnover_excluded=1,
            reasons=("nonstandard_or_unknown_delivery",),
        )
    same_session = (
        row.observed_at is not None
        and row.observed_at <= at
        and row.observed_at.astimezone(NEW_YORK).date()
        == at.astimezone(NEW_YORK).date()
    )
    timely = row.source_available_at <= snapshot.source_available_at and (
        snapshot.time_evidence == "historical_verified"
        or row.ingested_at <= snapshot.source_available_at
    )
    if not same_session or not timely:
        return OptionMapSideValues(
            contracts=1,
            exposure_excluded=1,
            turnover_excluded=1,
            reasons=("stale_or_unproven_session",),
        )
    turnover = row.turnover
    turnover_excluded = 0
    if turnover is not None and row.volume is not None:
        if row.volume == 0 and turnover > 0:
            turnover_excluded = 1
        elif row.volume > 0 and row.multiplier is not None:
            average = turnover / row.volume / row.multiplier
            if (
                row.session_high is not None
                and row.session_high > 0
                and average > row.session_high * 1.05
            ) or (
                row.session_low is not None
                and row.session_low > 0
                and average < row.session_low * 0.95
            ):
                turnover_excluded = 1
    if turnover_excluded:
        reasons.append("turnover_session_range_conflict")
        turnover = None
    if turnover is None and not turnover_excluded:
        reasons.append("turnover_unavailable")
    gex = vex = None
    spot = snapshot.underlying_price
    end = datetime.combine(row.expiration, time(16), NEW_YORK)
    t = (end.timestamp() - at.timestamp()) / (365 * 86400)
    if (
        row.iv is not None
        and row.open_interest is not None
        and row.multiplier is not None
        and spot is not None
        and t > 0
    ):
        try:
            sigma = row.iv
            # shortcut: r=q=0 European sensitivities; upgrade when dated rates/dividends and an American model are approved.
            root_t = sqrt(t)
            d1 = (log(spot) - log(row.strike) + sigma * sigma * t / 2) / (
                sigma * root_t
            )
            d2 = d1 - sigma * root_t
            density = exp(-d1 * d1 / 2) / sqrt(2 * pi)
            gamma = density / (spot * sigma * root_t)
            vanna = -density * d2 / sigma
            sign = 1 if row.side == "call" else -1
            weight = sign * row.open_interest * row.multiplier
            gex = gamma * weight * spot**2 * 0.01
            vex = vanna * weight * spot * 0.01
            if not isfinite(gex) or not isfinite(vex):
                gex = vex = None
                reasons.append("metric_numeric_overflow")
        except (OverflowError, ZeroDivisionError, ValueError):
            reasons.append("metric_numeric_overflow")
            gex = vex = None
    else:
        reasons.append("model_inputs_unavailable")
    if row.oi_date is None:
        reasons.append("oi_date_unproven")
    elif row.oi_date > at.astimezone(NEW_YORK).date():
        gex = vex = None
        reasons.append("future_oi_date")
    return OptionMapSideValues(
        gex=gex,
        vex=vex,
        turnover=turnover,
        contracts=1,
        exposure_valid=int(gex is not None),
        turnover_valid=int(turnover is not None),
        turnover_excluded=turnover_excluded,
        reasons=tuple(reasons),
    )


def build_option_structure_map(
    snapshot: OptionChainSnapshot,
    *,
    dte_min: int,
    dte_max: int,
    strike_min_ratio: float,
    strike_max_ratio: float,
) -> OptionStructureMapReadModel:
    if (
        not 1 <= dte_min <= dte_max <= 90
        or not 0 < strike_min_ratio <= strike_max_ratio
    ):
        raise ValueError("invalid option map window")
    at = snapshot.market_as_of or snapshot.underlying_at
    empty = OptionCoverage(
        expected=0, valid=0, missing=0, excluded=0, discovery_complete=False
    )
    if at is None or snapshot.underlying_price is None:
        return OptionStructureMapReadModel(
            symbol=snapshot.symbol,
            snapshot=snapshot,
            exposure_coverage=empty,
            turnover_coverage=empty,
            reasons=("model_anchor_unavailable",),
        )
    if at > snapshot.source_available_at:
        raise ValueError("model anchor follows snapshot availability")
    if snapshot.market_as_of is None:
        # A collection fetches spot before its option batches; use the latest known same-session input.
        at = max(
            [
                at,
                *(
                    r.observed_at
                    for r in snapshot.contracts
                    if r.observed_at is not None
                    and r.observed_at <= snapshot.source_available_at
                    and r.observed_at.astimezone(NEW_YORK).date()
                    == at.astimezone(NEW_YORK).date()
                ),
            ]
        )
    day = at.astimezone(NEW_YORK).date()
    grouped: dict[tuple[float, Any], list[OptionContract]] = defaultdict(list)
    for row in snapshot.contracts:
        dte = (row.expiration - day).days
        if (
            1 <= dte <= 90
            and strike_min_ratio
            <= row.strike / snapshot.underlying_price
            <= strike_max_ratio
        ):
            grouped[(row.strike, row.expiration)].append(row)
    all_cells = []
    for (strike, expiration), rows in sorted(grouped.items()):
        values = {
            side: _side(
                [_values(row, snapshot, at) for row in rows if row.side == side]
            )
            for side in ("call", "put")
        }
        all_cells.append(
            OptionMapCell(
                strike=strike,
                expiration=expiration,
                **values,
                contract_ids=tuple(r.contract_id for r in rows),
            )
        )
    cells = [c for c in all_cells if dte_min <= (c.expiration - day).days <= dte_max]
    total = _group(cells)
    by_strike = {
        str(k): _group([c for c in cells if c.strike == k])
        for k in sorted({c.strike for c in cells})
    }
    by_expiry = {
        str(k): _group([c for c in cells if c.expiration == k])
        for k in sorted({c.expiration for c in cells})
    }
    windows = {}
    for maximum in (5, 14, 30, 90):
        subset = [c for c in all_cells if (c.expiration - day).days <= maximum]
        group = _group(subset)
        levels = {
            k: _group([c for c in subset if c.strike == k])
            for k in {c.strike for c in subset}
        }

        def rankings(side: str) -> tuple[OptionMapRanking, ...]:
            amounts = {
                k: _sum([getattr(g, s).turnover for s in ("call", "put")])
                if side == "all"
                else getattr(g, side).turnover
                for k, g in levels.items()
            }
            denominator = _sum(list(amounts.values()))
            if denominator is None or denominator <= 0:
                return ()
            return tuple(
                OptionMapRanking(strike=k, turnover=v, share=v / denominator)
                for k, v in sorted(
                    amounts.items(), key=lambda x: (-(x[1] or 0), -x[0])
                )[:3]
                if v is not None and v > 0
            )

        coverage = _coverage(group, "turnover", snapshot.complete)
        windows[maximum] = OptionMapWindow(
            dte_max=maximum,
            total=group,
            rankings=rankings("all"),
            call_rankings=rankings("call"),
            put_rankings=rankings("put"),
            partial=not snapshot.complete
            or coverage.missing > 0
            or coverage.excluded > 0,
        )

    def peaks(metric: str, side: str) -> tuple[float, ...]:
        values = {
            float(k): _sum([g.call.turnover, g.put.turnover])
            if metric == "turnover"
            else getattr(g, side).gex
            for k, g in by_strike.items()
        }
        largest = max((abs(v) for v in values.values() if v is not None), default=0)
        return tuple(
            sorted(
                k
                for k, v in values.items()
                if v is not None and largest > 0 and abs(v) == largest
            )
        )

    ec = _coverage(total, "exposure", snapshot.complete)
    tc = _coverage(total, "turnover", snapshot.complete)
    reasons = tuple(
        dict.fromkeys(r for side in (total.call, total.put) for r in side.reasons)
    )
    status: Literal["available", "partial", "unavailable", "unsupported"] = (
        "unavailable"
        if not ec.valid and not tc.valid
        else "partial"
        if (
            not snapshot.complete
            or ec.missing
            or ec.excluded
            or tc.missing
            or tc.excluded
            or "metric_numeric_overflow" in reasons
        )
        else "available"
    )
    return OptionStructureMapReadModel(
        symbol=snapshot.symbol,
        snapshot=snapshot,
        status=status,
        market_at=snapshot.market_as_of
        or max(
            (
                r.observed_at
                for r in snapshot.contracts
                if r.observed_at is not None and r.observed_at <= at
            ),
            default=None,
        ),
        model_at=at,
        cells=tuple(cells),
        total=total,
        strikes=tuple(sorted({c.strike for c in cells})),
        expirations=tuple(sorted({c.expiration for c in cells})),
        strike_summary=by_strike,
        expiry_summary=by_expiry,
        windows=windows,
        call_walls=peaks("gex", "call"),
        put_walls=peaks("gex", "put"),
        turnover_peaks=peaks("turnover", "all"),
        exposure_coverage=ec,
        turnover_coverage=tc,
        reasons=reasons,
    )
