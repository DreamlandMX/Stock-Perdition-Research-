"""Immutable, content-addressed M7 model package publication."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
from importlib.metadata import version
import json
import os
import platform as runtime_platform
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from typing import Literal, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import to_jsonable_python

from stock_prediction.contracts.operations import (
    BlindEvidenceScope,
    ModelCapabilityBundle,
)
from stock_prediction.contracts.time import UtcDatetime


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
M7_PACKAGE_SCHEMA_VERSION: Literal["m7-package-manifest-v1"] = "m7-package-manifest-v1"
ArtifactCategory = Literal["label_threshold", "model", "calibration", "explanation"]


class M7PackageError(RuntimeError):
    """An M7 package cannot be safely built, published, or loaded."""


class M7RuntimeCompatibility(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    python_version: str = Field(pattern=r"^3\.11(?:\.[0-9]+)?$")
    platform: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    numpy_version: str = Field(min_length=1)
    scikit_learn_version: str = Field(min_length=1)
    catboost_version: str = Field(min_length=1)
    torch_version: str = Field(min_length=1)
    inference_devices: tuple[Literal["cpu", "cuda"], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_cpu_inference(self) -> Self:
        if len(set(self.inference_devices)) != len(self.inference_devices):
            raise ValueError("M7 runtime inference devices must be unique")
        if "cpu" not in self.inference_devices:
            raise ValueError("M7 runtime compatibility requires cpu inference")
        return self


def detect_m7_runtime_compatibility() -> M7RuntimeCompatibility:
    """Describe the current process using the exact frozen package fields."""

    import torch

    platform_name = {"win32": "windows", "darwin": "macos"}.get(
        sys.platform, sys.platform
    )
    devices: tuple[Literal["cpu", "cuda"], ...] = (
        ("cpu", "cuda") if torch.cuda.is_available() else ("cpu",)
    )
    return M7RuntimeCompatibility(
        python_version=runtime_platform.python_version(),
        platform=platform_name,
        architecture=runtime_platform.machine().lower(),
        numpy_version=version("numpy"),
        scikit_learn_version=version("scikit-learn"),
        catboost_version=version("catboost"),
        torch_version=version("torch"),
        inference_devices=devices,
    )


class M7PackageArtifact(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    artifact_id: str = Field(min_length=1)
    category: ArtifactCategory
    relative_path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)

    @field_validator("artifact_id")
    @classmethod
    def validate_artifact_id(cls, value: str) -> str:
        if _SAFE_ID.fullmatch(value) is None:
            raise ValueError("m7 artifact_id is unsafe")
        return value

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        _validate_relative_path(value)
        return value


class M7PackageBuildSpec(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    package_id: str = Field(min_length=1)
    bundle_id: ModelCapabilityBundle
    recipe_id: str = Field(min_length=1)
    recipe_generation: int = Field(ge=1)
    code_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    universe_version: str = Field(min_length=1)
    dataset_revision: str = Field(min_length=1)
    feature_set_version: str = Field(min_length=1)
    feature_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    label_version: str = Field(min_length=1)
    split_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_matrix_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dependency_lock_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_compatibility: M7RuntimeCompatibility
    evidence_scope: BlindEvidenceScope
    blind_window_id: str | None
    created_at: UtcDatetime

    @field_validator("package_id")
    @classmethod
    def validate_package_id(cls, value: str) -> str:
        if _SAFE_ID.fullmatch(value) is None:
            raise ValueError("m7 package_id is unsafe")
        return value


class M7PackageManifest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["m7-package-manifest-v1"] = M7_PACKAGE_SCHEMA_VERSION
    package_id: str = Field(min_length=1)
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle_id: ModelCapabilityBundle
    recipe_id: str = Field(min_length=1)
    recipe_generation: int = Field(ge=1)
    code_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    universe_version: str = Field(min_length=1)
    dataset_revision: str = Field(min_length=1)
    feature_set_version: str = Field(min_length=1)
    feature_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    label_version: str = Field(min_length=1)
    label_threshold_artifacts: tuple[M7PackageArtifact, ...] = Field(min_length=1)
    split_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_matrix_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_artifacts: tuple[M7PackageArtifact, ...] = Field(min_length=1)
    calibration_artifacts: tuple[M7PackageArtifact, ...] = Field(min_length=1)
    explanation_artifacts: tuple[M7PackageArtifact, ...] = Field(min_length=1)
    dependency_lock_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_compatibility: M7RuntimeCompatibility
    runtime_observations_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    evidence_scope: BlindEvidenceScope
    blind_window_id: str | None
    created_at: UtcDatetime

    @field_validator("package_id")
    @classmethod
    def validate_package_id(cls, value: str) -> str:
        if _SAFE_ID.fullmatch(value) is None:
            raise ValueError("m7 package_id is unsafe")
        return value

    @model_validator(mode="after")
    def validate_artifact_sets(self) -> Self:
        groups = (
            ("label_threshold", self.label_threshold_artifacts),
            ("model", self.model_artifacts),
            ("calibration", self.calibration_artifacts),
            ("explanation", self.explanation_artifacts),
        )
        all_artifacts = tuple(item for _, entries in groups for item in entries)
        if any(
            item.category != category
            for category, entries in groups
            for item in entries
        ):
            raise ValueError("M7 package artifact category mismatch")
        if len({item.artifact_id for item in all_artifacts}) != len(all_artifacts):
            raise ValueError("M7 package artifact IDs must be unique")
        if len({item.relative_path for item in all_artifacts}) != len(all_artifacts):
            raise ValueError("M7 package artifact paths must be unique")
        if self.bundle_id == "direction" and len(self.model_artifacts) < 3:
            raise ValueError(
                "M7 direction package requires three horizon model artifacts"
            )
        return self

    @property
    def all_artifacts(self) -> tuple[M7PackageArtifact, ...]:
        return (
            *self.label_threshold_artifacts,
            *self.model_artifacts,
            *self.calibration_artifacts,
            *self.explanation_artifacts,
        )


class M7PackageArtifactInput(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", arbitrary_types_allowed=True
    )

    artifact_id: str
    category: ArtifactCategory
    source_path: Path
    schema_version: str = Field(min_length=1)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("artifact_id")
    @classmethod
    def validate_artifact_id(cls, value: str) -> str:
        if _SAFE_ID.fullmatch(value) is None:
            raise ValueError("m7 artifact_id is unsafe")
        return value


@dataclass(frozen=True, slots=True)
class PublishedM7Package:
    manifest: M7PackageManifest
    package_directory: Path


def publish_m7_package(
    package_root: Path,
    *,
    build_spec: M7PackageBuildSpec,
    artifacts: Sequence[M7PackageArtifactInput],
    runtime_observations: Mapping[str, object] | None = None,
) -> PublishedM7Package:
    root = package_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / build_spec.package_id
    if target.exists():
        raise M7PackageError("m7_package_already_exists")
    supplied = tuple(artifacts)
    _validate_artifact_inputs(supplied)
    staging_root = root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = staging_root / f"{build_spec.package_id}-{uuid4().hex}"
    staging.mkdir()
    entries: list[M7PackageArtifact] = []
    try:
        for item in supplied:
            source = item.source_path.resolve(strict=True)
            if not source.is_file() or item.source_path.is_symlink():
                raise M7PackageError("m7_package_source_artifact_invalid")
            suffix = (
                source.suffix
                if re.fullmatch(r"\.[A-Za-z0-9]{1,12}", source.suffix)
                else ".bin"
            )
            relative = PurePosixPath(
                "artifacts", item.category, f"{item.artifact_id}{suffix}"
            )
            destination = staging / Path(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            digest = _file_sha256(destination)
            if item.content_hash is not None and item.content_hash != digest:
                raise M7PackageError("m7_package_declared_content_hash_mismatch")
            entries.append(
                M7PackageArtifact(
                    artifact_id=item.artifact_id,
                    category=item.category,
                    relative_path=relative.as_posix(),
                    sha256=digest,
                    content_hash=item.content_hash or digest,
                    schema_version=item.schema_version,
                    size_bytes=destination.stat().st_size,
                )
            )
        runtime_hash = _publish_runtime_observations(root, runtime_observations)
        base = _manifest_payload(build_spec, entries, runtime_hash=runtime_hash)
        package_hash = _package_hash(staging, base)
        manifest = M7PackageManifest.model_validate(
            {**base, "package_sha256": package_hash},
            strict=True,
        )
        (staging / "manifest.json").write_bytes(
            _canonical_json(manifest.model_dump(mode="json"))
        )
        try:
            os.replace(staging, target)
        except OSError as error:
            raise M7PackageError("m7_package_atomic_publish_failed") from error
        return PublishedM7Package(manifest=manifest, package_directory=target)
    except M7PackageError:
        raise
    except (OSError, ValidationError, ValueError) as error:
        raise M7PackageError("m7_package_build_failed") from error


def load_m7_package(
    package_root: Path,
    package_id: str,
    *,
    current_runtime: M7RuntimeCompatibility | None = None,
) -> M7PackageManifest:
    if _SAFE_ID.fullmatch(package_id) is None:
        raise M7PackageError("m7_package_id_invalid")
    root = package_root.resolve()
    package_directory = root / package_id
    manifest = _load_package_directory(root, package_directory)
    if current_runtime is not None:
        verify_m7_runtime_compatibility(manifest.runtime_compatibility, current_runtime)
    return manifest


def verify_m7_runtime_compatibility(
    required: M7RuntimeCompatibility,
    current: M7RuntimeCompatibility,
) -> None:
    """Fail closed when serving cannot replay the frozen dependency contract."""

    fields = (
        "python_version",
        "platform",
        "architecture",
        "numpy_version",
        "scikit_learn_version",
        "catboost_version",
        "torch_version",
    )
    mismatches = tuple(
        name for name in fields if getattr(required, name) != getattr(current, name)
    )
    unavailable_devices = set(required.inference_devices) - set(
        current.inference_devices
    )
    if mismatches or unavailable_devices:
        detail = ",".join(
            (*mismatches, *(f"device:{value}" for value in sorted(unavailable_devices)))
        )
        raise M7PackageError(f"m7_package_runtime_incompatible:{detail}")


def recover_m7_package_staging(package_root: Path) -> tuple[PublishedM7Package, ...]:
    root = package_root.resolve()
    staging_root = root / ".staging"
    if not staging_root.is_dir():
        return ()
    recovered: list[PublishedM7Package] = []
    for staging in sorted(path for path in staging_root.iterdir() if path.is_dir()):
        manifest = _load_package_directory(root, staging, allow_staging=True)
        target = root / manifest.package_id
        if target.exists():
            raise M7PackageError("m7_package_recovery_target_exists")
        try:
            os.replace(staging, target)
        except OSError as error:
            raise M7PackageError("m7_package_recovery_publish_failed") from error
        recovered.append(
            PublishedM7Package(manifest=manifest, package_directory=target)
        )
    return tuple(recovered)


def _load_package_directory(
    root: Path,
    package_directory: Path,
    *,
    allow_staging: bool = False,
) -> M7PackageManifest:
    try:
        resolved_directory = package_directory.resolve(strict=True)
        if not _is_relative_to(resolved_directory, root):
            raise M7PackageError("m7_package_path_outside_root")
        manifest_path = resolved_directory / "manifest.json"
        if manifest_path.is_symlink():
            raise M7PackageError("m7_package_manifest_symlink_invalid")
        manifest = M7PackageManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8"),
            strict=True,
        )
    except M7PackageError:
        raise
    except (OSError, ValidationError, ValueError) as error:
        raise M7PackageError("m7_package_manifest_invalid") from error
    if not allow_staging and resolved_directory.name != manifest.package_id:
        raise M7PackageError("m7_package_identity_mismatch")
    for artifact in manifest.all_artifacts:
        candidate = resolved_directory / Path(
            *PurePosixPath(artifact.relative_path).parts
        )
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise M7PackageError("m7_package_artifact_missing") from error
        if candidate.is_symlink() or not _is_relative_to(resolved, resolved_directory):
            raise M7PackageError("m7_package_artifact_path_invalid")
        if not resolved.is_file() or resolved.stat().st_size != artifact.size_bytes:
            raise M7PackageError("m7_package_artifact_size_mismatch")
        digest = _file_sha256(resolved)
        if digest != artifact.sha256 or digest != artifact.content_hash:
            raise M7PackageError("m7_package_artifact_hash_mismatch")
    if manifest.runtime_observations_hash is not None:
        observation = (
            root / "runtime-observations" / f"{manifest.runtime_observations_hash}.json"
        )
        if (
            not observation.is_file()
            or _file_sha256(observation) != manifest.runtime_observations_hash
        ):
            raise M7PackageError("m7_package_runtime_observations_invalid")
    base = manifest.model_dump(mode="python", exclude={"package_sha256"})
    if _package_hash(resolved_directory, base) != manifest.package_sha256:
        raise M7PackageError("m7_package_hash_mismatch")
    return manifest


def _manifest_payload(
    build_spec: M7PackageBuildSpec,
    entries: Sequence[M7PackageArtifact],
    *,
    runtime_hash: str | None,
) -> dict[str, object]:
    payload = build_spec.model_dump(mode="python")
    payload.update(
        {
            "schema_version": M7_PACKAGE_SCHEMA_VERSION,
            "label_threshold_artifacts": tuple(
                item for item in entries if item.category == "label_threshold"
            ),
            "model_artifacts": tuple(
                item for item in entries if item.category == "model"
            ),
            "calibration_artifacts": tuple(
                item for item in entries if item.category == "calibration"
            ),
            "explanation_artifacts": tuple(
                item for item in entries if item.category == "explanation"
            ),
            "runtime_observations_hash": runtime_hash,
        }
    )
    return payload


def _validate_artifact_inputs(artifacts: Sequence[M7PackageArtifactInput]) -> None:
    if not artifacts or len({item.artifact_id for item in artifacts}) != len(artifacts):
        raise M7PackageError("m7_package_artifact_inputs_invalid")
    categories = {item.category for item in artifacts}
    if categories != {"label_threshold", "model", "calibration", "explanation"}:
        raise M7PackageError("m7_package_artifact_categories_incomplete")


def _publish_runtime_observations(
    root: Path,
    observations: Mapping[str, object] | None,
) -> str | None:
    if observations is None:
        return None
    if not observations:
        raise M7PackageError("m7_runtime_observations_empty")
    try:
        payload = _canonical_json(dict(observations))
    except (TypeError, ValueError) as error:
        raise M7PackageError("m7_runtime_observations_invalid") from error
    digest = hashlib.sha256(payload).hexdigest()
    directory = root / "runtime-observations"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}.json"
    if target.is_file():
        if target.read_bytes() != payload:
            raise M7PackageError("m7_runtime_observations_hash_conflict")
        return digest
    temporary = directory / f".{digest}.{uuid4().hex}.tmp"
    temporary.write_bytes(payload)
    os.replace(temporary, target)
    return digest


def _package_hash(
    package_directory: Path, manifest_without_hash: Mapping[str, object]
) -> str:
    digest = hashlib.sha256()
    digest.update(_canonical_json(dict(manifest_without_hash)))
    artifacts = _artifact_payloads(manifest_without_hash)
    for artifact in sorted(artifacts, key=lambda item: str(item["relative_path"])):
        relative_path = str(artifact["relative_path"])
        _validate_relative_path(relative_path)
        path = package_directory / Path(*PurePosixPath(relative_path).parts)
        digest.update(relative_path.encode("utf-8"))
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def _artifact_payloads(
    manifest: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    rows: list[Mapping[str, object]] = []
    for name in (
        "label_threshold_artifacts",
        "model_artifacts",
        "calibration_artifacts",
        "explanation_artifacts",
    ):
        values = manifest.get(name)
        if not isinstance(values, (list, tuple)):
            raise M7PackageError("m7_package_artifact_manifest_invalid")
        for value in values:
            if isinstance(value, M7PackageArtifact):
                rows.append(value.model_dump(mode="python"))
                continue
            if not isinstance(value, Mapping):
                raise M7PackageError("m7_package_artifact_manifest_invalid")
            rows.append(value)
    return tuple(rows)


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("m7 package artifact relative_path is unsafe")


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            to_jsonable_python(value),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = [
    "M7PackageArtifact",
    "M7PackageArtifactInput",
    "M7PackageBuildSpec",
    "M7PackageError",
    "M7PackageManifest",
    "M7RuntimeCompatibility",
    "PublishedM7Package",
    "detect_m7_runtime_compatibility",
    "load_m7_package",
    "publish_m7_package",
    "recover_m7_package_staging",
    "verify_m7_runtime_compatibility",
]
