import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { loadSnapshotContextFromApi } from "../../api/snapshot-context";
import { useOverviewHistory } from "../overview/useOverviewHistory";
import {
  buildMarketResearchHref,
  type MarketLinkState,
} from "../overview/overview-terminal";
import { useOptionsOverview } from "./useOptionsOverview";
import { useOptionsBoard } from "./useOptionsBoard";
import { OptionsPage } from "./OptionsPage";

vi.mock("../../api/snapshot-context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/snapshot-context")>();
  const load = vi.fn<typeof actual.loadSnapshotContextFromApi>();
  return {
    ...actual,
    loadSnapshotContextFromApi: load,
    snapshotContextOptions: (query = {}) => ({
      ...actual.snapshotContextOptions(query),
      queryFn: () => load(query),
    }),
  };
});
vi.mock("./useOptionsOverview", () => ({ useOptionsOverview: vi.fn() }));
vi.mock("./useOptionsBoard", () => ({ useOptionsBoard: vi.fn() }));
vi.mock("./OptionRefreshControl", () => ({
  OptionRefreshControl: () => <button>更新全部期权</button>,
}));
vi.mock("../overview/useOverviewHistory", () => ({ useOverviewHistory: vi.fn() }));
vi.mock("../overview/OptionsBoard", () => ({
  OptionsBoard: ({ linkState }: { linkState: MarketLinkState }) => (
    <Link to={buildMarketResearchHref("US.SPY", linkState)}>打开 SPY 研究</Link>
  ),
}));
vi.mock("../health/OptionCollectionMatrix", () => ({
  OptionCollectionMatrix: () => <p>采集矩阵</p>,
}));
vi.mock("../overview/OptionsDivergenceScatter", () => ({
  OptionsDivergenceScatter: ({
    horizon,
    onHorizonChange,
  }: {
    horizon: number;
    onHorizonChange: (h: 20) => void;
  }) => <button onClick={() => onHorizonChange(20)}>对比区间 {horizon}</button>,
}));

const snapshot = {
  snapshot_id: "saved",
  as_of: "2026-10-09T20:00:00Z",
  revision_no: 2,
  dataset_revision: "bars-r2",
  feature_set_version: "features-v7",
  universe_version: "us-204",
  model_version: null,
  calibration_version: null,
  prediction_readiness: "data_only" as const,
  research_readiness: "rules_only" as const,
};
const response = {
  resolved_snapshot: snapshot,
  data: { symbol_count: 204, instruments: [] },
};
const refetch = vi.fn();
const historyRetry = vi.fn();

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
    </output>
  );
}

function mount(
  path = "/options?snapshot_id=saved&as_of=2026-10-09T20%3A00%3A00Z&options_horizon=5",
) {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter initialEntries={[path]}>
        <OptionsPage />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(loadSnapshotContextFromApi).mockResolvedValue(
    response as unknown as Awaited<ReturnType<typeof loadSnapshotContextFromApi>>,
  );
  vi.mocked(useOptionsOverview).mockReturnValue({
    data: { items: [] },
    isPending: false,
    isError: false,
    refetch,
  } as unknown as ReturnType<typeof useOptionsOverview>);
  vi.mocked(useOptionsBoard).mockReturnValue({
    data: { items: [{ symbol: "US.SPY" }, { symbol: "US.TSM" }] },
    isPending: false,
    isError: false,
    refetch,
  } as unknown as ReturnType<typeof useOptionsBoard>);
  vi.mocked(useOverviewHistory).mockReturnValue({
    histories: {},
    failed: { "US.SPY": "日线不可用" },
    completed: 9,
    total: 9,
    pending: false,
    retry: historyRetry,
  });
});
afterEach(cleanup);

test("historical option selection stays pinned when opening research", async () => {
  const user = userEvent.setup();
  mount();
  await screen.findByRole("link", { name: "打开 SPY 研究" });
  await user.click(screen.getByRole("button", { name: "历史期权快照" }));
  expect(useOptionsBoard).toHaveBeenLastCalledWith(
    { snapshot_id: "saved", as_of: snapshot.as_of, latest: false },
    true,
  );
  expect(screen.getByRole("link", { name: "打开 SPY 研究" })).toHaveAttribute(
    "href",
    expect.stringContaining("pulse_mode=daily"),
  );
});

