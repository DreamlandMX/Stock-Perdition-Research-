from datetime import timedelta

import pandas as pd
import pytest

from backend.tests.options.test_structure import NOW, contract, snapshot
from stock_prediction.contracts.options import OptionChainSnapshot
from stock_prediction.opend.ports import SdkCallResult
from stock_prediction.options.collector import OptionCollector
from stock_prediction.options.store import OptionSnapshotStore
from stock_prediction.options.structure import build_option_heatmap


@pytest.mark.parametrize("field", ["market_as_of", "underlying_at"])
def test_snapshot_future_market_and_underlying_times_are_rejected(field):
    with pytest.raises(ValueError, match="snapshot market time"):
        OptionChainSnapshot.model_validate(
            snapshot().model_dump() | {field: NOW + timedelta(days=1)}
        )


def test_historical_import_does_not_replace_newer_market_data(tmp_path):
    store = OptionSnapshotStore(tmp_path)
    current = snapshot()
    store.save(current)
    old = current.model_copy(
        update={
            "snapshot_id": "old-import",
            "captured_at": NOW + timedelta(days=1),
            "market_as_of": NOW - timedelta(days=30),
            "source_available_at": NOW,
            "underlying_at": NOW - timedelta(days=30),
        }
    )
    store.save(old)
    assert store.load("US.AAPL").snapshot_id == current.snapshot_id
    assert store.load("US.AAPL", as_of=NOW).snapshot_id == current.snapshot_id
    assert store.latest_attempt("US.AAPL").snapshot_id == "old-import"


def test_gamma_overflow_is_missing():
    huge = contract().model_copy(update={"underlying_price": 1e200})
    assert (
        build_option_heatmap(snapshot((huge,)), metric="gamma", side="call")
        .cells[0]
        .value
        is None
    )


def test_aggregate_overflow_is_missing_with_explicit_quality():
    a = contract().model_copy(update={"open_interest": 1e308})
    b = a.model_copy(update={"contract_id": "other"})
    heat = build_option_heatmap(snapshot((a, b)), metric="oi", side="call")
    assert heat.total is None
    assert "metric_numeric_overflow" in heat.reasons


def test_known_expiry_and_malformed_empty_chains_never_replace_complete_snapshot(
    tmp_path,
):
    store = OptionSnapshotStore(tmp_path)
    store.save(snapshot())

    class EmptyChain:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_global_state(self):
            return SdkCallResult.success({"server_ver": "test"})

        def get_option_expiration_date(self, code):
            return SdkCallResult.success(pd.DataFrame([{"strike_time": "2026-10-16"}]))

        def get_market_snapshot(self, codes):
            return SdkCallResult.success(
                pd.DataFrame(
                    [
                        {
                            "code": "US.AAPL",
                            "last_price": 100,
                            "update_time": "2026-10-09 16:00:00",
                        }
                    ]
                )
            )

        def get_option_chain(self, code, **kwargs):
            return SdkCallResult.success(pd.DataFrame())

    result = OptionCollector(
        store, context_factory=EmptyChain, clock=lambda: NOW
    ).collect(["US.AAPL"])[0]
    assert result.complete is False
    assert result.discovery_complete is False
    assert store.load("US.AAPL").snapshot_id == "sample-1"
