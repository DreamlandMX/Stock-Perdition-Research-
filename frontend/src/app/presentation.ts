import { translate, type Locale, type MessageKey } from "../i18n/core";

const ASSET_LABELS: Readonly<Record<string, MessageKey>> = {
  all: "common.labels.asset_labels.all",
  equity: "common.labels.asset_labels.equity",
  etf: "common.labels.asset_labels.etf",
  stock: "common.labels.asset_labels.stock",
  stock_etf: "common.labels.asset_labels.stock_etf",
  us_equity: "common.labels.asset_labels.us_equity",
};

const SECTOR_LABELS: Readonly<Record<string, MessageKey>> = {
  communication: "common.labels.sector_labels.communication",
  communication_services: "common.labels.sector_labels.communication_services",
  consumer_discretionary: "common.labels.sector_labels.consumer_discretionary",
  consumer_staples: "common.labels.sector_labels.consumer_staples",
  energy: "common.labels.sector_labels.energy",
  materials: "common.labels.sector_labels.materials",
  real_estate: "common.labels.sector_labels.real_estate",
  utilities: "common.labels.sector_labels.utilities",
  consumer: "common.labels.sector_labels.consumer",
  energy_materials: "common.labels.sector_labels.energy_materials",
  etf: "common.labels.sector_labels.etf",
  financials: "common.labels.sector_labels.financials",
  healthcare: "common.labels.sector_labels.healthcare",
  industrials: "common.labels.sector_labels.industrials",
  other: "common.labels.sector_labels.other",
  technology: "common.labels.sector_labels.technology",
};

const TASK_LABELS: Readonly<Record<string, MessageKey>> = {
  breakout: "common.labels.task_labels.breakout",
  breakout_direction: "common.labels.task_labels.breakout_direction",
  forecast_direction: "common.labels.task_labels.forecast_direction",
  mae: "common.labels.task_labels.mae",
  mfe: "common.labels.task_labels.mfe",
  range_exit: "common.labels.task_labels.range_exit",
  return_fwd: "common.labels.task_labels.return_fwd",
  returns: "common.labels.task_labels.returns",
};

const REASON_LABELS: Readonly<Record<string, MessageKey>> = {
  snapshot_evaluation_missing: "common.labels.reason_labels.snapshot_evaluation_missing",
  historical_source_gap: "common.labels.reason_labels.historical_source_gap",
  statistical_gate_failed: "common.labels.reason_labels.statistical_gate_failed",
  calibration_gate_failed: "common.labels.reason_labels.calibration_gate_failed",
  baseline_not_beaten: "common.labels.reason_labels.baseline_not_beaten",
  "daily-bars": "common.labels.reason_labels.daily-bars",
  calibration_window: "common.labels.reason_labels.calibration_window",
  cost_gate: "common.labels.reason_labels.cost_gate",
  duckdb_file_locked: "common.labels.reason_labels.duckdb_file_locked",
  final_lockbox_consumed: "common.labels.reason_labels.final_lockbox_consumed",
  feature_artifact_mismatch: "common.labels.reason_labels.feature_artifact_mismatch",
  historical_point_in_time_proven: "common.labels.reason_labels.historical_point_in_time_proven",
  historical_observed_at_unproven: "common.labels.reason_labels.historical_observed_at_unproven",
  interval_coverage_gate: "common.labels.reason_labels.interval_coverage_gate",
  model_registry: "common.labels.reason_labels.model_registry",
  no_champion_candidate: "common.labels.reason_labels.no_champion_candidate",
  oos_skill_gate_failed: "common.labels.reason_labels.oos_skill_gate_failed",
  oos_promotion_gate: "common.labels.reason_labels.oos_promotion_gate",
  permission_denied: "common.labels.reason_labels.permission_denied",
  quarterly_liquidity_review: "common.labels.reason_labels.quarterly_liquidity_review",
  static_seed_disclosed_survivorship_bias: "common.labels.reason_labels.static_seed_disclosed_survivorship_bias",
  static_seed_universe: "common.labels.reason_labels.static_seed_universe",
  survivorship_limited: "common.labels.reason_labels.survivorship_limited",
  synthetic_offline: "common.labels.reason_labels.synthetic_offline",
};

const ROLE_LABELS: Readonly<Record<string, MessageKey>> = {
  equity_research: "common.labels.role_labels.equity_research",
  etf_research: "common.labels.role_labels.etf_research",
  market_proxy: "common.labels.role_labels.market_proxy",
};

