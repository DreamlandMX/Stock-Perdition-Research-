"""Explicit option candidate promotion, persisted inference, and stock fallback."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Literal, Self

from filelock import FileLock
from pydantic import Field, model_validator

from stock_prediction.contracts.options import (
    OPTION_SYMBOLS,
    OptionModel,
    OptionStockReturn,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.live.locking import _atomic_json
from stock_prediction.modeling.contracts import TaskName
from stock_prediction.options.experiment import (
    OptionExperimentStore,
    apply_feature_transform,
    assess_promotion,
    content_hash,
    predict_task_models,
)
from stock_prediction.options.research import (
    OPTION_SOURCE_FIELDS,
    registered_option_protocol,
)
from stock_prediction.options.store import OptionSnapshotStore
from stock_prediction.options.structure import build_option_structure_features


class OptionCandidateState(OptionModel):
    status: str
    version: str | None = None
    promoted: bool = False


class OptionForecastHorizon(OptionModel):
    horizon_days: Literal[1, 5, 20]
    q10: float
    q50: float
    q90: float
    probabilities: dict[str, float] | None = None
    interval_low: float | None = None
    interval_high: float | None = None
    interval_method: str | None = None
    target_coverage: float | None = Field(default=None, gt=0, lt=1)

    @model_validator(mode="after")
    def validate_forecast(self) -> Self:
        if self.q10 > self.q50 or self.q50 > self.q90:
            raise ValueError("crossing forecast quantiles")
        if (self.interval_low is None) != (self.interval_high is None) or (
            self.interval_low is not None
            and self.interval_high is not None
            and self.interval_low > self.interval_high
        ):
            raise ValueError("invalid calibrated interval")
        if self.probabilities is not None:
            if (
                set(self.probabilities) != {"up", "flat", "down"}
                or any(v < 0 or v > 1 for v in self.probabilities.values())
                or abs(sum(self.probabilities.values()) - 1) > 1e-6
            ):
                raise ValueError("invalid calibrated probabilities")
        return self


class OptionForecastReadModel(OptionModel):
    symbol: str
    status: Literal["enhanced", "baseline_fallback", "refused", "research_only"]
    model_source: str | None = None
    model_version: str | None = None
    reasons: tuple[str, ...] = ()
    candidate: OptionCandidateState
    option_snapshot_id: str | None = None
    option_cutoff: UtcDatetime | None = None
    stock_snapshot_id: str | None = None
    stock_cutoff: UtcDatetime | None = None
    horizons: tuple[OptionForecastHorizon, ...] = ()
    generated_at: UtcDatetime | None = None
    input_hash: str | None = None
    model_bundle_hash: str | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if len({h.horizon_days for h in self.horizons}) != len(self.horizons):
            raise ValueError("duplicate forecast horizon")
        if self.status == "enhanced":
            if (
                not self.candidate.promoted
                or self.model_version != self.candidate.version
                or self.model_source != "stock_plus_options_numeric"
                or not all(
                    (
                        self.input_hash,
                        self.model_bundle_hash,
                        self.generated_at,
                        self.stock_snapshot_id,
                        self.option_snapshot_id,
                        self.horizons,
                    )
                )
            ):
                raise ValueError(
                    "enhanced forecast requires promoted model and complete provenance"
                )
            if (
                self.option_cutoff is None
                or self.stock_cutoff is None
                or self.option_cutoff > self.stock_cutoff
            ):
                raise ValueError("enhanced forecast contains future option input")
        return self


class OptionInferenceInput(OptionModel):
    symbol: str
    as_of: UtcDatetime
    stock_snapshot_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    stock_model_version: str
    stock_feature_set_version: str
    stock_numeric_features: dict[str, float | None]
    stock_categorical_features: dict[str, str]
    stock_source_available_at: UtcDatetime
    stock_returns: tuple[OptionStockReturn, ...] = ()

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        if self.symbol not in OPTION_SYMBOLS:
            raise ValueError("unsupported option symbol")
        if self.stock_source_available_at > self.as_of:
            raise ValueError("future stock features")
        if any(
            r.symbol != self.symbol or r.source_available_at > self.as_of
            for r in self.stock_returns
        ):
            raise ValueError("future or mismatched realized return evidence")
        return self


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("option artifact must be a JSON object")
    return payload


def validated_active_candidate(
    store: OptionExperimentStore,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    active = _read(store.root / "active.json")
    directory = store.directory(active["experiment_id"])
    report, models, registration, consumed = (
        _read(directory / name)
        for name in (
            "evaluation.json",
            "models.json",
            "registration.json",
            "blind-test-consumed.json",
        )
    )
    protocol = registered_option_protocol(registration["protocol"])
    gates = assess_promotion(report, protocol.promotion_rules)
    allowed = {int(h) for h, gate in gates["horizons"].items() if gate["eligible"]}
    bundle_hash = content_hash(models["bundles"])
    if (
        content_hash(report) != active["report_hash"]
        or bundle_hash != active["model_bundle_hash"]
        or bundle_hash != consumed["model_bundle_hash"]
        or registration != report["registration"]
        or registration != models["registration"]
        or not gates["eligible"]
        or not set(active["horizons"]).issubset(allowed)
        or not active["horizons"]
    ):
        raise ValueError("active option candidate integrity or release gates failed")
    return active, report, models


def promote_option_candidate(
    store: OptionExperimentStore,
    experiment_id: str,
    *,
    expected_report_hash: str,
    at: datetime,
) -> dict[str, Any]:
    directory = store.directory(experiment_id)
    report = _read(directory / "evaluation.json")
    if content_hash(report) != expected_report_hash:
        raise ValueError("reviewed evaluation report hash mismatch")
    protocol = registered_option_protocol(report["registration"]["protocol"])
    gates = assess_promotion(report, protocol.promotion_rules)
    if not gates["eligible"]:
        raise ValueError("option candidate failed registered release gates")
    models = _read(directory / "models.json")
    registration = _read(directory / "registration.json")
    blind = _read(directory / "blind-test-consumed.json")
    bundle_hash = content_hash(models["bundles"])
    if (
        registration != report["registration"]
        or models["registration"] != registration
        or bundle_hash != report["model_bundle_hash"]
        or bundle_hash != blind["model_bundle_hash"]
    ):
        raise ValueError("candidate registration/model/consumed evidence mismatch")
    pointer = {
        "experiment_id": experiment_id,
        "report_hash": expected_report_hash,
        "model_bundle_hash": bundle_hash,
        "promoted_at": at.isoformat(),
        "horizons": [
            int(h) for h, gate in gates["horizons"].items() if gate["eligible"]
        ],
    }
    with FileLock(str(store.root / ".promotion.lock"), timeout=10):
        _atomic_json(directory / "promotion.json", pointer)
        _atomic_json(store.root / "active.json", pointer)
    return pointer


def candidate_state(store: OptionExperimentStore) -> OptionCandidateState:
    path = store.root / "active.json"
    if path.exists():
        active, _, _ = validated_active_candidate(store)
        return OptionCandidateState(
            status="promoted", version=active["experiment_id"], promoted=True
        )
    latest = store.root / "latest-run.json"
    if latest.exists():
        experiment_id = _read(latest)["experiment_id"]
        report = _read(store.directory(experiment_id) / "evaluation.json")
        return OptionCandidateState(status=report["status"], version=experiment_id)
    return OptionCandidateState(status="untrained_historical_evidence_missing")


def infer_option_candidate(
    input_row: OptionInferenceInput,
    snapshots: OptionSnapshotStore,
    store: OptionExperimentStore,
) -> OptionForecastReadModel:
    input_row = OptionInferenceInput.model_validate(input_row.model_dump())
    try:
        state = candidate_state(store)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        state = OptionCandidateState(status="candidate_integrity_failed")
    fallback = OptionForecastReadModel(
        symbol=input_row.symbol,
        status="baseline_fallback",
        model_source="stock_baseline",
        model_version=input_row.stock_model_version,
        candidate=state,
        stock_snapshot_id=input_row.stock_snapshot_id,
        stock_cutoff=input_row.as_of,
        reasons=("option_candidate_not_promoted",),
        generated_at=datetime.now(UTC),
    )
    if not state.promoted:
        return fallback
    try:
        active, report, model_bundle = validated_active_candidate(store)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return fallback.model_copy(
            update={"reasons": ("option_candidate_integrity_failed",)}
        )
    protocol = registered_option_protocol(report["registration"]["protocol"])
    if input_row.stock_feature_set_version != report["registration"].get(
        "stock_feature_set_version"
    ):
        return fallback.model_copy(
            update={"reasons": ("stock_feature_definition_changed",)}
        )
    snapshot = snapshots.load(input_row.symbol, as_of=input_row.as_of)
    if snapshot is None:
        return fallback.model_copy(
            update={"reasons": ("no_option_snapshot_available_at_stock_cutoff",)}
        )
    market_at = snapshot.market_as_of or snapshot.underlying_at or snapshot.captured_at
    if (input_row.as_of - market_at).total_seconds() > protocol.maximum_age_seconds:
        return fallback.model_copy(
            update={
                "reasons": ("option_snapshot_too_old",),
                "option_snapshot_id": snapshot.snapshot_id,
                "option_cutoff": snapshot.source_available_at,
            }
        )
    sources = report["registration"]["option_source_definitions"]
    if {name: getattr(snapshot, name) for name in OPTION_SOURCE_FIELDS} not in sources:
        return fallback.model_copy(
            update={
                "reasons": ("option_source_definition_changed_requires_reevaluation",)
            }
        )
    features = build_option_structure_features(
        snapshot,
        as_of=input_row.as_of,
        feature_version=protocol.feature_version,
        history=snapshots.snapshots(input_row.symbol),
        stock_returns=input_row.stock_returns,
    )
    if not features.available:
        return fallback.model_copy(
            update={
                "reasons": features.reasons,
                "option_snapshot_id": snapshot.snapshot_id,
                "option_cutoff": snapshot.source_available_at,
            }
        )
    horizons = []
    for horizon in active["horizons"]:
        eligible = [
            b
            for b in model_bundle["bundles"]
            if b["horizon"] == horizon
            and datetime.fromisoformat(b["selection_end_at"]) < input_row.as_of
        ]
        if not eligible:
            continue
        bundle = max(eligible, key=lambda b: b["selection_end_at"])["groups"]["E3"]
        row = {
            "stock": input_row.stock_numeric_features,
            "option": features.values,
            "categories": input_row.stock_categorical_features
            | {"symbol": input_row.symbol},
        }
        fitted = bundle["transform"]
        predictions = predict_task_models(
            bundle["models"],
            apply_feature_transform([row], fitted),
            fitted["feature_names"],
        )
        q = predictions[TaskName.CAPITAL_GAIN_RETURN]["quantiles"][0]
        p = predictions[TaskName.FORECAST_DIRECTION]["probabilities"][0]
        interval = predictions[TaskName.CAPITAL_GAIN_RETURN]["intervals"][0]
        horizons.append(
            OptionForecastHorizon(
                horizon_days=horizon,
                q10=q[0],
                q50=q[1],
                q90=q[2],
                probabilities=dict(zip(("up", "flat", "down"), p, strict=True)),
                interval_low=interval[0],
                interval_high=interval[1],
                interval_method="CQR-0.8",
                target_coverage=0.8,
            )
        )
    if not horizons:
        return fallback.model_copy(
            update={"reasons": ("no_promoted_model_precedes_inference_cutoff",)}
        )
    return OptionForecastReadModel(
        symbol=input_row.symbol,
        status="enhanced",
        model_source="stock_plus_options_numeric",
        model_version=active["experiment_id"],
        candidate=state,
        option_snapshot_id=snapshot.snapshot_id,
        option_cutoff=snapshot.source_available_at,
        stock_snapshot_id=input_row.stock_snapshot_id,
        stock_cutoff=input_row.as_of,
        horizons=tuple(horizons),
        generated_at=datetime.now(UTC),
        input_hash=content_hash(input_row.model_dump(mode="json")),
        model_bundle_hash=active["model_bundle_hash"],
    )


def save_option_inference(
    forecast: OptionForecastReadModel, store: OptionExperimentStore
) -> Path:
    if forecast.stock_snapshot_id is None:
        raise ValueError("inference must pin a stock snapshot")
    target = (
        store.root
        / "forecasts"
        / forecast.symbol
        / f"{forecast.stock_snapshot_id}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(target.parent / ".writer.lock"), timeout=10):
        if (
            target.exists()
            and OptionForecastReadModel.model_validate_json(
                target.read_text(encoding="utf-8")
            )
            != forecast
        ):
            raise ValueError("persisted inference conflict; retain existing evidence")
        payload = forecast.model_dump(mode="json")
        _atomic_json(target, payload)
        _atomic_json(
            target.with_suffix(".manifest.json"),
            {"forecast_hash": content_hash(payload)},
        )
    return target


def load_option_inference(
    symbol: str, stock_snapshot_id: str, store: OptionExperimentStore
) -> OptionForecastReadModel | None:
    store.directory(stock_snapshot_id)
    if symbol not in OPTION_SYMBOLS:
        return None
    path = store.root / "forecasts" / symbol / f"{stock_snapshot_id}.json"
    if not path.exists():
        return None
    forecast = OptionForecastReadModel.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    if forecast.symbol != symbol or forecast.stock_snapshot_id != stock_snapshot_id:
        raise ValueError("persisted forecast identity mismatch")
    if (
        content_hash(forecast.model_dump(mode="json"))
        != _read(path.with_suffix(".manifest.json"))["forecast_hash"]
    ):
        raise ValueError("persisted forecast content hash mismatch")
    if forecast.status == "enhanced":
        active, _, _ = validated_active_candidate(store)
        if (
            active["experiment_id"] != forecast.model_version
            or active["model_bundle_hash"] != forecast.model_bundle_hash
        ):
            return None
        if not {h.horizon_days for h in forecast.horizons}.issubset(
            set(active["horizons"])
        ):
            return None
    return forecast


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explicit reviewed promotion or saved-input option inference"
    )
    parser.add_argument("--root", type=Path, default=Path("var/options/research/runs"))
    sub = parser.add_subparsers(dest="command", required=True)
    promote = sub.add_parser("promote")
    promote.add_argument("experiment_id")
    promote.add_argument("--expected-report-hash", required=True)
    infer = sub.add_parser("infer")
    infer.add_argument("--input", type=Path, required=True)
    infer.add_argument("--snapshots-root", type=Path, default=Path("var/options"))
    args = parser.parse_args()
    store = OptionExperimentStore(args.root)
    if args.command == "promote":
        print(
            json.dumps(
                promote_option_candidate(
                    store,
                    args.experiment_id,
                    expected_report_hash=args.expected_report_hash,
                    at=datetime.now(UTC),
                )
            )
        )
    else:
        result = infer_option_candidate(
            OptionInferenceInput.model_validate_json(
                args.input.read_text(encoding="utf-8")
            ),
            OptionSnapshotStore(args.snapshots_root),
            store,
        )
        print(
            json.dumps(
                {
                    "status": result.status,
                    "output": str(save_option_inference(result, store)),
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
