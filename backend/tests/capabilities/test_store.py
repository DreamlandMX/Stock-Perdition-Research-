"""Tests for immutable capability snapshot persistence."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from multiprocessing.synchronize import Event as EventType
from pathlib import Path
from threading import Barrier

import pytest

from stock_prediction.capabilities.store import JsonCapabilityStore
from stock_prediction.contracts.capability import (
    CapabilitySnapshot,
    CapabilityStatus,
    DataCapability,
    PointInTimeStatus,
)


PROBED_AT = datetime(2026, 7, 10, 22, 0, tzinfo=UTC)


def make_snapshot(
    snapshot_id: str = "snapshot-1",
    *,
    opend_version: str = "10.7.3218",
    probed_at: datetime = PROBED_AT,
    row_count: int = 1,
) -> CapabilitySnapshot:
    capability = DataCapability(
        interface="get_market_snapshot",
        market="US",
        asset_type="stock",
        timeframe=None,
        status=CapabilityStatus.AVAILABLE,
        point_in_time_status=PointInTimeStatus.FORWARD_ONLY,
        probed_at=probed_at,
    )
    return CapabilitySnapshot(
        snapshot_id=snapshot_id,
        probed_at=probed_at,
        opend_version=opend_version,
        sdk_version="10.8.6808",
        capabilities=[capability.model_copy() for _ in range(row_count)],
    )


def immutable_files(root: Path) -> list[Path]:
    return sorted(path for path in root.glob("*.json") if path.name != "latest.json")


def process_save_snapshot(
    root: str,
    worker_number: int,
    start_event: EventType,
) -> None:
    start_event.wait(timeout=30)
    probed_at = datetime(2026, 7, 10, 22, worker_number, tzinfo=UTC)
    JsonCapabilityStore(Path(root)).save(
        make_snapshot(
            "shared-process-id",
            opend_version=f"worker-{worker_number}",
            probed_at=probed_at,
            row_count=500,
        )
    )


def test_save_writes_immutable_timestamped_snapshot_and_deterministic_latest(
    tmp_path: Path,
) -> None:
    store = JsonCapabilityStore(tmp_path)
    snapshot = make_snapshot()

    stored_path = store.save(snapshot)

    assert stored_path.parent == tmp_path
    assert stored_path.name.startswith("20260710T220000.000000Z--")
    assert stored_path.name.endswith(".json")
    assert immutable_files(tmp_path) == [stored_path]
    expected = json.dumps(
        snapshot.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    assert stored_path.read_text(encoding="utf-8") == expected
    assert (tmp_path / "latest.json").read_text(encoding="utf-8") == expected
    assert not list(tmp_path.glob("*.tmp"))


def test_latest_is_replaced_atomically_after_immutable_file_exists(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    store = JsonCapabilityStore(tmp_path)
    replace_calls: list[tuple[Path, Path, bool]] = []
    real_replace = os.replace

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        replace_calls.append(
            (source_path, destination_path, bool(immutable_files(tmp_path)))
        )
        real_replace(source_path, destination_path)

    monkeypatch.setattr(os, "replace", recording_replace)  # type: ignore[attr-defined]

    store.save(make_snapshot())

    assert len(replace_calls) == 1
    source, destination, immutable_exists = replace_calls[0]
    assert source.parent == tmp_path
    assert destination == tmp_path / "latest.json"
    assert immutable_exists
    assert not source.exists()


def test_conflicting_payload_for_existing_snapshot_id_returns_canonical(
    tmp_path: Path,
) -> None:
    store = JsonCapabilityStore(tmp_path)
    original = make_snapshot()
    stored_path = store.save(original)
    original_latest = (tmp_path / "latest.json").read_bytes()

    duplicate_path = store.save(
        make_snapshot(snapshot_id=original.snapshot_id, opend_version="changed")
    )

    assert duplicate_path == stored_path
    assert immutable_files(tmp_path) == [stored_path]
    assert (tmp_path / "latest.json").read_bytes() == original_latest
    assert store.load_latest() == original


def test_same_snapshot_id_and_payload_is_idempotent(tmp_path: Path) -> None:
    store = JsonCapabilityStore(tmp_path)
    snapshot = make_snapshot()

    first_path = store.save(snapshot)
    second_path = store.save(snapshot.model_copy(deep=True))

    assert second_path == first_path
    assert immutable_files(tmp_path) == [first_path]
    assert store.load_latest() == snapshot


def test_new_snapshot_preserves_prior_file_and_updates_latest(tmp_path: Path) -> None:
    store = JsonCapabilityStore(tmp_path)
    first = make_snapshot("snapshot-1")
    second = make_snapshot(
        "snapshot-2", probed_at=datetime(2026, 7, 10, 22, 1, tzinfo=UTC)
    )

    first_path = store.save(first)
    first_bytes = first_path.read_bytes()
    second_path = store.save(second)

    assert first_path != second_path
    assert first_path.read_bytes() == first_bytes
    assert immutable_files(tmp_path) == [first_path, second_path]
    assert store.load_latest() == second


def test_idempotent_save_recovers_missing_latest_file(tmp_path: Path) -> None:
    store = JsonCapabilityStore(tmp_path)
    snapshot = make_snapshot()
    stored_path = store.save(snapshot)
    (tmp_path / "latest.json").unlink()

    duplicate_path = store.save(snapshot)

    assert duplicate_path == stored_path
    assert immutable_files(tmp_path) == [stored_path]
    assert store.load_latest() == snapshot


def test_load_latest_returns_none_when_no_snapshot_exists(tmp_path: Path) -> None:
    store = JsonCapabilityStore(tmp_path)

    assert store.load_latest() is None
    assert not any(tmp_path.iterdir())


def test_default_root_is_runtime_capability_directory() -> None:
    assert JsonCapabilityStore().root == Path("var/capabilities")


def test_latest_never_moves_backwards_when_older_snapshot_is_saved_last(
    tmp_path: Path,
) -> None:
    store = JsonCapabilityStore(tmp_path)
    newer = make_snapshot(
        "newer", probed_at=datetime(2026, 7, 10, 22, 2, tzinfo=UTC)
    )
    older = make_snapshot(
        "older", probed_at=datetime(2026, 7, 10, 22, 1, tzinfo=UTC)
    )

    store.save(newer)
    store.save(older)

    assert len(immutable_files(tmp_path)) == 2
    assert store.load_latest() == newer


@pytest.mark.parametrize(
    "save_order",
    [("snapshot-b", "snapshot-a"), ("snapshot-a", "snapshot-b")],
)
def test_latest_uses_snapshot_id_as_deterministic_timestamp_tie_break(
    tmp_path: Path,
    save_order: tuple[str, str],
) -> None:
    store = JsonCapabilityStore(tmp_path)

    for snapshot_id in save_order:
        store.save(make_snapshot(snapshot_id))

    assert store.load_latest() == make_snapshot("snapshot-b")


def test_concurrent_threads_publish_one_canonical_snapshot_for_same_id(
    tmp_path: Path,
) -> None:
    workers = 8
    barrier = Barrier(workers)
    snapshots = [
        make_snapshot(
            "shared-thread-id",
            opend_version=f"worker-{worker}",
            probed_at=datetime(2026, 7, 10, 22, worker, tzinfo=UTC),
            row_count=500,
        )
        for worker in range(workers)
    ]

    def save(snapshot: CapabilitySnapshot) -> Path:
        barrier.wait(timeout=30)
        return JsonCapabilityStore(tmp_path).save(snapshot)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        stored_paths = list(executor.map(save, snapshots))

    persisted_files = immutable_files(tmp_path)
    assert len(persisted_files) == 1
    assert set(stored_paths) == set(persisted_files)
    canonical = JsonCapabilityStore(tmp_path).load_latest()
    assert canonical is not None
    assert canonical == JsonCapabilityStore._load(persisted_files[0])
    assert canonical in snapshots


def test_concurrent_processes_publish_one_canonical_snapshot_for_same_id(
    tmp_path: Path,
) -> None:
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    processes = [
        context.Process(
            target=process_save_snapshot,
            args=(str(tmp_path), worker, start_event),
        )
        for worker in range(4)
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=30)

    assert [process.exitcode for process in processes] == [0, 0, 0, 0]
    persisted_files = immutable_files(tmp_path)
    assert len(persisted_files) == 1
    assert JsonCapabilityStore(tmp_path).load_latest() == JsonCapabilityStore._load(
        persisted_files[0]
    )


@pytest.mark.parametrize(
    "snapshot_id",
    [
        "",
        " leading-space",
        "trailing-space ",
        "has space",
        "path/segment",
        r"path\segment",
        "dot..traversal",
        "control\ncharacter",
        "éxternal",
        ".starts-with-dot",
        "-starts-with-dash",
        "a" * 129,
    ],
)
def test_save_rejects_unsafe_snapshot_ids_without_touching_disk(
    tmp_path: Path,
    snapshot_id: str,
) -> None:
    store = JsonCapabilityStore(tmp_path)

    with pytest.raises(ValueError, match="invalid snapshot_id"):
        store.save(make_snapshot(snapshot_id))

    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize("snapshot_id", ["a", "A0._-", "a.b_c-1", "a" * 128])
def test_save_accepts_safe_snapshot_ids(tmp_path: Path, snapshot_id: str) -> None:
    store = JsonCapabilityStore(tmp_path)

    stored_path = store.save(make_snapshot(snapshot_id))

    assert stored_path in immutable_files(tmp_path)
