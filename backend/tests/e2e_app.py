"""Deterministic, test-only full-stack application for M5 browser acceptance."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from backend.tests.api.test_research_routes import make_research_app
from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_status import build_m7_lifecycle_status
from stock_prediction.options.store import OptionSnapshotStore


_snapshot_directory = TemporaryDirectory(prefix="prediction-stock-m5-e2e-")
_workspace = Path(_snapshot_directory.name)
_live_paths = LivePaths.from_workspace(_workspace)
app = make_research_app(
    _workspace,
    options_store=OptionSnapshotStore(Path.cwd() / "var" / "options"),
    request_id="m5-e2e-request",
    m7_status_provider=lambda: build_m7_lifecycle_status(_live_paths),
)
