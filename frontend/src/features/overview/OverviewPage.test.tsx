import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  createMemoryRouter,
  MemoryRouter,
  RouterProvider,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { components, paths } from "../../api/generated/schema";
import { APP_ROUTES } from "../../app/router";
import { OverviewPage } from "./OverviewPage";

vi.mock("./useOverviewHistory", () => {
  const empty = {
    histories: {},
    failed: {},
    completed: 0,
    total: 0,
    pending: false,
    retry: vi.fn(),
  };
  return { useOverviewHistory: () => empty };
});

const chartHarness = vi.hoisted(() => {
  const fitContent = vi.fn();
  const remove = vi.fn();
  const resize = vi.fn();
  const setData = vi.fn();
  const addSeries = vi.fn(() => ({ setData }));
  const createChart = vi.fn(() => ({
    addSeries,
    remove,
    resize,
    timeScale: () => ({ fitContent }),
  }));
  return { addSeries, createChart, fitContent, remove, resize, setData };
});

const resizeObserverHarness = {
  observers: [] as {
    callback: ResizeObserverCallback;
    targets: Element[];
    disconnect: ReturnType<typeof vi.fn>;
  }[],
  disconnect: vi.fn(),
  observe: vi.fn(),
  unobserve: vi.fn(),
};

vi.mock("lightweight-charts", () => ({
  ColorType: { Solid: "solid" },
  createChart: chartHarness.createChart,
  LineSeries: Symbol("LineSeries"),
}));

type OverviewResponse = components["schemas"]["ResearchResponse_OverviewReadModel_"];
type OverviewQuery = NonNullable<
  paths["/api/v1/overview"]["get"]["parameters"]["query"]
>;
type OverviewLoader = (query: OverviewQuery) => Promise<OverviewResponse>;

const SNAPSHOT = {
  as_of: "2026-07-10T20:05:00Z",
  calibration_version: "CAL-2026-07-08",
  dataset_revision: "bars-r42",
  feature_set_version: "features-v7",
  model_version: "SL-2.6.1",
  prediction_readiness: "forecast_ready",
  research_readiness: "full_verified",
  revision_no: 3,
  snapshot_id: "SL-20260710-1605",
  universe_version: "us-100-2026q3",
} satisfies components["schemas"]["ResolvedSnapshot"];

const DATA_ONLY_SNAPSHOT = {
  ...SNAPSHOT,
  calibration_version: null,
  model_version: null,
  prediction_readiness: "data_only",
  research_readiness: "rules_only",
  snapshot_id: "LIVE-20260710-1605",
} satisfies components["schemas"]["ResolvedSnapshot"];

const EXECUTABLE_ROW = {
  abstain_reasons: [],
  as_of: SNAPSHOT.as_of,
  asset_type: "stock",
  breakout_down_probability: 0.15,
  breakout_up_probability: 0.7,
  capital_gain_q10: -0.1,
  capital_gain_q50: 0.07,
  confidence_level: "medium",
  data_freshness_seconds: 18,
  effective_abstain: false,
  executable_q10: -0.112,
  executable_q50: 0.064,
  executable_up_probability: 0.72,
  forecast_up_probability: 0.76,
  horizon: 5,
  interval_width: 0.428,
  model_version: SNAPSHOT.model_version,
  name: "NVIDIA",
  regime: "uptrend",
  sector: "technology",
  serving_status: "live",
  similar_case_count: 28,
  symbol: "US.NVDA",
} satisfies components["schemas"]["ScreenRow"];

const ABSTAIN_ROW = {
  ...EXECUTABLE_ROW,
  abstain_reasons: ["interval_too_wide"],
  breakout_down_probability: null,
  breakout_up_probability: null,
  capital_gain_q10: null,
  capital_gain_q50: null,
  confidence_level: "unavailable",
  effective_abstain: true,
  executable_q10: null,
  executable_q50: null,
  executable_up_probability: null,
  forecast_up_probability: null,
  interval_width: null,
  name: "Broadcom",
  serving_status: "partial",
  symbol: "US.AVGO",
} satisfies components["schemas"]["ScreenRow"];

function verifiedEvidence(
  horizon: 1 | 5 | 20,
  breakoutUp: number,
  breakoutDown: number,
) {
  const priceBands = {
    1: { q10: 732.61, q25: 736, q50: 739.91, q75: 744, q90: 747.46 },
    5: { q10: 721.48, q25: 732, q50: 740.82, q75: 752, q90: 764.14 },
    20: { q10: 698.73, q25: 724, q50: 746.12, q75: 765, q90: 785.17 },
  } as const;
  return {
    as_of: SNAPSHOT.as_of,
    horizon,
    price_projection: {
      currency: "USD",
      endpoint_price_band: priceBands[horizon],
      endpoint_price_unavailable_reason: null,
      reference_price: 740.86,
      reference_price_at: SNAPSHOT.as_of,
      stage_high_price_q50: horizon === 20 ? 792.4 : 770.2,
      stage_high_price_unavailable_reason: null,
      stage_low_price_q50: horizon === 20 ? null : 711.2,
      stage_low_price_unavailable_reason:
        horizon === 20 ? "mae_model_not_verified" : null,
    },
    sample_abstain_reasons: [],
    scope: "verified",
    symbol: "US.SPY",
    tasks: [
      {
        baseline_wins: 4,
        calibration_version: SNAPSHOT.calibration_version,
        evaluation_run_id: `eval-${horizon}`,
        fold_count: 4,
        gate_passed: true,
        horizon,
        model_version: `breakout-v${horizon}`,
        scope: "verified",
        task: "breakout",
      },
    ],
    values: {
      breakout_down_probability: breakoutDown,
      breakout_up_probability: breakoutUp,
      capital_gain_q10: -0.054,
      capital_gain_q50: 0.001,
      mae_q50: -0.03,
      mfe_q50: 0.03,
    },
  } satisfies components["schemas"]["ResearchModelEvidence"];
}

const SPY_RESEARCH_OPPORTUNITY = {
  as_of: SNAPSHOT.as_of,
  asset_type: "etf",
  challenger_preview: null,
  challenger_preview_by_horizon: [],
  horizon: 5,
  name: "SPDR S&P 500 ETF",
  rank_basis: ["5日研究证据排名"],
  regime: "range",
  rule_evidence: {
    atr_pct_20: 0.02,
    breakout_down_rule: false,
    breakout_up_rule: false,
    channel_position_20: 0.62,
    channel_width_20: 0.05,
    intraday_available: true,
    last_close: 738.93,
    return_20: 0.03,
    return_5: 0.004,
    session_return: 0.001,
    volume_z_20: 0.1,
  },
  sector: "broad_market",
  symbol: "US.SPY",
  verified_research: verifiedEvidence(5, 0.61, 0.35),
  verified_research_by_horizon: [
    verifiedEvidence(5, 0.61, 0.35),
    verifiedEvidence(20, 0.477192, 0.471729),
  ],
} satisfies components["schemas"]["ResearchOpportunityRow"];

