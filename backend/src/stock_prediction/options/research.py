"""Independent audit, paired research inputs and diagnostic model comparison."""

import argparse
from dataclasses import asdict
from collections import defaultdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import Field

from stock_prediction.contracts.options import (
    OPTION_SYMBOLS,
    OptionChainSnapshot,
    OptionModel,
    OptionMetric,
    OptionSide,
    OptionStockReturn,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.live.locking import _atomic_json
from stock_prediction.modeling.contracts import WalkForwardSpec
from stock_prediction.modeling.metrics import (
    multiclass_brier_score,
    multiclass_log_loss,
    mean_pinball_loss,
    empirical_interval_diagnostics,
)
from stock_prediction.modeling.m7_metrics import date_block_bootstrap_delta_brier
from stock_prediction.modeling.splits import build_walk_forward_manifest
from stock_prediction.research.calendar import TradingCalendarArtifact
from stock_prediction.research.dataset import (
    ResearchDataset,
    compute_research_dataset_input_hash,
)
from stock_prediction.options.store import OptionSnapshotStore
from stock_prediction.options.structure import (
    build_option_heatmap,
    build_option_structure_features,
)

OPTION_SOURCE_FIELDS = (
    "provider",
    "feed",
    "normalization_version",
    "dataset_revision",
    "dte_min",
    "dte_max",
    "strike_min_ratio",
    "strike_max_ratio",
)


class OptionTrainingBudget(OptionModel):
    seed: int = Field(ge=0)
    num_boost_round: int = Field(ge=1, le=500)
    learning_rate: float = Field(gt=0, le=1)
    num_leaves: int = Field(ge=2, le=64)
    min_data_in_leaf: int = Field(ge=1)
    num_threads: int = Field(default=2, ge=1, le=4)


class OptionPromotionRules(OptionModel):
    minimum_test_dates: int = Field(ge=40)
    minimum_outer_folds: int = Field(ge=2)
    minimum_positive_fold_fraction: float = Field(gt=0, le=1)
    maximum_log_loss_degradation: float = Field(ge=0)
    maximum_pinball_degradation: float = Field(ge=0)
    maximum_calibration_error: float = Field(ge=0, le=1)
    maximum_interval_coverage_error: float = Field(ge=0, le=1)
    maximum_symbol_brier_degradation: float = Field(ge=0)
    maximum_quality_only_gain_fraction: float = Field(ge=0, le=1)
    minimum_deployment_coverage: float = Field(gt=0, le=1)
    minimum_net_return_delta: float
    maximum_drawdown_degradation: float = Field(ge=0)
    minimum_cohort_dates: int = Field(ge=20)
    maximum_cohort_brier_degradation: float = Field(ge=0)
    require_all_nine_symbols: bool = True


class OptionExperimentProtocol(OptionModel):
    experiment_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    registered_at: UtcDatetime
    maximum_age_seconds: int = Field(gt=0)
    split_version: str
    split_spec: WalkForwardSpec
    license_reference: str = Field(min_length=1)
    time_evidence_reference: str = Field(min_length=1)
    baseline_model_reference: str = Field(min_length=1)
    training_budget_reference: str = Field(min_length=1)
    promotion_rules_reference: str = Field(min_length=1)
    feature_version: Literal["option-numeric-v1", "option-numeric-v2"] = (
        "option-numeric-v2"
    )
    training_budget: OptionTrainingBudget | None = None
    promotion_rules: OptionPromotionRules | None = None
    minimum_data_quality: float = Field(default=0, ge=0, le=1)
    event_cohorts: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    event_evidence_reference: str | None = None


def registered_option_protocol(payload: dict[str, Any]) -> OptionExperimentProtocol:
    # Registrations predating the version field used v1; new protocols default to v2.
    return OptionExperimentProtocol.model_validate_json(
        json.dumps({"feature_version": "option-numeric-v1", **payload})
    )


def audit_options(store: OptionSnapshotStore) -> dict[str, Any]:
    symbols: dict[str, Any] = {}
    blockers = {
        "historical_time_evidence_missing",
        "historical_license_unverified",
        "mature_matched_targets_missing",
        "experiment_protocol_not_registered",
    }
    for symbol in OPTION_SYMBOLS:
        snapshots = store.snapshots(symbol)
        snapshot = store.load(symbol)
        if snapshot is None:
            symbols[symbol] = {"status": "unavailable", "contracts": 0}
            continue
        features = build_option_structure_features(
            snapshot,
            as_of=snapshot.source_available_at,
            feature_version="option-numeric-v2",
            history=snapshots,
        )
        sides: tuple[OptionSide, ...] = ("call", "put")
        metrics: tuple[OptionMetric, ...] = ("oi", "volume", "iv", "gamma", "spread")
        coverage = {
            f"{side}_{metric}": build_option_heatmap(
                snapshot, metric=metric, side=side
            ).coverage.model_dump()
            for side in sides
            for metric in metrics
        }
        files = list((store.root / symbol).glob("*.json"))
        symbols[symbol] = {
            "snapshot_id": snapshot.snapshot_id,
            "captured_at": snapshot.captured_at.isoformat(),
            "batch_complete": snapshot.complete,
            "contracts": len(snapshot.contracts),
            "snapshot_count": len(snapshots),
            "snapshot_dates": sorted(
                {s.captured_at.date().isoformat() for s in snapshots}
            ),
            "stored_bytes": sum(p.stat().st_size for p in files),
            "field_coverage": coverage,
            "oi_date_known": sum(r.oi_date is not None for r in snapshot.contracts),
            "greeks_timestamp_known": sum(
                r.greeks_at is not None for r in snapshot.contracts
            ),
            "features": features.model_dump(mode="json"),
            "errors": snapshot.errors,
        }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "feature_version": "option-numeric-v2",
        "historical_experiment_ready": False,
        "published": False,
        "promoted": False,
        "horizons": [1, 5, 20],
        "blockers": sorted(blockers),
        "symbols": symbols,
    }


