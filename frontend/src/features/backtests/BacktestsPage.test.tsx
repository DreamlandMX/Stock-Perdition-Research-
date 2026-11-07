import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nProvider, useI18n } from "../../i18n/context";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { components } from "../../api/generated/schema";
import { ResearchApiError } from "../../api/client";
import { BacktestsPage } from "./BacktestsPage";
import {
  buildBacktestRequest,
  buildModelRequest,
  decodeBacktestUrlState,
  encodeBacktestUrlState,
  selectBacktestEvidence,
  type BacktestUrlState,
} from "./backtest-url-state";

const chartHarness = vi.hoisted(() => {
  const instances: Array<{
    dispose: ReturnType<typeof vi.fn>;
    resize: ReturnType<typeof vi.fn>;
    setOption: ReturnType<typeof vi.fn>;
  }> = [];
  const init = vi.fn(() => {
    const instance = {
      dispose: vi.fn(),
      resize: vi.fn(),
      setOption: vi.fn(),
    };
    instances.push(instance);
    return instance;
  });
  return { init, instances };
});

vi.mock("echarts/core", () => ({ init: chartHarness.init, use: vi.fn() }));

type BacktestResponse = components["schemas"]["ResearchResponse_BacktestReadModel_"];
type ModelResponse = components["schemas"]["ResearchResponse_ModelMatrixReadModel_"];

const SNAPSHOT = {
  as_of: "2026-07-10T20:05:00Z",
  calibration_version: "cal-v7",
  dataset_revision: "bars-r42",
  feature_set_version: "features-v7",
  model_version: "model-v4",
  prediction_readiness: "forecast_ready",
  research_readiness: "full_verified",
  revision_no: 3,
  snapshot_id: "snapshot-20260710-1605",
  universe_version: "universe-v3",
} satisfies components["schemas"]["ResolvedSnapshot"];

