"""Preregistered one-time promotion windows and live evidence readiness."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

import pandas as pd
import pyarrow.parquet as pq

from stock_prediction.contracts.operations import (
    CandidateTrainingAction,
    CandidateTrainingState,
    ModelTrainingReadiness,
    ModelTrainingReadinessState,
)
from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_V2,
    next_candidate_recipe_version,
    report_recipe_version,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.features import load_latest_feature_artifact
from stock_prediction.universe.active import (
    ACTIVE_UNIVERSE,
    ACTIVE_UNIVERSE_VERSION,
)
from stock_prediction.universe.seed import SEED_UNIVERSE, SEED_UNIVERSE_VERSION


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class PromotionPackageBinding:
    package_sha256: str
    universe_version: str
    frozen_at: datetime

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.package_sha256) is None:
            raise ValueError("promotion_package_hash_invalid")
        if not self.universe_version:
            raise ValueError("promotion_package_universe_invalid")
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise ValueError("promotion_package_frozen_at_invalid")


@dataclass(frozen=True, slots=True)
class PromotionWindowDefinition:
    window_id: str
    start: date
    end: date
    label_complete_through: date
    automatic_training_at: datetime
    required_anchor_sessions: int
    label_horizon: int
    calendar_source: str
    candidate_config_version: str
    random_seed: int
    universe_version: str = ACTIVE_UNIVERSE_VERSION
    universe_size: int = len(ACTIVE_UNIVERSE)
    package_sha256: str | None = None
    frozen_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("promotion_window_dates_invalid")
        if self.label_complete_through < self.end:
            raise ValueError("promotion_window_label_date_invalid")
        if self.required_anchor_sessions < 1 or self.label_horizon < 1:
            raise ValueError("promotion_window_size_invalid")
        if self.universe_size < 1 or not self.universe_version:
            raise ValueError("promotion_window_universe_invalid")
        if (self.package_sha256 is None) != (self.frozen_at is None):
            raise ValueError("promotion_window_package_binding_incomplete")
        if (
            self.package_sha256 is not None
            and _SHA256.fullmatch(self.package_sha256) is None
        ):
            raise ValueError("promotion_window_package_hash_invalid")
        if self.frozen_at is not None and (
            self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None
        ):
            raise ValueError("promotion_window_frozen_at_invalid")

    @property
    def ledger_path_name(self) -> str:
        return f"PROMOTION-WINDOW-{self.start:%Y%m%d}-{self.end:%Y%m%d}.json"


NEXT_PROMOTION_WINDOW = PromotionWindowDefinition(
    window_id="promotion-20260811-20261127-universe204-v1",
    start=date(2026, 8, 11),
    end=date(2026, 11, 27),
    label_complete_through=date(2026, 12, 28),
    automatic_training_at=datetime(2026, 12, 29, 15, 0, tzinfo=UTC),
    required_anchor_sessions=77,
    label_horizon=20,
    calendar_source="opend-us-calendar-verified-2026-08-10",
    candidate_config_version="candidate-recipe-universe204-v1",
    random_seed=17,
)


@dataclass(frozen=True, slots=True)
class RegisteredPromotionWindow:
    definition: PromotionWindowDefinition
    registration_hash: str

    @property
    def package_sha256(self) -> str | None:
        return self.definition.package_sha256

    @property
    def universe_version(self) -> str:
        return self.definition.universe_version

    @property
    def frozen_at(self) -> datetime | None:
        return self.definition.frozen_at


class PromotionWindowRegistry:
    def __init__(self, paths: LivePaths) -> None:
        self.paths = paths
        self.directory = paths.models / "promotion-windows"
        self.consumed_directory = self.directory / "consumed"

    def register(self, definition: PromotionWindowDefinition) -> Path:
        payload = _registration_payload(definition)
        target = self.directory / f"{definition.window_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError("promotion_window_registration_conflict") from error
            if existing != payload:
                raise RuntimeError("promotion_window_registration_conflict")
            return target
        _atomic_json(target, payload)
        return target

    def load(self, window_id: str) -> RegisteredPromotionWindow:
        if not window_id or Path(window_id).name != window_id:
            raise RuntimeError("promotion_window_id_invalid")
        target = self.directory / f"{window_id}.json"
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RuntimeError("promotion_window_unavailable") from None
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("promotion_window_registration_invalid") from error
        if not isinstance(payload, dict) or payload.get("window_id") != window_id:
            raise RuntimeError("promotion_window_registration_invalid")
        registration_hash = payload.get("registration_hash")
        canonical_payload = {
            key: value for key, value in payload.items() if key != "registration_hash"
        }
        expected_hash = hashlib.sha256(
            json.dumps(canonical_payload, indent=2, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if registration_hash != expected_hash:
            raise RuntimeError("promotion_window_registration_hash_mismatch")
        try:
            definition = PromotionWindowDefinition(
                window_id=str(payload["window_id"]),
                start=date.fromisoformat(str(payload["start"])),
                end=date.fromisoformat(str(payload["end"])),
                label_complete_through=date.fromisoformat(
                    str(payload["label_complete_through"])
                ),
                automatic_training_at=datetime.fromisoformat(
                    str(payload["automatic_training_at"])
                ),
                required_anchor_sessions=int(payload["required_anchor_sessions"]),
                label_horizon=int(payload["label_horizon"]),
                calendar_source=str(payload["calendar_source"]),
                candidate_config_version=str(payload["candidate_config_version"]),
                random_seed=int(payload["random_seed"]),
                universe_version=str(payload["universe_version"]),
                universe_size=int(payload["universe_size"]),
                package_sha256=(
                    str(payload["package_sha256"])
                    if payload.get("package_sha256") is not None
                    else None
                ),
                frozen_at=(
                    datetime.fromisoformat(str(payload["frozen_at"]))
                    if payload.get("frozen_at") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("promotion_window_registration_invalid") from error
        return RegisteredPromotionWindow(
            definition=definition,
            registration_hash=str(registration_hash),
        )

    def readiness(
        self,
        window_id: str,
        package: PromotionPackageBinding,
    ) -> ModelTrainingReadiness:
        registered = self.load(window_id)
        definition = registered.definition
        if definition.universe_version != package.universe_version:
            raise RuntimeError("promotion_window_universe_mismatch")
        if definition.package_sha256 is None:
            raise RuntimeError("promotion_window_package_unbound")
        if (
            definition.package_sha256 != package.package_sha256
            or definition.frozen_at != package.frozen_at
        ):
            raise RuntimeError("promotion_window_package_mismatch")
        sessions = _full_universe_sessions(
            self.paths,
            required_size=definition.universe_size,
        )
        anchors = [
            session
            for session in sessions
            if definition.start <= session <= definition.end
        ]
        fully_labeled = [
            session
            for index, session in enumerate(sessions)
            if session in anchors
            and len(sessions) - index - 1 >= definition.label_horizon
        ]
        consumed = self._consumption_path(window_id).is_file()
        ready = (
            len(anchors) == definition.required_anchor_sessions
            and len(fully_labeled) == definition.required_anchor_sessions
            and bool(sessions)
            and sessions[-1] >= definition.label_complete_through
        )
        if consumed:
            state = ModelTrainingReadinessState.CONSUMED
            detail = "promotion_window_consumed"
        elif ready:
            state = ModelTrainingReadinessState.READY
            detail = "promotion_window_ready"
        elif len(anchors) < definition.required_anchor_sessions:
            state = ModelTrainingReadinessState.COLLECTING
            detail = "collecting_anchor_sessions"
        else:
            state = ModelTrainingReadinessState.COLLECTING
            detail = "collecting_20_day_labels"
        return ModelTrainingReadiness(
            state=state,
            window_id=definition.window_id,
            window_start=definition.start,
            window_end=definition.end,
            label_complete_through=definition.label_complete_through,
            automatic_training_at=definition.automatic_training_at,
            data_cutoff=sessions[-1] if sessions else None,
            anchor_sessions_collected=len(anchors),
            required_anchor_sessions=definition.required_anchor_sessions,
            fully_labeled_sessions=len(fully_labeled),
            required_fully_labeled_sessions=definition.required_anchor_sessions,
            label_horizon=definition.label_horizon,
            can_submit_promotion=ready and not consumed,
            can_submit=ready and not consumed,
            detail=detail,
        )

    def consume(
        self,
        window_id: str,
        *,
        package_sha256: str,
        reason: str,
    ) -> Path:
        registered = self.load(window_id)
        if registered.package_sha256 != package_sha256:
            raise RuntimeError("promotion_window_package_mismatch")
        if not reason:
            raise ValueError("promotion_window_consumption_reason_required")
        target = self._consumption_path(window_id)
        if target.is_file():
            raise RuntimeError("promotion_window_already_consumed")
        payload = {
            "window_id": window_id,
            "package_sha256": package_sha256,
            "registration_hash": registered.registration_hash,
            "reason": reason,
            "consumed_at": datetime.now(UTC).isoformat(),
        }
        payload["consumption_hash"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(target, payload)
        return target

    def _consumption_path(self, window_id: str) -> Path:
        return self.consumed_directory / f"{window_id}.json"


class PromotionWindowNotReadyError(RuntimeError):
    """A preregistered promotion window does not yet have complete labels."""


def build_seed100_promotion_window(
    package: PromotionPackageBinding,
    eligible_sessions: list[date],
) -> PromotionWindowDefinition:
    if package.universe_version != SEED_UNIVERSE_VERSION:
        raise ValueError("seed100_promotion_package_universe_mismatch")
    sessions = sorted(
        {
            session
            for session in eligible_sessions
            if session > package.frozen_at.astimezone(UTC).date()
        }
    )
    required = 2 + 77 - 1 + 20
    if len(sessions) < required:
        raise ValueError("seed100_promotion_calendar_incomplete")
    start = sessions[1]
    end = sessions[77]
    label_complete_through = sessions[97]
    automatic_training_at = datetime.combine(
        label_complete_through + timedelta(days=1),
        time(hour=15),
        tzinfo=UTC,
    )
    return PromotionWindowDefinition(
        window_id=(
            f"promotion-{start:%Y%m%d}-{end:%Y%m%d}-seed100-"
            f"{package.package_sha256[:12]}-v1"
        ),
        start=start,
        end=end,
        label_complete_through=label_complete_through,
        automatic_training_at=automatic_training_at,
        required_anchor_sessions=77,
        label_horizon=20,
        calendar_source="registered-us-trading-calendar-v1",
        candidate_config_version="m7-targeted-vnext-v1",
        random_seed=17,
        universe_version=SEED_UNIVERSE_VERSION,
        universe_size=len(SEED_UNIVERSE),
        package_sha256=package.package_sha256,
        frozen_at=package.frozen_at.astimezone(UTC),
    )


def registration_path(paths: LivePaths) -> Path:
    return (
        paths.models / "promotion-windows" / f"{NEXT_PROMOTION_WINDOW.window_id}.json"
    )


def ledger_path(paths: LivePaths) -> Path:
    return paths.models / NEXT_PROMOTION_WINDOW.ledger_path_name


def ensure_promotion_window_registered(paths: LivePaths) -> Path:
    """Persist the frozen window definition without rewriting an existing file."""

    return PromotionWindowRegistry(paths).register(NEXT_PROMOTION_WINDOW)


def _registration_payload(
    definition: PromotionWindowDefinition,
) -> dict[str, object]:
    base_definition = {
        key: value.isoformat() if isinstance(value, (date, datetime)) else value
        for key, value in asdict(definition).items()
        if key
        not in {"universe_version", "universe_size", "package_sha256", "frozen_at"}
    }
    payload: dict[str, object] = {
        **base_definition,
        "one_time": True,
        "universe_size": definition.universe_size,
        "universe_version": definition.universe_version,
    }
    if definition.package_sha256 is not None:
        payload["package_sha256"] = definition.package_sha256
        payload["frozen_at"] = definition.frozen_at.isoformat()  # type: ignore[union-attr]
    canonical = json.dumps(payload, indent=2, sort_keys=True)
    payload["registration_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def _atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def model_training_readiness(paths: LivePaths) -> ModelTrainingReadiness:
    """Measure immediate development training and later blind-test readiness."""

    ensure_promotion_window_registered(paths)
    definition = NEXT_PROMOTION_WINDOW
    full_sessions = _full_universe_sessions(paths)
    anchors = [
        session
        for session in full_sessions
        if definition.start <= session <= definition.end
    ]
    fully_labeled = [
        session
        for index, session in enumerate(full_sessions)
        if session in anchors
        and len(full_sessions) - index - 1 >= definition.label_horizon
    ]
    consumed = ledger_path(paths).is_file()
    ready = (
        len(anchors) == definition.required_anchor_sessions
        and len(fully_labeled) == definition.required_anchor_sessions
        and bool(full_sessions)
        and full_sessions[-1] >= definition.label_complete_through
    )
    if consumed:
        state = ModelTrainingReadinessState.CONSUMED
        detail = "promotion_window_consumed"
    elif ready:
        state = ModelTrainingReadinessState.READY
        detail = "promotion_window_ready"
    elif len(anchors) < definition.required_anchor_sessions:
        state = ModelTrainingReadinessState.COLLECTING
        detail = "collecting_anchor_sessions"
    else:
        state = ModelTrainingReadinessState.COLLECTING
        detail = "collecting_20_day_labels"
    latest_report = _latest_training_report(paths)
    from stock_prediction.live.candidate_locks import latest_candidate_lock

    candidate_lock = latest_candidate_lock(paths)
    if (
        candidate_lock is not None
        and candidate_lock.get("universe_version") != ACTIVE_UNIVERSE_VERSION
    ):
        candidate_lock = None
    report_matches_active_universe = (
        latest_report is not None
        and latest_report.get("universe_version") == ACTIVE_UNIVERSE_VERSION
    )
    latest_recipe = (
        report_recipe_version(latest_report)
        if report_matches_active_universe
        else CANDIDATE_RECIPE_V2
    )
    next_recipe = (
        None
        if candidate_lock is not None
        else next_candidate_recipe_version(latest_recipe)
    )
    candidate_active = _candidate_training_active(paths)
    report_tasks = latest_report.get("tasks") if latest_report is not None else None
    passed_tasks = (
        sum(
            bool(row.get("gate_passed"))
            for row in report_tasks
            if isinstance(row, dict)
        )
        if isinstance(report_tasks, list)
        else 0
    )
    if candidate_active:
        candidate_state = CandidateTrainingState.RUNNING
    elif candidate_lock is not None:
        candidate_state = CandidateTrainingState.FROZEN
    elif latest_report is not None:
        candidate_state = CandidateTrainingState.DEVELOPMENT_FAILED
    else:
        candidate_state = CandidateTrainingState.READY
    if candidate_active:
        candidate_action = CandidateTrainingAction.ACTIVE
        candidate_block_reason = "candidate_training_already_active"
    elif candidate_lock is not None:
        candidate_action = CandidateTrainingAction.FROZEN
        candidate_block_reason = "candidate_frozen_for_promotion"
    elif next_recipe is None:
        candidate_action = CandidateTrainingAction.EXHAUSTED
        candidate_block_reason = "no_additional_candidate_recipe_registered"
    else:
        candidate_action = CandidateTrainingAction.AVAILABLE
        candidate_block_reason = None
    return ModelTrainingReadiness(
        state=state,
        window_id=definition.window_id,
        window_start=definition.start,
        window_end=definition.end,
        label_complete_through=definition.label_complete_through,
        automatic_training_at=definition.automatic_training_at,
        data_cutoff=full_sessions[-1] if full_sessions else None,
        anchor_sessions_collected=len(anchors),
        required_anchor_sessions=definition.required_anchor_sessions,
        fully_labeled_sessions=len(fully_labeled),
        required_fully_labeled_sessions=definition.required_anchor_sessions,
        label_horizon=definition.label_horizon,
        candidate_training_state=candidate_state,
        candidate_data_cutoff=date(2026, 6, 29),
        candidate_passed_tasks=passed_tasks,
        candidate_total_tasks=17,
        frozen_candidate_version=(
            str(candidate_lock["candidate_model_version"])
            if candidate_lock is not None
            else None
        ),
        frozen_candidate_at=(
            datetime.fromisoformat(str(candidate_lock["created_at"]))
            if candidate_lock is not None
            else None
        ),
        can_submit_candidate_training=(
            candidate_action is CandidateTrainingAction.AVAILABLE
        ),
        latest_candidate_recipe_version=latest_recipe,
        next_candidate_recipe_version=next_recipe,
        candidate_training_action=candidate_action,
        candidate_training_block_reason=candidate_block_reason,
        can_submit_promotion=ready and not consumed and candidate_lock is not None,
        can_submit=ready and not consumed and candidate_lock is not None,
        detail=detail,
    )


def _latest_training_report(paths: LivePaths) -> dict[str, object] | None:
    from stock_prediction.live.training import load_latest_training_report

    try:
        report = load_latest_training_report(paths)
    except FileNotFoundError:
        return None
    return report


def _candidate_training_active(paths: LivePaths) -> bool:
    pointer = paths.root / "operations" / "model-training" / "LATEST.json"
    if not pointer.is_file():
        return False
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        operation_path = pointer.parent / f"{payload['operation_id']}.json"
        operation = json.loads(operation_path.read_text(encoding="utf-8"))
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return operation.get("status") in {"queued", "running"}


def _full_universe_sessions(
    paths: LivePaths,
    required_size: int | None = None,
) -> list[date]:
    try:
        artifact = load_latest_feature_artifact(paths)
        frame = pq.read_table(
            artifact.path,
            columns=["symbol", "session_date"],
        ).to_pandas()
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return []
    frame["session_date"] = pd.to_datetime(frame["session_date"]).dt.date
    counts = frame.groupby("session_date")["symbol"].nunique()
    return sorted(
        session
        for session, count in counts.items()
        if int(count) >= (required_size or len(ACTIVE_UNIVERSE))
    )


__all__ = [
    "NEXT_PROMOTION_WINDOW",
    "PromotionWindowDefinition",
    "PromotionPackageBinding",
    "PromotionWindowRegistry",
    "RegisteredPromotionWindow",
    "PromotionWindowNotReadyError",
    "build_seed100_promotion_window",
    "ensure_promotion_window_registered",
    "ledger_path",
    "model_training_readiness",
    "registration_path",
]
