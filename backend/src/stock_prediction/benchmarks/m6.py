"""M6 100-symbol local SLO benchmark after OpenD data is available."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import os
from pathlib import Path
import platform
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import psutil  # type: ignore[import-untyped]
import pyarrow as pa
import pyarrow.parquet as pq
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from stock_prediction.contracts.research import CalendarSource
from stock_prediction.features.core import build_core_feature_table
from stock_prediction.operations.slo import measure_slo
from stock_prediction.research.primitives import MarketPrimitives


@dataclass(frozen=True, slots=True)
class M6BenchmarkConfig:
    symbols: int = 100
    horizons: int = 3
    warmups: int = 2
    samples: int = 5
    seed: int = 20260721
    lightgbm_threads: int = min(12, os.cpu_count() or 1)
    blas_threads: int = 1

    def __post_init__(self) -> None:
        if (
            min(
                self.symbols,
                self.horizons,
                self.samples,
                self.lightgbm_threads,
                self.blas_threads,
            )
            <= 0
            or self.warmups < 0
        ):
            raise ValueError("M6 benchmark configuration is invalid")


def run_m6_benchmark(
    work_root: Path,
    config: M6BenchmarkConfig | None = None,
) -> dict[str, Any]:
    active = config or M6BenchmarkConfig()
    root = work_root.resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("M6 benchmark root must be empty")
    root.mkdir(parents=True, exist_ok=True)
    generator = np.random.default_rng(active.seed)
    symbols = [f"US.S{index:03d}" for index in range(active.symbols)]
    close = generator.lognormal(mean=5.0, sigma=0.2, size=active.symbols)
    day_end = pa.table(
        {
            "symbol": symbols,
            "session_date": [date(2026, 7, 20)] * active.symbols,
            "close": close,
            "volume": generator.integers(1_000_000, 20_000_000, active.symbols),
            "dataset_revision": ["dataset-v1"] * active.symbols,
        }
    )
    run_number = 0

    def sync_day_end() -> None:
        nonlocal run_number
        run_number += 1
        staging = root / f"sync-{run_number:03d}.staging"
        final = root / f"sync-{run_number:03d}"
        pq.write_to_dataset(
            day_end,
            root_path=staging,
            partition_cols=["symbol"],
            compression="zstd",
        )
        if len(list(staging.rglob("*.parquet"))) != active.symbols:
            raise RuntimeError("day-end sync did not publish every symbol partition")
        os.replace(staging, final)

    primitives = _primitives(symbols)

    def build_features() -> None:
        table = build_core_feature_table(
            primitives,
            feature_set_version="feature-v1",
            universe_version="universe-v1",
        )
        if table.num_rows != active.symbols:
            raise RuntimeError("feature batch does not contain every symbol")

    feature_names = [f"feature_{index}" for index in range(12)]
    training = pd.DataFrame(
        generator.normal(size=(4_096, len(feature_names))), columns=feature_names
    )
    labels = (training["feature_0"] + training["feature_1"] > 0).astype(int)
    prediction = pd.DataFrame(
        generator.normal(size=(active.symbols * active.horizons, len(feature_names))),
        columns=feature_names,
    )
    model = lgb.LGBMClassifier(
        n_estimators=32,
        max_depth=5,
        learning_rate=0.08,
        random_state=active.seed,
        n_jobs=active.lightgbm_threads,
        deterministic=True,
        verbosity=-1,
    )
    with threadpool_limits(limits=active.blas_threads, user_api="blas"):
        model.fit(training, labels)

    def predict_batch() -> None:
        probabilities = model.predict_proba(prediction)[:, 1]
        if probabilities.shape != (active.symbols * active.horizons,):
            raise RuntimeError("prediction batch shape is invalid")
        if not np.isfinite(probabilities).all():
            raise RuntimeError("prediction batch is non-finite")

    with threadpool_limits(limits=active.blas_threads, user_api="blas"):
        measurements = (
            measure_slo(
                metric_id="day-end-sync",
                workload="100 symbol partition publication after OpenD data availability",
                target_ms=300_000,
                operation=sync_day_end,
                warmups=active.warmups,
                samples=active.samples,
            ),
            measure_slo(
                metric_id="incremental-features",
                workload="100 symbol Arrow core feature batch",
                target_ms=120_000,
                operation=build_features,
                warmups=active.warmups,
                samples=active.samples,
            ),
            measure_slo(
                metric_id="batch-prediction",
                workload="one loaded LightGBM model over 100 symbols x 3 horizons",
                target_ms=60_000,
                operation=predict_batch,
                warmups=active.warmups,
                samples=active.samples,
            ),
        )
    return {
        "schema_version": "m6-slo-benchmark-v1",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_logical": psutil.cpu_count(logical=True),
            "cpu_physical": psutil.cpu_count(logical=False),
            "memory_bytes": psutil.virtual_memory().total,
        },
        "thread_budget": {
            "lightgbm": active.lightgbm_threads,
            "blas": active.blas_threads,
            "execution": "sequential",
        },
        "workload": {
            "symbols": active.symbols,
            "horizons": active.horizons,
            "prediction_rows": active.symbols * active.horizons,
            "warmups": active.warmups,
            "samples": active.samples,
            "data_source": "deterministic_offline_shape",
            "opend_calls": 0,
        },
        "measurements": [item.model_dump(mode="json") for item in measurements],
        "all_passed": all(item.status.value == "passed" for item in measurements),
        "scope_note": (
            "The day-end timer begins after OpenD data is available; network and quota "
            "wait time remain separately observable and are not hidden in this benchmark."
        ),
    }


def _primitives(symbols: list[str]) -> tuple[MarketPrimitives, ...]:
    timestamp = datetime(2026, 7, 20, 20, 5, tzinfo=UTC)
    values: list[MarketPrimitives] = []
    for index, symbol in enumerate(symbols):
        offset = index / 10_000
        values.append(
            MarketPrimitives(
                symbol=symbol,
                asset_group="stock",
                session_date=date(2026, 7, 20),
                source_max_available_at=timestamp,
                source_max_ingested_at=timestamp,
                dataset_revision="dataset-v1",
                adjustment_revision="adjustment-v1",
                feature_set_version="feature-v1",
                calendar_version="calendar-v1",
                calendar_hash="a" * 64,
                calendar_source=CalendarSource.SYNTHETIC_OFFLINE,
                anchor_close=100 + index,
                return_1=0.001 + offset,
                return_5=0.005 + offset,
                return_20=0.02 + offset,
                prior_high_20=101 + index,
                prior_low_20=99 + index,
                atr20=2.0,
                channel_width20=0.04,
                efficiency_ratio20=0.5,
                normalized_slope20=0.01,
                trend_r2_20=0.6,
                realized_volatility20=0.2,
                downside_volatility20=0.1,
                max_drawdown20=-0.05,
                max_up_move20=0.08,
                channel_position20=0.5,
                ma20=100 + index,
                ma50=99 + index,
                distance_ma20=0.01,
                distance_ma50=0.02,
                bollinger_bandwidth20=0.05,
                atr_percent20=0.02,
                volume_ratio20=1.1,
                turnover_ratio20=1.0,
                data_coverage20=1.0,
                missing_session_count20=0,
                valid_core=True,
            )
        )
    return tuple(values)


__all__ = ["M6BenchmarkConfig", "run_m6_benchmark"]
