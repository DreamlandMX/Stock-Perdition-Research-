import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ResearchOptionPulse } from "./ResearchOptionPulse";

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}));
vi.mock("../../api/client", async (loadOriginal) => ({
  ...(await loadOriginal<typeof import("../../api/client")>()),
  createResearchApiClient: () => api,
}));
vi.mock("./OptionPulseChart", () => ({ OptionPulseChart: () => <p>脉搏图表</p> }));

beforeEach(() => {
  api.get.mockResolvedValue({
    data: {
      symbol: "US.META",
      mode: "daily",
      timeframe: "1d",
      generated_at: "2026-10-13T15:00:00Z",
      spot: null,
      bars: [],
      profile: [],
      cone: [],
      reasons: [],
      collection_state: "inactive",
    },
  });
  api.post.mockResolvedValue({ data: { session_id: "session-1" } });
  api.put.mockResolvedValue({ data: { session_id: "session-1" } });
  api.del.mockResolvedValue({});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[
          "/research/US.META?snapshot_id=saved&as_of=2026-10-12T20:00:00Z&pulse_mode=daily",
        ]}
      >
        <ResearchOptionPulse symbol="US.META" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("historical reads stay pinned; latest option reads omit the stock cutoff without collecting", async () => {
  const user = userEvent.setup();
  const view = mount();
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  const dailyRequest = api.get.mock.calls[0]?.[1] as { params: { query: unknown } };
  expect(dailyRequest.params.query).toMatchObject({
    mode: "daily",
    snapshot_id: "saved",
    as_of: "2026-10-12T20:00:00Z",
  });
  await user.click(screen.getByRole("button", { name: "最新期权快照" }));
  await waitFor(() =>
    expect(api.get).toHaveBeenLastCalledWith(
      expect.any(String),
      expect.objectContaining({
        params: {
          path: { symbol: "US.META" },
          query: { mode: "latest", snapshot_id: "saved" },
        },
      }),
    ),
  );
  expect(api.post).not.toHaveBeenCalled();
  view.unmount();
  expect(api.del).not.toHaveBeenCalled();
});
test("leaving latest view never starts or cancels an option collection", async () => {
  const user = userEvent.setup();
  const view = mount();
  await user.click(screen.getByRole("button", { name: "最新期权快照" }));
  await user.click(screen.getByRole("button", { name: "历史快照" }));
  view.unmount();
  expect(api.post).not.toHaveBeenCalled();
  expect(api.put).not.toHaveBeenCalled();
  expect(api.del).not.toHaveBeenCalled();
});
