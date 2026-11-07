from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self

import duckdb
import pandas as pd
import pytest

from stock_prediction.contracts.market_data import Timeframe
from stock_prediction.live.config import LivePaths
from stock_prediction.live.ingestion import (
    LiveOpenDService,
    _last_completed_session,
    _missing_history_tasks,
)
from stock_prediction.live.universe_scope import EXPANDED_LIVE_UNIVERSE
from stock_prediction.live.universe_scope import LiveUniverseSpec
from stock_prediction.live.storage import LiveStorage, task_id_for
from stock_prediction.opend.errors import OpenDError, OpenDErrorClass
from stock_prediction.opend.ports import RawHistoryPage, SdkCallResult
from stock_prediction.universe.active import ACTIVE_UNIVERSE
from stock_prediction.universe.seed import SEED_UNIVERSE, SEED_UNIVERSE_VERSION


class PreflightContext:
    def __init__(
        self,
        *,
        history_used: int = 0,
        history_remaining: int = 100,
        history_details: list[object] | None = None,
    ) -> None:
        self.history_calls = 0
        self.closed = False
        self.history_used = history_used
        self.history_remaining = history_remaining
        self.history_details = history_details or []

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        self.closed = True
        return False

    def get_global_state(self) -> SdkCallResult[dict[str, object]]:
        return SdkCallResult.success({"qot_logined": True, "server_ver": "10.7.6718"})

    def get_history_kl_quota(
        self, get_detail: bool = False
    ) -> SdkCallResult[tuple[int, int, list[object]]]:
        assert get_detail is True
        return SdkCallResult.success(
            (self.history_used, self.history_remaining, self.history_details)
        )

    def query_subscription(
        self, is_all_conn: bool = True
    ) -> SdkCallResult[dict[str, object]]:
        return SdkCallResult.success({"total_used": 0, "remain": 100, "sub_list": {}})

    def request_trading_days(
        self,
        market: str,
        start: str,
        end: str,
        code: str | None = None,
    ) -> SdkCallResult[list[dict[str, object]]]:
        assert market == "US"
        return SdkCallResult.success(
            [
                {"time": "2026-07-09", "trade_date_type": "WHOLE"},
                {"time": "2026-07-10", "trade_date_type": "WHOLE"},
                {"time": "2026-07-13", "trade_date_type": "WHOLE"},
            ]
        )

    def request_history_kline_page(
        self,
        *,
        code: str,
        timeframe: str,
        start: str,
        end: str,
        max_count: int,
        page_token: str | None,
    ) -> SdkCallResult[RawHistoryPage]:
        self.history_calls += 1
        raise AssertionError("preflight must not request historical K lines")

    def get_market_state(self, code_list: list[str]) -> SdkCallResult[Any]:
        raise AssertionError

    def get_market_snapshot(self, code_list: list[str]) -> SdkCallResult[Any]:
        raise AssertionError

    def get_rehab(self, code: str) -> SdkCallResult[Any]:
        raise AssertionError

    def request_history_kline(self, **kwargs: object) -> SdkCallResult[Any]:
        raise AssertionError


