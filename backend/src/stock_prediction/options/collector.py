"""Explicit, paced read-only collection; never invoked by an HTTP request."""

import argparse
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from importlib.metadata import version
import json
from math import isfinite
from pathlib import Path
import time
from uuid import uuid4
from zoneinfo import ZoneInfo
from typing import Any, Literal

from filelock import FileLock, Timeout
import pandas as pd

from stock_prediction.contracts.options import (
    OPTION_SYMBOLS,
    OptionChainSnapshot,
    OptionContract,
)
from stock_prediction.ingestion.scheduler import EndpointRateLimiter
from stock_prediction.opend.futu_adapter import FutuQuoteContext
from stock_prediction.opend.ports import SdkCallResult
from stock_prediction.opend.errors import classify_sdk_exception
from stock_prediction.options.store import OptionSnapshotStore
from stock_prediction.options.monitor import (
    OptionCollectionRun,
    save_option_collection_run,
)


def expiration_windows(
    today: date, minimum: int, maximum: int
) -> list[tuple[str, str]]:
    if not 1 <= minimum <= maximum <= 90:
        raise ValueError("invalid expiry range")
    return [
        (
            (today + timedelta(days=start)).isoformat(),
            (today + timedelta(days=min(start + 29, maximum))).isoformat(),
        )
        for start in range(minimum, maximum + 1, 30)
    ]


def _number(
    value: Any, *, positive: bool = False, signed: bool = False
) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return (
        number
        if isfinite(number) and (signed or number >= 0) and (not positive or number > 0)
        else None
    )


def _timestamp(value: Any, now: datetime) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            zone = ZoneInfo("America/New_York")
            first, second = (
                parsed.replace(tzinfo=zone, fold=0),
                parsed.replace(tzinfo=zone, fold=1),
            )
            if first.utcoffset() != second.utcoffset():
                return None
            parsed = first
        parsed = parsed.astimezone(UTC)
        return parsed if parsed <= now else None
    except ValueError:
        return None


def normalize_contract(
    chain: dict[str, Any], quote: dict[str, Any], *, symbol: str, now: datetime
) -> OptionContract:
    if chain.get("stock_owner") != symbol or chain.get("option_type") not in (
        "CALL",
        "PUT",
    ):
        raise ValueError("option identity mismatch")
    flags = [
        "oi_date_unproven",
        "greeks_timestamp_unproven",
        "quote_timestamp_unproven",
    ]
    valid = quote.get("option_valid") is True
    if not valid:
        flags.append("option_fields_unavailable")
    observed = _timestamp(quote.get("update_time"), now)
    if observed is None:
        flags.append("observed_time_missing_or_invalid")
    standard_type = chain.get("option_standard_type")
    standard = (
        True
        if standard_type == "STANDARD"
        else False
        if standard_type in ("NON_STANDARD", "NONSTANDARD")
        else None
    )
    if standard is not True:
        flags.append("nonstandard_or_unknown_delivery")
    bid, ask = _number(quote.get("bid_price")), _number(quote.get("ask_price"))
    if bid is not None and ask is not None and bid > ask:
        flags.append("crossed_quote")
        bid = ask = None
    if bid == 0 or bid is None or ask is None:
        flags.append("zero_or_one_sided_quote")
    iv = (
        _number(quote.get("option_implied_volatility"), positive=True)
        if valid
        else None
    )
    # Futu market-snapshot protobuf defines 20 as 20%; other endpoints differ.
    iv = iv / 100 if iv is not None else None
    multiplier = (
        _number(quote.get("option_contract_size"), positive=True) if valid else None
    ) or _number(chain.get("lot_size"), positive=True)
    return OptionContract(
        contract_id=chain["code"],
        underlying=symbol,
        side=chain["option_type"].lower(),
        strike=chain["strike_price"],
        expiration=chain["strike_time"],
        multiplier=multiplier,
        standard=standard,
        settlement=chain.get("option_settlement_mode")
        if chain.get("option_settlement_mode") != "N/A"
        else None,
        bid=bid,
        ask=ask,
        volume=_number(quote.get("volume")),
        turnover=_number(quote.get("turnover")),
        session_low=_number(quote.get("low_price")),
        session_high=_number(quote.get("high_price")),
        open_interest=_number(quote.get("option_open_interest")) if valid else None,
        iv=iv,
        delta=_number(quote.get("option_delta"), signed=True) if valid else None,
        gamma=_number(quote.get("option_gamma")) if valid else None,
        theta=_number(quote.get("option_theta"), signed=True) if valid else None,
        vega=_number(quote.get("option_vega"), signed=True) if valid else None,
        observed_at=observed,
        source_available_at=now,
        ingested_at=now,
        quality_flags=tuple(flags),
    )


