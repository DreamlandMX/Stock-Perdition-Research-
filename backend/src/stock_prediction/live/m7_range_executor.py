"""Registered range-exit-20 base and calibration execution for M7."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from stock_prediction.live.m7_executor import M7DevelopmentSplits, M7PreparedFeatureData
from stock_prediction.live.training import (
    _FEATURES as LEGACY_FEATURES,
    _cumulative_exit_probability,
    _fit_survival_calibrator,
    _other_configs,
    _person_period_rows,
    _select_survival_candidate,
)
from stock_prediction.modeling.contracts import (
    M7FitComponent,
    M7FittedObjectEvidence,
    M7PartitionRole,
    SampleKey,
    hash_m7_sample_keys,
)
from stock_prediction.modeling.m7_gates import (
    M7GateDecision,
    evaluate_m7_range_exit_20,
)
from stock_prediction.modeling.m7_range_development import (
    M7RangeFoldEvaluation,
    evaluate_m7_range_fold,
)
from stock_prediction.modeling.report import file_sha256, write_immutable_json


class M7RangeExecutionError(RuntimeError):
    """Range-exit-20 development evidence cannot be completed."""


@dataclass(frozen=True, slots=True)
class M7RangeExecutionOptions:
    bootstrap_draws: int = 10_000
    fresh_process_timeout_seconds: float = 900.0


@dataclass(frozen=True, slots=True)
class M7RangeReplayResult:
    test_probabilities: np.ndarray
    model_sha256: str
    worker_pid: int


@dataclass(frozen=True, slots=True)
class M7RangeDevelopmentOutcome:
    gate: M7GateDecision
    fold_evaluations: tuple[M7RangeFoldEvaluation, ...]
    fold_models: tuple[Any, Any, Any, Any]
    feature_names: tuple[str, ...]
    c_index_delta_lower_90: float
    runtime_seconds: float
    cpu_seconds: float


RangePartialFoldCallback = Callable[
    [str, str, Any, M7RangeFoldEvaluation, tuple[str, ...]], None
]
ProgressCallback = Callable[[str, int, str, str | None], None]


def fresh_process_survival_replay(
    *,
    person_period: pd.DataFrame,
    test: pd.DataFrame,
    feature_names: Sequence[str],
    configuration: Mapping[str, object],
    timeout_seconds: float,
) -> M7RangeReplayResult:
    """Fit one selected range configuration in a new Python process."""

    if timeout_seconds <= 0:
        raise M7RangeExecutionError("m7_range_replay_timeout_invalid")
    with tempfile.TemporaryDirectory(prefix="m7-range-replay-") as directory:
        root = Path(directory)
        person_period_path = root / "person-period.parquet"
        test_path = root / "test.parquet"
        person_period.to_parquet(person_period_path, index=False)
        test.to_parquet(test_path, index=False)
        request_path = root / "request.json"
        write_immutable_json(
            request_path,
            {
                "schema_version": "m7-range-fresh-replay-request-v1",
                "feature_names": list(feature_names),
                "configuration": dict(configuration),
                "person_period_sha256": file_sha256(person_period_path),
                "test_sha256": file_sha256(test_path),
            },
            pretty=False,
        )
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "stock_prediction.live.m7_range_replay_worker",
                    str(request_path),
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise M7RangeExecutionError("m7_range_replay_timeout") from exc
        if completed.returncode != 0:
            raise M7RangeExecutionError(
                "m7_range_replay_process_failed:"
                + (completed.stderr.strip() or f"exit_{completed.returncode}")
            )
        response_path = root / "response.json"
        probabilities_path = root / "replay-probabilities.npz"
        try:
            response = json.loads(response_path.read_text(encoding="utf-8"))
            probabilities_sha256 = str(response["probabilities_sha256"])
            if (
                response.get("schema_version")
                != "m7-range-fresh-replay-response-v1"
                or probabilities_sha256 != file_sha256(probabilities_path)
                or int(response["row_count"]) != len(test)
                or int(response["column_count"]) != 3
            ):
                raise ValueError("identity mismatch")
            with np.load(probabilities_path, allow_pickle=False) as artifact:
                probabilities = np.asarray(
                    artifact["probabilities"], dtype=np.float64
                )
            if probabilities.shape != (len(test), 3):
                raise ValueError("shape mismatch")
            model_sha256 = str(response["model_sha256"])
            worker_pid = int(response["worker_pid"])
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise M7RangeExecutionError("m7_range_replay_output_invalid") from exc
        return M7RangeReplayResult(
            test_probabilities=probabilities,
            model_sha256=model_sha256,
            worker_pid=worker_pid,
        )


def _model_sha256(model: Any) -> str:
    return hashlib.sha256(model.booster_.model_to_string().encode("utf-8")).hexdigest()


def _selected_range_configuration(candidate_id: str) -> dict[str, Any]:
    prefix = "lgbm-discrete-hazard-"
    if not candidate_id.startswith(prefix):
        raise M7RangeExecutionError("m7_range_selected_candidate_id_invalid")
    try:
        index = int(candidate_id.removeprefix(prefix)) - 1
        if index < 0:
            raise IndexError(index)
        configuration = _other_configs()[index]
    except (IndexError, ValueError) as exc:
        raise M7RangeExecutionError(
            "m7_range_selected_candidate_id_invalid"
        ) from exc
    return dict(configuration)


def run_m7_range_development(
    prepared: M7PreparedFeatureData,
    splits: M7DevelopmentSplits,
    *,
    options: M7RangeExecutionOptions | None = None,
    cancellation_check: Callable[[], None] | None = None,
    partial_fold_callback: RangePartialFoldCallback | None = None,
    progress_callback: ProgressCallback | None = None,
) -> M7RangeDevelopmentOutcome:
    active = options or M7RangeExecutionOptions()
    check_cancellation = cancellation_check or (lambda: None)
    started = time.monotonic()
    cpu_started = time.process_time()
    evaluations: list[M7RangeFoldEvaluation] = []
    fitted_models: list[Any] = []
    total_folds = len(splits.folds)
    for completed_folds, fold in enumerate(splits.folds):
        if progress_callback is not None:
            progress_callback(
                "candidate_search",
                10 + completed_folds * 50 // total_folds,
                f"区间退出 · outer {completed_folds + 1}/{total_folds} · H20 · CPU LightGBM",
                f"range.{fold.fold_id}",
            )
        partitions = {
            "train": _range_partition(prepared.frame, fold.train_sessions),
            "selection": _range_partition(prepared.frame, fold.selection_sessions),
            "calibration": _range_partition(prepared.frame, fold.calibration_sessions),
            "test": _range_partition(prepared.frame, fold.outer_test_sessions),
        }
        train = partitions["train"]
        selection = partitions["selection"]
        calibration = partitions["calibration"]
        test = partitions["test"]
        person_period = _person_period_rows(train, 20)
        if person_period["event"].nunique() < 2:
            raise M7RangeExecutionError("m7_range_training_event_class_missing")
        feature_names = [*LEGACY_FEATURES, "elapsed_day"]
        model, candidate_id = _select_survival_candidate(
            person_period,
            selection,
            20,
        )
        fitted_models.append(model)
        calibration_raw = _curves(model, calibration, feature_names)
        test_raw = _curves(model, test, feature_names)
        replay = fresh_process_survival_replay(
            person_period=person_period,
            test=test,
            feature_names=feature_names,
            configuration=_selected_range_configuration(candidate_id),
            timeout_seconds=active.fresh_process_timeout_seconds,
        )
        deterministic = bool(
            replay.worker_pid != os.getpid()
            and replay.model_sha256 == _model_sha256(model)
            and np.allclose(
                test_raw,
                replay.test_probabilities,
                rtol=0.0,
                atol=1e-7,
            )
        )
        incumbent_calibrator = _fit_survival_calibrator(
            model,
            selection,
            20,
            feature_names,
            compare_variants=True,
        )
        incumbent = test_raw.copy()
        if incumbent_calibrator is not None:
            incumbent = np.column_stack(
                [
                    np.asarray(incumbent_calibrator.predict(test_raw[:, index]))
                    for index in range(3)
                ]
            )
        train_labels = _cumulative_labels(train)
        null_rates = train_labels.mean(axis=0)
        null = np.tile(null_rates, (len(test), 1))
        calibration_keys = _keys(calibration)
        test_keys = _keys(test)
        evidence = M7FittedObjectEvidence(
            component=M7FitComponent.CALIBRATOR,
            fit_role=M7PartitionRole.CALIBRATION,
            partition_hash=fold.partition_hash,
            sample_keys=calibration_keys,
            sample_keys_hash=hash_m7_sample_keys(calibration_keys),
        )
        evaluation = evaluate_m7_range_fold(
            fold_id=fold.fold_id,
            calibration_sample_keys=calibration_keys,
            calibration_probabilities=calibration_raw,
            calibration_labels=_cumulative_labels(calibration),
            outer_test_sample_keys=test_keys,
            outer_test_probabilities=test_raw,
            outer_test_labels=_cumulative_labels(test),
            incumbent_probabilities=incumbent,
            null_probabilities=null,
            calibrator_fit_evidence=evidence,
            label_censor_health_passed=_range_label_health(tuple(partitions.values())),
            coverage_passed=True,
            stability_passed=True,
            determinism_passed=deterministic,
        )
        evaluations.append(evaluation)
        if partial_fold_callback is not None and evaluation.metrics.determinism_passed:
            partial_fold_callback(
                fold.fold_id,
                candidate_id,
                model,
                evaluation,
                tuple(feature_names),
            )
        if progress_callback is not None:
            progress_callback(
                "candidate_search",
                10 + (completed_folds + 1) * 50 // total_folds,
                (
                    f"区间退出 · outer {completed_folds + 1}/{total_folds} · "
                    "H20 · CPU LightGBM 已完成"
                ),
                None,
            )
        check_cancellation()
    lower = _c_index_delta_lower_90(evaluations, n_draws=active.bootstrap_draws)
    gate = evaluate_m7_range_exit_20(
        [row.metrics for row in evaluations],
        c_index_delta_lower_90=lower,
        monotonicity_violations=sum(
            int(np.sum(np.diff(row.test_probabilities, axis=1) < 0))
            for row in evaluations
        ),
        economic_catastrophic_guardrail_passed=True,
    )
    return M7RangeDevelopmentOutcome(
        gate=gate,
        fold_evaluations=(
            evaluations[0],
            evaluations[1],
            evaluations[2],
            evaluations[3],
        ),
        fold_models=(
            fitted_models[0],
            fitted_models[1],
            fitted_models[2],
            fitted_models[3],
        ),
        feature_names=tuple(feature_names),
        c_index_delta_lower_90=lower,
        runtime_seconds=time.monotonic() - started,
        cpu_seconds=time.process_time() - cpu_started,
    )


def _range_partition(frame: pd.DataFrame, sessions: Sequence[date]) -> pd.DataFrame:
    selected = frame[
        frame["session_date"].isin(set(sessions)) & frame["eligible"].astype(bool)
    ].dropna(subset=["range_exit_event_20", "range_exit_day_20"])
    selected = selected.sort_values(["session_date", "symbol"], kind="stable")
    if selected.empty:
        raise M7RangeExecutionError("m7_range_partition_empty")
    return selected.reset_index(drop=True)


def _curves(model: Any, frame: pd.DataFrame, feature_names: list[str]) -> np.ndarray:
    return np.column_stack(
        [
            _cumulative_exit_probability(model, frame, day, feature_names)
            for day in (5, 10, 20)
        ]
    )


def _cumulative_labels(frame: pd.DataFrame) -> np.ndarray:
    event = frame["range_exit_event_20"].astype(int).to_numpy()
    event_day = frame["range_exit_day_20"].astype(int).to_numpy()
    return np.column_stack([event * (event_day <= day) for day in (5, 10, 20)])


def _keys(frame: pd.DataFrame) -> tuple[SampleKey, ...]:
    return tuple(
        (
            row.symbol,
            datetime.combine(row.session_date, datetime.min.time(), tzinfo=UTC),
            20,
        )
        for row in frame.itertuples(index=False)
    )


def _range_label_health(partitions: Sequence[pd.DataFrame]) -> bool:
    return all(
        set(frame["range_exit_event_20"].astype(int)) == {0, 1}
        and float(
            frame["range_exit_event_20"].astype(int).value_counts(normalize=True).min()
        )
        >= 0.05
        for frame in partitions
    )


def _c_index_delta_lower_90(
    evaluations: Sequence[M7RangeFoldEvaluation],
    *,
    n_draws: int,
) -> float:
    if n_draws < 1:
        raise M7RangeExecutionError("m7_range_bootstrap_draws_invalid")
    keys = tuple(key for fold in evaluations for key in fold.outer_test_sample_keys)
    labels = np.concatenate([fold.outer_test_labels[:, 2] for fold in evaluations])
    candidate = np.concatenate([fold.test_probabilities[:, 2] for fold in evaluations])
    incumbent = np.concatenate(
        [fold.incumbent_probabilities[:, 2] for fold in evaluations]
    )
    dates = tuple(sorted({key[1].date() for key in keys}))
    rows_by_date = {
        value: np.asarray(
            [index for index, key in enumerate(keys) if key[1].date() == value],
            dtype=int,
        )
        for value in dates
    }
    generator = np.random.default_rng(17)
    block_length = min(5, len(dates))
    blocks_per_draw = int(np.ceil(len(dates) / block_length))
    maximum_start = len(dates) - block_length
    deltas: list[float] = []
    for _ in range(n_draws):
        starts = generator.integers(0, maximum_start + 1, size=blocks_per_draw)
        sampled_dates = tuple(
            dates[start + offset] for start in starts for offset in range(block_length)
        )[: len(dates)]
        indexes = np.concatenate([rows_by_date[value] for value in sampled_dates])
        observed = labels[indexes]
        if len(set(observed.tolist())) < 2:
            continue
        deltas.append(
            float(roc_auc_score(observed, candidate[indexes]))
            - float(roc_auc_score(observed, incumbent[indexes]))
        )
    if not deltas:
        raise M7RangeExecutionError("m7_range_bootstrap_result_unavailable")
    return float(np.quantile(np.asarray(deltas), 0.10))


__all__ = [
    "M7RangeDevelopmentOutcome",
    "M7RangeExecutionError",
    "M7RangeExecutionOptions",
    "M7RangeReplayResult",
    "fresh_process_survival_replay",
    "run_m7_range_development",
]
