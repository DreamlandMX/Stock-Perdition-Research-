"""Exact weighting, calibration, and bootstrap metrics for M7."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import math
from types import MappingProxyType
from typing import Literal, TypeAlias

import numpy as np

from stock_prediction.modeling.metrics import (
    MetricInputError,
    relative_loss_improvement,
)


M7SampleKey: TypeAlias = tuple[str, date, int]


class M7MetricInputError(MetricInputError):
    """M7 metric inputs are misaligned or mathematically invalid."""


@dataclass(frozen=True, slots=True)
class M7TrainingWeightReport:
    symbol_weights: tuple[float, ...]
    recency_weights: tuple[float, ...]
    class_weights: tuple[float, ...]
    combined_weights: tuple[float, ...]
    training_cutoff: date
    recency_half_life_sessions: int = 756


@dataclass(frozen=True, slots=True)
class M7DirectionBrierReport:
    row_equal_brier: float
    symbol_equal_brier: float
    per_symbol_brier: Mapping[str, float]
    sample_count: int
    symbol_count: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "per_symbol_brier",
            MappingProxyType(dict(self.per_symbol_brier)),
        )


@dataclass(frozen=True, slots=True)
class M7DirectionBrierComparison:
    candidate: M7DirectionBrierReport
    incumbent: M7DirectionBrierReport
    relative_improvement: float


M7BinStatus = Literal["empty", "underpopulated", "populated"]


@dataclass(frozen=True, slots=True)
class M7ReliabilityBin:
    bin_index: int
    count: int
    weight: float
    mean_probability: float | None
    observed_rate: float | None
    absolute_gap: float | None
    status: M7BinStatus


@dataclass(frozen=True, slots=True)
class M7BrierDecomposition:
    uncertainty: float
    resolution: float
    reliability: float


@dataclass(frozen=True, slots=True)
class M7DirectionCalibrationReport:
    macro_ece: float
    per_class_ece: Mapping[str, float]
    reliability_bins: Mapping[str, tuple[M7ReliabilityBin, ...]]
    brier_decomposition: Mapping[str, M7BrierDecomposition]
    sample_count: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "per_class_ece",
            MappingProxyType(dict(self.per_class_ece)),
        )
        object.__setattr__(
            self,
            "reliability_bins",
            MappingProxyType(dict(self.reliability_bins)),
        )
        object.__setattr__(
            self,
            "brier_decomposition",
            MappingProxyType(dict(self.brier_decomposition)),
        )


@dataclass(frozen=True, slots=True)
class M7RangeCalibrationReport:
    calibration_error: float
    bins: tuple[M7ReliabilityBin, ...]
    sample_count: int


@dataclass(frozen=True, slots=True)
class M7DateBlockBootstrapReport:
    point_estimate: float
    one_sided_upper_90: float
    n_draws: int
    n_dates: int
    block_length: int
    seed: int


def compute_m7_training_weights(
    *,
    symbols: Sequence[str],
    sessions: Sequence[date],
    labels: Sequence[str],
    training_cutoff: date,
) -> M7TrainingWeightReport:
    symbol_order = tuple(symbols)
    session_order = tuple(sessions)
    label_order = tuple(labels)
    if not symbol_order or not (
        len(symbol_order) == len(session_order) == len(label_order)
    ):
        raise M7MetricInputError("m7_training_weights_inputs_misaligned")
    unique_sessions = tuple(sorted(set(session_order)))
    if training_cutoff not in unique_sessions or any(
        session > training_cutoff for session in session_order
    ):
        raise M7MetricInputError("m7_training_cutoff_invalid")
    session_position = {session: index for index, session in enumerate(unique_sessions)}
    cutoff_position = session_position[training_cutoff]
    symbol_counts = {symbol: symbol_order.count(symbol) for symbol in set(symbol_order)}
    inverse_symbol = np.asarray(
        [1.0 / symbol_counts[symbol] for symbol in symbol_order],
        dtype=np.float64,
    )
    symbol_weights = inverse_symbol / float(np.mean(inverse_symbol))
    ages = np.asarray(
        [cutoff_position - session_position[session] for session in session_order],
        dtype=np.float64,
    )
    recency_weights = np.power(2.0, -ages / 756.0)
    classes = tuple(sorted(set(label_order)))
    class_counts = {label: label_order.count(label) for label in classes}
    class_weights = np.asarray(
        [
            np.clip(
                len(label_order) / (len(classes) * class_counts[label]),
                0.5,
                5.0,
            )
            for label in label_order
        ],
        dtype=np.float64,
    )
    combined = symbol_weights * recency_weights * class_weights
    combined /= float(np.mean(combined))
    return M7TrainingWeightReport(
        symbol_weights=tuple(float(value) for value in symbol_weights),
        recency_weights=tuple(float(value) for value in recency_weights),
        class_weights=tuple(float(value) for value in class_weights),
        combined_weights=tuple(float(value) for value in combined),
        training_cutoff=training_cutoff,
    )


def direction_brier_report(
    *,
    symbols: Sequence[str],
    labels: Sequence[str],
    probabilities: object,
    classes: Sequence[str],
) -> M7DirectionBrierReport:
    symbol_order, _indices, _matrix, losses = _direction_inputs(
        symbols,
        labels,
        probabilities,
        classes,
    )
    per_symbol = {
        symbol: float(np.mean(losses[np.asarray(symbol_order) == symbol]))
        for symbol in sorted(set(symbol_order))
    }
    return M7DirectionBrierReport(
        row_equal_brier=float(np.mean(losses)),
        symbol_equal_brier=float(np.mean(tuple(per_symbol.values()))),
        per_symbol_brier=per_symbol,
        sample_count=len(symbol_order),
        symbol_count=len(per_symbol),
    )


def compare_direction_brier(
    *,
    candidate_sample_keys: Sequence[M7SampleKey],
    incumbent_sample_keys: Sequence[M7SampleKey],
    labels: Sequence[str],
    candidate_probabilities: object,
    incumbent_probabilities: object,
    classes: Sequence[str],
) -> M7DirectionBrierComparison:
    candidate_keys = tuple(candidate_sample_keys)
    incumbent_keys = tuple(incumbent_sample_keys)
    if candidate_keys != incumbent_keys:
        raise M7MetricInputError("m7_direction_sample_keys_mismatch")
    if not candidate_keys or len(set(candidate_keys)) != len(candidate_keys):
        raise M7MetricInputError("m7_direction_sample_keys_invalid")
    symbols = tuple(key[0] for key in candidate_keys)
    candidate = direction_brier_report(
        symbols=symbols,
        labels=labels,
        probabilities=candidate_probabilities,
        classes=classes,
    )
    incumbent = direction_brier_report(
        symbols=symbols,
        labels=labels,
        probabilities=incumbent_probabilities,
        classes=classes,
    )
    try:
        improvement = relative_loss_improvement(
            candidate_loss=candidate.symbol_equal_brier,
            incumbent_loss=incumbent.symbol_equal_brier,
        )
    except MetricInputError as error:
        raise M7MetricInputError(str(error)) from error
    return M7DirectionBrierComparison(
        candidate=candidate,
        incumbent=incumbent,
        relative_improvement=improvement,
    )


def adaptive_classwise_ece(
    *,
    symbols: Sequence[str],
    labels: Sequence[str],
    probabilities: object,
    classes: Sequence[str],
    n_bins: int = 10,
    minimum_bin_count: int = 10,
) -> M7DirectionCalibrationReport:
    if n_bins < 2 or minimum_bin_count < 1:
        raise M7MetricInputError("m7_calibration_bin_spec_invalid")
    symbol_order, indices, matrix, _losses = _direction_inputs(
        symbols,
        labels,
        probabilities,
        classes,
    )
    weights = _symbol_equal_weights(symbol_order)
    total_weight = float(np.sum(weights))
    class_order = tuple(classes)
    ece_by_class: dict[str, float] = {}
    bins_by_class: dict[str, tuple[M7ReliabilityBin, ...]] = {}
    decomposition: dict[str, M7BrierDecomposition] = {}
    for class_index, class_name in enumerate(class_order):
        binary = (indices == class_index).astype(np.float64)
        class_probabilities = matrix[:, class_index]
        bins = _adaptive_bins(
            probabilities=class_probabilities,
            observed=binary,
            weights=weights,
            n_bins=n_bins,
            minimum_bin_count=minimum_bin_count,
        )
        ece = (
            sum(item.weight * float(item.absolute_gap or 0.0) for item in bins)
            / total_weight
        )
        base_rate = float(np.average(binary, weights=weights))
        resolution = (
            sum(
                item.weight * (float(item.observed_rate or 0.0) - base_rate) ** 2
                for item in bins
            )
            / total_weight
        )
        reliability = (
            sum(item.weight * (float(item.absolute_gap or 0.0) ** 2) for item in bins)
            / total_weight
        )
        ece_by_class[class_name] = ece
        bins_by_class[class_name] = bins
        decomposition[class_name] = M7BrierDecomposition(
            uncertainty=base_rate * (1.0 - base_rate),
            resolution=resolution,
            reliability=reliability,
        )
    return M7DirectionCalibrationReport(
        macro_ece=float(np.mean(tuple(ece_by_class.values()))),
        per_class_ece=ece_by_class,
        reliability_bins=bins_by_class,
        brier_decomposition=decomposition,
        sample_count=len(symbol_order),
    )


def range_exit_calibration_report(
    *,
    symbols: Sequence[str],
    observed: Sequence[int],
    cumulative_probability: Sequence[float],
    n_bins: int = 10,
    minimum_bin_count: int = 10,
) -> M7RangeCalibrationReport:
    symbol_order = tuple(symbols)
    observed_array = np.asarray(observed, dtype=np.int8)
    probabilities = np.asarray(cumulative_probability, dtype=np.float64)
    if (
        not symbol_order
        or len(symbol_order) != len(observed_array)
        or probabilities.shape != (len(symbol_order),)
        or set(observed_array.tolist()) - {0, 1}
        or not np.all(np.isfinite(probabilities))
        or np.any(probabilities < 0)
        or np.any(probabilities > 1)
    ):
        raise M7MetricInputError("m7_range_calibration_inputs_invalid")
    if n_bins < 2 or minimum_bin_count < 1:
        raise M7MetricInputError("m7_calibration_bin_spec_invalid")
    weights = _symbol_equal_weights(symbol_order)
    bins = _adaptive_bins(
        probabilities=probabilities,
        observed=observed_array.astype(float),
        weights=weights,
        n_bins=n_bins,
        minimum_bin_count=minimum_bin_count,
    )
    total_weight = float(np.sum(weights))
    error = (
        sum(item.weight * float(item.absolute_gap or 0.0) for item in bins)
        / total_weight
    )
    return M7RangeCalibrationReport(
        calibration_error=error,
        bins=bins,
        sample_count=len(symbol_order),
    )


def date_block_bootstrap_delta_brier(
    *,
    sample_keys: Sequence[M7SampleKey],
    labels: Sequence[str],
    candidate_probabilities: object,
    incumbent_probabilities: object,
    classes: Sequence[str],
    n_draws: int = 10_000,
    block_length: int = 5,
    seed: int = 17,
) -> M7DateBlockBootstrapReport:
    keys = tuple(sample_keys)
    if not keys or len(set(keys)) != len(keys):
        raise M7MetricInputError("m7_bootstrap_sample_keys_invalid")
    if n_draws < 1 or block_length < 1 or seed < 0:
        raise M7MetricInputError("m7_bootstrap_spec_invalid")
    symbols = tuple(key[0] for key in keys)
    sessions = tuple(key[1] for key in keys)
    _symbols, _indices, _matrix, candidate_losses = _direction_inputs(
        symbols,
        labels,
        candidate_probabilities,
        classes,
    )
    _symbols, _indices, _matrix, incumbent_losses = _direction_inputs(
        symbols,
        labels,
        incumbent_probabilities,
        classes,
    )
    deltas = candidate_losses - incumbent_losses
    unique_dates = tuple(sorted(set(sessions)))
    unique_symbols = tuple(sorted(set(symbols)))
    date_index = {value: index for index, value in enumerate(unique_dates)}
    symbol_index = {value: index for index, value in enumerate(unique_symbols)}
    matrix = np.full((len(unique_dates), len(unique_symbols)), np.nan, dtype=float)
    for symbol, session, delta in zip(symbols, sessions, deltas, strict=True):
        matrix[date_index[session], symbol_index[symbol]] = delta
    if np.any(np.all(np.isnan(matrix), axis=0)):
        raise M7MetricInputError("m7_bootstrap_symbol_without_evidence")
    point_estimate = _symbol_equal_delta(matrix)
    symbol_counts = np.sum(~np.isnan(matrix), axis=0)
    weight_matrix = np.where(
        np.isnan(matrix),
        0.0,
        1.0 / symbol_counts.reshape(1, -1),
    )
    effective_block = min(block_length, len(unique_dates))
    blocks_per_draw = math.ceil(len(unique_dates) / effective_block)
    maximum_start = len(unique_dates) - effective_block
    generator = np.random.default_rng(seed)
    starts = generator.integers(
        0,
        maximum_start + 1,
        size=(n_draws, blocks_per_draw),
    )
    offsets = np.arange(effective_block, dtype=int)
    sampled_dates = (starts[..., None] + offsets).reshape(n_draws, -1)[
        :, : len(unique_dates)
    ]
    bootstrap = np.empty(n_draws, dtype=float)
    batch_size = 256
    for start in range(0, n_draws, batch_size):
        stop = min(start + batch_size, n_draws)
        sampled = matrix[sampled_dates[start:stop]]
        sampled_weights = weight_matrix[sampled_dates[start:stop]]
        numerator = np.nansum(sampled * sampled_weights, axis=(1, 2))
        denominator = np.sum(sampled_weights, axis=(1, 2))
        bootstrap[start:stop] = numerator / denominator
    if not np.all(np.isfinite(bootstrap)):
        raise M7MetricInputError("m7_bootstrap_result_non_finite")
    return M7DateBlockBootstrapReport(
        point_estimate=point_estimate,
        one_sided_upper_90=float(np.quantile(bootstrap, 0.90)),
        n_draws=n_draws,
        n_dates=len(unique_dates),
        block_length=block_length,
        seed=seed,
    )


def _direction_inputs(
    symbols: Sequence[str],
    labels: Sequence[str],
    probabilities: object,
    classes: Sequence[str],
) -> tuple[tuple[str, ...], np.ndarray, np.ndarray, np.ndarray]:
    symbol_order = tuple(symbols)
    label_order = tuple(labels)
    class_order = tuple(classes)
    if (
        not symbol_order
        or len(symbol_order) != len(label_order)
        or len(class_order) != 3
        or len(set(class_order)) != 3
        or set(label_order) - set(class_order)
    ):
        raise M7MetricInputError("m7_direction_inputs_invalid")
    matrix = np.asarray(probabilities, dtype=np.float64)
    if (
        matrix.shape != (len(symbol_order), 3)
        or not np.all(np.isfinite(matrix))
        or np.any(matrix < 0)
        or np.any(matrix > 1)
        or not np.allclose(matrix.sum(axis=1), 1.0, rtol=0.0, atol=1e-9)
    ):
        raise M7MetricInputError("m7_direction_probabilities_invalid")
    class_index = {name: index for index, name in enumerate(class_order)}
    indices = np.asarray([class_index[label] for label in label_order], dtype=int)
    one_hot = np.eye(3, dtype=float)[indices]
    losses = np.sum(np.square(matrix - one_hot), axis=1)
    return symbol_order, indices, matrix, losses


def _symbol_equal_weights(symbols: Sequence[str]) -> np.ndarray:
    symbol_order = tuple(symbols)
    counts = {symbol: symbol_order.count(symbol) for symbol in set(symbol_order)}
    weights = np.asarray([1.0 / counts[symbol] for symbol in symbol_order], dtype=float)
    return weights / float(np.mean(weights))


def _adaptive_bins(
    *,
    probabilities: np.ndarray,
    observed: np.ndarray,
    weights: np.ndarray,
    n_bins: int,
    minimum_bin_count: int,
) -> tuple[M7ReliabilityBin, ...]:
    order = np.argsort(probabilities, kind="stable")
    partitions = np.array_split(order, n_bins)
    bins: list[M7ReliabilityBin] = []
    for bin_index, positions in enumerate(partitions):
        count = len(positions)
        if count == 0:
            bins.append(
                M7ReliabilityBin(
                    bin_index=bin_index,
                    count=0,
                    weight=0.0,
                    mean_probability=None,
                    observed_rate=None,
                    absolute_gap=None,
                    status="empty",
                )
            )
            continue
        bin_weights = weights[positions]
        weight = float(np.sum(bin_weights))
        mean_probability = float(
            np.average(probabilities[positions], weights=bin_weights)
        )
        observed_rate = float(np.average(observed[positions], weights=bin_weights))
        bins.append(
            M7ReliabilityBin(
                bin_index=bin_index,
                count=count,
                weight=weight,
                mean_probability=mean_probability,
                observed_rate=observed_rate,
                absolute_gap=abs(mean_probability - observed_rate),
                status=("underpopulated" if count < minimum_bin_count else "populated"),
            )
        )
    return tuple(bins)


def _symbol_equal_delta(matrix: np.ndarray) -> float:
    with np.errstate(invalid="ignore"):
        per_symbol = np.nanmean(matrix, axis=0)
    if not np.all(np.isfinite(per_symbol)):
        raise M7MetricInputError("m7_delta_brier_non_finite")
    return float(np.mean(per_symbol))


__all__ = [
    "M7BrierDecomposition",
    "M7DateBlockBootstrapReport",
    "M7DirectionBrierComparison",
    "M7DirectionBrierReport",
    "M7DirectionCalibrationReport",
    "M7MetricInputError",
    "M7RangeCalibrationReport",
    "M7ReliabilityBin",
    "M7TrainingWeightReport",
    "adaptive_classwise_ece",
    "compare_direction_brier",
    "compute_m7_training_weights",
    "date_block_bootstrap_delta_brier",
    "direction_brier_report",
    "range_exit_calibration_report",
]
