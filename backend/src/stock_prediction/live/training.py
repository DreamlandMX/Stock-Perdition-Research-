"""Registered real-data walk-forward challenge evaluation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
import hashlib
import json
import os
from pathlib import Path
import pickle
from typing import Any
from uuid import uuid4

from lightgbm import LGBMClassifier, LGBMRegressor
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import log_loss, mean_pinball_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

from stock_prediction.live.quantiles import (
    EVALUATION_PIPELINE_VERSION, finite_sample_cqr, ordered_quantiles,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.artifact_references import (
    ArtifactReference,
    ArtifactReferenceError,
    build_artifact_reference,
    resolve_artifact_reference,
)
from stock_prediction.live.candidate_locks import create_candidate_lock
from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_V1,
    candidate_recipe,
    uses_expanded_direction_search,
)
from stock_prediction.live.features import load_latest_feature_artifact
from stock_prediction.live.promotion_windows import (
    NEXT_PROMOTION_WINDOW,
)
from stock_prediction.operations.registry import LocalModelRegistry
from stock_prediction.live.universe_scope import resolve_live_universe
from stock_prediction.modeling.report import build_m7_training_report_sections

_FEATURES = (
    "return_1",
    "return_5",
    "return_20",
    "return_60",
    "volatility_5",
    "volatility_20",
    "volatility_60",
    "atr_pct_20",
    "channel_width_20",
    "channel_position_20",
    "trend_slope_20",
    "ma_distance_20",
    "ma_distance_50",
    "ma_distance_200",
    "gap_1",
    "volume_z_20",
    "volume_price_anomaly_20",
    "market_breadth_up_lag1",
    "relative_spy_20",
    "relative_spy_60",
    "rolling_beta_spy_60_lag1",
    "rolling_corr_spy_60_lag1",
    "is_etf",
)
_RANDOM_SEED = 17
_DEVELOPMENT_CUTOFF = date(2026, 6, 29)
_PROMOTION_START = NEXT_PROMOTION_WINDOW.start
_PROMOTION_END = NEXT_PROMOTION_WINDOW.end
_EXPECTED_PROMOTION_SESSIONS = NEXT_PROMOTION_WINDOW.required_anchor_sessions
_MIN_CLASS_SHARE = 0.02
_MIN_SURVIVAL_SHARE = 0.05
_CLASSIFICATION_CANDIDATES = 16
_V2_DIRECTION_CANDIDATES = 24
_OTHER_CANDIDATES = 12
_MAX_PARALLEL_TRIALS = 4
_EXECUTION_COST_SCENARIOS_BPS = (5, 10, 20)
ProgressCallback = Callable[[str, int, str], None]
LIVE_TRAINING_REPORT_SCHEMA_VERSION = "live-training-report-v1"


def attach_m7_training_report_sections(
    report: dict[str, Any],
    *,
    completed_sections: dict[str, object],
    failed_sections: dict[str, str],
    not_run_sections: dict[str, str],
) -> dict[str, Any]:
    """Return a report copy containing the complete governed M7 section set."""

    return {
        **report,
        "m7_sections": build_m7_training_report_sections(
            completed_sections=completed_sections,
            failed_sections=failed_sections,
            not_run_sections=not_run_sections,
        ),
    }


def write_latest_training_pointer(
    paths: LivePaths,
    report_path: Path,
    run_id: str,
) -> Path:
    reference = build_artifact_reference(
        artifact_root=paths.models,
        artifact_id=run_id,
        target=report_path,
        schema_version=LIVE_TRAINING_REPORT_SCHEMA_VERSION,
        created_at=datetime.now(UTC),
    )
    pointer = paths.models / "LATEST-TRAINING.json"
    temporary = pointer.with_name(f".{pointer.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "report_reference": reference.as_dict(),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, pointer)
    return pointer


def load_latest_training_report(paths: LivePaths) -> dict[str, Any]:
    pointer = paths.models / "LATEST-TRAINING.json"
    if not pointer.is_file():
        raise FileNotFoundError("latest training report is unavailable")
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("training_report_pointer_invalid") from error
    if not isinstance(payload, dict):
        raise RuntimeError("training_report_pointer_invalid")
    if "report_path" in payload:
        raise RuntimeError("training_report_path_invalid")
    run_id = payload.get("run_id")
    reference_payload = payload.get("report_reference")
    if not isinstance(run_id, str) or not isinstance(reference_payload, dict):
        raise RuntimeError("training_report_pointer_invalid")
    try:
        reference = ArtifactReference.from_dict(reference_payload)
    except ArtifactReferenceError as error:
        raise _training_report_reference_error(error) from error
    if reference.schema_version != LIVE_TRAINING_REPORT_SCHEMA_VERSION:
        raise RuntimeError("training_report_schema_invalid")
    if reference.artifact_id != run_id:
        raise RuntimeError("training_report_identity_invalid")
    try:
        report_path = resolve_artifact_reference(paths.models, reference)
    except ArtifactReferenceError as error:
        raise _training_report_reference_error(error) from error
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("training_report_content_invalid") from error
    if not isinstance(report, dict) or report.get("run_id") != run_id:
        raise RuntimeError("training_report_identity_invalid")
    return report


def _training_report_reference_error(error: ArtifactReferenceError) -> RuntimeError:
    code = str(error)
    if code == "artifact_hash_mismatch":
        return RuntimeError("training_report_hash_mismatch")
    if code == "artifact_missing":
        return RuntimeError("training_report_missing")
    if code in {"artifact_path_invalid", "artifact_path_outside_root"}:
        return RuntimeError("training_report_path_invalid")
    return RuntimeError("training_report_reference_invalid")


class DecomposedDirectionClassifier:
    """Two binary tail models combined into {-1, 0, 1} probabilities."""

    classes_ = np.asarray((-1, 0, 1), dtype=int)

    def __init__(self, config: dict[str, Any]) -> None:
        self.up_model = LGBMClassifier(objective="binary", **config, verbosity=-1)
        self.down_model = LGBMClassifier(objective="binary", **config, verbosity=-1)

    def fit(self, features: pd.DataFrame, labels: np.ndarray) -> None:
        self.up_model.fit(features, (labels == 1).astype(int))
        self.down_model.fit(features, (labels == -1).astype(int))

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        up = np.asarray(self.up_model.predict_proba(features)[:, 1], dtype=float)
        down = np.asarray(self.down_model.predict_proba(features)[:, 1], dtype=float)
        tail_sum = up + down
        scale = np.where(tail_sum > 0.98, 0.98 / tail_sum, 1.0)
        up *= scale
        down *= scale
        flat = 1.0 - up - down
        return np.column_stack((down, flat, up))


class ElasticNetDirectionClassifier:
    """Point-in-time imputed, class-balanced multinomial elastic net."""

    def __init__(
        self,
        *,
        alpha: float,
        l1_ratio: float,
        class_weight: str | None = "balanced",
    ) -> None:
        self.model = SGDClassifier(
            alpha=alpha,
            class_weight=class_weight,
            l1_ratio=l1_ratio,
            loss="log_loss",
            max_iter=1_000,
            n_jobs=4,
            penalty="elasticnet",
            random_state=_RANDOM_SEED,
            tol=1e-3,
        )
        self.imputation: pd.Series | None = None
        self.scaler = StandardScaler()

    @property
    def classes_(self) -> np.ndarray:
        return np.asarray(self.model.classes_)

    def fit(self, features: pd.DataFrame, labels: np.ndarray) -> None:
        self.imputation = features.median().fillna(0.0)
        scaled = self.scaler.fit_transform(features.fillna(self.imputation))
        self.model.fit(scaled, labels)

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        if self.imputation is None:
            raise RuntimeError("elastic_net_classifier_not_fitted")
        return np.asarray(
            self.model.predict_proba(
                self.scaler.transform(features.fillna(self.imputation))
            ),
            dtype=float,
        )


class ProbabilityBlendDirectionClassifier:
    """Blend regularized linear and nonlinear direction probabilities."""

    classes_ = np.asarray((-1, 0, 1), dtype=int)

    def __init__(self, config: dict[str, Any], *, nonlinear_weight: float) -> None:
        self.linear = ElasticNetDirectionClassifier(
            alpha=0.0001,
            l1_ratio=0.4,
            class_weight=None,
        )
        self.nonlinear = MarketStateFusionClassifier(config)
        self.nonlinear_weight = nonlinear_weight

    def fit(self, features: pd.DataFrame, labels: np.ndarray) -> None:
        self.linear.fit(features, labels)
        self.nonlinear.fit(features, labels)

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        linear = _probabilities_in_class_order(
            self.linear.predict_proba(features),
            self.linear.classes_,
            self.classes_,
        )
        nonlinear = _probabilities_in_class_order(
            self.nonlinear.predict_proba(features),
            self.nonlinear.classes_,
            self.classes_,
        )
        return (
            1.0 - self.nonlinear_weight
        ) * linear + self.nonlinear_weight * nonlinear


class SurvivalProbabilityCalibrator:
    """Pickle-safe probability calibration wrapper used by survival packages."""

    def __init__(
        self,
        method: str,
        *,
        model: Any | None = None,
        event_rate: float = 0.0,
        shrinkage: float = 0.0,
    ) -> None:
        self.method = method
        self.model = model
        self.event_rate = event_rate
        self.shrinkage = shrinkage

    def predict(self, values: np.ndarray | list[float]) -> np.ndarray:
        raw = np.asarray(values, dtype=float)
        if self.method == "platt" and self.model is not None:
            calibrated = np.asarray(
                self.model.predict_proba(raw.reshape(-1, 1))[:, 1],
                dtype=float,
            )
        elif self.method == "isotonic" and self.model is not None:
            calibrated = np.asarray(self.model.predict(raw), dtype=float)
        else:
            calibrated = raw
        if self.shrinkage > 0:
            calibrated = (
                1.0 - self.shrinkage
            ) * calibrated + self.shrinkage * self.event_rate
        return np.asarray(np.clip(calibrated, 0.0, 1.0), dtype=float)


class MarketStateFusionClassifier:
    """Balanced multiclass model with a deterministic lagged market-state feature."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.model = LGBMClassifier(
            objective="multiclass",
            class_weight="balanced",
            **config,
            verbosity=-1,
        )

    @property
    def classes_(self) -> np.ndarray:
        return np.asarray(self.model.classes_)

    @staticmethod
    def _augment(features: pd.DataFrame) -> pd.DataFrame:
        augmented = features.copy()
        slope = augmented["trend_slope_20"].fillna(0.0)
        distance = augmented["ma_distance_50"].fillna(0.0)
        augmented["derived_market_state"] = np.select(
            ((slope > 0.0) & (distance > 0.0), (slope < 0.0) & (distance < 0.0)),
            (1.0, -1.0),
            default=0.0,
        )
        return augmented

    def fit(self, features: pd.DataFrame, labels: np.ndarray) -> None:
        self.model.fit(self._augment(features), labels)

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        return np.asarray(
            self.model.predict_proba(self._augment(features)), dtype=float
        )