const BACKTEST_RESPONSE = {
  data: {
    asset_group: "us_equity",
    calibration_bins: [
      {
        lower_bound: 0.4,
        mean_prediction: 0.46,
        observed_frequency: 0.42,
        sample_count: 180,
        upper_bound: 0.5,
      },
      {
        lower_bound: 0.5,
        mean_prediction: 0.57,
        observed_frequency: 0.54,
        sample_count: 240,
        upper_bound: 0.6,
      },
    ],
    capacity_sensitivity: [
      { capacity_exceeded_count: 0, fold_count: 3, portfolio_scale: 0.5 },
      { capacity_exceeded_count: 0, fold_count: 3, portfolio_scale: 1 },
      { capacity_exceeded_count: 2, fold_count: 3, portfolio_scale: 2 },
      { capacity_exceeded_count: 4, fold_count: 3, portfolio_scale: 5 },
    ],
    cost_scenario: "cost-12bps",
    cost_sensitivity: [
      { estimated_total_cost: 0.004, net_portfolio_return: 0.07, per_side_bps: 5 },
      { estimated_total_cost: 0.008, net_portfolio_return: 0.06, per_side_bps: 10 },
      { estimated_total_cost: 0.016, net_portfolio_return: 0.04, per_side_bps: 20 },
    ],
    deflated_sharpe: {
      assumptions: ["OOS returns are stationary within each outer window"],
      attempted_sharpe_mean: 0.1,
      attempted_sharpe_std: 0,
      attempted_trials: 9,
      expected_maximum_sharpe: 0.31,
      observation_count: 12,
      observed_sharpe: 0.48,
      probability_sharpe_is_genuine: null,
      status: "insufficient_trial_variance",
      successful_trials_with_sharpe: 8,
    },
    diagnostic_slices: [
      {
        dimension: "confidence",
        metrics: [
          { name: "brier_skill", unavailable_reason: null, value: 0.11 },
          { name: "ranking_ic", unavailable_reason: null, value: 0.09 },
        ],
        sample_count: 280,
        value: "high",
      },
      {
        dimension: "confidence",
        metrics: [{ name: "brier_skill", unavailable_reason: null, value: 0.06 }],
        sample_count: 420,
        value: "medium",
      },
      {
        dimension: "confidence",
        metrics: [{ name: "brier_skill", unavailable_reason: null, value: 0.01 }],
        sample_count: 350,
        value: "low",
      },
      {
        dimension: "confidence",
        metrics: [
          {
            name: "ranking_ic",
            unavailable_reason: "abstained_samples_not_ranked",
            value: null,
          },
        ],
        sample_count: 300,
        value: "abstain",
      },
      {
        dimension: "regime",
        metrics: [{ name: "brier_skill", unavailable_reason: null, value: 0.05 }],
        sample_count: 510,
        value: "range",
      },
      {
        dimension: "sector",
        metrics: [{ name: "brier_skill", unavailable_reason: null, value: 0.07 }],
        sample_count: 390,
        value: "technology",
      },
      {
        dimension: "asset_type",
        metrics: [{ name: "brier_skill", unavailable_reason: null, value: 0.04 }],
        sample_count: 1_050,
        value: "stock",
      },
      {
        dimension: "liquidity",
        metrics: [{ name: "brier_skill", unavailable_reason: null, value: 0.08 }],
        sample_count: 720,
        value: "high_liquidity",
      },
      {
        dimension: "year",
        metrics: [{ name: "brier_skill", unavailable_reason: null, value: 0.05 }],
        sample_count: 1_350,
        value: "2025",
      },
    ],
    economic_series: [
      {
        at: "2025-01-31T20:00:00Z",
        cash_fraction: 0.35,
        cumulative_cost: 0.004,
        drawdown: -0.02,
        equity: 1.03,
        fold_id: "outer-1",
        turnover: 0.18,
      },
      {
        at: "2025-12-31T20:00:00Z",
        cash_fraction: 0.42,
        cumulative_cost: 0.013,
        drawdown: -0.08,
        equity: 1.09,
        fold_id: "outer-2",
        turnover: 0.27,
      },
    ],
    evaluation_window_id: "lockbox-outer-2025",
    evaluation_windows: [
      {
        consumed_at: "2026-01-02T00:00:00Z",
        consumption_count: 1,
        role: "outer_test",
        scope: "research_only",
        window_id: "lockbox-outer-2025",
      },
      {
        consumed_at: null,
        consumption_count: 0,
        role: "lockbox",
        scope: "production",
        window_id: "final-lockbox-2026",
      },
      {
        consumed_at: null,
        consumption_count: 1,
        role: "outer_test",
        scope: "research_only",
        window_id: "consumed-without-timestamp",
      },
    ],
    evidence_scope: "research_only",
    fold_metrics: [
      {
        brier_score: 0.19,
        brier_skill: 0.07,
        concordance_index: 0.61,
        evaluation_end: "2025-04-30T20:00:00Z",
        evaluation_start: "2025-01-02T00:00:00Z",
        expected_calibration_error: 0.03,
        fold_id: "outer-1",
        interval_coverage: 0.83,
        interval_width: 0.11,
        pinball_loss: 0.04,
        pr_auc: 0.62,
        ranking_ic: 0.08,
        raw_interval_coverage: 0.76,
        raw_interval_width: 0.1,
        sample_count: 500,
        survival_brier: 0.17,
        tail_failure_rate: 0.02,
        worst_window: false,
      },
      {
        brier_score: 0.24,
        brier_skill: -0.01,
        concordance_index: 0.52,
        evaluation_end: "2025-08-29T20:00:00Z",
        evaluation_start: "2025-05-01T00:00:00Z",
        expected_calibration_error: 0.08,
        fold_id: "outer-2",
        interval_coverage: 0.72,
        interval_width: 0.14,
        pinball_loss: 0.06,
        pr_auc: 0.49,
        ranking_ic: -0.01,
        raw_interval_coverage: 0.65,
        raw_interval_width: 0.12,
        sample_count: 400,
        survival_brier: 0.22,
        tail_failure_rate: 0.09,
        worst_window: true,
      },
      {
        brier_score: 0.2,
        brier_skill: 0.04,
        concordance_index: 0.58,
        evaluation_end: "2025-12-31T20:00:00Z",
        evaluation_start: "2025-09-01T00:00:00Z",
        expected_calibration_error: 0.04,
        fold_id: "outer-3",
        interval_coverage: 0.8,
        interval_width: 0.12,
        pinball_loss: 0.05,
        pr_auc: 0.57,
        ranking_ic: 0.05,
        raw_interval_coverage: 0.73,
        raw_interval_width: 0.1,
        sample_count: 450,
        survival_brier: 0.18,
        tail_failure_rate: 0.03,
        worst_window: false,
      },
    ],
    horizon: 5,
    interval_calibration: {
      method: "conformalized_quantile_regression",
      status: "available",
      target_coverage: 0.8,
      unavailable_reason: null,
    },
    model_version: "model-v4",
    pbo: {
      block_count: 3,
      combination_count: 0,
      probability: null,
      status: "insufficient_blocks",
    },
    research_only_reasons: ["synthetic_offline", "survivorship_limited"],
    sample_count: 1_350,
    split_version: "split-v6",
    strategy_version: "strategy-v4",
    survival_calibration: [
      {
        horizon: 5,
        observed_exit_frequency: 0.28,
        predicted_exit_probability: 0.3,
        sample_count: 240,
      },
      {
        horizon: 5,
        observed_exit_frequency: 0.66,
        predicted_exit_probability: 0.7,
        sample_count: 210,
      },
      {
        horizon: 20,
        observed_exit_frequency: 0.37,
        predicted_exit_probability: 0.4,
        sample_count: 200,
      },
      {
        horizon: 20,
        observed_exit_frequency: 0.75,
        predicted_exit_probability: 0.8,
        sample_count: 180,
      },
    ],
    survivorship_limitation_reason: "static_seed_universe",
    survivorship_limited: true,
    task: "forecast_direction",
    trial_ledger: {
      aborted: 1,
      failed: 2,
      registered: 12,
      rejected: 2,
      running: 1,
      succeeded: 6,
    },
    universe_version: "universe-v3",
  },
  data_status: "ready",
  effective_abstain: false,
  evaluated_at: "2026-07-10T20:05:04Z",
  exclusion_reason: [],
  generated_at: "2026-07-10T20:05:04Z",
  request_id: "request-backtests-1",
  resolved_snapshot: SNAPSHOT,
  serving_status: "live",
} satisfies BacktestResponse;

