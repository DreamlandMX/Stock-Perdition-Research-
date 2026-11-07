from __future__ import annotations

from io import StringIO
import json
import math
from pathlib import Path
import shutil

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stock_prediction.cli import main
import stock_prediction.modeling.pipeline as m4_pipeline
from stock_prediction.modeling.report import (
    canonical_json_bytes,
    deterministic_hash,
    file_sha256,
)


def _run_demo(work_root: Path) -> dict[str, object]:
    stdout = StringIO()
    stderr = StringIO()

    code = main(
        ["m4-offline-demo", "--work-root", str(work_root)],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0, stderr.getvalue()
    assert stderr.getvalue() == ""
    return json.loads(stdout.getvalue())


@pytest.fixture(scope="module")
def replayable_m4_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work_root = tmp_path_factory.mktemp("m4-replay") / "baseline"
    _run_demo(work_root)
    return work_root


def _refresh_replay_index_entry(
    work_root: Path, artifact_name: str, *, update_size: bool = True
) -> None:
    artifact = work_root / artifact_name
    index_path = work_root / "evidence-replay-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entry = next(
        item for item in index["artifacts"] if item["relative_path"] == artifact_name
    )
    entry["sha256"] = file_sha256(artifact)
    if update_size:
        entry["size_bytes"] = artifact.stat().st_size
    entry["content_hash"] = deterministic_hash(
        m4_pipeline._artifact_semantic_payload(artifact)
    )
    payload = {
        name: value for name, value in index.items() if name != "index_content_hash"
    }
    index["index_content_hash"] = deterministic_hash(payload)
    index_path.write_bytes(canonical_json_bytes(index, pretty=True))


def _rewrite_parquet_and_refresh_replay_index(
    work_root: Path, artifact_name: str, rows: list[dict[str, object]]
) -> None:
    artifact = work_root / artifact_name
    pq.write_table(
        pa.Table.from_pylist(rows),
        artifact,
        compression="NONE",
        data_page_version="1.0",
        version="2.6",
        write_statistics=True,
    )
    _refresh_replay_index_entry(work_root, artifact_name)


def _rewrite_json_and_refresh_replay_index(
    work_root: Path, artifact_name: str, value: object
) -> None:
    artifact = work_root / artifact_name
    artifact.write_bytes(canonical_json_bytes(value, pretty=True))
    _refresh_replay_index_entry(work_root, artifact_name)


def _core_pin(work_root: Path) -> str:
    core = json.loads(
        (work_root / "deterministic-core.json").read_text(encoding="utf-8")
    )
    return str(core["deterministic_core_hash"])


def _refresh_local_deterministic_core(
    work_root: Path, artifact_names: tuple[str, ...]
) -> str:
    core_path = work_root / "deterministic-core.json"
    core = json.loads(core_path.read_text(encoding="utf-8"))
    for artifact_name in artifact_names:
        core["artifact_hashes"][artifact_name] = file_sha256(
            work_root / artifact_name
        )
    payload = {
        name: value for name, value in core.items() if name != "deterministic_core_hash"
    }
    core["deterministic_core_hash"] = deterministic_hash(payload)
    core_path.write_bytes(canonical_json_bytes(core, pretty=True))
    return str(core["deterministic_core_hash"])


