import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor, act } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, test, vi, beforeEach } from "vitest";
import type { components } from "../../api/generated/schema";
import { loadOverviewHistoryFromApi } from "../../api/overview";
import { useOverviewHistory } from "./useOverviewHistory";

vi.mock("../../api/overview", () => ({
  HISTORY_BATCH_SIZE: 32,
  loadOverviewHistoryFromApi: vi.fn(),
}));
const snapshot: components["schemas"]["ResolvedSnapshot"] = {
  snapshot_id: "a",
  as_of: "2026-10-02T20:00:00Z",
  revision_no: 1,
  dataset_revision: "a",
  feature_set_version: "f",
  universe_version: "u",
  model_version: null,
  calibration_version: null,
  prediction_readiness: "data_only",
  research_readiness: "rules_only",
};
const data: components["schemas"]["InstrumentHistory"] = {
  symbol: "US.A",
  adjustment: "raw",
  timeframe: "1d",
  events: [],
  gaps: [],
  bars: [
    {
      at: snapshot.as_of,
      open: 10,
      high: 11,
      low: 9,
      close: 10,
      volume: 100,
      regime: "range",
      data_gap: false,
    },
  ],
};
beforeEach(() => {
  vi.mocked(loadOverviewHistoryFromApi).mockReset();
});
function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retryDelay: 0, gcTime: Infinity } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}
test("keeps successful histories when one request fails, retries only failures through cache", async () => {
  const load = vi.mocked(loadOverviewHistoryFromApi);
  load.mockImplementation((_, symbol) => {
    if (symbol === "US.FAIL") return Promise.reject(new Error("downstream 500"));
    return Promise.resolve({ ...data, symbol });
  });
  const { result } = renderHook(
    () => useOverviewHistory(snapshot, ["US.A", "US.FAIL"]),
    { wrapper: setup() },
  );
  await waitFor(() => expect(result.current.pending).toBe(false));
  expect(result.current.histories["US.A"]).toHaveLength(1);
  expect(result.current.failed["US.FAIL"]).toBe("downstream 500");
  load.mockImplementation((_, symbol) => Promise.resolve({ ...data, symbol }));
  act(() => result.current.retry());
  await waitFor(() => expect(result.current.completed).toBe(2));
  expect(result.current.failed).toEqual({});
  expect(load.mock.calls.filter(([, s]) => s === "US.A")).toHaveLength(1);
});
test("late responses from the old snapshot cannot appear after snapshot switching", async () => {
  let releaseOld!: (value: typeof data) => void;
  vi.mocked(loadOverviewHistoryFromApi).mockImplementation((s) => {
    return s.snapshot_id === "a"
      ? new Promise((resolve) => {
          releaseOld = resolve;
        })
      : Promise.resolve({ ...data, bars: [{ ...data.bars[0]!, close: 11 }] });
  });
  const { result, rerender } = renderHook(
    ({ selected }) => useOverviewHistory(selected, ["US.A"]),
    { wrapper: setup(), initialProps: { selected: snapshot } },
  );
  await waitFor(() => expect(releaseOld).toBeDefined());
  rerender({ selected: { ...snapshot, snapshot_id: "b", dataset_revision: "b" } });
  await waitFor(() => expect(result.current.histories["US.A"]?.[0]?.close).toBe(11));
  act(() => releaseOld(data));
  expect(result.current.histories["US.A"]?.[0]?.close).toBe(11);
});
