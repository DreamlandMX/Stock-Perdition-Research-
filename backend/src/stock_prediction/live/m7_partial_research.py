"""Immutable, research-only M7 partial-fold artifacts.

Partial artifacts intentionally live outside package publication.  A valid fold
can be read for research while a run is incomplete, but it can never satisfy a
development, shadow, blind, or promotion gate.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
import json
import os
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_prediction.live.config import LivePaths
from stock_prediction.modeling.report import canonical_json_bytes, deterministic_hash
from stock_prediction.universe.seed import SEED_UNIVERSE


_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$"
_SHA256 = r"^[0-9a-f]{64}$"
_RECIPE_ID = "candidate-recipe-seed100-targeted-v10"


class M7PartialResearchError(RuntimeError):
    """A partial artifact cannot be trusted or atomically published."""


class M7PartialDirectionPrediction(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)

    symbol: str = Field(pattern=_IDENTIFIER)
    horizon: Literal[1, 5, 20]
    down_probability: float = Field(ge=0, le=1)
    flat_probability: float = Field(ge=0, le=1)
    up_probability: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_probability(self) -> Self:
        if abs(
            self.down_probability + self.flat_probability + self.up_probability - 1.0
        ) > 1e-9:
            raise ValueError("m7_partial_direction_probability_invalid")
        return self


class M7PartialRangePrediction(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)

    symbol: str = Field(pattern=_IDENTIFIER)
    horizon: Literal[20]
    range_exit_probability: float = Field(ge=0, le=1)


M7PartialPrediction = M7PartialDirectionPrediction | M7PartialRangePrediction


class M7PartialFoldManifest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)

    schema_version: Literal["m7-partial-fold-v1"] = "m7-partial-fold-v1"
    run_id: str = Field(pattern=_IDENTIFIER)
    recipe_id: Literal["candidate-recipe-seed100-targeted-v10"] = _RECIPE_ID
    bundle_id: Literal["direction", "range_exit_20"]
    fold_id: str = Field(pattern=_IDENTIFIER)
    as_of: date
    universe_version: str = Field(min_length=1)
    dataset_revision: str = Field(min_length=1)
    feature_set_version: str = Field(min_length=1)
    feature_input_hash: str = Field(pattern=_SHA256)
    candidate_matrix_hash: str = Field(pattern=_SHA256)
    selected_components: tuple[str, ...] = Field(min_length=1)
    training_devices: tuple[str, ...] = Field(min_length=1)
    predictions: tuple[M7PartialPrediction, ...] = Field(min_length=1)
    source_result_hashes: tuple[str, ...] = Field(min_length=1)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    manifest_hash: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity_and_hash(self) -> Self:
        if len(set(self.selected_components)) != len(self.selected_components):
            raise ValueError("m7_partial_selected_components_duplicate")
        if len(set(self.training_devices)) != len(self.training_devices):
            raise ValueError("m7_partial_training_devices_duplicate")
        if any(not _is_sha256(value) for value in self.source_result_hashes):
            raise ValueError("m7_partial_source_result_hash_invalid")
        _validate_prediction_set(self.bundle_id, self.predictions)
        expected = deterministic_hash(
            self.model_dump(mode="json", exclude={"manifest_hash"})
        )
        if self.manifest_hash and self.manifest_hash != expected:
            raise ValueError("m7_partial_manifest_hash_invalid")
        object.__setattr__(self, "manifest_hash", expected)
        return self


class M7PartialResearchUnavailable(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    availability: Literal["unavailable"] = "unavailable"
    reason: Literal[
        "m7_partial_no_valid_fold",
        "m7_partial_symbol_not_in_universe",
        "m7_partial_manifest_invalid",
        "m7_partial_bundle_not_requested",
        "m7_partial_run_not_found",
    ]


class M7PartialResearchAvailable(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)

    availability: Literal["available"] = "available"
    run_id: str
    recipe_id: Literal["candidate-recipe-seed100-targeted-v10"]
    bundle_id: Literal["direction", "range_exit_20"]
    as_of: date
    universe_version: str
    dataset_revision: str
    feature_set_version: str
    feature_input_hash: str = Field(pattern=_SHA256)
    candidate_matrix_hash: str = Field(pattern=_SHA256)
    predictions: tuple[M7PartialPrediction, ...]
    contributing_folds: tuple[str, ...] = Field(min_length=1, max_length=4)
    required_folds: Literal[4] = 4
    training_devices: tuple[str, ...]
    aggregate_hash: str = Field(pattern=_SHA256)


M7PartialResearchResult = M7PartialResearchAvailable | M7PartialResearchUnavailable


class M7PartialResearchService:
    def __init__(self, paths: LivePaths) -> None:
        self.paths = paths

    def publish(self, manifest: M7PartialFoldManifest) -> M7PartialFoldManifest:
        target = self._target(manifest.run_id, manifest.bundle_id, manifest.fold_id)
        lock = FileLock(f"{target}.lock")
        payload = manifest.model_dump(mode="json")
        with lock:
            if target.is_file():
                existing = self._load(target)
                if existing != manifest:
                    raise M7PartialResearchError("m7_partial_manifest_conflict")
                return existing
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                with temporary.open("xb") as stream:
                    stream.write(canonical_json_bytes(payload, pretty=False))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        published = self._load(target)
        if published != manifest:
            raise M7PartialResearchError("m7_partial_manifest_write_invalid")
        return published

    def result(
        self,
        *,
        run_id: str,
        bundle_id: Literal["direction", "range_exit_20"],
        symbol: str,
    ) -> M7PartialResearchResult:
        if symbol not in SEED_UNIVERSE:
            return M7PartialResearchUnavailable(
                reason="m7_partial_symbol_not_in_universe"
            )
        run_root = self.paths.models / "runs" / run_id
        if not run_root.is_dir():
            return M7PartialResearchUnavailable(reason="m7_partial_run_not_found")
        directory = run_root / "partial-research" / bundle_id
        if not directory.is_dir():
            return M7PartialResearchUnavailable(reason="m7_partial_no_valid_fold")
        manifests: list[M7PartialFoldManifest] = []
        try:
            for path in sorted(directory.glob("*.json")):
                manifest = self._load(path)
                if (
                    manifest.run_id != run_id
                    or manifest.bundle_id != bundle_id
                    or path.stem != manifest.fold_id
                ):
                    raise M7PartialResearchError("m7_partial_manifest_identity_invalid")
                manifests.append(manifest)
        except M7PartialResearchError:
            return M7PartialResearchUnavailable(reason="m7_partial_manifest_invalid")
        if not manifests:
            return M7PartialResearchUnavailable(reason="m7_partial_no_valid_fold")
        if len(manifests) > 4 or len({row.fold_id for row in manifests}) != len(manifests):
            return M7PartialResearchUnavailable(reason="m7_partial_manifest_invalid")
        identity = _identity(manifests[0])
        if any(_identity(manifest) != identity for manifest in manifests[1:]):
            return M7PartialResearchUnavailable(reason="m7_partial_manifest_invalid")
        aggregate = _aggregate_predictions(manifests, symbol=symbol)
        aggregate_hash = deterministic_hash(
            {
                "identity": identity,
                "manifest_hashes": [manifest.manifest_hash for manifest in manifests],
                "predictions": [row.model_dump(mode="json") for row in aggregate],
            }
        )
        devices = tuple(
            sorted({device for manifest in manifests for device in manifest.training_devices})
        )
        first = manifests[0]
        return M7PartialResearchAvailable(
            run_id=run_id,
            recipe_id=first.recipe_id,
            bundle_id=bundle_id,
            as_of=first.as_of,
            universe_version=first.universe_version,
            dataset_revision=first.dataset_revision,
            feature_set_version=first.feature_set_version,
            feature_input_hash=first.feature_input_hash,
            candidate_matrix_hash=first.candidate_matrix_hash,
            predictions=aggregate,
            contributing_folds=tuple(manifest.fold_id for manifest in manifests),
            training_devices=devices,
            aggregate_hash=aggregate_hash,
        )

    def _target(self, run_id: str, bundle_id: str, fold_id: str) -> Path:
        return (
            self.paths.models
            / "runs"
            / run_id
            / "partial-research"
            / bundle_id
            / f"{fold_id}.json"
        )

    @staticmethod
    def _load(path: Path) -> M7PartialFoldManifest:
        try:
            return M7PartialFoldManifest.model_validate_json(
                path.read_text(encoding="utf-8"), strict=True
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise M7PartialResearchError("m7_partial_manifest_invalid") from error


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _validate_prediction_set(
    bundle_id: str,
    predictions: tuple[M7PartialPrediction, ...],
) -> None:
    expected = {(symbol, horizon) for symbol in SEED_UNIVERSE for horizon in (1, 5, 20)}
    if bundle_id == "direction":
        if not all(isinstance(row, M7PartialDirectionPrediction) for row in predictions):
            raise ValueError("m7_partial_direction_prediction_type_invalid")
        keys = {(row.symbol, row.horizon) for row in predictions}
        if len(keys) != len(predictions) or keys != expected:
            raise ValueError("m7_partial_direction_prediction_identity_invalid")
        return
    expected_range = {(symbol, 20) for symbol in SEED_UNIVERSE}
    if not all(isinstance(row, M7PartialRangePrediction) for row in predictions):
        raise ValueError("m7_partial_range_prediction_type_invalid")
    keys = {(row.symbol, row.horizon) for row in predictions}
    if len(keys) != len(predictions) or keys != expected_range:
        raise ValueError("m7_partial_range_prediction_identity_invalid")


def _identity(manifest: M7PartialFoldManifest) -> dict[str, str]:
    return {
        "as_of": manifest.as_of.isoformat(),
        "candidate_matrix_hash": manifest.candidate_matrix_hash,
        "dataset_revision": manifest.dataset_revision,
        "feature_input_hash": manifest.feature_input_hash,
        "feature_set_version": manifest.feature_set_version,
        "recipe_id": manifest.recipe_id,
        "universe_version": manifest.universe_version,
    }


def _aggregate_predictions(
    manifests: list[M7PartialFoldManifest], *, symbol: str
) -> tuple[M7PartialPrediction, ...]:
    grouped: dict[int, list[M7PartialPrediction]] = {}
    for manifest in manifests:
        for prediction in manifest.predictions:
            if prediction.symbol == symbol:
                grouped.setdefault(prediction.horizon, []).append(prediction)
    if manifests[0].bundle_id == "direction":
        if set(grouped) != {1, 5, 20} or any(len(rows) != len(manifests) for rows in grouped.values()):
            raise M7PartialResearchError("m7_partial_direction_aggregate_invalid")
        return tuple(
            M7PartialDirectionPrediction(
                symbol=symbol,
                horizon=horizon,
                down_probability=sum(row.down_probability for row in rows) / len(rows),
                flat_probability=sum(row.flat_probability for row in rows) / len(rows),
                up_probability=sum(row.up_probability for row in rows) / len(rows),
            )
            for horizon, rows in sorted(grouped.items())
            if all(isinstance(row, M7PartialDirectionPrediction) for row in rows)
        )
    rows = grouped.get(20, [])
    if len(rows) != len(manifests) or not all(
        isinstance(row, M7PartialRangePrediction) for row in rows
    ):
        raise M7PartialResearchError("m7_partial_range_aggregate_invalid")
    return (
        M7PartialRangePrediction(
            symbol=symbol,
            horizon=20,
            range_exit_probability=sum(row.range_exit_probability for row in rows)
            / len(rows),
        ),
    )


__all__ = [
    "M7PartialDirectionPrediction",
    "M7PartialFoldManifest",
    "M7PartialRangePrediction",
    "M7PartialResearchAvailable",
    "M7PartialResearchError",
    "M7PartialResearchResult",
    "M7PartialResearchService",
    "M7PartialResearchUnavailable",
]
