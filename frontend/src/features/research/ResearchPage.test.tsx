import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ResearchApiError } from "../../api/client";
import type { components, paths } from "../../api/generated/schema";
import { PriceHistoryChart } from "./PriceHistoryChart";
import { ResearchPage } from "./ResearchPage";
import { I18nProvider, useI18n } from "../../i18n/context";

const chartHarness = vi.hoisted(() => {
  const makeScale = () => ({
    fitContent: vi.fn(),
    setVisibleLogicalRange: vi.fn(),
    timeToCoordinate: vi.fn((time: number) => time),
    subscribeVisibleLogicalRangeChange: vi.fn<(callback: () => void) => void>(),
    unsubscribeVisibleLogicalRangeChange: vi.fn(),
    subscribeSizeChange: vi.fn<(callback: () => void) => void>(),
    unsubscribeSizeChange: vi.fn(),
  });
  const charts: Array<{
    addSeries: ReturnType<typeof vi.fn>;
    applyOptions: ReturnType<typeof vi.fn>;
    remove: ReturnType<typeof vi.fn>;
    resize: ReturnType<typeof vi.fn>;
    timeScale: ReturnType<typeof vi.fn<() => ReturnType<typeof makeScale>>>;
  }> = [];
  const series: Array<{ setData: ReturnType<typeof vi.fn> }> = [];
  const markerApis: Array<{
    setMarkers: ReturnType<typeof vi.fn<(markers: Array<{ text: string }>) => void>>;
  }> = [];
  const createChart = vi.fn(() => {
    const chart = {
      addSeries: vi.fn(() => {
        const item = {
          attachPrimitive: vi.fn(),
          priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
          setData: vi.fn(),
        };
        series.push(item);
        return item;
      }),
      subscribeCrosshairMove: vi.fn(),
      applyOptions: vi.fn(),
      remove: vi.fn(),
      resize: vi.fn(),
      timeScale: vi.fn(makeScale),
    };
    charts.push(chart);
    return chart;
  });
  const createSeriesMarkers = vi.fn(() => {
    const api = { setMarkers: vi.fn() };
    markerApis.push(api);
    return api;
  });
  return { charts, createChart, createSeriesMarkers, markerApis, series };
});

vi.mock("lightweight-charts", () => ({
  CandlestickSeries: "CandlestickSeries",
  ColorType: { Solid: "Solid" },
  HistogramSeries: "HistogramSeries",
  LineSeries: "LineSeries",
  createChart: chartHarness.createChart,
  createSeriesMarkers: chartHarness.createSeriesMarkers,
}));

type ForecastResponse =
  components["schemas"]["ResearchResponse_Annotated_Union_ForecastAvailable__ForecastUnavailable___FieldInfo_annotation_NoneType__required_True__discriminator__availability____"];
type ForecastAvailable = components["schemas"]["ForecastAvailable"];
type ForecastUnavailable = components["schemas"]["ForecastUnavailable"];
type HistoryResponse = components["schemas"]["ResearchResponse_InstrumentHistory_"];
type HistoryBar = components["schemas"]["HistoryBar"];
type HorizonForecast = components["schemas"]["HorizonForecast"];
type HistoryQuery = NonNullable<
  paths["/api/v1/instruments/{symbol}/history"]["get"]["parameters"]["query"]
>;

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

const SNAPSHOT_B = {
  as_of: "2026-07-09T20:05:00Z",
  calibration_version: "CAL-2026-07-07",
  dataset_revision: "bars-r41",
  feature_set_version: "features-v6",
  model_version: "SL-2.6.0",
  prediction_readiness: "forecast_ready",
  research_readiness: "full_verified",
  revision_no: 2,
  snapshot_id: "SL-20260709-1605",
  universe_version: "us-100-2026q2",
} satisfies components["schemas"]["ResolvedSnapshot"];

const EVIDENCE = {
  calibration_evidence: {
    calibration_error: 0.031,
    cqr_average_width: 0.184,
    cqr_empirical_coverage: 0.91,
    probability_bin_sample_count: 412,
  },
  data_quality_score: 0.97,
  evidence_cutoff: "2026-07-10T20:00:00Z",
  missing_rate: 0.012,
  model_disagreement: 0.14,
  model_disagreement_summary: "tree and sequence families agree within threshold",
  ood_method: "robust-mahalanobis",
  ood_score: 0.32,
  ood_threshold: 0.7,
  regime_model_version: "REG-4.2",
  regime_probabilities: {
    downtrend: 0.1,
    range: 0.17,
    transition: 0.08,
    uptrend: 0.65,
  },
  regime_probabilities_unavailable_reason: null,
  similar_case_summary: {
    breakout_rate: 0.58,
    effective_independent_count: 18.4,
    insufficient_evidence: false,
    median_distance: 0.21,
    return_quantiles: { q10: -0.08, q50: 0.035, q90: 0.16 },
    win_rate: 0.62,
    worst_mae_case_id: "case-worst",
  },
  top_feature_families: [
    { contribution: 0.22, family: "trend", training_percentile: 0.81 },
    { contribution: -0.11, family: "liquidity", training_percentile: 0.34 },
  ],
} satisfies components["schemas"]["ForecastEvidence"];

function quantiles(scale: number) {
  return {
    q10: -0.1 * scale,
    q25: -0.035 * scale,
    q50: 0.025 * scale,
    q75: 0.08 * scale,
    q90: 0.16 * scale,
  };
}

