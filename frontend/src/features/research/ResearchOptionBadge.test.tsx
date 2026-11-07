import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { useOptionsBoard } from "../options/useOptionsBoard";
import { useOptionsOverview } from "../options/useOptionsOverview";
import { ResearchOptionBadge } from "./ResearchOptionBadge";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test.each([
  ["saved", false],
  ["newer", true],
] as const)(
  "shares identity before option requests and preserves historical cutoff (latest %s)",
  async (latestId, hasCutoff) => {
    const requests: URL[] = [];
    let resolveContext!: (response: Response) => void;
    const asOf = "2026-10-09T20:00:00Z";
    vi.stubGlobal("fetch", (request: Request) => {
      const url = new URL(request.url);
      requests.push(url);
      if (url.pathname === "/api/v1/snapshot-context")
        return new Promise<Response>((resolve) => {
          resolveContext = resolve;
        });
      return Promise.resolve(
        new Response(
          JSON.stringify(
            url.pathname.endsWith("/header")
              ? {
                  data: {
                    symbol: "US.SPY",
                    snapshot_id: "option-a",
                    market_as_of: asOf,
                    captured_at: asOf,
                    complete: true,
                    contract_count: 12,
                    reasons: [],
                  },
                }
              : { items: [] },
          ),
          { status: 200 },
        ),
      );
    });
    function Probe() {
      const query = { snapshot_id: "saved", as_of: asOf };
      useOptionsBoard(query);
      useOptionsOverview(query);
      return <ResearchOptionBadge symbol="US.SPY" />;
    }
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter
          initialEntries={[
            `/research/US.SPY?snapshot_id=saved&as_of=${encodeURIComponent(asOf)}&option_snapshot=option-a`,
          ]}
        >
          <Probe />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(resolveContext).toBeDefined());
    expect(requests).toHaveLength(1);
    resolveContext(
      new Response(
        JSON.stringify({
          resolved_snapshot: { snapshot_id: latestId, as_of: asOf },
          data: { symbol_count: 204, instruments: [] },
        }),
        { status: 200 },
      ),
    );
    expect(await screen.findByRole("button", { name: /期权行情.*完整/ })).toBeVisible();
    await waitFor(() => expect(requests).toHaveLength(4));
    expect(
      requests.filter((url) => url.pathname.endsWith("/snapshot-context")),
    ).toHaveLength(1);
    expect(requests.some((url) => url.pathname.endsWith("/heatmap"))).toBe(false);
    expect(
      requests.slice(1).every((url) => url.searchParams.has("as_of") === hasCutoff),
    ).toBe(true);
    expect(
      requests
        .find((url) => url.pathname.endsWith("/header"))
        ?.searchParams.get("snapshot_id"),
    ).toBe("option-a");
  },
);