const MODEL_RESPONSE = {
  data: {
    rows: [
      {
        asset_group: "us_equity",
        baseline_version: "baseline-v2",
        calibration_version: "cal-v7",
        challenger_version: "challenger-v4",
        champion_version: null,
        horizon: 5,
        status: "no_champion_candidate",
        task: "forecast_direction",
        unavailable_reason: "failed_independent_oos_gate",
      },
      {
        asset_group: "us_equity",
        baseline_version: "baseline-survival-v1",
        calibration_version: "cal-v7",
        challenger_version: "survival-v3",
        champion_version: null,
        horizon: 20,
        status: "research_only",
        task: "range_exit",
        unavailable_reason: null,
      },
    ],
  },
  data_status: "ready",
  effective_abstain: false,
  evaluated_at: "2026-07-10T20:05:04Z",
  exclusion_reason: [],
  generated_at: "2026-07-10T20:05:04Z",
  request_id: "request-models-1",
  resolved_snapshot: SNAPSHOT,
  serving_status: "live",
} satisfies ModelResponse;

class ResizeObserverStub {
  static instances: ResizeObserverStub[] = [];

  readonly callback: ResizeObserverCallback;
  disconnect = vi.fn();
  observe = vi.fn();
  unobserve = vi.fn();

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    ResizeObserverStub.instances.push(this);
  }

  trigger(target: Element) {
    this.callback(
      [{ contentRect: { width: 720 }, target } as unknown as ResizeObserverEntry],
      this,
    );
  }
}

