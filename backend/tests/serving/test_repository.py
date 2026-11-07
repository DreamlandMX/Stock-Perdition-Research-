from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import shutil
from typing import Any

import pyarrow as pa
import pytest
from pydantic import ValidationError

from backend.tests.fixtures.research_snapshot_factory import (
    GENERATED_AT,
    SYMBOLS,
    make_snapshot_tables,
    resolved_snapshot,
    write_snapshot_set,
    write_valid_snapshot,
)
from stock_prediction.contracts.serving import (
    BacktestReadModel,
    ForecastAvailable,
    ForecastUnavailable,
    PredictionBasis,
    ResolvedSnapshot,
    ScreenConfidenceFloor,
    ScreenReturnBasis,
    ServingStatus,
    SortDirection,
)
from stock_prediction.serving.manifest import RESEARCH_TABLE_SCHEMAS
from stock_prediction.serving.repository import (
    BacktestQuery,
    DataHealthQuery,
    DuckDbResearchRepository,
    HistoryQuery,
    InstrumentQuery,
    ModelQuery,
    ScreenQuery,
    SnapshotIntegrityError,
    SnapshotNotFound,
    SnapshotQuery,
    SnapshotVersionConflict,
    UnavailableResearchRepository,
)
from stock_prediction.serving.materialize import materialize_research_snapshot


