"""Add display metadata to API copies; never rewrite published evidence."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import TypeVar, cast
from zoneinfo import ZoneInfo

from stock_prediction.contracts.operations import UpdateOperation
from stock_prediction.contracts.serving import (
    CandidateEvaluationSummary,
    DataHealth,
    DataHealthReadModel,
    FailedTaskRef,
    HistoryBatchReadModel,
    HistoryEvent,
    InstrumentHistory,
    ModelMatrixReadModel,
    ModelTaskMatrixRow,
    OverviewReadModel,
    UniverseReadModel,
    UniverseVersion,
)
from stock_prediction.contracts.text import LocalizableText
from stock_prediction.universe.active import ACTIVE_UNIVERSE_REASON
from stock_prediction.universe.seed import SEED_UNIVERSE_REASON


DataT = TypeVar("DataT")
_NUMBER = r"(?:\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
_SPLIT = re.compile(rf"拆并股：每 1 股变为 ({_NUMBER}) 股")
_DIVIDEND = re.compile(rf"每股现金分红（供应商原值）：({_NUMBER})")
_DATE = re.compile(r"除权息日期：(\d{4}-\d{2}-\d{2})；日期定位，不代表盘中公告时间。")
_SOURCE = re.compile(r"来源快照：([A-Za-z0-9_.:-]+)；观察时间：(.+)")
_GAP = re.compile(
    r"保留 ([1-9]\d*) 个历史交易日的分钟线缺口；OpenD 返回空数据。最新收盘数据正常。"
)
_OFFLINE = re.compile(
    r"([A-Za-z][A-Za-z0-9_. -]{0,63}) is offline; serving the last valid snapshot\."
)
_RETROSPECTIVE = "供应商回溯记录，不代表事件当时已知；不改变原始价格或生成总回报。"

_HEALTH_MESSAGES = {
    "The latest market-data task is incomplete or stale.": "market_incomplete",
    "Retrospective adjustment evidence is versioned but is not eligible as historical point-in-time training evidence.": "retrospective",
    "No corporate-action snapshot has been captured.": "rehab_missing",
}
_HEALTH_ACTIONS = {
    "Restore the provider and run live-refresh.": "restore_provider",
    "Run live-refresh and inspect the task ledger.": "refresh_ledger",
    "Keep retrospective factors out of point-in-time training.": "exclude_retrospective",
    "Run live-refresh to capture rehab evidence.": "refresh_rehab",
    "已按用户要求保留原始缺口，后续查漏补缺。": "preserve_gap",
}
_RETRY_MESSAGES = {
    "实时数据存储暂时繁忙；系统已停止并发访问，请重新提交收盘更新。": "duckdb_locked",
    "历史标的额度不足；仅扩容 staged 数据集后重试，当前生产数据集不会被改写。": "history_quota",
    "数据集版本与标的池不匹配；修复版本绑定后重试，旧快照已保留。": "universe_scope",
    "确认 OpenD 已登录并监听 127.0.0.1:11111 后重试。": "opend",
    "等待富途日线可用，检查失败标的水位后重试。": "daily",
    "最后有效快照已保留；检查磁盘和快照目录后重试。": "snapshot",
    "查看模型数据页的失败阶段与日志，修复后重新提交。": "inspect",
    "保留旧 run 产物与 staging 作为中断证据；不要排名、冻结或晋升。": "interrupted",
    "不要修改门禁阈值或重复消费晋升窗口。": "keep_gates",
    "不要重新搜索候选或重复消费同一晋升窗口。": "keep_window",
    "不要复用 stale approval 请求。": "stale_approval",
}


def _ref(key: str, **params: str | int | float) -> LocalizableText:
    return LocalizableText(key=f"common.backend.{key}", params=params or None)


def _event_label(event: HistoryEvent) -> LocalizableText:
    pattern = (
        _SPLIT
        if event.event_type == "split"
        else _DIVIDEND
        if event.event_type == "dividend"
        else None
    )
    match = pattern.fullmatch(event.label) if pattern is not None else None
    if match is not None:
        value = float(match[1])
        if (
            math.isfinite(value)
            and value > 0
            and (event.event_type != "split" or value != 1)
        ):
            return (
                _ref("event.split", ratio=match[1])
                if event.event_type == "split"
                else _ref("event.dividend", cash=match[1])
            )
    if event.event_type == "earnings" and event.label == "Quarterly earnings":
        return _ref("event.earnings")
    return _ref("event.original")


def _event_detail(event: HistoryEvent, detail: str) -> LocalizableText:
    # Legacy parsing is restricted to the publisher's exact corporate-action templates.
    if event.event_type not in {"split", "dividend"}:
        return _ref("event.original_detail")
    if detail == _RETROSPECTIVE:
        return _ref("event.retrospective")
    if match := _DATE.fullmatch(detail):
        try:
            effective_date = date.fromisoformat(match[1])
            if (
                effective_date
                == event.occurred_at.astimezone(ZoneInfo("America/New_York")).date()
            ):
                return _ref("event.effective_date", date=match[1])
        except ValueError:
            pass
    if match := _SOURCE.fullmatch(detail):
        try:
            observed_at = datetime.fromisoformat(match[2])
            if observed_at.tzinfo is not None and observed_at.utcoffset() is not None:
                return _ref("event.source", snapshot=match[1], observed_at=match[2])
        except ValueError:
            pass
    return _ref("event.original_detail")


def _health_message(message: str) -> LocalizableText:
    if key := _HEALTH_MESSAGES.get(message):
        return _ref(f"health.{key}")
    if match := _GAP.fullmatch(message):
        return _ref("health.source_gap", count=int(match[1]))
    if match := _OFFLINE.fullmatch(message):
        return _ref("health.provider_offline", provider=match[1])
    return _ref("health.original")


def _failed_tasks(
    summary: CandidateEvaluationSummary, rows: tuple[ModelTaskMatrixRow, ...]
) -> tuple[FailedTaskRef, ...] | None:
    evaluated = tuple(
        row
        for row in rows
        if (
            row.evaluation_run_id == summary.run_id
            # Manual activation uses component champion IDs and clears the challenger.
            and row.challenger_version in {None, summary.model_version}
            and row.challenger_calibration_version == summary.calibration_version
            and row.candidate_generated_at == summary.generated_at
            and row.gate_passed is not None
        )
    )
    if (
        len(evaluated) != summary.total_tasks
        or sum(row.gate_passed is True for row in evaluated) != summary.passed_tasks
    ):
        return None
    failed = tuple(
        FailedTaskRef(task=row.task, horizon=row.horizon)
        for row in evaluated
        if row.gate_passed is False
    )
    if len({(row.task, row.horizon) for row in failed}) != len(failed):
        return None
    return failed


def enrich_display_refs(
    data: DataT, *, model_rows: tuple[ModelTaskMatrixRow, ...] = ()
) -> DataT:
    """Copy only display-bearing read models, keeping all original values intact."""
    updates: dict[str, object] = {}
    if isinstance(data, HistoryEvent):
        updates = {
            "label_ref": data.label_ref or _event_label(data),
            "details_refs": data.details_refs
            if data.details_refs is not None
            else tuple(_event_detail(data, detail) for detail in data.details),
        }
    elif isinstance(data, InstrumentHistory):
        updates = {"events": tuple(enrich_display_refs(event) for event in data.events)}
    elif isinstance(data, HistoryBatchReadModel):
        updates = {
            "items": tuple(
                item.model_copy(update={"history": enrich_display_refs(item.history)})
                if item.history is not None
                else item
                for item in data.items
            )
        }
    elif isinstance(data, DataHealth):
        if data.message is not None:
            updates["message_ref"] = data.message_ref or _health_message(data.message)
        if data.action is not None:
            updates["action_ref"] = data.action_ref or _ref(
                f"health.{_HEALTH_ACTIONS.get(data.action, 'original_action')}"
            )
    elif isinstance(data, DataHealthReadModel):
        updates = {"items": tuple(enrich_display_refs(item) for item in data.items)}
    elif isinstance(data, UniverseVersion):
        key = (
            "seed"
            if data.reason == SEED_UNIVERSE_REASON
            else "expanded"
            if data.reason == ACTIVE_UNIVERSE_REASON
            else "original"
        )
        updates = {"reason_ref": data.reason_ref or _ref(f"universe.{key}")}
    elif isinstance(data, UniverseReadModel):
        updates = {
            "versions": tuple(enrich_display_refs(version) for version in data.versions)
        }
    elif isinstance(data, CandidateEvaluationSummary):
        if (
            data.failed_task_refs is None
            and (refs := _failed_tasks(data, model_rows)) is not None
        ):
            updates = {"failed_task_refs": refs}
    elif (
        isinstance(data, (OverviewReadModel, ModelMatrixReadModel))
        and data.candidate_summary is not None
    ):
        updates = {
            "candidate_summary": enrich_display_refs(
                data.candidate_summary, model_rows=model_rows
            )
        }
    return (
        cast(DataT, data.model_copy(update=updates))
        if updates and hasattr(data, "model_copy")
        else data
    )


def operation_display_refs(operation: UpdateOperation) -> UpdateOperation:
    if (
        operation.retry_recommendation is None
        or operation.retry_recommendation_ref is not None
    ):
        return operation
    key = _RETRY_MESSAGES.get(operation.retry_recommendation, "original")
    return operation.model_copy(
        update={"retry_recommendation_ref": _ref(f"retry.{key}")}
    )