function horizonForecast(horizon: 1 | 5 | 20): HorizonForecast {
  const scale = horizon === 1 ? 0.25 : horizon === 5 ? 0.55 : 1;
  return {
    adjustment_mode: "split_adjusted",
    breakout_probabilities: { down: 0.14, none: 0.41, up: 0.45 },
    calibrated_return_intervals: [
      {
        calibration_version: SNAPSHOT.calibration_version,
        coverage_target: 0.8,
        lower: -0.11 * scale,
        method: "CQR",
        return_basis: "executable",
        upper: 0.17 * scale,
      },
    ],
    capital_gain_return_quantiles: quantiles(scale),
    coverage_target: 0.8,
    currency: "USD",
    executable_direction_probabilities: { down: 0.31, flat: 0.18, up: 0.51 },
    executable_mae_quantiles: quantiles(scale * -0.5),
    executable_mfe_quantiles: quantiles(scale * 0.7),
    executable_return_quantiles: quantiles(scale * 0.9),
    forecast_direction_probabilities: { down: 0.21, flat: 0.16, up: 0.63 },
    forecast_mae_quantiles: quantiles(scale * -0.45),
    forecast_mfe_quantiles: quantiles(scale * 0.8),
    horizon,
    interval_method: "CQR",
    price_band: null,
    price_band_unavailable_reason: "single target prices are not served",
    range_exit_hazard_curve:
      horizon === 1
        ? []
        : [
            {
              cumulative_probability: 0.18,
              hazard_probability: 0.18,
              trading_day: 2,
            },
          ],
    range_exit_metadata: {
      censoring_rule: "two-close confirmation",
      confirmation_days: 2,
      not_generated_reason:
        horizon === 1 ? "two-day confirmation exceeds the one-day horizon" : null,
      sample_denominator: 412,
    },
    range_exit_probability: horizon === 1 ? null : 0.44,
    reference_price: 146.8,
    reference_price_at: SNAPSHOT.as_of,
    total_return_quantiles: quantiles(scale * 1.02),
  };
}

const HORIZONS = [horizonForecast(1), horizonForecast(5), horizonForecast(20)] as const;

function availableData(overrides: Partial<ForecastAvailable> = {}): ForecastAvailable {
  const bundle = {
    abstain: false,
    abstain_reasons: [],
    as_of: SNAPSHOT.as_of,
    calibration_version: SNAPSHOT.calibration_version,
    confidence_level: "high" as const,
    confidence_score: 0.86,
    current_event_flags: ["earnings-window"],
    current_regime: "uptrend" as const,
    data_freshness_seconds: 18,
    data_quality_status: "current" as const,
    dataset_revision: SNAPSHOT.dataset_revision,
    distance_version: "distance-v3",
    evidence: EVIDENCE,
    feature_set_version: SNAPSHOT.feature_set_version,
    generated_at: "2026-07-10T20:05:04Z",
    horizons: [...HORIZONS],
    inference_latency_ms: 48,
    inference_latency_scope: "forecast-bundle",
    invalidation_conditions: [
      "new corporate action",
      "data freshness exceeds 900 seconds",
    ],
    label_version: "labels-v6",
    model_disagreement: EVIDENCE.model_disagreement,
    model_version: SNAPSHOT.model_version,
    ood_score: EVIDENCE.ood_score,
    regime_probabilities: EVIDENCE.regime_probabilities,
    regime_probabilities_unavailable_reason: null,
    revision_no: SNAPSHOT.revision_no,
    similar_case_summary: EVIDENCE.similar_case_summary,
    snapshot_id: SNAPSHOT.snapshot_id,
    symbol: "US.NVDA",
    universe_version: SNAPSHOT.universe_version,
    valid_until: "2026-07-10T20:20:00Z",
  } satisfies components["schemas"]["ForecastBundle"];

  return {
    availability: "available",
    bundle,
    evidence: EVIDENCE,
    selected_horizon: HORIZONS[1],
    serving: {
      effective_abstain: bundle.abstain,
      evaluated_at: "2026-07-10T20:05:04Z",
      exclusion_reason: bundle.abstain_reasons,
      serving_status: "live",
    },
    similar_cases: [
      {
        as_of: "2025-10-14T20:00:00Z",
        breakout_result: "up",
        case_id: "case-1",
        distance: 0.12,
        mae: -0.09,
        regime: "uptrend",
        return_quantiles: { q10: -0.04, q50: 0.06, q90: 0.19 },
        worst_case: false,
      },
      {
        as_of: "2024-08-05T20:00:00Z",
        breakout_result: "down",
        case_id: "case-worst",
        distance: 0.28,
        mae: -0.21,
        regime: "transition",
        return_quantiles: { q10: -0.18, q50: -0.03, q90: 0.08 },
        worst_case: true,
      },
    ],
    ...overrides,
  };
}

function forecastResponse(
  data: ForecastAvailable | ForecastUnavailable,
): ForecastResponse {
  return {
    data,
    data_status: data.availability === "available" ? "ready" : "degraded",
    effective_abstain:
      data.availability === "available" ? data.serving.effective_abstain : true,
    evaluated_at: "2026-07-10T20:05:04Z",
    exclusion_reason:
      data.availability === "available"
        ? data.serving.exclusion_reason
        : [data.not_generated_reason],
    generated_at: "2026-07-10T20:05:04Z",
    request_id: "request-forecast-1",
    resolved_snapshot: SNAPSHOT,
    serving_status:
      data.availability === "available"
        ? data.serving.serving_status
        : data.serving_status,
  };
}

function makeBars(): HistoryBar[] {
  return Array.from({ length: 24 }, (_, index) => {
    const date = new Date(Date.UTC(2026, 5, index + 1)).toISOString();
    const baseline = 120 + index;
    const close = index === 21 ? 158 : index === 22 ? 141 : baseline + 0.5;
    return {
      at: date,
      close,
      data_gap: index === 9,
      high: Math.max(baseline + 1.5, close + 0.5),
      low: Math.min(baseline - 1, close - 0.5),
      open: baseline,
      regime: index < 12 ? "range" : "uptrend",
      volume: 1_000_000 + index * 25_000,
    };
  });
}

function historyResponse(
  bars = makeBars(),
  snapshot: components["schemas"]["ResolvedSnapshot"] = SNAPSHOT,
): HistoryResponse {
  return {
    data: {
      adjustment: "split_adjusted",
      bars,
      events: [
        {
          details: ["EPS release after close"],
          event_id: "event-earnings",
          event_type: "earnings",
          label: "Earnings",
          occurred_at: bars[15]?.at ?? "2026-06-16T00:00:00Z",
        },
      ],
      gaps: [
        {
          end_at: bars[10]?.at ?? "2026-06-11T00:00:00Z",
          reason: "vendor outage",
          start_at: bars[9]?.at ?? "2026-06-10T00:00:00Z",
        },
      ],
      symbol: "US.NVDA",
      timeframe: "1d",
    },
    data_status: "ready",
    effective_abstain: false,
    evaluated_at: "2026-07-10T20:05:04Z",
    exclusion_reason: [],
    generated_at: "2026-07-10T20:05:04Z",
    request_id: "request-history-1",
    resolved_snapshot: snapshot,
    serving_status: "live",
  };
}

