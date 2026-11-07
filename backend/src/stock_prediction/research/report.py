"""M3 audit summaries and a deterministic, fully offline M2-to-M3 demo."""

from __future__ import annotations

from argparse import Namespace
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter

import pyarrow as pa

from stock_prediction.contracts.research import (
    CalendarSource,
    FeatureVector,
    FundamentalObservation,
    LabelBundle,
    PointInTimeEligibility,
    RegimeState,
)
from stock_prediction.research.calendar import (
    TradingCalendarArtifact,
    build_trading_calendar,
)
from stock_prediction.features.core import build_core_feature_table
from stock_prediction.features.context import (
    BreadthObservation,
    ReturnObservation,
    SectorMapping,
    UniverseMembership,
    compute_market_breadth,
    compute_relative_context,
)
from stock_prediction.features.dag import FeatureCacheKey, FeatureDag, FeatureNode
from stock_prediction.features.intraday import (
    HourlyBar,
    IntradaySessionSpec,
    aggregate_intraday_session,
)
from stock_prediction.features.store import FeatureArtifactStore
from stock_prediction.features.transforms import (
    TransformInputRow,
    TransformedFeatureRow,
    TransformerArtifact,
    TransformerTrainingSet,
    apply_transformer,
    fit_transformer,
)
from stock_prediction.research.asof import (
    MissingObservationReason,
    select_observations_as_of,
)
from stock_prediction.research.dataset import (
    FeatureArtifactEvidence,
    ResearchDataset,
    assemble_research_dataset,
    compute_feature_values_hash,
)
from stock_prediction.research.labels import (
    LabelBar,
    LabelConfig,
    RegimeAnchor,
    assign_regime_state,
    generate_label_bundle,
)
from stock_prediction.research.primitives import (
    DailyFeatureInput,
    MarketPrimitives,
    compute_market_primitives,
    compute_market_primitives_batch,
)
from stock_prediction.research.thresholds import (
    RegimeThresholdArtifact,
    RegimeThresholdTrainingSet,
    ThresholdFitRow,
    fit_regime_thresholds,
)


DATASET_REVISION = "offline-m3-dataset-r1"
ADJUSTMENT_REVISION = "offline-m3-adjustment-r1"
FEATURE_VERSION = "offline-core-v1"
UNIVERSE_VERSION = "offline-universe-v1"
LABEL_VERSION = "offline-labels-v1"
THRESHOLD_VERSION = "offline-thresholds-v1"
TRANSFORMER_VERSION = "offline-transform-v1"


def run_m3_command(args: Namespace) -> dict[str, object]:
    if str(args.command) != "m3-offline-demo":
        raise ValueError("unsupported M3 command")
    return offline_demo(Path(args.work_root))


