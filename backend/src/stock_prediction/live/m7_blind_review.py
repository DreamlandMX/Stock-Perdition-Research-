"""Assemble immutable M7 shadow predictions into blind gate decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import json
from pathlib import Path
from typing import Literal, cast

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.metrics import roc_auc_score

from stock_prediction.contracts.research import Horizon
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_audit import M7AuditLog
from stock_prediction.live.m7_blind import M7BlindWindowLedger
from stock_prediction.live.m7_executor import prepare_m7_feature_data
from stock_prediction.modeling.contracts import SampleKey
from stock_prediction.modeling.m7_blind_gates import (
    M7BlindDirectionEvidence,
    M7BlindGateDecision,
    M7BlindRangeEvidence,
    evaluate_m7_blind_direction,
    evaluate_m7_blind_range,
)
from stock_prediction.modeling.m7_economics import (
    M7EconomicDiagnostic,
    M7EconomicInputError,
    evaluate_m7_direction_economics,
)
from stock_prediction.modeling.report import deterministic_hash, file_sha256
from stock_prediction.operations.registry import LocalModelRegistry
from stock_prediction.universe.seed import SEED_UNIVERSE


_DIRECTION_KEY_COLUMNS = ("symbol", "anchor_session", "horizon")
_DIRECTION_CLASSES = ("down", "flat", "up")


@dataclass(frozen=True, slots=True)
class M7BlindReviewResult:
    window_id: str
    bundle_id: str
    decision_status: str
    report_path: Path
    report_hash: str


class M7BlindReviewRunner:
    def __init__(self, paths: LivePaths) -> None:
        self.paths = paths

    def run_ready(
        self,
        *,
        generated_at: datetime,
        n_bootstrap: int = 10_000,
    ) -> tuple[M7BlindReviewResult, ...]:
        _require_utc(generated_at)
        root = self.paths.models / "m7-blind"
        if not root.is_dir():
            return ()
        results: list[M7BlindReviewResult] = []
        for manifest_path in sorted(root.glob("*/manifest.json")):
            ledger = M7BlindWindowLedger(
                manifest_path.parent,
                audit=M7AuditLog(self.paths),
            )
            if ledger.status().state != "ready_for_review":
                continue
            predictions, labels, inference_sla = _load_blind_window(ledger)
            if ledger.manifest.bundle_id == "direction":
                economic = _direction_economics(
                    self.paths,
                    predictions=predictions,
                    generated_at=generated_at,
                )
                decision = evaluate_m7_direction_blind_frames(
                    predictions,
                    labels,
                    inference_sla_passed=inference_sla,
                    economic_guardrail_passed=(
                        economic.catastrophic_guardrail_passed
                        if economic is not None
                        else False
                    ),
                    n_bootstrap=n_bootstrap,
                )
                if economic is not None:
                    decision = _attach_economic_metrics(decision, economic)
            else:
                anchor_id = ledger.manifest.range_five_day_anchor_id
                anchor_hash, anchor_verified = resolve_verified_m7_range_anchor(
                    self.paths,
                    anchor_id,
                )
                decision = evaluate_m7_range_blind_frames(
                    predictions,
                    labels,
                    inference_sla_passed=inference_sla,
                    five_day_anchor_hash=anchor_hash,
                    five_day_anchor_verified=anchor_verified,
                    n_bootstrap=n_bootstrap,
                )
            report_path, report_hash = ledger.publish_review_report(
                decision=decision,
                generated_at=generated_at,
                evidence=_blind_review_evidence(
                    ledger,
                    predictions=predictions,
                    labels=labels,
                ),
            )
            results.append(
                M7BlindReviewResult(
                    window_id=ledger.manifest.window_id,
                    bundle_id=ledger.manifest.bundle_id,
                    decision_status=decision.status,
                    report_path=report_path,
                    report_hash=report_hash,
                )
            )
        return tuple(results)


def resolve_verified_m7_range_anchor(
    paths: LivePaths,
    anchor_id: str | None,
) -> tuple[str, bool]:
    """Resolve the current verified five-day release hash without ID-only trust."""

    fallback_hash = deterministic_hash({"range_five_day_anchor_id": anchor_id})
    if not anchor_id:
        return fallback_hash, False
    registry = LocalModelRegistry(paths.models / "registry")
    try:
        release = registry.verify_release(anchor_id)
        current = registry.snapshot("range_exit", 5).champion_version
    except RuntimeError:
        return fallback_hash, False
    valid = (
        current == anchor_id
        and release.task == "range_exit"
        and release.horizon == 5
        and release.promotion_eligible
    )
    return release.manifest_hash, valid


def evaluate_m7_direction_blind_frames(
    predictions: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    inference_sla_passed: bool,
    economic_guardrail_passed: bool,
    n_bootstrap: int = 10_000,
) -> M7BlindGateDecision:
    """Evaluate exact 77-anchor Seed100 direction evidence without mutation."""

    required_prediction = {
        *_DIRECTION_KEY_COLUMNS,
        "probability_down",
        "probability_flat",
        "probability_up",
        "incumbent_available",
        "incumbent_probability_down",
        "incumbent_probability_flat",
        "incumbent_probability_up",
    }
    required_labels = {
        *_DIRECTION_KEY_COLUMNS,
        "observed_label",
        "label_available",
    }
    if required_prediction - set(predictions.columns) or required_labels - set(
        labels.columns
    ):
        return _direction_rejection("direction_blind_schema_invalid")
    candidate = predictions.copy()
    observed = labels.copy()
    for frame in (candidate, observed):
        frame["anchor_session"] = pd.to_datetime(frame["anchor_session"]).dt.date
        frame["symbol"] = frame["symbol"].astype(str)
        frame["horizon"] = frame["horizon"].astype(int)
    if (
        candidate.duplicated(list(_DIRECTION_KEY_COLUMNS)).any()
        or observed.duplicated(list(_DIRECTION_KEY_COLUMNS)).any()
    ):
        return _direction_rejection("direction_blind_sample_keys_duplicate")
    try:
        joined = candidate.merge(
            observed,
            on=list(_DIRECTION_KEY_COLUMNS),
            how="inner",
            validate="one_to_one",
        )
    except pd.errors.MergeError:
        return _direction_rejection("direction_blind_sample_keys_mismatch")
    anchors = tuple(sorted(set(joined["anchor_session"])))
    exact_rows = 77 * len(SEED_UNIVERSE) * 3
    if (
        len(anchors) != 77
        or len(joined) != exact_rows
        or set(joined["symbol"]) != set(SEED_UNIVERSE)
        or set(joined["horizon"]) != {1, 5, 20}
        or len(joined) != len(candidate)
        or len(joined) != len(observed)
        or any(
            len(group) != len(SEED_UNIVERSE) * 3
            for _, group in joined.groupby("anchor_session", sort=False)
        )
    ):
        return _direction_rejection("direction_blind_seed100_coverage")
    if (
        not joined["label_available"].astype(bool).all()
        or not joined["observed_label"].isin(_DIRECTION_CLASSES).all()
    ):
        return _direction_rejection("direction_label_coverage")
    if not joined["incumbent_available"].astype(bool).all():
        return _direction_rejection("direction_incumbent_coverage")
    evidence: dict[int, M7BlindDirectionEvidence] = {}
    for horizon in (1, 5, 20):
        subset = joined[joined["horizon"] == horizon].sort_values(
            ["anchor_session", "symbol"], kind="stable"
        )
        candidate_values = subset[
            ["probability_down", "probability_flat", "probability_up"]
        ].to_numpy(dtype=np.float64)
        incumbent_values = subset[
            [
                "incumbent_probability_down",
                "incumbent_probability_flat",
                "incumbent_probability_up",
            ]
        ].to_numpy(dtype=np.float64)
        probability_valid = _direction_probabilities_valid(candidate_values) and (
            _direction_probabilities_valid(incumbent_values)
        )
        if not probability_valid:
            return _direction_rejection("direction_probability_validity")
        sample_keys = tuple(
            (str(row.symbol), row.anchor_session, horizon)
            for row in subset.itertuples(index=False)
        )
        evidence[horizon] = M7BlindDirectionEvidence(
            horizon=horizon,
            sample_keys=sample_keys,
            labels=tuple(subset["observed_label"]),
            candidate_probabilities=tuple(map(tuple, candidate_values)),
            incumbent_probabilities=tuple(map(tuple, incumbent_values)),
            coverage_passed=True,
            artifact_integrity_passed=True,
            probability_validity_passed=True,
            inference_sla_passed=inference_sla_passed,
        )
    decision = evaluate_m7_blind_direction(evidence, n_bootstrap=n_bootstrap)
    failures = list(decision.failed_gates)
    if not economic_guardrail_passed:
        failures.append("direction_economic_catastrophic_guardrail")
    failures = list(dict.fromkeys(failures))
    metrics = dict(decision.metrics)
    metrics.update(
        {
            "available_anchor_count": 77,
            "mature_sample_count": exact_rows,
            "economic_catastrophic_guardrail": economic_guardrail_passed,
        }
    )
    return decision.model_copy(
        update={
            "status": "rejected" if failures else "awaiting_manual_approval",
            "failed_gates": tuple(failures),
            "metrics": metrics,
        }
    )


def evaluate_m7_range_blind_frames(
    predictions: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    inference_sla_passed: bool,
    five_day_anchor_hash: str,
    five_day_anchor_verified: bool,
    n_bootstrap: int = 10_000,
) -> M7BlindGateDecision:
    required_prediction = {
        "symbol",
        "anchor_session",
        "horizon",
        "range_task_eligible",
        "probability_exit_by_5",
        "probability_exit_by_20",
        "incumbent_available",
        "incumbent_probability_exit_by_20",
    }
    required_labels = {
        "symbol",
        "anchor_session",
        "horizon",
        "observed_event",
        "event_day",
        "label_available",
    }
    if required_prediction - set(predictions.columns) or required_labels - set(
        labels.columns
    ):
        return _range_rejection("range_blind_schema_invalid")
    if not five_day_anchor_verified:
        return _range_rejection("range_five_day_anchor_unverified")
    candidate = predictions.copy()
    observed = labels.copy()
    for frame in (candidate, observed):
        frame["anchor_session"] = pd.to_datetime(frame["anchor_session"]).dt.date
        frame["symbol"] = frame["symbol"].astype(str)
        frame["horizon"] = frame["horizon"].astype(int)
    keys = ["symbol", "anchor_session", "horizon"]
    if candidate.duplicated(keys).any() or observed.duplicated(keys).any():
        return _range_rejection("range_blind_sample_keys_duplicate")
    try:
        joined = candidate.merge(
            observed,
            on=keys,
            how="inner",
            validate="one_to_one",
        )
    except pd.errors.MergeError:
        return _range_rejection("range_blind_sample_keys_mismatch")
    anchors = tuple(sorted(set(joined["anchor_session"])))
    exact_rows = 77 * len(SEED_UNIVERSE)
    if (
        len(anchors) != 77
        or len(joined) != exact_rows
        or len(joined) != len(candidate)
        or len(joined) != len(observed)
        or set(joined["symbol"]) != set(SEED_UNIVERSE)
        or set(joined["horizon"]) != {20}
        or any(
            len(group) != len(SEED_UNIVERSE)
            for _, group in joined.groupby("anchor_session", sort=False)
        )
    ):
        return _range_rejection("range_blind_seed100_coverage")
    eligible = joined[joined["range_task_eligible"].astype(bool)].copy()
    if eligible.empty or not eligible["label_available"].astype(bool).all():
        return _range_rejection("range_label_coverage")
    if not eligible["incumbent_available"].astype(bool).all():
        return _range_rejection("range_incumbent_coverage")
    observed_values = eligible["observed_event"].to_numpy(dtype=np.float64)
    candidate_5 = eligible["probability_exit_by_5"].to_numpy(dtype=np.float64)
    candidate_20 = eligible["probability_exit_by_20"].to_numpy(dtype=np.float64)
    incumbent_20 = eligible["incumbent_probability_exit_by_20"].to_numpy(
        dtype=np.float64
    )
    if (
        set(observed_values.tolist()) != {0.0, 1.0}
        or not _binary_probabilities_valid(candidate_5)
        or not _binary_probabilities_valid(candidate_20)
        or not _binary_probabilities_valid(incumbent_20)
        or np.any(candidate_5 > candidate_20 + 1e-12)
    ):
        return _range_rejection("range_probability_or_label_validity")
    candidate_c_index = float(roc_auc_score(observed_values, candidate_20))
    incumbent_c_index = float(roc_auc_score(observed_values, incumbent_20))
    lower = _range_c_index_delta_lower_90(
        eligible,
        observed=observed_values,
        candidate=candidate_20,
        incumbent=incumbent_20,
        n_draws=n_bootstrap,
    )
    sample_keys = tuple(
        (str(row.symbol), row.anchor_session, 20)
        for row in eligible.itertuples(index=False)
    )
    observed_labels = tuple(
        cast(Literal[0, 1], int(value)) for value in observed_values
    )
    evidence = M7BlindRangeEvidence(
        sample_keys=sample_keys,
        observed=observed_labels,
        candidate_probability_5d=tuple(float(value) for value in candidate_5),
        candidate_probability_20d=tuple(float(value) for value in candidate_20),
        incumbent_probability_20d=tuple(float(value) for value in incumbent_20),
        five_day_anchor_hash=five_day_anchor_hash,
        candidate_c_index=candidate_c_index,
        incumbent_c_index=incumbent_c_index,
        c_index_delta_lower_90=lower,
        coverage_passed=True,
        artifact_integrity_passed=True,
        inference_sla_passed=inference_sla_passed,
    )
    decision = evaluate_m7_blind_range(evidence)
    metrics = dict(decision.metrics)
    metrics.update(
        {
            "available_anchor_count": 77,
            "mature_sample_count": len(eligible),
            "seed100_prediction_row_count": exact_rows,
            "five_day_anchor_verified": True,
        }
    )
    return decision.model_copy(update={"metrics": metrics})


def _binary_probabilities_valid(values: np.ndarray) -> bool:
    return bool(
        values.ndim == 1
        and np.all(np.isfinite(values))
        and np.all(values >= 0.0)
        and np.all(values <= 1.0)
    )


def _range_c_index_delta_lower_90(
    frame: pd.DataFrame,
    *,
    observed: np.ndarray,
    candidate: np.ndarray,
    incumbent: np.ndarray,
    n_draws: int,
) -> float:
    if n_draws < 1:
        raise ValueError("m7_blind_range_bootstrap_draws_invalid")
    per_date: list[float] = []
    dates = frame["anchor_session"].to_numpy()
    for anchor in sorted(set(dates)):
        selected = dates == anchor
        date_labels = observed[selected]
        if len(set(date_labels.tolist())) < 2:
            continue
        per_date.append(
            float(roc_auc_score(date_labels, candidate[selected]))
            - float(roc_auc_score(date_labels, incumbent[selected]))
        )
    if not per_date:
        raise ValueError("m7_blind_range_bootstrap_unavailable")
    values = np.asarray(per_date, dtype=np.float64)
    generator = np.random.default_rng(17)
    draws = generator.choice(values, size=(n_draws, len(values)), replace=True).mean(
        axis=1
    )
    return float(np.quantile(draws, 0.10))


def _direction_probabilities_valid(values: np.ndarray) -> bool:
    return bool(
        values.ndim == 2
        and values.shape[1] == 3
        and np.all(np.isfinite(values))
        and np.all(values >= 0.0)
        and np.all(values <= 1.0)
        and np.allclose(values.sum(axis=1), 1.0, atol=1e-9)
    )


def _direction_rejection(reason: str) -> M7BlindGateDecision:
    return M7BlindGateDecision(
        bundle_id="direction",
        status="rejected",
        failed_gates=(reason,),
        metrics={
            "evidence_scope": "blind_complete",
            "available_anchor_count": 0,
            "mature_sample_count": 0,
            "economic_catastrophic_guardrail": False,
        },
        per_horizon={},
    )


def _range_rejection(reason: str) -> M7BlindGateDecision:
    return M7BlindGateDecision(
        bundle_id="range_exit_20",
        status="rejected",
        failed_gates=(reason,),
        metrics={
            "evidence_scope": "blind_complete",
            "available_anchor_count": 0,
            "mature_sample_count": 0,
            "five_day_anchor_verified": False,
        },
        per_horizon={},
    )


def _load_blind_window(
    ledger: M7BlindWindowLedger,
) -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    prediction_frames: list[pd.DataFrame] = []
    label_frames: list[pd.DataFrame] = []
    sla_results: list[bool] = []
    label_root = ledger.directory / "labels"
    for label_event_path in sorted(label_root.glob("*.json")):
        label_event = json.loads(label_event_path.read_text(encoding="utf-8"))
        anchor = date.fromisoformat(label_event_path.stem)
        prediction_event_path = (
            ledger.directory / "predictions" / f"{anchor.isoformat()}.json"
        )
        prediction_event = json.loads(prediction_event_path.read_text(encoding="utf-8"))
        artifact_root = ledger.directory / "artifacts" / anchor.isoformat()
        artifact_manifest_path = artifact_root / "manifest.json"
        artifact_manifest = json.loads(
            artifact_manifest_path.read_text(encoding="utf-8")
        )
        prediction_path = artifact_root / str(artifact_manifest["prediction_file"])
        explanation_path = artifact_root / str(artifact_manifest["explanation_file"])
        label_path = artifact_root / "labels.parquet"
        if (
            prediction_event.get("status") != "available"
            or prediction_event.get("prediction_artifact_hash")
            != file_sha256(artifact_manifest_path)
            or artifact_manifest.get("window_id") != ledger.manifest.window_id
            or artifact_manifest.get("package_sha256") != ledger.manifest.package_sha256
            or artifact_manifest.get("anchor_session") != anchor.isoformat()
            or file_sha256(prediction_path)
            != artifact_manifest.get("prediction_sha256")
            or file_sha256(explanation_path)
            != artifact_manifest.get("explanation_sha256")
            or file_sha256(label_path) != label_event.get("label_artifact_hash")
        ):
            raise RuntimeError("m7_blind_review_artifact_integrity_invalid")
        predictions = pq.read_table(prediction_path).to_pandas()
        labels = pq.read_table(label_path).to_pandas()
        sample_keys = [
            [str(row.symbol), anchor.isoformat(), int(row.horizon)]
            for row in labels.itertuples(index=False)
        ]
        if deterministic_hash(sample_keys) != label_event.get("sample_keys_hash"):
            raise RuntimeError("m7_blind_review_sample_keys_hash_invalid")
        prediction_frames.append(predictions)
        label_frames.append(labels)
        sla_results.append(artifact_manifest.get("inference_sla_passed") is True)
    if len(prediction_frames) != 77 or len(label_frames) != 77:
        raise RuntimeError("m7_blind_review_anchor_count_invalid")
    return (
        pd.concat(prediction_frames, ignore_index=True),
        pd.concat(label_frames, ignore_index=True),
        all(sla_results),
    )


def _direction_economics(
    paths: LivePaths,
    *,
    predictions: pd.DataFrame,
    generated_at: datetime,
) -> M7EconomicDiagnostic | None:
    if not predictions.get("incumbent_available", pd.Series(dtype=bool)).all():
        return None
    prepared = prepare_m7_feature_data(
        paths,
        development_cutoff=generated_at.date(),
    )
    candidate: dict[int, tuple[tuple[SampleKey, ...], object]] = {}
    incumbent: dict[int, tuple[tuple[SampleKey, ...], object]] = {}
    for horizon in (1, 5, 20):
        typed_horizon: Horizon = horizon
        subset = predictions[predictions["horizon"].astype(int) == horizon].copy()
        subset["anchor_session"] = pd.to_datetime(subset["anchor_session"]).dt.date
        subset.sort_values(["anchor_session", "symbol"], inplace=True, kind="stable")
        keys: tuple[SampleKey, ...] = tuple(
            (
                str(row.symbol),
                datetime.combine(row.anchor_session, datetime.min.time(), tzinfo=UTC),
                typed_horizon,
            )
            for row in subset.itertuples(index=False)
        )
        candidate[horizon] = (
            keys,
            subset[["probability_down", "probability_flat", "probability_up"]].to_numpy(
                dtype=np.float64
            ),
        )
        incumbent[horizon] = (
            keys,
            subset[
                [
                    "incumbent_probability_down",
                    "incumbent_probability_flat",
                    "incumbent_probability_up",
                ]
            ].to_numpy(dtype=np.float64),
        )
    try:
        return evaluate_m7_direction_economics(
            prepared.frame,
            candidate_probabilities=candidate,
            incumbent_probabilities=incumbent,
        )
    except M7EconomicInputError:
        return None


def _attach_economic_metrics(
    decision: M7BlindGateDecision,
    economic: M7EconomicDiagnostic,
) -> M7BlindGateDecision:
    per_horizon = {key: dict(value) for key, value in decision.per_horizon.items()}
    for horizon, row in economic.horizons.items():
        metrics = per_horizon.setdefault(str(horizon), {})
        metrics.update(
            {
                "economic_candidate_net_return": row.candidate.net_return,
                "economic_incumbent_net_return": row.incumbent.net_return,
                "economic_candidate_sharpe": row.candidate.annualized_sharpe,
                "economic_incumbent_sharpe": row.incumbent.annualized_sharpe,
                "economic_candidate_max_drawdown": row.candidate.maximum_drawdown,
                "economic_incumbent_max_drawdown": row.incumbent.maximum_drawdown,
                "economic_catastrophic": row.catastrophic,
            }
        )
    metrics = dict(decision.metrics)
    metrics["economic_catastrophic_horizon_count"] = economic.catastrophic_horizon_count
    return decision.model_copy(update={"metrics": metrics, "per_horizon": per_horizon})


def _blind_review_evidence(
    ledger: M7BlindWindowLedger,
    *,
    predictions: pd.DataFrame,
    labels: pd.DataFrame,
) -> dict[str, object]:
    joined = predictions.merge(
        labels,
        on=["symbol", "anchor_session", "horizon"],
        how="inner",
        validate="one_to_one",
    )
    if ledger.manifest.bundle_id == "direction":
        reliability = _direction_reliability_rows(joined)
    else:
        eligible = joined[joined["range_task_eligible"].astype(bool)].copy()
        reliability = _range_reliability_rows(eligible)
    return {
        "schema_version": "m7-blind-review-evidence-v1",
        "bundle_id": ledger.manifest.bundle_id,
        "window_id": ledger.manifest.window_id,
        "package_sha256": ledger.manifest.package_sha256,
        "incumbent_id": ledger.manifest.incumbent_id,
        "range_five_day_anchor_id": ledger.manifest.range_five_day_anchor_id,
        "prediction_row_count": len(predictions),
        "label_row_count": len(labels),
        "reliability": reliability,
        "non_causal_caveat": True,
    }


def _direction_reliability_rows(frame: pd.DataFrame) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for horizon in (1, 5, 20):
        subset = frame[frame["horizon"].astype(int) == horizon]
        for class_name in _DIRECTION_CLASSES:
            observed = (subset["observed_label"] == class_name).to_numpy(
                dtype=np.float64
            )
            for model, column in (
                ("candidate", f"probability_{class_name}"),
                ("incumbent", f"incumbent_probability_{class_name}"),
            ):
                rows.extend(
                    _fixed_reliability_bins(
                        subset[column].to_numpy(dtype=np.float64),
                        observed,
                        horizon=horizon,
                        class_name=class_name,
                        model=model,
                    )
                )
    return rows


def _range_reliability_rows(frame: pd.DataFrame) -> list[dict[str, object]]:
    observed = frame["observed_event"].to_numpy(dtype=np.float64)
    rows: list[dict[str, object]] = []
    for model, column in (
        ("candidate", "probability_exit_by_20"),
        ("incumbent", "incumbent_probability_exit_by_20"),
    ):
        rows.extend(
            _fixed_reliability_bins(
                frame[column].to_numpy(dtype=np.float64),
                observed,
                horizon=20,
                class_name="exit_by_20",
                model=model,
            )
        )
    return rows


def _fixed_reliability_bins(
    probabilities: np.ndarray,
    observed: np.ndarray,
    *,
    horizon: int,
    class_name: str,
    model: str,
) -> list[dict[str, object]]:
    if (
        probabilities.shape != observed.shape
        or not _binary_probabilities_valid(probabilities)
        or not np.all(np.isin(observed, (0.0, 1.0)))
    ):
        raise RuntimeError("m7_blind_reliability_input_invalid")
    assignments = np.minimum((probabilities * 10).astype(int), 9)
    rows: list[dict[str, object]] = []
    for index in range(10):
        selected = assignments == index
        count = int(np.sum(selected))
        if count == 0:
            continue
        rows.append(
            {
                "horizon": horizon,
                "class_name": class_name,
                "model": model,
                "bin_index": index,
                "lower_bound": index / 10,
                "upper_bound": (index + 1) / 10,
                "mean_prediction": float(np.mean(probabilities[selected])),
                "observed_frequency": float(np.mean(observed[selected])),
                "sample_count": count,
            }
        )
    return rows


def _require_utc(value: datetime) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError("m7_blind_review_generated_at_must_be_utc")


__all__ = [
    "M7BlindReviewResult",
    "M7BlindReviewRunner",
    "evaluate_m7_direction_blind_frames",
    "evaluate_m7_range_blind_frames",
    "resolve_verified_m7_range_anchor",
]
