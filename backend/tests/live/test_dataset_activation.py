from __future__ import annotations

import json
from pathlib import Path

import pytest

from stock_prediction.live.config import (
    ALPACA_SIP_DATASET_REVISION,
    ALPACA_IEX_YAHOO_DATASET_REVISION,
    LivePaths,
    activate_live_dataset,
    read_active_dataset,
)


def test_staged_dataset_has_a_separate_root_from_legacy_live(tmp_path: Path) -> None:
    legacy = LivePaths.from_workspace(tmp_path)
    staged = LivePaths.for_dataset(tmp_path, ALPACA_SIP_DATASET_REVISION)

    assert legacy.root == tmp_path.resolve() / "var" / "live"
    assert staged.root == (
        tmp_path.resolve()
        / "var"
        / "live-datasets"
        / ALPACA_SIP_DATASET_REVISION
    )
    assert staged.root != legacy.root


def test_activation_is_atomic_and_resolves_the_selected_dataset(tmp_path: Path) -> None:
    staged = LivePaths.for_dataset(tmp_path, ALPACA_SIP_DATASET_REVISION)
    staged.ensure()
    (staged.root / "validation.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "dataset_revision": ALPACA_SIP_DATASET_REVISION,
                "provider_comparison_status": "pass",
            }
        ),
        encoding="utf-8",
    )

    payload = activate_live_dataset(
        tmp_path,
        dataset_revision=ALPACA_SIP_DATASET_REVISION,
    )

    pointer = tmp_path / "var" / "live-active.json"
    assert pointer.is_file()
    assert not pointer.with_suffix(".json.tmp").exists()
    assert json.loads(pointer.read_text(encoding="utf-8"))["dataset_revision"] == (
        ALPACA_SIP_DATASET_REVISION
    )
    assert payload["dataset_revision"] == ALPACA_SIP_DATASET_REVISION
    assert read_active_dataset(tmp_path) == ALPACA_SIP_DATASET_REVISION
    assert LivePaths.active(tmp_path).root == staged.root


def test_unvalidated_dataset_cannot_be_activated(tmp_path: Path) -> None:
    LivePaths.for_dataset(tmp_path, ALPACA_SIP_DATASET_REVISION).ensure()
    with pytest.raises(RuntimeError, match="dataset_validation_required"):
        activate_live_dataset(
            tmp_path,
            dataset_revision=ALPACA_SIP_DATASET_REVISION,
        )

    assert not (tmp_path / "var" / "live-active.json").exists()


def test_alpaca_dataset_without_provider_comparison_cannot_be_activated(
    tmp_path: Path,
) -> None:
    staged = LivePaths.for_dataset(tmp_path, ALPACA_SIP_DATASET_REVISION)
    staged.ensure()
    (staged.root / "validation.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "dataset_revision": ALPACA_SIP_DATASET_REVISION,
                "provider_comparison_status": "not_run",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="dataset_validation_required"):
        activate_live_dataset(tmp_path, dataset_revision=ALPACA_SIP_DATASET_REVISION)


def test_iex_yahoo_dataset_requires_provider_comparison(tmp_path: Path) -> None:
    staged = LivePaths.for_dataset(tmp_path, ALPACA_IEX_YAHOO_DATASET_REVISION)
    staged.ensure()
    (staged.root / "validation.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "dataset_revision": ALPACA_IEX_YAHOO_DATASET_REVISION,
                "provider_comparison_status": "not_run",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="dataset_validation_required"):
        activate_live_dataset(
            tmp_path, dataset_revision=ALPACA_IEX_YAHOO_DATASET_REVISION
        )
