"""Frozen-reference PSI and operating-quality evidence for M7 packages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Self

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_prediction.features.m7 import M7_CORE_V2_COLUMNS
from stock_prediction.modeling.report import deterministic_hash


class M7FrozenDistribution(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    cut_points: tuple[float, ...]
    reference_proportions: tuple[float, ...] = Field(min_length=10, max_length=10)
    minimum: float
    maximum: float
    missingness_rate: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_distribution(self) -> Self:
        if (
            len(self.cut_points) != 9
            or tuple(sorted(self.cut_points)) != self.cut_points
        ):
            raise ValueError("m7_drift_cut_points_invalid")
        if not np.isclose(sum(self.reference_proportions), 1.0, atol=1e-9):
            raise ValueError("m7_drift_reference_proportions_invalid")
        if self.minimum > self.maximum:
            raise ValueError("m7_drift_reference_bounds_invalid")
        return self


class M7DriftReference(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["m7-drift-reference-v1"] = "m7-drift-reference-v1"
    source_identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    core_features: tuple[str, ...]
    feature_distributions: dict[str, M7FrozenDistribution]
    prediction_distributions: dict[str, M7FrozenDistribution]
    regime_mix: dict[str, float]
    reference_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        if self.core_features != M7_CORE_V2_COLUMNS:
            raise ValueError("m7_drift_core_feature_contract_invalid")
        if set(self.feature_distributions) != set(self.core_features):
            raise ValueError("m7_drift_feature_reference_incomplete")
        if not self.prediction_distributions:
            raise ValueError("m7_drift_prediction_reference_empty")
        expected = deterministic_hash(
            self.model_dump(mode="json", exclude={"reference_hash"})
        )
        if self.reference_hash != expected:
            raise ValueError("m7_drift_reference_hash_invalid")
        return self


class M7DailyDriftReport(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    schema_version: Literal["m7-daily-drift-report-v1"] = "m7-daily-drift-report-v1"
    reference_package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_psi: dict[str, float]
    prediction_psi: dict[str, float]
    core_feature_psi_fraction_above_020: float = Field(ge=0, le=1)
    feature_warning: bool
    prediction_distribution_shifted: bool
    missingness_rate: float = Field(ge=0, le=1)
    coverage_rate: float = Field(ge=0, le=1)
    ood_rate: float = Field(ge=0, le=1)
    regime_mix: dict[str, float]
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if set(self.feature_psi) != set(M7_CORE_V2_COLUMNS):
            raise ValueError("m7_drift_feature_psi_incomplete")
        expected_warning = self.core_feature_psi_fraction_above_020 >= 0.20
        if self.feature_warning != expected_warning:
            raise ValueError("m7_drift_feature_warning_invalid")
        if self.evidence_hash != self.recomputed_evidence_hash():
            raise ValueError("m7_drift_report_hash_invalid")
        return self

    def recomputed_evidence_hash(self) -> str:
        return deterministic_hash(
            self.model_dump(mode="json", exclude={"evidence_hash"})
        )


def build_m7_drift_reference(
    frame: pd.DataFrame,
    *,
    prediction_values: Mapping[str, Sequence[float] | np.ndarray],
    source_identity_hash: str,
) -> M7DriftReference:
    if set(M7_CORE_V2_COLUMNS) - set(frame.columns):
        raise ValueError("m7_drift_reference_features_missing")
    eligible = _eligible_frame(frame)
    feature_distributions = {
        name: _frozen_distribution(
            pd.to_numeric(eligible[name], errors="coerce").to_numpy(dtype=np.float64)
        )
        for name in M7_CORE_V2_COLUMNS
    }
    prediction_distributions = {
        name: _frozen_distribution(np.asarray(values, dtype=np.float64))
        for name, values in sorted(prediction_values.items())
    }
    regime_mix = _regime_mix(eligible)
    hash_payload = {
        "schema_version": "m7-drift-reference-v1",
        "source_identity_hash": source_identity_hash,
        "core_features": M7_CORE_V2_COLUMNS,
        "feature_distributions": {
            name: value.model_dump(mode="json")
            for name, value in feature_distributions.items()
        },
        "prediction_distributions": {
            name: value.model_dump(mode="json")
            for name, value in prediction_distributions.items()
        },
        "regime_mix": regime_mix,
    }
    return M7DriftReference(
        source_identity_hash=source_identity_hash,
        core_features=M7_CORE_V2_COLUMNS,
        feature_distributions=feature_distributions,
        prediction_distributions=prediction_distributions,
        regime_mix=regime_mix,
        reference_hash=deterministic_hash(hash_payload),
    )


def evaluate_m7_daily_drift(
    reference: M7DriftReference,
    frame: pd.DataFrame,
    *,
    prediction_values: Mapping[str, Sequence[float] | np.ndarray],
    reference_package_sha256: str,
) -> M7DailyDriftReport:
    if set(M7_CORE_V2_COLUMNS) - set(frame.columns):
        raise ValueError("m7_drift_current_features_missing")
    if set(prediction_values) != set(reference.prediction_distributions):
        raise ValueError("m7_drift_prediction_identity_mismatch")
    eligible_mask = (
        frame.get("eligible", pd.Series(True, index=frame.index))
        .fillna(False)
        .astype(bool)
    )
    eligible = frame.loc[eligible_mask]
    if eligible.empty:
        raise ValueError("m7_drift_current_coverage_empty")
    feature_psi: dict[str, float] = {}
    missing = 0
    finite_count = 0
    ood = 0
    for name in M7_CORE_V2_COLUMNS:
        values = pd.to_numeric(eligible[name], errors="coerce").to_numpy(
            dtype=np.float64
        )
        finite = values[np.isfinite(values)]
        missing += len(values) - len(finite)
        finite_count += len(finite)
        frozen = reference.feature_distributions[name]
        ood += int(np.sum((finite < frozen.minimum) | (finite > frozen.maximum)))
        feature_psi[name] = _psi(frozen, values)
    prediction_psi = {
        name: _psi(
            reference.prediction_distributions[name],
            np.asarray(values, dtype=np.float64),
        )
        for name, values in sorted(prediction_values.items())
    }
    shifted_features = sum(value > 0.20 for value in feature_psi.values())
    shifted_fraction = shifted_features / len(M7_CORE_V2_COLUMNS)
    total_feature_values = len(eligible) * len(M7_CORE_V2_COLUMNS)
    base = {
        "schema_version": "m7-daily-drift-report-v1",
        "reference_package_sha256": reference_package_sha256,
        "feature_psi": feature_psi,
        "prediction_psi": prediction_psi,
        "core_feature_psi_fraction_above_020": shifted_fraction,
        "feature_warning": shifted_fraction >= 0.20,
        "prediction_distribution_shifted": any(
            value > 0.20 for value in prediction_psi.values()
        ),
        "missingness_rate": missing / total_feature_values,
        "coverage_rate": float(eligible_mask.mean()),
        "ood_rate": 0.0 if finite_count == 0 else ood / finite_count,
        "regime_mix": _regime_mix(eligible),
    }
    return M7DailyDriftReport.model_validate(
        {**base, "evidence_hash": deterministic_hash(base)}, strict=True
    )


def _frozen_distribution(values: np.ndarray) -> M7FrozenDistribution:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    finite = array[np.isfinite(array)]
    if finite.size < 10:
        raise ValueError("m7_drift_reference_sample_insufficient")
    cut_points = tuple(
        float(value) for value in np.quantile(finite, np.linspace(0.1, 0.9, 9)).tolist()
    )
    assignments = np.searchsorted(np.asarray(cut_points), finite, side="right")
    counts = np.bincount(assignments, minlength=10).astype(np.float64)
    proportions = tuple(float(value) for value in counts / counts.sum())
    return M7FrozenDistribution(
        cut_points=cut_points,
        reference_proportions=proportions,
        minimum=float(np.min(finite)),
        maximum=float(np.max(finite)),
        missingness_rate=(len(array) - len(finite)) / len(array),
    )


def _psi(reference: M7FrozenDistribution, values: np.ndarray) -> float:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    finite = array[np.isfinite(array)]
    if finite.size == 0:
        raise ValueError("m7_drift_current_distribution_empty")
    assignments = np.searchsorted(
        np.asarray(reference.cut_points), finite, side="right"
    )
    counts = np.bincount(assignments, minlength=10).astype(np.float64)
    current = counts / counts.sum()
    frozen = np.asarray(reference.reference_proportions, dtype=np.float64)
    epsilon = 1e-6
    current = np.clip(current, epsilon, None)
    frozen = np.clip(frozen, epsilon, None)
    value = float(np.sum((current - frozen) * np.log(current / frozen)))
    if not np.isfinite(value) or value < 0:
        raise ValueError("m7_drift_psi_invalid")
    return value


def _eligible_frame(frame: pd.DataFrame) -> pd.DataFrame:
    mask = frame.get("eligible", pd.Series(True, index=frame.index)).fillna(False)
    selected = frame.loc[mask.astype(bool)]
    if selected.empty:
        raise ValueError("m7_drift_reference_coverage_empty")
    return selected


def _regime_mix(frame: pd.DataFrame) -> dict[str, float]:
    if "regime" not in frame:
        return {"unavailable": 1.0}
    values = frame["regime"].fillna("unavailable").astype(str)
    return {
        name: float(rate)
        for name, rate in sorted(values.value_counts(normalize=True).items())
    }


__all__ = [
    "M7DailyDriftReport",
    "M7DriftReference",
    "M7FrozenDistribution",
    "build_m7_drift_reference",
    "evaluate_m7_daily_drift",
]
