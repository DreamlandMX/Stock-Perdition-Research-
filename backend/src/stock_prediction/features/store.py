"""Checksum-verified immutable Parquet storage for feature cache entries."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
import shutil
from time import sleep
from typing import Any

from filelock import FileLock
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import TypeAdapter

from stock_prediction.contracts.research import (
    ArtifactStatus,
    FeatureArtifact,
    Sha256,
    hash_feature_values,
)
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.features.dag import FeatureCacheKey, FeatureInvalidation


class FeatureArtifactError(RuntimeError):
    """Base class for feature artifact storage failures."""


class FeatureArtifactConflictError(FeatureArtifactError):
    """An immutable cache identity already owns different evidence."""


class FeatureArtifactCorruptError(FeatureArtifactError):
    """Stored metadata, schema, or content does not match its evidence."""


class FeatureArtifactInvalidatedError(FeatureArtifactError):
    """A cache entry is retained for audit but cannot be reused."""


class FeatureArtifactStore:
    """Local immutable feature store with append-only invalidation records."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.root / ".feature-store.lock"))

    def put(
        self,
        key: FeatureCacheKey,
        table: pa.Table,
        *,
        input_hash: Sha256,
        dependency_hashes: dict[str, Sha256],
        source_max_available_at: UtcDatetime | str,
        generated_at: UtcDatetime | str,
        source_max_ingested_at: UtcDatetime | str | None = None,
    ) -> FeatureArtifact:
        source_max = TypeAdapter(UtcDatetime).validate_python(
            source_max_available_at
        )
        generated = TypeAdapter(UtcDatetime).validate_python(generated_at)
        _validate_table_key(table, key)
        source_ingested_max = (
            TypeAdapter(UtcDatetime).validate_python(source_max_ingested_at)
            if source_max_ingested_at is not None
            else max(table.column("source_max_ingested_at").to_pylist())
        )
        table_source_max = max(table.column("source_max_available_at").to_pylist())
        if table_source_max != source_max:
            raise FeatureArtifactConflictError(
                "source_max_available_at does not match feature table"
            )
        table_ingested_max = max(
            table.column("source_max_ingested_at").to_pylist()
        )
        if table_ingested_max != source_ingested_max:
            raise FeatureArtifactConflictError(
                "source_max_ingested_at does not match feature table"
            )
        parquet_bytes = _parquet_bytes(table)
        content_hash = hashlib.sha256(parquet_bytes).hexdigest()
        schema_hash = _schema_hash(table.schema)
        feature_values_hashes = (
            tuple(sorted(set(table.column("feature_values_hash").to_pylist())))
            if "feature_values_hash" in table.column_names
            else ()
        )
        artifact = FeatureArtifact(
            artifact_id=f"feature-{key.cache_id[:24]}",
            dataset_revision=key.dataset_revision,
            feature_set_version=key.feature_set_version,
            symbol=key.symbol,
            as_of_start=key.as_of,
            as_of_end=key.as_of,
            horizon=key.horizon,
            input_hash=input_hash,
            content_hash=content_hash,
            dependency_hashes=dependency_hashes,
            schema_hash=schema_hash,
            row_count=table.num_rows,
            generated_at=generated,
            status=ArtifactStatus.CURRENT,
            affected_start=None,
            affected_end=None,
            invalidation_reason=None,
            source_max_available_at=source_max,
            source_max_ingested_at=source_ingested_max,
            feature_values_hashes=feature_values_hashes,
        )
        directory = self._directory(key)
        metadata = {
            "cache_key": key.model_dump(mode="json"),
            "artifact": artifact.model_dump(mode="json"),
        }
        metadata_bytes = json.dumps(
            metadata,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        with self._lock:
            directory.parent.mkdir(parents=True, exist_ok=True)
            metadata_path = directory / "artifact.json"
            data_path = directory / "artifact.parquet"
            if (directory / "invalidation.json").exists():
                raise FeatureArtifactInvalidatedError(
                    "invalidated feature artifact cache key cannot be reused"
                )
            if metadata_path.exists() or data_path.exists():
                if not metadata_path.exists() or not data_path.exists():
                    raise FeatureArtifactCorruptError(
                        "feature artifact publication is incomplete"
                    )
                existing = self._read_current(key, allow_invalidated=True)[1]
                if _same_immutable_content(existing, artifact):
                    return existing
                raise FeatureArtifactConflictError(
                    "feature artifact cache key already exists with different evidence"
                )
            staging = directory.with_name(directory.name + ".staging")
            if staging.exists():
                staged_data = staging / "artifact.parquet"
                staged_metadata = staging / "artifact.json"
                if (
                    staged_data.is_file()
                    and staged_metadata.is_file()
                    and staged_data.read_bytes() == parquet_bytes
                    and staged_metadata.read_bytes() == metadata_bytes
                ):
                    _promote_directory(staging, directory)
                    return artifact
                _remove_tree_with_retry(staging)
            staging.mkdir()
            _write_atomic(staging / "artifact.parquet", parquet_bytes)
            _write_atomic(staging / "artifact.json", metadata_bytes)
            _promote_directory(staging, directory)
        return artifact

    def get(self, key: FeatureCacheKey) -> tuple[pa.Table, FeatureArtifact]:
        with self._lock:
            self._recover_staging_unlocked()
            return self._read_current(key, allow_invalidated=False)

    def invalidate(
        self,
        key: FeatureCacheKey,
        *,
        affected_start: UtcDatetime | str,
        affected_end: UtcDatetime | str,
        reason: str,
    ) -> FeatureArtifact:
        start = TypeAdapter(UtcDatetime).validate_python(affected_start)
        end = TypeAdapter(UtcDatetime).validate_python(affected_end)
        if not start <= key.as_of <= end:
            raise FeatureArtifactConflictError(
                "invalidation range does not intersect cache key as_of"
            )
        with self._lock:
            return self._invalidate_unlocked(key, start, end, reason)

    def apply_invalidation(
        self,
        invalidation: FeatureInvalidation,
        *,
        reason: str,
    ) -> tuple[FeatureArtifact, ...]:
        """Resolve a DAG range to exact cache keys and append invalidations."""

        invalidated: list[FeatureArtifact] = []
        with self._lock:
            self._recover_staging_unlocked()
            for metadata_path in sorted((self.root / "artifacts").rglob("artifact.json")):
                if metadata_path.parent.name.endswith(".staging"):
                    continue
                try:
                    payload = json.loads(metadata_path.read_text("utf-8"))
                    key = FeatureCacheKey.model_validate_json(
                        json.dumps(payload["cache_key"]), strict=True
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise FeatureArtifactCorruptError(
                        "feature artifact metadata is invalid"
                    ) from error
                if (
                    key.node_name != invalidation.node_name
                    or key.symbol != invalidation.symbol
                    or key.dataset_revision != invalidation.dataset_revision
                    or key.feature_set_version != invalidation.feature_set_version
                    or not invalidation.affected_start
                    <= key.as_of.date()
                    <= invalidation.affected_end
                    or (
                        invalidation.horizons is not None
                        and key.horizon not in invalidation.horizons
                    )
                ):
                    continue
                invalidated.append(
                    self._invalidate_unlocked(key, key.as_of, key.as_of, reason)
                )
        return tuple(invalidated)

    def _recover_staging_unlocked(self) -> None:
        artifacts_root = self.root / "artifacts"
        if not artifacts_root.exists():
            return
        for staging in sorted(artifacts_root.rglob("*.staging")):
            if not staging.is_dir():
                continue
            final = staging.with_name(staging.name.removesuffix(".staging"))
            if final.exists():
                _remove_tree_with_retry(staging)
                continue
            metadata_path = staging / "artifact.json"
            data_path = staging / "artifact.parquet"
            try:
                payload = json.loads(metadata_path.read_text("utf-8"))
                key = FeatureCacheKey.model_validate_json(
                    json.dumps(payload["cache_key"]), strict=True
                )
                artifact = FeatureArtifact.model_validate_json(
                    json.dumps(payload["artifact"]), strict=True
                )
                content = data_path.read_bytes()
                if (
                    key.cache_id != final.name
                    or hashlib.sha256(content).hexdigest() != artifact.content_hash
                ):
                    raise ValueError("staging evidence mismatch")
                table = pq.read_table(pa.BufferReader(content))
                if (
                    _schema_hash(table.schema) != artifact.schema_hash
                    or table.num_rows != artifact.row_count
                ):
                    raise ValueError("staging table evidence mismatch")
                _validate_table_key(table, key)
                _validate_artifact_table_evidence(table, artifact)
            except (FeatureArtifactError, KeyError, TypeError, ValueError, OSError):
                _remove_tree_with_retry(staging)
                continue
            _promote_directory(staging, final)

    def _invalidate_unlocked(
        self,
        key: FeatureCacheKey,
        start: datetime,
        end: datetime,
        reason: str,
    ) -> FeatureArtifact:
        _, current = self._read_current(key, allow_invalidated=True)
        invalidated = current.model_copy(
            update={
                "status": ArtifactStatus.INVALIDATED,
                "affected_start": start,
                "affected_end": end,
                "invalidation_reason": reason,
            }
        )
        invalidated = FeatureArtifact.model_validate(
            invalidated.model_dump(), strict=True
        )
        path = self._directory(key) / "invalidation.json"
        payload = invalidated.model_dump_json().encode("utf-8")
        if path.exists():
            existing = FeatureArtifact.model_validate_json(
                path.read_bytes(), strict=True
            )
            if existing == invalidated:
                return existing
            raise FeatureArtifactConflictError(
                "feature artifact already has a different invalidation"
            )
        _write_atomic(path, payload)
        return invalidated

    def _read_current(
        self, key: FeatureCacheKey, *, allow_invalidated: bool
    ) -> tuple[pa.Table, FeatureArtifact]:
        directory = self._directory(key)
        invalidation_path = directory / "invalidation.json"
        if invalidation_path.exists() and not allow_invalidated:
            raise FeatureArtifactInvalidatedError(
                f"feature artifact {key.cache_id} is invalidated"
            )
        metadata_path = directory / "artifact.json"
        data_path = directory / "artifact.parquet"
        if not metadata_path.is_file() or not data_path.is_file():
            raise FileNotFoundError(f"feature artifact {key.cache_id} does not exist")
        try:
            payload: dict[str, Any] = json.loads(metadata_path.read_text("utf-8"))
            stored_key = FeatureCacheKey.model_validate_json(
                json.dumps(payload["cache_key"]), strict=True
            )
            artifact = FeatureArtifact.model_validate_json(
                json.dumps(payload["artifact"]), strict=True
            )
        except (KeyError, TypeError, ValueError) as error:
            raise FeatureArtifactCorruptError(
                "feature artifact metadata is invalid"
            ) from error
        if stored_key != key:
            raise FeatureArtifactCorruptError("feature artifact cache key mismatch")
        if (
            artifact.dataset_revision != key.dataset_revision
            or artifact.feature_set_version != key.feature_set_version
            or artifact.symbol != key.symbol
            or artifact.as_of_start != key.as_of
            or artifact.as_of_end != key.as_of
            or artifact.horizon != key.horizon
        ):
            raise FeatureArtifactCorruptError(
                "feature artifact metadata does not match cache key"
            )
        content = data_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != artifact.content_hash:
            raise FeatureArtifactCorruptError("feature artifact content hash mismatch")
        try:
            table = pq.read_table(pa.BufferReader(content))
        except Exception as error:
            raise FeatureArtifactCorruptError(
                "feature artifact Parquet cannot be read"
            ) from error
        if _schema_hash(table.schema) != artifact.schema_hash:
            raise FeatureArtifactCorruptError("feature artifact schema hash mismatch")
        if table.num_rows != artifact.row_count:
            raise FeatureArtifactCorruptError("feature artifact row count mismatch")
        _validate_table_key(table, key)
        _validate_artifact_table_evidence(table, artifact)
        return table, artifact

    def _directory(self, key: FeatureCacheKey) -> Path:
        return self.root / "artifacts" / key.cache_id[:2] / key.cache_id


def _validate_table_key(table: pa.Table, key: FeatureCacheKey) -> None:
    required = {
        "symbol",
        "as_of",
        "horizon",
        "feature_set_version",
        "dataset_revision",
        "source_max_available_at",
        "source_max_ingested_at",
    }
    if not required.issubset(table.column_names):
        raise FeatureArtifactConflictError("feature table is missing cache key columns")
    if table.num_rows == 0:
        raise FeatureArtifactConflictError("feature cache entry cannot be empty")
    expected: dict[str, object] = {
        "symbol": key.symbol,
        "as_of": key.as_of,
        "horizon": key.horizon,
        "feature_set_version": key.feature_set_version,
        "dataset_revision": key.dataset_revision,
    }
    for column, value in expected.items():
        values = table.column(column).to_pylist()
        if any(item != value for item in values):
            raise FeatureArtifactConflictError(
                f"feature table {column} does not match cache key"
            )


def _validate_artifact_table_evidence(
    table: pa.Table, artifact: FeatureArtifact
) -> None:
    feature_values_hashes = (
        tuple(sorted(set(table.column("feature_values_hash").to_pylist())))
        if "feature_values_hash" in table.column_names
        else ()
    )
    if feature_values_hashes != artifact.feature_values_hashes:
        raise FeatureArtifactCorruptError(
            "feature artifact values hash metadata does not match Parquet"
        )
    if {
        "feature_values_hash",
        "values_json",
    }.issubset(table.column_names):
        for values_hash, values_json in zip(
            table.column("feature_values_hash").to_pylist(),
            table.column("values_json").to_pylist(),
            strict=True,
        ):
            try:
                values = json.loads(values_json)
            except (TypeError, ValueError) as error:
                raise FeatureArtifactCorruptError(
                    "feature artifact values JSON is invalid"
                ) from error
            if not isinstance(values, dict) or hash_feature_values(values) != values_hash:
                raise FeatureArtifactCorruptError(
                    "feature artifact row values do not match values hash"
                )
    source_max_available_at = max(
        table.column("source_max_available_at").to_pylist()
    )
    source_max_ingested_at = max(
        table.column("source_max_ingested_at").to_pylist()
    )
    if (
        source_max_available_at != artifact.source_max_available_at
        or source_max_ingested_at != artifact.source_max_ingested_at
    ):
        raise FeatureArtifactCorruptError(
            "feature artifact source maxima do not match Parquet"
        )


def _parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd")
    return bytes(sink.getvalue())


def _schema_hash(schema: pa.Schema) -> str:
    return hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()


def _same_immutable_content(
    existing: FeatureArtifact, proposed: FeatureArtifact
) -> bool:
    return existing.model_dump(exclude={"generated_at"}) == proposed.model_dump(
        exclude={"generated_at"}
    )


def _write_atomic(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _promote_directory(source: Path, destination: Path) -> None:
    """Retry only transient Windows directory locks during atomic publication."""

    for attempt in range(5):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 4:
                raise
            sleep(0.01 * (2**attempt))


def _remove_tree_with_retry(path: Path) -> None:
    """Retry transient Windows locks while preserving permanent failures."""

    for attempt in range(5):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            sleep(0.01 * (2**attempt))