class HistoryCollectionContext:
    def __init__(self, responses: list[SdkCallResult[RawHistoryPage]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def request_history_kline_page(
        self, **kwargs: object
    ) -> SdkCallResult[RawHistoryPage]:
        self.calls.append(kwargs)
        return self.responses.pop(0)


class RehabCollectionContext:
    def __init__(self) -> None:
        self.code_lists: list[list[str]] = []

    def get_rehab(self, code_list: list[str]) -> SdkCallResult[pd.DataFrame]:
        self.code_lists.append(code_list)
        return SdkCallResult.success(
            pd.DataFrame(
                {
                    "ex_div_date": ["2026-07-10"],
                    "split_ratio": [1.0],
                    "per_cash_div": [0.0],
                }
            )
        )


def _daily_history_frame(
    time_key: str = "2026-07-10 00:00:00",
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": ["US.SPY"],
            "time_key": [time_key],
            "open": [620.0],
            "high": [622.0],
            "low": [619.0],
            "close": [621.0],
            "volume": [1_000_000],
            "turnover": [620_500_000.0],
        }
    )


def _nonretryable_history_failure() -> SdkCallResult[RawHistoryPage]:
    return SdkCallResult.failure(
        OpenDError(
            OpenDErrorClass.PERMISSION_DENIED,
            "OpenD permission denied",
            retryable=False,
        )
    )


def test_rehab_collection_uses_the_list_based_opend_contract(
    tmp_path: Path,
) -> None:
    context = RehabCollectionContext()
    service = LiveOpenDService(
        LivePaths.from_workspace(tmp_path),
        clock=lambda: datetime(2026, 8, 23, 20, tzinfo=UTC),
        sleep=lambda _seconds: None,
    )

    result = service._collect_rehab(context, symbols=("US.SPY",))

    assert result == {"committed": 1, "failed": 0}
    assert context.code_lists == [["US.SPY"]]


def _retryable_history_failure() -> SdkCallResult[RawHistoryPage]:
    return SdkCallResult.failure(
        OpenDError(
            OpenDErrorClass.SDK_ERROR,
            "OpenD SDK request failed",
            retryable=True,
        )
    )


def _single_symbol_history_service(tmp_path: Path) -> LiveOpenDService:
    universe = LiveUniverseSpec(
        version="test-single-symbol-v1",
        symbols=("US.SPY",),
        etfs=("US.SPY",),
        effective_from=date(2026, 7, 10),
        reason="focused EOD collection regression coverage",
    )
    return LiveOpenDService(
        LivePaths.from_workspace(tmp_path),
        clock=lambda: datetime(2026, 7, 15, 20, 2, tzinfo=UTC),
        sleep=lambda _seconds: None,
        universe=universe,
    )


def _collect_daily_history(
    service: LiveOpenDService,
    context: HistoryCollectionContext,
    *,
    start: date,
    end: date,
) -> dict[str, int]:
    return service._collect_ranges(
        context,
        ranges=((Timeframe.DAY_1, start, end, "high"),),
    )


def _history_task_id(*, start: date, end: date) -> str:
    return task_id_for(
        symbol="US.SPY",
        timeframe=Timeframe.DAY_1,
        start=start,
        end=end,
    )


def _history_page_count(storage: LiveStorage, task_id: str) -> int:
    with duckdb.connect(str(storage.paths.ingestion)) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM history_pages WHERE task_id=?", [task_id]
        ).fetchone()
    assert row is not None
    return int(row[0])


def test_live_preflight_never_requests_historical_k_lines(tmp_path: Path) -> None:
    context = PreflightContext()
    service = LiveOpenDService(
        LivePaths.from_workspace(tmp_path),
        context_factory=lambda: context,
        clock=lambda: datetime(2026, 7, 13, 18, tzinfo=UTC),
        universe=EXPANDED_LIVE_UNIVERSE,
    )

    result = service.preflight()

    assert context.closed is True
    assert context.history_calls == 0
    assert result.symbol_count == 204
    assert result.history_quota_total == 100
    assert result.new_history_symbols == 204
    assert result.end == date(2026, 7, 10)
    assert result.blockers == ("history_quota_insufficient",)
    assert result.as_dict()["requests_history_kline"] is False
    health = service.storage.latest_health()
    assert health is not None
    assert health["data_cutoff"] is None
    assert health["expected_cutoff"] == "2026-07-10"
    assert health["snapshot_cutoff"] is None


def test_live_preflight_reuses_seed_quota_and_requires_one_symbol_reserve(
    tmp_path: Path,
) -> None:
    details = [{"code": symbol} for symbol in SEED_UNIVERSE]
    exact_context = PreflightContext(
        history_used=100,
        history_remaining=104,
        history_details=details,
    )
    reserve_context = PreflightContext(
        history_used=100,
        history_remaining=105,
        history_details=details,
    )

    exact = LiveOpenDService(
        LivePaths.from_workspace(tmp_path / "exact"),
        context_factory=lambda: exact_context,
        clock=lambda: datetime(2026, 7, 13, 18, tzinfo=UTC),
        universe=EXPANDED_LIVE_UNIVERSE,
    ).preflight()
    reserve = LiveOpenDService(
        LivePaths.from_workspace(tmp_path / "reserve"),
        context_factory=lambda: reserve_context,
        clock=lambda: datetime(2026, 7, 13, 18, tzinfo=UTC),
        universe=EXPANDED_LIVE_UNIVERSE,
    ).preflight()

    assert exact.new_history_symbols == 104
    assert exact.blockers == ("history_quota_reserve_exhausted",)
    assert reserve.new_history_symbols == 104
    assert reserve.blockers == ()


