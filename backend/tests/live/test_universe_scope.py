from datetime import date
from pathlib import Path

import pytest

from stock_prediction.live.config import (
    ALPACA_IEX_YAHOO_DATASET_REVISION,
    ALPACA_SIP_DATASET_REVISION,
    LivePaths,
)
from stock_prediction.live.universe_scope import resolve_live_universe
from stock_prediction.universe.active import ACTIVE_ETFS, ACTIVE_UNIVERSE
from stock_prediction.universe.seed import (
    SEED_ETFS,
    SEED_UNIVERSE,
    SEED_UNIVERSE_VERSION,
)


def test_legacy_opend_dataset_resolves_to_frozen_seed_universe(tmp_path: Path) -> None:
    scope = resolve_live_universe(LivePaths.from_workspace(tmp_path))

    assert scope.version == SEED_UNIVERSE_VERSION
    assert scope.symbols == SEED_UNIVERSE
    assert scope.etfs == SEED_ETFS
    assert scope.effective_from == date(2026, 7, 11)


@pytest.mark.parametrize(
    "revision",
    [ALPACA_SIP_DATASET_REVISION, ALPACA_IEX_YAHOO_DATASET_REVISION],
)
def test_staged_alpaca_datasets_resolve_to_expanded_universe(
    tmp_path: Path, revision: str
) -> None:
    scope = resolve_live_universe(LivePaths.for_dataset(tmp_path, revision))

    assert scope.symbols == ACTIVE_UNIVERSE
    assert scope.etfs == ACTIVE_ETFS
    assert len(scope.symbols) == 204


def test_unknown_dataset_revision_fails_closed(tmp_path: Path) -> None:
    paths = LivePaths.for_dataset(tmp_path, "unknown-provider-v1")

    with pytest.raises(RuntimeError, match="universe_scope_mismatch"):
        resolve_live_universe(paths)
