from datetime import timedelta
import hashlib
import json

from stock_prediction.options.monitor import build_option_monitor, save_option_monitor
from stock_prediction.options.store import OptionSnapshotStore
from test_analytics import NOW, chain


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_intermediate_collection_persists_evidence_without_rebuilding_all_symbols(
    tmp_path, monkeypatch
):
    import stock_prediction.options.monitor as module
    from datetime import UTC, datetime

    at = datetime(2026, 10, 30, 20, tzinfo=UTC)
    store = OptionSnapshotStore(tmp_path)
    run = module.OptionCollectionRun(
        started_at=at,
        ended_at=at,
        trading_date=at.date(),
        status="failed",
        reason="permission_denied",
        contracts={"US.DIA": 0},
        complete={"US.DIA": False},
    )

    def unexpected(*args, **kwargs):
        raise AssertionError("intermediate item rebuilt all-symbol monitoring")

    monkeypatch.setattr(module, "build_option_monitor", unexpected)
    path = module.save_option_collection_run(run, store, refresh_monitor=False)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["collection_run"]["reason"] == "permission_denied"
    assert saved["collection_run"]["complete"] == {"US.DIA": False}
    assert saved["monitor_status"] == "pending"


def test_monitor_separates_stale_data_and_unpromoted_predictions_and_retained_attempt(
    tmp_path,
):
    store = OptionSnapshotStore(tmp_path / "options")
    store.save(chain())
    partial = chain(NOW + timedelta(hours=1), "partial").model_copy(
        update={"complete": False, "errors": ("provider_timeout",)}
    )
    store.save(partial)
    result = build_option_monitor(store, evaluated_at=NOW + timedelta(days=2))
    symbol = result["symbols"]["US.AAPL"]
    assert symbol["data_status"] == "stale"
    assert symbol["prediction_status"] == "baseline_fallback"
    assert symbol["latest_attempt"]["complete_snapshot_retained"] is True
    assert symbol["snapshot_id"] == "current"
    assert symbol["market_snapshot_age_seconds"] == 172800
    assert symbol["arrival_delay_seconds"] == 0
    assert symbol["feature_drift"]["status"] == "reference_missing"
    assert result["outcome_evaluation"]["status"] == "missing"
    assert result["candidate"]["promoted"] is False
    target = save_option_monitor(result, store)
    assert (
        target.exists()
        and json.loads((target.parent / "latest.json").read_text()) == result
    )


def test_monitor_checks_registered_source_and_feature_drift(tmp_path):
    store = OptionSnapshotStore(tmp_path / "options")
    store.save(chain())
    runs = tmp_path / "runs"
    registration = {
        "option_source_definitions": [
            {"provider": "other", "feed": "eod", "normalization_version": "v2"}
        ],
        "protocol": {"feature_version": "option-numeric-v2"},
        "feature_distributions": {
            "option__atm_iv_30d": {
                "mean": 0.1,
                "std": 0.01,
                "minimum": 0.08,
                "maximum": 0.12,
            }
        },
    }
    write(runs / "latest-run.json", {"experiment_id": "candidate"})
    write(runs / "candidate" / "registration.json", registration)
    write(runs / "candidate" / "evaluation.json", {"status": "evaluated_unpromoted"})
    report = build_option_monitor(store, evaluated_at=NOW, experiments_root=runs)
    symbol = report["symbols"]["US.AAPL"]
    assert symbol["source_definition"]["status"] == "changed_requires_reevaluation"
    assert symbol["feature_drift"]["features"]["atm_iv_30d"]["z_score"] == 10
    assert symbol["feature_drift"]["status"] == "drift_detected"
    assert symbol["prediction_status"] == "baseline_fallback"


