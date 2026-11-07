"""Small deterministic dependency graph for incremental feature invalidation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_prediction.contracts.market_data import Identifier, UsSymbol, VersionId
from stock_prediction.contracts.research import Horizon, Sha256
from stock_prediction.contracts.time import UtcDatetime

HORIZONS: tuple[Horizon, ...] = (1, 5, 20)


class FeatureDagError(ValueError):
    """Feature graph declarations or invalidation inputs are inconsistent."""


class FeatureNode(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    name: Identifier
    version: VersionId
    dependencies: tuple[Identifier, ...] = ()
    forward_impact_sessions: int = Field(default=0, ge=0)
    horizon_dependent: bool = False

    @model_validator(mode="after")
    def validate_dependencies(self) -> "FeatureNode":
        if self.name in self.dependencies:
            raise ValueError("feature node cannot directly depend on itself")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("feature node dependencies must be unique")
        return self


class FeatureCacheKey(BaseModel):
    """Exact cache identity; horizon exists only for horizon-dependent nodes."""

    model_config = ConfigDict(strict=True, frozen=True)

    node_name: Identifier
    dataset_revision: VersionId
    feature_set_version: VersionId
    symbol: UsSymbol
    as_of: UtcDatetime
    horizon_dependent: bool
    horizon: Horizon | None

    @model_validator(mode="after")
    def validate_horizon(self) -> "FeatureCacheKey":
        if self.horizon_dependent and self.horizon is None:
            raise ValueError("horizon is required for a horizon-dependent node")
        if not self.horizon_dependent and self.horizon is not None:
            raise ValueError("horizon is forbidden for a horizon-invariant node")
        return self

    @property
    def cache_id(self) -> str:
        evidence = self.model_dump(mode="json")
        return hashlib.sha256(
            json.dumps(
                evidence,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


class FeatureInvalidation(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    node_name: Identifier
    symbol: UsSymbol
    dataset_revision: VersionId
    feature_set_version: VersionId
    affected_start: date
    affected_end: date
    horizons: tuple[Horizon, ...] | None

    @model_validator(mode="after")
    def validate_range(self) -> "FeatureInvalidation":
        if self.affected_start > self.affected_end:
            raise ValueError("feature invalidation range is reversed")
        if self.horizons is not None and (
            not self.horizons
            or len(set(self.horizons)) != len(self.horizons)
            or tuple(sorted(self.horizons)) != self.horizons
        ):
            raise ValueError(
                "invalidation horizons must be non-empty unique and ordered"
            )
        return self


class FeatureDag:
    """Validated DAG with deterministic hashes and calendar-aware invalidation."""

    def __init__(self, nodes: Sequence[FeatureNode]) -> None:
        self._nodes = tuple(nodes)
        names = [node.name for node in self._nodes]
        if len(set(names)) != len(names):
            raise FeatureDagError("feature DAG contains a duplicate node")
        self._by_name = {node.name: node for node in self._nodes}
        for node in self._nodes:
            unknown = set(node.dependencies) - self._by_name.keys()
            if unknown:
                raise FeatureDagError(
                    f"node {node.name} has unknown dependency {sorted(unknown)[0]}"
                )
        self._topological_order = self._sort_topologically()

    @property
    def topological_order(self) -> tuple[str, ...]:
        return self._topological_order

    def input_hash(
        self,
        node_name: str,
        *,
        dependency_hashes: Mapping[str, Sha256],
    ) -> str:
        node = self._require_node(node_name)
        if set(dependency_hashes) != set(node.dependencies):
            raise FeatureDagError(f"node {node_name} requires exact dependency hashes")
        evidence = {
            "node": node.model_dump(mode="json"),
            "dependencies": dict(sorted(dependency_hashes.items())),
        }
        return hashlib.sha256(
            json.dumps(
                evidence,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def affected_descendants(
        self,
        upstream_name: str,
        *,
        symbol: str,
        affected_start: date,
        affected_end: date,
        trading_calendar: Sequence[date],
        dataset_revision: str,
        feature_set_version: str,
    ) -> tuple[FeatureInvalidation, ...]:
        self._require_node(upstream_name)
        calendar = tuple(trading_calendar)
        if tuple(sorted(set(calendar))) != calendar:
            raise FeatureDagError("trading calendar must be unique and ordered")
        positions = {item: index for index, item in enumerate(calendar)}
        if (
            affected_start > affected_end
            or affected_start not in positions
            or affected_end not in positions
        ):
            raise FeatureDagError("affected range must be ordered and in the calendar")

        impacts: dict[str, tuple[date, date]] = {
            upstream_name: (affected_start, affected_end)
        }
        output: list[FeatureInvalidation] = []
        for name in self._topological_order:
            if name == upstream_name:
                continue
            node = self._by_name[name]
            dependency_ranges = [
                impacts[dependency]
                for dependency in node.dependencies
                if dependency in impacts
            ]
            if not dependency_ranges:
                continue
            start = min(item[0] for item in dependency_ranges)
            dependency_end = max(item[1] for item in dependency_ranges)
            end_position = min(
                len(calendar) - 1,
                positions[dependency_end] + node.forward_impact_sessions,
            )
            end = calendar[end_position]
            impacts[name] = (start, end)
            output.append(
                FeatureInvalidation(
                    node_name=name,
                    symbol=symbol,
                    dataset_revision=dataset_revision,
                    feature_set_version=feature_set_version,
                    affected_start=start,
                    affected_end=end,
                    horizons=HORIZONS if node.horizon_dependent else None,
                )
            )
        return tuple(output)

    def _require_node(self, node_name: str) -> FeatureNode:
        try:
            return self._by_name[node_name]
        except KeyError as error:
            raise FeatureDagError(f"unknown feature node {node_name}") from error

    def _sort_topologically(self) -> tuple[str, ...]:
        remaining = {node.name: set(node.dependencies) for node in self._nodes}
        ordered: list[str] = []
        while remaining:
            ready = [
                node.name
                for node in self._nodes
                if node.name in remaining and not remaining[node.name]
            ]
            if not ready:
                raise FeatureDagError("feature DAG contains a cycle")
            for name in ready:
                ordered.append(name)
                del remaining[name]
            for dependencies in remaining.values():
                dependencies.difference_update(ready)
        return tuple(ordered)


def build_m7_feature_dag() -> FeatureDag:
    """Return the frozen horizon-invariant M7 daily feature graph."""

    return FeatureDag(
        (
            FeatureNode(name="raw_daily", version="pit-adjusted-bars-v1"),
            FeatureNode(
                name="core_v2",
                version="live-daily-features-v2",
                dependencies=("raw_daily",),
                forward_impact_sessions=200,
            ),
            FeatureNode(
                name="cross_sectional",
                version="m7-cross-sectional-v1",
                dependencies=("core_v2",),
            ),
            FeatureNode(
                name="structure_context",
                version="m7-structure-context-v1",
                dependencies=("core_v2",),
                forward_impact_sessions=252,
            ),
            FeatureNode(
                name="full_daily",
                version="live-daily-features-v5-seed100",
                dependencies=("cross_sectional", "structure_context"),
            ),
            FeatureNode(
                name="intraday_optional",
                version="m7-intraday-optional-v1",
                dependencies=("full_daily",),
            ),
            FeatureNode(
                name="full_daily_intraday_optional",
                version="live-daily-features-v5-seed100-intraday-v1",
                dependencies=("full_daily", "intraday_optional"),
            ),
        )
    )


__all__ = [
    "FeatureCacheKey",
    "FeatureDag",
    "FeatureDagError",
    "FeatureInvalidation",
    "FeatureNode",
    "HORIZONS",
    "build_m7_feature_dag",
]
