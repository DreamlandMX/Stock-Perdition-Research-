from datetime import UTC, date, datetime, timedelta

from stock_prediction.contracts.ingestion import (
    IngestionPriority,
    IngestionStatus,
    IngestionTask,
)
from stock_prediction.contracts.market_data import Timeframe
from stock_prediction.ingestion.scheduler import (
    EndpointRateLimiter,
    QuotaAwareScheduler,
    RetryPolicy,
    ScheduledCandidate,
)


NOW = datetime(2026, 7, 10, 20, 0, tzinfo=UTC)


def task(
    task_id: str,
    priority: IngestionPriority,
    *,
    status: IngestionStatus = IngestionStatus.PLANNED,
    next_retry_at: datetime | None = None,
) -> IngestionTask:
    return IngestionTask(
        task_id=task_id,
        dataset="market_bars",
        market="US",
        symbol="US.SPY",
        timeframe=Timeframe.DAY_1,
        requested_start=date(2026, 7, 1),
        requested_end=date(2026, 7, 10),
        priority=priority,
        attempt=1 if status is IngestionStatus.RETRYING else 0,
        status=status,
        error_class="rate_limited" if status is IngestionStatus.RETRYING else None,
        next_retry_at=next_retry_at,
        created_at=NOW,
        updated_at=NOW,
    )


def test_scheduler_respects_priority_due_time_and_five_percent_reserve() -> None:
    candidates = [
        ScheduledCandidate(task("low", IngestionPriority.LOW), 2),
        ScheduledCandidate(task("high", IngestionPriority.HIGH), 2),
        ScheduledCandidate(
            task(
                "retry-not-due",
                IngestionPriority.HIGH,
                status=IngestionStatus.RETRYING,
                next_retry_at=NOW + timedelta(minutes=1),
            ),
            1,
        ),
        ScheduledCandidate(task("medium", IngestionPriority.MEDIUM), 2),
    ]

    selected = QuotaAwareScheduler(reserve_ratio=0.05).select(
        candidates, quota_total=100, quota_remaining=9, now=NOW
    )

    assert [item.task.task_id for item in selected] == ["high", "medium"]
    assert sum(item.estimated_requests for item in selected) == 4


def test_endpoint_rate_limiter_uses_only_eighty_percent_of_declared_budget() -> None:
    limiter = EndpointRateLimiter(
        {"request_history_kline": (10, 30.0)}, safety_ratio=0.8
    )

    assert [limiter.allow("request_history_kline", now=0.0) for _ in range(9)] == [
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        False,
    ]
    assert limiter.allow("request_history_kline", now=30.0) is True


def test_retry_policy_is_bounded_and_uses_deterministic_jitter() -> None:
    policy = RetryPolicy(
        base_delay=10.0,
        max_delay=60.0,
        max_attempts=3,
        jitter_ratio=0.2,
        random_value=lambda: 0.75,
    )

    first = policy.next_retry_at(now=NOW, attempt=1, retryable=True)
    third = policy.next_retry_at(now=NOW, attempt=3, retryable=True)

    assert first == NOW + timedelta(seconds=11)
    assert third is None
    assert policy.next_retry_at(now=NOW, attempt=1, retryable=False) is None
