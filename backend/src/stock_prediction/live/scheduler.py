"""Independent local APScheduler process for live health and research jobs."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from filelock import Timeout

from stock_prediction.live.config import (
    ALPACA_IEX_YAHOO_DATASET_REVISION,
    ALPACA_SIP_DATASET_REVISION,
    LivePaths,
)
from stock_prediction.live.ingestion import LiveOpenDService
from stock_prediction.live.features import load_latest_feature_artifact
from stock_prediction.live.market_data_service import LiveMarketDataService
from stock_prediction.live.locking import LiveWriterLease
from stock_prediction.live.m7_orchestration import (
    M7OrchestrationError,
    M7OrchestrationStore,
    M7TrainingIntent,
    build_monthly_training_intent,
)
from stock_prediction.live.m7_outcome_monitor import M7WeeklyOutcomeMonitor
from stock_prediction.live.m7_blind_review import M7BlindReviewRunner
from stock_prediction.live.m7_retention import M7RetentionManager
from stock_prediction.live.operations import EodUpdateCoordinator
from stock_prediction.live.model_operations import (
    CandidateRecipeExhaustedError,
    ModelTrainingCoordinator,
    PromotionWindowAlreadyConsumedError,
    PromotionWindowNotReadyError,
)
from stock_prediction.live.promotion_operations import ModelPromotionCoordinator
from stock_prediction.options.scheduled_collection import (
    run_scheduled_option_collection,
)
from stock_prediction.options.intraday import OptionPulseStore
from stock_prediction.options.refresh import OptionRefreshStore, run_option_refresh_tick


def _initialize_worker_state(
    coordinator: EodUpdateCoordinator,
    model_coordinator: ModelTrainingCoordinator,
    promotion_coordinator: ModelPromotionCoordinator,
) -> None:
    model_coordinator.reconcile_interrupted_operation()
    coordinator.store.heartbeat()
    model_coordinator.store.heartbeat()
    promotion_coordinator.store.heartbeat()


def _shutdown_worker_state(
    coordinator: EodUpdateCoordinator,
    model_coordinator: ModelTrainingCoordinator,
    promotion_coordinator: ModelPromotionCoordinator,
) -> None:
    try:
        model_coordinator.reconcile_interrupted_operation()
    finally:
        coordinator.store.mark_worker_offline()
        model_coordinator.store.mark_worker_offline()
        promotion_coordinator.store.mark_worker_offline()


def run_scheduler(paths: LivePaths) -> dict[str, object]:
    scheduler = BlockingScheduler(timezone="America/New_York")
    service = (
        LiveMarketDataService(paths)
        if paths.dataset_revision
        in {ALPACA_SIP_DATASET_REVISION, ALPACA_IEX_YAHOO_DATASET_REVISION}
        else LiveOpenDService(paths)
    )
    coordinator = EodUpdateCoordinator(paths)
    model_coordinator = ModelTrainingCoordinator(paths)
    promotion_coordinator = ModelPromotionCoordinator(paths)
    m7_orchestration = M7OrchestrationStore(paths)
    live_root = paths.root.resolve()
    pulse_workspace = (
        live_root.parents[2]
        if live_root.parent.name == "live-datasets"
        else live_root.parents[1]
    )
    pulse_store = OptionPulseStore(pulse_workspace / "var" / "options" / "_pulse")
    refresh_store = OptionRefreshStore(pulse_workspace / "var" / "options")

    def worker_heartbeat() -> None:
        coordinator.store.heartbeat()
        model_coordinator.store.heartbeat()
        promotion_coordinator.store.heartbeat()
        pulse_store.heartbeat(datetime.now(UTC))
        refresh_store.heartbeat(datetime.now(UTC))

    def health_probe() -> None:
        try:
            with LiveWriterLease(paths, task_id="health-probe", timeout=0):
                service.preflight()
        except Timeout:
            # The EOD pipeline owns OpenD and live DuckDB while it is running.
            return

    def monthly_training() -> None:
        try:
            artifact = load_latest_feature_artifact(paths)
            intent = build_monthly_training_intent(
                last_completed_session=artifact.as_of.date(),
                universe_version=artifact.universe_version,
                evidence_hash=artifact.input_hash,
            )
            now = datetime.now(UTC)
            if (
                now < intent.eligible_at
                or artifact.as_of.month == now.month
                or (now.date() - artifact.as_of.date()).days > 8
            ):
                return
            m7_orchestration.queue_once(intent, _submit_m7_intent)
        except (CandidateRecipeExhaustedError, FileNotFoundError):
            # Candidate development is intentionally finite.  A later round
            # must be preregistered before the scheduler may submit it.
            return

    def confirmed_drift_training() -> None:
        try:
            decision = m7_orchestration.drift_decision()
            if decision.state != "training_authorized":
                return
            artifact = load_latest_feature_artifact(paths)
            intent = m7_orchestration.build_drift_training_intent(
                data_cutoff=artifact.as_of.date(),
                universe_version=artifact.universe_version,
                eligible_at=datetime.now(UTC),
            )
            m7_orchestration.queue_once(intent, _submit_m7_intent)
        except (CandidateRecipeExhaustedError, FileNotFoundError, M7OrchestrationError):
            return

    def m7_retention_cleanup() -> None:
        M7RetentionManager(paths).cleanup_failed_checkpoints(now=datetime.now(UTC))

    def m7_blind_review() -> None:
        try:
            with LiveWriterLease(paths, task_id="m7-blind-review", timeout=0):
                M7BlindReviewRunner(paths).run_ready(generated_at=datetime.now(UTC))
        except Timeout:
            return

    def m7_weekly_outcome_drift() -> None:
        try:
            with LiveWriterLease(paths, task_id="m7-weekly-outcome", timeout=0):
                artifact = load_latest_feature_artifact(paths)
                M7WeeklyOutcomeMonitor(paths).run(
                    week_ending=artifact.as_of.date(),
                    generated_at=datetime.now(UTC),
                )
        except (FileNotFoundError, Timeout):
            return

    def _submit_m7_intent(intent: M7TrainingIntent) -> str:
        operation, _created = model_coordinator.submit(
            candidate_recipe_version=intent.recipe_id,
            bundles=intent.bundles,
            trigger=intent.trigger,
        )
        return operation.operation_id

    def promotion_blind_test() -> None:
        try:
            promotion_coordinator.submit()
        except (PromotionWindowAlreadyConsumedError, PromotionWindowNotReadyError):
            return

    def universe_review() -> None:
        directory = paths.root / "universe-reviews"
        directory.mkdir(parents=True, exist_ok=True)
        at = datetime.now(UTC)
        target = directory / f"review-{at:%Y%m%dT%H%M%SZ}.json"
        target.write_text(
            json.dumps(
                {
                    "generated_at": at.isoformat(),
                    "status": "manual_review_required",
                    "automatic_replacements": 0,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def daily_live_run() -> None:
        local_now = datetime.now(ZoneInfo("America/New_York"))
        scheduled_at = local_now.replace(
            hour=16,
            minute=20,
            second=0,
            microsecond=0,
        ).astimezone(UTC)
        coordinator.submit(completed_not_before=scheduled_at)

    def daily_option_collection() -> None:
        run_scheduled_option_collection(paths)

    scheduler.add_job(
        daily_option_collection,
        CronTrigger(
            day_of_week="mon-fri", hour=15, minute=40, timezone="America/New_York"
        ),
        id="daily_option_collection",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=900,
    )
    scheduler.add_job(
        lambda: run_option_refresh_tick(paths),
        IntervalTrigger(seconds=2),
        id="manual_option_refresh",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=10,
    )
    scheduler.add_job(
        health_probe,
        IntervalTrigger(seconds=60),
        id="opend-health",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        daily_live_run,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=20),
        id="daily-live-run",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        worker_heartbeat,
        IntervalTrigger(seconds=2),
        id="eod-worker-heartbeat",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        coordinator.run_pending,
        IntervalTrigger(seconds=2),
        id="eod-operation-worker",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        model_coordinator.run_pending,
        IntervalTrigger(seconds=2),
        id="model-training-operation-worker",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        promotion_coordinator.run_pending,
        IntervalTrigger(seconds=2),
        id="model-promotion-operation-worker",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        monthly_training,
        CronTrigger(day="1-7", day_of_week="sat", hour=2, minute=0),
        id="monthly-training",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        m7_weekly_outcome_drift,
        CronTrigger(day_of_week="fri", hour=17, minute=30),
        id="m7-weekly-outcome-drift",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        confirmed_drift_training,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=0),
        id="confirmed-drift-training",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        m7_retention_cleanup,
        CronTrigger(day_of_week="sun", hour=3, minute=0),
        id="m7-retention-cleanup",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        m7_blind_review,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=30),
        id="m7-blind-review",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        promotion_blind_test,
        CronTrigger(day_of_week="sat", hour=10, minute=0),
        id="promotion-window-blind-test",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        universe_review,
        CronTrigger(month="1,4,7,10", day="1-7", day_of_week="sat", hour=11),
        id="quarterly-universe-review",
        max_instances=1,
        coalesce=True,
    )
    _initialize_worker_state(coordinator, model_coordinator, promotion_coordinator)
    try:
        scheduler.start()
    finally:
        _shutdown_worker_state(coordinator, model_coordinator, promotion_coordinator)
    return {"status": "stopped"}


__all__ = ["run_scheduler"]
