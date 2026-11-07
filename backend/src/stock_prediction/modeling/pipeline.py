"""Deterministic, fully offline M4 validation and backtest runner."""

from __future__ import annotations

from argparse import Namespace
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
import json
import math
import os
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, cast

import numpy as np
import psutil  # type: ignore[import-untyped]
import pyarrow as pa
import pyarrow.parquet as pq

from stock_prediction.backtesting.costs import CostScenario
from stock_prediction.backtesting.diagnostics import (
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)
from stock_prediction.backtesting.engine import (
    ExecutableBar,
    LongCashBacktester,
    StrategySpec,
    UniverseMembership,
    VerifiedMarketCalendar,
    VerifiedMarketSession,
    WeeklySignal,
)
from stock_prediction.contracts.research import (
    BreakoutEvent,
    CalendarSource,
    DirectionLabel,
    Horizon,
    HorizonTarget,
)
from stock_prediction.modeling.baselines import (
    ChannelAtrRuleBaseline,
    HistoricalFrequencyBaseline,
    LogisticBaseline,
)
from stock_prediction.modeling.calibration import (
    CalibrationPartitionEvidence,
    CqrCalibrator,
    TemperatureScaler,
    apply_temperature_scaling,
    rearrange_quantiles,
)
from stock_prediction.modeling.confidence import (
    ConfidenceEvidence,
    ConfidenceGate,
    ConfidencePolicy,
)
from stock_prediction.modeling.contracts import (
    BacktestTrial,
    EvaluationRole,
    EvaluationScope,
    EvaluationWindow,
    OuterFold,
    SplitManifest,
    TaskName,
    TrialFinished,
    TrialStatus,
    WalkForwardSpec,
)
from stock_prediction.modeling.ledger import JsonlTrialLedger
from stock_prediction.modeling.lightgbm_models import (
    ClassifierArtifact,
    ClassifierConfig,
    LightGbmClassifierTrainer,
    LightGbmQuantileTrainer,
    QuantileConfig,
    explain_tree_prediction,
)
from stock_prediction.modeling.metrics import (
    calibration_report,
    empirical_interval_diagnostics,
    mean_pinball_loss,
    multiclass_brier_score,
    multiclass_log_loss,
)
from stock_prediction.modeling.report import (
    artifact_entry,
    deterministic_hash,
    file_sha256,
    promotion_decision,
    write_immutable_json,
)
from stock_prediction.modeling.similar_cases import (
    DistanceConfig,
    SimilarCaseIndex,
    SimilarCaseQuery,
    SimilarCaseRow,
)
from stock_prediction.modeling.splits import build_walk_forward_manifest
from stock_prediction.modeling.survival import (
    PersonPeriodEvidence,
    RangeExitObservation,
    build_person_period_rows,
    cumulative_incidence,
)
from stock_prediction.modeling.training import (
    ClassificationTrainingData,
    RegressionTrainingData,
    TrainingPartitionEvidence,
)
from stock_prediction.research.calendar import (
    TradingCalendarArtifact,
    build_trading_calendar,
)
from stock_prediction.research.dataset import (
    ResearchDataset,
    ResearchSample,
    compute_research_dataset_input_hash,
)


SEED = 17
FEATURE_NAMES = (
    "momentum",
    "volatility",
    "compression",
    "channel_position",
    "atr_breakout_score",
)
CLASS_ORDER = ("down", "flat", "up")
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
FIXED_AT = datetime(2026, 7, 12, 16, tzinfo=UTC)
CLASSIFICATION_TASK_HORIZONS: tuple[tuple[TaskName, tuple[Horizon, ...]], ...] = (
    (TaskName.FORECAST_DIRECTION, (1, 5, 20)),
    (TaskName.BREAKOUT, (1, 5, 20)),
)


@dataclass(frozen=True, slots=True)
class SyntheticEvidence:
    dataset: ResearchDataset
    calendar: TradingCalendarArtifact


@dataclass(frozen=True, slots=True)
class TrialPlan:
    trial_id: str
    fold_id: str
    kind: str
    task: TaskName
    horizon: Horizon
    primary_metric: str
    secondary_metrics: tuple[str, ...] = ()
    candidate_index: int = 0


@dataclass(frozen=True, slots=True)
class FoldArrays:
    train: tuple[ResearchSample, ...]
    calibration: tuple[ResearchSample, ...]
    selection: tuple[ResearchSample, ...]
    test: tuple[ResearchSample, ...]


@dataclass(frozen=True, slots=True)
class AdjustedQuantilePredictions:
    rows: tuple[tuple[float, float, float, float, float], ...]
    crossing_row_count: int
    crossing_rate: float
    maximum_violation: float


@dataclass(frozen=True, slots=True)
class FoldModelEvidence:
    fold: OuterFold
    classifier: ClassifierArtifact
    calibrator: TemperatureScaler
    test_rows: tuple[ResearchSample, ...]
    raw_probabilities: tuple[tuple[float, ...], ...]
    raw_logits: tuple[tuple[float, ...], ...]
    calibrated_probabilities: tuple[tuple[float, ...], ...]


def adjust_quantile_predictions(values: object) -> AdjustedQuantilePredictions:
    """Rearrange q10..q90 rows once and assert the persisted order."""

    adjusted = rearrange_quantiles(values)
    if any(
        not (q10 <= q25 <= q50 <= q75 <= q90)
        for q10, q25, q50, q75, q90 in adjusted.quantiles
    ):
        raise RuntimeError("quantile rearrangement produced a crossing row")
    return AdjustedQuantilePredictions(
        rows=adjusted.quantiles,
        crossing_row_count=adjusted.crossing_row_count,
        crossing_rate=adjusted.crossing_rate,
        maximum_violation=adjusted.maximum_violation,
    )


def _task_horizons(task: TaskName) -> tuple[Horizon, ...]:
    return next(
        horizons
        for candidate, horizons in CLASSIFICATION_TASK_HORIZONS
        if candidate is task
    )


def run_m4_command(args: Namespace) -> dict[str, object]:
    if str(args.command) != "m4-offline-demo":
        raise ValueError("unsupported M4 command")
    return offline_demo(Path(args.work_root))


def offline_demo(work_root: Path) -> dict[str, object]:
    """Run bounded M4 acceptance using synthetic evidence and no live services."""

    started = perf_counter()
    process = psutil.Process()
    if work_root.exists() and any(work_root.iterdir()):
        raise FileExistsError("offline demo root must be empty")
    work_root.mkdir(parents=True, exist_ok=True)

    evidence = _build_synthetic_evidence()
    spec = WalkForwardSpec(
        train_years=5,
        validation_months=12,
        test_months=6,
        step_months=6,
        embargo_sessions=20,
        minimum_outer_folds=3,
        evaluation_scope=EvaluationScope.SYNTHETIC_OFFLINE,
    )
    manifest = build_walk_forward_manifest(
        evidence.dataset,
        evidence.calendar,
        spec,
        "m4-offline-split-v1",
    )
    if len(manifest.outer_folds) < 3:
        raise RuntimeError("synthetic evidence did not form three outer folds")

    artifact_paths = _write_source_artifacts(work_root, evidence, manifest)
    ledger = JsonlTrialLedger(work_root / "trial-ledger.jsonl")
    plans = _trial_plans(manifest)
    _preregister_trials(ledger, plans, manifest, evidence.dataset)
    fold_reports, metric_rows, fold_models, quantile_prediction_rows = _evaluate_folds(
        ledger, plans, manifest, evidence.dataset
    )
    runtime_quantile_model_identities = [
        {
            "fold_id": fold["fold_id"],
            "task": task,
            "model_artifact_hash": metrics["actual_model_artifact_hash"],
            "calibration_artifact_hash": metrics["actual_calibration_artifact_hash"],
        }
        for fold in fold_reports
        for task, metrics in cast(
            dict[str, dict[str, Any]], fold["quantile_metrics"]
        ).items()
    ]
    runtime_economic_model_identities = [
        {
            "fold_id": item.fold.fold_id,
            "model_artifact_hash": item.classifier.artifact_hash,
            "calibration_artifact_hash": item.calibrator.artifact_hash,
        }
        for item in fold_models
    ]
    artifact_paths.append(work_root / "trial-ledger.jsonl")
    artifact_paths.extend(
        _write_survival_evidence(work_root, manifest, evidence.dataset)
    )
    artifact_paths.extend(
        _write_economic_evidence(work_root, evidence, manifest, fold_models)
    )

    similar = _run_similar_cases(manifest, evidence.dataset)
    backtest = _run_long_cash_backtest(work_root)
    selected_model = fold_models[-1]
    confidence = _run_confidence_gate(
        selected_model.classifier.artifact_hash,
        selected_model.calibrator.artifact_hash,
        similar["eligible_count"],
        fold_reports[-1]["calibration_ece"],
    )
    diagnostics = _run_diagnostics(len(manifest.outer_folds))

    predictions_path = work_root / "fold-metrics.parquet"
    _write_parquet_immutable(predictions_path, metric_rows)
    artifact_paths.append(predictions_path)
    oos_predictions_path = work_root / "oos-predictions.parquet"
    _write_parquet_immutable(
        oos_predictions_path,
        [
            row
            for fold_report in fold_reports
            for row in cast(list[dict[str, Any]], fold_report["oos_prediction_rows"])
        ],
    )
    artifact_paths.append(oos_predictions_path)
    quantile_path = work_root / "quantile-predictions.parquet"
    _write_parquet_immutable(quantile_path, quantile_prediction_rows)
    artifact_paths.append(quantile_path)
    similar_path = work_root / "similar-cases.json"
    write_immutable_json(similar_path, similar)
    artifact_paths.append(similar_path)
    backtest_path = work_root / "long-cash-backtest.json"
    write_immutable_json(backtest_path, backtest)
    artifact_paths.append(backtest_path)
    replay_index_path = _write_replay_index(
        work_root,
        artifact_paths,
        dataset_input_hash=evidence.dataset.input_hash,
        split_manifest_hash=manifest.manifest_hash,
        calendar_hash=manifest.calendar_hash,
    )
    artifact_paths.append(replay_index_path)
    audit = _semantic_replay_m4_evidence(work_root)

    terminal = _terminal_counts(ledger)
    failed_trials = _failed_trials(ledger)
    failed_gates = [
        "synthetic_evidence_not_production",
        "economic_evidence_research_only",
        "predictive_lift_not_assessed",
        "point_in_time_universe_not_verified",
        "high_confidence_oos_improvement_not_demonstrated",
    ]
    decision = promotion_decision(
        outer_fold_count=len(manifest.outer_folds),
        failed_gates=failed_gates,
        evaluated_thresholds={
            "minimum_outer_folds": 3,
            "embargo_sessions": 20,
            "main_cost_per_side_bps": 6.5,
            "high_confidence_requires_oos_improvement": True,
            "production_calendar_required": "opend_verified",
        },
        evaluation_scope="synthetic_offline",
        economic_evidence_scope="research_only",
    )
    report = _acceptance_report(
        manifest=manifest,
        fold_reports=fold_reports,
        failed_trials=failed_trials,
        similar=similar,
        backtest=backtest,
        confidence=confidence,
        diagnostics=diagnostics,
        audit=audit,
        decision=decision.model_dump(mode="json"),
        artifact_paths=artifact_paths,
        work_root=work_root,
    )
    report_path = work_root / "m4-acceptance-report.json"
    write_immutable_json(report_path, report)

    deterministic_files = sorted(
        [*artifact_paths, report_path],
        key=lambda item: item.relative_to(work_root).as_posix(),
    )
    deterministic_artifact_hashes = {
        path.relative_to(work_root).as_posix(): artifact_entry(work_root, path)[
            "sha256"
        ]
        for path in deterministic_files
    }
    core_payload = {
        "dataset_input_hash": evidence.dataset.input_hash,
        "split_manifest_hash": manifest.manifest_hash,
        "promotion": decision.model_dump(mode="json"),
        "artifact_hashes": deterministic_artifact_hashes,
    }
    core_hash = deterministic_hash(core_payload)
    core_path = work_root / "deterministic-core.json"
    write_immutable_json(
        core_path, {**core_payload, "deterministic_core_hash": core_hash}
    )
    deterministic_artifact_hashes[core_path.name] = artifact_entry(
        work_root, core_path
    )["sha256"]
    pinned_audit = replay_m4_evidence(
        work_root,
        expected_deterministic_core_hash=core_hash,
    )

    runtime = {
        "runtime_seconds": perf_counter() - started,
        "peak_rss_bytes": process.memory_info().rss,
        "logical_cpu_count": os.cpu_count() or 1,
        "runtime_fields_excluded_from_deterministic_core": [
            "runtime_seconds",
            "peak_rss_bytes",
            "logical_cpu_count",
            "quantile_model_artifact_identities",
            "outer_fold_economic_model_artifact_identities",
        ],
        "quantile_model_artifact_identities": runtime_quantile_model_identities,
        "outer_fold_economic_model_artifact_identities": (
            runtime_economic_model_identities
        ),
    }
    write_immutable_json(work_root / "runtime-observations.json", runtime)
    return {
        "status": "ok",
        "evidence_scope": "synthetic_offline",
        "economic_evidence_scope": "research_only",
        "research_only": True,
        "predictive_lift_claimed": False,
        "outer_fold_count": len(manifest.outer_folds),
        "registered_trial_count": terminal["registered"],
        "succeeded_trial_count": terminal["succeeded"],
        "failed_trial_count": terminal["failed"],
        "aborted_trial_count": terminal["aborted"],
        "rejected_trial_count": terminal["rejected"],
        "promotion_status": decision.status.value,
        "failed_gates": list(decision.failed_gates),
        "main_cost_per_side_bps": 6.5,
        "production_window_consumed_count": audit["production_window_consumed_count"],
        "outer_window_consumed_count": len(audit["outer_window_consumption"]),
        "model_families": [
            "channel_atr_rule",
            "historical_frequency",
            "lightgbm",
            "logistic_regression",
        ],
        "quantiles": list(QUANTILES),
        "survival_horizons": [5, 20],
        "dataset_input_hash": evidence.dataset.input_hash,
        "split_manifest_hash": manifest.manifest_hash,
        "deterministic_core_hash": core_hash,
        "deterministic_artifact_hashes": deterministic_artifact_hashes,
        "external_integrity": pinned_audit["external_integrity"],
    }


def _synthetic_sessions() -> tuple[date, ...]:
    sessions: list[date] = []
    current = date(2016, 1, 4)
    end = date(2023, 9, 29)
    while current <= end:
        if current.weekday() in {0, 3}:
            sessions.append(current)
        current += timedelta(days=1)
    return tuple(sessions)


