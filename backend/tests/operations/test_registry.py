from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from stock_prediction.operations.disk import DiskSpaceGuard
from stock_prediction.operations.registry import (
    LocalModelRegistry,
    ModelRegistryError,
)


NOW = datetime(2026, 7, 21, 12, tzinfo=UTC)


def registry(tmp_path: Path) -> LocalModelRegistry:
    return LocalModelRegistry(
        tmp_path / "registry",
        disk_guard=DiskSpaceGuard(
            minimum_free_bytes=0,
            minimum_free_ratio=0,
            usage=lambda _path: (1_000_000, 0, 1_000_000),
        ),
    )


def artifact(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / "sources" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(payload)
    return path


def register(
    store: LocalModelRegistry,
    tmp_path: Path,
    version: str,
    *,
    eligible: bool = True,
    at: datetime = NOW,
) -> None:
    source = artifact(tmp_path, f"{version}.bin", version.encode())
    store.register_release(
        model_version=version,
        task="direction",
        horizon=5,
        calibration_version="cal-v1",
        source_decision_id=f"decision-{version}",
        promotion_eligible=eligible,
        artifacts={"model.bin": source},
        registered_at=at,
    )


def test_promote_and_recover_missing_champion_by_hash_chained_rollback(
    tmp_path: Path,
) -> None:
    store = registry(tmp_path)
    register(store, tmp_path, "model-v1")
    register(store, tmp_path, "model-v2", at=NOW + timedelta(minutes=1))
    store.mark_challenger(
        "model-v1", reason="shadow candidate", recorded_at=NOW + timedelta(minutes=2)
    )
    store.promote(
        "model-v1",
        reason="initial accepted champion",
        recorded_at=NOW + timedelta(minutes=3),
    )
    store.mark_challenger(
        "model-v2",
        reason="new shadow candidate",
        recorded_at=NOW + timedelta(minutes=4),
    )
    promoted = store.promote(
        "model-v2",
        reason="independent OOS gate passed",
        recorded_at=NOW + timedelta(minutes=5),
    )
    assert promoted.champion_version == "model-v2"
    assert promoted.prior_champion_versions == ("model-v1",)

    champion_artifact = (
        tmp_path / "registry" / "releases" / "model-v2" / "artifacts" / "model.bin"
    )
    champion_artifact.unlink()
    recovered = store.recover_champion(
        task="direction", horizon=5, recorded_at=NOW + timedelta(minutes=6)
    )

    assert recovered.champion_version == "model-v1"
    assert recovered.rejected_versions == ("model-v2",)
    events = store.events()
    assert events[-1].event_type.value == "rolled_back"
    assert events[-1].previous_event_hash == events[-2].event_hash


def test_deactivate_first_champion_leaves_no_active_model(
    tmp_path: Path,
) -> None:
    store = registry(tmp_path)
    register(store, tmp_path, "model-v1")
    store.promote(
        "model-v1",
        reason="initial accepted champion",
        recorded_at=NOW + timedelta(minutes=1),
    )

    deactivated = store.deactivate_champion(
        task="direction",
        horizon=5,
        reason="formal publication failed",
        recorded_at=NOW + timedelta(minutes=2),
    )

    assert deactivated.champion_version is None
    assert deactivated.rejected_versions == ("model-v1",)
    assert store.events()[-1].event_type.value == "deactivated"


def test_release_and_ledger_integrity_fail_closed(tmp_path: Path) -> None:
    store = registry(tmp_path)
    register(store, tmp_path, "model-v1")
    store.mark_challenger("model-v1", reason="shadow", recorded_at=NOW)
    ledger = tmp_path / "registry" / "lifecycle-events.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[-1])
    payload["reason"] = "tampered"
    lines[-1] = json.dumps(payload)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ModelRegistryError, match="ledger is invalid"):
        store.events()


def test_ineligible_and_unsafe_releases_never_promote_or_escape_root(
    tmp_path: Path,
) -> None:
    store = registry(tmp_path)
    register(store, tmp_path, "model-v1", eligible=False)
    with pytest.raises(ModelRegistryError, match="promotion gate"):
        store.promote("model-v1", reason="operator error", recorded_at=NOW)

    source = artifact(tmp_path, "escape.bin", b"unsafe")
    with pytest.raises(ModelRegistryError, match="model version is invalid"):
        store.register_release(
            model_version="../escape",
            task="direction",
            horizon=5,
            calibration_version=None,
            source_decision_id="decision-escape",
            promotion_eligible=True,
            artifacts={"model.bin": source},
            registered_at=NOW,
        )
    assert not (tmp_path / "escape").exists()
