"""Explicit option refresh submission; reads never start provider work."""

from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, Request

from stock_prediction.api.errors import ApiError
from stock_prediction.contracts.operations import EodWorkerState, EodWorkerStatus
from stock_prediction.options.refresh import (
    OptionRefreshOperation,
    OptionRefreshRequest,
    OptionRefreshStore,
    RefreshBusyError,
)
from stock_prediction.options.store import OptionSnapshotStore


def build_option_refresh_router(
    store: OptionSnapshotStore, clock: Callable[[], datetime]
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/options/refresh", tags=["options-refresh"])
    jobs = OptionRefreshStore(store.root)

    def fail(code: str, status: int) -> None:
        raise ApiError(
            status_code=status,
            error_code=code,
            message=code,
            retryable=status in (409, 503),
            affected_scope=["options"],
        )

    @router.get("/worker", response_model=EodWorkerStatus)
    def worker() -> EodWorkerStatus:
        return jobs.worker_status(clock())

    @router.get("/latest", response_model=OptionRefreshOperation | None)
    def latest() -> OptionRefreshOperation | None:
        return jobs.latest()

    @router.get("/{operation_id}", response_model=OptionRefreshOperation)
    def read(operation_id: str) -> OptionRefreshOperation:
        operation = jobs.get(operation_id)
        if operation is None:
            fail("option_refresh_not_found", 404)
        assert operation is not None
        return operation

    @router.post("", response_model=OptionRefreshOperation, status_code=202)
    def submit(body: OptionRefreshRequest, request: Request) -> OptionRefreshOperation:
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            fail("option_refresh_origin_denied", 403)
        if jobs.worker_status(clock()).state != EodWorkerState.READY:
            fail("option_refresh_worker_offline", 503)
        try:
            return jobs.submit(body.symbol, clock())
        except ValueError:
            fail("symbol_outside_option_scope", 422)
        except RefreshBusyError:
            fail("option_refresh_busy", 409)
        raise AssertionError("unreachable")

    return router
