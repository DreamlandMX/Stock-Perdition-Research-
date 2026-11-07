"""Foreground leases and a scheduler-owned, single-writer option pulse."""

from collections.abc import Callable, Sequence
from contextlib import nullcontext
from datetime import UTC, date, datetime, time, timedelta
import json
from math import sqrt
from pathlib import Path
import re
import time as clock_time
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from filelock import FileLock, Timeout

from stock_prediction.contracts.options import (
    OPTION_SYMBOLS,
    OptionPulseBar,
    OptionPulseBatch,
    OptionPulseConePoint,
    OptionPulseLease,
    OptionPulseProfile,
    OptionPulseResponse,
    OptionChainSnapshot,
)
from stock_prediction.data.sessions import (
    NotTradingDayError,
    SessionSchedule,
    UsSessionRules,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.locking import LiveWriterLease, _atomic_json
from stock_prediction.opend.errors import classify_sdk_exception
from stock_prediction.opend.futu_adapter import FutuQuoteContext
from stock_prediction.options.analytics import build_option_analytics
from stock_prediction.options.collector import run_option_collection
from stock_prediction.options.store import OptionSnapshotStore
from stock_prediction.options.structure_map import build_option_structure_map

NEW_YORK = ZoneInfo("America/New_York")


def verified_schedule(day: str, rows: Sequence[dict[str, Any]]) -> SessionSchedule:
    kinds: dict[date, str] = {}
    for row in rows:
        session, kind = date.fromisoformat(row["time"]), row["trade_date_type"]
        if kind not in ("WHOLE", "MORNING", "AFTERNOON"):
            raise ValueError("invalid session kind")
        if session in kinds and kinds[session] != kind:
            raise ValueError("conflicting calendar")
        kinds[session] = kind
    rules = UsSessionRules(
        version="option-pulse-session-v1",
        half_day_closes={d: time(13) for d, k in kinds.items() if k != "WHOLE"},
    )
    return rules.schedule_for(date.fromisoformat(day), kinds)


def normalize_bars(
    rows: Sequence[dict[str, Any]], now: datetime, schedule: SessionSchedule
) -> tuple[OptionPulseBar, ...]:
    bars: dict[datetime, OptionPulseBar] = {}
    for row in rows:
        at = datetime.fromisoformat(row["time_key"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=NEW_YORK)
        # OpenD labels current bars by interval end, including the forming bar.
        at = at.astimezone(UTC) - timedelta(minutes=5)
        if not schedule.open_at <= at < schedule.close_at or at > now:
            continue
        bar = OptionPulseBar(
            at=at,
            open=row["open"],
            close=row["close"],
            low=row["low"],
            high=row["high"],
            forming=at + timedelta(minutes=5) > now,
        )
        if (
            bar.low > min(bar.open, bar.close)
            or bar.high < max(bar.open, bar.close)
            or at in bars
        ):
            raise ValueError("invalid or duplicate pulse bar")
        bars[at] = bar
    return tuple(bars[at] for at in sorted(bars))


def intraday_cone(
    spot: float | None, iv: float | None, at: datetime, schedule: SessionSchedule
) -> tuple[OptionPulseConePoint, ...]:
    if (
        spot is None
        or iv is None
        or iv <= 0
        or at < schedule.open_at
        or at >= schedule.close_at
    ):
        return ()
    points = []
    cursor = at
    while True:
        sigma = iv * sqrt((cursor - at).total_seconds() / 60 / 390 / 252)
        points.append(
            OptionPulseConePoint(
                at=cursor,
                lower68=spot * (1 - sigma),
                upper68=spot * (1 + sigma),
                lower80=spot * (1 - 1.2816 * sigma),
                upper80=spot * (1 + 1.2816 * sigma),
            )
        )
        if cursor == schedule.close_at:
            break
        cursor = min(cursor + timedelta(minutes=5), schedule.close_at)
    return tuple(points)


class OptionPulseStore:
    def __init__(self, root: Path):
        self.root = root

    def _lease_path(self, symbol: str, session_id: str) -> Path:
        if symbol not in OPTION_SYMBOLS or not re.fullmatch(
            r"[a-f0-9]{32}", session_id
        ):
            raise ValueError("invalid pulse lease")
        return self.root / "leases" / f"{session_id}.json"

    def acquire(self, symbol: str, now: datetime) -> OptionPulseLease:
        lease = OptionPulseLease(
            session_id=uuid4().hex,
            symbol=symbol,
            expires_at=now + timedelta(seconds=90),
        )
        path = self._lease_path(symbol, lease.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(path, lease.model_dump(mode="json"))
        return lease

    def renew(
        self, symbol: str, session_id: str, now: datetime
    ) -> OptionPulseLease | None:
        path = self._lease_path(symbol, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(path.with_suffix(".lock")), timeout=2):
            if not path.exists():
                return None
            lease = OptionPulseLease.model_validate_json(path.read_bytes())
            if lease.symbol != symbol or lease.expires_at <= now:
                return None
            lease = lease.model_copy(update={"expires_at": now + timedelta(seconds=90)})
            _atomic_json(path, lease.model_dump(mode="json"))
            return lease

    def release(self, symbol: str, session_id: str) -> None:
        path = self._lease_path(symbol, session_id)
        if not path.parent.exists():
            return
        with FileLock(str(path.with_suffix(".lock")), timeout=2):
            if (
                path.exists()
                and OptionPulseLease.model_validate_json(path.read_bytes()).symbol
                == symbol
            ):
                path.unlink()

    def active_symbols(self, now: datetime) -> tuple[str, ...]:
        active = set()
        for path in (self.root / "leases").glob("*.json"):
            try:
                lease = OptionPulseLease.model_validate_json(path.read_bytes())
                if lease.symbol in OPTION_SYMBOLS and lease.expires_at > now:
                    active.add(lease.symbol)
            except (OSError, ValueError):
                continue
        return tuple(sorted(active))

    def state(self, symbol: str) -> dict[str, Any]:
        path = self.root / symbol / "state.json"
        try:
            return json.loads(path.read_bytes()) if path.exists() else {}
        except (OSError, ValueError):
            return {"reason": "pulse_state_unreadable"}

    def heartbeat(self, now: datetime) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.root / "worker.json", {"heartbeat_at": now.isoformat()})

    def save_state(self, symbol: str, **state: Any) -> None:
        directory = self.root / symbol
        directory.mkdir(parents=True, exist_ok=True)
        _atomic_json(directory / "state.json", state)

    def save_batch(self, symbol: str, batch: OptionPulseBatch) -> None:
        directory = self.root / symbol
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{batch.snapshot_id}.json"
        if target.exists():
            raise ValueError("immutable pulse batch conflict")
        _atomic_json(target, batch.model_dump(mode="json"))
        _atomic_json(directory / "latest.json", {"snapshot_id": batch.snapshot_id})

    def latest_batch(self, symbol: str) -> OptionPulseBatch | None:
        pointer = self.root / symbol / "latest.json"
        if not pointer.exists():
            return None
        identity = json.loads(pointer.read_bytes())["snapshot_id"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", identity):
            raise ValueError("invalid pulse pointer")
        return OptionPulseBatch.model_validate_json(
            (pointer.parent / f"{identity}.json").read_bytes()
        )


def pulse_from_snapshot(
    snapshot: OptionChainSnapshot,
    now: datetime,
    *,
    mode: Literal["live", "daily"],
    bars: tuple[OptionPulseBar, ...] = (),
    cone: tuple[OptionPulseConePoint, ...] = (),
) -> OptionPulseResponse:
    analytics = build_option_analytics(snapshot, as_of=now)
    structure = build_option_structure_map(
        snapshot,
        dte_min=snapshot.dte_min,
        dte_max=snapshot.dte_max,
        strike_min_ratio=snapshot.strike_min_ratio,
        strike_max_ratio=snapshot.strike_max_ratio,
    )
    profile = tuple(
        OptionPulseProfile(
            strike=float(strike), call=values.call.turnover, put=values.put.turnover
        )
        for strike, values in sorted(
            structure.strike_summary.items(),
            key=lambda item: float(item[0]),
            reverse=True,
        )
    )
    peaks = [
        max(
            (
                p
                for p in profile
                if getattr(p, side) is not None and getattr(p, side) > 0
            ),
            key=lambda p: getattr(p, side),
            default=None,
        )
        for side in ("call", "put")
    ]
    coverage = structure.turnover_coverage
    observed = [r.observed_at for r in snapshot.contracts if r.observed_at is not None]
    totals = [
        v
        for v in (structure.total.call.turnover, structure.total.put.turnover)
        if v is not None
    ]
    return OptionPulseResponse(
        symbol=snapshot.symbol,
        mode=mode,
        timeframe="5m" if mode == "live" else "1d",
        generated_at=now,
        data_status="available"
        if snapshot.complete and coverage.missing == 0 and coverage.excluded == 0
        else "partial",
        snapshot_id=snapshot.snapshot_id,
        captured_at=snapshot.captured_at,
        market_at=snapshot.underlying_at,
        contract_market_min_at=min(observed, default=None),
        contract_market_max_at=max(observed, default=None),
        spot=snapshot.underlying_price,
        iv30=analytics.values.get("atm_iv_30d"),
        put_call_volume=analytics.values.get("put_call_volume"),
        total_turnover=sum(totals) if totals else None,
        call_peak=peaks[0].strike if peaks[0] else None,
        put_peak=peaks[1].strike if peaks[1] else None,
        expected_contracts=coverage.expected,
        valid_turnover_contracts=coverage.valid,
        missing_turnover_contracts=coverage.missing,
        excluded_turnover_contracts=coverage.excluded,
        complete=snapshot.complete,
        dte_min=snapshot.dte_min,
        dte_max=snapshot.dte_max,
        strike_min_ratio=snapshot.strike_min_ratio,
        strike_max_ratio=snapshot.strike_max_ratio,
        bars=bars,
        profile=profile,
        cone=cone,
        reasons=tuple(dict.fromkeys((*snapshot.errors, *structure.reasons))),
    )


def _pulse_context() -> FutuQuoteContext:
    return FutuQuoteContext(connect_timeout_seconds=5)


def run_intraday_tick(
    paths: LivePaths,
    *,
    context_factory: Callable[[], Any] = _pulse_context,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    live_root = paths.root.resolve()
    workspace = (
        live_root.parents[2]
        if live_root.parent.name == "live-datasets"
        else live_root.parents[1]
    )
    options = OptionSnapshotStore(workspace / "var" / "options")
    pulse = OptionPulseStore(options.root / "_pulse")
    pulse.root.mkdir(parents=True, exist_ok=True)
    pulse.heartbeat(clock())
    try:
        with FileLock(str(pulse.root / ".worker.lock"), timeout=0):
            for symbol in pulse.active_symbols(clock()):
                now = clock()
                last = pulse.state(symbol).get("last_attempt_at")
                if last and (now - datetime.fromisoformat(last)).total_seconds() < 60:
                    continue
                pulse.save_state(
                    symbol, last_attempt_at=now.isoformat(), reason="collecting"
                )
                deadline = clock_time.monotonic() + 55

                def budget() -> float:
                    remaining = deadline - clock_time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("collection_timeout")
                    return remaining

                try:
                    with LiveWriterLease(paths, task_id="option-pulse", timeout=0):
                        with context_factory() as context:
                            budget()
                            day = now.astimezone(NEW_YORK).date().isoformat()
                            calendar = context.request_trading_days(
                                market="US", start=day, end=day, code=None
                            )
                            budget()
                            if not calendar.is_ok:
                                raise RuntimeError(calendar.error.error_class.value)
                            schedule = verified_schedule(day, calendar.value)
                            if not schedule.open_at <= now < schedule.close_at:
                                pulse.save_state(
                                    symbol,
                                    last_attempt_at=now.isoformat(),
                                    reason="market_closed",
                                )
                                continue
                            kline = context.get_current_5m(symbol)
                            budget()
                            if not kline.is_ok:
                                raise RuntimeError(kline.error.error_class.value)
                            bars = normalize_bars(
                                kline.value.to_dict(orient="records"), clock(), schedule
                            )
                            if not bars:
                                raise RuntimeError("intraday_bars_unavailable")
                            run = run_option_collection(
                                options,
                                symbols=(symbol,),
                                context_factory=lambda: nullcontext(context),
                                clock=clock,
                                timeout_seconds=min(50, budget()),
                                intraday=True,
                            )
                            if run.status != "completed":
                                raise RuntimeError(run.reason or "incomplete_snapshots")
                            snapshot = options.load(symbol)
                            if (
                                snapshot is None
                                or snapshot.captured_at < now
                                or not snapshot.complete
                            ):
                                raise RuntimeError("incomplete_snapshots")
                            kline = context.get_current_5m(symbol)
                            budget()
                            if not kline.is_ok:
                                raise RuntimeError(kline.error.error_class.value)
                            bars = normalize_bars(
                                kline.value.to_dict(orient="records"), clock(), schedule
                            )
                            pulse.save_batch(
                                symbol,
                                OptionPulseBatch(
                                    snapshot_id=snapshot.snapshot_id,
                                    captured_at=clock(),
                                    open_at=schedule.open_at,
                                    close_at=schedule.close_at,
                                    bars=bars,
                                ),
                            )
                            pulse.save_state(
                                symbol,
                                last_attempt_at=now.isoformat(),
                                reason="active",
                                duration_seconds=(clock() - now).total_seconds(),
                            )
                except Timeout:
                    pulse.save_state(
                        symbol, last_attempt_at=now.isoformat(), reason="opend_busy"
                    )
                except TimeoutError:
                    pulse.save_state(
                        symbol,
                        last_attempt_at=now.isoformat(),
                        reason="collection_timeout",
                    )
                except NotTradingDayError:
                    pulse.save_state(
                        symbol,
                        last_attempt_at=now.isoformat(),
                        reason="not_trading_day",
                    )
                except ValueError:
                    pulse.save_state(
                        symbol,
                        last_attempt_at=now.isoformat(),
                        reason="calendar_or_data_invalid",
                    )
                except Exception as error:
                    reason = (
                        str(error)
                        if isinstance(error, RuntimeError)
                        else classify_sdk_exception(error).error_class.value
                    )
                    pulse.save_state(
                        symbol, last_attempt_at=now.isoformat(), reason=reason
                    )
    except Timeout:
        return
