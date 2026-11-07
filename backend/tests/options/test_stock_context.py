from datetime import timedelta
from math import log

from stock_prediction.contracts.serving import HistoryBar, InstrumentHistory
from stock_prediction.contracts.research import RegimeLabel
from stock_prediction.options.stock_context import stock_returns_from_history
from stock_prediction.options.analytics import build_option_analytics
from backend.tests.options.test_analytics import chain, NOW


def test_realized_volatility_is_wired_from_pinned_stock_history_without_future_bars():
    bars = tuple(
        HistoryBar(
            at=NOW - timedelta(days=21 - i),
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100 + i,
            volume=100,
            regime=RegimeLabel.RANGE,
            data_gap=False,
        )
        for i in range(23)
    )
    history = InstrumentHistory(
        symbol="US.AAPL",
        timeframe="1d",
        adjustment="split_adjusted",
        bars=bars,
        events=(),
        gaps=(),
    )
    returns = stock_returns_from_history(
        history, available_at=NOW, annualization_sessions=252
    )
    assert len(returns) == 21
    assert returns[-1].observed_at == NOW
    assert returns[-1].log_return == log(121 / 120)
    result = build_option_analytics(chain(), as_of=NOW, stock_returns=returns)
    assert result.values["realized_volatility_20sessions"] is not None
    assert result.values["iv_minus_realized_20sessions"] is not None


def test_saved_feature_returns_are_unavailable_before_the_artifact_was_created(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import pandas as pd
    import stock_prediction.options.stock_context as context
    from stock_prediction.live.config import LivePaths

    paths = LivePaths.from_workspace(tmp_path)
    paths.features.mkdir(parents=True)
    path = paths.features / "features.parquet"
    pd.DataFrame(
        [
            {
                "symbol": "US.AAPL",
                "session_date": NOW.date(),
                "source_available_at": NOW,
                "capital_gain_return_1": 0.01,
                "quality_quarantined": False,
            }
        ]
    ).to_parquet(path)
    monkeypatch.setattr(
        context,
        "load_latest_feature_artifact",
        lambda _: SimpleNamespace(path=path, generated_at=NOW + timedelta(hours=1)),
    )
    paths = LivePaths.from_workspace(tmp_path)
    assert (
        context.stock_returns_from_feature_artifact(paths, "US.AAPL", as_of=NOW) == ()
    )
    result = context.stock_returns_from_feature_artifact(
        paths, "US.AAPL", as_of=NOW + timedelta(hours=2)
    )
    assert result[0].source_available_at == NOW + timedelta(hours=1)
    assert result[0].observed_at == NOW


def test_feature_request_reuses_sha_and_frame_without_merging_cutoffs(
    tmp_path, monkeypatch
):
    import json
    import pandas as pd
    import pytest
    import stock_prediction.options.stock_context as context
    import stock_prediction.live.artifact_references as references
    from stock_prediction.live.config import LivePaths

    paths = LivePaths.from_workspace(tmp_path)
    directory = paths.features / "batch"
    directory.mkdir(parents=True)
    path = directory / "features.parquet"
    pd.DataFrame(
        [
            {
                "symbol": symbol,
                "session_date": at.date(),
                "source_available_at": at,
                "capital_gain_return_1": value,
                "quality_quarantined": False,
            }
            for symbol in ("US.AAPL", "US.MSFT")
            for at, value in ((NOW - timedelta(days=1), 0.01), (NOW, 0.02))
        ]
    ).to_parquet(path)
    generated = NOW - timedelta(days=2)
    reference = references.build_artifact_reference(
        artifact_root=paths.features,
        artifact_id="batch",
        target=path,
        schema_version="live-feature-artifact-v1",
        created_at=generated,
    )
    pointer = paths.features / "LATEST.json"
    pointer.write_text(
        json.dumps(
            {
                "artifact_reference": reference.as_dict(),
                "input_hash": "batch",
                "generated_at": generated.isoformat(),
                "as_of": NOW.isoformat(),
                "row_count": 4,
                "feature_set_version": "test",
                "dataset_revision": "test",
            }
        ),
        encoding="utf-8",
    )
    hash_file = references.sha256_file
    calls = []

    def counted(target):
        calls.append(target)
        return hash_file(target)

    monkeypatch.setattr(references, "sha256_file", counted)
    with context.stock_return_scope():
        early = context.stock_returns_from_feature_artifact(
            paths, "US.AAPL", as_of=NOW - timedelta(seconds=1)
        )
        late = context.stock_returns_from_feature_artifact(paths, "US.AAPL", as_of=NOW)
        other = context.stock_returns_from_feature_artifact(paths, "US.MSFT", as_of=NOW)
    assert len(early) == 1
    assert len(late) == len(other) == 2
    assert late[-1].log_return == pytest.approx(0.01980262729617971)
    assert len(calls) == 1
    with context.stock_return_scope():
        assert (
            context.stock_returns_from_feature_artifact(paths, "US.AAPL", as_of=NOW)
            == late
        )
    assert len(calls) == 2
    path.write_bytes(b"corrupt parquet")
    with (
        context.stock_return_scope(),
        pytest.raises(RuntimeError, match="hash_mismatch"),
    ):
        context.stock_returns_from_feature_artifact(paths, "US.AAPL", as_of=NOW)
    path.unlink()
    with context.stock_return_scope(), pytest.raises(RuntimeError):
        context.stock_returns_from_feature_artifact(paths, "US.AAPL", as_of=NOW)
