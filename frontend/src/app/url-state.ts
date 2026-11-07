import { translate, translateRef, type Locale, type LocalizableText } from "../i18n/core";
import type { components, paths } from "../api/generated/schema";

type ScreenQuery = NonNullable<paths["/api/v1/screen"]["get"]["parameters"]["query"]>;
type HistoryQuery = NonNullable<
  paths["/api/v1/instruments/{symbol}/history"]["get"]["parameters"]["query"]
>;
type Horizon = NonNullable<ScreenQuery["horizon"]>;
type PredictionBasis = components["schemas"]["PredictionBasis"];
type ReturnBasis = components["schemas"]["ReturnBasis"];
type ScreenReturnBasis = components["schemas"]["ScreenReturnBasis"];
type HistoryTimeframe = NonNullable<HistoryQuery["timeframe"]>;
type HistoryAdjustment = NonNullable<HistoryQuery["adjustment"]>;
type HistoryDateTime = Exclude<HistoryQuery["from"], null | undefined>;
type Regime = components["schemas"]["RegimeLabel"];
type Confidence = components["schemas"]["ConfidenceLevel"];
type ScreenConfidenceFloor = components["schemas"]["ScreenConfidenceFloor"];
type DataStatus = components["schemas"]["ServingStatus"];
type SortKey = NonNullable<ScreenQuery["sort"]>;
type SortDirection = components["schemas"]["SortDirection"];
type ResearchDisplayMode = components["schemas"]["ResearchDisplayMode"];
type ResolvedSnapshot = components["schemas"]["ResolvedSnapshot"];
type BacktestReadModel = components["schemas"]["BacktestReadModel"];
type FoldId = components["schemas"]["FoldMetrics"]["fold_id"];

export interface ResearchVersions {
  datasetRevision: ResolvedSnapshot["dataset_revision"] | undefined;
  featureSetVersion: ResolvedSnapshot["feature_set_version"] | undefined;
  universeVersion: ResolvedSnapshot["universe_version"] | undefined;
  modelVersion: ResolvedSnapshot["model_version"] | undefined;
  calibrationVersion: ResolvedSnapshot["calibration_version"] | undefined;
  strategyVersion: BacktestReadModel["strategy_version"] | undefined;
  splitVersion: BacktestReadModel["split_version"] | undefined;
}

export interface ResearchFilters {
  sector: string | undefined;
  assetType: string | undefined;
  regime: Regime | undefined;
  minimumProbability: number | undefined;
  maximumQ10: number | undefined;
  confidence: Confidence | undefined;
  dataStatus: DataStatus | undefined;
  effectiveAbstain: boolean | undefined;
}

export type ScreenFilters = Omit<ResearchFilters, "confidence"> & {
  confidence: ScreenConfidenceFloor | undefined;
};

export interface ResearchSort {
  key: SortKey;
  direction: SortDirection;
}

export interface ResearchPage {
  number: number;
  size: number;
}

export interface BacktestUrlState {
  evaluationWindowId: BacktestReadModel["evaluation_window_id"] | undefined;
  foldId: FoldId | undefined;
  costScenario: BacktestReadModel["cost_scenario"] | undefined;
}

export interface ResearchChartUrlState {
  timeframe: HistoryTimeframe;
  from: HistoryDateTime | undefined;
  to: HistoryDateTime | undefined;
  adjustment: HistoryAdjustment;
}

export interface UrlStateWarning {
  id: string;
  parameter: string;
  rejectedValue: string;
  message: string;
  behavior_ref?: LocalizableText;
}

export interface ResearchUrlState {
  optionParams?: Readonly<Record<string, string>>;
  asOf: string | undefined;
  snapshotId: string | undefined;
  revisionNo: number | undefined;
  symbol: string | undefined;
  horizon: Horizon;
  predictionBasis: PredictionBasis;
  returnBasis: ReturnBasis;
  versions: ResearchVersions;
  filters: ResearchFilters;
  sort: ResearchSort;
  page: ResearchPage;
  backtest: BacktestUrlState;
  chart: ResearchChartUrlState;
  warnings: readonly UrlStateWarning[];
}

