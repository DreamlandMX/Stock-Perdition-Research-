from __future__ import annotations

from datetime import UTC, date, datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import duckdb
import pytest

from stock_prediction.contracts.research import RegimeLabel
from stock_prediction.contracts.serving import (
    ResearchEvidenceScope,
    ResearchModelEvidence,
    ResearchModelValues,
    ReturnQuantiles,
    RuleEvidence,
    ScreenRow,
    ServingStatus,
    TaskForecastEvidence,
)
from stock_prediction.live.inference import ResearchTaskPrediction
from stock_prediction.live import publication
from stock_prediction.live.publication import (
    LiveSnapshotPublisher,
    _proxy_trends,
    _resolve_formal_publication_identity,
    _rule_evidence,
    _verified_research_evidence,
    _research_opportunities,
)
from stock_prediction.modeling.confidence import ConfidenceLevel


def test_abstaining_forecast_keeps_bundle_but_omits_screen_ranking_values() -> None:
    from backend.tests.contracts.test_serving import (
        horizon_forecast,
        valid_resolved_snapshot,
        valid_similar_case_summary,
    )
    from stock_prediction.modeling.confidence import RejectionReason

    forecast = SimpleNamespace(
        bundle=SimpleNamespace(
            horizons=(horizon_forecast(5),),
            similar_case_summary=valid_similar_case_summary(),
            confidence_level=ConfidenceLevel.LOW,
            abstain_reasons=(RejectionReason.OOD,),
        ),
        serving=SimpleNamespace(effective_abstain=True),
    )
    row = publication._screen_row(
        "US.AAPL", 5, {"regime": "range", "close": 100.0},
        valid_resolved_snapshot(), forecast, etfs=(),
    )
    assert row.effective_abstain
    assert row.abstain_reasons == (RejectionReason.OOD,)
    assert row.executable_up_probability is None
    assert row.capital_gain_q50 is None
    assert row.interval_width is None
    assert forecast.bundle.horizons[0].capital_gain_return_quantiles.q50 is not None


def test_data_only_model_rows_without_report_use_no_champion_reason() -> None:
    from stock_prediction.contracts.serving import PredictionReadiness

    rows = publication._model_rows(
        SimpleNamespace(prediction_readiness=PredictionReadiness.DATA_ONLY,
                        model_version=None, calibration_version=None),
        SimpleNamespace(challenger_versions=()),
        None,
    )
    assert len(rows) == 11
    assert all(row.champion_version is None and row.calibration_version is None
               and row.gate_passed is not True
               and row.unavailable_reason == "no_champion_candidate" for row in rows)


def test_health_discloses_retained_historical_gap(tmp_path: Path) -> None:
    (tmp_path / "validation.json").write_text(json.dumps({
        "opend_volume_validation": {"retained_source_gaps": [
            {"symbol": "US.BMNR", "session_date": "2025-05-16"}
        ]}
    }), encoding="utf-8")
    publisher = object.__new__(LiveSnapshotPublisher)
    publisher.paths = SimpleNamespace(root=tmp_path)
    publisher.universe = SimpleNamespace(symbols=("US.BMNR",))
    publisher.storage = SimpleNamespace(
        latest_health=lambda: {"status": "ok"},
        coverage=lambda: [{"symbol": "US.BMNR", "timeframe": timeframe,
                           "max_session_date": "2026-09-30", "last_ingested_at": None}
                          for timeframe in ("1d", "60m")],
        latest_task_states=lambda: [], rehab_coverage=lambda: [],
    )
    rows = publisher._health_rows(SimpleNamespace(as_of=datetime(2026, 9, 30, 20, tzinfo=UTC)))
    minute = next(row for row in rows if row.dataset == "bars_60m")
    assert minute.freshness.value == "current"
    assert minute.completeness.value == "partial"
    assert minute.coverage_rate is None and minute.missing_rate is None
    assert minute.error_class == "historical_source_gap"
    assert "1 个历史交易日" in minute.message


