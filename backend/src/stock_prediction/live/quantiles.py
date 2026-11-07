"""Small shared numerical policy for newly evaluated live quantile packages."""

from __future__ import annotations

import math
import numpy as np

EVALUATION_PIPELINE_VERSION = "live-evaluation-v2"


def ordered_quantiles(values: object, outcome: str) -> np.ndarray:
    matrix = np.asarray(values, dtype=float)
    if matrix.ndim != 2 or not matrix.size or not np.isfinite(matrix).all():
        raise ValueError("quantile_predictions_invalid")
    ordered = np.sort(matrix, axis=1)
    if outcome == "mfe":
        ordered = np.maximum(ordered, 0.0)
    elif outcome == "mae":
        ordered = np.minimum(ordered, 0.0)
    return ordered


def finite_sample_cqr(scores: np.ndarray) -> float:
    scores = np.asarray(scores, dtype=float)
    if scores.ndim != 1 or not np.isfinite(scores).all() or np.any(scores < 0):
        raise ValueError("cqr_scores_invalid")
    rank = math.ceil((len(scores) + 1) * 0.80)
    if not 1 <= rank <= len(scores):
        raise ValueError("cqr_sample_too_small")
    return float(np.partition(scores, rank - 1)[rank - 1])
