"""Point-in-time seed-100 feature extensions for M7 targeted models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Literal

import numpy as np
import pandas as pd

from stock_prediction.features.dag import FeatureDag, build_m7_feature_dag
from stock_prediction.modeling.m7_recipe import M7_FEATURE_SET_VERSION
from stock_prediction.universe.seed import SEED_ETFS, SEED_UNIVERSE_VERSION


M7_CROSS_SECTION_MIN_COVERAGE = 0.95
M7_INTRADAY_MIN_COVERAGE = 0.95
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

M7_CORE_V2_COLUMNS = (
    "return_1",
    "return_5",
    "return_20",
    "return_60",
    "volatility_5",
    "volatility_20",
    "volatility_60",
    "atr_pct_20",
    "channel_width_20",
    "channel_position_20",
    "trend_slope_20",
    "ma_distance_20",
    "ma_distance_50",
    "ma_distance_200",
    "gap_1",
    "volume_z_20",
    "volume_price_anomaly_20",
    "market_breadth_up_lag1",
    "relative_spy_20",
    "relative_spy_60",
    "rolling_beta_spy_60_lag1",
    "rolling_corr_spy_60_lag1",
    "is_etf",
)

M7_CROSS_SECTION_COLUMNS = (
    "return_1_percentile",
    "return_5_percentile",
    "return_20_percentile",
    "volatility_20_percentile",
    "atr_pct_20_percentile",
    "volume_z_20_percentile",
    "trend_slope_20_percentile",
    "ma_distance_20_percentile",
    "ma_distance_50_percentile",
    "relative_strength_spy_20",
    "relative_strength_spy_20_percentile",
    "relative_strength_sector_20",
    "relative_strength_sector_20_percentile",
)

M7_STRUCTURE_CONTEXT_COLUMNS = (
    "distance_recent_high_20",
    "distance_recent_low_20",
    "channel_width_atr_ratio",
    "overnight_gap_atr",
    "trend_slope_change_5",
    "volatility_compression_5_20",
    "volatility_expansion_20_60",
    "drawdown_252",
    "recovery_position_252",
    "spy_state",
    "qqq_state",
    "market_breadth_20",
    "cross_section_dispersion_20",
    "rolling_beta_spy_60",
    "rolling_correlation_spy_60",
)

M7_INTRADAY_COLUMNS = (
    "opening_gap",
    "first_hour_return",
    "first_hour_range",
    "first_hour_relative_volume",
    "completed_session_intraday_volatility",
)

_REQUIRED_INPUT_COLUMNS = {
    "symbol",
    "session_date",
    "source_available_at",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "eligible",
    "asset_type",
    "asset_type_version",
    "sector",
    "sector_mapping_version",
    "dataset_revision",
    "universe_version",
}


class M7FeatureInputError(ValueError):
    """M7 feature source data is incomplete, ambiguous, or not point-in-time."""


@dataclass(frozen=True, slots=True)
class M7FeatureGroupStatus:
    status: Literal["eligible", "not_eligible"]
    reason: str | None
    columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class M7FeatureBuildResult:
    feature_set_version: str
    dataset_revision: str
    universe_version: str
    input_hash: str
    dependency_hashes: Mapping[str, str]
    group_status: Mapping[str, M7FeatureGroupStatus]
    frame: pd.DataFrame

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "dependency_hashes",
            MappingProxyType(dict(self.dependency_hashes)),
        )
        object.__setattr__(
            self,
            "group_status",
            MappingProxyType(dict(self.group_status)),
        )


def m7_feature_dag() -> FeatureDag:
    return build_m7_feature_dag()


def build_m7_daily_features(
    source: pd.DataFrame,
    *,
    dependency_hashes: Mapping[str, str],
    intraday_extension: pd.DataFrame | None = None,
) -> M7FeatureBuildResult:
    """Build v5 features using only same-anchor and past-visible observations."""

    _validate_dependency_hashes(dependency_hashes)
    _validate_source(source)
    frame = source.copy()
    frame["session_date"] = pd.to_datetime(frame["session_date"]).dt.date
    frame["source_available_at"] = pd.to_datetime(
        frame["source_available_at"], utc=True
    )
    frame = frame.sort_values(["symbol", "session_date"], kind="stable").reset_index(
        drop=True
    )
    if any(
        available.date() > session
        for available, session in zip(
            frame["source_available_at"],
            frame["session_date"],
            strict=True,
        )
    ):
        raise M7FeatureInputError("m7_feature_future_source")

    frame = _add_core_and_structure_features(frame)
    frame = _add_same_anchor_features(frame)
    frame = _add_market_context(frame)
    frame, intraday_status = _add_intraday_extension(frame, intraday_extension)
    frame["feature_set_version"] = M7_FEATURE_SET_VERSION
    frame = frame.sort_values(["session_date", "symbol"], kind="stable").reset_index(
        drop=True
    )
    dataset_revision = str(frame["dataset_revision"].iloc[0])
    universe_version = str(frame["universe_version"].iloc[0])
    input_hash = _feature_input_hash(frame, dependency_hashes)
    statuses = {
        "core_v2": M7FeatureGroupStatus("eligible", None, M7_CORE_V2_COLUMNS),
        "cross_sectional": M7FeatureGroupStatus(
            "eligible", None, M7_CROSS_SECTION_COLUMNS
        ),
        "structure_context": M7FeatureGroupStatus(
            "eligible", None, M7_STRUCTURE_CONTEXT_COLUMNS
        ),
        "full_daily": M7FeatureGroupStatus(
            "eligible",
            None,
            (
                *M7_CORE_V2_COLUMNS,
                *M7_CROSS_SECTION_COLUMNS,
                *M7_STRUCTURE_CONTEXT_COLUMNS,
            ),
        ),
        "intraday_optional": intraday_status,
    }
    return M7FeatureBuildResult(
        feature_set_version=M7_FEATURE_SET_VERSION,
        dataset_revision=dataset_revision,
        universe_version=universe_version,
        input_hash=input_hash,
        dependency_hashes=dict(sorted(dependency_hashes.items())),
        group_status=statuses,
        frame=frame,
    )


def _validate_dependency_hashes(dependency_hashes: Mapping[str, str]) -> None:
    required = {"adjusted_bars", "calendar", "classification", "universe"}
    if set(dependency_hashes) != required or any(
        _SHA256.fullmatch(value) is None for value in dependency_hashes.values()
    ):
        raise M7FeatureInputError("m7_feature_dependency_hash_invalid")


def _validate_source(source: pd.DataFrame) -> None:
    missing = _REQUIRED_INPUT_COLUMNS - set(source.columns)
    if missing:
        raise M7FeatureInputError("m7_feature_source_columns_missing")
    if source.empty:
        raise M7FeatureInputError("m7_feature_source_empty")
    if source[["symbol", "session_date"]].duplicated().any():
        raise M7FeatureInputError("m7_feature_duplicate_sample_key")
    classification = source[
        ["asset_type", "asset_type_version", "sector", "sector_mapping_version"]
    ]
    if (
        classification.isna().any().any()
        or (classification.astype(str).apply(lambda values: values.str.strip().eq("")))
        .any()
        .any()
    ):
        raise M7FeatureInputError("m7_feature_classification_missing")
    for name in (
        "dataset_revision",
        "universe_version",
        "asset_type_version",
        "sector_mapping_version",
    ):
        if source[name].nunique(dropna=False) != 1:
            raise M7FeatureInputError(f"m7_feature_mixed_{name}")
    if set(source["universe_version"]) != {SEED_UNIVERSE_VERSION}:
        raise M7FeatureInputError("m7_feature_universe_version_invalid")
    if not source["symbol"].isin(set(_seed_symbols())).all():
        raise M7FeatureInputError("m7_feature_symbol_outside_seed100")


def _seed_symbols() -> tuple[str, ...]:
    from stock_prediction.universe.seed import SEED_UNIVERSE

    return SEED_UNIVERSE


def _add_core_and_structure_features(frame: pd.DataFrame) -> pd.DataFrame:
    groups: list[pd.DataFrame] = []
    for _, source in frame.groupby("symbol", sort=False):
        values = source.copy().sort_values("session_date")
        close = values["close"].astype(float)
        high = values["high"].astype(float)
        low = values["low"].astype(float)
        volume = values["volume"].astype(float)
        previous_close = close.shift(1)
        true_range = pd.concat(
            [
                high - low,
                (high - previous_close).abs(),
                (low - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        values["return_1"] = close.pct_change(1, fill_method=None)
        values["return_5"] = close.pct_change(5, fill_method=None)
        values["return_20"] = close.pct_change(20, fill_method=None)
        values["return_60"] = close.pct_change(60, fill_method=None)
        values["volatility_5"] = values["return_1"].rolling(5).std()
        values["volatility_20"] = values["return_1"].rolling(20).std()
        values["volatility_60"] = values["return_1"].rolling(60).std()
        values["atr_20"] = true_range.rolling(20).mean()
        values["atr_pct_20"] = values["atr_20"] / close
        values["channel_high_20"] = high.shift(1).rolling(20).max()
        values["channel_low_20"] = low.shift(1).rolling(20).min()
        channel_range = values["channel_high_20"] - values["channel_low_20"]
        values["channel_width_20"] = (
            values["channel_high_20"] / values["channel_low_20"] - 1.0
        )
        values["channel_position_20"] = (
            close - values["channel_low_20"]
        ) / channel_range.replace(0.0, np.nan)
        values["trend_slope_20"] = close / close.shift(20) - 1.0
        values["ma_distance_20"] = close / close.rolling(20).mean() - 1.0
        values["ma_distance_50"] = close / close.rolling(50).mean() - 1.0
        values["ma_distance_200"] = close / close.rolling(200).mean() - 1.0
        values["gap_1"] = values["open"].astype(float) / previous_close - 1.0
        volume_mean = volume.shift(1).rolling(20).mean()
        volume_std = volume.shift(1).rolling(20).std().replace(0.0, np.nan)
        values["volume_z_20"] = (volume - volume_mean) / volume_std
        values["volume_price_anomaly_20"] = values["volume_z_20"] * values["return_1"]
        values["is_etf"] = values["symbol"].isin(SEED_ETFS).astype(float)

        values["distance_recent_high_20"] = close / values["channel_high_20"] - 1.0
        values["distance_recent_low_20"] = close / values["channel_low_20"] - 1.0
        values["channel_width_atr_ratio"] = channel_range / values["atr_20"].replace(
            0.0, np.nan
        )
        values["overnight_gap_atr"] = (
            values["open"].astype(float) - previous_close
        ) / values["atr_20"].replace(0.0, np.nan)
        values["trend_slope_change_5"] = values["trend_slope_20"] - values[
            "trend_slope_20"
        ].shift(5)
        values["volatility_compression_5_20"] = values["volatility_5"] / values[
            "volatility_20"
        ].replace(0.0, np.nan)
        values["volatility_expansion_20_60"] = values["volatility_20"] / values[
            "volatility_60"
        ].replace(0.0, np.nan)
        trailing_high = close.rolling(252, min_periods=20).max()
        trailing_low = close.rolling(252, min_periods=20).min()
        values["drawdown_252"] = close / trailing_high - 1.0
        values["recovery_position_252"] = (close - trailing_low) / (
            trailing_high - trailing_low
        ).replace(0.0, np.nan)
        values["regime"] = _deterministic_state(values)
        groups.append(values)
    return pd.concat(groups, ignore_index=True)


def _deterministic_state(frame: pd.DataFrame) -> np.ndarray:
    enough = frame[["atr_pct_20", "trend_slope_20"]].notna().all(axis=1)
    threshold = 0.75 * frame["atr_pct_20"]
    return np.select(
        [
            ~enough,
            frame["trend_slope_20"] > threshold,
            frame["trend_slope_20"] < -threshold,
        ],
        ["unavailable", "uptrend", "downtrend"],
        default="range",
    )


def _add_same_anchor_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["cross_section_eligible_count"] = 0
    result["cross_section_coverage"] = 0.0
    result["cross_section_missing_reason"] = pd.Series(
        None, index=result.index, dtype="object"
    )
    for column in M7_CROSS_SECTION_COLUMNS:
        result[column] = np.nan
    rank_sources = {
        "return_1_percentile": "return_1",
        "return_5_percentile": "return_5",
        "return_20_percentile": "return_20",
        "volatility_20_percentile": "volatility_20",
        "atr_pct_20_percentile": "atr_pct_20",
        "volume_z_20_percentile": "volume_z_20",
        "trend_slope_20_percentile": "trend_slope_20",
        "ma_distance_20_percentile": "ma_distance_20",
        "ma_distance_50_percentile": "ma_distance_50",
    }
    for _, indexes in result.groupby("session_date", sort=True).groups.items():
        group_index = list(indexes)
        eligible_index = [
            index for index in group_index if bool(result.at[index, "eligible"])
        ]
        denominator = len(eligible_index)
        coverage = denominator / len(_seed_symbols())
        result.loc[group_index, "cross_section_eligible_count"] = denominator
        result.loc[group_index, "cross_section_coverage"] = coverage
        if coverage < M7_CROSS_SECTION_MIN_COVERAGE:
            result.loc[group_index, "cross_section_missing_reason"] = (
                "thin_cross_section"
            )
            continue
        for output, source in rank_sources.items():
            valid = result.loc[eligible_index, source].dropna()
            result.loc[valid.index, output] = valid.rank(method="average", pct=True)
        spy = result.loc[
            [
                index
                for index in eligible_index
                if result.at[index, "symbol"] == "US.SPY"
            ],
            "return_20",
        ]
        if not spy.empty and pd.notna(spy.iloc[0]):
            result.loc[eligible_index, "relative_strength_spy_20"] = result.loc[
                eligible_index, "return_20"
            ] - float(spy.iloc[0])
        sector_medians = (
            result.loc[eligible_index]
            .groupby("sector")["return_20"]
            .transform("median")
        )
        result.loc[eligible_index, "relative_strength_sector_20"] = (
            result.loc[eligible_index, "return_20"] - sector_medians
        )
        for source, output in (
            (
                "relative_strength_spy_20",
                "relative_strength_spy_20_percentile",
            ),
            (
                "relative_strength_sector_20",
                "relative_strength_sector_20_percentile",
            ),
        ):
            valid = result.loc[eligible_index, source].dropna()
            result.loc[valid.index, output] = valid.rank(method="average", pct=True)
        incomplete = (
            result.loc[group_index, M7_CROSS_SECTION_COLUMNS].isna().any(axis=1)
        )
        result.loc[incomplete[incomplete].index, "cross_section_missing_reason"] = (
            "source_feature_missing"
        )
    return result


def _add_market_context(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy().sort_values(["symbol", "session_date"])
    proxy = result[result["symbol"].eq("US.SPY")][
        ["session_date", "return_1", "return_20", "return_60", "regime"]
    ].rename(
        columns={
            "return_1": "spy_return_1",
            "return_20": "spy_return_20",
            "return_60": "spy_return_60",
            "regime": "spy_state",
        }
    )
    qqq = result[result["symbol"].eq("US.QQQ")][["session_date", "regime"]].rename(
        columns={"regime": "qqq_state"}
    )
    result = result.merge(proxy, on="session_date", how="left", validate="many_to_one")
    result = result.merge(qqq, on="session_date", how="left", validate="many_to_one")
    result["relative_spy_20"] = result["return_20"] - result["spy_return_20"]
    result["relative_spy_60"] = result["return_60"] - result["spy_return_60"]
    breadth = result.groupby("session_date").apply(
        lambda group: float(
            (group.loc[group["eligible"].astype(bool), "return_20"] > 0).mean()
        ),
        include_groups=False,
    )
    dispersion = result.groupby("session_date").apply(
        lambda group: float(
            group.loc[group["eligible"].astype(bool), "return_20"].std(ddof=0)
        ),
        include_groups=False,
    )
    result["market_breadth_20"] = result["session_date"].map(breadth)
    result["cross_section_dispersion_20"] = result["session_date"].map(dispersion)
    daily_breadth = (
        result.groupby("session_date")["return_1"]
        .apply(lambda values: float((values > 0).mean()))
        .sort_index()
        .shift(1)
    )
    result["market_breadth_up_lag1"] = result["session_date"].map(daily_breadth)
    groups: list[pd.DataFrame] = []
    for _, source in result.groupby("symbol", sort=False):
        values = source.copy().sort_values("session_date")
        stock_return = values["return_1"]
        spy_return = values["spy_return_1"]
        covariance = stock_return.rolling(60, min_periods=40).cov(spy_return)
        variance = spy_return.rolling(60, min_periods=40).var().replace(0.0, np.nan)
        values["rolling_beta_spy_60"] = covariance / variance
        values["rolling_correlation_spy_60"] = stock_return.rolling(
            60, min_periods=40
        ).corr(spy_return)
        values["rolling_beta_spy_60_lag1"] = values["rolling_beta_spy_60"].shift(1)
        values["rolling_corr_spy_60_lag1"] = values["rolling_correlation_spy_60"].shift(
            1
        )
        groups.append(values)
    return pd.concat(groups, ignore_index=True)


def _add_intraday_extension(
    frame: pd.DataFrame,
    intraday: pd.DataFrame | None,
) -> tuple[pd.DataFrame, M7FeatureGroupStatus]:
    if intraday is None:
        return frame, M7FeatureGroupStatus(
            "not_eligible",
            "intraday_not_supplied",
            M7_INTRADAY_COLUMNS,
        )
    required = {
        "symbol",
        "session_date",
        *M7_INTRADAY_COLUMNS,
        "available",
        "source_complete",
        "provider_provenance",
    }
    if required - set(intraday.columns):
        return frame, M7FeatureGroupStatus(
            "not_eligible",
            "intraday_schema_incomplete",
            M7_INTRADAY_COLUMNS,
        )
    extension = intraday.copy()
    extension["session_date"] = pd.to_datetime(extension["session_date"]).dt.date
    if extension[["symbol", "session_date"]].duplicated().any():
        raise M7FeatureInputError("m7_intraday_duplicate_sample_key")
    valid = (
        extension["available"].astype(bool)
        & extension["source_complete"].astype(bool)
        & extension["provider_provenance"].notna()
        & extension[list(M7_INTRADAY_COLUMNS)].notna().all(axis=1)
    )
    if not extension["symbol"].isin(set(_seed_symbols())).all():
        raise M7FeatureInputError("m7_intraday_symbol_outside_seed100")
    coverage = extension.assign(_valid=valid).groupby("session_date")[
        "_valid"
    ].sum().astype(float) / len(_seed_symbols())
    if coverage.empty or float(coverage.min()) < M7_INTRADAY_MIN_COVERAGE:
        return frame, M7FeatureGroupStatus(
            "not_eligible",
            "intraday_coverage_below_0_95",
            M7_INTRADAY_COLUMNS,
        )
    selected = extension.loc[valid, ["symbol", "session_date", *M7_INTRADAY_COLUMNS]]
    output = frame.merge(
        selected,
        on=["symbol", "session_date"],
        how="left",
        validate="one_to_one",
    )
    return output, M7FeatureGroupStatus("eligible", None, M7_INTRADAY_COLUMNS)


def _feature_input_hash(
    frame: pd.DataFrame,
    dependency_hashes: Mapping[str, str],
) -> str:
    identity_columns = [
        "symbol",
        "session_date",
        "source_available_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "eligible",
        "asset_type",
        "sector",
        "dataset_revision",
        "universe_version",
        "asset_type_version",
        "sector_mapping_version",
    ]
    row_hash = pd.util.hash_pandas_object(
        frame[identity_columns],
        index=False,
        categorize=True,
    ).to_numpy(dtype=np.uint64)
    digest = hashlib.sha256(row_hash.tobytes())
    digest.update(
        json.dumps(
            dict(sorted(dependency_hashes.items())),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    digest.update(M7_FEATURE_SET_VERSION.encode("utf-8"))
    return digest.hexdigest()


__all__ = [
    "M7_CORE_V2_COLUMNS",
    "M7_CROSS_SECTION_COLUMNS",
    "M7_FEATURE_SET_VERSION",
    "M7_INTRADAY_COLUMNS",
    "M7_STRUCTURE_CONTEXT_COLUMNS",
    "M7FeatureBuildResult",
    "M7FeatureGroupStatus",
    "M7FeatureInputError",
    "build_m7_daily_features",
    "m7_feature_dag",
]
