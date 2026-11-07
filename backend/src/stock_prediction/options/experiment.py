"""Frozen paired option experiments using the existing CPU trees and calibrators."""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, fields, is_dataclass
from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, cast

from filelock import FileLock
import numpy as np
import pandas as pd

from stock_prediction.contracts.options import AUTOMATIC_OPTION_SYMBOLS
from stock_prediction.contracts.research import Horizon
from stock_prediction.live.locking import _atomic_json
from stock_prediction.modeling.calibration import (
    CalibrationPartitionEvidence,
    CqrCalibrator,
    TemperatureScaler,
)
from stock_prediction.modeling.contracts import (
    EvaluationRole,
    EvaluationWindow,
    OuterFold,
    SampleKey,
    SplitManifest,
    TaskName,
)
from stock_prediction.modeling.lightgbm_models import (
    ClassifierArtifact,
    ClassifierConfig,
    LightGbmClassifierTrainer,
    LightGbmQuantileTrainer,
    QuantileConfig,
    QuantileModelSet,
)
from stock_prediction.modeling.metrics import (
    empirical_interval_diagnostics,
    mean_pinball_loss,
    multiclass_brier_score,
    multiclass_log_loss,
)
from stock_prediction.modeling.m7_economics import evaluate_m7_direction_economics
from stock_prediction.modeling.m7_metrics import date_block_bootstrap_delta_brier
from stock_prediction.modeling.training import (
    ClassificationTrainingData,
    RegressionTrainingData,
    TrainingPartitionEvidence,
)
from stock_prediction.options.research import (
    OPTION_SOURCE_FIELDS,
    OptionExperimentProtocol,
    OptionPromotionRules,
    registered_option_protocol,
)
from stock_prediction.research.dataset import (
    ResearchDataset,
    ResearchSample,
    compute_research_dataset_input_hash,
)

GROUPS = ("B0", "E1", "E2", "E3", "quality")
CLASSES = ("up", "flat", "down")


