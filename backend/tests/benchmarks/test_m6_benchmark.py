from pathlib import Path

from stock_prediction.benchmarks.m6 import M6BenchmarkConfig, run_m6_benchmark


def test_m6_benchmark_exercises_100_symbol_shape_with_small_repeat_count(
    tmp_path: Path,
) -> None:
    result = run_m6_benchmark(
        tmp_path / "benchmark",
        M6BenchmarkConfig(symbols=100, horizons=3, warmups=0, samples=1),
    )
    assert result["all_passed"] is True
    assert result["workload"]["symbols"] == 100
    assert result["workload"]["prediction_rows"] == 300
    assert [item["metric_id"] for item in result["measurements"]] == [
        "day-end-sync",
        "incremental-features",
        "batch-prediction",
    ]
    assert len(list((tmp_path / "benchmark").rglob("*.parquet"))) == 100
