"""Reproduce exploratory date-block uncertainty from the frozen development ledger."""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from stock_prediction.modeling.m7_metrics import date_block_bootstrap_delta_brier


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    directory = root / "var/research-audits/direction-baseline-20260915-v1"
    report_path = directory / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    path = directory / report["predictions_file"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != report["predictions_sha256"]:
        raise ValueError("baseline_ledger_hash_mismatch")
    frame = pq.read_table(path).to_pandas()
    if (
        len(frame) != report["prediction_rows"]
        or frame.duplicated(["symbol", "session_date", "horizon"]).any()
    ):
        raise ValueError("baseline_ledger_rows_invalid")
    results = []
    classes = ("down", "neutral", "up")
    for horizon in (1, 5, 20):
        part = frame[frame.horizon.eq(horizon)]
        labels = [classes[int(y) + 1] for y in part.actual]
        keys = list(zip(part.symbol, part.session_date, part.horizon, strict=True))
        candidate = part[["calibrated_logistic_" + c for c in classes]].to_numpy()
        for baseline in ("frequency", "logistic"):
            probabilities = part[[baseline + "_" + c for c in classes]].to_numpy()
            for length in (20, 40, 60):
                result = date_block_bootstrap_delta_brier(
                    sample_keys=keys,
                    labels=labels,
                    candidate_probabilities=candidate,
                    incumbent_probabilities=probabilities,
                    classes=classes,
                    n_draws=2000,
                    block_length=length,
                    seed=17,
                )
                # All symbols have the same number of dates here, so weights agree.
                observed = np.eye(3)[part.actual.to_numpy() + 1]
                paired = (
                    np.square(candidate - observed).sum(axis=1)
                    - np.square(probabilities - observed).sum(axis=1)
                ).mean()
                assert np.isclose(paired, result.point_estimate)
                results.append(
                    dict(horizon=horizon, baseline=baseline, **asdict(result))
                )
    output = dict(
        source_report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
        predictions_sha256=digest,
        method="stock_prediction.modeling.m7_metrics.date_block_bootstrap_delta_brier",
        scope="exploratory_development_not_selection_adjusted_or_prospective",
        notes="Negative delta favors candidate; an upper bound below zero is conditional evidence only. Blocks may cross adjacent walk-forward model boundaries.",
        promotion_window_consumed=False,
        results=results,
    )
    target = directory / "block-uncertainty.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, indent=2)
    print(json.dumps(output))


if __name__ == "__main__":
    main()