def offline_demo(work_root: Path) -> dict[str, object]:
    """Build a synthetic research dataset from M2-shaped adjusted daily evidence."""

    started = perf_counter()
    if work_root.exists() and any(work_root.iterdir()):
        raise FileExistsError("offline demo root must be empty")
    work_root.mkdir(parents=True, exist_ok=True)

    sessions = _weekdays(date(2025, 1, 2), 95)
    calendar = build_trading_calendar(
        sessions=sessions,
        calendar_version="offline-synthetic-calendar-v1",
        session_rule_version="offline-synthetic-session-v1",
        dataset_revision=DATASET_REVISION,
        source=CalendarSource.SYNTHETIC_OFFLINE,
        source_reference_ids=("synthetic-weekday-generator-v1",),
        generated_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    inputs = {
        "US.AAPL": _daily_inputs("US.AAPL", "stock", sessions),
        "US.SPY": _daily_inputs("US.SPY", "etf", sessions),
    }
    primitive_batch = compute_market_primitives_batch(
        inputs,
        calendar=calendar,
        feature_set_version=FEATURE_VERSION,
    )
    primitives_by_symbol = {
        symbol: tuple(item for item in primitive_batch if item.symbol == symbol)
        for symbol in inputs
    }
    thresholds = _fit_thresholds(primitives_by_symbol)
    states = _build_states(inputs, primitives_by_symbol, thresholds)
    bundles = _build_labels(inputs, primitives_by_symbol, states, calendar)
    feature_vectors = _build_feature_vectors(primitives_by_symbol, states, bundles)
    artifact_hashes, artifact_evidence, feature_vectors = _write_feature_artifacts(
        work_root / "features", feature_vectors, primitives_by_symbol
    )
    transformer, transformed_rows = _fit_and_apply_transformer(feature_vectors)
    dataset = assemble_research_dataset(
        feature_vectors,
        bundles,
        transformed_rows,
        transformer,
        eligible_adjustment_revisions={ADJUSTMENT_REVISION},
        eligible_feature_artifacts=artifact_evidence,
    )
    engineering_audit = _engineering_path_audit(
        inputs,
        sessions,
        batch_primitive_symbol_count=len(primitives_by_symbol),
        batch_core_row_count=len(artifact_hashes),
    )
    future_sentinel = _future_feature_sentinel(inputs["US.AAPL"], calendar)
    forward_only_sentinel = _forward_only_sentinel()
    report = _audit_summary(
        dataset=dataset,
        feature_vectors=feature_vectors,
        bundles=bundles,
        primitives_by_symbol=primitives_by_symbol,
        artifact_hashes=artifact_hashes,
        future_sentinel=future_sentinel,
        forward_only_sentinel=forward_only_sentinel,
        runtime_seconds=perf_counter() - started,
        engineering_audit=engineering_audit,
    )
    (work_root / "m3-audit-report.json").write_text(
        json.dumps(
            report,
            ensure_ascii=True,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return report


def _weekdays(start: date, count: int) -> tuple[date, ...]:
    output: list[date] = []
    current = start
    while len(output) < count:
        if current.weekday() < 5:
            output.append(current)
        current += timedelta(days=1)
    return tuple(output)


def _daily_inputs(
    symbol: str,
    asset_group: str,
    sessions: tuple[date, ...],
) -> tuple[DailyFeatureInput, ...]:
    base = 100.0 if symbol == "US.AAPL" else 400.0
    output: list[DailyFeatureInput] = []
    for index, session in enumerate(sessions):
        if symbol == "US.AAPL":
            close = (
                base + 2.0 * math.sin(index / 3)
                if index < 55
                else base + 0.9 * (index - 55) + math.sin(index / 4)
            )
        else:
            close = base + 0.18 * index + 1.5 * math.sin(index / 7)
        available_at = datetime.combine(session, time(21, 0), tzinfo=UTC)
        output.append(
            DailyFeatureInput(
                symbol=symbol,
                asset_group=asset_group,
                session_date=session,
                available_at=available_at,
                ingested_at=available_at + timedelta(minutes=1),
                adjusted_high=close + 1.0,
                adjusted_low=close - 1.0,
                adjusted_close=close,
                adjusted_volume=1_000_000.0 + index * 10_000.0,
                turnover=close * (1_000_000.0 + index * 10_000.0),
                complete_regular_session=True,
                dataset_revision=DATASET_REVISION,
                adjustment_revision=ADJUSTMENT_REVISION,
            )
        )
    return tuple(output)


def _fit_thresholds(
    primitives_by_symbol: dict[str, tuple[MarketPrimitives, ...]],
) -> dict[str, RegimeThresholdArtifact]:
    rows: list[ThresholdFitRow] = []
    for primitives in primitives_by_symbol.values():
        eligible = [item for item in primitives if item.valid_core][:30]
        rows.extend(
            ThresholdFitRow(
                symbol=item.symbol,
                asset_group=item.asset_group,
                as_of=item.session_date,
                source_max_available_at=item.source_max_available_at,
                dataset_revision=item.dataset_revision,
                feature_set_version=item.feature_set_version,
                calendar_version=item.calendar_version,
                calendar_hash=item.calendar_hash,
                calendar_source=item.calendar_source,
                channel_width=item.channel_width20 or 0.0,
                efficiency_ratio20=item.efficiency_ratio20 or 0.0,
                absolute_normalized_slope20=abs(item.normalized_slope20 or 0.0),
                realized_volatility20=item.realized_volatility20 or 0.0,
            )
            for item in eligible
        )
    train_start = min(item.as_of for item in rows)
    train_end = max(item.as_of for item in rows)
    artifacts = fit_regime_thresholds(
        RegimeThresholdTrainingSet(
            train_start=train_start,
            train_end=train_end,
            rows=tuple(rows),
            dataset_revision=DATASET_REVISION,
            feature_set_version=FEATURE_VERSION,
            calendar_version=rows[0].calendar_version,
            calendar_hash=rows[0].calendar_hash,
            calendar_source=rows[0].calendar_source,
        ),
        threshold_version=THRESHOLD_VERSION,
        fitted_at=datetime.combine(
            train_end + timedelta(days=1), time(0, 0), tzinfo=UTC
        ),
    )
    return {item.asset_group: item for item in artifacts}


def _build_states(
    inputs: dict[str, tuple[DailyFeatureInput, ...]],
    primitives_by_symbol: dict[str, tuple[MarketPrimitives, ...]],
    thresholds: dict[str, RegimeThresholdArtifact],
) -> dict[tuple[str, date], RegimeState]:
    output: dict[tuple[str, date], RegimeState] = {}
    by_symbol_date = {
        (item.symbol, item.session_date): item
        for rows in inputs.values()
        for item in rows
    }
    training_widths = {
        group: sorted(
            item.channel_width20
            for rows in primitives_by_symbol.values()
            for item in rows
            if item.asset_group == group
            and item.valid_core
            and item.channel_width20 is not None
            and thresholds[group].train_start
            <= item.session_date
            <= thresholds[group].train_end
        )
        for group in thresholds
    }
    for rows in primitives_by_symbol.values():
        for item in rows:
            bar = by_symbol_date[(item.symbol, item.session_date)]
            threshold = thresholds[item.asset_group]
            if threshold.fitted_at > bar.available_at:
                continue
            widths = training_widths[item.asset_group]
            percentile = (
                sum(value <= (item.channel_width20 or 0.0) for value in widths)
                / len(widths)
                if widths
                else 1.0
            )
            slope = item.normalized_slope20 or 0.0
            state = assign_regime_state(
                RegimeAnchor(
                    symbol=item.symbol,
                    as_of=bar.available_at,
                    adjusted_high=bar.adjusted_high,
                    adjusted_low=bar.adjusted_low,
                    adjusted_close=bar.adjusted_close,
                    complete_regular_session=True,
                    corporate_action_clean=True,
                    higher_highs=slope > 0,
                    higher_lows=slope > 0,
                    lower_highs=slope < 0,
                    lower_lows=slope < 0,
                    compression_percentile=percentile,
                    dataset_revision=DATASET_REVISION,
                ),
                item,
                threshold,
                atr_buffer_multiplier=0.25,
                label_version=LABEL_VERSION,
            )
            output[(item.symbol, item.session_date)] = state
    return output


def _label_bar(
    row: DailyFeatureInput,
    *,
    opening: float,
    initial_close: float,
    state: RegimeState,
) -> LabelBar:
    low = min(row.adjusted_low, opening)
    high = max(row.adjusted_high, opening)
    return LabelBar(
        symbol=row.symbol,
        session_date=row.session_date,
        bar_at=row.available_at,
        adjusted_open=opening,
        adjusted_high=high,
        adjusted_low=low,
        adjusted_close=row.adjusted_close,
        total_return_index=row.adjusted_close / initial_close,
        cash_distribution=0.0,
        complete_regular_session=True,
        corporate_action_clean=True,
        regime_state=state.state,
        dataset_revision=DATASET_REVISION,
        adjustment_revision=ADJUSTMENT_REVISION,
    )


def _build_labels(
    inputs: dict[str, tuple[DailyFeatureInput, ...]],
    primitives_by_symbol: dict[str, tuple[MarketPrimitives, ...]],
    states: dict[tuple[str, date], RegimeState],
    calendar: TradingCalendarArtifact,
) -> list[LabelBundle]:
    del primitives_by_symbol
    config = LabelConfig(
        label_version=LABEL_VERSION,
        atr_buffer_multiplier=0.25,
        flat_band_atr_multiplier=0.10,
        false_breakout_sessions=3,
        range_exit_confirmation_sessions=2,
    )
    bundles: list[LabelBundle] = []
    for symbol, rows in sorted(inputs.items()):
        initial_close = rows[0].adjusted_close
        for anchor_index in range(50, 75):
            anchor_row = rows[anchor_index]
            anchor_state = states[(symbol, anchor_row.session_date)]
            anchor_open = rows[anchor_index - 1].adjusted_close
            anchor_bar = _label_bar(
                anchor_row,
                opening=anchor_open,
                initial_close=initial_close,
                state=anchor_state,
            )
            future = [
                _label_bar(
                    rows[index],
                    opening=rows[index - 1].adjusted_close,
                    initial_close=initial_close,
                    state=states[(symbol, rows[index].session_date)],
                )
                for index in range(anchor_index + 1, anchor_index + 21)
            ]
            bundles.append(
                generate_label_bundle(
                    anchor_state,
                    anchor_bar,
                    future,
                    config=config,
                    calendar=calendar,
                )
            )
    return bundles


def _build_feature_vectors(
    primitives_by_symbol: dict[str, tuple[MarketPrimitives, ...]],
    states: dict[tuple[str, date], RegimeState],
    bundles: list[LabelBundle],
) -> list[FeatureVector]:
    primitives = {
        (item.symbol, item.session_date): item
        for rows in primitives_by_symbol.values()
        for item in rows
    }
    output: list[FeatureVector] = []
    for bundle in bundles:
        primitive = primitives[(bundle.symbol, bundle.anchor_at.date())]
        state = states[(bundle.symbol, bundle.anchor_at.date())]
        values = {
            "return_20": primitive.return_20,
            "atr_percent20": primitive.atr_percent20,
            "channel_width20": primitive.channel_width20,
            "efficiency_ratio20": primitive.efficiency_ratio20,
            "normalized_slope20": primitive.normalized_slope20,
            "state": state.state.value,
        }
        lineage_hash = _primitive_lineage_hash(primitive)
        for horizon in (1, 5, 20):
            draft = FeatureVector(
                    symbol=bundle.symbol,
                    as_of=bundle.anchor_at,
                    horizon=horizon,
                    values=values,
                    feature_set_version=FEATURE_VERSION,
                    universe_version=UNIVERSE_VERSION,
                    dataset_revision=DATASET_REVISION,
                    adjustment_revision=ADJUSTMENT_REVISION,
                    threshold_version=THRESHOLD_VERSION,
                    calendar_version=primitive.calendar_version,
                    calendar_hash=primitive.calendar_hash,
                    calendar_source=primitive.calendar_source,
                    feature_lineage_hash=lineage_hash,
                    feature_content_hash="0" * 64,
                    feature_values_hash="0" * 64,
                    source_max_available_at=primitive.source_max_available_at,
                    source_max_ingested_at=primitive.source_max_ingested_at,
                    data_quality_score=primitive.data_coverage20,
                    missing_flags={
                        name: value is None
                        for name, value in values.items()
                    },
                    ood_inputs={
                        name: value
                        for name, value in values.items()
                        if isinstance(value, float)
                    },
                )
            output.append(
                FeatureVector.model_validate(
                    {
                        **draft.model_dump(),
                        "feature_values_hash": compute_feature_values_hash(draft),
                    },
                    strict=True,
                )
            )
    return output


def _transform_input(feature: FeatureVector) -> TransformInputRow:
    return TransformInputRow(
        symbol=feature.symbol,
        as_of=feature.as_of,
        horizon=feature.horizon,
        adjustment_revision=feature.adjustment_revision,
        threshold_version=feature.threshold_version,
        feature_lineage_hash=feature.feature_lineage_hash,
        feature_content_hash=feature.feature_content_hash,
        feature_values_hash=feature.feature_values_hash,
        numeric={
            name: None if value is None else float(value)
            for name, value in feature.values.items()
            if name != "state"
        },
        categorical={"state": str(feature.values["state"])},
    )


def _fit_and_apply_transformer(
    feature_vectors: list[FeatureVector],
) -> tuple[TransformerArtifact, list[TransformedFeatureRow]]:
    ordered = sorted(feature_vectors, key=lambda item: (item.as_of, item.symbol, item.horizon))
    train_features = ordered[:60]
    transformer = fit_transformer(
        TransformerTrainingSet(
            train_start=min(item.as_of for item in train_features),
            train_end=max(item.as_of for item in train_features),
            rows=tuple(_transform_input(item) for item in train_features),
            feature_set_version=FEATURE_VERSION,
            dataset_revision=DATASET_REVISION,
            adjustment_revision=ADJUSTMENT_REVISION,
            threshold_version=THRESHOLD_VERSION,
        ),
        transformer_version=TRANSFORMER_VERSION,
        fitted_at=max(item.as_of for item in train_features) + timedelta(days=1),
    )
    return transformer, [
        apply_transformer(transformer, _transform_input(item))
        for item in feature_vectors
    ]


def _write_feature_artifacts(
    root: Path,
    feature_vectors: list[FeatureVector],
    primitives_by_symbol: dict[str, tuple[MarketPrimitives, ...]],
) -> tuple[
    list[str],
    dict[str, FeatureArtifactEvidence],
    list[FeatureVector],
]:
    store = FeatureArtifactStore(root)
    hashes: list[str] = []
    selected = [
        item
        for _, rows in sorted(primitives_by_symbol.items())
        for item in rows[50:75]
    ]
    # Exercise the shared batch core path before final feature-vector materialization.
    build_core_feature_table(
        selected,
        feature_set_version=FEATURE_VERSION,
        universe_version=UNIVERSE_VERSION,
    )
    base_vectors = {
        (item.symbol, item.as_of): item
        for item in feature_vectors
        if item.horizon == 1
    }
    vector_table = pa.Table.from_pylist(
        [
            {
                "symbol": item.symbol,
                "as_of": item.as_of,
                "horizon": None,
                "feature_set_version": item.feature_set_version,
                "dataset_revision": item.dataset_revision,
                "source_max_available_at": item.source_max_available_at,
                "source_max_ingested_at": item.source_max_ingested_at,
                "adjustment_revision": item.adjustment_revision,
                "threshold_version": item.threshold_version,
                "calendar_version": item.calendar_version,
                "calendar_hash": item.calendar_hash,
                "calendar_source": item.calendar_source.value,
                "feature_values_hash": item.feature_values_hash,
                "values_json": json.dumps(
                    item.values,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
            for item in sorted(base_vectors.values(), key=lambda row: (row.symbol, row.as_of))
        ]
    )
    evidence_by_lineage: dict[str, FeatureArtifactEvidence] = {}
    content_by_key: dict[tuple[str, datetime], str] = {}
    for (symbol, as_of), vector in sorted(base_vectors.items()):
        table = vector_table.filter(
            pa.array(
                [
                    symbol_value.as_py() == symbol
                    and as_of_value.as_py() == as_of
                    for symbol_value, as_of_value in zip(
                        vector_table.column("symbol"),
                        vector_table.column("as_of"),
                        strict=True,
                    )
                ]
            )
        )
        dependency_hash = hashlib.sha256(
            vector.feature_values_hash.encode("ascii")
        ).hexdigest()
        artifact = store.put(
            FeatureCacheKey(
                node_name="core",
                dataset_revision=DATASET_REVISION,
                feature_set_version=FEATURE_VERSION,
                symbol=symbol,
                as_of=as_of,
                horizon_dependent=False,
                horizon=None,
            ),
            table,
            input_hash=vector.feature_lineage_hash,
            dependency_hashes={"final_feature_values": dependency_hash},
            source_max_available_at=vector.source_max_available_at,
            generated_at=vector.as_of + timedelta(minutes=2),
            source_max_ingested_at=vector.source_max_ingested_at,
        )
        hashes.append(artifact.content_hash)
        content_by_key[(symbol, as_of)] = artifact.content_hash
        evidence_by_lineage[artifact.input_hash] = FeatureArtifactEvidence(
            symbol=artifact.symbol,
            as_of_start=artifact.as_of_start,
            as_of_end=artifact.as_of_end,
            horizon=artifact.horizon,
            input_hash=artifact.input_hash,
            content_hash=artifact.content_hash,
            feature_values_hashes=artifact.feature_values_hashes,
        )
    updated = [
        FeatureVector.model_validate(
            {
                **item.model_dump(),
                "feature_content_hash": content_by_key[(item.symbol, item.as_of)],
            },
            strict=True,
        )
        for item in feature_vectors
    ]
    return hashes, evidence_by_lineage, updated


def _primitive_evidence(primitive: MarketPrimitives) -> bytes:
    return json.dumps(
        primitive.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _primitive_lineage_hash(primitive: MarketPrimitives) -> str:
    dependency_hash = hashlib.sha256(_primitive_evidence(primitive)).hexdigest()
    return _feature_dag().input_hash(
        "core", dependency_hashes={"primitives": dependency_hash}
    )


def _feature_dag() -> FeatureDag:
    return FeatureDag(
        (
            FeatureNode(name="raw_daily", version="offline-raw-v1"),
            FeatureNode(
                name="primitives",
                version="offline-primitives-v1",
                dependencies=("raw_daily",),
                forward_impact_sessions=20,
            ),
            FeatureNode(
                name="core",
                version=FEATURE_VERSION,
                dependencies=("primitives",),
            ),
        )
    )


def _engineering_path_audit(
    inputs: dict[str, tuple[DailyFeatureInput, ...]],
    sessions: tuple[date, ...],
    *,
    batch_primitive_symbol_count: int,
    batch_core_row_count: int,
) -> dict[str, object]:
    anchor = inputs["US.SPY"][74]
    session = IntradaySessionSpec(
        symbol="US.SPY",
        session_date=anchor.session_date,
        market_open_at=datetime.combine(
            anchor.session_date, time(13, 30), tzinfo=UTC
        ),
        market_close_at=datetime.combine(
            anchor.session_date, time(20, 0), tzinfo=UTC
        ),
        dataset_revision=DATASET_REVISION,
        adjustment_revision=ADJUSTMENT_REVISION,
    )
    hourly: list[HourlyBar] = []
    for index in range(6):
        start = session.market_open_at + timedelta(hours=index)
        opening = anchor.adjusted_close + index * 0.1
        hourly.append(
            HourlyBar(
                symbol="US.SPY",
                session_date=anchor.session_date,
                window_start=start,
                window_end=start + timedelta(hours=1),
                adjusted_open=opening,
                adjusted_high=opening + 0.3,
                adjusted_low=opening - 0.2,
                adjusted_close=opening + 0.1,
                adjusted_volume=100_000.0 + index * 10_000.0,
                source_available_at=start + timedelta(hours=1),
                ingested_at=start + timedelta(hours=1, minutes=1),
                complete_window=True,
                dataset_revision=DATASET_REVISION,
                adjustment_revision=ADJUSTMENT_REVISION,
            )
        )
    intraday = aggregate_intraday_session(
        hourly, session, as_of=session.market_close_at
    )
    memberships = [
        UniverseMembership(
            symbol=symbol,
            asset_group="etf" if symbol == "US.SPY" else "stock",
            universe_version=UNIVERSE_VERSION,
            effective_from=session.market_open_at - timedelta(days=365),
            effective_to=None,
        )
        for symbol in ("US.AAPL", "US.SPY")
    ]
    breadth_rows = [
        BreadthObservation(
            symbol=symbol,
            as_of=session.market_close_at,
            source_available_at=session.market_close_at,
            ingested_at=session.market_close_at + timedelta(minutes=1),
            return_1=(rows[74].adjusted_close / rows[73].adjusted_close - 1.0),
            above_ma20=True,
            new_high20=False,
            new_low20=False,
            high_volatility=False,
            benchmark_correlation20=0.7,
            dataset_revision=DATASET_REVISION,
        )
        for symbol, rows in sorted(inputs.items())
    ]
    breadth = compute_market_breadth(
        memberships,
        breadth_rows,
        as_of=session.market_close_at,
        universe_version=UNIVERSE_VERSION,
        minimum_coverage=0.80,
    )
    expected = sessions[55:75]
    return_rows: list[ReturnObservation] = []
    for symbol, rows in inputs.items():
        for index in range(55, 75):
            return_rows.append(
                ReturnObservation(
                    symbol=symbol,
                    session_date=rows[index].session_date,
                    return_1=rows[index].adjusted_close
                    / rows[index - 1].adjusted_close
                    - 1.0,
                    source_available_at=rows[index].available_at,
                    ingested_at=rows[index].ingested_at,
                    dataset_revision=DATASET_REVISION,
                )
            )
    for index in range(55, 75):
        spy = inputs["US.SPY"][index]
        return_rows.append(
            ReturnObservation(
                symbol="US.QQQ",
                session_date=spy.session_date,
                return_1=(spy.adjusted_close / inputs["US.SPY"][index - 1].adjusted_close - 1.0)
                * 1.1,
                source_available_at=spy.available_at,
                ingested_at=spy.ingested_at,
                dataset_revision=DATASET_REVISION,
            )
        )
    relative = compute_relative_context(
        "US.AAPL",
        return_rows,
        [
            SectorMapping(
                symbol="US.AAPL",
                sector_etf="US.QQQ",
                mapping_version="offline-sector-map-v1",
                effective_from=session.market_open_at - timedelta(days=365),
                effective_to=None,
            )
        ],
        as_of=anchor.available_at,
        mapping_version="offline-sector-map-v1",
        benchmark_symbol="US.SPY",
        lookback_sessions=20,
        minimum_aligned_sessions=15,
        expected_sessions=expected,
        anchor_session=anchor.session_date,
    )
    dag_preview = _feature_dag().affected_descendants(
        "raw_daily",
        symbol="US.AAPL",
        affected_start=sessions[60],
        affected_end=sessions[60],
        trading_calendar=sessions,
        dataset_revision=DATASET_REVISION,
        feature_set_version=FEATURE_VERSION,
    )
    return {
        "batch_primitive_symbol_count": batch_primitive_symbol_count,
        "batch_core_row_count": batch_core_row_count,
        "dag_invalidation_preview_count": len(dag_preview),
        "intraday_complete_hour_count": intraday.complete_hour_count,
        "breadth_constituent_count": breadth.constituent_count,
        "breadth_partial": breadth.partial,
        "relative_aligned_session_count": relative.aligned_session_count,
        "relative_partial": relative.partial,
    }


def _future_feature_sentinel(
    rows: tuple[DailyFeatureInput, ...], calendar: TradingCalendarArtifact
) -> bool:
    anchor_date = calendar.sessions[60]
    baseline = {
        item.session_date: item
        for item in compute_market_primitives(
            rows,
            calendar=calendar,
            feature_set_version=FEATURE_VERSION,
        )
    }[anchor_date]
    shocked = list(rows)
    future = shocked[61]
    shocked[61] = future.model_copy(
        update={"adjusted_high": 10_001.0, "adjusted_close": 10_000.0}
    )
    comparison = {
        item.session_date: item
        for item in compute_market_primitives(
            shocked,
            calendar=calendar,
            feature_set_version=FEATURE_VERSION,
        )
    }[anchor_date]
    return baseline == comparison


def _forward_only_sentinel() -> bool:
    cutoff = datetime(2025, 5, 1, 20, tzinfo=UTC)
    observation = FundamentalObservation(
        observation_id="offline-analyst-r1",
        event_id="offline-analyst-event",
        revision_no=1,
        symbol="US.AAPL",
        metric="analyst_rating",
        value_numeric=5.0,
        value_text=None,
        unit="score",
        report_period=date(2025, 5, 1),
        published_at=None,
        source_available_at=cutoff,
        ingested_at=cutoff + timedelta(minutes=1),
        source_field="snapshot.analyst_rating",
        point_in_time_eligibility=PointInTimeEligibility.FORWARD_ONLY,
        raw_batch_id="offline-forward-batch",
        schema_version="fundamental-v1",
        dataset_revision=DATASET_REVISION,
    )
    [selected] = select_observations_as_of([observation], cutoff)
    return (
        selected.observation is None
        and selected.missing_reason is MissingObservationReason.FORWARD_ONLY
    )


def _audit_summary(
    *,
    dataset: ResearchDataset,
    feature_vectors: list[FeatureVector],
    bundles: list[LabelBundle],
    primitives_by_symbol: dict[str, tuple[MarketPrimitives, ...]],
    artifact_hashes: list[str],
    future_sentinel: bool,
    forward_only_sentinel: bool,
    runtime_seconds: float,
    engineering_audit: dict[str, object],
) -> dict[str, object]:
    direction = Counter(
        f"h{sample.horizon}:{sample.target.forecast_direction.value}"
        for sample in dataset.samples
    )
    breakout = Counter(
        f"h{sample.horizon}:{sample.target.breakout_event.value}"
        for sample in dataset.samples
    )
    state_distribution = Counter(bundle.anchor_state.value for bundle in bundles)
    feature_names = sorted(feature_vectors[0].values)
    feature_missingness = {
        name: sum(item.values[name] is None for item in feature_vectors)
        / len(feature_vectors)
        for name in feature_names
    }
    source_lags = [
        (item.source_max_ingested_at - item.source_max_available_at).total_seconds()
        for item in feature_vectors
    ]
    primitive_rows = [
        item for rows in primitives_by_symbol.values() for item in rows
    ]
    unresolved = sum(not sample.target.resolved for sample in dataset.samples)
    censored = sum(
        sample.horizon in {5, 20}
        and sample.target.range_exit is None
        and sample.target.resolved is False
        for sample in dataset.samples
    )
    return {
        "status": "ok",
        "mode": "synthetic_offline_no_opend",
        "sample_count": dataset.sample_count,
        "symbol_count": len({sample.symbol for sample in dataset.samples}),
        "asset_group_coverage": {"stock": 1, "etf": 1},
        "dataset_input_hash": dataset.input_hash,
        "calendar_evidence": {
            "calendar_version": dataset.calendar_version,
            "calendar_hash": dataset.calendar_hash,
            "calendar_source": dataset.calendar_source.value,
        },
        "feature_artifact_hashes": artifact_hashes,
        "direction_distribution": dict(sorted(direction.items())),
        "breakout_distribution": dict(sorted(breakout.items())),
        "state_distribution": dict(sorted(state_distribution.items())),
        "unresolved_target_count": unresolved,
        "range_exit_censored_count": censored,
        "feature_missingness": feature_missingness,
        "source_lag_seconds": {
            "minimum": min(source_lags),
            "median": sorted(source_lags)[len(source_lags) // 2],
            "maximum": max(source_lags),
        },
        "availability_audit": {
            "core_valid_ratio": sum(item.valid_core for item in primitive_rows)
            / len(primitive_rows),
            "intraday_60m_optional_ratio": 1.0,
            "point_in_time_fundamental_optional_ratio": 0.0,
            "interpretation": "availability_only_not_predictive_lift",
        },
        "future_feature_sentinel_passed": future_sentinel,
        "forward_only_sentinel_passed": forward_only_sentinel,
        "engineering_path_audit": engineering_audit,
        "runtime_seconds": round(runtime_seconds, 6),
        "versions": {
            "dataset_revision": DATASET_REVISION,
            "adjustment_revision": ADJUSTMENT_REVISION,
            "feature_set_version": FEATURE_VERSION,
            "label_version": LABEL_VERSION,
            "threshold_version": THRESHOLD_VERSION,
            "transformer_version": TRANSFORMER_VERSION,
            "transformer_input_hash": dataset.transformer_input_hash,
            "transformer_manifest_hash": dataset.transformer_manifest_hash,
            "universe_version": UNIVERSE_VERSION,
        },
    }