export type ScreenUrlState = Omit<ResearchUrlState, "filters" | "returnBasis"> & {
  filters: ScreenFilters;
  mode: ResearchDisplayMode;
  returnBasis: ScreenReturnBasis;
};

export function researchStateForSymbol(
  state: ResearchUrlState,
  current: string,
  target: string,
): ResearchUrlState {
  const optionParams = { ...state.optionParams };
  if (current !== target) delete optionParams.option_snapshot;
  return { ...state, symbol: undefined, optionParams };
}

export type UrlStateCodecOptions = { route: "screen" } | { route?: "research" };

const HORIZON_VALUES = { 1: true, 5: true, 20: true } satisfies Record<Horizon, true>;
const PREDICTION_BASIS_VALUES = {
  forecast: true,
  executable: true,
} satisfies Record<PredictionBasis, true>;
const RETURN_BASIS_VALUES = {
  capital_gain: true,
  total_return: true,
  executable: true,
} satisfies Record<ReturnBasis, true>;
const SCREEN_RETURN_BASIS_VALUES = {
  capital_gain: true,
  executable: true,
} satisfies Record<ScreenReturnBasis, true>;
const HISTORY_TIMEFRAME_VALUES = {
  "1d": true,
  "60m": true,
} satisfies Record<HistoryTimeframe, true>;
const HISTORY_ADJUSTMENT_VALUES = {
  raw: true,
  split_adjusted: true,
  total_return: true,
} satisfies Record<HistoryAdjustment, true>;
const REGIME_VALUES = {
  range: true,
  uptrend: true,
  downtrend: true,
  transition: true,
  invalid: true,
} satisfies Record<Regime, true>;
const CONFIDENCE_VALUES = {
  high: true,
  medium: true,
  low: true,
  unavailable: true,
} satisfies Record<Confidence, true>;
const SCREEN_CONFIDENCE_VALUES = {
  high: true,
  medium: true,
  low: true,
} satisfies Record<ScreenConfidenceFloor, true>;
const DATA_STATUS_VALUES = {
  live: true,
  delayed: true,
  stale: true,
  partial: true,
  offline: true,
  unavailable: true,
} satisfies Record<DataStatus, true>;
const SORT_KEY_VALUES = {
  default: true,
  symbol: true,
  executable_up_probability: true,
  breakout_up_probability: true,
  breakout_down_probability: true,
  executable_q50: true,
  capital_gain_q50: true,
  interval_width: true,
  rule_atr_pct_20: true,
  rule_channel_position_20: true,
  rule_return_20: true,
  rule_return_5: true,
  rule_volume_z_20: true,
  similar_case_count: true,
  confidence_level: true,
  data_freshness_seconds: true,
  verified_q10: true,
  verified_q50: true,
} satisfies Record<SortKey, true>;
const SORT_DIRECTION_VALUES = {
  asc: true,
  desc: true,
} satisfies Record<SortDirection, true>;
const RESEARCH_DISPLAY_MODE_VALUES = {
  challenger: true,
  formal: true,
  research: true,
} satisfies Record<ResearchDisplayMode, true>;
const COMMON_RESEARCH_SORT_VALUES = new Set<SortKey>([
  "data_freshness_seconds",
  "rule_atr_pct_20",
  "rule_channel_position_20",
  "rule_return_20",
  "rule_return_5",
  "rule_volume_z_20",
  "symbol",
]);
const VERIFIED_RESEARCH_SORT_VALUES = new Set<SortKey>([
  ...COMMON_RESEARCH_SORT_VALUES,
  "verified_q10",
  "verified_q50",
]);

export const SCREEN_MODE_COMPATIBILITY_MESSAGE =
  "已移除与研究模式不兼容的正式预测筛选或排序，并从第 1 页重新加载。";