class LiveModelTrainer:
    """Train the complete production matrix and atomically promote only full wins."""

    def __init__(self, paths: LivePaths, *, cache: Any | None = None) -> None:
        self.paths = paths
        self.universe = resolve_live_universe(paths)
        self.cache = cache
        paths.ensure()

    def train(
        self,
        *,
        progress_callback: ProgressCallback | None = None,
        recipe_version: str = CANDIDATE_RECIPE_V1,
        run_id: str | None = None,
    ) -> dict[str, object]:
        recipe = candidate_recipe(recipe_version)
        artifact = load_latest_feature_artifact(self.paths)
        if artifact.universe_version != self.universe.version:
            raise RuntimeError("candidate_feature_universe_mismatch")
        frame = pq.read_table(artifact.path).to_pandas()
        frame["session_date"] = pd.to_datetime(frame["session_date"]).dt.date
        development_frame = _freeze_development_labels(frame)
        if development_frame.empty:
            raise RuntimeError("candidate_development_data_unavailable")
        if max(development_frame["session_date"]) > _DEVELOPMENT_CUTOFF:
            raise RuntimeError("candidate_development_cutoff_violation")
        development_data_hash = _frame_fingerprint(development_frame)
        _progress(
            progress_callback,
            "data_repair",
            5,
            "复权数据已加载；正在检查未解释的异常收益",
        )
        quarantined_rows = (
            int(
                development_frame["quality_quarantined"]
                .fillna(False)
                .astype(bool)
                .sum()
            )
            if "quality_quarantined" in development_frame
            else 0
        )
        data_quality_passed = quarantined_rows == 0
        _progress(
            progress_callback,
            "label_health",
            10,
            "正在验证开发、校准与测试窗口的标签类别占比",
        )
        run_id = run_id or (
            f"live-train-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
            f"-r{recipe.generation}-{artifact.input_hash[:8]}"
        )
        if not run_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in run_id):
            raise ValueError("candidate_run_id_invalid")
        run_directory = self.paths.models / "runs" / run_id
        run_directory.mkdir(parents=True, exist_ok=True)
        candidate_matrix = _registered_candidate_matrix(recipe.version)
        candidate_matrix_payload = json.dumps(
            candidate_matrix,
            indent=2,
            sort_keys=True,
        )
        candidate_matrix_hash = _candidate_matrix_hash(candidate_matrix)
        candidate_fingerprint = hashlib.sha256(
            (
                f"{artifact.universe_version}:{development_data_hash}:"
                f"{recipe.version}:{candidate_matrix_hash}:{EVALUATION_PIPELINE_VERSION}"
            ).encode("ascii")
        ).hexdigest()
        matrix_path = run_directory / "candidate-matrix.json"
        if matrix_path.exists():
            if matrix_path.read_text(encoding="utf-8") != candidate_matrix_payload:
                raise RuntimeError("candidate_matrix_resume_mismatch")
        else:
            matrix_path.write_text(candidate_matrix_payload, encoding="utf-8")
        tasks: list[dict[str, Any]] = []
        classifiers: dict[tuple[str, int], dict[str, Any]] = {}
        quantile_models: dict[tuple[str, int], dict[str, Any]] = {}
        survival_models: dict[int, dict[str, Any]] = {}
        package: dict[str, Any] = {
            "universe_version": artifact.universe_version,
            "features": _FEATURES,
            "candidate_recipe_version": recipe.version,
            "candidate_generation": recipe.generation,
            "evaluation_pipeline_version": EVALUATION_PIPELINE_VERSION,
            "classifiers": classifiers,
            "quantiles": quantile_models,
            "survival": survival_models,
            "feature_distribution": {
                "mean": development_frame[list(_FEATURES)].mean().fillna(0.0).to_dict(),
                "std": (
                    development_frame[list(_FEATURES)]
                    .std()
                    .replace(0, 1)
                    .fillna(1.0)
                    .to_dict()
                ),
            },
        }
        completed_tasks = 0
        _progress(
            progress_callback,
            "candidate_search",
            11,
            "候选搜索 0/17 · 正在训练第一个方向任务",
        )
        for horizon in (1, 5, 20):
            for target_prefix, task_name in (
                ("direction", "forecast_direction"),
                ("breakout_direction", "breakout"),
            ):
                result, model, temperature = self._cached(
                    f"main-task-{task_name}-{horizon}",
                    lambda: self._classification_task(
                        development_frame,
                        horizon,
                        target_prefix=target_prefix,
                        task_name=task_name,
                        recipe_version=recipe.version,
                        oos_directory=run_directory / "oos",
                    ),
                )
                tasks.append(result)
                completed_tasks += 1
                _progress(
                    progress_callback,
                    "candidate_search",
                    10 + round(55 * completed_tasks / 17),
                    f"候选搜索 {completed_tasks}/17 · {task_name} {horizon}日",
                )
                if model is not None:
                    classifiers[(task_name, horizon)] = {
                        "model": model,
                        "temperature": temperature,
                    }
            for outcome in ("return_fwd", "mfe", "mae"):
                result, models = self._cached(
                    f"main-task-{outcome}-{horizon}",
                    lambda: self._quantile_task(
                        development_frame,
                        horizon,
                        outcome,
                        recipe_version=recipe.version,
                        oos_directory=run_directory / "oos",
                    ),
                )
                tasks.append(result)
                completed_tasks += 1
                _progress(
                    progress_callback,
                    "candidate_search",
                    10 + round(55 * completed_tasks / 17),
                    f"候选搜索 {completed_tasks}/17 · {outcome} {horizon}日",
                )
                if models:
                    quantile_models[(outcome, horizon)] = models
        for horizon in (5, 20):
            result, survival_model = self._cached(
                f"main-task-range-exit-{horizon}",
                lambda: self._survival_task(
                    development_frame,
                    horizon,
                    recipe_version=recipe.version,
                    oos_directory=run_directory / "oos",
                ),
            )
            tasks.append(result)
            completed_tasks += 1
            _progress(
                progress_callback,
                "candidate_search",
                10 + round(55 * completed_tasks / 17),
                f"候选搜索 {completed_tasks}/17 · range_exit {horizon}日",
            )
            if survival_model is not None:
                survival_models[horizon] = survival_model
        production_gate_complete = all(int(row["fold_count"]) > 0 for row in tasks)
        development_all_gates_passed = (
            data_quality_passed
            and production_gate_complete
            and all(bool(row["gate_passed"]) for row in tasks)
        )
        all_gates_passed = development_all_gates_passed
        _progress(
            progress_callback,
            "oos_validation",
            70,
            f"开发窗口完成：{sum(bool(row['gate_passed']) for row in tasks)}/17 项通过",
        )
        _progress(
            progress_callback,
            "promotion_window",
            82,
            (
                "开发门禁 17/17，正在冻结候选；独立晋升窗口未读取"
                if development_all_gates_passed
                else "开发门禁未达到 17/17；独立晋升窗口未读取"
            ),
        )
        execution_gate = _execution_gate_summary(tasks, require_promotion=False)
        execution_ready = False
        package["gate_metrics"] = tasks
        model_version = (
            f"live-model-{datetime.now(UTC):%Y%m%d%H%M%S}"
            f"-r{recipe.generation}-{artifact.input_hash[:8]}"
        )
        calibration_version = f"live-cal-{artifact.input_hash[:12]}"
        package.update(
            {
                "model_version": model_version,
                "calibration_version": calibration_version,
                "dataset_revision": artifact.dataset_revision,
                "feature_set_version": artifact.feature_set_version,
                "universe_version": artifact.universe_version,
                "input_hash": artifact.input_hash,
                "development_data_hash": development_data_hash,
                "development_data_cutoff": _DEVELOPMENT_CUTOFF.isoformat(),
                "trained_at": datetime.now(UTC),
                "candidate_recipe_version": recipe.version,
                "candidate_generation": recipe.generation,
                "evaluation_pipeline_version": EVALUATION_PIPELINE_VERSION,
            }
        )
        package_path = run_directory / "production-package.pkl"
        _atomic_pickle(package_path, package)
        registry = LocalModelRegistry(self.paths.models / "registry")
        before = registry.snapshot("forecast_bundle", 20)
        release = registry.register_release(
            model_version=model_version,
            task="forecast_bundle",
            horizon=20,
            calibration_version=calibration_version,
            source_decision_id=run_id,
            promotion_eligible=False,
            artifacts={"production-package.pkl": package_path},
            registered_at=datetime.now(UTC),
        )
        automatic_promotion = (
            "candidate_frozen" if development_all_gates_passed else "development_failed"
        )
        registry.mark_challenger(
            model_version,
            reason=(
                "development candidate frozen for future blind promotion"
                if development_all_gates_passed
                else "candidate retained for research after development gate failure"
            ),
            recorded_at=datetime.now(UTC),
        )
        component_directory = run_directory / "components"
        component_directory.mkdir()
        component_releases: list[dict[str, Any]] = []
        for task_result in tasks:
            task_name = str(task_result["task"])
            horizon = int(task_result["horizon"])
            component_payload = _component_package(
                package,
                task=task_name,
                horizon=horizon,
            )
            if component_payload is None:
                continue
            component_version = (
                f"{model_version}-{_component_abbreviation(task_name)}{horizon}"
            )
            component_payload.update(
                {
                    "model_version": component_version,
                    "bundle_model_version": model_version,
                    "calibration_version": calibration_version,
                    "gate_metrics": task_result,
                }
            )
            component_path = component_directory / f"{task_name}-{horizon}-package.pkl"
            _atomic_pickle(component_path, component_payload)
            before_component = registry.snapshot(task_name, horizon)
            component_release = registry.register_release(
                model_version=component_version,
                task=task_name,
                horizon=horizon,
                calibration_version=calibration_version,
                source_decision_id=run_id,
                promotion_eligible=False,
                artifacts={"component-package.pkl": component_path},
                registered_at=datetime.now(UTC),
            )
            registry.mark_challenger(
                component_version,
                reason=(
                    "development component retained without replacing verified research"
                ),
                recorded_at=datetime.now(UTC),
            )
            lifecycle_status = "challenger"
            component_releases.append(
                {
                    "task": task_name,
                    "horizon": horizon,
                    "model_version": component_version,
                    "calibration_version": calibration_version,
                    "gate_passed": bool(task_result["gate_passed"]),
                    "lifecycle_status": lifecycle_status,
                    "previous_champion_version": before_component.champion_version,
                    "release_hash": component_release.manifest_hash,
                }
            )
        report: dict[str, Any] = {
            "run_id": run_id,
            "status": "evaluated",
            "dataset_revision": artifact.dataset_revision,
            "feature_set_version": artifact.feature_set_version,
            "universe_version": artifact.universe_version,
            "feature_input_hash": artifact.input_hash,
            "development_data_cutoff": _DEVELOPMENT_CUTOFF.isoformat(),
            "development_data_hash": development_data_hash,
            "candidate_fingerprint": candidate_fingerprint,
            "observation_policy": "latest_revision_retrospective",
            "strict_historical_pit": False,
            "candidate_recipe_version": recipe.version,
            "candidate_generation": recipe.generation,
            "evaluation_pipeline_version": EVALUATION_PIPELINE_VERSION,
            "maximum_label_date": max(development_frame["session_date"]).isoformat(),
            "random_seed": _RANDOM_SEED,
            "label_version": "live-labels-v2",
            "generated_at": datetime.now(UTC).isoformat(),
            "split_policy": {
                "minimum_train_years": 5,
                "validation_trading_days": 252,
                "outer_test_trading_days": 126,
                "roll_trading_days": 126,
                "purge_embargo_trading_days": 20,
                "random_split": False,
                "selection_sessions": 116,
                "selection_calibration_embargo_sessions": 20,
                "calibration_sessions": 116,
            },
            "candidate_matrix_hash": candidate_matrix_hash,
            "candidate_matrix": candidate_matrix,
            "tasks": tasks,
            "component_releases": component_releases,
            "candidate_release_hash": release.manifest_hash,
            "model_version": model_version,
            "calibration_version": calibration_version,
            "previous_champion_version": before.champion_version,
            "champion_promoted": False,
            "automatic_promotion": automatic_promotion,
            "production_gate_complete": production_gate_complete,
            "development_all_gates_passed": development_all_gates_passed,
            "all_gates_passed": all_gates_passed,
            "formal_all_gates_passed": False,
            "data_quality_status": "passed" if data_quality_passed else "blocked",
            "data_quality_summary": {
                "quarantined_rows": quarantined_rows,
                "unexplained_return_limit": 0.50,
            },
            "label_health_status": (
                "passed"
                if all(bool(row.get("label_health_passed")) for row in tasks)
                else "blocked"
            ),
            "promotion_window_status": "not_evaluated",
            "promotion_window": {
                "window_id": NEXT_PROMOTION_WINDOW.window_id,
                "start": _PROMOTION_START.isoformat(),
                "end": _PROMOTION_END.isoformat(),
                "label_complete_through": (
                    NEXT_PROMOTION_WINDOW.label_complete_through.isoformat()
                ),
                "automatic_training_at": (
                    NEXT_PROMOTION_WINDOW.automatic_training_at.isoformat()
                ),
                "calendar_source": NEXT_PROMOTION_WINDOW.calendar_source,
                "candidate_config_version": (
                    NEXT_PROMOTION_WINDOW.candidate_config_version
                ),
                "session_count": 0,
                "required_session_count": _EXPECTED_PROMOTION_SESSIONS,
                "fully_labeled_session_count": 0,
                "row_count": 0,
                "one_time": True,
                "already_consumed": False,
                "candidate_search_access": "forbidden",
                "status": "not_evaluated",
            },
            "execution_readiness": "ready" if execution_ready else "blocked",
            "execution_gate": {
                **execution_gate,
                "evaluated": False,
                "ready": execution_ready,
            },
            "candidate_lock_status": (
                "created"
                if development_all_gates_passed
                else "not_created_development_incomplete"
            ),
            "failure_codes": sorted(
                {str(code) for row in tasks for code in row.get("failure_codes", [])}
                | (
                    {"unexplained_corporate_action_return"}
                    if not data_quality_passed
                    else set()
                )
            ),
            "incomplete_gate_reasons": (
                []
                if production_gate_complete
                else ["one_or_more_tasks_have_no_valid_walk_forward_fold"]
            ),
        }
        report_path = run_directory / "training-report.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        write_latest_training_pointer(self.paths, report_path, run_id)
        if development_all_gates_passed:
            frozen_candidate = create_candidate_lock(
                self.paths,
                report=report,
                report_path=report_path,
                package_path=package_path,
                candidate_matrix_path=run_directory / "candidate-matrix.json",
            )
            report["frozen_candidate_version"] = frozen_candidate[
                "candidate_model_version"
            ]
        _progress(
            progress_callback,
            "execution_gate",
            94,
            "开发交易评估已记录；正式交易门禁等待独立盲测",
        )
        return report

    def _cached(self, key: str, operation: Callable[[], Any]) -> Any:
        return operation() if self.cache is None else self.cache.run(key, operation)

    def _classification_task(
        self,
        frame: pd.DataFrame,
        horizon: int,
        *,
        target_prefix: str,
        task_name: str,
        recipe_version: str,
        oos_directory: Path | None = None,
    ) -> tuple[dict[str, Any], Any | None, float | None]:
        target = f"{target_prefix}_{horizon}"
        # LightGBM handles missing feature values natively.  In particular, the
        # two-year 60-minute auxiliary history must not truncate the five-year
        # daily walk-forward window.
        usable = frame.dropna(subset=[target]).copy()
        development = usable[usable["session_date"] <= _DEVELOPMENT_CUTOFF].copy()
        dates = sorted(set(development["session_date"]))
        folds = _walk_forward_folds(dates)
        label_health = _classification_label_health(
            development,
            folds,
            target,
        )
        fold_results: list[dict[str, Any]] = []
        last_model: Any | None = None
        last_temperature: float | None = None
        for index, (train_dates, validation_dates, test_dates) in enumerate(folds, 1):
            train = development[development["session_date"].isin(train_dates)]
            validation = development[development["session_date"].isin(validation_dates)]
            selection, validation = split_validation_selection_calibration(validation)
            test = development[development["session_date"].isin(test_dates)]
            if min(len(train), len(validation), len(test)) == 0:
                continue
            labels = (-1, 0, 1)
            if set(train[target].astype(int).unique()) != set(labels):
                continue
            model, candidate_id = _select_classifier_candidate(
                train,
                selection,
                target,
                task_name=task_name,
                recipe_version=recipe_version,
                **_cache_arguments(
                    self.cache,
                    f"main-candidate-{task_name}-{horizon}-fold-{index}",
                ),
            )
            validation_probability = model.predict_proba(validation[list(_FEATURES)])
            temperature = _fit_temperature(
                validation_probability,
                validation[target].astype(int).to_numpy(),
                model.classes_,
            )
            probability = _temperature_scale(
                model.predict_proba(test[list(_FEATURES)]), temperature
            )
            y = test[target].astype(int).to_numpy()
            candidate_brier = _multiclass_brier(y, probability, model.classes_)
            frequencies = np.array(
                [
                    (train[target].astype(int) == label).mean()
                    for label in model.classes_
                ]
            )
            frequency_probability = np.tile(frequencies, (len(test), 1))
            frequency_brier = _multiclass_brier(
                y, frequency_probability, model.classes_
            )
            # The linear baseline cannot consume NaN.  Fit imputation statistics
            # on the training window only so the baseline remains point-in-time.
            imputation = train[list(_FEATURES)].median().fillna(0.0)
            logistic = LogisticRegression(max_iter=500, random_state=_RANDOM_SEED)
            logistic.fit(
                train[list(_FEATURES)].fillna(imputation),
                train[target].astype(int),
            )
            logistic_probability = logistic.predict_proba(
                test[list(_FEATURES)].fillna(imputation)
            )
            logistic_brier = _multiclass_brier(
                y, logistic_probability, logistic.classes_
            )
            execution_metrics = _execution_cost_metrics(
                test,
                probability,
                model.classes_,
                horizon,
            )
            fold_results.append(
                {
                    "fold_id": f"fold-{index}",
                    "train_start": min(train_dates).isoformat(),
                    "train_end": max(train_dates).isoformat(),
                    "validation_start": min(validation_dates).isoformat(),
                    "validation_end": max(validation_dates).isoformat(),
                    **_partition_evidence(selection, validation),
                    "test_start": min(test_dates).isoformat(),
                    "test_end": max(test_dates).isoformat(),
                    "sample_count": len(test),
                    "candidate_brier": candidate_brier,
                    "frequency_brier": frequency_brier,
                    "logistic_brier": logistic_brier,
                    "brier_skill_vs_frequency": 1.0 - candidate_brier / frequency_brier,
                    "beat_both_baselines": candidate_brier
                    < min(frequency_brier, logistic_brier),
                    "net_long_cash_return": _net_long_cash_return(
                        test,
                        probability,
                        model.classes_,
                        horizon,
                    ),
                    "execution_cost_sensitivity": execution_metrics,
                    "temperature": temperature,
                    "calibration_error": _multiclass_calibration_error(
                        y,
                        probability,
                        model.classes_,
                    ),
                    "selected_candidate": candidate_id,
                    "accuracy": float(np.mean(model.classes_[probability.argmax(axis=1)] == y)),
                    "oos_evidence": _write_fold_evidence(
                        oos_directory, frame, test, task_name, horizon, index,
                        {"actual": y, **{
                            f"{name}_p_{label}": matrix[:, column]
                            for name, matrix, classes in (
                                ("candidate", probability, model.classes_),
                                ("frequency", frequency_probability, model.classes_),
                                ("logistic", logistic_probability, logistic.classes_),
                            )
                            for column, label in enumerate(classes)
                        }},
                    ),
                }
            )
            last_model = model
            last_temperature = temperature
        wins = sum(bool(row["beat_both_baselines"]) for row in fold_results)
        positive_economic_windows = sum(
            float(row["net_long_cash_return"]) > 0 for row in fold_results
        )
        selected_candidate = (
            Counter(str(row["selected_candidate"]) for row in fold_results).most_common(
                1
            )[0][0]
            if fold_results
            else None
        )
        statistical_gate = len(fold_results) == 4 and wins >= 3
        calibration_gate = (
            len(fold_results) == 4
            and sum(float(row["calibration_error"]) <= 0.05 for row in fold_results)
            >= 3
        )
        stability_gate = len(fold_results) == 4 and label_health["passed"]
        gate_passed = statistical_gate and calibration_gate and stability_gate
        execution_gate = _task_execution_gate(fold_results, require_promotion=False)
        failure_codes = _gate_failure_codes(
            statistical_gate=statistical_gate,
            calibration_gate=calibration_gate,
            stability_gate=stability_gate,
            promotion_gate=None,
            label_health=label_health,
        )
        deployment_model, deployment_temperature = _fit_deployment_classifier(
            development,
            target,
            selected_candidate=selected_candidate,
            recipe_version=recipe_version,
            cache=self.cache,
            cache_prefix=f"main-deployment-{task_name}-{horizon}",
        )
        return (
            {
                "task": task_name,
                "horizon": horizon,
                "fold_count": len(fold_results),
                "baseline_wins": wins,
                "positive_cost_windows": positive_economic_windows,
                "gate_passed": gate_passed,
                "statistical_gate_passed": statistical_gate,
                "calibration_gate_passed": calibration_gate,
                "stability_gate_passed": stability_gate,
                "promotion_window_passed": None,
                "execution_gate_passed": execution_gate,
                "label_health_passed": label_health["passed"],
                "label_health": label_health,
                "promotion_window": {"status": "not_evaluated"},
                "failure_codes": failure_codes,
                "candidate_count": _classification_candidate_count(
                    task_name,
                    recipe_version,
                ),
                "selected_candidate": selected_candidate,
                "folds": fold_results,
            },
            deployment_model or last_model,
            (
                deployment_temperature
                if deployment_temperature is not None
                else last_temperature
            ),
        )

    def _quantile_task(
        self,
        frame: pd.DataFrame,
        horizon: int,
        outcome: str,
        *,
        recipe_version: str,
        oos_directory: Path | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        target = f"{outcome}_{horizon}"
        usable = frame.dropna(subset=[target]).copy()
        development = usable[usable["session_date"] <= _DEVELOPMENT_CUTOFF].copy()
        dates = sorted(set(development["session_date"]))
        folds = _walk_forward_folds(dates)
        quantiles = (0.10, 0.25, 0.50, 0.75, 0.90)
        results: list[dict[str, Any]] = []
        last_models: dict[float, LGBMRegressor] = {}
        last_adjustment = 0.0
        last_adjustments: dict[str, float] = {}
        last_lower_adjustments: dict[str, float] = {}
        last_upper_adjustments: dict[str, float] = {}
        for index, (train_dates, validation_dates, test_dates) in enumerate(folds, 1):
            train = development[development["session_date"].isin(train_dates)]
            validation = development[development["session_date"].isin(validation_dates)]
            selection, validation = split_validation_selection_calibration(validation)
            test = development[development["session_date"].isin(test_dates)]
            if min(len(train), len(validation), len(test)) == 0:
                continue
            models: dict[float, LGBMRegressor] = {}
            validation_predictions: dict[float, np.ndarray] = {}
            test_predictions: dict[float, np.ndarray] = {}
            selected_config, candidate_id = _select_quantile_candidate(
                train,
                selection,
                target,
                **_cache_arguments(
                    self.cache,
                    f"main-candidate-{outcome}-{horizon}-fold-{index}",
                ),
            )
            for alpha in quantiles:
                model = LGBMRegressor(
                    objective="quantile",
                    alpha=alpha,
                    **selected_config,
                    verbosity=-1,
                )
                model.fit(train[list(_FEATURES)], train[target].astype(float))
                models[alpha] = model
                validation_predictions[alpha] = np.asarray(
                    model.predict(validation[list(_FEATURES)]), dtype=float
                )
                test_predictions[alpha] = np.asarray(
                    model.predict(test[list(_FEATURES)]), dtype=float
                )
            validation_predictions = _ordered_predictions(validation_predictions, outcome)
            test_predictions = _ordered_predictions(test_predictions, outcome)
            validation_y = validation[target].astype(float).to_numpy()
            nonconformity = np.maximum.reduce((
                validation_predictions[0.10] - validation_y,
                validation_y - validation_predictions[0.90],
                np.zeros(len(validation_y)),
            ))
            adjustments = _group_cqr_adjustments(validation, nonconformity)
            lower_adjustments = upper_adjustments = adjustments
            lower_test_adjustment = _cqr_adjustment_vector(test, adjustments)
            upper_test_adjustment = lower_test_adjustment
            adjustment = float(adjustments["pooled"])
            test_y = test[target].astype(float).to_numpy()
            lower = test_predictions[0.10] - lower_test_adjustment
            upper = test_predictions[0.90] + upper_test_adjustment
            coverage = float(np.mean((test_y >= lower) & (test_y <= upper)))
            width = float(np.mean(upper - lower))
            candidate_pinball = float(
                np.mean(
                    [
                        mean_pinball_loss(test_y, test_predictions[alpha], alpha=alpha)
                        for alpha in quantiles
                    ]
                )
            )
            baselines = {
                alpha: np.full(len(test), train[target].quantile(alpha), dtype=float)
                for alpha in quantiles
            }
            baseline_pinball = float(
                np.mean(
                    [
                        mean_pinball_loss(test_y, baselines[alpha], alpha=alpha)
                        for alpha in quantiles
                    ]
                )
            )
            scaled_baselines = _volatility_quantile_baseline(train, test, target, quantiles)
            scaled_pinball = float(np.mean([
                mean_pinball_loss(test_y, scaled_baselines[alpha], alpha=alpha)
                for alpha in quantiles
            ]))
            results.append(
                {
                    "fold_id": f"fold-{index}",
                    **_partition_evidence(selection, validation),
                    "test_start": min(test_dates).isoformat(),
                    "test_end": max(test_dates).isoformat(),
                    "sample_count": len(test),
                    "volatility_baseline_pinball": scaled_pinball,
                    "oos_evidence": _write_fold_evidence(
                        oos_directory, frame, test, outcome, horizon, index,
                        {"actual": test_y, "calibrated_lower": lower,
                         "calibrated_upper": upper, **{
                            f"{name}_q{round(alpha * 100)}": values
                            for name, predictions in (
                                ("candidate", test_predictions),
                                ("historical", baselines),
                                ("volatility", scaled_baselines),
                            )
                            for alpha, values in predictions.items()
                        }},
                    ),
                    "beat_volatility_baseline": candidate_pinball < scaled_pinball,
                    "candidate_pinball": candidate_pinball,
                    "baseline_pinball": baseline_pinball,
                    "beat_baseline": candidate_pinball < baseline_pinball,
                    "cqr_coverage": coverage,
                    "cqr_average_width": width,
                    "cqr_adjustments": adjustments,
                    "cqr_lower_adjustments": lower_adjustments,
                    "cqr_upper_adjustments": upper_adjustments,
                    "cqr_calibration_strategy": "asset_type_symmetric_finite_sample",
                    "tail_failure_rate": float(
                        np.mean((test_y < lower) | (test_y > upper))
                    ),
                    "selected_candidate": candidate_id,
                }
            )
            last_models = models
            last_adjustment = adjustment
            last_adjustments = adjustments
            last_lower_adjustments = lower_adjustments
            last_upper_adjustments = upper_adjustments
        wins = sum(bool(row["beat_baseline"]) for row in results)
        coverage_ok = sum(0.75 <= float(row["cqr_coverage"]) <= 0.85 for row in results)
        statistical_gate = len(results) == 4 and wins >= 3
        calibration_gate = len(results) == 4 and coverage_ok >= 3
        stability_gate = len(results) == 4
        gate_passed = statistical_gate and calibration_gate and stability_gate
        failure_codes = _gate_failure_codes(
            statistical_gate=statistical_gate,
            calibration_gate=calibration_gate,
            stability_gate=stability_gate,
            promotion_gate=None,
        )
        deployment_package = _fit_deployment_quantiles(
            development,
            target,
            quantiles,
            recipe_version=recipe_version,
            cache=self.cache,
            cache_prefix=f"main-deployment-{outcome}-{horizon}",
        )
        package = (
            deployment_package
            or {
                "evaluation_pipeline_version": EVALUATION_PIPELINE_VERSION,
                "outcome": outcome,
                "models": last_models,
                "cqr_adjustment": last_adjustment,
                "cqr_adjustments": last_adjustments,
                "cqr_lower_adjustments": last_lower_adjustments,
                "cqr_upper_adjustments": last_upper_adjustments,
                "quantiles": quantiles,
            }
            if last_models
            else {}
        )
        return (
            {
                "task": outcome,
                "horizon": horizon,
                "fold_count": len(results),
                "pinball_wins": wins,
                "volatility_baseline_wins": sum(bool(row["beat_volatility_baseline"]) for row in results),
                "volatility_baseline_gate_role": "diagnostic_only_original_gate_unchanged",
                "coverage_windows_passed": coverage_ok,
                "gate_passed": gate_passed,
                "statistical_gate_passed": statistical_gate,
                "calibration_gate_passed": calibration_gate,
                "stability_gate_passed": stability_gate,
                "promotion_window_passed": None,
                "execution_gate_passed": False,
                "label_health_passed": True,
                "promotion_window": {"status": "not_evaluated"},
                "failure_codes": failure_codes,
                "candidate_count": _OTHER_CANDIDATES,
                "folds": results,
            },
            package,
        )

    def _survival_task(
        self,
        frame: pd.DataFrame,
        horizon: int,
        *,
        recipe_version: str,
        oos_directory: Path | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        event_target = f"range_exit_event_{horizon}"
        day_target = f"range_exit_day_{horizon}"
        usable = frame.dropna(subset=[event_target, day_target]).copy()
        development = usable[usable["session_date"] <= _DEVELOPMENT_CUTOFF].copy()
        dates = sorted(set(development["session_date"]))
        folds = _walk_forward_folds(dates)
        label_health = _survival_label_health(
            development,
            folds,
            event_target,
        )
        results: list[dict[str, Any]] = []
        last_model: LGBMClassifier | None = None
        last_calibrator: Any | None = None
        for index, (train_dates, validation_dates, test_dates) in enumerate(folds, 1):
            train = development[development["session_date"].isin(train_dates)]
            validation = development[development["session_date"].isin(validation_dates)]
            selection, validation = split_validation_selection_calibration(validation)
            test = development[development["session_date"].isin(test_dates)]
            if min(len(train), len(validation), len(test)) == 0:
                continue
            person_period = _person_period_rows(train, horizon)
            if person_period["event"].nunique() < 2:
                continue
            feature_names = [*_FEATURES, "elapsed_day"]
            model, candidate_id = _select_survival_candidate(
                person_period,
                selection,
                horizon,
                **_cache_arguments(
                    self.cache,
                    f"main-candidate-range-exit-{horizon}-fold-{index}",
                ),
            )
            calibrator = _fit_survival_calibrator(
                model,
                validation,
                horizon,
                feature_names,
                compare_variants=(
                    uses_expanded_direction_search(recipe_version) and horizon == 20
                ),
            )
            cumulative = _calibrated_cumulative_exit_probability(
                model,
                test,
                horizon,
                feature_names,
                calibrator,
            )
            observed = test[event_target].astype(int).to_numpy()
            brier = float(np.mean((cumulative - observed) ** 2))
            null_probability = float(train[event_target].mean())
            null_brier = float(np.mean((null_probability - observed) ** 2))
            c_index = (
                float(roc_auc_score(observed, cumulative))
                if len(set(observed)) == 2
                else 0.5
            )
            results.append(
                {
                    "fold_id": f"fold-{index}",
                    **_partition_evidence(selection, validation),
                    "test_start": min(test_dates).isoformat(),
                    "test_end": max(test_dates).isoformat(),
                    "sample_count": len(test),
                    "time_dependent_brier": brier,
                    "oos_evidence": _write_fold_evidence(
                        oos_directory, frame, test, "range_exit", horizon, index,
                        {"actual": observed, "candidate_probability": cumulative,
                         "frequency_probability": np.full(len(test), null_probability),
                         "observed_exit_day": test[day_target].to_numpy()},
                    ),
                    "null_brier": null_brier,
                    "c_index": c_index,
                    "cumulative_probability_calibration_error": abs(
                        float(cumulative.mean()) - float(observed.mean())
                    ),
                    "beat_baseline": brier < null_brier and c_index > 0.5,
                    "selected_candidate": candidate_id,
                    "calibration_method": getattr(
                        calibrator,
                        "method",
                        "isotonic" if calibrator is not None else "raw",
                    ),
                }
            )
            last_model = model
            last_calibrator = calibrator
        wins = sum(bool(row["beat_baseline"]) for row in results)
        statistical_gate = (
            len(results) == 4
            and wins >= 3
            and sum(float(row["c_index"]) > 0.5 for row in results) >= 3
        )
        calibration_gate = (
            len(results) == 4
            and sum(
                float(row["cumulative_probability_calibration_error"]) <= 0.05
                for row in results
            )
            >= 3
        )
        stability_gate = len(results) == 4 and label_health["passed"]
        gate_passed = statistical_gate and calibration_gate and stability_gate
        failure_codes = _gate_failure_codes(
            statistical_gate=statistical_gate,
            calibration_gate=calibration_gate,
            stability_gate=stability_gate,
            promotion_gate=None,
            label_health=label_health,
        )
        deployment_package = _fit_deployment_survival(
            development,
            horizon,
            recipe_version=recipe_version,
            cache=self.cache,
            cache_prefix=f"main-deployment-range-exit-{horizon}",
        )
        return (
            {
                "task": "range_exit",
                "horizon": horizon,
                "fold_count": len(results),
                "baseline_wins": wins,
                "gate_passed": gate_passed,
                "statistical_gate_passed": statistical_gate,
                "calibration_gate_passed": calibration_gate,
                "stability_gate_passed": stability_gate,
                "promotion_window_passed": None,
                "execution_gate_passed": False,
                "label_health_passed": label_health["passed"],
                "label_health": label_health,
                "promotion_window": {"status": "not_evaluated"},
                "failure_codes": failure_codes,
                "candidate_count": _OTHER_CANDIDATES,
                "folds": results,
            },
            deployment_package
            or (
                {
                    "model": last_model,
                    "calibrator": last_calibrator,
                    "feature_names": (*_FEATURES, "elapsed_day"),
                    "horizon": horizon,
                }
                if last_model is not None
                else None
            ),
        )


def _classifier_configs(
    count: int = _CLASSIFICATION_CANDIDATES,
) -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    for leaves in (15, 31, 63, 95):
        for learning_rate in (0.025, 0.04):
            for min_child_samples in (75, 150):
                configs.append(
                    {
                        "n_estimators": 110 if learning_rate < 0.03 else 80,
                        "learning_rate": learning_rate,
                        "num_leaves": leaves,
                        "min_child_samples": min_child_samples,
                        "subsample": 0.85,
                        "colsample_bytree": 0.85,
                        "reg_lambda": 1.0 if leaves <= 31 else 2.0,
                        "max_bin": 127,
                        "n_jobs": 4,
                        "random_state": _RANDOM_SEED,
                    }
                )
    if count > len(configs):
        for leaves, min_child_samples in (
            (15, 50),
            (31, 50),
            (63, 50),
            (95, 50),
            (15, 225),
            (31, 225),
            (63, 225),
            (95, 225),
        ):
            configs.append(
                {
                    "n_estimators": 95,
                    "learning_rate": 0.03,
                    "num_leaves": leaves,
                    "min_child_samples": min_child_samples,
                    "subsample": 0.85,
                    "colsample_bytree": 0.85,
                    "reg_lambda": 2.0,
                    "max_bin": 127,
                    "n_jobs": 4,
                    "random_state": _RANDOM_SEED,
                }
            )
    return configs[:count]


def _other_configs(count: int = _OTHER_CANDIDATES) -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    for leaves in (15, 31, 63):
        for learning_rate in (0.025, 0.04):
            for min_child_samples in (75, 150):
                configs.append(
                    {
                        "n_estimators": 100 if learning_rate < 0.03 else 75,
                        "learning_rate": learning_rate,
                        "num_leaves": leaves,
                        "min_child_samples": min_child_samples,
                        "subsample": 0.85,
                        "colsample_bytree": 0.85,
                        "reg_lambda": 1.0 if leaves <= 31 else 2.0,
                        "max_bin": 127,
                        "n_jobs": 4,
                        "random_state": _RANDOM_SEED,
                    }
                )
    return configs[:count]


def _registered_candidate_matrix(
    recipe_version: str = CANDIDATE_RECIPE_V1,
) -> dict[str, Any]:
    recipe = candidate_recipe(recipe_version)
    tasks: list[dict[str, Any]] = []
    for horizon in (1, 5, 20):
        direction_count = _classification_candidate_count(
            "forecast_direction",
            recipe.version,
        )
        direction_families = {
            "class_balanced_lgbm": 4,
            "market_state_fusion": 2,
            "elastic_net_multiclass": 4,
            "threshold_decomposition": 6,
        }
        if uses_expanded_direction_search(recipe.version):
            direction_families.update(
                {
                    "unweighted_regularized_multiclass": 2,
                    "linear_nonlinear_probability_blend": 6,
                }
            )
        tasks.extend(
            (
                {
                    "task": "forecast_direction",
                    "horizon": horizon,
                    "candidate_count": direction_count,
                    "families": direction_families,
                },
                {
                    "task": "breakout",
                    "horizon": horizon,
                    "candidate_count": _CLASSIFICATION_CANDIDATES,
                    "families": {"first_touch_lgbm_multiclass": 16},
                },
            )
        )
        for task in ("return_fwd", "mfe", "mae"):
            tasks.append(
                {
                    "task": task,
                    "horizon": horizon,
                    "candidate_count": _OTHER_CANDIDATES,
                    "families": {"historical_quantile_and_lgbm": 12},
                }
            )
    for horizon in (5, 20):
        tasks.append(
            {
                "task": "range_exit",
                "horizon": horizon,
                "candidate_count": _OTHER_CANDIDATES,
                "families": {"discrete_time_hazard": 12},
            }
        )
    return {
        "candidate_recipe_version": recipe.version,
        "candidate_generation": recipe.generation,
        "evaluation_pipeline_version": EVALUATION_PIPELINE_VERSION,
        "registered_at_start": True,
        "random_seed": _RANDOM_SEED,
        "maximum_parallel_trials": _MAX_PARALLEL_TRIALS,
        "threads_per_trial": 4,
        "tasks": tasks,
    }


def _select_classifier_candidate(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    target: str,
    *,
    task_name: str,
    recipe_version: str = CANDIDATE_RECIPE_V1,
    cache: Any | None = None,
    cache_prefix: str = "classifier",
) -> tuple[Any, str]:
    best_model: Any | None = None
    best_score = float("inf")
    best_id = ""
    labels = train[target].astype(int).to_numpy()
    validation_labels = validation[target].astype(int).to_numpy()
    candidate_count = _classification_candidate_count(task_name, recipe_version)
    configs = _classifier_configs(candidate_count)

    def fit_candidate(
        item: tuple[int, dict[str, Any]],
    ) -> tuple[Any, str, float]:
        index, config = item
        model: Any
        if task_name == "forecast_direction" and 1 <= index <= 4:
            model = LGBMClassifier(
                objective="multiclass",
                class_weight="balanced",
                **config,
                verbosity=-1,
            )
            family = "class-balanced-lgbm"
        elif task_name == "forecast_direction" and 5 <= index <= 6:
            model = MarketStateFusionClassifier(config)
            family = "market-state-fusion"
        elif task_name == "forecast_direction" and 7 <= index <= 10:
            elastic_index = index - 7
            model = ElasticNetDirectionClassifier(
                alpha=(0.00005, 0.0001, 0.0005, 0.001)[elastic_index],
                l1_ratio=(0.2, 0.4, 0.6, 0.8)[elastic_index],
            )
            family = "elastic-net-multiclass"
        elif task_name == "forecast_direction" and index <= 16:
            decomposed_config = {**config, "class_weight": "balanced"}
            model = DecomposedDirectionClassifier(decomposed_config)
            family = "threshold-decomposition"
        elif task_name == "forecast_direction" and index <= 18:
            model = ElasticNetDirectionClassifier(
                alpha=(0.0001, 0.0005)[index - 17],
                l1_ratio=(0.2, 0.6)[index - 17],
                class_weight=None,
            )
            family = "unweighted-regularized-multiclass"
        elif task_name == "forecast_direction":
            blend_weights = (0.20, 0.35, 0.50, 0.65, 0.80, 0.90)
            model = ProbabilityBlendDirectionClassifier(
                config,
                nonlinear_weight=blend_weights[index - 19],
            )
            family = "linear-nonlinear-probability-blend"
        else:
            breakout_config = (
                {**config, "class_weight": "balanced"} if index > 8 else config
            )
            model = LGBMClassifier(
                objective="multiclass",
                **breakout_config,
                verbosity=-1,
            )
            family = "first-touch-lgbm"
        model.fit(train[list(_FEATURES)], labels)
        probability = model.predict_proba(validation[list(_FEATURES)])
        score = _multiclass_brier(
            validation_labels,
            probability,
            model.classes_,
        )
        return model, f"{family}-{index:02d}", score

    with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_TRIALS) as executor:
        fitted = executor.map(
            lambda item: _run_cached(
                cache,
                f"{cache_prefix}-candidate-{item[0]:02d}",
                lambda: fit_candidate(item),
            ),
            enumerate(configs, 1),
        )
        for model, candidate_id, score in fitted:
            if score < best_score:
                best_score = score
                best_model = model
                best_id = candidate_id
    if best_model is None:
        raise RuntimeError("classification_candidate_search_failed")
    return best_model, best_id


def _fit_classifier_candidate_id(
    train: pd.DataFrame,
    target: str,
    candidate_id: str,
    *,
    recipe_version: str = CANDIDATE_RECIPE_V1,
) -> Any:
    try:
        index = int(candidate_id.rsplit("-", maxsplit=1)[-1])
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_classifier_candidate_id") from error
    configs = _classifier_configs(
        _classification_candidate_count(
            (
                "forecast_direction"
                if not candidate_id.startswith("first-touch-lgbm-")
                else "breakout"
            ),
            recipe_version,
        )
    )
    if not 1 <= index <= len(configs):
        raise ValueError("invalid_classifier_candidate_id")
    config = configs[index - 1]
    if candidate_id.startswith("class-balanced-lgbm-"):
        model: Any = LGBMClassifier(
            objective="multiclass",
            class_weight="balanced",
            **config,
            verbosity=-1,
        )
    elif candidate_id.startswith("market-state-fusion-"):
        model = MarketStateFusionClassifier(config)
    elif candidate_id.startswith("elastic-net-multiclass-"):
        elastic_index = index - 7
        if not 0 <= elastic_index < 4:
            raise ValueError("invalid_classifier_candidate_id")
        model = ElasticNetDirectionClassifier(
            alpha=(0.00005, 0.0001, 0.0005, 0.001)[elastic_index],
            l1_ratio=(0.2, 0.4, 0.6, 0.8)[elastic_index],
        )
    elif candidate_id.startswith("threshold-decomposition-"):
        model = DecomposedDirectionClassifier({**config, "class_weight": "balanced"})
    elif candidate_id.startswith("unweighted-regularized-multiclass-"):
        model = ElasticNetDirectionClassifier(
            alpha=(0.0001, 0.0005)[index - 17],
            l1_ratio=(0.2, 0.6)[index - 17],
            class_weight=None,
        )
    elif candidate_id.startswith("linear-nonlinear-probability-blend-"):
        model = ProbabilityBlendDirectionClassifier(
            config,
            nonlinear_weight=(0.20, 0.35, 0.50, 0.65, 0.80, 0.90)[index - 19],
        )
    else:
        breakout_config = (
            {**config, "class_weight": "balanced"}
            if candidate_id.startswith("first-touch-lgbm-") and index > 8
            else config
        )
        model = LGBMClassifier(
            objective="multiclass",
            **breakout_config,
            verbosity=-1,
        )
    model.fit(
        train[list(_FEATURES)],
        train[target].astype(int).to_numpy(),
    )
    return model


def _classification_candidate_count(task_name: str, recipe_version: str) -> int:
    if task_name == "forecast_direction" and uses_expanded_direction_search(
        recipe_version
    ):
        return _V2_DIRECTION_CANDIDATES
    return _CLASSIFICATION_CANDIDATES


def _probabilities_in_class_order(
    probabilities: np.ndarray,
    source_classes: np.ndarray,
    target_classes: np.ndarray,
) -> np.ndarray:
    aligned = np.zeros((len(probabilities), len(target_classes)), dtype=float)
    for source_index, label in enumerate(source_classes):
        matches = np.flatnonzero(target_classes == label)
        if matches.size:
            aligned[:, int(matches[0])] = probabilities[:, source_index]
    return aligned


def _select_quantile_candidate(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    target: str,
    *,
    cache: Any | None = None,
    cache_prefix: str = "quantile",
) -> tuple[dict[str, Any], str]:
    best_config: dict[str, Any] | None = None
    best_score = float("inf")
    best_id = ""
    validation_y = validation[target].astype(float).to_numpy()

    def fit_candidate(
        item: tuple[int, dict[str, Any]],
    ) -> tuple[dict[str, Any], str, float]:
        index, config = item
        model = LGBMRegressor(
            objective="quantile",
            alpha=0.50,
            **config,
            verbosity=-1,
        )
        model.fit(train[list(_FEATURES)], train[target].astype(float))
        score = mean_pinball_loss(
            validation_y,
            ordered_quantiles(
                np.asarray(model.predict(validation[list(_FEATURES)])).reshape(-1, 1),
                target.rsplit("_", 1)[0],
            )[:, 0],
            alpha=0.50,
        )
        return config, f"lgbm-quantile-{index:02d}", float(score)

    with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_TRIALS) as executor:
        fitted = executor.map(
            lambda item: _run_cached(
                cache,
                f"{cache_prefix}-candidate-{item[0]:02d}",
                lambda: fit_candidate(item),
            ),
            enumerate(_other_configs(), 1),
        )
        for config, candidate_id, score in fitted:
            if score < best_score:
                best_score = score
                best_config = config
                best_id = candidate_id
    if best_config is None:
        raise RuntimeError("quantile_candidate_search_failed")
    return best_config, best_id


