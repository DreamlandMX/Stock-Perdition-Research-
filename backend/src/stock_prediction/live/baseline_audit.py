"""Bounded development-only comparison; never registers or promotes a model."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any
import warnings

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from stock_prediction.live.training import (
    _DEVELOPMENT_CUTOFF,
    _FEATURES,
    _fit_temperature,
    _frame_fingerprint,
    _freeze_development_labels,
    _multiclass_brier,
    _multiclass_calibration_error,
    _temperature_scale,
)


def compare_direction_baseline(
    train: pd.DataFrame, calibration: pd.DataFrame, test: pd.DataFrame, horizon: int
) -> tuple[dict[str, object], pd.DataFrame]:
    """Compare one fixed classifier with/without calibration on unseen rows."""
    target = f"direction_{horizon}"
    train, calibration, test = (
        part.dropna(subset=[target]) for part in (train, calibration, test)
    )
    if any(part.empty for part in (train, calibration, test)):
        raise ValueError("baseline_partition_empty")
    if not (
        train.session_date.max() < calibration.session_date.min()
        and calibration.session_date.max() < test.session_date.min()
    ):
        raise ValueError("baseline_partitions_overlap")
    medians = train[list(_FEATURES)].median().fillna(0.0)
    model = LogisticRegression(max_iter=500, random_state=17)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", ConvergenceWarning)
        model.fit(train[list(_FEATURES)].fillna(medians), train[target].astype(int))
    if tuple(model.classes_) != (-1, 0, 1):
        raise ValueError("baseline_requires_three_classes")
    raw = model.predict_proba(test[list(_FEATURES)].fillna(medians))
    temperature = _fit_temperature(
        model.predict_proba(calibration[list(_FEATURES)].fillna(medians)),
        calibration[target].astype(int).to_numpy(),
        model.classes_,
    )
    calibrated = _temperature_scale(raw, temperature)
    frequency = np.tile(
        [(train[target] == label).mean() for label in model.classes_], (len(test), 1)
    )
    actual = test[target].astype(int).to_numpy()
    rows = test[["symbol", "session_date", "is_etf"]].reset_index(drop=True)
    rows["actual"] = actual
    metrics: dict[str, object] = {
        "horizon": horizon,
        "sample_count": len(test),
        "train_start": str(train.session_date.min()),
        "train_end": str(train.session_date.max()),
        "calibration_start": str(calibration.session_date.min()),
        "calibration_end": str(calibration.session_date.max()),
        "test_start": str(test.session_date.min()),
        "test_end": str(test.session_date.max()),
        "temperature": temperature,
        "converged": not any(
            issubclass(w.category, ConvergenceWarning) for w in recorded
        ),
    }
    for name, probability in (
        ("frequency", frequency),
        ("logistic", raw),
        ("calibrated_logistic", calibrated),
    ):
        metrics[name + "_brier"] = _multiclass_brier(
            actual, probability, model.classes_
        )
        metrics[name + "_calibration_error"] = _multiclass_calibration_error(
            actual, probability, model.classes_
        )
        metrics[name + "_accuracy"] = float(
            np.mean(model.classes_[probability.argmax(axis=1)] == actual)
        )
        for index, label in enumerate(("down", "neutral", "up")):
            rows[name + "_" + label] = probability[:, index]
    return metrics, rows


def load_audit_development(
    features: Path,
    source_report: Path,
) -> tuple[dict[str, Any], dict[str, Any], pd.DataFrame]:
    report = json.loads(source_report.read_text(encoding="utf-8"))
    manifest = json.loads(
        (features.parent / "artifact.json").read_text(encoding="utf-8")
    )
    if manifest["input_hash"] != report["feature_input_hash"]:
        raise ValueError("baseline_feature_identity_mismatch")
    # Filter before materializing: no selection on the reserved promotion window.
    frame = pq.read_table(
        features, filters=[("session_date", "<=", _DEVELOPMENT_CUTOFF)]
    ).to_pandas()
    frame["session_date"] = pd.to_datetime(frame["session_date"]).dt.date
    development = _freeze_development_labels(frame)
    if _frame_fingerprint(development) != report["development_data_hash"]:
        raise ValueError("baseline_development_hash_mismatch")
    return report, manifest, development


def run_audit(features: Path, source_report: Path, output: Path) -> dict[str, object]:
    report, manifest, development = load_audit_development(features, source_report)
    output.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, object]] = []
    artifact_rows: list[pd.DataFrame] = []
    with threadpool_limits(limits=2):
        for task in report["tasks"]:
            if task["task"] != "forecast_direction":
                continue
            horizon = int(task["horizon"])
            if horizon not in (1, 5, 20):
                raise ValueError("baseline_invalid_horizon")
            for fold in task["folds"]:
                dates = pd.to_datetime(development.session_date)
                train = development[
                    dates.between(fold["train_start"], fold["train_end"])
                ]
                validation = development[
                    dates.between(fold["validation_start"], fold["validation_end"])
                ]
                # Fixed model, no tuning: reserve the latter half exclusively for calibration.
                sessions = sorted(validation.session_date.unique())
                calibration = validation[
                    validation.session_date.isin(sessions[len(sessions) // 2 :])
                ]
                test = development[dates.between(fold["test_start"], fold["test_end"])]
                metrics, rows = compare_direction_baseline(
                    train, calibration, test, horizon
                )
                if metrics["sample_count"] != fold["sample_count"]:
                    raise ValueError("baseline_test_sample_mismatch")
                metrics["fold_id"] = fold["fold_id"]
                metrics["archived_candidate_brier"] = fold["candidate_brier"]
                metrics["archived_logistic_brier"] = fold["logistic_brier"]
                results.append(metrics)
                rows["fold_id"] = fold["fold_id"]
                rows["horizon"] = horizon
                rows["task"] = "forecast_direction"
                symbol_dates = {
                    symbol: dict(
                        zip(
                            part.session_date,
                            part.session_date.shift(-horizon),
                            strict=True,
                        )
                    )
                    for symbol, part in development.sort_values("session_date").groupby(
                        "symbol"
                    )
                }
                rows["target_end_date"] = [
                    symbol_dates[symbol][day]
                    for symbol, day in zip(rows.symbol, rows.session_date, strict=True)
                ]
                if rows.target_end_date.isna().any():
                    raise ValueError("baseline_immature_target")
                artifact_rows.append(rows)
                print(json.dumps(metrics), flush=True)
    rows = pd.concat(artifact_rows, ignore_index=True)
    rows["source_run_id"] = report["run_id"]
    rows["feature_input_hash"] = manifest["input_hash"]
    predictions_path = output / "oos-predictions.parquet"
    pq.write_table(pa.Table.from_pandas(rows, preserve_index=False), predictions_path)
    result: dict[str, object] = {
        "schema_version": "direction-baseline-audit-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_run_id": report["run_id"],
        "source_report_sha256": hashlib.sha256(source_report.read_bytes()).hexdigest(),
        "feature_input_hash": manifest["input_hash"],
        "feature_file_sha256": hashlib.sha256(features.read_bytes()).hexdigest(),
        "development_data_hash": report["development_data_hash"],
        "scope": "development_replay_not_prospective",
        "strict_historical_pit": False,
        "promotion_window_consumed": False,
        "model_promoted": False,
        "configuration": {"C": 1.0, "max_iter": 500, "random_seed": 17, "threads": 2},
        "folds": results,
        "predictions_file": predictions_path.name,
        "predictions_sha256": hashlib.sha256(predictions_path.read_bytes()).hexdigest(),
        "prediction_rows": len(rows),
    }
    (output / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_audit(args.features, args.source_report, args.output)


if __name__ == "__main__":
    main()
