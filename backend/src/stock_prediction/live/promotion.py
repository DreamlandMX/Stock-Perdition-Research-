"""One-time blind evaluation of an immutable development candidate."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import pickle
from typing import Any

from sklearn.linear_model import LogisticRegression
from sklearn.metrics import mean_pinball_loss, roc_auc_score
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from stock_prediction.live.candidate_locks import latest_candidate_lock
from stock_prediction.live.config import LivePaths
from stock_prediction.live.features import load_latest_feature_artifact
from stock_prediction.live.promotion_windows import (
    NEXT_PROMOTION_WINDOW,
    PromotionPackageBinding,
    PromotionWindowDefinition,
    PromotionWindowRegistry,
    model_training_readiness,
)
from stock_prediction.live.training import (
    _FEATURES,
    _cqr_adjustment_vector,
    _cumulative_exit_probability,
    _execution_cost_metrics,
    _multiclass_brier,
    _multiclass_calibration_error,
    _net_long_cash_return,
    _temperature_scale,
)
from stock_prediction.operations.registry import LocalModelRegistry


class LiveModelPromoter:
    """Evaluate locked models without candidate search or parameter changes."""

    def __init__(self, paths: LivePaths) -> None:
        self.paths = paths

    def promote(
        self,
        *,
        progress_callback: Any | None = None,
        evaluation_window: PromotionWindowDefinition | None = None,
    ) -> dict[str, object]:
        window = evaluation_window or NEXT_PROMOTION_WINDOW
        target_ledger = self.paths.models / window.ledger_path_name
        lock = latest_candidate_lock(self.paths)
        windows = PromotionWindowRegistry(self.paths)
        if evaluation_window is None:
            readiness = model_training_readiness(self.paths)
        else:
            if target_ledger.exists():
                raise RuntimeError("promotion_window_not_ready")
            if lock is None:
                raise RuntimeError("frozen_candidate_unavailable")
            frozen_at = datetime.fromisoformat(str(lock["created_at"]))
            if (
                window.start <= pd.Timestamp(lock["development_data_cutoff"]).date()
                or window.end >= frozen_at.date()
                or window.label_horizon != NEXT_PROMOTION_WINDOW.label_horizon
                or window.random_seed != lock["random_seed"]
                or window.candidate_config_version
                != NEXT_PROMOTION_WINDOW.candidate_config_version
            ):
                raise RuntimeError("retrospective_promotion_window_invalid")
            if windows.load(window.window_id).definition != window:
                raise RuntimeError("promotion_window_registration_conflict")
            readiness = windows.readiness(
                window.window_id,
                PromotionPackageBinding(
                    package_sha256=str(lock["package_hash"]),
                    universe_version=str(lock["universe_version"]),
                    frozen_at=frozen_at,
                ),
            )
        if not readiness.can_submit_promotion:
            raise RuntimeError("promotion_window_not_ready")
        if lock is None:
            raise RuntimeError("frozen_candidate_unavailable")
        report_path = Path(str(lock["report_path"]))
        package_path = Path(str(lock["package_path"]))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        expected_tasks = {
            (task, horizon)
            for task in ("forecast_direction", "breakout", "return_fwd", "mfe", "mae")
            for horizon in (1, 5, 20)
        } | {("range_exit", horizon) for horizon in (5, 20)}
        if (
            len(report["tasks"]) != 17
            or {(row["task"], row["horizon"]) for row in report["tasks"]}
            != expected_tasks
        ):
            raise RuntimeError("promotion_task_set_incomplete")
        with package_path.open("rb") as handle:
            package = pickle.load(handle)  # noqa: S301 - verified local lock/hash
        evaluation_label = "历史留出评估" if evaluation_window is not None else "盲测"
        if progress_callback is not None:
            progress_callback(
                "promotion_window", 25, f"正在执行冻结候选的一次性{evaluation_label}"
            )
        artifact = load_latest_feature_artifact(self.paths)
        frame = pq.read_table(artifact.path).to_pandas()
        frame["session_date"] = pd.to_datetime(frame["session_date"]).dt.date
        development = frame[
            frame["session_date"]
            <= pd.Timestamp(lock["development_data_cutoff"]).date()
        ]
        promotion = frame[
            frame["session_date"].between(
                window.start,
                window.end,
            )
        ]
        if evaluation_window is not None:
            with target_ledger.open("x", encoding="utf-8") as handle:
                json.dump(
                    {
                        "window_id": window.window_id,
                        "candidate_lock_hash": lock["lock_hash"],
                        "evaluation_scope": "retrospective_holdout",
                        "status": "evaluation_started",
                    },
                    handle,
                )
            windows.consume(
                window.window_id,
                package_sha256=str(lock["package_hash"]),
                reason="retrospective_holdout_evaluation_started",
            )
        results: list[dict[str, Any]] = []
        for row in report["tasks"]:
            task = str(row["task"])
            horizon = int(row["horizon"])
            if task in {"forecast_direction", "breakout"}:
                result = _blind_classification(
                    package,
                    development,
                    promotion,
                    task,
                    horizon,
                )
            elif task in {"return_fwd", "mfe", "mae"}:
                result = _blind_quantile(
                    package,
                    development,
                    promotion,
                    task,
                    horizon,
                )
            else:
                result = _blind_survival(
                    package,
                    development,
                    promotion,
                    horizon,
                )
            result["development_gate_passed"] = bool(row["gate_passed"])
            result["gate_passed"] = bool(row["gate_passed"]) and bool(
                result["gate_passed"]
            )
            results.append(result)
        all_gates_passed = len(results) == 17 and all(
            bool(row["gate_passed"]) for row in results
        )
        registry = LocalModelRegistry(self.paths.models / "registry")
        before = registry.snapshot("forecast_bundle", 20)
        component_releases = list(report.get("component_releases", []))
        promoted_component_releases: list[dict[str, Any]] = []
        formal_model_version = (
            f"formal-{'mature-' if evaluation_window is not None else ''}"
            f"{window.end:%Y%m%d}-{str(lock['package_hash'])[:8]}"
        )
        if all_gates_passed:
            if (
                len(component_releases) != 17
                or {(row["task"], row["horizon"]) for row in component_releases}
                != expected_tasks
            ):
                raise RuntimeError("promotion_component_set_incomplete")
            for component in component_releases:
                component_path = (
                    report_path.parent
                    / "components"
                    / f"{component['task']}-{component['horizon']}-package.pkl"
                )
                if not component_path.is_file():
                    raise RuntimeError("promotion_component_package_unavailable")
            formal_release = registry.register_release(
                model_version=formal_model_version,
                task="forecast_bundle",
                horizon=20,
                calibration_version=str(report["calibration_version"]),
                source_decision_id=str(
                    promotion_report_id := (
                        f"promotion-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
                    )
                ),
                promotion_eligible=True,
                artifacts={"production-package.pkl": package_path},
                registered_at=datetime.now(UTC),
            )
            registry.promote(
                formal_model_version,
                reason=f"immutable candidate passed one-time {evaluation_label}",
                recorded_at=datetime.now(UTC),
            )
            for component in component_releases:
                task = str(component["task"])
                horizon = int(component["horizon"])
                component_path = (
                    report_path.parent / "components" / f"{task}-{horizon}-package.pkl"
                )
                formal_component_version = f"{formal_model_version}-{task}-{horizon}"
                previous = registry.snapshot(task, horizon).champion_version
                release = registry.register_release(
                    model_version=formal_component_version,
                    task=task,
                    horizon=horizon,
                    calibration_version=str(report["calibration_version"]),
                    source_decision_id=promotion_report_id,
                    promotion_eligible=True,
                    artifacts={"component-package.pkl": component_path},
                    registered_at=datetime.now(UTC),
                )
                registry.promote(
                    formal_component_version,
                    reason=f"component passed same-batch one-time {evaluation_label}",
                    recorded_at=datetime.now(UTC),
                )
                promoted_component_releases.append(
                    {
                        **component,
                        "model_version": formal_component_version,
                        "previous_champion_version": previous,
                        "release_hash": release.manifest_hash,
                    }
                )
        else:
            promotion_report_id = f"promotion-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
        promotion_report = {
            "run_id": promotion_report_id,
            "candidate_model_version": lock["candidate_model_version"],
            "candidate_lock_hash": lock["lock_hash"],
            "window_id": window.window_id,
            "evaluation_scope": (
                "retrospective_holdout"
                if evaluation_window is not None
                else "registered_blind_window"
            ),
            "window_start": window.start.isoformat(),
            "window_end": window.end.isoformat(),
            "required_anchor_sessions": window.required_anchor_sessions,
            "window_registration_hash": windows.load(
                window.window_id
            ).registration_hash,
            "feature_input_hash": artifact.input_hash,
            "evaluated_at": datetime.now(UTC).isoformat(),
            "candidate_search_executed": False,
            "tasks": results,
            "all_gates_passed": all_gates_passed,
            "automatic_promotion": ("promoted" if all_gates_passed else "kept_current"),
            "model_version": (
                formal_model_version
                if all_gates_passed
                else lock["candidate_model_version"]
            ),
            "previous_champion_version": before.champion_version,
            "component_releases": promoted_component_releases,
            "candidate_release_hash": (
                formal_release.manifest_hash if all_gates_passed else None
            ),
        }
        directory = self.paths.models / "promotion-runs"
        directory.mkdir(parents=True, exist_ok=True)
        report_target = directory / f"{promotion_report['run_id']}.json"
        report_target.write_text(
            json.dumps(promotion_report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary = target_ledger.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "run_id": promotion_report["run_id"],
                    "consumed_at": datetime.now(UTC).isoformat(),
                    "candidate_lock_hash": lock["lock_hash"],
                    "window_id": window.window_id,
                    "result": "passed" if all_gates_passed else "failed",
                    "report_path": str(report_target.resolve()),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, target_ledger)
        if progress_callback is not None:
            progress_callback(
                "champion_publication",
                90,
                f"{evaluation_label}通过并晋升整套模型"
                if all_gates_passed
                else f"{evaluation_label}未通过，保留研究候选",
            )
        return promotion_report


def _blind_classification(
    package: dict[str, Any],
    development: pd.DataFrame,
    promotion: pd.DataFrame,
    task: str,
    horizon: int,
) -> dict[str, Any]:
    target = (
        f"direction_{horizon}"
        if task == "forecast_direction"
        else f"breakout_direction_{horizon}"
    )
    development = development.dropna(subset=[target])
    promotion = promotion.dropna(subset=[target])
    entry = package["classifiers"][(task, horizon)]
    model = entry["model"]
    probability = _temperature_scale(
        model.predict_proba(promotion[list(_FEATURES)]),
        float(entry.get("temperature") or 1.0),
    )
    labels = promotion[target].astype(int).to_numpy()
    candidate_brier = _multiclass_brier(labels, probability, model.classes_)
    frequencies = np.asarray(
        [
            float((development[target].astype(int) == label).mean())
            for label in model.classes_
        ]
    )
    frequency_brier = _multiclass_brier(
        labels,
        np.tile(frequencies, (len(promotion), 1)),
        model.classes_,
    )
    imputation = development[list(_FEATURES)].median().fillna(0.0)
    logistic = LogisticRegression(max_iter=500, random_state=17)
    logistic.fit(
        development[list(_FEATURES)].fillna(imputation),
        development[target].astype(int),
    )
    logistic_probability = logistic.predict_proba(
        promotion[list(_FEATURES)].fillna(imputation)
    )
    logistic_brier = _multiclass_brier(
        labels,
        logistic_probability,
        logistic.classes_,
    )
    calibration_error = _multiclass_calibration_error(
        labels,
        probability,
        model.classes_,
    )
    shares = promotion[target].astype(int).value_counts(normalize=True)
    labels_healthy = all(float(shares.get(value, 0.0)) >= 0.02 for value in (-1, 0, 1))
    passed = (
        candidate_brier < min(frequency_brier, logistic_brier)
        and calibration_error <= 0.05
        and labels_healthy
    )
    return {
        "task": task,
        "horizon": horizon,
        "sample_count": len(promotion),
        "candidate_brier": candidate_brier,
        "frequency_brier": frequency_brier,
        "logistic_brier": logistic_brier,
        "calibration_error": calibration_error,
        "label_health_passed": labels_healthy,
        "gate_passed": passed,
        "net_long_cash_return": _net_long_cash_return(
            promotion, probability, model.classes_, horizon
        ),
        "execution_cost_sensitivity": _execution_cost_metrics(
            promotion, probability, model.classes_, horizon
        ),
    }


def _blind_quantile(
    package: dict[str, Any],
    development: pd.DataFrame,
    promotion: pd.DataFrame,
    task: str,
    horizon: int,
) -> dict[str, Any]:
    target = f"{task}_{horizon}"
    development = development.dropna(subset=[target])
    promotion = promotion.dropna(subset=[target])
    entry = package["quantiles"][(task, horizon)]
    quantiles = tuple(float(value) for value in entry["quantiles"])
    predictions = {
        alpha: np.asarray(
            entry["models"][alpha].predict(promotion[list(_FEATURES)]),
            dtype=float,
        )
        for alpha in quantiles
    }
    observed = promotion[target].astype(float).to_numpy()
    adjustments = dict(
        entry.get("cqr_adjustments", {"pooled": entry["cqr_adjustment"]})
    )
    lower_adjustment = _cqr_adjustment_vector(
        promotion,
        dict(entry.get("cqr_lower_adjustments", adjustments)),
    )
    upper_adjustment = _cqr_adjustment_vector(
        promotion,
        dict(entry.get("cqr_upper_adjustments", adjustments)),
    )
    coverage = float(
        np.mean(
            (observed >= predictions[0.10] - lower_adjustment)
            & (observed <= predictions[0.90] + upper_adjustment)
        )
    )
    candidate_pinball = float(
        np.mean(
            [
                mean_pinball_loss(observed, predictions[alpha], alpha=alpha)
                for alpha in quantiles
            ]
        )
    )
    baseline_pinball = float(
        np.mean(
            [
                mean_pinball_loss(
                    observed,
                    np.full(len(promotion), development[target].quantile(alpha)),
                    alpha=alpha,
                )
                for alpha in quantiles
            ]
        )
    )
    passed = candidate_pinball < baseline_pinball and 0.75 <= coverage <= 0.85
    return {
        "task": task,
        "horizon": horizon,
        "sample_count": len(promotion),
        "candidate_pinball": candidate_pinball,
        "baseline_pinball": baseline_pinball,
        "cqr_coverage": coverage,
        "gate_passed": passed,
    }


def _blind_survival(
    package: dict[str, Any],
    development: pd.DataFrame,
    promotion: pd.DataFrame,
    horizon: int,
) -> dict[str, Any]:
    target = f"range_exit_event_{horizon}"
    day_target = f"range_exit_day_{horizon}"
    development = development.dropna(subset=[target, day_target])
    promotion = promotion.dropna(subset=[target, day_target])
    entry = package["survival"][horizon]
    raw = _cumulative_exit_probability(
        entry["model"],
        promotion,
        horizon,
        [*_FEATURES, "elapsed_day"],
    )
    calibrator = entry.get("calibrator")
    cumulative = (
        np.asarray(calibrator.predict(raw), dtype=float)
        if calibrator is not None
        else raw
    )
    observed = promotion[target].astype(int).to_numpy()
    brier = float(np.mean((cumulative - observed) ** 2))
    null_probability = float(development[target].mean())
    null_brier = float(np.mean((null_probability - observed) ** 2))
    c_index = (
        float(roc_auc_score(observed, cumulative)) if len(set(observed)) == 2 else 0.5
    )
    calibration_error = abs(float(cumulative.mean()) - float(observed.mean()))
    event_share = float(observed.mean()) if len(observed) else 0.0
    labels_healthy = 0.05 <= event_share <= 0.95
    passed = (
        brier < null_brier
        and c_index > 0.5
        and calibration_error <= 0.05
        and labels_healthy
    )
    return {
        "task": "range_exit",
        "horizon": horizon,
        "sample_count": len(promotion),
        "time_dependent_brier": brier,
        "null_brier": null_brier,
        "c_index": c_index,
        "cumulative_probability_calibration_error": calibration_error,
        "label_health_passed": labels_healthy,
        "gate_passed": passed,
    }


__all__ = ["LiveModelPromoter"]
