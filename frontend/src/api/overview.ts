import { createResearchApiClient } from "./client";
import type { components, paths } from "./generated/schema";

type Query = NonNullable<paths["/api/v1/overview"]["get"]["parameters"]["query"]>;
type Overview = components["schemas"]["ResearchResponse_OverviewReadModel_"];
const client = createResearchApiClient();
const historyClient = createResearchApiClient({ trackConnectivity: false });
const pending = new Map<string, Promise<Overview>>();
export const HISTORY_BATCH_SIZE = 32;
let activeHistoryRequests = 0;
const historyWaiters: (() => void)[] = [];
type Snapshot = components["schemas"]["ResolvedSnapshot"];
type History = components["schemas"]["InstrumentHistory"];
type HistoryRequest = {
  symbol: string;
  signal: AbortSignal | undefined;
  resolve: (history: History) => void;
  reject: (error: unknown) => void;
};
const pendingHistories = new Map<string, HistoryRequest[]>();

export async function loadOverviewHistoryBatchFromApi(
  snapshot: Snapshot,
  symbols: string[],
  signal?: AbortSignal,
) {
  if (activeHistoryRequests >= 2)
    await new Promise<void>((resolve) => historyWaiters.push(resolve));
  else activeHistoryRequests++;
  try {
    signal?.throwIfAborted();
    const from = new Date(Date.parse(snapshot.as_of) - 365 * 86_400_000).toISOString();
    const { data } = await historyClient.get("/api/v1/history/batch", {
      params: {
        query: {
          snapshot_id: snapshot.snapshot_id,
          as_of: snapshot.as_of,
          dataset_revision: snapshot.dataset_revision,
          feature_set_version: snapshot.feature_set_version,
          universe_version: snapshot.universe_version,
          model_version: snapshot.model_version,
          calibration_version: snapshot.calibration_version,
          symbols,
          timeframe: "1d",
          adjustment: "raw",
          from,
          to: snapshot.as_of,
        },
      },
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(15_000)])
        : AbortSignal.timeout(15_000),
    });
    if (data === undefined) throw new Error("日线响应不可用");
    const actual = data.resolved_snapshot;
    if (
      [
        "snapshot_id",
        "as_of",
        "revision_no",
        "dataset_revision",
        "feature_set_version",
        "universe_version",
        "model_version",
        "calibration_version",
      ].some((key) => actual[key as keyof Snapshot] !== snapshot[key as keyof Snapshot])
    )
      throw new Error("日线快照身份不一致");
    if (
      data.data.items.length !== symbols.length ||
      new Set(data.data.items.map((item) => item.symbol)).size !== symbols.length ||
      data.data.items.some((item) => !symbols.includes(item.symbol))
    )
      throw new Error("批量日线标的身份不一致");
    return data.data.items.map((item) => {
      const history = item.history;
      if (!history) return item;
      if (
        history.symbol !== item.symbol ||
        history.timeframe !== "1d" ||
        history.adjustment !== "raw"
      )
        throw new Error("日线标的或价格口径不一致");
      return {
        ...item,
        history: {
          ...history,
          bars: history.bars.map((bar) => ({
            ...bar,
            data_gap:
              bar.data_gap ||
              history.gaps.some(
                (gap) =>
                  Date.parse(bar.at) >= Date.parse(gap.start_at) &&
                  Date.parse(bar.at) <= Date.parse(gap.end_at),
              ),
          })),
        },
      };
    });
  } finally {
    const next = historyWaiters.shift();
    if (next) next();
    else activeHistoryRequests--;
  }
}

export function loadOverviewHistoryFromApi(
  snapshot: Snapshot,
  symbol: string,
  signal?: AbortSignal,
): Promise<History> {
  const key = JSON.stringify(snapshot);
  return new Promise((resolve, reject) => {
    const fail = (error: unknown) =>
      reject(error instanceof Error ? error : new Error("日线读取失败"));
    if (signal?.aborted) {
      fail(signal.reason);
      return;
    }
    const abort = () => fail(signal?.reason);
    signal?.addEventListener("abort", abort, { once: true });
    const requests = pendingHistories.get(key);
    const request: HistoryRequest = {
      symbol,
      signal,
      resolve: (history) => {
        signal?.removeEventListener("abort", abort);
        resolve(history);
      },
      reject: (error) => {
        signal?.removeEventListener("abort", abort);
        fail(error);
      },
    };
    if (requests) {
      requests.push(request);
      return;
    }
    pendingHistories.set(key, [request]);
    queueMicrotask(() => {
      const queued = pendingHistories
        .get(key)!
        .filter((caller) => !caller.signal?.aborted);
      pendingHistories.delete(key);
      const symbols = [...new Set(queued.map((item) => item.symbol))];
      for (let start = 0; start < symbols.length; start += HISTORY_BATCH_SIZE) {
        const batch = symbols.slice(start, start + HISTORY_BATCH_SIZE);
        const callers = queued.filter((item) => batch.includes(item.symbol));
        void loadOverviewHistoryBatchFromApi(snapshot, batch).then(
          (items) => {
            for (const caller of callers) {
              try {
                caller.signal?.throwIfAborted();
                const item = items.find((item) => item.symbol === caller.symbol)!;
                if (item.status !== "available" || !item.history)
                  throw new Error(item.error_code ?? "日线读取失败");
                caller.resolve(item.history);
              } catch (error) {
                caller.reject(error);
              }
            }
          },
          (error: unknown) => callers.forEach((caller) => caller.reject(error)),
        );
      }
    });
  });
}

export function loadOverviewFromApi(query: Query): Promise<Overview> {
  const key = JSON.stringify(
    Object.entries(query)
      .filter(([, value]) => value !== undefined)
      .sort(([a], [b]) => a.localeCompare(b)),
  );
  const existing = pending.get(key);
  if (existing) return existing;
  const request = client
    .get("/api/v1/overview", { params: { query } })
    .then(({ data }) => {
      if (data === undefined) throw new Error("overview response unavailable");
      return data;
    })
    .finally(() => pending.delete(key));
  pending.set(key, request);
  return request;
}
