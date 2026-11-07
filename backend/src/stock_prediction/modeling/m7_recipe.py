"""Frozen M7 recipe-generation and development-evidence contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Literal, TypeAlias
from uuid import uuid4

from filelock import FileLock

from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V4,
    CANDIDATE_RECIPE_SEED100_TARGETED_V5,
    CANDIDATE_RECIPE_SEED100_TARGETED_V6,
    CANDIDATE_RECIPE_SEED100_TARGETED_V7,
    CANDIDATE_RECIPE_SEED100_TARGETED_V8,
    CANDIDATE_RECIPE_SEED100_TARGETED_V9,
    CANDIDATE_RECIPE_SEED100_TARGETED_V10,
)
from stock_prediction.universe.seed import SEED_UNIVERSE_VERSION


M7_FEATURE_SET_VERSION = "live-daily-features-v5-seed100"
M7_LABEL_VERSION = "live-labels-v2"
M7_RECIPE_EVIDENCE_SCHEMA_VERSION = "m7-recipe-evidence-v1"
_M7_RECIPE_GENERATIONS = {
    CANDIDATE_RECIPE_SEED100_TARGETED_V4: 4,
    CANDIDATE_RECIPE_SEED100_TARGETED_V5: 5,
    CANDIDATE_RECIPE_SEED100_TARGETED_V6: 6,
    CANDIDATE_RECIPE_SEED100_TARGETED_V7: 7,
    CANDIDATE_RECIPE_SEED100_TARGETED_V8: 8,
    CANDIDATE_RECIPE_SEED100_TARGETED_V9: 9,
    CANDIDATE_RECIPE_SEED100_TARGETED_V10: 10,
}

JsonScalar: TypeAlias = bool | int | float | str
M7CandidateFamily = Literal[
    "catboost_multiclass",
    "quantile_cdf",
    "dlinear",
    "patchtst",
]
M7FeatureGroup = Literal[
    "core_v2",
    "core_v2_cross_sectional",
    "core_v2_structure_context",
    "full_daily",
    "full_daily_intraday_optional",
]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class M7CandidateConfiguration:
    configuration_id: str
    family: M7CandidateFamily
    seed: int
    feature_group: M7FeatureGroup
    parameters: Mapping[str, JsonScalar]

    def __post_init__(self) -> None:
        if not self.configuration_id.startswith(f"m7-{self.family}-"):
            raise ValueError("m7_configuration_id_invalid")
        canonical_parameters = dict(sorted(self.parameters.items()))
        if not all(
            isinstance(value, bool | int | float | str)
            for value in canonical_parameters.values()
        ):
            raise TypeError("m7_configuration_parameters_must_be_scalar")
        object.__setattr__(self, "parameters", MappingProxyType(canonical_parameters))

    def canonical_payload(self) -> dict[str, object]:
        return {
            "configuration_id": self.configuration_id,
            "family": self.family,
            "feature_group": self.feature_group,
            "parameters": dict(self.parameters),
            "seed": self.seed,
        }


@dataclass(frozen=True, slots=True)
class M7CandidateMatrix:
    recipe_id: str
    recipe_generation: int
    universe_version: str
    feature_set_version: str
    label_version: str
    direction_horizons: tuple[int, int, int]
    configurations: tuple[M7CandidateConfiguration, ...]
    matrix_hash: str = ""

    def __post_init__(self) -> None:
        expected_generation = _M7_RECIPE_GENERATIONS.get(self.recipe_id)
        if expected_generation is None:
            raise ValueError("m7_recipe_id_invalid")
        if self.recipe_generation != expected_generation:
            raise ValueError("m7_recipe_generation_invalid")
        if self.universe_version != SEED_UNIVERSE_VERSION:
            raise ValueError("m7_universe_version_invalid")
        if self.direction_horizons != (1, 5, 20):
            raise ValueError("m7_direction_horizons_invalid")
        configuration_ids = tuple(row.configuration_id for row in self.configurations)
        if len(set(configuration_ids)) != len(configuration_ids):
            raise ValueError("m7_configuration_id_duplicate")
        family_counts = {
            family: sum(row.family == family for row in self.configurations)
            for family in (
                "catboost_multiclass",
                "quantile_cdf",
                "dlinear",
                "patchtst",
            )
        }
        if family_counts != {
            "catboost_multiclass": 12,
            "quantile_cdf": 6,
            "dlinear": 8,
            "patchtst": 8,
        }:
            raise ValueError("m7_candidate_family_limits_invalid")
        expected = _sha256(self.canonical_payload(include_hash=False))
        if self.matrix_hash and self.matrix_hash != expected:
            raise ValueError("m7_candidate_matrix_hash_invalid")
        object.__setattr__(self, "matrix_hash", expected)

    def canonical_payload(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "configurations": [row.canonical_payload() for row in self.configurations],
            "direction_horizons": list(self.direction_horizons),
            "feature_set_version": self.feature_set_version,
            "label_version": self.label_version,
            "recipe_generation": self.recipe_generation,
            "recipe_id": self.recipe_id,
            "universe_version": self.universe_version,
        }
        if include_hash:
            payload["matrix_hash"] = self.matrix_hash
        return payload


def _configuration(
    family: M7CandidateFamily,
    index: int,
    *,
    seed: int,
    feature_group: M7FeatureGroup,
    **parameters: JsonScalar,
) -> M7CandidateConfiguration:
    return M7CandidateConfiguration(
        configuration_id=f"m7-{family}-{index:02d}",
        family=family,
        seed=seed,
        feature_group=feature_group,
        parameters=parameters,
    )


def _catboost_configurations(
    *, early_stopping_rounds: int | None = None
) -> tuple[M7CandidateConfiguration, ...]:
    specifications = (
        (6, 0.03, 3.0, 0.5, "explicit"),
        (6, 0.03, 8.0, 1.0, "explicit"),
        (6, 0.06, 3.0, 1.0, "explicit"),
        (6, 0.06, 8.0, 0.5, "native_equivalent"),
        (7, 0.03, 3.0, 1.0, "explicit"),
        (7, 0.03, 8.0, 0.5, "native_equivalent"),
        (7, 0.06, 3.0, 0.5, "explicit"),
        (7, 0.06, 8.0, 1.0, "native_equivalent"),
        (8, 0.03, 3.0, 0.5, "explicit"),
        (8, 0.03, 8.0, 1.0, "native_equivalent"),
        (8, 0.06, 3.0, 1.0, "explicit"),
        (8, 0.06, 8.0, 0.5, "native_equivalent"),
    )
    configurations: list[M7CandidateConfiguration] = []
    for index, (
        depth,
        learning_rate,
        l2_leaf_reg,
        random_strength,
        class_weight_policy,
    ) in enumerate(specifications, start=1):
        parameters: dict[str, JsonScalar] = {
            "depth": depth,
            "learning_rate": learning_rate,
            "l2_leaf_reg": l2_leaf_reg,
            "random_strength": random_strength,
            "class_weight_policy": class_weight_policy,
            "max_iterations": 1200,
            "early_stopping_metric": "multiclass_brier",
        }
        if early_stopping_rounds is not None:
            parameters["early_stopping_rounds"] = early_stopping_rounds
        configurations.append(
            _configuration(
                "catboost_multiclass",
                index,
                seed=17 + index,
                feature_group=(
                    "core_v2_cross_sectional" if index <= 6 else "full_daily"
                ),
                **parameters,
            )
        )
    return tuple(configurations)


def _quantile_configurations(
    *,
    staged_prediction_policy: str | None = None,
    quantile_mapping_policy: str | None = None,
    fit_execution_policy: str | None = None,
    fit_parallel_workers: int | None = None,
    selection_execution_policy: str | None = None,
    selection_parallel_workers: int | None = None,
) -> tuple[M7CandidateConfiguration, ...]:
    specifications = (
        (31, 0.03, 400),
        (31, 0.05, 300),
        (63, 0.03, 400),
        (63, 0.05, 300),
        (127, 0.02, 500),
        (127, 0.04, 350),
    )
    configurations: list[M7CandidateConfiguration] = []
    for index, (num_leaves, learning_rate, max_iterations) in enumerate(
        specifications,
        start=1,
    ):
        parameters: dict[str, JsonScalar] = {
            "num_leaves": num_leaves,
            "learning_rate": learning_rate,
            "max_iterations": max_iterations,
            "quantiles": "0.10,0.25,0.50,0.75,0.90",
            "crossing_repair": "isotonic_projection",
        }
        if staged_prediction_policy is not None:
            parameters["staged_prediction_policy"] = staged_prediction_policy
        if quantile_mapping_policy is not None:
            parameters["quantile_mapping_policy"] = quantile_mapping_policy
        if fit_execution_policy is not None:
            parameters["fit_execution_policy"] = fit_execution_policy
        if fit_parallel_workers is not None:
            parameters["fit_parallel_workers"] = fit_parallel_workers
        if selection_execution_policy is not None:
            parameters["selection_execution_policy"] = selection_execution_policy
        if selection_parallel_workers is not None:
            parameters["selection_parallel_workers"] = selection_parallel_workers
        configurations.append(
            _configuration(
                "quantile_cdf",
                index,
                seed=37 + index,
                feature_group="full_daily",
                **parameters,
            )
        )
    return tuple(configurations)


def _dlinear_configurations(
    *, isolated_batch_size: int | None = None
) -> tuple[M7CandidateConfiguration, ...]:
    configurations: list[M7CandidateConfiguration] = []
    index = 1
    for sequence_length in (64, 128):
        for hidden_size in (64, 128):
            for seed in (17, 29):
                parameters: dict[str, JsonScalar] = {
                    "sequence_length": sequence_length,
                    "hidden_size": hidden_size,
                    "max_epochs": 80,
                    "patience": 10,
                    "horizon_loss_weights": "1/3,1/3,1/3",
                }
                if isolated_batch_size is not None:
                    parameters.update(
                        batch_size=isolated_batch_size,
                        execution_policy="isolated_child_v1",
                    )
                configurations.append(
                    _configuration(
                        "dlinear",
                        index,
                        seed=seed,
                        feature_group="full_daily",
                        **parameters,
                    )
                )
                index += 1
    return tuple(configurations)


def _patchtst_configurations(
    *, isolated_batch_size: int | None = None
) -> tuple[M7CandidateConfiguration, ...]:
    structures = (
        (64, 8, 64, 2, 0.1),
        (64, 16, 128, 3, 0.2),
        (128, 8, 128, 2, 0.1),
        (128, 16, 64, 3, 0.2),
    )
    configurations: list[M7CandidateConfiguration] = []
    index = 1
    for sequence_length, patch_length, d_model, layers, dropout in structures:
        for seed in (17, 29):
            parameters: dict[str, JsonScalar] = {
                "sequence_length": sequence_length,
                "patch_length": patch_length,
                "d_model": d_model,
                "encoder_layers": layers,
                "attention_heads": 4,
                "dropout": dropout,
                "max_epochs": 80,
                "patience": 10,
                "horizon_loss_weights": "1/3,1/3,1/3",
            }
            if isolated_batch_size is not None:
                parameters.update(
                    batch_size=isolated_batch_size,
                    execution_policy="isolated_child_v1",
                )
            configurations.append(
                _configuration(
                    "patchtst",
                    index,
                    seed=seed,
                    feature_group="full_daily",
                    **parameters,
                )
            )
            index += 1
    return tuple(configurations)


def registered_m7_candidate_matrix(
    *, recipe_id: str = CANDIDATE_RECIPE_SEED100_TARGETED_V4
) -> M7CandidateMatrix:
    try:
        generation = _M7_RECIPE_GENERATIONS[recipe_id]
    except KeyError as error:
        raise ValueError("m7_recipe_id_invalid") from error
    isolated_batch_size = (
        64
        if recipe_id
        in {
            CANDIDATE_RECIPE_SEED100_TARGETED_V5,
            CANDIDATE_RECIPE_SEED100_TARGETED_V6,
            CANDIDATE_RECIPE_SEED100_TARGETED_V7,
            CANDIDATE_RECIPE_SEED100_TARGETED_V8,
            CANDIDATE_RECIPE_SEED100_TARGETED_V9,
            CANDIDATE_RECIPE_SEED100_TARGETED_V10,
        }
        else None
    )
    return M7CandidateMatrix(
        recipe_id=recipe_id,
        recipe_generation=generation,
        universe_version=SEED_UNIVERSE_VERSION,
        feature_set_version=M7_FEATURE_SET_VERSION,
        label_version=M7_LABEL_VERSION,
        direction_horizons=(1, 5, 20),
        configurations=(
            *_catboost_configurations(
                early_stopping_rounds=(
                    100
                    if recipe_id
                    in {
                        CANDIDATE_RECIPE_SEED100_TARGETED_V6,
                        CANDIDATE_RECIPE_SEED100_TARGETED_V7,
                        CANDIDATE_RECIPE_SEED100_TARGETED_V8,
                        CANDIDATE_RECIPE_SEED100_TARGETED_V9,
                        CANDIDATE_RECIPE_SEED100_TARGETED_V10,
                    }
                    else None
                )
            ),
            *_quantile_configurations(
                staged_prediction_policy=(
                    "leaf_path_cumsum_v1"
                    if recipe_id
                    in {
                        CANDIDATE_RECIPE_SEED100_TARGETED_V7,
                        CANDIDATE_RECIPE_SEED100_TARGETED_V8,
                        CANDIDATE_RECIPE_SEED100_TARGETED_V9,
                        CANDIDATE_RECIPE_SEED100_TARGETED_V10,
                    }
                    else None
                ),
                quantile_mapping_policy=(
                    "scipy_isotonic_compact_interp_v1"
                    if recipe_id
                    in {
                        CANDIDATE_RECIPE_SEED100_TARGETED_V8,
                        CANDIDATE_RECIPE_SEED100_TARGETED_V9,
                        CANDIDATE_RECIPE_SEED100_TARGETED_V10,
                    }
                    else None
                ),
                fit_execution_policy=(
                    "lightgbm_threadpool_ordered_v1"
                    if recipe_id
                    in {
                        CANDIDATE_RECIPE_SEED100_TARGETED_V9,
                        CANDIDATE_RECIPE_SEED100_TARGETED_V10,
                    }
                    else None
                ),
                fit_parallel_workers=(
                    4
                    if recipe_id
                    in {
                        CANDIDATE_RECIPE_SEED100_TARGETED_V9,
                        CANDIDATE_RECIPE_SEED100_TARGETED_V10,
                    }
                    else None
                ),
                selection_execution_policy=(
                    "joblib_loky_ordered_chunks_v1"
                    if recipe_id
                    in {
                        CANDIDATE_RECIPE_SEED100_TARGETED_V9,
                        CANDIDATE_RECIPE_SEED100_TARGETED_V10,
                    }
                    else None
                ),
                selection_parallel_workers=(
                    8
                    if recipe_id
                    in {
                        CANDIDATE_RECIPE_SEED100_TARGETED_V9,
                        CANDIDATE_RECIPE_SEED100_TARGETED_V10,
                    }
                    else None
                ),
            ),
            *_dlinear_configurations(isolated_batch_size=isolated_batch_size),
            *_patchtst_configurations(isolated_batch_size=isolated_batch_size),
        ),
    )


@dataclass(frozen=True, slots=True)
class M7DevelopmentEvidence:
    recipe_id: str
    recipe_generation: int
    candidate_matrix_hash: str
    data_cutoff: date
    dataset_revision: str
    feature_input_hash: str
    universe_version: str
    evidence_hash: str = ""

    def __post_init__(self) -> None:
        payload = self.canonical_payload(include_hash=False)
        expected = _sha256(payload)
        if self.evidence_hash and self.evidence_hash != expected:
            raise ValueError("m7_development_evidence_hash_invalid")
        object.__setattr__(self, "evidence_hash", expected)

    def canonical_payload(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "candidate_matrix_hash": self.candidate_matrix_hash,
            "data_cutoff": self.data_cutoff.isoformat(),
            "dataset_revision": self.dataset_revision,
            "feature_input_hash": self.feature_input_hash,
            "recipe_generation": self.recipe_generation,
            "recipe_id": self.recipe_id,
            "universe_version": self.universe_version,
        }
        if include_hash:
            payload["evidence_hash"] = self.evidence_hash
        return payload


M7RecipeEvidenceState = Literal["reserved", "inspected"]


@dataclass(frozen=True, slots=True)
class M7RecipeEvidenceRecord:
    evidence_hash: str
    run_id: str
    state: M7RecipeEvidenceState
    created_at: str
    inspected_at: str | None = None


class M7RecipeEvidenceConsumedError(RuntimeError):
    """Raised when inspected development evidence would be reused."""


class M7RecipeEvidenceLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = FileLock(f"{path}.lock")

    def reserve(
        self,
        evidence: M7DevelopmentEvidence,
        *,
        run_id: str,
    ) -> M7RecipeEvidenceRecord:
        with self._lock:
            records = self._load()
            existing = next(
                (
                    record
                    for record in records
                    if record.evidence_hash == evidence.evidence_hash
                ),
                None,
            )
            if existing is not None:
                if existing.state == "inspected":
                    raise M7RecipeEvidenceConsumedError(
                        "m7_recipe_evidence_already_inspected"
                    )
                return existing
            record = M7RecipeEvidenceRecord(
                evidence_hash=evidence.evidence_hash,
                run_id=run_id,
                state="reserved",
                created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
            self._write((*records, record))
            return record

    def mark_inspected(
        self,
        evidence: M7DevelopmentEvidence,
        *,
        run_id: str,
    ) -> M7RecipeEvidenceRecord:
        with self._lock:
            records = list(self._load())
            for index, record in enumerate(records):
                if record.evidence_hash != evidence.evidence_hash:
                    continue
                if record.run_id != run_id:
                    raise RuntimeError("m7_recipe_evidence_run_mismatch")
                if record.state == "inspected":
                    return record
                inspected = M7RecipeEvidenceRecord(
                    evidence_hash=record.evidence_hash,
                    run_id=record.run_id,
                    state="inspected",
                    created_at=record.created_at,
                    inspected_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                )
                records[index] = inspected
                self._write(tuple(records))
                return inspected
        raise RuntimeError("m7_recipe_evidence_not_reserved")

    def _load(self) -> tuple[M7RecipeEvidenceRecord, ...]:
        if not self.path.exists():
            return ()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != M7_RECIPE_EVIDENCE_SCHEMA_VERSION:
            raise RuntimeError("m7_recipe_evidence_schema_mismatch")
        records = tuple(
            M7RecipeEvidenceRecord(**record) for record in payload.get("records", [])
        )
        if len({record.evidence_hash for record in records}) != len(records):
            raise RuntimeError("m7_recipe_evidence_duplicate")
        return records

    def _write(self, records: tuple[M7RecipeEvidenceRecord, ...]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "records": [asdict(record) for record in records],
            "schema_version": M7_RECIPE_EVIDENCE_SCHEMA_VERSION,
        }
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        temporary.write_bytes(_canonical_json(payload))
        os.replace(temporary, self.path)


__all__ = [
    "M7CandidateConfiguration",
    "M7CandidateMatrix",
    "M7DevelopmentEvidence",
    "M7RecipeEvidenceConsumedError",
    "M7RecipeEvidenceLedger",
    "M7RecipeEvidenceRecord",
    "registered_m7_candidate_matrix",
]