def test_history_events_use_latest_eligible_snapshot_including_empty(tmp_path: Path) -> None:
    catalog = tmp_path / "actions.duckdb"
    with duckdb.connect(str(catalog)) as connection:
        connection.execute("CREATE TABLE rehab_snapshots(snapshot_id VARCHAR, symbol VARCHAR, observed_at TIMESTAMPTZ)")
        connection.execute("CREATE TABLE rehab_observations(raw_batch_id VARCHAR, symbol VARCHAR, ex_div_date DATE, split_ratio DOUBLE, per_cash_div DOUBLE)")
        connection.execute("INSERT INTO rehab_snapshots VALUES ('old','US.NVDA','2026-08-01'),('empty','US.NVDA','2026-08-03'),('future','US.NVDA','2026-08-05')")
        connection.execute("INSERT INTO rehab_observations VALUES ('old','US.NVDA','2024-06-10',0.1,NULL),('old','US.NVDA','2024-06-11',NULL,0.01),('future','US.NVDA','2024-06-10',0.5,NULL)")
        connection.execute("INSERT INTO rehab_observations VALUES ('old','US.NVDA','2026-08-02',NULL,0.02)")
    publisher = object.__new__(LiveSnapshotPublisher)
    publisher.paths = SimpleNamespace(catalog=catalog)
    publisher.universe = SimpleNamespace(symbols=("US.NVDA",))
    events = list(publisher._history_event_rows(datetime(2026, 8, 2, tzinfo=UTC)))
    assert len(events) == 2
    payloads = [json.loads(row["payload_json"]) for row in events]
    assert "10 股" in payloads[0]["label"]
    assert payloads[0]["occurred_at"] == "2024-06-10T04:00:00Z"
    assert "0.01" in payloads[1]["label"]
    assert "回溯" in payloads[0]["details"][2]
    assert all(row["adjustment"] == "raw" for row in events)
    effective = list(publisher._history_event_rows(datetime(2026, 8, 2, 4, tzinfo=UTC)))
    assert len(effective) == 3
    assert json.loads(effective[-1]["payload_json"])["occurred_at"] == "2026-08-02T04:00:00Z"
    assert list(publisher._history_event_rows(datetime(2026, 8, 4, tzinfo=UTC))) == []
    assert list(publisher._history_event_rows(datetime(2024, 6, 12, tzinfo=UTC))) == []
    publisher.universe = SimpleNamespace(symbols=("US.SPY",))
    assert list(publisher._history_event_rows(datetime(2026, 8, 2, tzinfo=UTC))) == []


def test_active_m7_requires_current_formal_artifact_and_never_substitutes_legacy() -> (
    None
):
    champion = ("legacy-model", "legacy-cal", {})

    assert _resolve_formal_publication_identity(
        champion, active_identity=None, formal_available=False
    ) == (True, "legacy-model", "legacy-cal")
    assert _resolve_formal_publication_identity(
        champion,
        active_identity=("m7-forecast-active", "a" * 64),
        formal_available=False,
    ) == (False, None, None)
    assert _resolve_formal_publication_identity(
        champion,
        active_identity=("m7-forecast-active", "a" * 64),
        formal_available=True,
    ) == (True, "m7-forecast-active", "m7-cal-aaaaaaaaaaaaaaaa")


def _evidence(horizon: int, up: float, down: float) -> ResearchModelEvidence:
    return ResearchModelEvidence(
        scope=ResearchEvidenceScope.VERIFIED,
        symbol="US.SPY",
        horizon=horizon,
        as_of=datetime(2026, 7, 28, 20, tzinfo=UTC),
        values=ResearchModelValues(
            breakout_up_probability=up,
            breakout_down_probability=down,
        ),
        tasks=(
            TaskForecastEvidence(
                task="breakout",
                horizon=horizon,
                scope=ResearchEvidenceScope.VERIFIED,
                gate_passed=True,
                model_version=f"breakout-v{horizon}",
                calibration_version=None,
                evaluation_run_id=f"eval-{horizon}",
                fold_count=4,
                baseline_wins=4,
            ),
        ),
    )


def _screen_row(horizon: int, up: float, down: float) -> ScreenRow:
    evidence = _evidence(horizon, up, down)
    return ScreenRow(
        symbol="US.SPY",
        name="SPDR S&P 500 ETF",
        asset_type="etf",
        sector="broad_market",
        regime=RegimeLabel.RANGE,
        horizon=horizon,
        executable_up_probability=0.51,
        breakout_up_probability=up,
        breakout_down_probability=down,
        executable_q50=0.001,
        executable_q10=-0.02,
        forecast_up_probability=0.51,
        capital_gain_q50=0.001,
        capital_gain_q10=-0.02,
        interval_width=0.08,
        similar_case_count=24,
        confidence_level=ConfidenceLevel.MEDIUM,
        data_freshness_seconds=30,
        serving_status=ServingStatus.LIVE,
        effective_abstain=False,
        abstain_reasons=(),
        model_version=f"screen-v{horizon}",
        as_of=datetime(2026, 7, 28, 20, tzinfo=UTC),
        rule_evidence=RuleEvidence(
            last_close=738.93,
            session_return=0.001,
            return_5=0.004,
            return_20=0.03,
            atr_pct_20=0.02,
            channel_position_20=0.62,
            channel_width_20=0.05,
            volume_z_20=0.1,
            breakout_up_rule=False,
            breakout_down_rule=False,
            intraday_available=True,
        ),
        verified_research=evidence,
    )


def test_research_opportunity_preserves_rank_horizon_and_publishes_20_day_evidence() -> (
    None
):
    five_day = _screen_row(5, 0.61, 0.35)
    twenty_day = _screen_row(20, 0.477192, 0.471729)

    opportunity = _research_opportunities(
        (five_day,),
        all_rows=(five_day, twenty_day),
    )[0]

    assert opportunity.horizon == 5
    assert opportunity.verified_research == five_day.verified_research
    evidence_by_horizon = {
        evidence.horizon: evidence
        for evidence in opportunity.verified_research_by_horizon
    }
    assert set(evidence_by_horizon) == {5, 20}
    assert evidence_by_horizon[20].values.breakout_up_probability == 0.477192
    assert evidence_by_horizon[20].values.breakout_down_probability == 0.471729