def _select_survival_candidate(
    person_period: pd.DataFrame,
    validation: pd.DataFrame,
    horizon: int,
    *,
    cache: Any | None = None,
    cache_prefix: str = "survival",
) -> tuple[LGBMClassifier, str]:
    feature_names = [*_FEATURES, "elapsed_day"]
    observed = validation[f"range_exit_event_{horizon}"].astype(int).to_numpy()
    best_model: LGBMClassifier | None = None
    best_score = float("inf")
    best_id = ""

    def fit_candidate(
        item: tuple[int, dict[str, Any]],
    ) -> tuple[LGBMClassifier, str, float]:
        index, config = item
        model = LGBMClassifier(
            objective="binary",
            **config,
            verbosity=-1,
        )
        model.fit(person_period[feature_names], person_period["event"])
        cumulative = _cumulative_exit_probability(
            model,
            validation,
            horizon,
            feature_names,
        )
        score = float(np.mean((cumulative - observed) ** 2))
        return model, f"lgbm-discrete-hazard-{index:02d}", score

    with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_TRIALS) as executor:
        fitted = executor.map(
            lambda item: _run_cached(
                cache,
                f"{cache_prefix}-candidate-{item[0]:02d}",
                lambda: fit_candidate(item),
            ),
            enumerate(_other_configs(), 1),
        )
        for model, candidate_id, score in fitted:
            if score < best_score:
                best_score = score
                best_model = model
                best_id = candidate_id
    if best_model is None:
        raise RuntimeError("survival_candidate_search_failed")
    return best_model, best_id


