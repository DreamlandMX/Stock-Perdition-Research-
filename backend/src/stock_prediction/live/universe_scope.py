"""Bind each immutable live dataset revision to its governed universe."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stock_prediction.live.config import (
    ALPACA_IEX_YAHOO_DATASET_REVISION,
    ALPACA_SIP_DATASET_REVISION,
    LIVE_DATASET_REVISION,
    OPEND_EXPANDED_DATASET_REVISION,
    LivePaths,
)
from stock_prediction.universe.active import (
    ACTIVE_ETFS,
    ACTIVE_UNIVERSE,
    ACTIVE_UNIVERSE_EFFECTIVE_FROM,
    ACTIVE_UNIVERSE_REASON,
    ACTIVE_UNIVERSE_VERSION,
)
from stock_prediction.universe.seed import (
    SEED_ETFS,
    SEED_UNIVERSE,
    SEED_UNIVERSE_REASON,
    SEED_UNIVERSE_VERSION,
)


@dataclass(frozen=True, slots=True)
class LiveUniverseSpec:
    version: str
    symbols: tuple[str, ...]
    etfs: tuple[str, ...]
    effective_from: date
    reason: str

    def __post_init__(self) -> None:
        if not self.symbols or len(self.symbols) != len(set(self.symbols)):
            raise RuntimeError("universe_scope_mismatch")
        if not set(self.etfs).issubset(self.symbols):
            raise RuntimeError("universe_scope_mismatch")
        if not all(symbol.startswith("US.") for symbol in self.symbols):
            raise RuntimeError("universe_scope_mismatch")


SEED_LIVE_UNIVERSE = LiveUniverseSpec(
    version=SEED_UNIVERSE_VERSION,
    symbols=SEED_UNIVERSE,
    etfs=SEED_ETFS,
    effective_from=date(2026, 7, 11),
    reason=SEED_UNIVERSE_REASON,
)

EXPANDED_LIVE_UNIVERSE = LiveUniverseSpec(
    version=ACTIVE_UNIVERSE_VERSION,
    symbols=ACTIVE_UNIVERSE,
    etfs=ACTIVE_ETFS,
    effective_from=ACTIVE_UNIVERSE_EFFECTIVE_FROM,
    reason=ACTIVE_UNIVERSE_REASON,
)


def resolve_live_universe(paths: LivePaths) -> LiveUniverseSpec:
    if paths.dataset_revision == LIVE_DATASET_REVISION:
        return SEED_LIVE_UNIVERSE
    if paths.dataset_revision in {
        OPEND_EXPANDED_DATASET_REVISION,
        ALPACA_SIP_DATASET_REVISION,
        ALPACA_IEX_YAHOO_DATASET_REVISION,
    }:
        return EXPANDED_LIVE_UNIVERSE
    raise RuntimeError("universe_scope_mismatch")


__all__ = [
    "EXPANDED_LIVE_UNIVERSE",
    "LiveUniverseSpec",
    "SEED_LIVE_UNIVERSE",
    "resolve_live_universe",
]
