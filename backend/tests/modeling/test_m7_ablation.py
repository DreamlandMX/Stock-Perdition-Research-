from __future__ import annotations

from stock_prediction.modeling.m7_ablation import (
    M7FeatureAblationObservation,
    build_m7_feature_ablation_report,
)


def _observation(feature_set: str, fold: int, horizon: int, brier: float):
    return M7FeatureAblationObservation(
        feature_set=feature_set,
        fold_id=f"fold-{fold}",
        horizon=horizon,
        symbol_equal_brier=brier,
        adaptive_macro_ece=0.04,
        sample_count=100,
        deterministic=True,
    )


def test_ablation_report_contains_all_five_registered_rows() -> None:
    observations = tuple(
        _observation(feature_set, fold, horizon, brier)
        for feature_set, brier in (
            ("core_v2", 0.67),
            ("core_v2_cross_sectional", 0.65),
            ("core_v2_structure_context", 0.66),
            ("full_daily", 0.64),
        )
        for fold in range(4)
        for horizon in (1, 5, 20)
    )

    report = build_m7_feature_ablation_report(
        observations,
        intraday_status="not_eligible",
        intraday_reason="intraday_not_supplied",
    )

    assert report["status"] == "completed"
    assert [row["feature_set"] for row in report["rows"]] == [
        "core_v2",
        "core_v2_cross_sectional",
        "core_v2_structure_context",
        "full_daily",
        "full_daily_intraday_optional",
    ]
    assert report["rows"][1]["delta_symbol_equal_brier_vs_core"] == -0.02
    assert report["rows"][4] == {
        "feature_set": "full_daily_intraday_optional",
        "status": "not_eligible",
        "reason": "intraday_not_supplied",
    }


def test_ablation_report_rejects_incomplete_daily_evidence() -> None:
    try:
        build_m7_feature_ablation_report(
            (_observation("core_v2", 1, 1, 0.67),),
            intraday_status="not_eligible",
            intraday_reason="intraday_not_supplied",
        )
    except ValueError as error:
        assert str(error) == "m7_feature_ablation_evidence_incomplete"
    else:
        raise AssertionError("incomplete ablation evidence must fail closed")