def _run_cached(cache: Any | None, key: str, operation: Callable[[], Any]) -> Any:
    return operation() if cache is None else cache.run(key, operation)


def _classification_promotion_result(
    development: pd.DataFrame,
    promotion: pd.DataFrame,
    target: str,
    horizon: int,
    *,
    selected_candidate: str | None,
) -> dict[str, Any]:
    if promotion.empty:
        return {
            "gate_passed": False,
            "calibration_gate_passed": False,
            "failure_code": "promotion_window_incomplete",
        }
    model, temperature = _fit_deployment_classifier(
        development,
        target,
        selected_candidate=selected_candidate,
    )
    if model is None or temperature is None:
        return {
            "gate_passed": False,
            "calibration_gate_passed": False,
            "failure_code": "promotion_model_unavailable",
        }
    probability = _temperature_scale(
        model.predict_proba(promotion[list(_FEATURES)]),
        temperature,
    )
    y = promotion[target].astype(int).to_numpy()
    candidate_brier = _multiclass_brier(y, probability, model.classes_)
    frequencies = np.array(
        [(development[target].astype(int) == label).mean() for label in model.classes_]
    )
    frequency_brier = _multiclass_brier(
        y,
        np.tile(frequencies, (len(promotion), 1)),
        model.classes_,
    )
    imputation = development[list(_FEATURES)].median().fillna(0.0)
    logistic = LogisticRegression(max_iter=500, random_state=_RANDOM_SEED)
    logistic.fit(
        development[list(_FEATURES)].fillna(imputation),
        development[target].astype(int),
    )
    logistic_probability = logistic.predict_proba(
        promotion[list(_FEATURES)].fillna(imputation)
    )
    logistic_brier = _multiclass_brier(
        y,
        logistic_probability,
        logistic.classes_,
    )
    calibration_error = _multiclass_calibration_error(
        y,
        probability,
        model.classes_,
    )
    beat_baselines = candidate_brier < min(frequency_brier, logistic_brier)
    calibration_ok = calibration_error <= 0.05
    return {
        "sample_count": len(promotion),
        "candidate_brier": candidate_brier,
        "frequency_brier": frequency_brier,
        "logistic_brier": logistic_brier,
        "calibration_error": calibration_error,
        "beat_both_baselines": beat_baselines,
        "calibration_gate_passed": calibration_ok,
        "net_long_cash_return": _net_long_cash_return(
            promotion,
            probability,
            model.classes_,
            horizon,
        ),
        "execution_cost_sensitivity": _execution_cost_metrics(
            promotion,
            probability,
            model.classes_,
            horizon,
        ),
        "gate_passed": beat_baselines and calibration_ok,
    }


