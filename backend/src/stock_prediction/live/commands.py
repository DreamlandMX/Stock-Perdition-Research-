"""CLI routing for production OpenD workflows."""

from __future__ import annotations

from argparse import Namespace
from datetime import UTC, date, datetime
from pathlib import Path

from filelock import Timeout

from stock_prediction.live.config import (
    ALPACA_SIP_DATASET_REVISION,
    ALPACA_IEX_YAHOO_DATASET_REVISION,
    LivePaths,
    activate_live_dataset,
)
from stock_prediction.live.dataset_validation import validate_live_dataset
from stock_prediction.live.features import LiveFeatureBuilder
from stock_prediction.live.ingestion import LiveOpenDService
from stock_prediction.live.market_data_service import LiveMarketDataService
from stock_prediction.live.locking import LiveWriterLease
from stock_prediction.live.operations import EodUpdateCoordinator
from stock_prediction.live.provider_comparison import compare_provider_catalogs
from stock_prediction.live.publication import LiveSnapshotPublisher
from stock_prediction.live.scheduler import run_scheduler
from stock_prediction.live.storage import LiveStorage
from stock_prediction.live.training import LiveModelTrainer
from stock_prediction.live.universe_scope import resolve_live_universe
from stock_prediction.live.ingestion import _last_completed_session
from stock_prediction.universe.seed import SEED_UNIVERSE


def run_live_command(args: Namespace) -> dict[str, object]:
    workspace = Path(args.workspace_root)
    command = str(args.command)
    provider_name = str(getattr(args, "provider", "opend"))
    revision = getattr(args, "dataset_revision", None)
    if command == "live-activate-dataset":
        return activate_live_dataset(workspace, dataset_revision=revision)
    if command == "live-validate-dataset":
        paths = LivePaths.for_dataset(workspace, revision)
        comparison = compare_provider_catalogs(
            LivePaths.from_workspace(workspace),
            paths,
            symbols=SEED_UNIVERSE,
        )
        return validate_live_dataset(
            paths,
            symbols=resolve_live_universe(paths).symbols,
            expected_cutoff=_expected_cutoff(paths),
            provider_comparison=comparison,
            required_daily_start=(
                date(2018, 1, 1)
                if revision == ALPACA_IEX_YAHOO_DATASET_REVISION
                else None
            ),
            required_intraday_start=(
                date(2024, 8, 12)
                if revision == ALPACA_IEX_YAHOO_DATASET_REVISION
                else None
            ),
        )
    staged_revision = (
        ALPACA_IEX_YAHOO_DATASET_REVISION
        if provider_name == "alpaca-iex"
        else ALPACA_SIP_DATASET_REVISION
    )
    staged = provider_name in {"alpaca-sip", "alpaca-iex"} and command in {
        "live-preflight",
        "live-bootstrap",
    }
    paths = (
        LivePaths.for_dataset(workspace, revision)
        if revision is not None
        else LivePaths.for_dataset(workspace, staged_revision)
        if staged
        else LivePaths.active(workspace)
    )
    if command in {
        "live-preflight",
        "live-bootstrap",
        "live-expand-universe",
        "live-refresh",
        "live-build-features",
        "live-train",
        "live-publish",
    }:
        try:
            with LiveWriterLease(paths, task_id=command, timeout=0):
                if command == "live-preflight":
                    if provider_name in {"alpaca-sip", "alpaca-iex"}:
                        return LiveMarketDataService(paths).preflight().as_dict()
                    return LiveOpenDService(paths).preflight().as_dict()
                if command == "live-bootstrap":
                    if provider_name in {"alpaca-sip", "alpaca-iex"}:
                        return LiveMarketDataService(paths).bootstrap()
                    return LiveOpenDService(paths).bootstrap()
                if command == "live-expand-universe":
                    return LiveOpenDService(paths).expand_universe(
                        allow_zero_reserve=bool(args.allow_zero_reserve)
                    )
                if command == "live-refresh":
                    if paths.dataset_revision in {
                        ALPACA_SIP_DATASET_REVISION,
                        ALPACA_IEX_YAHOO_DATASET_REVISION,
                    }:
                        return LiveMarketDataService(paths).refresh()
                    return LiveOpenDService(paths).refresh()
                if command == "live-build-features":
                    return {
                        "status": "ok",
                        **LiveFeatureBuilder(paths).build().as_dict(),
                    }
                if command == "live-train":
                    return LiveModelTrainer(paths).train()
                return LiveSnapshotPublisher(paths).publish()
        except Timeout as error:
            raise RuntimeError("live_pipeline_busy") from error
    if command == "live-run-once":
        coordinator = EodUpdateCoordinator(paths)
        operation = coordinator.submit()
        completed = coordinator.run_pending()
        return (completed or operation).model_dump(mode="json")
    if command == "live-scheduler":
        return run_scheduler(LivePaths.active(workspace))
    raise ValueError("unsupported live command")


def _expected_cutoff(paths: LivePaths) -> date:
    storage = LiveStorage(paths)
    sessions = storage.trading_days(date(2018, 1, 1), date.today())
    if not sessions:
        raise RuntimeError("dataset_calendar_unavailable")
    return _last_completed_session(tuple(sessions.items()), datetime.now(UTC))


__all__ = ["run_live_command"]
