"""Read-only option board: quote premiums and risk-neutral smile probabilities."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from math import erf, floor, fsum, isfinite, log, sqrt
from statistics import stdev
from typing import Literal
import warnings

import numpy as np

from stock_prediction.contracts.options import (
    OptionBoardItem,
    OptionBoardMarket,
    OptionChainSnapshot,
    OptionContract,
    OptionImpliedProbability,
    OptionIntentSplit,
    OptionMoneyTarget,
    OptionProbCurvePoint,
    OptionSkew30,
    OptionSide,
    OptionTopContract,
)
from stock_prediction.options.analytics import NEW_YORK, build_option_analytics


def _mid(row: OptionContract) -> float | None:
    ask, bid = row.ask, row.bid or 0
    if ask is None or ask <= 0 or bid > ask:
        return None
    return bid / 2 + ask / 2


def _interpolate_iv(rows: Sequence[OptionContract], strike: float) -> float | None:
    points: dict[float, list[float]] = defaultdict(list)
    for row in rows:
        if row.iv is not None:
            points[row.strike].append(row.iv)
    ordered = sorted((k, fsum(v / len(vs) for v in vs)) for k, vs in points.items())
    exact = next((v for k, v in ordered if k == strike), None)
    if exact is not None:
        return exact
    for (low_k, low_iv), (high_k, high_iv) in zip(ordered, ordered[1:]):
        if low_k < strike < high_k:
            weight = (strike - low_k) / (high_k - low_k)
            return low_iv * (1 - weight) + high_iv * weight
    return None


def _implied_probability(
    rows: Sequence[OptionContract], spot: float, expiration: date, dte: int
) -> OptionImpliedProbability:
    points = [
        (log(row.strike / spot), row.iv)
        for row in rows
        if row.expiration == expiration
        and row.iv is not None
        and 0.75 * spot <= row.strike <= 1.25 * spot
        and (
            (row.side == "put" and row.strike < spot)
            or (row.side == "call" and row.strike >= spot)
        )
    ]
    if len(points) < 6 or len({x for x, _ in points}) < 4 or dte <= 0:
        return OptionImpliedProbability(
            expiration=expiration,
            dte=dte,
            fit_n=len(points),
            reason="insufficient_smile_points",
        )
    xs, ys = np.array(points, dtype=float).T
    t = dte / 365
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", np.exceptions.RankWarning)
            warnings.simplefilter("error", RuntimeWarning)
            coef = np.polyfit(xs, ys, 3)
            lo_iv, hi_iv = min(ys), max(ys)
            rmse = float(np.sqrt(np.mean((np.polyval(coef, xs) - ys) ** 2)))
            if not np.isfinite(coef).all() or not isfinite(rmse):
                raise ValueError("nonfinite smile fit")

            def sigma(k: float) -> float:
                value = float(np.polyval(coef, log(k / spot)))
                return min(float(hi_iv) * 1.2, max(float(lo_iv) * 0.8, value))

            def call(k: float) -> float:
                s = sigma(k)
                width = s * sqrt(t)
                d1 = (log(spot / k) + s * s * t / 2) / width

                def ncdf(x: float) -> float:
                    return 0.5 * (1 + erf(x / sqrt(2)))

                value = spot * ncdf(d1) - k * ncdf(d1 - width)
                if not isfinite(value):
                    raise ValueError("nonfinite call price")
                return value

            h, curve, previous = spot * 0.0025, [], 1.0
            for i in range(-20, 21):
                k = spot * (1 + i / 100)
                p = -(call(k + h) - call(k - h)) / (2 * h)
                if not isfinite(p):
                    raise ValueError("nonfinite probability")
                previous = min(previous, max(0.0, min(1.0, p)))
                curve.append(
                    OptionProbCurvePoint(strike=k, p_above=previous, sigma=sigma(k))
                )

            def quantile(q: float) -> float | None:
                for left, right in zip(curve, curve[1:]):
                    if left.p_above >= q >= right.p_above:
                        weight = (left.p_above - q) / (
                            (left.p_above - right.p_above) or 1
                        )
                        return left.strike + (right.strike - left.strike) * weight
                return None

            low, high = quantile(0.9), quantile(0.1)
            return OptionImpliedProbability(
                expiration=expiration,
                dte=dte,
                fit_n=len(points),
                curve=tuple(curve),
                fit_rmse=rmse,
                p_up5=curve[25].p_above,
                p_dn5=1 - curve[15].p_above,
                p_up10=curve[30].p_above,
                p_dn10=1 - curve[10].p_above,
                lo80=low,
                hi80=high,
                reason="probability_interval_outside_grid"
                if low is None or high is None
                else None,
            )
    except (
        ValueError,
        ArithmeticError,
        np.linalg.LinAlgError,
        np.exceptions.RankWarning,
        RuntimeWarning,
    ):
        return OptionImpliedProbability(
            expiration=expiration, dte=dte, fit_n=len(points), reason="smile_fit_failed"
        )


def build_option_board_item(
    snapshot: OptionChainSnapshot, *, closes: Sequence[float], market_date: date
) -> OptionBoardItem:
    """Use the snapshot's Eastern market date; closes must already be point-in-time."""
    snapshot = OptionChainSnapshot.model_validate(snapshot.model_dump())
    market_at = snapshot.market_as_of or snapshot.underlying_at
    spot = snapshot.underlying_price
    if market_at is None or spot is None:
        return OptionBoardItem(
            symbol=snapshot.symbol,
            snapshot_id=snapshot.snapshot_id,
            spot=spot,
            market_date=market_date if market_at is not None else None,
            reasons=(
                "market_time_unproven"
                if market_at is None
                else "underlying_price_unavailable",
            ),
        )
    if market_date != market_at.astimezone(NEW_YORK).date():
        raise ValueError("board market_date must match snapshot Eastern market date")
    reasons = list(snapshot.errors)
    if not snapshot.complete:
        reasons.append("incomplete_snapshot")
    rows = [
        row
        for row in snapshot.contracts
        if row.standard is True
        and snapshot.dte_min <= (row.expiration - market_date).days <= snapshot.dte_max
        and snapshot.strike_min_ratio <= row.strike / spot <= snapshot.strike_max_ratio
        and row.source_available_at <= snapshot.source_available_at
        and (
            snapshot.time_evidence == "historical_verified"
            or row.ingested_at <= snapshot.source_available_at
        )
        and (row.observed_at is None or row.observed_at <= market_at)
    ]
    if len(rows) != len(snapshot.contracts):
        reasons.append("contracts_outside_verified_window")
    iv_rows = [
        row
        for row in rows
        if (iv_at := row.greeks_at or row.observed_at) is not None
        and iv_at <= market_at
    ]
    iv_ids = {row.contract_id for row in iv_rows}
    expirations = sorted({row.expiration for row in rows})
    month = min(
        expirations,
        key=lambda expiry: (abs((expiry - market_date).days - 30), expiry),
        default=None,
    )
    week = min(
        expirations,
        key=lambda expiry: (abs((expiry - market_date).days - 7), expiry),
        default=None,
    )
    skew = None
    if month is not None:
        put_iv, call_iv = (
            _interpolate_iv(
                [r for r in iv_rows if r.expiration == month and r.side == side],
                spot * ratio,
            )
            for side, ratio in (("put", 0.95), ("call", 1.05))
        )
        skew = OptionSkew30(
            expiration=month,
            dte=(month - market_date).days,
            put_iv=put_iv,
            call_iv=call_iv,
            skew=put_iv - call_iv
            if put_iv is not None and call_iv is not None
            else None,
        )
        if skew.skew is None:
            reasons.append("skew_30_iv_not_bracketed")

    realized = None
    close_window = list(closes[-21:])
    if len(close_window) == 21 and all(isfinite(c) and c > 0 for c in close_window):
        realized = stdev(
            log(right) - log(left)
            for left, right in zip(close_window, close_window[1:])
        ) * sqrt(252)
        if not isfinite(realized):
            realized = None
    if realized is None:
        reasons.append("realized_vol_20_closes_unavailable")

    volumes: dict[OptionSide, list[float]] = {"call": [], "put": []}
    premiums: dict[OptionSide, list[float]] = {"call": [], "put": []}
    expiry_volumes: list[list[float]] = [[], [], [], []]
    expiry_premiums: list[list[float]] = [[], [], [], []]
    windows: tuple[Literal["all", "short", "long"], ...] = ("all", "short", "long")
    intents: dict[Literal["all", "short", "long"], dict[str, list[float]]] = {
        window: {field: [] for field in OptionIntentSplit.model_fields}
        for window in windows
    }
    target_windows: tuple[Literal["week", "month"], ...] = ("week", "month")
    targets: dict[Literal["week", "month"], dict[str, list[tuple[float, float]]]] = {
        window: {"call": [], "put": []} for window in target_windows
    }
    profile: dict[float, list[list[float]]] = defaultdict(lambda: [[], [], [], []])
    top: list[OptionTopContract] = []
    missing = 0
    step = next((s for s in (1, 2.5, 5, 10, 25, 50) if s >= spot * 0.10 / 16), 50)
    for row in rows:
        dte = (row.expiration - market_date).days
        index = 0 if dte <= 7 else 1 if dte <= 30 else 2 if dte <= 60 else 3
        volume = row.volume or 0
        mid = _mid(row) if row.quote_at is None or row.quote_at <= market_at else None
        premium = volume * mid * (row.multiplier or 100) if mid is not None else 0
        if not isfinite(premium):
            premium, mid = 0, None
            reasons.append("premium_numeric_overflow")
        if row.volume is None or (volume > 0 and mid is None):
            missing += 1
        volumes[row.side].append(volume)
        premiums[row.side].append(premium)
        expiry_volumes[index].append(volume)
        expiry_premiums[index].append(premium)
        otm = row.strike > spot if row.side == "call" else row.strike < spot
        field = f"{'otm' if otm else 'itm'}_{row.side}"
        for window in windows:
            if (
                window == "all"
                or (window == "short" and dte <= 7)
                or (window == "long" and dte >= 30)
            ):
                intents[window][field].append(premium)
        for target_window in target_windows:
            if (
                dte <= (7 if target_window == "week" else 35)
                and otm
                and 0.8 * spot <= row.strike <= 1.2 * spot
            ):
                targets[target_window][row.side].append((row.strike, premium))
        if 0.94 * spot <= row.strike <= 1.06 * spot:
            bucket = floor(row.strike / step + 0.5) * step
            side_index = 0 if row.side == "call" else 1
            profile[bucket][side_index].append(volume)
            profile[bucket][side_index + 2].append(premium)
        if mid is not None and volume > 0:
            top.append(
                OptionTopContract(
                    contract_id=row.contract_id,
                    side=row.side,
                    strike=row.strike,
                    expiration=row.expiration,
                    dte=dte,
                    volume=volume,
                    mid=mid,
                    premium=premium,
                    iv=row.iv if row.contract_id in iv_ids else None,
                    moneyness=row.strike / spot - 1,
                )
            )
    if missing:
        reasons.append("premium_quotes_or_volume_missing")
    target_models: dict[Literal["week", "month"], OptionMoneyTarget] = {}
    for target_window in target_windows:
        prices, amounts_by_side = {}, {}
        for side in ("call", "put"):
            amounts = targets[target_window][side]
            total = fsum(amount for _, amount in amounts)
            prices[side] = (
                fsum(k * (amount / total) for k, amount in amounts)
                if total > 0
                else None
            )
            amounts_by_side[side] = total
        target_models[target_window] = OptionMoneyTarget(
            call=prices["call"],
            put=prices["put"],
            call_premium=amounts_by_side["call"],
            put_premium=amounts_by_side["put"],
        )
    strike_profile = tuple(
        (bucket, fsum(metrics[0]), fsum(metrics[1]), fsum(metrics[2]), fsum(metrics[3]))
        for bucket, metrics in sorted(profile.items())
    )
    call_peak = max(
        (p for p in strike_profile if p[0] > spot * 1.01 and p[1] > 0),
        key=lambda p: (p[1], -p[0]),
        default=None,
    )
    put_peak = max(
        (p for p in strike_profile if p[0] < spot * 0.99 and p[2] > 0),
        key=lambda p: (p[2], -p[0]),
        default=None,
    )
    probabilities = [
        _implied_probability(iv_rows, spot, expiry, (expiry - market_date).days)
        if expiry is not None
        else None
        for expiry in (week, month)
    ]
    for label, probability in zip(("week", "month"), probabilities, strict=True):
        if probability is None or probability.reason:
            reasons.append(
                f"prob_{label}:{probability.reason if probability else 'expiration_unavailable'}"
            )
    atm_iv = build_option_analytics(snapshot).values["atm_iv_30d"]
    if atm_iv is None:
        reasons.append("atm_iv_30d_unavailable")
    return OptionBoardItem(
        symbol=snapshot.symbol,
        snapshot_id=snapshot.snapshot_id,
        spot=spot,
        market_date=market_date,
        data_status="unavailable"
        if not rows
        else "partial"
        if reasons
        else "available",
        atm_iv_30d=atm_iv,
        realized_vol_20=realized,
        skew_30=skew,
        expiry_buckets_volume=(
            fsum(expiry_volumes[0]),
            fsum(expiry_volumes[1]),
            fsum(expiry_volumes[2]),
            fsum(expiry_volumes[3]),
        ),
        expiry_buckets_premium=(
            fsum(expiry_premiums[0]),
            fsum(expiry_premiums[1]),
            fsum(expiry_premiums[2]),
            fsum(expiry_premiums[3]),
        ),
        call_volume=fsum(volumes["call"]),
        put_volume=fsum(volumes["put"]),
        call_premium=fsum(premiums["call"]),
        put_premium=fsum(premiums["put"]),
        total_volume=fsum(v for values in volumes.values() for v in values),
        total_premium=fsum(v for values in premiums.values() for v in values),
        premium_missing_count=missing,
        strike_profile=strike_profile,
        call_peak=call_peak[0] if call_peak else None,
        put_peak=put_peak[0] if put_peak else None,
        prob_week=probabilities[0],
        prob_month=probabilities[1],
        intent={
            window: OptionIntentSplit(**{k: fsum(v) for k, v in fields.items()})
            for window, fields in intents.items()
        },
        targets=target_models,
        top_contracts=tuple(
            row
            for side in ("call", "put")
            for row in sorted(
                (r for r in top if r.side == side),
                key=lambda r: (-r.premium, r.contract_id),
            )[:20]
        ),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def build_option_board_market(items: Sequence[OptionBoardItem]) -> OptionBoardMarket:
    items = [i for i in items if i.data_status in ("available", "partial")]
    ivs = [i.atm_iv_30d for i in items if i.atm_iv_30d is not None]
    return OptionBoardMarket(
        volume=fsum(i.total_volume for i in items),
        premium=fsum(i.total_premium for i in items),
        call_volume=fsum(i.call_volume for i in items),
        put_volume=fsum(i.put_volume for i in items),
        call_premium=fsum(i.call_premium for i in items),
        put_premium=fsum(i.put_premium for i in items),
        avg_atm_iv_30d=fsum(v / len(ivs) for v in ivs) if ivs else None,
        expiry_buckets_volume=(
            fsum(i.expiry_buckets_volume[0] for i in items),
            fsum(i.expiry_buckets_volume[1] for i in items),
            fsum(i.expiry_buckets_volume[2] for i in items),
            fsum(i.expiry_buckets_volume[3] for i in items),
        ),
        intent={
            window: OptionIntentSplit(
                **{
                    field: fsum(getattr(i.intent[window], field) for i in items)
                    for field in OptionIntentSplit.model_fields
                }
            )
            for window in ("all", "short", "long")
        },
    )