const MARKET_SECTORS = [
  "technology",
  "consumer",
  "healthcare",
  "financials",
  "industrial",
];
const MARKET_POINTS = Array.from({ length: 100 }, (_, index) => {
  const symbol = index === 0 ? "US.SPY" : `US.T${String(index).padStart(2, "0")}`;
  const sessionReturn = (index - 50) / 1000;
  return {
    asset_type: index === 0 ? "etf" : "stock",
    atr_pct_20: 0.012 + index / 5000,
    breakout_down_rule: index === 1,
    breakout_up_rule: index < 6,
    channel_position_20: index / 99,
    channel_width_20: 0.08,
    intraday_available: true,
    last_close: 100 + index,
    name: index === 0 ? "SPDR S&P 500 ETF" : `Test ${index}`,
    previous_regime: index < 10 ? "range" : "uptrend",
    regime: index < 55 ? "uptrend" : index < 75 ? "range" : "downtrend",
    regime_changed: index < 10,
    return_20: sessionReturn * 4,
    return_5: sessionReturn * 2,
    sector: MARKET_SECTORS[index % MARKET_SECTORS.length] ?? "technology",
    session_return: sessionReturn,
    symbol,
    volume_z_20: index < 4 ? 2.2 + index / 10 : 0.2,
  } satisfies components["schemas"]["OverviewMarketPoint"];
});

const SECTOR_SCORECARDS = MARKET_SECTORS.map((sector, index) => ({
  breakout_down_count: index === 0 ? 1 : 0,
  breakout_up_count: index === 0 ? 5 : 0,
  laggard_symbol:
    MARKET_POINTS.filter((point) => point.sector === sector).at(0)?.symbol ?? null,
  leader_symbol:
    MARKET_POINTS.filter((point) => point.sector === sector).at(-1)?.symbol ?? null,
  median_atr_pct_20: 0.02,
  median_return_20: 0.05 - index * 0.02,
  median_return_5: 0.02 - index * 0.008,
  median_session_return: 0.01 - index * 0.004,
  previous_sample_count: 20,
  previous_uptrend_fraction: 0.45,
  sample_count: 20,
  sector,
  uptrend_change: 0.1 - index * 0.05,
  uptrend_fraction: 0.55 - index * 0.05,
  volume_anomaly_count: index === 0 ? 4 : 0,
})) satisfies components["schemas"]["SectorScorecard"][];

const INTERNAL_EVIDENCE = {
  advancers: 66,
  breakout_down: 0,
  breakout_up: 6,
  challenger_candidate_churn: { changes: [] },
  comparison_snapshot: {
    as_of: "2026-07-09T20:00:00Z",
    generated_at: "2026-07-09T20:03:00Z",
    revision_no: 2,
    snapshot_id: "SL-20260709-1600-r2",
  },
  current_anomaly_snapshot: {
    breakout_count: 6,
    lower_channel_count: 1,
    regime_change_count: 26,
    snapshot_id: SNAPSHOT.snapshot_id,
    total_symbols: 100,
    upper_channel_count: 1,
    volume_anomalies: 4,
  },
  decliners: 33,
  evidence_coverage: {
    atr_count: 100,
    channel_count: 100,
    comparable_symbols: 100,
    intraday_available_count: 100,
    return_20_count: 100,
    return_5_count: 100,
    rule_evidence_count: 100,
    session_return_count: 100,
    total_symbols: 100,
    volume_z_count: 100,
  },
  history: [
    {
      as_of: "2026-07-09T20:00:00Z",
      market_breadth: 0.54,
      median_session_return: -0.001,
      revision_no: 2,
      snapshot_id: "SL-20260709-1600-r2",
      uptrend_fraction: 0.49,
      volume_anomaly_count: 2,
    },
    {
      as_of: SNAPSHOT.as_of,
      market_breadth: 0.58,
      median_session_return: 0.005,
      revision_no: SNAPSHOT.revision_no,
      snapshot_id: SNAPSHOT.snapshot_id,
      uptrend_fraction: 0.55,
      volume_anomaly_count: 4,
    },
  ],
  market_points: MARKET_POINTS,
  median_session_return: 0.005,
  previous_anomaly_snapshot: {
    breakout_count: 4,
    lower_channel_count: 2,
    regime_change_count: 18,
    snapshot_id: "SL-20260709-1600-r2",
    total_symbols: 100,
    upper_channel_count: 3,
    volume_anomalies: 2,
  },
  regime_changes: 26,
  regime_transition_cells: [
    { count: 45, current_regime: "uptrend", previous_regime: "uptrend" },
    { count: 10, current_regime: "uptrend", previous_regime: "range" },
    { count: 20, current_regime: "range", previous_regime: "range" },
    { count: 25, current_regime: "downtrend", previous_regime: "uptrend" },
  ],
  return_dispersion: 0.0078,
  sector_scorecards: SECTOR_SCORECARDS,
  sector_uptrend_changes: [
    {
      change: 0.263,
      previous_sample_count: 19,
      previous_uptrend_fraction: 0.263,
      sample_count: 19,
      sector: "technology",
      uptrend_fraction: 0.526,
    },
    {
      change: -0.133,
      previous_sample_count: 15,
      previous_uptrend_fraction: 0.4,
      sample_count: 15,
      sector: "consumer",
      uptrend_fraction: 0.267,
    },
  ],
  to_downtrend: 3,
  to_uptrend: 11,
  unchanged: 1,
  verified_candidate_churn: {
    changes: [
      {
        current_rank: 1,
        name: "SPDR S&P 500 ETF",
        previous_rank: null,
        rank_change: null,
        status: "entered",
        symbol: "US.SPY",
      },
    ],
  },
  volume_anomalies: 4,
} satisfies components["schemas"]["OverviewInternalEvidence"];