def _build_synthetic_evidence() -> SyntheticEvidence:
    sessions = _synthetic_sessions()
    calendar = build_trading_calendar(
        sessions=sessions,
        calendar_version="synthetic-offline-calendar-v1",
        session_rule_version="twice-weekly-bounded-v1",
        dataset_revision="synthetic-m4-dataset-v1",
        source=CalendarSource.SYNTHETIC_OFFLINE,
        source_reference_ids=("deterministic-m4-generator-v1",),
        generated_at=datetime(2016, 1, 1, tzinfo=UTC),
    )
    samples: list[ResearchSample] = []
    fixed_hashes = {
        name: deterministic_hash({"synthetic_evidence": name, "version": 1})
        for name in (
            "transformer_input",
            "transformer_manifest",
            "feature_lineage",
            "feature_content",
        )
    }
    horizons = cast(tuple[Horizon, ...], (1, 5, 20))
    for index, session in enumerate(sessions[:-20]):
        as_of = datetime.combine(session, time(21), tzinfo=UTC)
        signal = math.sin(index / 6.0) + 0.35 * math.cos(index / 17.0)
        volatility = 0.012 + 0.006 * (1.0 + math.sin(index / 23.0))
        compression = 0.5 + 0.45 * math.cos(index / 11.0)
        channel_position = math.tanh(signal)
        atr_score = signal / max(volatility * 80.0, 0.1)
        numeric: dict[str, float | None] = {
            "momentum": signal,
            "volatility": volatility,
            "compression": compression,
            "channel_position": channel_position,
            "atr_breakout_score": atr_score,
        }
        for horizon in horizons:
            label_end = datetime.combine(
                sessions[index + horizon], time(21), tzinfo=UTC
            )
            noise = 0.004 * math.sin(index * 1.7 + horizon)
            forward_return = 0.012 * math.tanh(signal) * math.sqrt(horizon / 5) + noise
            direction = (
                DirectionLabel.UP
                if forward_return > 0.002
                else DirectionLabel.DOWN
                if forward_return < -0.002
                else DirectionLabel.FLAT
            )
            breakout = (
                BreakoutEvent.UP
                if signal > 0.85
                else BreakoutEvent.DOWN
                if signal < -0.85
                else BreakoutEvent.NONE
            )
            event_at = (
                datetime.combine(sessions[index + 1], time(21), tzinfo=UTC)
                if breakout is not BreakoutEvent.NONE
                else None
            )
            range_exit = abs(signal) > 0.70 if horizon in {5, 20} else None
            range_exit_at = None
            if range_exit:
                event_offset = 1 + index % horizon
                range_exit_at = datetime.combine(
                    sessions[index + event_offset], time(21), tzinfo=UTC
                )
            target = HorizonTarget(
                horizon=horizon,
                forecast_direction=direction,
                executable_direction=direction,
                capital_gain_return=forward_return,
                total_return=forward_return + 0.0002 * horizon,
                executable_return=forward_return - 0.0006,
                forecast_mfe=max(0.0, forward_return + volatility * math.sqrt(horizon)),
                forecast_mae=min(0.0, forward_return - volatility * math.sqrt(horizon)),
                executable_mfe=max(
                    0.0, forward_return + volatility * math.sqrt(horizon) - 0.0006
                ),
                executable_mae=min(
                    0.0, forward_return - volatility * math.sqrt(horizon) - 0.0006
                ),
                breakout_event=breakout,
                breakout_event_at=event_at,
                range_exit=range_exit,
                range_exit_at=range_exit_at,
                label_end_at=label_end,
                resolved=True,
            )
            feature_values_hash = deterministic_hash(
                {"as_of": as_of.isoformat(), "horizon": horizon, "numeric": numeric}
            )
            samples.append(
                ResearchSample(
                    symbol="US.AAPL",
                    as_of=as_of,
                    horizon=horizon,
                    numeric_features=numeric,
                    categorical_features={
                        "state": "uptrend"
                        if signal > 0.2
                        else "downtrend"
                        if signal < -0.2
                        else "range"
                    },
                    transform_flags={name: False for name in FEATURE_NAMES},
                    ood_score=abs(signal) / 4.0,
                    target=target,
                    feature_set_version="synthetic-features-v1",
                    label_version="synthetic-labels-v1",
                    threshold_version="synthetic-thresholds-v1",
                    calendar_version=calendar.calendar_version,
                    calendar_hash=calendar.input_hash,
                    calendar_source=CalendarSource.SYNTHETIC_OFFLINE,
                    transformer_version="synthetic-transformer-v1",
                    transformer_input_hash=fixed_hashes["transformer_input"],
                    transformer_manifest_hash=fixed_hashes["transformer_manifest"],
                    universe_version="synthetic-universe-v1",
                    dataset_revision=calendar.dataset_revision,
                    adjustment_revision="synthetic-adjustment-v1",
                    feature_lineage_hash=fixed_hashes["feature_lineage"],
                    feature_content_hash=fixed_hashes["feature_content"],
                    feature_values_hash=feature_values_hash,
                    feature_source_max_available_at=as_of,
                    feature_source_max_ingested_at=as_of + timedelta(minutes=1),
                    data_quality_score=1.0,
                    label_start_at=as_of,
                    label_end_at=label_end,
                )
            )
    ordered = tuple(
        sorted(samples, key=lambda item: (item.as_of, item.symbol, item.horizon))
    )
    input_hash = compute_research_dataset_input_hash(ordered)
    dataset = ResearchDataset(
        dataset_id=f"research-{input_hash[:24]}",
        input_hash=input_hash,
        feature_set_version="synthetic-features-v1",
        label_version="synthetic-labels-v1",
        threshold_version="synthetic-thresholds-v1",
        transformer_version="synthetic-transformer-v1",
        transformer_input_hash=fixed_hashes["transformer_input"],
        transformer_manifest_hash=fixed_hashes["transformer_manifest"],
        universe_version="synthetic-universe-v1",
        dataset_revision=calendar.dataset_revision,
        calendar_version=calendar.calendar_version,
        calendar_hash=calendar.input_hash,
        calendar_source=CalendarSource.SYNTHETIC_OFFLINE,
        sample_count=len(ordered),
        samples=ordered,
    )
    return SyntheticEvidence(dataset=dataset, calendar=calendar)


def _write_source_artifacts(
    root: Path,
    evidence: SyntheticEvidence,
    manifest: SplitManifest,
) -> list[Path]:
    calendar_path = root / "calendar-evidence.json"
    write_immutable_json(calendar_path, evidence.calendar.model_dump(mode="json"))
    split_path = root / "split-manifest.json"
    write_immutable_json(split_path, manifest.model_dump(mode="json"))
    transformer_path = root / "transformer-evidence.json"
    write_immutable_json(
        transformer_path,
        {
            "transformer_version": evidence.dataset.transformer_version,
            "transformer_input_hash": evidence.dataset.transformer_input_hash,
            "transformer_manifest_hash": evidence.dataset.transformer_manifest_hash,
            "fit_partition": "outer-01-train-only",
            "outer_test_mutation_allowed": False,
        },
    )
    dataset_path = root / "research-dataset.parquet"
    _write_parquet_immutable(
        dataset_path,
        [
            {
                "sample_key": _sample_key_text(sample),
                "research_sample_json": sample.model_dump_json(),
                "symbol": sample.symbol,
                "as_of": sample.as_of,
                "horizon": sample.horizon,
                **{
                    name: cast(float, sample.numeric_features[name])
                    for name in FEATURE_NAMES
                },
                "direction": sample.target.forecast_direction.value,
                "breakout": sample.target.breakout_event.value,
                "capital_gain_return": sample.target.capital_gain_return,
                "forecast_mfe": sample.target.forecast_mfe,
                "forecast_mae": sample.target.forecast_mae,
                "label_end_at": sample.label_end_at,
                "feature_lineage_hash": sample.feature_lineage_hash,
                "feature_content_hash": sample.feature_content_hash,
                "feature_values_hash": sample.feature_values_hash,
                "transformer_input_hash": sample.transformer_input_hash,
                "transformer_manifest_hash": sample.transformer_manifest_hash,
                "feature_set_version": sample.feature_set_version,
                "label_version": sample.label_version,
                "calendar_hash": sample.calendar_hash,
            }
            for sample in evidence.dataset.samples
        ],
    )
    labels_path = root / "label-evidence.parquet"
    _write_parquet_immutable(
        labels_path,
        [
            {
                "sample_key": _sample_key_text(sample),
                "symbol": sample.symbol,
                "as_of": sample.as_of,
                "horizon": sample.horizon,
                "direction": sample.target.forecast_direction.value,
                "breakout": sample.target.breakout_event.value,
                "return": sample.target.capital_gain_return,
                "mfe": sample.target.forecast_mfe,
                "mae": sample.target.forecast_mae,
                "range_exit": sample.target.range_exit,
                "range_exit_at": sample.target.range_exit_at,
                "breakout_event_at": sample.target.breakout_event_at,
                "label_end_at": sample.label_end_at,
            }
            for sample in evidence.dataset.samples
        ],
    )
    return [
        calendar_path,
        split_path,
        transformer_path,
        dataset_path,
        labels_path,
    ]


def _write_parquet_immutable(path: Path, rows: list[dict[str, Any]]) -> None:
    if path.exists():
        raise FileExistsError(f"immutable artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(
        table,
        path,
        compression="NONE",
        data_page_version="1.0",
        version="2.6",
        write_statistics=True,
    )


def _trial_plans(manifest: SplitManifest) -> tuple[TrialPlan, ...]:
    plans: list[TrialPlan] = []
    for fold in manifest.outer_folds:
        prefix = fold.fold_id
        for task, horizons in CLASSIFICATION_TASK_HORIZONS:
            for horizon in horizons:
                if task is TaskName.FORECAST_DIRECTION:
                    for family in (
                        "historical",
                        "channel",
                        "logistic",
                        "lgbm",
                    ):
                        candidate_count = 2 if family == "lgbm" else 1
                        for candidate_index in range(candidate_count):
                            kind = f"{task.value}_h{horizon}_{family}"
                            plans.append(
                                TrialPlan(
                                    f"{prefix}-{kind}-c{candidate_index}",
                                    prefix,
                                    kind,
                                    task,
                                    horizon,
                                    "brier_score",
                                    ("log_loss",),
                                    candidate_index,
                                )
                            )
                else:
                    kind = f"{task.value}_h{horizon}"
                    plans.append(
                        TrialPlan(
                            f"{prefix}-{kind}",
                            prefix,
                            kind,
                            task,
                            horizon,
                            "brier_score",
                        )
                    )
        plans.extend(
            (
                TrialPlan(
                    f"{prefix}-return-q",
                    prefix,
                    "return_quantiles",
                    TaskName.CAPITAL_GAIN_RETURN,
                    5,
                    "pinball_loss",
                ),
                TrialPlan(
                    f"{prefix}-mfe-q",
                    prefix,
                    "mfe_quantiles",
                    TaskName.FORECAST_MFE,
                    5,
                    "pinball_loss",
                ),
                TrialPlan(
                    f"{prefix}-mae-q",
                    prefix,
                    "mae_quantiles",
                    TaskName.FORECAST_MAE,
                    5,
                    "pinball_loss",
                ),
                TrialPlan(
                    f"{prefix}-survival-5",
                    prefix,
                    "survival_5",
                    TaskName.RANGE_EXIT_SURVIVAL,
                    5,
                    "time_dependent_brier",
                ),
                TrialPlan(
                    f"{prefix}-survival-20",
                    prefix,
                    "survival_20",
                    TaskName.RANGE_EXIT_SURVIVAL,
                    20,
                    "time_dependent_brier",
                ),
                TrialPlan(
                    f"{prefix}-failed-sentinel",
                    prefix,
                    "failed_sentinel",
                    TaskName.FORECAST_DIRECTION,
                    5,
                    "brier_score",
                ),
            )
        )
    return tuple(plans)


def _preregister_trials(
    ledger: JsonlTrialLedger,
    plans: tuple[TrialPlan, ...],
    manifest: SplitManifest,
    dataset: ResearchDataset,
) -> None:
    folds = {fold.fold_id: fold for fold in manifest.outer_folds}
    for index, plan in enumerate(plans):
        fold = folds[plan.fold_id]
        ledger.register(
            BacktestTrial(
                trial_id=plan.trial_id,
                registered_at=FIXED_AT + timedelta(seconds=index),
                owner="local-researcher",
                hypothesis=f"Evaluate preregistered {plan.kind} on one fixed outer fold.",
                task=plan.task,
                horizon=plan.horizon,
                model_version=f"synthetic-{plan.kind}-v1",
                feature_set_version=dataset.feature_set_version,
                threshold_version=dataset.threshold_version,
                label_version=dataset.label_version,
                split_version=manifest.split_version,
                dataset_input_hash=dataset.input_hash,
                calendar_hash=manifest.calendar_hash,
                evaluation_window_id=fold.evaluation_window.window_id,
                evaluation_window=fold.evaluation_window,
                primary_metrics=(plan.primary_metric,),
                secondary_metrics=plan.secondary_metrics,
                frozen_parameters={
                    "seed": SEED,
                    "candidate_index": plan.candidate_index,
                    "search_budget": 2,
                    "num_threads": 1,
                    "num_boost_round": 6 + plan.candidate_index * 2,
                },
                parent_trial_id=None,
                status=TrialStatus.REGISTERED,
                result=None,
                stop_reason=None,
                touched_lockbox=False,
                consumed_at=None,
            )
        )