const MODEL_STATUS_LABELS: Readonly<Record<string, MessageKey>> = {
  available: "common.labels.model_status_labels.available",
  blocked: "common.labels.model_status_labels.blocked",
  challenger: "common.labels.model_status_labels.challenger",
  challenger_evaluated: "common.labels.model_status_labels.challenger_evaluated",
  challenger_gate_failed: "common.labels.model_status_labels.challenger_gate_failed",
  champion: "common.labels.model_status_labels.champion",
  configured: "common.labels.model_status_labels.configured",
  gate_failed: "common.labels.model_status_labels.gate_failed",
  gate_passed: "common.labels.model_status_labels.gate_passed",
  insufficient_blocks: "common.labels.model_status_labels.insufficient_blocks",
  insufficient_trial_variance: "common.labels.model_status_labels.insufficient_trial_variance",
  no_champion_candidate: "common.labels.model_status_labels.no_champion_candidate",
  research_only: "common.labels.model_status_labels.research_only",
  unavailable: "common.labels.model_status_labels.unavailable",
  verified: "common.labels.model_status_labels.verified",
  verified_research_component: "common.labels.model_status_labels.verified_research_component",
};

const COMPONENT_LABELS: Readonly<Record<string, MessageKey>> = {
  feature_dag: "common.labels.component_labels.feature_dag",
  feature_pipeline: "common.labels.component_labels.feature_pipeline",
  fundamental_snapshot: "common.labels.component_labels.fundamental_snapshot",
  market_bars: "common.labels.component_labels.market_bars",
  model_registry: "common.labels.component_labels.model_registry",
  opend: "common.labels.component_labels.opend",
  opend_history: "common.labels.component_labels.opend_history",
  operations: "common.labels.component_labels.operations",
  research_snapshot: "common.labels.component_labels.research_snapshot",
};

const DATASET_LABELS: Readonly<Record<string, MessageKey>> = {
  bars_1d: "common.labels.dataset_labels.bars_1d",
  bars_60m: "common.labels.dataset_labels.bars_60m",
  features: "common.labels.dataset_labels.features",
  fundamentals: "common.labels.dataset_labels.fundamentals",
  health: "common.labels.dataset_labels.health",
  instruments: "common.labels.dataset_labels.instruments",
  operations: "common.labels.dataset_labels.operations",
  research: "common.labels.dataset_labels.research",
};

const POINT_IN_TIME_LABELS: Readonly<Record<string, MessageKey>> = {
  forward_only: "common.labels.point_in_time_labels.forward_only",
  not_applicable: "common.labels.point_in_time_labels.not_applicable",
  proven: "common.labels.point_in_time_labels.proven",
  unproven: "common.labels.point_in_time_labels.unproven",
};

const ELIGIBILITY_LABELS: Readonly<Record<string, MessageKey>> = {
  eligible: "common.labels.eligibility_labels.eligible",
  ineligible: "common.labels.eligibility_labels.ineligible",
};

const CAPABILITY_STATUS_LABELS: Readonly<Record<string, MessageKey>> = {
  available: "common.labels.capability_status_labels.available",
  denied: "common.labels.capability_status_labels.denied",
  degraded: "common.labels.capability_status_labels.degraded",
  unavailable: "common.labels.capability_status_labels.unavailable",
};

const PERMISSION_LABELS: Readonly<Record<string, MessageKey>> = {
  historical: "common.labels.permission_labels.historical",
  quote: "common.labels.permission_labels.quote",
};

const DEGRADATION_LABELS: Readonly<Record<string, MessageKey>> = {
  block: "common.labels.degradation_labels.block",
  disable_feature: "common.labels.degradation_labels.disable_feature",
};

const TIMEFRAME_LABELS: Readonly<Record<string, MessageKey>> = {
  "1d": "common.labels.timeframe_labels.1d",
  "60m": "common.labels.timeframe_labels.60m",
};

const EVENT_FLAG_LABELS: Readonly<Record<string, MessageKey>> = {
  dividend: "common.labels.event_flag_labels.dividend",
  "earnings-window": "common.labels.event_flag_labels.earnings-window",
  earnings: "common.labels.event_flag_labels.earnings",
  none: "common.labels.event_flag_labels.none",
  split: "common.labels.event_flag_labels.split",
};

const SERVING_STATUS_LABELS: Readonly<Record<string, MessageKey>> = {
  delayed: "common.labels.serving_status_labels.delayed",
  live: "common.labels.serving_status_labels.live",
  offline: "common.labels.serving_status_labels.offline",
  partial: "common.labels.serving_status_labels.partial",
  stale: "common.labels.serving_status_labels.stale",
  unavailable: "common.labels.serving_status_labels.unavailable",
};

const FRESHNESS_LABELS: Readonly<Record<string, MessageKey>> = {
  current: "common.labels.freshness_labels.current",
  delayed: "common.labels.freshness_labels.delayed",
  stale: "common.labels.freshness_labels.stale",
  unknown: "common.labels.freshness_labels.unknown",
};