test("pins the saved snapshot, loads all supported histories and preserves it through horizon and research navigation", async () => {
  const user = userEvent.setup();
  mount();
  const research = await screen.findByRole("link", { name: "打开 SPY 研究" });
  expect(loadSnapshotContextFromApi).toHaveBeenCalledWith({
    snapshot_id: "saved",
    as_of: snapshot.as_of,
  });
  expect(useOptionsBoard).toHaveBeenLastCalledWith(
    { snapshot_id: "saved", as_of: snapshot.as_of, latest: true },
    true,
  );
  expect(useOverviewHistory).toHaveBeenLastCalledWith(snapshot, ["US.SPY", "US.TSM"]);
  await user.click(screen.getByRole("button", { name: "重试失败日线（1）" }));
  expect(historyRetry).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", { name: "对比区间 5" }));
  expect(screen.getByTestId("location")).toHaveTextContent("options_horizon=20");
  expect(screen.getByTestId("location")).toHaveTextContent("revision_no=2");
  expect(research).toHaveAttribute(
    "href",
    expect.stringContaining("dataset_revision=bars-r2"),
  );
  await user.click(research);
  expect(screen.getByTestId("location")).toHaveTextContent("/research/US.SPY?");
  expect(screen.getByTestId("location")).toHaveTextContent("snapshot_id=saved");
});

test("a missing historical snapshot stays pinned and can retry without falling back to latest", async () => {
  const user = userEvent.setup();
  vi.mocked(loadSnapshotContextFromApi).mockRejectedValueOnce(
    new Error("历史快照不可用"),
  );
  mount("/options?snapshot_id=missing");
  expect(await screen.findByRole("alert")).toHaveTextContent("历史快照不可用");
  expect(screen.queryByRole("link", { name: "打开 SPY 研究" })).not.toBeInTheDocument();
  expect(useOptionsBoard).toHaveBeenLastCalledWith(
    { snapshot_id: undefined, as_of: undefined, latest: true },
    false,
  );
  await user.click(screen.getByRole("button", { name: "重试快照" }));
  await waitFor(() =>
    expect(loadSnapshotContextFromApi).toHaveBeenCalledWith({ snapshot_id: "missing" }),
  );
  expect(loadSnapshotContextFromApi).toHaveBeenCalledWith({
    snapshot_id: "missing",
  });
});

test("option read errors retry both sources and retain the chosen horizon", async () => {
  const user = userEvent.setup();
  vi.mocked(useOptionsBoard).mockReturnValue({
    isPending: false,
    isError: true,
    error: new Error("期权读取超时"),
    refetch,
  } as unknown as ReturnType<typeof useOptionsBoard>);
  mount("/options?options_horizon=20");
  expect(await screen.findByRole("alert")).toHaveTextContent("期权读取超时");
  await user.click(screen.getByRole("button", { name: "重新读取期权" }));
  expect(refetch).toHaveBeenCalledTimes(2);
  expect(screen.getByTestId("location")).toHaveTextContent("options_horizon=20");
});

test("shows board results before overview arrives, and overview comparison before board arrives", async () => {
  vi.mocked(useOptionsOverview).mockReturnValue({
    isPending: true,
    isError: false,
    refetch,
  } as unknown as ReturnType<typeof useOptionsOverview>);
  mount();
  expect(await screen.findByRole("link", { name: "打开 SPY 研究" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "对比区间 5" })).not.toBeInTheDocument();
  cleanup();
  vi.mocked(useOptionsOverview).mockReturnValue({
    data: { items: [] },
    isPending: false,
    isError: false,
    refetch,
  } as unknown as ReturnType<typeof useOptionsOverview>);
  vi.mocked(useOptionsBoard).mockReturnValue({
    isPending: true,
    isError: false,
    refetch,
  } as unknown as ReturnType<typeof useOptionsBoard>);
  mount();
  expect(await screen.findByRole("button", { name: "对比区间 5" })).toBeVisible();
  expect(screen.queryByRole("link", { name: "打开 SPY 研究" })).not.toBeInTheDocument();
});

test("waits for the core board before starting the heavy overview read", async () => {
  vi.mocked(useOptionsBoard).mockReturnValue({
    isPending: true,
    isError: false,
    refetch,
  } as unknown as ReturnType<typeof useOptionsBoard>);
  mount();
  await waitFor(() =>
    expect(useOptionsOverview).toHaveBeenLastCalledWith(
      { snapshot_id: "saved", as_of: snapshot.as_of, latest: true },
      false,
    ),
  );
});
