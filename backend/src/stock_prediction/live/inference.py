"""Batch inference for a hash-verified live composite Champion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import pickle
from time import perf_counter
from typing import Any, Callable, Literal, cast

import numpy as np
import pandas as pd

from stock_prediction.contracts.research import Horizon, RegimeLabel
from stock_prediction.contracts.serving import (
    BreakoutProbabilities,
    CalibratedInterval,
    CalibrationEvidence,
    DirectionProbabilities,
    FeatureFamilyEvidence,
    ForecastAvailable,
    ForecastBundle,
    ForecastEvidence,
    HorizonForecast,
    PriceBand,
    PredictionReadiness,
    RangeExitHazard,
    RangeExitMetadata,
    ResolvedSnapshot,
    ReturnBasis,
    ReturnQuantiles,
    SimilarCaseQuantiles,
    SimilarCaseSummary,
    SimilarCaseView,
    ServingOverlay,
    ServingStatus,
)
from stock_prediction.live.quantiles import (
    EVALUATION_PIPELINE_VERSION,
    ordered_quantiles,
)

from stock_prediction.live.training import load_latest_training_report
from stock_prediction.live.config import LivePaths
from stock_prediction.modeling.confidence import ConfidenceLevel, RejectionReason
from stock_prediction.operations.registry import LocalModelRegistry
from stock_prediction.live.universe_scope import LiveUniverseSpec, resolve_live_universe

_HORIZONS: tuple[Horizon, ...] = (1, 5, 20)
_QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
_REGISTRY_TASK = "forecast_bundle"
_COMPONENT_TASKS: tuple[tuple[str, Horizon], ...] = (
    *(("forecast_direction", horizon) for horizon in _HORIZONS),
    *(("breakout", horizon) for horizon in _HORIZONS),
    *(("return_fwd", horizon) for horizon in _HORIZONS),
    *(("mfe", horizon) for horizon in _HORIZONS),
    *(("mae", horizon) for horizon in _HORIZONS),
    ("range_exit", 5),
    ("range_exit", 20),
)


@dataclass(frozen=True)
class ResearchTaskPrediction:
    """One task-level Champion evaluated against the current feature snapshot."""

    task: str
    horizon: Horizon
    symbol: str
    model_version: str
    calibration_version: str | None
    evaluation_run_id: str
    component_evaluated_at: datetime
    gate_metrics: dict[str, Any]
    reference_price: float
    reference_price_at: datetime
    probabilities: dict[int, float] | None = None
    quantiles: ReturnQuantiles | None = None
    cqr_adjustment: float | None = None
    range_exit_probability: float | None = None
    sample_abstain_reasons: tuple[RejectionReason, ...] = ()


class LiveInferenceEngine:
    def __init__(
        self,
        paths: LivePaths,
        *,
        universe: LiveUniverseSpec | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self.paths = paths
        self.universe = universe or resolve_live_universe(paths)
        self.registry = LocalModelRegistry(paths.models / "registry")
        self.progress = progress

    def champion(self) -> tuple[str, str, dict[str, Any]] | None:
        snapshot = self.registry.snapshot(_REGISTRY_TASK, 20)
        if snapshot.champion_version is None:
            return None
        release = self.registry.verify_release(snapshot.champion_version)
        if release.calibration_version is None:
            return None
        artifact = next(
            (
                item
                for item in release.artifacts
                if item.relative_path.endswith("production-package.pkl")
            ),
            None,
        )
        if artifact is None:
            return None
        path = (
            self.paths.models
            / "registry"
            / "releases"
            / release.model_version
            / Path(artifact.relative_path)
        )
        with path.open("rb") as handle:
            package = pickle.load(handle)  # noqa: S301 - hash verified local registry
        if not isinstance(package, dict):
            raise RuntimeError("live_champion_package_invalid")
        if package.get("universe_version") != self.universe.version:
            return None
        _limit_inference_threads(package)
        return release.model_version, release.calibration_version, package

    def predict(
        self,
        snapshot: ResolvedSnapshot,
        frame: pd.DataFrame,
    ) -> dict[str, ForecastAvailable]:
        champion = self.champion()
        if champion is None:
            return {}
        model_version, calibration_version, package = champion
        if (
            snapshot.model_version != model_version
            or snapshot.calibration_version != calibration_version
            or package.get("feature_set_version") != snapshot.feature_set_version
            or package.get("universe_version") != snapshot.universe_version
        ):
            raise RuntimeError("live_champion_feature_contract_mismatch")
        return self._predict_package(snapshot, frame, package)

    def latest_candidate(
        self,
    ) -> tuple[str, str, dict[str, Any], dict[str, Any]] | None:
        try:
            report = load_latest_training_report(self.paths)
        except FileNotFoundError:
            return None
        model_version = str(report["model_version"])
        calibration_version = str(report["calibration_version"])
        release = self.registry.verify_release(model_version)
        artifact = next(
            (
                item
                for item in release.artifacts
                if item.relative_path.endswith("production-package.pkl")
            ),
            None,
        )
        if artifact is None:
            raise RuntimeError("live_candidate_package_missing")
        package_path = (
            self.paths.models
            / "registry"
            / "releases"
            / model_version
            / Path(artifact.relative_path)
        )
        with package_path.open("rb") as handle:
            package = pickle.load(handle)  # noqa: S301 - verified local registry
        if not isinstance(package, dict):
            raise RuntimeError("live_candidate_package_invalid")
        _limit_inference_threads(package)
        return model_version, calibration_version, package, report

    def predict_research(
        self,
        snapshot: ResolvedSnapshot,
        frame: pd.DataFrame,
    ) -> tuple[dict[str, ForecastAvailable], dict[str, Any] | None]:
        candidate = self.latest_candidate()
        if candidate is None:
            return {}, None
        model_version, calibration_version, package, report = candidate
        if (
            package.get("feature_set_version") != snapshot.feature_set_version
            or package.get("universe_version") != snapshot.universe_version
        ):
            return {}, None
        research_snapshot = snapshot.model_copy(
            update={
                "prediction_readiness": PredictionReadiness.FORECAST_READY,
                "model_version": model_version,
                "calibration_version": calibration_version,
            }
        )
        return (
            self._predict_package(
                research_snapshot,
                frame,
                package,
                include_similar_cases=False,
            ),
            report,
        )

    def verified_component_versions(self) -> dict[tuple[str, Horizon], str]:
        """Return the currently promoted task components without changing state."""

        result: dict[tuple[str, Horizon], str] = {}
        for task, horizon in _COMPONENT_TASKS:
            snapshot = self.registry.snapshot(task, horizon)
            if snapshot.champion_version is not None:
                result[(task, horizon)] = snapshot.champion_version
        return result

    def _load_component_package(
        self,
        model_version: str,
    ) -> tuple[Any, dict[str, Any]] | None:
        release = self.registry.verify_release(model_version)
        artifact = next(
            (
                item
                for item in release.artifacts
                if item.relative_path.endswith("component-package.pkl")
            ),
            None,
        )
        if artifact is None:
            return None
        package_path = (
            self.paths.models
            / "registry"
            / "releases"
            / release.model_version
            / Path(artifact.relative_path)
        )
        with package_path.open("rb") as handle:
            package = pickle.load(handle)  # noqa: S301 - hash verified registry
        if not isinstance(package, dict):
            return None
        return release, package

    def _verified_component_source(
        self,
        task: str,
        horizon: Horizon,
        snapshot: ResolvedSnapshot,
    ) -> tuple[Any, dict[str, Any]] | None:
        lifecycle = self.registry.snapshot(task, horizon)
        candidates: list[tuple[str, bool]] = []
        if lifecycle.champion_version is not None:
            candidates.append((lifecycle.champion_version, False))
        candidates.extend(
            (version, True) for version in reversed(lifecycle.challenger_versions)
        )
        for version, require_gate_pass in candidates:
            loaded = self._load_component_package(version)
            if loaded is None:
                continue
            release, package = loaded
            if (
                package.get("schema_version") != "research-component-v1"
                or package.get("task") != task
                or package.get("horizon") != horizon
                or package.get("feature_set_version") != snapshot.feature_set_version
                or package.get("universe_version") != snapshot.universe_version
            ):
                continue
            if (
                require_gate_pass
                or package.get("activation_basis") == "manual_override"
            ):
                gate_metrics = package.get("gate_metrics")
                if (
                    not isinstance(gate_metrics, dict)
                    or gate_metrics.get("gate_passed") is not True
                ):
                    continue
            return release, package
        return None

    def predict_verified_research(
        self,
        snapshot: ResolvedSnapshot,
        frame: pd.DataFrame,
    ) -> dict[str, tuple[ResearchTaskPrediction, ...]]:
        """Evaluate retained task Champions, or gate-passed challengers if none exist."""

        latest = (
            frame.sort_values(["symbol", "session_date"])
            .groupby("symbol", as_index=False)
            .tail(1)
            .reset_index(drop=True)
        )
        predictions: dict[str, list[ResearchTaskPrediction]] = {
            str(symbol): [] for symbol in latest["symbol"].tolist()
        }
        for task_index, (task, horizon) in enumerate(_COMPONENT_TASKS):
            if self.progress is not None:
                self.progress(task_index, len(_COMPONENT_TASKS))
            selected = self._verified_component_source(task, horizon, snapshot)
            if selected is None:
                continue
            release, package = selected
            _limit_component_threads(task, package.get("payload"))
            feature_names = tuple(cast(tuple[str, ...], package["features"]))
            values = latest[list(feature_names)].apply(pd.to_numeric, errors="coerce")
            if task == "range_exit":
                values = values.loc[latest["regime"] == RegimeLabel.RANGE.value]
            package = {
                **package,
                "payload": _batch_entry(
                    package["payload"],
                    values,
                    kind="classifier"
                    if task in {"forecast_direction", "breakout"}
                    else "survival"
                    if task == "range_exit"
                    else "quantile",
                    horizon=horizon,
                ),
            }
            gate_metrics = package.get("gate_metrics")
            if not isinstance(gate_metrics, dict):
                gate_metrics = {}
            for row_index, row in zip(
                latest.index, latest.itertuples(index=False), strict=True
            ):
                row_series = pd.Series(row._asdict(), name=row_index)
                prediction = _predict_component(
                    row_series,
                    task=task,
                    horizon=horizon,
                    package=package,
                    feature_names=feature_names,
                    model_version=release.model_version,
                    calibration_version=release.calibration_version,
                    evaluation_run_id=release.source_decision_id,
                    component_evaluated_at=release.registered_at,
                    reference_price_at=snapshot.as_of,
                    gate_metrics=gate_metrics,
                )
                predictions[str(row_series["symbol"])].append(prediction)
        if self.progress is not None:
            self.progress(len(_COMPONENT_TASKS), len(_COMPONENT_TASKS))
        return {
            symbol: tuple(sorted(items, key=lambda item: (item.horizon, item.task)))
            for symbol, items in predictions.items()
        }

    def _predict_package(
        self,
        snapshot: ResolvedSnapshot,
        frame: pd.DataFrame,
        package: dict[str, Any],
        *,
        include_similar_cases: bool = True,
    ) -> dict[str, ForecastAvailable]:
        feature_names = tuple(cast(tuple[str, ...], package["features"]))
        latest = (
            frame.sort_values(["symbol", "session_date"])
            .groupby("symbol", as_index=False)
            .tail(1)
            .reset_index(drop=True)
        )
        values = latest[list(feature_names)].apply(pd.to_numeric, errors="coerce")
        range_values = values.loc[latest["regime"] == RegimeLabel.RANGE.value]
        batch_started = perf_counter()
        entries = sum(
            len(package.get(group, {}))
            for group in ("classifiers", "quantiles", "survival")
        )
        total = entries + len(latest)
        completed = 0
        batched = dict(package)
        for group, kind in (
            ("classifiers", "classifier"),
            ("quantiles", "quantile"),
            ("survival", "survival"),
        ):
            batched[group] = {}
            for key, entry in package.get(group, {}).items():
                if self.progress is not None:
                    self.progress(completed, total)
                batched[group][key] = _batch_entry(
                    entry,
                    range_values if kind == "survival" else values,
                    kind=kind,
                    horizon=key if kind == "survival" else 1,
                )
                completed += 1
        package = batched
        predictions: dict[str, ForecastAvailable] = {}
        similar_history_cache: dict[pd.Timestamp, tuple[pd.DataFrame, np.ndarray]] = {}
        shared_inference_seconds = (perf_counter() - batch_started) / max(
            len(latest), 1
        )
        generated_at = max(datetime.now(UTC), snapshot.as_of)
        for row_index, row in zip(
            latest.index, latest.itertuples(index=False), strict=True
        ):
            symbol = str(row.symbol)
            row_series = pd.Series(row._asdict(), name=row_index)
            started = perf_counter() - shared_inference_seconds
            predictions[symbol] = _forecast(
                row_series,
                frame,
                snapshot=snapshot,
                package=package,
                feature_names=feature_names,
                generated_at=generated_at,
                batch_started=batch_started,
                symbol_started=started,
                include_similar_cases=include_similar_cases,
                similar_history_cache=similar_history_cache,
            )
            completed += 1
            if self.progress is not None:
                self.progress(completed, total)
        return predictions


class _BatchModelOutputs:
    def __init__(
        self, model: Any, values: pd.DataFrame, *, probability: bool, horizon: int = 1
    ) -> None:
        self.classes_ = getattr(model, "classes_", ())
        self.positions = {
            index: position for position, index in enumerate(values.index)
        }
        self.survival = horizon > 1
        samples = (
            pd.concat(
                [
                    values.assign(elapsed_day=float(day))
                    for day in range(1, horizon + 1)
                ],
                ignore_index=True,
            )
            if self.survival
            else values
        )
        if len(values) == 0:
            self.outputs = np.empty((horizon, 0, len(self.classes_)))
            return
        raw = np.asarray(
            model.predict_proba(samples) if probability else model.predict(samples)
        )
        if raw.shape[0] != len(samples):
            raise RuntimeError("live_batch_prediction_row_count_mismatch")
        self.outputs = raw.reshape((horizon, len(values), *raw.shape[1:]))

    def predict(self, values: pd.DataFrame) -> np.ndarray:
        positions = [self.positions[index] for index in values.index]
        days = (
            values["elapsed_day"].to_numpy(dtype=int) - 1
            if self.survival
            else np.zeros(len(values), dtype=int)
        )
        return self.outputs[days, positions]

    predict_proba = predict


def _batch_entry(
    entry: dict[str, Any], values: pd.DataFrame, *, kind: str, horizon: int = 1
) -> dict[str, Any]:
    if kind == "quantile":
        return {
            **entry,
            "models": {
                alpha: _BatchModelOutputs(model, values, probability=False)
                for alpha, model in entry["models"].items()
            },
        }
    return {
        **entry,
        "model": _BatchModelOutputs(
            entry["model"],
            values,
            probability=True,
            horizon=horizon if kind == "survival" else 1,
        ),
    }


def _predict_component(
    row: pd.Series,
    *,
    task: str,
    horizon: Horizon,
    package: dict[str, Any],
    feature_names: tuple[str, ...],
    model_version: str,
    calibration_version: str | None,
    evaluation_run_id: str,
    component_evaluated_at: datetime,
    reference_price_at: datetime,
    gate_metrics: dict[str, Any],
) -> ResearchTaskPrediction:
    raw_values = pd.to_numeric(row[list(feature_names)], errors="coerce")
    values = pd.DataFrame(
        [raw_values.to_dict()], columns=feature_names, index=[row.name]
    )
    missing_rate = float(raw_values.isna().mean())
    reasons: list[RejectionReason] = []
    if missing_rate > 0.20:
        reasons.append(RejectionReason.DATA_QUALITY)
    distribution = package.get("feature_distribution")
    if isinstance(distribution, dict):
        mean = distribution.get("mean")
        std = distribution.get("std")
        if isinstance(mean, dict) and isinstance(std, dict):
            z = np.nan_to_num(
                np.asarray(
                    [
                        (float(row[name]) - float(mean[name]))
                        / max(float(std[name]), 1e-9)
                        for name in feature_names
                    ],
                ),
                nan=0.0,
                posinf=0.0,
                neginf=0.0,
            )
            if float(np.sqrt(np.mean(np.square(z)))) > 4.0:
                reasons.append(RejectionReason.OOD)
    payload = package["payload"]
    if not isinstance(payload, dict):
        raise RuntimeError("research_component_payload_invalid")
    probabilities: dict[int, float] | None = None
    quantiles: ReturnQuantiles | None = None
    cqr_adjustment: float | None = None
    range_exit_probability: float | None = None
    if task in {"forecast_direction", "breakout"}:
        probabilities = _classifier_entry_probability(payload, values)
    elif task in {"return_fwd", "mfe", "mae"}:
        raw_quantiles, cqr_adjustment = _quantile_entry_prediction(payload, values)
        if task == "mfe":
            raw_quantiles = tuple(max(0.0, value) for value in raw_quantiles)
        elif task == "mae":
            raw_quantiles = tuple(min(0.0, value) for value in raw_quantiles)
        quantiles = _return_quantiles(raw_quantiles)
    elif task == "range_exit" and RegimeLabel(str(row["regime"])) is RegimeLabel.RANGE:
        range_exit_probability = _survival_component_probability(
            payload,
            horizon,
            values,
            feature_names,
        )
    return ResearchTaskPrediction(
        task=task,
        horizon=horizon,
        symbol=str(row["symbol"]),
        model_version=model_version,
        calibration_version=calibration_version,
        evaluation_run_id=evaluation_run_id,
        component_evaluated_at=component_evaluated_at,
        gate_metrics=dict(gate_metrics),
        reference_price=_market_reference_price(row),
        reference_price_at=reference_price_at,
        probabilities=probabilities,
        quantiles=quantiles,
        cqr_adjustment=cqr_adjustment,
        range_exit_probability=range_exit_probability,
        sample_abstain_reasons=tuple(dict.fromkeys(reasons)),
    )


def _market_reference_price(row: pd.Series) -> float:
    """Return the observable market quote, not the normalized model price path."""

    raw_close = pd.to_numeric(row.get("raw_close"), errors="coerce")
    if pd.notna(raw_close) and float(raw_close) > 0:
        return float(raw_close)
    return float(row["close"])


def _classifier_entry_probability(
    entry: dict[str, Any],
    values: pd.DataFrame,
) -> dict[int, float]:
    model = entry["model"]
    raw = np.asarray(model.predict_proba(values), dtype=float)
    temperature = float(entry["temperature"] or 1.0)
    logits = np.log(np.clip(raw, 1e-12, 1.0)) / temperature
    scaled = np.exp(logits - logits.max(axis=1, keepdims=True))
    scaled /= scaled.sum(axis=1, keepdims=True)
    return {
        int(label): float(scaled[0, index])
        for index, label in enumerate(model.classes_)
    }


def _quantile_entry_prediction(
    entry: dict[str, Any],
    values: pd.DataFrame,
) -> tuple[tuple[float, ...], float]:
    predictions = sorted(
        float(entry["models"][alpha].predict(values)[0]) for alpha in _QUANTILES
    )
    if entry.get("evaluation_pipeline_version") == EVALUATION_PIPELINE_VERSION:
        predictions = ordered_quantiles([predictions], entry["outcome"])[0].tolist()
    adjustment = float(entry["cqr_adjustment"])
    grouped = entry.get("cqr_adjustments")
    if isinstance(grouped, dict):
        is_etf = float(values.iloc[0].get("is_etf", 0.0)) >= 0.5
        asset_name = "etf" if is_etf else "stock"
        group_name = asset_name
        if "volatility_median" in grouped and "volatility_20" in values:
            high_volatility = float(values.iloc[0]["volatility_20"]) > float(
                grouped["volatility_median"]
            )
            group_name = f"{asset_name}_{'high_vol' if high_volatility else 'low_vol'}"
        selected_adjustment = grouped.get(
            group_name,
            grouped.get(asset_name, grouped.get("pooled", adjustment)),
        )
        if selected_adjustment is not None:
            adjustment = float(selected_adjustment)
    return tuple(predictions), max(0.0, adjustment)


def _survival_component_probability(
    entry: dict[str, Any],
    horizon: Horizon,
    values: pd.DataFrame,
    feature_names: tuple[str, ...],
) -> float:
    model = entry["model"]
    calibrator = entry.get("calibrator")
    survival = 1.0
    for day in range(1, horizon + 1):
        sample = values.copy()
        sample["elapsed_day"] = float(day)
        hazard = float(
            model.predict_proba(sample[[*feature_names, "elapsed_day"]])[0, 1]
        )
        survival *= 1.0 - float(np.clip(hazard, 0.0, 1.0))
    cumulative = 1.0 - survival
    if calibrator is not None:
        cumulative = float(calibrator.predict([cumulative])[0])
    return float(np.clip(cumulative, 0.0, 1.0))


def _forecast(
    row: pd.Series,
    history: pd.DataFrame,
    *,
    snapshot: ResolvedSnapshot,
    package: dict[str, Any],
    feature_names: tuple[str, ...],
    generated_at: datetime,
    batch_started: float,
    symbol_started: float,
    include_similar_cases: bool,
    similar_history_cache: dict[pd.Timestamp, tuple[pd.DataFrame, np.ndarray]]
    | None = None,
) -> ForecastAvailable:
    if snapshot.model_version is None or snapshot.calibration_version is None:
        raise RuntimeError("forecast_ready_snapshot_requires_versions")
    symbol = str(row["symbol"])
    raw_values = pd.to_numeric(row[list(feature_names)], errors="coerce")
    missing_rate = float(raw_values.isna().mean())
    values = pd.DataFrame(
        [raw_values.to_dict()], columns=feature_names, index=[row.name]
    )
    mean = cast(dict[str, float], package["feature_distribution"]["mean"])
    std = cast(dict[str, float], package["feature_distribution"]["std"])
    z = np.nan_to_num(
        np.asarray(
            [
                (float(row[name]) - float(mean[name])) / max(float(std[name]), 1e-9)
                for name in feature_names
            ],
        ),
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )
    ood_score = float(np.sqrt(np.mean(np.square(z))))
    if include_similar_cases:
        similar_summary, similar_views = _similar_cases(
            symbol,
            row,
            history,
            feature_names,
            mean,
            std,
            prepared=similar_history_cache,
        )
    else:
        similar_summary, similar_views = _empty_similar_summary(), ()
    current_regime = RegimeLabel(str(row["regime"]))
    horizons: list[HorizonForecast] = []
    disagreements: list[float] = []
    interval_widths: list[float] = []
    for horizon in _HORIZONS:
        direction_values = _classifier_probability(
            package, "forecast_direction", horizon, values
        )
        breakout_values = _classifier_probability(package, "breakout", horizon, values)
        direction = DirectionProbabilities(
            up=direction_values[1],
            flat=direction_values[0],
            down=direction_values[-1],
        )
        breakout = BreakoutProbabilities(
            up=breakout_values[1],
            down=breakout_values[-1],
            none=breakout_values[0],
        )
        disagreements.append(abs(direction.up - breakout.up))
        returns, return_adjustment = _quantile_prediction(
            package, "return_fwd", horizon, values
        )
        mfe, _ = _quantile_prediction(package, "mfe", horizon, values)
        mae, _ = _quantile_prediction(package, "mae", horizon, values)
        mfe = tuple(max(0.0, value) for value in mfe)
        mae = tuple(min(0.0, value) for value in mae)
        executable = tuple(value - 0.0015 for value in returns)
        lower = returns[0] - return_adjustment
        upper = returns[-1] + return_adjustment
        interval_widths.append(upper - lower)
        intervals = tuple(
            CalibratedInterval(
                return_basis=basis,
                lower=lower - (0.0015 if basis is ReturnBasis.EXECUTABLE else 0.0),
                upper=upper - (0.0015 if basis is ReturnBasis.EXECUTABLE else 0.0),
                coverage_target=0.80,
                method="cqr",
                calibration_version=snapshot.calibration_version,
            )
            for basis in ReturnBasis
        )
        hazard_curve: tuple[RangeExitHazard, ...] = ()
        exit_probability: float | None = None
        unavailable_reason: str | None = (
            "two_day_confirmation_required" if horizon == 1 else "not_range_regime"
        )
        if current_regime is RegimeLabel.RANGE and horizon in (5, 20):
            hazard_curve = _survival_curve(package, horizon, values, feature_names)
            exit_probability = hazard_curve[-1].cumulative_probability
            unavailable_reason = None
        reference_price = _market_reference_price(row)
        return_quantiles = _return_quantiles(returns)
        horizons.append(
            HorizonForecast(
                horizon=horizon,
                forecast_direction_probabilities=direction,
                breakout_probabilities=breakout,
                range_exit_probability=exit_probability,
                range_exit_hazard_curve=hazard_curve,
                range_exit_metadata=RangeExitMetadata(
                    censoring_rule="right_censored_at_horizon",
                    sample_denominator=_survival_sample_count(package, horizon),
                    not_generated_reason=unavailable_reason,
                ),
                capital_gain_return_quantiles=return_quantiles,
                total_return_quantiles=return_quantiles,
                executable_direction_probabilities=direction,
                executable_return_quantiles=_return_quantiles(executable),
                calibrated_return_intervals=intervals,
                interval_method="cqr",
                coverage_target=0.80,
                forecast_mfe_quantiles=_return_quantiles(mfe),
                forecast_mae_quantiles=_return_quantiles(mae),
                executable_mfe_quantiles=_return_quantiles(mfe),
                executable_mae_quantiles=_return_quantiles(mae),
                reference_price=reference_price,
                reference_price_at=snapshot.as_of,
                currency="USD",
                adjustment_mode="split_adjusted_capital_gain",
                price_band=PriceBand(
                    **{
                        name: reference_price * (1.0 + value)
                        for name, value in zip(
                            ("q10", "q25", "q50", "q75", "q90"),
                            returns,
                            strict=True,
                        )
                    }
                ),
                price_band_unavailable_reason=None,
            )
        )
    disagreement = float(max(disagreements, default=0.0))
    widest_interval = float(max(interval_widths, default=0.0))
    calibration_error, empirical_coverage, average_width, sample_count = (
        _calibration_metrics(package)
    )
    reasons: list[RejectionReason] = []
    if missing_rate > 0.20:
        reasons.append(RejectionReason.DATA_QUALITY)
    if ood_score > 4.0:
        reasons.append(RejectionReason.OOD)
    if widest_interval > 0.50:
        reasons.append(RejectionReason.INTERVAL_TOO_WIDE)
    if disagreement > 0.35:
        reasons.append(RejectionReason.MODEL_DISAGREEMENT)
    if similar_summary.effective_independent_count < 10:
        reasons.append(RejectionReason.INSUFFICIENT_SIMILAR_CASES)
    if calibration_error > 0.12:
        reasons.append(RejectionReason.POOR_CALIBRATION)
    confidence_score = float(
        np.clip(
            1.0
            - min(ood_score / 6.0, 0.5)
            - min(disagreement, 0.25)
            - min(widest_interval / 2.0, 0.25)
            - min(missing_rate, 0.25),
            0.0,
            1.0,
        )
    )
    confidence_level = (
        ConfidenceLevel.UNAVAILABLE
        if reasons
        else ConfidenceLevel.HIGH
        if confidence_score >= 0.75
        else ConfidenceLevel.MEDIUM
        if confidence_score >= 0.50
        else ConfidenceLevel.LOW
    )
    evidence = ForecastEvidence(
        top_feature_families=_feature_evidence(feature_names, z),
        regime_probabilities=None,
        regime_probabilities_unavailable_reason="rule_state_only",
        regime_model_version=None,
        ood_score=ood_score,
        ood_method="training_zscore_rms",
        ood_threshold=4.0,
        model_disagreement=disagreement,
        model_disagreement_summary="direction_breakout_probability_gap",
        similar_case_summary=similar_summary,
        calibration_evidence=CalibrationEvidence(
            probability_bin_sample_count=sample_count,
            calibration_error=calibration_error,
            cqr_empirical_coverage=empirical_coverage,
            cqr_average_width=average_width,
        ),
        data_quality_score=max(0.0, 1.0 - missing_rate),
        missing_rate=missing_rate,
        evidence_cutoff=snapshot.as_of,
    )
    bundle = ForecastBundle(
        symbol=symbol,
        as_of=snapshot.as_of,
        current_regime=current_regime,
        regime_probabilities=None,
        regime_probabilities_unavailable_reason="rule_state_only",
        current_event_flags=_event_flags(row),
        horizons=tuple(horizons),
        invalidation_conditions=(
            "data_revision_changed",
            "corporate_action_revision",
            "model_version_changed",
        ),
        confidence_level=confidence_level,
        confidence_score=0.0 if reasons else confidence_score,
        ood_score=ood_score,
        model_disagreement=disagreement,
        similar_case_summary=similar_summary,
        evidence=evidence,
        abstain=bool(reasons),
        abstain_reasons=tuple(dict.fromkeys(reasons)),
        dataset_revision=snapshot.dataset_revision,
        feature_set_version=snapshot.feature_set_version,
        label_version="live-labels-v2",
        universe_version=snapshot.universe_version,
        model_version=snapshot.model_version,
        calibration_version=snapshot.calibration_version,
        distance_version="live-distance-v1",
        data_freshness_seconds=max(
            0, int((datetime.now(UTC) - snapshot.as_of).total_seconds())
        ),
        data_quality_status="current",
        generated_at=generated_at,
        inference_latency_ms=(perf_counter() - symbol_started) * 1000.0,
        inference_latency_scope="per_symbol_batch_amortized",
        revision_no=snapshot.revision_no,
        snapshot_id=snapshot.snapshot_id,
        valid_until=max(generated_at, snapshot.as_of) + timedelta(days=4),
    )
    return ForecastAvailable(
        bundle=bundle,
        serving=ServingOverlay(
            serving_status=ServingStatus.LIVE,
            effective_abstain=bundle.abstain,
            evaluated_at=generated_at,
            exclusion_reason=(),
        ),
        evidence=bundle.evidence,
        similar_cases=similar_views,
    )


def _classifier_probability(
    package: dict[str, Any],
    task: str,
    horizon: int,
    values: pd.DataFrame,
) -> dict[int, float]:
    entry = package["classifiers"][(task, horizon)]
    return _classifier_entry_probability(entry, values)


def _quantile_prediction(
    package: dict[str, Any], outcome: str, horizon: int, values: pd.DataFrame
) -> tuple[tuple[float, ...], float]:
    entry = package["quantiles"][(outcome, horizon)]
    return _quantile_entry_prediction(entry, values)


def _return_quantiles(values: tuple[float, ...]) -> ReturnQuantiles:
    return ReturnQuantiles(
        **dict(zip(("q10", "q25", "q50", "q75", "q90"), values, strict=True))
    )


def _survival_curve(
    package: dict[str, Any],
    horizon: int,
    values: pd.DataFrame,
    feature_names: tuple[str, ...],
) -> tuple[RangeExitHazard, ...]:
    entry = package["survival"][horizon]
    model = entry["model"]
    calibrator = entry.get("calibrator")
    raw_survival = 1.0
    prior_cumulative = 0.0
    rows: list[RangeExitHazard] = []
    for day in range(1, horizon + 1):
        sample = values.copy()
        sample["elapsed_day"] = float(day)
        hazard = float(
            model.predict_proba(sample[[*feature_names, "elapsed_day"]])[0, 1]
        )
        hazard = float(np.clip(hazard, 0.0, 1.0))
        raw_survival *= 1.0 - hazard
        cumulative = 1.0 - raw_survival
        if calibrator is not None:
            cumulative = float(calibrator.predict([cumulative])[0])
        cumulative = float(np.clip(cumulative, prior_cumulative, 1.0))
        calibrated_hazard = (cumulative - prior_cumulative) / max(
            1.0 - prior_cumulative, 1e-12
        )
        rows.append(
            RangeExitHazard(
                trading_day=day,
                hazard_probability=float(np.clip(calibrated_hazard, 0.0, 1.0)),
                cumulative_probability=cumulative,
            )
        )
        prior_cumulative = cumulative
    return tuple(rows)


def _similar_cases(
    symbol: str,
    row: pd.Series,
    history: pd.DataFrame,
    feature_names: tuple[str, ...],
    mean: dict[str, float],
    std: dict[str, float],
    *,
    prepared: dict[pd.Timestamp, tuple[pd.DataFrame, np.ndarray]] | None = None,
) -> tuple[SimilarCaseSummary, tuple[SimilarCaseView, ...]]:
    cutoff = pd.Timestamp(row["session_date"]) - pd.Timedelta(days=30)
    target = np.nan_to_num(
        np.asarray(
            [
                (float(row[name]) - mean[name]) / max(std[name], 1e-9)
                for name in feature_names
            ],
        ),
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )
    case_columns = (
        "symbol",
        "session_date",
        "return_fwd_20",
        "mae_20",
        "regime",
        "breakout_direction_20",
    )
    cached = None if prepared is None else prepared.get(cutoff)
    if cached is None:
        candidates = history.loc[
            (pd.to_datetime(history["session_date"]) < cutoff)
            & history["return_fwd_20"].notna()
            & history["mae_20"].notna(),
            list(dict.fromkeys((*feature_names, *case_columns))),
        ].copy()
        if candidates.empty:
            return _empty_similar_summary(), ()
        matrix = np.nan_to_num(
            np.column_stack(
                [
                    (candidates[name].astype(float).to_numpy() - mean[name])
                    / max(std[name], 1e-9)
                    for name in feature_names
                ],
            ),
            nan=0.0,
            posinf=0.0,
            neginf=0.0,
        )
        candidates = candidates[list(case_columns)]
        if prepared is not None:
            prepared.clear()
            prepared[cutoff] = (candidates, matrix)
    else:
        candidates, matrix = cached
    candidates = candidates.assign(
        distance=np.sqrt(np.mean(np.square(matrix - target), axis=1))
    )
    candidates = candidates.sort_values("distance")
    selected: list[pd.Series] = []
    last_dates: dict[str, pd.Timestamp] = {}
    for position, (candidate_symbol, candidate_session) in enumerate(
        candidates[["symbol", "session_date"]].itertuples(index=False, name=None)
    ):
        candidate_symbol = str(candidate_symbol)
        candidate_date = pd.Timestamp(candidate_session)
        prior = last_dates.get(candidate_symbol)
        if prior is not None and abs((candidate_date - prior).days) < 25:
            continue
        selected.append(candidates.iloc[position])
        last_dates[candidate_symbol] = candidate_date
        if len(selected) == 20:
            break
    if not selected:
        return _empty_similar_summary(), ()
    returns = np.asarray([float(item["return_fwd_20"]) for item in selected])
    worst_index = int(np.argmin([float(item["mae_20"]) for item in selected]))
    views = tuple(
        SimilarCaseView(
            case_id=_case_id(item),
            as_of=_session_as_utc(item["session_date"]),
            distance=float(item["distance"]),
            regime=RegimeLabel(str(item["regime"])),
            return_quantiles=SimilarCaseQuantiles(
                q10=float(item["return_fwd_20"]),
                q50=float(item["return_fwd_20"]),
                q90=float(item["return_fwd_20"]),
            ),
            breakout_result=_breakout_result(int(item["breakout_direction_20"])),
            mae=min(0.0, float(item["mae_20"])),
            worst_case=index == worst_index,
        )
        for index, item in enumerate(selected)
    )
    sufficient = len(selected) >= 10
    summary = SimilarCaseSummary(
        effective_independent_count=len(selected),
        insufficient_evidence=not sufficient,
        median_distance=float(
            np.median([float(item["distance"]) for item in selected])
        ),
        return_quantiles=SimilarCaseQuantiles(
            q10=float(np.quantile(returns, 0.10)),
            q50=float(np.quantile(returns, 0.50)),
            q90=float(np.quantile(returns, 0.90)),
        ),
        win_rate=float(np.mean(returns > 0)) if sufficient else None,
        breakout_rate=(
            float(
                np.mean([int(item["breakout_direction_20"]) != 0 for item in selected])
            )
            if sufficient
            else None
        ),
        worst_mae_case_id=_case_id(selected[worst_index]),
    )
    return summary, views


def _empty_similar_summary() -> SimilarCaseSummary:
    return SimilarCaseSummary(
        effective_independent_count=0,
        insufficient_evidence=True,
        median_distance=None,
        return_quantiles=None,
        win_rate=None,
        breakout_rate=None,
        worst_mae_case_id=None,
    )


def _breakout_result(value: int) -> Literal["up", "down", "none"]:
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "none"


def _case_id(row: pd.Series) -> str:
    return (
        f"case-{str(row['symbol']).replace('.', '-')}-"
        f"{pd.Timestamp(row['session_date']):%Y%m%d}"
    )


def _session_as_utc(value: object) -> datetime:
    return datetime.combine(pd.Timestamp(value).date(), datetime.min.time(), tzinfo=UTC)


def _event_flags(row: pd.Series) -> tuple[str, ...]:
    flags: list[str] = []
    if bool(row.get("channel_compressed")):
        flags.append("range_compression")
    if bool(row.get("breakout_up_rule")):
        flags.append("up_breakout_rule")
    if bool(row.get("breakout_down_rule")):
        flags.append("down_breakout_rule")
    return tuple(flags)


def _feature_evidence(
    feature_names: tuple[str, ...], z: np.ndarray
) -> tuple[FeatureFamilyEvidence, ...]:
    order = np.argsort(np.abs(z))[::-1][:3]
    return tuple(
        FeatureFamilyEvidence(
            family=feature_names[int(index)],
            contribution=float(z[int(index)]),
            training_percentile=float(np.clip(0.5 + z[int(index)] / 8.0, 0.0, 1.0)),
        )
        for index in order
    )


def _calibration_metrics(package: dict[str, Any]) -> tuple[float, float, float, int]:
    tasks = cast(list[dict[str, Any]], package.get("gate_metrics", []))
    classification_folds = [
        fold
        for task in tasks
        if task.get("task") in {"forecast_direction", "breakout"}
        for fold in task.get("folds", [])
    ]
    quantile_folds = [
        fold
        for task in tasks
        if task.get("task") == "return_fwd"
        for fold in task.get("folds", [])
    ]
    calibration_error = float(
        np.mean(
            [
                max(0.0, -float(fold.get("brier_skill_vs_frequency", 0.0)))
                for fold in classification_folds
            ]
            or [0.0]
        )
    )
    coverage = float(
        np.mean([float(fold["cqr_coverage"]) for fold in quantile_folds] or [0.80])
    )
    width = float(
        np.mean([float(fold["cqr_average_width"]) for fold in quantile_folds] or [0.0])
    )
    samples = int(
        sum(int(fold.get("sample_count", 0)) for fold in classification_folds)
    )
    return min(calibration_error, 1.0), coverage, max(width, 0.0), samples


def _limit_inference_threads(package: dict[str, Any]) -> None:
    """Avoid LightGBM thread-pool setup for hundreds of one-row predictions."""

    models: list[object] = []
    classifiers = package.get("classifiers")
    if isinstance(classifiers, dict):
        models.extend(
            entry.get("model")
            for entry in classifiers.values()
            if isinstance(entry, dict)
        )
    quantiles = package.get("quantiles")
    if isinstance(quantiles, dict):
        models.extend(
            model
            for entry in quantiles.values()
            if isinstance(entry, dict)
            for nested in (entry.get("models"),)
            if isinstance(nested, dict)
            for model in nested.values()
        )
    survival = package.get("survival")
    if isinstance(survival, dict):
        models.extend(
            entry.get("model") for entry in survival.values() if isinstance(entry, dict)
        )
    _limit_model_threads(models)


def _limit_component_threads(task: str, payload: object) -> None:
    if not isinstance(payload, dict):
        return
    models: list[object]
    if task in {"forecast_direction", "breakout", "range_exit"}:
        models = [payload.get("model")]
    else:
        nested = payload.get("models")
        models = list(nested.values()) if isinstance(nested, dict) else []
    _limit_model_threads(models)


def _limit_model_threads(models: list[object]) -> None:
    seen: set[int] = set()
    pending = list(models)
    while pending:
        model = pending.pop()
        if model is None or id(model) in seen:
            continue
        seen.add(id(model))
        set_params = getattr(model, "set_params", None)
        if callable(set_params):
            set_params(n_jobs=1)
        pending.extend(
            getattr(model, name, None)
            for name in ("model", "linear", "nonlinear", "up_model", "down_model")
        )


def _survival_sample_count(package: dict[str, Any], horizon: int) -> int:
    tasks = cast(list[dict[str, Any]], package.get("gate_metrics", []))
    return sum(
        int(fold.get("sample_count", 0))
        for task in tasks
        if task.get("task") == "range_exit" and int(task.get("horizon", 0)) == horizon
        for fold in task.get("folds", [])
    )


__all__ = ["LiveInferenceEngine", "ResearchTaskPrediction"]
