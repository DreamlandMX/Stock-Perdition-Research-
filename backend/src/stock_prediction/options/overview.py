"""Saved option comparisons with verified session alignment and no collection side effects."""

from collections.abc import Callable, Sequence
from collections import defaultdict
from datetime import date, datetime, time
from math import expm1, fsum, isfinite
from zoneinfo import ZoneInfo
from typing import Literal, cast

from stock_prediction.contracts.options import (
    OptionChainSnapshot,
    OptionCoverageHistory,
    OptionDailyPair,
    OptionIntervalCoverage,
    OptionIVHistory,
    OptionModelComparison,
    OptionOverviewItem,
    OptionRiskAlert,
    OptionStockReturn,
)
from stock_prediction.options.analytics import build_option_analytics
from stock_prediction.options.monitor import option_session_close
from stock_prediction.options.serving import OptionForecastReadModel

NEW_YORK = ZoneInfo("America/New_York")
TERM_THRESHOLD = 0.0001


def market_at(snapshot: OptionChainSnapshot) -> datetime | None:
    return snapshot.market_as_of or snapshot.underlying_at


def eligible_history(
    history: Sequence[OptionChainSnapshot], at: datetime
) -> list[OptionChainSnapshot]:
    return sorted(
        (
            s
            for s in history
            if s.complete
            and cast(datetime, market_at(s)) is not None
            and s.source_available_at <= at
            and (s.time_evidence == "historical_verified" or s.captured_at <= at)
            and cast(datetime, market_at(s)) <= at
        ),
        key=lambda s: (
            cast(datetime, market_at(s)),
            s.source_available_at,
            s.snapshot_id,
        ),
    )


def completed_returns(
    returns: Sequence[OptionStockReturn], sessions: dict[date, str], at: datetime
) -> list[OptionStockReturn]:
    return sorted(
        (
            r
            for r in returns
            if r.source_available_at <= at
            and r.observed_at <= at
            and r.session in sessions
            and option_session_close(r.session, sessions[r.session]) <= at
        ),
        key=lambda r: r.session,
    )


