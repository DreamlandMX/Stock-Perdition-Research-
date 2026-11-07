import { afterEach, expect, test, vi } from "vitest";
import { loadOverviewHistoryFromApi } from "./overview";
import type { components } from "./generated/schema";
import { renderHook, waitFor } from "@testing-library/react";
import { reportApiConnectivity, useConnectivity } from "../app/connectivity";

export const historySnapshot: components["schemas"]["ResolvedSnapshot"] = {
  as_of: "2026-10-02T20:00:00Z",
  snapshot_id: "snap-a",
  revision_no: 1,
  dataset_revision: "data-a",
  feature_set_version: "features",
  universe_version: "universe",
  model_version: null,
  calibration_version: null,
  prediction_readiness: "data_only",
  research_readiness: "rules_only",
};
afterEach(() => vi.unstubAllGlobals());
function response(symbols: string[], snapshot = historySnapshot) {
  return new Response(
    JSON.stringify({
      resolved_snapshot: snapshot,
      data: {
        items: symbols.map((symbol) => ({
          symbol,
          status: symbol === "US.FAIL" ? "unavailable" : "available",
          error_code: symbol === "US.FAIL" ? "downstream 500" : null,
          history:
            symbol === "US.FAIL"
              ? null
              : {
                  symbol,
                  timeframe: "1d",
                  adjustment: "raw",
                  bars: [],
                  gaps: [],
                  events: [],
                },
        })),
      },
    }),
    { status: 200 },
  );
}

test("pins raw daily history to every snapshot identity field and rejects mismatched response", async () => {
  const transport = vi.fn((request: Request) => {
    const url = new URL(request.url);
    expect(url.searchParams.get("snapshot_id")).toBe("snap-a");
    expect(url.searchParams.get("dataset_revision")).toBe("data-a");
    expect(url.searchParams.get("adjustment")).toBe("raw");
    expect(url.searchParams.get("to")).toBe(historySnapshot.as_of);
    expect(url.searchParams.get("from")).toBe("2025-10-02T20:00:00.000Z");
    return Promise.resolve(
      response(["US.SPY"], { ...historySnapshot, snapshot_id: "wrong" }),
    );
  });
  vi.stubGlobal("fetch", transport);
  await expect(loadOverviewHistoryFromApi(historySnapshot, "US.SPY")).rejects.toThrow(
    "快照",
  );
});
test("coalesces symbols in batches of 32, caps two requests and isolates unavailable symbols", async () => {
  let running = 0,
    maximum = 0;
  vi.stubGlobal("fetch", async (request: Request) => {
    maximum = Math.max(maximum, ++running);
    await new Promise((resolve) => setTimeout(resolve, 5));
    running--;
    const symbols = new URL(request.url).searchParams.getAll("symbols");
    expect(symbols.length).toBeLessThanOrEqual(32);
    return response(symbols);
  });
  const results = await Promise.allSettled(
    Array.from({ length: 70 }, (_, i) =>
      loadOverviewHistoryFromApi(historySnapshot, i === 0 ? "US.FAIL" : `US.A${i}`),
    ),
  );
  expect(maximum).toBe(2);
  expect(results.filter((r) => r.status === "fulfilled")).toHaveLength(69);
});
test("one optional history failure does not turn the application offline", async () => {
  reportApiConnectivity("online");
  const { result, unmount } = renderHook(() => useConnectivity());
  vi.stubGlobal("fetch", () => Promise.reject(new Error("history timeout")));
  await expect(loadOverviewHistoryFromApi(historySnapshot, "US.SPY")).rejects.toThrow();
  expect(result.current).toBe("online");
  unmount();
});

test("cancels one batch consumer without dropping another and preserves gap flags", async () => {
  let release!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    () =>
      new Promise<Response>((resolve) => {
        release = resolve;
      }),
  );
  const controller = new AbortController();
  const first = loadOverviewHistoryFromApi(historySnapshot, "US.A", controller.signal);
  const second = loadOverviewHistoryFromApi(historySnapshot, "US.B");
  await waitFor(() => expect(release).toBeDefined());
  const rejected = expect(first).rejects.toThrow("cancelled");
  controller.abort(new Error("cancelled"));
  await rejected;
  const batch = (await response([
    "US.A",
    "US.B",
  ]).json()) as components["schemas"]["ResearchResponse_HistoryBatchReadModel_"];
  const history = batch.data.items[1]!.history!;
  history.bars = [
    {
      at: historySnapshot.as_of,
      open: 1,
      high: 1,
      low: 1,
      close: 1,
      volume: 0,
      regime: "range",
      data_gap: false,
    },
  ];
  history.gaps = [
    {
      start_at: historySnapshot.as_of,
      end_at: historySnapshot.as_of,
      reason: "missing",
    },
  ];
  release(new Response(JSON.stringify(batch), { status: 200 }));
  expect((await second).bars[0]?.data_gap).toBe(true);
});
