"""Fixed-recipe return audit on already consumed development windows only."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from lightgbm import LGBMRegressor
import numpy as np
import pandas as pd
from sklearn.metrics import mean_pinball_loss
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from stock_prediction.live.baseline_audit import load_audit_development
from stock_prediction.live.training import (
    _FEATURES,
    _cqr_adjustment_vector,
    _group_cqr_adjustments,
    _ordered_predictions,
    _volatility_quantile_baseline,
    _write_fold_evidence,
    split_validation_selection_calibration,
)


def run_audit(features: Path, source_report: Path, output: Path) -> dict[str, Any]:
    report, manifest, development = load_audit_development(features, source_report)
    output.mkdir(parents=True, exist_ok=False)
    quantiles = (0.1, 0.25, 0.5, 0.75, 0.9)
    # Frozen before evaluation; no search or selection on these test results.
    config: dict[str, Any] = dict(
        n_estimators=180,
        learning_rate=0.04,
        num_leaves=31,
        min_child_samples=100,
        subsample=0.85,
        colsample_bytree=0.85,
        reg_lambda=1.0,
        n_jobs=2,
        random_state=17,
        verbosity=-1,
    )
    folds = []
    dates = pd.to_datetime(development.session_date)
    with threadpool_limits(limits=2):
        for task in report["tasks"]:
            if task["task"] != "forecast_direction":
                continue
            horizon = task["horizon"]
            if horizon not in (1, 5, 20):
                raise ValueError("quantile_audit_invalid_horizon")
            target = f"return_fwd_{horizon}"
            for index, fold in enumerate(task["folds"], 1):
                train = development[
                    dates.between(fold["train_start"], fold["train_end"])
                ].dropna(subset=[target])
                validation = development[
                    dates.between(fold["validation_start"], fold["validation_end"])
                ]
                _, calibration = split_validation_selection_calibration(validation)
                calibration = calibration.dropna(subset=[target])
                test = development[
                    dates.between(fold["test_start"], fold["test_end"])
                ].dropna(subset=[target])
                if len(test) != fold["sample_count"]:
                    raise ValueError("quantile_audit_sample_mismatch")
                raw, cal = {}, {}
                for alpha in quantiles:
                    model = LGBMRegressor(objective="quantile", alpha=alpha, **config)
                    model.fit(train[list(_FEATURES)], train[target])
                    raw[alpha] = model.predict(test[list(_FEATURES)])
                    cal[alpha] = model.predict(calibration[list(_FEATURES)])
                ordered = _ordered_predictions(raw, "return_fwd")
                cal = _ordered_predictions(cal, "return_fwd")
                scores = np.maximum.reduce(
                    (
                        cal[0.1] - calibration[target].to_numpy(),
                        calibration[target].to_numpy() - cal[0.9],
                        np.zeros(len(calibration)),
                    )
                )
                adjustments = _group_cqr_adjustments(calibration, scores)
                adjustment = _cqr_adjustment_vector(test, adjustments)
                lower, upper = ordered[0.1] - adjustment, ordered[0.9] + adjustment
                scaled = _volatility_quantile_baseline(train, test, target, quantiles)
                historical = {
                    q: np.full(len(test), train[target].quantile(q)) for q in quantiles
                }
                y = test[target].to_numpy()
                metrics: dict[str, Any] = {
                    name + "_pinball": float(
                        np.mean(
                            [
                                mean_pinball_loss(y, predictions[q], alpha=q)
                                for q in quantiles
                            ]
                        )
                    )
                    for name, predictions in (
                        ("raw", raw),
                        ("ordered", ordered),
                        ("historical", historical),
                        ("volatility", scaled),
                    )
                }
                metrics.update(
                    horizon=horizon,
                    fold_id=fold["fold_id"],
                    sample_count=len(test),
                    test_start=fold["test_start"],
                    test_end=fold["test_end"],
                    calibration_start=str(calibration.session_date.min()),
                    calibration_end=str(calibration.session_date.max()),
                    coverage=float(np.mean((y >= lower) & (y <= upper))),
                    average_width=float(np.mean(upper - lower)),
                    crossing_rate=float(
                        np.mean(
                            np.any(
                                np.diff(np.column_stack(list(raw.values())), axis=1)
                                < 0,
                                axis=1,
                            )
                        )
                    ),
                )
                metrics["oos_evidence"] = _write_fold_evidence(
                    output / "oos",
                    development,
                    test,
                    "return_fwd",
                    horizon,
                    index,
                    {
                        "actual": y,
                        "calibrated_lower": lower,
                        "calibrated_upper": upper,
                        **{
                            f"{name}_q{round(q * 100)}": values
                            for name, predictions in (
                                ("raw", raw),
                                ("ordered", ordered),
                                ("historical", historical),
                                ("volatility", scaled),
                            )
                            for q, values in predictions.items()
                        },
                    },
                )
                folds.append(metrics)
                print(json.dumps(metrics), flush=True)
    result = dict(
        scope="development_replay_not_prospective",
        strict_historical_pit=False,
        promotion_window_consumed=False,
        model_promoted=False,
        source_run_id=report["run_id"],
        feature_input_hash=manifest["input_hash"],
        development_data_hash=report["development_data_hash"],
        source_report_sha256=hashlib.sha256(source_report.read_bytes()).hexdigest(),
        configuration=config,
        generated_at=datetime.now(UTC).isoformat(),
        folds=folds,
    )
    (output / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_audit(args.features, args.source_report, args.output)