def test_m4_demo_registers_every_trial_and_never_claims_unearned_champion(
    tmp_path: Path,
) -> None:
    work_root = tmp_path / "m4-demo"

    result = _run_demo(work_root)

    assert result["status"] == "ok"
    assert result["evidence_scope"] == "synthetic_offline"
    assert result["economic_evidence_scope"] == "research_only"
    assert result["research_only"] is True
    assert result["predictive_lift_claimed"] is False
    assert result["outer_fold_count"] >= 3
    assert result["registered_trial_count"] == (
        result["succeeded_trial_count"]
        + result["failed_trial_count"]
        + result["aborted_trial_count"]
        + result["rejected_trial_count"]
    )
    assert result["failed_trial_count"] >= 1
    assert result["promotion_status"] in {
        "champion_candidate",
        "no_champion_candidate",
        "prototype",
    }
    assert result["promotion_status"] != "champion_candidate"
    assert result["failed_gates"]
    assert result["main_cost_per_side_bps"] > 0
    assert result["production_window_consumed_count"] == 0
    assert result["outer_window_consumed_count"] == result["outer_fold_count"]
    assert result["model_families"] == [
        "channel_atr_rule",
        "historical_frequency",
        "lightgbm",
        "logistic_regression",
    ]
    assert result["quantiles"] == [0.1, 0.25, 0.5, 0.75, 0.9]
    assert result["survival_horizons"] == [5, 20]
    assert result["deterministic_core_hash"] == _core_pin(work_root)
    pinned_audit = m4_pipeline.replay_m4_evidence(
        work_root,
        expected_deterministic_core_hash=str(result["deterministic_core_hash"]),
    )
    assert pinned_audit["external_integrity"]["caller_pin_verified"] is True
    assert pinned_audit["external_integrity"]["core_artifact_map_verified"] is True

    report_path = work_root / "m4-acceptance-report.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert len(report["outer_folds"]) == result["outer_fold_count"]
    assert report["walk_forward_geometry"] == {
        "embargo_sessions": 20,
        "minimum_outer_folds": 3,
        "test_months": 6,
        "train_years": 5,
        "validation_months": 12,
    }
    assert report["shared_outer_folds"] is True
    assert report["failed_trials"]
    assert report["calibration_bins"]
    assert set(report["quantile_metrics"]) >= {"coverage", "mean_width"}
    assert set(report["survival_metrics"]) == {"5", "20"}
    assert report["confidence_strata"]["high_enabled"] is False
    assert report["top_contributions"]
    assert report["similar_cases"]["eligible_count"] >= 0
    assert report["long_cash"]["net_return"] < report["long_cash"]["gross_return"]
    assert report["long_cash"]["cost_sensitivity"]
    assert report["long_cash"]["capacity_sensitivity"]
    assert report["worst_fold"]
    assert report["worst_month"]
    assert report["dsr"]["status"].startswith("insufficient_")
    assert report["pbo"]["status"].startswith("insufficient_")
    assert report["window_consumption"]["production_consumed"] == 0
    assert report["runtime_observations_file"] == "runtime-observations.json"
    assert "runtime_seconds" not in report
    assert "peak_rss_bytes" not in report

    artifact_paths = [work_root / item["relative_path"] for item in report["artifacts"]]
    assert artifact_paths
    assert all(path.is_file() for path in artifact_paths)
    assert any(path.suffix == ".json" for path in artifact_paths)
    assert any(path.suffix == ".parquet" for path in artifact_paths)

    assert report["task_horizon_matrix"] == [
        {"horizons": [1, 5, 20], "task": "forecast_direction"},
        {"horizons": [1, 5, 20], "task": "breakout"},
    ]
    expected_task_keys = {
        f"{task}:h{horizon}"
        for task in ("forecast_direction", "breakout")
        for horizon in (1, 5, 20)
    }
    assert all(
        set(fold["task_horizon_metrics"]) == expected_task_keys
        for fold in report["outer_folds"]
    )

    quantile_rows = pq.read_table(
        work_root / "quantile-predictions.parquet"
    ).to_pylist()
    assert quantile_rows
    assert all(
        row["q10"] <= row["q25"] <= row["q50"] <= row["q75"] <= row["q90"]
        for row in quantile_rows
    )
    assert report["quantile_metrics"]["noncrossing_assertion_passed"] is True

    for fold in report["outer_folds"]:
        for horizon in ("5", "20"):
            survival = fold["survival_metrics"][horizon]
            assert survival["event_day_source"] == (
                "range_exit_at_calendar_session_position"
            )
            assert survival["censoring_day_source"] == (
                "label_end_at_calendar_session_position"
            )
            assert survival["timestamp_fidelity_passed"] is True
            assert (
                survival["event_count"] + survival["non_event_count"]
                == (survival["observation_count"])
            )

    economics = report["long_cash"]["outer_fold_economics"]
    assert len(economics) == result["outer_fold_count"]
    for economic, fold in zip(economics, report["outer_folds"], strict=True):
        assert economic["fold_id"] == fold["fold_id"]
        assert economic["window_id"] == fold["test_window_id"]
        assert economic["signal_source"] == "calibrated_outer_test_predictions"
        assert economic["model_evidence_hash"]
        assert economic["calibration_evidence_hash"]
        assert economic["actual_artifact_identity_location"] == (
            "runtime-observations.json#outer_fold_economic_model_artifact_identities"
        )
        assert economic["dataset_input_hash"] == result["dataset_input_hash"]
        assert economic["calendar_hash"] == report["hashes"]["calendar_hash"]
        assert economic["trade_count"] > 0
    runtime = json.loads(
        (work_root / "runtime-observations.json").read_text(encoding="utf-8")
    )
    runtime_identities = runtime["outer_fold_economic_model_artifact_identities"]
    assert [item["fold_id"] for item in runtime_identities] == [
        fold["fold_id"] for fold in report["outer_folds"]
    ]
    assert all(
        item["model_artifact_hash"] and item["calibration_artifact_hash"]
        for item in runtime_identities
    )

    monthly = report["monthly_metrics"]
    assert len(monthly) >= 2
    assert report["worst_month"] == max(
        monthly, key=lambda item: (item["brier_score"], item["month"])
    )

    audit = report["audit"]
    assert audit["source"] == "semantic_replay_from_locally_indexed_artifacts"
    assert audit["external_tamper_resistance"] == (
        "not_verified_without_caller_supplied_pin"
    )
    assert (
        audit["ledger_replay"]["registered_trial_count"]
        == result["registered_trial_count"]
    )
    assert (
        audit["ledger_replay"]["terminal_trial_count"]
        == result["registered_trial_count"]
    )
    assert all(
        item["consumption_count"] == 1 and item["scope"] == "synthetic_offline"
        for item in audit["outer_window_consumption"]
    )
    assert audit["production_window_consumed_count"] == 0
    assert all(audit["binding_checks"].values())
    assert audit["binding_hashes"]["transformer_input_hash"]
    assert audit["binding_hashes"]["feature_lineage_hash"]
    assert audit["binding_hashes"]["label_evidence_hash"]
    assert audit["artifact_replay"]["all_content_hashes_match"] is True
    assert audit["economic_replay"]["all_outer_folds_reconstructed"] is True
    assert audit["survival_replay"]["all_observations_bound"] is True
    assert audit["shared_trial_matrix"]
    assert all(
        "candidate_index" in item and "frozen_parameters" in item
        for item in audit["shared_trial_matrix"]
    )

    label_rows = pq.read_table(work_root / "label-evidence.parquet").to_pylist()
    assert all(row["sample_key"] for row in label_rows)
    assert any(row["range_exit"] and row["range_exit_at"] for row in label_rows)
    assert any(row["breakout"] != "none" and row["breakout_event_at"] for row in label_rows)

    survival_rows = pq.read_table(
        work_root / "survival-observations.parquet"
    ).to_pylist()
    person_period_rows = pq.read_table(
        work_root / "survival-person-period.parquet"
    ).to_pylist()
    assert survival_rows and person_period_rows
    assert all(row["sample_key"] and row["label_evidence_hash"] for row in survival_rows)
    assert all(row["sample_key"] and row["observation_id"] for row in person_period_rows)

    economic_rows = pq.read_table(
        work_root / "economic-predictions.parquet"
    ).to_pylist()
    execution_rows = pq.read_table(work_root / "execution-bars.parquet").to_pylist()
    assert economic_rows and execution_rows
    assert all(
        row["sample_key"]
        and row["model_evidence_hash"]
        and row["calibration_evidence_hash"]
        and 0.0 <= row["raw_probability_down"] <= 1.0
        and 0.0 <= row["raw_probability_flat"] <= 1.0
        and 0.0 <= row["raw_probability_up"] <= 1.0
        and row["raw_logit_down"]
        == pytest.approx(math.log(max(row["raw_probability_down"], 1e-9)))
        and row["raw_logit_flat"]
        == pytest.approx(math.log(max(row["raw_probability_flat"], 1e-9)))
        and row["raw_logit_up"]
        == pytest.approx(math.log(max(row["raw_probability_up"], 1e-9)))
        and row["signal_decision"] in {"eligible", "not_rebalance_anchor"}
        for row in economic_rows
    )
    assert all(row["execution_bar_key"] and row["verified"] for row in execution_rows)

    cost_sensitivity = report["long_cash"]["cost_sensitivity"]
    assert all(item["fold_count"] == result["outer_fold_count"] for item in cost_sensitivity)
    assert all(len(item["outer_folds"]) == result["outer_fold_count"] for item in cost_sensitivity)
    assert all(
        item["net_portfolio_return"]
        == pytest.approx(
            __import__("math").prod(
                1.0 + fold["net_portfolio_return"] for fold in item["outer_folds"]
            )
            - 1.0
        )
        for item in cost_sensitivity
    )
    assert any(
        item["net_portfolio_return"]
        != pytest.approx(item["outer_folds"][-1]["net_portfolio_return"])
        for item in cost_sensitivity
    )
    capacity_sensitivity = report["long_cash"]["capacity_sensitivity"]
    assert all(item["fold_count"] == result["outer_fold_count"] for item in capacity_sensitivity)
    assert all(
        item["capacity_exceeded_count"]
        == sum(fold["capacity_exceeded_count"] for fold in item["outer_folds"])
        for item in capacity_sensitivity
    )