export function normalizeScreenModeState(
  state: ScreenUrlState,
  options: { resetPage?: boolean; warn?: boolean } = {},
): ScreenUrlState {
  if (state.mode === "formal") {
    return state;
  }
  const compatibleSorts =
    state.mode === "research"
      ? VERIFIED_RESEARCH_SORT_VALUES
      : COMMON_RESEARCH_SORT_VALUES;
  const sort = compatibleSorts.has(state.sort.key)
    ? state.sort
    : { direction: "asc" as const, key: "symbol" as const };
  const warnings = options.warn
    ? [
        ...state.warnings.filter(
          (warning) => warning.id !== "url-state-mode-compatibility",
        ),
        {
          id: "url-state-mode-compatibility",
          message: SCREEN_MODE_COMPATIBILITY_MESSAGE,
          parameter: "mode",
          rejectedValue: state.mode,
        },
      ]
    : state.warnings;
  return {
    ...state,
    filters: {
      ...state.filters,
      confidence: undefined,
      effectiveAbstain: undefined,
      maximumQ10: undefined,
      minimumProbability: undefined,
    },
    page: options.resetPage ? { ...state.page, number: 1 } : state.page,
    predictionBasis: "executable",
    returnBasis: "executable",
    sort,
    warnings,
  };
}

export const DEFAULT_RESEARCH_URL_STATE: ResearchUrlState = {
  asOf: undefined,
  snapshotId: undefined,
  revisionNo: undefined,
  symbol: undefined,
  horizon: 5,
  predictionBasis: "executable",
  returnBasis: "executable",
  versions: {
    datasetRevision: undefined,
    featureSetVersion: undefined,
    universeVersion: undefined,
    modelVersion: undefined,
    calibrationVersion: undefined,
    strategyVersion: undefined,
    splitVersion: undefined,
  },
  filters: {
    sector: undefined,
    assetType: undefined,
    regime: undefined,
    minimumProbability: undefined,
    maximumQ10: undefined,
    confidence: undefined,
    dataStatus: undefined,
    effectiveAbstain: undefined,
  },
  sort: {
    key: "default",
    direction: "desc",
  },
  page: {
    number: 1,
    size: 50,
  },
  backtest: {
    evaluationWindowId: undefined,
    foldId: undefined,
    costScenario: undefined,
  },
  chart: {
    timeframe: "1d",
    from: undefined,
    to: undefined,
    // Published snapshots currently only carry raw bars
    // (live/publication.py writes adjustment="raw"), so defaulting to
    // split_adjusted renders an empty chart for every symbol.
    adjustment: "raw",
  },
  warnings: [],
};

function hasOwn<T extends PropertyKey>(values: object, value: T): boolean {
  return Object.prototype.hasOwnProperty.call(values, value);
}

function addWarning(
  warnings: UrlStateWarning[],
  parameter: string,
  rejectedValue: string,
  behavior: LocalizableText,
) {
  warnings.push({
    id: `url-state-${parameter}-${warnings.length}`,
    parameter,
    rejectedValue,
    message: translate("zh-CN", "common.url.invalidValue", { parameter, value: rejectedValue, behavior: translateRef("zh-CN", behavior) }),
    behavior_ref: behavior,
  });
}

function optionalString(params: URLSearchParams, parameter: string) {
  const value = params.get(parameter)?.trim();
  // "null"/"undefined" can only come from a stale URL that serialized an absent
  // value; they are never valid identifiers, so treat them as missing.
  return value === undefined ||
    value === "" ||
    value === "null" ||
    value === "undefined"
    ? undefined
    : value;
}

const ISO_DATE_TIME_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/;

function isValidIsoDateTime(value: string) {
  const match = ISO_DATE_TIME_PATTERN.exec(value);
  if (match === null) {
    return false;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6] ?? "0");
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return (
    month >= 1 &&
    month <= 12 &&
    day >= 1 &&
    day <= daysInMonth &&
    hour <= 23 &&
    minute <= 59 &&
    second <= 59 &&
    Number.isFinite(Date.parse(value))
  );
}

function parseOptionalHistoryDateTime(
  params: URLSearchParams,
  parameter: "from" | "to",
  warnings: UrlStateWarning[],
): HistoryDateTime | undefined {
  const rawValue = params.get(parameter);
  if (rawValue === null) {
    return undefined;
  }
  if (isValidIsoDateTime(rawValue)) {
    return rawValue;
  }
  addWarning(warnings, parameter, rawValue, { key: "common.url.ignoredTime" });
  return undefined;
}