def test_resolution_scope_reuses_catalogue_but_rechecks_selected_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = write_valid_snapshot(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    load = repository._load_manifest
    calls = []

    def counted(directory: Path):
        calls.append(directory)
        return load(directory)

    monkeypatch.setattr(repository, "_load_manifest", counted)
    with repository.snapshot_resolution_scope():
        selected = repository.resolve_snapshot(SnapshotQuery())
        first = len(calls)
        assert (
            repository.resolve_snapshot(SnapshotQuery(as_of=selected.as_of)) == selected
        )
        assert len(calls) == first
        published.joinpath("screen.parquet").write_bytes(b"corrupt")
        with pytest.raises(SnapshotIntegrityError):
            repository.screen(selected, ScreenQuery())
    assert repository._resolution_manifests.get() is None
    with repository.snapshot_resolution_scope(), pytest.raises(SnapshotIntegrityError):
        repository.resolve_snapshot(SnapshotQuery())


def test_lightweight_context_and_batch_history_preserve_identity(
    tmp_path: Path,
) -> None:
    write_valid_snapshot(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    snapshot = repository.resolve_snapshot(SnapshotQuery())
    context = repository.snapshot_context(snapshot, (SYMBOLS[0],))
    assert context.symbol_count == len(SYMBOLS)
    assert tuple(item.symbol for item in context.instruments) == (SYMBOLS[0],)
    query = HistoryQuery()
    batch = repository.history_batch(snapshot, (SYMBOLS[0], "US.MISSING"), query)
    assert batch.items[0].history == repository.history(snapshot, SYMBOLS[0], query)
    assert batch.items[1].status == "unavailable"
    assert batch.items[1].error_code == "symbol_not_in_universe"


def test_history_range_filters_before_payload_loading(tmp_path: Path) -> None:
    write_valid_snapshot(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    snapshot = repository.resolve_snapshot(SnapshotQuery())
    complete = repository.history(snapshot, "US.AAPL", HistoryQuery())
    cutoff = complete.bars[-1].at
    selected = repository.history(
        snapshot, "US.AAPL", HistoryQuery(from_at=cutoff, to_at=cutoff)
    )
    assert selected.bars == (complete.bars[-1],)
    assert not repository.history(
        snapshot,
        "US.AAPL",
        HistoryQuery(
            from_at=cutoff + timedelta(seconds=1), to_at=cutoff + timedelta(seconds=1)
        ),
    ).bars


def test_overview_cache_reuses_calculation_but_checks_all_published_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior, _ = write_snapshot_set(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    snapshot = repository.resolve_snapshot(SnapshotQuery())
    first = repository.overview(snapshot, 5)
    monkeypatch.setattr(
        repository,
        "_build_overview",
        lambda *args: pytest.fail("cached calculation repeated"),
    )
    assert repository.overview(snapshot, 5) == first
    (prior / "screen.parquet").unlink()
    with pytest.raises(SnapshotIntegrityError):
        repository.overview(snapshot, 5)


@pytest.mark.parametrize(
    "table,column",
    [("history_bars.parquet", "at"), ("history_events.parquet", "occurred_at")],
)
def test_batch_rejects_payload_time_different_from_index(
    tmp_path: Path, table: str, column: str
) -> None:
    snapshot = resolved_snapshot()
    tables = make_snapshot_tables(snapshot)
    rows = tables[table].to_pylist()
    indexed_at = datetime(2026, 7, 8, 20, tzinfo=UTC)
    rows[0][column] = indexed_at
    tables[table] = pa.Table.from_pylist(rows, schema=tables[table].schema)
    materialize_research_snapshot(
        tmp_path,
        snapshot=snapshot,
        generated_at=GENERATED_AT,
        tables=tables,
        champion_exists=True,
    )
    repository = DuckDbResearchRepository(tmp_path)
    with pytest.raises(SnapshotIntegrityError, match="time differs"):
        repository.history_batch(
            snapshot, ("US.AAPL",), HistoryQuery(from_at=indexed_at, to_at=indexed_at)
        )


def test_history_cache_reuses_verified_models_but_detects_deleted_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = write_valid_snapshot(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    snapshot = repository.resolve_snapshot(SnapshotQuery())
    query = HistoryQuery()
    first = repository.history(snapshot, "US.AAPL", query)
    monkeypatch.setattr(
        repository, "history_batch", lambda *args: pytest.fail("cached history reread")
    )
    assert repository.history(snapshot, "US.AAPL", query) == first
    (directory / "history_bars.parquet").unlink()
    with pytest.raises(SnapshotIntegrityError):
        repository.history(snapshot, "US.AAPL", query)


def _symlink_or_skip(
    link: Path, target: Path, *, target_is_directory: bool = False
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"reparse creation is unavailable: {error}")


def _all_unavailable_forecast_rows(
    tables: dict[str, pa.Table],
) -> list[dict[str, object]]:
    rows = tables["forecasts.parquet"].to_pylist()
    unavailable = next(row for row in rows if row["availability"] == "unavailable")
    unavailable_payload = json.loads(unavailable["payload_json"])
    forecast_rows: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        payload = dict(unavailable_payload)
        payload["symbol"] = symbol
        forecast_rows.append(
            {
                "symbol": symbol,
                "availability": "unavailable",
                "payload_json": json.dumps(payload),
            }
        )
    return forecast_rows


def _materialize_forecast_rows(
    root: Path, forecast_rows: list[dict[str, object]]
) -> tuple[DuckDbResearchRepository, ResolvedSnapshot]:
    snapshot = resolved_snapshot()
    tables = make_snapshot_tables(snapshot)
    tables["forecasts.parquet"] = pa.Table.from_pylist(
        forecast_rows,
        schema=tables["forecasts.parquet"].schema,
    )
    materialize_research_snapshot(
        root,
        snapshot=snapshot,
        generated_at=GENERATED_AT,
        tables=tables,
        champion_exists=True,
    )
    return DuckDbResearchRepository(root), snapshot


def _materialize_confidence_sort_rows(
    root: Path,
) -> tuple[DuckDbResearchRepository, ResolvedSnapshot]:
    snapshot = resolved_snapshot()
    tables = make_snapshot_tables(snapshot)
    rows = tables["screen.parquet"].to_pylist()
    confidence_by_symbol = {
        "US.AAPL": "low",
        "US.AMZN": "high",
        "US.MSFT": "medium",
    }
    for row in rows:
        confidence = confidence_by_symbol.get(str(row["symbol"]))
        if confidence is None:
            continue
        payload = json.loads(str(row["payload_json"]))
        payload["confidence_level"] = confidence
        row["confidence_level"] = confidence
        row["payload_json"] = json.dumps(payload)
    tables["screen.parquet"] = pa.Table.from_pylist(
        rows,
        schema=tables["screen.parquet"].schema,
    )
    materialize_research_snapshot(
        root,
        snapshot=snapshot,
        generated_at=GENERATED_AT,
        tables=tables,
        champion_exists=True,
    )
    return DuckDbResearchRepository(root), snapshot


def _materialize_backtest_rows(
    root: Path,
) -> tuple[DuckDbResearchRepository, ResolvedSnapshot]:
    snapshot = resolved_snapshot()
    tables = make_snapshot_tables(snapshot)
    first = BacktestReadModel.model_validate_json(
        str(tables["backtests.parquet"].to_pylist()[0]["payload_json"]),
        strict=True,
    )
    second = BacktestReadModel.model_validate(
        {
            **first.model_dump(mode="python"),
            "split_version": "split-v2",
        },
        strict=True,
    )

    def physical_row(backtest: BacktestReadModel) -> dict[str, object]:
        return {
            "model_version": backtest.model_version,
            "strategy_version": backtest.strategy_version,
            "universe_version": backtest.universe_version,
            "split_version": backtest.split_version,
            "task": backtest.task,
            "horizon": backtest.horizon,
            "asset_group": backtest.asset_group,
            "evaluation_window_id": backtest.evaluation_window_id,
            "cost_scenario": backtest.cost_scenario,
            "payload_json": backtest.model_dump_json(),
        }

    tables["backtests.parquet"] = pa.Table.from_pylist(
        [physical_row(first), physical_row(second)],
        schema=RESEARCH_TABLE_SCHEMAS["backtests.parquet"],
    )
    materialize_research_snapshot(
        root,
        snapshot=snapshot,
        generated_at=GENERATED_AT,
        tables=tables,
        champion_exists=True,
    )
    return DuckDbResearchRepository(root), snapshot


def test_repository_rejects_table_hash_mismatch(tmp_path: Path) -> None:
    snapshot = write_valid_snapshot(tmp_path)
    snapshot.joinpath("screen.parquet").write_bytes(b"corrupt")
    with pytest.raises(SnapshotIntegrityError, match="screen.parquet"):
        DuckDbResearchRepository(tmp_path).resolve_snapshot(
            SnapshotQuery(snapshot_id="snapshot-20260710-r1")
        )


def test_warmed_manifest_cache_rejects_later_manifest_change(tmp_path: Path) -> None:
    published = write_valid_snapshot(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    repository.resolve_snapshot(SnapshotQuery())

    published.joinpath("manifest.json").write_bytes(b"{}")

    with pytest.raises(SnapshotIntegrityError, match="integrity verification"):
        repository.resolve_snapshot(SnapshotQuery())


def test_warmed_forecast_cache_rejects_later_table_change(tmp_path: Path) -> None:
    published = write_valid_snapshot(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    snapshot = repository.resolve_snapshot(SnapshotQuery())
    assert isinstance(repository.forecast(snapshot, "US.AAPL"), ForecastAvailable)

    published.joinpath("forecasts.parquet").write_bytes(b"corrupt")

    with pytest.raises(SnapshotIntegrityError, match="integrity verification"):
        repository.forecast(snapshot, "US.AAPL")


def test_warmed_screen_cache_rejects_later_table_change(tmp_path: Path) -> None:
    published = write_valid_snapshot(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    snapshot = repository.resolve_snapshot(SnapshotQuery())
    query = ScreenQuery(page=1, page_size=100)
    assert repository.screen(snapshot, query).items

    published.joinpath("screen.parquet").write_bytes(b"corrupt")

    with pytest.raises(SnapshotIntegrityError, match="integrity verification"):
        repository.screen(snapshot, query)


def test_repository_ignores_staging_and_rejects_manifest_directory_mismatch(
    tmp_path: Path,
) -> None:
    write_valid_snapshot(tmp_path)
    staging = tmp_path / "snapshot-hidden.staging"
    staging.mkdir()
    (staging / "manifest.json").write_text("{}", encoding="utf-8")
    repository = DuckDbResearchRepository(tmp_path)
    assert repository.resolve_snapshot(SnapshotQuery()).snapshot_id == (
        "snapshot-20260710-r1"
    )

    published = tmp_path / "copy-of-snapshot"
    published.mkdir()
    (published / "manifest.json").write_bytes(
        (tmp_path / "snapshot-20260710-r1" / "manifest.json").read_bytes()
    )
    with pytest.raises(SnapshotIntegrityError, match="directory"):
        repository.resolve_snapshot(SnapshotQuery())


def test_repository_resolves_latest_at_or_before_as_of_and_checks_versions(
    tmp_path: Path,
) -> None:
    stale, fresh = write_snapshot_set(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    assert repository.resolve_snapshot(SnapshotQuery()).snapshot_id == fresh.name
    historical = repository.resolve_snapshot(
        SnapshotQuery(as_of=datetime(2026, 7, 9, 21, tzinfo=UTC))
    )
    assert historical.snapshot_id == stale.name

    with pytest.raises(SnapshotVersionConflict, match="model_version"):
        repository.resolve_snapshot(
            SnapshotQuery(
                snapshot_id=fresh.name,
                model_version="model-v-conflict",
            )
        )
    with pytest.raises(SnapshotNotFound):
        repository.resolve_snapshot(
            SnapshotQuery(as_of=datetime(2020, 1, 1, tzinfo=UTC))
        )


def test_screen_has_stable_default_order_and_validated_page_bounds(
    tmp_path: Path,
) -> None:
    write_valid_snapshot(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    snapshot = repository.resolve_snapshot(SnapshotQuery())
    page = repository.screen(snapshot, ScreenQuery(page=1, page_size=5))
    assert [row.symbol for row in page.items[:2]] == ["US.AMZN", "US.MSFT"]
    assert page.sort_key == "default"
    assert page.total == 100
    assert not any(row.effective_abstain for row in page.items)

    abstain_tail = repository.screen(snapshot, ScreenQuery(page=10, page_size=10))
    assert abstain_tail.items[-1].effective_abstain
    with pytest.raises(ValueError, match="page exceeds"):
        repository.screen(snapshot, ScreenQuery(page=11, page_size=10))


def test_screen_thresholds_are_basis_aware_inclusive_and_exclude_nulls(
    tmp_path: Path,
) -> None:
    write_valid_snapshot(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    snapshot = repository.resolve_snapshot(SnapshotQuery())

    executable_probability = repository.screen(
        snapshot,
        ScreenQuery(
            prediction_basis=PredictionBasis.EXECUTABLE,
            min_probability=0.90,
        ),
    )
    forecast_probability = repository.screen(
        snapshot,
        ScreenQuery(
            prediction_basis=PredictionBasis.FORECAST,
            min_probability=0.99,
        ),
    )
    executable_q10 = repository.screen(
        snapshot,
        ScreenQuery(
            return_basis=ScreenReturnBasis.EXECUTABLE,
            max_q10=-0.045,
            page_size=100,
        ),
    )
    capital_gain_q10 = repository.screen(
        snapshot,
        ScreenQuery(
            return_basis=ScreenReturnBasis.CAPITAL_GAIN,
            max_q10=-0.04,
            page_size=100,
        ),
    )

    assert [row.symbol for row in executable_probability.items] == [
        "US.AMZN",
        "US.MSFT",
    ]
    assert [row.symbol for row in forecast_probability.items] == ["US.AAPL"]
    assert executable_q10.total == 89
    assert capital_gain_q10.total == 89
    assert all(row.executable_q10 == -0.045 for row in executable_q10.items)
    assert all(row.capital_gain_q10 == -0.04 for row in capital_gain_q10.items)


def test_screen_combines_thresholds_and_orders_minimum_confidence(
    tmp_path: Path,
) -> None:
    write_valid_snapshot(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    snapshot = repository.resolve_snapshot(SnapshotQuery())

    combined = repository.screen(
        snapshot,
        ScreenQuery(
            min_probability=0.90,
            max_q10=-0.045,
            minimum_confidence=ScreenConfidenceFloor.MEDIUM,
        ),
    )
    minimum_low = repository.screen(
        snapshot,
        ScreenQuery(minimum_confidence=ScreenConfidenceFloor.LOW, page_size=100),
    )
    minimum_high = repository.screen(
        snapshot,
        ScreenQuery(minimum_confidence=ScreenConfidenceFloor.HIGH, page_size=100),
    )

    assert [row.symbol for row in combined.items] == ["US.AMZN", "US.MSFT"]
    assert minimum_low.total == 89
    assert all(row.confidence_level.value != "unavailable" for row in minimum_low.items)
    assert minimum_high.total == 0


def test_screen_sorts_confidence_by_fixed_rank_and_keeps_unavailable_last(
    tmp_path: Path,
) -> None:
    repository, snapshot = _materialize_confidence_sort_rows(tmp_path)

    ascending = repository.screen(
        snapshot,
        ScreenQuery(
            sort_key="confidence_level",
            sort_direction=SortDirection.ASC,
            page_size=100,
        ),
    )
    descending = repository.screen(
        snapshot,
        ScreenQuery(
            sort_key="confidence_level",
            sort_direction=SortDirection.DESC,
            page_size=100,
        ),
    )

    ascending_symbols = [row.symbol for row in ascending.items]
    descending_symbols = [row.symbol for row in descending.items]
    assert ascending.items[0].confidence_level.value == "low"
    assert ascending_symbols.index("US.MSFT") < ascending_symbols.index("US.AMZN")
    assert descending.items[0].confidence_level.value == "high"
    assert descending_symbols.index("US.MSFT") < descending_symbols.index("US.AAPL")
    assert [row.confidence_level.value for row in ascending.items[-3:]] == [
        "unavailable",
        "unavailable",
        "unavailable",
    ]
    assert [row.confidence_level.value for row in descending.items[-3:]] == [
        "unavailable",
        "unavailable",
        "unavailable",
    ]


def test_repository_uses_sort_allowlists_and_parameterized_filter_values(
    tmp_path: Path,
) -> None:
    write_valid_snapshot(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    snapshot = repository.resolve_snapshot(SnapshotQuery())
    with pytest.raises(ValidationError):
        ScreenQuery.model_validate(
            {"sort_key": "symbol; DROP TABLE screen", "page": 1, "page_size": 25}
        )
    page = repository.instruments(
        snapshot,
        InstrumentQuery(search="US.' OR TRUE --", page=1, page_size=25),
    )
    assert page.total == 0
    assert page.items == ()


def test_overview_excludes_unavailable_stale_and_effective_abstain_rows(
    tmp_path: Path,
) -> None:
    write_valid_snapshot(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    snapshot = repository.resolve_snapshot(SnapshotQuery())
    overview = repository.overview(snapshot, 5)
    assert tuple(proxy.symbol for proxy in overview.proxies) == (
        "US.SPY",
        "US.QQQ",
        "US.IWM",
        "US.DIA",
    )
    assert len(overview.regime_distribution) == 5
    assert sum(item.count for item in overview.regime_distribution) == 100
    assert len({item.sector for item in overview.sector_regime_matrix}) == 5
    assert overview.top_opportunities
    assert all(not row.effective_abstain for row in overview.top_opportunities)
    assert all(
        row.serving_status
        not in {
            ServingStatus.STALE,
            ServingStatus.OFFLINE,
            ServingStatus.UNAVAILABLE,
        }
        for row in overview.top_opportunities
    )


def test_repository_returns_canonical_models_for_all_page_datasets(
    tmp_path: Path,
) -> None:
    write_valid_snapshot(tmp_path)
    repository = DuckDbResearchRepository(tmp_path)
    snapshot = repository.resolve_snapshot(SnapshotQuery())

    instruments = repository.instruments(
        snapshot,
        InstrumentQuery(
            page=1,
            page_size=100,
            sort_key="symbol",
            sort_direction=SortDirection.ASC,
        ),
    )
    assert instruments.total == 100
    assert len(instruments.items) == 100
    available = repository.forecast(snapshot, "US.AAPL")
    proxy_available = repository.forecast(snapshot, "US.SPY")
    post_abstain = repository.forecast(snapshot, "US.POST")
    unavailable = repository.forecast(snapshot, "US.PRE")
    assert isinstance(available, ForecastAvailable)
    assert isinstance(proxy_available, ForecastAvailable)
    assert isinstance(post_abstain, ForecastAvailable)
    assert post_abstain.serving.effective_abstain
    assert isinstance(unavailable, ForecastUnavailable)
    assert unavailable.not_generated_reason == "no_champion_candidate"
    assert repository.history(snapshot, "US.AAPL", HistoryQuery()).bars
    assert repository.backtests(snapshot, BacktestQuery()).sample_count == 500
    assert len(repository.models(snapshot, ModelQuery()).rows) == 2
    assert (
        repository.data_health(snapshot, DataHealthQuery(page=1, page_size=25)).total
        == 1
    )
    assert repository.universes(snapshot).versions[0].member_count == 100


def test_backtests_resolve_exact_physical_identity_and_project_one_fold(
    tmp_path: Path,
) -> None:
    repository, snapshot = _materialize_backtest_rows(tmp_path)

    selected = repository.backtests(
        snapshot,
        BacktestQuery(
            model_version="model-v1",
            strategy_version="strategy-v1",
            universe_version="universe-v1",
            split_version="split-v1",
            task="forecast_direction",
            horizon=5,
            asset_group="us_equity",
            evaluation_window_id="outer-window-v1",
            cost_scenario="base_costs",
            fold_id="outer-1",
        ),
    )

    assert selected.split_version == "split-v1"
    assert tuple(item.fold_id for item in selected.fold_metrics) == ("outer-1",)
    assert {point.fold_id for point in selected.economic_series} == {"outer-1"}


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("split_version", "split-missing"),
        ("task", "missing_task"),
        ("horizon", 20),
        ("asset_group", "missing_group"),
        ("fold_id", "missing-fold"),
    ),
)
def test_backtests_never_fall_back_from_a_missing_exact_identity(
    tmp_path: Path, field: str, value: object
) -> None:
    repository, snapshot = _materialize_backtest_rows(tmp_path)
    values: dict[str, object] = {
        "split_version": "split-v1",
        "task": "forecast_direction",
        "horizon": 5,
        "asset_group": "us_equity",
        "fold_id": "outer-1",
    }
    values[field] = value

    with pytest.raises(SnapshotNotFound):
        repository.backtests(
            snapshot,
            BacktestQuery.model_validate(values, strict=True),
        )


def test_valid_fixture_forecasts_cover_the_verified_universe_exactly_once() -> None:
    tables = make_snapshot_tables()
    instrument_symbols = [
        row["symbol"] for row in tables["instruments.parquet"].to_pylist()
    ]
    forecast_symbols = [
        row["symbol"] for row in tables["forecasts.parquet"].to_pylist()
    ]

    assert len(forecast_symbols) == 100
    assert len(forecast_symbols) == len(set(forecast_symbols))
    assert set(forecast_symbols) == set(instrument_symbols)


@pytest.mark.parametrize(
    "corruption",
    ("empty", "partial", "duplicate", "orphan", "payload_mismatch"),
)
def test_snapshot_reference_rejects_invalid_forecast_universe_relations(
    tmp_path: Path, corruption: str
) -> None:
    tables = make_snapshot_tables()
    forecast_rows = _all_unavailable_forecast_rows(tables)
    if corruption == "empty":
        forecast_rows = []
    elif corruption == "partial":
        forecast_rows.pop()
    elif corruption == "duplicate":
        forecast_rows.append(dict(forecast_rows[0]))
    elif corruption == "orphan":
        orphan = dict(forecast_rows[0])
        orphan_payload = json.loads(str(orphan["payload_json"]))
        orphan_payload["symbol"] = "US.ORPHAN"
        orphan["symbol"] = "US.ORPHAN"
        orphan["payload_json"] = json.dumps(orphan_payload)
        forecast_rows.append(orphan)
    else:
        mismatched = dict(forecast_rows[0])
        mismatched_payload = json.loads(str(mismatched["payload_json"]))
        mismatched_payload["symbol"] = "US.MSFT"
        mismatched["payload_json"] = json.dumps(mismatched_payload)
        forecast_rows[0] = mismatched

    repository, snapshot = _materialize_forecast_rows(tmp_path, forecast_rows)

    with pytest.raises(SnapshotIntegrityError):
        repository.snapshot_serving_reference(snapshot)


def test_single_symbol_forecast_rejects_row_key_payload_identity_mismatch(
    tmp_path: Path,
) -> None:
    tables = make_snapshot_tables()
    forecast_rows = _all_unavailable_forecast_rows(tables)
    mismatched_payload = json.loads(str(forecast_rows[0]["payload_json"]))
    mismatched_payload["symbol"] = "US.MSFT"
    forecast_rows[0]["payload_json"] = json.dumps(mismatched_payload)
    repository, snapshot = _materialize_forecast_rows(tmp_path, forecast_rows)

    with pytest.raises(SnapshotIntegrityError):
        repository.forecast(snapshot, "US.AAPL")


def test_forecast_rejects_orphan_payload_outside_resolved_universe(
    tmp_path: Path,
) -> None:
    snapshot = resolved_snapshot()
    tables = make_snapshot_tables(snapshot)
    forecast_rows = tables["forecasts.parquet"].to_pylist()
    orphan_payload = json.loads(forecast_rows[0]["payload_json"])
    orphan_payload["bundle"]["symbol"] = "US.ORPHAN"
    forecast_rows.append(
        {
            "symbol": "US.ORPHAN",
            "availability": "available",
            "payload_json": json.dumps(orphan_payload),
        }
    )
    tables["forecasts.parquet"] = pa.Table.from_pylist(
        forecast_rows,
        schema=tables["forecasts.parquet"].schema,
    )
    materialize_research_snapshot(
        tmp_path,
        snapshot=snapshot,
        generated_at=GENERATED_AT,
        tables=tables,
        champion_exists=True,
    )
    repository = DuckDbResearchRepository(tmp_path)

    with pytest.raises(SnapshotIntegrityError):
        repository.forecast(snapshot, "US.ORPHAN", 5)


def test_unavailable_repository_never_falls_back_to_fixture_data() -> None:
    repository = UnavailableResearchRepository()
    with pytest.raises(SnapshotNotFound, match="research snapshot unavailable"):
        repository.resolve_snapshot(SnapshotQuery())


@pytest.mark.parametrize(
    "snapshot_id",
    ("snapshot-safe", "COM10", "LPT0", "snapshot.trailing-name"),
)
def test_exact_and_latest_resolution_agree_for_publishable_identity(
    tmp_path: Path, snapshot_id: str
) -> None:
    write_valid_snapshot(tmp_path, snapshot_id=snapshot_id)
    repository = DuckDbResearchRepository(tmp_path)
    exact = repository.resolve_snapshot(SnapshotQuery(snapshot_id=snapshot_id))
    latest = repository.resolve_snapshot(SnapshotQuery())
    assert exact == latest


def test_repository_rejects_snapshot_directory_reparse_escape(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    outside = tmp_path / "outside"
    root.mkdir()
    target = write_valid_snapshot(outside)
    link = root / target.name
    _symlink_or_skip(link, target, target_is_directory=True)
    repository = DuckDbResearchRepository(root)
    with pytest.raises(SnapshotIntegrityError, match="reparse|repository root"):
        repository.resolve_snapshot(SnapshotQuery(snapshot_id=target.name))
    with pytest.raises(SnapshotIntegrityError, match="reparse|repository root"):
        repository.resolve_snapshot(SnapshotQuery())


def test_repository_rejects_reparse_manifest(tmp_path: Path) -> None:
    snapshot = write_valid_snapshot(tmp_path)
    manifest = snapshot / "manifest.json"
    external = tmp_path / "external-manifest.json"
    shutil.copyfile(manifest, external)
    manifest.unlink()
    _symlink_or_skip(manifest, external)
    with pytest.raises(SnapshotIntegrityError, match="manifest.json"):
        DuckDbResearchRepository(tmp_path).resolve_snapshot(SnapshotQuery())


def test_repository_rejects_reparse_table_even_when_target_stays_inside_snapshot(
    tmp_path: Path,
) -> None:
    snapshot = write_valid_snapshot(tmp_path)
    table = snapshot / "screen.parquet"
    target = snapshot / "screen.real.parquet"
    table.rename(target)
    _symlink_or_skip(table, target)
    with pytest.raises(SnapshotIntegrityError, match="screen.parquet"):
        DuckDbResearchRepository(tmp_path).resolve_snapshot(SnapshotQuery())


def test_query_reads_verified_copy_when_source_is_swapped_after_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stock_prediction.serving import repository as repository_module

    snapshot_path = write_valid_snapshot(tmp_path)
    source = snapshot_path / "forecasts.parquet"
    repository = DuckDbResearchRepository(tmp_path)
    snapshot = repository.resolve_snapshot(SnapshotQuery())
    real_connection = repository_module._connection
    mutated = False

    class MutatingConnection:
        def __init__(self) -> None:
            self.connection = real_connection()

        def __enter__(self) -> "MutatingConnection":
            return self

        def __exit__(self, *args: object) -> None:
            self.connection.close()

        def execute(self, query: str, parameters: list[object] | None = None) -> Any:
            nonlocal mutated
            reads_forecast_copy = (
                parameters is not None
                and bool(parameters)
                and Path(str(parameters[0])).name == "forecasts.parquet"
            )
            if "read_parquet" in query and reads_forecast_copy and not mutated:
                source.write_bytes(b"unverified replacement")
                mutated = True
            return self.connection.execute(query, parameters or [])

    monkeypatch.setattr(repository_module, "_connection", MutatingConnection)
    result = repository.forecast(snapshot, "US.AAPL")
    assert isinstance(result, ForecastAvailable)
    assert result.bundle.symbol == "US.AAPL"
    assert mutated
