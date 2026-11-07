"""Fully offline, bounded-thread M1 performance benchmark."""

import os

from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
import platform
from statistics import median
from time import perf_counter
from typing import Any

import duckdb
from fastapi.testclient import TestClient
import lightgbm as lgb
import numpy as np
from numpy.typing import NDArray
import pandas as pd
import psutil  # type: ignore[import-untyped]
import pyarrow as pa
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from stock_prediction.api.app import create_app
from stock_prediction.contracts.capability import (
    CapabilitySnapshot,
    CapabilityStatus,
    DataCapability,
    PointInTimeStatus,
)
from stock_prediction.contracts.health import (
    Completeness,
    ComponentHealth,
    Connectivity,
    Freshness,
    Readiness,
)


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Stable workload controls; timing values remain observational."""

    symbols: int = 100
    horizons: int = 3
    history_rows_per_symbol: int = 1_250
    training_rows: int = 4_096
    warmups: int = 2
    repeats: int = 10
    seed: int = 20260711
    duckdb_threads: int = min(8, os.cpu_count() or 1)
    lightgbm_threads: int = min(12, os.cpu_count() or 1)
    blas_threads: int = 1

    def __post_init__(self) -> None:
        if min(
            self.symbols,
            self.horizons,
            self.history_rows_per_symbol,
            self.training_rows,
            self.repeats,
            self.duckdb_threads,
            self.lightgbm_threads,
            self.blas_threads,
        ) <= 0 or self.warmups < 0:
            raise ValueError("benchmark sizes must be positive and warmups non-negative")


@dataclass(frozen=True, slots=True)
class SyntheticWorkload:
    """In-memory data used by all benchmark components."""

    arrow_table: pa.Table
    training_features: pd.DataFrame
    training_labels: NDArray[np.int64]
    prediction_features: pd.DataFrame


def make_synthetic_workload(config: BenchmarkConfig) -> SyntheticWorkload:
    """Create deterministic data without reading any external source."""

    generator = np.random.default_rng(config.seed)
    feature_names = [f"feature_{index}" for index in range(8)]
    training_values = generator.normal(size=(config.training_rows, 8))
    training_features = pd.DataFrame(training_values, columns=feature_names)
    score = training_values[:, 0] + 0.5 * training_values[:, 1]
    training_labels = (score > np.median(score)).astype(np.int64)
    batch_rows = config.symbols * config.horizons
    prediction_values = generator.normal(size=(batch_rows, 8))
    prediction_features = pd.DataFrame(prediction_values, columns=feature_names)
    history_symbols = [
        f"US.S{symbol_index:03d}"
        for symbol_index in range(config.symbols)
        for _session in range(config.history_rows_per_symbol)
    ]
    history_rows = config.symbols * config.history_rows_per_symbol
    arrow_table = pa.table(
        {
            "symbol": history_symbols,
            "session_index": list(range(config.history_rows_per_symbol))
            * config.symbols,
            "feature_value": generator.normal(size=history_rows),
        }
    )
    return SyntheticWorkload(
        arrow_table=arrow_table,
        training_features=training_features,
        training_labels=training_labels,
        prediction_features=prediction_features,
    )


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _measure(
    operation: Any,
    *,
    rows: int,
    warmups: int,
    repeats: int,
) -> dict[str, object]:
    for _ in range(warmups):
        operation()
    durations: list[float] = []
    wall_start = perf_counter()
    for _ in range(repeats):
        started = perf_counter()
        operation()
        durations.append((perf_counter() - started) * 1000)
    wall_time_ms = (perf_counter() - wall_start) * 1000
    p50_ms = median(durations)
    throughput = rows / (p50_ms / 1000) if p50_ms > 0 else 0.0
    return {
        "samples": repeats,
        "p50_ms": p50_ms,
        "p95_ms": _percentile(durations, 0.95),
        "throughput_rows_per_second": throughput,
        "wall_time_ms": wall_time_ms,
        "status": "not_comparable",
        "target_note": "No matching M1 blueprint threshold for this component smoke",
    }


def _api_client() -> TestClient:
    probed_at = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    snapshot = CapabilitySnapshot(
        snapshot_id="benchmark-capability",
        probed_at=probed_at,
        opend_version="synthetic-not-used",
        sdk_version="synthetic-not-used",
        capabilities=[
            DataCapability(
                interface="benchmark_fixture",
                market="GLOBAL",
                asset_type="system",
                timeframe=None,
                status=CapabilityStatus.AVAILABLE,
                point_in_time_status=PointInTimeStatus.NOT_APPLICABLE,
                probed_at=probed_at,
            )
        ],
    )

    class StaticStore:
        def load_latest(self) -> CapabilitySnapshot:
            return snapshot

    ready = ComponentHealth(
        component="persisted_capability",
        connectivity=Connectivity.NOT_APPLICABLE,
        freshness=Freshness.CURRENT,
        completeness=Completeness.COMPLETE,
        readiness=Readiness.READY,
    )
    app = create_app(
        capability_store=StaticStore(),
        state_provider=lambda: [ready],
        clock=lambda: probed_at,
        request_id_factory=lambda: "benchmark-request",
    )
    return TestClient(app)


def _require_health_success(response: Any) -> None:
    try:
        payload = response.json()
    except Exception:
        raise RuntimeError("unexpected health response semantics") from None
    components = payload.get("components", []) if isinstance(payload, dict) else []
    if (
        response.status_code != 200
        or payload.get("service_status") != "ready"
        or payload.get("data_status") != "ready"
        or len(components) != 1
        or components[0].get("component") != "persisted_capability"
        or components[0].get("readiness") != "ready"
    ):
        raise RuntimeError("unexpected health response semantics")


def _require_capability_success(response: Any) -> None:
    try:
        payload = response.json()
    except Exception:
        raise RuntimeError("unexpected capability response semantics") from None
    snapshot = payload.get("snapshot", {}) if isinstance(payload, dict) else {}
    if (
        response.status_code != 200
        or payload.get("data_status") != "ready"
        or snapshot.get("snapshot_id") != "benchmark-capability"
    ):
        raise RuntimeError("unexpected capability response semantics")


def run_m1_benchmark(
    config: BenchmarkConfig | None = None,
) -> dict[str, Any]:
    """Run the synthetic M1 benchmark and return JSON-safe observations."""

    active = config or BenchmarkConfig()
    total_started = perf_counter()
    workload = make_synthetic_workload(active)
    batch_rows = active.symbols * active.horizons
    scan_rows = active.symbols * active.history_rows_per_symbol
    with ExitStack() as resources:
        duck_connection = duckdb.connect(
            config={"threads": active.duckdb_threads}
        )
        resources.callback(duck_connection.close)
        duck_connection.register("benchmark_arrow", workload.arrow_table)

        def scan_arrow() -> None:
            rows = duck_connection.execute(
                "SELECT symbol, avg(feature_value) "
                "FROM benchmark_arrow GROUP BY symbol"
            ).fetchall()
            if len(rows) != active.symbols:
                raise RuntimeError("unexpected aggregate shape")

        training_started = perf_counter()
        model = lgb.LGBMClassifier(
            n_estimators=24,
            max_depth=4,
            learning_rate=0.1,
            random_state=active.seed,
            n_jobs=active.lightgbm_threads,
            deterministic=True,
            verbosity=-1,
        )
        with threadpool_limits(limits=active.blas_threads, user_api="blas"):
            model.fit(workload.training_features, workload.training_labels)
        model_training_ms = (perf_counter() - training_started) * 1000

        def predict_batch() -> None:
            probabilities = model.predict_proba(workload.prediction_features)[:, 1]
            if (
                probabilities.shape != (batch_rows,)
                or not np.isfinite(probabilities).all()
                or not ((probabilities >= 0) & (probabilities <= 1)).all()
            ):
                raise RuntimeError("invalid probability batch")

        client = _api_client()
        resources.callback(client.close)

        def get_health() -> None:
            _require_health_success(client.get("/api/v1/health"))

        def get_capabilities() -> None:
            _require_capability_success(
                client.get("/api/v1/data-capabilities")
            )

        with threadpool_limits(limits=active.blas_threads, user_api="blas"):
            measurements = {
                "duckdb_arrow": _measure(
                    scan_arrow,
                    rows=scan_rows,
                    warmups=active.warmups,
                    repeats=active.repeats,
                ),
                "lightgbm_batch": _measure(
                    predict_batch,
                    rows=batch_rows,
                    warmups=active.warmups,
                    repeats=active.repeats,
                ),
                "api_health": _measure(
                    get_health,
                    rows=1,
                    warmups=active.warmups,
                    repeats=active.repeats,
                ),
                "api_capabilities": _measure(
                    get_capabilities,
                    rows=1,
                    warmups=active.warmups,
                    repeats=active.repeats,
                ),
            }

    return {
        "schema_version": "m1-benchmark-v1",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "environment": {
            "cpu_logical": psutil.cpu_count(logical=True),
            "cpu_physical": psutil.cpu_count(logical=False),
            "memory_bytes": psutil.virtual_memory().total,
            "python_version": platform.python_version(),
            "dependency_versions": {
                name: version(name)
                for name in ("duckdb", "lightgbm", "numpy", "pyarrow", "fastapi")
            },
        },
        "thread_budget": {
            "duckdb": active.duckdb_threads,
            "lightgbm": active.lightgbm_threads,
            "blas": active.blas_threads,
        },
        "execution_mode": "sequential_components",
        "workload": {
            "seed": active.seed,
            "symbols": active.symbols,
            "horizons": active.horizons,
            "batch_rows": batch_rows,
            "history_rows_per_symbol": active.history_rows_per_symbol,
            "scan_rows": scan_rows,
            "training_rows": active.training_rows,
            "warmups": active.warmups,
            "repeats": active.repeats,
        },
        "setup": {"model_training_ms": model_training_ms},
        "measurements": measurements,
        "total_wall_time_ms": (perf_counter() - total_started) * 1000,
    }
