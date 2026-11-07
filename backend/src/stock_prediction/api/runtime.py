"""Environment-bound local runtime without fixture or trading fallbacks."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import os
from pathlib import Path

from fastapi import FastAPI

from stock_prediction.api.app import create_app
from stock_prediction.contracts.m7_status import M7LifecycleStatus
from stock_prediction.api.dependencies import live_component_state_provider
from stock_prediction.live.config import LivePaths
from stock_prediction.live.operation_store import EodOperationStore
from stock_prediction.live.model_operations import ModelTrainingCoordinator
from stock_prediction.live.m7_status import build_m7_lifecycle_status
from stock_prediction.live.m7_bundle_executor import m7_executor_configured
from stock_prediction.live.promotion_operations import ModelPromotionOperationStore
from stock_prediction.serving.repository import DuckDbResearchRepository
from stock_prediction.options.store import OptionSnapshotStore
from stock_prediction.contracts.options import OptionStockReturn
from stock_prediction.options.stock_context import stock_returns_from_feature_artifact


class RuntimeConfigurationError(RuntimeError):
    """Runtime environment points to an unsafe or unavailable research root."""


def create_runtime_app(environ: Mapping[str, str] | None = None) -> FastAPI:
    environment = os.environ if environ is None else environ
    allow_unavailable = environment.get("PREDICTION_STOCK_ALLOW_UNAVAILABLE") == "1"
    configured = environment.get("PREDICTION_STOCK_RESEARCH_ROOT")
    if configured is None or configured.strip() == "":
        if allow_unavailable:
            return create_app()
        raise RuntimeConfigurationError("research snapshot root is unavailable")
    supplied = Path(configured)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise RuntimeConfigurationError("research snapshot root is invalid")
    try:
        root = supplied.resolve(strict=True)
    except OSError as error:
        raise RuntimeConfigurationError(
            "research snapshot root is unavailable"
        ) from error
    if not root.is_dir():
        raise RuntimeConfigurationError("research snapshot root is unavailable")
    if not any(root.glob("*/manifest.json")):
        if allow_unavailable:
            return create_app()
        raise RuntimeConfigurationError("research snapshot manifest is unavailable")
    live_root = environment.get("PREDICTION_STOCK_LIVE_ROOT")
    provider = None
    operation_controller = None
    model_training_controller = None
    model_promotion_controller = None
    m7_status_provider: Callable[[], M7LifecycleStatus] | None = None
    options_store = OptionSnapshotStore()
    option_stock_returns_provider: (
        Callable[[str, datetime], tuple[OptionStockReturn, ...]] | None
    ) = None
    if live_root:
        candidate = Path(live_root)
        if candidate.is_absolute():
            provider = live_component_state_provider(
                candidate / "status" / "opend-health.json",
                lambda: datetime.now(UTC),
            )
            resolved_live = candidate.resolve()
            workspace = (
                resolved_live.parents[2]
                if resolved_live.parent.name == "live-datasets"
                else resolved_live.parents[1]
            )
            options_store = OptionSnapshotStore(workspace / "var" / "options")
            operation_controller = EodOperationStore(
                _paths_for_live_root(candidate, workspace)
            )
            model_training_controller = ModelTrainingCoordinator(
                _paths_for_live_root(candidate, workspace)
            )
            model_promotion_controller = ModelPromotionOperationStore(
                _paths_for_live_root(candidate, workspace)
            )
            live_paths = _paths_for_live_root(candidate, workspace)

            def provide_option_stock_returns(
                symbol: str, as_of: datetime
            ) -> tuple[OptionStockReturn, ...]:
                return stock_returns_from_feature_artifact(
                    live_paths, symbol, as_of=as_of
                )

            option_stock_returns_provider = provide_option_stock_returns

            def provide_m7_status() -> M7LifecycleStatus:
                return build_m7_lifecycle_status(
                    live_paths,
                    executor_configured=m7_executor_configured(),
                )

            m7_status_provider = provide_m7_status
    return create_app(
        options_store=options_store,
        option_stock_returns_provider=option_stock_returns_provider,
        research_repository=DuckDbResearchRepository(root),
        state_provider=provider,
        operation_controller=operation_controller,
        model_training_controller=model_training_controller,
        model_promotion_controller=model_promotion_controller,
        m7_status_provider=m7_status_provider,
    )


def _paths_for_live_root(candidate: Path, workspace: Path) -> LivePaths:
    resolved = candidate.resolve()
    if resolved.parent.name == "live-datasets":
        return LivePaths.for_dataset(workspace, resolved.name)
    if resolved.name == "live":
        return LivePaths.from_workspace(workspace)
    raise RuntimeConfigurationError("live root is not a governed dataset")


try:
    app = create_runtime_app()
except RuntimeConfigurationError:
    if os.environ.get("PREDICTION_STOCK_STRICT_STARTUP") == "1":
        raise
    app = create_app()
