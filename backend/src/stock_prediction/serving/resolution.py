"""Point-in-time resolution of immutable published research snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pydantic import TypeAdapter

from stock_prediction.contracts.serving import ResolvedSnapshot
from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.serving.manifest import ResearchSnapshotManifest
from stock_prediction.serving.repository import (
    SnapshotNotFound,
    SnapshotQuery,
    SnapshotVersionConflict,
)


class FutureSnapshotQuery(SnapshotVersionConflict):
    """A query asks the serving layer to resolve evidence from the future."""


class SnapshotRevisionAmbiguity(SnapshotVersionConflict):
    """Published manifests do not form one deterministic revision history."""


def resolve_snapshot_query(
    query: SnapshotQuery,
    manifests: Sequence[ResearchSnapshotManifest],
    *,
    evaluated_at: datetime | str,
) -> ResolvedSnapshot:
    """Resolve one snapshot without observing a publication from the future.

    Exact identity has precedence over point-in-time selection. Otherwise, an
    explicit ``as_of`` is the publication cutoff, and the serving clock is the
    cutoff for a latest query. Version filters are conjunctive.
    """

    evaluated = TypeAdapter(UtcDatetime).validate_python(evaluated_at)
    if query.as_of is not None and query.as_of > evaluated:
        raise FutureSnapshotQuery("snapshot as_of cannot be in the future")

    if query.snapshot_id is not None:
        identity_matches = tuple(
            manifest
            for manifest in manifests
            if manifest.snapshot_id == query.snapshot_id
        )
        if not identity_matches:
            raise SnapshotNotFound("requested research snapshot was not found")
        matches = tuple(
            manifest
            for manifest in identity_matches
            if manifest.generated_at <= evaluated
        )
        if not matches:
            raise FutureSnapshotQuery(
                "requested research snapshot is not published at evaluated_at"
            )
        if len(matches) != 1:
            raise SnapshotRevisionAmbiguity("published snapshot identity is ambiguous")
        selected = matches[0]
        _require_exact_match(selected, query)
        return selected.resolved_snapshot()

    cutoff = query.as_of if query.as_of is not None else evaluated
    visible = tuple(
        manifest
        for manifest in manifests
        if manifest.as_of <= cutoff and manifest.generated_at <= cutoff
    )
    _validate_visible_revision_history(visible)
    candidates = tuple(
        manifest for manifest in visible if _matches_version_filters(manifest, query)
    )
    if not candidates:
        raise SnapshotNotFound("no published research snapshot matches the query")
    selected = max(
        candidates,
        key=lambda manifest: (manifest.as_of, manifest.revision_no),
    )
    _require_version_match(selected, query)
    return selected.resolved_snapshot()


def _validate_visible_revision_history(
    manifests: Sequence[ResearchSnapshotManifest],
) -> None:
    identities: set[str] = set()
    grouped: dict[datetime, list[ResearchSnapshotManifest]] = {}
    for manifest in manifests:
        if manifest.snapshot_id in identities:
            raise SnapshotRevisionAmbiguity("published snapshot identity is ambiguous")
        identities.add(manifest.snapshot_id)
        grouped.setdefault(manifest.as_of, []).append(manifest)

    for as_of, revisions in grouped.items():
        revision_numbers = tuple(manifest.revision_no for manifest in revisions)
        if len(set(revision_numbers)) != len(revision_numbers):
            raise SnapshotRevisionAmbiguity(
                f"snapshot {as_of.isoformat()} has duplicate revision numbers"
            )
        chronological = sorted(revisions, key=lambda manifest: manifest.revision_no)
        if any(
            current.generated_at <= previous.generated_at
            for previous, current in zip(chronological, chronological[1:], strict=False)
        ):
            raise SnapshotRevisionAmbiguity(
                f"snapshot {as_of.isoformat()} has ambiguous revision chronology"
            )


def _require_exact_match(
    manifest: ResearchSnapshotManifest, query: SnapshotQuery
) -> None:
    expected: tuple[tuple[str, object | None, object], ...] = (
        ("as_of", query.as_of, manifest.as_of),
        ("dataset_revision", query.dataset_revision, manifest.dataset_revision),
        (
            "feature_set_version",
            query.feature_set_version,
            manifest.feature_set_version,
        ),
        ("universe_version", query.universe_version, manifest.universe_version),
        ("model_version", query.model_version, manifest.model_version),
        (
            "calibration_version",
            query.calibration_version,
            manifest.calibration_version,
        ),
    )
    for field, requested, actual in expected:
        if requested is not None and requested != actual:
            raise SnapshotVersionConflict(
                f"requested {field} conflicts with snapshot manifest"
            )


def _require_version_match(
    manifest: ResearchSnapshotManifest, query: SnapshotQuery
) -> None:
    if not _matches_version_filters(manifest, query):
        raise SnapshotVersionConflict(
            "resolved snapshot conflicts with requested version filters"
        )


def _matches_version_filters(
    manifest: ResearchSnapshotManifest, query: SnapshotQuery
) -> bool:
    return all(
        requested is None or requested == actual
        for requested, actual in (
            (query.dataset_revision, manifest.dataset_revision),
            (query.feature_set_version, manifest.feature_set_version),
            (query.universe_version, manifest.universe_version),
            (query.model_version, manifest.model_version),
            (query.calibration_version, manifest.calibration_version),
        )
    )


__all__ = [
    "FutureSnapshotQuery",
    "SnapshotRevisionAmbiguity",
    "resolve_snapshot_query",
]
