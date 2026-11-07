"""Read-only option health and mature saved-forecast evaluation.

Outcome files pin paired forecast paths and SHA-256 hashes, prediction as_of,
and explicit label_start_at (for example next-open entry). Forecasts may be
generated after their stock cutoff, but must precede that entry and label end.
"""

import argparse
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time
import hashlib
import json
from math import isfinite
from pathlib import Path
import re
from typing import Any, Literal, Self, cast

from filelock import FileLock
from pydantic import Field, model_validator

from stock_prediction.contracts.options import (
    AUTOMATIC_OPTION_SYMBOLS,
    OPTION_SYMBOLS,
    OptionModel,
    OptionCollectionSummary,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.live.locking import _atomic_json
from stock_prediction.modeling.metrics import (
    empirical_interval_diagnostics,
    mean_pinball_loss,
    multiclass_brier_score,
    multiclass_log_loss,
)
from stock_prediction.options.analytics import build_option_analytics
from stock_prediction.options.store import OptionSnapshotStore
from stock_prediction.options.structure import (
    build_option_heatmap,
    build_option_structure_features,
)
from stock_prediction.options.research import OPTION_SOURCE_FIELDS


def verified_option_sessions(
    store: OptionSnapshotStore, at: datetime
) -> dict[date, str]:
    """Read the existing provider calendar without creating a database or fetching data."""
    import duckdb
    from stock_prediction.live.config import LivePaths

    paths = LivePaths.active(store.root.resolve().parent.parent)
    if not paths.ingestion.exists():
        return {}
    try:
        with duckdb.connect(str(paths.ingestion), read_only=True) as connection:
            rows = connection.execute(
                "SELECT session_date, day_type FROM trading_days WHERE observed_at <= ? ORDER BY session_date",
                [at],
            ).fetchall()
        return {
            day: kind
            for day, kind in rows
            if kind in ("WHOLE", "HALF", "MORNING", "AFTERNOON")
        }
    except (duckdb.Error, OSError, ValueError):
        return {}


def option_session_close(day: date, kind: str) -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.combine(
        day, time(16 if kind == "WHOLE" else 13), ZoneInfo("America/New_York")
    ).astimezone(UTC)


def build_option_collection(
    store: OptionSnapshotStore, *, evaluated_at: datetime, sessions: dict[date, str]
) -> OptionCollectionSummary:
    from zoneinfo import ZoneInfo
    from stock_prediction.contracts.options import (
        OptionAutomaticCollection,
        OptionCollectionCell,
    )

    day = evaluated_at.astimezone(ZoneInfo("America/New_York")).date()
    window = tuple(sorted(d for d in sessions if d <= day)[-10:])
    runs = []
    reasons = [] if window else ["verified_calendar_unavailable"]
    for path in (store.root / "monitor").glob("*.json"):
        if path.name.startswith("latest"):
            continue
        try:
            raw = _read(path).get("collection_run")
            if raw:
                run = OptionCollectionRun.model_validate(raw)
                if run.ended_at <= evaluated_at:
                    runs.append(run)
        except (OSError, ValueError, KeyError, TypeError):
            reasons.append("collection_record_invalid")
    automatic = max(
        (r for r in runs if r.automatic), key=lambda r: r.ended_at, default=None
    )
    # The pointer is also readable when historical monitor files have been archived.
    pointer = store.root / "monitor" / "latest-automatic-collection.json"
    if pointer.exists():
        try:
            run = OptionCollectionRun.model_validate(_read(pointer))
            if (
                run.ended_at <= evaluated_at
                and run.automatic
                and (automatic is None or run.ended_at > automatic.ended_at)
            ):
                automatic = run
                runs.append(run)
        except (OSError, ValueError, KeyError, TypeError):
            reasons.append("collection_record_invalid")
    cells = []
    accumulated: set[date] = set()
    for symbol in AUTOMATIC_OPTION_SYMBOLS:
        with store.read_scope():
            try:
                history = [
                    s
                    for s in store.snapshots(symbol)
                    if s.source_available_at <= evaluated_at
                    and (
                        s.time_evidence == "historical_verified"
                        or s.captured_at <= evaluated_at
                    )
                ]
            except (OSError, ValueError, KeyError, TypeError):
                history = []
                reasons.append("snapshot_artifacts_unreadable")
        accumulated.update(
            cast(datetime, s.market_as_of or s.underlying_at)
            .astimezone(ZoneInfo("America/New_York"))
            .date()
            for s in history
            if s.complete and (s.market_as_of or s.underlying_at) is not None
        )
        for session in window:
            snapshots = [
                s
                for s in history
                if (s.market_as_of or s.underlying_at or s.captured_at)
                .astimezone(ZoneInfo("America/New_York"))
                .date()
                == session
            ]
            saved = max(
                snapshots,
                key=lambda s: (s.complete, s.source_available_at),
                default=None,
            )
            attempt = max(
                (r for r in runs if r.trading_date == session and symbol in r.complete),
                key=lambda r: r.ended_at,
                default=None,
            )
            status: Literal[
                "complete", "partial", "skipped", "failed", "unavailable"
            ] = (
                "complete"
                if saved and saved.complete
                else "partial"
                if saved
                else "skipped"
                if attempt and attempt.status == "skipped"
                else "partial"
                if attempt and attempt.contracts.get(symbol, 0) > 0
                else "failed"
                if attempt
                else "unavailable"
            )
            cells.append(
                OptionCollectionCell(
                    symbol=symbol,
                    session=session,
                    status=status,
                    contracts=len(saved.contracts)
                    if saved
                    else attempt.contracts.get(symbol)
                    if attempt and attempt.status != "skipped"
                    else None,
                    snapshot_id=saved.snapshot_id if saved else None,
                    reasons=saved.errors
                    if saved
                    else (attempt.reason,)
                    if attempt and attempt.reason
                    else ("collection_record_missing",)
                    if attempt is None
                    else (),
                )
            )
    if automatic is None:
        reasons.append("automatic_collection_not_detected")
    return OptionCollectionSummary(
        sessions=window,
        cells=tuple(cells),
        latest_automatic=OptionAutomaticCollection(
            ended_at=automatic.ended_at,
            status=automatic.status,
            reason=automatic.reason,
        )
        if automatic
        else None,
        history_days=len(accumulated & sessions.keys()) if sessions else None,
        complete_symbols=sum(automatic.complete.values())
        if automatic and automatic.status != "skipped"
        else None,
        scheduler_loaded=None,
        reasons=tuple(dict.fromkeys((*reasons, "scheduler_runtime_unverified"))),
    )


CLASSES = ("up", "flat", "down")


class OptionCollectionRun(OptionModel):
    scheduled_at: UtcDatetime | None = None
    started_at: UtcDatetime
    ended_at: UtcDatetime
    trading_date: date
    is_half_day: bool | None = None
    automatic: bool = False
    status: Literal["completed", "partial", "failed", "skipped"]
    reason: str | None = None
    contracts: dict[str, int] = Field(default_factory=dict)
    complete: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_run(self) -> Self:
        if self.ended_at < self.started_at:
            raise ValueError("collection end precedes start")
        if set(self.contracts) != set(self.complete) or any(
            symbol not in OPTION_SYMBOLS or count < 0
            for symbol, count in self.contracts.items()
        ):
            raise ValueError("invalid collection symbol summary")
        return self


class OptionMonitorOutcome(OptionModel):
    symbol: str
    as_of: UtcDatetime
    label_start_at: UtcDatetime
    label_end_at: UtcDatetime
    source_available_at: UtcDatetime
    horizon: Literal[1, 5, 20]
    label: Literal["up", "flat", "down"]
    realized_return: float
    baseline_forecast_path: str
    candidate_forecast_path: str
    baseline_forecast_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    candidate_forecast_sha256: str = Field(pattern="^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def check_times(self) -> Self:
        if self.symbol not in OPTION_SYMBOLS:
            raise ValueError("unsupported outcome symbol")
        if (
            not self.as_of
            < self.label_start_at
            <= self.label_end_at
            <= self.source_available_at
        ):
            raise ValueError(
                "outcome requires explicit entry, mature label and availability ordering"
            )
        return self


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("monitor artifact must be a JSON object")
    return payload


def _time(value: Any) -> datetime:
    at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("monitor time requires timezone")
    return at.astimezone(UTC)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _reference(
    root: Path | None, at: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    state: dict[str, Any] = {
        "status": "untrained_historical_evidence_missing",
        "version": None,
        "promoted": False,
        "reasons": [],
    }
    if root is None:
        return {}, state
    pointer = root / "active.json"
    promoted = pointer.exists()
    if not promoted:
        pointer = root / "latest-run.json"
    if not pointer.exists():
        return {}, state
    try:
        identity = _read(pointer)
        experiment_id = identity["experiment_id"]
        if not isinstance(experiment_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", experiment_id
        ):
            raise ValueError("invalid monitor experiment identity")
        directory = root / experiment_id
        registration = _read(directory / "registration.json")
        if (
            not isinstance(registration.get("protocol", {}), dict)
            or not isinstance(registration.get("feature_distributions", {}), dict)
            or not isinstance(registration.get("option_source_definitions", []), list)
        ):
            raise ValueError("invalid monitor registration shape")
        report_path = directory / "evaluation.json"
        report = _read(report_path) if report_path.exists() else {}
        state.update(
            version=experiment_id, status=report.get("status", "registered_untrained")
        )
        if promoted:
            from stock_prediction.options.serving import validated_active_candidate
            from stock_prediction.options.experiment import OptionExperimentStore

            validated_active_candidate(OptionExperimentStore(root))
            models = _read(directory / "models.json")
            if _time(identity["promoted_at"]) > at:
                raise ValueError("promotion_not_available")
            if (
                _hash(report) != identity.get("report_hash")
                or _hash(models["bundles"]) != identity.get("model_bundle_hash")
                or report.get("registration") != registration
                or models.get("registration") != registration
            ):
                raise ValueError("promotion_integrity_failed")
            state.update(status="promoted", promoted=True)
        return registration, state
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        state["status"] = "artifact_unavailable"
        state["reasons"] = [str(error)]
        return {}, state


def _drift(
    values: dict[str, float | None], registration: dict[str, Any]
) -> dict[str, Any]:
    references = registration.get("feature_distributions", {})
    if not isinstance(references, dict) or not references:
        return {
            "status": "reference_missing",
            "features": {},
            "method": "registered_train_mean_std_and_range_v1",
        }
    features = {}
    for name, reference in references.items():
        canonical = name.removeprefix("option__")
        if canonical not in values or not isinstance(reference, dict):
            continue
        observed = values[canonical]
        entry: dict[str, Any] = {
            "value": observed,
            "z_score": None,
            "outside_train_range": None,
            "status": "current_value_missing",
        }
        if observed is not None:
            try:
                mean, std = float(reference["mean"]), float(reference["std"])
                low, high = float(reference["minimum"]), float(reference["maximum"])
                if (
                    not all(isfinite(v) for v in (mean, std, low, high))
                    or std < 0
                    or low > high
                ):
                    raise ValueError("invalid distribution")
                z = (observed - mean) / std if std > 0 else None
                outside = not low <= observed <= high
                flagged = (
                    outside
                    or (z is not None and abs(z) >= 3)
                    or (std == 0 and observed != mean)
                )
                entry.update(
                    z_score=z if z is not None and isfinite(z) else None,
                    outside_train_range=outside,
                    status="drift_detected" if flagged else "within_reference",
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                entry["status"] = "invalid_reference"
        features[canonical] = entry
    statuses = {f["status"] for f in features.values()}
    return {
        "status": "drift_detected"
        if "drift_detected" in statuses
        else "available"
        if statuses == {"within_reference"}
        else "partial"
        if features
        else "reference_missing",
        "features": features,
        "method": "registered_train_mean_std_and_range_v1",
        "z_score_threshold": 3,
    }


def _forecast_counts(root: Path | None, symbol: str, at: datetime) -> dict[str, Any]:
    statuses: Counter[str] = Counter()
    invalid = 0
    if root is not None:
        for path in (root / "forecasts" / symbol).glob("*.json"):
            try:
                forecast = _read(path)
                if forecast.get("symbol") != symbol or forecast.get("status") not in (
                    "enhanced",
                    "baseline_fallback",
                    "refused",
                    "research_only",
                ):
                    raise ValueError("invalid saved forecast identity/status")
                if _time(forecast["generated_at"]) <= at:
                    statuses[forecast["status"]] += 1
            except (OSError, KeyError, TypeError, ValueError):
                invalid += 1
    count = sum(statuses.values())
    return {
        "saved_forecasts": count,
        "statuses": dict(statuses),
        "invalid_records": invalid,
        "refusal_rate": statuses["refused"] / count if count else None,
        "fallback_rate": statuses["baseline_fallback"] / count if count else None,
    }


def _saved_prediction(
    path: Path, expected_hash: str, row: OptionMonitorOutcome, model: str, at: datetime
) -> dict[str, Any]:
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError("forecast_hash_mismatch")
    forecast = json.loads(content)
    if (
        not isinstance(forecast, dict)
        or forecast.get("symbol") != row.symbol
        or _time(forecast.get("stock_cutoff")) != row.as_of
    ):
        raise ValueError("forecast_identity_or_cutoff_mismatch")
    generated = _time(forecast.get("generated_at"))
    if generated > row.label_start_at:
        raise ValueError("forecast_after_entry")
    if generated > at or generated < row.as_of:
        raise ValueError("forecast_time_unavailable")
    if model == "candidate":
        if (
            forecast.get("status") != "enhanced"
            or forecast.get("model_source") != "stock_plus_options_numeric"
            or not forecast.get("input_hash")
            or not forecast.get("model_bundle_hash")
        ):
            raise ValueError("enhanced_forecast_evidence_missing")
        if _time(forecast.get("option_cutoff")) > row.as_of:
            raise ValueError("future_forecast_inputs")
    elif forecast.get("model_source") != "stock_baseline" or forecast.get("status") in (
        "refused",
        "research_only",
    ):
        raise ValueError("baseline_forecast_missing_or_refused")
    if any(
        _time(forecast[key]) > row.as_of
        for key in (
            "stock_source_available_at",
            "option_source_available_at",
            "max_source_available_at",
        )
        if forecast.get(key) is not None
    ):
        raise ValueError("future_forecast_inputs")
    horizons = [
        h for h in forecast.get("horizons", []) if h.get("horizon_days") == row.horizon
    ]
    if len(horizons) != 1:
        raise ValueError("saved_forecast_horizon_missing")
    horizon = horizons[0]
    probabilities = horizon.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(CLASSES):
        raise ValueError("calibrated_probabilities_missing")
    p = [float(probabilities[c]) for c in CLASSES]
    q = [float(horizon[name]) for name in ("q10", "q50", "q90")]
    if (
        not all(isfinite(v) for v in (*p, *q))
        or any(v < 0 or v > 1 for v in p)
        or abs(sum(p) - 1) > 1e-6
        or q != sorted(q)
    ):
        raise ValueError("invalid_saved_forecast_values")
    return {"probabilities": p, "quantiles": q}


def _outcomes(path: Path | None, at: datetime) -> dict[str, Any]:
    empty = {
        str(h): {"status": "mature_samples_missing", "samples": 0} for h in (1, 5, 20)
    }
    if path is None or not path.exists():
        return {
            "status": "missing",
            "horizons": empty,
            "excluded": {},
            "reason": "mature_paired_saved_forecasts_and_labels_missing",
        }
    content = path.read_bytes()
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("monitor outcomes must be a JSON object")
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise ValueError("monitor outcomes require a rows array")
    excluded: Counter[str] = Counter()
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    keys = set()
    for item in rows:
        try:
            row = OptionMonitorOutcome.model_validate(item)
            if row.label_end_at > at:
                excluded["immature_label"] += 1
                continue
            if row.source_available_at > at:
                excluded["label_source_not_available"] += 1
                continue
            key = row.symbol, row.as_of, row.horizon
            if key in keys:
                raise ValueError("duplicate_outcome_sample")
            predictions = {}
            for model in ("baseline", "candidate"):
                forecast_path = Path(getattr(row, f"{model}_forecast_path"))
                if not forecast_path.is_absolute():
                    forecast_path = path.parent / forecast_path
                predictions[model] = _saved_prediction(
                    forecast_path,
                    getattr(row, f"{model}_forecast_sha256"),
                    row,
                    model,
                    at,
                )
            keys.add(key)
            grouped[row.horizon].append(
                {
                    "label": row.label,
                    "return": row.realized_return,
                    "predictions": predictions,
                }
            )
        except (OSError, KeyError, TypeError, ValueError) as error:
            reason = (
                str(error)
                if str(error)
                in (
                    "forecast_hash_mismatch",
                    "forecast_identity_or_cutoff_mismatch",
                    "forecast_after_entry",
                    "forecast_time_unavailable",
                    "enhanced_forecast_evidence_missing",
                    "future_forecast_inputs",
                    "baseline_forecast_missing_or_refused",
                    "saved_forecast_horizon_missing",
                    "calibrated_probabilities_missing",
                    "invalid_saved_forecast_values",
                    "duplicate_outcome_sample",
                )
                else "invalid_outcome_or_forecast_evidence"
            )
            excluded[reason] += 1
    reports: dict[str, Any] = dict(empty)
    for horizon, group in grouped.items():
        labels, returns = [r["label"] for r in group], [r["return"] for r in group]
        metrics = {}
        for model in ("baseline", "candidate"):
            probs = [r["predictions"][model]["probabilities"] for r in group]
            quantiles = [r["predictions"][model]["quantiles"] for r in group]
            curves = {}
            errors = []
            for i, name in enumerate(CLASSES):
                bins = []
                weighted_error = 0.0
                for index in range(10):
                    eligible = [
                        j
                        for j, p in enumerate(probs)
                        if index / 10 <= p[i]
                        and (p[i] < (index + 1) / 10 or index == 9 and p[i] == 1)
                    ]
                    mean = (
                        sum(probs[j][i] for j in eligible) / len(eligible)
                        if eligible
                        else None
                    )
                    observed = (
                        sum(labels[j] == name for j in eligible) / len(eligible)
                        if eligible
                        else None
                    )
                    if mean is not None and observed is not None:
                        weighted_error += (
                            len(eligible) / len(group) * abs(mean - observed)
                        )
                    bins.append(
                        {
                            "bin": index,
                            "count": len(eligible),
                            "predicted": mean,
                            "observed": observed,
                        }
                    )
                errors.append(weighted_error)
                curves[name] = bins
            metrics[model] = {
                "brier": multiclass_brier_score(labels, probs, classes=CLASSES),
                "log_loss": multiclass_log_loss(labels, probs, classes=CLASSES),
                "macro_ece": sum(errors) / len(errors),
                "calibration_curves": curves,
                "pinball": {
                    str(tau): mean_pinball_loss(
                        returns, [q[i] for q in quantiles], quantile=tau
                    )
                    for i, tau in enumerate((0.1, 0.5, 0.9))
                },
                "interval": empirical_interval_diagnostics(
                    returns,
                    lower=[q[0] for q in quantiles],
                    upper=[q[2] for q in quantiles],
                    target_coverage=0.8,
                ).model_dump(),
            }
        reports[str(horizon)] = {
            "status": "evaluated",
            "samples": len(group),
            **metrics,
        }
    return {
        "status": "evaluated_mature_only" if grouped else "mature_samples_missing",
        "horizons": reports,
        "excluded": dict(excluded),
        "outcomes_sha256": hashlib.sha256(content).hexdigest(),
        "automatic_promotion": False,
    }


def build_option_monitor(
    store: OptionSnapshotStore,
    *,
    evaluated_at: datetime,
    maximum_age_seconds: int = 86400,
    experiments_root: Path | None = None,
    outcomes_path: Path | None = None,
) -> dict[str, Any]:
    at = _time(evaluated_at)
    if maximum_age_seconds <= 0:
        raise ValueError("maximum_age_seconds must be positive")
    registration, candidate = _reference(experiments_root, at)
    symbols = {}
    for symbol in OPTION_SYMBOLS:
        try:
            snapshot = store.load(symbol, as_of=at)
            attempt = store.latest_attempt(symbol)
        except (OSError, ValueError, KeyError, TypeError):
            symbols[symbol] = {
                "data_status": "unavailable",
                "prediction_status": "baseline_fallback",
                "reasons": ["snapshot_artifacts_unreadable"],
            }
            continue
        if attempt is not None and attempt.captured_at > at:
            attempt = None
        if (
            snapshot is None
            and attempt is not None
            and attempt.source_available_at <= at
        ):
            snapshot = attempt
        entry: dict[str, Any] = {
            "data_status": "unavailable",
            "prediction_status": "baseline_fallback",
            "snapshot_id": None,
            "field_coverage": {},
            "arrival_delay_seconds": None,
            "market_snapshot_age_seconds": None,
            "feature_drift": {"status": "reference_missing", "features": {}},
            "source_definition": {"status": "reference_missing"},
            "saved_prediction_counts": _forecast_counts(experiments_root, symbol, at),
            "latest_attempt": None,
            "reasons": [],
        }
        if attempt is not None:
            entry["latest_attempt"] = {
                "snapshot_id": attempt.snapshot_id,
                "captured_at": attempt.captured_at.isoformat(),
                "complete": attempt.complete,
                "errors": list(attempt.errors),
                "complete_snapshot_retained": not attempt.complete
                and snapshot is not None
                and snapshot.complete
                and attempt.snapshot_id != snapshot.snapshot_id,
            }
        if snapshot is None:
            entry["reasons"] = ["no_complete_snapshot_available"]
            symbols[symbol] = entry
            continue
        market_at = snapshot.market_as_of or snapshot.underlying_at
        age = (at - market_at).total_seconds() if market_at is not None else None
        analytics = build_option_analytics(
            snapshot, as_of=at, history=store.snapshots(symbol)
        )
        version = registration.get("protocol", {}).get(
            "feature_version",
            "option-numeric-v1" if registration else "option-numeric-v2",
        )
        features = build_option_structure_features(
            snapshot, as_of=at, feature_version=version
        )
        current_source = {
            name: getattr(snapshot, name) for name in OPTION_SOURCE_FIELDS
        }
        expected_sources = registration.get("option_source_definitions", [])
        source_status = (
            "unchanged"
            if current_source in expected_sources
            else "changed_requires_reevaluation"
            if expected_sources
            else "reference_missing"
        )
        entry.update(
            snapshot_id=snapshot.snapshot_id,
            source=current_source,
            arrival_delay_seconds=(
                snapshot.source_available_at - market_at
            ).total_seconds()
            if market_at
            else None,
            market_snapshot_age_seconds=age,
            captured_delay_seconds=(
                snapshot.captured_at - snapshot.source_available_at
            ).total_seconds(),
            data_status="stale"
            if age is not None and age > maximum_age_seconds
            else "partial"
            if age is None
            or not features.available
            or analytics.values["missing_field_ratio"] != 0
            else "available",
            source_definition={
                "status": source_status,
                "reference_role": "active" if candidate["promoted"] else "candidate",
                "current": current_source,
            },
            feature_drift=_drift(features.values, registration),
            reasons=list(analytics.reasons),
            field_evidence_coverage={
                name: value
                for name, value in analytics.values.items()
                if name.endswith("valid_ratio")
                or name in ("contract_coverage_ratio", "missing_field_ratio")
            },
        )
        for side in ("call", "put"):
            for metric in ("oi", "volume", "iv", "gamma", "spread"):
                entry["field_coverage"][f"{side}_{metric}"] = build_option_heatmap(
                    snapshot, metric=metric, side=side
                ).coverage.model_dump()
        if candidate["promoted"]:
            entry["prediction_status"] = (
                "enhanced_inputs_ready"
                if entry["data_status"] == "available" and source_status == "unchanged"
                else "baseline_fallback_inputs_degraded"
            )
        symbols[symbol] = entry
    collection: dict[str, Any] | None = None
    latest_collection = store.root / "monitor" / "latest-automatic-collection.json"
    if latest_collection.exists():
        try:
            run = OptionCollectionRun.model_validate_json(
                latest_collection.read_text(encoding="utf-8")
            )
            if run.automatic and run.ended_at <= at:
                collection = run.model_dump(mode="json")
        except (OSError, ValueError):
            collection = {
                "status": "unavailable",
                "reason": "collection_record_invalid",
            }
    return {
        "evaluated_at": at.isoformat(),
        "maximum_age_seconds": maximum_age_seconds,
        "candidate": candidate,
        "symbols": symbols,
        "outcome_evaluation": _outcomes(outcomes_path, at),
        "automatic_promotion": False,
        "automatic_collection": True,
        "latest_automatic_collection": collection,
        "automatic_training": False,
    }


def _saved_record_time(path: Path, key: str) -> datetime | None:
    try:
        return _time(_read(path)[key])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_option_monitor(report: dict[str, Any], store: OptionSnapshotStore) -> Path:
    at = _time(report["evaluated_at"])
    directory = store.root / "monitor"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{at.strftime('%Y%m%dT%H%M%S%fZ')}-{_hash(report)[:16]}.json"
    with FileLock(str(directory / ".writer.lock"), timeout=10):
        if target.exists() and _read(target) != report:
            raise ValueError("immutable monitor record conflict")
        if not target.exists():
            _atomic_json(target, report)
        latest = directory / "latest.json"
        previous_at = _saved_record_time(latest, "evaluated_at")
        if previous_at is None or previous_at <= at:
            _atomic_json(latest, report)
        collection = report.get("collection_run")
        if collection and collection["automatic"]:
            latest_collection = directory / "latest-automatic-collection.json"
            previous_at = _saved_record_time(latest_collection, "ended_at")
            if previous_at is None or previous_at <= _time(collection["ended_at"]):
                _atomic_json(latest_collection, collection)
    return target


def save_option_collection_run(
    run: OptionCollectionRun,
    store: OptionSnapshotStore,
    *,
    refresh_monitor: bool = True,
) -> Path:
    minimal = {
        "evaluated_at": run.ended_at.isoformat(),
        "collection_run": run.model_dump(mode="json"),
        "monitor_status": "pending",
    }
    # Persist the outcome before reading snapshots, which may be damaged or missing.
    saved = save_option_monitor(minimal, store)
    if not refresh_monitor:
        return saved
    try:
        report = build_option_monitor(
            store,
            evaluated_at=run.ended_at,
            experiments_root=store.root / "research" / "runs",
        )
        report["monitor_status"] = "available"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        report = minimal | {
            "monitor_status": "degraded",
            "monitor_reason": "snapshot_artifacts_unreadable",
        }
    report["collection_run"] = run.model_dump(mode="json")
    if run.automatic:
        report["latest_automatic_collection"] = report["collection_run"]
    return save_option_monitor(report, store)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("var/options"))
    parser.add_argument("--experiments-root", type=Path)
    parser.add_argument("--outcomes", type=Path)
    parser.add_argument("--maximum-age-seconds", type=int, default=86400)
    args = parser.parse_args()
    store = OptionSnapshotStore(args.root)
    report = build_option_monitor(
        store,
        evaluated_at=datetime.now(UTC),
        maximum_age_seconds=args.maximum_age_seconds,
        experiments_root=args.experiments_root,
        outcomes_path=args.outcomes,
    )
    print(
        json.dumps(
            {
                "record": str(save_option_monitor(report, store)),
                "automatic_promotion": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