def _quantile_promotion_result(
    development: pd.DataFrame,
    promotion: pd.DataFrame,
    target: str,
    quantiles: tuple[float, ...],
) -> dict[str, Any]:
    if promotion.empty:
        return {
            "gate_passed": False,
            "coverage_gate_passed": False,
            "failure_code": "promotion_window_incomplete",
        }
    package = _fit_deployment_quantiles(development, target, quantiles)
    if not package:
        return {
            "gate_passed": False,
            "coverage_gate_passed": False,
            "failure_code": "promotion_model_unavailable",
        }
    predictions = {
        alpha: np.asarray(
            package["models"][alpha].predict(promotion[list(_FEATURES)]),
            dtype=float,
        )
        for alpha in quantiles
    }
    predictions = _ordered_predictions(predictions, target.rsplit("_", 1)[0])
    y = promotion[target].astype(float).to_numpy()
    adjustments = dict(
        package.get("cqr_adjustments", {"pooled": package["cqr_adjustment"]})
    )
    lower_adjustment = _cqr_adjustment_vector(
        promotion,
        dict(package.get("cqr_lower_adjustments", adjustments)),
    )
    upper_adjustment = _cqr_adjustment_vector(
        promotion,
        dict(package.get("cqr_upper_adjustments", adjustments)),
    )
    lower = predictions[0.10] - lower_adjustment
    upper = predictions[0.90] + upper_adjustment
    coverage = float(np.mean((y >= lower) & (y <= upper)))
    candidate_pinball = float(
        np.mean(
            [
                mean_pinball_loss(y, predictions[alpha], alpha=alpha)
                for alpha in quantiles
            ]
        )
    )
    baselines = {
        alpha: np.full(
            len(promotion),
            development[target].quantile(alpha),
            dtype=float,
        )
        for alpha in quantiles
    }
    baseline_pinball = float(
        np.mean(
            [mean_pinball_loss(y, baselines[alpha], alpha=alpha) for alpha in quantiles]
        )
    )
    coverage_ok = 0.75 <= coverage <= 0.85
    beat_baseline = candidate_pinball < baseline_pinball
    return {
        "sample_count": len(promotion),
        "candidate_pinball": candidate_pinball,
        "baseline_pinball": baseline_pinball,
        "cqr_coverage": coverage,
        "coverage_gate_passed": coverage_ok,
        "beat_baseline": beat_baseline,
        "gate_passed": coverage_ok and beat_baseline,
    }


