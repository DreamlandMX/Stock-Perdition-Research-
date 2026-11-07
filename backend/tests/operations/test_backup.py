from datetime import UTC, datetime
from pathlib import Path

import pytest

from stock_prediction.operations.backup import BackupError, LocalBackupStore
from stock_prediction.operations.disk import DiskSpaceGuard, InsufficientDiskSpace


NOW = datetime(2026, 7, 21, 12, tzinfo=UTC)


def guard(*, free: int = 10_000_000) -> DiskSpaceGuard:
    return DiskSpaceGuard(
        minimum_free_bytes=0,
        minimum_free_ratio=0,
        usage=lambda _path: (10_000_000, 10_000_000 - free, free),
    )


def test_raw_and_model_backup_round_trip_is_checksum_bound(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    models = tmp_path / "models"
    (raw / "2026").mkdir(parents=True)
    (models / "releases").mkdir(parents=True)
    (raw / "2026" / "bars.parquet").write_bytes(b"raw-evidence")
    (models / "releases" / "manifest.json").write_text(
        '{"model":"v1"}', encoding="utf-8"
    )
    store = LocalBackupStore(tmp_path / "backups", disk_guard=guard())

    backup = store.create(
        backup_id="backup-20260721-v1",
        sources={"raw": raw, "models": models},
        created_at=NOW,
    )
    manifest = store.verify(backup)
    restored = store.restore(backup, target=tmp_path / "restored")

    assert len(manifest.items) == 2
    assert (restored / "raw" / "2026" / "bars.parquet").read_bytes() == b"raw-evidence"
    assert (restored / "models" / "releases" / "manifest.json").is_file()


def test_corrupt_backup_and_existing_target_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    (source / "bars.parquet").write_bytes(b"good")
    store = LocalBackupStore(tmp_path / "backups", disk_guard=guard())
    backup = store.create(
        backup_id="backup-v1", sources={"raw": source}, created_at=NOW
    )
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(BackupError, match="must not already exist"):
        store.restore(backup, target=existing)

    (backup / "payload" / "raw" / "bars.parquet").write_bytes(b"evil")
    with pytest.raises(BackupError, match="size changed|checksum changed"):
        store.verify(backup)


def test_disk_reserve_blocks_backup_before_publication(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    (source / "bars.parquet").write_bytes(b"payload")
    disk_guard = DiskSpaceGuard(
        minimum_free_bytes=100,
        minimum_free_ratio=0,
        usage=lambda _path: (1_000, 950, 50),
    )
    store = LocalBackupStore(tmp_path / "backups", disk_guard=disk_guard)

    with pytest.raises(InsufficientDiskSpace, match="reserve"):
        store.create(backup_id="backup-v1", sources={"raw": source}, created_at=NOW)
    assert not (tmp_path / "backups" / "backup-v1").exists()
