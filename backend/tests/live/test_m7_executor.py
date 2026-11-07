from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

from lightgbm import LGBMClassifier
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stock_prediction.live.artifact_references import build_artifact_reference
from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V10,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.features import LIVE_FEATURE_ARTIFACT_SCHEMA_VERSION
from stock_prediction.live.m7_executor import (
    M7_ASSET_TYPE_VERSION,
    M7_SECTOR_MAPPING_VERSION,
    M7BundleExecutionError,
    _write_parquet_immutable,
    build_m7_development_folds,
    materialize_m7_development_dataset,
    prepare_m7_feature_data,
)
from stock_prediction.live.m7_bundle_executor import M7Seed100BundleExecutor
from stock_prediction.live.m7_partial_research import M7PartialResearchService
from stock_prediction.live.m7_direction_executor import M7DirectionExecutionOptions
from stock_prediction.live.m7_range_executor import M7RangeExecutionOptions
from stock_prediction.live.training import _FEATURES as LEGACY_FEATURES
from stock_prediction.modeling.m7_calibration import M7RangeCalibrator
from stock_prediction.modeling.m7_dataset import (
    M7DatasetManifest,
    M7EligibilityRow,
)
from stock_prediction.modeling.m7_recipe import M7_FEATURE_SET_VERSION
from stock_prediction.modeling.m7_gates import M7GateDecision, M7GateStatus
from stock_prediction.universe.seed import SEED_ETFS, SEED_UNIVERSE


def _source_frame() -> pd.DataFrame:
    sessions = pd.bdate_range("2025-05-01", periods=300)
    rows: list[dict[str, object]] = []
    for symbol_index, symbol in enumerate(SEED_UNIVERSE):
        for session_index, session in enumerate(sessions):
            close = 80.0 + symbol_index + 0.03 * session_index
            atr_pct = 0.02
            row: dict[str, object] = {
                "symbol": symbol,
                "session_date": session.date(),
                "source_available_at": datetime.combine(
                    session.date(), datetime.min.time(), tzinfo=UTC
                ),
                "open": close - 0.1,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1_000_000.0 + symbol_index + session_index,
                "quality_quarantined": False,
                "atr_pct_20": atr_pct,
                "spy_return_20": 0.01,
                "dataset_revision": "opend-live-market-bars-pit-adjusted-v2",
            }
            for horizon in (1, 5, 20):
                row[f"direction_{horizon}"] = float((-1, 0, 1)[session_index % 3])
                row[f"return_fwd_{horizon}"] = 0.001 * horizon
                row[f"range_exit_event_{horizon}"] = float(session_index % 2)
                row[f"range_exit_day_{horizon}"] = float(min(horizon, 5))
            rows.append(row)
    frame = pd.DataFrame(rows)
    for horizon in (1, 5, 20):
        final_sessions = set(sessions[-horizon:].date)
        frame.loc[
            frame["session_date"].isin(final_sessions),
            [
                f"direction_{horizon}",
                f"return_fwd_{horizon}",
                f"range_exit_event_{horizon}",
                f"range_exit_day_{horizon}",
            ],
        ] = np.nan
    return frame


def _write_source_artifact(paths: LivePaths, frame: pd.DataFrame) -> None:
    input_hash = "a" * 64
    generated_at = datetime(2026, 7, 1, tzinfo=UTC)
    target = (
        paths.features / "live-daily-features-v2" / input_hash[:16] / "features.parquet"
    )
    target.parent.mkdir(parents=True)
    frame.to_parquet(target, index=False)
    reference = build_artifact_reference(
        artifact_root=paths.features,
        artifact_id=input_hash,
        target=target,
        schema_version=LIVE_FEATURE_ARTIFACT_SCHEMA_VERSION,
        created_at=generated_at,
    )
    payload = {
        "universe_version": "m1-seed-2026-07-11-v1",
        "feature_set_version": "live-daily-features-v2",
        "dataset_revision": "opend-live-market-bars-pit-adjusted-v2",
        "input_hash": input_hash,
        "generated_at": generated_at.isoformat(),
        "as_of": generated_at.isoformat(),
        "row_count": len(frame),
        "artifact_reference": reference.as_dict(),
    }
    (paths.features / "LATEST.json").write_text(json.dumps(payload), encoding="utf-8")


