"""Explicit local operator commands for M6 backup and model lifecycle actions."""

from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime

from stock_prediction.operations.backup import LocalBackupStore
from stock_prediction.operations.registry import LocalModelRegistry


def run_operator_command(args: Namespace) -> dict[str, object]:
    if args.command == "m6-registry-status":
        snapshot = LocalModelRegistry(args.registry_root).snapshot(
            args.task, args.horizon
        )
        return snapshot.model_dump(mode="json")
    if args.command == "m6-promote":
        snapshot = LocalModelRegistry(args.registry_root).promote(
            args.model_version,
            reason=args.reason,
            recorded_at=datetime.now(UTC),
        )
        return {
            "action": "promoted",
            "champion_version": snapshot.champion_version,
            "revision": snapshot.revision,
            "ledger_head_hash": snapshot.ledger_head_hash,
        }
    if args.command == "m6-rollback":
        snapshot = LocalModelRegistry(args.registry_root).rollback(
            task=args.task,
            horizon=args.horizon,
            target_version=args.target_version,
            reason=args.reason,
            recorded_at=datetime.now(UTC),
        )
        return {
            "action": "rolled_back",
            "champion_version": snapshot.champion_version,
            "revision": snapshot.revision,
            "ledger_head_hash": snapshot.ledger_head_hash,
        }
    if args.command == "m6-backup":
        store = LocalBackupStore(args.backup_root)
        backup = store.create(
            backup_id=args.backup_id,
            sources={"raw": args.raw_root, "models": args.registry_root},
            created_at=datetime.now(UTC),
        )
        manifest = store.verify(backup)
        return {
            "action": "backup_created",
            "backup_id": manifest.backup_id,
            "item_count": len(manifest.items),
            "manifest_hash": manifest.manifest_hash,
        }
    if args.command == "m6-verify-backup":
        store = LocalBackupStore(args.backup_root)
        manifest = store.verify(args.backup_root / args.backup_id)
        return {
            "action": "backup_verified",
            "backup_id": manifest.backup_id,
            "item_count": len(manifest.items),
            "manifest_hash": manifest.manifest_hash,
        }
    if args.command == "m6-restore":
        store = LocalBackupStore(args.backup_root)
        backup = args.backup_root / args.backup_id
        manifest = store.verify(backup)
        store.restore(backup, target=args.target_root)
        return {
            "action": "backup_restored",
            "backup_id": manifest.backup_id,
            "item_count": len(manifest.items),
            "manifest_hash": manifest.manifest_hash,
        }
    raise ValueError("unsupported M6 operator command")