const BASE_RESPONSE = {
  data: {
    abstain_count: 1,
    abstain_reasons: [{ count: 1, reason: "interval_too_wide" }],
    batch_generated_at: "2026-07-10T20:05:02Z",
    challenger_opportunities: [],
    data_confidence: 0.95,
    internal_evidence: INTERNAL_EVIDENCE,
    market_breadth: 0.58,
    proxies: [
      {
        as_of: SNAPSHOT.as_of,
        direction: "up",
        last_close: 447.0,
        name: "DIA proxy",
        regime: "uptrend",
        regime_probability: 0.56,
        serving_status: "live",
        session_return: 0.0029,
        symbol: "US.DIA",
        trend: [1, 1.01],
        unavailable_reason: null,
      },
      {
        as_of: SNAPSHOT.as_of,
        direction: "down",
        last_close: 223.5,
        name: "IWM proxy",
        regime: "transition",
        regime_probability: 0.44,
        serving_status: "live",
        session_return: -0.0024,
        symbol: "US.IWM",
        trend: [1, 0.99],
        unavailable_reason: null,
      },
      {
        as_of: SNAPSHOT.as_of,
        direction: "up",
        last_close: 523.0,
        name: "QQQ proxy",
        regime: "range",
        regime_probability: 0.62,
        serving_status: "live",
        session_return: 0.0032,
        symbol: "US.QQQ",
        trend: [1, 1.02],
        unavailable_reason: null,
      },
      {
        as_of: SNAPSHOT.as_of,
        direction: "up",
        last_close: 740.86,
        name: "SPY proxy",
        regime: "range",
        regime_probability: 0.64,
        serving_status: "live",
        session_return: 0.0048,
        symbol: "US.SPY",
        trend: [1, 1.03],
        unavailable_reason: null,
        verified_research_by_horizon: [
          verifiedEvidence(1, 0.52, 0.38),
          verifiedEvidence(5, 0.61, 0.35),
          verifiedEvidence(20, 0.477192, 0.471729),
        ],
      },
    ],
    research_opportunities: [],
    regime_distribution: [
      { count: 31, fraction: 0.31, regime: "uptrend" },
      { count: 28, fraction: 0.28, regime: "range" },
      { count: 20, fraction: 0.2, regime: "transition" },
      { count: 12, fraction: 0.12, regime: "downtrend" },
      { count: 9, fraction: 0.09, regime: "invalid" },
    ],
    sector_regime_matrix: [
      { count: 38, fraction: 0.38, regime: "uptrend", sector: "technology" },
      { count: 34, fraction: 0.34, regime: "range", sector: "technology" },
      { count: 16, fraction: 0.16, regime: "transition", sector: "technology" },
      { count: 8, fraction: 0.08, regime: "downtrend", sector: "technology" },
      { count: 4, fraction: 0.04, regime: "invalid", sector: "technology" },
    ],
    top_opportunities: [EXECUTABLE_ROW, ABSTAIN_ROW],
    total_inference_latency_ms: 2410,
  },
  data_status: "ready",
  effective_abstain: false,
  evaluated_at: "2026-07-10T20:05:04Z",
  exclusion_reason: [],
  generated_at: "2026-07-10T20:05:04Z",
  request_id: "request-overview-1",
  resolved_snapshot: SNAPSHOT,
  serving_status: "live",
} satisfies OverviewResponse;

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { gcTime: Number.POSITIVE_INFINITY, retry: false, staleTime: 60_000 },
    },
  });

  test("uses one compact model-availability line when a proxy has no price component", async () => {
    const responseWithoutProxyModels = structuredClone(BASE_RESPONSE);
    responseWithoutProxyModels.data.proxies =
      responseWithoutProxyModels.data.proxies.map((proxy) => {
        const { verified_research_by_horizon: _ignored, ...withoutModels } = proxy;
        void _ignored;
        return withoutModels;
      });

    renderOverview(vi.fn().mockResolvedValue(responseWithoutProxyModels), {
      initialEntry: "/overview?mode=research",
    });

    await userEvent.setup().click(await screen.findByText("指数已验证研究证据"));
    const proxyStrip = await screen.findByRole("region", { name: "美国大盘代理" });
    expect(
      within(proxyStrip).getAllByTestId("market-proxy-model-availability"),
    ).toHaveLength(4);
    expect(proxyStrip).toHaveTextContent("$740.86");
    expect(proxyStrip).toHaveTextContent("$523.00");
    expect(proxyStrip).not.toHaveTextContent("暂不可用");
    expect(
      within(proxyStrip).queryByText("当前证据不足，无法生成风险摘要"),
    ).not.toBeInTheDocument();
    expect(
      within(proxyStrip).queryByText("模型参考价与 q50 均已标记"),
    ).not.toBeInTheDocument();
  });
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location-search">{location.search}</output>;
}

function IdentitySwitcher() {
  const navigate = useNavigate();
  return (
    <button
      onClick={() => {
        void navigate("/overview?snapshot_id=other-snapshot&horizon=20");
      }}
      type="button"
    >
      切换研究快照
    </button>
  );
}

function renderOverview(
  loadOverview: OverviewLoader,
  options: { initialEntry?: string; withIdentitySwitcher?: boolean } = {},
) {
  const queryClient = createTestQueryClient();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[options.initialEntry ?? "/overview?mode=formal"]}>
        <OverviewPage loadOverview={loadOverview} />
        <LocationProbe />
        {options.withIdentitySwitcher === true ? <IdentitySwitcher /> : null}
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...result, queryClient };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  chartHarness.addSeries.mockClear();
  chartHarness.createChart.mockClear();
  chartHarness.fitContent.mockClear();
  chartHarness.remove.mockClear();
  chartHarness.resize.mockClear();
  chartHarness.setData.mockClear();
  resizeObserverHarness.observers = [];
  resizeObserverHarness.disconnect.mockClear();
  resizeObserverHarness.observe.mockClear();
  resizeObserverHarness.unobserve.mockClear();
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(
    function getClientWidth(this: HTMLElement) {
      return this.classList.contains("market-proxy__sparkline") ? 72 : 0;
    },
  );
  vi.stubGlobal(
    "ResizeObserver",
    class ResizeObserverMock {
      targets: Element[] = [];
      constructor(readonly callback: ResizeObserverCallback) {
        resizeObserverHarness.observers.push(this);
      }

      disconnect = vi.fn(() => {
        resizeObserverHarness.disconnect();
      });
      observe = (target: Element) => {
        this.targets.push(target);
        resizeObserverHarness.observe(target);
      };
      unobserve = resizeObserverHarness.unobserve;
    },
  );
});

describe("overview route integration", () => {
  test("replaces the Task 6 empty outlet with the overview evidence workspace", async () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1440,
    });
    const router = createMemoryRouter(APP_ROUTES, { initialEntries: ["/overview"] });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole("heading", { name: "市场总览", level: 1 }),
    ).toBeInTheDocument();
  });
});