def _evaluate_folds(
    ledger: JsonlTrialLedger,
    plans: tuple[TrialPlan, ...],
    manifest: SplitManifest,
    dataset: ResearchDataset,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[FoldModelEvidence],
    list[dict[str, Any]],
]:
    by_key = {(item.symbol, item.as_of, item.horizon): item for item in dataset.samples}
    plan_map = {(item.fold_id, item.kind, item.candidate_index): item for item in plans}
    if 5 not in _task_horizons(TaskName.FORECAST_DIRECTION):
        raise RuntimeError("task matrix must include the economic direction horizon")
    reports: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    fold_models: list[FoldModelEvidence] = []
    quantile_prediction_rows: list[dict[str, Any]] = []
    finish_index = 0

    def finish(
        plan: TrialPlan, result: dict[str, float] | None, *, failed: bool = False
    ) -> None:
        nonlocal finish_index
        ledger.finish(
            plan.trial_id,
            status=TrialStatus.FAILED if failed else TrialStatus.SUCCEEDED,
            result=result,
            finished_at=FIXED_AT + timedelta(days=1, seconds=finish_index),
            stop_reason="preregistered invalid-candidate sentinel" if failed else None,
        )
        finish_index += 1

    for fold in manifest.outer_folds:
        arrays = _fold_arrays(fold, by_key, 5)
        train_x = _features(arrays.train)
        calibration_x = _features(arrays.calibration)
        selection_x = _features(arrays.selection)
        test_x = _features(arrays.test)
        train_y = _direction_labels(arrays.train)
        calibration_y = _direction_labels(arrays.calibration)
        selection_y = _direction_labels(arrays.selection)
        test_y = _direction_labels(arrays.test)
        family_metrics: dict[str, dict[str, float]] = {}

        historical = HistoricalFrequencyBaseline.fit(
            train_y,
            classes=CLASS_ORDER,
            task=TaskName.FORECAST_DIRECTION,
            horizon=5,
            training_start=arrays.train[0].as_of,
            training_end=arrays.train[-1].as_of,
            dataset_input_hash=dataset.input_hash,
            seed=SEED,
        )
        historical_p = historical.predict_proba(tuple({} for _ in arrays.test))
        family_metrics["historical_frequency"] = _classification_metrics(
            test_y, historical_p, CLASS_ORDER
        )
        finish(
            plan_map[(fold.fold_id, "forecast_direction_h5_historical", 0)],
            family_metrics["historical_frequency"],
        )

        channel = ChannelAtrRuleBaseline.create(
            classes=CLASS_ORDER,
            channel_buffer=0.10,
            task=TaskName.FORECAST_DIRECTION,
            horizon=5,
            training_start=arrays.train[0].as_of,
            training_end=arrays.train[-1].as_of,
            dataset_input_hash=dataset.input_hash,
            seed=SEED,
        )
        channel_p = channel.predict_proba(
            tuple(
                {
                    "channel_position": cast(
                        float, row.numeric_features["channel_position"]
                    ),
                    "atr_breakout_score": cast(
                        float, row.numeric_features["atr_breakout_score"]
                    ),
                }
                for row in arrays.test
            )
        )
        family_metrics["channel_atr_rule"] = _classification_metrics(
            test_y, channel_p, CLASS_ORDER
        )
        finish(
            plan_map[(fold.fold_id, "forecast_direction_h5_channel", 0)],
            family_metrics["channel_atr_rule"],
        )

        logistic = LogisticBaseline.fit(
            train_x,
            train_y,
            feature_names=FEATURE_NAMES,
            classes=CLASS_ORDER,
            c=1.0,
            max_iter=500,
            task=TaskName.FORECAST_DIRECTION,
            horizon=5,
            training_start=arrays.train[0].as_of,
            training_end=arrays.train[-1].as_of,
            dataset_input_hash=dataset.input_hash,
            seed=SEED,
        )
        logistic_p = logistic.predict_proba(test_x, feature_names=FEATURE_NAMES)
        family_metrics["logistic_regression"] = _classification_metrics(
            test_y, logistic_p, CLASS_ORDER
        )
        finish(
            plan_map[(fold.fold_id, "forecast_direction_h5_logistic", 0)],
            family_metrics["logistic_regression"],
        )

        train_partition = _training_partition(
            fold, manifest, TaskName.FORECAST_DIRECTION, 5, arrays.train
        )
        training = ClassificationTrainingData.build(
            train_x,
            train_y,
            sample_keys=tuple(_sample_key(row) for row in arrays.train),
            feature_names=FEATURE_NAMES,
            dataset_input_hash=dataset.input_hash,
            partition=train_partition,
        )
        calibration_partition = _calibration_partition(
            fold, manifest, TaskName.FORECAST_DIRECTION, 5, arrays.calibration
        )
        candidates: list[
            tuple[ClassifierArtifact, TemperatureScaler, float, np.ndarray]
        ] = []
        for candidate_index in range(2):
            artifact = LightGbmClassifierTrainer(
                ClassifierConfig(
                    task=TaskName.FORECAST_DIRECTION,
                    horizon=5,
                    model_version=f"lgbm-direction-c{candidate_index}-v1",
                    asset_group="all",
                    class_order=CLASS_ORDER,
                    seed=SEED,
                    num_threads=1,
                    num_boost_round=6 + candidate_index * 2,
                    learning_rate=0.08 + candidate_index * 0.02,
                    num_leaves=7,
                    min_data_in_leaf=8,
                )
            ).fit(training)
            calibration_raw = artifact.predict_proba(
                calibration_x,
                feature_names=FEATURE_NAMES,
                expected_artifact_hash=artifact.artifact_hash,
            )
            scaler = TemperatureScaler.fit(
                np.log(np.clip(calibration_raw, 1e-9, 1.0)),
                calibration_y,
                class_order=CLASS_ORDER,
                calibration_partition=calibration_partition,
                sample_keys=tuple(_sample_key(row) for row in arrays.calibration),
                source_model=artifact,
                expected_source_model_artifact_hash=artifact.artifact_hash,
            )
            selection_raw = artifact.predict_proba(
                selection_x,
                feature_names=FEATURE_NAMES,
                expected_artifact_hash=artifact.artifact_hash,
            )
            selection_p = scaler.apply(
                np.log(np.clip(selection_raw, 1e-9, 1.0)),
                expected_artifact_hash=scaler.artifact_hash,
                source_model_artifact_hash=artifact.artifact_hash,
            )
            selection_score = multiclass_brier_score(
                selection_y, selection_p, classes=CLASS_ORDER
            )
            candidates.append((artifact, scaler, selection_score, selection_p))
        selected_artifact, selected_scaler, _, _ = min(
            candidates, key=lambda item: item[2]
        )
        for candidate_index, (artifact, scaler, _, _) in enumerate(candidates):
            before = (artifact.model_dump_json(), scaler.model_dump_json())
            raw = artifact.predict_proba(
                test_x,
                feature_names=FEATURE_NAMES,
                expected_artifact_hash=artifact.artifact_hash,
            )
            calibrated = scaler.apply(
                np.log(np.clip(raw, 1e-9, 1.0)),
                expected_artifact_hash=scaler.artifact_hash,
                source_model_artifact_hash=artifact.artifact_hash,
            )
            if before != (artifact.model_dump_json(), scaler.model_dump_json()):
                raise RuntimeError("outer test mutated model or calibration artifact")
            result = _classification_metrics(test_y, calibrated, CLASS_ORDER)
            finish(
                plan_map[(fold.fold_id, "forecast_direction_h5_lgbm", candidate_index)],
                result,
            )
            if artifact.artifact_hash == selected_artifact.artifact_hash:
                family_metrics["lightgbm"] = result
        selected_raw = selected_artifact.predict_proba(
            test_x,
            feature_names=FEATURE_NAMES,
            expected_artifact_hash=selected_artifact.artifact_hash,
        )
        selected_logits = np.log(np.clip(selected_raw, 1e-9, 1.0))
        selected_p = selected_scaler.apply(
            selected_logits,
            expected_artifact_hash=selected_scaler.artifact_hash,
            source_model_artifact_hash=selected_artifact.artifact_hash,
        )
        calibration_bins = calibration_report(
            test_y, selected_p, classes=CLASS_ORDER, n_bins=5
        )
        task_horizon_metrics: dict[str, Any] = {
            "forecast_direction:h5": {"family_metrics": family_metrics}
        }
        for horizon in _task_horizons(TaskName.FORECAST_DIRECTION):
            if horizon == 5:
                continue
            extra_arrays = _fold_arrays(fold, by_key, horizon)
            extra_metrics = _evaluate_direction_horizon(
                fold,
                manifest,
                dataset,
                extra_arrays,
                horizon,
                finish,
                plan_map,
            )
            task_horizon_metrics[f"forecast_direction:h{horizon}"] = {
                "family_metrics": extra_metrics
            }

        breakout_metrics: dict[str, dict[str, float]] = {}
        for horizon in _task_horizons(TaskName.BREAKOUT):
            breakout_arrays = _fold_arrays(fold, by_key, horizon)
            breakout_metric = _evaluate_breakout(
                fold,
                manifest,
                dataset,
                breakout_arrays,
                horizon,
                finish,
                plan_map[(fold.fold_id, f"breakout_h{horizon}", 0)],
            )
            breakout_metrics[str(horizon)] = breakout_metric
            task_horizon_metrics[f"breakout:h{horizon}"] = breakout_metric

        quantile_metrics: dict[str, dict[str, Any]] = {}
        for name, task, outcome_name in (
            (
                "return_quantiles",
                TaskName.CAPITAL_GAIN_RETURN,
                "capital_gain_return",
            ),
            ("mfe_quantiles", TaskName.FORECAST_MFE, "forecast_mfe"),
            ("mae_quantiles", TaskName.FORECAST_MAE, "forecast_mae"),
        ):
            metrics, prediction_rows = _evaluate_quantiles(
                fold,
                manifest,
                dataset,
                arrays,
                task,
                outcome_name,
                finish,
                plan_map[(fold.fold_id, name, 0)],
            )
            quantile_metrics[name] = metrics
            quantile_prediction_rows.extend(prediction_rows)
        survival_metrics = {
            str(horizon): _evaluate_survival(
                fold,
                by_key,
                manifest.calendar_sessions,
                horizon,
                finish,
                plan_map[(fold.fold_id, f"survival_{horizon}", 0)],
            )
            for horizon in cast(tuple[Horizon, ...], (5, 20))
        }
        finish(plan_map[(fold.fold_id, "failed_sentinel", 0)], None, failed=True)
        ledger.consume_window(
            fold.evaluation_window,
            consumed_at=FIXED_AT + timedelta(days=2, seconds=fold.fold_index),
            reason="m4_outer_evaluation",
        )
        explanation = explain_tree_prediction(
            selected_artifact,
            test_x[0],
            feature_names=FEATURE_NAMES,
            class_name="up",
            top_n=3,
            expected_artifact_hash=selected_artifact.artifact_hash,
        )
        report = {
            "fold_id": fold.fold_id,
            "fold_hash": fold.fold_hash,
            "test_window_id": fold.evaluation_window.window_id,
            "train_start": fold.train_start_session.isoformat(),
            "train_end": fold.train_end_session.isoformat(),
            "calibration_start": fold.calibration_start_session.isoformat(),
            "calibration_end": fold.calibration_end_session.isoformat(),
            "selection_start": fold.selection_start_session.isoformat(),
            "selection_end": fold.selection_end_session.isoformat(),
            "test_start": fold.test_start_session.isoformat(),
            "test_end": fold.test_end_session.isoformat(),
            "purged_sample_count": len(fold.purged_sample_keys),
            "embargoed_sample_count": len(fold.embargoed_sample_keys),
            "embargo_sessions": len(fold.embargo_sessions),
            "family_metrics": family_metrics,
            "breakout_metrics": breakout_metrics,
            "task_horizon_metrics": task_horizon_metrics,
            "quantile_metrics": quantile_metrics,
            "survival_metrics": survival_metrics,
            "calibration_bins": calibration_bins.model_dump(mode="json")["bins"],
            "calibration_ece": calibration_bins.ece,
            "top_features": [
                item.model_dump(mode="json") for item in explanation.top_contributions
            ],
            "oos_prediction_rows": _oos_prediction_rows(
                fold, arrays.test, test_y, selected_p
            ),
        }
        reports.append(report)
        for family, metrics in sorted(family_metrics.items()):
            metric_rows.append({"fold_id": fold.fold_id, "family": family, **metrics})
        fold_models.append(
            FoldModelEvidence(
                fold=fold,
                classifier=selected_artifact,
                calibrator=selected_scaler,
                test_rows=arrays.test,
                raw_probabilities=tuple(
                    tuple(float(value) for value in row) for row in selected_raw
                ),
                raw_logits=tuple(
                    tuple(float(value) for value in row) for row in selected_logits
                ),
                calibrated_probabilities=tuple(
                    tuple(float(value) for value in row) for row in selected_p
                ),
            )
        )
    if not fold_models:
        raise RuntimeError("M4 produced no selected model")
    return reports, metric_rows, fold_models, quantile_prediction_rows


def _fold_arrays(
    fold: OuterFold,
    by_key: dict[tuple[str, datetime, Horizon], ResearchSample],
    horizon: Horizon,
) -> FoldArrays:
    def rows(
        keys: tuple[tuple[str, datetime, Horizon], ...],
    ) -> tuple[ResearchSample, ...]:
        return tuple(by_key[key] for key in keys if key[2] == horizon)

    return FoldArrays(
        train=rows(fold.train_sample_keys),
        calibration=rows(fold.calibration_sample_keys),
        selection=rows(fold.selection_sample_keys),
        test=rows(fold.test_sample_keys),
    )


def _sample_key(sample: ResearchSample) -> tuple[str, datetime, Horizon]:
    return (sample.symbol, sample.as_of, sample.horizon)


def _sample_key_text(sample: ResearchSample) -> str:
    return f"{sample.symbol}|{sample.as_of.isoformat()}|{sample.horizon}"


def _features(rows: tuple[ResearchSample, ...]) -> np.ndarray:
    return np.asarray(
        [
            [cast(float, row.numeric_features[name]) for name in FEATURE_NAMES]
            for row in rows
        ],
        dtype=np.float64,
    )


def _direction_labels(rows: tuple[ResearchSample, ...]) -> tuple[str, ...]:
    return tuple(row.target.forecast_direction.value for row in rows)


def _classification_metrics(
    labels: tuple[str, ...], probabilities: np.ndarray, classes: tuple[str, ...]
) -> dict[str, float]:
    return {
        "brier_score": multiclass_brier_score(labels, probabilities, classes=classes),
        "log_loss": multiclass_log_loss(labels, probabilities, classes=classes),
    }


def _evaluate_direction_horizon(
    fold: OuterFold,
    manifest: SplitManifest,
    dataset: ResearchDataset,
    arrays: FoldArrays,
    horizon: Horizon,
    finish: Any,
    plan_map: dict[tuple[str, str, int], TrialPlan],
) -> dict[str, dict[str, float]]:
    train_x = _features(arrays.train)
    calibration_x = _features(arrays.calibration)
    selection_x = _features(arrays.selection)
    test_x = _features(arrays.test)
    train_y = _direction_labels(arrays.train)
    calibration_y = _direction_labels(arrays.calibration)
    selection_y = _direction_labels(arrays.selection)
    test_y = _direction_labels(arrays.test)
    metrics: dict[str, dict[str, float]] = {}
    common = {
        "task": TaskName.FORECAST_DIRECTION,
        "horizon": horizon,
        "training_start": arrays.train[0].as_of,
        "training_end": arrays.train[-1].as_of,
        "dataset_input_hash": dataset.input_hash,
        "seed": SEED,
    }
    historical = HistoricalFrequencyBaseline.fit(
        train_y,
        classes=CLASS_ORDER,
        **common,  # type: ignore[arg-type]
    )
    metrics["historical_frequency"] = _classification_metrics(
        test_y,
        historical.predict_proba(tuple({} for _ in arrays.test)),
        CLASS_ORDER,
    )
    finish(
        plan_map[(fold.fold_id, f"forecast_direction_h{horizon}_historical", 0)],
        metrics["historical_frequency"],
    )
    channel = ChannelAtrRuleBaseline.create(
        classes=CLASS_ORDER,
        channel_buffer=0.10,
        **common,  # type: ignore[arg-type]
    )
    channel_probabilities = channel.predict_proba(
        tuple(
            {
                "channel_position": cast(
                    float, row.numeric_features["channel_position"]
                ),
                "atr_breakout_score": cast(
                    float, row.numeric_features["atr_breakout_score"]
                ),
            }
            for row in arrays.test
        )
    )
    metrics["channel_atr_rule"] = _classification_metrics(
        test_y, channel_probabilities, CLASS_ORDER
    )
    finish(
        plan_map[(fold.fold_id, f"forecast_direction_h{horizon}_channel", 0)],
        metrics["channel_atr_rule"],
    )
    logistic = LogisticBaseline.fit(
        train_x,
        train_y,
        feature_names=FEATURE_NAMES,
        classes=CLASS_ORDER,
        c=1.0,
        max_iter=500,
        **common,  # type: ignore[arg-type]
    )
    metrics["logistic_regression"] = _classification_metrics(
        test_y,
        logistic.predict_proba(test_x, feature_names=FEATURE_NAMES),
        CLASS_ORDER,
    )
    finish(
        plan_map[(fold.fold_id, f"forecast_direction_h{horizon}_logistic", 0)],
        metrics["logistic_regression"],
    )
    training_partition = _training_partition(
        fold, manifest, TaskName.FORECAST_DIRECTION, horizon, arrays.train
    )
    training = ClassificationTrainingData.build(
        train_x,
        train_y,
        sample_keys=tuple(_sample_key(row) for row in arrays.train),
        feature_names=FEATURE_NAMES,
        dataset_input_hash=dataset.input_hash,
        partition=training_partition,
    )
    calibration_partition = _calibration_partition(
        fold, manifest, TaskName.FORECAST_DIRECTION, horizon, arrays.calibration
    )
    candidates: list[tuple[ClassifierArtifact, TemperatureScaler, float]] = []
    for candidate_index in range(2):
        artifact = LightGbmClassifierTrainer(
            ClassifierConfig(
                task=TaskName.FORECAST_DIRECTION,
                horizon=horizon,
                model_version=f"lgbm-direction-h{horizon}-c{candidate_index}-v1",
                asset_group="all",
                class_order=CLASS_ORDER,
                seed=SEED,
                num_threads=1,
                num_boost_round=6 + candidate_index * 2,
                learning_rate=0.08 + candidate_index * 0.02,
                num_leaves=7,
                min_data_in_leaf=8,
            )
        ).fit(training)
        calibration_raw = artifact.predict_proba(
            calibration_x,
            feature_names=FEATURE_NAMES,
            expected_artifact_hash=artifact.artifact_hash,
        )
        scaler = TemperatureScaler.fit(
            np.log(np.clip(calibration_raw, 1e-9, 1.0)),
            calibration_y,
            class_order=CLASS_ORDER,
            calibration_partition=calibration_partition,
            sample_keys=tuple(_sample_key(row) for row in arrays.calibration),
            source_model=artifact,
            expected_source_model_artifact_hash=artifact.artifact_hash,
        )
        selection_raw = artifact.predict_proba(
            selection_x,
            feature_names=FEATURE_NAMES,
            expected_artifact_hash=artifact.artifact_hash,
        )
        selection_probabilities = scaler.apply(
            np.log(np.clip(selection_raw, 1e-9, 1.0)),
            expected_artifact_hash=scaler.artifact_hash,
            source_model_artifact_hash=artifact.artifact_hash,
        )
        candidates.append(
            (
                artifact,
                scaler,
                multiclass_brier_score(
                    selection_y, selection_probabilities, classes=CLASS_ORDER
                ),
            )
        )
    selected = min(candidates, key=lambda item: item[2])[0].artifact_hash
    for candidate_index, (artifact, scaler, _) in enumerate(candidates):
        raw = artifact.predict_proba(
            test_x,
            feature_names=FEATURE_NAMES,
            expected_artifact_hash=artifact.artifact_hash,
        )
        probabilities = scaler.apply(
            np.log(np.clip(raw, 1e-9, 1.0)),
            expected_artifact_hash=scaler.artifact_hash,
            source_model_artifact_hash=artifact.artifact_hash,
        )
        result = _classification_metrics(test_y, probabilities, CLASS_ORDER)
        finish(
            plan_map[
                (
                    fold.fold_id,
                    f"forecast_direction_h{horizon}_lgbm",
                    candidate_index,
                )
            ],
            result,
        )
        if artifact.artifact_hash == selected:
            metrics["lightgbm"] = result
    return metrics


