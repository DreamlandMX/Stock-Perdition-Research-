from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pandas.testing as pdt
import pytest

from stock_prediction.features.m7 import (
    M7FeatureInputError,
    build_m7_daily_features,
    m7_feature_dag,
)
from stock_prediction.modeling.m7_recipe import M7_FEATURE_SET_VERSION
from stock_prediction.universe.seed import SEED_ETFS, SEED_UNIVERSE


DEPENDENCIES = {
    "adjusted_bars": "1" * 64,
    "calendar": "2" * 64,
    "classification": "3" * 64,
    "universe": "4" * 64,
}


def _daily_frame(session_count: int = 80) -> pd.DataFrame:
    sessions = pd.bdate_range("2026-04-01", periods=session_count)
    rows: list[dict[str, object]] = []
    for symbol_index, symbol in enumerate(SEED_UNIVERSE):
        base = 80.0 + symbol_index
        for session_index, timestamp in enumerate(sessions):
            trend = 1.0 + 0.0005 * (symbol_index + 1)
            close = base * (trend**session_index)
            rows.append(
                {
                    "symbol": symbol,
                    "session_date": timestamp.date(),
                    "source_available_at": datetime.combine(
                        timestamp.date(),
                        datetime.min.time(),
                        tzinfo=UTC,
                    ),
                    "open": close * (1.0 - 0.001),
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 1_000_000.0 + symbol_index * 10_000 + session_index,
                    "eligible": True,
                    "asset_type": "etf" if symbol in SEED_ETFS else "stock",
                    "asset_type_version": "asset-type-v1",
                    "sector": "technology" if symbol_index % 2 else "broad_market",
                    "sector_mapping_version": "sector-map-v1",
                    "dataset_revision": "bars-r7",
                    "universe_version": "m1-seed-2026-07-11-v1",
                }
            )
    return pd.DataFrame(rows)


def test_builds_same_anchor_cross_section_structure_and_context() -> None:
    result = build_m7_daily_features(
        _daily_frame(),
        dependency_hashes=DEPENDENCIES,
    )
    latest = result.frame[
        result.frame["session_date"] == result.frame["session_date"].max()
    ].sort_values("symbol")

    assert result.feature_set_version == M7_FEATURE_SET_VERSION
    assert len(result.input_hash) == 64
    assert dict(result.dependency_hashes) == DEPENDENCIES
    assert len(latest) == 100
    assert set(latest["cross_section_eligible_count"]) == {100}
    assert set(latest["cross_section_coverage"]) == {1.0}
    assert latest["return_20_percentile"].between(0.0, 1.0).all()
    assert latest["return_20_percentile"].is_monotonic_increasing is False
    assert latest["distance_recent_high_20"].notna().all()
    assert latest["distance_recent_low_20"].notna().all()
    assert latest["overnight_gap_atr"].notna().all()
    assert latest["spy_state"].notna().all()
    assert latest["qqq_state"].notna().all()
    assert latest["market_breadth_20"].notna().all()
    assert latest["cross_section_dispersion_20"].notna().all()
    assert latest["asset_type_version"].eq("asset-type-v1").all()
    assert latest["sector_mapping_version"].eq("sector-map-v1").all()
    assert result.group_status["intraday_optional"].status == "not_eligible"
    assert result.group_status["intraday_optional"].reason == "intraday_not_supplied"


def test_rank_direction_uses_only_same_anchor_eligible_symbols() -> None:
    source = _daily_frame()
    result = build_m7_daily_features(source, dependency_hashes=DEPENDENCIES)
    anchor = result.frame["session_date"].max()
    latest = result.frame[result.frame["session_date"] == anchor]
    low = latest.loc[latest["return_20"].idxmin()]
    high = latest.loc[latest["return_20"].idxmax()]

    assert low["return_20_percentile"] == pytest.approx(0.01)
    assert high["return_20_percentile"] == pytest.approx(1.0)
    assert low["relative_strength_spy_20"] <= high["relative_strength_spy_20"]