def _survival_promotion_result(
    development: pd.DataFrame,
    promotion: pd.DataFrame,
    horizon: int,
) -> dict[str, Any]:
    event_target = f"range_exit_event_{horizon}"
    if promotion.empty or promotion[event_target].nunique() < 2:
        return {
            "gate_passed": False,
            "calibration_gate_passed": False,
            "failure_code": "label_degenerate",
        }
    package = _fit_deployment_survival(development, horizon)
    if package is None:
        return {
            "gate_passed": False,
            "calibration_gate_passed": False,
            "failure_code": "promotion_model_unavailable",
        }
    feature_names = [*_FEATURES, "elapsed_day"]
    cumulative = _calibrated_cumulative_exit_probability(
        package["model"],
        promotion,
        horizon,
        feature_names,
        package.get("calibrator"),
    )
    observed = promotion[event_target].astype(int).to_numpy()
    brier = float(np.mean((cumulative - observed) ** 2))
    null_probability = float(development[event_target].mean())
    null_brier = float(np.mean((null_probability - observed) ** 2))
    c_index = float(roc_auc_score(observed, cumulative))
    calibration_error = abs(float(cumulative.mean()) - float(observed.mean()))
    calibration_ok = calibration_error <= 0.05
    gate = brier < null_brier and c_index > 0.5 and calibration_ok
    return {
        "sample_count": len(promotion),
        "time_dependent_brier": brier,
        "null_brier": null_brier,
        "c_index": c_index,
        "cumulative_probability_calibration_error": calibration_error,
        "calibration_gate_passed": calibration_ok,
        "gate_passed": gate,
    }


def _classification_label_health(
    development: pd.DataFrame,
    folds: list[tuple[list[date], list[date], list[date]]],
    target: str,
) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    for index, fold in enumerate(folds, 1):
        for name, dates in zip(("train", "calibration", "test"), fold, strict=True):
            subset = development[development["session_date"].isin(dates)]
            windows.append(_class_share_summary(subset[target], f"fold-{index}-{name}"))
    passed = bool(windows) and all(bool(row["passed"]) for row in windows)
    return {"passed": passed, "minimum_share": _MIN_CLASS_SHARE, "windows": windows}


def _class_share_summary(values: pd.Series, window: str) -> dict[str, Any]:
    shares = {
        str(label): float((values.astype(int) == label).mean()) if len(values) else 0.0
        for label in (-1, 0, 1)
    }
    return {
        "window": window,
        "sample_count": len(values),
        "shares": shares,
        "passed": len(values) > 0 and min(shares.values()) >= _MIN_CLASS_SHARE,
    }


def _survival_label_health(
    development: pd.DataFrame,
    folds: list[tuple[list[date], list[date], list[date]]],
    target: str,
) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    for index, fold in enumerate(folds, 1):
        for name, dates in zip(("train", "calibration", "test"), fold, strict=True):
            subset = development[development["session_date"].isin(dates)]
            windows.append(
                _survival_share_summary(subset[target], f"fold-{index}-{name}")
            )
    passed = bool(windows) and all(bool(row["passed"]) for row in windows)
    return {
        "passed": passed,
        "minimum_event_and_censor_share": _MIN_SURVIVAL_SHARE,
        "windows": windows,
    }


def _survival_share_summary(values: pd.Series, window: str) -> dict[str, Any]:
    event_share = float(values.astype(float).mean()) if len(values) else 0.0
    return {
        "window": window,
        "sample_count": len(values),
        "event_share": event_share,
        "censored_share": 1.0 - event_share,
        "passed": (
            len(values) > 0
            and _MIN_SURVIVAL_SHARE <= event_share <= 1.0 - _MIN_SURVIVAL_SHARE
        ),
    }


def _gate_failure_codes(
    *,
    statistical_gate: bool,
    calibration_gate: bool,
    stability_gate: bool,
    promotion_gate: bool | None,
    label_health: dict[str, Any] | None = None,
) -> list[str]:
    failures: list[str] = []
    if label_health is not None and not label_health.get("passed"):
        failures.append("label_degenerate")
    if not statistical_gate:
        failures.append("statistical_gate_failed")
    if not calibration_gate:
        failures.append("calibration_gate_failed")
    if not stability_gate:
        failures.append("stability_gate_failed")
    if promotion_gate is False:
        failures.append("promotion_window_failed")
    return failures


def _multiclass_calibration_error(
    labels: np.ndarray,
    probabilities: np.ndarray,
    classes: np.ndarray,
) -> float:
    predicted_index = probabilities.argmax(axis=1)
    confidence = probabilities[np.arange(len(probabilities)), predicted_index]
    predicted_label = classes[predicted_index]
    correct = (predicted_label == labels).astype(float)
    error = 0.0
    for lower in np.linspace(0.0, 0.9, 10):
        upper = lower + 0.1
        mask = (confidence >= lower) & (
            confidence <= upper if upper >= 1.0 else confidence < upper
        )
        if not mask.any():
            continue
        error += float(mask.mean()) * abs(
            float(correct[mask].mean()) - float(confidence[mask].mean())
        )
    return error


def _progress(
    callback: ProgressCallback | None,
    stage: str,
    percent: int,
    detail: str,
) -> None:
    if callback is not None:
        callback(stage, percent, detail)


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    """Hash only the frozen development rows, independent of later snapshots."""

    ordered = frame.sort_values(["session_date", "symbol"]).reset_index(drop=True)
    columns = sorted(ordered.columns)
    row_hashes = pd.util.hash_pandas_object(
        ordered[columns],
        index=False,
        categorize=True,
    )
    digest = hashlib.sha256()
    digest.update("\n".join(columns).encode("utf-8"))
    digest.update(row_hashes.to_numpy(dtype=np.uint64).tobytes())
    digest.update(_DEVELOPMENT_CUTOFF.isoformat().encode("ascii"))
    return digest.hexdigest()


def _freeze_development_labels(frame: pd.DataFrame) -> pd.DataFrame:
    """Remove every target whose future observation would cross the cutoff."""

    development = frame[frame["session_date"] <= _DEVELOPMENT_CUTOFF].copy()
    sessions = sorted(set(development["session_date"]))
    for horizon in (1, 5, 20):
        if len(sessions) <= horizon:
            continue
        mature_anchor_through = sessions[-(horizon + 1)]
        target_columns = [
            name
            for name in (
                f"direction_{horizon}",
                f"breakout_direction_{horizon}",
                f"return_fwd_{horizon}",
                f"mfe_{horizon}",
                f"mae_{horizon}",
                f"range_exit_event_{horizon}",
                f"range_exit_day_{horizon}",
            )
            if name in development
        ]
        development.loc[
            development["session_date"] > mature_anchor_through,
            target_columns,
        ] = np.nan
    return development


def candidate_training_fingerprint(
    paths: LivePaths,
    recipe_version: str = CANDIDATE_RECIPE_V1,
) -> str:
    recipe = candidate_recipe(recipe_version)
    artifact = load_latest_feature_artifact(paths)
    if artifact.universe_version != resolve_live_universe(paths).version:
        raise RuntimeError("candidate_feature_universe_mismatch")
    frame = pq.read_table(artifact.path).to_pandas()
    frame["session_date"] = pd.to_datetime(frame["session_date"]).dt.date
    development = _freeze_development_labels(frame)
    if development.empty:
        raise RuntimeError("candidate_development_data_unavailable")
    matrix_hash = _candidate_matrix_hash(_registered_candidate_matrix(recipe.version))
    return hashlib.sha256(
        (
            f"{artifact.universe_version}:{_frame_fingerprint(development)}:"
            f"{recipe.version}:{matrix_hash}:{EVALUATION_PIPELINE_VERSION}"
        ).encode("ascii")
    ).hexdigest()


def candidate_training_report_fingerprint(report: dict[str, Any]) -> str | None:
    """Recover the semantic fingerprint from older completed training reports."""

    development_hash = report.get("development_data_hash")
    candidate_matrix = report.get("candidate_matrix")
    if not isinstance(development_hash, str) or not isinstance(candidate_matrix, dict):
        return None
    matrix_hash = _candidate_matrix_hash(candidate_matrix)
    recipe_version = report.get("candidate_recipe_version")
    if not isinstance(recipe_version, str):
        recipe_version = CANDIDATE_RECIPE_V1
    base = f"{development_hash}:{recipe_version}:{matrix_hash}"
    universe_version = report.get("universe_version")
    if isinstance(universe_version, str):
        base = f"{universe_version}:{base}"
    if report.get("evaluation_pipeline_version"):
        base += f":{report['evaluation_pipeline_version']}"
    return hashlib.sha256(base.encode("ascii")).hexdigest()


