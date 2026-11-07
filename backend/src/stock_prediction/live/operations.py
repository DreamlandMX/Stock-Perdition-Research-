"""Single-instance EOD pipeline coordinator shared by UI and scheduler."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import time
from pathlib import Path, PurePath
from typing import Callable, Protocol, TypeVar, cast

from filelock import Timeout

from stock_prediction.contracts.operations import (
    UpdateOperation,
    UpdateOperationStage,
    UpdateOperationStatus,
)
from stock_prediction.live.config import (
    ALPACA_IEX_YAHOO_DATASET_REVISION,
    ALPACA_SIP_DATASET_REVISION,
    LivePaths,
)
from stock_prediction.live.features import LiveFeatureBuilder, LiveFeatureArtifact
from stock_prediction.live.ingestion import LiveOpenDService, LivePreflightResult
from stock_prediction.live.market_data_service import LiveMarketDataService
from stock_prediction.live.m7_drift_monitor import M7DriftMonitor
from stock_prediction.live.m7_formal_inference import M7FormalInferenceRunner
from stock_prediction.live.m7_shadow_inference import M7ShadowInferenceRunner
from stock_prediction.market_data.ports import ProviderPreflightResult
from stock_prediction.live.locking import LiveWriterLease
from stock_prediction.live.operation_store import EodOperationStore
from stock_prediction.live.publication import LiveSnapshotPublisher
from stock_prediction.live.storage import LiveStorage
from stock_prediction.live.training import LiveModelTrainer
from stock_prediction.live.universe_scope import resolve_live_universe
from stock_prediction.operations.registry import LocalModelRegistry


_STAGES: tuple[UpdateOperationStage, ...] = tuple(UpdateOperationStage)
_PUBLICATION_OPERATION_REVISION = "publication-v3-eod-inference-only"
_RETRYABLE_DAILY_ERRORS = {
    "daily_cutoff_incomplete",
    "daily_universe_incomplete",
}
_DEFAULT_RETRY_INTERVAL_SECONDS = 60.0
_DEFAULT_RETRY_WINDOW_SECONDS = 15 * 60.0
_T = TypeVar("_T")


class _M7DriftRunner(Protocol):
    def run(self, *, generated_at: datetime) -> object: ...


class EodUpdateCoordinator:
    """Claim and execute the durable EOD queue from the scheduler process."""

    def __init__(
        self,
        paths: LivePaths,
        *,
        service_factory: Callable[[], LiveOpenDService | LiveMarketDataService]
        | None = None,
        feature_factory: Callable[[], LiveFeatureBuilder] | None = None,
        publisher_factory: Callable[[], LiveSnapshotPublisher] | None = None,
        trainer_factory: Callable[[], LiveModelTrainer] | None = None,
        shadow_factory: Callable[[], M7ShadowInferenceRunner] | None = None,
        drift_factory: Callable[[], _M7DriftRunner] | None = None,
        formal_factory: Callable[[], _M7DriftRunner] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
        retry_interval_seconds: float = _DEFAULT_RETRY_INTERVAL_SECONDS,
        retry_window_seconds: float = _DEFAULT_RETRY_WINDOW_SECONDS,
    ) -> None:
        if retry_interval_seconds <= 0:
            raise ValueError("retry_interval_seconds must be positive")
        if retry_window_seconds <= 0:
            raise ValueError("retry_window_seconds must be positive")
        self.paths = paths
        self.universe = resolve_live_universe(paths)
        self.store = EodOperationStore(paths)
        self.storage = LiveStorage(paths)
        self.service_factory = service_factory or (
            (lambda: LiveMarketDataService(paths))
            if paths.dataset_revision
            in {ALPACA_SIP_DATASET_REVISION, ALPACA_IEX_YAHOO_DATASET_REVISION}
            else (lambda: LiveOpenDService(paths))
        )
        self.feature_factory = feature_factory or (lambda: LiveFeatureBuilder(paths))
        self.publisher_factory = publisher_factory or (
            lambda: LiveSnapshotPublisher(paths)
        )
        self.trainer_factory = trainer_factory or (lambda: LiveModelTrainer(paths))
        self.shadow_factory = shadow_factory or (lambda: M7ShadowInferenceRunner(paths))
        self.drift_factory = drift_factory or (lambda: M7DriftMonitor(paths))
        self.formal_factory = formal_factory or (lambda: M7FormalInferenceRunner(paths))
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sleeper = sleeper or time.sleep
        self.retry_interval_seconds = retry_interval_seconds
        self.retry_window_seconds = retry_window_seconds

    def submit(
        self,
        *,
        completed_not_before: datetime | None = None,
    ) -> UpdateOperation:
        return self.store.submit(completed_not_before=completed_not_before)[0]

    def run_pending(self) -> UpdateOperation | None:
        operation = self.store.latest()
        if operation is None or operation.status not in {
            UpdateOperationStatus.QUEUED,
            UpdateOperationStatus.RUNNING,
        }:
            return None
        try:
            with LiveWriterLease(
                self.paths,
                task_id=operation.operation_id,
                timeout=0,
            ):
                return self._run(operation)
        except Timeout:
            return self.store.get(operation.operation_id)

    def _run(self, operation: UpdateOperation) -> UpdateOperation:
        operation_id = operation.operation_id
        if operation.started_at is None:
            operation = self.store.update(
                operation_id,
                status=UpdateOperationStatus.RUNNING,
                started_at=self.clock(),
            )
        active_stage: UpdateOperationStage | None = None
        training: dict[str, object] | None = None
        try:
            service = self.service_factory()

            active_stage = UpdateOperationStage.OPEND_PREFLIGHT
            self._start(
                operation_id, active_stage, "正在连接 OpenD 并确认最近完整交易日"
            )
            preflight = cast(
                LivePreflightResult | ProviderPreflightResult,
                _with_lock_retry(service.preflight),
            )
            if not preflight.ready and any(
                blocker != "history_quota_reserve_exhausted"
                for blocker in preflight.blockers
            ):
                raise RuntimeError(_preflight_error_code(preflight.blockers))
            self.store.update(
                operation_id,
                stage=active_stage,
                progress_percent=10,
                phase_status="completed",
                phase_detail=f"目标交易日 {preflight.end.isoformat()}",
                target_session=preflight.end,
            )

            previous_cutoff = operation.previous_cutoff
            if previous_cutoff is None and operation.retry_deadline_at is None:
                previous_cutoff, _previous_complete = self.storage.common_daily_cutoff(
                    self.universe.symbols
                )
                self.store.update(operation_id, previous_cutoff=previous_cutoff)

            active_stage = UpdateOperationStage.INCREMENTAL_COLLECTION
            actual_cutoff, complete = self._collect_until_complete(
                operation_id,
                service=service,
                target_session=preflight.end,
            )
            active_stage = UpdateOperationStage.DATA_QUALITY
            intraday_complete = _timeframe_completion(
                self.storage, "60m", self.universe.symbols
            )
            self.store.update(
                operation_id,
                stage=active_stage,
                progress_percent=35,
                phase_status="completed",
                phase_detail=(
                    f"日线 {complete}/{len(self.universe.symbols)}；"
                    f"60 分钟 {intraday_complete}/{len(self.universe.symbols)}"
                ),
                actual_cutoff=actual_cutoff,
                session_advanced=(
                    previous_cutoff is None or actual_cutoff > previous_cutoff
                ),
                next_retry_at=None,
            )

            active_stage = UpdateOperationStage.FEATURE_BUILD
            self._start(operation_id, active_stage, "构建点时特征与标签")
            artifact = self.feature_factory().build()
            operation_hash = f"{artifact.input_hash}:{_PUBLICATION_OPERATION_REVISION}"
            self.store.update(
                operation_id,
                stage=active_stage,
                progress_percent=45,
                phase_status="completed",
                phase_detail=f"特征截止 {artifact.as_of.date().isoformat()}",
                data_hash=operation_hash,
            )
            prior_result = self.store.completed_for_hash(
                operation_hash, exclude_operation_id=operation_id
            )
            if prior_result is not None and prior_result.actual_cutoff == actual_cutoff:
                return self._complete_no_change(
                    operation_id,
                    prior_result,
                    preflight=preflight,
                    artifact=artifact,
                )

            active_stage = UpdateOperationStage.MARKET_SNAPSHOT_PUBLICATION
            self._start(operation_id, active_stage, "先发布最新真实行情与规则状态")
            market_publication = self._publish(operation_id, active_stage)
            self.store.update(
                operation_id,
                stage=active_stage,
                progress_percent=55,
                phase_status="completed",
                phase_detail=str(market_publication["prediction_readiness"]),
                snapshot_id=str(market_publication["snapshot_id"]),
            )

            registry = LocalModelRegistry(self.paths.models / "registry")
            champion = registry.snapshot("forecast_bundle", 20)
            active_stage = UpdateOperationStage.MODEL_TRAINING
            self._start(
                operation_id,
                active_stage,
                "收盘更新不执行深度候选搜索；复用当前 Champion",
            )
            self.store.update(
                operation_id,
                stage=active_stage,
                progress_percent=64,
                phase_status="completed",
                phase_detail="深度训练仅由手动训练或每月首个周六调度触发",
            )

            active_stage = UpdateOperationStage.PROMOTION_GATE
            self._start(operation_id, active_stage, "检查现有正式预测 Champion 状态")
            gate_passed = champion.champion_version is not None
            self.store.update(
                operation_id,
                stage=active_stage,
                progress_percent=72,
                phase_status="completed",
                phase_detail="复用已发布的正式预测 Champion"
                if gate_passed
                else "尚无 17/17 同批次正式预测 Champion",
            )

            active_stage = UpdateOperationStage.CHAMPION_SWITCH
            self._start(
                operation_id, active_stage, "保持现有 Champion，不在日终任务中切换"
            )
            promotion = "kept_current"
            self.store.update(
                operation_id,
                stage=active_stage,
                progress_percent=80,
                phase_status="completed",
                phase_detail=promotion,
                automatic_promotion=promotion,
                model_version=champion.champion_version,
            )

            active_stage = UpdateOperationStage.BATCH_INFERENCE
            self._start(
                operation_id,
                active_stage,
                f"批量处理 {len(self.universe.symbols)} 标的 × 3 周期",
            )
            self.store.update(
                operation_id,
                stage=active_stage,
                progress_percent=90,
                phase_status="completed",
                phase_detail=(
                    f"使用当前 Champion 完成 {len(self.universe.symbols)} 个标的 × 3 周期推理"
                    if gate_passed
                    else "无正式 Champion；保留已验证研究组件，不生成正式数值"
                ),
            )

            active_stage = UpdateOperationStage.FINAL_SNAPSHOT_PUBLICATION
            self._start(operation_id, active_stage, "发布最终不可变研究快照")
            formal_at = self.clock()
            self.formal_factory().run(generated_at=formal_at)
            final_publication = self._publish(operation_id, active_stage)
            readiness = str(final_publication["prediction_readiness"])
            shadow_runner = self.shadow_factory()
            shadow_at = self.clock()
            shadow_runner.run_due(
                generated_at=shadow_at,
                incumbent_snapshot_path=Path(str(final_publication["path"])),
            )
            shadow_runner.run_mature_labels(generated_at=shadow_at)
            self.drift_factory().run(generated_at=shadow_at)
            status = (
                UpdateOperationStatus.SUCCEEDED_FORECAST_READY
                if readiness == "forecast_ready"
                else UpdateOperationStatus.SUCCEEDED_DATA_ONLY
            )
            finished = self.clock()
            completed = self.store.update(
                operation_id,
                status=status,
                stage=active_stage,
                progress_percent=100,
                phase_status="completed",
                phase_detail=readiness,
                snapshot_id=str(final_publication["snapshot_id"]),
                finished_at=finished,
                next_retry_at=None,
            )
            self._record_pipeline_health(completed, preflight, artifact)
            return completed
        except Exception as error:
            code, diagnostic, retry_action = _error_contract(error)
            stored_operation = self.store.get(operation_id)
            if (
                stored_operation is not None
                and stored_operation.current_stage is not None
            ):
                active_stage = stored_operation.current_stage
            if (
                training is not None
                and training.get("automatic_promotion") == "promoted"
                and active_stage
                in {
                    UpdateOperationStage.BATCH_INFERENCE,
                    UpdateOperationStage.FINAL_SNAPSHOT_PUBLICATION,
                }
            ):
                self._rollback_failed_publication(training)
            if active_stage is not None:
                self.store.update(
                    operation_id,
                    stage=active_stage,
                    phase_status="failed",
                    phase_detail=diagnostic,
                )
            return self.store.update(
                operation_id,
                status=UpdateOperationStatus.FAILED,
                error_code=code,
                diagnostic_detail=diagnostic,
                retry_action=retry_action,
                retry_recommendation=retry_action,
                finished_at=self.clock(),
                next_retry_at=None,
            )

    def _collect_until_complete(
        self,
        operation_id: str,
        *,
        service: LiveOpenDService | LiveMarketDataService,
        target_session: date,
    ) -> tuple[date, int]:
        operation = self.store.get(operation_id)
        if operation is None:
            raise KeyError(operation_id)
        retry_attempt = operation.retry_attempt
        retry_deadline = operation.retry_deadline_at

        while True:
            active_stage = UpdateOperationStage.INCREMENTAL_COLLECTION
            detail = (
                f"增量采集固定 {len(self.universe.symbols)} 个标的"
                if retry_attempt == 0
                else (
                    f"第 {retry_attempt} 次自动补采固定 "
                    f"{len(self.universe.symbols)} 个标的"
                )
            )
            self._start(operation_id, active_stage, detail)
            last_reported = -1

            def report_collection_progress(
                completed: int,
                total: int,
                symbol: str,
                timeframe: object,
            ) -> None:
                nonlocal last_reported
                if completed != total and completed % 5 != 0:
                    return
                if completed == last_reported:
                    return
                last_reported = completed
                timeframe_value = getattr(timeframe, "value", str(timeframe))
                universe_size = len(self.universe.symbols)
                within_timeframe = ((completed - 1) % universe_size) + 1
                self.store.update(
                    operation_id,
                    status=UpdateOperationStatus.RUNNING,
                    stage=active_stage,
                    progress_percent=10 + round(15 * completed / max(total, 1)),
                    phase_status="running",
                    phase_detail=(
                        f"{timeframe_value} 采集 {within_timeframe}/{universe_size} · {symbol}"
                    ),
                )

            refresh = _with_lock_retry(
                lambda: service.refresh(progress_callback=report_collection_progress)
            )
            self.store.update(
                operation_id,
                stage=active_stage,
                progress_percent=25,
                phase_status="completed",
                phase_detail=(
                    f"写入 {refresh.get('tasks_committed', 0)} 项；"
                    f"{refresh.get('tasks_failed', 0)} 项增量请求受阻"
                ),
            )

            active_stage = UpdateOperationStage.DATA_QUALITY
            self._start(operation_id, active_stage, "验证日线完整性与辅助 60 分钟覆盖")
            actual_cutoff, complete = self.storage.common_daily_cutoff(
                self.universe.symbols
            )
            incomplete_code = _daily_incomplete_code(
                actual_cutoff=actual_cutoff,
                complete=complete,
                target_session=target_session,
                expected_count=len(self.universe.symbols),
            )
            self.store.update(operation_id, actual_cutoff=actual_cutoff)
            if incomplete_code is None:
                if actual_cutoff is None:
                    raise RuntimeError("daily_cutoff_incomplete")
                return actual_cutoff, complete

            now = self.clock()
            if retry_deadline is None:
                retry_deadline = now + timedelta(seconds=self.retry_window_seconds)
            if now >= retry_deadline:
                raise RuntimeError(incomplete_code)

            retry_attempt += 1
            next_retry_at = min(
                now + timedelta(seconds=self.retry_interval_seconds),
                retry_deadline,
            )
            self.store.update(
                operation_id,
                status=UpdateOperationStatus.RUNNING,
                stage=active_stage,
                progress_percent=30,
                phase_status="running",
                phase_detail=(
                    f"当前完整至 {actual_cutoff.isoformat() if actual_cutoff else '尚无'}；"
                    f"目标 {target_session.isoformat()}；"
                    f"第 {retry_attempt} 次自动补采将在 "
                    f"{next_retry_at.isoformat()} 开始"
                ),
                retry_attempt=retry_attempt,
                next_retry_at=next_retry_at,
                retry_deadline_at=retry_deadline,
            )
            self.sleeper(max((next_retry_at - now).total_seconds(), 0.0))

    def _rollback_failed_publication(self, training: dict[str, object]) -> None:
        registry = LocalModelRegistry(self.paths.models / "registry")
        component_releases = training.get("component_releases")
        if isinstance(component_releases, list):
            for component in component_releases:
                if not isinstance(component, dict) or not component.get("gate_passed"):
                    continue
                task = component.get("task")
                horizon = component.get("horizon")
                previous = component.get("previous_champion_version")
                if not isinstance(task, str) or not isinstance(horizon, int):
                    continue
                if isinstance(previous, str):
                    registry.rollback(
                        task=task,
                        horizon=horizon,
                        target_version=previous,
                        reason=(
                            "automatic task-component rollback after final "
                            "snapshot publication failure"
                        ),
                        recorded_at=datetime.now(UTC),
                    )
                else:
                    registry.deactivate_champion(
                        task=task,
                        horizon=horizon,
                        reason=(
                            "automatic task-component deactivation after first "
                            "snapshot publication failure"
                        ),
                        recorded_at=datetime.now(UTC),
                    )
        previous = training.get("previous_champion_version")
        if isinstance(previous, str):
            registry.rollback(
                task="forecast_bundle",
                horizon=20,
                target_version=previous,
                reason="automatic rollback after final snapshot publication failure",
                recorded_at=datetime.now(UTC),
            )
        else:
            registry.deactivate_champion(
                task="forecast_bundle",
                horizon=20,
                reason="automatic deactivation after first snapshot publication failure",
                recorded_at=datetime.now(UTC),
            )

    def _complete_no_change(
        self,
        operation_id: str,
        prior: UpdateOperation,
        *,
        preflight: LivePreflightResult | ProviderPreflightResult,
        artifact: LiveFeatureArtifact,
    ) -> UpdateOperation:
        remaining = _STAGES[
            _STAGES.index(
                UpdateOperationStage.MARKET_SNAPSHOT_PUBLICATION
            ) : _STAGES.index(UpdateOperationStage.FINAL_SNAPSHOT_PUBLICATION) + 1
        ]
        for index, stage in enumerate(remaining, 5):
            self.store.update(
                operation_id,
                stage=stage,
                progress_percent=index * 10,
                phase_status="completed",
                phase_detail="数据哈希未变化，复用已验证结果",
            )
        completed = self.store.update(
            operation_id,
            status=prior.status,
            progress_percent=100,
            actual_cutoff=prior.actual_cutoff,
            snapshot_id=prior.snapshot_id,
            model_version=prior.model_version,
            automatic_promotion="kept_current",
            no_change=True,
            finished_at=self.clock(),
            next_retry_at=None,
        )
        self._record_pipeline_health(completed, preflight, artifact)
        return completed

    def _publish(
        self, operation_id: str, stage: UpdateOperationStage
    ) -> dict[str, object]:
        spans = {
            "prepare": (0, 10),
            "formal": (10, 35),
            "verified": (35, 60),
            "candidate": (60, 85),
            "tables": (85, 95),
            "materialize": (95, 100),
        }
        base, width = (
            (50, 4)
            if stage is UpdateOperationStage.MARKET_SNAPSHOT_PUBLICATION
            else (90, 8)
        )
        last_write = float("-inf")
        last_percent = base

        def report(step: str, completed: int, total: int) -> None:
            nonlocal last_write, last_percent
            lower, upper = spans[step]
            fraction = min(1.0, max(0.0, completed / max(total, 1)))
            percent = base + int(width * (lower + (upper - lower) * fraction) / 100)
            now = time.monotonic()
            if percent == last_percent and now - last_write < 1.0:
                return
            self.store.update(
                operation_id,
                stage=stage,
                progress_percent=percent,
                phase_status="running",
                phase_detail=f"snapshot_publication:{step}:{completed}/{total}",
            )
            last_write, last_percent = now, percent

        return self.publisher_factory().publish(progress=report)

    def _start(
        self, operation_id: str, stage: UpdateOperationStage, detail: str
    ) -> None:
        index = _STAGES.index(stage)
        self.store.update(
            operation_id,
            status=UpdateOperationStatus.RUNNING,
            stage=stage,
            progress_percent=index * 10,
            phase_status="running",
            phase_detail=detail,
        )

    def _record_pipeline_health(
        self,
        operation: UpdateOperation,
        preflight: LivePreflightResult | ProviderPreflightResult,
        artifact: LiveFeatureArtifact,
    ) -> None:
        payload = self.storage.latest_health() or {}
        payload.update(
            {
                "data_cutoff": operation.actual_cutoff.isoformat()
                if operation.actual_cutoff is not None
                else None,
                "expected_cutoff": preflight.end.isoformat(),
                "snapshot_cutoff": artifact.as_of.date().isoformat(),
                "lag_sessions": self.storage.lag_sessions(
                    operation.actual_cutoff, preflight.end
                ),
                "last_pipeline_success_at": operation.finished_at.isoformat()
                if operation.finished_at is not None
                else datetime.now(UTC).isoformat(),
                "last_operation_id": operation.operation_id,
                "daily_symbols_complete": len(self.universe.symbols),
                "daily_symbols_total": len(self.universe.symbols),
            }
        )
        self.storage.record_health(payload)


def _daily_incomplete_code(
    *,
    actual_cutoff: date | None,
    complete: int,
    target_session: date,
    expected_count: int,
) -> str | None:
    code: str | None = None
    if complete != expected_count:
        code = "daily_universe_incomplete"
    elif actual_cutoff is None or actual_cutoff < target_session:
        code = "daily_cutoff_incomplete"
    if code is not None and code not in _RETRYABLE_DAILY_ERRORS:
        raise RuntimeError(code)
    return code


def _timeframe_completion(
    storage: LiveStorage, timeframe: str, symbols: tuple[str, ...]
) -> int:
    return sum(
        1
        for row in storage.coverage()
        if row["timeframe"] == timeframe and row["symbol"] in symbols
    )


def _retry_recommendation(error_code: str) -> str:
    if error_code == "duckdb_file_locked":
        return "实时数据存储暂时繁忙；系统已停止并发访问，请重新提交收盘更新。"
    if error_code == "history_quota_insufficient":
        return (
            "历史标的额度不足；仅扩容 staged 数据集后重试，当前生产数据集不会被改写。"
        )
    if error_code == "universe_scope_mismatch":
        return "数据集版本与标的池不匹配；修复版本绑定后重试，旧快照已保留。"
    if "opend" in error_code:
        return "确认 OpenD 已登录并监听 127.0.0.1:11111 后重试。"
    if "daily" in error_code:
        return "等待富途日线可用，检查失败标的水位后重试。"
    if "publish" in error_code or "snapshot" in error_code:
        return "最后有效快照已保留；检查磁盘和快照目录后重试。"
    return "查看模型数据页的失败阶段与日志，修复后重新提交。"


def _is_duckdb_lock_error(error: Exception) -> bool:
    message = str(error).lower()
    return (
        "cannot open file" in message
        and ("another process" in message or "being used" in message)
    ) or ("conflicting lock" in message and "duckdb" in message)


def _with_lock_retry(action: Callable[[], _T]) -> _T:
    delays = (0.25, 0.5, 1.0, 2.0, 4.0)
    for delay in delays:
        try:
            return action()
        except Exception as error:
            if not _is_duckdb_lock_error(error):
                raise
            time.sleep(delay)
    return action()


def _preflight_error_code(blockers: tuple[str, ...]) -> str:
    for code in (
        "history_quota_insufficient",
        "history_quota_detail_unavailable",
        "universe_scope_mismatch",
        "opend_quote_not_logged_in",
        "opend_connection_unavailable",
    ):
        if code in blockers:
            return code
    return blockers[0] if blockers else "market_data_preflight_blocked"


def _os_error_hint(error: Exception) -> str:
    """Return an actionable, path-free hint for filesystem failures.

    Operating-system errors carry the one detail an operator needs - which file
    failed and why - inside a message that also contains an absolute path. The
    path must not be surfaced, but discarding the whole message leaves an
    undiagnosable bare class name such as "PermissionError". Keep the errno /
    WinError number and the file name only.
    """
    if not isinstance(error, OSError):
        return ""
    parts: list[str] = []
    win = getattr(error, "winerror", None)
    if win is not None:
        parts.append(f"winerror{win}")
    elif error.errno is not None:
        parts.append(f"errno{error.errno}")
    for candidate in (error.filename, error.filename2):
        if isinstance(candidate, str) and candidate:
            parts.append(PurePath(candidate).name)
    return " ".join(parts)


def _error_contract(error: Exception) -> tuple[str, str, str]:
    if _is_duckdb_lock_error(error):
        code = "duckdb_file_locked"
        diagnostic = "实时数据存储被其他进程占用。"
    else:
        raw = str(error).strip()
        code = (
            raw
            if raw and "/" not in raw and "\\" not in raw
            else error.__class__.__name__
        )
        hint = _os_error_hint(error)
        diagnostic = {
            "opend_preflight_blocked": "OpenD 预检未通过。",
            "history_quota_insufficient": "新增标的所需的 OpenD 历史额度不足。",
            "history_quota_detail_unavailable": "OpenD 未返回可核验的历史额度明细。",
            "universe_scope_mismatch": "数据集版本与标的池绑定不匹配。",
            "daily_universe_incomplete": ("当前数据集的日线标的尚未全部完成。"),
            "daily_cutoff_incomplete": "实际落库日期尚未达到目标交易日。",
            "live_pipeline_busy": "另一项实时数据任务正在运行。",
        }.get(code, "流水线在当前阶段未能完成。")
        if hint:
            diagnostic = f"{diagnostic}（{hint}）"
    return code, diagnostic, _retry_recommendation(code)


__all__ = ["EodUpdateCoordinator"]
