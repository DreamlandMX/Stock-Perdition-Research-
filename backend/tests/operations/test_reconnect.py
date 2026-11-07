from pathlib import Path
from typing import cast

from stock_prediction.opend.errors import OpenDError, OpenDErrorClass
from stock_prediction.opend.ports import SdkCallResult
from stock_prediction.operations.reconnect import (
    ClosableQuoteContext,
    ResilientQuoteSession,
)


class FakeContext:
    def __init__(self, result: SdkCallResult[str]) -> None:
        self.result = result
        self.closed = 0

    def request(self) -> SdkCallResult[str]:
        return self.result

    def close(self) -> None:
        self.closed += 1


def invoke(context: ClosableQuoteContext) -> SdkCallResult[str]:
    return cast(FakeContext, context).request()


def test_retryable_disconnect_recreates_context_and_preserves_caller_cursor(
    tmp_path: Path,
) -> None:
    failed = FakeContext(
        SdkCallResult.failure(
            OpenDError(
                OpenDErrorClass.CONNECTION_FAILED,
                "OpenD connection failed",
                retryable=True,
            )
        )
    )
    recovered = FakeContext(SdkCallResult.success("next-page"))
    contexts = iter([failed, recovered])
    delays: list[float] = []
    persisted_cursor = tmp_path / "cursor.txt"
    persisted_cursor.write_text("last-committed-page", encoding="utf-8")

    with ResilientQuoteSession(
        lambda: next(contexts), delays=(0.25,), sleeper=delays.append
    ) as session:
        result = session.call(invoke)

    assert result.value == "next-page"
    assert persisted_cursor.read_text(encoding="utf-8") == "last-committed-page"
    assert failed.closed == 1
    assert recovered.closed == 1
    assert delays == [0.25]
    assert session.attempts[0].error_class == "connection_failed"


def test_non_retryable_permission_error_does_not_reconnect() -> None:
    denied = FakeContext(
        SdkCallResult.failure(
            OpenDError(
                OpenDErrorClass.PERMISSION_DENIED,
                "OpenD permission denied",
                retryable=False,
            )
        )
    )
    calls = 0

    def factory() -> FakeContext:
        nonlocal calls
        calls += 1
        return denied

    with ResilientQuoteSession(
        factory, delays=(0.1,), sleeper=lambda _delay: None
    ) as session:
        result = session.call(invoke)

    assert result.error is not None
    assert result.error.error_class is OpenDErrorClass.PERMISSION_DENIED
    assert calls == 1
    assert session.attempts == []