def build_option_overview_item(
    symbol: str,
    snapshot: OptionChainSnapshot | None,
    *,
    history: Sequence[OptionChainSnapshot],
    returns: Sequence[OptionStockReturn],
    analytics_returns: Sequence[OptionStockReturn] = (),
    forecast: OptionForecastReadModel | None,
    at: datetime,
    sessions: dict[date, str],
) -> OptionOverviewItem:
    saved = eligible_history(history, at)
    dates = {cast(datetime, market_at(s)).astimezone(NEW_YORK).date() for s in saved}
    maturity: Literal["none", "single", "history"] = (
        "none" if not saved else "single" if len(saved) == 1 else "history"
    )
    if snapshot is None or not snapshot.complete:
        return OptionOverviewItem(
            symbol=symbol,
            data_status="unavailable",
            maturity="none",
            history_days=0,
            reasons=("no_complete_snapshot_available",),
        )
    current_at = market_at(snapshot)
    current = build_option_analytics(
        snapshot,
        history=saved,
        stock_returns=completed_returns(analytics_returns, sessions, current_at)
        if current_at
        else (),
        as_of=at,
    )
    prior = next(
        (
            s
            for s in reversed(saved)
            if cast(datetime, market_at(s)) < cast(datetime, current_at)
            and all(
                getattr(s, key) == getattr(snapshot, key)
                for key in (
                    "provider",
                    "feed",
                    "normalization_version",
                    "dataset_revision",
                    "dte_min",
                    "dte_max",
                    "strike_min_ratio",
                    "strike_max_ratio",
                )
            )
        ),
        None,
    )
    previous = build_option_analytics(prior, as_of=at) if prior else None
    comparisons = []
    for h in (1, 5, 20):
        model = (
            next((m for m in forecast.horizons if m.horizon_days == h), None)
            if forecast
            else None
        )
        move = current.implied_moves[h - 1]
        aligned = (
            forecast is not None
            and forecast.stock_cutoff is not None
            and current_at is not None
            and snapshot.source_available_at <= forecast.stock_cutoff
            and current_at <= forecast.stock_cutoff
            and (
                snapshot.time_evidence == "historical_verified"
                or snapshot.captured_at <= forecast.stock_cutoff
            )
            and current_at.astimezone(NEW_YORK).date()
            == forecast.stock_cutoff.astimezone(NEW_YORK).date()
        )
        comparisons.append(
            OptionModelComparison(
                horizon_days=h,
                q10=model.q10 if model else None,
                q50=model.q50 if model else None,
                q90=model.q90 if model else None,
                width_ratio=(model.q90 - model.q10) / (2 * 1.2816 * move.sigma_pct)
                if model
                and move.sigma_pct is not None
                and move.sigma_pct > 0
                and aligned
                else None,
                model_source=forecast.model_source if forecast else None,
                status=forecast.status if forecast else "refused",
                reasons=tuple(
                    dict.fromkeys(
                        (
                            *(
                                forecast.reasons
                                if forecast
                                else ("stock_forecast_unavailable",)
                            ),
                            *(
                                ("model_option_cutoff_mismatch",)
                                if model and not aligned
                                else ()
                            ),
                        )
                    )
                ),
            )
        )
    slope = current.values.get("iv_term_slope_per_day")
    term: Literal["upward", "flat", "inverted"] | None = (
        "upward"
        if slope is not None and slope > TERM_THRESHOLD
        else "inverted"
        if slope is not None and slope < -TERM_THRESHOLD
        else "flat"
        if slope is not None
        else None
    )
    alerts = []
    if current_at is not None:
        if term == "inverted":
            alerts.append(
                OptionRiskAlert(
                    rule="term_structure_inverted",
                    value=cast(float, slope),
                    threshold=-TERM_THRESHOLD,
                    market_as_of=current_at,
                )
            )
        gap = current.values.get("iv_minus_realized_20sessions")
        if gap is not None and gap > 0.1:
            alerts.append(
                OptionRiskAlert(
                    rule="iv_minus_realized_extreme",
                    value=gap,
                    threshold=0.1,
                    market_as_of=current_at,
                )
            )
        skew = current.values.get("put_call_skew_25delta")
        prior_skew = previous.values.get("put_call_skew_25delta") if previous else None
        if skew is not None and prior_skew is not None and skew - prior_skew > 0.03:
            alerts.append(
                OptionRiskAlert(
                    rule="skew_increase",
                    value=skew - prior_skew,
                    threshold=0.03,
                    market_as_of=current_at,
                )
            )
        for comparison in comparisons:
            if comparison.width_ratio is not None and (
                comparison.width_ratio < 0.7 or comparison.width_ratio > 1.3
            ):
                alerts.append(
                    OptionRiskAlert(
                        rule=f"width_ratio_{comparison.horizon_days}d_extreme",
                        value=comparison.width_ratio,
                        threshold=0.7 if comparison.width_ratio < 0.7 else 1.3,
                        market_as_of=current_at,
                    )
                )
    daily = None
    actuals = completed_returns(returns, sessions, at)
    if actuals:
        actual = actuals[-1]
        days = sorted(d for d in sessions if d < actual.session)
        previous_day = days[-1] if days else None
        paired = next(
            (
                s
                for s in reversed(saved)
                if cast(datetime, market_at(s)).astimezone(NEW_YORK).date()
                == previous_day
                and previous_day is not None
                and cast(datetime, market_at(s))
                < option_session_close(previous_day, sessions[previous_day])
                and s.source_available_at
                <= option_session_close(previous_day, sessions[previous_day])
                and (
                    s.time_evidence == "historical_verified"
                    or s.captured_at
                    <= option_session_close(previous_day, sessions[previous_day])
                )
            ),
            None,
        )
        if paired:
            implied = build_option_analytics(paired).implied_moves[0]
            if implied.sigma_pct is not None:
                daily = OptionDailyPair(
                    session=actual.session,
                    realized_return=expm1(actual.log_return),
                    sigma_pct=implied.sigma_pct,
                    previous_snapshot_id=paired.snapshot_id,
                    previous_market_as_of=cast(datetime, market_at(paired)),
                )
    # One observation per market session keeps the accumulated-day denominator honest.
    by_day = {
        cast(datetime, market_at(s)).astimezone(NEW_YORK).date(): s for s in saved
    }
    iv_history = (
        tuple(
            OptionIVHistory(
                market_as_of=cast(datetime, market_at(s)),
                atm_iv=current.values.get("atm_iv_30d")
                if s.snapshot_id == snapshot.snapshot_id
                else build_option_analytics(s).values.get("atm_iv_30d"),
            )
            for s in by_day.values()
        )
        if len(by_day) >= 20
        else ()
    )
    reasons = list(current.reasons)
    if daily is None:
        reasons.append("previous_session_implied_pair_unavailable")
    if len(saved) < 2:
        reasons.append("history_requires_two_snapshots")
    return OptionOverviewItem(
        symbol=symbol,
        snapshot_id=snapshot.snapshot_id,
        market_as_of=current_at,
        data_status=current.status,
        maturity=maturity,
        history_days=len(dates & sessions.keys()) if sessions else None,
        analytics=current,
        implied_moves=current.implied_moves,
        model_horizons=tuple(comparisons),
        daily_pair=daily,
        iv_history=iv_history,
        previous_skew_points=previous.skew_points if previous else (),
        previous_snapshot_id=prior.snapshot_id if prior else None,
        term_structure=term,
        term_slope_threshold=TERM_THRESHOLD,
        risk_alerts=tuple(alerts),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def build_interval_coverage(
    histories: dict[str, Sequence[OptionChainSnapshot]],
    returns: dict[str, Sequence[OptionStockReturn]],
    *,
    sessions: dict[date, str],
    at: datetime,
    forecast_reader: Callable[[str, datetime], OptionForecastReadModel | None],
) -> tuple[OptionIntervalCoverage, ...]:
    """Score only matched model/option bands saved before entry; use the same labels."""
    return summarize_interval_coverage(
        build_interval_samples(
            histories,
            returns,
            sessions=sessions,
            at=at,
            forecast_reader=forecast_reader,
        )
    )


def build_interval_samples(
    histories: dict[str, Sequence[OptionChainSnapshot]],
    returns: dict[str, Sequence[OptionStockReturn]],
    *,
    sessions: dict[date, str],
    at: datetime,
    forecast_reader: Callable[[str, datetime], OptionForecastReadModel | None],
) -> dict[int, list[tuple[datetime, bool, bool]]]:
    samples: dict[int, list[tuple[datetime, bool, bool]]] = {h: [] for h in (1, 5, 20)}
    verified = sorted(sessions)
    for symbol, history in histories.items():
        actuals = completed_returns(returns.get(symbol, ()), sessions, at)
        by_session = {r.session: r for r in actuals}
        if len(by_session) != len(actuals):
            continue
        anchors = defaultdict(list)
        for saved in eligible_history(history, at):
            anchors[
                cast(datetime, market_at(saved)).astimezone(NEW_YORK).date()
            ].append(saved)
        for day, candidates in anchors.items():
            if day not in sessions:
                continue
            index = verified.index(day)
            if index + 1 >= len(verified) or verified[index + 1] not in by_session:
                continue
            entry = datetime.combine(verified[index + 1], time(9, 30), NEW_YORK)
            forecast = forecast_reader(symbol, entry)
            if (
                forecast is None
                or forecast.status not in ("enhanced", "baseline_fallback")
                or forecast.stock_cutoff is None
                or forecast.stock_cutoff.astimezone(NEW_YORK).date() != day
            ):
                continue
            cutoff = min(
                forecast.stock_cutoff, option_session_close(day, sessions[day])
            )
            snapshot = next(
                (
                    s
                    for s in reversed(candidates)
                    if cast(datetime, market_at(s)) <= cutoff
                    and s.source_available_at <= cutoff
                    and (
                        s.time_evidence == "historical_verified"
                        or s.captured_at <= cutoff
                    )
                ),
                None,
            )
            if snapshot is None:
                continue
            analytics = build_option_analytics(snapshot)
            for h in (1, 5, 20):
                future = verified[index + 1 : index + h + 1]
                if len(future) != h or any(d not in by_session for d in future):
                    continue
                move = analytics.implied_moves[h - 1]
                if move.lower_pct is None or move.upper_pct is None:
                    continue
                band = next((m for m in forecast.horizons if m.horizon_days == h), None)
                if (
                    band is None
                    or forecast.stock_cutoff is None
                    or forecast.stock_cutoff.astimezone(NEW_YORK).date() != day
                    or snapshot.source_available_at > forecast.stock_cutoff
                    or cast(datetime, market_at(snapshot)) > forecast.stock_cutoff
                    or (
                        snapshot.time_evidence != "historical_verified"
                        and snapshot.captured_at > forecast.stock_cutoff
                    )
                ):
                    continue
                value = expm1(fsum(by_session[d].log_return for d in future))
                if isfinite(value):
                    samples[h].append(
                        (
                            by_session[future[-1]].observed_at,
                            band.q10 <= value <= band.q90,
                            move.lower_pct <= value <= move.upper_pct,
                        )
                    )
    return samples


def summarize_interval_coverage(
    samples: dict[int, list[tuple[datetime, bool, bool]]],
) -> tuple[OptionIntervalCoverage, ...]:
    output = []
    for h, values in samples.items():
        values.sort(key=lambda row: row[0])
        history = []
        for index, (time_at, _, _) in enumerate(values):
            window = values[max(0, index - 19) : index + 1]
            history.append(
                OptionCoverageHistory(
                    market_as_of=time_at,
                    model_samples=len(window),
                    implied_samples=len(window),
                    model_coverage=sum(r[1] for r in window) / len(window)
                    if len(window) >= 20
                    else None,
                    implied_coverage=sum(r[2] for r in window) / len(window)
                    if len(window) >= 20
                    else None,
                )
            )
        enough = len(values) >= 20
        output.append(
            OptionIntervalCoverage(
                horizon_days=h,
                model_samples=len(values),
                implied_samples=len(values),
                model_coverage=sum(r[1] for r in values) / len(values)
                if enough
                else None,
                implied_coverage=sum(r[2] for r in values) / len(values)
                if enough
                else None,
                status="available" if enough else "insufficient_samples",
                history=tuple(history),
                reasons=() if enough else ("mature_matched_samples_insufficient",),
            )
        )
    return tuple(output)