function parseStringEnum<T extends string>(
  params: URLSearchParams,
  parameter: string,
  values: Readonly<Record<T, true>>,
  fallback: T | undefined,
  warnings: UrlStateWarning[],
): T | undefined {
  const rawValue = params.get(parameter);
  if (rawValue === null) {
    return fallback;
  }
  if (hasOwn(values, rawValue)) {
    return rawValue as T;
  }
  addWarning(
    warnings,
    parameter,
    rawValue,
    fallback === undefined ? { key: "common.url.ignoredFilter" } : { key: "common.url.defaultValue", params: { value: fallback } },
  );
  return fallback;
}

function parseHorizon(params: URLSearchParams, warnings: UrlStateWarning[]): Horizon {
  const rawValue = params.get("horizon");
  if (rawValue === null) {
    return DEFAULT_RESEARCH_URL_STATE.horizon;
  }
  const horizon = Number(rawValue);
  if (Number.isInteger(horizon) && hasOwn(HORIZON_VALUES, horizon)) {
    return horizon as Horizon;
  }
  addWarning(warnings, "horizon", rawValue, { key: "common.url.defaultValue", params: { value: 5 } });
  return DEFAULT_RESEARCH_URL_STATE.horizon;
}

interface NumberRules {
  integer?: boolean;
  minimum?: number;
  maximum?: number;
}

function parseOptionalNumber(
  params: URLSearchParams,
  parameter: string,
  rules: NumberRules,
  warnings: UrlStateWarning[],
) {
  const rawValue = params.get(parameter);
  if (rawValue === null) {
    return undefined;
  }
  const parsed = Number(rawValue);
  const valid =
    Number.isFinite(parsed) &&
    (rules.integer !== true || Number.isInteger(parsed)) &&
    (rules.minimum === undefined || parsed >= rules.minimum) &&
    (rules.maximum === undefined || parsed <= rules.maximum);
  if (valid) {
    return parsed;
  }
  addWarning(warnings, parameter, rawValue, { key: "common.url.ignoredParameter" });
  return undefined;
}

function parseNumberWithDefault(
  params: URLSearchParams,
  parameter: string,
  fallback: number,
  rules: NumberRules,
  warnings: UrlStateWarning[],
) {
  const rawValue = params.get(parameter);
  if (rawValue === null) {
    return fallback;
  }
  const parsed = Number(rawValue);
  const valid =
    Number.isFinite(parsed) &&
    (rules.integer !== true || Number.isInteger(parsed)) &&
    (rules.minimum === undefined || parsed >= rules.minimum) &&
    (rules.maximum === undefined || parsed <= rules.maximum);
  if (valid) {
    return parsed;
  }
  addWarning(warnings, parameter, rawValue, { key: "common.url.defaultValue", params: { value: fallback } });
  return fallback;
}

function parseOptionalBoolean(
  params: URLSearchParams,
  parameter: string,
  warnings: UrlStateWarning[],
) {
  const rawValue = params.get(parameter);
  if (rawValue === null) {
    return undefined;
  }
  if (rawValue === "true") {
    return true;
  }
  if (rawValue === "false") {
    return false;
  }
  addWarning(warnings, parameter, rawValue, { key: "common.url.ignoredFilter" });
  return undefined;
}

