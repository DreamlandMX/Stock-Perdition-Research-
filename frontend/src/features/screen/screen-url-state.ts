import type { Locale } from "../../i18n/core";
import type { components, paths } from "../../api/generated/schema";
import { formatUsMarketTime } from "../../app/presentation";
import {
  decodeResearchUrlState,
  encodeResearchUrlState,
  normalizeScreenModeState,
  type ScreenUrlState,
} from "../../app/url-state";
export type ScreenQuery = NonNullable<
  paths["/api/v1/screen"]["get"]["parameters"]["query"]
>;
export type InstrumentQuery = NonNullable<
  paths["/api/v1/instruments"]["get"]["parameters"]["query"]
>;
export const DEFAULT_SCREEN_URL_STATE = decodeResearchUrlState(new URLSearchParams(), {
  route: "screen",
});
function addOptional<T extends object, K extends keyof T>(
  target: T,
  key: K,
  value: T[K] | undefined,
) {
  if (value !== undefined) {
    target[key] = value;
  }
}
export function buildScreenRequest(state: ScreenUrlState): ScreenQuery {
  const compatibleState = normalizeScreenModeState(state);
  const query: ScreenQuery = {
    direction: compatibleState.sort.direction,
    horizon: compatibleState.horizon,
    limit: compatibleState.page.size,
    mode: compatibleState.mode,
    page: compatibleState.page.number,
    sort: compatibleState.sort.key,
  };
  if (compatibleState.mode === "formal") {
    query.prediction_basis = compatibleState.predictionBasis;
    query.return_basis = compatibleState.returnBasis;
  }
  addOptional(query, "as_of", compatibleState.asOf);
  addOptional(query, "snapshot_id", compatibleState.snapshotId);
  addOptional(query, "dataset_revision", compatibleState.versions.datasetRevision);
  addOptional(query, "feature_set_version", compatibleState.versions.featureSetVersion);
  addOptional(query, "universe_version", compatibleState.versions.universeVersion);
  addOptional(query, "model_version", compatibleState.versions.modelVersion);
  addOptional(
    query,
    "calibration_version",
    compatibleState.versions.calibrationVersion,
  );
  addOptional(query, "sector", compatibleState.filters.sector);
  addOptional(query, "asset_type", compatibleState.filters.assetType);
  addOptional(query, "regime", compatibleState.filters.regime);
  if (compatibleState.mode === "formal") {
    addOptional(query, "min_probability", compatibleState.filters.minimumProbability);
    addOptional(query, "max_q10", compatibleState.filters.maximumQ10);
    addOptional(query, "confidence", compatibleState.filters.confidence);
    addOptional(query, "effective_abstain", compatibleState.filters.effectiveAbstain);
  }
  addOptional(query, "serving_status", compatibleState.filters.dataStatus);
  return query;
}
export function buildInstrumentRequest(state: ScreenUrlState): InstrumentQuery {
  const query: InstrumentQuery = { limit: 100, page: 1, sort: "symbol" };
  addOptional(query, "as_of", state.asOf);
  addOptional(query, "snapshot_id", state.snapshotId);
  addOptional(query, "dataset_revision", state.versions.datasetRevision);
  addOptional(query, "feature_set_version", state.versions.featureSetVersion);
  addOptional(query, "universe_version", state.versions.universeVersion);
  addOptional(query, "model_version", state.versions.modelVersion);
  addOptional(query, "calibration_version", state.versions.calibrationVersion);
  return query;
}
export function resolveScreenUrlState(
  state: ScreenUrlState,
  snapshot: components["schemas"]["ResolvedSnapshot"],
): ScreenUrlState {
  return {
    ...state,
    asOf: snapshot.as_of,
    revisionNo: snapshot.revision_no,
    snapshotId: snapshot.snapshot_id,
    versions: {
      ...state.versions,
      calibrationVersion: snapshot.calibration_version ?? undefined,
      datasetRevision: snapshot.dataset_revision ?? undefined,
      featureSetVersion: snapshot.feature_set_version ?? undefined,
      modelVersion: snapshot.model_version ?? undefined,
      universeVersion: snapshot.universe_version ?? undefined,
    },
  };
}
export function resetScreenControls(state: ScreenUrlState): ScreenUrlState {
  return {
    ...state,
    filters: DEFAULT_SCREEN_URL_STATE.filters,
    horizon: DEFAULT_SCREEN_URL_STATE.horizon,
    page: DEFAULT_SCREEN_URL_STATE.page,
    predictionBasis: DEFAULT_SCREEN_URL_STATE.predictionBasis,
    returnBasis: DEFAULT_SCREEN_URL_STATE.returnBasis,
    sort: DEFAULT_SCREEN_URL_STATE.sort,
    warnings: [],
  };
}
export function screenResearchHref(
  row: components["schemas"]["ScreenRow"],
  state: ScreenUrlState,
) {
  const symbol = row.symbol;
  const params = encodeResearchUrlState({ ...state, symbol });
  params.set("horizon", String(state.horizon));
  return `/research/${encodeURIComponent(symbol)}?${params.toString()}`;
}
export function displaySymbol(symbol: string) {
  return symbol.startsWith("US.") ? symbol.slice(3) : symbol;
}
export function displayDataTime(value: string, locale: Locale = "zh-CN") {
  return formatUsMarketTime(value, locale);
}
