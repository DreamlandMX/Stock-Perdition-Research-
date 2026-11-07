from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from stock_prediction.live.config import LivePaths
from stock_prediction.live.m7_blind import M7BlindWindowLedger, register_m7_blind_ledger
from stock_prediction.live.m7_shadow_inference import M7ShadowInferenceRunner
from stock_prediction.live.promotion_windows import (
    PromotionPackageBinding,
    PromotionWindowRegistry,
    build_seed100_promotion_window,
)
from stock_prediction.universe.seed import SEED_UNIVERSE


def _sessions() -> tuple[date, ...]:
    return tuple(value.date() for value in pd.bdate_range("2026-08-25", periods=120))


def _register_direction_ledger(paths: LivePaths) -> tuple[object, tuple[date, ...]]:
    sessions = _sessions()
    package = PromotionPackageBinding(
        package_sha256="a" * 64,
        universe_version="m1-seed-2026-07-11-v1",
        frozen_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
    )
    definition = build_seed100_promotion_window(package, list(sessions))
    registration = PromotionWindowRegistry(paths).register(definition)
    scheduled = sessions[1:]
    ledger = register_m7_blind_ledger(
        paths,
        window_id=definition.window_id,
        bundle_id="direction",
        package_sha256=package.package_sha256,
        incumbent_id="incumbent-v1",
        scheduled_sessions=scheduled,
        registration_path=registration,
    )
    return ledger, scheduled


class _DirectionEnsemble:
    def predict(self, _frame: pd.DataFrame, *, horizon: int) -> np.ndarray:
        row = np.asarray([0.2, 0.3, 0.5], dtype=np.float64)
        return np.tile(row + np.asarray([horizon, 0, -horizon]) * 0.0001, (100, 1))


class _InvalidDirectionEnsemble:
    def predict(self, _frame: pd.DataFrame, *, horizon: int) -> np.ndarray:
        del horizon
        return np.tile(np.asarray([0.2, np.nan, 0.8]), (100, 1))


def test_due_shadow_run_persists_prediction_explanation_manifest_and_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    ledger, scheduled = _register_direction_ledger(paths)
    anchor = scheduled[0]
    frame = pd.DataFrame(
        {
            "symbol": list(SEED_UNIVERSE),
            "session_date": [anchor] * 100,
            "eligible": [True] * 100,
        }
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference.prepare_m7_feature_data",
        lambda *_args, **_kwargs: SimpleNamespace(
            frame=frame,
            source_artifact=SimpleNamespace(
                as_of=datetime.combine(anchor, time(20), tzinfo=UTC),
                input_hash="b" * 64,
            ),
        ),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference._resolve_package",
        lambda *_args, **_kwargs: SimpleNamespace(
            package_directory=tmp_path / "package",
            manifest=SimpleNamespace(
                package_id="m7-direction-shadow",
                package_sha256="a" * 64,
                bundle_id="direction",
            ),
        ),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference.load_m7_direction_package",
        lambda _path: _DirectionEnsemble(),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference.build_m7_direction_shadow_explanations",
        lambda *_args, **_kwargs: {
            "schema_version": "m7-shadow-explanations-v1",
            "prediction_count": 300,
            "non_causal_caveat": True,
        },
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference._load_incumbent_predictions",
        lambda *_args, **_kwargs: SimpleNamespace(
            incumbent_id="incumbent-v1",
            snapshot_id="incumbent-snapshot-v1",
            manifest_hash="d" * 64,
            predictions={
                (symbol, horizon): SimpleNamespace(
                    direction=(0.25, 0.35, 0.40),
                    range_exit=None,
                )
                for symbol in SEED_UNIVERSE
                for horizon in (1, 5, 20)
            },
        ),
    )
    generated_at = datetime.combine(anchor, time(21), tzinfo=UTC)

    results = M7ShadowInferenceRunner(paths).run_due(
        generated_at=generated_at,
        incumbent_snapshot_path=tmp_path / "snapshot",
    )

    assert len(results) == 1
    result = results[0]
    assert result.status == "available"
    assert result.bundle_id == "direction"
    assert result.anchor_session == anchor
    assert len(result.prediction_artifact_hash) == 64
    table = pq.read_table(result.prediction_path).to_pandas()
    assert len(table) == 300
    assert set(table["horizon"]) == {1, 5, 20}
    assert table["formal_eligible"].eq(False).all()
    assert table["evidence_scope"].eq("shadow_forward").all()
    assert table["incumbent_id"].eq("incumbent-v1").all()
    assert table["incumbent_available"].all()
    assert table["incumbent_probability_up"].eq(0.40).all()
    assert result.explanation_path.is_file()
    assert result.manifest_path.is_file()
    status = ledger.status()
    assert status.available_anchor_count == 1
    assert status.missing_anchor_count == 0