def prepare_option_experiment(
    dataset: ResearchDataset,
    calendar: TradingCalendarArtifact,
    protocol: OptionExperimentProtocol,
    store: OptionSnapshotStore,
    *,
    evaluated_at: datetime,
    stock_returns: tuple[OptionStockReturn, ...] = (),
) -> dict[str, Any]:
    if protocol.split_spec.embargo_sessions < 20:
        raise ValueError("option experiments require at least 20-session embargo")
    if dataset.input_hash != compute_research_dataset_input_hash(dataset.samples):
        raise ValueError("baseline input hash mismatch")
    samples, sidecar = [], []
    excluded: dict[str, int] = defaultdict(int)
    history_by_symbol = {symbol: store.snapshots(symbol) for symbol in OPTION_SYMBOLS}
    for row in dataset.samples:
        if (
            row.symbol not in OPTION_SYMBOLS
            or row.label_end_at > evaluated_at
            or not row.target.resolved
        ):
            excluded["unsupported_or_immature_target"] += 1
            continue
        if row.data_quality_score < protocol.minimum_data_quality:
            excluded["stock_data_quality_below_registered_minimum"] += 1
            continue
        candidates = [
            s
            for s in history_by_symbol[row.symbol]
            if s.complete and s.source_available_at <= row.as_of
        ]
        snapshot = max(candidates, key=store._market_key, default=None)
        if snapshot is None:
            excluded["no_available_snapshot"] += 1
            continue
        market_at = (
            snapshot.market_as_of or snapshot.underlying_at or snapshot.captured_at
        )
        if (row.as_of - market_at).total_seconds() > protocol.maximum_age_seconds:
            excluded["snapshot_too_old"] += 1
            continue
        if (
            snapshot.time_evidence != "historical_verified"
            and snapshot.captured_at > row.as_of
        ):
            excluded["historical_availability_unproven"] += 1
            continue
        features = build_option_structure_features(
            snapshot,
            as_of=row.as_of,
            feature_version=protocol.feature_version,
            history=tuple(
                s
                for s in history_by_symbol[row.symbol]
                if s.source_available_at <= row.as_of
            ),
            stock_returns=stock_returns,
        )
        if not features.available:
            for reason in features.reasons:
                excluded[reason] += 1
            continue
        samples.append(row)
        sidecar.append(
            {
                "symbol": row.symbol,
                "as_of": row.as_of.isoformat(),
                "horizon": row.horizon,
                "snapshot_id": snapshot.snapshot_id,
                "stock_features": row.numeric_features,
                "option_features": features.values,
                "option_source": {
                    **{name: getattr(snapshot, name) for name in OPTION_SOURCE_FIELDS},
                    "time_evidence": snapshot.time_evidence,
                    "source_available_at": snapshot.source_available_at.isoformat(),
                    "market_as_of": market_at.isoformat(),
                },
            }
        )
    if not samples:
        return {
            "status": "blocked",
            "excluded": dict(excluded),
            "matched_samples": 0,
            "promoted": False,
        }
    horizons = {row.horizon for row in samples}
    if horizons != {1, 5, 20}:
        raise ValueError("matched experiment must include 1/5/20 horizons")
    input_hash = compute_research_dataset_input_hash(samples)
    matched = dataset.model_copy(
        update={
            "samples": tuple(samples),
            "sample_count": len(samples),
            "input_hash": input_hash,
            "dataset_id": "research-" + input_hash[:24],
        }
    )
    manifest = build_walk_forward_manifest(
        matched, calendar, protocol.split_spec, protocol.split_version
    )
    if protocol.registered_at > evaluated_at:
        raise ValueError("protocol registration is in the future")
    option_hash = hashlib.sha256(
        json.dumps(sidecar, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "status": "prepared_untrained",
        "experiment": protocol.model_dump(mode="json"),
        "baseline_original_input_hash": dataset.input_hash,
        "matched_input_hash": input_hash,
        "option_input_hash": option_hash,
        "stock_returns_input_hash": hashlib.sha256(
            json.dumps(
                [r.model_dump(mode="json") for r in stock_returns], sort_keys=True
            ).encode()
        ).hexdigest(),
        "matched_samples": len(samples),
        "original_sample_count": dataset.sample_count,
        "deployment_coverage": len(samples) / dataset.sample_count,
        "excluded": dict(excluded),
        "stock_baseline_and_candidate_share_samples": True,
        "split_manifest": manifest.model_dump(mode="json"),
        "feature_sidecar": sidecar,
        "matched_dataset": matched.model_dump(mode="json"),
        "promoted": False,
    }


def compare_predictions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("prediction comparison requires paired rows")
    keys: set[tuple[str, str, int]] = set()
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (row["symbol"], row["as_of"], row["horizon"])
        if key in keys:
            raise ValueError("duplicate prediction sample")
        keys.add(key)
        if row["symbol"] not in OPTION_SYMBOLS or row["horizon"] not in (1, 5, 20):
            raise ValueError("unsupported prediction sample")
        for model in ("baseline", "candidate"):
            quantiles = row[f"{model}_quantiles"]
            if (
                len(quantiles) != 3
                or not np.all(np.isfinite(quantiles))
                or quantiles != sorted(quantiles)
            ):
                raise ValueError("invalid or crossing quantiles")
        groups[f"{row['horizon']}:{row['fold_id']}"].append(row)
    reports = {}
    for group_key, group in groups.items():
        report: dict[str, Any] = {
            "samples": len(group),
            "symbols": sorted({r["symbol"] for r in group}),
            "dates": len({r["as_of"][:10] for r in group}),
        }
        labels, returns = [r["label"] for r in group], [r["return"] for r in group]
        for model in ("baseline", "candidate"):
            probs = [r[f"{model}_probabilities"] for r in group]
            q = np.asarray([r[f"{model}_quantiles"] for r in group])
            report[model] = {
                "brier": multiclass_brier_score(
                    labels, probs, classes=("up", "flat", "down")
                ),
                "log_loss": multiclass_log_loss(
                    labels, probs, classes=("up", "flat", "down")
                ),
                "pinball": {
                    str(tau): mean_pinball_loss(
                        returns, q[:, index].tolist(), quantile=tau
                    )
                    for index, tau in enumerate((0.1, 0.5, 0.9))
                },
                "interval": empirical_interval_diagnostics(
                    returns,
                    lower=q[:, 0].tolist(),
                    upper=q[:, 2].tolist(),
                    target_coverage=0.8,
                ).model_dump(),
            }
        block = max(20, group[0]["horizon"])
        if report["dates"] >= block * 2:
            report["date_block_uncertainty"] = asdict(
                date_block_bootstrap_delta_brier(
                    sample_keys=[
                        (
                            r["symbol"],
                            datetime.fromisoformat(
                                r["as_of"].replace("Z", "+00:00")
                            ).date(),
                            r["horizon"],
                        )
                        for r in group
                    ],
                    labels=labels,
                    candidate_probabilities=[
                        r["candidate_probabilities"] for r in group
                    ],
                    incumbent_probabilities=[
                        r["baseline_probabilities"] for r in group
                    ],
                    classes=("up", "flat", "down"),
                    block_length=block,
                )
            )
        else:
            report["date_block_uncertainty"] = {"status": "insufficient_time_blocks"}
        reports[group_key] = report
    return {
        "evidence_status": "diagnostic_supplied_predictions_not_release_evidence",
        "classes": ["up", "flat", "down"],
        "groups": reports,
        "promoted": False,
        "published": False,
        "economic_evaluation": "not_evaluated_requires_executable_prices_costs_and_strategy",
        "release_requirements": [
            "frozen_data_and_protocol",
            "matched_baseline_and_ablation",
            "calibration_and_stability",
            "time_block_uncertainty",
            "registered_promotion_rules",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Independent option research; no training or publication side effects"
    )
    parser.add_argument("--root", type=Path, default=Path("var/options"))
    parser.add_argument("--output", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit")
    importer = sub.add_parser("import-snapshot")
    importer.add_argument("snapshot", type=Path)
    prep = sub.add_parser("prepare")
    prep.add_argument("--dataset", type=Path, required=True)
    prep.add_argument("--calendar", type=Path, required=True)
    prep.add_argument(
        "--protocol",
        type=Path,
        required=True,
        help='OptionExperimentProtocol JSON; default "feature_version": "option-numeric-v2"; explicit v1 remains supported',
    )
    prep.add_argument(
        "--stock-returns",
        type=Path,
        help="Canonical JSON OptionStockReturn rows with proven source times",
    )
    compare = sub.add_parser("compare")
    compare.add_argument("predictions", type=Path)
    args = parser.parse_args()
    store = OptionSnapshotStore(args.root)
    if args.command == "audit":
        report = audit_options(store)
    elif args.command == "import-snapshot":
        snapshot = OptionChainSnapshot.model_validate_json(
            args.snapshot.read_text(encoding="utf-8")
        )
        path = store.save(
            snapshot,
            {
                "import_file_sha256": hashlib.sha256(
                    args.snapshot.read_bytes()
                ).hexdigest()
            },
        )
        report = {
            "snapshot_id": snapshot.snapshot_id,
            "path": str(path),
            "published": False,
        }
    elif args.command == "prepare":
        report = prepare_option_experiment(
            ResearchDataset.model_validate_json(
                args.dataset.read_text(encoding="utf-8")
            ),
            TradingCalendarArtifact.model_validate_json(
                args.calendar.read_text(encoding="utf-8")
            ),
            OptionExperimentProtocol.model_validate_json(
                args.protocol.read_text(encoding="utf-8")
            ),
            store,
            evaluated_at=datetime.now(UTC),
            stock_returns=tuple(
                OptionStockReturn.model_validate_json(json.dumps(row))
                for row in json.loads(args.stock_returns.read_text(encoding="utf-8"))
            )
            if args.stock_returns
            else (),
        )
    else:
        report = compare_predictions(
            json.loads(args.predictions.read_text(encoding="utf-8"))
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(args.output, report)
    if args.command == "audit":
        exported = [
            value["features"] | {"contracts": value["contracts"]}
            for value in report["symbols"].values()
            if "features" in value
        ]
        pd.DataFrame(exported).to_parquet(
            args.output.with_suffix(".parquet"), index=False
        )
    print(
        json.dumps({"output": str(args.output), "published": False, "promoted": False})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
