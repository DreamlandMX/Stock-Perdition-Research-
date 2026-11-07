"""Stable, identity-preserving multi-horizon sequence batches for M7."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import torch


class M7SequenceInputError(ValueError):
    """Sequence inputs are misaligned, future-looking, or incomplete."""


@dataclass(frozen=True, slots=True)
class M7SequenceBatch:
    sample_keys: tuple[tuple[str, date], ...]
    input_sessions: tuple[tuple[date, ...], ...]
    feature_names: tuple[str, ...]
    sequences: torch.Tensor
    labels: torch.Tensor
    weights: torch.Tensor
    availability: torch.Tensor
    horizons: tuple[int, int, int] = (1, 5, 20)

    @property
    def sequence_length(self) -> int:
        return int(self.sequences.shape[1])

    @property
    def feature_count(self) -> int:
        return int(self.sequences.shape[2])


def make_m7_sequence_batch(
    *,
    sample_keys: Sequence[tuple[str, date]],
    input_sessions: Sequence[Sequence[date]],
    sequences: object,
    labels_by_horizon: Mapping[int, object],
    weights_by_horizon: Mapping[int, object],
    availability_by_horizon: Mapping[int, object],
    feature_names: Sequence[str],
) -> M7SequenceBatch:
    keys = tuple(sample_keys)
    windows = tuple(tuple(value) for value in input_sessions)
    names = tuple(feature_names)
    values = np.asarray(sequences, dtype=np.float32)
    if (
        not keys
        or len(set(keys)) != len(keys)
        or len(windows) != len(keys)
        or values.ndim != 3
        or values.shape[0] != len(keys)
        or values.shape[1] < 1
        or values.shape[2] != len(names)
        or not names
        or len(set(names)) != len(names)
    ):
        raise M7SequenceInputError("m7_sequence_inputs_misaligned")
    if any(
        len(window) != values.shape[1]
        or tuple(sorted(set(window))) != window
        or window[-1] != key[1]
        or any(session > key[1] for session in window)
        for key, window in zip(keys, windows, strict=True)
    ):
        raise M7SequenceInputError("m7_sequence_window_invalid")
    horizons = (1, 5, 20)
    if (
        set(labels_by_horizon) != set(horizons)
        or set(weights_by_horizon) != set(horizons)
        or set(availability_by_horizon) != set(horizons)
    ):
        raise M7SequenceInputError("m7_sequence_horizons_incomplete")
    labels = np.column_stack(
        [np.asarray(labels_by_horizon[horizon], dtype=np.int64) for horizon in horizons]
    )
    weights = np.column_stack(
        [
            np.asarray(weights_by_horizon[horizon], dtype=np.float32)
            for horizon in horizons
        ]
    )
    availability = np.column_stack(
        [
            np.asarray(availability_by_horizon[horizon], dtype=bool)
            for horizon in horizons
        ]
    )
    if (
        labels.shape != (len(keys), 3)
        or weights.shape != (len(keys), 3)
        or availability.shape != (len(keys), 3)
        or np.any(labels < 0)
        or np.any(labels > 2)
        or not np.all(np.isfinite(weights))
        or np.any(weights <= 0)
    ):
        raise M7SequenceInputError("m7_sequence_targets_invalid")
    order = tuple(
        sorted(
            range(len(keys)),
            key=lambda index: (keys[index][1], keys[index][0]),
        )
    )
    return M7SequenceBatch(
        sample_keys=tuple(keys[index] for index in order),
        input_sessions=tuple(windows[index] for index in order),
        feature_names=names,
        sequences=torch.from_numpy(values[np.asarray(order)].copy()),
        labels=torch.from_numpy(labels[np.asarray(order)].copy()),
        weights=torch.from_numpy(weights[np.asarray(order)].copy()),
        availability=torch.from_numpy(availability[np.asarray(order)].copy()),
    )


__all__ = [
    "M7SequenceBatch",
    "M7SequenceInputError",
    "make_m7_sequence_batch",
]