const FULL_QUERY =
  "?as_of=2026-07-10T20%3A05%3A00Z&snapshot_id=snapshot-20260710-1605" +
  "&dataset_revision=bars-r42&feature_set_version=features-v7" +
  "&calibration_version=cal-v7&model_version=model-v4" +
  "&strategy_version=strategy-v4&universe_version=universe-v3" +
  "&split_version=split-v6&evaluation_window_id=lockbox-outer-2025" +
  "&cost_scenario=cost-12bps&horizon=5&task=forecast_direction" +
  "&asset_group=us_equity";

function renderPage(initialEntry = `/backtests${FULL_QUERY}`) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const loadBacktest = vi.fn(() => Promise.resolve(BACKTEST_RESPONSE));
  const loadModels = vi.fn(() => Promise.resolve(MODEL_RESPONSE));
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <BacktestsPage loadBacktest={loadBacktest} loadModels={loadModels} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, loadBacktest, loadModels };
}

beforeEach(() => {
  ResizeObserverStub.instances = [];
  chartHarness.instances.length = 0;
  chartHarness.init.mockClear();
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      addEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
      matches: query === "(prefers-reduced-motion: reduce)",
      media: query,
      onchange: null,
      removeEventListener: vi.fn(),
    })),
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("backtest URL state", () => {
  test("round-trips every reproducible API and chart slice", () => {
    const state: BacktestUrlState = {
      asOf: "2026-07-10T20:05:00Z",
      snapshotId: "snapshot-20260710-1605",
      versions: {
        calibrationVersion: "cal-v7",
        datasetRevision: "bars-r42",
        featureSetVersion: "features-v7",
        modelVersion: "model-v4",
        splitVersion: "split-v6",
        strategyVersion: "strategy-v4",
        universeVersion: "universe-v3",
      },
      evaluationWindowId: "lockbox-outer-2025",
      foldId: "outer-2",
      horizon: 20,
      task: "range_exit",
      assetGroup: "us_equity",
      sector: "technology",
      regime: "transition",
      confidenceSlice: "abstain",
      costScenario: "cost-25bps",
      from: "2025-05-01",
      to: "2025-12-31",
      warnings: [],
    };

    const encoded = encodeBacktestUrlState(state);
    const decoded = decodeBacktestUrlState(encoded);

    expect(decoded).toEqual(state);
    expect(Object.fromEntries(encoded)).toMatchObject({
      asset_group: "us_equity",
      confidence: "abstain",
      fold: "outer-2",
      from: "2025-05-01",
      horizon: "20",
      model_version: "model-v4",
      regime: "transition",
      sector: "technology",
      split_version: "split-v6",
      task: "range_exit",
      to: "2025-12-31",
    });
    expect(buildBacktestRequest(decoded)).toEqual({
      as_of: state.asOf,
      asset_group: "us_equity",
      calibration_version: "cal-v7",
      cost_scenario: "cost-25bps",
      dataset_revision: "bars-r42",
      evaluation_window_id: "lockbox-outer-2025",
      feature_set_version: "features-v7",
      fold: "outer-2",
      horizon: 20,
      model_version: "model-v4",
      snapshot_id: state.snapshotId,
      split_version: "split-v6",
      strategy_version: "strategy-v4",
      task: "range_exit",
      universe_version: "universe-v3",
    });
    expect(buildModelRequest(decoded)).toEqual({
      as_of: state.asOf,
      asset_group: "us_equity",
      calibration_version: "cal-v7",
      dataset_revision: "bars-r42",
      feature_set_version: "features-v7",
      horizon: 20,
      model_version: "model-v4",
      snapshot_id: state.snapshotId,
      task: "range_exit",
      universe_version: "universe-v3",
    });
    expect(selectBacktestEvidence(BACKTEST_RESPONSE.data, decoded)).toMatchObject({
      economicSeries: [BACKTEST_RESPONSE.data.economic_series[1]],
      folds: [BACKTEST_RESPONSE.data.fold_metrics[1]],
    });
  });

  test("rejects invalid horizons, dates and confidence values without inventing slices", () => {
    const decoded = decodeBacktestUrlState(
      new URLSearchParams({
        confidence: "guaranteed",
        from: "2025-13-40",
        horizon: "10",
        to: "tomorrow",
      }),
    );

    expect(decoded.horizon).toBe(5);
    expect(decoded.confidenceSlice).toBeUndefined();
    expect(decoded.from).toBeUndefined();
    expect(decoded.to).toBeUndefined();
    expect(decoded.warnings.map((warning) => warning.parameter)).toEqual([
      "horizon",
      "confidence",
      "from",
      "to",
    ]);
  });
});

