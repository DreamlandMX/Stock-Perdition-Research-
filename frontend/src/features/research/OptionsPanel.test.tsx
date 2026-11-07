import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, cleanup, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "../../api/generated/schema";
import { OptionsPanel } from "./OptionsPanel";
import { OptionStructureMap } from "./OptionStructureMap";
vi.mock("./ResearchOptionPulse", () => ({ ResearchOptionPulse: () => null }));
vi.mock("../options/OptionRefreshControl", () => ({
  OptionRefreshControl: () => null,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const empty: components["schemas"]["OptionResponse"] = {
  request_id: "test",
  generated_at: "2026-10-10T18:00:00Z",
  data_status: "unavailable",
  resolved_snapshot_id: null,
  available_snapshots: [],
  data: {
    symbol: "US.AAPL",
    snapshot: null,
    status: "unavailable",
    metric: "oi",
    side: "call",
    unit: "contracts",
    aggregation: "sum",
    coverage: {
      expected: 0,
      valid: 0,
      missing: 0,
      excluded: 0,
      discovery_complete: false,
    },
    reasons: ["no_option_snapshot"],
    prediction_status: "research_unpublished",
    cells: [],
    strikes: [],
    expirations: [],
    strike_summary: {},
    expiry_summary: {},
    dte_min: 1,
    dte_max: 90,
    strike_min_ratio: 0.7,
    strike_max_ratio: 1.3,
    formula_version: "gamma-scale-1pct-v1",
  },
};

function mount(
  loadData: NonNullable<Parameters<typeof OptionsPanel>[0]["loadData"]>,
  metric?: string,
) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter
        initialEntries={[metric ? `/?option_metric=${metric}` : "/?option_view=legacy"]}
      >
        <OptionsPanel
          symbol="US.AAPL"
          loadData={loadData}
          loadAnalytics={() =>
            Promise.resolve({
              request_id: "test",
              generated_at: "2026-10-10T18:00:00Z",
              reasons: ["no_option_snapshot"],
              data: null,
            })
          }
          loadForecast={() =>
            Promise.resolve({
              request_id: "test",
              generated_at: "2026-10-10T18:00:00Z",
              data: {
                symbol: "US.AAPL",
                status: "refused",
                model_source: null,
                model_version: null,
                reasons: ["baseline_unavailable"],
                candidate: { status: "unavailable", version: null, promoted: false },
                option_snapshot_id: null,
                option_cutoff: null,
                stock_snapshot_id: null,
                stock_cutoff: null,
                horizons: [],
              },
            })
          }
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("option structure panel", () => {
  it("reads the dual map in descending strike order and keeps filters in the URL", async () => {
    const absent = {
      contracts: 0,
      exposure_valid: 0,
      turnover_valid: 0,
      exposure_excluded: 0,
      turnover_excluded: 0,
      reasons: [],
    };
    const load = vi.fn(() =>
      Promise.resolve({
        request_id: "map",
        generated_at: empty.generated_at,
        data_status: "partial",
        resolved_snapshot_id: "saved",
        data: {
          symbol: "US.AAPL",
          status: "partial" as const,
          call_walls: [],
          put_walls: [],
          turnover_peaks: [],
          windows: {},
          reasons: [],
          total: { call: absent, put: absent },
          strike_summary: {
            "240.0": { call: { ...absent, gex: 0, vex: 0, turnover: 0 }, put: absent },
          },
          expiry_summary: {},
          formula_version: "bs-r0-q0-act365-gex1pct-vex1pp-v1",
          exposure_coverage: empty.data.coverage,
          turnover_coverage: empty.data.coverage,
          strikes: [240, 260],
          expirations: ["2026-11-06"],
          cells: [
            {
              strike: 240,
              expiration: "2026-11-06",
              call: {
                ...absent,
                contracts: 1,
                exposure_valid: 1,
                turnover_valid: 1,
                gex: 0,
                vex: 0,
                turnover: 0,
              },
              put: absent,
              contract_ids: [],
            },
            {
              strike: 260,
              expiration: "2026-11-06",
              call: absent,
              put: { ...absent, contracts: 1 },
              contract_ids: [],
            },
          ],
        },
      }),
    );
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <OptionStructureMap symbol="US.AAPL" query={{}} loadData={load} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const table = await screen.findByRole("table", { name: "GEX 与 VEX 双行热力图" });
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("$260");
    expect(rows[2]).toHaveTextContent("$240");
    expect(within(rows[1]!).getByRole("button")).toHaveTextContent("缺失");
    expect(within(rows[2]!).getByRole("button")).toHaveTextContent("$0.0");
    expect(within(rows[2]!).getAllByRole("cell").at(-1)).toHaveTextContent("G $0.0");
    await userEvent.click(screen.getByRole("button", { name: "Call" }));
    expect(within(rows[1]!).queryByRole("button")).not.toBeInTheDocument();
    expect(within(rows[1]!).getAllByRole("cell")[0]).toHaveTextContent("—");
    await userEvent.click(screen.getByRole("button", { name: "全部" }));
    await userEvent.click(screen.getAllByRole("button", { name: "近5天" })[0]!);
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(
        "US.AAPL",
        expect.objectContaining({ dte_max: 5 }),
      ),
    );
    await userEvent.click(screen.getByRole("button", { name: "成交额" }));
    expect(
      await screen.findByRole("table", { name: "供应商报告成交额热力图" }),
    ).toBeVisible();
  });
  it("shows no-data without zero heatmap and allows keyboard-side selection", async () => {
    const load = vi.fn(() => Promise.resolve(empty));
    mount(load);
    expect(await screen.findByText("尚无已保存期权快照")).toBeVisible();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("合约侧"), "put");
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(
        "US.AAPL",
        expect.objectContaining({ side: "put" }),
      ),
    );
    expect(screen.getByText(/期权增强预测：候选研究与发布状态分别记录/)).toBeVisible();
  });
  it("shows a clear API error", async () => {
    mount(() => Promise.reject(new Error("范围超出已保存快照")));
    expect(await screen.findByRole("alert")).toHaveTextContent("范围超出已保存快照");
  });
  it("shows the actual dollar marker, numeric scale, complete attributes and retained-source notice", async () => {
    const at = "2026-10-10T17:58:00Z";
    const contract = {
      contract_id: "saved-call",
      underlying: "US.AAPL",
      side: "call" as const,
      strike: 250,
      expiration: "2026-11-06",
      multiplier: 100,
      standard: true,
      exercise: "American",
      settlement: "physical",
      bid: 1.2,
      ask: 1.4,
      volume: 0,
      open_interest: 12,
      oi_date: "2026-10-09",
      iv: 0.2,
      delta: 0.5,
      delta_units: "signed_unit_v1" as const,
      greek_units: "unknown" as const,
      observed_at: at,
      quote_at: at,
      source_available_at: at,
      ingested_at: at,
      underlying_at: at,
      underlying_price: 251,
      quality_flags: [],
    };
    const snapshot = {
      snapshot_id: "retained-complete",
      raw_batch_id: "raw-saved",
      symbol: "US.AAPL",
      provider: "futu",
      feed: "saved-feed",
      sdk_version: "verified",
      opend_version: "verified",
      normalization_version: "options-v2",
      dataset_revision: "saved-v2",
      captured_at: at,
      source_available_at: at,
      underlying_at: at,
      underlying_price: 251,
      market_as_of: at,
      contracts: [contract],
      expected_contract_ids: [contract.contract_id],
      discovery_complete: true,
      complete: true,
      errors: [],
      time_evidence: "forward_only" as const,
      availability_evidence: [],
      dte_min: 1,
      dte_max: 90,
      strike_min_ratio: 0.7,
      strike_max_ratio: 1.3,
    };
    const response: components["schemas"]["OptionResponse"] = {
      ...empty,
      data_status: "partial",
      resolved_snapshot_id: snapshot.snapshot_id,
      data: {
        ...empty.data,
        snapshot,
        status: "partial",
        reasons: [],
        latest_attempt: {
          ...snapshot,
          snapshot_id: "partial-attempt",
          captured_at: "2026-10-10T18:00:00Z",
          complete: false,
          errors: ["partial_response"],
        },
        cells: [
          {
            strike: 240,
            expiration: "2026-11-06",
            value: 0,
            valid_count: 1,
            missing_count: 0,
            excluded_count: 0,
            contract_ids: [],
          },
          {
            strike: 250,
            expiration: "2026-11-06",
            value: 12,
            valid_count: 1,
            missing_count: 0,
            excluded_count: 0,
            contract_ids: [contract.contract_id],
          },
          {
            strike: 260,
            expiration: "2026-11-06",
            value: null,
            valid_count: 0,
            missing_count: 1,
            excluded_count: 0,
            contract_ids: [],
          },
        ],
        strikes: [240, 250, 260],
        expirations: ["2026-11-06"],
        coverage: {
          expected: 1,
          valid: 1,
          missing: 0,
          excluded: 0,
          discovery_complete: true,
        },
        total: 12,
        strike_summary: { "240.0": 0, "250.0": 12, "260.0": null },
        expiry_summary: { "2026-11-06": 12 },
      },
    };
    mount(
      (_symbol, query) =>
        Promise.resolve({
          ...response,
          data: {
            ...response.data,
            metric: query.metric ?? "oi",
            unit: query.metric === "premium" ? "USD" : "contracts",
            cells: response.data.cells.map((cell) => ({
              ...cell,
              value:
                cell.value == null || cell.value === 0
                  ? cell.value
                  : query.metric === "premium"
                    ? 1250000
                    : 12,
            })),
            total: query.metric === "premium" ? 1250000 : 12,
          },
        }),
      "oi",
    );
    expect(await screen.findByText(/色阶 0–12 张/)).toBeVisible();
    const heat = screen.getByRole("table", { name: "到期日 × 行权价数字热力表" });
    expect(heat).toHaveTextContent("12");
    expect(
      within(heat).getByRole("row", { name: /\$250/ }).lastElementChild,
    ).toHaveTextContent("12");
    expect(
      within(heat).getByRole("button", { name: /行权价 \$240.*：0 张/ }),
    ).toHaveTextContent("0");
    expect(
      within(heat).getByRole("button", { name: /行权价 \$260.*：缺失/ }),
    ).toHaveTextContent("缺失");
    expect(screen.getByText("现价 $251.00")).toBeVisible();
    expect(screen.getByText(/最新采集未完成，保留旧完整快照/)).toBeVisible();
    await userEvent.click(screen.getByText("更多字段与质量证据"));
    expect(screen.getByText(/美式行权\s*\/\s*实物交割/)).toBeVisible();
    expect(screen.getAllByText("1.2 / 1.4")).toHaveLength(2);
    expect(screen.getByText(/US.AAPL\s*\/\s*call/)).toBeVisible();
    expect(screen.getByText("saved-call")).toBeInTheDocument();
    expect(screen.getByText("当日累计成交量").nextElementSibling).toHaveTextContent(
      "0",
    );
    await userEvent.selectOptions(screen.getByLabelText("指标"), "premium");
    expect(
      await screen.findByRole("button", {
        name: /行权价 \$250.*估算成交额.*\$1,250,000/,
      }),
    ).toHaveTextContent("$1.3M");
    expect(screen.getByText(/估算成交额 = 成交量/)).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: /行权价 \$250.*估算成交额.*\$1,250,000/ }),
    );
    expect(screen.getByText(/行权价 \$250.*估算成交额：\$1,250,000/)).toBeVisible();
  });
});
