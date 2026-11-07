from __future__ import annotations

import numpy as np
import pandas as pd

from stock_prediction.features.m7 import M7_CORE_V2_COLUMNS
from stock_prediction.modeling.m7_drift import (
    build_m7_drift_reference,
    evaluate_m7_daily_drift,
)


def _frame(seed: int, *, shifted_features: int = 0) -> pd.DataFrame:
    generator = np.random.default_rng(seed)
    rows = 1_000
    values = {
        name: generator.normal(
            loc=2.5 if index < shifted_features else 0.0,
            size=rows,
        )
        for index, name in enumerate(M7_CORE_V2_COLUMNS)
    }
    frame = pd.DataFrame(values)
    frame["eligible"] = True
    frame["regime"] = np.resize(np.asarray(["trend_up", "range"]), rows)
    return frame


def test_m7_daily_drift_uses_frozen_bins_and_exact_twenty_percent_threshold() -> None:
    reference = build_m7_drift_reference(
        _frame(17),
        prediction_values={"direction_h1_up": np.linspace(0.05, 0.95, 1_000)},
        source_identity_hash="c" * 64,
    )

    below = evaluate_m7_daily_drift(
        reference,
        _frame(19, shifted_features=4),
        prediction_values={"direction_h1_up": np.linspace(0.05, 0.95, 1_000)},
        reference_package_sha256="a" * 64,
    )
    warning = evaluate_m7_daily_drift(
        reference,
        _frame(19, shifted_features=5),
        prediction_values={"direction_h1_up": np.linspace(0.55, 0.99, 1_000)},
        reference_package_sha256="a" * 64,
    )

    assert below.core_feature_psi_fraction_above_020 < 0.20
    assert below.feature_warning is False
    assert warning.core_feature_psi_fraction_above_020 >= 0.20
    assert warning.feature_warning is True
    assert warning.prediction_distribution_shifted is True
    assert warning.reference_package_sha256 == "a" * 64
    assert warning.coverage_rate == 1.0
    assert warning.missingness_rate == 0.0
    assert warning.evidence_hash != below.evidence_hash


def test_m7_daily_drift_reports_missingness_coverage_ood_and_regime_mix() -> None:
    reference = build_m7_drift_reference(
        _frame(23),
        prediction_values={"range_exit_by_20": np.linspace(0.1, 0.9, 1_000)},
        source_identity_hash="d" * 64,
    )
    current = _frame(29)
    current.loc[:99, M7_CORE_V2_COLUMNS[0]] = np.nan
    current.loc[:49, "eligible"] = False
    current.loc[100:149, M7_CORE_V2_COLUMNS[1]] = 100.0

    report = evaluate_m7_daily_drift(
        reference,
        current,
        prediction_values={"range_exit_by_20": np.linspace(0.1, 0.9, 1_000)},
        reference_package_sha256="b" * 64,
    )

    assert report.coverage_rate == 0.95
    assert report.missingness_rate > 0.0
    assert report.ood_rate > 0.0
    assert set(report.regime_mix) == {"range", "trend_up"}
    assert set(report.feature_psi) == set(M7_CORE_V2_COLUMNS)
    assert report.evidence_hash == report.recomputed_evidence_hash()
