"""Run the single approved M7 v9 quantile-CDF Seed-100 preflight."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
import hashlib
import json
import os
from pathlib import Path
from threading import Event, Thread
import time
from typing import Any, cast

import numpy as np
import pandas as pd
import psutil  # type: ignore[import-untyped]

from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V9,
)
from stock_prediction.live.m7_direction_executor import (
    _configuration_features,
    _direction_thresholds,
    _labels,
    _partition_frame,
    _transform_partitions,
)
from stock_prediction.modeling.contracts import SampleKey
from stock_prediction.modeling.m7_candidates import (
    M7_QUANTILE_FIT_POLICY_THREADPOOL,
    M7_QUANTILE_MAPPING_POLICY_SCIPY_COMPACT,
    M7_QUANTILE_SELECTION_POLICY_LOKY,
    fit_m7_quantile_cdf_candidate,
    predict_m7_quantile_cdf,
)
from stock_prediction.modeling.m7_direction_package import (
    load_m7_direction_tree_candidate,
    write_m7_direction_tree_candidate,
)
from stock_prediction.modeling.m7_metrics import compute_m7_training_weights
from stock_prediction.modeling.m7_recipe import registered_m7_candidate_matrix
from stock_prediction.modeling.report import file_sha256, write_immutable_json
from stock_prediction.modeling.splits import build_m7_fold_partitions


_CONFIGURATION_ID = "m7-quantile_cdf-01"
_HORIZON = 1
_MAX_WALL_SECONDS = 8 * 60
_MAX_MEMORY_BYTES = 24 * 1024**3


class _ResourceSampler:
    def __init__(self) -> None:
        self._process = psutil.Process()
        self._stop = Event()
        self._thread = Thread(target=self._sample_until_stopped, daemon=True)
        self.peak_tree_rss_bytes = 0
        self.peak_tree_private_bytes = 0
        self.peak_child_count = 0

    def start(self) -> None:
        self._sample()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._sample()

    def _sample_until_stopped(self) -> None:
        while not self._stop.wait(0.1):
            self._sample()

    def _sample(self) -> None:
        try:
            processes = (self._process, *self._process.children(recursive=True))
        except psutil.Error:
            processes = (self._process,)
        tree_rss_bytes = 0
        tree_private_bytes = 0
        sampled_child_count = 0
        for index, process in enumerate(processes):
            try:
                memory = process.memory_info()
                rss_bytes = int(memory.rss)
                try:
                    private_bytes = int(getattr(process.memory_full_info(), "private"))
                except (AttributeError, psutil.Error):
                    private_bytes = rss_bytes
            except psutil.Error:
                continue
            tree_rss_bytes += rss_bytes
            tree_private_bytes += private_bytes
            if index > 0:
                sampled_child_count += 1
        self.peak_tree_rss_bytes = max(
            self.peak_tree_rss_bytes,
            tree_rss_bytes,
        )
        self.peak_tree_private_bytes = max(
            self.peak_tree_private_bytes,
            tree_private_bytes,
        )
        self.peak_child_count = max(self.peak_child_count, sampled_child_count)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--output-run-id", required=True)
    return parser


def _resolved_child(root: Path, relative_path: str) -> Path:
    resolved_root = root.resolve()
    target = (resolved_root / Path(*relative_path.split("/"))).resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise ValueError("m7_v9_preflight_source_path_invalid")
    return target


def _array_sha256(values: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(values, dtype=np.float64)
    return hashlib.sha256(contiguous.tobytes()).hexdigest()


def _cpu_seconds(process: psutil.Process) -> float:
    times = process.cpu_times()
    return float(times.user + times.system)


def _load_source_dataset(
    live_root: Path,
    source_run_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], tuple[date, ...]]:
    dataset_root = (
        live_root / "models" / "runs" / source_run_id / "executor" / "dataset"
    )
    manifest_path = dataset_root / "dataset-manifest.json"
    eligibility_path = dataset_root / "eligibility.parquet"
    calendar_path = dataset_root / "calendar.json"
    if not all(
        path.is_file() for path in (manifest_path, eligibility_path, calendar_path)
    ):
        raise ValueError("m7_v9_preflight_source_dataset_unavailable")
    manifest = cast(dict[str, Any], json.loads(manifest_path.read_text("utf-8")))
    if (
        manifest.get("schema_version") != "m7-dataset-manifest-v1"
        or manifest.get("gate_passed") is not True
        or manifest.get("symbol_count") != 100
    ):
        raise ValueError("m7_v9_preflight_source_manifest_invalid")
    eligibility_reference = manifest.get("eligibility_artifact")
    if not isinstance(eligibility_reference, dict) or eligibility_reference.get(
        "sha256"
    ) != file_sha256(eligibility_path):
        raise ValueError("m7_v9_preflight_eligibility_hash_mismatch")
    eligibility = pd.read_parquet(eligibility_path)
    if eligibility.empty:
        raise ValueError("m7_v9_preflight_eligibility_empty")
    feature_reference = eligibility.iloc[0]["feature_reference"]
    if not isinstance(feature_reference, dict):
        raise ValueError("m7_v9_preflight_feature_reference_invalid")
    feature_path = _resolved_child(live_root, str(feature_reference["relative_path"]))
    if not feature_path.is_file() or str(feature_reference["sha256"]) != file_sha256(
        feature_path
    ):
        raise ValueError("m7_v9_preflight_feature_hash_mismatch")
    calendar = cast(dict[str, Any], json.loads(calendar_path.read_text("utf-8")))
    if calendar.get("schema_version") != "m7-calendar-evidence-v1":
        raise ValueError("m7_v9_preflight_calendar_invalid")
    sessions = tuple(date.fromisoformat(str(value)) for value in calendar["sessions"])
    frame = pd.read_parquet(feature_path)
    frame["session_date"] = pd.to_datetime(frame["session_date"]).dt.date
    return frame, eligibility, manifest, sessions


def _first_fold(
    eligibility: pd.DataFrame,
    manifest_hash: str,
    calendar_sessions: tuple[date, ...],
) -> Any:
    anchor_sessions = calendar_sessions[252:-20]
    first_test_position = len(anchor_sessions) - 4 * 126
    if first_test_position <= 0:
        raise ValueError("m7_v9_preflight_history_insufficient")
    keys: tuple[SampleKey, ...] = tuple(
        sorted(
            (
                (
                    str(row.symbol),
                    datetime.combine(
                        date.fromisoformat(str(row.anchor_session)),
                        datetime.min.time(),
                        tzinfo=UTC,
                    ),
                    int(row.horizon),
                )
                for row in eligibility.itertuples(index=False)
                if bool(row.eligible)
            ),
            key=lambda key: (key[1], key[0], key[2]),
        )
    )
    return build_m7_fold_partitions(
        fold_id="m7-outer-01",
        split_version="m7-purged-four-fold-v1",
        dataset_input_hash=manifest_hash,
        calendar_sessions=anchor_sessions,
        source_sample_keys=keys,
        outer_test_start=anchor_sessions[first_test_position],
        training_start=anchor_sessions[0],
    )


def _run(args: argparse.Namespace) -> dict[str, Any]:
    workspace = args.workspace_root.resolve()
    live_root = workspace / "var" / "live"
    output_root = (
        live_root / "models" / "runs" / args.output_run_id / "v9-quantile-preflight"
    )
    output_root.mkdir(parents=True, exist_ok=False)
    report_path = output_root / "preflight-report.json"
    artifact_root = output_root / "m7-outer-01" / "h1"
    artifact_root.mkdir(parents=True, exist_ok=False)
    staging_path = artifact_root / ".m7-quantile_cdf-01.staging.pkl"
    artifact_path = artifact_root / "m7-quantile_cdf-01.pkl"
    started_at = datetime.now(UTC)
    started = time.monotonic()
    candidate_started: float | None = None
    process = psutil.Process()
    cpu_started = _cpu_seconds(process)
    sampler = _ResourceSampler()
    sampler.start()
    report: dict[str, Any] = {
        "schema_version": "m7-v9-quantile-preflight-report-v1",
        "status": "failed",
        "recipe_id": CANDIDATE_RECIPE_SEED100_TARGETED_V9,
        "configuration_id": _CONFIGURATION_ID,
        "horizon": _HORIZON,
        "source_run_id": args.source_run_id,
        "output_run_id": args.output_run_id,
        "started_at": started_at.isoformat(),
        "promotion_performed": False,
        "phase_timings": {},
    }
    phase_timings = cast(dict[str, float], report["phase_timings"])
    try:
        phase_started = time.monotonic()
        frame, eligibility, manifest, calendar_sessions = _load_source_dataset(
            live_root,
            args.source_run_id,
        )
        phase_timings["source_loading_seconds"] = time.monotonic() - phase_started
        phase_started = time.monotonic()
        fold = _first_fold(
            eligibility,
            str(manifest["manifest_hash"]),
            calendar_sessions,
        )
        partitions = {
            "train": _partition_frame(frame, fold.train_sessions),
            "selection": _partition_frame(frame, fold.selection_sessions),
            "calibration": _partition_frame(frame, fold.calibration_sessions),
            "test": _partition_frame(frame, fold.outer_test_sessions),
        }
        matrix = registered_m7_candidate_matrix(
            recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V9
        )
        configuration = next(
            row
            for row in matrix.configurations
            if row.configuration_id == _CONFIGURATION_ID
        )
        if (
            configuration.family != "quantile_cdf"
            or configuration.parameters.get("max_iterations") != 400
            or configuration.parameters.get("quantile_mapping_policy")
            != M7_QUANTILE_MAPPING_POLICY_SCIPY_COMPACT
            or configuration.parameters.get("fit_execution_policy")
            != M7_QUANTILE_FIT_POLICY_THREADPOOL
            or configuration.parameters.get("fit_parallel_workers") != 4
            or configuration.parameters.get("selection_execution_policy")
            != M7_QUANTILE_SELECTION_POLICY_LOKY
            or configuration.parameters.get("selection_parallel_workers") != 8
        ):
            raise ValueError("m7_v9_preflight_configuration_mismatch")
        feature_names, categorical = _configuration_features(
            configuration,
            partitions["train"],
        )
        if categorical:
            raise ValueError("m7_v9_preflight_quantile_categorical_features")
        transformed = _transform_partitions(
            partitions,
            feature_names=feature_names,
            categorical_feature_names=categorical,
        )
        train_labels = _labels(partitions["train"], _HORIZON)
        weights = compute_m7_training_weights(
            symbols=tuple(partitions["train"]["symbol"]),
            sessions=tuple(partitions["train"]["session_date"]),
            labels=train_labels,
            training_cutoff=max(partitions["train"]["session_date"]),
        ).combined_weights
        phase_timings["fold_and_preparation_seconds"] = time.monotonic() - phase_started
        candidate_started = time.monotonic()
        phase_started = candidate_started
        candidate = fit_m7_quantile_cdf_candidate(
            configuration_id=_CONFIGURATION_ID,
            train_features=transformed["train"],
            train_returns=partitions["train"]["return_fwd_1"].astype(float),
            train_weights=weights,
            selection_features=transformed["selection"],
            selection_labels=_labels(partitions["selection"], _HORIZON),
            selection_direction_thresholds=_direction_thresholds(
                partitions["selection"], _HORIZON
            ),
            feature_names=feature_names,
            classes=("down", "flat", "up"),
            synthetic_iteration_cap=None,
            timeout_seconds=float(_MAX_WALL_SECONDS),
            recipe_id=CANDIDATE_RECIPE_SEED100_TARGETED_V9,
        )
        phase_timings["candidate_fit_and_selection_seconds"] = (
            time.monotonic() - phase_started
        )
        phase_started = time.monotonic()
        direct_predictions = {
            name: predict_m7_quantile_cdf(
                candidate,
                transformed[name],
                direction_thresholds=_direction_thresholds(source, _HORIZON),
            )
            for name, source in partitions.items()
            if name in {"selection", "calibration", "test"}
        }
        phase_timings["direct_prediction_seconds"] = time.monotonic() - phase_started
        phase_started = time.monotonic()
        artifact = write_m7_direction_tree_candidate(
            staging_path,
            candidate=candidate,
            training_frame=partitions["train"],
            artifact_id=f"{args.output_run_id}-{_CONFIGURATION_ID}",
        )
        if (
            artifact.schema_version != "m7-direction-tree-candidate-v3"
            or artifact.content_hash != file_sha256(staging_path)
        ):
            raise ValueError("m7_v9_preflight_artifact_hash_mismatch")
        phase_timings["artifact_staging_seconds"] = time.monotonic() - phase_started
        phase_started = time.monotonic()
        loaded = load_m7_direction_tree_candidate(staging_path)
        replay_predictions = {
            name: loaded.predict(source, horizon=_HORIZON)
            for name, source in partitions.items()
            if name in direct_predictions
        }
        exact_replay = {
            name: bool(
                np.array_equal(direct_predictions[name], replay_predictions[name])
            )
            for name in direct_predictions
        }
        if not all(exact_replay.values()):
            raise ValueError("m7_v9_preflight_selected_model_replay_mismatch")
        phase_timings["artifact_load_and_replay_seconds"] = (
            time.monotonic() - phase_started
        )
        phase_started = time.monotonic()
        os.replace(staging_path, artifact_path)
        published_hash = file_sha256(artifact_path)
        if published_hash != artifact.content_hash:
            raise ValueError("m7_v9_preflight_published_hash_mismatch")
        phase_timings["publication_and_hash_seconds"] = time.monotonic() - phase_started
        sampler.stop()
        elapsed_seconds = time.monotonic() - started
        candidate_wall_seconds = time.monotonic() - candidate_started
        if candidate_wall_seconds > _MAX_WALL_SECONDS:
            raise ValueError("m7_v9_preflight_wall_time_exceeded_after_publication")
        if (
            sampler.peak_tree_rss_bytes > _MAX_MEMORY_BYTES
            or sampler.peak_tree_private_bytes > _MAX_MEMORY_BYTES
        ):
            raise ValueError("m7_v9_preflight_memory_limit_exceeded")
        report.update(
            {
                "status": "passed",
                "finished_at": datetime.now(UTC).isoformat(),
                "wall_seconds": elapsed_seconds,
                "preparation_seconds": candidate_started - started,
                "candidate_wall_seconds": candidate_wall_seconds,
                "cpu_seconds": _cpu_seconds(process) - cpu_started,
                "peak_tree_rss_bytes": sampler.peak_tree_rss_bytes,
                "peak_tree_private_bytes": sampler.peak_tree_private_bytes,
                "peak_child_count": sampler.peak_child_count,
                "max_wall_seconds": _MAX_WALL_SECONDS,
                "max_memory_bytes": _MAX_MEMORY_BYTES,
                "candidate_matrix_hash": matrix.matrix_hash,
                "dataset_manifest_hash": manifest["manifest_hash"],
                "dataset_revision": manifest["dataset_revision"],
                "feature_set_version": manifest["feature_set_version"],
                "symbol_count": int(manifest["symbol_count"]),
                "selection_row_count": len(partitions["selection"]),
                "calibration_row_count": len(partitions["calibration"]),
                "test_row_count": len(partitions["test"]),
                "feature_count": len(feature_names),
                "registered_iterations": int(
                    configuration.parameters["max_iterations"]
                ),
                "synthetic_iteration_cap": None,
                "best_iteration": candidate.best_iteration,
                "selection_brier": candidate.selection_brier,
                "selection_brier_vector_length": len(candidate.selection_brier_vector),
                "selection_brier_vector_sha256": _array_sha256(
                    np.asarray(candidate.selection_brier_vector, dtype=np.float64)
                ),
                "quantile_mapping_policy": candidate.quantile_mapping_policy,
                "fit_execution_policy": candidate.fit_execution_policy,
                "fit_parallel_workers": candidate.fit_parallel_workers,
                "selection_execution_policy": candidate.selection_execution_policy,
                "selection_parallel_workers": candidate.selection_parallel_workers,
                "artifact": {
                    "relative_path": artifact_path.relative_to(live_root).as_posix(),
                    "schema_version": artifact.schema_version,
                    "sha256": published_hash,
                    "size_bytes": artifact_path.stat().st_size,
                },
                "exact_selected_model_replay": exact_replay,
                "prediction_hashes": {
                    name: _array_sha256(values)
                    for name, values in replay_predictions.items()
                },
            }
        )
    except Exception as error:
        if not sampler._stop.is_set():
            sampler.stop()
        report.update(
            {
                "finished_at": datetime.now(UTC).isoformat(),
                "wall_seconds": time.monotonic() - started,
                "preparation_seconds": (
                    None if candidate_started is None else candidate_started - started
                ),
                "candidate_wall_seconds": (
                    None
                    if candidate_started is None
                    else time.monotonic() - candidate_started
                ),
                "cpu_seconds": _cpu_seconds(process) - cpu_started,
                "peak_tree_rss_bytes": sampler.peak_tree_rss_bytes,
                "peak_tree_private_bytes": sampler.peak_tree_private_bytes,
                "peak_child_count": sampler.peak_child_count,
                "failure": str(error) or type(error).__name__,
            }
        )
    finally:
        if staging_path.is_file():
            staging_path.unlink()
        if report["status"] != "passed" and artifact_path.is_file():
            artifact_path.unlink()
        write_immutable_json(report_path, report)
    return report


def main() -> int:
    report = _run(_parser().parse_args())
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