class OptionCollector:
    def __init__(
        self,
        store: OptionSnapshotStore,
        *,
        context_factory: Callable[[], Any] = FutuQuoteContext,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleeper: Callable[[float], None] = time.sleep,
        timeout_seconds: float | None = None,
        intraday: bool = False,
        limiter: EndpointRateLimiter | None = None,
    ) -> None:
        self.store, self.context_factory, self.clock, self.sleeper = (
            store,
            context_factory,
            clock,
            sleeper,
        )
        self.limiter = limiter or EndpointRateLimiter(
            {"chain": (10, 30), "snapshot": (60, 30), "expiration": (10, 30)}
        )
        self._results: list[OptionChainSnapshot] = []
        self.intraday = intraday
        self.deadline = (
            None if timeout_seconds is None else time.monotonic() + timeout_seconds
        )

    def _call(
        self,
        endpoint: str,
        method: Callable[..., SdkCallResult[Any]],
        *args: Any,
        **kwargs: Any,
    ) -> SdkCallResult[Any]:
        for attempt in range(3):
            self._check_deadline()
            while not self.limiter.allow(endpoint, now=time.monotonic()):
                self._check_deadline()
                self.sleeper(0.5)
            result = method(*args, **kwargs)
            self._check_deadline()
            if result.is_ok or result.error is None or not result.error.retryable:
                return result
            if attempt < 2:
                delay = (
                    30
                    if result.error.error_class.value == "rate_limited"
                    else 2**attempt
                )
                self.sleeper(
                    delay
                    if self.deadline is None
                    else min(delay, max(0, self.deadline - time.monotonic()))
                )
        return result

    def _check_deadline(self) -> None:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError("collection_timeout")

    def collect(
        self, symbols: Sequence[str] = OPTION_SYMBOLS, *, sample: bool = False
    ) -> tuple[OptionChainSnapshot, ...]:
        if not symbols or any(s not in OPTION_SYMBOLS for s in symbols):
            raise ValueError("unsupported option symbols")
        self.store.root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.store.root / ".collector.lock"), timeout=0):
            self._results = []
            results = self._results
            try:
                opened_context = self.context_factory()
            except Exception as exception:
                error = classify_sdk_exception(exception)
                for symbol in symbols:
                    failed = self._failed(symbol, error.error_class.value)
                    self.store.save(failed)
                    results.append(failed)
                return tuple(results)
            with opened_context as context:
                global_state = context.get_global_state()
                server = (
                    str(global_state.value.get("server_ver", "unknown"))
                    if global_state.value
                    else "unknown"
                )
                for symbol in symbols:
                    try:
                        result = self._collect_symbol(context, symbol, server, sample)
                    except (ValueError, KeyError, TypeError):
                        result = self._failed(symbol, "partial_response")
                        self.store.save(result)
                    results.append(result)
                    print(
                        f"{symbol}: contracts={len(result.contracts)} complete={result.complete} errors={','.join(result.errors) or 'none'}",
                        flush=True,
                    )
        return tuple(results)

    def _failed(self, symbol: str, error: str) -> OptionChainSnapshot:
        now = self.clock()
        identity = "option-" + uuid4().hex
        return OptionChainSnapshot(
            snapshot_id=identity,
            raw_batch_id=identity,
            symbol=symbol,
            captured_at=now,
            source_available_at=now,
            sdk_version=version("futu-api"),
            errors=(error,),
        )

    def _collect_symbol(
        self, context: Any, symbol: str, server: str, sample: bool
    ) -> OptionChainSnapshot:
        started = self.clock()
        today = started.astimezone(ZoneInfo("America/New_York")).date()
        raw: dict[str, object] = {}
        errors: list[str] = []
        chain: dict[str, dict[str, Any]] = {}
        discovery = True
        expirations = self._call(
            "expiration", context.get_option_expiration_date, symbol
        )
        if not expirations.is_ok:
            assert expirations.error is not None
            snapshot = self._failed(symbol, expirations.error.error_class.value)
            self.store.save(snapshot)
            return snapshot
        raw["expirations"] = self._rows(expirations.value)
        assert expirations.value is not None
        if "strike_time" not in expirations.value.columns:
            failed = self._failed(symbol, "invalid_expiration_schema")
            self.store.save(failed, raw)
            return failed
        expected_expirations = {
            date.fromisoformat(row["strike_time"])
            for row in self._rows(expirations.value)
        }
        price_result = self._call("snapshot", context.get_market_snapshot, [symbol])
        prices = self._rows(price_result.value) if price_result.is_ok else []
        raw["underlying"] = prices
        underlying = next((r for r in prices if r.get("code") == symbol), {})
        price = _number(underlying.get("last_price"), positive=True)
        price_at = _timestamp(underlying.get("update_time"), self.clock())
        if price is None:
            errors.append("underlying_price_unavailable")
        for start, end in expiration_windows(today, 1, 90):
            response = self._call(
                "chain", context.get_option_chain, symbol, start=start, end=end
            )
            if not response.is_ok:
                assert response.error is not None
                discovery = False
                errors.append(response.error.error_class.value)
                if not response.error.retryable:
                    break
                continue
            rows = self._rows(response.value)
            assert response.value is not None
            raw[f"chain-{start}-{end}"] = rows
            required_columns = {
                "code",
                "stock_owner",
                "option_type",
                "strike_price",
                "strike_time",
            }
            window_expirations = {
                day
                for day in expected_expirations
                if date.fromisoformat(start) <= day <= date.fromisoformat(end)
            }
            returned_expirations = {
                date.fromisoformat(row["strike_time"])
                for row in rows
                if isinstance(row.get("strike_time"), str)
            }
            if not required_columns.issubset(
                response.value.columns
            ) or not window_expirations.issubset(returned_expirations):
                discovery = False
                errors.append("incomplete_expiration_discovery")
                continue
            for row in rows:
                try:
                    expiration = date.fromisoformat(row["strike_time"])
                    if (
                        not date.fromisoformat(start)
                        <= expiration
                        <= date.fromisoformat(end)
                    ):
                        raise ValueError("expiry outside requested window")
                    strike = _number(row.get("strike_price"), positive=True)
                    if (
                        strike is None
                        or row.get("stock_owner") != symbol
                        or not isinstance(row.get("code"), str)
                    ):
                        raise ValueError("invalid chain identity")
                    if price is not None and not 0.7 <= strike / price <= 1.3:
                        continue
                    if row["code"] in chain and chain[row["code"]] != row:
                        raise ValueError("conflicting chain identity")
                    chain[row["code"]] = row
                except (ValueError, KeyError, TypeError):
                    discovery = False
                    errors.append("invalid_chain_identity")
        quotes = {}
        codes = sorted(chain)
        if discovery and not codes:
            errors.append("no_option_contracts")
        requested = codes
        if sample:
            sampled = [
                min(
                    (c for c in codes if chain[c]["option_type"] == side),
                    key=lambda c: (
                        chain[c]["strike_time"],
                        abs(float(chain[c]["strike_price"]) - (price or 0)),
                    ),
                    default=None,
                )
                for side in ("CALL", "PUT")
            ]
            requested = [c for c in sampled if c is not None]
            errors.append("sample_only_not_full_chain")
        for offset in range(0, len(requested), 400):
            response = self._call(
                "snapshot",
                context.get_market_snapshot,
                requested[offset : offset + 400],
            )
            if response.is_ok:
                rows = self._rows(response.value)
                raw[f"quotes-{offset}"] = rows
                for row in rows:
                    code = row.get("code")
                    if code not in requested[offset : offset + 400] or code in quotes:
                        errors.append("unexpected_or_duplicate_quote")
                        continue
                    quotes[code] = row
            else:
                assert response.error is not None
                errors.append(response.error.error_class.value)
                if not response.error.retryable:
                    break
        records: list[OptionContract] = []
        for code in codes:
            try:
                normalized = normalize_contract(
                    chain[code], quotes.get(code, {}), symbol=symbol, now=self.clock()
                )
                records.append(normalized)
            except (ValueError, KeyError, TypeError):
                errors.append("invalid_contract_record")
        ended = self.clock()
        identity = "option-" + uuid4().hex
        market_times = [
            at
            for at in (price_at, *(row.observed_at for row in records))
            if at is not None
        ]
        snapshot = OptionChainSnapshot(
            snapshot_id=identity,
            raw_batch_id=identity,
            symbol=symbol,
            sdk_version=version("futu-api"),
            opend_version=server,
            captured_at=ended,
            source_available_at=ended,
            underlying_price=price,
            underlying_at=price_at,
            market_as_of=max(market_times, default=None) if self.intraday else None,
            expected_contract_ids=tuple(codes),
            contracts=tuple(records),
            discovery_complete=discovery,
            complete=discovery
            and not sample
            and not errors
            and len(quotes) == len(codes),
            errors=tuple(dict.fromkeys(errors)),
        )
        self.store.save(snapshot, raw)
        return snapshot

    @staticmethod
    def _rows(frame: Any) -> list[dict[str, Any]]:
        if not isinstance(frame, pd.DataFrame):
            raise ValueError("option response is not a dataframe")
        rows: list[dict[str, Any]] = json.loads(
            frame.to_json(orient="records", date_format="iso")
        )
        return rows


