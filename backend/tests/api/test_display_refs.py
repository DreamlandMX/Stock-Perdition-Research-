"""Display references enrich reads without rewriting historical evidence."""

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.tests.api.test_research_routes import make_research_app

from stock_prediction.api.display_refs import (
    enrich_display_refs,
    operation_display_refs,
)
from stock_prediction.api.app import create_app
from stock_prediction.live.config import LivePaths
from stock_prediction.live.operation_store import EodOperationStore
from stock_prediction.contracts.operations import UpdateOperation, UpdateOperationStatus
from stock_prediction.contracts.serving import (
    CandidateEvaluationSummary,
    DataHealth,
    HistoryEvent,
    ModelTaskMatrixRow,
    UniverseVersion,
)


NOW = datetime(2026, 10, 30, 4, tzinfo=UTC)


def event(
    label: str, kind: str = "split", details: tuple[str, ...] = ()
) -> HistoryEvent:
    return HistoryEvent(
        event_id="event-1",
        occurred_at=NOW,
        event_type=kind,
        label=label,
        details=details,
    )


def health(message: str, action: str) -> DataHealth:
    return DataHealth.model_validate_json(
        json.dumps(
            {
                "component": "market_bars",
                "dataset": "bars_60m",
                "symbol": "US.AAPL",
                "serving_status": "partial",
                "connectivity": "not_applicable",
                "freshness": "current",
                "completeness": "partial",
                "readiness": "degraded",
                "last_success_at": None,
                "expected_next_at": None,
                "watermark": None,
                "error_class": "historical_source_gap",
                "message": message,
                "action": action,
                "affected_symbols": [],
                "affected_datasets": [],
            }
        )
    )


def test_legacy_fields_are_omitted_and_source_is_unchanged() -> None:
    source = event("拆并股：每 1 股变为 4 股")
    before = source.model_dump_json()
    assert "label_ref" not in source.model_dump()
    result = enrich_display_refs(source)
    assert result.label_ref.key == "common.backend.event.split"
    assert result.label_ref.params == {"ratio": "4"}
    assert source.model_dump_json() == before
    assert result.label == source.label
    assert HistoryEvent.model_validate_json(before).model_dump_json() == before


@pytest.mark.parametrize(
    "label,kind",
    [
        ("拆并股：每 1 股变为 0 股", "split"),
        ("拆并股：每 1 股变为 nan 股", "split"),
        ("拆并股：每 1 股变为 1 股", "split"),
        ("拆并股：每 1 股变为 4 股 trailing", "split"),
        ("拆并股：每 1 股变为 4 股", "dividend"),
        ("每股现金分红（供应商原值）：-2", "dividend"),
        ("每股现金分红（供应商原值）：1e309", "dividend"),
    ],
)
def test_invalid_legacy_event_facts_use_generic_ref(label: str, kind: str) -> None:
    result = enrich_display_refs(event(label, kind))
    assert result.label_ref.key == "common.backend.event.original"
    assert not result.label_ref.params
    assert result.label == label


def test_legacy_event_details_are_validated_and_retained() -> None:
    details = (
        "除权息日期：2026-10-30；日期定位，不代表盘中公告时间。",
        "来源快照：rehab-123；观察时间：2026-10-31T12:00:00+00:00",
        "供应商回溯记录，不代表事件当时已知；不改变原始价格或生成总回报。",
        "除权息日期：2026-02-30；日期定位，不代表盘中公告时间。",
        "除权息日期：2026-10-29；日期定位，不代表盘中公告时间。",
        "来源快照：rehab-123；观察时间：not-a-date",
        "unknown source text",
    )
    result = enrich_display_refs(
        event("每股现金分红（供应商原值）：1.25", "dividend", details)
    )
    assert result.label_ref.params == {"cash": "1.25"}
    assert [ref.key for ref in result.details_refs] == [
        "common.backend.event.effective_date",
        "common.backend.event.source",
        "common.backend.event.retrospective",
        *(["common.backend.event.original_detail"] * 4),
    ]
    assert result.details == details


def test_health_known_english_chinese_and_unknown_messages() -> None:
    rows = [
        (
            "The latest market-data task is incomplete or stale.",
            "Run live-refresh and inspect the task ledger.",
            "market_incomplete",
            "refresh_ledger",
        ),
        (
            "保留 42 个历史交易日的分钟线缺口；OpenD 返回空数据。最新收盘数据正常。",
            "已按用户要求保留原始缺口，后续查漏补缺。",
            "source_gap",
            "preserve_gap",
        ),
        (
            "No corporate-action snapshot has been captured.",
            "Run live-refresh to capture rehab evidence.",
            "rehab_missing",
            "refresh_rehab",
        ),
        (
            "unknown vendor diagnostic",
            "unknown vendor action",
            "original",
            "original_action",
        ),
    ]
    for message, action, message_key, action_key in rows:
        source = health(message, action)
        result = enrich_display_refs(source)
        assert result.message_ref.key == f"common.backend.health.{message_key}"
        assert result.action_ref.key == f"common.backend.health.{action_key}"
        assert result.message == message and result.action == action
    assert enrich_display_refs(health(rows[1][0], rows[1][1])).message_ref.params == {
        "count": 42
    }