def test_legacy_seed_refresh_does_not_need_free_quota_for_known_symbols(
    tmp_path: Path,
) -> None:
    details = [{"code": symbol} for symbol in SEED_UNIVERSE]
    context = PreflightContext(
        history_used=100,
        history_remaining=0,
        history_details=details,
    )
    service = LiveOpenDService(
        LivePaths.from_workspace(tmp_path),
        context_factory=lambda: context,
        clock=lambda: datetime(2026, 7, 13, 18, tzinfo=UTC),
    )

    result = service.preflight()

    assert result.ready is True
    assert result.universe_version == SEED_UNIVERSE_VERSION
    assert result.symbol_count == 100
    assert result.new_history_symbols == 0
    assert result.history_quota_remaining == 0
    assert result.blockers == ()


def test_universe_expansion_plans_only_missing_symbol_timeframes() -> None:
    coverage = tuple(
        {"symbol": symbol, "timeframe": timeframe}
        for symbol in SEED_UNIVERSE
        for timeframe in ("1d", "60m")
    )

    tasks = _missing_history_tasks(
        coverage,
        end=date(2026, 8, 10),
        intraday_start=date(2024, 8, 10),
    )

    assert len(tasks) == 208
    assert {task.symbol for task in tasks} == set(ACTIVE_UNIVERSE) - set(
        SEED_UNIVERSE
    )
    assert sum(task.timeframe is Timeframe.DAY_1 for task in tasks) == 104
    assert sum(task.timeframe is Timeframe.MINUTE_60 for task in tasks) == 104
    assert all(
        task.start == date(2018, 1, 1)
        for task in tasks
        if task.timeframe is Timeframe.DAY_1
    )
    assert all(
        task.start == date(2024, 8, 10)
        for task in tasks
        if task.timeframe is Timeframe.MINUTE_60
    )


def test_universe_expansion_replans_a_partial_range_for_resumption() -> None:
    partial_symbol = "US.SPCX"
    coverage = (
        {
            "symbol": partial_symbol,
            "timeframe": "1d",
            "min_session_date": "2018-01-01",
            "max_session_date": "2023-12-29",
        },
    )

    tasks = _missing_history_tasks(
        coverage,
        end=date(2026, 8, 10),
        intraday_start=date(2024, 8, 10),
    )

    assert any(
        task.symbol == partial_symbol and task.timeframe is Timeframe.DAY_1
        for task in tasks
    )


def test_universe_expansion_fails_before_history_when_quota_is_insufficient(
    tmp_path: Path,
) -> None:
    context = PreflightContext(history_used=100, history_remaining=0)
    service = LiveOpenDService(
        LivePaths.from_workspace(tmp_path),
        context_factory=lambda: context,
        clock=lambda: datetime(2026, 7, 13, 18, tzinfo=UTC),
        universe=EXPANDED_LIVE_UNIVERSE,
    )

    with pytest.raises(RuntimeError, match="live_preflight_blocked"):
        service.expand_universe(allow_zero_reserve=True)

    assert context.history_calls == 0


def test_last_completed_session_switches_at_1610_new_york_close_boundary() -> None:
    sessions = (
        (date(2026, 7, 30), "WHOLE"),
        (date(2026, 7, 31), "WHOLE"),
    )

    before_boundary = _last_completed_session(
        sessions,
        datetime(2026, 7, 31, 20, 9, 59, tzinfo=UTC),
    )
    at_boundary = _last_completed_session(
        sessions,
        datetime(2026, 7, 31, 20, 10, tzinfo=UTC),
    )

    assert before_boundary == date(2026, 7, 30)
    assert at_boundary == date(2026, 7, 31)


