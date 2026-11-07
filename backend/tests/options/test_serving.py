from datetime import UTC, datetime
import json

import pytest

from stock_prediction.options.experiment import OptionExperimentStore
from stock_prediction.options.serving import (
    OptionInferenceInput,
    promote_option_candidate,
    infer_option_candidate,
)
from stock_prediction.options.store import OptionSnapshotStore


def test_unpromoted_candidate_never_produces_enhanced_forecast(tmp_path):
    input_row = OptionInferenceInput(
        symbol="US.AAPL",
        as_of="2026-10-10T20:00:00Z",
        stock_snapshot_id="stock-1",
        stock_model_version="baseline-v1",
        stock_feature_set_version="stock-v1",
        stock_numeric_features={"x": 1},
        stock_categorical_features={},
        stock_source_available_at="2026-10-10T20:00:00Z",
    )
    result = infer_option_candidate(
        input_row,
        OptionSnapshotStore(tmp_path / "options"),
        OptionExperimentStore(tmp_path / "runs"),
    )
    assert result.status == "baseline_fallback"
    assert result.model_version == "baseline-v1"
    assert result.horizons == ()
    assert result.candidate.promoted is False


def test_future_stock_features_are_rejected():
    with pytest.raises(ValueError, match="future"):
        OptionInferenceInput(
            symbol="US.AAPL",
            as_of="2026-10-10T20:00:00Z",
            stock_snapshot_id="stock-1",
            stock_model_version="baseline-v1",
            stock_feature_set_version="stock-v1",
            stock_numeric_features={"x": 1},
            stock_categorical_features={},
            stock_source_available_at="2026-10-10T20:00:01Z",
        )


@pytest.mark.parametrize(
    "corrupt", [None, [], {"experiment_id": "demo", "horizons": None}]
)
def test_invalid_candidate_metadata_degrades_to_named_stock_baseline(tmp_path, corrupt):
    store = OptionExperimentStore(tmp_path / "runs")
    store.root.mkdir()
    (store.root / "active.json").write_text(json.dumps(corrupt))
    row = OptionInferenceInput(
        symbol="US.AAPL",
        as_of="2026-10-10T20:00:00Z",
        stock_snapshot_id="stock-1",
        stock_model_version="baseline-v1",
        stock_feature_set_version="stock-v1",
        stock_numeric_features={"x": 1},
        stock_categorical_features={},
        stock_source_available_at="2026-10-10T20:00:00Z",
    )
    result = infer_option_candidate(
        row, OptionSnapshotStore(tmp_path / "options"), store
    )
    assert result.status == "baseline_fallback"
    assert result.model_version == "baseline-v1"
    assert result.candidate.status == "candidate_integrity_failed"


def test_promotion_rejects_missing_or_diagnostic_evidence(tmp_path):
    store = OptionExperimentStore(tmp_path)
    directory = store.directory("demo")
    directory.mkdir()
    (directory / "evaluation.json").write_text(
        json.dumps(
            {
                "evaluation_scope": "synthetic_offline",
                "registration": {"protocol": {}},
                "promotion_assessment": {"eligible": True},
            }
        )
    )
    with pytest.raises(ValueError):
        promote_option_candidate(
            store,
            "demo",
            expected_report_hash="a" * 64,
            at=datetime(2026, 10, 10, tzinfo=UTC),
        )
    assert not (tmp_path / "active.json").exists()


def test_cached_enhanced_forecast_requires_actual_release_evidence_and_content_hash(
    tmp_path,
):
    from stock_prediction.options.serving import (
        OptionForecastReadModel,
        OptionForecastHorizon,
        OptionCandidateState,
        save_option_inference,
        load_option_inference,
    )
    from stock_prediction.options.experiment import content_hash

    store = OptionExperimentStore(tmp_path)
    forecast = OptionForecastReadModel(
        symbol="US.AAPL",
        status="enhanced",
        model_source="stock_plus_options_numeric",
        model_version="demo",
        candidate=OptionCandidateState(
            status="promoted", version="demo", promoted=True
        ),
        stock_snapshot_id="stock-1",
        option_snapshot_id="option-1",
        stock_cutoff="2026-10-10T20:00:00Z",
        option_cutoff="2026-10-10T19:59:00Z",
        generated_at="2026-10-10T20:01:00Z",
        input_hash="a" * 64,
        model_bundle_hash="b" * 64,
        horizons=(OptionForecastHorizon(horizon_days=1, q10=-0.1, q50=0, q90=0.1),),
    )
    path = save_option_inference(forecast, store)
    (tmp_path / "active.json").write_text(
        json.dumps({"experiment_id": "demo", "model_bundle_hash": "b" * 64})
    )
    with pytest.raises((OSError, ValueError, KeyError)):
        load_option_inference("US.AAPL", "stock-1", store)
    payload = json.loads(path.read_text())
    payload["horizons"][0]["q90"] = 0.2
    path.write_text(json.dumps(payload))
    assert content_hash(payload) != content_hash(forecast.model_dump(mode="json"))
    with pytest.raises(ValueError, match="content hash"):
        load_option_inference("US.AAPL", "stock-1", store)


def test_calibrated_interval_is_separate_from_raw_quantile_endpoints():
    from stock_prediction.options.serving import OptionForecastHorizon

    row = OptionForecastHorizon(
        horizon_days=5,
        q10=-0.03,
        q50=0.01,
        q90=0.06,
        interval_low=-0.05,
        interval_high=0.08,
        interval_method="CQR-0.8",
        target_coverage=0.8,
    )
    assert row.q10 != row.interval_low
    assert row.q90 != row.interval_high