def run_option_collection(
    store: OptionSnapshotStore,
    *,
    symbols: Sequence[str] = OPTION_SYMBOLS,
    sample: bool = False,
    context_factory: Callable[[], Any] = FutuQuoteContext,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    scheduled_at: datetime | None = None,
    is_half_day: bool | None = None,
    started_at: datetime | None = None,
    timeout_seconds: float | None = None,
    intraday: bool = False,
    refresh_monitor: bool = True,
    limiter: EndpointRateLimiter | None = None,
) -> OptionCollectionRun:
    if not symbols or any(symbol not in OPTION_SYMBOLS for symbol in symbols):
        raise ValueError("unsupported option symbols")
    started = started_at or clock()
    if started.tzinfo is None or started.utcoffset() is None:
        raise ValueError("option collection clock requires timezone")
    status: Literal["completed", "partial", "failed", "skipped"] = "failed"
    reason = None
    snapshots: tuple[OptionChainSnapshot, ...] = ()
    collector = OptionCollector(
        store,
        context_factory=context_factory,
        clock=clock,
        timeout_seconds=timeout_seconds,
        intraday=intraday,
        limiter=limiter,
    )
    try:
        snapshots = collector.collect(symbols, sample=sample)
    except Timeout as error:
        status = "skipped"
        reason = (
            "collector_busy"
            if error.lock_file == str(store.root / ".collector.lock")
            else "snapshot_writer_busy"
        )
    except TimeoutError:
        reason = "collection_timeout"
    except Exception as error:
        reason = classify_sdk_exception(error).error_class.value
    snapshots = snapshots or tuple(collector._results)
    if reason is not None and status != "skipped":
        attempted = {snapshot.symbol for snapshot in snapshots}
        for symbol in symbols:
            if symbol not in attempted:
                failed = collector._failed(symbol, reason)
                store.save(failed)
                snapshots = (*snapshots, failed)
    if snapshots:
        status = (
            "completed"
            if len(snapshots) == len(symbols) and all(s.complete for s in snapshots)
            else "partial"
            if any(s.complete or s.contracts for s in snapshots)
            else "failed"
        )
        if status == "completed" and reason is not None:
            status = "partial"
        if status != "completed" and reason is None:
            reason = "incomplete_snapshots"
    run = OptionCollectionRun(
        scheduled_at=scheduled_at,
        started_at=started,
        ended_at=clock(),
        trading_date=started.astimezone(ZoneInfo("America/New_York")).date(),
        is_half_day=is_half_day,
        automatic=scheduled_at is not None,
        status=status,
        reason=reason,
        contracts={symbol: 0 for symbol in symbols}
        | {s.symbol: len(s.contracts) for s in snapshots},
        complete=dict.fromkeys(symbols, False)
        | {s.symbol: s.complete for s in snapshots},
    )
    save_option_collection_run(run, store, refresh_monitor=refresh_monitor)
    return run


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect read-only, forward option snapshots"
    )
    parser.add_argument("--root", type=Path, default=Path("var/options"))
    parser.add_argument(
        "--symbols", nargs="+", choices=OPTION_SYMBOLS, default=list(OPTION_SYMBOLS)
    )
    parser.add_argument("--sample", action="store_true")
    args = parser.parse_args()
    store = OptionSnapshotStore(args.root)
    run = run_option_collection(
        store,
        symbols=args.symbols,
        sample=args.sample,
    )
    return 0 if run.status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
