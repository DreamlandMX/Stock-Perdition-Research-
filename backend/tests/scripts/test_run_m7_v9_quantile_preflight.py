from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "run-m7-v9-quantile-preflight.py"
)


def _load_runner() -> object:
    assert SCRIPT_PATH.is_file(), "v9 preflight runner is missing"
    spec = importlib.util.spec_from_file_location("m7_v9_preflight_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_v9_preflight_fails_closed_before_source_data_without_partial_artifact(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    report = runner._run(  # type: ignore[attr-defined]
        argparse.Namespace(
            workspace_root=tmp_path,
            source_run_id="missing-source",
            output_run_id="v9-preflight-test",
        )
    )

    report_path = (
        tmp_path
        / "var"
        / "live"
        / "models"
        / "runs"
        / "v9-preflight-test"
        / "v9-quantile-preflight"
        / "preflight-report.json"
    )
    persisted = json.loads(report_path.read_text("utf-8"))
    files = tuple(path.name for path in report_path.parent.rglob("*") if path.is_file())

    assert report == persisted
    assert report["schema_version"] == "m7-v9-quantile-preflight-report-v1"
    assert report["recipe_id"] == "candidate-recipe-seed100-targeted-v9"
    assert report["status"] == "failed"
    assert report["failure"] == "m7_v9_preflight_source_dataset_unavailable"
    assert report["promotion_performed"] is False
    assert report["phase_timings"] == {}
    assert files == ("preflight-report.json",)


def test_v9_resource_sampler_counts_parent_and_descendant_memory(
    monkeypatch,
) -> None:
    runner = _load_runner()

    class FakeProcess:
        def __init__(
            self,
            rss: int,
            private: int,
            children: tuple["FakeProcess", ...] = (),
        ) -> None:
            self._rss = rss
            self._private = private
            self._children = children

        def memory_info(self) -> SimpleNamespace:
            return SimpleNamespace(rss=self._rss)

        def memory_full_info(self) -> SimpleNamespace:
            return SimpleNamespace(private=self._private)

        def children(self, *, recursive: bool) -> tuple["FakeProcess", ...]:
            assert recursive is True
            return self._children

    child_one = FakeProcess(20, 30)
    child_two = FakeProcess(40, 50)
    parent = FakeProcess(100, 200, (child_one, child_two))
    monkeypatch.setattr(runner.psutil, "Process", lambda: parent)  # type: ignore[attr-defined]

    sampler = runner._ResourceSampler()  # type: ignore[attr-defined]
    sampler._sample()

    assert sampler.peak_tree_rss_bytes == 160
    assert sampler.peak_tree_private_bytes == 280
    assert sampler.peak_child_count == 2
