import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { buildOptionsBoardRows } from "../options/options-board-rows";
import type { OptionBoardItem } from "../options/useOptionsBoard";
import type { OverviewHistory } from "./overview-history";
import type { MarketLinkState } from "./overview-terminal";
import { OptionResearchCard } from "./OptionResearchCard";

const chart = vi.hoisted(() => ({ build: vi.fn(() => ({})) }));
vi.mock("../research/option-pulse-chart", () => ({
  buildOptionPulseOption: chart.build,
}));
vi.mock("./OverviewTerminalChart", () => ({
  OverviewTerminalChart: ({
    title,
    children,
  }: {
    title: string;
    children: ReactNode;
  }) => (
    <div>
      {title}
      {children}
    </div>
  ),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const state: MarketLinkState = {
  asOf: "2026-10-30T20:00:00Z",
  snapshotId: "saved",
  horizon: 5,
  calibrationVersion: undefined,
  datasetRevision: undefined,
  featureSetVersion: undefined,
  modelVersion: undefined,
  universeVersion: undefined,
};
const bars = [
  {
    at: "2026-10-29T04:00:00Z",
    open: 100,
    close: 101,
    low: 99,
    high: 102,
    data_gap: false,
  },
  {
    at: "2026-10-30T04:00:00Z",
    open: 101,
    close: 102,
    low: 100,
    high: 103,
    data_gap: false,
  },
  {
    at: "2026-11-02T05:00:00Z",
    open: 102,
    close: 999,
    low: 100,
    high: 1000,
    data_gap: false,
  },
];
test("saved stock candles remain visible without an option snapshot and respect the stock cutoff", () => {
  const row = buildOptionsBoardRows(
    [
      {
        symbol: "US.DIA",
        data_status: "unavailable",
        snapshot_id: null,
        reasons: ["option_snapshot_unavailable"],
        strike_profile: [],
      } as unknown as OptionBoardItem,
    ],
    [],
  )[0]!;
  render(
    <MemoryRouter>
      <OptionResearchCard
        row={row}
        history={{ histories: { "US.DIA": bars } } as unknown as OverviewHistory}
        point={undefined}
        linkState={state}
      />
    </MemoryRouter>,
  );
  expect(screen.getByTitle("股票快照收盘价")).toHaveTextContent("102.00");
  expect(screen.getByText("2026-10-30")).toBeVisible();
  expect(screen.queryByText("2026-11-02")).not.toBeInTheDocument();
  expect(chart.build).toHaveBeenCalledWith(
    expect.objectContaining({ bars: bars.slice(0, 2), cone: [] }),
  );
});

test.each([
  ["option_snapshot_unavailable", "待采集"],
  ["permission_denied", "权限不足"],
  ["no_option_contracts", "无可用合约"],
  ["collection_timeout", "采集失败"],
])("missing option state %s has a specific label", (reason, label) => {
  const row = buildOptionsBoardRows(
    [
      {
        symbol: "US.DIA",
        data_status: "unavailable",
        reasons: [reason],
      } as unknown as OptionBoardItem,
    ],
    [],
  )[0]!;
  expect(row.look.t).toBe(label);
});

test("a saved partial snapshot explains unavailable metrics on its card", () => {
  const row = buildOptionsBoardRows(
    [
      {
        symbol: "US.MFG",
        data_status: "partial",
        snapshot_id: "mfg-saved",
        spot: 2,
        market_date: "2026-10-30",
        reasons: ["atm_iv_30d_unavailable"],
        strike_profile: [],
        expiry_buckets_volume: [0, 0, 0, 0],
      } as unknown as OptionBoardItem,
    ],
    [],
  )[0]!;
  render(
    <MemoryRouter>
      <OptionResearchCard
        row={row}
        history={{ histories: {} } as OverviewHistory}
        point={undefined}
        linkState={state}
      />
    </MemoryRouter>,
  );
  expect(screen.getByText("30 天 ATM IV 不可用")).toBeVisible();
});