export function decodeResearchUrlState(
  params: URLSearchParams,
  options: { route: "screen" },
): ScreenUrlState;
export function decodeResearchUrlState(
  params: URLSearchParams,
  options?: { route?: "research" },
): ResearchUrlState;
export function decodeResearchUrlState(
  params: URLSearchParams,
  options: UrlStateCodecOptions = {},
): ResearchUrlState | ScreenUrlState {
  const warnings: UrlStateWarning[] = [];
  const screenRoute = options.route === "screen";
  const mode = screenRoute
    ? parseStringEnum<ResearchDisplayMode>(
        params,
        "mode",
        RESEARCH_DISPLAY_MODE_VALUES,
        "research",
        warnings,
      )
    : undefined;
  const revisionNo = parseOptionalNumber(
    params,
    "revision_no",
    { integer: true, minimum: 0 },
    warnings,
  );
  const horizon = parseHorizon(params, warnings);
  const predictionBasis = parseStringEnum(
    params,
    "prediction_basis",
    PREDICTION_BASIS_VALUES,
    DEFAULT_RESEARCH_URL_STATE.predictionBasis,
    warnings,
  );
  const returnBasis = screenRoute
    ? parseStringEnum<ScreenReturnBasis>(
        params,
        "return_basis",
        SCREEN_RETURN_BASIS_VALUES,
        "executable",
        warnings,
      )
    : parseStringEnum<ReturnBasis>(
        params,
        "return_basis",
        RETURN_BASIS_VALUES,
        DEFAULT_RESEARCH_URL_STATE.returnBasis,
        warnings,
      );
  const chartTimeframe = parseStringEnum<HistoryTimeframe>(
    params,
    "timeframe",
    HISTORY_TIMEFRAME_VALUES,
    DEFAULT_RESEARCH_URL_STATE.chart.timeframe,
    warnings,
  );
  let chartAdjustment = parseStringEnum<HistoryAdjustment>(
    params,
    "adjustment",
    HISTORY_ADJUSTMENT_VALUES,
    DEFAULT_RESEARCH_URL_STATE.chart.adjustment,
    warnings,
  );
  if (!screenRoute && chartAdjustment === "split_adjusted") {
    addWarning(
      warnings,
      "adjustment",
      chartAdjustment,
      { key: "common.url.rawOnly" },
    );
    chartAdjustment = "raw";
  }
  let chartFrom = parseOptionalHistoryDateTime(params, "from", warnings);
  let chartTo = parseOptionalHistoryDateTime(params, "to", warnings);
  if (
    chartFrom !== undefined &&
    chartTo !== undefined &&
    Date.parse(chartFrom) > Date.parse(chartTo)
  ) {
    addWarning(warnings, "from", chartFrom, { key: "common.url.reversedRange" });
    addWarning(warnings, "to", chartTo, { key: "common.url.reversedRange" });
    chartFrom = undefined;
    chartTo = undefined;
  }
  const regime = parseStringEnum<Regime>(
    params,
    "regime",
    REGIME_VALUES,
    undefined,
    warnings,
  );
  const confidence = screenRoute
    ? parseStringEnum<ScreenConfidenceFloor>(
        params,
        "confidence",
        SCREEN_CONFIDENCE_VALUES,
        undefined,
        warnings,
      )
    : parseStringEnum<Confidence>(
        params,
        "confidence",
        CONFIDENCE_VALUES,
        undefined,
        warnings,
      );
  const dataStatus = parseStringEnum<DataStatus>(
    params,
    "serving_status",
    DATA_STATUS_VALUES,
    undefined,
    warnings,
  );
  const sortKey = parseStringEnum(
    params,
    "sort",
    SORT_KEY_VALUES,
    DEFAULT_RESEARCH_URL_STATE.sort.key,
    warnings,
  );
  const sortDirection = parseStringEnum(
    params,
    "direction",
    SORT_DIRECTION_VALUES,
    DEFAULT_RESEARCH_URL_STATE.sort.direction,
    warnings,
  );
  const minimumProbability = parseOptionalNumber(
    params,
    "min_probability",
    { minimum: 0, maximum: 1 },
    warnings,
  );
  const maximumQ10 = parseOptionalNumber(
    params,
    "max_q10",
    { minimum: -1, maximum: 1 },
    warnings,
  );
  const effectiveAbstain = parseOptionalBoolean(params, "effective_abstain", warnings);
  const pageNumber = parseNumberWithDefault(
    params,
    "page",
    DEFAULT_RESEARCH_URL_STATE.page.number,
    { integer: true, minimum: 1 },
    warnings,
  );
  const pageSize = parseNumberWithDefault(
    params,
    "page_size",
    DEFAULT_RESEARCH_URL_STATE.page.size,
    { integer: true, minimum: 1, maximum: 100 },
    warnings,
  );

  const optionEntries = [...params.entries()].filter(([key]) =>
    [
      "option_metric",
      "option_view",
      "option_map_side",
      "option_rank_window",
      "option_side",
      "option_snapshot",
      "option_dte_min",
      "option_dte_max",
      "option_strike_min",
      "option_strike_max",
      "option_scale",
      "option_tab",
      "pulse_mode",
      "option_cone",
      "model_cone",
      "options_horizon",
    ].includes(key),
  );
  const state: ResearchUrlState = {
    ...(optionEntries.length
      ? { optionParams: Object.fromEntries(optionEntries) }
      : {}),
    asOf: optionalString(params, "as_of"),
    snapshotId: optionalString(params, "snapshot_id"),
    revisionNo,
    symbol: optionalString(params, "symbol")?.toUpperCase(),
    horizon,
    predictionBasis: predictionBasis ?? DEFAULT_RESEARCH_URL_STATE.predictionBasis,
    returnBasis: returnBasis ?? DEFAULT_RESEARCH_URL_STATE.returnBasis,
    versions: {
      datasetRevision: optionalString(params, "dataset_revision"),
      featureSetVersion: optionalString(params, "feature_set_version"),
      universeVersion: optionalString(params, "universe_version"),
      modelVersion: optionalString(params, "model_version"),
      calibrationVersion: optionalString(params, "calibration_version"),
      strategyVersion: optionalString(params, "strategy_version"),
      splitVersion: optionalString(params, "split_version"),
    },
    filters: {
      sector: optionalString(params, "sector"),
      assetType: optionalString(params, "asset_type"),
      regime,
      minimumProbability,
      maximumQ10,
      confidence,
      dataStatus,
      effectiveAbstain,
    },
    sort: {
      key: sortKey ?? DEFAULT_RESEARCH_URL_STATE.sort.key,
      direction: sortDirection ?? DEFAULT_RESEARCH_URL_STATE.sort.direction,
    },
    page: {
      number: pageNumber,
      size: pageSize,
    },
    backtest: {
      evaluationWindowId: optionalString(params, "evaluation_window_id"),
      foldId: optionalString(params, "fold"),
      costScenario: optionalString(params, "cost_scenario"),
    },
    chart: {
      timeframe: chartTimeframe ?? DEFAULT_RESEARCH_URL_STATE.chart.timeframe,
      from: chartFrom,
      to: chartTo,
      adjustment: chartAdjustment ?? DEFAULT_RESEARCH_URL_STATE.chart.adjustment,
    },
    warnings,
  };
  if (!screenRoute) {
    return state;
  }
  const screenState: ScreenUrlState = {
    ...state,
    filters: {
      ...state.filters,
      confidence: state.filters.confidence as ScreenConfidenceFloor | undefined,
    },
    mode: mode ?? "research",
    returnBasis: state.returnBasis as ScreenReturnBasis,
  };
  const compatibleSorts =
    screenState.mode === "research"
      ? VERIFIED_RESEARCH_SORT_VALUES
      : COMMON_RESEARCH_SORT_VALUES;
  const incompatibleParameters: string[] = [];
  if (
    params.has("prediction_basis") &&
    hasOwn(PREDICTION_BASIS_VALUES, params.get("prediction_basis") ?? "")
  ) {
    incompatibleParameters.push("prediction_basis");
  }
  if (
    params.has("return_basis") &&
    hasOwn(SCREEN_RETURN_BASIS_VALUES, params.get("return_basis") ?? "")
  ) {
    incompatibleParameters.push("return_basis");
  }
  if (minimumProbability !== undefined) {
    incompatibleParameters.push("min_probability");
  }
  if (maximumQ10 !== undefined) {
    incompatibleParameters.push("max_q10");
  }
  if (confidence !== undefined) {
    incompatibleParameters.push("confidence");
  }
  if (effectiveAbstain !== undefined) {
    incompatibleParameters.push("effective_abstain");
  }
  if (
    params.has("sort") &&
    screenState.mode !== "formal" &&
    hasOwn(SORT_KEY_VALUES, params.get("sort") ?? "") &&
    !compatibleSorts.has(screenState.sort.key)
  ) {
    incompatibleParameters.push("sort");
  }
  if (params.has("direction") && screenState.mode !== "formal" && !params.has("sort")) {
    incompatibleParameters.push("direction");
  }
  return normalizeScreenModeState(screenState, {
    resetPage: incompatibleParameters.length > 0,
    warn: incompatibleParameters.length > 0,
  });
}

