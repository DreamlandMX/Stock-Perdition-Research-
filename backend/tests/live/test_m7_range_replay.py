from __future__ import annotations

import hashlib
import os

from lightgbm import LGBMClassifier
import numpy as np
import pandas as pd

from stock_prediction.live.m7_range_executor import (
    _curves,
    fresh_process_survival_replay,
)
from stock_prediction.live.training import _FEATURES as LEGACY_FEATURES


def test_range_selected_configuration_replays_in_a_fresh_process() -> None:
    generator = np.random.default_rng(17)
    person_period = pd.DataFrame(
        {
            name: generator.normal(size=80)
            for name in LEGACY_FEATURES
        }
    )
    person_period["elapsed_day"] = np.tile(np.arange(1, 5), 20)
    person_period["event"] = (
        person_period[LEGACY_FEATURES[0]] + person_period[LEGACY_FEATURES[1]] > 0
    ).astype(int)
    test = pd.DataFrame(
        {name: generator.normal(size=12) for name in LEGACY_FEATURES}
    )
    feature_names = [*LEGACY_FEATURES, "elapsed_day"]
    configuration = {
        "n_estimators": 5,
        "learning_rate": 0.04,
        "num_leaves": 7,
        "min_child_samples": 2,
        "subsample": 0.85,
        "colsample_bytree": 0.85,
        "reg_lambda": 1.0,
        "max_bin": 31,
        "n_jobs": 1,
        "random_state": 17,
        "deterministic": True,
        "force_col_wise": True,
    }
    reference = LGBMClassifier(
        objective="binary",
        **configuration,
        verbosity=-1,
    )
    reference.fit(person_period[feature_names], person_period["event"])

    replay = fresh_process_survival_replay(
        person_period=person_period,
        test=test,
        feature_names=feature_names,
        configuration=configuration,
        timeout_seconds=30.0,
    )

    assert replay.worker_pid != os.getpid()
    assert replay.model_sha256 == hashlib.sha256(
        reference.booster_.model_to_string().encode("utf-8")
    ).hexdigest()
    assert np.allclose(
        replay.test_probabilities,
        _curves(reference, test, feature_names),
        rtol=0.0,
        atol=1e-7,
    )