def test_due_shadow_run_records_missing_when_current_seed100_features_are_not_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    ledger, scheduled = _register_direction_ledger(paths)
    anchor = scheduled[0]
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference._resolve_package",
        lambda *_args, **_kwargs: SimpleNamespace(
            package_directory=tmp_path / "package",
            manifest=SimpleNamespace(
                package_id="m7-direction-shadow",
                package_sha256="a" * 64,
                bundle_id="direction",
            ),
        ),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference.prepare_m7_feature_data",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("m7_source_feature_artifact_unavailable")
        ),
    )

    results = M7ShadowInferenceRunner(paths).run_due(
        generated_at=datetime.combine(anchor, time(21), tzinfo=UTC)
    )

    assert len(results) == 1
    assert results[0].status == "missing"
    assert results[0].reason == "m7_source_feature_artifact_unavailable"
    assert ledger.status().missing_anchor_count == 1


def test_direction_shadow_rejects_non_finite_probability_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    ledger, scheduled = _register_direction_ledger(paths)
    anchor = scheduled[0]
    frame = pd.DataFrame(
        {
            "symbol": list(SEED_UNIVERSE),
            "session_date": [anchor] * 100,
            "eligible": [True] * 100,
        }
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference.prepare_m7_feature_data",
        lambda *_args, **_kwargs: SimpleNamespace(
            frame=frame,
            source_artifact=SimpleNamespace(
                as_of=datetime.combine(anchor, time(20), tzinfo=UTC),
                input_hash="b" * 64,
            ),
        ),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference._resolve_package",
        lambda *_args, **_kwargs: SimpleNamespace(
            package_directory=tmp_path / "package",
            manifest=SimpleNamespace(
                package_id="m7-direction-shadow",
                package_sha256="a" * 64,
                bundle_id="direction",
            ),
        ),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference.load_m7_direction_package",
        lambda _path: _InvalidDirectionEnsemble(),
    )

    results = M7ShadowInferenceRunner(paths).run_due(
        generated_at=datetime.combine(anchor, time(21), tzinfo=UTC)
    )

    assert results[0].status == "missing"
    assert results[0].reason == "m7_shadow_direction_output_invalid"
    assert ledger.status().missing_anchor_count == 1


def test_existing_shadow_artifact_is_recovered_only_when_hashes_are_intact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    ledger, scheduled = _register_direction_ledger(paths)
    anchor = scheduled[0]
    frame = pd.DataFrame(
        {
            "symbol": list(SEED_UNIVERSE),
            "session_date": [anchor] * 100,
            "eligible": [True] * 100,
        }
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference.prepare_m7_feature_data",
        lambda *_args, **_kwargs: SimpleNamespace(
            frame=frame,
            source_artifact=SimpleNamespace(
                as_of=datetime.combine(anchor, time(20), tzinfo=UTC),
                input_hash="b" * 64,
            ),
        ),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference._resolve_package",
        lambda *_args, **_kwargs: SimpleNamespace(
            package_directory=tmp_path / "package",
            manifest=SimpleNamespace(
                package_id="m7-direction-shadow",
                package_sha256="a" * 64,
                bundle_id="direction",
            ),
        ),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference.load_m7_direction_package",
        lambda _path: _DirectionEnsemble(),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference.build_m7_direction_shadow_explanations",
        lambda *_args, **_kwargs: {
            "schema_version": "m7-shadow-explanations-v1",
            "prediction_count": 300,
            "non_causal_caveat": True,
        },
    )
    generated_at = datetime.combine(anchor, time(21), tzinfo=UTC)
    first = M7ShadowInferenceRunner(paths).run_due(generated_at=generated_at)[0]
    (ledger.directory / "predictions" / f"{anchor.isoformat()}.json").unlink()

    recovered = M7ShadowInferenceRunner(paths)._run_one(
        M7BlindWindowLedger(ledger.directory),
        anchor=anchor,
        generated_at=generated_at,
    )

    assert recovered.prediction_artifact_hash == first.prediction_artifact_hash
    assert ledger.status().available_anchor_count == 1

    (ledger.directory / "predictions" / f"{anchor.isoformat()}.json").unlink()
    first.prediction_path.write_bytes(first.prediction_path.read_bytes() + b"tamper")
    with pytest.raises(RuntimeError, match="m7_shadow_artifact_manifest_invalid"):
        M7ShadowInferenceRunner(paths)._run_one(
            M7BlindWindowLedger(ledger.directory),
            anchor=anchor,
            generated_at=generated_at,
        )