describe("BacktestsPage diagnostic evidence", () => {
  test("retains backtest evidence when the independent model request fails", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[`/backtests${FULL_QUERY}`]}>
          <BacktestsPage
            loadBacktest={() => Promise.resolve(BACKTEST_RESPONSE)}
            loadModels={() => Promise.reject(new Error("model service offline"))}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(
      await screen.findByRole("status", { name: "模型比较状态" }),
    ).toHaveTextContent("不能据此确认 Champion 状态");
    expect(screen.getByRole("region", { name: "聚合经济诊断" })).toBeVisible();
    expect(
      screen.queryByRole("alert", { name: "回测诊断不可用" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("alert", { name: "无 champion 候选" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试模型证据" })).toBeVisible();
  });
  test("puts outer folds and the worst window before aggregate economics", async () => {
    renderPage();

    const folds = await screen.findByRole("region", {
      name: "独立样本外 outer folds",
    });
    const economics = screen.getByRole("region", { name: "聚合经济诊断" });

    expect(
      folds.compareDocumentPosition(economics) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(within(folds).getByText("最差窗口")).toBeVisible();
    expect(within(folds).getByText("outer-2")).toBeVisible();
    expect(within(folds).getByText("n=400")).toBeVisible();
    expect(economics).toHaveTextContent("聚合结果不能覆盖逐折失效证据");
  });

  test("shows calibration, interval and survival values in summaries and accessible tables", async () => {
    renderPage();

    const reliability = await screen.findByRole("table", {
      name: "概率可靠性数据",
    });
    expect(reliability).toHaveTextContent("40.0%–50.0%");
    expect(reliability).toHaveTextContent("46.0%");
    expect(reliability).toHaveTextContent("42.0%");
    expect(reliability).toHaveTextContent("180");
    expect(screen.getByText(/可靠性桶共 420 个样本/)).toBeVisible();

    const interval = screen.getByRole("table", { name: "CQR 区间覆盖数据" });
    expect(interval).toHaveTextContent("conformalized_quantile_regression");
    expect(within(interval).getAllByText("80.0%").length).toBeGreaterThan(0);
    expect(interval).toHaveTextContent("65.0%");
    expect(interval).toHaveTextContent("72.0%");
    expect(interval).toHaveTextContent("14.0%");
    expect(interval).toHaveTextContent("9.0%");
    expect(screen.getByText(/目标覆盖率 80.0%/)).toBeVisible();

    const survival = screen.getByRole("table", { name: "生存校准数据" });
    expect(survival).toHaveTextContent("5 日");
    expect(survival).toHaveTextContent("20 日");
    expect(survival).toHaveTextContent("30.0%");
    expect(survival).toHaveTextContent("28.0%");
    const survivalFolds = screen.getByRole("table", { name: "生存逐折指标" });
    expect(survivalFolds).toHaveTextContent("时间相关 Brier");
    expect(survivalFolds).toHaveTextContent("C-index");
    expect(survivalFolds).toHaveTextContent("0.220");
    expect(survivalFolds).toHaveTextContent("0.520");
  });

  test("renders slice, trial, window, cost and capacity evidence without placeholders", async () => {
    renderPage();

    expect(await screen.findByText("总样本数 n=1,350")).toBeVisible();
    const confidenceTable = screen.getByRole("table", { name: "置信切片样本证据" });
    expect(
      within(confidenceTable).getByRole("row", { name: /高置信度/ }),
    ).toHaveTextContent("280");
    expect(
      within(confidenceTable).getByRole("row", { name: /高置信度/ }),
    ).toHaveTextContent("brier_skill 0.110");
    expect(
      within(confidenceTable).getByRole("row", { name: /弃权/ }),
    ).toHaveTextContent("ranking_ic：abstained_samples_not_ranked");

    const governance = screen.getByRole("region", { name: "研究治理证据" });
    expect(governance).toHaveTextContent("试验差异不足");
    expect(governance).toHaveTextContent("观测夏普 0.480");
    expect(governance).toHaveTextContent("预期最大夏普 0.310");
    expect(governance).toHaveTextContent("数据分块不足");
    expect(screen.getByTitle("insufficient_blocks")).toBeVisible();
    expect(governance).toHaveTextContent("已登记12");
    expect(governance).toHaveTextContent("失败2");
    expect(governance).toHaveTextContent("运行中1");
    expect(governance).toHaveTextContent("lockbox-outer-2025");
    expect(governance).toHaveTextContent("2026/01/01 19:00 美东时间");
    expect(screen.getByTitle("2026-01-02T00:00:00Z")).toBeVisible();
    expect(governance).toHaveTextContent("final-lockbox-2026");
    expect(governance).toHaveTextContent("未消费");
    expect(
      screen.getByRole("row", { name: /consumed-without-timestamp/ }),
    ).toHaveTextContent("已消费，时间未提供");
    expect(governance).toHaveTextContent("离线合成验收证据、受幸存者偏差限制");
    expect(governance).toHaveTextContent("静态种子标的池");
    expect(screen.getByTitle("static_seed_universe")).toBeVisible();

    const cost = screen.getByRole("table", { name: "成本敏感性" });
    expect(cost).toHaveTextContent("5 bps");
    expect(cost).toHaveTextContent("10 bps");
    expect(cost).toHaveTextContent("20 bps");
    const capacity = screen.getByRole("table", { name: "容量敏感性" });
    expect(capacity).toHaveTextContent("0.5×");
    expect(capacity).toHaveTextContent("5.0×");
    expect(screen.queryByText(/只读 API 未提供/)).not.toBeInTheDocument();
  });

  test("passes a stale split and fold through once and shows unavailable without fallback", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const loadBacktest = vi.fn(() =>
      Promise.reject(
        new ResearchApiError({
          kind: "http",
          status: 404,
          code: "backtest_not_found",
          message: "No matching evidence",
          requestId: "missing-evidence-request",
          retryable: false,
          affectedScope: ["backtest"],
        }),
      ),
    );
    const loadModels = vi.fn(() => Promise.resolve(MODEL_RESPONSE));
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter
          initialEntries={[
            "/backtests?split_version=split-stale&fold=outer-stale&horizon=5" +
              "&task=forecast_direction&asset_group=us_equity",
          ]}
        >
          <BacktestsPage loadBacktest={loadBacktest} loadModels={loadModels} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole("status", { name: "当前快照尚无匹配回测" }),
    ).toBeVisible();
    expect(screen.getByText(/所选标的池与查询窗口尚无匹配回测/)).toBeVisible();
    screen.getByText("诊断详情与查询条件").closest("details")?.setAttribute("open", "");
    expect(screen.getByText(/missing-evidence-request/)).toBeVisible();
    expect(screen.getByText(/健康页的服务连接表示当前运行状态/)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "重试只读诊断" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("table", { name: "模型任务比较" })).toBeVisible();
    expect(loadBacktest).toHaveBeenCalledTimes(1);
    expect(loadBacktest).toHaveBeenCalledWith(
      expect.objectContaining({ fold: "outer-stale", split_version: "split-stale" }),
    );
  });

  test("prominently blocks promotion while retaining model comparisons", async () => {
    renderPage();

    const governance = await screen.findByRole("alert", {
      name: "无 champion 候选",
    });
    expect(governance).toHaveTextContent("no_champion_candidate");
    expect(governance).toHaveTextContent("不得按最好看的试验晋升");
    expect(screen.getByText("仅限研究")).toBeVisible();
    expect(screen.getByText("幸存者偏差受限")).toBeVisible();

    const comparisons = screen.getByRole("table", { name: "模型任务比较" });
    expect(comparisons).toHaveTextContent("baseline-v2");
    expect(comparisons).toHaveTextContent("challenger-v4");
    expect(comparisons).toHaveTextContent("无（未晋升）");
    expect(screen.queryByText(/目标价|保证收益|收益承诺/)).not.toBeInTheDocument();
  });

  test("uses reduced-motion ECharts and cleans up observers and instances", async () => {
    const view = renderPage();

    await waitFor(() => expect(chartHarness.init).toHaveBeenCalledTimes(4));
    expect(ResizeObserverStub.instances).toHaveLength(4);
    for (const chart of chartHarness.instances) {
      expect(chart.setOption).toHaveBeenCalledWith(
        expect.objectContaining({ animation: false }),
      );
    }

    for (const observer of ResizeObserverStub.instances) {
      const target = observer.observe.mock.calls[0]?.[0] as Element;
      observer.trigger(target);
    }
    expect(
      chartHarness.instances.every((chart) => chart.resize.mock.calls.length === 1),
    ).toBe(true);

    view.unmount();
    expect(
      ResizeObserverStub.instances.every(
        (observer) => observer.disconnect.mock.calls.length === 1,
      ),
    ).toBe(true);
    expect(
      chartHarness.instances.every((chart) => chart.dispose.mock.calls.length === 1),
    ).toBe(true);
  });
});

function LocaleControl() {
  const i18n = useI18n();
  return <button onClick={() => i18n.setLocale("en-US")}>English</button>;
}

test("locale changes update chart labels and retain chart instances without refetching", async () => {
  localStorage.clear();
  const loadBacktest = vi.fn().mockResolvedValue(BACKTEST_RESPONSE);
  const loadModels = vi.fn().mockResolvedValue(MODEL_RESPONSE);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <I18nProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[`/backtests${FULL_QUERY}`]}>
          <LocaleControl />
          <BacktestsPage loadBacktest={loadBacktest} loadModels={loadModels} />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nProvider>,
  );
  await screen.findByRole("table", { name: "概率可靠性数据" });
  const calls = [loadBacktest.mock.calls.length, loadModels.mock.calls.length];
  const chartCount = chartHarness.init.mock.calls.length;
  Object.assign(chartHarness.instances[0]!, {
    getOption: () => ({ legend: [{ selected: { 观测频率: false, 理想校准: true } }] }),
  });
  await userEvent.click(screen.getByRole("button", { name: "English" }));
  expect(
    screen.getByRole("table", { name: "Probability reliability data" }),
  ).toHaveTextContent("42.0%");
  expect(chartHarness.init).toHaveBeenCalledTimes(chartCount);
  expect(
    chartHarness.instances.every((chart) => chart.dispose.mock.calls.length === 0),
  ).toBe(true);
  expect([loadBacktest.mock.calls.length, loadModels.mock.calls.length]).toEqual(calls);
  const option = chartHarness.instances[0]?.setOption.mock.lastCall?.[0] as {
    legend: { data: string[]; selected: Record<string, boolean> };
  };
  expect(option.legend.data).toContain("Observed frequency");
  expect(option.legend.selected["Observed frequency"]).toBe(false);
  localStorage.clear();
});
