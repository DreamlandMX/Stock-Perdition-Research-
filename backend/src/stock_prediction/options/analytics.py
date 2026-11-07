"""Evidence-gated numerical option research; missing values never mean zero."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from math import fsum, isfinite, sqrt
from statistics import stdev
from zoneinfo import ZoneInfo

from stock_prediction.contracts.options import (
    OptionAnalyticsReadModel,
    OptionChainSnapshot,
    OptionConcentrationPoint,
    OptionContract,
    OptionHistoryPoint,
    OptionIVTerm,
    OptionMetric,
    OptionSkewPoint,
    OptionStockReturn,
    OptionImpliedMove,
)
from stock_prediction.options.structure import metric_value

NEW_YORK = ZoneInfo("America/New_York")


def implied_moves(
    terms: Sequence[OptionIVTerm], spot: float | None, *, complete: bool = True
) -> tuple[OptionImpliedMove, ...]:
    """Convert h trading days to h*365/252 calendar days; never extrapolate IV."""
    valid = sorted((t.dte, t.atm_iv) for t in terms if t.atm_iv is not None)
    output = []
    for horizon in range(1, 21):
        target = horizon * 365 / 252
        iv = next((v for d, v in valid if d == target), None)
        if iv is None:
            for (left, low), (right, high) in zip(valid, valid[1:]):
                if left <= target <= right and right > left:
                    iv = low + (high - low) * (target - left) / (right - left)
                    break
        reason = (
            "incomplete_snapshot"
            if not complete
            else "underlying_price_unavailable"
            if spot is None
            else "iv_horizon_not_bracketed"
            if iv is None
            else None
        )
        sigma = iv * sqrt(horizon / 252) if iv is not None and reason is None else None
        band = 1.2816 * sigma if sigma is not None else None
        output.append(
            OptionImpliedMove(
                horizon_days=horizon,
                iv=iv if reason is None else None,
                sigma_pct=sigma,
                lower_pct=-band if band is not None else None,
                upper_pct=band,
                lower_price=spot * (1 - band)
                if spot is not None and band is not None
                else None,
                upper_price=spot * (1 + band)
                if spot is not None and band is not None
                else None,
                reason=reason,
            )
        )
    return tuple(output)


FEATURE_NAMES = (
    "call_oi",
    "put_oi",
    "put_call_oi",
    "call_volume",
    "put_volume",
    "put_call_volume",
    "call_gamma",
    "put_gamma",
    "atm_iv_30d",
    "atm_iv_dte",
    "iv_term_slope_per_day",
    "iv_term_near_dte",
    "iv_term_far_dte",
    "put_call_skew_25delta",
    "skew_dte",
    "otm_put_minus_atm_iv",
    "realized_volatility_20sessions",
    "iv_minus_realized_20sessions",
    "realized_annualization_sessions",
    "realized_observations",
    "volume_oi_ratio",
    "oi_strike_hhi",
    "volume_strike_hhi",
    "gamma_strike_hhi",
    "oi_near_30d_share",
    "volume_near_30d_share",
    "gamma_near_30d_share",
    "oi_peak_distance_ratio",
    "gamma_peak_distance_ratio",
    "oi_change_fraction",
    "volume_change_fraction",
    "atm_iv_change",
    "oi_concentration_change",
    "history_gap_seconds",
    "snapshot_age_seconds",
    "contract_coverage_ratio",
    "excluded_contract_ratio",
    "iv_valid_ratio",
    "oi_valid_ratio",
    "gamma_valid_ratio",
    "quote_valid_ratio",
    "mean_relative_spread",
    "missing_field_ratio",
)


def _market_at(snapshot: OptionChainSnapshot) -> datetime | None:
    return snapshot.market_as_of or snapshot.underlying_at


def _available(snapshot: OptionChainSnapshot, at: datetime) -> bool:
    market_at = _market_at(snapshot)
    return (
        snapshot.source_available_at <= at
        and (
            snapshot.time_evidence == "historical_verified"
            or snapshot.captured_at <= at
        )
        and (market_at is None or market_at <= at)
    )


def _rows(snapshot: OptionChainSnapshot) -> list[OptionContract]:
    market_at = _market_at(snapshot)
    spot = snapshot.underlying_price
    if market_at is None or spot is None:
        return []
    day = market_at.astimezone(NEW_YORK).date()
    return [
        r
        for r in snapshot.contracts
        if r.standard is True
        and snapshot.dte_min <= (r.expiration - day).days <= snapshot.dte_max
        and snapshot.strike_min_ratio <= r.strike / spot <= snapshot.strike_max_ratio
    ]


def _value(
    row: OptionContract,
    metric: OptionMetric,
    snapshot: OptionChainSnapshot,
    at: datetime,
) -> float | None:
    if row.source_available_at > at or (
        snapshot.time_evidence != "historical_verified" and row.ingested_at > at
    ):
        return None
    observed = (
        (row.quote_at or row.observed_at)
        if metric == "spread"
        else (row.greeks_at or row.observed_at)
        if metric in ("iv", "gamma")
        else row.observed_at
    )
    market_at = _market_at(snapshot)
    if observed is None or observed > at or market_at is None or observed > market_at:
        return None
    if metric in ("oi", "gamma") and (
        row.oi_date is None or row.oi_date > market_at.astimezone(NEW_YORK).date()
    ):
        return None
    return metric_value(row, metric)


def _sum(values: Sequence[float]) -> float | None:
    if not values:
        return None
    try:
        total = fsum(values)
    except OverflowError:
        return None
    return total if isfinite(total) else None


def _complete_values(
    rows: Sequence[OptionContract],
    metric: OptionMetric,
    snapshot: OptionChainSnapshot,
    at: datetime,
) -> list[float] | None:
    values = [_value(r, metric, snapshot, at) for r in rows]
    if not snapshot.complete or not values or any(v is None for v in values):
        return None
    return [v for v in values if v is not None]


def _terms(
    snapshot: OptionChainSnapshot, rows: Sequence[OptionContract], at: datetime
) -> tuple[OptionIVTerm, ...]:
    spot, market_at = snapshot.underlying_price, _market_at(snapshot)
    if spot is None or market_at is None:
        return ()
    grouped: dict[object, list[OptionContract]] = defaultdict(list)
    for row in rows:
        if abs(row.strike / spot - 1) <= 0.05:
            grouped[row.expiration].append(row)
    terms = []
    for expiry_rows in grouped.values():
        strike = min(
            {r.strike for r in expiry_rows}, key=lambda k: (abs(k / spot - 1), k)
        )
        selected = [r for r in expiry_rows if r.strike == strike]
        legs: dict[str, float | None] = {}
        for side in ("call", "put"):
            side_rows = [r for r in selected if r.side == side]
            legs[side] = (
                _value(side_rows[0], "iv", snapshot, at)
                if len(side_rows) == 1
                else None
            )
        valid = [v for v in legs.values() if v is not None]
        terms.append(
            OptionIVTerm(
                expiration=selected[0].expiration,
                dte=(
                    selected[0].expiration - market_at.astimezone(NEW_YORK).date()
                ).days,
                strike=strike,
                moneyness=strike / spot,
                call_iv=legs["call"],
                put_iv=legs["put"],
                atm_iv=fsum(v / 2 for v in valid) if len(valid) == 2 else None,
                contract_ids=tuple(r.contract_id for r in selected),
            )
        )
    return tuple(sorted(terms, key=lambda t: t.expiration))


def _skews(
    snapshot: OptionChainSnapshot, rows: Sequence[OptionContract], at: datetime
) -> tuple[OptionSkewPoint, ...]:
    market_at, spot = _market_at(snapshot), snapshot.underlying_price
    if market_at is None or spot is None:
        return ()
    points = []
    for expiry in sorted({r.expiration for r in rows}):
        legs: dict[str, OptionContract | None] = {}
        for side in ("put", "call"):
            candidates = [
                r
                for r in rows
                if r.expiration == expiry
                and r.side == side
                and r.delta_units == "signed_unit_v1"
                and r.delta is not None
                and abs(abs(r.delta) - 0.25) <= 0.05
                and (r.strike < spot if side == "put" else r.strike > spot)
                and _value(r, "iv", snapshot, at) is not None
            ]
            legs[side] = min(
                candidates,
                key=lambda r: (abs(abs(r.delta or 0) - 0.25), r.contract_id),
                default=None,
            )
        put, call = legs["put"], legs["call"]
        put_iv, call_iv = (put.iv if put else None), (call.iv if call else None)
        valid = put_iv is not None and call_iv is not None
        points.append(
            OptionSkewPoint(
                expiration=expiry,
                dte=(expiry - market_at.astimezone(NEW_YORK).date()).days,
                put_25delta_iv=put_iv,
                call_25delta_iv=call_iv,
                skew=put_iv - call_iv
                if put_iv is not None and call_iv is not None
                else None,
                contract_ids=tuple(r.contract_id for r in (put, call) if r is not None),
                reasons=() if valid else ("delta_units_or_25delta_legs_missing",),
            )
        )
    return tuple(points)


def _comparable(
    current: OptionChainSnapshot, previous: OptionChainSnapshot, at: datetime
) -> bool:
    current_at, previous_at = _market_at(current), _market_at(previous)
    if not current.complete or not previous.complete or not _available(previous, at):
        return False
    if current_at is None or previous_at is None or previous_at >= current_at:
        return False
    keys = (
        "symbol",
        "provider",
        "feed",
        "normalization_version",
        "dataset_revision",
        "dte_min",
        "dte_max",
        "strike_min_ratio",
        "strike_max_ratio",
    )
    if any(getattr(current, k) != getattr(previous, k) for k in keys):
        return False
    if (
        current_at.astimezone(NEW_YORK).time()
        != previous_at.astimezone(NEW_YORK).time()
    ):
        return False

    def identities(s: OptionChainSnapshot) -> set[tuple[object, ...]]:
        return {
            (
                r.contract_id,
                r.side,
                r.strike,
                r.expiration,
                r.multiplier,
                r.standard,
                r.settlement,
                r.exercise,
            )
            for r in _rows(s)
        }

    return bool(_rows(current)) and identities(current) == identities(previous)


def build_option_analytics(
    snapshot: OptionChainSnapshot,
    *,
    history: Sequence[OptionChainSnapshot] = (),
    stock_returns: Sequence[OptionStockReturn] = (),
    as_of: datetime | None = None,
) -> OptionAnalyticsReadModel:
    """V2 rules: ATM within 5%, 30D within 7 days, Delta within 0.05; no interpolation."""
    snapshot = OptionChainSnapshot.model_validate(snapshot.model_dump())
    at = as_of or snapshot.source_available_at
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("as_of requires timezone")
    values: dict[str, float | None] = dict.fromkeys(FEATURE_NAMES)
    missing: dict[str, tuple[str, ...]] = {}
    reasons = list(snapshot.errors)
    market_at, spot = _market_at(snapshot), snapshot.underlying_price
    valid_snapshot = _available(snapshot, at)
    if not valid_snapshot:
        reasons.append("snapshot_not_available")
    if market_at is None:
        reasons.append("market_time_unproven")
    if spot is None:
        reasons.append("underlying_price_unavailable")
    if not snapshot.complete:
        reasons.append("incomplete_snapshot")
    rows = _rows(snapshot) if valid_snapshot else []
    terms = _terms(snapshot, rows, at)
    skews = _skews(snapshot, rows, at)
    concentration: list[OptionConcentrationPoint] = []
    if valid_snapshot:
        expected = len(snapshot.expected_contract_ids)
        values["contract_coverage_ratio"] = (
            len(snapshot.contracts) / expected if expected else None
        )
        values["excluded_contract_ratio"] = (
            sum(r.standard is not True for r in snapshot.contracts)
            / len(snapshot.contracts)
            if snapshot.contracts
            else None
        )
        values["snapshot_age_seconds"] = (
            (at - market_at).total_seconds() if market_at else None
        )
        for metric in ("oi", "volume", "gamma"):
            for side in ("call", "put"):
                complete = _complete_values(
                    [r for r in rows if r.side == side], metric, snapshot, at
                )
                values[f"{side}_{metric}"] = (
                    _sum(complete) if complete is not None else None
                )
            if metric in ("oi", "volume"):
                call, put = values[f"call_{metric}"], values[f"put_{metric}"]
                values[f"put_call_{metric}"] = (
                    put / call
                    if call is not None and call > 0 and put is not None
                    else None
                )
            complete = _complete_values(rows, metric, snapshot, at)
            total = _sum(complete) if complete is not None else None
            if total is None or total <= 0 or spot is None or market_at is None:
                continue
            by_strike: dict[float, list[float]] = defaultdict(list)
            for row, value in zip(rows, complete or (), strict=True):
                by_strike[row.strike].append(value)
            points = [
                OptionConcentrationPoint(
                    metric=metric,
                    strike=strike,
                    value=fsum(numbers),
                    share=fsum(v / total for v in numbers),
                    distance_ratio=strike / spot - 1,
                    contract_ids=tuple(
                        r.contract_id for r in rows if r.strike == strike
                    ),
                )
                for strike, numbers in sorted(by_strike.items())
            ]
            concentration.extend(points)
            values[f"{metric}_strike_hhi"] = fsum(p.share**2 for p in points)
            near = [
                _value(r, metric, snapshot, at) or 0
                for r in rows
                if (r.expiration - market_at.astimezone(NEW_YORK).date()).days <= 30
            ]
            values[f"{metric}_near_30d_share"] = fsum(v / total for v in near)
            if metric in ("oi", "gamma"):
                values[f"{metric}_peak_distance_ratio"] = max(
                    points, key=lambda p: (p.share, -abs(p.distance_ratio), -p.strike)
                ).distance_ratio
        coverage_metrics: tuple[tuple[OptionMetric, str], ...] = (
            ("iv", "iv"),
            ("oi", "oi"),
            ("gamma", "gamma"),
            ("spread", "quote"),
        )
        for metric, name in coverage_metrics:
            valid_count = sum(_value(r, metric, snapshot, at) is not None for r in rows)
            values[f"{name}_valid_ratio"] = valid_count / len(rows) if rows else None
        known = sum(
            _value(r, metric, snapshot, at) is not None
            for r in rows
            for metric in ("iv", "oi", "gamma", "spread")
        )
        values["missing_field_ratio"] = 1 - known / (len(rows) * 4) if rows else None
        spreads = _complete_values(rows, "spread", snapshot, at)
        if spreads is not None:
            relative = [
                spread / ((r.ask or 0) / 2 + (r.bid or 0) / 2)
                for r, spread in zip(rows, spreads, strict=True)
            ]
            values["mean_relative_spread"] = fsum(v / len(relative) for v in relative)
    near_term = min(
        (t for t in terms if t.atm_iv is not None and abs(t.dte - 30) <= 7),
        key=lambda t: (abs(t.dte - 30), t.dte),
        default=None,
    )
    if near_term is not None:
        values["atm_iv_30d"], values["atm_iv_dte"] = (
            near_term.atm_iv,
            float(near_term.dte),
        )
        far = min(
            (
                t
                for t in terms
                if t.atm_iv is not None
                and t.dte > near_term.dte
                and t.strike == near_term.strike
            ),
            key=lambda t: t.dte,
            default=None,
        )
        if far is not None and far.atm_iv is not None and near_term.atm_iv is not None:
            values["iv_term_slope_per_day"] = (far.atm_iv - near_term.atm_iv) / (
                far.dte - near_term.dte
            )
            values["iv_term_near_dte"], values["iv_term_far_dte"] = (
                float(near_term.dte),
                float(far.dte),
            )
        skew = next((s for s in skews if s.expiration == near_term.expiration), None)
        if skew is not None:
            values["put_call_skew_25delta"] = skew.skew
            values["skew_dte"] = float(skew.dte) if skew.skew is not None else None
            if skew.put_25delta_iv is not None and near_term.atm_iv is not None:
                values["otm_put_minus_atm_iv"] = skew.put_25delta_iv - near_term.atm_iv
    oi = (
        _sum([v for k in ("call_oi", "put_oi") if (v := values[k]) is not None])
        if values["call_oi"] is not None and values["put_oi"] is not None
        else None
    )
    volume = (
        _sum([v for k in ("call_volume", "put_volume") if (v := values[k]) is not None])
        if values["call_volume"] is not None and values["put_volume"] is not None
        else None
    )
    values["volume_oi_ratio"] = (
        volume / oi if volume is not None and oi is not None and oi > 0 else None
    )
    if valid_snapshot and market_at is not None:
        returns = sorted(
            (
                OptionStockReturn.model_validate(r.model_dump())
                for r in stock_returns
                if r.symbol == snapshot.symbol
                and r.source_available_at <= at
                and r.observed_at <= market_at
                and r.session <= market_at.astimezone(NEW_YORK).date()
            ),
            key=lambda r: r.session,
        )
        if len({r.session for r in returns}) == len(returns) and len(returns) >= 20:
            window = returns[-20:]
            annualizations = {r.annualization_sessions for r in window}
            if len(annualizations) == 1:
                annualization = window[0].annualization_sessions
                rv = stdev(r.log_return for r in window) * sqrt(annualization)
                if isfinite(rv):
                    values["realized_volatility_20sessions"] = rv
                    values["realized_annualization_sessions"] = float(annualization)
                    values["realized_observations"] = 20.0
                    if near_term is not None and near_term.atm_iv is not None:
                        values["iv_minus_realized_20sessions"] = near_term.atm_iv - rv
    comparable = sorted(
        (s for s in history if valid_snapshot and _comparable(snapshot, s, at)),
        key=lambda s: (_market_at(s) or s.captured_at, s.snapshot_id),
    )
    history_points = []
    if comparable:
        previous = comparable[-1]
        prior = build_option_analytics(previous, as_of=at)
        for metric in ("oi", "volume"):
            current_values, previous_values = (
                [values[f"{s}_{metric}"] for s in ("call", "put")],
                [prior.values[f"{s}_{metric}"] for s in ("call", "put")],
            )
            if all(v is not None for v in (*current_values, *previous_values)):
                curr, prev = (
                    _sum([v for v in current_values if v is not None]),
                    _sum([v for v in previous_values if v is not None]),
                )
                if curr is not None and prev is not None and prev > 0:
                    values[f"{metric}_change_fraction"] = (curr - prev) / prev
        for target, source in (
            ("atm_iv_change", "atm_iv_30d"),
            ("oi_concentration_change", "oi_strike_hhi"),
        ):
            current, old = values[source], prior.values[source]
            if current is not None and old is not None:
                if source != "atm_iv_30d" or (
                    near_term is not None
                    and any(
                        t.expiration == near_term.expiration
                        and t.strike == near_term.strike
                        and t.atm_iv == old
                        for t in prior.iv_terms
                    )
                ):
                    values[target] = current - old
        previous_at = _market_at(previous)
        values["history_gap_seconds"] = (
            (market_at - previous_at).total_seconds()
            if market_at and previous_at
            else None
        )
        for s in (*comparable, snapshot):
            source_at, source_spot = _market_at(s), s.underlying_price
            if source_at is None or source_spot is None:
                continue
            source_rows = _rows(s)
            for strike in sorted({r.strike for r in source_rows}):
                group = [r for r in source_rows if r.strike == strike]
                aggregates = {
                    m: _complete_values(group, m, s, at) for m in ("oi", "gamma")
                }
                history_points.append(
                    OptionHistoryPoint(
                        snapshot_id=s.snapshot_id,
                        market_as_of=source_at,
                        source_available_at=s.source_available_at,
                        underlying_price=source_spot,
                        strike=strike,
                        oi=_sum(aggregates["oi"])
                        if aggregates["oi"] is not None
                        else None,
                        gamma=_sum(aggregates["gamma"])
                        if aggregates["gamma"] is not None
                        else None,
                        contract_ids=tuple(r.contract_id for r in group),
                    )
                )
    for name, feature_value in values.items():
        if feature_value is not None and not isfinite(feature_value):
            values[name] = None
            missing[name] = ("metric_numeric_overflow",)
        elif feature_value is None:
            reason = (
                "delta_units_or_25delta_legs_missing"
                if name in ("put_call_skew_25delta", "skew_dte")
                else "historical_returns_or_annualization_unproven"
                if name.startswith("realized") or name.startswith("iv_minus")
                else "comparable_history_missing"
                if name
                in (
                    "oi_change_fraction",
                    "volume_change_fraction",
                    "atm_iv_change",
                    "oi_concentration_change",
                    "history_gap_seconds",
                )
                else "field_time_unit_or_coverage_unproven"
            )
            missing[name] = (reason,)
    return OptionAnalyticsReadModel(
        symbol=snapshot.symbol,
        snapshot_id=snapshot.snapshot_id,
        as_of=at,
        status="unavailable"
        if not rows
        else "partial"
        if reasons or missing
        else "available",
        provider=snapshot.provider,
        feed=snapshot.feed,
        normalization_version=snapshot.normalization_version,
        dataset_revision=snapshot.dataset_revision,
        captured_at=snapshot.captured_at,
        market_as_of=snapshot.market_as_of,
        source_available_at=snapshot.source_available_at,
        values=values,
        missing_reasons=missing,
        source_snapshot_ids=tuple(s.snapshot_id for s in (*comparable, snapshot))
        if valid_snapshot
        else (),
        iv_terms=terms,
        skew_points=skews,
        concentration=tuple(concentration),
        history=tuple(history_points),
        reasons=tuple(dict.fromkeys(reasons)),
        implied_moves=implied_moves(
            terms, spot, complete=snapshot.complete and valid_snapshot
        ),
    )