function unavailableData(
  overrides: Partial<ForecastUnavailable> = {},
): ForecastUnavailable {
  return {
    affected_inputs: ["daily-bars", "feature-bundle"],
    as_of: SNAPSHOT.as_of,
    availability: "unavailable",
    effective_abstain: true,
    last_available_snapshot: null,
    not_generated_reason: "feature_artifact_mismatch",
    requested_horizon: 5,
    serving_status: "unavailable",
    symbol: "US.NVDA",
    verified_research: [],
    challenger_preview: [],
    ...overrides,
  };
}

function verifiedResearch(
  horizon: 1 | 5 | 20,
): components["schemas"]["ResearchModelEvidence"] {
  return {
    as_of: SNAPSHOT.as_of,
    horizon,
    price_projection: {
      currency: "USD",
      endpoint_price_band: {
        q10: 140 + horizon,
        q25: 142 + horizon,
        q50: 146 + horizon,
        q75: 149 + horizon,
        q90: 152 + horizon,
      },
      endpoint_price_unavailable_reason: null,
      reference_price: 146.8,
      reference_price_at: SNAPSHOT.as_of,
      stage_high_price_q50: null,
      stage_high_price_unavailable_reason: "not_published",
      stage_low_price_q50: null,
      stage_low_price_unavailable_reason: "not_published",
    },
    sample_abstain_reasons: [],
    scope: "verified",
    symbol: "US.NVDA",
    tasks: [],
    values: {
      breakout_down_probability: 0.18,
      breakout_up_probability: 0.42,
      capital_gain_q10: -0.04,
      capital_gain_q50: 0.01 * horizon,
      capital_gain_q90: 0.08,
    },
  };
}

function createClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 60_000 } },
  });
}

