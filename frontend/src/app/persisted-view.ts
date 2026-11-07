import type { QueryClient, QueryKey } from "@tanstack/react-query";

import type { components } from "../api/generated/schema";

const STORAGE_KEY = "prediction-stock:persisted-views:v1";
const SCHEMA_VERSION = 1;
const DEFAULT_MAX_AGE_MS = 72 * 60 * 60 * 1000;
const DEFAULT_MAX_RECORDS = 12;

type ResolvedSnapshot = components["schemas"]["ResolvedSnapshot"];
type SnapshotIdentity = Pick<
  ResolvedSnapshot,
  | "as_of"
  | "calibration_version"
  | "dataset_revision"
  | "feature_set_version"
  | "model_version"
  | "revision_no"
  | "snapshot_id"
  | "universe_version"
>;

export interface PersistedViewStorage {
  getItem(key: string): string | null;
  removeItem(key: string): void;
  setItem(key: string, value: string): void;
}

interface PersistedViewRecord {
  data: unknown;
  queryKey: QueryKey;
  savedAt: string;
  snapshotIdentity: SnapshotIdentity;
}

interface PersistedViewEnvelope {
  records: PersistedViewRecord[];
  schemaVersion: 1;
}

export interface PersistedViewOptions {
  maxAgeMs?: number;
  maxRecords?: number;
  now?: () => Date;
  storage?: PersistedViewStorage;
}

function defaultStorage() {
  try {
    const candidate: unknown = globalThis.localStorage;
    if (
      !isRecord(candidate) ||
      typeof candidate.getItem !== "function" ||
      typeof candidate.removeItem !== "function" ||
      typeof candidate.setItem !== "function"
    ) {
      return undefined;
    }
    return candidate as unknown as PersistedViewStorage;
  } catch {
    return undefined;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function snapshotIdentity(value: unknown): SnapshotIdentity | null {
  if (!isRecord(value)) return null;
  const requiredStrings = [
    "as_of",
    "dataset_revision",
    "feature_set_version",
    "snapshot_id",
    "universe_version",
  ] as const;
  if (!requiredStrings.every((key) => isString(value[key]))) return null;
  if (
    !["calibration_version", "model_version"].every(
      (key) => value[key] === null || isString(value[key]),
    )
  ) {
    return null;
  }
  if (!Number.isInteger(value.revision_no) || Number(value.revision_no) < 1) {
    return null;
  }
  return value as unknown as SnapshotIdentity;
}

function extractValidatedIdentity(data: unknown) {
  if (!isRecord(data)) return null;
  if (!isString(data.generated_at) || !isString(data.request_id)) return null;
  if (typeof data.effective_abstain !== "boolean") return null;
  return snapshotIdentity(data.resolved_snapshot);
}

const IDENTITY_ALIASES = {
  as_of: ["as_of", "asOf"],
  calibration_version: ["calibration_version", "calibrationVersion"],
  dataset_revision: ["dataset_revision", "datasetRevision"],
  feature_set_version: ["feature_set_version", "featureSetVersion"],
  model_version: ["model_version", "modelVersion"],
  snapshot_id: ["snapshot_id", "snapshotId"],
  universe_version: ["universe_version", "universeVersion"],
} as const;

function collectIdentityValues(value: unknown, output: Map<string, (string | null)[]>) {
  if (Array.isArray(value)) {
    value.forEach((item) => collectIdentityValues(item, output));
    return;
  }
  if (!isRecord(value)) return;
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === "string" || item === null) {
      const values = output.get(key) ?? [];
      values.push(item);
      output.set(key, values);
    }
    collectIdentityValues(item, output);
  }
}

function queryKeyMatchesIdentity(queryKey: QueryKey, identity: SnapshotIdentity) {
  const values = new Map<string, (string | null)[]>();
  collectIdentityValues(queryKey, values);
  return Object.entries(IDENTITY_ALIASES).every(([identityKey, aliases]) => {
    const expected = identity[identityKey as keyof typeof IDENTITY_ALIASES];
    return aliases.some((alias) => values.get(alias)?.includes(expected));
  });
}

function sameIdentity(left: SnapshotIdentity, right: SnapshotIdentity) {
  return (
    left.as_of === right.as_of &&
    left.calibration_version === right.calibration_version &&
    left.dataset_revision === right.dataset_revision &&
    left.feature_set_version === right.feature_set_version &&
    left.model_version === right.model_version &&
    left.revision_no === right.revision_no &&
    left.snapshot_id === right.snapshot_id &&
    left.universe_version === right.universe_version
  );
}

