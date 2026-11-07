"""Repeatable local SLO measurement primitives."""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter_ns

import numpy as np

from stock_prediction.operations.contracts import SloMeasurement, SloStatus


def measure_slo(
    *,
    metric_id: str,
    workload: str,
    target_ms: float,
    operation: Callable[[], object],
    warmups: int = 2,
    samples: int = 10,
) -> SloMeasurement:
    if warmups < 0 or samples < 1 or target_ms <= 0:
        raise ValueError("SLO measurement configuration is invalid")
    for _ in range(warmups):
        operation()
    timings: list[float] = []
    for _ in range(samples):
        started = perf_counter_ns()
        operation()
        timings.append((perf_counter_ns() - started) / 1_000_000)
    p50, p95 = np.percentile(np.asarray(timings), [50, 95]).tolist()
    return SloMeasurement(
        metric_id=metric_id,
        p50_ms=float(p50),
        p95_ms=float(p95),
        target_ms=target_ms,
        sample_count=samples,
        workload=workload,
        status=SloStatus.PASSED if p95 <= target_ms else SloStatus.FAILED,
    )
