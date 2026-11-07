import { cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, test, vi } from "vitest";
import { useOptionsBoard } from "./useOptionsBoard";
import { useOptionsOverview } from "./useOptionsOverview";

const get = vi.hoisted(() => vi.fn());
vi.mock("../../api/client", () => ({ createResearchApiClient: () => ({ get }) }));
afterEach(cleanup);

test("explicit historical cutoff survives even when stock identity is the latest", async () => {
  const asOf = "2026-10-14T20:00:00Z";
  get.mockImplementation((path: string) =>
    Promise.resolve({
      data:
        path === "/api/v1/snapshot-context"
          ? { resolved_snapshot: { snapshot_id: "stock-current" } }
          : { items: [] },
    }),
  );
  function Views() {
    const parameters = { snapshot_id: "stock-current", as_of: asOf, latest: false };
    useOptionsBoard(parameters);
    useOptionsOverview(parameters);
    return null;
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Views />
    </QueryClientProvider>,
  );
  await waitFor(() => {
    for (const path of ["/api/v1/options/board", "/api/v1/options/overview"]) {
      expect(get).toHaveBeenCalledWith(
        path,
        expect.objectContaining({
          params: { query: { snapshot_id: "stock-current", as_of: asOf } },
        }),
      );
    }
  });
});