function ChartContext({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={createClient()}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

function deferred<T>() {
  let resolvePromise!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise };
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location-search">{location.search}</output>;
}

interface RenderOptions {
  entry?: string;
  forecast?: ForecastResponse;
  forecastError?: Error;
  history?: HistoryResponse;
  historyLoader?: (symbol: string, query: HistoryQuery) => Promise<HistoryResponse>;
}

function renderResearch({
  entry = "/research/US.NVDA?horizon=5&timeframe=1d&adjustment=split_adjusted",
  forecast = forecastResponse(availableData()),
  forecastError,
  history = historyResponse(),
  historyLoader,
}: RenderOptions = {}) {
  const loadForecast = vi.fn(() =>
    forecastError === undefined
      ? Promise.resolve(forecast)
      : Promise.reject(forecastError),
  );
  const loadHistory = vi.fn(historyLoader ?? (() => Promise.resolve(history)));
  render(
    <QueryClientProvider client={createClient()}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route
            path="/research/:symbol"
            element={
              <ResearchPage loadForecast={loadForecast} loadHistory={loadHistory} />
            }
          />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { loadForecast, loadHistory };
}

const resizeObservers: ResizeObserverStub[] = [];

class ResizeObserverStub {
  readonly callback: ResizeObserverCallback;
  disconnect = vi.fn();
  observe = vi.fn();
  unobserve = vi.fn();

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    resizeObservers.push(this);
  }

  trigger(target: Element, width: number) {
    this.callback(
      [
        {
          contentRect: { width },
          target,
        } as ResizeObserverEntry,
      ],
      this,
    );
  }
}

beforeEach(() => {
  chartHarness.charts.length = 0;
  chartHarness.series.length = 0;
  chartHarness.markerApis.length = 0;
  chartHarness.createChart.mockClear();
  chartHarness.createSeriesMarkers.mockClear();
  resizeObservers.length = 0;
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("instrument research forecast states", () => {
  test("does not offer history retry for a non-retryable API error", async () => {
    renderResearch({
      historyLoader: () =>
        Promise.reject(
          new ResearchApiError({
            kind: "http",
            status: 404,
            code: "history_not_found",
            message: "Missing history",
            requestId: "history-not-found-test",
            retryable: false,
            affectedScope: ["history"],
          }),
        ),
    });
    expect(await screen.findByText(/价格历史暂不可用/)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "重试价格历史" }),
    ).not.toBeInTheDocument();
  });
  test("retries failed history independently of forecast evidence", async () => {
    let failed = true;
    const { loadForecast } = renderResearch({
      historyLoader: () =>
        failed
          ? Promise.reject(new Error("history unavailable"))
          : Promise.resolve(historyResponse()),
    });
    const retry = await screen.findByRole("button", { name: "重试价格历史" });
    const forecastCalls = loadForecast.mock.calls.length;
    failed = false;
    await userEvent.setup().click(retry);
    expect(
      await screen.findByRole("region", { name: "US.NVDA 价格历史" }),
    ).toBeVisible();
    expect(loadForecast).toHaveBeenCalledTimes(forecastCalls);
    expect(screen.getByRole("combobox", { name: "价格图显示范围" })).toHaveFocus();
    expect(
      screen.queryByRole("button", { name: "重试价格历史" }),
    ).not.toBeInTheDocument();
  });
  test("history recovery preserves focus moved to another control", async () => {
    const recovery = deferred<HistoryResponse>();
    let recovering = false;
    renderResearch({
      entry: `/research/US.NVDA?${new URLSearchParams({
        snapshot_id: SNAPSHOT.snapshot_id,
        as_of: SNAPSHOT.as_of,
        revision_no: String(SNAPSHOT.revision_no),
        dataset_revision: SNAPSHOT.dataset_revision,
        feature_set_version: SNAPSHOT.feature_set_version,
        universe_version: SNAPSHOT.universe_version,
        model_version: SNAPSHOT.model_version,
        calibration_version: SNAPSHOT.calibration_version,
        horizon: "5",
        timeframe: "1d",
        adjustment: "split_adjusted",
      })}`,

      historyLoader: () =>
        recovering
          ? recovery.promise
          : Promise.reject(new Error("history unavailable")),
    });
    const retry = await screen.findByRole("button", { name: "重试价格历史" });
    recovering = true;
    await userEvent.setup().click(retry);
    expect(
      await screen.findByRole("status", { name: /(正在|重新|等待).*价格历史/ }),
    ).toBeVisible();
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "重试价格历史" }),
      ).not.toBeInTheDocument(),
    );
    const otherControl = screen.getByRole("link", { name: "查看历史 OOS 表现摘要" });
    otherControl.focus();
    expect(otherControl).toHaveFocus();
    await act(async () => {
      recovery.resolve(historyResponse());
      await recovery.promise;
    });
    expect(
      await screen.findByRole("combobox", { name: "价格图显示范围" }),
    ).toBeVisible();
    expect(otherControl).toHaveFocus();
  });
  test("keeps independent price history visible when forecast evidence fails", async () => {
    renderResearch({ forecastError: new Error("forecast unavailable") });
    expect(await screen.findByText(/预测证据读取失败/)).toBeVisible();
    expect(
      await screen.findByRole("region", { name: "US.NVDA 价格历史" }),
    ).toBeVisible();
    expect(
      screen.getByText(new RegExp(`行情快照：${SNAPSHOT.snapshot_id}`)),
    ).toBeVisible();
    expect(screen.queryByTestId("probability-node")).not.toBeInTheDocument();
  });
  test("renders available non-abstain evidence as three discrete horizon groups", async () => {
    const { loadForecast, loadHistory } = renderResearch();

    expect(
      await screen.findByRole("heading", { name: "US.NVDA 标的研究室", level: 1 }),
    ).toBeVisible();
    expect((await screen.findAllByTestId("probability-node")).length).toBeGreaterThan(
      0,
    );
    expect(screen.getByRole("region", { name: "1 个交易日预测" })).toBeVisible();
    expect(screen.getByRole("region", { name: "5 个交易日预测" })).toBeVisible();
    expect(screen.getByRole("region", { name: "20 个交易日预测" })).toBeVisible();
    expect(screen.getByText("原始分位数与 CQR 校准区间")).toBeVisible();
    expect(screen.getAllByText("CAL-2026-07-08").length).toBeGreaterThan(0);
    expect(screen.getByText("关联性解释，不代表因果关系")).toBeVisible();
    expect(screen.getByRole("table", { name: "历史相似案例" })).toBeVisible();
    expect(screen.getByRole("link", { name: "查看历史 OOS 表现摘要" })).toHaveAttribute(
      "href",
      expect.stringContaining("/backtests"),
    );
    expect(screen.getByText(/q50 不是保证目标价/)).toBeVisible();
    expect(
      screen.queryByRole("columnheader", { name: "目标价" }),
    ).not.toBeInTheDocument();
    expect(loadForecast).toHaveBeenCalledWith(
      "US.NVDA",
      expect.objectContaining({ horizon: 5, include: "evidence,similar_cases" }),
    );
    expect(loadHistory).toHaveBeenCalledWith(
      "US.NVDA",
      expect.objectContaining({ adjustment: "raw", timeframe: "1d" }),
    );
    expect(screen.getByTestId("location-search")).not.toHaveTextContent(
      "adjustment=split_adjusted",
    );
  });

  test("degrades legacy unavailable payloads without research arrays to empty evidence", async () => {
    const legacy = unavailableData() as unknown as Record<string, unknown>;
    delete legacy.verified_research;
    delete legacy.challenger_preview;

    renderResearch({
      forecast: forecastResponse(legacy as unknown as ForecastUnavailable),
    });

    expect(await screen.findByText("模型门禁诊断")).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "已验证研究证据" }),
    ).not.toBeInTheDocument();
  });

  test("persists the resolved snapshot identity into reproducible URL state", async () => {
    renderResearch({ entry: "/research/US.NVDA?horizon=20" });

    await screen.findAllByTestId("probability-node");
    await waitFor(() => {
      expect(screen.getByTestId("location-search")).toHaveTextContent(
        `snapshot_id=${SNAPSHOT.snapshot_id}`,
      );
      expect(screen.getByTestId("location-search")).toHaveTextContent(
        `dataset_revision=${SNAPSHOT.dataset_revision}`,
      );
      expect(screen.getByTestId("location-search")).toHaveTextContent(
        `model_version=${SNAPSHOT.model_version}`,
      );
      expect(screen.getByTestId("location-search")).toHaveTextContent("horizon=20");
    });
  });

  test("does not migrate mismatched history into the forecast snapshot cache key", async () => {
    const staleBars = makeBars();
    staleBars[staleBars.length - 1] = {
      ...staleBars.at(-1)!,
      close: 999,
      high: 1000,
      low: 998,
      open: 998.5,
    };
    const historyLoader = vi.fn((_symbol: string, query: HistoryQuery) =>
      Promise.resolve(
        query.snapshot_id === SNAPSHOT.snapshot_id
          ? historyResponse(makeBars(), SNAPSHOT)
          : historyResponse(staleBars, SNAPSHOT_B),
      ),
    );

    renderResearch({ historyLoader });

    await waitFor(() => expect(historyLoader).toHaveBeenCalledTimes(2));
    expect(historyLoader.mock.calls[0]?.[1].snapshot_id).toBeUndefined();
    expect(historyLoader.mock.calls[1]?.[1]).toMatchObject({
      as_of: SNAPSHOT.as_of,
      calibration_version: SNAPSHOT.calibration_version,
      dataset_revision: SNAPSHOT.dataset_revision,
      feature_set_version: SNAPSHOT.feature_set_version,
      model_version: SNAPSHOT.model_version,
      snapshot_id: SNAPSHOT.snapshot_id,
      universe_version: SNAPSHOT.universe_version,
    });
    await waitFor(() => {
      expect(screen.getByRole("table", { name: "图表末值摘要" })).not.toHaveTextContent(
        "999.00",
      );
    });
  });

  test.each([
    {
      branch: "available",
      forecast: forecastResponse(availableData()),
    },
    {
      branch: "unavailable",
      forecast: forecastResponse(unavailableData()),
    },
  ] satisfies Array<{ branch: string; forecast: ForecastResponse }>)(
    "keeps mismatched history out of the $branch branch until pinned history resolves",
    async ({ forecast }) => {
      const staleBars = makeBars();
      staleBars[staleBars.length - 1] = {
        ...staleBars.at(-1)!,
        close: 999,
        high: 1000,
        low: 998,
        open: 998.5,
      };
      const mismatchedHistory = historyResponse(staleBars, SNAPSHOT_B);
      mismatchedHistory.data.events = [
        {
          ...mismatchedHistory.data.events[0]!,
          label: "B-only evidence",
        },
      ];
      const pinnedHistory = deferred<HistoryResponse>();
      const historyLoader = vi.fn((_symbol: string, query: HistoryQuery) =>
        query.snapshot_id === SNAPSHOT.snapshot_id
          ? pinnedHistory.promise
          : Promise.resolve(mismatchedHistory),
      );
      const observedEvidence: string[] = [];
      const collectEvidence = (records: MutationRecord[]) => {
        records.forEach((record) => {
          record.addedNodes.forEach((node) => {
            observedEvidence.push(node.textContent ?? "");
          });
        });
      };
      const mutationObserver = new MutationObserver(collectEvidence);
      mutationObserver.observe(document.body, { childList: true, subtree: true });

      try {
        renderResearch({ forecast, historyLoader });

        await waitFor(() => expect(historyLoader).toHaveBeenCalledTimes(2));
        collectEvidence(mutationObserver.takeRecords());
        expect(screen.queryByText("999.00")).not.toBeInTheDocument();
        expect(screen.queryByText("B-only evidence")).not.toBeInTheDocument();
        expect(observedEvidence.join(" ")).not.toContain("999.00");
        expect(observedEvidence.join(" ")).not.toContain("B-only evidence");
        expect(chartHarness.createChart).not.toHaveBeenCalled();
        expect(
          screen.getByRole("status", {
            name: "正在等待固定快照价格历史",
          }),
        ).toBeVisible();

        pinnedHistory.resolve(historyResponse(makeBars(), SNAPSHOT));
        expect(
          await screen.findByRole("table", { name: "图表末值摘要" }),
        ).toHaveTextContent("143.50");
        expect(screen.queryByText("999.00")).not.toBeInTheDocument();
        expect(screen.queryByText("B-only evidence")).not.toBeInTheDocument();
        expect(chartHarness.createChart).toHaveBeenCalledTimes(1);
      } finally {
        mutationObserver.disconnect();
      }
    },
  );

  test("exposes a desktop main-evidence and right-diagnostics structure with risk first in mobile DOM order", async () => {
    renderResearch();

    const layout = await screen.findByTestId("research-workbench-layout");
    expect(layout).toHaveAttribute(
      "data-layout",
      "main-evidence-with-right-diagnostics",
    );
    expect(layout).toHaveClass("research-workbench-grid--responsive");
    expect(layout.children[0]).toHaveAttribute("data-mobile-priority", "key-risk");
    expect(layout.children[1]).toHaveClass("research-workbench-grid__main");
    expect(layout.children[2]).toHaveClass("research-workbench-grid__diagnostics");

    const css = readFileSync(
      resolve(process.cwd(), "frontend/src/features/research/research.css"),
      "utf8",
    );
    expect(css).toMatch(
      /\.research-workbench-grid\s*\{[^}]*grid-template-areas:\s*"main risk"\s*"main diagnostics"[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+minmax\(320px,\s*380px\)/s,
    );
    expect(css).toMatch(/\.research-workbench-grid__risk\s*\{[^}]*grid-area:\s*risk/s);
    expect(css).toMatch(/\.research-workbench-grid__main\s*\{[^}]*grid-area:\s*main/s);
    expect(css).toMatch(
      /\.research-workbench-grid__diagnostics\s*\{[^}]*grid-area:\s*diagnostics/s,
    );
    expect(css).toMatch(
      /@media \(max-width:\s*1023px\)[\s\S]*\.research-workbench-grid\s*\{[^}]*grid-template-areas:\s*"risk"\s*"main"\s*"diagnostics"[^}]*grid-template-columns:\s*1fr/s,
    );
  });

  test("keeps available probabilities but marks an abstained forecast as non-executable", async () => {
    const data = availableData();
    data.bundle = {
      ...data.bundle,
      abstain: true,
      abstain_reasons: ["out_of_distribution", "model_disagreement"],
      confidence_level: "low",
      data_freshness_seconds: 902,
    };
    data.serving = {
      effective_abstain: true,
      evaluated_at: "2026-07-10T20:05:04Z",
      exclusion_reason: ["out_of_distribution", "model_disagreement"],
      serving_status: "partial",
    };
    renderResearch({ forecast: forecastResponse(data) });

    expect(
      await screen.findByRole("heading", { name: "本次预测已拒绝执行" }),
    ).toBeVisible();
    const reasons = screen.getByRole("list", { name: "全部拒绝原因" });
    expect(reasons).toHaveTextContent("分布外样本");
    expect(reasons).toHaveTextContent("模型分歧过大");
    expect(screen.getAllByTestId("probability-node").length).toBeGreaterThan(0);
    expect(screen.getByText(/902 秒/)).not.toBeVisible();
    await userEvent.setup().click(screen.getByText("查看置信、校准与失效明细"));
    expect(screen.getByText(/902 秒/)).toBeVisible();
    expect(screen.getByText(/0.320/)).toBeVisible();
    expect(screen.getByText(/阈值 0.700/)).toBeVisible();
    expect(screen.getByText("新增公司行动")).toBeVisible();
  });

  test("renders a pre-gate unavailable state without any probability nodes", async () => {
    renderResearch({ forecast: forecastResponse(unavailableData()) });

    await userEvent.setup().click(await screen.findByText("模型门禁诊断"));
    expect(await screen.findByRole("heading", { name: "预测未生成" })).toBeVisible();
    expect(screen.getAllByText("特征制品版本不一致")[0]).toBeVisible();
    expect(screen.getAllByTitle("feature_artifact_mismatch")[0]).toBeVisible();
    expect(screen.getByRole("list", { name: "受影响输入" })).toHaveTextContent(
      "daily-bars",
    );
    expect(screen.queryByTestId("probability-node")).not.toBeInTheDocument();
  });

  test("keeps verified research primary and separates market freshness from formal readiness", async () => {
    renderResearch({
      forecast: forecastResponse(
        unavailableData({
          not_generated_reason: "no_champion_candidate",
          verified_research: [
            verifiedResearch(1),
            verifiedResearch(5),
            verifiedResearch(20),
          ],
        }),
      ),
    });

    const ledger = await screen.findByRole("region", { name: "已验证研究证据" });
    expect(within(ledger).getByText(/已验证研究\s*[；;]\s*不可执行$/)).toBeVisible();
    const status = screen.getByRole("status", { name: "研究与正式模型状态" });
    expect(status).toHaveTextContent("行情历史");
    expect(status).toHaveTextContent("历史已覆盖至");
    expect(status).toHaveTextContent("24 根");
    expect(status).toHaveTextContent("可用");
    expect(status).toHaveTextContent("正式模型");
    expect(status).toHaveTextContent("未就绪");
    expect(screen.queryByText("数据不可用")).not.toBeInTheDocument();

    const diagnostics = screen.getByText("模型门禁诊断").closest("details");
    expect(diagnostics).not.toHaveAttribute("open");
    expect(status.compareDocumentPosition(ledger)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(
      screen.getByText("模型运行与治理详情").closest("details"),
    ).not.toHaveAttribute("open");
  });

  test("reports factual history coverage when aggregate serving status is unavailable", async () => {
    const history = historyResponse();
    history.serving_status = "unavailable";
    history.data_status = "degraded";
    renderResearch({
      forecast: forecastResponse(
        unavailableData({
          not_generated_reason: "no_champion_candidate",
          verified_research: [verifiedResearch(5)],
        }),
      ),
      history,
    });

    const status = await screen.findByRole("status", {
      name: "研究与正式模型状态",
    });
    const market = within(status).getByTestId("market-history-coverage");
    expect(market).toHaveTextContent("历史已覆盖至 交易日 2026-06-24");
    expect(within(market).getByTitle("2026-06-24T00:00:00.000Z")).toBeVisible();
    expect(market).toHaveTextContent("24 根");
    expect(market).not.toHaveTextContent("不可用");
    const aggregate = within(status).getByTestId("aggregate-platform-status");
    expect(aggregate).toHaveTextContent("平台聚合状态");
    expect(aggregate).toHaveTextContent("不可用");
  });

  test("offers canonical ETF routes in SPY, DIA, QQQ, IWM order and preserves snapshot state", async () => {
    renderResearch({ entry: "/research/US.NVDA?horizon=20" });

    const navigation = await screen.findByRole("navigation", {
      name: "研究快速切换与返回",
    });
    const links = within(navigation)
      .getAllByRole("link")
      .filter((link) => ["SPY", "DIA", "QQQ", "IWM"].includes(link.textContent ?? ""));
    expect(links.map((link) => link.textContent)).toEqual(["SPY", "DIA", "QQQ", "IWM"]);
    await waitFor(() => {
      expect(links[0]).toHaveAttribute(
        "href",
        expect.stringContaining(`snapshot_id=${SNAPSHOT.snapshot_id}`),
      );
    });
    const targets = links.map(
      (link) => new URL(link.getAttribute("href")!, "https://signal.test"),
    );
    expect(targets.map((target) => target.pathname)).toEqual([
      "/research/US.SPY",
      "/research/US.DIA",
      "/research/US.QQQ",
      "/research/US.IWM",
    ]);
    for (const target of targets) {
      expect(target.searchParams.get("snapshot_id")).toBe(SNAPSHOT.snapshot_id);
      expect(target.searchParams.get("dataset_revision")).toBe(
        SNAPSHOT.dataset_revision,
      );
      expect(target.searchParams.get("horizon")).toBe("20");
      expect(target.searchParams.has("symbol")).toBe(false);
    }
  });

  test("distinguishes a stale last snapshot without reviving its probabilities", async () => {
    renderResearch({
      forecast: forecastResponse(
        unavailableData({
          last_available_snapshot: SNAPSHOT,
          not_generated_reason: "stale_data",
          serving_status: "stale",
        }),
      ),
    });

    await userEvent.setup().click(await screen.findByText("模型门禁诊断"));
    expect(
      await screen.findByRole("heading", { name: "仅显示最后可用快照" }),
    ).toBeVisible();
    expect(screen.getByText("SL-20260710-1605")).toBeVisible();
    expect(
      within(screen.getByRole("region", { name: "预测不可用证据" })).getByText(
        "2026-07-10T20:05:00Z",
      ),
    ).toBeVisible();
    expect(screen.queryByTestId("probability-node")).not.toBeInTheDocument();
  });

  test("shows an unknown-symbol state for a typed API 404", async () => {
    renderResearch({
      entry: "/research/US.UNKNOWN",
      forecastError: new ResearchApiError({
        affectedScope: ["US.UNKNOWN"],
        code: "instrument_not_found",
        kind: "http",
        message: "instrument was not found",
        requestId: "request-404",
        retryable: false,
        status: 404,
      }),
    });

    expect(await screen.findByRole("heading", { name: "未找到标的" })).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("US.UNKNOWN");
    expect(screen.queryByTestId("probability-node")).not.toBeInTheDocument();
  });

  test.each([
    [true, false],
    [false, false],
    [true, true],
  ])(
    "identity respects pinned snapshot (matches=%s, forecastFails=%s)",
    async (matches, forecastFails) => {
      const requests: string[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn((request: Request) => {
          requests.push(request.url);
          if (!new URL(request.url).pathname.endsWith("/instruments/US.NVDA")) {
            return Promise.resolve(new Response("{}", { status: 503 }));
          }
          return Promise.resolve(
            new Response(
              JSON.stringify({
                resolved_snapshot: {
                  ...SNAPSHOT,
                  snapshot_id: matches ? SNAPSHOT.snapshot_id : "wrong-snapshot",
                },
                data: {
                  symbol: "US.NVDA",
                  name: "NVIDIA",
                  asset_type: "stock",
                  sector: "technology",
                  currency: "USD",
                  exchange: "US",
                },
              }),
              { headers: { "content-type": "application/json" } },
            ),
          );
        }),
      );
      renderResearch(
        forecastFails ? { forecastError: new Error("forecast unavailable") } : {},
      );
      const identity = await screen.findByRole("region", { name: "标的基础信息" });
      if (matches) {
        await waitFor(() => expect(identity).toHaveTextContent("英伟达"));
        expect(identity).toHaveTextContent("币种：USD");
        expect(identity).toHaveTextContent("市场/交易所源代码：US");
      } else {
        await waitFor(() => expect(identity).toHaveTextContent("基础信息不可用"));
        expect(identity).not.toHaveTextContent("NVIDIA");
      }
      expect(
        requests.some(
          (url) =>
            new URL(url).searchParams.get("snapshot_id") === SNAPSHOT.snapshot_id,
        ),
      ).toBe(true);
    },
  );

  test("empty event and gap records do not assert market completeness", () => {
    render(
      <PriceHistoryChart
        history={{ ...historyResponse().data, events: [], gaps: [] }}
        symbol="US.NVDA"
      />,
      { wrapper: ChartContext },
    );
    expect(
      screen.getByText("当前快照未提供缺口记录，不能据此确认行情完整。"),
    ).toBeVisible();
    expect(screen.queryByText("没有拆股、分红或其他事件")).not.toBeInTheDocument();
  });

  test("exposes chart gaps and essential last values without precise dragging", async () => {
    renderResearch();

    const chart = await screen.findByRole("region", { name: "US.NVDA 价格历史" });
    expect(within(chart).getByRole("table", { name: "图表末值摘要" })).toBeVisible();
    const summary = within(chart).getByRole("table", { name: "图表末值摘要" });
    expect(within(summary).getByRole("columnheader", { name: "开盘" })).toBeVisible();
    expect(within(summary).getByRole("columnheader", { name: "最高" })).toBeVisible();
    expect(within(summary).getByRole("columnheader", { name: "最低" })).toBeVisible();
    expect(within(summary).getByRole("columnheader", { name: "收盘" })).toBeVisible();
    expect(within(summary).getByText("143.00")).toBeVisible();
    expect(within(summary).getByText("144.50")).toBeVisible();
    expect(within(summary).getByText("142.00")).toBeVisible();
    expect(within(summary).getByText("143.50")).toBeVisible();
    expect(within(chart).getByText("ATR 缓冲")).toBeVisible();
    expect(within(chart).getByText("20 日通道上沿")).toBeVisible();
    expect(within(chart).getByRole("list", { name: "数据缺口" })).toHaveTextContent(
      "vendor outage",
    );
    expect(within(chart).getByText(/图中含 1 个缺口区间/)).toBeVisible();
    const structureMarkers = within(chart).getByRole("list", {
      name: "最近价格结构标记",
    });
    expect(structureMarkers).toHaveTextContent("状态 → 趋势偏多");
    expect(structureMarkers).toHaveTextContent("向上突破");
    expect(structureMarkers).toHaveTextContent("假突破");
  });

  test("does not render a one-day survival curve and explains two-day confirmation", async () => {
    renderResearch();

    const oneDay = await screen.findByRole("region", { name: "1 个交易日预测" });
    expect(within(oneDay).queryByText(/存活曲线/)).not.toBeInTheDocument();
    expect(within(oneDay).getByText(/连续 2 个交易日确认/)).toBeVisible();
  });
});

