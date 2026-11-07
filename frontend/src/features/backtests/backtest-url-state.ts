import type { components, paths } from "../../api/generated/schema";

export type BacktestQuery = NonNullable<
  paths["/api/v1/backtests"]["get"]["parameters"]["query"]
>;
export type ModelQuery = NonNullable<
  paths["/api/v1/models"]["get"]["parameters"]["query"]
>;
type Horizon = NonNullable<ModelQuery["horizon"]>;
type BacktestReadModel = components["schemas"]["BacktestReadModel"];
type ResolvedSnapshot = components["schemas"]["ResolvedSnapshot"];

export interface BacktestUrlStateWarning {
  message: string;
  parameter: string;
  rejectedValue: string;
}

export interface BacktestUrlState {
  asOf: string | undefined;
  snapshotId: string | undefined;
  versions: {
    calibrationVersion: string | undefined;
    datasetRevision: string | undefined;
    featureSetVersion: string | undefined;
    modelVersion: string | undefined;
    splitVersion: string | undefined;
    strategyVersion: string | undefined;
    universeVersion: string | undefined;
  };
  evaluationWindowId: string | undefined;
  foldId: string | undefined;
  horizon: Horizon;
  task: string | undefined;
  assetGroup: string | undefined;
  sector: string | undefined;
  regime: string | undefined;
  confidenceSlice: "high" | "medium" | "low" | "abstain" | undefined;
  costScenario: string | undefined;
  from: string | undefined;
  to: string | undefined;
  warnings: readonly BacktestUrlStateWarning[];
}

export const DEFAULT_BACKTEST_URL_STATE: BacktestUrlState = {
  asOf: undefined,
  snapshotId: undefined,
  versions: {
    calibrationVersion: undefined,
    datasetRevision: undefined,
    featureSetVersion: undefined,
    modelVersion: undefined,
    splitVersion: undefined,
    strategyVersion: undefined,
    universeVersion: undefined,
  },
  evaluationWindowId: undefined,
  foldId: undefined,
  horizon: 5,
  task: undefined,
  assetGroup: undefined,
  sector: undefined,
  regime: undefined,
  confidenceSlice: undefined,
  costScenario: undefined,
  from: undefined,
  to: undefined,
  warnings: [],
};

function optionalString(params: URLSearchParams, parameter: string) {
  const value = params.get(parameter)?.trim();
  return value === undefined || value === "" ? undefined : value;
}

function warning(
  warnings: BacktestUrlStateWarning[],
  parameter: string,
  rejectedValue: string,
) {
  warnings.push({
    message: `URL 参数 ${parameter} 的值“${rejectedValue}”无效，已忽略。`,
    parameter,
    rejectedValue,
  });
}

function parseHorizon(
  params: URLSearchParams,
  warnings: BacktestUrlStateWarning[],
): Horizon {
  const raw = params.get("horizon");
  if (raw === null) {
    return DEFAULT_BACKTEST_URL_STATE.horizon;
  }
  const value = Number(raw);
  if (value === 1 || value === 5 || value === 20) {
    return value;
  }
  warning(warnings, "horizon", raw);
  return DEFAULT_BACKTEST_URL_STATE.horizon;
}

const CONFIDENCE_SLICES = new Set(["high", "medium", "low", "abstain"]);

function parseConfidence(
  params: URLSearchParams,
  warnings: BacktestUrlStateWarning[],
): BacktestUrlState["confidenceSlice"] {
  const raw = params.get("confidence");
  if (raw === null) {
    return undefined;
  }
  if (CONFIDENCE_SLICES.has(raw)) {
    return raw as NonNullable<BacktestUrlState["confidenceSlice"]>;
  }
  warning(warnings, "confidence", raw);
  return undefined;
}

const ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function parseDate(
  params: URLSearchParams,
  parameter: "from" | "to",
  warnings: BacktestUrlStateWarning[],
) {
  const raw = params.get(parameter);
  if (raw === null) {
    return undefined;
  }
  if (ISO_DATE_PATTERN.test(raw)) {
    const parsed = new Date(`${raw}T00:00:00Z`);
    if (!Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === raw) {
      return raw;
    }
  }
  warning(warnings, parameter, raw);
  return undefined;
}

export function decodeBacktestUrlState(params: URLSearchParams): BacktestUrlState {
  const warnings: BacktestUrlStateWarning[] = [];
  const horizon = parseHorizon(params, warnings);
  const confidenceSlice = parseConfidence(params, warnings);
  let from = parseDate(params, "from", warnings);
  let to = parseDate(params, "to", warnings);
  if (from !== undefined && to !== undefined && from > to) {
    warning(warnings, "from", from);
    warning(warnings, "to", to);
    from = undefined;
    to = undefined;
  }

  return {
    asOf: optionalString(params, "as_of"),
    snapshotId: optionalString(params, "snapshot_id"),
    versions: {
      calibrationVersion: optionalString(params, "calibration_version"),
      datasetRevision: optionalString(params, "dataset_revision"),
      featureSetVersion: optionalString(params, "feature_set_version"),
      modelVersion: optionalString(params, "model_version"),
      splitVersion: optionalString(params, "split_version"),
      strategyVersion: optionalString(params, "strategy_version"),
      universeVersion: optionalString(params, "universe_version"),
    },
    evaluationWindowId: optionalString(params, "evaluation_window_id"),
    foldId: optionalString(params, "fold"),
    horizon,
    task: optionalString(params, "task"),
    assetGroup: optionalString(params, "asset_group"),
    sector: optionalString(params, "sector"),
    regime: optionalString(params, "regime"),
    confidenceSlice,
    costScenario: optionalString(params, "cost_scenario"),
    from,
    to,
    warnings,
  };
}

