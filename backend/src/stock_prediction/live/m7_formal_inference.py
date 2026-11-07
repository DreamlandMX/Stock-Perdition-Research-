"""Formal, current-session inference ledger for promoted M7 components."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
import json
import os
from pathlib import Path, PurePosixPath
from typing import Literal, Self
from uuid import uuid4

from filelock import FileLock
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_prediction.live.config import LivePaths
from stock_prediction.live.features import load_latest_feature_artifact
from stock_prediction.live.m7_executor import prepare_m7_feature_data
from stock_prediction.live.m7_integrity import (
    M7IntegrityMonitor,
    M7IntegrityResult,
    M7ServingObservation,
)
from stock_prediction.live.m7_promotion import (
    M7ForecastBundleComponent,
    load_active_m7_forecast_bundle,
)
from stock_prediction.modeling.m7_direction_package import (
    load_m7_direction_package,
)
from stock_prediction.modeling.m7_range_package import load_m7_range_package
from stock_prediction.modeling.m7_package import (
    detect_m7_runtime_compatibility,
    load_m7_package,
)
from stock_prediction.modeling.report import (
    deterministic_hash,
    file_sha256,
    write_immutable_json,
)
from stock_prediction.universe.seed import SEED_UNIVERSE


class M7FormalInferenceError(RuntimeError):
    """A promoted component cannot produce valid current formal evidence."""


class M7FormalBundleBinding(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    forecast_bundle_id: str = Field(min_length=1)
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    components: tuple[M7ForecastBundleComponent, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_components(self) -> Self:
        if len({row.bundle_id for row in self.components}) != len(self.components):
            raise ValueError("m7_formal_components_duplicate")
        return self


class M7FormalInferenceResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["m7-formal-inference-result-v1"] = (
        "m7-formal-inference-result-v1"
    )
    availability: Literal["available", "unavailable"]
    session: date
    forecast_bundle_id: str
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    component_row_counts: dict[str, int]
    prediction_path: Path | None
    prediction_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    manifest_path: Path
    reason: str | None

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        available = self.availability == "available"
        if available != (self.prediction_path is not None):
            raise ValueError("m7_formal_prediction_path_availability_invalid")
        if available != (self.prediction_sha256 is not None):
            raise ValueError("m7_formal_prediction_hash_availability_invalid")
        if available == (self.reason is not None):
            raise ValueError("m7_formal_reason_availability_invalid")
        return self


BundleProvider = Callable[[], M7FormalBundleBinding | None]
PredictionProvider = Callable[[M7ForecastBundleComponent], tuple[date, pd.DataFrame]]
IntegrityObserver = Callable[[M7ServingObservation, datetime], object]


class M7FormalInferenceRunner:
    """Publish one immutable formal prediction ledger per complete EOD session."""

    def __init__(
        self,
        paths: LivePaths,
        *,
        bundle_provider: BundleProvider | None = None,
        prediction_provider: PredictionProvider | None = None,
        integrity_observer: IntegrityObserver | None = None,
    ) -> None:
        self.paths = paths
        self.root = paths.models / "m7-formal"
        self.root.mkdir(parents=True, exist_ok=True)
        self.bundle_provider = bundle_provider or self._active_binding
        self.prediction_provider = prediction_provider or self._predict_component
        self.integrity_observer = integrity_observer or self._observe_integrity
        self._lock = FileLock(str(self.root / ".formal-inference.lock"))

    def run(
        self, *, generated_at: datetime | None = None
    ) -> M7FormalInferenceResult | None:
        observed_at = generated_at or datetime.now(UTC)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise M7FormalInferenceError("m7_formal_generated_at_invalid")
        with self._lock:
            binding = self.bundle_provider()
            if binding is None:
                return None
            sessions: set[date] = set()
            frames: list[pd.DataFrame] = []
            counts: dict[str, int] = {}
            try:
                for component in sorted(
                    binding.components, key=lambda row: row.bundle_id
                ):
                    session, frame = self.prediction_provider(component)
                    normalized = self._validate_component_frame(component, frame)
                    sessions.add(session)
                    counts[component.bundle_id] = len(normalized)
                    normalized["anchor_session"] = session.isoformat()
                    normalized["forecast_bundle_id"] = binding.forecast_bundle_id
                    normalized["bundle_sha256"] = binding.bundle_sha256
                    normalized["bundle_id"] = component.bundle_id
                    normalized["package_id"] = component.package_id
                    normalized["package_sha256"] = component.package_sha256
                    normalized["evidence_scope"] = "formal"
                    frames.append(normalized)
                if len(sessions) != 1:
                    raise M7FormalInferenceError("m7_formal_component_sessions_mixed")
                session = next(iter(sessions))
                existing = self._load_existing(binding, session)
                if existing is not None:
                    return existing
                combined = pd.concat(frames, ignore_index=True)
                combined.sort_values(
                    ["bundle_id", "horizon", "symbol", "outcome"],
                    inplace=True,
                    kind="stable",
                )
                prediction_target = (
                    self.root
                    / "predictions"
                    / session.isoformat()
                    / f"{binding.bundle_sha256}.parquet"
                )
                prediction_hash = self._stage_and_verify(
                    prediction_target,
                    combined,
                    binding=binding,
                    observed_at=observed_at.astimezone(UTC),
                )
                manifest_target = prediction_target.with_suffix(".manifest.json")
                payload = {
                    "schema_version": "m7-formal-prediction-manifest-v1",
                    "session": session.isoformat(),
                    "forecast_bundle_id": binding.forecast_bundle_id,
                    "bundle_sha256": binding.bundle_sha256,
                    "component_row_counts": counts,
                    "prediction_path": prediction_target.relative_to(
                        self.paths.root
                    ).as_posix(),
                    "prediction_sha256": prediction_hash,
                }
                write_immutable_json(manifest_target, payload, pretty=False)
                return M7FormalInferenceResult(
                    availability="available",
                    session=session,
                    forecast_bundle_id=binding.forecast_bundle_id,
                    bundle_sha256=binding.bundle_sha256,
                    component_row_counts=counts,
                    prediction_path=prediction_target,
                    prediction_sha256=prediction_hash,
                    manifest_path=manifest_target,
                    reason=None,
                )
            except (M7FormalInferenceError, OSError, RuntimeError, ValueError) as error:
                session = next(iter(sessions), observed_at.date())
                return self._record_unavailable(
                    binding,
                    session=session,
                    reason=str(error) or type(error).__name__,
                    component_row_counts=counts,
                )

    def _active_binding(self) -> M7FormalBundleBinding | None:
        active = load_active_m7_forecast_bundle(self.paths)
        if active is None:
            return None
        return M7FormalBundleBinding(
            forecast_bundle_id=active.forecast_bundle_id,
            bundle_sha256=active.bundle_sha256,
            components=active.components,
        )

    def _predict_component(
        self, component: M7ForecastBundleComponent
    ) -> tuple[date, pd.DataFrame]:
        try:
            artifact = load_latest_feature_artifact(self.paths)
            dates = pq.read_table(artifact.path, columns=["session_date"]).to_pandas()[
                "session_date"
            ]
            session = max(pd.to_datetime(dates).dt.date)
            prepared = prepare_m7_feature_data(self.paths, development_cutoff=session)
        except (OSError, RuntimeError, ValueError) as error:
            raise M7FormalInferenceError(
                "m7_formal_current_features_unavailable"
            ) from error
        eligible = prepared.frame[prepared.frame["eligible"].fillna(False).astype(bool)]
        latest = eligible[eligible["session_date"] == session].copy()
        latest.sort_values("symbol", inplace=True, kind="stable")
        if len(latest) != 100 or set(latest["symbol"].astype(str)) != set(
            SEED_UNIVERSE
        ):
            raise M7FormalInferenceError("m7_formal_seed100_features_incomplete")
        package = self.paths.models / "m7-packages" / component.package_id
        manifest = load_m7_package(
            package.parent,
            component.package_id,
            current_runtime=detect_m7_runtime_compatibility(),
        )
        if (
            manifest.package_sha256 != component.package_sha256
            or manifest.bundle_id != component.bundle_id
            or manifest.universe_version != prepared.feature_result.universe_version
            or manifest.dataset_revision != prepared.feature_result.dataset_revision
            or manifest.feature_set_version
            != prepared.feature_result.feature_set_version
        ):
            raise M7FormalInferenceError("m7_formal_package_contract_mismatch")
        rows: list[dict[str, object]] = []
        if component.bundle_id == "direction":
            direction_model = load_m7_direction_package(package)
            for horizon in (1, 5, 20):
                values = direction_model.predict(prepared.frame, horizon=horizon)
                for symbol, probabilities in zip(
                    latest["symbol"].astype(str), values, strict=True
                ):
                    for outcome, probability in zip(
                        ("down", "flat", "up"), probabilities, strict=True
                    ):
                        rows.append(
                            {
                                "symbol": symbol,
                                "horizon": horizon,
                                "outcome": outcome,
                                "probability": float(probability),
                            }
                        )
        else:
            range_model = load_m7_range_package(package)
            values = range_model.predict(latest.reset_index(drop=True))
            for symbol, probabilities in zip(
                latest["symbol"].astype(str), values, strict=True
            ):
                for horizon, probability in zip((1, 5, 20), probabilities, strict=True):
                    rows.append(
                        {
                            "symbol": symbol,
                            "horizon": horizon,
                            "outcome": "exit_by",
                            "probability": float(probability),
                        }
                    )
        return session, pd.DataFrame(rows)

    @staticmethod
    def _validate_component_frame(
        component: M7ForecastBundleComponent, frame: pd.DataFrame
    ) -> pd.DataFrame:
        required = {"symbol", "horizon", "outcome", "probability"}
        if set(frame.columns) != required:
            raise M7FormalInferenceError("m7_formal_prediction_schema_invalid")
        result = frame.copy()
        result["symbol"] = result["symbol"].astype(str)
        result["horizon"] = pd.to_numeric(result["horizon"], errors="coerce")
        result["outcome"] = result["outcome"].astype(str)
        result["probability"] = pd.to_numeric(result["probability"], errors="coerce")
        if not np.isfinite(result["probability"].to_numpy(dtype=float)).all():
            raise M7FormalInferenceError("m7_formal_probability_non_finite")
        if not result["probability"].between(0.0, 1.0).all():
            raise M7FormalInferenceError("m7_formal_probability_out_of_bounds")
        if component.bundle_id == "direction":
            expected = {
                (symbol, horizon, outcome)
                for symbol in SEED_UNIVERSE
                for horizon in (1, 5, 20)
                for outcome in ("down", "flat", "up")
            }
            actual = set(
                result[["symbol", "horizon", "outcome"]].itertuples(
                    index=False, name=None
                )
            )
            sums = result.groupby(["symbol", "horizon"])["probability"].sum()
            if actual != expected or not np.allclose(sums.to_numpy(), 1.0, atol=1e-9):
                raise M7FormalInferenceError("m7_formal_direction_sample_keys_invalid")
        else:
            expected = {
                (symbol, horizon, "exit_by")
                for symbol in SEED_UNIVERSE
                for horizon in (1, 5, 20)
            }
            actual = set(
                result[["symbol", "horizon", "outcome"]].itertuples(
                    index=False, name=None
                )
            )
            pivot = result.pivot(
                index="symbol", columns="horizon", values="probability"
            )
            if (
                actual != expected
                or (np.diff(pivot[[1, 5, 20]], axis=1) < -1e-12).any()
            ):
                raise M7FormalInferenceError("m7_formal_range_sample_keys_invalid")
        return result

    def _stage_and_verify(
        self,
        target: Path,
        frame: pd.DataFrame,
        *,
        binding: M7FormalBundleBinding,
        observed_at: datetime,
    ) -> str:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            pq.write_table(
                pa.Table.from_pandas(frame, preserve_index=False),
                temporary,
                compression="zstd",
                use_dictionary=True,
            )
            digest = file_sha256(temporary)
            for component in binding.components:
                selected = frame[frame["bundle_id"] == component.bundle_id]
                if component.bundle_id == "direction":
                    for horizon in (1, 5, 20):
                        pivot = selected[selected["horizon"] == horizon].pivot(
                            index="symbol", columns="outcome", values="probability"
                        )
                        pivot = pivot.reindex(SEED_UNIVERSE)
                        observation = M7ServingObservation(
                            bundle_id="direction",
                            package_sha256=component.package_sha256,
                            universe_version="m1-seed-2026-07-11-v1",
                            expected_symbol_count=100,
                            observed_symbol_count=len(pivot),
                            direction_probabilities=tuple(
                                (float(row.down), float(row.flat), float(row.up))
                                for row in pivot.itertuples()
                            ),
                            range_probability_5d=None,
                            range_probability_20d=None,
                            prediction_artifact_hash=digest,
                        )
                        self._require_integrity(observation, observed_at)
                else:
                    pivot = selected.pivot(
                        index="symbol", columns="horizon", values="probability"
                    ).reindex(SEED_UNIVERSE)
                    observation = M7ServingObservation(
                        bundle_id="range_exit_20",
                        package_sha256=component.package_sha256,
                        universe_version="m1-seed-2026-07-11-v1",
                        expected_symbol_count=100,
                        observed_symbol_count=len(pivot),
                        direction_probabilities=None,
                        range_probability_5d=tuple(float(value) for value in pivot[5]),
                        range_probability_20d=tuple(
                            float(value) for value in pivot[20]
                        ),
                        prediction_artifact_hash=digest,
                    )
                    self._require_integrity(observation, observed_at)
            os.replace(temporary, target)
            return digest
        finally:
            temporary.unlink(missing_ok=True)

    def _require_integrity(
        self, observation: M7ServingObservation, observed_at: datetime
    ) -> None:
        result = self.integrity_observer(observation, observed_at)
        if isinstance(result, M7IntegrityResult) and result.action != "healthy":
            raise M7FormalInferenceError("m7_formal_integrity_rollback")

    def _observe_integrity(
        self, observation: M7ServingObservation, observed_at: datetime
    ) -> object:
        return M7IntegrityMonitor(self.paths).verify_observation(
            observation, observed_at=observed_at
        )

    def _load_existing(
        self, binding: M7FormalBundleBinding, session: date
    ) -> M7FormalInferenceResult | None:
        target = (
            self.root
            / "predictions"
            / session.isoformat()
            / f"{binding.bundle_sha256}.manifest.json"
        )
        if not target.is_file():
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            prediction = _resolve_prediction_path(
                self.paths, str(payload["prediction_path"])
            )
            if (
                payload.get("forecast_bundle_id") != binding.forecast_bundle_id
                or payload.get("bundle_sha256") != binding.bundle_sha256
                or file_sha256(prediction) != payload.get("prediction_sha256")
            ):
                raise ValueError
            counts = {
                str(name): int(value)
                for name, value in payload["component_row_counts"].items()
            }
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise M7FormalInferenceError(
                "m7_formal_existing_artifact_invalid"
            ) from error
        return M7FormalInferenceResult(
            availability="available",
            session=session,
            forecast_bundle_id=binding.forecast_bundle_id,
            bundle_sha256=binding.bundle_sha256,
            component_row_counts=counts,
            prediction_path=prediction,
            prediction_sha256=str(payload["prediction_sha256"]),
            manifest_path=target,
            reason=None,
        )

    def _record_unavailable(
        self,
        binding: M7FormalBundleBinding,
        *,
        session: date,
        reason: str,
        component_row_counts: dict[str, int],
    ) -> M7FormalInferenceResult:
        payload = {
            "schema_version": "m7-formal-unavailable-v1",
            "session": session.isoformat(),
            "forecast_bundle_id": binding.forecast_bundle_id,
            "bundle_sha256": binding.bundle_sha256,
            "component_row_counts": component_row_counts,
            "reason": reason,
        }
        target = (
            self.root
            / "unavailable"
            / session.isoformat()
            / f"{deterministic_hash(payload)}.json"
        )
        if not target.is_file():
            write_immutable_json(target, payload, pretty=False)
        return M7FormalInferenceResult(
            availability="unavailable",
            session=session,
            forecast_bundle_id=binding.forecast_bundle_id,
            bundle_sha256=binding.bundle_sha256,
            component_row_counts=component_row_counts,
            prediction_path=None,
            prediction_sha256=None,
            manifest_path=target,
            reason=reason,
        )


def load_m7_formal_inference_result(
    paths: LivePaths,
    *,
    binding: M7FormalBundleBinding,
    session: date,
) -> M7FormalInferenceResult | None:
    """Load one hash-verified available result without running inference."""

    return M7FormalInferenceRunner(
        paths,
        bundle_provider=lambda: None,
    )._load_existing(binding, session)


def _resolve_prediction_path(paths: LivePaths, value: str) -> Path:
    posix = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or posix.is_absolute()
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise M7FormalInferenceError("m7_formal_prediction_path_invalid")
    root = paths.root.resolve()
    unresolved = root / Path(*posix.parts)
    if unresolved.is_symlink():
        raise M7FormalInferenceError("m7_formal_prediction_path_invalid")
    candidate = unresolved.resolve(strict=True)
    if not candidate.is_relative_to(root):
        raise M7FormalInferenceError("m7_formal_prediction_path_invalid")
    return candidate


__all__ = [
    "M7FormalBundleBinding",
    "M7FormalInferenceError",
    "M7FormalInferenceResult",
    "M7FormalInferenceRunner",
    "load_m7_formal_inference_result",
]
