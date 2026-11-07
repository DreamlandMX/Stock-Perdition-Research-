from datetime import UTC, datetime

import pytest

from stock_prediction.options.experiment import (
    OptionExperimentStore,
    fit_feature_transform,
    apply_feature_transform,
    assess_promotion,
)


def test_imputation_and_vocabulary_are_fitted_on_train_only():
    train = [
        {
            "stock": {"x": 1.0},
            "option": {"atm_iv_30d": None},
            "categories": {"state": "range"},
        }
    ]
    fitted = fit_feature_transform(train, group="E3")
    test = [
        {
            "stock": {"x": 999.0},
            "option": {"atm_iv_30d": 0.4},
            "categories": {"state": "new"},
        }
    ]
    matrix = apply_feature_transform(test, fitted)
    assert fitted["medians"]["stock__x"] == 1.0
    assert "new" not in str(fitted["categories"])
    assert matrix.shape[1] == len(fitted["feature_names"])
    assert matrix[0, fitted["feature_names"].index("stock__x")] == 999.0
    assert matrix[0, fitted["feature_names"].index("category__state__unseen")] == 1


def test_registration_is_immutable_and_blind_test_is_consumed_once(tmp_path):
    store = OptionExperimentStore(tmp_path)
    store.register("demo", {"seed": 1})
    store.register("demo", {"seed": 1})
    with pytest.raises(ValueError, match="conflict"):
        store.register("demo", {"seed": 2})
    store.consume_test(
        "demo", {"blind_window": "fold-1"}, datetime(2026, 10, 10, tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="consumed"):
        store.consume_test(
            "demo", {"blind_window": "fold-1"}, datetime(2026, 10, 10, tzinfo=UTC)
        )


def test_synthetic_or_missing_economic_evidence_cannot_pass_release():
    report = {
        "evaluation_scope": "synthetic_offline",
        "horizons": {},
        "deployment_coverage": 1,
    }
    result = assess_promotion(report, None)
    assert not result["eligible"]
    assert "registered_promotion_rules_missing" in result["reasons"]
    assert "synthetic_evidence_not_promotable" in result["reasons"]


def test_quality_only_group_contains_all_quality_fields_and_keeps_iv_change_in_iv_group():
    names = (
        "iv_valid_ratio",
        "oi_valid_ratio",
        "gamma_valid_ratio",
        "quote_valid_ratio",
        "excluded_contract_ratio",
        "history_gap_seconds",
    )
    row = {
        "stock": {"x": 1},
        "option": dict.fromkeys(names, 0.5) | {"atm_iv_change": 0.1},
        "categories": {},
    }
    quality = fit_feature_transform([row], group="quality")
    assert all("option__" + name in quality["names"] for name in names)
    iv = fit_feature_transform([row], group="E1")
    assert "option__atm_iv_change" in iv["names"]
    assert "option__iv_valid_ratio" not in iv["names"]


def test_blind_samples_cannot_be_reused_under_another_experiment_id(tmp_path):
    store = OptionExperimentStore(tmp_path)
    store.register("one", {})
    store.register("two", {})
    store.consume_test("one", {"sample_keys": ["same-key"]}, datetime.now(UTC))
    with pytest.raises(ValueError, match="another experiment"):
        store.consume_test("two", {"sample_keys": ["same-key"]}, datetime.now(UTC))


def test_all_ablation_groups_train_calibrate_evaluate_and_consume_same_folds(tmp_path):
    from backend.tests.modeling.test_splits import _calendar, _dataset, _spec
    from stock_prediction.contracts.research import DirectionLabel, BreakoutEvent
    from stock_prediction.modeling.splits import build_walk_forward_manifest
    from stock_prediction.options.experiment import (
        run_option_experiment,
        content_hash,
        GROUPS,
    )
    from stock_prediction.options.research import (
        OptionExperimentProtocol,
        OptionTrainingBudget,
        OptionPromotionRules,
    )
    from stock_prediction.research.dataset import compute_research_dataset_input_hash

    calendar = _calendar()
    source = _dataset(calendar)
    samples = []
    sidecar = []
    for index, row in enumerate(source.samples[:470]):
        for horizon in (1, 5, 20):
            label = (DirectionLabel.UP, DirectionLabel.FLAT, DirectionLabel.DOWN)[
                index % 3
            ]
            target = row.target.model_copy(
                update={
                    "horizon": horizon,
                    "forecast_direction": label,
                    "executable_direction": label,
                    "range_exit": None
                    if horizon == 1 or index % 4 == 0
                    else index % 3 == 0,
                    "range_exit_at": row.label_start_at
                    if horizon != 1 and index % 4 != 0 and index % 3 == 0
                    else None,
                    "breakout_event": (
                        BreakoutEvent.NONE,
                        BreakoutEvent.UP,
                        BreakoutEvent.DOWN,
                        BreakoutEvent.EXTREME_BOTH,
                    )[index % 4],
                    "breakout_event_at": row.label_start_at if index % 4 else None,
                    "capital_gain_return": (1 - index % 3) * 0.01,
                    "executable_return": (1 - index % 3) * 0.009,
                }
            )
            sample = row.model_copy(update={"horizon": horizon, "target": target})
            samples.append(sample)
            sidecar.append(
                {
                    "symbol": row.symbol,
                    "as_of": row.as_of.isoformat(),
                    "horizon": horizon,
                    "snapshot_id": f"offline-{index}",
                    "stock_features": row.numeric_features,
                    "option_features": {
                        "atm_iv_30d": 0.2 + index % 3 * 0.01,
                        "call_oi": float(index % 5),
                        "quality_coverage": 0.9,
                    },
                    "option_source": {
                        "source_available_at": row.as_of.isoformat(),
                        "provider": "offline",
                        "feed": "offline",
                        "normalization_version": "offline-v1",
                        "dataset_revision": "offline-r1",
                        "dte_min": 1,
                        "dte_max": 90,
                        "strike_min_ratio": 0.7,
                        "strike_max_ratio": 1.3,
                    },
                }
            )
    digest = compute_research_dataset_input_hash(samples)
    dataset = source.model_copy(
        update={
            "samples": tuple(samples),
            "sample_count": len(samples),
            "input_hash": digest,
            "dataset_id": "research-" + digest[:24],
        }
    )
    protocol = OptionExperimentProtocol(
        experiment_id="offline-integration",
        registered_at="2026-10-09T20:00:00Z",
        maximum_age_seconds=86400,
        split_version="option-split-v1",
        split_spec=_spec(
            train_years=1,
            validation_months=6,
            test_months=2,
            step_months=2,
            minimum_outer_folds=1,
        ),
        license_reference="offline-only",
        time_evidence_reference="offline-only",
        baseline_model_reference="offline-only",
        training_budget_reference="offline-only",
        promotion_rules_reference="offline-only",
        feature_version="option-numeric-v2",
        training_budget=OptionTrainingBudget(
            seed=17,
            num_boost_round=2,
            learning_rate=0.05,
            num_leaves=4,
            min_data_in_leaf=3,
            num_threads=1,
        ),
        promotion_rules=OptionPromotionRules(
            minimum_test_dates=40,
            minimum_outer_folds=2,
            minimum_positive_fold_fraction=0.75,
            maximum_log_loss_degradation=0,
            maximum_pinball_degradation=0,
            maximum_calibration_error=0.1,
            maximum_interval_coverage_error=0.1,
            maximum_symbol_brier_degradation=0,
            maximum_quality_only_gain_fraction=0.5,
            minimum_deployment_coverage=0.9,
            minimum_net_return_delta=0,
            maximum_drawdown_degradation=0,
            minimum_cohort_dates=20,
            maximum_cohort_brier_degradation=0,
        ),
    )
    manifest = build_walk_forward_manifest(
        dataset, calendar, protocol.split_spec, protocol.split_version
    )
    prepared = {
        "status": "prepared_untrained",
        "experiment": protocol.model_dump(mode="json"),
        "matched_dataset": dataset.model_dump(mode="json"),
        "split_manifest": manifest.model_dump(mode="json"),
        "feature_sidecar": sidecar,
        "option_input_hash": content_hash(sidecar),
        "deployment_coverage": 1,
    }
    store = OptionExperimentStore(tmp_path)
    import pandas as pd

    market = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "session_date": session,
                "source_available_at": datetime(
                    session.year, session.month, session.day, 20, tzinfo=UTC
                ),
                "open": 100 + index * 0.1,
                "close": 100.01 + index * 0.1,
                "volume": 1000000,
                "eligible": True,
            }
            for symbol in ("US.AAPL", "US.SPY")
            for index, session in enumerate(calendar.sessions[:510])
        ]
    )
    result = run_option_experiment(
        prepared,
        store,
        evaluated_at=datetime(2026, 10, 10, tzinfo=UTC),
        market_frame=market,
    )
    assert set(result["horizons"]) == {"1", "5", "20"}
    assert all(
        set(entry["metrics"]) == set(GROUPS) for entry in result["horizons"].values()
    )
    assert result["promotion_assessment"]["eligible"] is False
    assert result["economic_evaluation"]["status"] == "evaluated"
    assert "breakout" in result["horizons"]["1"]["metrics"]["E3"]
    assert (
        0
        < result["horizons"]["5"]["metrics"]["E3"]["range_exit_survival"][
            "task_not_defined_fraction"
        ]
        < 1
    )
    assert set(result["economic_evaluation"]["horizons"]) == {"1", "5", "20"}
    assert (store.directory(protocol.experiment_id) / "models.json").is_file()
    with pytest.raises(ValueError, match="consumed"):
        run_option_experiment(
            prepared,
            store,
            evaluated_at=datetime(2026, 10, 10, tzinfo=UTC),
            market_frame=market,
        )
