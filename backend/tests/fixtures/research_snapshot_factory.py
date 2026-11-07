"""Deterministic, test-only M5 research snapshot fixtures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa

from backend.tests.contracts.test_serving import (
    valid_backtest_read_model,
    valid_forecast_bundle_payload,
    valid_screen_row,
)
from stock_prediction.contracts.health import (
    Completeness,
    Connectivity,
    Freshness,
    Readiness,
)
from stock_prediction.contracts.research import RegimeLabel
from stock_prediction.contracts.serving import (
    DataHealth,
    ForecastAvailable,
    ForecastBundle,
    ForecastUnavailable,
    HistoryBar,
    HistoryEvent,
    InstrumentMetadata,
    MarketProxy,
    ModelTaskMatrixRow,
    OverviewReadModel,
    RegimeDistribution,
    RejectionReasonCount,
    ResolvedSnapshot,
    ScreenRow,
    SectorRegimeCell,
    ServingOverlay,
    ServingStatus,
    UniverseVersion,
)
from stock_prediction.modeling.confidence import ConfidenceLevel, RejectionReason
from stock_prediction.serving.manifest import RESEARCH_TABLE_SCHEMAS
from stock_prediction.serving.materialize import materialize_research_snapshot

FRESH_AS_OF = datetime(2026, 7, 10, 20, tzinfo=UTC)
STALE_AS_OF = datetime(2026, 7, 9, 20, tzinfo=UTC)
GENERATED_AT = datetime(2026, 7, 10, 20, 2, tzinfo=UTC)
SYMBOLS = (
    "US.SPY",
    "US.AAPL",
    "US.MSFT",
    "US.AMZN",
    "US.POST",
    "US.PRE",
    *(f"US.S{index:03d}" for index in range(94)),
)
SECTORS = ("technology", "communication_services", "consumer", "financials", "healthcare")
REGIME_BUCKETS = (
    (RegimeLabel.UPTREND, 31),
    (RegimeLabel.RANGE, 28),
    (RegimeLabel.TRANSITION, 20),
    (RegimeLabel.DOWNTREND, 12),
    (RegimeLabel.INVALID, 9),
)


def _sector_for_index(index: int) -> str:
    return SECTORS[index % len(SECTORS)]


def _regime_for_index(index: int) -> RegimeLabel:
    boundary = 0
    for regime, count in REGIME_BUCKETS:
        boundary += count
        if index < boundary:
            return regime
    raise ValueError("fixture regime index is outside the 100-symbol universe")


def resolved_snapshot(
    *,
    snapshot_id: str = "snapshot-20260710-r1",
    as_of: datetime = FRESH_AS_OF,
    revision_no: int = 1,
    model_version: str = "model-v1",
) -> ResolvedSnapshot:
    return ResolvedSnapshot(
        snapshot_id=snapshot_id,
        revision_no=revision_no,
        as_of=as_of,
        dataset_revision="dataset-r1",
        feature_set_version="features-v1",
        universe_version="universe-v1",
        model_version=model_version,
        calibration_version="calibration-v1",
    )


def make_snapshot_tables(
    snapshot: ResolvedSnapshot | None = None,
    *,
    stale: bool = False,
) -> dict[str, pa.Table]:
    snapshot = snapshot or resolved_snapshot()
    instruments = tuple(_instrument(symbol, snapshot) for symbol in SYMBOLS)
    screen_rows = tuple(
        _screen_row(symbol, index, snapshot, stale=stale)
        for index, symbol in enumerate(SYMBOLS)
    )
    overview = _overview(snapshot, screen_rows, stale=stale)
    forecasts = tuple(
        _post_model_abstain_forecast(symbol, snapshot)
        if symbol == "US.POST"
        else _unavailable_forecast(symbol, snapshot)
        if symbol == "US.PRE"
        else _available_forecast(symbol, snapshot)
        for symbol in SYMBOLS
    )
    bar = HistoryBar(
        at=snapshot.as_of - timedelta(days=1),
        open=218.0,
        high=222.0,
        low=217.0,
        close=220.0,
        volume=10_000_000,
        regime=RegimeLabel.RANGE,
        data_gap=False,
    )
    event = HistoryEvent(
        event_id="event-aapl-1",
        occurred_at=snapshot.as_of - timedelta(hours=1),
        event_type="earnings",
        label="Quarterly earnings",
        details=("Test fixture event",),
    )
    backtest = valid_backtest_read_model().model_copy(
        update={
            "model_version": snapshot.model_version,
            "universe_version": snapshot.universe_version,
        }
    )
    models = (
        ModelTaskMatrixRow(
            task="forecast_direction",
            horizon=5,
            asset_group="us_equity",
            champion_version=snapshot.model_version,
            baseline_version="baseline-v1",
            challenger_version="challenger-v1",
            calibration_version=snapshot.calibration_version,
            status="champion",
            unavailable_reason=None,
        ),
        ModelTaskMatrixRow(
            task="range_exit",
            horizon=20,
            asset_group="us_equity",
            champion_version=None,
            baseline_version="baseline-v1",
            challenger_version=None,
            calibration_version=None,
            status="unavailable",
            unavailable_reason="no_champion_candidate",
        ),
    )
    health = (
        DataHealth(
            component="collector",
            dataset="daily_bars",
            symbol=None,
            serving_status=ServingStatus.LIVE,
            connectivity=Connectivity.CONNECTED,
            freshness=Freshness.CURRENT,
            completeness=Completeness.COMPLETE,
            readiness=Readiness.READY,
            last_success_at=snapshot.as_of,
            expected_next_at=snapshot.as_of + timedelta(minutes=5),
            coverage_rate=1.0,
            missing_rate=0.0,
            duplicate_count=0,
            quarantine_count=0,
            watermark=snapshot.as_of,
            quota_total=1000,
            quota_used=100,
            quota_remaining=900,
            error_class=None,
            message=None,
            action=None,
            affected_symbols=(),
            affected_datasets=(),
        ),
    )
    universes = (
        UniverseVersion(
            universe_version=snapshot.universe_version,
            effective_from=datetime(2026, 1, 2, tzinfo=UTC),
            effective_to=None,
            member_count=100,
            reason="deterministic fixture universe",
            point_in_time_status="proven",
        ),
    )
    return {
        "overview.parquet": _fixture_table(
            "overview.parquet",
            ({"horizon": 5, "payload_json": overview.model_dump_json()},),
        ),
        "instruments.parquet": _fixture_table(
            "instruments.parquet",
            tuple(
                {
                    "symbol": instrument.symbol,
                    "name": instrument.name,
                    "asset_type": instrument.asset_type,
                    "sector": instrument.sector,
                    "payload_json": instrument.model_dump_json(),
                }
                for instrument in instruments
            ),
        ),
        "screen.parquet": _fixture_table(
            "screen.parquet",
            tuple(
                {
                    "symbol": row.symbol,
                    "horizon": row.horizon,
                    "asset_type": row.asset_type,
                    "sector": row.sector,
                    "regime": row.regime.value,
                    "executable_up_probability": row.executable_up_probability,
                    "breakout_up_probability": row.breakout_up_probability,
                    "breakout_down_probability": row.breakout_down_probability,
                    "executable_q50": row.executable_q50,
                    "capital_gain_q50": row.capital_gain_q50,
                    "interval_width": row.interval_width,
                    "similar_case_count": row.similar_case_count,
                    "confidence_level": row.confidence_level.value,
                    "data_freshness_seconds": row.data_freshness_seconds,
                    "serving_status": row.serving_status.value,
                    "effective_abstain": row.effective_abstain,
                    "payload_json": row.model_dump_json(),
                }
                for row in screen_rows
            ),
        ),
        "forecasts.parquet": _fixture_table(
            "forecasts.parquet",
            tuple(
                {
                    "symbol": result.bundle.symbol
                    if isinstance(result, ForecastAvailable)
                    else result.symbol,
                    "availability": result.availability,
                    "payload_json": result.model_dump_json(),
                }
                for result in forecasts
            ),
        ),
        "history_bars.parquet": _fixture_table(
            "history_bars.parquet",
            (
                {
                    "symbol": "US.AAPL",
                    "timeframe": "1d",
                    "adjustment": "split_adjusted",
                    "at": bar.at,
                    "payload_json": bar.model_dump_json(),
                },
            ),
        ),
        "history_events.parquet": _fixture_table(
            "history_events.parquet",
            (
                {
                    "symbol": "US.AAPL",
                    "timeframe": "1d",
                    "adjustment": "split_adjusted",
                    "occurred_at": event.occurred_at,
                    "payload_json": event.model_dump_json(),
                },
            ),
        ),
        "similar_cases.parquet": _fixture_table("similar_cases.parquet", ()),
        "backtests.parquet": _fixture_table(
            "backtests.parquet",
            (
                {
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
                },
            ),
        ),
        "models.parquet": _fixture_table(
            "models.parquet",
            tuple(
                {
                    "task": row.task,
                    "horizon": row.horizon,
                    "asset_group": row.asset_group,
                    "payload_json": row.model_dump_json(),
                }
                for row in models
            ),
        ),
        "data_health.parquet": _fixture_table(
            "data_health.parquet",
            tuple(
                {
                    "component": row.component,
                    "dataset": row.dataset,
                    "symbol": row.symbol,
                    "serving_status": row.serving_status.value,
                    "payload_json": row.model_dump_json(),
                }
                for row in health
            ),
        ),
        "universes.parquet": _fixture_table(
            "universes.parquet",
            tuple(
                {
                    "universe_version": row.universe_version,
                    "effective_from": row.effective_from,
                    "payload_json": row.model_dump_json(),
                }
                for row in universes
            ),
        ),
    }


def _fixture_table(
    path: str, rows: tuple[dict[str, object], ...]
) -> pa.Table:
    return pa.Table.from_pylist(list(rows), schema=RESEARCH_TABLE_SCHEMAS[path])


def write_valid_snapshot(
    root: Path,
    *,
    snapshot_id: str = "snapshot-20260710-r1",
    as_of: datetime = FRESH_AS_OF,
    revision_no: int = 1,
    model_version: str = "model-v1",
    stale: bool = False,
) -> Path:
    snapshot = resolved_snapshot(
        snapshot_id=snapshot_id,
        as_of=as_of,
        revision_no=revision_no,
        model_version=model_version,
    )
    result = materialize_research_snapshot(
        root,
        snapshot=snapshot,
        generated_at=max(GENERATED_AT, as_of),
        tables=make_snapshot_tables(snapshot, stale=stale),
        champion_exists=True,
    )
    assert result.published_path is not None
    return result.published_path


def write_snapshot_set(root: Path) -> tuple[Path, Path]:
    stale = write_valid_snapshot(
        root,
        snapshot_id="snapshot-20260709-r1",
        as_of=STALE_AS_OF,
        stale=True,
    )
    fresh = write_valid_snapshot(root)
    return stale, fresh


def _instrument(symbol: str, snapshot: ResolvedSnapshot) -> InstrumentMetadata:
    index = SYMBOLS.index(symbol)
    return InstrumentMetadata(
        symbol=symbol,
        name=f"Fixture {symbol.removeprefix('US.')}",
        asset_type="stock",
        sector=_sector_for_index(index),
        currency="USD",
        exchange="NASDAQ",
        effective_from=datetime(2026, 1, 2, tzinfo=UTC),
        effective_to=None,
        universe_version=snapshot.universe_version,
    )


def _screen_row(
    symbol: str,
    index: int,
    snapshot: ResolvedSnapshot,
    *,
    stale: bool,
) -> ScreenRow:
    base = valid_screen_row().model_dump(mode="python")
    base.update(
        symbol=symbol,
        name=f"Fixture {symbol.removeprefix('US.')}",
        model_version=snapshot.model_version,
        as_of=snapshot.as_of,
        regime=_regime_for_index(index),
        sector=_sector_for_index(index),
        executable_up_probability=(
            0.90 if symbol in {"US.AMZN", "US.MSFT"} else 0.50 - index / 1000
        ),
        forecast_up_probability=0.99 if symbol == "US.AAPL" else 0.56,
        executable_q50=0.03 if symbol == "US.AMZN" else 0.01,
        capital_gain_q50=0.04 if symbol == "US.MSFT" else 0.02,
    )
    reason: RejectionReason | None = None
    status = ServingStatus.LIVE
    if stale or symbol == "US.S092":
        reason = RejectionReason.STALE_DATA
        status = ServingStatus.STALE
    elif symbol == "US.POST":
        reason = RejectionReason.OOD
    elif symbol == "US.PRE":
        reason = RejectionReason.NO_CHAMPION_CANDIDATE
        status = ServingStatus.UNAVAILABLE
    elif _regime_for_index(index) == RegimeLabel.INVALID:
        reason = RejectionReason.DATA_QUALITY
        status = ServingStatus.PARTIAL
    if reason is not None:
        for field in (
            "executable_up_probability",
            "breakout_up_probability",
            "breakout_down_probability",
            "executable_q50",
            "executable_q10",
            "forecast_up_probability",
            "capital_gain_q50",
            "capital_gain_q10",
            "interval_width",
        ):
            base[field] = None
        base.update(
            confidence_level=ConfidenceLevel.UNAVAILABLE,
            serving_status=status,
            effective_abstain=True,
            abstain_reasons=(reason,),
        )
    return ScreenRow.model_validate(base, strict=True)


def _overview(
    snapshot: ResolvedSnapshot,
    rows: tuple[ScreenRow, ...],
    *,
    stale: bool,
) -> OverviewReadModel:
    abstain_rows = tuple(row for row in rows if row.effective_abstain)
    reason_counts = {
        reason: sum(reason in row.abstain_reasons for row in abstain_rows)
        for reason in RejectionReason
    }
    return OverviewReadModel(
        proxies=(
            MarketProxy(
                symbol="US.SPY",
                name="Fixture SPY proxy",
                regime=RegimeLabel.RANGE,
                regime_probability=None if stale else 0.64,
                direction=None if stale else "up",
                unavailable_reason="stale_snapshot" if stale else None,
                session_return=0.0048,
                trend=(600.0, 602.0, 601.5, 604.0),
                as_of=snapshot.as_of,
                serving_status=ServingStatus.STALE if stale else ServingStatus.LIVE,
            ),
            MarketProxy(
                symbol="US.QQQ",
                name="Fixture QQQ proxy",
                regime=RegimeLabel.RANGE,
                regime_probability=None if stale else 0.62,
                direction=None if stale else "up",
                unavailable_reason="stale_snapshot" if stale else None,
                session_return=0.0032,
                trend=(520.0, 521.5, 521.0, 523.0),
                as_of=snapshot.as_of,
                serving_status=ServingStatus.STALE if stale else ServingStatus.LIVE,
            ),
            MarketProxy(
                symbol="US.IWM",
                name="Fixture IWM proxy",
                regime=RegimeLabel.TRANSITION,
                regime_probability=None if stale else 0.44,
                direction=None if stale else "down",
                unavailable_reason="stale_snapshot" if stale else None,
                session_return=-0.0024,
                trend=(225.0, 224.0, 224.5, 223.5),
                as_of=snapshot.as_of,
                serving_status=ServingStatus.STALE if stale else ServingStatus.LIVE,
            ),
            MarketProxy(
                symbol="US.DIA",
                name="Fixture DIA proxy",
                regime=RegimeLabel.UPTREND,
                regime_probability=None if stale else 0.56,
                direction=None if stale else "up",
                unavailable_reason="stale_snapshot" if stale else None,
                session_return=0.0029,
                trend=(445.0, 446.0, 445.5, 447.0),
                as_of=snapshot.as_of,
                serving_status=ServingStatus.STALE if stale else ServingStatus.LIVE,
            ),
        ),
        top_opportunities=(),
        regime_distribution=tuple(
            RegimeDistribution(regime=regime, count=count, fraction=count / 100)
            for regime, count in REGIME_BUCKETS
        ),
        sector_regime_matrix=tuple(
            SectorRegimeCell(
                sector=sector,
                regime=regime,
                count=sum(
                    row.sector == sector and row.regime == regime for row in rows
                ),
                fraction=sum(
                    row.sector == sector and row.regime == regime for row in rows
                )
                / sum(row.sector == sector for row in rows),
            )
            for sector in SECTORS
            for regime, _count in REGIME_BUCKETS
        ),
        market_breadth=0.52,
        abstain_count=len(abstain_rows),
        abstain_reasons=tuple(
            RejectionReasonCount(reason=reason, count=count)
            for reason, count in reason_counts.items()
            if count
        ),
        data_confidence=0.0 if stale else 0.95,
        batch_generated_at=max(GENERATED_AT, snapshot.as_of),
        total_inference_latency_ms=410.0,
    )


def _available_forecast(
    symbol: str, snapshot: ResolvedSnapshot
) -> ForecastAvailable:
    payload = valid_forecast_bundle_payload()
    evidence = payload["evidence"]
    assert hasattr(evidence, "model_copy")
    payload["evidence"] = evidence.model_copy(  # type: ignore[union-attr]
        update={"evidence_cutoff": snapshot.as_of}
    )
    payload["horizons"] = tuple(
        horizon.model_copy(update={"reference_price_at": snapshot.as_of})
        for horizon in payload["horizons"]  # type: ignore[union-attr]
    )
    payload.update(
        symbol=symbol,
        snapshot_id=snapshot.snapshot_id,
        revision_no=snapshot.revision_no,
        as_of=snapshot.as_of,
        dataset_revision=snapshot.dataset_revision,
        feature_set_version=snapshot.feature_set_version,
        universe_version=snapshot.universe_version,
        model_version=snapshot.model_version,
        calibration_version=snapshot.calibration_version,
        generated_at=max(GENERATED_AT, snapshot.as_of),
        valid_until=max(GENERATED_AT, snapshot.as_of) + timedelta(days=3),
    )
    bundle = ForecastBundle.model_validate(payload, strict=True)
    return ForecastAvailable(
        bundle=bundle,
        serving=ServingOverlay(
            serving_status=ServingStatus.LIVE,
            effective_abstain=False,
            evaluated_at=bundle.generated_at,
            exclusion_reason=(),
        ),
        evidence=bundle.evidence,
        similar_cases=(),
    )


def _post_model_abstain_forecast(
    symbol: str, snapshot: ResolvedSnapshot
) -> ForecastAvailable:
    available = _available_forecast(symbol, snapshot)
    payload = available.bundle.model_dump(mode="python")
    payload.update(
        confidence_level=ConfidenceLevel.UNAVAILABLE,
        confidence_score=0.0,
        abstain=True,
        abstain_reasons=(RejectionReason.OOD,),
    )
    bundle = ForecastBundle.model_validate(payload, strict=True)
    return ForecastAvailable(
        bundle=bundle,
        serving=ServingOverlay(
            serving_status=ServingStatus.LIVE,
            effective_abstain=True,
            evaluated_at=bundle.generated_at,
            exclusion_reason=(),
        ),
        evidence=bundle.evidence,
        similar_cases=(),
    )


def _unavailable_forecast(
    symbol: str, snapshot: ResolvedSnapshot
) -> ForecastUnavailable:
    return ForecastUnavailable(
        symbol=symbol,
        as_of=snapshot.as_of,
        serving_status=ServingStatus.UNAVAILABLE,
        not_generated_reason="no_champion_candidate",
        affected_inputs=("model_registry",),
        last_available_snapshot=None,
    )