def test_failed_tasks_require_matching_complete_same_snapshot_evaluation() -> None:
    summary = CandidateEvaluationSummary(
        run_id="run-1",
        model_version="candidate-v1",
        calibration_version="cal-v1",
        generated_at=NOW,
        passed_tasks=1,
        total_tasks=2,
        all_gates_passed=False,
        failed_tasks=("untrusted untranslated task display",),
    )

    def row(task: str, passed: bool) -> ModelTaskMatrixRow:
        return ModelTaskMatrixRow(
            task=task,
            horizon=5,
            asset_group="us_equity",
            champion_version=None,
            baseline_version="baseline-v1",
            challenger_version="candidate-v1",
            calibration_version=None,
            status="challenger_evaluated",
            unavailable_reason="gate_failed",
            gate_passed=passed,
            evaluation_run_id="run-1",
            candidate_generated_at=NOW,
            challenger_calibration_version="cal-v1",
        )

    rows = (row("direction", True), row("return_fwd", False))
    result = enrich_display_refs(summary, model_rows=rows)
    assert [(ref.task, ref.horizon) for ref in result.failed_task_refs] == [
        ("return_fwd", 5)
    ]
    assert summary.failed_task_refs is None
    assert result.failed_tasks == summary.failed_tasks
    manual_rows = tuple(
        item.model_copy(
            update={
                "challenger_version": None,
                "champion_version": f"manual-{item.task}",
            }
        )
        for item in rows
    )
    assert (
        enrich_display_refs(summary, model_rows=manual_rows).failed_task_refs
        == result.failed_task_refs
    )
    for wrong_rows in (
        rows[:1],
        tuple(r.model_copy(update={"evaluation_run_id": "other"}) for r in rows),
    ):
        assert (
            enrich_display_refs(summary, model_rows=wrong_rows).failed_task_refs is None
        )


def test_operation_refs_preserve_persisted_originals() -> None:
    source = UpdateOperation(
        operation_id="operation-1",
        status=UpdateOperationStatus.FAILED,
        progress_percent=0,
        created_at=NOW,
        updated_at=NOW,
        error_code="duckdb_file_locked",
        retry_recommendation="实时数据存储暂时繁忙；系统已停止并发访问，请重新提交收盘更新。",
    )
    before = source.model_dump_json()
    assert "retry_recommendation_ref" not in source.model_dump()
    result = operation_display_refs(source)
    assert result.retry_recommendation_ref.key == "common.backend.retry.duckdb_locked"
    assert result.retry_recommendation == source.retry_recommendation
    assert source.model_dump_json() == before


def test_unknown_universe_reason_preserves_original_without_guessing() -> None:
    source = UniverseVersion(
        universe_version="universe-v1",
        effective_from=NOW,
        effective_to=None,
        member_count=3,
        reason="unknown original reason",
        point_in_time_status="forward_only",
    )
    assert "reason_ref" not in source.model_dump()
    result = enrich_display_refs(source)
    assert result.reason_ref.key == "common.backend.universe.original"
    assert result.reason == source.reason


def test_published_historical_aapl_event_sample() -> None:
    # Copied from the immutable 2026-11-02 manual snapshot, not from a new publication.
    source = HistoryEvent.model_validate_json(
        json.dumps(
            {
                "event_id": "rehab-US-AAPL-20261030T221742Z-e7eb34adbf08-1987-06-16-split",
                "occurred_at": "1987-06-16T04:00:00Z",
                "event_type": "split",
                "label": "拆并股：每 1 股变为 2 股",
                "details": [
                    "除权息日期：1987-06-16；日期定位，不代表盘中公告时间。",
                    "来源快照：rehab-US-AAPL-20261030T221742Z-e7eb34adbf08；观察时间：2026-10-30T18:17:42.791384-04:00",
                    "供应商回溯记录，不代表事件当时已知；不改变原始价格或生成总回报。",
                ],
            }
        )
    )
    result = enrich_display_refs(source)
    assert result.label_ref.params == {"ratio": "2"}
    assert result.details_refs[0].params == {"date": "1987-06-16"}
    assert result.details_refs[1].params == {
        "snapshot": "rehab-US-AAPL-20261030T221742Z-e7eb34adbf08",
        "observed_at": "2026-10-30T18:17:42.791384-04:00",
    }


def test_api_read_refs_leave_published_snapshot_bytes_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "research"
    app = make_research_app(root)
    before = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }
    with TestClient(app) as client:
        history = client.get("/api/v1/instruments/US.AAPL/history").json()["data"]
        batch = client.get("/api/v1/history/batch?symbols=US.AAPL").json()["data"]
        universe = client.get("/api/v1/universes").json()["data"]
    assert history["events"][0]["label_ref"]["key"] == "common.backend.event.earnings"
    assert (
        batch["items"][0]["history"]["events"][0]["label_ref"]
        == history["events"][0]["label_ref"]
    )
    assert (
        universe["versions"][0]["reason_ref"]["key"]
        == "common.backend.universe.original"
    )
    after = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_operation_api_enrichment_does_not_persist_refs(tmp_path: Path) -> None:
    store = EodOperationStore(LivePaths.from_workspace(tmp_path))
    operation, _ = store.submit()
    store.update(
        operation.operation_id,
        status=UpdateOperationStatus.FAILED,
        error_code="duckdb_file_locked",
        retry_recommendation="实时数据存储暂时繁忙；系统已停止并发访问，请重新提交收盘更新。",
    )
    before = {path.name: path.read_bytes() for path in store.directory.glob("*.json")}
    with TestClient(create_app(operation_controller=store)) as client:
        for path in ("eod-update/latest", operation.operation_id):
            result = client.get(f"/api/v1/operations/{path}")
            assert result.status_code == 200
            assert (
                result.json()["retry_recommendation_ref"]["key"]
                == "common.backend.retry.duckdb_locked"
            )
    assert {
        path.name: path.read_bytes() for path in store.directory.glob("*.json")
    } == before
    assert store.get(operation.operation_id).retry_recommendation_ref is None
