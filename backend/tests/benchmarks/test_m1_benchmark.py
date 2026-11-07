"""Correctness tests for the fully offline M1 benchmark."""

import json
from math import isfinite
from pathlib import Path

import numpy as np
import pytest


def small_config() -> object:
    from stock_prediction.benchmarks.m1 import BenchmarkConfig

    return BenchmarkConfig(
        symbols=8,
        horizons=3,
        history_rows_per_symbol=4,
        training_rows=96,
        warmups=1,
        repeats=2,
        seed=20260711,
        duckdb_threads=1,
        lightgbm_threads=1,
        blas_threads=1,
    )


def test_synthetic_workload_is_fixed_seed_and_has_batch_shape() -> None:
    from stock_prediction.benchmarks.m1 import make_synthetic_workload

    first = make_synthetic_workload(small_config())
    second = make_synthetic_workload(small_config())

    assert first.prediction_features.shape == (24, 8)
    assert first.arrow_table.num_rows == 32
    assert np.array_equal(first.training_features, second.training_features)
    assert np.array_equal(first.training_labels, second.training_labels)
    assert np.array_equal(first.prediction_features, second.prediction_features)


def test_benchmark_returns_finite_structured_offline_metrics() -> None:
    from stock_prediction.benchmarks.m1 import run_m1_benchmark

    result = run_m1_benchmark(small_config())

    assert result["schema_version"] == "m1-benchmark-v1"
    assert result["workload"]["batch_rows"] == 24
    assert result["thread_budget"] == {"duckdb": 1, "lightgbm": 1, "blas": 1}
    assert set(result["measurements"]) == {
        "duckdb_arrow",
        "lightgbm_batch",
        "api_health",
        "api_capabilities",
    }
    for measurement in result["measurements"].values():
        assert measurement["samples"] == 2
        assert measurement["status"] in {
            "meets",
            "needs_tuning",
            "not_comparable",
        }
        for field in (
            "p50_ms",
            "p95_ms",
            "throughput_rows_per_second",
            "wall_time_ms",
        ):
            assert isfinite(measurement[field])
            assert measurement[field] >= 0
    assert isfinite(result["total_wall_time_ms"])
    json.dumps(result, allow_nan=False)


def test_default_benchmark_uses_bounded_blueprint_thread_budget() -> None:
    import os

    from stock_prediction.benchmarks.m1 import BenchmarkConfig

    config = BenchmarkConfig()
    logical_cpus = os.cpu_count() or 1

    assert config.duckdb_threads == min(8, logical_cpus)
    assert config.lightgbm_threads == min(12, logical_cpus)
    assert config.blas_threads == 1
    assert config.history_rows_per_symbol == 1_250


def test_benchmark_source_cannot_import_or_call_opend() -> None:
    source = Path("backend/src/stock_prediction/benchmarks/m1.py").read_text(
        encoding="utf-8"
    )

    forbidden = (
        "futu",
        "OpenQuoteContext",
        "CapabilityProbe",
        "request_history_kline",
        "account",
        "fit_model_to_disk",
    )
    assert all(term not in source for term in forbidden)


def test_benchmark_closes_duckdb_when_model_training_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stock_prediction.benchmarks.m1 as benchmark_module

    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False

        def register(self, _name: str, _table: object) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    class FailingModel:
        def fit(self, _features: object, _labels: object) -> None:
            raise RuntimeError("synthetic training failure")

    connection = FakeConnection()
    monkeypatch.setattr(benchmark_module.duckdb, "connect", lambda **_kwargs: connection)
    monkeypatch.setattr(
        benchmark_module.lgb,
        "LGBMClassifier",
        lambda **_kwargs: FailingModel(),
    )

    with pytest.raises(RuntimeError, match="synthetic training failure"):
        benchmark_module.run_m1_benchmark(small_config())

    assert connection.closed is True


def test_api_benchmark_validates_ready_fixture_payloads() -> None:
    from stock_prediction.benchmarks.m1 import (
        _api_client,
        _require_capability_success,
        _require_health_success,
    )

    with _api_client() as client:
        _require_health_success(client.get("/api/v1/health"))
        _require_capability_success(client.get("/api/v1/data-capabilities"))

        fallback = client.get("/api/v1/not-found")
        with pytest.raises(RuntimeError, match="health response semantics"):
            _require_health_success(fallback)
        with pytest.raises(RuntimeError, match="capability response semantics"):
            _require_capability_success(fallback)
