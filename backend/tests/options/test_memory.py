from datetime import timedelta

import pytest

from backend.tests.options.test_analytics import NOW, chain
from stock_prediction.options.store import OptionSnapshotStore


def test_repeated_snapshot_resolution_is_scoped_to_one_request(tmp_path):
    from collections import Counter
    from types import SimpleNamespace
    from stock_prediction.api.options_router import (
        build_options_router,
        OptionOverviewParameters,
    )
    from stock_prediction.serving.repository import SnapshotNotFound

    calls = Counter()

    def resolve(query):
        calls[query.as_of] += 1
        raise SnapshotNotFound("no historical snapshot")

    store = OptionSnapshotStore(tmp_path)
    store.save(chain())
    router = build_options_router(
        store=store,
        clock=lambda: NOW,
        request_id_factory=lambda: "scoped",
        repository=SimpleNamespace(resolve_snapshot=resolve),
    )
    endpoint = next(
        r.endpoint for r in router.routes if r.path == "/api/v1/options/overview"
    )
    for expected in (1, 2):
        endpoint(OptionOverviewParameters(symbol="US.AAPL"))
        assert calls[NOW] == expected


def test_snapshot_cache_is_byte_bounded_and_still_detects_replacements(
    tmp_path, monkeypatch
):
    import stock_prediction.options.store as storage

    monkeypatch.setattr(storage, "_SNAPSHOT_CACHE_BYTES", 1)
    store = OptionSnapshotStore(tmp_path)
    first = chain(identity="first")
    store.save(first)
    store.save(chain(at=NOW + timedelta(days=1), identity="second"))
    assert len(store._snapshots) == 1
    with store.read_scope():
        loaded = store.load("US.AAPL", "first")
        before = store.identity(loaded)
        store.load("US.AAPL", "second")
        assert store.load("US.AAPL", "first") is loaded
        path = tmp_path / "US.AAPL" / "first.json"
        path.write_text(
            first.model_copy(update={"underlying_price": 101}).model_dump_json()
        )
        with pytest.raises(ValueError, match="changed during the request"):
            store.identity(loaded)
    assert store._request_reads.get() is None
    assert store.identity(store.load("US.AAPL", "first")) != before


def test_full_overview_and_experiments_release_previous_symbol_reads(
    tmp_path, monkeypatch
):
    import stock_prediction.api.options_router as routes
    from stock_prediction.contracts.options import OPTION_SYMBOLS

    store = OptionSnapshotStore(tmp_path)
    for symbol in OPTION_SYMBOLS[:3]:
        saved = chain(identity=symbol.replace(".", "-"))
        store.save(
            saved.model_copy(
                update={
                    "symbol": symbol,
                    "contracts": tuple(
                        c.model_copy(update={"underlying": symbol})
                        for c in saved.contracts
                    ),
                }
            )
        )
    original = store.snapshots
    seen = []

    def tracked(symbol):
        result = original(symbol)
        retained = {path.parent.name for path in (store._request_reads.get() or {})}
        assert retained <= {symbol}
        seen.append(symbol)
        return result

    monkeypatch.setattr(store, "snapshots", tracked)
    router = routes.build_options_router(
        store=store, clock=lambda: NOW, request_id_factory=lambda: "memory"
    )
    overview = next(
        r.endpoint for r in router.routes if r.path == "/api/v1/options/overview"
    )
    experiments = next(
        r.endpoint for r in router.routes if r.path == "/api/v1/options/experiments"
    )
    result = overview(routes.OptionOverviewParameters())
    assert len(result.items) == len(OPTION_SYMBOLS)
    assert result.items[0].snapshot_id is not None
    experiments()
    assert set(OPTION_SYMBOLS[:3]) <= set(seen)
    assert store._request_reads.get() is None


def test_streamed_coverage_preserves_pooled_counts_and_rolling_windows():
    from stock_prediction.contracts.options import OPTION_SYMBOLS, OptionStockReturn
    from stock_prediction.options.overview import (
        build_interval_coverage,
        build_interval_samples,
        summarize_interval_coverage,
    )
    from stock_prediction.options.serving import (
        OptionCandidateState,
        OptionForecastHorizon,
        OptionForecastReadModel,
    )

    symbols = OPTION_SYMBOLS[:20]
    sessions = {(NOW + timedelta(days=n)).date(): "WHOLE" for n in range(21)}
    at = NOW + timedelta(days=20)
    histories = {symbol: (chain(),) for symbol in symbols}
    returns = {
        symbol: tuple(
            OptionStockReturn(
                symbol=symbol,
                session=(NOW + timedelta(days=n)).date(),
                log_return=0,
                observed_at=NOW + timedelta(days=n),
                source_available_at=NOW + timedelta(days=n),
                annualization_sessions=252,
            )
            for n in range(1, 21)
        )
        for symbol in symbols
    }

    def forecast(symbol, entry):
        return OptionForecastReadModel(
            symbol=symbol,
            status="baseline_fallback",
            stock_cutoff=NOW,
            candidate=OptionCandidateState(status="historical_baseline"),
            horizons=tuple(
                OptionForecastHorizon(horizon_days=h, q10=-0.01, q50=0, q90=0.01)
                for h in (1, 5, 20)
            ),
        )

    expected = build_interval_coverage(
        histories, returns, sessions=sessions, at=at, forecast_reader=forecast
    )
    samples = {h: [] for h in (1, 5, 20)}
    for symbol in symbols:
        matched = build_interval_samples(
            {symbol: histories[symbol]},
            {symbol: returns[symbol]},
            sessions=sessions,
            at=at,
            forecast_reader=forecast,
        )
        for horizon, values in matched.items():
            samples[horizon].extend(values)
    assert summarize_interval_coverage(samples) == expected
    for result in expected:
        if result.horizon_days != 20:
            assert result.status == "insufficient_samples"
            assert result.model_samples == result.implied_samples == 0
            continue
        assert result.status == "available"
        assert result.model_samples == result.implied_samples == 20
        assert result.model_coverage == result.implied_coverage == 1
        assert result.history[-2].model_coverage is None
        assert result.history[-1].model_coverage == 1
