from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from stock_prediction.features.dag import (
    FeatureCacheKey,
    FeatureDag,
    FeatureDagError,
    FeatureNode,
)


def calendar(count: int = 40) -> tuple[date, ...]:
    start = date(2026, 1, 1)
    return tuple(start + timedelta(days=index) for index in range(count))


def dag() -> FeatureDag:
    return FeatureDag(
        (
            FeatureNode(name="raw_daily", version="raw-v1"),
            FeatureNode(
                name="primitives",
                version="primitives-v1",
                dependencies=("raw_daily",),
                forward_impact_sessions=20,
            ),
            FeatureNode(
                name="core",
                version="core-v1",
                dependencies=("primitives",),
            ),
            FeatureNode(
                name="horizon_context",
                version="context-v1",
                dependencies=("core",),
                horizon_dependent=True,
            ),
        )
    )


def test_topology_and_input_hash_are_deterministic() -> None:
    graph = dag()

    assert graph.topological_order == (
        "raw_daily",
        "primitives",
        "core",
        "horizon_context",
    )
    hash_graph = FeatureDag(
        (
            FeatureNode(name="a", version="v1"),
            FeatureNode(name="z", version="v1"),
            FeatureNode(name="combined", version="v1", dependencies=("a", "z")),
        )
    )
    first = hash_graph.input_hash(
        "combined", dependency_hashes={"z": "2" * 64, "a": "1" * 64}
    )
    second = hash_graph.input_hash(
        "combined", dependency_hashes={"a": "1" * 64, "z": "2" * 64}
    )
    assert first == second


def test_cache_key_only_contains_horizon_for_true_horizon_nodes() -> None:
    base = {
        "node_name": "core",
        "dataset_revision": "dataset-r1",
        "feature_set_version": "features-v1",
        "symbol": "US.AAPL",
        "as_of": datetime(2026, 7, 10, 20, tzinfo=UTC),
    }
    key = FeatureCacheKey(horizon_dependent=False, horizon=None, **base)
    assert key.horizon is None

    with pytest.raises(ValueError, match="horizon is forbidden"):
        FeatureCacheKey(horizon_dependent=False, horizon=5, **base)
    with pytest.raises(ValueError, match="horizon is required"):
        FeatureCacheKey(
            horizon_dependent=True,
            horizon=None,
            **{**base, "node_name": "horizon_context"},
        )


def test_upstream_change_invalidates_only_symbol_descendants_and_lookahead() -> None:
    dates = calendar()
    change_day = dates[5]

    invalidations = dag().affected_descendants(
        "raw_daily",
        symbol="US.AAPL",
        affected_start=change_day,
        affected_end=change_day,
        trading_calendar=dates,
        dataset_revision="dataset-r1",
        feature_set_version="features-v1",
    )

    assert [(item.node_name, item.symbol) for item in invalidations] == [
        ("primitives", "US.AAPL"),
        ("core", "US.AAPL"),
        ("horizon_context", "US.AAPL"),
    ]
    assert {item.affected_start for item in invalidations} == {change_day}
    assert {item.affected_end for item in invalidations} == {dates[25]}
    assert all(item.dataset_revision == "dataset-r1" for item in invalidations)
    assert invalidations[-1].horizons == (1, 5, 20)


def test_rejects_missing_dependencies_duplicates_and_cycles() -> None:
    with pytest.raises(FeatureDagError, match="duplicate"):
        FeatureDag((FeatureNode(name="a", version="v1"),) * 2)
    with pytest.raises(FeatureDagError, match="unknown dependency"):
        FeatureDag((FeatureNode(name="a", version="v1", dependencies=("x",)),))
    with pytest.raises(FeatureDagError, match="cycle"):
        FeatureDag(
            (
                FeatureNode(name="a", version="v1", dependencies=("b",)),
                FeatureNode(name="b", version="v1", dependencies=("a",)),
            )
        )
