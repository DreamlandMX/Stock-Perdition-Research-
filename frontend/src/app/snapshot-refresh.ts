import type { paths } from "../api/generated/schema";

export const EOD_OPERATION_EVENT = "prediction-stock:eod-operation";

type SnapshotQuery = Pick<
  NonNullable<paths["/api/v1/instruments"]["get"]["parameters"]["query"]>,
  | "as_of"
  | "snapshot_id"
  | "dataset_revision"
  | "feature_set_version"
  | "universe_version"
  | "model_version"
  | "calibration_version"
>;

export function snapshotQueryFromSearch(search: string): SnapshotQuery {
  const params = new URLSearchParams(search);
  const query: SnapshotQuery = {};
  for (const key of [
    "as_of",
    "snapshot_id",
    "dataset_revision",
    "feature_set_version",
    "universe_version",
    "model_version",
    "calibration_version",
  ] as const) {
    const value = params.get(key);
    if (value) query[key] = value;
  }
  return query;
}
export const EOD_UPDATE_SETTLED_EVENT = "prediction-stock:eod-update-settled";

const MANUAL_EOD_UPDATE_KEY = "prediction-stock:manual-eod-update:v1";

export const SNAPSHOT_IDENTITY_KEYS = [
  "as_of",
  "snapshot_id",
  "revision_no",
  "dataset_revision",
  "feature_set_version",
  "universe_version",
  "model_version",
  "calibration_version",
  "split_version",
  "strategy_version",
] as const;

export const EOD_REFRESH_QUERY_ROOTS = new Set([
  "overview",
  "snapshot-context",
  "options-header",
  "screen",
  "screen-instruments",
  "search-instruments",
  "instrument-forecast",
  "instrument-history",
  "instrument-metadata",
  "backtests",
  "backtest-models",
  "health",
  "options-overview",
  "options-board",
  "options-collection",
  "options-experiments",
  "options",
  "option-analytics",
  "option-forecast",
]);

export interface ManualEodUpdateMarker {
  operationId: string;
  previousCutoff: string | null;
  previousSnapshotId: string | null;
}

function sessionDate(value: string | null) {
  if (value === null) return null;
  const matched = /^(\d{4}-\d{2}-\d{2})/.exec(value);
  return matched?.[1] ?? null;
}

export function rememberManualEodUpdate(operationId: string, search: string) {
  const params = new URLSearchParams(search);
  const marker: ManualEodUpdateMarker = {
    operationId,
    previousCutoff: sessionDate(params.get("as_of")),
    previousSnapshotId: params.get("snapshot_id"),
  };
  try {
    window.sessionStorage.setItem(MANUAL_EOD_UPDATE_KEY, JSON.stringify(marker));
  } catch {
    // The operation still runs when storage is unavailable; only auto-switch is skipped.
  }
  return marker;
}

export function readManualEodUpdate(): ManualEodUpdateMarker | null {
  try {
    const raw = window.sessionStorage.getItem(MANUAL_EOD_UPDATE_KEY);
    if (raw === null) return null;
    const value = JSON.parse(raw) as Partial<ManualEodUpdateMarker>;
    if (typeof value.operationId !== "string") return null;
    return {
      operationId: value.operationId,
      previousCutoff:
        typeof value.previousCutoff === "string" ? value.previousCutoff : null,
      previousSnapshotId:
        typeof value.previousSnapshotId === "string" ? value.previousSnapshotId : null,
    };
  } catch {
    return null;
  }
}

export function clearManualEodUpdate(operationId: string) {
  const marker = readManualEodUpdate();
  if (marker?.operationId !== operationId) return;
  try {
    window.sessionStorage.removeItem(MANUAL_EOD_UPDATE_KEY);
  } catch {
    // No cleanup is needed when storage is unavailable.
  }
}

export function withoutSnapshotIdentity(search: string) {
  const params = new URLSearchParams(search);
  SNAPSHOT_IDENTITY_KEYS.forEach((key) => params.delete(key));
  const query = params.toString();
  return query === "" ? "" : `?${query}`;
}
