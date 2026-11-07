"""Immutable option batches with independent latest-complete/attempt pointers."""

from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import hashlib
import json
from pathlib import Path
from threading import RLock

from filelock import FileLock

from stock_prediction.contracts.options import OPTION_SYMBOLS, OptionChainSnapshot
from stock_prediction.live.locking import _atomic_json
from stock_prediction.serving.repository import (
    _file_identity,
    _is_reparse_point,
    _read_confined_regular_bytes_with_identity,
    _validate_confined_regular_file,
)

_SnapshotIdentity = tuple[int | str, ...]
_ParsedSnapshot = tuple[_SnapshotIdentity, OptionChainSnapshot, int]
_SNAPSHOT_CACHE_BYTES = 32 * 1024 * 1024


def _validated_file_identity(root: Path, path: Path) -> tuple[int, ...]:
    information = _validate_confined_regular_file(root, path, "option input")
    for parent in path.absolute().parents:
        if _is_reparse_point(parent):
            raise ValueError("option input parent is a reparse point")
        if parent == root.absolute():
            break
    return _file_identity(information)


class OptionSnapshotStore:
    def __init__(self, root: Path = Path("var/options")) -> None:
        self.root = root
        self._snapshots: OrderedDict[Path, _ParsedSnapshot] = OrderedDict()
        self._read_lock = RLock()
        self._request_reads: ContextVar[dict[Path, _ParsedSnapshot] | None] = (
            ContextVar("option_snapshot_reads", default=None)
        )

    @contextmanager
    def read_scope(self) -> Iterator[None]:
        token = self._request_reads.set({})
        try:
            yield
        finally:
            self._request_reads.reset(token)

    def save(self, snapshot: OptionChainSnapshot, raw: object = None) -> Path:
        snapshot = OptionChainSnapshot.model_validate(snapshot.model_dump())
        directory = self.root / snapshot.symbol
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{snapshot.snapshot_id}.json"
        with FileLock(str(directory / ".writer.lock"), timeout=10):
            if target.exists():
                if self._read(target) != snapshot:
                    raise ValueError("option snapshot conflict")
            else:
                if raw is not None:
                    _atomic_json(directory / f"{snapshot.snapshot_id}.raw.json", raw)
                _atomic_json(target, snapshot.model_dump(mode="json"))
            for pointer, publish in (
                ("attempt", True),
                ("complete", snapshot.complete),
            ):
                existing = self._pointer(directory, pointer)
                key = self._market_key if pointer == "complete" else self._capture_key
                if publish and (existing is None or key(snapshot) > key(existing)):
                    _atomic_json(
                        directory / f"latest-{pointer}.json",
                        {"snapshot_id": snapshot.snapshot_id},
                    )
        return target

    def load(
        self,
        symbol: str,
        snapshot_id: str | None = None,
        *,
        as_of: datetime | None = None,
    ) -> OptionChainSnapshot | None:
        if symbol not in OPTION_SYMBOLS:
            raise ValueError("unsupported option symbol")
        if snapshot_id is not None:
            import re

            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", snapshot_id):
                raise ValueError("invalid option snapshot id")
            path = self.root / symbol / f"{snapshot_id}.json"
            selected = self._read(path) if path.exists() else None
        elif as_of is not None:
            selected = self._pointer(self.root / symbol, "complete")
            if selected is None or selected.source_available_at > as_of:
                candidates = [
                    s
                    for s in self.snapshots(symbol)
                    if s.complete and s.source_available_at <= as_of
                ]
                selected = max(candidates, key=self._market_key, default=None)
        else:
            selected = self._pointer(self.root / symbol, "complete")
            if selected is None:
                selected = self.latest_attempt(symbol)
        if (
            selected is not None
            and as_of is not None
            and selected.source_available_at > as_of
        ):
            return None
        return selected

    def latest_attempt(
        self, symbol: str, *, as_of: datetime | None = None
    ) -> OptionChainSnapshot | None:
        if symbol not in OPTION_SYMBOLS:
            raise ValueError("unsupported option symbol")
        attempt = self._pointer(self.root / symbol, "attempt")
        if as_of is None or attempt is None:
            return attempt
        if attempt.captured_at <= as_of and attempt.source_available_at <= as_of:
            return attempt
        return max(
            (
                s
                for s in self.snapshots(symbol)
                if s.captured_at <= as_of and s.source_available_at <= as_of
            ),
            key=self._capture_key,
            default=None,
        )

    def with_turnover(self, snapshot: OptionChainSnapshot) -> OptionChainSnapshot:
        from stock_prediction.options.structure_map import enrich_turnover

        self.identity(snapshot)
        path = self.root / snapshot.symbol / f"{snapshot.snapshot_id}.raw.json"
        if not path.exists():
            return snapshot
        _validated_file_identity(self.root, path)
        content, identity = _read_confined_regular_bytes_with_identity(
            self.root, path, "raw option batch"
        )
        raw = json.loads(content)
        if not isinstance(raw, dict):
            raise ValueError("raw option batch must be an object")
        result = enrich_turnover(snapshot, raw)
        if _validated_file_identity(self.root, path) != identity:
            raise ValueError("raw option batch changed while it was parsed")
        self.identity(snapshot)
        return result

    def snapshots(self, symbol: str) -> tuple[OptionChainSnapshot, ...]:
        if symbol not in OPTION_SYMBOLS:
            raise ValueError("unsupported option symbol")
        return tuple(
            sorted(
                (
                    self._read(p)
                    for p in (self.root / symbol).glob("*.json")
                    if not p.name.startswith("latest-")
                    and not p.name.endswith(".raw.json")
                ),
                key=lambda s: (s.captured_at, s.snapshot_id),
            )
        )

    def _pointer(self, directory: Path, name: str) -> OptionChainSnapshot | None:
        pointer = directory / f"latest-{name}.json"
        if not pointer.exists():
            return None
        _validated_file_identity(self.root, pointer)
        content, _ = _read_confined_regular_bytes_with_identity(
            self.root, pointer, "option pointer"
        )
        identity = json.loads(content)["snapshot_id"]
        if (
            not isinstance(identity, str)
            or "/" in identity
            or "\\" in identity
            or ".." in identity
        ):
            raise ValueError("invalid option pointer")
        return self._read(directory / f"{identity}.json")

    @staticmethod
    def _capture_key(snapshot: OptionChainSnapshot) -> tuple[datetime, datetime, str]:
        return snapshot.captured_at, snapshot.captured_at, snapshot.snapshot_id

    @staticmethod
    def _market_key(snapshot: OptionChainSnapshot) -> tuple[datetime, datetime, str]:
        return (
            snapshot.market_as_of or snapshot.underlying_at or snapshot.captured_at,
            snapshot.source_available_at,
            snapshot.snapshot_id,
        )

    def identity(self, snapshot: OptionChainSnapshot) -> _SnapshotIdentity:
        with self._read_lock:
            path = self.root / snapshot.symbol / f"{snapshot.snapshot_id}.json"
            if self._read(path) is not snapshot:
                raise ValueError("option snapshot changed during the request")
            scope = self._request_reads.get()
            return (scope[path] if scope is not None else self._snapshots[path])[0]

    def _read(self, path: Path) -> OptionChainSnapshot:
        with self._read_lock:
            _validated_file_identity(self.root, path)
            content, file_identity = _read_confined_regular_bytes_with_identity(
                self.root, path, "option snapshot"
            )
            identity = (*file_identity, hashlib.sha256(content).hexdigest())
            scope = self._request_reads.get()
            cached = scope.get(path) if scope is not None else None
            cached = cached or self._snapshots.get(path)
            if cached is not None and cached[0] == identity:
                if path in self._snapshots:
                    self._snapshots.move_to_end(path)
                if scope is not None:
                    scope[path] = cached
                return cached[1]
            snapshot = OptionChainSnapshot.model_validate_json(content)
            if _validated_file_identity(self.root, path) != file_identity:
                raise ValueError("option snapshot changed while it was parsed")
            self._snapshots[path] = identity, snapshot, len(content)
            if scope is not None:
                scope[path] = self._snapshots[path]
            self._snapshots.move_to_end(path)
            # Retain one oversized snapshot so identity checks reuse the same object.
            while len(self._snapshots) > 1 and (
                len(self._snapshots) > 24
                or sum(entry[2] for entry in self._snapshots.values())
                > _SNAPSHOT_CACHE_BYTES
            ):
                self._snapshots.popitem(last=False)
            return snapshot