def test_monitor_only_scores_mature_saved_immutable_forecasts_before_entry(tmp_path):
    store = OptionSnapshotStore(tmp_path / "options")
    store.save(chain())
    anchor, start, end = NOW, NOW + timedelta(hours=17), NOW + timedelta(days=1)
    horizon = {
        "horizon_days": 1,
        "q10": -0.02,
        "q50": 0.01,
        "q90": 0.03,
        "probabilities": {"up": 0.7, "flat": 0.2, "down": 0.1},
    }
    base = {
        "symbol": "US.AAPL",
        "stock_cutoff": anchor.isoformat(),
        "generated_at": (anchor + timedelta(hours=1)).isoformat(),
        "status": "baseline_fallback",
        "model_source": "stock_baseline",
        "horizons": [horizon],
    }
    candidate = base | {
        "status": "enhanced",
        "model_source": "stock_plus_options_numeric",
        "input_hash": "input",
        "model_bundle_hash": "model",
        "option_cutoff": anchor.isoformat(),
    }
    write(tmp_path / "base.json", base)
    write(tmp_path / "candidate.json", candidate)
    row = {
        "symbol": "US.AAPL",
        "as_of": anchor.isoformat(),
        "label_start_at": start.isoformat(),
        "label_end_at": end.isoformat(),
        "source_available_at": end.isoformat(),
        "horizon": 1,
        "label": "up",
        "realized_return": 0.01,
        "baseline_forecast_path": "base.json",
        "candidate_forecast_path": "candidate.json",
        "baseline_forecast_sha256": hashlib.sha256(
            (tmp_path / "base.json").read_bytes()
        ).hexdigest(),
        "candidate_forecast_sha256": hashlib.sha256(
            (tmp_path / "candidate.json").read_bytes()
        ).hexdigest(),
    }
    future = row | {
        "horizon": 20,
        "label_end_at": (NOW + timedelta(days=30)).isoformat(),
        "source_available_at": (NOW + timedelta(days=30)).isoformat(),
    }
    write(tmp_path / "outcomes.json", {"rows": [row, future]})
    report = build_option_monitor(
        store,
        evaluated_at=NOW + timedelta(days=2),
        outcomes_path=tmp_path / "outcomes.json",
    )
    outcomes = report["outcome_evaluation"]
    assert outcomes["status"] == "evaluated_mature_only"
    assert outcomes["excluded"]["immature_label"] == 1
    metrics = outcomes["horizons"]["1"]["candidate"]
    assert abs(metrics["brier"] - 0.14) < 1e-10
    assert metrics["interval"]["empirical_coverage"] == 1
    assert outcomes["horizons"]["20"]["status"] == "mature_samples_missing"
    candidate["generated_at"] = (start + timedelta(hours=1)).isoformat()
    write(tmp_path / "candidate.json", candidate)
    row["candidate_forecast_sha256"] = hashlib.sha256(
        (tmp_path / "candidate.json").read_bytes()
    ).hexdigest()
    write(tmp_path / "outcomes.json", {"rows": [row]})
    outcome = build_option_monitor(
        store,
        evaluated_at=NOW + timedelta(days=2),
        outcomes_path=tmp_path / "outcomes.json",
    )["outcome_evaluation"]
    assert outcome["excluded"]["forecast_after_entry"] == 1
    row["candidate_forecast_sha256"] = "0" * 64
    write(tmp_path / "outcomes.json", {"rows": [row]})
    outcome = build_option_monitor(
        store,
        evaluated_at=NOW + timedelta(days=2),
        outcomes_path=tmp_path / "outcomes.json",
    )["outcome_evaluation"]
    assert outcome["excluded"]["forecast_hash_mismatch"] == 1


def test_partial_first_attempt_reports_evidence_and_zero_requests_are_not_zero_refusals(
    tmp_path,
):
    store = OptionSnapshotStore(tmp_path / "options")
    store.save(chain().model_copy(update={"complete": False}))
    result = build_option_monitor(store, evaluated_at=NOW)
    symbol = result["symbols"]["US.AAPL"]
    assert symbol["data_status"] == "partial"
    assert symbol["field_coverage"]["call_iv"]["valid"] == 4
    assert symbol["saved_prediction_counts"]["refusal_rate"] is None
    assert symbol["latest_attempt"]["complete_snapshot_retained"] is False
