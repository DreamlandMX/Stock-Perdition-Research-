"""Explicit schema compatibility gate used by publication and recovery drills."""

from __future__ import annotations

from collections.abc import Mapping


class SchemaMismatch(RuntimeError):
    """Observed columns differ from the frozen schema contract."""


def require_exact_schema(
    expected: Mapping[str, str], observed: Mapping[str, str]
) -> None:
    if dict(expected) == dict(observed):
        return
    missing = sorted(set(expected) - set(observed))
    unexpected = sorted(set(observed) - set(expected))
    changed = sorted(
        name
        for name in set(expected) & set(observed)
        if expected[name] != observed[name]
    )
    detail = ",".join(
        (
            f"missing={missing}",
            f"unexpected={unexpected}",
            f"changed={changed}",
        )
    )
    raise SchemaMismatch(f"schema mismatch: {detail}")