def _candidate_matrix_hash(candidate_matrix: dict[str, Any]) -> str:
    """Hash candidate content without depending on presentation whitespace."""

    canonical = json.dumps(
        candidate_matrix,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _walk_forward_folds(
    dates: list[date],
) -> list[tuple[list[date], list[date], list[date]]]:
    if len(dates) < 5 * 252 + 252 + 20 + 126:
        return []
    folds: list[tuple[list[date], list[date], list[date]]] = []
    validation_start = 5 * 252
    while validation_start + 252 + 20 + 126 <= len(dates):
        train_end = validation_start - 20
        validation_end = validation_start + 252
        test_start = validation_end + 20
        test_end = test_start + 126
        folds.append(
            (
                dates[:train_end],
                dates[validation_start:validation_end],
                dates[test_start:test_end],
            )
        )
        validation_start += 126
    return folds


def _fit_temperature(
    probabilities: np.ndarray, labels: np.ndarray, classes: np.ndarray
) -> float:
    candidates = np.linspace(0.5, 3.0, 51)
    losses = [
        log_loss(
            labels,
            _temperature_scale(probabilities, float(value)),
            labels=classes,
        )
        for value in candidates
    ]
    return float(candidates[int(np.argmin(losses))])


def _temperature_scale(probabilities: np.ndarray, temperature: float) -> np.ndarray:
    logits = np.log(np.clip(probabilities, 1e-12, 1.0)) / temperature
    logits -= logits.max(axis=1, keepdims=True)
    values = np.exp(logits)
    return np.asarray(values / values.sum(axis=1, keepdims=True), dtype=np.float64)


def _multiclass_brier(
    labels: np.ndarray, probabilities: np.ndarray, classes: np.ndarray
) -> float:
    expected = np.column_stack([labels == label for label in classes]).astype(float)
    return float(np.mean(np.sum((probabilities - expected) ** 2, axis=1)))


def _net_long_cash_return(
    test: pd.DataFrame,
    probabilities: np.ndarray,
    classes: np.ndarray,
    horizon: int,
) -> float:
    up_index = next(
        (index for index, value in enumerate(classes) if int(value) == 1), None
    )
    down_index = next(
        (index for index, value in enumerate(classes) if int(value) == -1), None
    )
    if up_index is None or down_index is None:
        return 0.0
    position = (probabilities[:, up_index] >= 0.45) & (
        probabilities[:, up_index] > probabilities[:, down_index]
    )
    returns = test[f"return_fwd_{horizon}"].astype(float).to_numpy()
    net = np.where(position, returns - 0.0015, 0.0)
    return float(np.mean(net))


def _execution_cost_metrics(
    frame: pd.DataFrame,
    probabilities: np.ndarray,
    classes: np.ndarray,
    horizon: int,
) -> dict[str, Any]:
    """Evaluate a long/cash forecast strategy with non-zero total frictions."""

    up_index = next(
        (index for index, value in enumerate(classes) if int(value) == 1), None
    )
    down_index = next(
        (index for index, value in enumerate(classes) if int(value) == -1), None
    )
    if up_index is None or down_index is None or frame.empty:
        return {
            "trade_count": 0,
            "net_returns": {str(cost): 0.0 for cost in _EXECUTION_COST_SCENARIOS_BPS},
            "all_cost_scenarios_positive": False,
            "maximum_symbol_concentration": 1.0,
            "capacity_notional_proxy": 0.0,
        }

    liquid = frame["turnover"].fillna(0.0).astype(float).to_numpy() > 0.0
    position = (
        (probabilities[:, up_index] >= 0.45)
        & (probabilities[:, up_index] > probabilities[:, down_index])
        & liquid
    )
    trade_count = int(position.sum())
    if trade_count == 0:
        return {
            "trade_count": 0,
            "net_returns": {str(cost): 0.0 for cost in _EXECUTION_COST_SCENARIOS_BPS},
            "all_cost_scenarios_positive": False,
            "maximum_symbol_concentration": 1.0,
            "capacity_notional_proxy": 0.0,
        }

    returns = frame.loc[position, f"return_fwd_{horizon}"].astype(float).to_numpy()
    net_returns = {
        str(cost): float(np.mean(returns - cost / 10_000.0))
        for cost in _EXECUTION_COST_SCENARIOS_BPS
    }
    selected = frame.loc[position]
    concentration = float(selected["symbol"].value_counts(normalize=True).max())
    capacity_proxy = float(selected["turnover"].astype(float).median() * 0.01)
    return {
        "trade_count": trade_count,
        "net_returns": net_returns,
        "all_cost_scenarios_positive": all(value > 0 for value in net_returns.values()),
        "maximum_symbol_concentration": concentration,
        "capacity_notional_proxy": capacity_proxy,
    }


def _task_execution_gate(
    folds: list[dict[str, Any]],
    promotion: dict[str, Any] | None = None,
    *,
    require_promotion: bool = True,
) -> bool:
    development_passes = sum(
        bool(
            row.get("execution_cost_sensitivity", {}).get("all_cost_scenarios_positive")
        )
        and float(
            row.get("execution_cost_sensitivity", {}).get(
                "maximum_symbol_concentration", 1.0
            )
        )
        <= 0.25
        and float(
            row.get("execution_cost_sensitivity", {}).get(
                "capacity_notional_proxy", 0.0
            )
        )
        > 0.0
        for row in folds
    )
    promotion_metrics = (promotion or {}).get("execution_cost_sensitivity", {})
    promotion_passed = (
        bool(promotion_metrics.get("all_cost_scenarios_positive"))
        and float(promotion_metrics.get("maximum_symbol_concentration", 1.0)) <= 0.25
        and float(promotion_metrics.get("capacity_notional_proxy", 0.0)) > 0.0
    )
    return (
        len(folds) == 4
        and development_passes >= 3
        and (promotion_passed or not require_promotion)
    )


def _execution_gate_summary(
    tasks: list[dict[str, Any]],
    *,
    require_promotion: bool = True,
) -> dict[str, Any]:
    """Use the registered 5-day direction strategy for independent execution."""

    strategy = next(
        (
            row
            for row in tasks
            if row.get("task") == "forecast_direction" and row.get("horizon") == 5
        ),
        None,
    )
    if strategy is None:
        return {
            "ready": False,
            "strategy": "forecast_direction:5",
            "cost_scenarios_bps": list(_EXECUTION_COST_SCENARIOS_BPS),
            "development_windows_passed": 0,
            "promotion_window_passed": False,
            "failure_codes": ["execution_strategy_unavailable"],
        }
    folds = list(strategy.get("folds", []))
    promotion = dict(strategy.get("promotion_window", {}))
    development_passes = sum(
        bool(
            row.get("execution_cost_sensitivity", {}).get("all_cost_scenarios_positive")
        )
        and float(
            row.get("execution_cost_sensitivity", {}).get(
                "maximum_symbol_concentration", 1.0
            )
        )
        <= 0.25
        and float(
            row.get("execution_cost_sensitivity", {}).get(
                "capacity_notional_proxy", 0.0
            )
        )
        > 0.0
        for row in folds
    )
    promotion_metrics = promotion.get("execution_cost_sensitivity", {})
    promotion_passed = (
        bool(promotion_metrics.get("all_cost_scenarios_positive"))
        and float(promotion_metrics.get("maximum_symbol_concentration", 1.0)) <= 0.25
        and float(promotion_metrics.get("capacity_notional_proxy", 0.0)) > 0.0
    )
    ready = (
        len(folds) == 4
        and development_passes >= 3
        and (promotion_passed or not require_promotion)
    )
    return {
        "ready": ready,
        "strategy": "forecast_direction:5",
        "cost_scenarios_bps": list(_EXECUTION_COST_SCENARIOS_BPS),
        "development_windows_passed": development_passes,
        "promotion_window_passed": promotion_passed,
        "promotion_required": require_promotion,
        "maximum_allowed_symbol_concentration": 0.25,
        "capacity_fraction_of_median_turnover": 0.01,
        "failure_codes": [] if ready else ["execution_economics_gate_failed"],
    }


def _person_period_rows(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    exit_day = frame[f"range_exit_day_{horizon}"].astype(int)
    observed = frame[f"range_exit_event_{horizon}"].astype(int)
    for elapsed_day in range(1, horizon + 1):
        at_risk = frame[exit_day >= elapsed_day].copy()
        at_risk["elapsed_day"] = float(elapsed_day)
        at_risk["event"] = (
            (exit_day.loc[at_risk.index] == elapsed_day)
            & (observed.loc[at_risk.index] == 1)
        ).astype(int)
        rows.append(at_risk)
    return pd.concat(rows, ignore_index=True)


def _cumulative_exit_probability(
    model: LGBMClassifier,
    frame: pd.DataFrame,
    horizon: int,
    feature_names: list[str],
) -> np.ndarray:
    survival = np.ones(len(frame), dtype=float)
    for elapsed_day in range(1, horizon + 1):
        values = frame[list(_FEATURES)].copy()
        values["elapsed_day"] = float(elapsed_day)
        hazard = np.asarray(
            model.predict_proba(values[feature_names])[:, 1], dtype=float
        )
        survival *= 1.0 - np.clip(hazard, 0.0, 1.0)
    return 1.0 - survival


def _calibrated_cumulative_exit_probability(
    model: LGBMClassifier,
    frame: pd.DataFrame,
    horizon: int,
    feature_names: list[str],
    calibrator: Any | None,
) -> np.ndarray:
    cumulative = _cumulative_exit_probability(model, frame, horizon, feature_names)
    if calibrator is None:
        return cumulative
    return np.asarray(calibrator.predict(cumulative), dtype=float)


def _fit_survival_calibrator(
    model: LGBMClassifier,
    calibration: pd.DataFrame,
    horizon: int,
    feature_names: list[str],
    *,
    compare_variants: bool = False,
) -> Any | None:
    target = f"range_exit_event_{horizon}"
    if calibration.empty or calibration[target].nunique() < 2:
        return None
    raw = _cumulative_exit_probability(
        model,
        calibration,
        horizon,
        feature_names,
    )
    observed = calibration[target].astype(int).to_numpy()
    if not compare_variants:
        calibrator = IsotonicRegression(
            increasing=True,
            out_of_bounds="clip",
            y_min=0.0,
            y_max=1.0,
        )
        calibrator.fit(raw, observed.astype(float))
        return calibrator

    if calibration.session_date.nunique() < 28:
        # Too little time history to select a calibrator: use the fixed method.
        return _refit_survival_calibrator("isotonic", raw, observed)
    fit_partition, select_partition = split_validation_selection_calibration(calibration)
    fit_mask = calibration.session_date.isin(fit_partition.session_date).to_numpy()
    select_mask = calibration.session_date.isin(select_partition.session_date).to_numpy()
    fit_raw, select_raw = raw[fit_mask], raw[select_mask]
    fit_observed, select_observed = observed[fit_mask], observed[select_mask]
    event_rate = float(fit_observed.mean())
    candidates: list[SurvivalProbabilityCalibrator] = [
        SurvivalProbabilityCalibrator("raw"),
        SurvivalProbabilityCalibrator(
            "event-rate-shrink",
            event_rate=event_rate,
            shrinkage=0.20,
        ),
    ]
    if len(set(fit_observed)) == 2:
        platt = LogisticRegression(
            max_iter=500,
            random_state=_RANDOM_SEED,
        )
        platt.fit(fit_raw.reshape(-1, 1), fit_observed)
        candidates.append(SurvivalProbabilityCalibrator("platt", model=platt))
        isotonic = IsotonicRegression(
            increasing=True,
            out_of_bounds="clip",
            y_min=0.0,
            y_max=1.0,
        )
        isotonic.fit(fit_raw, fit_observed.astype(float))
        candidates.append(SurvivalProbabilityCalibrator("isotonic", model=isotonic))
    selected = min(
        candidates,
        key=lambda item: float(
            np.mean((item.predict(select_raw) - select_observed) ** 2)
        ),
    )
    return _refit_survival_calibrator(
        selected.method,
        raw,
        observed,
    )


def _refit_survival_calibrator(
    method: str,
    raw: np.ndarray,
    observed: np.ndarray,
) -> SurvivalProbabilityCalibrator:
    event_rate = float(observed.mean())
    if method == "platt" and len(set(observed)) == 2:
        model = LogisticRegression(max_iter=500, random_state=_RANDOM_SEED)
        model.fit(raw.reshape(-1, 1), observed)
        return SurvivalProbabilityCalibrator("platt", model=model)
    if method == "isotonic" and len(set(observed)) == 2:
        model = IsotonicRegression(
            increasing=True,
            out_of_bounds="clip",
            y_min=0.0,
            y_max=1.0,
        )
        model.fit(raw, observed.astype(float))
        return SurvivalProbabilityCalibrator("isotonic", model=model)
    if method == "event-rate-shrink":
        return SurvivalProbabilityCalibrator(
            method,
            event_rate=event_rate,
            shrinkage=0.20,
        )
    return SurvivalProbabilityCalibrator("raw")


def _deployment_train_and_calibration_dates(
    frame: pd.DataFrame,
) -> tuple[list[date], list[date]]:
    dates = sorted(set(frame["session_date"]))
    if len(dates) < 252 + 20 + 252:
        return [], []
    calibration_dates = dates[-252:]
    training_dates = dates[: -(252 + 20)]
    return training_dates, calibration_dates


def _write_fold_evidence(
    directory: Path | None, history: pd.DataFrame, test: pd.DataFrame,
    task: str, horizon: int, fold: int, predictions: dict[str, np.ndarray],
) -> dict[str, Any] | None:
    """Persist scored rows, bound by hash to the owning run's task report."""
    if directory is None:
        return None
    if task not in {"forecast_direction", "breakout", "return_fwd", "mfe", "mae", "range_exit"}:
        raise ValueError("oos_task_invalid")
    if horizon not in (1, 5, 20) or fold < 1:
        raise ValueError("oos_window_invalid")
    keys = ["symbol", "session_date"]
    calendar = history.loc[history.session_date <= _DEVELOPMENT_CUTOFF, keys].copy()
    if calendar.duplicated(keys).any() or test.duplicated(keys).any():
        raise ValueError("oos_duplicate_anchor")
    calendar = calendar.sort_values(keys)
    calendar["target_end_date"] = calendar.groupby("symbol").session_date.shift(-horizon)
    rows = test[keys + ["is_etf"]].merge(calendar, on=keys, how="left", validate="one_to_one")
    if rows.empty or rows.target_end_date.isna().any():
        raise ValueError("oos_immature_target")
    if not (rows.target_end_date > rows.session_date).all():
        raise ValueError("oos_target_order_invalid")
    for name, values in predictions.items():
        array = np.asarray(values)
        if array.shape != (len(rows),) or not np.isfinite(array).all():
            raise ValueError("oos_prediction_alignment_invalid")
        rows[name] = array
    rows["task"] = task
    rows["horizon"] = horizon
    rows["fold_id"] = f"fold-{fold}"
    rows["evaluation_pipeline_version"] = EVALUATION_PIPELINE_VERSION
    rows["strict_historical_pit"] = False
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{task}-{horizon}-fold-{fold}.parquet"
    # A failed write stays unreferenced: only a successful file enters the report.
    if path.exists():
        try:
            pd.testing.assert_frame_equal(
                pq.read_table(path).to_pandas(),
                rows.reset_index(drop=True),
                check_dtype=True,
                check_like=False,
            )
        except (OSError, AssertionError) as error:
            raise ValueError("oos_resume_evidence_mismatch") from error
    else:
        with path.open("xb") as stream:
            pq.write_table(pa.Table.from_pandas(rows, preserve_index=False), stream)
    return {
        "path": f"oos/{path.name}", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "sample_count": len(rows), "scope": "development_replay_not_prospective",
    }


def split_validation_selection_calibration(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep selection labels out of calibration's information window."""
    dates = sorted(frame["session_date"].dropna().unique())
    if len(dates) < 28:
        raise ValueError("selection_calibration_sessions_insufficient")
    boundary = (len(dates) - 20) // 2
    selection = frame[frame["session_date"].isin(dates[:boundary])].copy()
    calibration = frame[frame["session_date"].isin(dates[boundary + 20 :])].copy()
    return selection, calibration


def _cache_arguments(cache: Any | None, prefix: str) -> dict[str, Any]:
    return {} if cache is None else {"cache": cache, "cache_prefix": prefix}


def _partition_evidence(
    selection: pd.DataFrame, calibration: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "selection_start": str(selection.session_date.min()),
        "selection_end": str(selection.session_date.max()),
        "selection_sample_count": len(selection),
        "calibration_start": str(calibration.session_date.min()),
        "calibration_end": str(calibration.session_date.max()),
        "calibration_sample_count": len(calibration),
        "selection_calibration_embargo_sessions": 20,
    }


def _ordered_predictions(
    predictions: dict[float, np.ndarray], outcome: str,
) -> dict[float, np.ndarray]:
    levels = sorted(predictions)
    ordered = ordered_quantiles(
        np.column_stack([predictions[level] for level in levels]), outcome,
    )
    return {level: ordered[:, index] for index, level in enumerate(levels)}


def _volatility_quantile_baseline(
    train: pd.DataFrame, test: pd.DataFrame, target: str,
    quantiles: tuple[float, ...],
) -> dict[float, np.ndarray]:
    """Fit standardized historical quantiles using training rows only."""
    train_scale = train["volatility_20"].astype(float)
    valid = np.isfinite(train_scale) & (train_scale > 0)
    if not valid.any():
        raise ValueError("volatility_baseline_scale_unavailable")
    fallback = float(train_scale[valid].median())
    train_scale = train_scale.where(valid, fallback)
    test_scale = test["volatility_20"].astype(float)
    test_scale = test_scale.where(np.isfinite(test_scale) & (test_scale > 0), fallback)
    standardized = train[target].astype(float) / train_scale
    if not np.isfinite(standardized).all():
        raise ValueError("volatility_baseline_target_invalid")
    return {
        alpha: float(standardized.quantile(alpha)) * test_scale.to_numpy()
        for alpha in quantiles
    }


def _fit_deployment_classifier(
    frame: pd.DataFrame,
    target: str,
    *,
    selected_candidate: str | None = None,
    recipe_version: str = CANDIDATE_RECIPE_V1,
    cache: Any | None = None,
    cache_prefix: str = "classifier-deployment",
) -> tuple[Any | None, float | None]:
    training_dates, calibration_dates = _deployment_train_and_calibration_dates(frame)
    if not training_dates or not calibration_dates:
        return None, None
    train = frame[frame["session_date"].isin(training_dates)]
    calibration = frame[frame["session_date"].isin(calibration_dates)]
    labels = (-1, 0, 1)
    if set(train[target].astype(int).unique()) != set(labels) or calibration.empty:
        return None, None
    selection, calibration = split_validation_selection_calibration(calibration)
    if selected_candidate is None:
        model, _candidate_id = _select_classifier_candidate(
            train,
            selection,
            target,
            task_name=(
                "forecast_direction" if target.startswith("direction_") else "breakout"
            ),
            recipe_version=recipe_version,
            **_cache_arguments(cache, cache_prefix),
        )
    else:
        model = _fit_classifier_candidate_id(
            train,
            target,
            selected_candidate,
            recipe_version=recipe_version,
        )
    temperature = _fit_temperature(
        model.predict_proba(calibration[list(_FEATURES)]),
        calibration[target].astype(int).to_numpy(),
        model.classes_,
    )
    return model, temperature


def _fit_deployment_quantiles(
    frame: pd.DataFrame,
    target: str,
    quantiles: tuple[float, ...],
    *,
    recipe_version: str = CANDIDATE_RECIPE_V1,
    cache: Any | None = None,
    cache_prefix: str = "quantile-deployment",
) -> dict[str, Any]:
    training_dates, calibration_dates = _deployment_train_and_calibration_dates(frame)
    if not training_dates or not calibration_dates:
        return {}
    train = frame[frame["session_date"].isin(training_dates)]
    calibration = frame[frame["session_date"].isin(calibration_dates)]
    if train.empty or calibration.empty:
        return {}
    selection, calibration = split_validation_selection_calibration(calibration)
    selected_config, candidate_id = _select_quantile_candidate(
        train,
        selection,
        target,
        **_cache_arguments(cache, cache_prefix),
    )
    models: dict[float, LGBMRegressor] = {}
    calibration_predictions: dict[float, np.ndarray] = {}
    for alpha in quantiles:
        model = LGBMRegressor(
            objective="quantile",
            alpha=alpha,
            **selected_config,
            verbosity=-1,
        )
        model.fit(train[list(_FEATURES)], train[target].astype(float))
        models[alpha] = model
        calibration_predictions[alpha] = np.asarray(
            model.predict(calibration[list(_FEATURES)]),
            dtype=float,
        )
    calibration_predictions = _ordered_predictions(
        calibration_predictions, target.rsplit("_", 1)[0]
    )
    calibration_y = calibration[target].astype(float).to_numpy()
    lower_nonconformity = np.maximum(
        calibration_predictions[0.10] - calibration_y,
        0.0,
    )
    upper_nonconformity = np.maximum(
        calibration_y - calibration_predictions[0.90],
        0.0,
    )
    nonconformity = np.maximum(lower_nonconformity, upper_nonconformity)
    adjustments = _group_cqr_adjustments(calibration, nonconformity)
    lower_adjustments = upper_adjustments = adjustments
    return {
        "evaluation_pipeline_version": EVALUATION_PIPELINE_VERSION,
        "outcome": target.rsplit("_", 1)[0],
        "selected_candidate": candidate_id,
        "partition_evidence": _partition_evidence(selection, calibration),
        "models": models,
        "cqr_adjustment": float(adjustments["pooled"]),
        "cqr_adjustments": adjustments,
        "cqr_lower_adjustments": lower_adjustments,
        "cqr_upper_adjustments": upper_adjustments,
        "quantiles": quantiles,
    }


def _group_cqr_adjustments(
    calibration: pd.DataFrame,
    nonconformity: np.ndarray,
    *,
    include_volatility_state: bool = False,
) -> dict[str, float]:
    """Calibrate with deterministic group -> asset -> pooled fallback."""

    if len(nonconformity) != len(calibration):
        raise ValueError("cqr_sample_alignment_invalid")
    pooled = finite_sample_cqr(nonconformity)
    adjustments = {"pooled": pooled, "stock": pooled, "etf": pooled}
    if "is_etf" not in calibration:
        return adjustments
    groups = calibration["is_etf"].fillna(0.0).astype(float).to_numpy() >= 0.5
    for name, mask in (("stock", ~groups), ("etf", groups)):
        if int(mask.sum()) >= 50:
            adjustments[name] = finite_sample_cqr(nonconformity[mask])
    if include_volatility_state and "volatility_20" in calibration:
        volatility = calibration["volatility_20"].astype(float).fillna(0.0).to_numpy()
        threshold = float(np.median(volatility))
        adjustments["volatility_median"] = threshold
        high_volatility = volatility > threshold
        for asset_name, asset_mask in (("stock", ~groups), ("etf", groups)):
            for state_name, state_mask in (
                ("low_vol", ~high_volatility),
                ("high_vol", high_volatility),
            ):
                mask = asset_mask & state_mask
                fallback = adjustments[asset_name]
                adjustments[f"{asset_name}_{state_name}"] = (
                    finite_sample_cqr(nonconformity[mask])
                    if int(mask.sum()) >= 50
                    else fallback
                )
    return adjustments


def _cqr_adjustment_vector(
    frame: pd.DataFrame,
    adjustments: dict[str, float],
) -> np.ndarray:
    pooled = float(adjustments.get("pooled", 0.0))
    if "is_etf" not in frame:
        return np.full(len(frame), pooled, dtype=float)
    is_etf = frame["is_etf"].fillna(0.0).astype(float).to_numpy() >= 0.5
    asset_adjustment = np.where(
        is_etf,
        float(adjustments.get("etf", pooled)),
        float(adjustments.get("stock", pooled)),
    )
    if "volatility_median" not in adjustments or "volatility_20" not in frame:
        return asset_adjustment
    threshold = float(adjustments["volatility_median"])
    high_volatility = (
        frame["volatility_20"].astype(float).fillna(0.0).to_numpy() > threshold
    )
    result = asset_adjustment.copy()
    for index, (etf, high_vol) in enumerate(zip(is_etf, high_volatility, strict=True)):
        asset_name = "etf" if etf else "stock"
        state_name = "high_vol" if high_vol else "low_vol"
        result[index] = float(
            adjustments.get(
                f"{asset_name}_{state_name}",
                adjustments.get(asset_name, pooled),
            )
        )
    return result


def _fit_deployment_survival(
    frame: pd.DataFrame,
    horizon: int,
    *,
    recipe_version: str = CANDIDATE_RECIPE_V1,
    cache: Any | None = None,
    cache_prefix: str = "survival-deployment",
) -> dict[str, Any] | None:
    training_dates, calibration_dates = _deployment_train_and_calibration_dates(frame)
    if not training_dates or not calibration_dates:
        return None
    train = frame[frame["session_date"].isin(training_dates)]
    calibration = frame[frame["session_date"].isin(calibration_dates)]
    person_period = _person_period_rows(train, horizon)
    if person_period.empty or person_period["event"].nunique() < 2:
        return None
    feature_names = [*_FEATURES, "elapsed_day"]
    selection, calibration = split_validation_selection_calibration(calibration)
    model, candidate_id = _select_survival_candidate(
        person_period,
        selection,
        horizon,
        **_cache_arguments(cache, cache_prefix),
    )
    return {
        "evaluation_pipeline_version": EVALUATION_PIPELINE_VERSION,
        "selected_candidate": candidate_id,
        "partition_evidence": _partition_evidence(selection, calibration),
        "model": model,
        "calibrator": _fit_survival_calibrator(
            model,
            calibration,
            horizon,
            feature_names,
            compare_variants=(
                uses_expanded_direction_search(recipe_version) and horizon == 20
            ),
        ),
        "feature_names": tuple(feature_names),
        "horizon": horizon,
    }


def _component_abbreviation(task: str) -> str:
    return {
        "forecast_direction": "dir",
        "breakout": "brk",
        "return_fwd": "ret",
        "mfe": "mfe",
        "mae": "mae",
        "range_exit": "rex",
    }[task]


def _component_package(
    package: dict[str, Any],
    *,
    task: str,
    horizon: int,
) -> dict[str, Any] | None:
    if task in {"forecast_direction", "breakout"}:
        payload = package["classifiers"].get((task, horizon))
    elif task in {"return_fwd", "mfe", "mae"}:
        payload = package["quantiles"].get((task, horizon))
    elif task == "range_exit":
        payload = package["survival"].get(horizon)
    else:
        return None
    if payload is None:
        return None
    return {
        "schema_version": "research-component-v1",
        "evaluation_pipeline_version": package.get("evaluation_pipeline_version"),
        "task": task,
        "horizon": horizon,
        "features": package["features"],
        "feature_distribution": package["feature_distribution"],
        "dataset_revision": package["dataset_revision"],
        "feature_set_version": package["feature_set_version"],
        "universe_version": package["universe_version"],
        "input_hash": package["input_hash"],
        "trained_at": package["trained_at"],
        "payload": payload,
    }


def _atomic_pickle(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with temporary.open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "LIVE_TRAINING_REPORT_SCHEMA_VERSION",
    "LiveModelTrainer",
    "candidate_training_fingerprint",
    "candidate_training_report_fingerprint",
    "load_latest_training_report",
    "write_latest_training_pointer",
]
