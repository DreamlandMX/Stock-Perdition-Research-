"""Promotion governance and immutable report helpers for the M4 runner."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


M7_REPORT_SECTION_NAMES = (
    "bundle_results",
    "evidence_scope",
    "feature_ablation",
    "calibration_selection",
    "baseline_deltas",
    "bootstrap_results",
    "determinism_results",
    "artifact_pointer_policy",
    "window_registration",
    "window_consumption",
    "resource_observations",
    "shadow_status",
    "drift_evidence",
    "economic_diagnostics",
    "explanation_manifest",
)


def build_m7_training_report_sections(
    *,
    completed_sections: dict[str, object],
    failed_sections: dict[str, str],
    not_run_sections: dict[str, str],
) -> dict[str, dict[str, object]]:
    """Build all governed M7 report sections with explicit status semantics."""

    required = set(M7_REPORT_SECTION_NAMES)
    supplied_groups = (
        set(completed_sections),
        set(failed_sections),
        set(not_run_sections),
    )
    union = set().union(*supplied_groups)
    overlap = any(
        supplied_groups[left] & supplied_groups[right]
        for left in range(3)
        for right in range(left + 1, 3)
    )
    if union != required or overlap:
        raise ValueError("m7_report_sections_must_appear_exactly_once")
    if any(
        isinstance(payload, dict) and not payload
        for payload in completed_sections.values()
    ):
        raise ValueError("m7_completed_report_section_cannot_be_empty")
    if any(
        not reason.strip()
        for reason in (*failed_sections.values(), *not_run_sections.values())
    ):
        raise ValueError("m7_report_section_requires_non_empty_reason")
    sections: dict[str, dict[str, object]] = {}
    for name in M7_REPORT_SECTION_NAMES:
        if name in completed_sections:
            sections[name] = {
                "status": "completed",
                "reason": None,
                "payload": completed_sections[name],
            }
        elif name in failed_sections:
            sections[name] = {
                "status": "failed",
                "reason": failed_sections[name],
                "payload": None,
            }
        else:
            sections[name] = {
                "status": "not_run",
                "reason": not_run_sections[name],
                "payload": None,
            }
    return sections


class PromotionStatus(StrEnum):
    """The only promotion outcomes exposed by M4."""

    CHAMPION_CANDIDATE = "champion_candidate"
    NO_CHAMPION_CANDIDATE = "no_champion_candidate"
    PROTOTYPE = "prototype"


class PromotionDecision(BaseModel):
    """Frozen gate decision that preserves every failed threshold."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    status: PromotionStatus
    failed_gates: tuple[str, ...]
    evaluated_thresholds: dict[str, float | int | bool | str]
    evaluation_scope: str
    economic_evidence_scope: str
    outer_fold_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_gate_semantics(self) -> "PromotionDecision":
        if len(set(self.failed_gates)) != len(self.failed_gates):
            raise ValueError("promotion failed gates must be unique")
        if self.status is PromotionStatus.CHAMPION_CANDIDATE and self.failed_gates:
            raise ValueError("a champion candidate cannot contain failed gates")
        if self.status is PromotionStatus.PROTOTYPE and self.outer_fold_count >= 3:
            raise ValueError("prototype is reserved for fewer than three outer folds")
        if (
            self.status is PromotionStatus.NO_CHAMPION_CANDIDATE
            and not self.failed_gates
        ):
            raise ValueError("no champion candidate requires at least one failed gate")
        return self


def promotion_decision(
    *,
    outer_fold_count: int,
    failed_gates: list[str] | tuple[str, ...],
    evaluated_thresholds: dict[str, float | int | bool | str],
    evaluation_scope: str,
    economic_evidence_scope: str,
) -> PromotionDecision:
    """Apply fixed promotion semantics without relaxing any threshold."""

    failures = tuple(dict.fromkeys(failed_gates))
    if outer_fold_count < 3:
        status = PromotionStatus.PROTOTYPE
        if "minimum_outer_folds" not in failures:
            failures += ("minimum_outer_folds",)
    elif failures:
        status = PromotionStatus.NO_CHAMPION_CANDIDATE
    else:
        status = PromotionStatus.CHAMPION_CANDIDATE
    return PromotionDecision(
        status=status,
        failed_gates=failures,
        evaluated_thresholds=evaluated_thresholds,
        evaluation_scope=evaluation_scope,
        economic_evidence_scope=economic_evidence_scope,
        outer_fold_count=outer_fold_count,
    )


def canonical_json_bytes(value: object, *, pretty: bool = False) -> bytes:
    """Return a deterministic JSON representation with no NaN relaxation."""

    if pretty:
        rendered = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    else:
        rendered = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    return (rendered + "\n").encode("utf-8")


def write_immutable_json(path: Path, value: object, *, pretty: bool = True) -> str:
    """Create one JSON artifact exactly once and return its SHA-256."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(value, pretty=pretty)
    with path.open("xb") as stream:
        stream.write(payload)
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def deterministic_hash(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def artifact_entry(root: Path, path: Path) -> dict[str, Any]:
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "sha256": file_sha256(path),
        "size_bytes": path.stat().st_size,
    }