def test_m4_demo_is_deterministic_across_fresh_roots(tmp_path: Path) -> None:
    first = _run_demo(tmp_path / "first")
    second = _run_demo(tmp_path / "second")

    assert first["deterministic_core_hash"] == second["deterministic_core_hash"]
    assert (
        first["deterministic_artifact_hashes"]
        == second["deterministic_artifact_hashes"]
    )
    assert "runtime-observations.json" not in first["deterministic_artifact_hashes"]
    assert first["dataset_input_hash"] == second["dataset_input_hash"]
    assert first["split_manifest_hash"] == second["split_manifest_hash"]


def test_m4_command_failure_is_sanitized_and_does_not_reuse_work_root(
    tmp_path: Path,
) -> None:
    work_root = tmp_path / "not-empty"
    work_root.mkdir()
    (work_root / "existing.txt").write_text("occupied", encoding="utf-8")
    stdout = StringIO()
    stderr = StringIO()

    code = main(
        ["m4-offline-demo", "--work-root", str(work_root)],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "error_code=m4_command_failed\n"


def test_m4_quantile_adjustment_rearranges_crossing_rows() -> None:
    assert hasattr(m4_pipeline, "adjust_quantile_predictions")
    adjusted = m4_pipeline.adjust_quantile_predictions(
        [[0.4, 0.2, 0.3, 0.1, 0.5], [-0.2, -0.1, 0.0, 0.1, 0.2]]
    )

    assert adjusted.crossing_row_count == 1
    assert adjusted.rows[0] == (0.1, 0.2, 0.3, 0.4, 0.5)
    assert all(
        q10 <= q25 <= q50 <= q75 <= q90 for q10, q25, q50, q75, q90 in adjusted.rows
    )


def test_m4_replay_rejects_corrupted_persisted_artifact(
    replayable_m4_root: Path, tmp_path: Path
) -> None:
    work_root = tmp_path / "corrupt-artifact"
    shutil.copytree(replayable_m4_root, work_root)
    artifact = work_root / "label-evidence.parquet"
    payload = bytearray(artifact.read_bytes())
    payload[len(payload) // 2] ^= 0x01
    artifact.write_bytes(payload)

    with pytest.raises(RuntimeError, match="deterministic core artifact mismatch"):
        m4_pipeline.replay_m4_evidence(
            work_root,
            expected_deterministic_core_hash=_core_pin(replayable_m4_root),
        )


def test_m4_external_pin_rejects_refreshed_index_and_local_core(
    replayable_m4_root: Path, tmp_path: Path
) -> None:
    work_root = tmp_path / "refreshed-local-trust-root"
    shutil.copytree(replayable_m4_root, work_root)
    expected_pin = _core_pin(work_root)
    similar_path = work_root / "similar-cases.json"
    similar = json.loads(similar_path.read_text(encoding="utf-8"))
    similar["eligible_count"] = int(similar["eligible_count"]) + 1
    _rewrite_json_and_refresh_replay_index(work_root, "similar-cases.json", similar)
    refreshed_pin = _refresh_local_deterministic_core(
        work_root,
        ("similar-cases.json", "evidence-replay-index.json"),
    )
    assert refreshed_pin != expected_pin

    with pytest.raises(RuntimeError, match="caller-supplied deterministic core pin"):
        m4_pipeline.replay_m4_evidence(
            work_root,
            expected_deterministic_core_hash=expected_pin,
        )


def test_m4_replay_rejects_reindexed_execution_bar_identity_corruption(
    replayable_m4_root: Path, tmp_path: Path
) -> None:
    work_root = tmp_path / "reindexed-execution-key"
    shutil.copytree(replayable_m4_root, work_root)
    rows = pq.read_table(work_root / "execution-bars.parquet").to_pylist()
    rows[0]["execution_bar_key"] = "US.AAPL|2099-01-01"
    _rewrite_parquet_and_refresh_replay_index(
        work_root, "execution-bars.parquet", rows
    )

    with pytest.raises(RuntimeError, match="execution bar identity mismatch"):
        m4_pipeline._semantic_replay_m4_evidence(work_root)


def test_m4_replay_rejects_stale_index_size_after_reindex(
    replayable_m4_root: Path, tmp_path: Path
) -> None:
    work_root = tmp_path / "stale-size"
    shutil.copytree(replayable_m4_root, work_root)
    artifact = work_root / "similar-cases.json"
    artifact.write_bytes(artifact.read_bytes() + b"\n")
    _refresh_replay_index_entry(
        work_root, "similar-cases.json", update_size=False
    )

    with pytest.raises(RuntimeError, match="artifact size mismatch"):
        m4_pipeline._semantic_replay_m4_evidence(work_root)


def test_m4_replay_rejects_reindexed_label_semantic_corruption(
    replayable_m4_root: Path, tmp_path: Path
) -> None:
    work_root = tmp_path / "reindexed-label"
    shutil.copytree(replayable_m4_root, work_root)
    rows = pq.read_table(work_root / "label-evidence.parquet").to_pylist()
    row = next(item for item in rows if item["range_exit_at"] is not None)
    row["range_exit_at"] = None
    _rewrite_parquet_and_refresh_replay_index(
        work_root, "label-evidence.parquet", rows
    )

    with pytest.raises(RuntimeError, match="artifact binding mismatch"):
        m4_pipeline._semantic_replay_m4_evidence(work_root)


def test_m4_replay_rejects_reindexed_survival_timestamp_corruption(
    replayable_m4_root: Path, tmp_path: Path
) -> None:
    work_root = tmp_path / "reindexed-survival"
    shutil.copytree(replayable_m4_root, work_root)
    rows = pq.read_table(work_root / "survival-observations.parquet").to_pylist()
    row = next(item for item in rows if item["event_day"] not in {None, 1})
    row["event_day"] = int(row["event_day"]) - 1
    _rewrite_parquet_and_refresh_replay_index(
        work_root, "survival-observations.parquet", rows
    )

    with pytest.raises(RuntimeError, match="survival observation timestamp binding"):
        m4_pipeline._semantic_replay_m4_evidence(work_root)


def test_m4_replay_rejects_reindexed_calibrated_probability_corruption(
    replayable_m4_root: Path, tmp_path: Path
) -> None:
    work_root = tmp_path / "reindexed-probability"
    shutil.copytree(replayable_m4_root, work_root)
    rows = pq.read_table(work_root / "economic-predictions.parquet").to_pylist()
    row = rows[0]
    shift = min(0.001, float(row["probability_up"]) / 2.0)
    row["probability_down"] = float(row["probability_down"]) + shift
    row["probability_up"] = float(row["probability_up"]) - shift
    row["signal_score"] = row["probability_up"]
    _rewrite_parquet_and_refresh_replay_index(
        work_root, "economic-predictions.parquet", rows
    )

    with pytest.raises(RuntimeError, match="calibrated probability reconstruction"):
        m4_pipeline._semantic_replay_m4_evidence(work_root)


def test_m4_replay_reconstructs_calibration_before_signal_and_backtest(
    replayable_m4_root: Path, tmp_path: Path
) -> None:
    work_root = tmp_path / "coordinated-calibrated-probability"
    shutil.copytree(replayable_m4_root, work_root)
    predictions = pq.read_table(
        work_root / "economic-predictions.parquet"
    ).to_pylist()
    spec_path = work_root / "economic-replay-spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    fold_spec = spec["folds"][0]
    fold_rows = [
        row for row in predictions if row["fold_id"] == fold_spec["fold_id"]
    ]
    row = fold_rows[0]
    shift = min(0.001, float(row["probability_up"]) / 2.0)
    row["probability_down"] = float(row["probability_down"]) + shift
    row["probability_up"] = float(row["probability_up"]) - shift
    row["signal_score"] = row["probability_up"]
    raw_logits = [
        [
            item["raw_logit_down"],
            item["raw_logit_flat"],
            item["raw_logit_up"],
        ]
        for item in fold_rows
    ]
    calibrated = [
        [
            item["probability_down"],
            item["probability_flat"],
            item["probability_up"],
        ]
        for item in fold_rows
    ]
    calibration_hash = deterministic_hash(
        {
            **fold_spec["calibration_evidence"],
            "raw_logits": raw_logits,
            "calibrated_probabilities": calibrated,
        }
    )
    fold_spec["calibration_evidence_hash"] = calibration_hash
    for item in fold_rows:
        item["calibration_evidence_hash"] = calibration_hash
    _rewrite_json_and_refresh_replay_index(
        work_root, "economic-replay-spec.json", spec
    )
    _rewrite_parquet_and_refresh_replay_index(
        work_root, "economic-predictions.parquet", predictions
    )
    replayed_backtest = m4_pipeline._run_long_cash_backtest(work_root)
    _rewrite_json_and_refresh_replay_index(
        work_root, "long-cash-backtest.json", replayed_backtest
    )

    with pytest.raises(RuntimeError, match="calibrated probability reconstruction"):
        m4_pipeline._semantic_replay_m4_evidence(work_root)


def test_m4_external_pin_rejects_coordinated_temperature_rewrite(
    replayable_m4_root: Path, tmp_path: Path
) -> None:
    work_root = tmp_path / "coordinated-temperature-rewrite"
    shutil.copytree(replayable_m4_root, work_root)
    expected_pin = _core_pin(work_root)
    predictions = pq.read_table(
        work_root / "economic-predictions.parquet"
    ).to_pylist()
    spec_path = work_root / "economic-replay-spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    fold_spec = spec["folds"][0]
    fold_rows = [
        row for row in predictions if row["fold_id"] == fold_spec["fold_id"]
    ]
    calibration_evidence = fold_spec["calibration_evidence"]
    calibration_evidence["temperature"] = (
        float(calibration_evidence["temperature"]) * 1.1
    )
    raw_logits = [
        [
            item["raw_logit_down"],
            item["raw_logit_flat"],
            item["raw_logit_up"],
        ]
        for item in fold_rows
    ]
    calibrated = m4_pipeline.apply_temperature_scaling(
        raw_logits,
        temperature=float(calibration_evidence["temperature"]),
        class_order=tuple(calibration_evidence["class_order"]),
    ).tolist()
    for item, probabilities in zip(fold_rows, calibrated, strict=True):
        item["probability_down"] = probabilities[0]
        item["probability_flat"] = probabilities[1]
        item["probability_up"] = probabilities[2]
        item["signal_score"] = probabilities[2]
    calibration_hash = deterministic_hash(
        {
            **calibration_evidence,
            "raw_logits": raw_logits,
            "calibrated_probabilities": calibrated,
        }
    )
    fold_spec["calibration_evidence_hash"] = calibration_hash
    for item in fold_rows:
        item["calibration_evidence_hash"] = calibration_hash
    _rewrite_json_and_refresh_replay_index(
        work_root, "economic-replay-spec.json", spec
    )
    _rewrite_parquet_and_refresh_replay_index(
        work_root, "economic-predictions.parquet", predictions
    )
    replayed_backtest = m4_pipeline._run_long_cash_backtest(work_root)
    _rewrite_json_and_refresh_replay_index(
        work_root, "long-cash-backtest.json", replayed_backtest
    )
    _refresh_local_deterministic_core(
        work_root,
        (
            "economic-replay-spec.json",
            "economic-predictions.parquet",
            "long-cash-backtest.json",
            "evidence-replay-index.json",
        ),
    )

    with pytest.raises(RuntimeError, match="caller-supplied deterministic core pin"):
        m4_pipeline.replay_m4_evidence(
            work_root,
            expected_deterministic_core_hash=expected_pin,
        )


def test_m4_replay_rejects_reindexed_duplicate_prediction(
    replayable_m4_root: Path, tmp_path: Path
) -> None:
    work_root = tmp_path / "duplicate-prediction"
    shutil.copytree(replayable_m4_root, work_root)
    rows = pq.read_table(work_root / "economic-predictions.parquet").to_pylist()
    rows.append(dict(rows[0]))
    _rewrite_parquet_and_refresh_replay_index(
        work_root, "economic-predictions.parquet", rows
    )

    with pytest.raises(RuntimeError, match="prediction multiplicity"):
        m4_pipeline._semantic_replay_m4_evidence(work_root)


def test_m4_replay_rejects_reindexed_shared_but_incomplete_trial_matrix(
    replayable_m4_root: Path, tmp_path: Path
) -> None:
    work_root = tmp_path / "incomplete-shared-matrix"
    shutil.copytree(replayable_m4_root, work_root)
    ledger = work_root / "trial-ledger.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    removed_trial_ids = {
        str(record["record_id"])
        for record in records
        if record["record_type"] == "trial_registered"
        and record["payload"]["model_version"]
        == "synthetic-forecast_direction_h1_historical-v1"
    }
    assert len(removed_trial_ids) >= 3
    ledger.write_text(
        "\n".join(
            line
            for line, record in zip(lines, records, strict=True)
            if record["record_id"] not in removed_trial_ids
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _refresh_replay_index_entry(work_root, "trial-ledger.jsonl")

    with pytest.raises(RuntimeError, match="trial matrix differs from registered plan"):
        m4_pipeline._semantic_replay_m4_evidence(work_root)


def test_m4_replay_rejects_self_consistent_but_false_training_input_hash(
    replayable_m4_root: Path, tmp_path: Path
) -> None:
    work_root = tmp_path / "false-training-hash"
    shutil.copytree(replayable_m4_root, work_root)
    spec_path = work_root / "economic-replay-spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    predictions = pq.read_table(
        work_root / "economic-predictions.parquet"
    ).to_pylist()
    fold = spec["folds"][0]
    fold["model_evidence"]["training_input_hash"] = "f" * 64
    probabilities = [
        [
            row["probability_down"],
            row["probability_flat"],
            row["probability_up"],
        ]
        for row in predictions
        if row["fold_id"] == fold["fold_id"]
    ]
    false_model_hash = deterministic_hash(
        {
            **fold["model_evidence"],
            "calibrated_probabilities": probabilities,
        }
    )
    fold["model_evidence_hash"] = false_model_hash
    for row in predictions:
        if row["fold_id"] == fold["fold_id"]:
            row["model_evidence_hash"] = false_model_hash
    _rewrite_json_and_refresh_replay_index(
        work_root, "economic-replay-spec.json", spec
    )
    _rewrite_parquet_and_refresh_replay_index(
        work_root, "economic-predictions.parquet", predictions
    )
    replayed_backtest = m4_pipeline._run_long_cash_backtest(work_root)
    _rewrite_json_and_refresh_replay_index(
        work_root, "long-cash-backtest.json", replayed_backtest
    )

    with pytest.raises(RuntimeError, match="training input binding"):
        m4_pipeline._semantic_replay_m4_evidence(work_root)


@pytest.mark.parametrize(
    ("record_type", "error_match"),
    [
        ("trial_finished", "terminal multiplicity"),
        ("window_consumed", "consumption multiplicity"),
    ],
)
def test_m4_replay_rejects_duplicate_ledger_records(
    replayable_m4_root: Path,
    tmp_path: Path,
    record_type: str,
    error_match: str,
) -> None:
    work_root = tmp_path / record_type
    shutil.copytree(replayable_m4_root, work_root)
    ledger = work_root / "trial-ledger.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    duplicate = next(line for line in lines if json.loads(line)["record_type"] == record_type)
    with ledger.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(duplicate + "\n")

    with pytest.raises(RuntimeError, match=error_match):
        m4_pipeline._semantic_replay_m4_evidence(work_root)
