"""Deterministic priority, quota, rate, and retry scheduling."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from threading import Lock

from stock_prediction.contracts.ingestion import (
    IngestionPriority,
    IngestionStatus,
    IngestionTask,
)


@dataclass(frozen=True, slots=True)
class ScheduledCandidate:
    task: IngestionTask
    estimated_requests: int

    def __post_init__(self) -> None:
        if self.estimated_requests < 1:
            raise ValueError("estimated_requests must be positive")


class QuotaAwareScheduler:
    def __init__(self, *, reserve_ratio: float = 0.05) -> None:
        if not 0 <= reserve_ratio < 1:
            raise ValueError("reserve_ratio must be in [0, 1)")
        self.reserve_ratio = reserve_ratio

    def select(
        self,
        candidates: Sequence[ScheduledCandidate],
        *,
        quota_total: int,
        quota_remaining: int,
        now: datetime,
    ) -> tuple[ScheduledCandidate, ...]:
        if quota_total < 0 or quota_remaining < 0 or quota_remaining > quota_total:
            raise ValueError("quota values are inconsistent")
        reserve = math.ceil(quota_total * self.reserve_ratio)
        usable = max(0, quota_remaining - reserve)
        priority_rank = {
            IngestionPriority.HIGH: 0,
            IngestionPriority.MEDIUM: 1,
            IngestionPriority.LOW: 2,
        }
        eligible = [
            candidate
            for candidate in candidates
            if candidate.task.status is IngestionStatus.PLANNED
            or (
                candidate.task.status is IngestionStatus.RETRYING
                and candidate.task.next_retry_at is not None
                and candidate.task.next_retry_at <= now
            )
        ]
        eligible.sort(
            key=lambda candidate: (
                priority_rank[candidate.task.priority],
                candidate.task.updated_at,
                candidate.task.task_id,
            )
        )
        selected: list[ScheduledCandidate] = []
        for candidate in eligible:
            if candidate.estimated_requests <= usable:
                selected.append(candidate)
                usable -= candidate.estimated_requests
        return tuple(selected)


class EndpointRateLimiter:
    """Thread-safe sliding-window limiter with an explicit safety haircut."""

    def __init__(
        self,
        limits: Mapping[str, tuple[int, float]],
        *,
        safety_ratio: float = 0.8,
    ) -> None:
        if not 0 < safety_ratio <= 1:
            raise ValueError("safety_ratio must be in (0, 1]")
        self._limits: dict[str, tuple[int, float]] = {}
        for endpoint, (calls, window) in limits.items():
            if calls < 1 or window <= 0:
                raise ValueError("rate-limit declarations must be positive")
            self._limits[endpoint] = (max(1, math.floor(calls * safety_ratio)), window)
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, endpoint: str, *, now: float) -> bool:
        if endpoint not in self._limits:
            raise KeyError(endpoint)
        capacity, window = self._limits[endpoint]
        with self._lock:
            events = self._events[endpoint]
            while events and events[0] <= now - window:
                events.popleft()
            if len(events) >= capacity:
                return False
            events.append(now)
            return True


class RetryPolicy:
    def __init__(
        self,
        *,
        base_delay: float,
        max_delay: float,
        max_attempts: int,
        jitter_ratio: float,
        random_value: Callable[[], float],
    ) -> None:
        if (
            base_delay <= 0
            or max_delay < base_delay
            or max_attempts < 1
            or not 0 <= jitter_ratio <= 1
        ):
            raise ValueError("retry policy is invalid")
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_attempts = max_attempts
        self.jitter_ratio = jitter_ratio
        self.random_value = random_value

    def next_retry_at(
        self, *, now: datetime, attempt: int, retryable: bool
    ) -> datetime | None:
        if not retryable or attempt >= self.max_attempts:
            return None
        raw_random = self.random_value()
        if not 0 <= raw_random <= 1:
            raise ValueError("random_value must return a value in [0, 1]")
        delay = min(self.max_delay, self.base_delay * (2 ** max(0, attempt - 1)))
        jitter = 1 + (2 * raw_random - 1) * self.jitter_ratio
        return now + timedelta(seconds=delay * jitter)
