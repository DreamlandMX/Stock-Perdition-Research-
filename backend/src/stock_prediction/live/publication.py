"""Build immutable data-only research snapshots from real local OpenD artifacts."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime
import math
from typing import Any, Callable, Iterable, Literal
from zoneinfo import ZoneInfo

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from stock_prediction.contracts.health import (
    Completeness,
    Connectivity,
    Freshness,
    Readiness,
)
from stock_prediction.contracts.research import Horizon, RegimeLabel
from stock_prediction.contracts.serving import (
    CandidateEvaluationSummary,
    DataHealth,
    ExecutionReadiness,
    ForecastAvailable,
    ForecastUnavailable,
    HistoryBar,
    HistoryEvent,
    InstrumentMetadata,
    MarketProxy,
    ModelTaskMatrixRow,
    OverviewReadModel,
    PriceBand,
    PredictionReadiness,
    ResearchEvidenceScope,
    ResearchModelEvidence,
    ResearchModelValues,
    ResearchOpportunityRow,
    ResearchPriceProjection,
    ResearchReadiness,
    RegimeDistribution,
    RejectionReasonCount,
    ResolvedSnapshot,
    ReturnBasis,
    RuleEvidence,
    ScreenRow,
    SectorRegimeCell,
    ServingStatus,
    TaskForecastEvidence,
    UniverseVersion,
)
from stock_prediction.live.config import LivePaths
from stock_prediction.live.features import load_latest_feature_artifact
from stock_prediction.live.inference import LiveInferenceEngine, ResearchTaskPrediction
from stock_prediction.live.m7_integrity import M7IntegrityMonitor
from stock_prediction.live.m7_formal_assembly import apply_m7_formal_predictions
from stock_prediction.live.m7_formal_inference import (
    M7FormalBundleBinding,
    M7FormalInferenceResult,
    load_m7_formal_inference_result,
)
from stock_prediction.live.m7_promotion import load_active_m7_forecast_bundle
from stock_prediction.live.training import load_latest_training_report
from stock_prediction.live.storage import LiveStorage
from stock_prediction.live.dataset_validation import read_validation
from stock_prediction.modeling.confidence import ConfidenceLevel, RejectionReason
from stock_prediction.operations.contracts import LifecycleSnapshot
from stock_prediction.serving.manifest import (
    RESEARCH_TABLE_SCHEMAS,
    ResearchSnapshotManifest,
)
from stock_prediction.serving.materialize import materialize_research_snapshot
from stock_prediction.live.universe_scope import LiveUniverseSpec, resolve_live_universe

_NO_CHAMPION = "no_champion_candidate"
_M7_FORMAL_UNAVAILABLE = "m7_formal_current_unavailable"
_PROXY_SYMBOLS = ("US.SPY", "US.QQQ", "US.DIA", "US.IWM")
_HORIZONS: tuple[Horizon, ...] = (1, 5, 20)
_PUBLICATION_SCHEMA_REVISION = 8
_EXPERIMENTAL_WARNING = "未通过门禁，仅供实验研究；不得用于可执行机会或交易建议。"


def _resolve_formal_publication_identity(
    champion: tuple[str, str, dict[str, Any]] | None,
    *,
    active_identity: tuple[str, str] | None,
    formal_available: bool,
) -> tuple[bool, str | None, str | None]:
    if champion is None:
        return False, None, None
    if active_identity is None:
        return True, champion[0], champion[1]
    if not formal_available:
        return False, None, None
    forecast_bundle_id, bundle_hash = active_identity
    return True, forecast_bundle_id, f"m7-cal-{bundle_hash[:16]}"


class LiveSnapshotPublisher:
    def __init__(
        self,
        paths: LivePaths,
        *,
        universe: LiveUniverseSpec | None = None,
        research_paths: LivePaths | None = None,
    ) -> None:
        self.paths = paths
        self.storage = LiveStorage(paths)
        self.universe = universe or resolve_live_universe(paths)
        self.research_paths = research_paths

    def publish(
        self, *, progress: Callable[[str, int, int], None] | None = None
    ) -> dict[str, object]:
        from stock_prediction.live.training_job import resolve_research_paths

        if progress is not None:
            progress("prepare", 0, 1)
        research_paths = self.research_paths or resolve_research_paths(self.paths)
        artifact = load_latest_feature_artifact(self.paths)
        generated_at = datetime.now(UTC)
        M7IntegrityMonitor(self.paths).verify_active(observed_at=generated_at)
        inference = LiveInferenceEngine(self.paths, universe=self.universe)
        research_inference = (
            inference
            if research_paths.models == self.paths.models
            else LiveInferenceEngine(research_paths, universe=self.universe)
        )
        research_lifecycle = research_inference.registry.snapshot("forecast_bundle", 20)
        champion = inference.champion()
        active_m7 = load_active_m7_forecast_bundle(self.paths)
        m7_formal = (
            None
            if active_m7 is None
            else load_m7_formal_inference_result(
                self.paths,
                binding=M7FormalBundleBinding(
                    forecast_bundle_id=active_m7.forecast_bundle_id,
                    bundle_sha256=active_m7.bundle_sha256,
                    components=active_m7.components,
                ),
                session=artifact.as_of.date(),
            )
        )
        training_report = _latest_training_report(
            research_paths, universe_version=self.universe.version
        )
        formal_report = (
            training_report
            if research_paths.models == self.paths.models
            else _latest_training_report(
                self.paths, universe_version=self.universe.version
            )
        )
        passed_tasks, total_tasks = _task_gate_counts(training_report)
        formal_source_ready, model_version, calibration_version = (
            _resolve_formal_publication_identity(
                champion,
                active_identity=(
                    None
                    if active_m7 is None
                    else (active_m7.forecast_bundle_id, active_m7.bundle_sha256)
                ),
                formal_available=m7_formal is not None,
            )
        )
        readiness = (
            PredictionReadiness.FORECAST_READY
            if formal_source_ready
            else PredictionReadiness.DATA_ONLY
        )
        manual_activation = (
            formal_source_ready
            and champion is not None
            and champion[2].get("activation_basis") == "manual_override"
        )
        execution_readiness = (
            ExecutionReadiness.READY
            if formal_source_ready
            and not manual_activation
            and formal_report is not None
            and formal_report.get("execution_readiness") == "ready"
            else ExecutionReadiness.BLOCKED
        )
        research_readiness = (
            ResearchReadiness.MANUAL_UNVERIFIED
            if manual_activation
            else ResearchReadiness.FULL_VERIFIED
            if formal_source_ready
            else ResearchReadiness.PARTIAL_VERIFIED
            if passed_tasks > 0
            else ResearchReadiness.RULES_ONLY
        )
        challenger_version = (
            research_lifecycle.challenger_versions[-1]
            if training_report is not None and research_lifecycle.challenger_versions
            else None
        )
        kind = (
            f"forecast-{model_version}"
            if model_version is not None
            else f"data-challenger-{challenger_version}"
            if challenger_version is not None
            else "data"
        )
        snapshot_id = (
            f"live-{artifact.as_of:%Y%m%dT%H%M%SZ}-{kind}-"
            f"{artifact.input_hash[:10]}-r{_PUBLICATION_SCHEMA_REVISION}"
        )
        if research_paths.models != self.paths.models:
            from stock_prediction.modeling.report import file_sha256

            snapshot_id += f"-research-{file_sha256(research_paths.models / 'LATEST-TRAINING.json')[:16]}"
        existing = self.paths.research / snapshot_id / "manifest.json"
        republish = False
        if existing.is_file():
            manifest = ResearchSnapshotManifest.model_validate_json(
                existing.read_bytes(), strict=True
            )
            resolved = manifest.resolved_snapshot()
            # The snapshot id is derived from the data alone (as-of, feature
            # artifact hash, schema revision). Two different kinds of mismatch
            # can therefore show up against a stored manifest, and they must be
            # handled differently:
            #
            #   * Data identity drift means the stored snapshot does not
            #     describe this data at all. That is real corruption and must
            #     still fail loudly.
            #   * Derived-readiness drift means the same data is now labelled
            #     differently because training, gating or promotion state moved
            #     on (for example task gates going from 0 to 13 passed). The
            #     data is unchanged, so failing the whole EOD pipeline is wrong
            #     - the snapshot is simply re-materialized with current labels.
            data_identity_mismatches = tuple(
                name
                for name, stored, computed in (
                    ("snapshot_id", resolved.snapshot_id, snapshot_id),
                    ("as_of", resolved.as_of, artifact.as_of),
                    (
                        "dataset_revision",
                        resolved.dataset_revision,
                        f"live-{artifact.input_hash[:16]}",
                    ),
                    (
                        "feature_set_version",
                        resolved.feature_set_version,
                        artifact.feature_set_version,
                    ),
                    (
                        "universe_version",
                        resolved.universe_version,
                        self.universe.version,
                    ),
                )
                if stored != computed
            )
            if data_identity_mismatches:
                # The error message is surfaced as the operation error code, so
                # it must name the offending fields without any path separator.
                raise RuntimeError(
                    "live_snapshot_identity_conflict:"
                    + ",".join(data_identity_mismatches)
                )
            readiness_mismatches = tuple(
                name
                for name, stored, computed in (
                    (
                        "prediction_readiness",
                        resolved.prediction_readiness,
                        readiness,
                    ),
                    (
                        "execution_readiness",
                        resolved.execution_readiness,
                        execution_readiness,
                    ),
                    (
                        "research_readiness",
                        resolved.research_readiness,
                        research_readiness,
                    ),
                    ("model_version", resolved.model_version, model_version),
                    (
                        "calibration_version",
                        resolved.calibration_version,
                        calibration_version,
                    ),
                )
                if stored != computed
            )
            if not readiness_mismatches:
                return {
                    "status": "unchanged",
                    "snapshot_id": snapshot_id,
                    "prediction_readiness": readiness.value,
                    "research_readiness": research_readiness.value,
                    "path": str(existing.parent),
                }
            republish = True
        revision_no = 1 + sum(
            1
            for path in self.paths.research.glob(
                f"live-{artifact.as_of:%Y%m%dT%H%M%SZ}-*/manifest.json"
            )
            if path.is_file()
        )
        snapshot = ResolvedSnapshot(
            snapshot_id=snapshot_id,
            revision_no=revision_no,
            as_of=artifact.as_of,
            dataset_revision=f"live-{artifact.input_hash[:16]}",
            feature_set_version=artifact.feature_set_version,
            universe_version=self.universe.version,
            prediction_readiness=readiness,
            activation_basis="manual_override" if manual_activation else None,
            execution_readiness=execution_readiness,
            research_readiness=research_readiness,
            model_version=model_version,
            calibration_version=calibration_version,
        )
        tables = self._tables(
            snapshot,
            generated_at=generated_at,
            inference=inference,
            research_inference=research_inference,
            lifecycle=research_lifecycle,
            training_report=training_report,
            legacy_champion=champion,
            m7_formal=m7_formal,
            m7_active=active_m7 is not None,
            progress=progress,
        )
        if progress is not None:
            progress("materialize", 0, 1)
        result = materialize_research_snapshot(
            self.paths.research,
            snapshot=snapshot,
            generated_at=max(generated_at, snapshot.as_of),
            tables=tables,
            champion_exists=formal_source_ready,
        )
        if result.published_path is None:
            raise RuntimeError("live_snapshot_publication_failed")
        if progress is not None:
            progress("materialize", 1, 1)
        return {
            "status": "republished" if republish else "published",
            "snapshot_id": snapshot_id,
            "prediction_readiness": readiness.value,
            "research_readiness": research_readiness.value,
            "path": str(result.published_path),
            "instruments": len(self.universe.symbols),
            "screen_rows": len(self.universe.symbols) * len(_HORIZONS),
            "available_forecasts": len(self.universe.symbols)
            if formal_source_ready
            else 0,
            "verified_tasks": passed_tasks,
            "total_tasks": total_tasks,
        }

    def _tables(
        self,
        snapshot: ResolvedSnapshot,
        *,
        generated_at: datetime,
        inference: LiveInferenceEngine,
        research_inference: LiveInferenceEngine,
        lifecycle: LifecycleSnapshot,
        training_report: dict[str, object] | None,
        legacy_champion: tuple[str, str, dict[str, Any]] | None,
        m7_formal: M7FormalInferenceResult | None,
        m7_active: bool,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, pa.Table]:
        latest, feature_regimes = self._latest_features()
        missing = set(self.universe.symbols) - set(latest)
        if missing:
            raise RuntimeError("live_features_do_not_cover_active_universe")
        metadata = self.storage.latest_instrument_metadata()
        instruments = tuple(
            _instrument(
                symbol,
                snapshot,
                metadata.get(symbol),
                etfs=self.universe.etfs,
                effective_from=self.universe.effective_from,
            )
            for symbol in self.universe.symbols
        )
        feature_frame = pq.read_table(
            load_latest_feature_artifact(self.paths).path
        ).to_pandas()
        available: dict[str, ForecastAvailable] = {}
        inference.progress = (
            None
            if progress is None
            else lambda completed, total: progress("formal", completed, total)
        )
        if snapshot.prediction_readiness is PredictionReadiness.FORECAST_READY:
            if m7_formal is None:
                available = inference.predict(snapshot, feature_frame)
            else:
                if legacy_champion is None or m7_formal.prediction_path is None:
                    raise RuntimeError("m7_formal_assembly_base_unavailable")
                legacy_snapshot = ResolvedSnapshot.model_validate(
                    {
                        **snapshot.model_dump(mode="python"),
                        "model_version": legacy_champion[0],
                        "calibration_version": legacy_champion[1],
                    },
                    strict=True,
                )
                base = inference.predict(legacy_snapshot, feature_frame)
                ledger = pq.read_table(m7_formal.prediction_path).to_pandas()
                if (
                    snapshot.model_version is None
                    or snapshot.calibration_version is None
                ):
                    raise RuntimeError("m7_formal_assembly_identity_unavailable")
                available = apply_m7_formal_predictions(
                    base,
                    ledger,
                    model_version=snapshot.model_version,
                    calibration_version=snapshot.calibration_version,
                )
        if progress is not None:
            progress("formal", 1, 1)
        research_inference.progress = (
            None
            if progress is None
            else lambda completed, total: progress("verified", completed, total)
        )
        verified_research = research_inference.predict_verified_research(
            snapshot,
            feature_frame,
        )
        if progress is not None:
            progress("verified", 1, 1)
        research_inference.progress = (
            None
            if progress is None
            else lambda completed, total: progress("candidate", completed, total)
        )
        candidate_available, candidate_report = research_inference.predict_research(
            snapshot,
            feature_frame,
        )
        if progress is not None:
            progress("candidate", 1, 1)
            progress("tables", 0, 1)
        effective_report = candidate_report or training_report
        screen_rows = tuple(
            _screen_row(
                symbol,
                horizon,
                latest[symbol],
                snapshot,
                available.get(symbol),
                metadata.get(symbol),
                verified_research.get(symbol, ()),
                candidate_available.get(symbol),
                effective_report,
                etfs=self.universe.etfs,
            )
            for symbol in self.universe.symbols
            for horizon in _HORIZONS
        )
        forecasts = tuple(
            available.get(symbol)
            or ForecastUnavailable(
                symbol=symbol,
                as_of=snapshot.as_of,
                serving_status=ServingStatus.UNAVAILABLE,
                not_generated_reason=(
                    _M7_FORMAL_UNAVAILABLE if m7_active else _NO_CHAMPION
                ),
                affected_inputs=(
                    ("m7_active_forecast_bundle", "m7_formal_current_prediction")
                    if m7_active
                    else ("model_registry", "oos_promotion_gate")
                ),
                last_available_snapshot=None,
                verified_research=tuple(
                    evidence
                    for horizon in _HORIZONS
                    if (
                        evidence := _verified_research_evidence(
                            verified_research.get(symbol, ()),
                            horizon,
                        )
                    )
                    is not None
                ),
                challenger_preview=tuple(
                    evidence
                    for horizon in _HORIZONS
                    if (
                        evidence := _research_evidence(
                            candidate_available.get(symbol),
                            horizon,
                            ResearchEvidenceScope.CHALLENGER,
                            effective_report,
                        )
                    )
                    is not None
                ),
            )
            for symbol in self.universe.symbols
        )
        manual_components = (
            legacy_champion[2]["manual_component_versions"]
            if snapshot.activation_basis == "manual_override"
            and legacy_champion is not None
            else None
        )
        if manual_components is not None and any(
            inference.registry.snapshot(task, horizon).champion_version != version
            for (task, horizon), version in manual_components.items()
        ):
            raise RuntimeError("manual_activation_component_registry_mismatch")
        models = _model_rows(
            snapshot, lifecycle, effective_report, manual_components=manual_components
        )
        proxy_trends = _proxy_trends(feature_frame)
        overview = _overview(
            latest,
            screen_rows,
            snapshot=snapshot,
            generated_at=generated_at,
            candidate_summary=_candidate_summary(effective_report),
            proxy_trends=proxy_trends,
            universe_size=len(self.universe.symbols),
            etfs=self.universe.etfs,
        )
        history_rows = self._history_rows(feature_regimes)
        health = self._health_rows(snapshot)
        universe = UniverseVersion(
            universe_version=self.universe.version,
            effective_from=datetime.combine(
                self.universe.effective_from,
                datetime.min.time(),
                tzinfo=UTC,
            ),
            effective_to=None,
            member_count=len(self.universe.symbols),
            reason=self.universe.reason,
            point_in_time_status="forward_only",
        )
        return {
            "overview.parquet": _table(
                "overview.parquet",
                ({"horizon": 5, "payload_json": overview.model_dump_json()},),
            ),
            "instruments.parquet": _table(
                "instruments.parquet",
                (
                    {
                        "symbol": row.symbol,
                        "name": row.name,
                        "asset_type": row.asset_type,
                        "sector": row.sector,
                        "payload_json": row.model_dump_json(),
                    }
                    for row in instruments
                ),
            ),
            "screen.parquet": _table(
                "screen.parquet",
                (
                    {
                        "symbol": row.symbol,
                        "horizon": row.horizon,
                        "asset_type": row.asset_type,
                        "sector": row.sector,
                        "regime": row.regime.value,
                        "executable_up_probability": row.executable_up_probability,
                        "breakout_up_probability": row.breakout_up_probability,
                        "breakout_down_probability": row.breakout_down_probability,
                        "executable_q50": row.executable_q50,
                        "capital_gain_q50": row.capital_gain_q50,
                        "interval_width": row.interval_width,
                        "similar_case_count": row.similar_case_count,
                        "confidence_level": row.confidence_level.value,
                        "data_freshness_seconds": row.data_freshness_seconds,
                        "serving_status": row.serving_status.value,
                        "effective_abstain": row.effective_abstain,
                        "payload_json": row.model_dump_json(),
                    }
                    for row in screen_rows
                ),
            ),
            "forecasts.parquet": _table(
                "forecasts.parquet",
                (
                    {
                        "symbol": (
                            row.bundle.symbol
                            if isinstance(row, ForecastAvailable)
                            else row.symbol
                        ),
                        "availability": row.availability,
                        "payload_json": row.model_dump_json(),
                    }
                    for row in forecasts
                ),
            ),
            "verified_research.parquet": _table(
                "verified_research.parquet",
                (
                    {
                        "symbol": row.symbol,
                        "horizon": row.horizon,
                        "payload_json": row.verified_research.model_dump_json(),
                    }
                    for row in screen_rows
                    if row.verified_research is not None
                ),
            ),
            "challenger_preview.parquet": _table(
                "challenger_preview.parquet",
                (
                    {
                        "symbol": row.symbol,
                        "horizon": row.horizon,
                        "payload_json": row.challenger_preview.model_dump_json(),
                    }
                    for row in screen_rows
                    if row.challenger_preview is not None
                ),
            ),
            "history_bars.parquet": _table("history_bars.parquet", history_rows),
            "history_events.parquet": _table(
                "history_events.parquet", self._history_event_rows(snapshot.as_of)
            ),
            "similar_cases.parquet": _table(
                "similar_cases.parquet",
                (
                    {
                        "symbol": forecast.bundle.symbol,
                        "rank": rank,
                        "payload_json": case.model_dump_json(),
                    }
                    for forecast in forecasts
                    if isinstance(forecast, ForecastAvailable)
                    for rank, case in enumerate(forecast.similar_cases, 1)
                ),
            ),
            "backtests.parquet": _table("backtests.parquet", ()),
            "models.parquet": _table(
                "models.parquet",
                (
                    {
                        "task": row.task,
                        "horizon": row.horizon,
                        "asset_group": row.asset_group,
                        "payload_json": row.model_dump_json(),
                    }
                    for row in models
                ),
            ),
            "data_health.parquet": _table(
                "data_health.parquet",
                (
                    {
                        "component": row.component,
                        "dataset": row.dataset,
                        "symbol": row.symbol,
                        "serving_status": row.serving_status.value,
                        "payload_json": row.model_dump_json(),
                    }
                    for row in health
                ),
            ),
            "universes.parquet": _table(
                "universes.parquet",
                (
                    {
                        "universe_version": universe.universe_version,
                        "effective_from": universe.effective_from,
                        "payload_json": universe.model_dump_json(),
                    },
                ),
            ),
        }

    def _latest_features(
        self,
    ) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, object], str]]:
        artifact = load_latest_feature_artifact(self.paths)
        with duckdb.connect() as connection:
            latest_frame = connection.execute(
                """
                SELECT * FROM read_parquet(?)
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY symbol ORDER BY session_date DESC
                ) = 1
                """,
                [str(artifact.path)],
            ).fetch_df()
            regimes = connection.execute(
                """
                SELECT symbol, session_date, regime FROM read_parquet(?)
                """,
                [str(artifact.path)],
            ).fetchall()
        latest = {
            str(row["symbol"]): row.to_dict() for _, row in latest_frame.iterrows()
        }
        return latest, {
            (
                str(row[0]),
                row[1].date() if isinstance(row[1], datetime) else row[1],
            ): str(row[2])
            for row in regimes
        }

    def _history_event_rows(self, as_of: datetime) -> Iterable[dict[str, object]]:
        with duckdb.connect(str(self.paths.catalog)) as connection:
            rows = connection.execute(
                """
                WITH latest AS (
                    SELECT * FROM rehab_snapshots WHERE observed_at <= ?
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY symbol ORDER BY observed_at DESC, snapshot_id DESC
                    ) = 1
                )
                SELECT o.symbol, o.ex_div_date, o.split_ratio, o.per_cash_div,
                       s.observed_at, s.snapshot_id
                FROM latest s JOIN rehab_observations o
                  ON o.raw_batch_id = s.snapshot_id AND o.symbol = s.symbol
                WHERE o.ex_div_date <= CAST(? AS DATE)
                ORDER BY o.symbol, o.ex_div_date
                """,
                [as_of, as_of.astimezone(ZoneInfo("America/New_York")).date()],
            ).fetchall()
        for symbol, effective_date, factor, cash, observed_at, source_id in rows:
            if symbol not in self.universe.symbols:
                continue
            facts = []
            if (
                factor is not None
                and math.isfinite(factor)
                and factor > 0
                and factor != 1
            ):
                facts.append(("split", f"拆并股：每 1 股变为 {1 / factor:g} 股"))
            if cash is not None and math.isfinite(cash) and cash > 0:
                facts.append(("dividend", f"每股现金分红（供应商原值）：{cash:g}"))
            for kind, label in facts:
                event = HistoryEvent(
                    event_id=f"{source_id}-{effective_date}-{kind}",
                    occurred_at=datetime.combine(
                        effective_date,
                        datetime.min.time(),
                        tzinfo=ZoneInfo("America/New_York"),
                    ).astimezone(UTC),
                    event_type=kind,
                    label=label,
                    details=(
                        f"除权息日期：{effective_date}；日期定位，不代表盘中公告时间。",
                        f"来源快照：{source_id}；观察时间：{observed_at.isoformat()}",
                        "供应商回溯记录，不代表事件当时已知；不改变原始价格或生成总回报。",
                    ),
                )
                yield {
                    "symbol": symbol,
                    "timeframe": "1d",
                    "adjustment": "raw",
                    "occurred_at": event.occurred_at,
                    "payload_json": event.model_dump_json(),
                }

    def _history_rows(
        self, regimes: dict[tuple[str, object], str]
    ) -> Iterable[dict[str, object]]:
        with duckdb.connect(str(self.paths.catalog)) as connection:
            rows = connection.execute(
                """
                SELECT symbol, timeframe, session_date, bar_start,
                       open, high, low, close, volume
                FROM canonical_market_bars
                WHERE (timeframe = '1d' AND session_date >= current_date - INTERVAL 5 YEAR)
                   OR (timeframe = '60m' AND session_date >= current_date - INTERVAL 90 DAY)
                ORDER BY symbol, timeframe, bar_start
                """
            ).fetchall()
        for row in rows:
            session_date = row[2].date() if isinstance(row[2], datetime) else row[2]
            regime_value = regimes.get((str(row[0]), session_date), "invalid")
            bar = HistoryBar(
                at=row[3],
                open=float(row[4]),
                high=float(row[5]),
                low=float(row[6]),
                close=float(row[7]),
                volume=int(row[8]),
                regime=RegimeLabel(regime_value),
                data_gap=False,
            )
            yield {
                "symbol": row[0],
                "timeframe": row[1],
                "adjustment": "raw",
                "at": row[3],
                "payload_json": bar.model_dump_json(),
            }

    def _health_rows(self, snapshot: ResolvedSnapshot) -> tuple[DataHealth, ...]:
        latest_health = self.storage.latest_health() or {}
        connected = (
            latest_health.get("connection_status") == "connected"
            or latest_health.get("status") == "ok"
        )
        provider = str(latest_health.get("provider") or "opend")
        corporate_actions_dataset = f"{provider}_corporate_actions_retrospective"
        aggregate = DataHealth(
            component="market_data",
            dataset=None,
            symbol=None,
            serving_status=ServingStatus.LIVE if connected else ServingStatus.OFFLINE,
            connectivity=(
                Connectivity.CONNECTED if connected else Connectivity.DISCONNECTED
            ),
            freshness=Freshness.CURRENT if connected else Freshness.STALE,
            completeness=Completeness.COMPLETE,
            readiness=Readiness.READY if connected else Readiness.DEGRADED,
            last_success_at=_optional_datetime(latest_health.get("observed_at")),
            expected_next_at=None,
            coverage_rate=1.0,
            missing_rate=0.0,
            duplicate_count=0,
            quarantine_count=0,
            watermark=snapshot.as_of,
            quota_total=_optional_int(latest_health.get("history_quota_total")),
            quota_used=_optional_int(latest_health.get("history_quota_used")),
            quota_remaining=_optional_int(latest_health.get("history_quota_remaining")),
            error_class=None if connected else "market_data_disconnected",
            message=None
            if connected
            else f"{provider} is offline; serving the last valid snapshot.",
            action=None if connected else "Restore the provider and run live-refresh.",
            affected_symbols=(),
            affected_datasets=(),
        )
        coverage = {
            (str(row["symbol"]), str(row["timeframe"])): row
            for row in self.storage.coverage()
        }
        task_states = {
            (str(row["symbol"]), str(row["timeframe"])): row
            for row in self.storage.latest_task_states()
        }
        rehab = {str(row["symbol"]): row for row in self.storage.rehab_coverage()}
        rows: list[DataHealth] = [aggregate]
        for symbol in self.universe.symbols:
            for timeframe in ("1d", "60m"):
                evidence = coverage.get((symbol, timeframe))
                task = task_states.get((symbol, timeframe))
                available = evidence is not None
                quarantined = task is not None and task.get("status") in {
                    "blocked",
                    "quarantined",
                }
                latest_session = (
                    evidence.get("max_session_date") if evidence is not None else None
                )
                current = latest_session == snapshot.as_of.date().isoformat()
                rows.append(
                    DataHealth(
                        component="market_bars",
                        dataset=f"bars_{timeframe}",
                        symbol=symbol,
                        serving_status=(
                            ServingStatus.LIVE
                            if available and current and not quarantined
                            else ServingStatus.PARTIAL
                        ),
                        connectivity=Connectivity.NOT_APPLICABLE,
                        freshness=(
                            Freshness.CURRENT
                            if available and current
                            else Freshness.STALE
                            if available
                            else Freshness.UNKNOWN
                        ),
                        completeness=(
                            Completeness.COMPLETE if available else Completeness.EMPTY
                        ),
                        readiness=(
                            Readiness.READY
                            if available and current and not quarantined
                            else Readiness.DEGRADED
                        ),
                        last_success_at=(
                            _optional_datetime(evidence["last_ingested_at"])
                            if evidence is not None
                            else None
                        ),
                        expected_next_at=None,
                        coverage_rate=1.0 if available else 0.0,
                        missing_rate=0.0 if available else 1.0,
                        duplicate_count=0,
                        quarantine_count=1 if quarantined else 0,
                        watermark=(
                            _optional_datetime(task.get("watermark"))
                            if task is not None
                            else snapshot.as_of
                            if available
                            else None
                        ),
                        quota_total=None,
                        quota_used=None,
                        quota_remaining=None,
                        error_class=(
                            str(task.get("error_code"))
                            if task is not None and task.get("error_code") is not None
                            else None
                        ),
                        message=(
                            None
                            if available and current and not quarantined
                            else "The latest market-data task is incomplete or stale."
                        ),
                        action=(
                            None
                            if available and current and not quarantined
                            else "Run live-refresh and inspect the task ledger."
                        ),
                        affected_symbols=(),
                        affected_datasets=(),
                    )
                )
            rehab_evidence = rehab.get(symbol)
            rows.append(
                DataHealth(
                    component="rehab",
                    dataset=corporate_actions_dataset,
                    symbol=symbol,
                    serving_status=(
                        ServingStatus.PARTIAL
                        if rehab_evidence is not None
                        else ServingStatus.UNAVAILABLE
                    ),
                    connectivity=Connectivity.NOT_APPLICABLE,
                    freshness=(
                        Freshness.CURRENT
                        if rehab_evidence is not None
                        else Freshness.UNKNOWN
                    ),
                    completeness=(
                        Completeness.COMPLETE
                        if rehab_evidence is not None
                        else Completeness.EMPTY
                    ),
                    readiness=Readiness.DEGRADED,
                    last_success_at=(
                        _optional_datetime(rehab_evidence["observed_at"])
                        if rehab_evidence is not None
                        else None
                    ),
                    expected_next_at=None,
                    coverage_rate=1.0 if rehab_evidence is not None else 0.0,
                    missing_rate=0.0 if rehab_evidence is not None else 1.0,
                    duplicate_count=0,
                    quarantine_count=0,
                    watermark=None,
                    quota_total=None,
                    quota_used=None,
                    quota_remaining=None,
                    error_class=(
                        "retrospective_only"
                        if rehab_evidence is not None
                        else "rehab_unavailable"
                    ),
                    message=(
                        "Retrospective adjustment evidence is versioned but is not "
                        "eligible as historical point-in-time training evidence."
                        if rehab_evidence is not None
                        else "No corporate-action snapshot has been captured."
                    ),
                    action=(
                        "Keep retrospective factors out of point-in-time training."
                        if rehab_evidence is not None
                        else "Run live-refresh to capture rehab evidence."
                    ),
                    affected_symbols=(symbol,),
                    affected_datasets=(corporate_actions_dataset,),
                )
            )
        validation = read_validation(self.paths) or {}
        volume_validation = validation.get("opend_volume_validation") or {}
        assert isinstance(volume_validation, dict)
        gaps = volume_validation.get("retained_source_gaps", [])
        counts = Counter(gap["symbol"] for gap in gaps)
        rows = [
            row.model_copy(
                update={
                    "completeness": Completeness.PARTIAL,
                    "coverage_rate": None,
                    "missing_rate": None,
                    "error_class": "historical_source_gap",
                    "message": f"保留 {counts[row.symbol]} 个历史交易日的分钟线缺口；OpenD 返回空数据。最新收盘数据正常。",
                    "action": "已按用户要求保留原始缺口，后续查漏补缺。",
                    "affected_symbols": (row.symbol,),
                    "affected_datasets": ("bars_60m",),
                }
            )
            if row.dataset == "bars_60m" and row.symbol in counts
            else row
            for row in rows
        ]
        return tuple(rows)


def _screen_row(
    symbol: str,
    horizon: Horizon,
    feature: dict[str, Any],
    snapshot: ResolvedSnapshot,
    forecast: ForecastAvailable | None = None,
    metadata: dict[str, object] | None = None,
    verified_task_predictions: tuple[ResearchTaskPrediction, ...] = (),
    candidate_forecast: ForecastAvailable | None = None,
    training_report: dict[str, object] | None = None,
    *,
    etfs: tuple[str, ...],
) -> ScreenRow:
    horizon_forecast = (
        next(
            (item for item in forecast.bundle.horizons if item.horizon == horizon),
            None,
        )
        if forecast is not None and not forecast.serving.effective_abstain
        else None
    )
    interval_width = (
        horizon_forecast.calibrated_return_intervals[0].upper
        - horizon_forecast.calibrated_return_intervals[0].lower
        if horizon_forecast is not None
        else None
    )
    return ScreenRow(
        symbol=symbol,
        name=_display_name(symbol, metadata),
        asset_type="etf" if symbol in etfs else "stock",
        sector=_sector(symbol, etfs=etfs, metadata=metadata),
        regime=RegimeLabel(str(feature["regime"])),
        horizon=horizon,
        executable_up_probability=(
            horizon_forecast.executable_direction_probabilities.up
            if horizon_forecast is not None
            else None
        ),
        breakout_up_probability=(
            horizon_forecast.breakout_probabilities.up
            if horizon_forecast is not None
            else None
        ),
        breakout_down_probability=(
            horizon_forecast.breakout_probabilities.down
            if horizon_forecast is not None
            else None
        ),
        executable_q50=(
            horizon_forecast.executable_return_quantiles.q50
            if horizon_forecast is not None
            else None
        ),
        executable_q10=(
            horizon_forecast.executable_return_quantiles.q10
            if horizon_forecast is not None
            else None
        ),
        forecast_up_probability=(
            horizon_forecast.forecast_direction_probabilities.up
            if horizon_forecast is not None
            else None
        ),
        capital_gain_q50=(
            horizon_forecast.capital_gain_return_quantiles.q50
            if horizon_forecast is not None
            else None
        ),
        capital_gain_q10=(
            horizon_forecast.capital_gain_return_quantiles.q10
            if horizon_forecast is not None
            else None
        ),
        interval_width=interval_width,
        similar_case_count=(
            forecast.bundle.similar_case_summary.effective_independent_count
            if forecast is not None
            else 0
        ),
        confidence_level=(
            forecast.bundle.confidence_level
            if forecast is not None
            else ConfidenceLevel.UNAVAILABLE
        ),
        data_freshness_seconds=max(
            0, int((datetime.now(UTC) - snapshot.as_of).total_seconds())
        ),
        serving_status=ServingStatus.LIVE,
        effective_abstain=(
            forecast.serving.effective_abstain if forecast is not None else True
        ),
        abstain_reasons=(
            forecast.bundle.abstain_reasons
            if forecast is not None
            else (RejectionReason.NO_CHAMPION_CANDIDATE,)
        ),
        model_version=snapshot.model_version,
        as_of=snapshot.as_of,
        rule_evidence=_rule_evidence(feature),
        verified_research=_verified_research_evidence(
            verified_task_predictions,
            horizon,
        ),
        challenger_preview=_research_evidence(
            candidate_forecast,
            horizon,
            ResearchEvidenceScope.CHALLENGER,
            training_report,
        ),
    )


def _overview(
    latest: dict[str, dict[str, Any]],
    screen_rows: tuple[ScreenRow, ...],
    *,
    snapshot: ResolvedSnapshot,
    generated_at: datetime,
    candidate_summary: CandidateEvaluationSummary | None,
    proxy_trends: dict[str, tuple[float, ...]],
    universe_size: int,
    etfs: tuple[str, ...],
) -> OverviewReadModel:
    five_day = {row.symbol: row for row in screen_rows if row.horizon == 5}
    verified_research_by_symbol = {
        symbol: tuple(
            evidence
            for row in screen_rows
            if row.symbol == symbol and (evidence := row.verified_research) is not None
        )
        for symbol in _PROXY_SYMBOLS
    }
    challenger_research_by_symbol = {
        symbol: tuple(
            evidence
            for row in screen_rows
            if row.symbol == symbol and (evidence := row.challenger_preview) is not None
        )
        for symbol in _PROXY_SYMBOLS
    }
    forecast_ready = snapshot.prediction_readiness is PredictionReadiness.FORECAST_READY
    regimes = Counter(RegimeLabel(str(row["regime"])) for row in latest.values())
    sectors: dict[str, Counter[RegimeLabel]] = defaultdict(Counter)
    for symbol, row in latest.items():
        sector = (
            five_day[symbol].sector
            if symbol in five_day
            else _sector(symbol, etfs=etfs)
        )
        sectors[sector][RegimeLabel(str(row["regime"]))] += 1
    proxies = tuple(
        MarketProxy(
            symbol=symbol,
            name=f"{_display_name(symbol)} market proxy",
            regime=RegimeLabel(str(latest[symbol]["regime"])),
            regime_probability=1.0 if forecast_ready else None,
            direction=(
                _direction_label(five_day[symbol])
                if forecast_ready and symbol in five_day
                else None
            ),
            unavailable_reason=None if forecast_ready else _NO_CHAMPION,
            last_close=_market_close(latest[symbol]),
            session_return=_finite_or_zero(latest[symbol].get("return_1")),
            trend=proxy_trends.get(
                symbol,
                (_market_close(latest[symbol]),),
            ),
            as_of=snapshot.as_of,
            serving_status=ServingStatus.LIVE,
            verified_research_by_horizon=verified_research_by_symbol.get(symbol, ()),
            challenger_preview_by_horizon=challenger_research_by_symbol.get(symbol, ()),
        )
        for symbol in _PROXY_SYMBOLS
    )
    five_day_rows = tuple(five_day.values())
    abstained = tuple(row for row in five_day_rows if row.effective_abstain)
    reason_counts = Counter(
        reason for row in abstained for reason in row.abstain_reasons
    )
    opportunities = tuple(
        sorted(
            (row for row in five_day_rows if not row.effective_abstain),
            key=lambda row: (
                row.executable_up_probability or 0.0,
                row.executable_q50 or 0.0,
            ),
            reverse=True,
        )[:10]
    )
    research_opportunities = _research_opportunities(
        five_day_rows,
        all_rows=screen_rows,
    )
    challenger_opportunities = _research_opportunities(
        five_day_rows,
        all_rows=screen_rows,
        challenger=True,
    )
    return OverviewReadModel(
        proxies=proxies,
        top_opportunities=opportunities,
        research_opportunities=research_opportunities,
        challenger_opportunities=challenger_opportunities,
        regime_distribution=tuple(
            RegimeDistribution(
                regime=regime,
                count=count,
                fraction=count / universe_size,
            )
            for regime, count in sorted(regimes.items(), key=lambda item: item[0].value)
        ),
        sector_regime_matrix=tuple(
            SectorRegimeCell(
                sector=sector,
                regime=regime,
                count=count,
                fraction=count / sum(values.values()),
            )
            for sector, values in sorted(sectors.items())
            for regime, count in sorted(values.items(), key=lambda item: item[0].value)
        ),
        market_breadth=sum(
            _finite_or_zero(row.get("return_20")) > 0 for row in latest.values()
        )
        / universe_size,
        abstain_count=len(abstained),
        abstain_reasons=tuple(
            RejectionReasonCount(reason=reason, count=count)
            for reason, count in sorted(
                reason_counts.items(), key=lambda item: item[0].value
            )
        ),
        data_confidence=1.0,
        batch_generated_at=max(generated_at, snapshot.as_of),
        total_inference_latency_ms=sum(
            0.0 if row.model_version is None else 1.0 for row in five_day_rows
        ),
        candidate_summary=candidate_summary,
    )


def _instrument(
    symbol: str,
    snapshot: ResolvedSnapshot,
    metadata: dict[str, object] | None = None,
    *,
    etfs: tuple[str, ...],
    effective_from: date,
) -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol=symbol,
        name=_display_name(symbol, metadata),
        asset_type="etf" if symbol in etfs else "stock",
        sector=_sector(symbol, etfs=etfs, metadata=metadata),
        currency="USD",
        exchange="US",
        effective_from=datetime.combine(
            effective_from, datetime.min.time(), tzinfo=UTC
        ),
        effective_to=None,
        universe_version=snapshot.universe_version,
        universe_role=(
            "market_proxy"
            if symbol in _PROXY_SYMBOLS
            else "etf_research"
            if symbol in etfs
            else "equity_research"
        ),
        observed_at=(
            _parse_utc(metadata.get("observed_at")) if metadata is not None else None
        ),
        metadata_version=(
            str(metadata["metadata_version"])
            if metadata is not None
            and isinstance(metadata.get("metadata_version"), str)
            else None
        ),
    )


def _model_rows(
    snapshot: ResolvedSnapshot,
    lifecycle: LifecycleSnapshot,
    report: dict[str, object] | None,
    *,
    manual_components: dict[tuple[str, int], str] | None = None,
) -> tuple[ModelTaskMatrixRow, ...]:
    available = snapshot.prediction_readiness is PredictionReadiness.FORECAST_READY
    challenger_version = (
        lifecycle.challenger_versions[-1]
        if report is not None and lifecycle.challenger_versions
        else None
    )
    task_rows = report.get("tasks") if report is not None else None
    if report is not None and isinstance(task_rows, list) and task_rows:
        model_version = report.get("model_version")
        calibration_version = report.get("calibration_version")
        run_id = report.get("run_id")
        generated_at = _parse_utc(report.get("generated_at"))
        component_versions = _component_versions(report)
        return tuple(
            ModelTaskMatrixRow(
                task=(
                    "returns"
                    if row.get("task") == "return_fwd"
                    else str(row.get("task"))
                ),
                horizon=_horizon(row.get("horizon")),
                asset_group="us_equity",
                champion_version=(
                    manual_components[
                        (str(row.get("task")), _horizon(row.get("horizon")))
                    ]
                    if manual_components is not None
                    else component_versions.get(
                        (str(row.get("task")), _horizon(row.get("horizon")))
                    )
                    or (str(model_version) if isinstance(model_version, str) else None)
                    if manual_components is not None or row.get("gate_passed") is True
                    else None
                ),
                baseline_version="configured-baseline-v1",
                challenger_version=(
                    component_versions.get(
                        (str(row.get("task")), _horizon(row.get("horizon")))
                    )
                    or (
                        str(model_version)
                        if isinstance(model_version, str)
                        else challenger_version
                    )
                    if manual_components is None and row.get("gate_passed") is not True
                    else None
                ),
                calibration_version=(
                    str(calibration_version)
                    if (manual_components is not None or row.get("gate_passed") is True)
                    and isinstance(calibration_version, str)
                    else None
                ),
                status=(
                    "champion"
                    if manual_components is not None
                    else "verified_research_component"
                    if row.get("gate_passed") is True
                    else "challenger_gate_failed"
                ),
                unavailable_reason=(
                    None
                    if manual_components is not None or row.get("gate_passed") is True
                    else _NO_CHAMPION
                ),
                gate_passed=row.get("gate_passed") is True,
                fold_count=_optional_int(row.get("fold_count")),
                baseline_wins=_optional_int(row.get("baseline_wins")),
                positive_cost_windows=_optional_int(row.get("positive_cost_windows")),
                pinball_wins=_optional_int(row.get("pinball_wins")),
                coverage_windows_passed=_optional_int(
                    row.get("coverage_windows_passed")
                ),
                evaluation_run_id=str(run_id) if isinstance(run_id, str) else None,
                candidate_generated_at=generated_at,
                challenger_calibration_version=(
                    str(calibration_version)
                    if isinstance(calibration_version, str)
                    else None
                ),
                statistical_gate_passed=_optional_bool(
                    row.get("statistical_gate_passed")
                ),
                calibration_gate_passed=_optional_bool(
                    row.get("calibration_gate_passed")
                ),
                stability_gate_passed=_optional_bool(row.get("stability_gate_passed")),
                promotion_window_passed=_optional_bool(
                    row.get("promotion_window_passed")
                ),
                failure_codes=tuple(
                    str(code)
                    for code in row.get("failure_codes", ())
                    if isinstance(code, str)
                )
                if isinstance(row.get("failure_codes"), (list, tuple))
                else (),
                component_validated_at=generated_at,
            )
            for raw in task_rows
            if isinstance(raw, dict)
            for row in (raw,)
            if isinstance(row.get("horizon"), int) and isinstance(row.get("task"), str)
        )
    return tuple(
        ModelTaskMatrixRow(
            task=task,
            horizon=horizon,
            asset_group="us_equity",
            champion_version=snapshot.model_version,
            baseline_version="configured-baseline-v1",
            challenger_version=challenger_version,
            calibration_version=snapshot.calibration_version,
            status="champion" if available else "configured",
            unavailable_reason=None if available else _NO_CHAMPION,
        )
        for task in ("forecast_direction", "breakout", "range_exit", "returns")
        for horizon in (1, 5, 20)
        if not (task == "range_exit" and horizon == 1)
    )


def _task_evaluation_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    """Summarize complete task folds, never infer a symbol-specific score."""
    folds = metrics.get("folds")
    if (
        not isinstance(folds, list)
        or not folds
        or len(folds) != metrics.get("fold_count")
        or not all(isinstance(fold, dict) for fold in folds)
    ):
        return {}
    identities = [fold.get("fold_id") for fold in folds]
    if not all(isinstance(identity, str) for identity in identities):
        return {}
    if len(set(identities)) != len(identities):
        return {}
    counts = [fold.get("sample_count") for fold in folds]
    if not all(type(count) is int and count > 0 for count in counts):
        return {}
    total = sum(counts)
    result: dict[str, Any] = {"evaluation_sample_count": total}
    try:
        starts = [date.fromisoformat(fold["test_start"]) for fold in folds]
        ends = [date.fromisoformat(fold["test_end"]) for fold in folds]
        if all(start <= end for start, end in zip(starts, ends, strict=True)):
            result.update(evaluation_start=min(starts), evaluation_end=max(ends))
    except (KeyError, TypeError, ValueError):
        pass  # Older quantile reports have no dates; do not borrow other tasks' dates.
    for source, target in (
        ("cqr_coverage", "worst_coverage"),
        ("cqr_average_width", "average_interval_width"),
        ("candidate_pinball", "average_pinball_loss"),
        ("baseline_pinball", "baseline_pinball_loss"),
    ):
        values = [fold.get(source) for fold in folds]
        if not all(
            type(value) in (int, float) and math.isfinite(value) and value >= 0
            for value in values
        ):
            continue
        if source == "cqr_coverage":
            if max(values) <= 1:
                result[target] = float(min(values))
        else:
            result[target] = (
                sum(
                    float(value) * count
                    for value, count in zip(values, counts, strict=True)
                )
                / total
            )
    return result


def _verified_research_evidence(
    predictions: tuple[ResearchTaskPrediction, ...],
    horizon: Horizon,
) -> ResearchModelEvidence | None:
    matching = tuple(item for item in predictions if item.horizon == horizon)
    if not matching:
        return None
    by_task = {item.task: item for item in matching}
    direction = by_task.get("forecast_direction")
    breakout = by_task.get("breakout")
    returns = by_task.get("return_fwd")
    mfe = by_task.get("mfe")
    mae = by_task.get("mae")
    range_exit = by_task.get("range_exit")
    value_fields = {
        "forecast_up_probability": (
            direction.probabilities.get(1)
            if direction is not None and direction.probabilities is not None
            else None
        ),
        "breakout_up_probability": (
            breakout.probabilities.get(1)
            if breakout is not None and breakout.probabilities is not None
            else None
        ),
        "breakout_down_probability": (
            breakout.probabilities.get(-1)
            if breakout is not None and breakout.probabilities is not None
            else None
        ),
        "range_exit_probability": (
            range_exit.range_exit_probability if range_exit is not None else None
        ),
        "capital_gain_q10": (
            returns.quantiles.q10
            if returns is not None and returns.quantiles is not None
            else None
        ),
        "capital_gain_q50": (
            returns.quantiles.q50
            if returns is not None and returns.quantiles is not None
            else None
        ),
        "capital_gain_q90": (
            returns.quantiles.q90
            if returns is not None and returns.quantiles is not None
            else None
        ),
        "calibrated_lower": (
            returns.quantiles.q10 - (returns.cqr_adjustment or 0.0)
            if returns is not None and returns.quantiles is not None
            else None
        ),
        "calibrated_upper": (
            returns.quantiles.q90 + (returns.cqr_adjustment or 0.0)
            if returns is not None and returns.quantiles is not None
            else None
        ),
        "mfe_q50": (
            mfe.quantiles.q50 if mfe is not None and mfe.quantiles is not None else None
        ),
        "mae_q50": (
            mae.quantiles.q50 if mae is not None and mae.quantiles is not None else None
        ),
    }
    if not any(value is not None for value in value_fields.values()):
        return None
    tasks = tuple(
        TaskForecastEvidence(
            task=item.task,
            horizon=horizon,
            scope=ResearchEvidenceScope.VERIFIED,
            gate_passed=True,
            model_version=item.model_version,
            calibration_version=item.calibration_version,
            evaluation_run_id=item.evaluation_run_id,
            component_evaluated_at=item.component_evaluated_at,
            fold_count=_optional_int(item.gate_metrics.get("fold_count")) or 0,
            baseline_wins=_optional_int(item.gate_metrics.get("baseline_wins")),
            positive_cost_windows=_optional_int(
                item.gate_metrics.get("positive_cost_windows")
            ),
            pinball_wins=_optional_int(item.gate_metrics.get("pinball_wins")),
            coverage_windows_passed=_optional_int(
                item.gate_metrics.get("coverage_windows_passed")
            ),
            **_task_evaluation_summary(item.gate_metrics),
        )
        for item in matching
    )
    sample_reasons = tuple(
        dict.fromkeys(
            reason for item in matching for reason in item.sample_abstain_reasons
        )
    )
    return ResearchModelEvidence(
        scope=ResearchEvidenceScope.VERIFIED,
        symbol=matching[0].symbol,
        horizon=horizon,
        as_of=matching[0].reference_price_at,
        values=ResearchModelValues(**value_fields),
        price_projection=_component_price_projection(by_task),
        tasks=tasks,
        sample_abstain_reasons=sample_reasons,
    )


def _component_price_projection(
    by_task: dict[str, ResearchTaskPrediction],
) -> ResearchPriceProjection:
    reference = next(iter(by_task.values()))
    returns = by_task.get("return_fwd")
    mfe = by_task.get("mfe")
    mae = by_task.get("mae")
    return_quantiles = returns.quantiles if returns is not None else None
    mfe_quantiles = mfe.quantiles if mfe is not None else None
    mae_quantiles = mae.quantiles if mae is not None else None
    return ResearchPriceProjection(
        reference_price=reference.reference_price,
        reference_price_at=reference.reference_price_at,
        currency="USD",
        endpoint_price_band=(
            PriceBand(
                **{
                    key: reference.reference_price * (1.0 + value)
                    for key, value in return_quantiles.model_dump().items()
                }
            )
            if return_quantiles is not None
            else None
        ),
        endpoint_price_unavailable_reason=(
            None if return_quantiles is not None else "return_fwd_model_not_verified"
        ),
        stage_high_price_q50=(
            reference.reference_price * (1.0 + mfe_quantiles.q50)
            if mfe_quantiles is not None
            else None
        ),
        stage_high_price_unavailable_reason=(
            None if mfe_quantiles is not None else "mfe_model_not_verified"
        ),
        stage_low_price_q50=(
            reference.reference_price * (1.0 + mae_quantiles.q50)
            if mae_quantiles is not None
            else None
        ),
        stage_low_price_unavailable_reason=(
            None if mae_quantiles is not None else "mae_model_not_verified"
        ),
    )


def _research_evidence(
    forecast: ForecastAvailable | None,
    horizon: Horizon,
    scope: ResearchEvidenceScope,
    report: dict[str, object] | None,
) -> ResearchModelEvidence | None:
    if forecast is None or report is None:
        return None
    horizon_forecast = next(
        (item for item in forecast.bundle.horizons if item.horizon == horizon),
        None,
    )
    if horizon_forecast is None:
        return None
    report_tasks = report.get("tasks")
    if not isinstance(report_tasks, list):
        return None
    wanted_pass = scope is ResearchEvidenceScope.VERIFIED
    matching = tuple(
        row
        for raw in report_tasks
        if isinstance(raw, dict)
        for row in (raw,)
        if row.get("horizon") == horizon
        and (row.get("gate_passed") is True) is wanted_pass
        and isinstance(row.get("task"), str)
    )
    if not matching:
        return None
    release_versions = _component_versions(report)
    default_model_version = report.get("model_version")
    calibration_version = report.get("calibration_version")
    run_id = report.get("run_id")
    if not isinstance(default_model_version, str) or not isinstance(run_id, str):
        return None
    tasks = tuple(
        TaskForecastEvidence(
            task=str(row["task"]),
            horizon=horizon,
            scope=scope,
            gate_passed=wanted_pass,
            model_version=release_versions.get(
                (str(row["task"]), horizon),
                default_model_version,
            ),
            calibration_version=(
                str(calibration_version)
                if isinstance(calibration_version, str)
                else None
            ),
            evaluation_run_id=run_id,
            component_evaluated_at=_parse_utc(report.get("generated_at")),
            fold_count=_optional_int(row.get("fold_count")) or 0,
            baseline_wins=_optional_int(row.get("baseline_wins")),
            positive_cost_windows=_optional_int(row.get("positive_cost_windows")),
            pinball_wins=_optional_int(row.get("pinball_wins")),
            coverage_windows_passed=_optional_int(row.get("coverage_windows_passed")),
            **_task_evaluation_summary(row),
        )
        for row in matching
    )
    task_names = {task.task for task in tasks}
    capital_interval = next(
        (
            interval
            for interval in horizon_forecast.calibrated_return_intervals
            if interval.return_basis is ReturnBasis.CAPITAL_GAIN
        ),
        None,
    )
    values = ResearchModelValues(
        forecast_up_probability=(
            horizon_forecast.forecast_direction_probabilities.up
            if "forecast_direction" in task_names
            else None
        ),
        breakout_up_probability=(
            horizon_forecast.breakout_probabilities.up
            if "breakout" in task_names
            else None
        ),
        breakout_down_probability=(
            horizon_forecast.breakout_probabilities.down
            if "breakout" in task_names
            else None
        ),
        range_exit_probability=(
            horizon_forecast.range_exit_probability
            if "range_exit" in task_names
            else None
        ),
        capital_gain_q10=(
            horizon_forecast.capital_gain_return_quantiles.q10
            if "return_fwd" in task_names
            else None
        ),
        capital_gain_q50=(
            horizon_forecast.capital_gain_return_quantiles.q50
            if "return_fwd" in task_names
            else None
        ),
        capital_gain_q90=(
            horizon_forecast.capital_gain_return_quantiles.q90
            if "return_fwd" in task_names
            else None
        ),
        calibrated_lower=(
            capital_interval.lower
            if "return_fwd" in task_names and capital_interval is not None
            else None
        ),
        calibrated_upper=(
            capital_interval.upper
            if "return_fwd" in task_names and capital_interval is not None
            else None
        ),
        mfe_q50=(
            horizon_forecast.forecast_mfe_quantiles.q50 if "mfe" in task_names else None
        ),
        mae_q50=(
            horizon_forecast.forecast_mae_quantiles.q50 if "mae" in task_names else None
        ),
    )
    return ResearchModelEvidence(
        scope=scope,
        symbol=forecast.bundle.symbol,
        horizon=horizon,
        as_of=forecast.bundle.as_of,
        values=values,
        price_projection=ResearchPriceProjection(
            reference_price=horizon_forecast.reference_price,
            reference_price_at=horizon_forecast.reference_price_at,
            currency=horizon_forecast.currency,
            endpoint_price_band=(
                horizon_forecast.price_band if "return_fwd" in task_names else None
            ),
            endpoint_price_unavailable_reason=(
                None
                if "return_fwd" in task_names
                else "return_fwd_not_in_candidate_scope"
            ),
            stage_high_price_q50=(
                horizon_forecast.reference_price
                * (1.0 + horizon_forecast.forecast_mfe_quantiles.q50)
                if "mfe" in task_names
                else None
            ),
            stage_high_price_unavailable_reason=(
                None if "mfe" in task_names else "mfe_not_in_candidate_scope"
            ),
            stage_low_price_q50=(
                horizon_forecast.reference_price
                * (1.0 + horizon_forecast.forecast_mae_quantiles.q50)
                if "mae" in task_names
                else None
            ),
            stage_low_price_unavailable_reason=(
                None if "mae" in task_names else "mae_not_in_candidate_scope"
            ),
        ),
        tasks=tasks,
        sample_abstain_reasons=forecast.bundle.abstain_reasons,
        experimental_warning=(
            _EXPERIMENTAL_WARNING if scope is ResearchEvidenceScope.CHALLENGER else None
        ),
    )


def _component_versions(
    report: dict[str, object],
) -> dict[tuple[str, Horizon], str]:
    raw_releases = report.get("component_releases")
    if not isinstance(raw_releases, list):
        return {}
    result: dict[tuple[str, Horizon], str] = {}
    for raw in raw_releases:
        if not isinstance(raw, dict):
            continue
        task = raw.get("task")
        horizon = raw.get("horizon")
        model_version = raw.get("model_version")
        if (
            isinstance(task, str)
            and horizon in _HORIZONS
            and isinstance(model_version, str)
        ):
            result[(task, _horizon(horizon))] = model_version
    return result


def _research_opportunities(
    rows: tuple[ScreenRow, ...],
    *,
    all_rows: tuple[ScreenRow, ...] | None = None,
    challenger: bool = False,
) -> tuple[ResearchOpportunityRow, ...]:
    candidates = tuple(
        row
        for row in rows
        if row.rule_evidence is not None
        and (row.challenger_preview is not None if challenger else True)
    )

    def evidence_for(row: ScreenRow) -> ResearchModelEvidence | None:
        return row.challenger_preview if challenger else row.verified_research

    evidence_rows = all_rows or rows
    evidence_by_symbol = {
        symbol: tuple(
            evidence
            for candidate in evidence_rows
            if candidate.symbol == symbol
            and (evidence := evidence_for(candidate)) is not None
        )
        for symbol in {row.symbol for row in rows}
    }

    def rank_key(row: ScreenRow) -> tuple[object, ...]:
        evidence = evidence_for(row)
        values = evidence.values if evidence is not None else None
        rule = row.rule_evidence
        assert rule is not None
        regime_rank = {
            RegimeLabel.UPTREND: 0,
            RegimeLabel.RANGE: 1,
            RegimeLabel.TRANSITION: 2,
            RegimeLabel.DOWNTREND: 3,
            RegimeLabel.INVALID: 4,
        }[row.regime]
        return (
            0 if rule.breakout_up_rule else 1,
            -(
                values.capital_gain_q10
                if values and values.capital_gain_q10 is not None
                else -1e9
            ),
            -(
                values.capital_gain_q50
                if values and values.capital_gain_q50 is not None
                else -1e9
            ),
            regime_rank,
            -(rule.return_20 if rule.return_20 is not None else -1e9),
            -(
                rule.channel_position_20
                if rule.channel_position_20 is not None
                else -1e9
            ),
            row.symbol,
        )

    selected = sorted(candidates, key=rank_key)[:10]
    return tuple(
        ResearchOpportunityRow(
            symbol=row.symbol,
            name=row.name,
            asset_type=row.asset_type,
            sector=row.sector,
            regime=row.regime,
            horizon=row.horizon,
            rule_evidence=row.rule_evidence,
            verified_research=(None if challenger else row.verified_research),
            challenger_preview=(row.challenger_preview if challenger else None),
            verified_research_by_horizon=(
                () if challenger else evidence_by_symbol.get(row.symbol, ())
            ),
            challenger_preview_by_horizon=(
                evidence_by_symbol.get(row.symbol, ()) if challenger else ()
            ),
            rank_basis=_rank_basis(row, challenger=challenger),
            as_of=row.as_of,
        )
        for row in selected
        if row.rule_evidence is not None
    )


def _rank_basis(
    row: ScreenRow,
    *,
    challenger: bool,
) -> tuple[str, ...]:
    evidence = row.challenger_preview if challenger else row.verified_research
    result = ["规则型向上突破优先", "趋势和20日相对强弱排序"]
    if evidence is not None and evidence.values.capital_gain_q10 is not None:
        result.insert(1, "收益q10（偏弱收益情景）优先")
    if evidence is not None and evidence.values.capital_gain_q50 is not None:
        result.insert(2, "收益q50（中位收益情景）优先")
    return tuple(result)


def _proxy_trends(frame: Any) -> dict[str, tuple[float, ...]]:
    proxy_frame = frame[frame["symbol"].isin(_PROXY_SYMBOLS)].copy()
    proxy_frame = proxy_frame.sort_values(["symbol", "session_date"])
    result: dict[str, tuple[float, ...]] = {}
    for symbol, group in proxy_frame.groupby("symbol", sort=False):
        values = tuple(
            value
            for row in group.tail(20).to_dict(orient="records")
            if (value := _market_close(row)) > 0
        )
        if values:
            result[str(symbol)] = values
    return result


def _task_gate_counts(
    report: dict[str, object] | None,
) -> tuple[int, int]:
    tasks = report.get("tasks") if report is not None else None
    if not isinstance(tasks, list):
        return 0, 0
    valid = tuple(row for row in tasks if isinstance(row, dict))
    return sum(row.get("gate_passed") is True for row in valid), len(valid)


def _direction_label(row: ScreenRow) -> Literal["up", "flat", "down"]:
    up = row.executable_up_probability or 0.0
    median = row.executable_q50 or 0.0
    if up >= 0.5 and median > 0:
        return "up"
    if up <= 0.3 and median < 0:
        return "down"
    return "flat"


def _table(path: str, rows: Iterable[dict[str, object]]) -> pa.Table:
    return pa.Table.from_pylist(list(rows), schema=RESEARCH_TABLE_SCHEMAS[path])


def _display_name(symbol: str, metadata: dict[str, object] | None = None) -> str:
    if metadata is not None:
        name = metadata.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return symbol.removeprefix("US.")


def _market_close(feature: dict[str, Any]) -> float:
    market_close = _optional_finite(feature.get("raw_close"))
    return (
        _finite_or_zero(feature.get("close")) if market_close is None else market_close
    )


def _rule_evidence(feature: dict[str, Any]) -> RuleEvidence:
    adjusted_close = _finite_or_zero(feature.get("close"))
    close = _market_close(feature)
    low = _optional_finite(feature.get("channel_low_20"))
    high = _optional_finite(feature.get("channel_high_20"))
    position = (
        (adjusted_close - low) / (high - low)
        if low is not None and high is not None and high > low
        else None
    )
    return RuleEvidence(
        last_close=close,
        session_return=_optional_finite(feature.get("return_1")),
        return_5=_optional_finite(feature.get("return_5")),
        return_20=_optional_finite(feature.get("return_20")),
        atr_pct_20=_optional_finite(feature.get("atr_pct_20")),
        channel_position_20=position,
        channel_width_20=_optional_finite(feature.get("channel_width_20")),
        volume_z_20=_optional_finite(feature.get("volume_z_20")),
        breakout_up_rule=bool(feature.get("breakout_up_rule")),
        breakout_down_rule=bool(feature.get("breakout_down_rule")),
        intraday_available=bool(feature.get("intraday_available")),
    )


def _optional_finite(value: object) -> float | None:
    try:
        converted = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _latest_training_report(
    paths: LivePaths, *, universe_version: str
) -> dict[str, object] | None:
    try:
        report = load_latest_training_report(paths)
    except FileNotFoundError:
        return None
    return report if report.get("universe_version") == universe_version else None


def _candidate_summary(
    report: dict[str, object] | None,
) -> CandidateEvaluationSummary | None:
    if report is None:
        return None
    task_rows = report.get("tasks")
    if not isinstance(task_rows, list):
        return None
    tasks = tuple(row for row in task_rows if isinstance(row, dict))
    model_version = report.get("model_version")
    calibration_version = report.get("calibration_version")
    run_id = report.get("run_id")
    generated_at = _parse_utc(report.get("generated_at"))
    if (
        not tasks
        or not isinstance(model_version, str)
        or not isinstance(calibration_version, str)
        or not isinstance(run_id, str)
        or generated_at is None
    ):
        return None
    passed = sum(row.get("gate_passed") is True for row in tasks)
    raw_failure_codes = report.get("failure_codes")
    failure_codes = (
        tuple(str(code) for code in raw_failure_codes if isinstance(code, str))
        if isinstance(raw_failure_codes, (list, tuple))
        else ()
    )
    return CandidateEvaluationSummary(
        run_id=run_id,
        model_version=model_version,
        calibration_version=calibration_version,
        generated_at=generated_at,
        passed_tasks=passed,
        total_tasks=len(tasks),
        all_gates_passed=passed == len(tasks),
        failed_tasks=tuple(
            f"{_task_display_name(row.get('task'))} · {row.get('horizon')}日"
            for row in tasks
            if row.get("gate_passed") is not True
        ),
        data_quality_status=(
            str(report["data_quality_status"])
            if isinstance(report.get("data_quality_status"), str)
            else None
        ),
        label_health_status=(
            str(report["label_health_status"])
            if isinstance(report.get("label_health_status"), str)
            else None
        ),
        promotion_window_status=(
            str(report["promotion_window_status"])
            if isinstance(report.get("promotion_window_status"), str)
            else None
        ),
        execution_readiness=(
            ExecutionReadiness.READY
            if report.get("execution_readiness") == "ready"
            else ExecutionReadiness.BLOCKED
        ),
        failure_codes=failure_codes,
    )


def _task_display_name(value: object) -> str:
    return {
        "forecast_direction": "方向预测",
        "breakout": "突破预测",
        "range_exit": "震荡退出",
        "return_fwd": "收益分布",
        "mfe": "最大有利波动",
        "mae": "最大不利波动",
    }.get(str(value), "未知任务")


def _sector(
    symbol: str,
    *,
    etfs: tuple[str, ...] = (),
    metadata: dict[str, object] | None = None,
) -> str:
    ticker = symbol.removeprefix("US.")
    if symbol in etfs:
        return "etf"
    if metadata is not None:
        sector = metadata.get("sector")
        if (
            isinstance(sector, str)
            and sector
            in {
                "technology",
                "consumer",
                "financials",
                "healthcare",
                "industrials",
                "energy_materials",
                "communication",
                "communication_services",
                "consumer_discretionary",
                "consumer_staples",
                "energy",
                "materials",
                "real_estate",
                "utilities",
                "other",
            }
            and isinstance(metadata.get("sector_source"), str)
            and str(metadata["sector_source"]).strip()
            and isinstance(metadata.get("sector_version"), str)
            and str(metadata["sector_version"]).strip()
        ):
            return sector
    groups = (
        (
            "technology",
            "AAPL MSFT NVDA GOOGL META AVGO ORCL CRM AMD INTC QCOM MU AMAT LRCX KLAC TXN CSCO NFLX NOW",
        ),
        ("consumer", "TSLA WMT COST HD MCD NKE SBUX LOW TGT DIS BKNG CMG PG KO PEP"),
        ("financials", "JPM BAC WFC GS MS C SCHW BLK AXP V MA COF"),
        ("healthcare", "LLY UNH JNJ ABBV MRK TMO AMGN GILD ISRG PFE BMY MDT ABT"),
        ("industrials", "GE CAT BA RTX LMT DE UPS FDX ETN HON"),
        ("energy_materials", "XOM CVX COP SLB EOG NEE LIN FCX PLD"),
        ("communication", "T"),
    )
    for sector, values in groups:
        if ticker in values.split():
            return sector
    return "other"


def _finite_or_zero(value: object) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed and abs(parsed) != float("inf") else 0.0


def _optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return None


def _horizon(value: object) -> Horizon:
    parsed = _optional_int(value)
    if parsed == 1:
        return 1
    if parsed == 5:
        return 5
    if parsed == 20:
        return 20
    raise ValueError("unsupported_training_horizon")


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


__all__ = ["LiveSnapshotPublisher"]