describe("price history chart adapter", () => {
  test("caps and aggregates recent markers while filters leave complete evidence lists intact", async () => {
    const bars = Array.from({ length: 90 }, (_, index) => ({
      ...makeBars()[index % makeBars().length]!,
      at: new Date(Date.UTC(2026, 0, index + 1)).toISOString(),
      regime: index % 2 === 0 ? ("range" as const) : ("uptrend" as const),
    }));
    const latestAt = bars.at(-1)!.at;
    const history = historyResponse(bars).data;
    history.events = [
      {
        details: ["full event detail"],
        event_id: "same-time-event",
        event_type: "earnings",
        label: "Same-time earnings",
        occurred_at: latestAt,
      },
    ];
    history.gaps = [{ end_at: latestAt, reason: "same-time gap", start_at: latestAt }];
    render(<PriceHistoryChart history={history} symbol="US.NVDA" />, {
      wrapper: ChartContext,
    });

    await waitFor(() =>
      expect(chartHarness.markerApis[0]?.setMarkers).toHaveBeenCalled(),
    );
    const initial = chartHarness.markerApis[0]?.setMarkers.mock.lastCall?.[0] as Array<{
      text?: string;
      time: number;
    }>;
    expect(initial).toHaveLength(12);
    expect(new Set(initial.map((marker) => marker.time)).size).toBe(initial.length);
    expect(
      initial.every((marker) => marker.time >= Date.parse(bars[30]!.at) / 1000),
    ).toBe(true);
    const user = userEvent.setup();
    expect(
      screen.getByText("已载入标记 12/12；最近 60 根内，非当前视窗计数"),
    ).toBeVisible();
    await user.click(screen.getByText("已载入标记明细（12）"));
    const markerDetails = screen.getByRole("list", { name: "已载入标记" });
    expect(within(markerDetails).getAllByRole("listitem")).toHaveLength(12);
    type MockScale = ReturnType<(typeof chartHarness.charts)[number]["timeScale"]>;
    expect(markerDetails).toHaveTextContent("状态 →");
    expect(markerDetails).toHaveTextContent("数据缺口");

    const filters = screen.getByRole("group", { name: "图表标记筛选" });
    await user.click(within(filters).getByRole("checkbox", { name: "缺口" }));
    expect(markerDetails).not.toHaveTextContent("数据缺口");
    const scales = chartHarness.charts[0]!.timeScale.mock.results.map(
      ({ value }) => value as MockScale,
    );
    expect(
      scales.reduce(
        (count, scale) => count + scale.setVisibleLogicalRange.mock.calls.length,
        0,
      ),
    ).toBe(1);
    await user.selectOptions(
      screen.getByRole("combobox", { name: "价格图显示范围" }),
      "0",
    );
    expect(chartHarness.charts[0]!.timeScale.mock.lastCall).toBeDefined();
    expect(
      (chartHarness.charts[0]!.timeScale.mock.results.at(-1)!.value as MockScale)
        .fitContent,
    ).toHaveBeenCalledOnce();
    const filtered = chartHarness.markerApis[0]?.setMarkers.mock
      .lastCall?.[0] as Array<{
      text?: string;
    }>;
    expect(
      filtered.every((marker) => !marker.text?.includes("Same-time earnings")),
    ).toBe(true);
    expect(history.events[0]?.label).toBe("Same-time earnings");
    expect(screen.getByRole("list", { name: "数据缺口" })).toHaveTextContent(
      "same-time gap",
    );
  });

  test("declares a wrapping marker toolbar and one-column narrow annotation layout", () => {
    const css = readFileSync(
      resolve(process.cwd(), "frontend/src/features/research/research.css"),
      "utf8",
    );
    expect(css).toMatch(/\.price-history__filters\s*\{[^}]*flex-wrap:\s*wrap/s);
    expect(css).toMatch(
      /@media \(max-width:\s*767px\)[\s\S]*\.price-history__annotations\s*\{[^}]*grid-template-columns:\s*1fr/s,
    );
  });

  test("updates existing series, markers and landscape state, then destroys the chart", async () => {
    const addEventListener = vi.spyOn(document, "addEventListener");
    const removeEventListener = vi.spyOn(document, "removeEventListener");
    const first = historyResponse();
    const { rerender, unmount } = render(
      <PriceHistoryChart history={first.data} symbol="US.NVDA" />,
      { wrapper: ChartContext },
    );

    await waitFor(() => expect(chartHarness.createChart).toHaveBeenCalledTimes(1));
    expect(chartHarness.series.length).toBeGreaterThanOrEqual(6);
    expect(chartHarness.createSeriesMarkers).toHaveBeenCalledTimes(1);
    const initialSetDataCalls = chartHarness.series.reduce(
      (total, item) => total + item.setData.mock.calls.length,
      0,
    );

    rerender(
      <PriceHistoryChart
        history={
          historyResponse([
            ...makeBars(),
            { ...makeBars()[0]!, at: "2026-07-01T00:00:00Z" },
          ]).data
        }
        symbol="US.NVDA"
      />,
    );

    await waitFor(() => {
      const updatedSetDataCalls = chartHarness.series.reduce(
        (total, item) => total + item.setData.mock.calls.length,
        0,
      );
      expect(updatedSetDataCalls).toBeGreaterThan(initialSetDataCalls);
    });
    expect(chartHarness.createChart).toHaveBeenCalledTimes(1);
    expect(chartHarness.markerApis[0]?.setMarkers).toHaveBeenCalledTimes(2);

    const user = userEvent.setup();
    const landscapeButton = screen.getByRole("button", { name: "进入横屏全屏图表" });
    await user.click(landscapeButton);
    expect(screen.getByRole("button", { name: "退出横屏全屏图表" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("table", { name: "图表末值摘要" })).toBeVisible();

    unmount();
    expect(resizeObservers[0]?.disconnect).toHaveBeenCalledTimes(1);
    const fullscreenListener = addEventListener.mock.calls.find(
      ([event]) => event === "fullscreenchange",
    )?.[1];
    expect(fullscreenListener).toBeTypeOf("function");
    expect(removeEventListener).toHaveBeenCalledWith(
      "fullscreenchange",
      fullscreenListener,
    );
    expect(chartHarness.charts[0]?.remove).toHaveBeenCalledTimes(1);
  });

  test("preserves landscape height during ResizeObserver updates and follows native fullscreen exit", async () => {
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 900 });
    let fullscreenElement: Element | null = null;
    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      get: () => fullscreenElement,
    });
    const { unmount } = render(
      <PriceHistoryChart history={historyResponse().data} symbol="US.NVDA" />,
      { wrapper: ChartContext },
    );
    const chartRoot = screen.getByRole("region", { name: "US.NVDA 价格历史" });
    Object.defineProperty(chartRoot, "requestFullscreen", {
      configurable: true,
      value: vi.fn(() => {
        fullscreenElement = chartRoot;
        document.dispatchEvent(new Event("fullscreenchange"));
        return Promise.resolve();
      }),
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "进入横屏全屏图表" }));
    const canvas = screen.getByRole("img", { name: /US.NVDA OHLC/ });
    resizeObservers[0]?.trigger(canvas, 800);
    expect(chartHarness.charts[0]?.resize).toHaveBeenLastCalledWith(800, 690);

    fullscreenElement = null;
    act(() => {
      document.dispatchEvent(new Event("fullscreenchange"));
    });
    expect(screen.getByRole("button", { name: "进入横屏全屏图表" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    unmount();
  });
});

