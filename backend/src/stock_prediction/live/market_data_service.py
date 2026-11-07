"""Provider-neutral staged live history collection."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from stock_prediction.alpaca.provider import AlpacaMarketDataProvider, AlpacaSipProvider
from stock_prediction.contracts.market_data import (
    MarketBar,
    MarketSession,
    Timeframe,
)
from stock_prediction.live.config import (
    ALPACA_SIP_DATASET_REVISION,
    ALPACA_IEX_YAHOO_DATASET_REVISION,
    LIVE_SCHEMA_VERSION,
    LivePaths,
)
from stock_prediction.live.storage import LiveStorage, RawPagePart, task_id_for
from stock_prediction.market_data.ports import (
    MarketDataProvider,
    ProviderBar,
    ProviderPreflightResult,
)
from stock_prediction.live.universe_scope import resolve_live_universe
from stock_prediction.yahoo.volume import YahooDailyVolumeProvider


class LiveMarketDataService:
    """Collect one versioned universe without touching another dataset root."""

    def __init__(
        self,
        paths: LivePaths,
        *,
        provider: MarketDataProvider | None = None,
        symbols: Sequence[str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        volume_provider: YahooDailyVolumeProvider | None = None,
    ) -> None:
        if paths.dataset_revision not in {
            ALPACA_SIP_DATASET_REVISION,
            ALPACA_IEX_YAHOO_DATASET_REVISION,
        }:
            raise ValueError("alpaca_staged_dataset_required")
        self.paths = paths
        self.paths.ensure()
        self.provider = provider or (
            AlpacaMarketDataProvider(feed="iex")
            if paths.dataset_revision == ALPACA_IEX_YAHOO_DATASET_REVISION
            else AlpacaSipProvider()
        )
        self.volume_provider = volume_provider
        if (
            paths.dataset_revision == ALPACA_IEX_YAHOO_DATASET_REVISION
            and self.volume_provider is None
        ):
            self.volume_provider = YahooDailyVolumeProvider()
        self.symbols = tuple(
            dict.fromkeys(symbols or resolve_live_universe(paths).symbols)
        )
        self.clock = clock
        self._storage: LiveStorage | None = None

    @property
    def storage(self) -> LiveStorage:
        if self._storage is None:
            self._storage = LiveStorage(self.paths)
        return self._storage

    def preflight(self) -> ProviderPreflightResult:
        result = self.provider.preflight(
            workspace_root=self.paths.root,
            symbols=self.symbols,
        )
        if self.volume_provider is None or not result.sessions:
            return result
        blockers = list(result.blockers)
        try:
            probe_end = result.end
            observations = self.volume_provider.read_daily_volumes(
                symbols=(self.symbols[0],),
                start=probe_end - timedelta(days=7),
                end=probe_end,
            )
            if not observations:
                blockers.append("yahoo_volume_unavailable")
        except Exception:
            blockers.append("yahoo_volume_unavailable")
        return replace(result, ready=not blockers, blockers=tuple(blockers))

    def bootstrap(
        self,
        *,
        daily_start: date = date(2018, 1, 1),
        intraday_start: date = date(2024, 8, 10),
    ) -> dict[str, object]:
        preflight = self.preflight()
        if not preflight.ready:
            raise RuntimeError("live_preflight_blocked")
        sessions = {item.session_date: item for item in preflight.sessions}
        if not sessions:
            raise RuntimeError("alpaca_calendar_unavailable")
        end = max(sessions)
        self.storage.record_calendar(
            sessions=tuple(
                (
                    item.session_date,
                    "HALF"
                    if item.closes_at - item.opens_at < timedelta(hours=6)
                    else "WHOLE",
                )
                for item in sessions.values()
            ),
            source_hash="alpaca-calendar-" + preflight.provider_version,
            dataset_revision=self.paths.dataset_revision,
        )
        totals = {"committed": 0, "skipped": 0, "failed": 0, "rows": 0}
        yahoo_observations = (
            self.volume_provider.read_daily_volumes(
                symbols=self.symbols,
                start=min(daily_start, intraday_start),
                end=end,
            )
            if self.volume_provider is not None
            else None
        )
        if yahoo_observations is not None:
            self.storage.record_yahoo_volume_evidence(
                tuple(yahoo_observations.values())
            )
        for timeframe, start in (
            (Timeframe.DAY_1, daily_start),
            (Timeframe.MINUTE_60, intraday_start),
        ):
            pending = tuple(
                symbol
                for symbol in self.symbols
                if not self._task_committed(symbol, timeframe, start, end)
            )
            totals["skipped"] += len(self.symbols) - len(pending)
            if not pending:
                continue
            for offset in range(0, len(pending), 16):
                batch = pending[offset : offset + 16]
                try:
                    bars = (
                        self.provider.read_daily_bars(
                            symbols=batch, start=start, end=end
                        )
                        if timeframe is Timeframe.DAY_1
                        else self.provider.read_intraday_bars(
                            symbols=batch,
                            start=start,
                            end=end,
                            sessions=sessions,
                        )
                    )
                    if yahoo_observations is not None:
                        bars = self._apply_yahoo_volume(bars, yahoo_observations)
                except Exception:
                    for symbol in batch:
                        self._mark_task_failed(symbol, timeframe, start, end)
                    totals["failed"] += len(batch)
                    continue
                by_symbol = {
                    symbol: tuple(bar for bar in bars if bar.symbol == symbol)
                    for symbol in batch
                }
                try:
                    self._commit_batch(
                        timeframe=timeframe,
                        start=start,
                        end=end,
                        rows_by_symbol=by_symbol,
                    )
                except Exception:
                    for symbol in batch:
                        self._mark_task_failed(symbol, timeframe, start, end)
                    totals["failed"] += len(batch)
                    continue
                totals["committed"] += len(batch)
                totals["rows"] += sum(len(rows) for rows in by_symbol.values())
        actions = self.provider.read_corporate_actions(
            symbols=self.symbols, start=daily_start, end=end
        )
        observed_at = self.clock()
        if preflight.instrument_metadata:
            self.storage.record_instrument_metadata(
                preflight.instrument_metadata,
                observed_at=observed_at,
                provider=preflight.provider,
            )
        for symbol in self.symbols:
            self.storage.write_rehab_snapshot(
                symbol=symbol,
                frame=actions.get(symbol, pd.DataFrame()),
                observed_at=observed_at,
                dataset_revision=self.paths.dataset_revision,
            )
        actual, complete = self.storage.common_daily_cutoff(self.symbols)
        result: dict[str, object] = {
            "status": "ok" if totals["failed"] == 0 else "partial",
            "mode": "bootstrap",
            "provider": preflight.provider,
            "feed": preflight.feed,
            "provider_version": preflight.provider_version,
            "delay_minutes": preflight.delay_minutes,
            "dataset_revision": self.paths.dataset_revision,
            "symbol_count": len(self.symbols),
            "tasks_committed": totals["committed"],
            "tasks_skipped": totals["skipped"],
            "tasks_failed": totals["failed"],
            "rows_committed": totals["rows"],
            "instrument_metadata_count": len(preflight.instrument_metadata),
            "daily_symbols_complete": complete,
            "daily_symbols_total": len(self.symbols),
            "data_cutoff": actual.isoformat() if actual else None,
            "expected_cutoff": end.isoformat(),
        }
        self.storage.record_health(result)
        return result

    def refresh(
        self,
        progress_callback: Callable[[int, int, str, Timeframe], None] | None = None,
    ) -> dict[str, object]:
        daily_starts = [
            self._next_start(symbol, Timeframe.DAY_1) for symbol in self.symbols
        ]
        intraday_starts = [
            self._next_start(symbol, Timeframe.MINUTE_60) for symbol in self.symbols
        ]
        return self.bootstrap(
            daily_start=min(daily_starts, default=date(2018, 1, 1)),
            intraday_start=min(intraday_starts, default=date(2024, 8, 10)),
        )

    def _next_start(self, symbol: str, timeframe: Timeframe) -> date:
        watermark = self.storage.latest_watermark(symbol, timeframe)
        return watermark.date() if watermark else (
            date(2018, 1, 1)
            if timeframe is Timeframe.DAY_1
            else date(2024, 8, 10)
        )

    def _task_committed(
        self, symbol: str, timeframe: Timeframe, start: date, end: date
    ) -> bool:
        state = self.storage.task(
            task_id_for(symbol=symbol, timeframe=timeframe, start=start, end=end)
        )
        return state is not None and state.status == "committed"

    def _ensure_task(
        self, symbol: str, timeframe: Timeframe, start: date, end: date
    ) -> str:
        task_id = task_id_for(
            symbol=symbol, timeframe=timeframe, start=start, end=end
        )
        self.storage.ensure_task(
            task_id=task_id,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            priority="high" if timeframe is Timeframe.DAY_1 else "medium",
            dataset_revision=self.paths.dataset_revision,
        )
        return task_id

    def _mark_task_failed(
        self, symbol: str, timeframe: Timeframe, start: date, end: date
    ) -> None:
        task_id = self._ensure_task(symbol, timeframe, start, end)
        self.storage.mark_failed(
            task_id, status="retry", error_code="provider_request_failed"
        )

    def _commit_task(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: date,
        end: date,
        rows: Sequence[ProviderBar],
    ) -> None:
        task_id = self._ensure_task(symbol, timeframe, start, end)
        self.storage.mark_fetching(task_id)
        if not rows:
            self.storage.complete_empty_task(task_id)
            return
        normalized = tuple(self._market_bar(row, task_id) for row in rows)
        parts = self.storage.write_page(
            task_id=task_id, page_index=1, rows=normalized
        )
        self.storage.record_volume_provenance(
            rows=rows,
            task_id=task_id,
        )
        self.storage.commit_page(
            task_id=task_id,
            page_index=1,
            next_page_token=None,
            parts=parts,
        )

    def _commit_batch(
        self,
        *,
        timeframe: Timeframe,
        start: date,
        end: date,
        rows_by_symbol: dict[str, tuple[ProviderBar, ...]],
    ) -> None:
        prepared: list[
            tuple[str, str, tuple[ProviderBar, ...], tuple[RawPagePart, ...]]
        ] = []
        all_parts: list[RawPagePart] = []
        all_provider_rows: list[ProviderBar] = []
        for symbol, rows in rows_by_symbol.items():
            task_id = self._ensure_task(symbol, timeframe, start, end)
            self.storage.mark_fetching(task_id)
            if not rows:
                prepared.append((symbol, task_id, rows, ()))
                continue
            normalized = tuple(self._market_bar(row, task_id) for row in rows)
            parts = self.storage.prepare_page(
                task_id=task_id,
                page_index=1,
                rows=normalized,
            )
            prepared.append((symbol, task_id, rows, parts))
            all_parts.extend(parts)
            all_provider_rows.extend(rows)
        self.storage.register_catalog_parts(all_parts)
        self.storage.record_volume_provenance(
            rows=all_provider_rows,
            task_id="provider-batch",
        )
        for _symbol, task_id, _rows, raw_parts in prepared:
            if not raw_parts:
                self.storage.complete_empty_task(task_id)
                continue
            self.storage.commit_page(
                task_id=task_id,
                page_index=1,
                next_page_token=None,
                parts=raw_parts,
            )

    def _market_bar(self, row: ProviderBar, task_id: str) -> MarketBar:
        return MarketBar(
            symbol=row.symbol,
            timeframe=row.timeframe,
            session=MarketSession.REGULAR,
            session_date=row.session_date,
            bar_start=row.bar_start,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            turnover=row.turnover,
            source_available_at=row.source_available_at,
            ingested_at=max(self.clock(), row.source_available_at),
            raw_batch_id=task_id,
            schema_version=LIVE_SCHEMA_VERSION,
            dataset_revision=self.paths.dataset_revision,
            revision_no=1,
        )

    @staticmethod
    def _apply_yahoo_volume(
        bars: Sequence[ProviderBar],
        observations: object,
    ) -> tuple[ProviderBar, ...]:
        if not isinstance(observations, dict):
            raise RuntimeError("yahoo_volume_evidence_invalid")
        grouped: dict[tuple[str, date], list[ProviderBar]] = {}
        for bar in bars:
            grouped.setdefault((bar.symbol, bar.session_date), []).append(bar)
        result: list[ProviderBar] = []
        for key, members in grouped.items():
            observation = observations.get(key)
            if observation is None:
                continue
            yahoo_volume = int(observation.volume)
            observed_at = observation.observed_at
            if members[0].timeframe is Timeframe.DAY_1:
                daily_scale = (
                    yahoo_volume / members[0].volume
                    if members[0].volume > 0
                    else None
                )
                result.extend(
                    replace(
                        member,
                        volume=yahoo_volume,
                        volume_source="yahoo",
                        raw_volume=member.volume,
                        turnover=(
                            member.turnover * daily_scale
                            if member.turnover is not None
                            and daily_scale is not None
                            else None
                        ),
                        volume_scale=daily_scale,
                        volume_estimated=False,
                        volume_observed_at=observed_at,
                        source_available_at=max(
                            member.source_available_at, observation.available_at
                        ),
                    )
                    for member in members
                )
                continue
            raw_total = sum(member.volume for member in members)
            if raw_total <= 0:
                continue
            allocated = [
                int(yahoo_volume * member.volume / raw_total) for member in members
            ]
            allocated[-1] += yahoo_volume - sum(allocated)
            for member, scaled in zip(members, allocated, strict=True):
                scale = yahoo_volume / raw_total
                result.append(
                    replace(
                        member,
                        volume=scaled,
                        volume_source="yahoo-scaled-by-iex-shape",
                        raw_volume=member.volume,
                        turnover=(
                            member.turnover * scale
                            if member.turnover is not None
                            else None
                        ),
                        volume_scale=scale,
                        volume_estimated=True,
                        volume_observed_at=observed_at,
                        source_available_at=max(
                            member.source_available_at, observation.available_at
                        ),
                    )
                )
        return tuple(result)


__all__ = ["LiveMarketDataService"]
