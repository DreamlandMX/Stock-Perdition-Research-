"""Fail-closed disk capacity checks for publication and recovery paths."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import shutil


class InsufficientDiskSpace(RuntimeError):
    """A write would violate the configured free-space reserve."""


@dataclass(frozen=True, slots=True)
class DiskCapacity:
    total: int
    used: int
    free: int


class DiskSpaceGuard:
    def __init__(
        self,
        *,
        minimum_free_bytes: int,
        minimum_free_ratio: float = 0.05,
        usage: Callable[[Path], tuple[int, int, int]] | None = None,
    ) -> None:
        if minimum_free_bytes < 0 or not 0 <= minimum_free_ratio < 1:
            raise ValueError("disk reserve policy is invalid")
        self.minimum_free_bytes = minimum_free_bytes
        self.minimum_free_ratio = minimum_free_ratio
        self._usage = usage or self._system_usage

    def require(self, path: Path, *, planned_bytes: int = 0) -> DiskCapacity:
        if planned_bytes < 0:
            raise ValueError("planned bytes must be non-negative")
        probe = path if path.exists() else path.parent
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        total, used, free = self._usage(probe)
        if total <= 0 or min(used, free) < 0 or used + free > total:
            raise ValueError("disk usage provider returned invalid evidence")
        remaining = free - planned_bytes
        required = max(self.minimum_free_bytes, int(total * self.minimum_free_ratio))
        if remaining < required:
            raise InsufficientDiskSpace("disk reserve would be violated")
        return DiskCapacity(total=total, used=used, free=free)

    @staticmethod
    def _system_usage(path: Path) -> tuple[int, int, int]:
        usage = shutil.disk_usage(path)
        return usage.total, usage.used, usage.free
