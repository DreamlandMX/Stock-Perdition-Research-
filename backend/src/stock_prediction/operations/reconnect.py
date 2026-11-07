"""Bounded OpenD reconnect wrapper; ingestion watermarks remain external evidence."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import sleep as system_sleep
from typing import Protocol, TypeVar

from stock_prediction.opend.errors import OpenDError, classify_sdk_exception
from stock_prediction.opend.ports import SdkCallResult

T = TypeVar("T")


class ClosableQuoteContext(Protocol):
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ReconnectAttempt:
    attempt: int
    delay_seconds: float
    error_class: str


class ResilientQuoteSession:
    """Recreate one read-only quote context after retryable failures."""

    def __init__(
        self,
        factory: Callable[[], ClosableQuoteContext],
        *,
        delays: Sequence[float] = (0.5, 1.0, 2.0),
        sleeper: Callable[[float], None] = system_sleep,
    ) -> None:
        if not delays or any(delay < 0 for delay in delays):
            raise ValueError("reconnect delays must be non-negative")
        self.factory = factory
        self.delays = tuple(delays)
        self.sleeper = sleeper
        self._context: ClosableQuoteContext | None = None
        self._closed = False
        self.attempts: list[ReconnectAttempt] = []

    def call(
        self,
        operation: Callable[[ClosableQuoteContext], SdkCallResult[T]],
    ) -> SdkCallResult[T]:
        if self._closed:
            raise RuntimeError("quote session is closed")
        last_error: OpenDError | None = None
        for attempt in range(1, len(self.delays) + 2):
            try:
                if self._context is None:
                    self._context = self.factory()
                result = operation(self._context)
            except Exception as exception:
                result = SdkCallResult.failure(classify_sdk_exception(exception))
            if result.is_ok:
                return result
            if result.error is None:
                raise RuntimeError("failed SDK call omitted error evidence")
            last_error = result.error
            if not last_error.retryable or attempt > len(self.delays):
                return result
            self._discard_context()
            delay = self.delays[attempt - 1]
            self.attempts.append(
                ReconnectAttempt(
                    attempt=attempt,
                    delay_seconds=delay,
                    error_class=last_error.error_class.value,
                )
            )
            self.sleeper(delay)
        if last_error is None:
            raise RuntimeError("reconnect loop produced no result")
        return SdkCallResult.failure(last_error)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._discard_context()

    def __enter__(self) -> "ResilientQuoteSession":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _discard_context(self) -> None:
        if self._context is None:
            return
        context = self._context
        self._context = None
        try:
            context.close()
        except Exception:
            pass
