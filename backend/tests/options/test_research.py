from stock_prediction.options.research import audit_options, compare_predictions
from stock_prediction.options.store import OptionSnapshotStore
from backend.tests.options.test_structure import snapshot


def test_forward_data_audit_does_not_claim_historical_readiness(tmp_path):
    store = OptionSnapshotStore(tmp_path)
    store.save(snapshot())
    report = audit_options(store)
    assert report["historical_experiment_ready"] is False
    assert report["published"] is False
    assert "historical_time_evidence_missing" in report["blockers"]
    assert report["symbols"]["US.AAPL"]["contracts"] == 1


def test_comparison_uses_identical_pairs_and_existing_metrics():
    rows = [
        {
            "symbol": "US.AAPL",
            "as_of": "2026-01-02T21:00:00Z",
            "horizon": 1,
            "fold_id": "outer-1",
            "label": "up",
            "return": 0.1,
            "baseline_probabilities": [0.8, 0.1, 0.1],
            "candidate_probabilities": [0.9, 0.05, 0.05],
            "baseline_quantiles": [0.0, 0.05, 0.2],
            "candidate_quantiles": [0.0, 0.1, 0.2],
        }
    ]
    report = compare_predictions(rows)
    assert (
        report["groups"]["1:outer-1"]["candidate"]["brier"]
        < report["groups"]["1:outer-1"]["baseline"]["brier"]
    )
    assert report["promoted"] is False


def test_comparison_rejects_duplicate_samples_and_crossing_quantiles():
    import pytest

    row = {
        "symbol": "US.AAPL",
        "as_of": "2026-01-02T21:00:00Z",
        "horizon": 1,
        "fold_id": "outer-1",
        "label": "up",
        "return": 0.1,
        "baseline_probabilities": [0.8, 0.1, 0.1],
        "candidate_probabilities": [0.9, 0.05, 0.05],
        "baseline_quantiles": [0.0, 0.05, 0.2],
        "candidate_quantiles": [0.0, 0.1, 0.2],
    }
    with pytest.raises(ValueError, match="duplicate"):
        compare_predictions([row, row])
    with pytest.raises(ValueError, match="quantile"):
        compare_predictions([row | {"candidate_quantiles": [0.2, 0.1, 0.0]}])


def test_date_block_comparison_uses_existing_bootstrap_dataclass():
    from datetime import UTC, datetime, timedelta

    rows = []
    for i in range(40):
        rows.append(
            {
                "symbol": "US.AAPL",
                "as_of": (
                    datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i)
                ).isoformat(),
                "horizon": 1,
                "fold_id": "outer-1",
                "label": "up",
                "return": 0.1,
                "baseline_probabilities": [0.8, 0.1, 0.1],
                "candidate_probabilities": [0.9, 0.05, 0.05],
                "baseline_quantiles": [0.0, 0.05, 0.2],
                "candidate_quantiles": [0.0, 0.1, 0.2],
            }
        )
    report = compare_predictions(rows)
    assert report["groups"]["1:outer-1"]["date_block_uncertainty"]["n_dates"] == 40
    assert report["promoted"] is False


def test_existing_stock_dataset_without_option_history_is_blocked(tmp_path):
    from datetime import UTC, datetime
    from backend.tests.modeling.test_splits import _calendar, _dataset, _spec
    from stock_prediction.options.research import (
        OptionExperimentProtocol,
        prepare_option_experiment,
    )

    calendar = _calendar()
    protocol = OptionExperimentProtocol(
        experiment_id="offline-test",
        registered_at="2026-10-10T12:00:00Z",
        maximum_age_seconds=86400,
        split_version="options-split-v1",
        split_spec=_spec(),
        license_reference="test-only",
        time_evidence_reference="test-only",
        baseline_model_reference="test-only",
        training_budget_reference="test-only",
        promotion_rules_reference="test-only",
    )
    result = prepare_option_experiment(
        _dataset(calendar),
        calendar,
        protocol,
        OptionSnapshotStore(tmp_path),
        evaluated_at=datetime(2026, 10, 10, 20, tzinfo=UTC),
    )
    assert result["status"] == "blocked"
    assert result["matched_samples"] == 0
