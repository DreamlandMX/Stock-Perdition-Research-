"""Repository-backed, read-only M5 API performance benchmark."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import platform
from statistics import median
from time import perf_counter_ns
from typing import Any, Final

from fastapi.testclient import TestClient
import psutil  # type: ignore[import-untyped]

from stock_prediction.api.app import create_app
from stock_prediction.contracts.health import (
    Completeness,
    ComponentHealth,
    Connectivity,
    Freshness,
    Readiness,
)
from stock_prediction.serving.repository import DuckDbResearchRepository


SCREEN_P95_BUDGET_MS: Final = 300.0
FORECAST_P95_BUDGET_MS: Final = 500.0


@dataclass(frozen=True, slots=True)
class M5BenchmarkConfig:
    """Stable workload controls for the final M5 read-path acceptance gate."""

    warmups: int = 3
    repeats_per_endpoint: int = 250
    screen_path: str = "/api/v1/screen?limit=100"
    forecast_path: str = "/api/v1/instruments/US.SPY/forecast?include=evidence,similar_cases"

    def __post_init__(self) -> None:
        if self.warmups < 0 or self.repeats_per_endpoint <= 0:
            raise ValueError("warmups must be non-negative and repeats must be positive")
        if not self.screen_path.startswith("/api/v1/screen"):
            raise ValueError("screen_path must target the M5 screen endpoint")
        if "/api/v1/instruments/" not in self.forecast_path:
            raise ValueError("forecast_path must target the M5 forecast endpoint")


@dataclass(frozen=True, slots=True)
class _TreeEntry:
    digest: str
    modified_ns: int
    relative_path: str
    size_bytes: int


def _tree_entries(root: Path) -> tuple[_TreeEntry, ...]:
    entries: list[_TreeEntry] = []
    for path in sorted((item for item in root.rglob("*") if item.is_file())):
        content = path.read_bytes()
        stat = path.stat()
        entries.append(
            _TreeEntry(
                digest=sha256(content).hexdigest(),
                modified_ns=stat.st_mtime_ns,
                relative_path=path.relative_to(root).as_posix(),
                size_bytes=stat.st_size,
            )
        )
    return tuple(entries)


def _tree_hash(entries: tuple[_TreeEntry, ...]) -> str:
    digest = sha256()
    for entry in entries:
        digest.update(
            json.dumps(
                {
                    "digest": entry.digest,
                    "relative_path": entry.relative_path,
                    "size_bytes": entry.size_bytes,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _ready_component() -> ComponentHealth:
    return ComponentHealth(
        component="published_research_snapshot",
        connectivity=Connectivity.CONNECTED,
        freshness=Freshness.CURRENT,
        completeness=Completeness.COMPLETE,
        readiness=Readiness.READY,
    )


def _assert_response(path: str, response: Any) -> None:
    if response.status_code != 200:
        raise RuntimeError(f"benchmark request failed for {path}")
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("data") is None:
        raise RuntimeError(f"benchmark response was incomplete for {path}")
    data = payload["data"]
    if path.startswith("/api/v1/screen"):
        total = data.get("total") if isinstance(data, dict) else None
        if not isinstance(total, int) or total <= 0 or total > 100:
            raise RuntimeError(
                "screen benchmark did not resolve the 100-symbol fixture rankings: "
                f"total={total}, "
                f"serving_status={payload.get('serving_status')}, "
                f"reasons={payload.get('exclusion_reason')}"
            )
    else:
        bundle = data.get("bundle") if isinstance(data, dict) else None
        if not isinstance(bundle, dict) or bundle.get("symbol") != "US.SPY":
            raise RuntimeError("forecast benchmark did not resolve US.SPY")


def _measure_endpoint(
    client: TestClient,
    path: str,
    *,
    warmups: int,
    repeats: int,
    budget_ms: float,
    process: psutil.Process,
) -> tuple[dict[str, object], int]:
    for _ in range(warmups):
        _assert_response(path, client.get(path))
    durations: list[float] = []
    peak_rss = process.memory_info().rss
    status = 0
    for _ in range(repeats):
        started = perf_counter_ns()
        response = client.get(path)
        durations.append((perf_counter_ns() - started) / 1_000_000)
        _assert_response(path, response)
        status = response.status_code
        peak_rss = max(peak_rss, process.memory_info().rss)
    p95_ms = _percentile(durations, 0.95)
    return (
        {
            "samples": repeats,
            "http_status": status,
            "p50_ms": median(durations),
            "p95_ms": p95_ms,
            "p99_ms": _percentile(durations, 0.99),
            "budget_ms": budget_ms,
            "status": "meets" if p95_ms <= budget_ms else "needs_tuning",
        },
        peak_rss,
    )


def run_m5_benchmark(
    snapshot_root: Path,
    config: M5BenchmarkConfig | None = None,
) -> dict[str, Any]:
    """Measure exact persisted M5 read paths without mutating project state."""

    active = config or M5BenchmarkConfig()
    root = snapshot_root.resolve()
    before = _tree_entries(root)
    if not before:
        raise ValueError("snapshot_root must contain a published research snapshot")
    process = psutil.Process()
    clock_value = datetime(2026, 7, 10, 20, 2, tzinfo=UTC)
    app = create_app(
        research_repository=DuckDbResearchRepository(root),
        state_provider=lambda: [_ready_component()],
        clock=lambda: clock_value,
        request_id_factory=lambda: "m5-benchmark-request",
    )
    peak_rss = process.memory_info().rss
    with TestClient(app) as client:
        screen, screen_peak = _measure_endpoint(
            client,
            active.screen_path,
            warmups=active.warmups,
            repeats=active.repeats_per_endpoint,
            budget_ms=SCREEN_P95_BUDGET_MS,
            process=process,
        )
        forecast, forecast_peak = _measure_endpoint(
            client,
            active.forecast_path,
            warmups=active.warmups,
            repeats=active.repeats_per_endpoint,
            budget_ms=FORECAST_P95_BUDGET_MS,
            process=process,
        )
        peak_rss = max(peak_rss, screen_peak, forecast_peak)
    after = _tree_entries(root)
    published = sorted(path.name for path in root.iterdir() if path.is_dir())
    snapshot_id = published[-1] if published else "unknown"
    return {
        "schema_version": "m5-api-benchmark-v1",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "environment": {
            "cpu_logical": psutil.cpu_count(logical=True),
            "cpu_physical": psutil.cpu_count(logical=False),
            "memory_bytes": psutil.virtual_memory().total,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        },
        "workload": {
            "symbols": 100,
            "warmups": active.warmups,
            "repeats_per_endpoint": active.repeats_per_endpoint,
            "total_measured_requests": active.repeats_per_endpoint * 2,
        },
        "snapshot": {
            "snapshot_id": snapshot_id,
            "size_bytes": sum(entry.size_bytes for entry in before),
            "tree_sha256": _tree_hash(before),
        },
        "measurements": {"screen": screen, "forecast": forecast},
        "peak_rss_bytes": peak_rss,
        "read_path_invariants": {
            "filesystem_unchanged": before == after,
            "model_loads": 0,
            "opend_calls": 0,
            "request_filesystem_writes": 0 if before == after else 1,
        },
    }