function ChartLocaleSwitch() {
  const i18n = useI18n();
  return <button onClick={() => i18n.setLocale("en-US")}>English chart</button>;
}

test("updates chart labels in place while retaining the window, marker filter and expanded details", async () => {
  const client = createClient();
  render(
    <I18nProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ChartLocaleSwitch />
          <PriceHistoryChart history={historyResponse().data} symbol="US.NVDA" />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nProvider>,
  );
  const user = userEvent.setup();
  await user.selectOptions(
    screen.getByRole("combobox", { name: "价格图显示范围" }),
    "20",
  );
  await user.click(screen.getByRole("checkbox", { name: "缺口" }));
  const details = screen.getByText(/已载入标记明细/).closest("details");
  await user.click(screen.getByText(/已载入标记明细/));
  const chart = chartHarness.charts[0]!;
  const rangeChanges = chart.timeScale().setVisibleLogicalRange.mock.calls.length;
  const fits = chart.timeScale().fitContent.mock.calls.length;
  await user.click(screen.getByRole("button", { name: "English chart" }));
  expect(
    screen.getByRole("combobox", { name: "Price chart display range" }),
  ).toHaveValue("20");
  expect(screen.getByRole("checkbox", { name: "Gap" })).not.toBeChecked();
  expect(details).toHaveAttribute("open");
  expect(
    screen.getByRole("heading", { name: "Price, channels and structure" }),
  ).toBeVisible();
  expect(chartHarness.createChart).toHaveBeenCalledTimes(1);
  expect(chart.timeScale().setVisibleLogicalRange).toHaveBeenCalledTimes(rangeChanges);
  expect(chart.timeScale().fitContent).toHaveBeenCalledTimes(fits);
  expect(chart.applyOptions).toHaveBeenCalledWith({
    localization: { locale: "en-US" },
  });
});