function setOptional(
  params: URLSearchParams,
  parameter: string,
  value: string | number | undefined,
) {
  if (value !== undefined) {
    params.set(parameter, String(value));
  }
}

export function encodeBacktestUrlState(state: BacktestUrlState) {
  const params = new URLSearchParams();
  setOptional(params, "as_of", state.asOf);
  setOptional(params, "snapshot_id", state.snapshotId);
  setOptional(params, "dataset_revision", state.versions.datasetRevision);
  setOptional(params, "feature_set_version", state.versions.featureSetVersion);
  setOptional(params, "calibration_version", state.versions.calibrationVersion);
  setOptional(params, "model_version", state.versions.modelVersion);
  setOptional(params, "strategy_version", state.versions.strategyVersion);
  setOptional(params, "universe_version", state.versions.universeVersion);
  setOptional(params, "split_version", state.versions.splitVersion);
  setOptional(params, "evaluation_window_id", state.evaluationWindowId);
  setOptional(params, "fold", state.foldId);
  if (state.horizon !== DEFAULT_BACKTEST_URL_STATE.horizon) {
    params.set("horizon", String(state.horizon));
  }
  setOptional(params, "task", state.task);
  setOptional(params, "asset_group", state.assetGroup);
  setOptional(params, "sector", state.sector);
  setOptional(params, "regime", state.regime);
  setOptional(params, "confidence", state.confidenceSlice);
  setOptional(params, "cost_scenario", state.costScenario);
  setOptional(params, "from", state.from);
  setOptional(params, "to", state.to);
  return params;
}

function addOptional<T extends object, K extends keyof T>(
  target: T,
  key: K,
  value: T[K] | undefined,
) {
  if (value !== undefined) {
    target[key] = value;
  }
}

export function buildBacktestRequest(state: BacktestUrlState): BacktestQuery {
  const query: BacktestQuery = {};
  addOptional(query, "as_of", state.asOf);
  addOptional(query, "snapshot_id", state.snapshotId);
  addOptional(query, "dataset_revision", state.versions.datasetRevision);
  addOptional(query, "feature_set_version", state.versions.featureSetVersion);
  addOptional(query, "calibration_version", state.versions.calibrationVersion);
  addOptional(query, "model_version", state.versions.modelVersion);
  addOptional(query, "strategy_version", state.versions.strategyVersion);
  addOptional(query, "universe_version", state.versions.universeVersion);
  addOptional(query, "split_version", state.versions.splitVersion);
  addOptional(query, "task", state.task);
  addOptional(query, "horizon", state.horizon);
  addOptional(query, "asset_group", state.assetGroup);
  addOptional(query, "fold", state.foldId);
  addOptional(query, "evaluation_window_id", state.evaluationWindowId);
  addOptional(query, "cost_scenario", state.costScenario);
  return query;
}

export function buildModelRequest(state: BacktestUrlState): ModelQuery {
  const query: ModelQuery = { horizon: state.horizon };
  addOptional(query, "as_of", state.asOf);
  addOptional(query, "snapshot_id", state.snapshotId);
  addOptional(query, "dataset_revision", state.versions.datasetRevision);
  addOptional(query, "feature_set_version", state.versions.featureSetVersion);
  addOptional(query, "calibration_version", state.versions.calibrationVersion);
  addOptional(query, "model_version", state.versions.modelVersion);
  addOptional(query, "universe_version", state.versions.universeVersion);
  addOptional(query, "task", state.task);
  addOptional(query, "asset_group", state.assetGroup);
  return query;
}

export function selectBacktestEvidence(
  data: components["schemas"]["BacktestReadModel"],
  state: BacktestUrlState,
) {
  const startsBeforeEnd = (value: string) =>
    state.to === undefined || value.slice(0, 10) <= state.to;
  const endsAfterStart = (value: string) =>
    state.from === undefined || value.slice(0, 10) >= state.from;
  const folds = data.fold_metrics.filter(
    (fold) =>
      (state.foldId === undefined || fold.fold_id === state.foldId) &&
      startsBeforeEnd(fold.evaluation_start) &&
      endsAfterStart(fold.evaluation_end),
  );
  const economicSeries = data.economic_series.filter(
    (point) =>
      (state.foldId === undefined || point.fold_id === state.foldId) &&
      startsBeforeEnd(point.at) &&
      endsAfterStart(point.at),
  );
  return { economicSeries, folds };
}

export function resolveBacktestUrlState(
  state: BacktestUrlState,
  snapshot: ResolvedSnapshot,
  data: BacktestReadModel,
): BacktestUrlState {
  return {
    ...state,
    asOf: snapshot.as_of,
    snapshotId: snapshot.snapshot_id,
    versions: {
      calibrationVersion: snapshot.calibration_version ?? undefined,
      datasetRevision: snapshot.dataset_revision,
      featureSetVersion: snapshot.feature_set_version,
      modelVersion: data.model_version,
      splitVersion: data.split_version,
      strategyVersion: data.strategy_version,
      universeVersion: data.universe_version,
    },
    evaluationWindowId: data.evaluation_window_id,
    horizon: data.horizon,
    task: data.task,
    assetGroup: data.asset_group,
    costScenario: data.cost_scenario,
  };
}