def test_thin_cross_section_returns_null_with_reason_instead_of_rescaling() -> None:
    source = _daily_frame()
    anchor = source["session_date"].max()
    affected = source["session_date"].eq(anchor) & source["symbol"].isin(
        SEED_UNIVERSE[:10]
    )
    source.loc[affected, "eligible"] = False

    result = build_m7_daily_features(source, dependency_hashes=DEPENDENCIES)
    latest = result.frame[result.frame["session_date"] == anchor]

    assert set(latest["cross_section_eligible_count"]) == {90}
    assert set(latest["cross_section_coverage"]) == {0.9}
    assert latest["return_20_percentile"].isna().all()
    assert latest["cross_section_missing_reason"].eq("thin_cross_section").all()


def test_future_rows_cannot_change_an_existing_anchor() -> None:
    source = _daily_frame()
    cutoff = sorted(source["session_date"].unique())[-11]
    past = source[source["session_date"] <= cutoff]
    past_result = build_m7_daily_features(past, dependency_hashes=DEPENDENCIES)
    full_result = build_m7_daily_features(source, dependency_hashes=DEPENDENCIES)
    columns = [
        "symbol",
        "session_date",
        "return_20_percentile",
        "distance_recent_high_20",
        "trend_slope_change_5",
        "rolling_beta_spy_60",
        "rolling_correlation_spy_60",
    ]

    expected = past_result.frame[past_result.frame["session_date"] == cutoff][columns]
    actual = full_result.frame[full_result.frame["session_date"] == cutoff][columns]

    pdt.assert_frame_equal(
        expected.reset_index(drop=True),
        actual.reset_index(drop=True),
    )


def test_future_available_source_and_unversioned_metadata_are_rejected() -> None:
    source = _daily_frame()
    source.loc[0, "source_available_at"] = pd.Timestamp("2030-01-01", tz="UTC")
    with pytest.raises(M7FeatureInputError, match="future_source"):
        build_m7_daily_features(source, dependency_hashes=DEPENDENCIES)

    source = _daily_frame()
    source.loc[0, "sector_mapping_version"] = None
    with pytest.raises(M7FeatureInputError, match="classification_missing"):
        build_m7_daily_features(source, dependency_hashes=DEPENDENCIES)


def test_dependency_hashes_and_natural_keys_are_strict() -> None:
    source = _daily_frame()
    with pytest.raises(M7FeatureInputError, match="dependency_hash_invalid"):
        build_m7_daily_features(
            source,
            dependency_hashes={**DEPENDENCIES, "calendar": "bad"},
        )
    with pytest.raises(M7FeatureInputError, match="duplicate_sample_key"):
        build_m7_daily_features(
            pd.concat([source, source.iloc[[0]]], ignore_index=True),
            dependency_hashes=DEPENDENCIES,
        )


def test_intraday_extension_is_separate_and_requires_complete_provenance() -> None:
    source = _daily_frame(session_count=70)
    latest = source[source["session_date"] == source["session_date"].max()][
        ["symbol", "session_date"]
    ].copy()
    latest["opening_gap"] = 0.001
    latest["first_hour_return"] = 0.002
    latest["first_hour_range"] = 0.01
    latest["first_hour_relative_volume"] = 1.2
    latest["completed_session_intraday_volatility"] = 0.015
    latest["available"] = True
    latest["source_complete"] = True
    latest["provider_provenance"] = "alpaca-sip-v1"

    eligible = build_m7_daily_features(
        source,
        dependency_hashes=DEPENDENCIES,
        intraday_extension=latest,
    )
    assert eligible.group_status["intraday_optional"].status == "eligible"
    assert eligible.frame["first_hour_return"].notna().sum() == 100

    latest.loc[latest.index[:6], "source_complete"] = False
    ineligible = build_m7_daily_features(
        source,
        dependency_hashes=DEPENDENCIES,
        intraday_extension=latest,
    )
    assert ineligible.group_status["intraday_optional"].status == "not_eligible"
    assert ineligible.group_status["intraday_optional"].reason == (
        "intraday_coverage_below_0_95"
    )
    assert "first_hour_return" not in ineligible.frame


def test_registered_m7_dag_is_deterministic_and_horizon_invariant() -> None:
    dag = m7_feature_dag()

    assert dag.topological_order == (
        "raw_daily",
        "core_v2",
        "cross_sectional",
        "structure_context",
        "full_daily",
        "intraday_optional",
        "full_daily_intraday_optional",
    )
    assert (
        len(
            dag.input_hash(
                "full_daily",
                dependency_hashes={
                    "cross_sectional": "5" * 64,
                    "structure_context": "6" * 64,
                },
            )
        )
        == 64
    )