def _oos_prediction_rows(
    fold: OuterFold,
    rows: tuple[ResearchSample, ...],
    labels: tuple[str, ...],
    probabilities: np.ndarray,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row, label, probability in zip(rows, labels, probabilities, strict=True):
        class_index = CLASS_ORDER.index(label)
        one_hot = np.zeros(len(CLASS_ORDER), dtype=np.float64)
        one_hot[class_index] = 1.0
        output.append(
            {
                "fold_id": fold.fold_id,
                "window_id": fold.evaluation_window.window_id,
                "sample_key": _sample_key_text(row),
                "symbol": row.symbol,
                "as_of": row.as_of.isoformat(),
                "horizon": row.horizon,
                "month": row.as_of.strftime("%Y-%m"),
                "brier_score": float(np.sum(np.square(probability - one_hot))),
                "capital_gain_return": cast(float, row.target.capital_gain_return),
            }
        )
    return output


def _training_partition(
    fold: OuterFold,
    manifest: SplitManifest,
    task: TaskName,
    horizon: Horizon,
    rows: tuple[ResearchSample, ...],
) -> TrainingPartitionEvidence:
    window = EvaluationWindow(
        window_id=f"{manifest.split_version}.{fold.fold_id}.{task.value}.h{horizon}.train",
        role=EvaluationRole.TRAIN,
        scope=EvaluationScope.SYNTHETIC_OFFLINE,
        start_session=fold.train_start_session,
        end_session=fold.train_end_session,
        split_version=manifest.split_version,
        dataset_input_hash=manifest.dataset_input_hash,
        calendar_hash=manifest.calendar_hash,
        consumed_at=None,
        consumption_reason=None,
    )
    return TrainingPartitionEvidence.register(
        partition_id=f"partition-{fold.fold_id}-{task.value}-h{horizon}",
        task=task,
        horizon=horizon,
        evaluation_window=window,
        registered_sample_keys=tuple(_sample_key(row) for row in rows),
        split_manifest_hash=manifest.manifest_hash,
        source_fold_hash=fold.fold_hash,
    )


def _calibration_partition(
    fold: OuterFold,
    manifest: SplitManifest,
    task: TaskName,
    horizon: Horizon,
    rows: tuple[ResearchSample, ...],
) -> CalibrationPartitionEvidence:
    window = EvaluationWindow(
        window_id=f"{manifest.split_version}.{fold.fold_id}.{task.value}.h{horizon}.calibration",
        role=EvaluationRole.CALIBRATION,
        scope=EvaluationScope.SYNTHETIC_OFFLINE,
        start_session=fold.calibration_start_session,
        end_session=fold.calibration_end_session,
        split_version=manifest.split_version,
        dataset_input_hash=manifest.dataset_input_hash,
        calendar_hash=manifest.calendar_hash,
        consumed_at=None,
        consumption_reason=None,
    )
    return CalibrationPartitionEvidence.register(
        partition_id=f"calibration-{fold.fold_id}-{task.value}-h{horizon}",
        task=task,
        horizon=horizon,
        asset_group="all",
        evaluation_window=window,
        registered_sample_keys=tuple(_sample_key(row) for row in rows),
        split_manifest_hash=manifest.manifest_hash,
        source_fold_hash=fold.fold_hash,
    )


def _evaluate_breakout(
    fold: OuterFold,
    manifest: SplitManifest,
    dataset: ResearchDataset,
    arrays: FoldArrays,
    horizon: Horizon,
    finish: Any,
    plan: TrialPlan,
) -> dict[str, float]:
    classes = ("no_breakout", "breakout")

    def labels(rows: tuple[ResearchSample, ...]) -> tuple[str, ...]:
        return tuple(
            "breakout"
            if row.target.breakout_event is not BreakoutEvent.NONE
            else "no_breakout"
            for row in rows
        )

    partition = _training_partition(
        fold, manifest, TaskName.BREAKOUT, horizon, arrays.train
    )
    training = ClassificationTrainingData.build(
        _features(arrays.train),
        labels(arrays.train),
        sample_keys=tuple(_sample_key(row) for row in arrays.train),
        feature_names=FEATURE_NAMES,
        dataset_input_hash=dataset.input_hash,
        partition=partition,
    )
    artifact = LightGbmClassifierTrainer(
        ClassifierConfig(
            task=TaskName.BREAKOUT,
            horizon=horizon,
            model_version=f"lgbm-breakout-h{horizon}-{fold.fold_id}-v1",
            asset_group="all",
            class_order=classes,
            seed=SEED,
            num_threads=1,
            num_boost_round=6,
            learning_rate=0.1,
            num_leaves=7,
            min_data_in_leaf=8,
        )
    ).fit(training)
    probabilities = artifact.predict_proba(
        _features(arrays.test),
        feature_names=FEATURE_NAMES,
        expected_artifact_hash=artifact.artifact_hash,
    )
    result = {
        "brier_score": multiclass_brier_score(
            labels(arrays.test), probabilities, classes=classes
        )
    }
    finish(plan, result)
    return result


def _outcomes(rows: tuple[ResearchSample, ...], name: str) -> tuple[float, ...]:
    values: list[float] = []
    for row in rows:
        value = getattr(row.target, name)
        if value is None:
            raise RuntimeError(f"resolved synthetic target lacks {name}")
        values.append(float(value))
    return tuple(values)


def _evaluate_quantiles(
    fold: OuterFold,
    manifest: SplitManifest,
    dataset: ResearchDataset,
    arrays: FoldArrays,
    task: TaskName,
    outcome_name: str,
    finish: Any,
    plan: TrialPlan,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    partition = _training_partition(fold, manifest, task, 5, arrays.train)
    training = RegressionTrainingData.build(
        _features(arrays.train),
        _outcomes(arrays.train, outcome_name),
        sample_keys=tuple(_sample_key(row) for row in arrays.train),
        feature_names=FEATURE_NAMES,
        dataset_input_hash=dataset.input_hash,
        partition=partition,
    )
    artifact = LightGbmQuantileTrainer(
        QuantileConfig(
            task=task,
            horizon=5,
            model_version=f"lgbm-{task.value}-{fold.fold_id}-v1",
            asset_group="all",
            seed=SEED,
            num_threads=1,
            num_boost_round=6,
            learning_rate=0.10,
            num_leaves=7,
            min_data_in_leaf=8,
        )
    ).fit(training)
    raw_calibration_predictions = artifact.predict(
        _features(arrays.calibration),
        feature_names=FEATURE_NAMES,
        expected_artifact_hash=artifact.artifact_hash,
    )
    calibration_partition = _calibration_partition(
        fold, manifest, task, 5, arrays.calibration
    )
    calibration_adjustment = adjust_quantile_predictions(raw_calibration_predictions)
    calibration_predictions = np.asarray(calibration_adjustment.rows, dtype=np.float64)
    cqr = CqrCalibrator.fit(
        _outcomes(arrays.calibration, outcome_name),
        calibration_predictions[:, 0].tolist(),
        calibration_predictions[:, 4].tolist(),
        target_coverage=0.80,
        calibration_partition=calibration_partition,
        sample_keys=tuple(_sample_key(row) for row in arrays.calibration),
        source_model=artifact,
        expected_source_model_artifact_hash=artifact.artifact_hash,
    )
    before = (artifact.model_dump_json(), cqr.model_dump_json())
    raw_test_predictions = artifact.predict(
        _features(arrays.test),
        feature_names=FEATURE_NAMES,
        expected_artifact_hash=artifact.artifact_hash,
    )
    test_adjustment = adjust_quantile_predictions(raw_test_predictions)
    test_predictions = np.asarray(test_adjustment.rows, dtype=np.float64)
    interval = cqr.apply(
        test_predictions[:, 0].tolist(),
        test_predictions[:, 4].tolist(),
        expected_artifact_hash=cqr.artifact_hash,
        source_model_artifact_hash=artifact.artifact_hash,
    )
    if before != (artifact.model_dump_json(), cqr.model_dump_json()):
        raise RuntimeError("outer test mutated quantile or CQR artifact")
    actual = _outcomes(arrays.test, outcome_name)
    diagnostics = empirical_interval_diagnostics(
        actual,
        lower=interval.lower,
        upper=interval.upper,
        target_coverage=0.80,
    )
    pinball = float(
        np.mean(
            [
                mean_pinball_loss(
                    actual, test_predictions[:, index].tolist(), quantile=quantile
                )
                for index, quantile in enumerate(QUANTILES)
            ]
        )
    )
    result = {"pinball_loss": pinball}
    finish(plan, result)
    model_evidence_hash = deterministic_hash(
        {
            "fold_hash": fold.fold_hash,
            "task": task.value,
            "training_input_hash": training.input_hash,
            "quantiles": QUANTILES,
            "adjusted_test_predictions": test_predictions.tolist(),
        }
    )
    calibration_evidence_hash = deterministic_hash(
        {
            "calibration_partition_hash": calibration_partition.partition_hash,
            "target_coverage": interval.target_coverage,
            "lower": interval.lower,
            "upper": interval.upper,
        }
    )
    output: dict[str, Any] = {
        "pinball_loss": pinball,
        "coverage": diagnostics.empirical_coverage,
        "mean_width": diagnostics.average_width,
        "calibration_evidence_hash": calibration_evidence_hash,
        "model_evidence_hash": model_evidence_hash,
        "actual_calibration_artifact_hash": cqr.artifact_hash,
        "actual_model_artifact_hash": artifact.artifact_hash,
        "calibration_crossing_row_count": calibration_adjustment.crossing_row_count,
        "test_crossing_row_count": test_adjustment.crossing_row_count,
        "noncrossing_assertion_passed": True,
    }
    prediction_rows = [
        {
            "fold_id": fold.fold_id,
            "task": task.value,
            "sample_key": f"{row.symbol}|{row.as_of.isoformat()}|{row.horizon}",
            "as_of": row.as_of,
            "q10": float(prediction[0]),
            "q25": float(prediction[1]),
            "q50": float(prediction[2]),
            "q75": float(prediction[3]),
            "q90": float(prediction[4]),
            "rearranged": bool(
                np.any(
                    raw_test_predictions[index]
                    != np.asarray(prediction, dtype=np.float64)
                )
            ),
        }
        for index, (row, prediction) in enumerate(
            zip(arrays.test, test_adjustment.rows, strict=True)
        )
    ]
    return output, prediction_rows


def _evaluate_survival(
    fold: OuterFold,
    by_key: dict[tuple[str, datetime, Horizon], ResearchSample],
    calendar_sessions: tuple[date, ...],
    horizon: Horizon,
    finish: Any,
    plan: TrialPlan,
) -> dict[str, Any]:
    train = tuple(by_key[key] for key in fold.train_sample_keys if key[2] == horizon)
    test = tuple(by_key[key] for key in fold.test_sample_keys if key[2] == horizon)
    session_positions = {
        session: index for index, session in enumerate(calendar_sessions)
    }
    train_event_days = tuple(
        _range_exit_event_day(row, horizon, session_positions) for row in train
    )
    test_event_days = tuple(
        _range_exit_event_day(row, horizon, session_positions) for row in test
    )
    train_observed_days = tuple(
        _range_exit_observed_days(row, horizon, session_positions) for row in train
    )
    test_observed_days = tuple(
        _range_exit_observed_days(row, horizon, session_positions) for row in test
    )
    observations = tuple(
        RangeExitObservation(
            observation_id=f"{fold.fold_id}-{horizon}-{index:04d}",
            observed_days=observed_days,
            event_day=event_day,
            right_censored=False,
        )
        for index, (event_day, observed_days) in enumerate(
            zip(train_event_days, train_observed_days, strict=True)
        )
    )
    person_period = PersonPeriodEvidence.from_rows(
        build_person_period_rows(observations, max_horizon=horizon),
        horizon=cast(Literal[5, 20], horizon),
    )
    hazards: list[float] = []
    for day in range(1, horizon + 1):
        at_risk = sum(
            event_day is None or event_day >= day for event_day in train_event_days
        )
        events = sum(event_day == day for event_day in train_event_days)
        hazards.append(events / at_risk if at_risk else 0.0)
    curve = cumulative_incidence(hazards)
    squared_errors = [
        (float(event_day is not None and event_day <= day) - curve[day - 1]) ** 2
        for event_day in test_event_days
        for day in range(1, horizon + 1)
    ]
    brier = float(np.mean(squared_errors))
    event_count = sum(event_day is not None for event_day in test_event_days)
    event_rate = event_count / len(test_event_days)
    result = {"time_dependent_brier": brier}
    finish(plan, result)
    return {
        "time_dependent_brier": brier,
        "event_rate": event_rate,
        "person_period_row_count": len(person_period.rows),
        "person_period_hash": person_period.rows_hash,
        "observation_count": len(test_event_days),
        "event_count": event_count,
        "non_event_count": len(test_event_days) - event_count,
        "event_day_source": "range_exit_at_calendar_session_position",
        "censoring_day_source": "label_end_at_calendar_session_position",
        "timestamp_fidelity_passed": all(
            (event_day is not None) == bool(row.target.range_exit)
            and observed_days == horizon
            for row, event_day, observed_days in zip(
                test, test_event_days, test_observed_days, strict=True
            )
        ),
        "daily_hazards": hazards,
        "cumulative_exit_probabilities": list(curve),
    }


def _range_exit_event_day(
    row: ResearchSample,
    horizon: Horizon,
    session_positions: dict[date, int],
) -> int | None:
    if horizon not in {5, 20}:
        raise RuntimeError("range-exit event day requires horizon 5 or 20")
    if row.target.range_exit is not True:
        if row.target.range_exit_at is not None:
            raise RuntimeError("non-event range target contains a timestamp")
        return None
    event_at = row.target.range_exit_at
    if event_at is None:
        raise RuntimeError("positive range-exit target lacks event timestamp")
    anchor_position = session_positions[row.as_of.date()]
    event_position = session_positions[event_at.date()]
    event_day = event_position - anchor_position
    if not 1 <= event_day <= horizon or event_at > row.label_end_at:
        raise RuntimeError("range-exit timestamp falls outside its label horizon")
    return event_day


def _range_exit_observed_days(
    row: ResearchSample,
    horizon: Horizon,
    session_positions: dict[date, int],
) -> int:
    anchor_position = session_positions[row.as_of.date()]
    label_end_position = session_positions[row.label_end_at.date()]
    observed_days = label_end_position - anchor_position
    if observed_days != horizon:
        raise RuntimeError("range-exit censoring timestamp differs from label horizon")
    return observed_days


def _write_survival_evidence(
    work_root: Path,
    manifest: SplitManifest,
    dataset: ResearchDataset,
) -> list[Path]:
    """Persist sample-keyed observations and person-period rows used by survival."""

    label_evidence_hash = file_sha256(work_root / "label-evidence.parquet")
    by_key = {(item.symbol, item.as_of, item.horizon): item for item in dataset.samples}
    session_positions = {
        session: index for index, session in enumerate(manifest.calendar_sessions)
    }
    observation_rows: list[dict[str, Any]] = []
    person_period_rows: list[dict[str, Any]] = []
    for fold in manifest.outer_folds:
        for partition_name, keys in (
            ("train", fold.train_sample_keys),
            ("test", fold.test_sample_keys),
        ):
            for horizon in (5, 20):
                samples = tuple(by_key[key] for key in keys if key[2] == horizon)
                for index, sample in enumerate(samples):
                    event_day = _range_exit_event_day(
                        sample, horizon, session_positions
                    )
                    observed_days = _range_exit_observed_days(
                        sample, horizon, session_positions
                    )
                    observation = RangeExitObservation(
                        observation_id=(
                            f"{fold.fold_id}-{partition_name}-{horizon}-{index:04d}"
                        ),
                        observed_days=observed_days,
                        event_day=event_day,
                        right_censored=False,
                    )
                    sample_key = _sample_key_text(sample)
                    observation_rows.append(
                        {
                            "observation_id": observation.observation_id,
                            "fold_id": fold.fold_id,
                            "partition": partition_name,
                            "horizon": horizon,
                            "sample_key": sample_key,
                            "as_of": sample.as_of,
                            "range_exit": sample.target.range_exit,
                            "range_exit_at": sample.target.range_exit_at,
                            "label_end_at": sample.label_end_at,
                            "observed_days": observed_days,
                            "event_day": event_day,
                            "right_censored": False,
                            "label_evidence_hash": label_evidence_hash,
                        }
                    )
                    for row in build_person_period_rows(
                        (observation,), max_horizon=horizon
                    ):
                        person_period_rows.append(
                            {
                                "observation_id": row.observation_id,
                                "fold_id": fold.fold_id,
                                "partition": partition_name,
                                "horizon": horizon,
                                "sample_key": sample_key,
                                "day": row.day,
                                "event": row.event,
                                "censored": row.censored,
                                "label_evidence_hash": label_evidence_hash,
                            }
                        )
    observations_path = work_root / "survival-observations.parquet"
    person_period_path = work_root / "survival-person-period.parquet"
    _write_parquet_immutable(observations_path, observation_rows)
    _write_parquet_immutable(person_period_path, person_period_rows)
    return [observations_path, person_period_path]


def _run_similar_cases(
    manifest: SplitManifest, dataset: ResearchDataset
) -> dict[str, Any]:
    fold = manifest.outer_folds[-1]
    by_key = {(item.symbol, item.as_of, item.horizon): item for item in dataset.samples}
    train = tuple(by_key[key] for key in fold.train_sample_keys if key[2] == 5)
    test = tuple(by_key[key] for key in fold.test_sample_keys if key[2] == 5)
    partition = _training_partition(
        fold, manifest, TaskName.CAPITAL_GAIN_RETURN, 5, train
    )
    rows = tuple(
        SimilarCaseRow(
            case_id=f"case-{index:04d}",
            sample_key=_sample_key(row),
            label_end=row.label_end_at,
            label_observed_at=row.label_end_at,
            feature_values=(
                cast(float, row.numeric_features["volatility"]),
                cast(float, row.numeric_features["compression"]),
            ),
            range_event_id=f"event-{row.as_of.year}-{row.as_of.month:02d}",
            independence_group_id=f"independent-{row.as_of.year}-{row.as_of.month:02d}",
            asset_group="stock",
            data_quality_pass=row.data_quality_score >= 0.95,
            forward_return=cast(float, row.target.capital_gain_return),
            direction_up=row.target.forecast_direction is DirectionLabel.UP,
            breakout_up=row.target.breakout_event is BreakoutEvent.UP,
            mae=cast(float, row.target.forecast_mae),
        )
        for index, row in enumerate(train)
    )
    artifact = SimilarCaseIndex.fit(
        rows,
        partition=partition,
        config=DistanceConfig(
            feature_names=("volatility", "compression"),
            feature_weights=(1.0, 1.0),
            minimum_independent_cases=3,
            distance_version="m4-distance-v1",
        ),
    )
    query_row = test[0]
    query = SimilarCaseQuery(
        sample_key=_sample_key(query_row),
        label_end=query_row.label_end_at,
        feature_values=(
            cast(float, query_row.numeric_features["volatility"]),
            cast(float, query_row.numeric_features["compression"]),
        ),
        range_event_id=f"event-{query_row.as_of.year}-{query_row.as_of.month:02d}",
        asset_group="stock",
        data_quality_pass=True,
    )
    result = artifact.query(
        query,
        cutoff=query_row.as_of,
        top_k=5,
        expected_artifact_hash=artifact.artifact_hash,
    )
    selected = [
        next(row for row in rows if row.case_id == case_id)
        for case_id in result.case_ids
    ]
    return {
        "artifact_hash": artifact.artifact_hash,
        "indexed_count": len(rows),
        "eligible_count": result.effective_independent_count,
        "case_ids": list(result.case_ids),
        "insufficient_evidence": result.insufficient_evidence,
        "future_cases_excluded": all(
            row.sample_key[1] < query.sample_key[1] for row in selected
        ),
        "overlapping_labels_excluded": all(
            row.label_end < query.sample_key[1] for row in selected
        ),
        "same_events_excluded": all(
            query.range_event_id is None
            or row.range_event_id is None
            or row.range_event_id != query.range_event_id
            for row in selected
        ),
        "adjacent_correlated_duplicates_excluded": len(
            {row.independence_group_id for row in selected}
        )
        == len(selected),
    }


def _economic_model_evidence_payload(model: FoldModelEvidence) -> dict[str, Any]:
    return {
        "fold_hash": model.fold.fold_hash,
        "model_version": model.classifier.model_version,
        "training_input_hash": model.classifier.training_input_hash,
        "test_sample_keys": [_sample_key_text(row) for row in model.test_rows],
    }


def _economic_model_evidence_hash(model: FoldModelEvidence) -> str:
    return deterministic_hash(
        {
            **_economic_model_evidence_payload(model),
            "raw_probabilities": model.raw_probabilities,
            "raw_logits": model.raw_logits,
        }
    )


def _economic_calibration_evidence_payload(
    model: FoldModelEvidence,
) -> dict[str, Any]:
    return {
        "fold_hash": model.fold.fold_hash,
        "calibration_method": "temperature_scaling",
        "temperature": model.calibrator.temperature,
        "class_order": model.calibrator.class_order,
        "calibration_partition_hash": model.calibrator.calibration_partition_hash,
    }


def _economic_calibration_evidence_hash(model: FoldModelEvidence) -> str:
    return deterministic_hash(
        {
            **_economic_calibration_evidence_payload(model),
            "raw_logits": model.raw_logits,
            "calibrated_probabilities": model.calibrated_probabilities,
        }
    )


def _write_economic_evidence(
    work_root: Path,
    evidence: SyntheticEvidence,
    manifest: SplitManifest,
    fold_models: list[FoldModelEvidence],
) -> list[Path]:
    """Persist the complete input needed to reconstruct every fold backtest."""

    sessions = tuple(
        VerifiedMarketSession(
            session_date=session,
            session_open_at=datetime.combine(session, time(14, 30), tzinfo=UTC),
            session_close_at=datetime.combine(session, time(21), tzinfo=UTC),
            half_day=False,
        )
        for session in evidence.calendar.sessions
    )
    calendar = VerifiedMarketCalendar.create(
        calendar_version="synthetic-oos-execution-calendar-v1",
        sessions=sessions,
        complete_through=sessions[-1].session_date + timedelta(days=7),
    )
    adjustment_hash = deterministic_hash({"synthetic_adjustment": 1})
    cost = CostScenario(
        half_spread_bps=2.0,
        liquidity_bps=3.0,
        commission_bps=1.0,
        regulatory_fee_bps=0.5,
        max_adv_participation=0.10,
        scenario_version="m4-main-cost-v1",
    )
    membership = UniverseMembership(
        symbol="US.AAPL",
        universe_version="synthetic-universe-v1",
        effective_from=datetime.combine(
            evidence.calendar.sessions[0], time(0), tzinfo=UTC
        ),
        effective_to=None,
        observed_at=datetime.combine(
            evidence.calendar.sessions[0], time(0), tzinfo=UTC
        ),
        point_in_time_eligible=True,
        asset_type="stock",
        industry="technology",
        eligible=True,
    )
    bars = tuple(
        ExecutableBar(
            symbol="US.AAPL",
            session_date=session.session_date,
            session_open_at=session.session_open_at,
            session_close_at=session.session_close_at,
            open_price=100.0 + index * 0.05 + math.sin(index / 9.0),
            close_price=100.1 + index * 0.05 + math.sin((index + 1) / 9.0),
            average_daily_dollar_volume=25_000_000.0,
            cash_dividend=0.0,
            verified=True,
            adjustment_revision="synthetic-adjustment-v1",
            adjustment_input_hash=adjustment_hash,
            point_in_time_eligible=True,
            adjustment_mode="point_in_time_split_adjusted",
            cumulative_split_factor=1.0,
            dividend_basis="split_adjusted_per_share",
        )
        for index, session in enumerate(sessions)
    )
    prediction_rows: list[dict[str, Any]] = []
    fold_specs: list[dict[str, Any]] = []
    for model in fold_models:
        model_evidence_hash = _economic_model_evidence_hash(model)
        calibration_evidence_hash = _economic_calibration_evidence_hash(model)
        for row, raw_probabilities, raw_logits, probabilities in zip(
            model.test_rows,
            model.raw_probabilities,
            model.raw_logits,
            model.calibrated_probabilities,
            strict=True,
        ):
            signal_eligible = row.as_of.weekday() == 3
            prediction_rows.append(
                {
                    "fold_id": model.fold.fold_id,
                    "window_id": model.fold.evaluation_window.window_id,
                    "sample_key": _sample_key_text(row),
                    "symbol": row.symbol,
                    "as_of": row.as_of,
                    "horizon": row.horizon,
                    "raw_probability_down": raw_probabilities[0],
                    "raw_probability_flat": raw_probabilities[1],
                    "raw_probability_up": raw_probabilities[2],
                    "raw_logit_down": raw_logits[0],
                    "raw_logit_flat": raw_logits[1],
                    "raw_logit_up": raw_logits[2],
                    "probability_down": probabilities[0],
                    "probability_flat": probabilities[1],
                    "probability_up": probabilities[2],
                    "model_evidence_hash": model_evidence_hash,
                    "calibration_evidence_hash": calibration_evidence_hash,
                    "data_quality_pass": row.data_quality_score >= 0.95,
                    "liquidity_eligible": True,
                    "signal_eligible": signal_eligible,
                    "signal_score": probabilities[2],
                    "signal_decision": (
                        "eligible" if signal_eligible else "not_rebalance_anchor"
                    ),
                }
            )
        fold_specs.append(
            {
                "fold_id": model.fold.fold_id,
                "window_id": model.fold.evaluation_window.window_id,
                "model_evidence_hash": model_evidence_hash,
                "calibration_evidence_hash": calibration_evidence_hash,
                "model_evidence": _economic_model_evidence_payload(model),
                "calibration_evidence": _economic_calibration_evidence_payload(model),
                "strategy_version": f"m4-{model.fold.fold_id}-long-cash-v1",
            }
        )
    strategy_template = StrategySpec(
            horizon=5,
            top_k=1,
            initial_capital=100_000.0,
            single_stock_cap=0.10,
            industry_stock_cap=0.25,
            single_etf_cap=0.10,
            total_etf_cap=0.20,
            broad_etf_allowlist=("US.SPY", "US.QQQ"),
            cash_annual_yield=0.04,
            one_vintage=True,
            rebalance_rule="weekly_last_full_session",
            entry_rule="next_verified_open",
            exit_rule="fixed_horizon_close",
            cost_scenario=cost,
            strategy_version="m4-replay-template-v1",
            universe_version="synthetic-universe-v1",
            adjustment_revision="synthetic-adjustment-v1",
            adjustment_input_hash=adjustment_hash,
            adjustment_mode="point_in_time_split_adjusted",
            calendar_version=calendar.calendar_version,
            calendar_hash=calendar.calendar_hash,
            post_entry_missing_exit_rule="next_verified_close_then_total_loss",
            maximum_exit_delay_sessions=2,
            minimum_executable_up_probability=0.0,
            score_definition="executable-up-probability",
            required_signal_version="m4-calibrated-oos-signal-v1",
            model_evidence_hash="0" * 64,
            calibration_evidence_hash="0" * 64,
            dataset_input_hash=evidence.dataset.input_hash,
            economic_evidence_scope="research_only",
        )
    predictions_path = work_root / "economic-predictions.parquet"
    bars_path = work_root / "execution-bars.parquet"
    spec_path = work_root / "economic-replay-spec.json"
    _write_parquet_immutable(predictions_path, prediction_rows)
    _write_parquet_immutable(
        bars_path,
        [
            {
                "execution_bar_key": f"{bar.symbol}|{bar.session_date.isoformat()}",
                **bar.model_dump(mode="python"),
            }
            for bar in bars
        ],
    )
    write_immutable_json(
        spec_path,
        {
            "spec_version": "m4-economic-replay-v1",
            "dataset_input_hash": evidence.dataset.input_hash,
            "source_calendar_hash": manifest.calendar_hash,
            "calendar": calendar.model_dump(mode="json"),
            "membership": membership.model_dump(mode="json"),
            "strategy_template": strategy_template.model_dump(mode="json"),
            "folds": fold_specs,
        },
    )
    return [predictions_path, bars_path, spec_path]


def _run_long_cash_backtest(work_root: Path) -> dict[str, Any]:
    prediction_rows = pq.read_table(
        work_root / "economic-predictions.parquet"
    ).to_pylist()
    bar_rows = pq.read_table(work_root / "execution-bars.parquet").to_pylist()
    spec = json.loads(
        (work_root / "economic-replay-spec.json").read_text(encoding="utf-8")
    )
    calendar = VerifiedMarketCalendar.model_validate_json(
        json.dumps(spec["calendar"]), strict=False
    )
    membership = UniverseMembership.model_validate_json(
        json.dumps(spec["membership"]), strict=False
    )
    bars = tuple(
        ExecutableBar.model_validate(
            {name: value for name, value in row.items() if name != "execution_bar_key"}
        )
        for row in bar_rows
    )
    fold_economics: list[dict[str, Any]] = []
    results = []
    for fold_spec in spec["folds"]:
        fold_rows = [
            row for row in prediction_rows if row["fold_id"] == fold_spec["fold_id"]
        ]
        signals = tuple(
            WeeklySignal(
                symbol=row["symbol"],
                anchor_at=row["as_of"],
                score=row["signal_score"],
                executable_up_probability=row["probability_up"],
                abstain=False,
                data_quality_pass=row["data_quality_pass"],
                liquidity_eligible=row["liquidity_eligible"],
                score_definition="executable-up-probability",
                signal_version="m4-calibrated-oos-signal-v1",
                model_evidence_hash=row["model_evidence_hash"],
                calibration_evidence_hash=row["calibration_evidence_hash"],
                dataset_input_hash=spec["dataset_input_hash"],
            )
            for row in fold_rows
            if row["signal_eligible"]
        )
        strategy_payload = dict(spec["strategy_template"])
        strategy_payload.update(
            {
                "strategy_version": fold_spec["strategy_version"],
                "model_evidence_hash": fold_spec["model_evidence_hash"],
                "calibration_evidence_hash": fold_spec[
                    "calibration_evidence_hash"
                ],
            }
        )
        strategy = StrategySpec.model_validate_json(
            json.dumps(strategy_payload), strict=False
        )
        result = LongCashBacktester().run(
            signals,
            bars,
            (membership,),
            strategy,
            market_calendar=calendar,
        )
        results.append(result)
        fold_economics.append(
            {
                "fold_id": fold_spec["fold_id"],
                "window_id": fold_spec["window_id"],
                "signal_source": "calibrated_outer_test_predictions",
                "signal_count": len(signals),
                "prediction_count": len(fold_rows),
                "trade_count": len(result.trades),
                "gross_return": result.gross_portfolio_return,
                "net_return": result.net_portfolio_return,
                "total_cost": sum(trade.total_cost for trade in result.trades),
                "model_evidence_hash": fold_spec["model_evidence_hash"],
                "calibration_evidence_hash": fold_spec[
                    "calibration_evidence_hash"
                ],
                "actual_artifact_identity_location": (
                    "runtime-observations.json#"
                    "outer_fold_economic_model_artifact_identities"
                ),
                "dataset_input_hash": spec["dataset_input_hash"],
                "calendar_hash": spec["source_calendar_hash"],
                "execution_calendar_hash": calendar.calendar_hash,
                "trades": [trade.model_dump(mode="json") for trade in result.trades],
                "cost_sensitivity": [
                    item.model_dump(mode="json") for item in result.cost_sensitivity
                ],
                "capacity_sensitivity": [
                    item.model_dump(mode="json")
                    for item in result.capacity_sensitivity
                ],
            }
        )
    gross_return = (
        math.prod(1.0 + item.gross_portfolio_return for item in results) - 1.0
    )
    net_return = math.prod(1.0 + item.net_portfolio_return for item in results) - 1.0
    cost_sensitivity = []
    for scenario_index, per_side_bps in enumerate((5, 10, 20)):
        outer_folds = [
            {
                "fold_id": fold_spec["fold_id"],
                **result.cost_sensitivity[scenario_index].model_dump(mode="json"),
            }
            for fold_spec, result in zip(spec["folds"], results, strict=True)
        ]
        cost_sensitivity.append(
            {
                "per_side_bps": per_side_bps,
                "fold_count": len(outer_folds),
                "aggregation_method": "compound_outer_fold_returns",
                "estimated_total_cost": sum(
                    item["estimated_total_cost"] for item in outer_folds
                ),
                "net_portfolio_return": math.prod(
                    1.0 + item["net_portfolio_return"] for item in outer_folds
                )
                - 1.0,
                "outer_folds": outer_folds,
            }
        )
    capacity_sensitivity = []
    for scenario_index, portfolio_scale in enumerate((0.5, 1.0, 2.0, 5.0)):
        outer_folds = [
            {
                "fold_id": fold_spec["fold_id"],
                **result.capacity_sensitivity[scenario_index].model_dump(mode="json"),
            }
            for fold_spec, result in zip(spec["folds"], results, strict=True)
        ]
        capacity_sensitivity.append(
            {
                "portfolio_scale": portfolio_scale,
                "fold_count": len(outer_folds),
                "aggregation_method": "sum_outer_fold_exceedances",
                "capacity_exceeded_count": sum(
                    item["capacity_exceeded_count"] for item in outer_folds
                ),
                "outer_folds": outer_folds,
            }
        )
    return {
        "economic_evidence_scope": "research_only",
        "research_only_reasons": sorted(
            {reason for item in results for reason in item.research_only_reasons}
        ),
        "entry_rule": "next_verified_open",
        "exit_rule": "fixed_horizon_close",
        "gross_return": gross_return,
        "net_return": net_return,
        "cash_weight": float(np.mean([item.cash_weight for item in results])),
        "turnover": float(np.mean([item.turnover for item in results])),
        "trade_count": sum(len(item.trades) for item in results),
        "failed_fills": sum(item.failed_fills for item in results),
        "capacity_exceeded_count": sum(
            item.capacity_exceeded_count for item in results
        ),
        "outer_fold_economics": fold_economics,
        "cost_sensitivity": cost_sensitivity,
        "capacity_sensitivity": capacity_sensitivity,
        "replay_source_artifacts": [
            "economic-predictions.parquet",
            "execution-bars.parquet",
            "economic-replay-spec.json",
        ],
        "concentration_limits": {
            "single_stock_cap": 0.10,
            "industry_stock_cap": 0.25,
            "total_etf_cap": 0.20,
        },
    }


def _run_confidence_gate(
    model_hash: str,
    calibration_hash: str,
    similar_count: int,
    calibration_error: Any,
) -> dict[str, Any]:
    policy = ConfidencePolicy.create(
        max_data_age_seconds=86_400,
        max_missing_rate=0.10,
        max_ood_score=1.0,
        max_interval_width=0.20,
        max_calibration_error=0.25,
        min_calibration_samples=20,
        max_model_disagreement=0.40,
        min_similar_cases=1,
        high_confidence_data_age_seconds=3_600,
        high_confidence_missing_rate=0.02,
        high_confidence_ood_score=0.30,
        high_confidence_interval_width=0.10,
        high_confidence_calibration_error=0.10,
        high_confidence_model_disagreement=0.20,
        high_confidence_similar_cases=3,
        high_confidence_calibration_samples=40,
        policy_version="m4-confidence-v1",
    )
    decision = ConfidenceGate(policy, expected_policy_hash=policy.policy_hash).evaluate(
        ConfidenceEvidence(
            data_age_seconds=0,
            missing_rate=0.0,
            schema_match=True,
            ood_score=0.1,
            interval_width=0.08,
            calibration_error=min(float(calibration_error), 1.0),
            calibration_sample_count=50,
            model_disagreement=0.10,
            similar_case_count=similar_count,
            data_health_version="synthetic-health-v1",
            feature_set_version="synthetic-features-v1",
            model_version="selected-lgbm-v1",
            calibration_version="temperature-v1",
            task=TaskName.FORECAST_DIRECTION,
            horizon=5,
            model_artifact_hash=model_hash,
            calibration_artifact_hash=calibration_hash,
            high_confidence_oos=None,
        ),
        expected_oos_evidence_hash=None,
    )
    return {
        "high_enabled": decision.level.value == "high",
        "level": decision.level.value,
        "abstain": decision.abstain,
        "reasons": [reason.value for reason in decision.reasons],
        "high_confidence_oos_evidence": "insufficient_oos_improvement_evidence",
        "policy_hash": policy.policy_hash,
    }


def _run_diagnostics(fold_count: int) -> dict[str, Any]:
    returns = np.asarray(
        [0.001 * (1 if index % 2 == 0 else -1) for index in range(max(3, fold_count))],
        dtype=np.float64,
    )
    dsr = deflated_sharpe_ratio(
        returns,
        annualization=12,
        attempted_trials=4,
        attempted_trial_sharpes=(0.0, 0.0, 0.0, 0.0),
    )
    pbo = probability_of_backtest_overfitting(
        np.column_stack((returns[:fold_count], -returns[:fold_count])),
        minimum_blocks=4,
    )
    return {
        "dsr": dsr.model_dump(mode="json"),
        "pbo": pbo.model_dump(mode="json"),
    }


def _terminal_counts(ledger: JsonlTrialLedger) -> dict[str, int]:
    records = ledger.read_records()
    statuses = Counter(
        record.payload.status.value
        for record in records
        if isinstance(record.payload, TrialFinished)
    )
    registered = sum(isinstance(record.payload, BacktestTrial) for record in records)
    return {
        "registered": registered,
        "succeeded": statuses["succeeded"],
        "failed": statuses["failed"],
        "aborted": statuses["aborted"],
        "rejected": statuses["rejected"],
    }


def _failed_trials(ledger: JsonlTrialLedger) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for record in ledger.read_records():
        if (
            isinstance(record.payload, TrialFinished)
            and record.payload.status is not TrialStatus.SUCCEEDED
        ):
            output.append(
                {
                    "trial_id": record.record_id,
                    "status": record.payload.status.value,
                    "stop_reason": record.payload.stop_reason,
                }
            )
    return output


def _jsonable_artifact_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _jsonable_artifact_value(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable_artifact_value(item) for item in value]
    return value


def _artifact_semantic_payload(path: Path) -> Any:
    if path.suffix == ".parquet":
        return _jsonable_artifact_value(pq.read_table(path).to_pylist())
    if path.suffix == ".jsonl":
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
    return json.loads(path.read_text(encoding="utf-8"))


def _write_replay_index(
    work_root: Path,
    artifact_paths: list[Path],
    *,
    dataset_input_hash: str,
    split_manifest_hash: str,
    calendar_hash: str,
) -> Path:
    relative_paths = [path.relative_to(work_root).as_posix() for path in artifact_paths]
    if any(count != 1 for count in Counter(relative_paths).values()):
        raise RuntimeError("replay source artifacts must be unique")
    payload: dict[str, Any] = {
        "index_version": "m4-evidence-replay-v1",
        "dataset_input_hash": dataset_input_hash,
        "split_manifest_hash": split_manifest_hash,
        "calendar_hash": calendar_hash,
        "artifacts": [
            {
                **artifact_entry(work_root, path),
                "content_hash": deterministic_hash(_artifact_semantic_payload(path)),
            }
            for path in artifact_paths
        ],
    }
    index_path = work_root / "evidence-replay-index.json"
    write_immutable_json(
        index_path,
        {**payload, "index_content_hash": deterministic_hash(payload)},
    )
    return index_path


def _sample_key_from_parts(symbol: str, as_of: datetime, horizon: int) -> str:
    return f"{symbol}|{as_of.isoformat()}|{horizon}"


def _load_replay_sources(
    work_root: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    SplitManifest,
    tuple[Any, ...],
]:
    index_path = work_root / "evidence-replay-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index_payload = {
        name: value for name, value in index.items() if name != "index_content_hash"
    }
    if deterministic_hash(index_payload) != index.get("index_content_hash"):
        raise RuntimeError("replay index content hash mismatch")
    entries = cast(list[dict[str, Any]], index["artifacts"])
    indexed_names = [str(item["relative_path"]) for item in entries]
    if any(count != 1 for count in Counter(indexed_names).values()):
        raise RuntimeError("replay index contains duplicate artifact paths")
    downstream_names = {
        "m4-acceptance-report.json",
        "deterministic-core.json",
        "runtime-observations.json",
        index_path.name,
    }
    persisted_source_names = sorted(
        path.relative_to(work_root).as_posix()
        for path in work_root.iterdir()
        if path.is_file()
        and path.name not in downstream_names
        and path.suffix in {".json", ".jsonl", ".parquet"}
    )
    if sorted(indexed_names) != persisted_source_names:
        raise RuntimeError("replay index does not cover every persisted source artifact")

    manifest = SplitManifest.model_validate_json(
        (work_root / "split-manifest.json").read_text(encoding="utf-8"),
        strict=False,
    )
    raw_ledger = cast(
        list[dict[str, Any]],
        _artifact_semantic_payload(work_root / "trial-ledger.jsonl"),
    )
    registration_counts = Counter(
        str(item["record_id"])
        for item in raw_ledger
        if item.get("record_type") == "trial_registered"
    )
    finish_counts = Counter(
        str(item["record_id"])
        for item in raw_ledger
        if item.get("record_type") == "trial_finished"
    )
    if any(count != 1 for count in registration_counts.values()):
        raise RuntimeError("trial registration multiplicity must be exactly one")
    if finish_counts != registration_counts or any(
        count != 1 for count in finish_counts.values()
    ):
        raise RuntimeError("terminal multiplicity must be exactly one per trial")
    expected_window_counts = Counter(
        fold.evaluation_window.window_id for fold in manifest.outer_folds
    )
    consumption_counts = Counter(
        str(item["record_id"])
        for item in raw_ledger
        if item.get("record_type") == "window_consumed"
    )
    if consumption_counts != expected_window_counts:
        raise RuntimeError("consumption multiplicity must be exactly one per window")

    records = JsonlTrialLedger(work_root / "trial-ledger.jsonl").read_records()

    loaded_payloads: dict[str, Any] = {}
    for entry in entries:
        relative_path = str(entry["relative_path"])
        path = work_root / relative_path
        if path.stat().st_size != int(entry["size_bytes"]):
            raise RuntimeError(f"artifact size mismatch: {relative_path}")
        if file_sha256(path) != entry["sha256"]:
            raise RuntimeError(f"artifact content hash mismatch: {relative_path}")
        payload = _artifact_semantic_payload(path)
        if deterministic_hash(payload) != entry["content_hash"]:
            raise RuntimeError(f"artifact semantic hash mismatch: {relative_path}")
        loaded_payloads[relative_path] = payload
    return index, loaded_payloads, manifest, records


def _replay_trial_governance(
    manifest: SplitManifest, records: tuple[Any, ...]
) -> dict[str, Any]:
    registrations = [
        record.payload for record in records if isinstance(record.payload, BacktestTrial)
    ]
    finishes = [
        record.payload for record in records if isinstance(record.payload, TrialFinished)
    ]
    consumptions = [
        record.payload for record in records if isinstance(record.payload, EvaluationWindow)
    ]
    window_ids = [fold.evaluation_window.window_id for fold in manifest.outer_folds]
    trial_matrix_by_window = {
        window_id: sorted(
            [
                {
                    "task": item.task.value,
                    "horizon": item.horizon,
                    "model_version": item.model_version,
                    "candidate_index": item.frozen_parameters["candidate_index"],
                    "frozen_parameters": _jsonable_artifact_value(
                        dict(item.frozen_parameters)
                    ),
                    "primary_metrics": list(item.primary_metrics),
                    "secondary_metrics": list(item.secondary_metrics),
                }
                for item in registrations
                if item.evaluation_window_id == window_id
            ],
            key=lambda item: json.dumps(item, sort_keys=True),
        )
        for window_id in window_ids
    }
    matrices = list(trial_matrix_by_window.values())
    if not matrices or any(matrix != matrices[0] for matrix in matrices[1:]):
        raise RuntimeError("outer-fold trial matrices are not identical")
    expected_by_window: dict[str, list[dict[str, Any]]] = {}
    folds_by_id = {fold.fold_id: fold for fold in manifest.outer_folds}
    for plan in _trial_plans(manifest):
        window_id = folds_by_id[plan.fold_id].evaluation_window.window_id
        expected_by_window.setdefault(window_id, []).append(
            {
                "task": plan.task.value,
                "horizon": plan.horizon,
                "model_version": f"synthetic-{plan.kind}-v1",
                "candidate_index": plan.candidate_index,
                "frozen_parameters": {
                    "seed": SEED,
                    "candidate_index": plan.candidate_index,
                    "search_budget": 2,
                    "num_threads": 1,
                    "num_boost_round": 6 + plan.candidate_index * 2,
                },
                "primary_metrics": [plan.primary_metric],
                "secondary_metrics": list(plan.secondary_metrics),
            }
        )
    expected_by_window = {
        window_id: sorted(matrix, key=lambda item: json.dumps(item, sort_keys=True))
        for window_id, matrix in expected_by_window.items()
    }
    if trial_matrix_by_window != expected_by_window:
        raise RuntimeError("trial matrix differs from registered plan")
    consumption_counts = Counter(item.window_id for item in consumptions)
    production_consumed = sum(
        item.scope is EvaluationScope.PRODUCTION for item in consumptions
    )
    if production_consumed:
        raise RuntimeError("production evaluation window was consumed")
    return {
        "shared_outer_folds": True,
        "shared_trial_matrix": matrices[0],
        "ledger_replay": {
            "record_count": len(records),
            "registered_trial_count": len(registrations),
            "terminal_trial_count": len(finishes),
            "all_registered_trials_terminal": True,
            "terminal_multiplicity": "exactly_one",
        },
        "outer_window_consumption": [
            {
                "window_id": fold.evaluation_window.window_id,
                "scope": fold.evaluation_window.scope.value,
                "consumption_count": consumption_counts[
                    fold.evaluation_window.window_id
                ],
            }
            for fold in manifest.outer_folds
        ],
        "production_window_consumed_count": production_consumed,
    }


def _as_utc_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise RuntimeError("persisted timestamp has an invalid type")
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise RuntimeError("persisted timestamp is not UTC")
    return parsed


def _replay_bindings(
    index: dict[str, Any],
    payloads: dict[str, Any],
    manifest: SplitManifest,
    work_root: Path,
) -> dict[str, Any]:
    calendar = TradingCalendarArtifact.model_validate_json(
        json.dumps(payloads["calendar-evidence.json"]), strict=False
    )
    transformer = cast(dict[str, Any], payloads["transformer-evidence.json"])
    dataset_rows = cast(list[dict[str, Any]], payloads["research-dataset.parquet"])
    label_rows = cast(list[dict[str, Any]], payloads["label-evidence.parquet"])
    if not dataset_rows or not label_rows:
        raise RuntimeError("replay dataset and labels must be non-empty")

    dataset_keys = [str(row["sample_key"]) for row in dataset_rows]
    label_keys = [str(row["sample_key"]) for row in label_rows]
    if any(count != 1 for count in Counter(dataset_keys).values()):
        raise RuntimeError("dataset sample multiplicity must be exactly one")
    if any(count != 1 for count in Counter(label_keys).values()):
        raise RuntimeError("label sample multiplicity must be exactly one")
    if dataset_keys != label_keys:
        raise RuntimeError("dataset and label sample ordering differs")

    samples = tuple(
        ResearchSample.model_validate_json(
            str(row["research_sample_json"]), strict=False
        )
        for row in dataset_rows
    )
    if any(
        str(row["sample_key"]) != _sample_key_text(sample)
        for row, sample in zip(dataset_rows, samples, strict=True)
    ):
        raise RuntimeError("persisted research sample identity differs from row key")
    source_keys = tuple(_sample_key(sample) for sample in samples)
    recomputed_dataset_hash = compute_research_dataset_input_hash(samples)
    dataset_matches = (
        source_keys == manifest.source_sample_keys
        and recomputed_dataset_hash == manifest.dataset_input_hash
        and recomputed_dataset_hash == index["dataset_input_hash"]
    )

    calendar_matches = (
        calendar.input_hash == manifest.calendar_hash
        and calendar.input_hash == index["calendar_hash"]
        and calendar.calendar_version == manifest.calendar_version
        and calendar.source is manifest.calendar_source
        and calendar.sessions == manifest.calendar_sessions
    )
    transformer_matches = all(
        sample.transformer_version == transformer["transformer_version"]
        and sample.transformer_input_hash == transformer["transformer_input_hash"]
        and sample.transformer_manifest_hash
        == transformer["transformer_manifest_hash"]
        for sample in samples
    )
    feature_matches = all(
        all(row[name] == sample.numeric_features[name] for name in FEATURE_NAMES)
        and row["feature_lineage_hash"] == sample.feature_lineage_hash
        and row["feature_content_hash"] == sample.feature_content_hash
        and row["feature_values_hash"] == sample.feature_values_hash
        and row["feature_set_version"] == sample.feature_set_version
        and row["calendar_hash"] == sample.calendar_hash
        for row, sample in zip(dataset_rows, samples, strict=True)
    )

    expected_labels = [
        {
            "sample_key": _sample_key_text(sample),
            "symbol": sample.symbol,
            "as_of": sample.as_of.isoformat(),
            "horizon": sample.horizon,
            "direction": sample.target.forecast_direction.value,
            "breakout": sample.target.breakout_event.value,
            "return": sample.target.capital_gain_return,
            "mfe": sample.target.forecast_mfe,
            "mae": sample.target.forecast_mae,
            "range_exit": sample.target.range_exit,
            "range_exit_at": (
                sample.target.range_exit_at.isoformat()
                if sample.target.range_exit_at is not None
                else None
            ),
            "breakout_event_at": (
                sample.target.breakout_event_at.isoformat()
                if sample.target.breakout_event_at is not None
                else None
            ),
            "label_end_at": sample.label_end_at.isoformat(),
        }
        for sample in samples
    ]
    label_matches = label_rows == expected_labels
    split_matches = (
        manifest.manifest_hash == index["split_manifest_hash"]
        and all(
            trial_hash == manifest.dataset_input_hash
            for trial_hash in {
                fold.dataset_input_hash for fold in manifest.outer_folds
            }
        )
    )
    checks = {
        "dataset_manifest": dataset_matches,
        "calendar_manifest": calendar_matches,
        "split_manifest": split_matches,
        "transformer_samples": transformer_matches,
        "feature_samples": feature_matches,
        "label_samples": label_matches,
    }
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise RuntimeError(f"persisted artifact binding mismatch: {','.join(failed)}")
    return {
        "binding_checks": checks,
        "binding_hashes": {
            "dataset_input_hash": recomputed_dataset_hash,
            "calendar_hash": calendar.input_hash,
            "transformer_input_hash": transformer["transformer_input_hash"],
            "transformer_manifest_hash": transformer["transformer_manifest_hash"],
            "feature_lineage_hash": deterministic_hash(
                sorted({sample.feature_lineage_hash for sample in samples})
            ),
            "feature_content_hash": deterministic_hash(
                sorted({sample.feature_content_hash for sample in samples})
            ),
            "label_evidence_hash": file_sha256(
                work_root / "label-evidence.parquet"
            ),
        },
    }


def _replay_survival_evidence(
    payloads: dict[str, Any], manifest: SplitManifest, label_evidence_hash: str
) -> dict[str, Any]:
    labels = cast(list[dict[str, Any]], payloads["label-evidence.parquet"])
    observations = cast(
        list[dict[str, Any]], payloads["survival-observations.parquet"]
    )
    person_period = cast(
        list[dict[str, Any]], payloads["survival-person-period.parquet"]
    )
    labels_by_key = {str(row["sample_key"]): row for row in labels}
    session_positions = {
        session: index for index, session in enumerate(manifest.calendar_sessions)
    }
    expected_membership = Counter(
        (
            fold.fold_id,
            partition,
            key[2],
            _sample_key_from_parts(key[0], key[1], key[2]),
        )
        for fold in manifest.outer_folds
        for partition, keys in (
            ("train", fold.train_sample_keys),
            ("test", fold.test_sample_keys),
        )
        for key in keys
        if key[2] in {5, 20}
    )
    actual_membership = Counter(
        (
            str(row["fold_id"]),
            str(row["partition"]),
            int(row["horizon"]),
            str(row["sample_key"]),
        )
        for row in observations
    )
    if actual_membership != expected_membership:
        raise RuntimeError("survival observations do not match fold sample evidence")
    observation_ids = [str(row["observation_id"]) for row in observations]
    if any(count != 1 for count in Counter(observation_ids).values()):
        raise RuntimeError("survival observation multiplicity must be exactly one")

    expected_person_period: list[dict[str, Any]] = []
    for row in observations:
        sample_key = str(row["sample_key"])
        label = labels_by_key.get(sample_key)
        if label is None:
            raise RuntimeError("survival observation has no persisted label")
        horizon = int(row["horizon"])
        if horizon not in {5, 20} or int(label["horizon"]) != horizon:
            raise RuntimeError("survival observation horizon differs from label")
        as_of = _as_utc_datetime(label["as_of"])
        label_end_at = _as_utc_datetime(label["label_end_at"])
        observed_days = (
            session_positions[label_end_at.date()] - session_positions[as_of.date()]
        )
        event_at = (
            _as_utc_datetime(label["range_exit_at"])
            if label["range_exit_at"] is not None
            else None
        )
        event_day = (
            session_positions[event_at.date()] - session_positions[as_of.date()]
            if event_at is not None
            else None
        )
        right_censored = event_day is None and observed_days < horizon
        if (
            row["label_evidence_hash"] != label_evidence_hash
            or bool(row["range_exit"]) != bool(label["range_exit"])
            or row["range_exit_at"] != label["range_exit_at"]
            or row["label_end_at"] != label["label_end_at"]
            or int(row["observed_days"]) != observed_days
            or row["event_day"] != event_day
            or bool(row["right_censored"]) != right_censored
        ):
            raise RuntimeError("survival observation timestamp binding mismatch")
        observation = RangeExitObservation(
            observation_id=str(row["observation_id"]),
            observed_days=observed_days,
            event_day=event_day,
            right_censored=right_censored,
        )
        for expanded in build_person_period_rows(
            (observation,), max_horizon=horizon
        ):
            expected_person_period.append(
                {
                    "observation_id": expanded.observation_id,
                    "fold_id": row["fold_id"],
                    "partition": row["partition"],
                    "horizon": horizon,
                    "sample_key": sample_key,
                    "day": expanded.day,
                    "event": expanded.event,
                    "censored": expanded.censored,
                    "label_evidence_hash": label_evidence_hash,
                }
            )
    def order_key(row: dict[str, Any]) -> tuple[str, int]:
        return (str(row["observation_id"]), int(row["day"]))

    if sorted(person_period, key=order_key) != sorted(
        expected_person_period, key=order_key
    ):
        raise RuntimeError("person-period evidence does not replay from observations")
    return {
        "all_observations_bound": True,
        "observation_count": len(observations),
        "person_period_row_count": len(person_period),
        "label_evidence_hash": label_evidence_hash,
    }


def _replay_economic_evidence(
    payloads: dict[str, Any], manifest: SplitManifest, work_root: Path
) -> dict[str, Any]:
    spec = cast(dict[str, Any], payloads["economic-replay-spec.json"])
    predictions = cast(
        list[dict[str, Any]], payloads["economic-predictions.parquet"]
    )
    labels = cast(list[dict[str, Any]], payloads["label-evidence.parquet"])
    persisted_backtest = cast(dict[str, Any], payloads["long-cash-backtest.json"])
    execution_rows = cast(
        list[dict[str, Any]], payloads["execution-bars.parquet"]
    )
    research_rows = cast(
        list[dict[str, Any]], payloads["research-dataset.parquet"]
    )
    research_samples = tuple(
        ResearchSample.model_validate_json(
            str(row["research_sample_json"]), strict=False
        )
        for row in research_rows
    )
    samples_by_key = {_sample_key(sample): sample for sample in research_samples}
    label_keys = {str(row["sample_key"]) for row in labels}
    folds_by_id = {fold.fold_id: fold for fold in manifest.outer_folds}
    fold_specs = cast(list[dict[str, Any]], spec["folds"])
    execution_calendar = VerifiedMarketCalendar.model_validate_json(
        json.dumps(spec["calendar"]), strict=False
    )
    membership = UniverseMembership.model_validate_json(
        json.dumps(spec["membership"]), strict=False
    )
    strategy_template = StrategySpec.model_validate_json(
        json.dumps(spec["strategy_template"]), strict=False
    )
    execution_keys = [str(row["execution_bar_key"]) for row in execution_rows]
    if any(count != 1 for count in Counter(execution_keys).values()):
        raise RuntimeError("execution bar multiplicity must be exactly one")
    execution_bars = tuple(
        ExecutableBar.model_validate_json(
            json.dumps(
                {
                    name: value
                    for name, value in row.items()
                    if name != "execution_bar_key"
                }
            ),
            strict=False,
        )
        for row in execution_rows
    )
    sessions_by_date = {
        session.session_date: session for session in execution_calendar.sessions
    }
    if (
        spec["dataset_input_hash"] != manifest.dataset_input_hash
        or spec["source_calendar_hash"] != manifest.calendar_hash
        or strategy_template.dataset_input_hash != manifest.dataset_input_hash
        or strategy_template.calendar_hash != execution_calendar.calendar_hash
        or [bar.session_date for bar in execution_bars]
        != [session.session_date for session in execution_calendar.sessions]
    ):
        raise RuntimeError("economic replay spec binding mismatch")
    for row, bar in zip(execution_rows, execution_bars, strict=True):
        session = sessions_by_date.get(bar.session_date)
        expected_key = f"{bar.symbol}|{bar.session_date.isoformat()}"
        if (
            row["execution_bar_key"] != expected_key
            or session is None
            or bar.session_open_at != session.session_open_at
            or bar.session_close_at != session.session_close_at
            or bar.symbol != membership.symbol
            or not bar.verified
            or not bar.point_in_time_eligible
            or bar.adjustment_revision != strategy_template.adjustment_revision
            or bar.adjustment_input_hash != strategy_template.adjustment_input_hash
            or bar.adjustment_mode != strategy_template.adjustment_mode
        ):
            raise RuntimeError("execution bar identity mismatch")
    if [str(item["fold_id"]) for item in fold_specs] != [
        fold.fold_id for fold in manifest.outer_folds
    ]:
        raise RuntimeError("economic replay folds differ from split manifest")

    prediction_keys = [str(row["sample_key"]) for row in predictions]
    if any(count != 1 for count in Counter(prediction_keys).values()):
        raise RuntimeError("economic prediction multiplicity must be exactly one")
    for fold_spec in fold_specs:
        fold_id = str(fold_spec["fold_id"])
        fold = folds_by_id[fold_id]
        fold_rows = [row for row in predictions if row["fold_id"] == fold_id]
        persisted_keys = [str(row["sample_key"]) for row in fold_rows]
        expected_keys = [
            _sample_key_from_parts(key[0], key[1], key[2])
            for key in fold.test_sample_keys
            if key[2] == 5
        ]
        model_evidence = cast(dict[str, Any], fold_spec["model_evidence"])
        calibration_evidence = cast(
            dict[str, Any], fold_spec["calibration_evidence"]
        )
        train_rows = tuple(
            samples_by_key[key] for key in fold.train_sample_keys if key[2] == 5
        )
        calibration_rows = tuple(
            samples_by_key[key]
            for key in fold.calibration_sample_keys
            if key[2] == 5
        )
        training_partition = _training_partition(
            fold,
            manifest,
            TaskName.FORECAST_DIRECTION,
            5,
            train_rows,
        )
        training = ClassificationTrainingData.build(
            _features(train_rows),
            _direction_labels(train_rows),
            sample_keys=tuple(_sample_key(row) for row in train_rows),
            feature_names=FEATURE_NAMES,
            dataset_input_hash=manifest.dataset_input_hash,
            partition=training_partition,
        )
        if model_evidence["training_input_hash"] != training.input_hash:
            raise RuntimeError("economic model training input binding mismatch")
        calibration_partition = _calibration_partition(
            fold,
            manifest,
            TaskName.FORECAST_DIRECTION,
            5,
            calibration_rows,
        )
        if (
            calibration_evidence["calibration_partition_hash"]
            != calibration_partition.partition_hash
        ):
            raise RuntimeError("economic calibration partition binding mismatch")
        raw_probabilities = [
            [
                float(row["raw_probability_down"]),
                float(row["raw_probability_flat"]),
                float(row["raw_probability_up"]),
            ]
            for row in fold_rows
        ]
        raw_logits = [
            [
                float(row["raw_logit_down"]),
                float(row["raw_logit_flat"]),
                float(row["raw_logit_up"]),
            ]
            for row in fold_rows
        ]
        probabilities = [
            [
                float(row["probability_down"]),
                float(row["probability_flat"]),
                float(row["probability_up"]),
            ]
            for row in fold_rows
        ]
        expected_raw_logits = np.log(
            np.clip(np.asarray(raw_probabilities, dtype=np.float64), 1e-9, 1.0)
        )
        persisted_raw_logits = np.asarray(raw_logits, dtype=np.float64)
        if not np.allclose(
            persisted_raw_logits,
            expected_raw_logits,
            rtol=0.0,
            atol=1e-15,
        ):
            raise RuntimeError("raw probability and logit evidence mismatch")
        if (
            calibration_evidence.get("calibration_method")
            != "temperature_scaling"
            or tuple(calibration_evidence.get("class_order", ())) != CLASS_ORDER
        ):
            raise RuntimeError("economic calibration method or class order mismatch")
        reconstructed_probabilities = apply_temperature_scaling(
            persisted_raw_logits,
            temperature=float(calibration_evidence["temperature"]),
            class_order=tuple(calibration_evidence["class_order"]),
        )
        if not np.allclose(
            reconstructed_probabilities,
            np.asarray(probabilities, dtype=np.float64),
            rtol=0.0,
            atol=1e-15,
        ):
            raise RuntimeError("calibrated probability reconstruction mismatch")
        recomputed_model_hash = deterministic_hash(
            {
                **model_evidence,
                "raw_probabilities": raw_probabilities,
                "raw_logits": raw_logits,
            }
        )
        recomputed_calibration_hash = deterministic_hash(
            {
                **calibration_evidence,
                "raw_logits": raw_logits,
                "calibrated_probabilities": probabilities,
            }
        )
        if (
            fold_spec["window_id"] != fold.evaluation_window.window_id
            or model_evidence["fold_hash"] != fold.fold_hash
            or calibration_evidence["fold_hash"] != fold.fold_hash
            or model_evidence["test_sample_keys"] != expected_keys
            or persisted_keys != expected_keys
            or recomputed_model_hash != fold_spec["model_evidence_hash"]
            or recomputed_calibration_hash
            != fold_spec["calibration_evidence_hash"]
        ):
            raise RuntimeError("economic model or calibration evidence binding mismatch")
        for row, raw_probability, probability in zip(
            fold_rows, raw_probabilities, probabilities, strict=True
        ):
            as_of = _as_utc_datetime(row["as_of"])
            eligible = as_of.weekday() == 3
            if (
                str(row["sample_key"]) not in label_keys
                or int(row["horizon"]) != 5
                or any(value < 0.0 or value > 1.0 for value in raw_probability)
                or not math.isclose(sum(raw_probability), 1.0, abs_tol=1e-12)
                or any(value < 0.0 or value > 1.0 for value in probability)
                or not math.isclose(sum(probability), 1.0, abs_tol=1e-12)
                or row["model_evidence_hash"] != recomputed_model_hash
                or row["calibration_evidence_hash"]
                != recomputed_calibration_hash
                or bool(row["signal_eligible"]) != eligible
                or float(row["signal_score"]) != float(row["probability_up"])
                or row["signal_decision"]
                != ("eligible" if eligible else "not_rebalance_anchor")
            ):
                raise RuntimeError("economic prediction or signal evidence mismatch")

    replayed_backtest = _run_long_cash_backtest(work_root)
    if deterministic_hash(replayed_backtest) != deterministic_hash(persisted_backtest):
        raise RuntimeError("persisted long/cash result does not match replay")
    outer_folds = cast(
        list[dict[str, Any]], replayed_backtest["outer_fold_economics"]
    )
    if (
        len(outer_folds) != len(manifest.outer_folds)
        or replayed_backtest["gross_return"]
        != math.prod(1.0 + float(item["gross_return"]) for item in outer_folds)
        - 1.0
        or replayed_backtest["net_return"]
        != math.prod(1.0 + float(item["net_return"]) for item in outer_folds)
        - 1.0
    ):
        raise RuntimeError("aggregate long/cash return differs from outer folds")
    for scenario in cast(
        list[dict[str, Any]], replayed_backtest["cost_sensitivity"]
    ):
        scenario_folds = cast(list[dict[str, Any]], scenario["outer_folds"])
        expected_return = (
            math.prod(
                1.0 + float(item["net_portfolio_return"])
                for item in scenario_folds
            )
            - 1.0
        )
        if (
            int(scenario["fold_count"]) != len(manifest.outer_folds)
            or float(scenario["net_portfolio_return"]) != expected_return
        ):
            raise RuntimeError("cost sensitivity aggregation differs from outer folds")
    for scenario in cast(
        list[dict[str, Any]], replayed_backtest["capacity_sensitivity"]
    ):
        scenario_folds = cast(list[dict[str, Any]], scenario["outer_folds"])
        expected_count = sum(
            int(item["capacity_exceeded_count"]) for item in scenario_folds
        )
        if (
            int(scenario["fold_count"]) != len(manifest.outer_folds)
            or int(scenario["capacity_exceeded_count"]) != expected_count
        ):
            raise RuntimeError("capacity sensitivity aggregation differs from outer folds")
    return {
        "all_outer_folds_reconstructed": True,
        "fold_count": len(outer_folds),
        "prediction_count": len(predictions),
        "execution_bar_count": len(execution_bars),
        "replayed_backtest_hash": deterministic_hash(replayed_backtest),
    }


def _semantic_replay_m4_evidence(work_root: Path) -> dict[str, Any]:
    """Semantically replay artifacts using the local index without an external pin."""

    index, payloads, manifest, records = _load_replay_sources(work_root)
    governance = _replay_trial_governance(manifest, records)
    bindings = _replay_bindings(index, payloads, manifest, work_root)
    survival = _replay_survival_evidence(
        payloads,
        manifest,
        str(bindings["binding_hashes"]["label_evidence_hash"]),
    )
    economic = _replay_economic_evidence(payloads, manifest, work_root)
    return {
        "source": "semantic_replay_from_locally_indexed_artifacts",
        "external_tamper_resistance": "not_verified_without_caller_supplied_pin",
        **governance,
        **bindings,
        "artifact_replay": {
            "all_content_hashes_match": True,
            "artifact_count": len(index["artifacts"]),
            "index_content_hash": index["index_content_hash"],
            "claim_scope": "local_index_byte_and_semantic_hash_consistency",
        },
        "semantic_replay": {
            "reconstructed_artifacts": [
                "calendar-evidence.json",
                "trial-ledger.jsonl",
                "split-manifest.json",
                "transformer-evidence.json",
                "research-dataset.parquet",
                "label-evidence.parquet",
                "survival-observations.parquet",
                "survival-person-period.parquet",
                "economic-predictions.parquet",
                "economic-replay-spec.json",
                "execution-bars.parquet",
                "long-cash-backtest.json",
            ],
            "byte_pinned_only_artifacts": [
                "fold-metrics.parquet",
                "oos-predictions.parquet",
                "quantile-predictions.parquet",
                "similar-cases.json",
            ],
        },
        "survival_replay": survival,
        "economic_replay": economic,
    }


def _validate_pinned_deterministic_core(
    work_root: Path, expected_deterministic_core_hash: str
) -> dict[str, Any]:
    core_path = work_root / "deterministic-core.json"
    core = cast(
        dict[str, Any],
        json.loads(core_path.read_text(encoding="utf-8")),
    )
    core_payload = {
        name: value for name, value in core.items() if name != "deterministic_core_hash"
    }
    actual_core_hash = deterministic_hash(core_payload)
    if core.get("deterministic_core_hash") != actual_core_hash:
        raise RuntimeError("deterministic core content hash mismatch")
    if expected_deterministic_core_hash != actual_core_hash:
        raise RuntimeError("caller-supplied deterministic core pin mismatch")
    artifact_hashes = cast(dict[str, str], core.get("artifact_hashes"))
    if not artifact_hashes:
        raise RuntimeError("deterministic core artifact map is empty")
    root = work_root.resolve()
    for relative_path, expected_hash in artifact_hashes.items():
        path = (work_root / relative_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise RuntimeError(
                f"deterministic core artifact path is invalid: {relative_path}"
            )
        if file_sha256(path) != expected_hash:
            raise RuntimeError(
                f"deterministic core artifact mismatch: {relative_path}"
            )
    index_path = work_root / "evidence-replay-index.json"
    if index_path.name not in artifact_hashes:
        raise RuntimeError("deterministic core does not pin the replay index")
    index = cast(
        dict[str, Any], json.loads(index_path.read_text(encoding="utf-8"))
    )
    index_payload = {
        name: value for name, value in index.items() if name != "index_content_hash"
    }
    if deterministic_hash(index_payload) != index.get("index_content_hash"):
        raise RuntimeError("replay index content hash mismatch")
    indexed_names: set[str] = set()
    for entry in cast(list[dict[str, Any]], index.get("artifacts")):
        relative_path = str(entry["relative_path"])
        if relative_path in indexed_names:
            raise RuntimeError("replay index contains duplicate artifact paths")
        indexed_names.add(relative_path)
        if artifact_hashes.get(relative_path) != entry.get("sha256"):
            raise RuntimeError(
                f"replay index differs from deterministic core: {relative_path}"
            )
    expected_core_names = indexed_names | {
        index_path.name,
        "m4-acceptance-report.json",
    }
    if set(artifact_hashes) != expected_core_names:
        raise RuntimeError("deterministic core artifact map coverage mismatch")
    return {
        "caller_pin_verified": True,
        "deterministic_core_hash": actual_core_hash,
        "core_artifact_map_verified": True,
        "replay_index_verified_against_core": True,
        "pinned_artifact_count": len(artifact_hashes),
    }


def replay_m4_evidence(
    work_root: Path, *, expected_deterministic_core_hash: str
) -> dict[str, Any]:
    """Verify a caller-pinned core, then semantically replay persisted evidence."""

    external_integrity = _validate_pinned_deterministic_core(
        work_root, expected_deterministic_core_hash
    )
    semantic = _semantic_replay_m4_evidence(work_root)
    return {
        **semantic,
        "source": "caller_pinned_core_and_semantic_replay",
        "external_tamper_resistance": "verified_by_caller_supplied_pin",
        "external_integrity": external_integrity,
    }


def _acceptance_report(
    *,
    manifest: SplitManifest,
    fold_reports: list[dict[str, Any]],
    failed_trials: list[dict[str, Any]],
    similar: dict[str, Any],
    backtest: dict[str, Any],
    confidence: dict[str, Any],
    diagnostics: dict[str, Any],
    audit: dict[str, Any],
    decision: dict[str, Any],
    artifact_paths: list[Path],
    work_root: Path,
) -> dict[str, Any]:
    worst = max(
        fold_reports,
        key=lambda item: cast(dict[str, dict[str, float]], item["family_metrics"])[
            "lightgbm"
        ]["brier_score"],
    )
    deterministic_folds = [_deterministic_fold_report(item) for item in fold_reports]
    return_metrics = [
        cast(dict[str, dict[str, float]], item["quantile_metrics"])["return_quantiles"]
        for item in fold_reports
    ]
    family_average = {
        family: float(
            np.mean(
                [
                    cast(dict[str, dict[str, float]], item["family_metrics"])[family][
                        "brier_score"
                    ]
                    for item in fold_reports
                ]
            )
        )
        for family in (
            "historical_frequency",
            "channel_atr_rule",
            "logistic_regression",
            "lightgbm",
        )
    }
    top_features = cast(list[dict[str, Any]], fold_reports[-1]["top_features"])
    monthly_metrics = _monthly_oos_metrics(fold_reports)
    worst_month = max(
        monthly_metrics, key=lambda item: (item["brier_score"], item["month"])
    )
    return {
        "report_version": "m4-model-validation-v1",
        "evidence_scope": "synthetic_offline",
        "economic_output_scope": "research_only",
        "research_only": True,
        "predictive_lift_claimed": False,
        "shared_outer_folds": audit["shared_outer_folds"],
        "task_horizon_matrix": [
            {"task": task.value, "horizons": list(horizons)}
            for task, horizons in CLASSIFICATION_TASK_HORIZONS
        ],
        "walk_forward_geometry": {
            "train_years": manifest.spec.train_years,
            "validation_months": manifest.spec.validation_months,
            "test_months": manifest.spec.test_months,
            "embargo_sessions": manifest.spec.embargo_sessions,
            "minimum_outer_folds": manifest.spec.minimum_outer_folds,
        },
        "outer_folds": deterministic_folds,
        "failed_trials": failed_trials,
        "calibration_bins": [
            {"fold_id": item["fold_id"], "bins": item["calibration_bins"]}
            for item in fold_reports
        ],
        "quantile_metrics": {
            "coverage": float(np.mean([item["coverage"] for item in return_metrics])),
            "mean_width": float(
                np.mean([item["mean_width"] for item in return_metrics])
            ),
            "quantiles": list(QUANTILES),
            "noncrossing_assertion_passed": all(
                bool(item["noncrossing_assertion_passed"])
                for fold in fold_reports
                for item in cast(
                    dict[str, dict[str, Any]], fold["quantile_metrics"]
                ).values()
            ),
            "folds": [
                {
                    name: value
                    for name, value in item.items()
                    if not name.startswith("actual_")
                }
                for item in return_metrics
            ],
        },
        "survival_metrics": {
            horizon: {
                "mean_time_dependent_brier": float(
                    np.mean(
                        [
                            cast(dict[str, dict[str, float]], item["survival_metrics"])[
                                horizon
                            ]["time_dependent_brier"]
                            for item in fold_reports
                        ]
                    )
                ),
                "folds": [
                    cast(dict[str, dict[str, Any]], item["survival_metrics"])[horizon]
                    for item in fold_reports
                ],
            }
            for horizon in ("5", "20")
        },
        "confidence_strata": confidence,
        "top_contributions": {
            "feature_contributions": top_features,
            "family_brier_scores": family_average,
            "non_causal": True,
        },
        "similar_cases": similar,
        "long_cash": backtest,
        "monthly_metrics": monthly_metrics,
        "worst_fold": {
            "fold_id": worst["fold_id"],
            "metric": "lightgbm_brier_score",
            "value": cast(dict[str, dict[str, float]], worst["family_metrics"])[
                "lightgbm"
            ]["brier_score"],
        },
        "worst_month": worst_month,
        "dsr": diagnostics["dsr"],
        "pbo": diagnostics["pbo"],
        "window_consumption": {
            "outer_registered": len(manifest.outer_folds),
            "outer_consumed_once": sum(
                item["consumption_count"] == 1
                for item in audit["outer_window_consumption"]
            ),
            "lockbox_consumed": sum(
                item["scope"] == EvaluationScope.PRODUCTION.value
                and "lockbox" in item["window_id"]
                for item in audit["outer_window_consumption"]
            ),
            "production_consumed": audit["production_window_consumed_count"],
        },
        "hashes": {
            **audit["binding_hashes"],
            "split_manifest_hash": manifest.manifest_hash,
            "fold_hashes": [fold.fold_hash for fold in manifest.outer_folds],
            "binding_checks": audit["binding_checks"],
        },
        "audit": audit,
        "search_budget": {
            "method": "fixed_lightweight_search",
            "candidate_count": 2,
            "seed": SEED,
            "threads_per_candidate": 1,
            "maximum_boost_rounds": 8,
        },
        "promotion_decision": decision,
        "artifacts": [artifact_entry(work_root, path) for path in artifact_paths],
        "runtime_observations_file": "runtime-observations.json",
        "resource_observations": "recorded_outside_hashed_deterministic_core",
    }


def _deterministic_fold_report(fold: dict[str, Any]) -> dict[str, Any]:
    output = dict(fold)
    output.pop("oos_prediction_rows", None)
    output["quantile_metrics"] = {
        task: {
            name: value
            for name, value in metrics.items()
            if not name.startswith("actual_")
        }
        for task, metrics in cast(
            dict[str, dict[str, Any]], fold["quantile_metrics"]
        ).items()
    }
    return output


def _monthly_oos_metrics(
    fold_reports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for fold in fold_reports:
        for row in cast(list[dict[str, Any]], fold["oos_prediction_rows"]):
            grouped.setdefault(str(row["month"]), []).append(row)
    return [
        {
            "month": month,
            "brier_score": float(np.mean([float(row["brier_score"]) for row in rows])),
            "mean_capital_gain_return": float(
                np.mean([float(row["capital_gain_return"]) for row in rows])
            ),
            "sample_count": len(rows),
        }
        for month, rows in sorted(grouped.items())
    ]
