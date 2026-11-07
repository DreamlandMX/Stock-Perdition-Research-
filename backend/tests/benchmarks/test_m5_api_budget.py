"""Acceptance tests for the repository-backed M5 read-path benchmark."""

from __future__ import annotations

import json
from math import isfinite
from pathlib import Path

from backend.tests.fixtures.research_snapshot_factory import write_valid_snapshot


def test_m5_benchmark_reports_exact_snapshot_and_read_path_invariants(
    tmp_path: Path,
) -> None:
    from stock_prediction.benchmarks.m5 import M5BenchmarkConfig, run_m5_benchmark

    write_valid_snapshot(tmp_path)
    result = run_m5_benchmark(
        tmp_path,
        M5BenchmarkConfig(warmups=1, repeats_per_endpoint=5),
    )

    assert result["schema_version"] == "m5-api-benchmark-v1"
    assert result["workload"] == {
        "symbols": 100,
        "warmups": 1,
        "repeats_per_endpoint": 5,
        "total_measured_requests": 10,
    }
    assert result["snapshot"]["snapshot_id"] == "snapshot-20260710-r1"
    assert result["snapshot"]["size_bytes"] > 0
    assert len(result["snapshot"]["tree_sha256"]) == 64
    assert result["read_path_invariants"] == {
        "filesystem_unchanged": True,
        "model_loads": 0,
        "opend_calls": 0,
        "request_filesystem_writes": 0,
    }
    assert result["peak_rss_bytes"] > 0
    assert set(result["measurements"]) == {"forecast", "screen"}
    for measurement in result["measurements"].values():
        assert measurement["samples"] == 5
        assert measurement["http_status"] == 200
        assert measurement["status"] == "meets", measurement
        for field in ("p50_ms", "p95_ms", "p99_ms"):
            assert isfinite(measurement[field])
            assert measurement[field] >= 0
    json.dumps(result, allow_nan=False)


def test_m5_acceptance_budget_uses_at_least_500_total_requests(tmp_path: Path) -> None:
    from stock_prediction.benchmarks.m5 import M5BenchmarkConfig, run_m5_benchmark

    write_valid_snapshot(tmp_path)
    result = run_m5_benchmark(tmp_path, M5BenchmarkConfig())

    assert result["workload"]["repeats_per_endpoint"] == 250
    assert result["workload"]["total_measured_requests"] == 500
    assert result["measurements"]["screen"]["p95_ms"] <= 300
    assert result["measurements"]["forecast"]["p95_ms"] <= 500


def test_m5_benchmark_source_has_no_opend_model_or_write_path() -> None:
    source = Path("backend/src/stock_prediction/benchmarks/m5.py").read_text(
        encoding="utf-8"
    )

    forbidden = (
        "OpenQuoteContext",
        "request_history_kline",
        "import futu",
        "load_model",
        "materialize_research_snapshot",
        "mlflow",
    )
    assert all(term not in source for term in forbidden)