def test_rehab_snapshots_are_immutable_versioned_and_queryable(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    storage = LiveStorage(paths)
    observed_at = datetime(2026, 7, 21, 20, 30, tzinfo=UTC)
    frame = pd.DataFrame(
        [
            {
                "ex_div_date": "2020-08-31",
                "split_ratio": 0.25,
                "per_cash_div": None,
                "special_dividend": None,
                "forward_adj_factorA": 0.25,
                "forward_adj_factorB": 0.0,
                "backward_adj_factorA": 4.0,
                "backward_adj_factorB": 0.0,
            }
        ]
    )

    first = storage.write_rehab_snapshot(
        symbol="US.AAPL",
        frame=frame,
        observed_at=observed_at,
        dataset_revision="opend-live-market-bars-v1",
    )
    second = storage.write_rehab_snapshot(
        symbol="US.AAPL",
        frame=frame,
        observed_at=observed_at,
        dataset_revision="opend-live-market-bars-v1",
    )

    assert first == second
    assert first.path.is_file()
    with duckdb.connect(str(paths.catalog)) as connection:
        snapshots = connection.execute(
            "SELECT COUNT(*) FROM rehab_snapshots"
        ).fetchone()
        observations = connection.execute(
            "SELECT split_ratio, raw_batch_id FROM latest_rehab_observations"
        ).fetchone()
    assert snapshots == (1,)
    assert observations == (0.25, first.snapshot_id)


def test_expired_page_token_can_reset_without_deleting_page_evidence(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    storage = LiveStorage(paths)
    task_id = "live-US-AAPL-1d-reset-test"
    storage.ensure_task(
        task_id=task_id,
        symbol="US.AAPL",
        timeframe=Timeframe.DAY_1,
        start=date(2018, 1, 1),
        end=date(2026, 7, 21),
        priority="high",
        dataset_revision="opend-live-market-bars-v1",
    )
    with duckdb.connect(str(paths.ingestion)) as connection:
        connection.execute(
            """
            UPDATE history_tasks SET status='blocked', last_page_index=2,
                next_page_token='expired', error_code='sdk_error'
            WHERE task_id=?
            """,
            [task_id],
        )
        connection.execute(
            """
            INSERT INTO history_pages VALUES (
                'existing-page', ?, 1, 'expired',
                '2021-01-01T00:00:00Z', '[]', '2026-07-21T20:00:00Z'
            )
            """,
            [task_id],
        )

    storage.reset_pagination(task_id)

    state = storage.task(task_id)
    assert state is not None
    assert state.status == "planned"
    assert state.last_page_index == 0
    assert state.next_page_token is None
    with duckdb.connect(str(paths.ingestion)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM history_pages WHERE task_id=?", [task_id]
        ).fetchone() == (1,)


def test_legacy_chain_upgrade_restarts_unverifiable_nonterminal_task_before_write(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    task_id = _history_task_id(start=date(2026, 7, 10), end=date(2026, 7, 15))
    with duckdb.connect(str(paths.ingestion)) as connection:
        connection.execute(
            """
            CREATE TABLE history_tasks (
                task_id VARCHAR PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                requested_start DATE NOT NULL,
                requested_end DATE NOT NULL,
                priority VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                last_page_index INTEGER NOT NULL,
                next_page_token VARCHAR,
                watermark TIMESTAMPTZ,
                dataset_revision VARCHAR NOT NULL,
                attempt_count INTEGER NOT NULL,
                error_code VARCHAR,
                updated_at TIMESTAMPTZ NOT NULL
            );
            CREATE TABLE history_pages (
                page_id VARCHAR PRIMARY KEY,
                task_id VARCHAR NOT NULL,
                page_index INTEGER NOT NULL,
                next_page_token VARCHAR,
                watermark TIMESTAMPTZ NOT NULL,
                parts_json JSON NOT NULL,
                committed_at TIMESTAMPTZ NOT NULL,
                UNIQUE(task_id, page_index)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO history_tasks VALUES (
                ?, 'US.SPY', '1d', '2026-07-10', '2026-07-15', 'high',
                'blocked', 2, 'new-2', '2026-07-13T04:00:00Z',
                'opend-live-market-bars-pit-adjusted-v2', 3, 'sdk_error',
                '2026-07-13T20:00:00Z'
            )
            """,
            [task_id],
        )
        connection.execute(
            """
            INSERT INTO history_pages VALUES
                ('legacy-page-1', ?, 1, 'old-1', '2026-07-10T04:00:00Z',
                 '[]', '2026-07-10T20:00:00Z'),
                ('legacy-page-2', ?, 2, 'old-2', '2026-07-13T04:00:00Z',
                 '[]', '2026-07-13T20:00:00Z')
            """,
            [task_id, task_id],
        )

    service = _single_symbol_history_service(tmp_path)
    migrated = service.storage.task(task_id)

    assert migrated is not None
    assert migrated.status == "planned"
    assert migrated.last_page_index == 0
    assert migrated.next_page_token is None
    assert migrated.watermark is None
    assert service.storage.committed_page_tokens(task_id) == ()
    assert _history_page_count(service.storage, task_id) == 2

    context = HistoryCollectionContext([_nonretryable_history_failure()])
    result = _collect_daily_history(
        service,
        context,
        start=date(2026, 7, 10),
        end=date(2026, 7, 15),
    )

    assert result == {
        "committed": 0,
        "skipped": 0,
        "failed": 1,
        "pages": 0,
        "rows": 0,
    }
    assert [call["page_token"] for call in context.calls] == [None]
    assert _history_page_count(service.storage, task_id) == 2


def test_partial_chain_migration_restarts_before_write_and_completes_once(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    unsafe_task_id = _history_task_id(
        start=date(2026, 7, 10), end=date(2026, 7, 15)
    )
    committed_task_id = "legacy-committed-task"
    with duckdb.connect(str(paths.ingestion)) as connection:
        connection.execute(
            """
            CREATE TABLE history_tasks (
                task_id VARCHAR PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                requested_start DATE NOT NULL,
                requested_end DATE NOT NULL,
                priority VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                last_page_index INTEGER NOT NULL,
                next_page_token VARCHAR,
                watermark TIMESTAMPTZ,
                pagination_chain INTEGER DEFAULT 0,
                dataset_revision VARCHAR NOT NULL,
                attempt_count INTEGER NOT NULL,
                error_code VARCHAR,
                updated_at TIMESTAMPTZ NOT NULL
            );
            CREATE TABLE history_pages (
                page_id VARCHAR PRIMARY KEY,
                task_id VARCHAR NOT NULL,
                page_index INTEGER NOT NULL,
                next_page_token VARCHAR,
                watermark TIMESTAMPTZ NOT NULL,
                parts_json JSON NOT NULL,
                committed_at TIMESTAMPTZ NOT NULL,
                UNIQUE(task_id, page_index)
            );
            CREATE TABLE history_page_chains (
                task_id VARCHAR NOT NULL,
                pagination_chain INTEGER NOT NULL,
                page_index INTEGER NOT NULL,
                next_page_token VARCHAR,
                watermark TIMESTAMPTZ NOT NULL,
                committed_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(task_id, pagination_chain, page_index)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO history_tasks VALUES
                (?, 'US.SPY', '1d', '2026-07-10', '2026-07-15', 'high',
                 'blocked', 2, 'new-2', '2026-07-13T04:00:00Z', 0,
                 'opend-live-market-bars-pit-adjusted-v2', 3, 'sdk_error',
                 '2026-07-13T20:00:00Z'),
                (?, 'US.SPY', '1d', '2026-07-10', '2026-07-15', 'high',
                 'committed', 1, NULL, '2026-07-10T04:00:00Z', 0,
                 'opend-live-market-bars-pit-adjusted-v2', 1, NULL,
                 '2026-07-10T20:00:00Z')
            """,
            [unsafe_task_id, committed_task_id],
        )
        connection.execute(
            """
            INSERT INTO history_pages VALUES
                ('unsafe-page-1', ?, 1, 'old-1', '2026-07-10T04:00:00Z',
                 '[]', '2026-07-10T20:00:00Z'),
                ('unsafe-page-2', ?, 2, 'old-2', '2026-07-13T04:00:00Z',
                 '[]', '2026-07-13T20:00:00Z'),
                ('committed-page-1', ?, 1, NULL, '2026-07-10T04:00:00Z',
                 '[]', '2026-07-10T20:00:00Z')
            """,
            [unsafe_task_id, unsafe_task_id, committed_task_id],
        )

    service = _single_symbol_history_service(tmp_path)
    unsafe = service.storage.task(unsafe_task_id)
    committed = service.storage.task(committed_task_id)

    assert unsafe is not None
    assert unsafe.status == "planned"
    assert unsafe.last_page_index == 0
    assert unsafe.next_page_token is None
    assert unsafe.watermark is None
    assert committed is not None
    assert committed.status == "committed"
    assert committed.last_page_index == 1
    assert _history_page_count(service.storage, unsafe_task_id) == 2
    assert _history_page_count(service.storage, committed_task_id) == 1

    context = HistoryCollectionContext([_nonretryable_history_failure()])
    result = _collect_daily_history(
        service,
        context,
        start=date(2026, 7, 10),
        end=date(2026, 7, 15),
    )
    assert result == {
        "committed": 0,
        "skipped": 0,
        "failed": 1,
        "pages": 0,
        "rows": 0,
    }
    assert [call["page_token"] for call in context.calls] == [None]
    assert _history_page_count(service.storage, unsafe_task_id) == 2

    LiveStorage(paths)
    unsafe_after_second_init = service.storage.task(unsafe_task_id)
    assert unsafe_after_second_init is not None
    assert unsafe_after_second_init.last_page_index == 0
    with duckdb.connect(str(paths.ingestion)) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM ingestion_schema_migrations
            WHERE migration_name = 'history_page_chains_v1'
            """
        ).fetchone() == (1,)


def test_eod_history_collection_commits_real_page_iterator_result(
    tmp_path: Path,
) -> None:
    context = HistoryCollectionContext(
        [SdkCallResult.success(RawHistoryPage(_daily_history_frame(), None))]
    )
    service = _single_symbol_history_service(tmp_path)

    result = _collect_daily_history(
        service,
        context,
        start=date(2026, 7, 10),
        end=date(2026, 7, 10),
    )

    task = service.storage.task(
        _history_task_id(start=date(2026, 7, 10), end=date(2026, 7, 10))
    )
    assert result == {
        "committed": 1,
        "skipped": 0,
        "failed": 0,
        "pages": 1,
        "rows": 1,
    }
    assert task is not None
    assert task.status == "committed"
    assert task.last_page_index == 1
    assert task.next_page_token is None
    assert [call["page_token"] for call in context.calls] == [None]


def test_eod_resume_rejects_row_at_durable_watermark_before_new_page_commit(
    tmp_path: Path,
) -> None:
    start = date(2026, 7, 10)
    service = _single_symbol_history_service(tmp_path)
    staged = _collect_daily_history(
        service,
        HistoryCollectionContext(
            [
                SdkCallResult.success(RawHistoryPage(_daily_history_frame(), "page-2")),
                _nonretryable_history_failure(),
            ]
        ),
        start=start,
        end=start,
    )
    task_id = _history_task_id(start=start, end=start)
    assert staged == {
        "committed": 0,
        "skipped": 0,
        "failed": 1,
        "pages": 1,
        "rows": 1,
    }
    assert _history_page_count(service.storage, task_id) == 1

    resumed_context = HistoryCollectionContext(
        [
            SdkCallResult.success(RawHistoryPage(_daily_history_frame(), None)),
            _nonretryable_history_failure(),
        ]
    )
    resumed = _collect_daily_history(
        service,
        resumed_context,
        start=start,
        end=start,
    )

    assert resumed == {
        "committed": 0,
        "skipped": 0,
        "failed": 1,
        "pages": 0,
        "rows": 0,
    }
    assert _history_page_count(service.storage, task_id) == 1
    assert [call["page_token"] for call in resumed_context.calls] == ["page-2", None]


def test_eod_resume_rejects_old_durable_page_token_before_new_page_commit(
    tmp_path: Path,
) -> None:
    start = date(2026, 7, 10)
    end = date(2026, 7, 14)
    service = _single_symbol_history_service(tmp_path)
    staged = _collect_daily_history(
        service,
        HistoryCollectionContext(
            [
                SdkCallResult.success(
                    RawHistoryPage(_daily_history_frame(), "page-1")
                ),
                SdkCallResult.success(
                    RawHistoryPage(
                        _daily_history_frame("2026-07-13 00:00:00"), "page-2"
                    )
                ),
                _nonretryable_history_failure(),
            ]
        ),
        start=start,
        end=end,
    )
    task_id = _history_task_id(start=start, end=end)
    assert staged == {
        "committed": 0,
        "skipped": 0,
        "failed": 1,
        "pages": 2,
        "rows": 2,
    }
    assert _history_page_count(service.storage, task_id) == 2

    resumed_context = HistoryCollectionContext(
        [
            SdkCallResult.success(
                RawHistoryPage(
                    _daily_history_frame("2026-07-14 00:00:00"), "page-1"
                )
            ),
            _nonretryable_history_failure(),
        ]
    )
    resumed = _collect_daily_history(
        service,
        resumed_context,
        start=start,
        end=end,
    )

    assert resumed == {
        "committed": 0,
        "skipped": 0,
        "failed": 1,
        "pages": 0,
        "rows": 0,
    }
    assert _history_page_count(service.storage, task_id) == 2
    assert [call["page_token"] for call in resumed_context.calls] == ["page-2", None]


def test_eod_later_resume_rejects_earlier_token_from_restarted_chain(
    tmp_path: Path,
) -> None:
    start = date(2026, 7, 10)
    end = date(2026, 7, 15)
    service = _single_symbol_history_service(tmp_path)
    original_chain = _collect_daily_history(
        service,
        HistoryCollectionContext(
            [
                SdkCallResult.success(
                    RawHistoryPage(_daily_history_frame(), "old-1")
                ),
                SdkCallResult.success(
                    RawHistoryPage(
                        _daily_history_frame("2026-07-13 00:00:00"), "old-2"
                    )
                ),
                _nonretryable_history_failure(),
            ]
        ),
        start=start,
        end=end,
    )
    task_id = _history_task_id(start=start, end=end)
    assert original_chain == {
        "committed": 0,
        "skipped": 0,
        "failed": 1,
        "pages": 2,
        "rows": 2,
    }
    assert _history_page_count(service.storage, task_id) == 2

    restarted_chain = _collect_daily_history(
        service,
        HistoryCollectionContext(
            [
                _retryable_history_failure(),
                SdkCallResult.success(
                    RawHistoryPage(_daily_history_frame(), "new-1")
                ),
                SdkCallResult.success(
                    RawHistoryPage(
                        _daily_history_frame("2026-07-13 00:00:00"), "new-2"
                    )
                ),
                _nonretryable_history_failure(),
            ]
        ),
        start=start,
        end=end,
    )
    state_after_restart = service.storage.task(task_id)
    assert restarted_chain == {
        "committed": 0,
        "skipped": 0,
        "failed": 1,
        "pages": 2,
        "rows": 2,
    }
    assert state_after_restart is not None
    assert state_after_restart.next_page_token == "new-2"
    assert _history_page_count(service.storage, task_id) == 2

    later_resume = _collect_daily_history(
        service,
        HistoryCollectionContext(
            [
                SdkCallResult.success(
                    RawHistoryPage(
                        _daily_history_frame("2026-07-14 00:00:00"), "new-1"
                    )
                ),
                _nonretryable_history_failure(),
            ]
        ),
        start=start,
        end=end,
    )

    assert later_resume == {
        "committed": 0,
        "skipped": 0,
        "failed": 1,
        "pages": 0,
        "rows": 0,
    }
    assert _history_page_count(service.storage, task_id) == 2
