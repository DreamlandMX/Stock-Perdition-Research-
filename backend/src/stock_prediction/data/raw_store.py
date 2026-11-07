"""Immutable Parquet batch storage with checksum manifests."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from filelock import FileLock, Timeout
from pydantic import TypeAdapter, ValidationError

from stock_prediction.contracts.ingestion import (
    BatchCommitState,
    DataQualityReport,
    RawBatchManifest,
)
from stock_prediction.contracts.market_data import MarketBar, Timeframe
from stock_prediction.contracts.time import UtcDatetime

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

MARKET_BAR_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timeframe", pa.string(), nullable=False),
        pa.field("session", pa.string(), nullable=False),
        pa.field("session_date", pa.date32(), nullable=False),
        pa.field("bar_start", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("open", pa.float64(), nullable=False),
        pa.field("high", pa.float64(), nullable=False),
        pa.field("low", pa.float64(), nullable=False),
        pa.field("close", pa.float64(), nullable=False),
        pa.field("volume", pa.int64(), nullable=False),
        pa.field("turnover", pa.float64(), nullable=True),
        pa.field("source_available_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("raw_batch_id", pa.string(), nullable=False),
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("dataset_revision", pa.string(), nullable=False),
        pa.field("revision_no", pa.int32(), nullable=False),
    ]
)


class RawBatchError(RuntimeError):
    """Base class for sanitized raw-store failures."""


class RawBatchConflictError(RawBatchError):
    """A batch identifier was reused with different evidence."""


class RawBatchIntegrityError(RawBatchError):
    """Raw evidence is incomplete, malformed, or corrupted."""


@dataclass(frozen=True, slots=True)
class StagedRawBatch:
    """A staged batch or an idempotently discovered committed batch."""

    manifest: RawBatchManifest
    staging_path: Path
    already_committed: bool = False

    @property
    def data_path(self) -> Path:
        return self.staging_path / "data.parquet"

    @property
    def manifest_path(self) -> Path:
        return self.staging_path / "manifest.json"


class RawBatchStore:
    """Publish immutable Parquet batches under a workspace-local root."""

    def __init__(
        self,
        root: Path,
        *,
        phase_hook: Callable[[str], None] = lambda _phase: None,
    ) -> None:
        self.root = root.resolve()
        self.phase_hook = phase_hook
        self.staging_root = self.root / ".staging"
        self.committed_root = self.root / "committed"
        self.quarantine_root = self.root / "quarantine"
        self.lock_root = self.root / ".locks"
        for directory in (
            self.staging_root,
            self.committed_root,
            self.quarantine_root,
            self.lock_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def stage_market_bars(
        self,
        rows: list[MarketBar],
        *,
        task_id: str,
        batch_id: str,
        request_start: date,
        request_end: date,
        request_fingerprint: str,
        opend_version: str,
        sdk_version: str,
        created_at: UtcDatetime | str,
    ) -> StagedRawBatch:
        if not _SAFE_ID.fullmatch(batch_id) or not _SAFE_ID.fullmatch(task_id):
            raise RawBatchIntegrityError("invalid batch metadata")
        if not rows:
            raise RawBatchIntegrityError("market-bar batch must not be empty")
        if any(row.raw_batch_id != batch_id for row in rows):
            raise RawBatchIntegrityError("raw_batch_id does not match batch metadata")

        first = rows[0]
        partition = (
            first.symbol,
            first.timeframe,
            first.session_date.year,
            first.session_date.month,
            first.schema_version,
            first.dataset_revision,
        )
        if any(
            (
                row.symbol,
                row.timeframe,
                row.session_date.year,
                row.session_date.month,
                row.schema_version,
                row.dataset_revision,
            )
            != partition
            for row in rows
        ):
            raise RawBatchIntegrityError("market-bar batch crosses a storage partition")

        relative_data_path = PurePosixPath(
            "market_bars",
            "US",
            first.timeframe.value,
            first.symbol,
            f"{first.session_date.year:04d}",
            f"{first.session_date.month:02d}",
            batch_id,
            "data.parquet",
        ).as_posix()
        lock = self.batch_lock(batch_id)
        with lock:
            return self._stage_table(
                self._market_bar_table(rows),
                task_id=task_id,
                batch_id=batch_id,
                symbol=first.symbol,
                timeframe=first.timeframe,
                schema_version=first.schema_version,
                dataset_revision=first.dataset_revision,
                request_start=request_start,
                request_end=request_end,
                request_fingerprint=request_fingerprint,
                opend_version=opend_version,
                sdk_version=sdk_version,
                created_at=created_at,
                relative_data_path=relative_data_path,
            )

    def _stage_table(
        self,
        table: pa.Table,
        *,
        task_id: str,
        batch_id: str,
        symbol: str,
        timeframe: Timeframe,
        schema_version: str,
        dataset_revision: str,
        request_start: date,
        request_end: date,
        request_fingerprint: str,
        opend_version: str,
        sdk_version: str,
        created_at: UtcDatetime | str,
        relative_data_path: str,
    ) -> StagedRawBatch:
        temporary = self.staging_root / f".{batch_id}.{uuid4().hex}.tmp"
        staged_path = self.staging_root / batch_id
        if staged_path.exists():
            raise RawBatchConflictError("batch ID already has staged evidence")
        temporary.mkdir(parents=False)
        try:
            data_path = temporary / "data.parquet"
            pq.write_table(
                table,
                data_path,
                compression="zstd",
                use_dictionary=True,
                write_statistics=True,
                coerce_timestamps="us",
            )
            _fsync_file(data_path)
            sha256 = _sha256(data_path)
            bar_start = table.column("bar_start")
            try:
                manifest = RawBatchManifest(
                    batch_id=batch_id,
                    task_id=task_id,
                    dataset="market_bars",
                    market="US",
                    symbol=symbol,
                    timeframe=timeframe,
                    schema_version=schema_version,
                    dataset_revision=dataset_revision,
                    request_start=request_start,
                    request_end=request_end,
                    request_fingerprint=request_fingerprint,
                    row_count=table.num_rows,
                    min_event_at=pc.min(bar_start).as_py(),
                    max_event_at=pc.max(bar_start).as_py(),
                    sha256=sha256,
                    relative_data_path=relative_data_path,
                    opend_version=opend_version,
                    sdk_version=sdk_version,
                    created_at=TypeAdapter(UtcDatetime).validate_python(created_at),
                    commit_state=BatchCommitState.STAGED,
                    committed_at=None,
                )
            except ValidationError as error:
                raise RawBatchIntegrityError("invalid batch metadata") from error
            _write_manifest(temporary / "manifest.json", manifest)

            existing_finalized = self._finalized_by_id(
                batch_id, already_locked=True
            )
            if existing_finalized is not None:
                if existing_finalized.commit_state is not BatchCommitState.COMMITTED:
                    raise RawBatchConflictError(
                        "batch ID already owns quarantined evidence"
                    )
                self.verify(existing_finalized)
                if _manifest_evidence(existing_finalized) != _manifest_evidence(
                    manifest
                ):
                    raise RawBatchConflictError(
                        "batch ID conflicts with committed evidence"
                    )
                shutil.rmtree(temporary)
                committed_path = self._committed_directory(existing_finalized)
                return StagedRawBatch(
                    manifest=existing_finalized,
                    staging_path=committed_path,
                    already_committed=True,
                )

            committed_path = self._committed_directory(manifest)
            if committed_path.exists():
                self._finalize_pending(
                    committed_path, BatchCommitState.COMMITTED
                )
                existing = self._load_manifest(committed_path / "manifest.json")
                self.verify(existing)
                if _manifest_evidence(existing) != _manifest_evidence(manifest):
                    raise RawBatchConflictError(
                        "batch ID conflicts with committed evidence"
                    )
                shutil.rmtree(temporary)
                return StagedRawBatch(
                    manifest=existing,
                    staging_path=committed_path,
                    already_committed=True,
                )

            os.replace(temporary, staged_path)
            return StagedRawBatch(manifest=manifest, staging_path=staged_path)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

    def commit(
        self,
        staged: StagedRawBatch,
        *,
        quality_report: DataQualityReport,
        committed_at: UtcDatetime | str,
    ) -> RawBatchManifest:
        if staged.already_committed:
            existing_report = self.quality_report(staged.manifest)
            if existing_report != quality_report:
                raise RawBatchConflictError(
                    "batch ID conflicts with committed quality evidence"
                )
            return self.verify(staged.manifest)
        if (
            quality_report.batch_id != staged.manifest.batch_id
            or quality_report.input_rows != staged.manifest.row_count
            or quality_report.quarantined
        ):
            raise RawBatchIntegrityError("quality report does not approve this batch")
        batch_id = staged.manifest.batch_id
        lock = self.batch_lock(batch_id)
        with lock:
            if not staged.staging_path.is_dir():
                raise RawBatchIntegrityError("staged batch is missing")
            self._verify_at(
                staged.manifest, staged.staging_path, require_quality=False
            )
            try:
                quality_sha256 = _sha256_content(
                    _quality_report_bytes(quality_report)
                )
                committed = RawBatchManifest(
                    **{
                        **staged.manifest.model_dump(),
                        "commit_state": BatchCommitState.COMMITTED,
                        "committed_at": committed_at,
                        "quality_report_sha256": quality_sha256,
                    }
                )
            except ValidationError as error:
                raise RawBatchIntegrityError("invalid commit metadata") from error
            target = self._committed_directory(committed)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                self._finalize_pending(target, BatchCommitState.COMMITTED)
                existing = self._load_manifest(target / "manifest.json")
                self.verify(existing)
                if _manifest_evidence(existing) != _manifest_evidence(committed):
                    raise RawBatchConflictError(
                        "batch ID conflicts with committed evidence"
                    )
                if self.quality_report(existing) != quality_report:
                    raise RawBatchConflictError(
                        "batch ID conflicts with committed quality evidence"
                    )
                shutil.rmtree(staged.staging_path)
                return existing
            _write_quality_report(
                staged.staging_path / "quality-report.json", quality_report
            )
            _write_manifest(
                staged.staging_path / "manifest.pending.json", committed
            )
            os.replace(staged.staging_path, target)
            self.phase_hook("commit_directory_published")
            os.replace(
                target / "manifest.pending.json", target / "manifest.json"
            )
            return self.verify(committed)

    def quarantine(
        self,
        staged: StagedRawBatch,
        *,
        quality_report: DataQualityReport,
    ) -> RawBatchManifest:
        if staged.already_committed:
            raise RawBatchConflictError("committed evidence cannot be quarantined")
        if (
            quality_report.batch_id != staged.manifest.batch_id
            or quality_report.input_rows != staged.manifest.row_count
            or not quality_report.quarantined
        ):
            raise RawBatchIntegrityError("quality report does not quarantine this batch")
        lock = self.batch_lock(staged.manifest.batch_id)
        with lock:
            self._verify_at(
                staged.manifest, staged.staging_path, require_quality=False
            )
            quarantined = RawBatchManifest(
                **{
                    **staged.manifest.model_dump(),
                    "commit_state": BatchCommitState.QUARANTINED,
                    "committed_at": None,
                    "quality_report_sha256": _sha256_content(
                        _quality_report_bytes(quality_report)
                    ),
                }
            )
            target = self._quarantine_directory(quarantined)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise RawBatchConflictError(
                    "batch ID already owns quarantined evidence"
                )
            _write_quality_report(
                staged.staging_path / "quality-report.json", quality_report
            )
            _write_manifest(
                staged.staging_path / "manifest.pending.json", quarantined
            )
            os.replace(staged.staging_path, target)
            self.phase_hook("quarantine_directory_published")
            os.replace(
                target / "manifest.pending.json", target / "manifest.json"
            )
            return self.verify_quarantined(quarantined)

    def verify(self, manifest: RawBatchManifest) -> RawBatchManifest:
        directory = self._committed_directory(manifest)
        if manifest.commit_state is not BatchCommitState.COMMITTED:
            raise RawBatchIntegrityError("only committed evidence can be verified")
        return self._verify_at(manifest, directory)

    def verify_quarantined(self, manifest: RawBatchManifest) -> RawBatchManifest:
        if manifest.commit_state is not BatchCommitState.QUARANTINED:
            raise RawBatchIntegrityError("evidence is not quarantined")
        return self._verify_at(manifest, self._quarantine_directory(manifest))

    def _verify_at(
        self,
        manifest: RawBatchManifest,
        directory: Path,
        *,
        require_quality: bool = True,
    ) -> RawBatchManifest:
        data_path = directory / "data.parquet"
        manifest_path = directory / "manifest.json"
        if not data_path.is_file() or not manifest_path.is_file():
            raise RawBatchIntegrityError("batch evidence is incomplete")
        persisted = self._load_manifest(manifest_path)
        if persisted != manifest:
            raise RawBatchIntegrityError("manifest does not match expected evidence")
        self._verify_payload(manifest, directory, require_quality=require_quality)
        return manifest

    def _verify_payload(
        self,
        manifest: RawBatchManifest,
        directory: Path,
        *,
        require_quality: bool,
    ) -> None:
        data_path = directory / "data.parquet"
        if not data_path.is_file():
            raise RawBatchIntegrityError("batch evidence is incomplete")
        if require_quality:
            quality_path = directory / "quality-report.json"
            report = self._load_quality_report(quality_path)
            expected_quarantine = (
                manifest.commit_state is BatchCommitState.QUARANTINED
            )
            if (
                report.batch_id != manifest.batch_id
                or report.input_rows != manifest.row_count
                or report.quarantined != expected_quarantine
            ):
                raise RawBatchIntegrityError("quality report does not approve this batch")
            if (
                manifest.quality_report_sha256 is None
                or _sha256(quality_path) != manifest.quality_report_sha256
            ):
                raise RawBatchIntegrityError("quality report checksum mismatch")
        if _sha256(data_path) != manifest.sha256:
            raise RawBatchIntegrityError("Parquet checksum mismatch")
        table = pq.read_table(data_path)
        if table.schema != MARKET_BAR_SCHEMA:
            raise RawBatchIntegrityError("Parquet schema mismatch")
        if table.num_rows != manifest.row_count:
            raise RawBatchIntegrityError("Parquet row count mismatch")
        bar_start = table.column("bar_start")
        if (
            pc.min(bar_start).as_py() != manifest.min_event_at
            or pc.max(bar_start).as_py() != manifest.max_event_at
        ):
            raise RawBatchIntegrityError("Parquet event bounds mismatch")

    def data_path(self, manifest: RawBatchManifest) -> Path:
        return self._committed_directory(manifest) / "data.parquet"

    def quality_report(self, manifest: RawBatchManifest) -> DataQualityReport:
        if manifest.commit_state is BatchCommitState.COMMITTED:
            directory = self._committed_directory(manifest)
        elif manifest.commit_state is BatchCommitState.QUARANTINED:
            directory = self._quarantine_directory(manifest)
        else:
            raise RawBatchIntegrityError("quality evidence is not finalized")
        return self._load_quality_report(directory / "quality-report.json")

    def staged_batch(self, batch_id: str) -> StagedRawBatch:
        if not _SAFE_ID.fullmatch(batch_id):
            raise RawBatchIntegrityError("invalid batch metadata")
        directory = self.staging_root / batch_id
        manifest = self._load_manifest(directory / "manifest.json")
        if manifest.commit_state is not BatchCommitState.STAGED:
            raise RawBatchIntegrityError("staging manifest has an invalid state")
        self._verify_at(manifest, directory, require_quality=False)
        return StagedRawBatch(manifest=manifest, staging_path=directory)

    def find_committed(self, batch_id: str) -> RawBatchManifest | None:
        matches = [
            manifest
            for manifest in self.committed_manifests()
            if manifest.batch_id == batch_id
        ]
        if len(matches) > 1:
            raise RawBatchIntegrityError("batch ID has multiple committed locations")
        return matches[0] if matches else None

    def _finalized_by_id(
        self, batch_id: str, *, already_locked: bool = False
    ) -> RawBatchManifest | None:
        matches = [
            manifest
            for manifest in (
                *self.committed_manifests(
                    _already_locked_batch_id=batch_id if already_locked else None
                ),
                *self.quarantined_manifests(
                    _already_locked_batch_id=batch_id if already_locked else None
                ),
            )
            if manifest.batch_id == batch_id
        ]
        if len(matches) > 1:
            raise RawBatchIntegrityError("batch ID has multiple finalized locations")
        return matches[0] if matches else None

    def find_quarantined(self, batch_id: str) -> RawBatchManifest | None:
        manifests = self.quarantined_manifests()
        matches = [manifest for manifest in manifests if manifest.batch_id == batch_id]
        if len(matches) > 1:
            raise RawBatchIntegrityError("batch ID has multiple quarantine locations")
        if not matches:
            return None
        return self.verify_quarantined(matches[0])

    def committed_manifests(
        self, *, _already_locked_batch_id: str | None = None
    ) -> list[RawBatchManifest]:
        self._finalize_all_pending(
            self.committed_root,
            BatchCommitState.COMMITTED,
            already_locked_batch_id=_already_locked_batch_id,
        )
        manifests = [
            self._load_manifest(path)
            for path in self.committed_root.rglob("manifest.json")
        ]
        manifests.sort(key=lambda item: item.batch_id)
        return [self.verify(manifest) for manifest in manifests]

    def quarantined_manifests(
        self, *, _already_locked_batch_id: str | None = None
    ) -> list[RawBatchManifest]:
        self._finalize_all_pending(
            self.quarantine_root,
            BatchCommitState.QUARANTINED,
            already_locked_batch_id=_already_locked_batch_id,
        )
        manifests = [
            self._load_manifest(path)
            for path in self.quarantine_root.rglob("manifest.json")
        ]
        manifests.sort(key=lambda item: item.batch_id)
        return [self.verify_quarantined(manifest) for manifest in manifests]

    def cleanup_staging(
        self,
        *,
        created_before: UtcDatetime | str,
        is_batch_protected: Callable[[str], bool],
    ) -> list[str]:
        cutoff = TypeAdapter(UtcDatetime).validate_python(created_before)
        removed: list[str] = []
        for path in sorted(self.staging_root.iterdir()):
            if not path.is_dir():
                continue
            if path.name.startswith("."):
                match = re.fullmatch(
                    r"\.([A-Za-z0-9][A-Za-z0-9._-]{0,127})\.[0-9a-f]+\.tmp",
                    path.name,
                )
                if match is None:
                    continue
                modified_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
                if modified_at >= cutoff:
                    continue
                batch_id = match.group(1)
                lock = self.batch_lock(batch_id, timeout=0)
                try:
                    with lock:
                        if path.exists() and not is_batch_protected(batch_id):
                            shutil.rmtree(path)
                            removed.append(path.name)
                except Timeout:
                    continue
                continue
            if not _SAFE_ID.fullmatch(path.name):
                continue
            try:
                manifest = self._load_manifest(path / "manifest.json")
            except RawBatchIntegrityError:
                continue
            if manifest.created_at >= cutoff:
                continue
            lock = self.batch_lock(path.name, timeout=0)
            try:
                with lock:
                    if path.exists() and not is_batch_protected(path.name):
                        shutil.rmtree(path)
                        removed.append(path.name)
            except Timeout:
                continue
        return removed

    def batch_lock(self, batch_id: str, *, timeout: float = -1) -> FileLock:
        if not _SAFE_ID.fullmatch(batch_id):
            raise RawBatchIntegrityError("invalid batch metadata")
        return FileLock(str(self.lock_root / f"{batch_id}.lock"), timeout=timeout)

    def _committed_directory(self, manifest: RawBatchManifest) -> Path:
        relative = PurePosixPath(manifest.relative_data_path)
        return self.committed_root.joinpath(*relative.parts).parent

    def _quarantine_directory(self, manifest: RawBatchManifest) -> Path:
        relative = PurePosixPath(manifest.relative_data_path)
        return self.quarantine_root.joinpath(*relative.parts).parent

    def _finalize_all_pending(
        self,
        root: Path,
        expected_state: BatchCommitState,
        *,
        already_locked_batch_id: str | None = None,
    ) -> None:
        for pending_path in sorted(root.rglob("manifest.pending.json")):
            pending = self._load_manifest(pending_path)
            if pending.batch_id == already_locked_batch_id:
                self._finalize_pending(pending_path.parent, expected_state)
                continue
            lock = self.batch_lock(pending.batch_id)
            with lock:
                self._finalize_pending(pending_path.parent, expected_state)

    def _finalize_pending(
        self, directory: Path, expected_state: BatchCommitState
    ) -> None:
        pending_path = directory / "manifest.pending.json"
        if not pending_path.is_file():
            return
        pending = self._load_manifest(pending_path)
        if pending.commit_state is not expected_state:
            raise RawBatchIntegrityError("pending manifest has an invalid state")
        current = self._load_manifest(directory / "manifest.json")
        if current.commit_state is expected_state:
            if current != pending:
                raise RawBatchIntegrityError("pending manifest conflicts with final evidence")
        elif (
            current.commit_state is not BatchCommitState.STAGED
            or _manifest_evidence(current) != _manifest_evidence(pending)
        ):
            raise RawBatchIntegrityError("pending manifest conflicts with staged evidence")
        self._verify_payload(pending, directory, require_quality=True)
        os.replace(pending_path, directory / "manifest.json")

    @staticmethod
    def _load_manifest(path: Path) -> RawBatchManifest:
        try:
            return RawBatchManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as error:
            raise RawBatchIntegrityError("manifest is unreadable") from error

    @staticmethod
    def _load_quality_report(path: Path) -> DataQualityReport:
        try:
            return DataQualityReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as error:
            raise RawBatchIntegrityError("quality report is unreadable") from error

    @staticmethod
    def _market_bar_table(rows: list[MarketBar]) -> pa.Table:
        payload = [
            {
                "symbol": row.symbol,
                "timeframe": row.timeframe.value,
                "session": row.session.value,
                "session_date": row.session_date,
                "bar_start": row.bar_start,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "turnover": row.turnover,
                "source_available_at": row.source_available_at,
                "ingested_at": row.ingested_at,
                "raw_batch_id": row.raw_batch_id,
                "schema_version": row.schema_version,
                "dataset_revision": row.dataset_revision,
                "revision_no": row.revision_no,
            }
            for row in rows
        ]
        return pa.Table.from_pylist(payload, schema=MARKET_BAR_SCHEMA)


def _manifest_evidence(manifest: RawBatchManifest) -> dict[str, object]:
    return manifest.model_dump(
        exclude={"commit_state", "committed_at", "quality_report_sha256"}
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(path: Path, manifest: RawBatchManifest) -> None:
    temporary = path.with_suffix(".json.tmp")
    _durable_write_bytes(
        temporary, manifest.model_dump_json(indent=2).encode("utf-8")
    )
    os.replace(temporary, path)


def _write_quality_report(path: Path, report: DataQualityReport) -> None:
    temporary = path.with_suffix(".json.tmp")
    _durable_write_bytes(temporary, _quality_report_bytes(report))
    os.replace(temporary, path)


def _quality_report_bytes(report: DataQualityReport) -> bytes:
    return report.model_dump_json(indent=2).encode("utf-8")


def _sha256_content(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _durable_write_bytes(path: Path, content: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())
