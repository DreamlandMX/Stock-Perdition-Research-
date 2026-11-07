"""Explicit collection leases; GET only reads saved pulse data."""

from collections.abc import Callable
from datetime import datetime
import json
from typing import Annotated, Literal, NoReturn
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, Request, Response

from stock_prediction.api.errors import ApiError
from stock_prediction.api.read_models import ErrorResponse
from stock_prediction.contracts.options import (
    OPTION_SYMBOLS,
    OptionPulseLease,
    OptionPulseResponse,
    OptionPulseBar,
    OptionPulseConePoint,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.data.sessions import SessionSchedule
from stock_prediction.options.analytics import build_option_analytics
from stock_prediction.options.intraday import (
    OptionPulseStore,
    intraday_cone,
    pulse_from_snapshot,
    NEW_YORK,
)
from stock_prediction.options.store import OptionSnapshotStore
from stock_prediction.serving.repository import (
    ResearchReadRepository,
    SnapshotQuery,
    HistoryQuery,
    SnapshotNotFound,
)


def build_pulse_router(
    store: OptionSnapshotStore,
    clock: Callable[[], datetime],
    repository: ResearchReadRepository | None,
) -> APIRouter:
    router = APIRouter(
        prefix="/instruments/{symbol}/options/pulse",
        tags=["options-pulse"],
        responses={422: {"model": ErrorResponse}},
    )
    pulse = OptionPulseStore(store.root / "_pulse")

    def fail(code: str, status: int, symbol: str) -> NoReturn:
        raise ApiError(
            status_code=status,
            error_code=code,
            message=code,
            retryable=status == 503,
            affected_scope=[symbol],
        )

    def write_guard(request: Request, symbol: str) -> None:
        if symbol not in OPTION_SYMBOLS:
            fail("symbol_outside_option_scope", 422, symbol)
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            fail("pulse_origin_denied", 403, symbol)

    @router.post("/sessions", response_model=OptionPulseLease, status_code=201)
    def acquire(symbol: str, request: Request) -> OptionPulseLease:
        write_guard(request, symbol)
        return pulse.acquire(symbol, clock())

    @router.put("/sessions/{session_id}", response_model=OptionPulseLease)
    def renew(symbol: str, session_id: str, request: Request) -> OptionPulseLease:
        write_guard(request, symbol)
        try:
            lease = pulse.renew(symbol, session_id, clock())
        except ValueError:
            lease = None
        if lease is None:
            fail("pulse_session_expired", 404, symbol)
        return lease

    @router.delete("/sessions/{session_id}", status_code=204)
    def release(symbol: str, session_id: str, request: Request) -> Response:
        write_guard(request, symbol)
        try:
            pulse.release(symbol, session_id)
        except ValueError:
            fail("invalid_pulse_session", 422, symbol)
        return Response(status_code=204)

    @router.get("", response_model=OptionPulseResponse)
    def read(
        symbol: str,
        mode: Literal["daily", "live", "latest"] = "daily",
        snapshot_id: Annotated[
            str | None, Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
        ] = None,
        option_snapshot_id: Annotated[
            str | None, Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
        ] = None,
        as_of: UtcDatetime | None = None,
    ) -> OptionPulseResponse:
        now = clock()
        empty = OptionPulseResponse(
            symbol=symbol,
            mode=mode,
            timeframe="5m" if mode == "live" else "1d",
            generated_at=now,
        )
        if symbol not in OPTION_SYMBOLS:
            return empty.model_copy(
                update={
                    "data_status": "unsupported",
                    "reasons": ("symbol_outside_option_scope",),
                }
            )
        try:
            if mode == "live":
                if snapshot_id or option_snapshot_id or as_of:
                    fail("live_pulse_cannot_pin_snapshot", 422, symbol)
                state = pulse.state(symbol)
                worker = pulse.root / "worker.json"
                heartbeat = (
                    datetime.fromisoformat(
                        json.loads(worker.read_bytes())["heartbeat_at"]
                    )
                    if worker.exists()
                    else None
                )
                active = symbol in pulse.active_symbols(now)
                reason = (
                    state.get("reason", "waiting_first_collection")
                    if active
                    else "inactive"
                )
                if active and (
                    heartbeat is None or (now - heartbeat).total_seconds() > 90
                ):
                    reason = "worker_offline"
                batch = pulse.latest_batch(symbol)
                response = empty
                if batch and batch.captured_at <= now:
                    snapshot = store.load(symbol, batch.snapshot_id, as_of=now)
                    if snapshot:
                        snapshot = store.with_turnover(snapshot)
                        schedule = SessionSchedule(
                            session_date=batch.open_at.astimezone(NEW_YORK).date(),
                            open_at=batch.open_at,
                            close_at=batch.close_at,
                            is_half_day=(batch.close_at - batch.open_at).total_seconds()
                            < 23400,
                            rule_version="option-pulse-session-v1",
                        )
                        iv = build_option_analytics(snapshot, as_of=now).values.get(
                            "atm_iv_30d"
                        )
                        anchor = snapshot.underlying_at
                        fresh = (
                            anchor is not None
                            and 0 <= (now - anchor).total_seconds() <= 120
                            and (now - batch.captured_at).total_seconds() <= 120
                            and bool(batch.bars)
                            and (now - batch.bars[-1].at).total_seconds() <= 420
                        )
                        cone = (
                            intraday_cone(
                                snapshot.underlying_price, iv, anchor, schedule
                            )
                            if fresh and anchor is not None
                            else ()
                        )
                        response = pulse_from_snapshot(
                            snapshot, now, mode="live", bars=batch.bars, cone=cone
                        )
                        response = response.model_copy(
                            update={
                                "stock_batch_id": batch.snapshot_id,
                                "close_at": batch.close_at,
                            }
                        )
                        if active and reason == "active" and not fresh:
                            reason = "delayed"
                return response.model_copy(
                    update={
                        "collection_state": reason,
                        "last_attempt_at": datetime.fromisoformat(
                            state["last_attempt_at"]
                        )
                        if state.get("last_attempt_at")
                        else None,
                        "worker_heartbeat_at": heartbeat,
                        "reasons": tuple(
                            dict.fromkeys(
                                (
                                    *response.reasons,
                                    *(
                                        (reason,)
                                        if reason not in ("active", "inactive")
                                        else ()
                                    ),
                                )
                            )
                        ),
                    }
                )
            cutoff = min(as_of, now) if as_of else now
            stock = None
            bars: tuple[OptionPulseBar, ...] = ()
            if repository is not None:
                try:
                    stock = repository.resolve_snapshot(
                        SnapshotQuery(snapshot_id=snapshot_id, as_of=as_of)
                    )
                    if mode != "latest":
                        cutoff = min(cutoff, stock.as_of)
                except SnapshotNotFound:
                    if snapshot_id:
                        fail("stock_snapshot_not_found", 404, symbol)
            snapshot = store.load(symbol, option_snapshot_id, as_of=cutoff)
            if snapshot is None:
                if option_snapshot_id:
                    fail("option_snapshot_not_found", 404, symbol)
                attempt = store.latest_attempt(symbol, as_of=cutoff)
                return empty.model_copy(
                    update={
                        "reasons": ("no_option_snapshot",)
                        + (
                            (attempt.errors or ("incomplete_snapshots",))
                            if attempt
                            else ()
                        )
                    }
                )
            snapshot = store.with_turnover(snapshot)
            market_at = snapshot.market_as_of or snapshot.underlying_at
            history_reason = None
            if stock is not None and market_at is not None and repository is not None:
                try:
                    historical = repository.history(
                        stock, symbol, HistoryQuery(timeframe="1d", adjustment="raw")
                    )
                    market_day = market_at.astimezone(NEW_YORK).date()
                    bars = tuple(
                        OptionPulseBar(
                            at=b.at,
                            open=b.open,
                            close=b.close,
                            low=b.low,
                            high=b.high,
                            data_gap=b.data_gap,
                        )
                        for b in historical.bars
                        if b.at <= cutoff and b.at.date() <= market_day
                    )[-30:]
                except (OSError, ValueError, RuntimeError):
                    history_reason = "stock_history_unavailable"
            response = pulse_from_snapshot(snapshot, cutoff, mode="daily", bars=bars)
            if history_reason:
                response = response.model_copy(
                    update={
                        "data_status": "partial",
                        "reasons": (*response.reasons, history_reason),
                    }
                )
            analytics = build_option_analytics(snapshot, as_of=cutoff)
            daily_cone: list[OptionPulseConePoint] = []
            if market_at and snapshot.underlying_price:
                spot = snapshot.underlying_price
                daily_cone.append(
                    OptionPulseConePoint(
                        at=market_at,
                        lower68=spot,
                        upper68=spot,
                        lower80=spot,
                        upper80=spot,
                    )
                )
                for move in analytics.implied_moves[:10]:
                    if (
                        move.sigma_pct is not None
                        and move.lower_price is not None
                        and move.upper_price is not None
                    ):
                        daily_cone.append(
                            OptionPulseConePoint(
                                at=market_at,
                                horizon_days=move.horizon_days,
                                lower68=spot * (1 - move.sigma_pct),
                                upper68=spot * (1 + move.sigma_pct),
                                lower80=move.lower_price,
                                upper80=move.upper_price,
                            )
                        )
            return response.model_copy(
                update={
                    "generated_at": now,
                    "mode": mode,
                    "stock_batch_id": stock.snapshot_id if stock else None,
                    "cone": tuple(daily_cone) if len(daily_cone) > 1 else (),
                    "formula_version": "atm-linear-calendar-h365over252-sigma-h252-z1.2816-v1",
                }
            )
        except ApiError:
            raise
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            return empty.model_copy(update={"reasons": ("pulse_data_invalid",)})

    return router