describe("overview evidence mapping", () => {
  test("shows rule-first research leads without unpublished model placeholders", async () => {
    const user = userEvent.setup();
    renderOverview(
      vi.fn().mockResolvedValue({
        ...BASE_RESPONSE,
        data: {
          ...BASE_RESPONSE.data,
          research_opportunities: [SPY_RESEARCH_OPPORTUNITY],
        },
      }),
      { initialEntry: "/overview?mode=research" },
    );

    const table = await screen.findByRole("table", { name: "Top 研究候选" });
    expect(within(table).getAllByRole("columnheader")).toHaveLength(8);
    expect(within(table).getByRole("columnheader", { name: "排名" })).toBeVisible();
    expect(
      within(table).getByRole("columnheader", { name: "为什么今天出现" }),
    ).toBeVisible();
    expect(
      within(table).getByRole("columnheader", { name: "1 / 5 / 20 日收益" }),
    ).toBeVisible();
    expect(within(table).getByRole("columnheader", { name: "成交量 Z" })).toBeVisible();
    expect(within(table).getByRole("columnheader", { name: "通道位置" })).toBeVisible();
    expect(within(table).getByRole("columnheader", { name: "研究入口" })).toBeVisible();
    const row = within(table).getByRole("row", { name: /SPY/ });
    expect(row).toHaveTextContent("按当前排序进入前列，未触发规则突破");
    expect(row).toHaveTextContent("0.10");
    expect(row).toHaveTextContent("62.0%");
    expect(row).not.toHaveTextContent("未发布");
    expect(within(row).getByRole("link", { name: "研究 SPY" })).toHaveAttribute(
      "href",
      expect.stringContaining(`snapshot_id=${SNAPSHOT.snapshot_id}`),
    );
    const compactList = screen.getByTestId("mobile-research-opportunity-list");
    expect(compactList.children).toHaveLength(1);
    expect(compactList).toHaveTextContent("按当前排序进入前列，未触发规则突破");
    expect(compactList).toHaveTextContent("成交量 Z");
    await user.click(within(compactList).getByRole("button", { name: "查看完整证据" }));
    expect(compactList).toHaveTextContent("20 日 ATR（占价格）");
    expect(
      within(compactList).getByRole("button", { name: "收起完整证据" }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  test("follows the v6 market, sector, symbol, research and risk chapters", async () => {
    renderOverview(
      vi.fn().mockResolvedValue({
        ...BASE_RESPONSE,
        data: {
          ...BASE_RESPONSE.data,
          research_opportunities: [SPY_RESEARCH_OPPORTUNITY],
        },
      }),
      { initialEntry: "/overview?mode=research" },
    );
    const band = await screen.findByRole("region", { name: "市场概况" });
    expect(band).toHaveTextContent("66 涨");
    expect(band).toHaveTextContent("33 跌");
    expect(band).toHaveTextContent("4 个量能异常");
    const ordered = [
      band,
      screen.getByRole("region", { name: "36 个代表标的市场地图" }),
      screen.getByRole("heading", { name: "行业" }),
      screen.getByRole("region", { name: "市场焦点 Top 5" }),
      screen.getByRole("table", { name: "Top 研究候选" }),
      screen.getByRole("complementary", { name: "风险与分歧" }),
      screen.getByRole("region", { name: "数据与模型覆盖状态" }),
    ];
    for (let i = 1; i < ordered.length; i++)
      expect(ordered[i - 1]!.compareDocumentPosition(ordered[i]!)).toBe(
        Node.DOCUMENT_POSITION_FOLLOWING,
      );
    expect(
      within(screen.getByRole("complementary", { name: "风险与分歧" })).getAllByRole(
        "listitem",
      ),
    ).toHaveLength(5);
  });

  test("labels missing comparison history instead of inventing zero changes", async () => {
    const response: OverviewResponse = structuredClone(BASE_RESPONSE);
    response.data.internal_evidence = {
      ...BASE_RESPONSE.data.internal_evidence,
      comparison_snapshot: null,
      history: response.data.internal_evidence?.history.slice(-1) ?? [],
      regime_changes: null,
      sector_uptrend_changes: [],
      to_downtrend: null,
      to_uptrend: null,
    };
    renderOverview(vi.fn().mockResolvedValue(response), {
      initialEntry: "/overview?mode=research",
    });

    expect(await screen.findAllByText("历史不足")).not.toHaveLength(0);
    expect(screen.queryByText("状态切换0 个")).not.toBeInTheDocument();
  });

  test("presents one horizon-controlled sector table with explicit sample insufficiency", async () => {
    const response: OverviewResponse = structuredClone(BASE_RESPONSE);
    response.data.internal_evidence = {
      ...BASE_RESPONSE.data.internal_evidence,
      sector_scorecards: SECTOR_SCORECARDS.map((card, index) =>
        index === 0
          ? {
              ...card,
              previous_sample_count: 3,
              sample_count: 4,
              uptrend_change: null,
            }
          : card,
      ),
    };
    renderOverview(vi.fn().mockResolvedValue(response), {
      initialEntry: "/overview?mode=research",
    });
    const user = userEvent.setup();

    await userEvent.setup().click(await screen.findByText("更多行业与研究诊断"));
    const sectorHealth = await screen.findByRole("region", {
      name: "行业体检与相对强弱",
    });
    expect(within(sectorHealth).getAllByRole("table")).toHaveLength(1);
    expect(
      within(sectorHealth).getByRole("group", { name: "行业收益周期" }),
    ).toBeVisible();
    expect(
      within(sectorHealth).getByRole("columnheader", { name: "20 日中位收益" }),
    ).toBeVisible();
    expect(sectorHealth).toHaveTextContent("行业样本不足（3/5）");

    const rowsAt20 = within(sectorHealth).getAllByRole("row").slice(1);
    expect(rowsAt20.at(-1)).toHaveTextContent("科技");
    expect(rowsAt20.at(-1)).toHaveTextContent("+25.0%");

    await user.click(within(sectorHealth).getByRole("button", { name: "5 日" }));
    expect(
      within(sectorHealth).getByRole("columnheader", { name: "5 日中位收益" }),
    ).toBeVisible();
    const rowsAt5 = within(sectorHealth).getAllByRole("row").slice(1);
    expect(rowsAt5.at(-1)).toHaveTextContent("科技");
    expect(rowsAt5.at(-1)).toHaveTextContent("+12.0%");
  });

  test("reorders sector rows by the selected horizon and preserves relative-SPY math", async () => {
    const response: OverviewResponse = structuredClone(BASE_RESPONSE);
    response.data.internal_evidence = {
      ...BASE_RESPONSE.data.internal_evidence,
      sector_scorecards: [
        {
          ...SECTOR_SCORECARDS[0]!,
          median_return_20: 0.01,
          median_return_5: 0.04,
          sector: "technology",
        },
        {
          ...SECTOR_SCORECARDS[1]!,
          median_return_20: 0.05,
          median_return_5: -0.01,
          sector: "consumer",
        },
      ],
    };
    renderOverview(vi.fn().mockResolvedValue(response), {
      initialEntry: "/overview?mode=research",
    });
    const user = userEvent.setup();
    await userEvent.setup().click(await screen.findByText("更多行业与研究诊断"));
    const sectorHealth = await screen.findByRole("region", {
      name: "行业体检与相对强弱",
    });

    let rows = within(sectorHealth).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("消费");
    expect(rows[0]).toHaveTextContent("+25.0%");
    expect(rows[1]).toHaveTextContent("科技");
    expect(rows[1]).toHaveTextContent("+21.0%");

    await user.click(within(sectorHealth).getByRole("button", { name: "5 日" }));
    rows = within(sectorHealth).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("科技");
    expect(rows[0]).toHaveTextContent("+14.0%");
    expect(rows[1]).toHaveTextContent("消费");
    expect(rows[1]).toHaveTextContent("+9.0%");
  });

  test("exposes the market-structure responsive layout as a semantic UI contract", async () => {
    renderOverview(vi.fn().mockResolvedValue(BASE_RESPONSE), {
      initialEntry: "/overview?mode=research",
    });

    await userEvent.setup().click(await screen.findByText("更多行业与研究诊断"));
    const structure = await screen.findByRole("region", {
      name: "市场结构四联图",
    });
    const grid = within(structure).getByRole("group", {
      name: "市场结构图表网格",
    });
    expect(grid).toHaveAttribute(
      "data-responsive-layout",
      "desktop-2x2 narrow-1-column",
    );
    expect(grid.children).toHaveLength(4);
  });

  test("uses the explicit indeterminate label for invalid regimes", async () => {
    renderOverview(vi.fn().mockResolvedValue(BASE_RESPONSE), {
      initialEntry: "/overview?mode=research",
    });

    await screen.findByRole("region", { name: "市场总览证据" });
    expect(screen.getAllByText("状态不可判定").length).toBeGreaterThan(0);
    expect(screen.queryByText("数据不足")).not.toBeInTheDocument();
  });

  test("shows 36 equal-area representatives, drills into sectors and keeps map state in the URL", async () => {
    const user = userEvent.setup();
    renderOverview(vi.fn().mockResolvedValue(BASE_RESPONSE), {
      initialEntry: "/overview?map_return=20&map_sector=technology&mode=research",
    });

    const sectorMap = await screen.findByRole("region", {
      name: "科技全部 20 个标的市场地图",
    });
    expect(sectorMap.querySelectorAll(".ov-map-cell")).toHaveLength(20);
    expect(
      within(sectorMap).getByRole("button", { name: "20 日收益" }),
    ).toHaveAttribute("aria-pressed", "true");

    await user.selectOptions(
      within(sectorMap).getByRole("combobox", { name: "行业" }),
      "all",
    );
    const marketMap = await screen.findByRole("region", {
      name: "36 个代表标的市场地图",
    });
    expect(marketMap.querySelectorAll(".ov-map-cell")).toHaveLength(36);
    expect(marketMap).toHaveTextContent("等面积，非市值权重");
    expect(
      within(marketMap).getByRole("link", { name: "查看全部 100 个标的" }),
    ).toHaveAttribute(
      "href",
      expect.stringContaining(`snapshot_id=${SNAPSHOT.snapshot_id}`),
    );

    await user.click(within(marketMap).getByRole("button", { name: /^T05，/ }));
    expect(within(marketMap).getByRole("button", { name: /^T05，/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const inspector = within(marketMap).getByRole("complementary", {
      name: "选中标的详情",
    });
    expect(within(inspector).getByRole("heading", { name: "T05" })).toBeVisible();
    const researchLink = within(inspector).getByRole("link", { name: "查看标的研究" });
    expect(researchLink).toHaveAttribute(
      "href",
      expect.stringContaining("/research/US.T05"),
    );
    expect(researchLink.getAttribute("href")).toContain(
      `snapshot_id=${SNAPSHOT.snapshot_id}`,
    );
    expect(researchLink.getAttribute("href")).toContain("dataset_revision=bars-r42");
    expect(researchLink.getAttribute("href")).toContain("horizon=5");
    await waitFor(() => {
      expect(screen.getByTestId("location-search")).toHaveTextContent(
        "map_symbol=US.T05",
      );
    });

    await user.click(within(marketMap).getByRole("button", { name: "5 日收益" }));
    await waitFor(() => {
      expect(screen.getByTestId("location-search")).toHaveTextContent("map_return=5");
      expect(screen.getByTestId("location-search")).toHaveTextContent("map_sector=all");
      expect(screen.getByTestId("location-search")).toHaveTextContent(
        `snapshot_id=${SNAPSHOT.snapshot_id}`,
      );
    });
  }, 10_000);

  test("limits research leads to ten desktop rows while retaining existing compact cards", async () => {
    const opportunities = Array.from({ length: 12 }, (_, index) => ({
      ...SPY_RESEARCH_OPPORTUNITY,
      name: `Research lead ${index + 1}`,
      rank_basis: [`第 ${index + 1} 名横截面证据`],
      symbol: `US.L${index + 1}`,
    })) satisfies components["schemas"]["ResearchOpportunityRow"][];
    renderOverview(
      vi.fn().mockResolvedValue({
        ...BASE_RESPONSE,
        data: { ...BASE_RESPONSE.data, research_opportunities: opportunities },
      }),
      { initialEntry: "/overview?mode=research" },
    );

    const table = await screen.findByRole("table", { name: "Top 研究候选" });
    expect(within(table).getAllByRole("row")).toHaveLength(11);
    expect(
      screen.getByTestId("mobile-research-opportunity-list").children,
    ).toHaveLength(4);
    expect(screen.queryByText("Research lead 11")).not.toBeInTheDocument();
  });

  test("expands one research evidence row at a time with keyboard controls", async () => {
    const user = userEvent.setup();
    const qqq = {
      ...SPY_RESEARCH_OPPORTUNITY,
      name: "Invesco QQQ Trust",
      rank_basis: ["20日规则证据排名"],
      symbol: "US.QQQ",
    } satisfies components["schemas"]["ResearchOpportunityRow"];
    renderOverview(
      vi.fn().mockResolvedValue({
        ...BASE_RESPONSE,
        data: {
          ...BASE_RESPONSE.data,
          research_opportunities: [SPY_RESEARCH_OPPORTUNITY, qqq],
        },
      }),
      { initialEntry: "/overview?mode=research" },
    );

    const table = await screen.findByRole("table", { name: "Top 研究候选" });
    const spyToggle = within(table).getByRole("button", { name: "展开SPY研究证据" });
    spyToggle.focus();
    await user.keyboard("{Enter}");
    expect(spyToggle).toHaveAttribute("aria-expanded", "true");
    expect(within(table).getAllByText("5日研究证据排名")).toHaveLength(1);
    expect(within(table).getByText("20 日 ATR（占价格）")).toBeVisible();
    expect(within(table).getByText("2.0%")).toBeVisible();

    const qqqToggle = within(table).getByRole("button", { name: "展开QQQ研究证据" });
    await user.click(qqqToggle);
    expect(spyToggle).toHaveAttribute("aria-expanded", "false");
    expect(qqqToggle).toHaveAttribute("aria-expanded", "true");
    expect(within(table).queryByText("5日研究证据排名")).not.toBeInTheDocument();
    expect(within(table).getAllByText("20日规则证据排名")).toHaveLength(1);
    const researchLink = within(table).getByRole("link", { name: "查看标的研究" });
    expect(researchLink).toBeVisible();
    const researchUrl = new URL(researchLink.getAttribute("href")!, "http://localhost");
    expect(researchUrl.searchParams.get("snapshot_id")).toBe(SNAPSHOT.snapshot_id);
    expect(researchUrl.searchParams.get("horizon")).toBeNull();
  });

  test("keeps verified SPY price projections visible and switches to the 20-day band", async () => {
    const user = userEvent.setup();
    renderOverview(vi.fn().mockResolvedValue(BASE_RESPONSE), {
      initialEntry: "/overview?mode=research",
    });

    await userEvent.setup().click(await screen.findByText("指数已验证研究证据"));
    const proxyStrip = await screen.findByRole("region", { name: "美国大盘代理" });
    expect(
      within(proxyStrip).getByRole("region", {
        name: "未来第 5 个交易日收盘价研究预测",
      }),
    ).toHaveTextContent("740.82");
    expect(proxyStrip).toHaveTextContent("$740.86");
    expect(within(proxyStrip).getAllByText("状态概率")).toHaveLength(4);
    expect(within(proxyStrip).getAllByText("研究方向 偏强")).toHaveLength(3);

    expect(within(proxyStrip).getByRole("group", { name: "预测周期" })).toBeVisible();
    expect(within(proxyStrip).getAllByText("2026/07/10 16:05 美东时间")).toHaveLength(
      4,
    );

    await user.click(screen.getByRole("button", { name: "未来 1 个交易日" }));
    const oneDayProjection = await screen.findByRole("region", {
      name: "未来第 1 个交易日收盘价研究预测",
    });
    expect(oneDayProjection).toHaveTextContent("739.91");
    expect(oneDayProjection).toHaveTextContent("−$0.95");
    expect(oneDayProjection).toHaveTextContent("-0.1%");
    expect(oneDayProjection).toHaveTextContent("模型参考价与 q50 均已标记");
    expect(oneDayProjection).toHaveTextContent("q10–q90");
    expect(oneDayProjection).toHaveTextContent(
      "q10 是模型条件分布的第 10 分位，不是最大亏损、止损或完整风险边界。",
    );
    oneDayProjection.closest("details")?.setAttribute("open", "");
    expect(
      within(oneDayProjection).getByRole("img", {
        name: /当前收盘.*q10.*q50.*q90/,
      }),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "未来 20 个交易日" }));

    const projection = await screen.findByRole("region", {
      name: "未来第 20 个交易日收盘价研究预测",
    });
    expect(projection).toHaveTextContent("746.12");
    expect(projection).toHaveTextContent("698.73");
    expect(projection).toHaveTextContent("785.17");
    expect(projection).toHaveTextContent("模型参考价与 q50 均已标记");
    expect(projection).toHaveTextContent("q10–q90");
    expect(projection).toHaveTextContent(/中位情景/);
  });

  test("keeps real market evidence visible without writing fake model versions in data-only mode", async () => {
    renderOverview(
      vi.fn().mockResolvedValue({
        ...BASE_RESPONSE,
        data: {
          ...BASE_RESPONSE.data,
          abstain_count: 100,
          abstain_reasons: [{ count: 100, reason: "no_champion_candidate" }],
          top_opportunities: [],
        },
        effective_abstain: true,
        exclusion_reason: ["no_champion_candidate"],
        resolved_snapshot: DATA_ONLY_SNAPSHOT,
      }),
      { initialEntry: "/overview" },
    );

    expect(await screen.findByText(/正式预测未覆盖：100 个标的/)).toBeVisible();
    expect(
      screen.queryByRole("region", { name: "期权脉搏章节" }),
    ).not.toBeInTheDocument();
    expect(
      within(screen.getByRole("navigation", { name: "章节" }))
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["01市场", "02行业", "03标的", "04研究", "05风险与覆盖"]);
    expect(screen.getByText(/正式预测仍等待完整主模型/)).toBeVisible();
    expect(screen.getByRole("region", { name: "美国大盘代理" })).toHaveTextContent(
      "SPY",
    );
    expect(screen.getByText("暂无研究候选")).toBeVisible();
    expect(
      within(screen.getByRole("region", { name: "美国大盘代理" })).getAllByText(
        "当日变动",
      ),
    ).toHaveLength(4);
    expect(screen.getByText("暂无主模型（Champion）")).toBeVisible();
    expect(screen.getByText("尚未校准")).toBeVisible();

    const locationSearch = screen.getByTestId("location-search").textContent ?? "";
    expect(locationSearch).not.toContain("model_version");
    expect(locationSearch).not.toContain("calibration_version");
  });

  test("renders v6 index order, executable evidence and preserved versions", async () => {
    renderOverview(vi.fn().mockResolvedValue(BASE_RESPONSE));
    const proxies = await screen.findByRole("region", { name: "四大指数行情" });
    expect(
      within(proxies)
        .getAllByTestId("market-proxy-symbol")
        .map((node) => node.textContent),
    ).toEqual(["SPY", "QQQ", "DIA", "IWM"]);
    const table = screen.getByRole("table", { name: "Top 机会 ； 可执行证据排序" });
    const nvda = within(table).getByRole("row", { name: /NVDA/ });
    expect(nvda).toHaveTextContent("72.0%");
    expect(nvda).toHaveTextContent("-11.2%");
    expect(nvda).toHaveTextContent("+6.4%");
    const target = new URL(
      within(nvda).getByRole("link", { name: "NVDA" }).getAttribute("href")!,
      "http://localhost",
    );
    expect(Object.fromEntries(target.searchParams)).toMatchObject({
      snapshot_id: SNAPSHOT.snapshot_id,
      dataset_revision: SNAPSHOT.dataset_revision,
      horizon: "5",
    });
    expect(screen.getByRole("heading", { name: "状态分布" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "行业 × 状态矩阵" })).toHaveTextContent(
      "科技",
    );
    const footer = screen.getByRole("contentinfo", { name: "预测版本与延迟" });
    expect(footer).toHaveTextContent("SL-2.6.1");
    expect(footer).toHaveTextContent("CAL-2026-07-08");
    expect(footer).toHaveTextContent("2.41 秒");
  });

  test("keeps v6 chapters and snapshot identity through the formal view", async () => {
    renderOverview(vi.fn().mockResolvedValue(BASE_RESPONSE));
    const page = await screen.findByRole("region", { name: "市场总览证据" });
    expect(page).toHaveAttribute("data-layout", "v6-chapters");
    const nav = screen.getByRole("navigation", { name: "章节" });
    expect(
      within(nav)
        .getAllByRole("link")
        .map((link) => link.getAttribute("href")),
    ).toEqual([
      "#overview-market",
      "#overview-sectors",
      "#overview-symbols",
      "#overview-research",
      "#overview-risk",
    ]);
    const table = screen.getByRole("table", { name: "Top 机会 ； 可执行证据排序" });
    expect(within(table).getByRole("link", { name: "NVDA" })).toHaveAttribute(
      "href",
      expect.stringContaining(`snapshot_id=${SNAPSHOT.snapshot_id}`),
    );
    expect(
      screen.getByRole("contentinfo", { name: "预测版本与延迟" }),
    ).toHaveTextContent("SL-20260710-1605");
  });

  test("keeps positive q10 sign-aware while preserving risk prominence", async () => {
    renderOverview(
      vi.fn().mockResolvedValue({
        ...BASE_RESPONSE,
        data: {
          ...BASE_RESPONSE.data,
          top_opportunities: [
            {
              ...EXECUTABLE_ROW,
              executable_q10: 0.01,
              name: "Positive q10 fixture",
              symbol: "US.POS",
            },
          ],
        },
      }),
    );

    const table = await screen.findByRole("table", {
      name: "Top 机会 ； 可执行证据排序",
    });
    const positiveQ10 = within(table).getByLabelText("收益: 上行 +1.0%");
    expect(positiveQ10).toHaveClass("return-value--up");
    expect(positiveQ10.closest("td")).toHaveClass("opportunity-table__risk");
  });

  test("sizes proxy trends from their containers and follows responsive resize", async () => {
    const view = renderOverview(vi.fn().mockResolvedValue(BASE_RESPONSE));

    await userEvent.setup().click(await screen.findByText("指数已验证研究证据"));
    const proxyStrip = await screen.findByRole("region", { name: "美国大盘代理" });
    const spyTrend = within(proxyStrip).getByRole("img", {
      name: "SPY 趋势：偏强，2 个观测点",
    });
    expect(chartHarness.createChart).toHaveBeenCalledWith(
      spyTrend,
      expect.objectContaining({ height: 34, width: 72 }),
    );
    expect(resizeObserverHarness.observe).toHaveBeenCalledWith(spyTrend);

    act(() => {
      resizeObserverHarness.observers
        .find((observer) => observer.targets.includes(spyTrend))
        ?.callback(
          [
            {
              contentRect: { width: 64 },
              target: spyTrend,
            } as unknown as ResizeObserverEntry,
          ],
          {} as ResizeObserver,
        );
    });

    expect(chartHarness.resize).toHaveBeenCalledWith(64, 34);
    view.unmount();
    const proxyObservers = resizeObserverHarness.observers.filter((observer) =>
      observer.targets.some((target) =>
        target.classList.contains("market-proxy__sparkline"),
      ),
    );
    expect(proxyObservers).toHaveLength(4);
    for (const observer of proxyObservers)
      expect(observer.disconnect).toHaveBeenCalledTimes(1);
    expect(chartHarness.remove).toHaveBeenCalledTimes(4);
  });
});

describe("overview query identity and failure states", () => {
  test("migrates a legacy proxy horizon while preserving snapshot identity", async () => {
    const loadOverview = vi.fn().mockResolvedValue(BASE_RESPONSE);
    renderOverview(loadOverview, {
      initialEntry:
        "/overview?as_of=2026-07-10T20%3A05%3A00Z&snapshot_id=requested&revision_no=2&dataset_revision=bars-r41&feature_set_version=features-v6&universe_version=us-100-2026q3&model_version=SL-2.6.0&calibration_version=CAL-old&horizon=20&mode=formal",
    });

    await within(
      await screen.findByRole("table", { name: "Top 机会 ； 可执行证据排序" }),
    ).findByText("NVDA");
    expect(loadOverview).toHaveBeenCalledWith({
      as_of: "2026-07-10T20:05:00Z",
      calibration_version: "CAL-old",
      dataset_revision: "bars-r41",
      feature_set_version: "features-v6",
      horizon: 5,
      model_version: "SL-2.6.0",
      mode: "formal",
      snapshot_id: "requested",
      universe_version: "us-100-2026q3",
    });
    const locationSearch = screen.getByTestId("location-search");
    await waitFor(
      () => expect(locationSearch).toHaveTextContent("snapshot_id=SL-20260710-1605"),
      { container: locationSearch, timeout: 5000 },
    );
    const resolvedSearch = locationSearch.textContent ?? "";
    expect(resolvedSearch).toContain("snapshot_id=SL-20260710-1605");
    expect(resolvedSearch).toContain("revision_no=3");
    expect(resolvedSearch).toContain("model_version=SL-2.6.1");
    expect(new URLSearchParams(resolvedSearch).get("horizon")).toBeNull();
    expect(resolvedSearch).toContain("proxy_horizon=20");
    expect(screen.getByRole("button", { name: "未来 20 个交易日" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  test("does not show a previous snapshot as placeholder for a different identity", async () => {
    const pending = new Promise<OverviewResponse>(() => undefined);
    const loadOverview = vi
      .fn<OverviewLoader>()
      .mockResolvedValueOnce(BASE_RESPONSE)
      .mockReturnValueOnce(pending);
    renderOverview(loadOverview, { withIdentitySwitcher: true });
    const user = userEvent.setup();

    expect(
      await within(
        await screen.findByRole("table", {
          name: "Top 机会 ； 可执行证据排序",
        }),
      ).findByText("NVDA"),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "切换研究快照" }));

    expect(screen.getByRole("status", { name: "正在加载总览证据" })).toBeVisible();
    expect(screen.queryAllByText("NVDA")).toHaveLength(0);
  });

  test("retains the last successful same-identity view after a failed refresh as offline", async () => {
    const loadOverview = vi
      .fn<OverviewLoader>()
      .mockResolvedValueOnce(BASE_RESPONSE)
      .mockRejectedValueOnce(new Error("connection failed"));
    const { queryClient } = renderOverview(loadOverview);

    expect(
      await within(
        await screen.findByRole("table", {
          name: "Top 机会 ； 可执行证据排序",
        }),
      ).findByText("NVDA"),
    ).toBeVisible();
    await queryClient.invalidateQueries({ queryKey: ["overview"] });

    const banner = await screen.findByRole("status", { name: "全局数据状态" });
    expect(banner).toHaveTextContent("当前离线");
    expect(
      within(
        screen.getByRole("table", { name: "Top 机会 ； 可执行证据排序" }),
      ).getByText("NVDA"),
    ).toBeVisible();
    expect(screen.getByRole("region", { name: "市场总览证据" })).toHaveAttribute(
      "data-view-status",
      "offline-retained",
    );
    expect(screen.queryByText("实时", { exact: true })).not.toBeInTheDocument();
  });

  test("shows loading, empty and unavailable states without invented values", async () => {
    const pending = new Promise<OverviewResponse>(() => undefined);
    const loadingView = renderOverview(vi.fn().mockReturnValue(pending));
    expect(screen.getByRole("status", { name: "正在加载总览证据" })).toBeVisible();
    loadingView.unmount();

    renderOverview(
      vi.fn().mockResolvedValue({
        ...BASE_RESPONSE,
        data: { ...BASE_RESPONSE.data, top_opportunities: [] },
        serving_status: "unavailable",
      }),
    );
    expect(await screen.findByRole("status", { name: "暂无可执行机会" })).toBeVisible();
    expect(screen.getByRole("status", { name: "全局数据状态" })).toHaveTextContent(
      "数据不可用",
    );
    expect(
      screen.getByRole("region", { name: "36 个代表标的市场地图" }),
    ).toHaveTextContent("SPY");
  });

  test("shows delayed data with its as-of time and never calls it live", async () => {
    renderOverview(
      vi.fn().mockResolvedValue({
        ...BASE_RESPONSE,
        serving_status: "delayed",
      }),
    );

    const banner = await screen.findByRole("status", { name: "全局数据状态" });
    expect(banner).toHaveTextContent("数据延迟");
    expect(banner).toHaveTextContent("所选快照行情截至: 2026/07/10 16:05 美东时间");
    expect(banner).not.toHaveTextContent("实时");
    expect(screen.getByRole("region", { name: "市场总览证据" })).toHaveAttribute(
      "data-serving-status",
      "delayed",
    );
  });

  test("shows a bounded retryable error and recovers without exposing raw details", async () => {
    const loadOverview = vi
      .fn<OverviewLoader>()
      .mockRejectedValueOnce(new Error("private upstream detail"))
      .mockResolvedValueOnce(BASE_RESPONSE);
    renderOverview(loadOverview);
    const user = userEvent.setup();

    const alert = await screen.findByRole("alert", { name: "总览数据不可用" });
    expect(alert).not.toHaveTextContent("private upstream detail");
    await user.click(within(alert).getByRole("button", { name: "重试总览数据" }));

    expect(
      await within(
        await screen.findByRole("table", {
          name: "Top 机会 ； 可执行证据排序",
        }),
      ).findByText("NVDA"),
    ).toBeVisible();
  });
});

describe("overview responsive contract", () => {
  test("uses a ranked expandable table and responsive evidence layout", () => {
    const css = readFileSync(
      resolve(process.cwd(), "frontend/src/features/overview/overview.css"),
      "utf8",
    );

    expect(css).toMatch(
      /\.overview-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)\s+minmax\(320px, 360px\)/s,
    );
    expect(css).toMatch(
      /\.opportunity-table--research\s*\{[^}]*table-layout:\s*fixed/s,
    );
    expect(css).toMatch(
      /\.opportunity-table--research thead th,[\s\S]*white-space:\s*normal/s,
    );
    expect(css).toMatch(/\.opportunity-table__scroll\s*\{[^}]*overflow-x:\s*auto/s);
    expect(css).toMatch(/\.opportunity-table tbody tr\s*\{[^}]*min-height:\s*44px/s);
    expect(css).not.toMatch(
      /\.opportunity-table__risk\s+\.return-value\s*\{[^}]*color:/s,
    );
    expect(css).toMatch(/\.opportunity-mobile-list\s*\{[^}]*display:\s*none/s);
    expect(css).toMatch(/\.opportunity-mobile-list__link\s*\{[^}]*min-height:\s*44px/s);
    expect(css).toMatch(
      /@media \(max-width:\s*1599px\)[\s\S]*\.overview-grid\s*\{[^}]*grid-template-columns:\s*1fr/s,
    );
    expect(css).toMatch(
      /@media \(max-width:\s*1599px\)[\s\S]*\.evidence-inspector\s*\{[^}]*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\)/s,
    );
    expect(css).toMatch(
      /@media \(max-width:\s*767px\)[\s\S]*\.overview-page\s*\{[^}]*overflow-x:\s*hidden/s,
    );
    expect(css).toMatch(
      /\.overview-pulse__rail\s*\{[^}]*grid-template-columns:\s*repeat\(6/s,
    );
    expect(css).toMatch(
      /@media \(max-width:\s*767px\)[\s\S]*\.overview-pulse__rail,[\s\S]*overflow-x:\s*auto/s,
    );
    expect(css).toMatch(
      /@media \(max-width:\s*767px\)[\s\S]*\.risk-divergence ul\[data-expanded="false"\][\s\S]*nth-child\(n \+ 4\)[^}]*display:\s*none/s,
    );
    expect(css).toMatch(
      /\.sector-change ol\s*\{[^}]*grid-template-columns:\s*repeat\(2/s,
    );
    expect(css).toMatch(
      /\.opportunity-table__rank button\s*\{[^}]*min-height:\s*44px/s,
    );
    expect(css).toMatch(
      /\.opportunity-mobile-list__expand\s*\{[^}]*min-height:\s*44px/s,
    );
    expect(css).toMatch(
      /@media \(max-width:\s*1599px\) and \(min-width:\s*1024px\)[\s\S]*\.opportunity-table--research[^}]*nth-child\(6\)[\s\S]*display:\s*none/s,
    );
    expect(css).toMatch(
      /@media \(max-width:\s*1023px\)[\s\S]*\.opportunity-table__scroll\s*\{[^}]*display:\s*none[\s\S]*\.opportunity-mobile-list\s*\{[^}]*display:\s*grid/s,
    );
    expect(css).toMatch(
      /\.regime-state--uptrend\s*\{[^}]*--regime-color:\s*var\(--color-up\)/s,
    );
    expect(css).toMatch(
      /\.regime-state--range\s*\{[^}]*--regime-color:\s*var\(--color-text-secondary\)/s,
    );
    expect(css).toMatch(
      /\.regime-state--transition\s*\{[^}]*--regime-color:\s*var\(--color-warning\)/s,
    );
    expect(css).toMatch(
      /\.regime-state--downtrend\s*\{[^}]*--regime-color:\s*var\(--color-down\)/s,
    );
    expect(css).toMatch(/\.overview-terminal-chart__canvas\s*\{[^}]*height:\s*240px/s);
    expect(css).toMatch(
      /\.market-structure-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/s,
    );
    expect(css).not.toMatch(
      /@media \(min-width:\s*1600px\)[\s\S]*\.market-structure-grid\s*\{[^}]*repeat\(4,\s*minmax\(0,\s*1fr\)\)/s,
    );
    expect(css).toMatch(
      /@media \(max-width:\s*767px\)[\s\S]*\.market-structure-grid\s*\{[^}]*grid-template-columns:\s*1fr/s,
    );
    expect(css).not.toMatch(
      /\.overview-terminal-chart__canvas\s*\{[^}]*height:\s*82px/s,
    );
    expect(css).not.toMatch(/\.anomaly-workbench-grid\s*\{[^}]*max-height:\s*185px/s);
    expect(css).not.toMatch(
      /\.opportunity-table--research\s*\{[^}]*min-width:\s*1120px/s,
    );
  });
});
