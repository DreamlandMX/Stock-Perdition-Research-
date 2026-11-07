from io import StringIO
import json
from pathlib import Path

from stock_prediction.cli import main


def test_m3_offline_demo_builds_audited_dataset_without_live_services(
    tmp_path: Path,
) -> None:
    work_root = tmp_path / "m3-demo"
    stdout = StringIO()

    code = main(
        ["m3-offline-demo", "--work-root", str(work_root)],
        stdout=stdout,
    )

    assert code == 0
    result = json.loads(stdout.getvalue())
    assert result["status"] == "ok"
    assert result["symbol_count"] == 2
    assert result["sample_count"] == 150
    assert result["future_feature_sentinel_passed"] is True
    assert result["forward_only_sentinel_passed"] is True
    assert result["unresolved_target_count"] == 0
    assert len(result["dataset_input_hash"]) == 64
    assert result["calendar_evidence"]["calendar_source"] == "synthetic_offline"
    assert len(result["calendar_evidence"]["calendar_hash"]) == 64
    assert len(result["feature_artifact_hashes"]) == 50
    assert result["engineering_path_audit"] == {
        "batch_core_row_count": 50,
        "batch_primitive_symbol_count": 2,
        "breadth_constituent_count": 2,
        "breadth_partial": False,
        "dag_invalidation_preview_count": 2,
        "intraday_complete_hour_count": 6,
        "relative_aligned_session_count": 20,
        "relative_partial": False,
    }
    assert (work_root / "m3-audit-report.json").is_file()
    assert len(list((work_root / "features").rglob("artifact.parquet"))) == 50


def test_m3_command_failure_is_sanitized(tmp_path: Path) -> None:
    work_root = tmp_path / "not-empty"
    work_root.mkdir()
    (work_root / "existing.txt").write_text("occupied", encoding="utf-8")
    stdout = StringIO()
    stderr = StringIO()

    code = main(
        ["m3-offline-demo", "--work-root", str(work_root)],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "error_code=m3_command_failed\n"