def _dataclass_json(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _dataclass_json(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _dataclass_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_dataclass_json(item) for item in value]
    return value


def content_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class OptionExperimentStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def directory(self, experiment_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", experiment_id):
            raise ValueError("invalid experiment id")
        return self.root / experiment_id

    def register(self, experiment_id: str, registration: Mapping[str, Any]) -> None:
        directory = self.directory(experiment_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "registration.json"
        with FileLock(str(directory / ".writer.lock"), timeout=10):
            if target.exists():
                if json.loads(target.read_text(encoding="utf-8")) != dict(registration):
                    raise ValueError(
                        "experiment registration conflict; use a new experiment id"
                    )
            else:
                _atomic_json(target, dict(registration))

    def consume_test(
        self, experiment_id: str, evidence: Mapping[str, Any], at: datetime
    ) -> None:
        directory = self.directory(experiment_id)
        if not (directory / "registration.json").exists():
            raise ValueError("experiment must be registered before blind test access")
        with (
            FileLock(str(self.root / ".blind-consumption.lock"), timeout=10),
            FileLock(str(directory / ".writer.lock"), timeout=10),
        ):
            target = directory / "blind-test-consumed.json"
            if target.exists():
                raise ValueError(
                    "blind test already consumed; reruns cannot be release evidence"
                )
            ledger_path = self.root / "blind-sample-ledger.json"
            ledger = (
                json.loads(ledger_path.read_text(encoding="utf-8"))
                if ledger_path.exists()
                else {}
            )
            blind_keys = evidence.get("sample_keys", [])
            if any(key in ledger for key in blind_keys):
                raise ValueError(
                    "blind test sample already consumed by another experiment"
                )
            _atomic_json(
                ledger_path, ledger | {key: experiment_id for key in blind_keys}
            )
            _atomic_json(target, dict(evidence) | {"consumed_at": at.isoformat()})


QUALITY_FEATURES = frozenset(
    (
        "history_gap_seconds",
        "snapshot_age_seconds",
        "contract_coverage_ratio",
        "excluded_contract_ratio",
        "iv_valid_ratio",
        "oi_valid_ratio",
        "gamma_valid_ratio",
        "quote_valid_ratio",
        "mean_relative_spread",
        "missing_field_ratio",
        "realized_observations",
        "realized_annualization_sessions",
    )
)
IV_FEATURES = frozenset(
    (
        "atm_iv_30d",
        "atm_iv_dte",
        "iv_term_slope_per_day",
        "iv_term_near_dte",
        "iv_term_far_dte",
        "put_call_skew_25delta",
        "skew_dte",
        "otm_put_minus_atm_iv",
        "realized_volatility_20sessions",
        "iv_minus_realized_20sessions",
        "atm_iv_change",
    )
)
OI_VOLUME_FEATURES = frozenset(
    (
        "call_oi",
        "put_oi",
        "put_call_oi",
        "call_volume",
        "put_volume",
        "put_call_volume",
        "volume_oi_ratio",
        "oi_strike_hhi",
        "volume_strike_hhi",
        "oi_near_30d_share",
        "volume_near_30d_share",
        "oi_peak_distance_ratio",
        "oi_change_fraction",
        "volume_change_fraction",
        "oi_concentration_change",
    )
)


def _option_group(name: str) -> str:
    if name in QUALITY_FEATURES or name.startswith("quality_"):
        return "quality"
    if name in IV_FEATURES:
        return "E1"
    return "E2" if name in OI_VOLUME_FEATURES else "E3"


def _values(row: Mapping[str, Any], group: str) -> dict[str, float | None]:
    values = {f"stock__{name}": value for name, value in row["stock"].items()}
    if group != "B0":
        for name, value in row["option"].items():
            if group == "E3" or _option_group(name) == group:
                values[f"option__{name}"] = value
    return values


def fit_feature_transform(
    rows: Sequence[Mapping[str, Any]], *, group: str
) -> dict[str, Any]:
    if not rows or group not in GROUPS:
        raise ValueError("empty training features or unknown ablation group")
    values = [_values(row, group) for row in rows]
    names = sorted({name for row in values for name in row})
    medians = {}
    for name in names:
        observed = [
            float(cast(float, row[name])) for row in values if row.get(name) is not None
        ]
        if any(not np.isfinite(value) for value in observed):
            raise ValueError("non-finite feature")
        medians[name] = float(np.median(observed)) if observed else 0.0
    categories = {
        name: sorted({row["categories"].get(name, "__missing__") for row in rows})
        for name in sorted({key for row in rows for key in row["categories"]})
    }
    feature_names = [item for name in names for item in (name, name + "__missing")]
    for name, vocabulary in categories.items():
        feature_names += [
            f"category__{name}__{index}" for index in range(len(vocabulary))
        ]
        feature_names.append(f"category__{name}__unseen")
    return {
        "group": group,
        "names": names,
        "medians": medians,
        "categories": categories,
        "feature_names": feature_names,
        "version": "option-fold-transform-v1",
        "fit_role": "train",
    }


def apply_feature_transform(
    rows: Sequence[Mapping[str, Any]], fitted: Mapping[str, Any]
) -> np.ndarray:
    matrix = []
    for row in rows:
        values = _values(row, fitted["group"])
        transformed = []
        for name in fitted["names"]:
            value = values.get(name)
            if value is not None and not np.isfinite(value):
                raise ValueError("non-finite inference feature")
            transformed += [
                fitted["medians"][name] if value is None else value,
                float(value is None),
            ]
        for name, vocabulary in fitted["categories"].items():
            value = row["categories"].get(name, "__missing__")
            transformed += [float(value == item) for item in vocabulary]
            transformed.append(float(value not in vocabulary))
        matrix.append(transformed)
    return np.asarray(matrix, dtype=np.float64).reshape(
        len(rows), len(fitted["feature_names"])
    )


def _key(row: ResearchSample) -> SampleKey:
    return row.symbol, row.as_of, row.horizon


def _window(
    fold: OuterFold, manifest: SplitManifest, role: EvaluationRole
) -> EvaluationWindow:
    if role is EvaluationRole.TRAIN:
        start, end = fold.train_start_session, fold.train_end_session
    else:
        start, end = fold.calibration_start_session, fold.calibration_end_session
    return EvaluationWindow(
        window_id=f"{fold.fold_id}.{role.value}",
        role=role,
        scope=manifest.spec.evaluation_scope,
        start_session=start,
        end_session=end,
        split_version=manifest.split_version,
        dataset_input_hash=manifest.dataset_input_hash,
        calendar_hash=manifest.calendar_hash,
        consumed_at=None,
        consumption_reason=None,
    )


def _partition(
    fold: OuterFold,
    manifest: SplitManifest,
    task: TaskName,
    horizon: Horizon,
    keys: Sequence[SampleKey],
    *,
    calibration: bool = False,
) -> Any:
    kwargs: dict[str, Any] = dict(
        partition_id=f"{fold.fold_id}.{task.value}.{horizon}",
        task=task,
        horizon=horizon,
        evaluation_window=_window(
            fold,
            manifest,
            EvaluationRole.CALIBRATION if calibration else EvaluationRole.TRAIN,
        ),
        registered_sample_keys=keys,
        split_manifest_hash=manifest.manifest_hash,
        source_fold_hash=fold.fold_hash,
    )
    if calibration:
        return CalibrationPartitionEvidence.register(
            **kwargs, asset_group="options-nine"
        )
    return TrainingPartitionEvidence.register(**kwargs)


def fit_task_models(
    train: Sequence[ResearchSample],
    calibration: Sequence[ResearchSample],
    x_train: np.ndarray,
    x_calibration: np.ndarray,
    feature_names: Sequence[str],
    *,
    protocol: OptionExperimentProtocol,
    fold: OuterFold,
    manifest: SplitManifest,
    group: str,
    horizon: Horizon,
) -> dict[str, Any]:
    if protocol.training_budget is None:
        raise ValueError("registered numeric training budget missing")
    budget = protocol.training_budget.model_dump()
    config = dict(
        budget,
        horizon=horizon,
        model_version=f"{protocol.experiment_id}-{group}-{horizon}",
        asset_group="options-nine",
    )
    models: dict[str, Any] = {}
    classification_tasks: dict[TaskName, Callable[[ResearchSample], str]] = {
        TaskName.FORECAST_DIRECTION: lambda r: str(r.target.forecast_direction),
        TaskName.EXECUTABLE_DIRECTION: lambda r: str(r.target.executable_direction),
        TaskName.BREAKOUT: lambda r: str(r.target.breakout_event),
    }
    if horizon != 1:
        classification_tasks[TaskName.RANGE_EXIT_SURVIVAL] = lambda r: (
            "exit" if r.target.range_exit else "inside"
        )
    for task, labels_for in classification_tasks.items():
        train_indices = [
            i
            for i, r in enumerate(train)
            if task is not TaskName.RANGE_EXIT_SURVIVAL
            or r.target.range_exit is not None
        ]
        cal_indices = [
            i
            for i, r in enumerate(calibration)
            if task is not TaskName.RANGE_EXIT_SURVIVAL
            or r.target.range_exit is not None
        ]
        task_train = [train[i] for i in train_indices]
        task_calibration = [calibration[i] for i in cal_indices]
        labels, cal_labels = (
            [labels_for(r) for r in task_train],
            [labels_for(r) for r in task_calibration],
        )
        classes = (
            CLASSES
            if task in (TaskName.FORECAST_DIRECTION, TaskName.EXECUTABLE_DIRECTION)
            else ("none", "up", "down", "extreme_both")
            if task is TaskName.BREAKOUT
            else ("inside", "exit")
        )
        if (
            len(classes) < 2
            or set(labels) != set(classes)
            or set(cal_labels) != set(classes)
        ):
            if task is TaskName.FORECAST_DIRECTION:
                raise ValueError(
                    "direction training/calibration must cover every declared class"
                )
            models[task.value] = {
                "status": "unavailable",
                "reason": "insufficient_task_class_coverage",
            }
            continue
        training = ClassificationTrainingData.build(
            x_train[train_indices],
            labels,
            sample_keys=[_key(r) for r in task_train],
            feature_names=feature_names,
            dataset_input_hash=manifest.dataset_input_hash,
            partition=_partition(
                fold, manifest, task, horizon, [_key(r) for r in task_train]
            ),
        )
        model = LightGbmClassifierTrainer(
            ClassifierConfig(**config, task=task, class_order=classes)
        ).fit(training)
        probs = model.predict_proba(
            x_calibration[cal_indices],
            feature_names=feature_names,
            expected_artifact_hash=model.artifact_hash,
        )
        calibrator = TemperatureScaler.fit(
            np.log(np.clip(probs, 1e-15, 1)),
            cal_labels,
            class_order=classes,
            calibration_partition=_partition(
                fold,
                manifest,
                task,
                horizon,
                [_key(r) for r in task_calibration],
                calibration=True,
            ),
            sample_keys=[_key(r) for r in task_calibration],
            source_model=model,
            expected_source_model_artifact_hash=model.artifact_hash,
        )
        models[task.value] = {
            "status": "trained",
            "kind": "classification",
            "model": model.model_dump(mode="json"),
            "calibrator": calibrator.model_dump(mode="json"),
        }
    for task in (
        TaskName.CAPITAL_GAIN_RETURN,
        TaskName.EXECUTABLE_RETURN,
        TaskName.FORECAST_MFE,
        TaskName.FORECAST_MAE,
    ):
        outcomes = [getattr(r.target, task.value) for r in train]
        cal_outcomes = [getattr(r.target, task.value) for r in calibration]
        if any(value is None for value in (*outcomes, *cal_outcomes)):
            models[task.value] = {
                "status": "unavailable",
                "reason": "mature_path_or_return_targets_missing",
            }
            continue
        data = RegressionTrainingData.build(
            x_train,
            outcomes,
            sample_keys=[_key(r) for r in train],
            feature_names=feature_names,
            dataset_input_hash=manifest.dataset_input_hash,
            partition=_partition(
                fold, manifest, task, horizon, [_key(r) for r in train]
            ),
        )
        model_q = LightGbmQuantileTrainer(QuantileConfig(**config, task=task)).fit(data)
        raw = np.sort(
            model_q.predict(
                x_calibration,
                feature_names=feature_names,
                expected_artifact_hash=model_q.artifact_hash,
            ),
            axis=1,
        )
        cqr = CqrCalibrator.fit(
            cal_outcomes,
            raw[:, 0],
            raw[:, 4],
            target_coverage=0.8,
            calibration_partition=_partition(
                fold,
                manifest,
                task,
                horizon,
                [_key(r) for r in calibration],
                calibration=True,
            ),
            sample_keys=[_key(r) for r in calibration],
            source_model=model_q,
            expected_source_model_artifact_hash=model_q.artifact_hash,
        )
        models[task.value] = {
            "status": "trained",
            "kind": "quantile",
            "model": model_q.model_dump(mode="json"),
            "calibrator": cqr.model_dump(mode="json"),
        }
    return models


def predict_task_models(
    models: Mapping[str, Any], matrix: np.ndarray, feature_names: Sequence[str]
) -> dict[str, Any]:
    predictions = {}
    for task, item in models.items():
        if item["status"] != "trained":
            continue
        if item["kind"] == "classification":
            decoded_model = ClassifierArtifact.model_validate_json(
                json.dumps(item["model"]), strict=False
            )
            model = ClassifierArtifact.model_validate(
                decoded_model.model_dump(mode="python"), strict=True
            )
            calibrator = TemperatureScaler.model_validate_json(
                json.dumps(item["calibrator"])
            )
            raw = model.predict_proba(
                matrix,
                feature_names=feature_names,
                expected_artifact_hash=model.artifact_hash,
            )
            predictions[task] = {
                "classes": model.class_order,
                "probabilities": calibrator.apply(
                    np.log(np.clip(raw, 1e-15, 1)),
                    expected_artifact_hash=calibrator.artifact_hash,
                    source_model_artifact_hash=model.artifact_hash,
                ).tolist(),
            }
        else:
            decoded_quantile = QuantileModelSet.model_validate_json(
                json.dumps(item["model"]), strict=False
            )
            model_q = QuantileModelSet.model_validate(
                decoded_quantile.model_dump(mode="python"), strict=True
            )
            cqr = CqrCalibrator.model_validate_json(json.dumps(item["calibrator"]))
            raw_q = model_q.predict(
                matrix,
                feature_names=feature_names,
                expected_artifact_hash=model_q.artifact_hash,
            )
            ordered = np.sort(raw_q, axis=1)
            intervals = cqr.apply(
                ordered[:, 0].tolist(),
                ordered[:, 4].tolist(),
                expected_artifact_hash=cqr.artifact_hash,
                source_model_artifact_hash=model_q.artifact_hash,
            )
            predictions[task] = {
                "quantiles": ordered[:, [0, 2, 4]].tolist(),
                "intervals": list(zip(intervals.lower, intervals.upper, strict=True)),
                "crossing_rate_before_rearrangement": float(
                    np.mean(np.any(np.diff(raw_q, axis=1) < 0, axis=1))
                ),
            }
    return predictions


def probability_metrics(
    labels: Sequence[str], probabilities: object, classes: Sequence[str]
) -> dict[str, Any]:
    matrix = np.asarray(probabilities, dtype=float)
    bins, ece = {}, []
    for index, name in enumerate(classes):
        target = np.asarray([label == name for label in labels], dtype=float)
        records, error = [], 0.0
        for b in range(10):
            mask = (matrix[:, index] >= b / 10) & (
                (matrix[:, index] < (b + 1) / 10) if b < 9 else (matrix[:, index] <= 1)
            )
            count = int(mask.sum())
            predicted = float(matrix[mask, index].mean()) if count else None
            observed = float(target[mask].mean()) if count else None
            if count:
                error += (
                    count
                    / len(labels)
                    * abs(cast(float, predicted) - cast(float, observed))
                )
            records.append(
                {"bin": b, "count": count, "predicted": predicted, "observed": observed}
            )
        bins[name] = records
        ece.append(error)
    return {
        "brier": multiclass_brier_score(labels, matrix.tolist(), classes=classes),
        "log_loss": multiclass_log_loss(labels, matrix.tolist(), classes=classes),
        "accuracy": float(
            np.mean(
                [
                    classes[int(i)] == label
                    for i, label in zip(matrix.argmax(axis=1), labels, strict=True)
                ]
            )
        ),
        "class_proportions": {
            name: labels.count(name) / len(labels) for name in classes
        },
        "macro_ece": float(np.mean(ece)),
        "calibration_curves": bins,
    }


def evaluate_tasks(
    rows: Sequence[ResearchSample], predictions: Mapping[str, Any]
) -> dict[str, Any]:
    report = {}
    for task, values in predictions.items():
        if "probabilities" in values:
            indices = [
                i
                for i, r in enumerate(rows)
                if task != TaskName.RANGE_EXIT_SURVIVAL
                or r.target.range_exit is not None
            ]
            if not indices:
                continue
            task_rows = [rows[i] for i in indices]
            labels = (
                ["exit" if r.target.range_exit else "inside" for r in task_rows]
                if task == TaskName.RANGE_EXIT_SURVIVAL
                else [
                    str(
                        getattr(
                            r.target,
                            "breakout_event" if task == TaskName.BREAKOUT else task,
                        )
                    )
                    for r in task_rows
                ]
            )
            report[task] = probability_metrics(
                labels, [values["probabilities"][i] for i in indices], values["classes"]
            )
            report[task]["task_sample_count"] = len(indices)
            report[task]["task_not_defined_fraction"] = 1 - len(indices) / len(rows)
        else:
            outcomes = [float(getattr(r.target, task)) for r in rows]
            q = np.asarray(values["quantiles"])
            intervals = np.asarray(values["intervals"])
            report[task] = {
                "pinball": {
                    str(tau): mean_pinball_loss(
                        outcomes, q[:, i].tolist(), quantile=tau
                    )
                    for i, tau in enumerate((0.1, 0.5, 0.9))
                },
                "interval": empirical_interval_diagnostics(
                    outcomes,
                    lower=intervals[:, 0].tolist(),
                    upper=intervals[:, 1].tolist(),
                    target_coverage=0.8,
                ).model_dump(),
                "crossing_rate_before_rearrangement": values[
                    "crossing_rate_before_rearrangement"
                ],
            }
    return report


def _bootstrap(
    rows: Sequence[ResearchSample], candidate: object, baseline: object, horizon: int
) -> dict[str, Any]:
    if len({r.as_of.date() for r in rows}) < 40:
        return {"status": "insufficient_time_blocks"}
    return asdict(
        date_block_bootstrap_delta_brier(
            sample_keys=[(r.symbol, r.as_of.date(), horizon) for r in rows],
            labels=[str(r.target.forecast_direction) for r in rows],
            candidate_probabilities=candidate,
            incumbent_probabilities=baseline,
            classes=CLASSES,
            block_length=max(20, horizon),
        )
    )


def assess_promotion(
    report: Mapping[str, Any], rules: OptionPromotionRules | None
) -> dict[str, Any]:
    reasons = []
    if rules is None:
        reasons.append("registered_promotion_rules_missing")
    if report.get("evaluation_scope") != "production":
        reasons.append(
            "synthetic_evidence_not_promotable"
            if report.get("evaluation_scope") == "synthetic_offline"
            else "research_scope_not_promotable"
        )
    registration = report.get("registration", {})
    if registration.get("calendar_source") in (None, "synthetic_offline"):
        reasons.append("production_calendar_evidence_missing")
    if registration.get("protocol", {}).get("split_spec", {}).get(
        "evaluation_scope"
    ) != report.get("evaluation_scope"):
        reasons.append("registered_evaluation_scope_mismatch")
    if (
        not report.get("economic_evaluation")
        or report.get("economic_evaluation", {}).get("status") != "evaluated"
    ):
        reasons.append("executable_economic_evidence_missing")
    if not report.get("event_evidence_reference"):
        reasons.append("event_cohort_evidence_missing")
    horizon_gates = {}
    for horizon in (1, 5, 20):
        entry = report.get("horizons", {}).get(str(horizon))
        failed = list(reasons)
        checks = {
            name: name in reasons
            for name in (
                "registered_promotion_rules_missing",
                "synthetic_evidence_not_promotable",
                "research_scope_not_promotable",
                "production_calendar_evidence_missing",
                "registered_evaluation_scope_mismatch",
                "executable_economic_evidence_missing",
                "event_cohort_evidence_missing",
            )
        }
        if entry is None:
            failed.append("horizon_evaluation_missing")
        elif rules is not None:
            base, enhanced, quality = (
                entry["metrics"][group] for group in ("B0", "E3", "quality")
            )
            b, e, q = (
                item[TaskName.FORECAST_DIRECTION] for item in (base, enhanced, quality)
            )
            delta = b["brier"] - e["brier"]
            checks.update(
                {
                    "test_dates_insufficient": entry["dates"]
                    < rules.minimum_test_dates,
                    "outer_folds_insufficient": entry["fold_count"]
                    < rules.minimum_outer_folds,
                    "fold_gain_unstable": entry["positive_fold_fraction"]
                    < rules.minimum_positive_fold_fraction,
                    "brier_time_block_gain_unproven": entry["bootstrap"].get(
                        "one_sided_upper_90", 1
                    )
                    >= 0,
                    "log_loss_degraded": e["log_loss"]
                    > b["log_loss"] + rules.maximum_log_loss_degradation,
                    "calibration_failed": e["macro_ece"]
                    > rules.maximum_calibration_error,
                    "deployment_coverage_failed": report["deployment_coverage"]
                    < rules.minimum_deployment_coverage,
                    "quality_only_explains_gain": delta <= 0
                    or b["brier"] - q["brier"]
                    > delta * rules.maximum_quality_only_gain_fraction,
                    "all_nine_symbols_required": rules.require_all_nine_symbols
                    and set(entry["symbols"]) != set(AUTOMATIC_OPTION_SYMBOLS),
                    "symbol_stability_failed": any(
                        v["E3"] - v["B0"] > rules.maximum_symbol_brier_degradation
                        for v in entry["per_symbol_brier"].values()
                    ),
                }
            )
            for task in (
                TaskName.CAPITAL_GAIN_RETURN,
                TaskName.EXECUTABLE_RETURN,
                TaskName.FORECAST_MFE,
                TaskName.FORECAST_MAE,
            ):
                if task not in enhanced or task not in base:
                    checks[f"{task}_evaluation_missing"] = True
                else:
                    checks[f"{task}_pinball_degraded"] = any(
                        enhanced[task]["pinball"][tau]
                        > base[task]["pinball"][tau] + rules.maximum_pinball_degradation
                        for tau in ("0.1", "0.5", "0.9")
                    )
                    checks[f"{task}_coverage_failed"] = (
                        abs(enhanced[task]["interval"]["empirical_coverage"] - 0.8)
                        > rules.maximum_interval_coverage_error
                    )
            for cohort_name in ("earnings", "high_volatility", "low_volatility"):
                cohort = entry["cohorts"].get(cohort_name)
                checks[f"{cohort_name}_cohort_missing_or_small"] = (
                    cohort is None or cohort["dates"] < rules.minimum_cohort_dates
                )
                if cohort is not None:
                    checks[f"{cohort_name}_cohort_degraded"] = (
                        cohort["brier"]["E3"] - cohort["brier"]["B0"]
                        > rules.maximum_cohort_brier_degradation
                    )
            economy = report.get("economic_evaluation", {})
            if economy.get("status") == "evaluated":
                item = economy["horizons"][str(horizon)]
                checks["net_economic_gain_failed"] = (
                    item["candidate"]["net_return"] - item["incumbent"]["net_return"]
                    < rules.minimum_net_return_delta
                )
                checks["drawdown_degraded"] = (
                    item["incumbent"]["maximum_drawdown"]
                    - item["candidate"]["maximum_drawdown"]
                    > rules.maximum_drawdown_degradation
                )
                checks["catastrophic_economic_window"] = item["catastrophic"]
            failed += [name for name, invalid in checks.items() if invalid]
        horizon_gates[str(horizon)] = {
            "eligible": not failed,
            "reasons": sorted(set(failed)),
            "checks": checks
            | ({"horizon_evaluation_missing": True} if entry is None else {}),
        }
    return {
        "eligible": any(v["eligible"] for v in horizon_gates.values()),
        "reasons": sorted(set(reasons)),
        "horizons": horizon_gates,
        "promoted": False,
        "published": False,
    }


def read_option_experiments(
    store: OptionExperimentStore,
    *,
    at: datetime,
    history_days: int | None,
    sessions: Sequence[date] = (),
):
    """Read saved evidence and expose the very same promotion checks used by serving."""
    from stock_prediction.contracts.options import (
        OptionAblationGroup,
        OptionExperimentHorizon,
        OptionExperimentReadModel,
        OptionPromotionCheck,
    )

    result, reasons = [], []
    for path in sorted(store.root.glob("*/registration.json")):
        identity = path.parent.name
        try:
            directory = store.directory(identity)
            registration = json.loads(path.read_text(encoding="utf-8"))
            protocol = registered_option_protocol(registration["protocol"])
            if protocol.registered_at > at:
                continue
            report_path = directory / "evaluation.json"
            report = (
                json.loads(report_path.read_text(encoding="utf-8"))
                if report_path.exists()
                else {}
            )
            if (
                report
                and datetime.fromisoformat(
                    report["generated_at"].replace("Z", "+00:00")
                )
                > at
            ):
                report = {}
            if report and (
                report.get("experiment_id") != identity
                or report.get("registration") != registration
            ):
                raise ValueError("experiment registration mismatch")
            assessment = assess_promotion(
                report
                or {
                    "evaluation_scope": str(protocol.split_spec.evaluation_scope),
                    "registration": registration,
                },
                protocol.promotion_rules,
            )
            horizons = []
            for horizon in (1, 5, 20):
                entry = report.get("horizons", {}).get(str(horizon), {})
                gate = assessment["horizons"][str(horizon)]
                groups = []
                base = (
                    entry.get("metrics", {})
                    .get("B0", {})
                    .get("forecast_direction", {})
                    .get("brier")
                )
                for group in GROUPS:
                    metrics = entry.get("metrics", {}).get(group, {})
                    brier = metrics.get("forecast_direction", {}).get("brier")
                    returns = metrics.get("capital_gain_return", {})
                    groups.append(
                        OptionAblationGroup(
                            group=group,
                            brier=brier,
                            delta_brier=brier - base
                            if brier is not None and base is not None
                            else None,
                            pinball=returns.get("pinball", {}),
                            empirical_coverage=returns.get("interval", {}).get(
                                "empirical_coverage"
                            ),
                        )
                    )
                horizons.append(
                    OptionExperimentHorizon(
                        horizon_days=horizon,
                        dates=entry.get("dates"),
                        samples=entry.get("samples"),
                        fold_count=entry.get("fold_count"),
                        positive_fold_fraction=entry.get("positive_fold_fraction"),
                        groups=tuple(groups),
                        bootstrap=entry.get("bootstrap", {}),
                        eligible=gate["eligible"],
                        reasons=tuple(gate["reasons"]),
                        checks=tuple(
                            OptionPromotionCheck(
                                name=name,
                                status="insufficient"
                                if failed
                                and any(
                                    word in name
                                    for word in (
                                        "missing",
                                        "insufficient",
                                        "unproven",
                                        "small",
                                    )
                                )
                                else "failed"
                                if failed
                                else "passed",
                            )
                            for name, failed in gate["checks"].items()
                        ),
                    )
                )
            minimum = (
                protocol.promotion_rules.minimum_test_dates
                if protocol.promotion_rules
                else None
            )
            remaining = (
                max(0, minimum - history_days)
                if minimum is not None and history_days is not None
                else None
            )
            future = sorted(day for day in sessions if day > at.date())
            estimate = (
                future[remaining - 1]
                if remaining is not None and remaining > 0 and len(future) >= remaining
                else at.date()
                if remaining == 0
                else None
            )
            result.append(
                OptionExperimentReadModel(
                    experiment_id=identity,
                    evaluation_scope=report.get(
                        "evaluation_scope", str(protocol.split_spec.evaluation_scope)
                    ),
                    synthetic=report.get("evaluation_scope") == "synthetic_offline"
                    or str(protocol.split_spec.evaluation_scope) == "synthetic_offline",
                    status=report.get("status", "registered_untrained"),
                    horizons=tuple(horizons),
                    minimum_test_dates=minimum,
                    history_days=history_days,
                    estimated_ready_date=estimate,
                    eligible=assessment["eligible"],
                    reasons=tuple(assessment["reasons"]),
                )
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            reasons.append("experiment_artifacts_invalid")
    return tuple(result), tuple(
        dict.fromkeys(
            reasons or (() if result else ("option_experiments_unavailable",))
        )
    )


def run_option_experiment(
    prepared: Mapping[str, Any],
    store: OptionExperimentStore,
    *,
    evaluated_at: datetime,
    market_frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    if prepared.get("status") == "blocked":
        return dict(prepared) | {"training_status": "blocked_no_mature_matched_history"}
    directory = store.directory(prepared["experiment"]["experiment_id"])
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / ".training.lock"), timeout=1):
        return _run_option_experiment(
            prepared, store, evaluated_at=evaluated_at, market_frame=market_frame
        )


def _run_option_experiment(
    prepared: Mapping[str, Any],
    store: OptionExperimentStore,
    *,
    evaluated_at: datetime,
    market_frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    if prepared.get("status") == "blocked":
        return dict(prepared) | {"training_status": "blocked_no_mature_matched_history"}
    protocol = registered_option_protocol(prepared["experiment"])
    empty_groups = [
        group
        for group in ("E1", "E2", "E3", "quality")
        if not any(
            group == "E3" or _option_group(name) == group
            for row in prepared["feature_sidecar"]
            for name in row["option_features"]
        )
    ]
    if empty_groups:
        return {
            "status": "blocked",
            "training_status": "blocked_empty_option_feature_groups",
            "feature_version": protocol.feature_version,
            "empty_feature_groups": empty_groups,
            "promoted": False,
            "published": False,
        }
    if protocol.training_budget is None or protocol.promotion_rules is None:
        raise ValueError(
            "training requires registered numeric budget and promotion rules"
        )
    if protocol.registered_at > evaluated_at:
        raise ValueError("future experiment registration")
    dataset = ResearchDataset.model_validate_json(
        json.dumps(prepared["matched_dataset"])
    )
    decoded_manifest = SplitManifest.model_validate_json(
        json.dumps(prepared["split_manifest"]), strict=False
    )
    manifest = SplitManifest.model_validate(
        decoded_manifest.model_dump(mode="python"), strict=True
    )
    if manifest.spec.embargo_sessions < 20:
        raise ValueError("at least 20-session embargo required")
    if (
        dataset.input_hash != compute_research_dataset_input_hash(dataset.samples)
        or manifest.dataset_input_hash != dataset.input_hash
    ):
        raise ValueError("matched data/manifest hash mismatch")
    sidecar = prepared["feature_sidecar"]
    if content_hash(sidecar) != prepared["option_input_hash"]:
        raise ValueError("option sidecar hash mismatch")
    features = {
        (
            row["symbol"],
            datetime.fromisoformat(row["as_of"].replace("Z", "+00:00")),
            row["horizon"],
        ): row
        for row in sidecar
    }
    if len(features) != len(sidecar) or set(features) != {
        _key(row) for row in dataset.samples
    }:
        raise ValueError("baseline and candidate samples must match exactly")
    for row in dataset.samples:
        source = features[_key(row)]["option_source"]
        if (
            not row.target.resolved
            or row.label_end_at > evaluated_at
            or row.feature_source_max_available_at > row.as_of
        ):
            raise ValueError("immature target or future stock input")
        if (
            datetime.fromisoformat(source["source_available_at"].replace("Z", "+00:00"))
            > row.as_of
        ):
            raise ValueError("future option availability")
        if features[_key(row)]["stock_features"] != row.numeric_features:
            raise ValueError("paired baseline feature mismatch")
    registration = {
        "protocol": protocol.model_dump(mode="json"),
        "matched_input_hash": dataset.input_hash,
        "option_input_hash": prepared["option_input_hash"],
        "split_manifest_hash": manifest.manifest_hash,
        "groups": list(GROUPS),
        "candidate_group": "E3",
        "tuning_trials_per_group": 1,
        "algorithm": "existing-lightgbm-temperature-cqr-v1",
        "stock_feature_set_version": dataset.feature_set_version,
        "calendar_source": str(dataset.calendar_source),
        "calendar_hash": manifest.calendar_hash,
        "stock_returns_input_hash": prepared.get("stock_returns_input_hash"),
        "option_source_definitions": [
            json.loads(value)
            for value in sorted(
                {
                    json.dumps(
                        {k: row["option_source"][k] for k in OPTION_SOURCE_FIELDS},
                        sort_keys=True,
                    )
                    for row in sidecar
                }
            )
        ],
        "economic_input_hash": None
        if market_frame is None
        else content_hash(
            json.loads(market_frame.to_json(orient="records", date_format="iso"))
        ),
    }
    last_train_keys = set(manifest.outer_folds[-1].train_sample_keys)
    last_train_features = [
        value["option_features"]
        for key, value in features.items()
        if key in last_train_keys
    ]
    distributions = {}
    for name in {name for row in last_train_features for name in row}:
        observed = [
            row[name] for row in last_train_features if row.get(name) is not None
        ]
        if observed:
            distributions[name] = {
                "mean": float(np.mean(observed)),
                "std": float(np.std(observed)),
                "minimum": min(observed),
                "maximum": max(observed),
            }
    registration["feature_distributions"] = distributions
    store.register(protocol.experiment_id, registration)
    directory = store.directory(protocol.experiment_id)
    if (directory / "blind-test-consumed.json").exists():
        raise ValueError("blind test already consumed")
    row_map = {_key(row): row for row in dataset.samples}
    all_predictions: dict[int, dict[str, list[Any]]] = {
        h: {g: [] for g in GROUPS} for h in (1, 5, 20)
    }
    evaluation_rows: dict[int, list[ResearchSample]] = {h: [] for h in (1, 5, 20)}
    bundles, selection_reports = [], []
    for fold in manifest.outer_folds:
        for horizon in (1, 5, 20):
            typed_horizon = horizon
            subsets = {
                role: [row_map[k] for k in keys if k[2] == horizon]
                for role, keys in (
                    ("train", fold.train_sample_keys),
                    ("calibration", fold.calibration_sample_keys),
                    ("selection", fold.selection_sample_keys),
                    ("test", fold.test_sample_keys),
                )
            }
            if any(not values for values in subsets.values()):
                raise ValueError(
                    "every partition requires identical 1/5/20 paired samples"
                )
            fold_bundle: dict[str, Any] = {
                "fold_id": fold.fold_id,
                "fold_hash": fold.fold_hash,
                "horizon": horizon,
                "training_end_at": max(
                    r.label_end_at for r in subsets["train"]
                ).isoformat(),
                "calibration_end_at": max(
                    r.label_end_at for r in subsets["calibration"]
                ).isoformat(),
                "selection_end_at": max(
                    r.label_end_at for r in subsets["selection"]
                ).isoformat(),
                "groups": {},
            }
            for group in GROUPS:
                inputs = {
                    role: [
                        {
                            "stock": r.numeric_features,
                            "option": features[_key(r)]["option_features"],
                            "categories": r.categorical_features | {"symbol": r.symbol},
                        }
                        for r in rows
                    ]
                    for role, rows in subsets.items()
                }
                fitted = fit_feature_transform(inputs["train"], group=group)
                matrices = {
                    role: apply_feature_transform(values, fitted)
                    for role, values in inputs.items()
                    if role != "test"
                }
                models = fit_task_models(
                    subsets["train"],
                    subsets["calibration"],
                    matrices["train"],
                    matrices["calibration"],
                    fitted["feature_names"],
                    protocol=protocol,
                    fold=fold,
                    manifest=manifest,
                    group=group,
                    horizon=cast(Horizon, typed_horizon),
                )
                selection_predictions = predict_task_models(
                    models, matrices["selection"], fitted["feature_names"]
                )
                selection_reports.append(
                    {
                        "fold_id": fold.fold_id,
                        "horizon": horizon,
                        "group": group,
                        "metrics": evaluate_tasks(
                            subsets["selection"], selection_predictions
                        ),
                    }
                )
                fold_bundle["groups"][group] = {"transform": fitted, "models": models}
            bundles.append(fold_bundle)
    _atomic_json(
        directory / "models.json", {"registration": registration, "bundles": bundles}
    )
    _atomic_json(
        directory / "selection.json",
        {
            "reports": selection_reports,
            "candidate_group": "E3",
            "selection_rule": "registered_fixed_configuration_no_test_selection",
        },
    )
    store.consume_test(
        protocol.experiment_id,
        {
            "manifest_hash": manifest.manifest_hash,
            "model_bundle_hash": content_hash(bundles),
            "trial_count": len(bundles) * len(GROUPS),
            "sample_keys": [
                content_hash([key[0], key[1].isoformat(), key[2]])
                for fold in manifest.outer_folds
                for key in fold.test_sample_keys
            ],
        },
        evaluated_at,
    )
    fold_metrics = []
    for bundle in bundles:
        fold = next(f for f in manifest.outer_folds if f.fold_id == bundle["fold_id"])
        horizon = bundle["horizon"]
        rows = [row_map[k] for k in fold.test_sample_keys if k[2] == horizon]
        evaluation_rows[horizon] += rows
        for group, item in bundle["groups"].items():
            test_inputs = [
                {
                    "stock": r.numeric_features,
                    "option": features[_key(r)]["option_features"],
                    "categories": r.categorical_features | {"symbol": r.symbol},
                }
                for r in rows
            ]
            prediction = predict_task_models(
                item["models"],
                apply_feature_transform(test_inputs, item["transform"]),
                item["transform"]["feature_names"],
            )
            all_predictions[horizon][group].append(prediction)
            fold_metrics.append(
                {
                    "fold_id": fold.fold_id,
                    "horizon": horizon,
                    "group": group,
                    "metrics": evaluate_tasks(rows, prediction),
                }
            )
    horizons, economics_candidate, economics_baseline = {}, {}, {}
    for horizon, rows in evaluation_rows.items():
        merged = {}
        for group in GROUPS:
            pieces = all_predictions[horizon][group]
            common = set.intersection(*(set(p) for p in pieces))
            merged[group] = {
                task: {
                    name: (
                        sum((p[task][name] for p in pieces), [])
                        if name in ("probabilities", "quantiles", "intervals")
                        else max(p[task][name] for p in pieces)
                        if name == "crossing_rate_before_rearrangement"
                        else pieces[0][task][name]
                    )
                    for name in pieces[0][task]
                }
                for task in common
            }
        metrics = {
            group: evaluate_tasks(rows, value) for group, value in merged.items()
        }
        per_symbol = {}
        for symbol in sorted({r.symbol for r in rows}):
            indices = [i for i, r in enumerate(rows) if r.symbol == symbol]
            per_symbol[symbol] = {
                group: multiclass_brier_score(
                    [str(rows[i].target.forecast_direction) for i in indices],
                    [
                        merged[group][TaskName.FORECAST_DIRECTION]["probabilities"][i]
                        for i in indices
                    ],
                    classes=CLASSES,
                )
                for group in GROUPS
            }
        periods = {}
        cohorts: dict[str, list[int]] = defaultdict(list)
        for i, row in enumerate(rows):
            cohorts[f"year:{row.as_of.year}"].append(i)
            cohorts["ETF" if row.symbol in ("US.SPY", "US.QQQ") else "stock"].append(i)
            for name, dates in protocol.event_cohorts.items():
                if row.as_of.date().isoformat() in dates:
                    cohorts[name].append(i)
        for name, indices in cohorts.items():
            periods[name] = {
                "samples": len(indices),
                "dates": len({rows[i].as_of.date() for i in indices}),
                "brier": {
                    group: multiclass_brier_score(
                        [str(rows[i].target.forecast_direction) for i in indices],
                        [
                            merged[group][TaskName.FORECAST_DIRECTION]["probabilities"][
                                i
                            ]
                            for i in indices
                        ],
                        classes=CLASSES,
                    )
                    for group in GROUPS
                },
            }
        gains = []
        for fold in manifest.outer_folds:
            values = {
                m["group"]: m["metrics"]
                for m in fold_metrics
                if m["fold_id"] == fold.fold_id and m["horizon"] == horizon
            }
            gains.append(
                values["E3"][TaskName.FORECAST_DIRECTION]["brier"]
                < values["B0"][TaskName.FORECAST_DIRECTION]["brier"]
            )
        base_probs, candidate_probs = (
            merged[g][TaskName.FORECAST_DIRECTION]["probabilities"]
            for g in ("B0", "E3")
        )
        horizons[str(horizon)] = {
            "samples": len(rows),
            "dates": len({r.as_of.date() for r in rows}),
            "symbols": sorted(per_symbol),
            "fold_count": len(gains),
            "positive_fold_fraction": sum(gains) / len(gains),
            "metrics": metrics,
            "per_symbol_brier": per_symbol,
            "cohorts": periods,
            "bootstrap": _bootstrap(rows, candidate_probs, base_probs, horizon),
        }
        economics_candidate[horizon] = ([_key(r) for r in rows], candidate_probs)
        economics_baseline[horizon] = ([_key(r) for r in rows], base_probs)
    economic: dict[str, Any] = {
        "status": "unavailable",
        "reason": "executable_price_cost_strategy_evidence_required",
    }
    if market_frame is not None:
        diagnostic = evaluate_m7_direction_economics(
            market_frame,
            candidate_probabilities=economics_candidate,
            incumbent_probabilities=economics_baseline,
        )
        economic = {
            "status": "evaluated",
            "method": "existing_m7_next_open_execution_costs_v1",
            **_dataclass_json(diagnostic),
        }
    report = {
        "status": "evaluated_unpromoted",
        "experiment_id": protocol.experiment_id,
        "generated_at": evaluated_at.isoformat(),
        "evaluation_scope": str(manifest.spec.evaluation_scope),
        "registration": registration,
        "model_bundle_hash": content_hash(bundles),
        "horizons": horizons,
        "fold_metrics": fold_metrics,
        "deployment_coverage": prepared.get("deployment_coverage", 1),
        "refusal_rate": 1 - prepared.get("deployment_coverage", 1),
        "excluded": prepared.get("excluded", {}),
        "event_evidence_reference": protocol.event_evidence_reference,
        "economic_evaluation": economic,
        "promoted": False,
        "published": False,
    }
    report["promotion_assessment"] = assess_promotion(report, protocol.promotion_rules)
    _atomic_json(directory / "evaluation.json", report)
    _atomic_json(
        store.root / "latest-run.json", {"experiment_id": protocol.experiment_id}
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Independent paired option training; no automatic publication"
    )
    parser.add_argument("--root", type=Path, default=Path("var/options/research/runs"))
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--market", type=Path)
    args = parser.parse_args()
    report = run_option_experiment(
        json.loads(args.prepared.read_text(encoding="utf-8")),
        OptionExperimentStore(args.root),
        evaluated_at=datetime.now(UTC),
        market_frame=pd.read_parquet(args.market) if args.market else None,
    )
    print(json.dumps({"status": report["status"], "promoted": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