class _HistoryConnection:
    def __enter__(self) -> "_HistoryConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, _query: str) -> "_HistoryConnection":
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return [
            (
                "US.SPY",
                "1d",
                datetime(2026, 7, 28),
                datetime(2026, 7, 28, 20, tzinfo=UTC),
                738.51,
                743.72,
                737.29,
                738.93,
                44_781_983,
            )
        ]


def test_history_regime_lookup_normalizes_duckdb_timestamp_to_date(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        publication.duckdb,
        "connect",
        lambda _path: _HistoryConnection(),
    )
    publisher = object.__new__(LiveSnapshotPublisher)
    publisher.paths = SimpleNamespace(catalog=tmp_path / "catalog.duckdb")

    rows = list(publisher._history_rows({("US.SPY", date(2026, 7, 28)): "range"}))
    payload = json.loads(str(rows[0]["payload_json"]))

    assert payload["regime"] == "range"


def test_published_market_prices_use_raw_quote_while_rules_use_adjusted_path() -> None:
    evidence = _rule_evidence(
        {
            "close": 844.03,
            "raw_close": 740.86,
            "channel_low_20": 800.0,
            "channel_high_20": 880.0,
            "return_1": 0.002,
        }
    )
    trends = _proxy_trends(
        pd.DataFrame(
            [
                {
                    "symbol": "US.SPY",
                    "session_date": date(2026, 7, 28),
                    "close": 844.03,
                    "raw_close": 740.86,
                }
            ]
        )
    )

    assert evidence.last_close == 740.86
    assert evidence.channel_position_20 == pytest.approx(0.550375)
    assert trends["US.SPY"] == (740.86,)


def _task_prediction(
    task: str,
    *,
    quantiles: ReturnQuantiles,
    horizon: int = 20,
) -> ResearchTaskPrediction:
    return ResearchTaskPrediction(
        task=task,
        horizon=horizon,
        symbol="US.SPY",
        model_version=f"{task}-{horizon}-champion",
        calibration_version=f"{task}-{horizon}-cal",
        evaluation_run_id=f"{task}-{horizon}-eval",
        component_evaluated_at=datetime(2026, 7, 28, 21, tzinfo=UTC),
        gate_metrics={
            "fold_count": 4,
            "pinball_wins": 4,
            "coverage_windows_passed": 4,
        },
        reference_price=740.86,
        reference_price_at=datetime(2026, 7, 28, 20, tzinfo=UTC),
        quantiles=quantiles,
        cqr_adjustment=0.01,
    )


def test_verified_research_price_projection_uses_independent_task_champions() -> None:
    evidence = _verified_research_evidence(
        (
            _task_prediction(
                "return_fwd",
                quantiles=ReturnQuantiles(
                    q10=-0.056865,
                    q25=-0.020000,
                    q50=0.007100,
                    q75=0.030000,
                    q90=0.059810,
                ),
            ),
            _task_prediction(
                "mfe",
                quantiles=ReturnQuantiles(
                    q10=0.010000,
                    q25=0.020000,
                    q50=0.045000,
                    q75=0.070000,
                    q90=0.090000,
                ),
            ),
        ),
        20,
    )

    assert evidence is not None
    projection = evidence.price_projection
    assert projection is not None
    assert projection.endpoint_price_band is not None
    assert projection.endpoint_price_band.q50 == pytest.approx(746.12, abs=0.01)
    assert projection.endpoint_price_band.q10 == pytest.approx(698.73, abs=0.01)
    assert projection.endpoint_price_band.q90 == pytest.approx(785.17, abs=0.01)
    assert projection.stage_high_price_q50 == pytest.approx(774.20, abs=0.01)
    assert projection.stage_low_price_q50 is None
    assert projection.stage_low_price_unavailable_reason == "mae_model_not_verified"
    assert {task.component_evaluated_at for task in evidence.tasks} == {
        datetime(2026, 7, 28, 21, tzinfo=UTC)
    }


def test_verified_research_price_projection_publishes_bands_for_all_horizons() -> None:
    quantiles = ReturnQuantiles(
        q10=-0.04,
        q25=-0.02,
        q50=0.01,
        q75=0.03,
        q90=0.05,
    )
    for horizon in (1, 5, 20):
        evidence = _verified_research_evidence(
            (_task_prediction("return_fwd", quantiles=quantiles, horizon=horizon),),
            horizon,
        )
        assert evidence is not None
        band = evidence.price_projection.endpoint_price_band
        assert band is not None
        assert band.q50 == pytest.approx(740.86 * 1.01)
        assert evidence.price_projection.endpoint_price_unavailable_reason is None
