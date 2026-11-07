from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from stock_prediction.live.baseline_audit import compare_direction_baseline
from stock_prediction.live.training import _FEATURES


def test_baseline_records_probabilities_and_rejects_time_overlap():
    rng = np.random.default_rng(17)
    frame = pd.DataFrame(rng.normal(size=(90, len(_FEATURES))), columns=_FEATURES)
    frame["symbol"] = "US.TEST"
    frame["session_date"] = [date(2020, 1, 1) + timedelta(days=i) for i in range(90)]
    frame["direction_1"] = np.tile([-1, 0, 1], 30)
    train, calibration, test = frame.iloc[:30], frame.iloc[30:60], frame.iloc[60:]
    metrics, rows = compare_direction_baseline(train, calibration, test, 1)
    assert metrics["sample_count"] == len(test)
    np.testing.assert_array_equal(rows.actual, test.direction_1)
    for model in ("frequency", "logistic", "calibrated_logistic"):
        probabilities = rows[
            [model + "_" + label for label in ("down", "neutral", "up")]
        ]
        np.testing.assert_allclose(probabilities.sum(axis=1), 1)
        actual = np.eye(3)[rows.actual.to_numpy() + 1]
        assert metrics[model + "_brier"] == pytest.approx(
            np.square(probabilities.to_numpy() - actual).sum(axis=1).mean()
        )
    with pytest.raises(ValueError, match="partitions_overlap"):
        compare_direction_baseline(train, calibration, calibration, 1)