function setOptional(
  params: URLSearchParams,
  parameter: string,
  value: string | number | boolean | null | undefined,
) {
  if (value !== undefined && value !== null) {
    params.set(parameter, String(value));
  }
}

export function encodeResearchUrlState(
  state: ScreenUrlState,
  options: { route: "screen" },
): URLSearchParams;
export function encodeResearchUrlState(
  state: ResearchUrlState,
  options?: { route?: "research" },
): URLSearchParams;
export function encodeResearchUrlState(
  state: ResearchUrlState,
  options: UrlStateCodecOptions = {},
) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(state.optionParams ?? {}))
    params.set(key, value);
  const returnBasis =
    options.route === "screen" && !hasOwn(SCREEN_RETURN_BASIS_VALUES, state.returnBasis)
      ? "executable"
      : state.returnBasis;

  if (options.route === "screen") {
    params.set("mode", (state as ScreenUrlState).mode);
  }

  setOptional(params, "as_of", state.asOf);
  setOptional(params, "snapshot_id", state.snapshotId);
  setOptional(params, "revision_no", state.revisionNo);
  setOptional(params, "symbol", state.symbol);
  if (state.horizon !== DEFAULT_RESEARCH_URL_STATE.horizon) {
    params.set("horizon", String(state.horizon));
  }
  if (state.predictionBasis !== DEFAULT_RESEARCH_URL_STATE.predictionBasis) {
    params.set("prediction_basis", state.predictionBasis);
  }
  if (returnBasis !== DEFAULT_RESEARCH_URL_STATE.returnBasis) {
    params.set("return_basis", returnBasis);
  }

  setOptional(params, "dataset_revision", state.versions.datasetRevision);
  setOptional(params, "feature_set_version", state.versions.featureSetVersion);
  setOptional(params, "universe_version", state.versions.universeVersion);
  setOptional(params, "model_version", state.versions.modelVersion);
  setOptional(params, "calibration_version", state.versions.calibrationVersion);
  setOptional(params, "strategy_version", state.versions.strategyVersion);
  setOptional(params, "split_version", state.versions.splitVersion);

  setOptional(params, "sector", state.filters.sector);
  setOptional(params, "asset_type", state.filters.assetType);
  setOptional(params, "regime", state.filters.regime);
  setOptional(params, "min_probability", state.filters.minimumProbability);
  setOptional(params, "max_q10", state.filters.maximumQ10);
  setOptional(params, "confidence", state.filters.confidence);
  setOptional(params, "serving_status", state.filters.dataStatus);
  setOptional(params, "effective_abstain", state.filters.effectiveAbstain);

  if (state.sort.key !== DEFAULT_RESEARCH_URL_STATE.sort.key) {
    params.set("sort", state.sort.key);
  }
  if (state.sort.direction !== DEFAULT_RESEARCH_URL_STATE.sort.direction) {
    params.set("direction", state.sort.direction);
  }
  if (state.page.number !== DEFAULT_RESEARCH_URL_STATE.page.number) {
    params.set("page", String(state.page.number));
  }
  if (state.page.size !== DEFAULT_RESEARCH_URL_STATE.page.size) {
    params.set("page_size", String(state.page.size));
  }

  setOptional(params, "evaluation_window_id", state.backtest.evaluationWindowId);
  setOptional(params, "fold", state.backtest.foldId);
  setOptional(params, "cost_scenario", state.backtest.costScenario);

  if (state.chart.timeframe !== DEFAULT_RESEARCH_URL_STATE.chart.timeframe) {
    params.set("timeframe", state.chart.timeframe);
  }
  setOptional(params, "from", state.chart.from);
  setOptional(params, "to", state.chart.to);
  if (state.chart.adjustment !== DEFAULT_RESEARCH_URL_STATE.chart.adjustment) {
    params.set("adjustment", state.chart.adjustment);
  }

  return params;
}

export function urlStateWarningLabel(warning: UrlStateWarning, locale: Locale = "zh-CN") {
 if (warning.id === "url-state-mode-compatibility") return translate(locale, "common.url.modeCompatibility");
 return warning.behavior_ref
 ? translate(locale, "common.url.invalidValue", {parameter:warning.parameter, value:warning.rejectedValue, behavior:translateRef(locale, warning.behavior_ref)})
 : translate(locale, "common.url.invalidGeneric", {parameter:warning.parameter, value:warning.rejectedValue});
}
