"""Seed-100 M7 training eligibility and immutable lineage evidence."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
import hashlib
import json
from types import MappingProxyType
from typing import Literal, TypeAlias

from stock_prediction.live.artifact_references import ArtifactReference
from stock_prediction.live.dataset_validation import m7_coverage_failures
from stock_prediction.modeling.m7_recipe import (
    M7_FEATURE_SET_VERSION,
    M7_LABEL_VERSION,
)
from stock_prediction.universe.seed import SEED_UNIVERSE, SEED_UNIVERSE_VERSION


M7_DATASET_MANIFEST_SCHEMA_VERSION = "m7-dataset-manifest-v1"
M7Horizon = Literal[1, 5, 20]
M7SampleKey: TypeAlias = tuple[str, date, M7Horizon]


class M7EligibilityReason(StrEnum):
    SOURCE_ROW_MISSING = "source_row_missing"
    PIT_LINEAGE_INVALID = "pit_lineage_invalid"
    ADJUSTMENT_UNVERIFIED = "adjustment_unverified"
    CALENDAR_INCOMPLETE = "calendar_incomplete"
    FEATURE_SCHEMA_INVALID = "feature_schema_invalid"
    LABEL_WINDOW_INCOMPLETE = "label_window_incomplete"
    REQUIRED_CORE_FEATURES_MISSING = "required_core_features_missing"


class M7DatasetValidationError(RuntimeError):
    """The M7 dataset identity or row contract is malformed."""


class M7DatasetCoverageError(M7DatasetValidationError):
    def __init__(
        self,
        failure_codes: tuple[str, ...],
        manifest: M7DatasetManifest,
    ) -> None:
        super().__init__("m7_dataset_coverage_failed:" + ",".join(failure_codes))
        self.failure_codes = failure_codes
        self.manifest = manifest


@dataclass(frozen=True, slots=True)
class M7EligibilityInputRow:
    symbol: str
    anchor_session: date
    horizon: M7Horizon
    universe_version: str
    dataset_revision: str
    feature_set_version: str
    label_version: str
    adjusted_bar_reference: ArtifactReference
    calendar_reference: ArtifactReference
    feature_reference: ArtifactReference
    label_reference: ArtifactReference
    pit_lineage_valid: bool
    adjustment_verified: bool
    calendar_complete: bool
    feature_schema_valid: bool
    label_window_complete: bool
    missing_core_features: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.horizon not in (1, 5, 20):
            raise M7DatasetValidationError("m7_horizon_invalid")
        if len(set(self.missing_core_features)) != len(self.missing_core_features):
            raise M7DatasetValidationError("m7_missing_core_feature_duplicate")
        if tuple(sorted(self.missing_core_features)) != self.missing_core_features:
            raise M7DatasetValidationError("m7_missing_core_features_not_canonical")

    @property
    def sample_key(self) -> M7SampleKey:
        return (self.symbol, self.anchor_session, self.horizon)

    def as_dict(self) -> dict[str, object]:
        return {
            "adjusted_bar_reference": self.adjusted_bar_reference,
            "adjustment_verified": self.adjustment_verified,
            "anchor_session": self.anchor_session,
            "calendar_complete": self.calendar_complete,
            "calendar_reference": self.calendar_reference,
            "dataset_revision": self.dataset_revision,
            "feature_reference": self.feature_reference,
            "feature_schema_valid": self.feature_schema_valid,
            "feature_set_version": self.feature_set_version,
            "horizon": self.horizon,
            "label_reference": self.label_reference,
            "label_version": self.label_version,
            "label_window_complete": self.label_window_complete,
            "missing_core_features": self.missing_core_features,
            "pit_lineage_valid": self.pit_lineage_valid,
            "symbol": self.symbol,
            "universe_version": self.universe_version,
        }


@dataclass(frozen=True, slots=True)
class M7EligibilityRow:
    symbol: str
    anchor_session: date
    horizon: M7Horizon
    eligible: bool
    exclusion_reasons: tuple[str, ...]
    adjusted_bar_reference: ArtifactReference | None
    calendar_reference: ArtifactReference | None
    feature_reference: ArtifactReference | None
    label_reference: ArtifactReference | None

    @property
    def sample_key(self) -> M7SampleKey:
        return (self.symbol, self.anchor_session, self.horizon)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "adjusted_bar_reference": _reference_payload(self.adjusted_bar_reference),
            "anchor_session": self.anchor_session.isoformat(),
            "calendar_reference": _reference_payload(self.calendar_reference),
            "eligible": self.eligible,
            "exclusion_reasons": list(self.exclusion_reasons),
            "feature_reference": _reference_payload(self.feature_reference),
            "horizon": self.horizon,
            "label_reference": _reference_payload(self.label_reference),
            "symbol": self.symbol,
        }


@dataclass(frozen=True, slots=True)
class M7DatasetManifest:
    schema_version: str
    dataset_id: str
    manifest_hash: str
    universe_version: str
    dataset_revision: str
    feature_set_version: str
    label_version: str
    symbols: tuple[str, ...]
    anchor_sessions: tuple[date, ...]
    horizons: tuple[M7Horizon, ...]
    symbol_count: int
    expected_row_count: int
    eligible_row_count: int
    symbol_coverage: Mapping[str, float]
    horizon_coverage: Mapping[int, float]
    exclusion_counts: Mapping[str, int]
    label_threshold_artifacts: tuple[ArtifactReference, ...]
    gate_passed: bool
    failure_codes: tuple[str, ...]
    rows: tuple[M7EligibilityRow, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "symbol_coverage",
            MappingProxyType(dict(self.symbol_coverage)),
        )
        object.__setattr__(
            self,
            "horizon_coverage",
            MappingProxyType(dict(self.horizon_coverage)),
        )
        object.__setattr__(
            self,
            "exclusion_counts",
            MappingProxyType(dict(self.exclusion_counts)),
        )

    def canonical_payload(self, *, include_identity: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "anchor_sessions": [value.isoformat() for value in self.anchor_sessions],
            "dataset_revision": self.dataset_revision,
            "eligible_row_count": self.eligible_row_count,
            "exclusion_counts": dict(self.exclusion_counts),
            "expected_row_count": self.expected_row_count,
            "failure_codes": list(self.failure_codes),
            "feature_set_version": self.feature_set_version,
            "gate_passed": self.gate_passed,
            "horizon_coverage": {
                str(key): value for key, value in self.horizon_coverage.items()
            },
            "horizons": list(self.horizons),
            "label_threshold_artifacts": [
                reference.as_dict() for reference in self.label_threshold_artifacts
            ],
            "label_version": self.label_version,
            "rows": [row.canonical_payload() for row in self.rows],
            "schema_version": self.schema_version,
            "symbol_count": self.symbol_count,
            "symbol_coverage": dict(self.symbol_coverage),
            "symbols": list(self.symbols),
            "universe_version": self.universe_version,
        }
        if include_identity:
            payload["dataset_id"] = self.dataset_id
            payload["manifest_hash"] = self.manifest_hash
        return payload


def build_m7_eligibility_manifest(
    source_rows: Sequence[M7EligibilityInputRow],
    *,
    expected_symbols: Sequence[str],
    expected_anchor_sessions: Sequence[date],
    label_threshold_artifacts: Sequence[ArtifactReference],
) -> M7DatasetManifest:
    symbols = tuple(expected_symbols)
    if symbols != SEED_UNIVERSE:
        raise M7DatasetValidationError("m7_universe_must_equal_seed100")
    anchors = tuple(expected_anchor_sessions)
    if not anchors or tuple(sorted(set(anchors))) != anchors:
        raise M7DatasetValidationError("m7_anchor_sessions_not_canonical")
    thresholds = tuple(label_threshold_artifacts)
    if not thresholds:
        raise M7DatasetValidationError("m7_label_threshold_artifacts_required")
    if len({reference.sha256 for reference in thresholds}) != len(thresholds):
        raise M7DatasetValidationError("m7_label_threshold_artifact_duplicate")
    keys = tuple(row.sample_key for row in source_rows)
    if len(set(keys)) != len(keys):
        raise M7DatasetValidationError("m7_duplicate_sample_key")
    _validate_source_identity(source_rows)
    row_by_key = dict(zip(keys, source_rows, strict=True))
    anchor_set = set(anchors)
    expected_key_set = {
        (symbol, anchor, horizon)
        for anchor in anchors
        for symbol in symbols
        for horizon in (1, 5, 20)
    }
    unexpected = set(keys) - expected_key_set
    if unexpected:
        raise M7DatasetValidationError("m7_source_row_outside_expected_grain")

    rows: list[M7EligibilityRow] = []
    for anchor in anchors:
        if anchor not in anchor_set:  # pragma: no cover - defensive invariant
            raise AssertionError("unreachable anchor")
        for symbol in symbols:
            for horizon in (1, 5, 20):
                typed_horizon: M7Horizon = horizon
                source = row_by_key.get((symbol, anchor, typed_horizon))
                rows.append(
                    _eligibility_row(
                        symbol=symbol,
                        anchor_session=anchor,
                        horizon=typed_horizon,
                        source=source,
                    )
                )

    expected_per_symbol = len(anchors) * 3
    expected_per_horizon = len(anchors) * len(symbols)
    symbol_coverage = {
        symbol: sum(row.eligible for row in rows if row.symbol == symbol)
        / expected_per_symbol
        for symbol in symbols
    }
    horizon_coverage = {
        horizon: sum(row.eligible for row in rows if row.horizon == horizon)
        / expected_per_horizon
        for horizon in (1, 5, 20)
    }
    failure_codes = m7_coverage_failures(
        symbol_coverage=symbol_coverage,
        horizon_coverage=horizon_coverage,
    )
    exclusions = Counter(reason for row in rows for reason in row.exclusion_reasons)
    dataset_revision = source_rows[0].dataset_revision if source_rows else "missing"
    manifest = _create_manifest(
        dataset_revision=dataset_revision,
        symbols=symbols,
        anchors=anchors,
        symbol_coverage=symbol_coverage,
        horizon_coverage=horizon_coverage,
        exclusion_counts=dict(sorted(exclusions.items())),
        thresholds=thresholds,
        failure_codes=failure_codes,
        rows=tuple(rows),
    )
    if failure_codes:
        raise M7DatasetCoverageError(failure_codes, manifest)
    return manifest


def _validate_source_identity(source_rows: Sequence[M7EligibilityInputRow]) -> None:
    if not source_rows:
        raise M7DatasetValidationError("m7_source_rows_required")
    identities = {
        "universe_version": {row.universe_version for row in source_rows},
        "dataset_revision": {row.dataset_revision for row in source_rows},
        "feature_set_version": {row.feature_set_version for row in source_rows},
        "label_version": {row.label_version for row in source_rows},
    }
    for name, values in identities.items():
        if len(values) != 1:
            raise M7DatasetValidationError(f"m7_mixed_{name}")
    if identities["universe_version"] != {SEED_UNIVERSE_VERSION}:
        raise M7DatasetValidationError("m7_universe_version_invalid")
    if identities["feature_set_version"] != {M7_FEATURE_SET_VERSION}:
        raise M7DatasetValidationError("m7_feature_set_version_invalid")
    if identities["label_version"] != {M7_LABEL_VERSION}:
        raise M7DatasetValidationError("m7_label_version_invalid")


def _eligibility_row(
    *,
    symbol: str,
    anchor_session: date,
    horizon: M7Horizon,
    source: M7EligibilityInputRow | None,
) -> M7EligibilityRow:
    if source is None:
        return M7EligibilityRow(
            symbol=symbol,
            anchor_session=anchor_session,
            horizon=horizon,
            eligible=False,
            exclusion_reasons=(M7EligibilityReason.SOURCE_ROW_MISSING.value,),
            adjusted_bar_reference=None,
            calendar_reference=None,
            feature_reference=None,
            label_reference=None,
        )
    reasons: list[str] = []
    checks = (
        (source.pit_lineage_valid, M7EligibilityReason.PIT_LINEAGE_INVALID),
        (source.adjustment_verified, M7EligibilityReason.ADJUSTMENT_UNVERIFIED),
        (source.calendar_complete, M7EligibilityReason.CALENDAR_INCOMPLETE),
        (source.feature_schema_valid, M7EligibilityReason.FEATURE_SCHEMA_INVALID),
        (
            source.label_window_complete,
            M7EligibilityReason.LABEL_WINDOW_INCOMPLETE,
        ),
        (
            not source.missing_core_features,
            M7EligibilityReason.REQUIRED_CORE_FEATURES_MISSING,
        ),
    )
    reasons.extend(reason.value for passed, reason in checks if not passed)
    return M7EligibilityRow(
        symbol=symbol,
        anchor_session=anchor_session,
        horizon=horizon,
        eligible=not reasons,
        exclusion_reasons=tuple(reasons),
        adjusted_bar_reference=source.adjusted_bar_reference,
        calendar_reference=source.calendar_reference,
        feature_reference=source.feature_reference,
        label_reference=source.label_reference,
    )


def _create_manifest(
    *,
    dataset_revision: str,
    symbols: tuple[str, ...],
    anchors: tuple[date, ...],
    symbol_coverage: Mapping[str, float],
    horizon_coverage: Mapping[int, float],
    exclusion_counts: Mapping[str, int],
    thresholds: tuple[ArtifactReference, ...],
    failure_codes: tuple[str, ...],
    rows: tuple[M7EligibilityRow, ...],
) -> M7DatasetManifest:
    provisional = M7DatasetManifest(
        schema_version=M7_DATASET_MANIFEST_SCHEMA_VERSION,
        dataset_id="pending",
        manifest_hash="pending",
        universe_version=SEED_UNIVERSE_VERSION,
        dataset_revision=dataset_revision,
        feature_set_version=M7_FEATURE_SET_VERSION,
        label_version=M7_LABEL_VERSION,
        symbols=symbols,
        anchor_sessions=anchors,
        horizons=(1, 5, 20),
        symbol_count=len(symbols),
        expected_row_count=len(rows),
        eligible_row_count=sum(row.eligible for row in rows),
        symbol_coverage=symbol_coverage,
        horizon_coverage=horizon_coverage,
        exclusion_counts=exclusion_counts,
        label_threshold_artifacts=thresholds,
        gate_passed=not failure_codes,
        failure_codes=failure_codes,
        rows=rows,
    )
    manifest_hash = hashlib.sha256(
        json.dumps(
            provisional.canonical_payload(include_identity=False),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return replace(
        provisional,
        dataset_id=f"m7-dataset-{manifest_hash[:24]}",
        manifest_hash=manifest_hash,
    )


def _reference_payload(reference: ArtifactReference | None) -> object:
    return reference.as_dict() if reference is not None else None


__all__ = [
    "M7_DATASET_MANIFEST_SCHEMA_VERSION",
    "M7DatasetCoverageError",
    "M7DatasetManifest",
    "M7DatasetValidationError",
    "M7EligibilityInputRow",
    "M7EligibilityReason",
    "M7EligibilityRow",
    "build_m7_eligibility_manifest",
]