def test_prepare_m7_feature_data_builds_versioned_seed100_v5_and_preserves_labels(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    source = _source_frame()
    _write_source_artifact(paths, source)

    prepared = prepare_m7_feature_data(paths, development_cutoff=date(2026, 6, 29))

    assert prepared.feature_result.feature_set_version == M7_FEATURE_SET_VERSION
    assert prepared.label_version == "live-labels-v2"
    assert set(prepared.frame["symbol"]) == set(SEED_UNIVERSE)
    assert prepared.frame["asset_type_version"].eq(M7_ASSET_TYPE_VERSION).all()
    assert prepared.frame["sector_mapping_version"].eq(M7_SECTOR_MAPPING_VERSION).all()
    assert set(
        prepared.frame.loc[prepared.frame["symbol"].isin(SEED_ETFS), "asset_type"]
    ) == {"etf"}
    assert prepared.frame["direction_20"].dropna().isin((-1.0, 0.0, 1.0)).all()
    source_value = source.loc[
        (source["symbol"] == SEED_UNIVERSE[0])
        & (source["session_date"] == date(2025, 5, 1)),
        "direction_20",
    ].item()
    prepared_value = prepared.frame.loc[
        (prepared.frame["symbol"] == SEED_UNIVERSE[0])
        & (prepared.frame["session_date"] == date(2025, 5, 1)),
        "direction_20",
    ].item()
    assert prepared_value == source_value
    assert prepared.frame["session_date"].max() <= date(2026, 6, 29)
    assert set(prepared.dependency_hashes) == {
        "adjusted_bars",
        "calendar",
        "classification",
        "universe",
    }
    assert all(len(value) == 64 for value in prepared.dependency_hashes.values())
    assert prepared.feature_result.group_status["full_daily"].status == "eligible"


def test_materialize_m7_development_dataset_binds_eligibility_and_artifacts(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    _write_source_artifact(paths, _source_frame())
    prepared = prepare_m7_feature_data(paths, development_cutoff=date(2026, 6, 29))

    materialized = materialize_m7_development_dataset(
        paths,
        run_id="m7-test-run-001",
        prepared=prepared,
    )

    assert materialized.manifest.gate_passed is True
    assert materialized.manifest.symbol_count == 100
    assert materialized.manifest.horizons == (1, 5, 20)
    assert materialized.manifest.anchor_sessions
    assert materialized.manifest.eligible_row_count == (
        materialized.manifest.expected_row_count
    )
    assert materialized.feature_path.is_file()
    assert materialized.label_path.is_file()
    assert materialized.eligibility_path.is_file()
    assert materialized.manifest_path.is_file()

    resumed = materialize_m7_development_dataset(
        paths,
        run_id="m7-test-run-001",
        prepared=prepared,
    )

    assert resumed.manifest == materialized.manifest
    assert resumed.feature_reference == materialized.feature_reference


@pytest.mark.parametrize("tamper", ["value", "column", "type"])
def test_write_parquet_immutable_rejects_storage_changes(
    tmp_path: Path,
    tamper: str,
) -> None:
    path = tmp_path / "frozen.parquet"
    frame = pd.DataFrame(
        {
            "close": [100.0, 101.0],
            "missing_reason": pd.Series([None, "source_missing"], dtype=object),
        }
    )
    _write_parquet_immutable(path, frame)
    table = pq.read_table(path)
    if tamper == "value":
        table = pa.table(
            {
                "close": [100.0, 999.0],
                "missing_reason": [None, "source_missing"],
            }
        )
    elif tamper == "column":
        table = table.rename_columns(["price", "missing_reason"])
    else:
        table = table.set_column(
            0,
            "close",
            pa.array([100, 101], type=pa.int64()),
        )
    pq.write_table(table, path)

    with pytest.raises(
        M7BundleExecutionError,
        match="m7_executor_artifact_resume_mismatch",
    ):
        _write_parquet_immutable(path, frame)


def test_build_m7_development_folds_uses_latest_four_exact_purged_windows() -> None:
    sessions = tuple(
        value.date() for value in pd.bdate_range("2020-01-02", periods=1200)
    )
    rows = tuple(
        M7EligibilityRow(
            symbol=symbol,
            anchor_session=session,
            horizon=horizon,
            eligible=True,
            exclusion_reasons=(),
            adjusted_bar_reference=None,
            calendar_reference=None,
            feature_reference=None,
            label_reference=None,
        )
        for session in sessions
        for symbol in SEED_UNIVERSE[:2]
        for horizon in (1, 5, 20)
    )
    manifest = M7DatasetManifest(
        schema_version="m7-dataset-manifest-v1",
        dataset_id="m7-test-dataset",
        manifest_hash="b" * 64,
        universe_version="m1-seed-2026-07-11-v1",
        dataset_revision="bars-v1",
        feature_set_version="live-daily-features-v5-seed100",
        label_version="live-labels-v2",
        symbols=SEED_UNIVERSE,
        anchor_sessions=sessions,
        horizons=(1, 5, 20),
        symbol_count=100,
        expected_row_count=len(rows),
        eligible_row_count=len(rows),
        symbol_coverage={symbol: 1.0 for symbol in SEED_UNIVERSE},
        horizon_coverage={1: 1.0, 5: 1.0, 20: 1.0},
        exclusion_counts={},
        label_threshold_artifacts=(),
        gate_passed=True,
        failure_codes=(),
        rows=rows,
    )

    split = build_m7_development_folds(manifest)

    assert len(split.folds) == 4
    assert len({fold.partition_hash for fold in split.folds}) == 4
    assert all(len(fold.selection_sessions) == 116 for fold in split.folds)
    assert all(len(fold.calibration_purge_sessions) == 20 for fold in split.folds)
    assert all(len(fold.calibration_sessions) == 116 for fold in split.folds)
    assert all(len(fold.outer_embargo_sessions) == 20 for fold in split.folds)
    assert all(len(fold.outer_test_sessions) == 126 for fold in split.folds)
    assert all(
        tuple(
            sorted(
                fold.outer_test_sample_keys, key=lambda key: (key[1], key[0], key[2])
            )
        )
        == fold.outer_test_sample_keys
        for fold in split.folds
    )
    assert len(split.split_manifest_hash) == 64


def test_default_seed100_bundle_executor_fails_closed_without_governed_data(
    tmp_path: Path,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)

    result = M7Seed100BundleExecutor(paths, run_id="m7-missing-data").__call__(
        "direction"
    )

    assert result.development_gate_passed is False
    assert result.gate_payload["status"] == "failed"
    assert "unconfigured" not in str(result.gate_payload)
    assert "m7_source_feature_artifact_unavailable" in str(result.gate_payload)


def test_seed100_bundle_executor_accepts_explicit_smoke_limits(tmp_path: Path) -> None:
    direction_options = M7DirectionExecutionOptions(
        synthetic_tree_iteration_cap=1,
        synthetic_deep_epoch_cap=1,
        bootstrap_draws=25,
    )
    range_options = M7RangeExecutionOptions(bootstrap_draws=25)

    executor = M7Seed100BundleExecutor(
        LivePaths.from_workspace(tmp_path),
        run_id="m7-smoke",
        direction_options=direction_options,
        range_options=range_options,
    )

    assert executor.direction_options == direction_options
    assert executor.range_options == range_options


def test_seed100_bundle_executor_allows_runs_longer_than_24_hours(
    tmp_path: Path,
) -> None:
    executor = M7Seed100BundleExecutor(
        LivePaths.from_workspace(tmp_path),
        run_id="m7-deadline",
    )

    assert executor._check_cancellation() is None


def test_range_executor_returns_publishable_inputs_after_gate_sla_and_calendar_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    _write_source_artifact(paths, _source_frame())
    prepared = prepare_m7_feature_data(paths, development_cutoff=date(2026, 6, 29))
    materialized = materialize_m7_development_dataset(
        paths, run_id="m7-range-publication", prepared=prepared
    )
    splits = SimpleNamespace(split_manifest_hash="3" * 64)
    training = pd.DataFrame(
        {
            name: np.linspace(-1.0, 1.0, 40) + index / 100
            for index, name in enumerate(LEGACY_FEATURES)
        }
    )
    training["elapsed_day"] = np.tile(np.arange(1, 5), 10)
    labels = (training[LEGACY_FEATURES[0]] > 0).astype(int)
    models = []
    for seed in range(4):
        model = LGBMClassifier(
            n_estimators=3,
            num_leaves=4,
            min_child_samples=2,
            random_state=seed,
            verbosity=-1,
        )
        model.fit(training, labels)
        models.append(model)
    calibrator = M7RangeCalibrator(
        method="identity",
        parameters=((), (), ()),
        x_thresholds=((), (), ()),
        y_thresholds=((), (), ()),
        fit_evidence_hash="fit",
        oof_fold_hash="oof",
        raw_brier=0.1,
        calibrated_brier=0.1,
    )
    evaluations = tuple(
        SimpleNamespace(
            metrics=SimpleNamespace(fold_id=f"fold-{index}", determinism_passed=True),
            calibrator=calibrator,
        )
        for index in range(1, 5)
    )
    outcome = SimpleNamespace(
        gate=M7GateDecision(
            gate_id="m7-range-exit-20",
            status=M7GateStatus.PASSED,
            failed_gates=(),
            metrics={"fold_wins": 4},
            diagnostics={},
            reason=None,
        ),
        fold_evaluations=evaluations,
        fold_models=tuple(models),
        feature_names=tuple([*LEGACY_FEATURES, "elapsed_day"]),
        c_index_delta_lower_90=0.01,
        runtime_seconds=2.0,
        cpu_seconds=1.5,
    )
    future_sessions = tuple(
        date(2026, 6, 30) + timedelta(days=index) for index in range(120)
    )
    progress_events: list[tuple[str, int, str, str | None]] = []

    def run_range(*_args: object, **kwargs: object) -> object:
        callback = kwargs["progress_callback"]
        assert callable(callback)
        callback("candidate_search", 22, "区间退出 · outer 1/4 · H20", "range.fold-1")
        return outcome

    monkeypatch.setattr(
        "stock_prediction.live.m7_bundle_executor.run_m7_range_development",
        run_range,
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_bundle_executor.resolve_seed100_promotion_calendar",
        lambda *_args, **_kwargs: SimpleNamespace(
            eligible_sessions=future_sessions,
            calendar_source="opend-us-calendar-v1:" + "9" * 64,
            source_hash="9" * 64,
        ),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_bundle_executor._workspace_root",
        lambda _paths: Path.cwd(),
    )
    executor = M7Seed100BundleExecutor(
        paths,
        run_id="m7-range-publication",
        progress_callback=lambda stage, percent, detail, trial_id: progress_events.append(
            (stage, percent, detail, trial_id)
        ),
        incumbent_provider=lambda task, horizon: (
            "range_exit-5-champion" if (task, horizon) == ("range_exit", 5) else None
        ),
    )
    executor._context = (prepared, materialized, splits)

    result = executor("range_exit_20")

    assert result.development_gate_passed is True
    assert result.gate_payload["status"] == "passed"
    assert result.package_build_spec is not None
    assert result.package_build_spec.bundle_id == "range_exit_20"
    assert len(result.artifacts) == 5
    assert result.eligible_sessions == future_sessions
    assert result.runtime_observations["inference_device"] == "cpu"
    assert result.report_payloads["explanation_manifest"]["status"] == "completed"
    assert result.incumbent_id is None
    assert result.range_five_day_anchor_id == "range_exit-5-champion"
    assert progress_events == [
        ("candidate_search", 22, "区间退出 · outer 1/4 · H20", "range.fold-1")
    ]


def test_direction_executor_returns_publishable_inputs_after_package_sla_and_calendar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fold_row = SimpleNamespace(
        metrics=SimpleNamespace(fold_id="fold-1", determinism_passed=True),
        calibration_methods={"candidate": "identity"},
        blend=SimpleNamespace(member_ids=("candidate",), weights=(1.0,)),
    )
    horizon_gate = M7GateDecision(
        gate_id="horizon",
        status=M7GateStatus.PASSED,
        failed_gates=(),
        metrics={"fold_wins": 4},
        diagnostics={},
        reason=None,
    )
    outcome = SimpleNamespace(
        gate=M7GateDecision(
            gate_id="m7-direction",
            status=M7GateStatus.PASSED,
            failed_gates=(),
            metrics={"bundle": "direction"},
            diagnostics={},
            reason=None,
        ),
        feature_ablation={"rows": []},
        fold_evaluations={1: (fold_row,), 5: (fold_row,), 20: (fold_row,)},
        horizon_gates={1: horizon_gate, 5: horizon_gate, 20: horizon_gate},
        bootstrap_upper_90=-0.01,
        bootstrap_draws=100,
        candidate_failures=(),
        economic_diagnostic=SimpleNamespace(
            evidence_scope="optimization_reused",
            catastrophic_horizon_count=0,
            catastrophic_guardrail_passed=True,
            horizons={},
        ),
    )
    build_spec = SimpleNamespace(
        bundle_id="direction",
        recipe_id="candidate-recipe-seed100-targeted-v9",
        created_at=datetime(2026, 8, 25, 16, tzinfo=UTC),
    )
    publication = SimpleNamespace(
        build_spec=build_spec,
        artifacts=(SimpleNamespace(category="model"),),
        sla=SimpleNamespace(
            inference_sla_passed=True,
            explanation_sla_passed=True,
            explanation_availability="available",
            inference_seconds=1.0,
            explanation_seconds=2.0,
            seed100_row_count=100,
        ),
        runtime_observations={
            "wall_seconds": 3.0,
            "cpu_seconds": 2.0,
            "peak_rss_bytes": 1024,
            "peak_private_bytes": 2048,
            "peak_gpu_memory_bytes": 0,
            "gpu_device": "cpu",
            "inference_device": "cpu",
        },
    )
    future_sessions = tuple(
        date(2026, 8, 26) + timedelta(days=index) for index in range(120)
    )
    progress_events: list[tuple[str, int, str, str | None]] = []

    def run_direction(*_args: object, **kwargs: object) -> object:
        callback = kwargs["progress_callback"]
        assert callable(callback)
        callback(
            "candidate_search",
            12,
            "候选 13/280 · outer 1/4 · H1 · CPU catboost_multiclass",
            "direction.fold-1.h1",
        )
        return outcome

    monkeypatch.setattr(
        "stock_prediction.live.m7_bundle_executor.run_m7_direction_development",
        run_direction,
    )
    monkeypatch.setattr(
        M7Seed100BundleExecutor,
        "_freeze_direction_publication",
        lambda *_args, **_kwargs: publication,
        raising=False,
    )
    monkeypatch.setattr(
        M7Seed100BundleExecutor,
        "_trial_context",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_bundle_executor.resolve_seed100_promotion_calendar",
        lambda *_args, **_kwargs: SimpleNamespace(
            eligible_sessions=future_sessions,
            calendar_source="opend-us-calendar-v1:" + "9" * 64,
            source_hash="9" * 64,
        ),
    )
    executor = M7Seed100BundleExecutor(
        LivePaths.from_workspace(tmp_path),
        run_id="m7-direction-publication",
        progress_callback=lambda stage, percent, detail, trial_id: progress_events.append(
            (stage, percent, detail, trial_id)
        ),
        incumbent_provider=lambda _task, _horizon: None,
    )
    executor._context = (SimpleNamespace(), SimpleNamespace(), SimpleNamespace())

    result = executor("direction")

    assert result.development_gate_passed is True
    assert result.package_build_spec is build_spec
    assert result.eligible_sessions == future_sessions
    assert result.report_payloads["explanation_manifest"]["status"] == "completed"
    assert result.incumbent_id is None
    assert progress_events == [
        (
            "candidate_search",
            12,
            "候选 13/280 · outer 1/4 · H1 · CPU catboost_multiclass",
            "direction.fold-1.h1",
        )
    ]


def test_v10_range_partial_publish_requires_one_verified_fold(tmp_path: Path) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    paths.ensure()
    _write_source_artifact(paths, _source_frame())
    prepared = prepare_m7_feature_data(paths, development_cutoff=date(2026, 6, 29))
    materialized = materialize_m7_development_dataset(
        paths,
        run_id="m7-v10-range-partial",
        prepared=prepared,
    )
    training = pd.DataFrame(
        {name: np.linspace(-1.0, 1.0, 40) for name in LEGACY_FEATURES}
    )
    training["elapsed_day"] = np.tile(np.arange(1, 5), 10)
    labels = (training[LEGACY_FEATURES[0]] > 0).astype(int)
    model = LGBMClassifier(
        n_estimators=3,
        num_leaves=4,
        min_child_samples=2,
        random_state=17,
        verbosity=-1,
    ).fit(training, labels)
    calibrator = M7RangeCalibrator(
        method="identity",
        parameters=((), (), ()),
        x_thresholds=((), (), ()),
        y_thresholds=((), (), ()),
        fit_evidence_hash="fit",
        oof_fold_hash="oof",
        raw_brier=0.1,
        calibrated_brier=0.1,
    )
    executor = M7Seed100BundleExecutor(
        paths,
        run_id="m7-v10-range-partial",
        recipe_version=CANDIDATE_RECIPE_SEED100_TARGETED_V10,
    )

    executor._publish_range_partial(
        prepared,
        materialized,
        "a" * 64,
        "fold-1",
        "range-candidate-1",
        model,
        SimpleNamespace(
            metrics=SimpleNamespace(determinism_passed=True), calibrator=calibrator
        ),
        tuple([*LEGACY_FEATURES, "elapsed_day"]),
    )

    result = M7PartialResearchService(paths).result(
        run_id="m7-v10-range-partial",
        bundle_id="range_exit_20",
        symbol="US.AAPL",
    )
    assert result.availability == "available"
    assert result.contributing_folds == ("fold-1",)
    assert result.predictions[0].range_exit_probability >= 0
