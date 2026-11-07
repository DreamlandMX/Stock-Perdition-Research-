"""Governed M7 feature-group ablation evidence."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np


M7DailyAblationFeatureSet = Literal[
    "core_v2",
    "core_v2_cross_sectional",
    "core_v2_structure_context",
    "full_daily",
]
_DAILY_FEATURE_SETS: tuple[M7DailyAblationFeatureSet, ...] = (
    "core_v2",
    "core_v2_cross_sectional",
    "core_v2_structure_context",
    "full_daily",
)


@dataclass(frozen=True, slots=True)
class M7FeatureAblationObservation:
    feature_set: str
    fold_id: str
    horizon: int
    symbol_equal_brier: float
    adaptive_macro_ece: float
    sample_count: int
    deterministic: bool


def build_m7_feature_ablation_report(
    observations: Sequence[M7FeatureAblationObservation],
    *,
    intraday_status: str,
    intraday_reason: str | None,
) -> dict[str, object]:
    """Aggregate the frozen four-fold, three-horizon diagnostic matrix."""

    supplied = tuple(observations)
    grouped = {
        feature_set: tuple(
            row for row in supplied if row.feature_set == feature_set
        )
        for feature_set in _DAILY_FEATURE_SETS
    }
    if any(
        len(rows) != 12
        or len({row.fold_id for row in rows}) != 4
        or {row.horizon for row in rows} != {1, 5, 20}
        or any(
            not np.isfinite(row.symbol_equal_brier)
            or not np.isfinite(row.adaptive_macro_ece)
            or row.sample_count < 1
            for row in rows
        )
        for rows in grouped.values()
    ):
        raise ValueError("m7_feature_ablation_evidence_incomplete")
    core_brier = float(
        np.mean([row.symbol_equal_brier for row in grouped["core_v2"]])
    )
    rows: list[dict[str, object]] = []
    for feature_set in _DAILY_FEATURE_SETS:
        evidence = grouped[feature_set]
        mean_brier = float(np.mean([row.symbol_equal_brier for row in evidence]))
        rows.append(
            {
                "feature_set": feature_set,
                "status": "completed",
                "fold_count": 4,
                "horizons": [1, 5, 20],
                "sample_count": sum(row.sample_count for row in evidence),
                "mean_symbol_equal_brier": mean_brier,
                "delta_symbol_equal_brier_vs_core": round(
                    mean_brier - core_brier,
                    12,
                ),
                "mean_adaptive_macro_ece": float(
                    np.mean([row.adaptive_macro_ece for row in evidence])
                ),
                "determinism_passed": all(row.deterministic for row in evidence),
                "evidence_role": "diagnostic_only",
            }
        )
    if intraday_status == "eligible":
        intraday = tuple(
            row
            for row in supplied
            if row.feature_set == "full_daily_intraday_optional"
        )
        if (
            len(intraday) != 12
            or len({row.fold_id for row in intraday}) != 4
            or {row.horizon for row in intraday} != {1, 5, 20}
        ):
            raise ValueError("m7_feature_ablation_intraday_evidence_incomplete")
        mean_brier = float(np.mean([row.symbol_equal_brier for row in intraday]))
        rows.append(
            {
                "feature_set": "full_daily_intraday_optional",
                "status": "completed",
                "fold_count": 4,
                "horizons": [1, 5, 20],
                "sample_count": sum(row.sample_count for row in intraday),
                "mean_symbol_equal_brier": mean_brier,
                "delta_symbol_equal_brier_vs_core": round(
                    mean_brier - core_brier,
                    12,
                ),
                "mean_adaptive_macro_ece": float(
                    np.mean([row.adaptive_macro_ece for row in intraday])
                ),
                "determinism_passed": all(row.deterministic for row in intraday),
                "evidence_role": "diagnostic_only",
            }
        )
    else:
        rows.append(
            {
                "feature_set": "full_daily_intraday_optional",
                "status": "not_eligible",
                "reason": intraday_reason or "intraday_feature_group_ineligible",
            }
        )
    return {
        "status": "completed",
        "primary_metric": "mean_symbol_equal_multiclass_brier",
        "selection_effect": "none_diagnostic_only",
        "rows": rows,
    }


__all__ = [
    "M7FeatureAblationObservation",
    "build_m7_feature_ablation_report",
]