const COMPLETENESS_LABELS: Readonly<Record<string, MessageKey>> = {
  complete: "common.labels.completeness_labels.complete",
  empty: "common.labels.completeness_labels.empty",
  partial: "common.labels.completeness_labels.partial",
  unknown: "common.labels.completeness_labels.unknown",
};

function knownOrUnknown(
  labels: Readonly<Record<string, MessageKey>>,
  value: string | null | undefined,
  locale: Locale,
) {
  if (value == null || value === "") {
    return translate(locale, "common.unknown");
  }
  const key = labels[value];
  return key ? translate(locale, key) : translate(locale, "common.unknownCode", { code: value });
}

export const assetLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(ASSET_LABELS, value, locale);
export const sectorLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(SECTOR_LABELS, value, locale);
export const taskLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(TASK_LABELS, value, locale);
export const reasonLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(REASON_LABELS, value, locale);
export const roleLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(ROLE_LABELS, value, locale);
export const modelStatusLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(MODEL_STATUS_LABELS, value, locale);
export const componentLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(COMPONENT_LABELS, value, locale);
export const datasetLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(DATASET_LABELS, value, locale);
export const servingStatusLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(SERVING_STATUS_LABELS, value, locale);
export const freshnessLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(FRESHNESS_LABELS, value, locale);
export const completenessLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(COMPLETENESS_LABELS, value, locale);
export const pointInTimeLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(POINT_IN_TIME_LABELS, value, locale);
export const eligibilityLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(ELIGIBILITY_LABELS, value, locale);
export const capabilityStatusLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(CAPABILITY_STATUS_LABELS, value, locale);
export const permissionLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(PERMISSION_LABELS, value, locale);
export const degradationLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(DEGRADATION_LABELS, value, locale);
export const timeframeLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(TIMEFRAME_LABELS, value, locale);
export const eventFlagLabel = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  knownOrUnknown(EVENT_FLAG_LABELS, value, locale);

const DATE_ONLY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

export function isRetainedHistoricalGap(item: {
  error_class: string | null;
  freshness: string;
  serving_status: string;
  connectivity: string;
}) {
  return (
    item.error_class === "historical_source_gap" &&
    item.freshness === "current" &&
    item.serving_status !== "offline" &&
    item.serving_status !== "unavailable" &&
    item.connectivity !== "disconnected"
  );
}

export function formatMarketSessionDate(value: string) {
  if (DATE_ONLY_PATTERN.test(value)) return value;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat("en-CA", {
    day: "2-digit",
    month: "2-digit",
    timeZone: "America/New_York",
    year: "numeric",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function formatUsMarketTime(value: string, locale: Locale = "zh-CN") {
  if (DATE_ONLY_PATTERN.test(value)) return value;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) {
    return value;
  }
  return `${new Intl.DateTimeFormat(locale, {
    day: "2-digit",
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    month: "2-digit",
    timeZone: "America/New_York",
    year: "numeric",
  }).format(date)} ${locale === "en-US" ? "ET" : "美东时间"}`;
}

export function displayInstrumentName(symbol: string, name: string) {
  const ticker = symbol.startsWith("US.") ? symbol.slice(3) : symbol;
  return name.trim().toUpperCase() === ticker.toUpperCase() ? null : name;
}

export function regimeLabel(value: string | null | undefined, locale: Locale = "zh-CN") {
 const keys: Readonly<Record<string, MessageKey>> = {"downtrend": "common.labels.regime.downtrend", "invalid": "common.labels.regime.invalid", "range": "common.labels.regime.range", "transition": "common.labels.regime.transition", "uptrend": "common.labels.regime.uptrend"};
 return knownOrUnknown(keys, value, locale);
}
export function rejectionReasonLabel(value: string | null | undefined, locale: Locale = "zh-CN") {
 const keys: Readonly<Record<string, MessageKey>> = {"corporate_action_unresolved": "common.labels.rejection.corporate_action_unresolved", "data_quality": "common.labels.rejection.data_quality", "feature_artifact_mismatch": "common.labels.rejection.feature_artifact_mismatch", "insufficient_calibration": "common.labels.rejection.insufficient_calibration", "insufficient_similar_cases": "common.labels.rejection.insufficient_similar_cases", "interval_too_wide": "common.labels.rejection.interval_too_wide", "model_disagreement": "common.labels.rejection.model_disagreement", "no_champion_candidate": "common.labels.rejection.no_champion_candidate", "out_of_distribution": "common.labels.rejection.out_of_distribution", "poor_calibration": "common.labels.rejection.poor_calibration", "schema_mismatch": "common.labels.rejection.schema_mismatch", "stale_data": "common.labels.rejection.stale_data"};
 return knownOrUnknown(keys, value, locale);
}
