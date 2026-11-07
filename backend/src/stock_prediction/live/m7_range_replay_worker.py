"""Fresh-process worker for one selected M7 range configuration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from lightgbm import LGBMClassifier
import numpy as np
import pandas as pd

from stock_prediction.live.training import _cumulative_exit_probability
from stock_prediction.modeling.report import file_sha256, write_immutable_json


def _curves(
    model: LGBMClassifier,
    frame: pd.DataFrame,
    feature_names: list[str],
) -> np.ndarray:
    return np.column_stack(
        [
            _cumulative_exit_probability(model, frame, day, feature_names)
            for day in (5, 10, 20)
        ]
    )


def _run(request_path: Path) -> int:
    root = request_path.resolve().parent
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("schema_version") != "m7-range-fresh-replay-request-v1":
        raise RuntimeError("m7_range_replay_request_schema_invalid")
    train_path = root / "person-period.parquet"
    test_path = root / "test.parquet"
    if (
        request.get("person_period_sha256") != file_sha256(train_path)
        or request.get("test_sha256") != file_sha256(test_path)
    ):
        raise RuntimeError("m7_range_replay_input_hash_invalid")
    feature_names = request.get("feature_names")
    configuration = request.get("configuration")
    if (
        not isinstance(feature_names, list)
        or not feature_names
        or not all(isinstance(value, str) and value for value in feature_names)
        or not isinstance(configuration, dict)
    ):
        raise RuntimeError("m7_range_replay_request_invalid")
    person_period = pd.read_parquet(train_path)
    test = pd.read_parquet(test_path)
    required_train = {*feature_names, "event"}
    if required_train - set(person_period.columns) or set(feature_names[:-1]) - set(
        test.columns
    ):
        raise RuntimeError("m7_range_replay_columns_invalid")
    model = LGBMClassifier(objective="binary", **configuration, verbosity=-1)
    model.fit(person_period[feature_names], person_period["event"])
    probabilities = _curves(model, test, feature_names)
    if (
        probabilities.shape != (len(test), 3)
        or not np.all(np.isfinite(probabilities))
        or np.any(probabilities < 0.0)
        or np.any(probabilities > 1.0)
    ):
        raise RuntimeError("m7_range_replay_probability_invalid")
    staging = root / "replay-probabilities.staging"
    target = root / "replay-probabilities.npz"
    with staging.open("xb") as stream:
        np.savez_compressed(stream, probabilities=probabilities)
    staging.replace(target)
    write_immutable_json(
        root / "response.json",
        {
            "schema_version": "m7-range-fresh-replay-response-v1",
            "worker_pid": os.getpid(),
            "model_sha256": hashlib.sha256(
                model.booster_.model_to_string().encode("utf-8")
            ).hexdigest(),
            "probabilities_sha256": file_sha256(target),
            "row_count": len(test),
            "column_count": 3,
        },
        pretty=False,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    arguments = parser.parse_args()
    try:
        return _run(arguments.request)
    except Exception as exc:  # pragma: no cover - parent validates terminal output
        print(f"{type(exc).__name__}:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess
    raise SystemExit(main())