function parseEnvelope(storage: PersistedViewStorage): PersistedViewEnvelope {
  const raw = storage.getItem(STORAGE_KEY);
  if (raw === null) return { records: [], schemaVersion: SCHEMA_VERSION };
  try {
    const parsed: unknown = JSON.parse(raw);
    if (
      !isRecord(parsed) ||
      parsed.schemaVersion !== SCHEMA_VERSION ||
      !Array.isArray(parsed.records)
    ) {
      storage.removeItem(STORAGE_KEY);
      return { records: [], schemaVersion: SCHEMA_VERSION };
    }
    return {
      records: parsed.records as PersistedViewRecord[],
      schemaVersion: SCHEMA_VERSION,
    };
  } catch {
    storage.removeItem(STORAGE_KEY);
    return { records: [], schemaVersion: SCHEMA_VERSION };
  }
}

function validatedRecord(
  value: unknown,
  now: Date,
  maxAgeMs: number,
): PersistedViewRecord | null {
  if (!isRecord(value) || !Array.isArray(value.queryKey)) return null;
  const savedAt = typeof value.savedAt === "string" ? Date.parse(value.savedAt) : NaN;
  if (!Number.isFinite(savedAt) || savedAt > now.getTime()) return null;
  if (now.getTime() - savedAt > maxAgeMs) return null;
  const identity = snapshotIdentity(value.snapshotIdentity);
  const dataIdentity = extractValidatedIdentity(value.data);
  if (identity === null || dataIdentity === null) return null;
  if (!sameIdentity(identity, dataIdentity)) return null;
  if (!queryKeyMatchesIdentity(value.queryKey, identity)) return null;
  return value as unknown as PersistedViewRecord;
}

function serializeQueryKey(queryKey: QueryKey) {
  try {
    return JSON.stringify(queryKey);
  } catch {
    return null;
  }
}

export function restorePersistedViews(
  client: QueryClient,
  options: PersistedViewOptions = {},
) {
  const storage = options.storage ?? defaultStorage();
  if (storage === undefined) return 0;
  const now = (options.now ?? (() => new Date()))();
  const maxAgeMs = options.maxAgeMs ?? DEFAULT_MAX_AGE_MS;
  const records = parseEnvelope(storage)
    .records.map((record) => validatedRecord(record, now, maxAgeMs))
    .filter((record): record is PersistedViewRecord => record !== null);
  for (const record of records) {
    client.setQueryData(record.queryKey, record.data, {
      updatedAt: Date.parse(record.savedAt),
    });
  }
  return records.length;
}

export function attachPersistedViewStorage(
  client: QueryClient,
  options: PersistedViewOptions = {},
) {
  const storage = options.storage ?? defaultStorage();
  if (storage === undefined) return () => undefined;
  const maxAgeMs = options.maxAgeMs ?? DEFAULT_MAX_AGE_MS;
  const maxRecords = options.maxRecords ?? DEFAULT_MAX_RECORDS;
  const now = options.now ?? (() => new Date());

  return client.getQueryCache().subscribe((event) => {
    if (event.type !== "updated" || event.action.type !== "success") return;
    const data: unknown = event.query.state.data;
    const rawQueryKey: unknown = event.query.queryKey;
    if (!Array.isArray(rawQueryKey)) return;
    const queryKey: QueryKey = rawQueryKey;
    const identity = extractValidatedIdentity(data);
    if (identity === null) return;
    if (!queryKeyMatchesIdentity(queryKey, identity)) return;
    const serialized = serializeQueryKey(queryKey);
    if (serialized === null) return;
    const savedAt = now().toISOString();
    const existing = parseEnvelope(storage)
      .records.map((record) => validatedRecord(record, now(), maxAgeMs))
      .filter((record): record is PersistedViewRecord => record !== null)
      .filter((record) => serializeQueryKey(record.queryKey) !== serialized);
    const records = [
      ...existing,
      {
        data,
        queryKey,
        savedAt,
        snapshotIdentity: identity,
      },
    ]
      .sort((left, right) => left.savedAt.localeCompare(right.savedAt))
      .slice(-maxRecords);
    const envelope: PersistedViewEnvelope = {
      records,
      schemaVersion: SCHEMA_VERSION,
    };
    storage.setItem(STORAGE_KEY, JSON.stringify(envelope));
  });
}