def test_mature_direction_labels_are_hash_bound_to_prediction_sample_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = LivePaths.from_workspace(tmp_path)
    ledger, scheduled = _register_direction_ledger(paths)
    anchor = scheduled[0]
    prediction_frame = pd.DataFrame(
        {
            "symbol": list(SEED_UNIVERSE),
            "session_date": [anchor] * 100,
            "eligible": [True] * 100,
        }
    )
    package = SimpleNamespace(
        package_directory=tmp_path / "package",
        manifest=SimpleNamespace(
            package_id="m7-direction-shadow",
            package_sha256="a" * 64,
            bundle_id="direction",
        ),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference.prepare_m7_feature_data",
        lambda *_args, **_kwargs: SimpleNamespace(
            frame=prediction_frame,
            source_artifact=SimpleNamespace(
                as_of=datetime.combine(anchor, time(20), tzinfo=UTC),
                input_hash="b" * 64,
            ),
        ),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference._resolve_package",
        lambda *_args, **_kwargs: package,
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference.load_m7_direction_package",
        lambda _path: _DirectionEnsemble(),
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference.build_m7_direction_shadow_explanations",
        lambda *_args, **_kwargs: {
            "schema_version": "m7-shadow-explanations-v1",
            "prediction_count": 300,
            "non_causal_caveat": True,
        },
    )
    runner = M7ShadowInferenceRunner(paths)
    runner.run_due(generated_at=datetime.combine(anchor, time(21), tzinfo=UTC))
    maturity = scheduled[20]
    labels = pd.DataFrame(
        {
            "symbol": list(SEED_UNIVERSE),
            "session_date": [anchor] * 100,
            "eligible": [True] * 100,
            "direction_1": [1.0] * 100,
            "direction_5": [0.0] * 100,
            "direction_20": [-1.0] * 100,
            "return_fwd_1": [0.01] * 100,
            "return_fwd_5": [0.02] * 100,
            "return_fwd_20": [-0.03] * 100,
        }
    )
    monkeypatch.setattr(
        "stock_prediction.live.m7_shadow_inference.prepare_m7_feature_data",
        lambda *_args, **_kwargs: SimpleNamespace(
            frame=labels,
            source_artifact=SimpleNamespace(
                as_of=datetime.combine(maturity, time(20), tzinfo=UTC),
                input_hash="c" * 64,
            ),
        ),
    )

    results = runner.run_mature_labels(
        generated_at=datetime.combine(maturity, time(21), tzinfo=UTC)
    )

    assert len(results) == 1
    result = results[0]
    assert result.status == "available"
    assert result.anchor_session == anchor
    assert len(result.label_artifact_hash) == 64
    assert len(result.sample_keys_hash) == 64
    label_frame = pq.read_table(result.label_path).to_pandas()
    assert len(label_frame) == 300
    assert set(label_frame["observed_label"]) == {"down", "flat", "up"}
    assert set(label_frame["realized_return"]) == {0.01, 0.02, -0.03}
    assert ledger.status().mature_label_count == 1
