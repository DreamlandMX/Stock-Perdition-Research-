import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { components } from "../../api/generated/schema";
import { HealthPage } from "./HealthPage";
import { DataHealthTable } from "./DataHealthTable";
import { CapabilityMatrix } from "./CapabilityMatrix";
import { IncidentList } from "./IncidentList";
import { I18nProvider } from "../../i18n/context";
import { LanguageSelect } from "../../i18n/LanguageSelect";

type CapabilityResponse = components["schemas"]["CapabilityResponse"];
type DataHealthResponse =
  components["schemas"]["ResearchResponse_DataHealthReadModel_"];
type HealthResponse = components["schemas"]["HealthResponse"];
type ModelResponse = components["schemas"]["ResearchResponse_ModelMatrixReadModel_"];
type UniverseResponse = components["schemas"]["ResearchResponse_UniverseReadModel_"];

const SNAPSHOT = {
  as_of: "2026-07-17T20:00:00Z",
  calibration_version: "calibration-v4",
  dataset_revision: "dataset-r42",
  feature_set_version: "features-v8",
  model_version: "model-v5",
  prediction_readiness: "forecast_ready",
  research_readiness: "full_verified",
  revision_no: 7,
  snapshot_id: "snapshot-20260717-r7",
  universe_version: "universe-2026q3",
} satisfies components["schemas"]["ResolvedSnapshot"];

const HEALTH = {
  components: [
    {
      completeness: "partial",
      component: "opend_history",
      connectivity: "disconnected",
      freshness: "stale",
      readiness: "blocked",
    },
    {
      completeness: "complete",
      component: "forecast_batch",
      connectivity: "connected",
      freshness: "current",
      readiness: "ready",
    },
  ],
  data_status: "blocked",
  generated_at: "2026-07-18T13:00:00Z",
  request_id: "health-request",
  service_status: "degraded",
} satisfies HealthResponse;

const DATA_HEALTH = {
  data: {
    items: [
      {
        action: "reconnect_opend",
        affected_datasets: ["daily_bars"],
        affected_symbols: ["SPY", "QQQ"],
        completeness: "partial",
        component: "opend_history",
        connectivity: "disconnected",
        coverage_rate: 0.92,
        dataset: "daily_bars",
        duplicate_count: 0,
        error_class: "connection_failed",
        expected_next_at: "2026-07-18T20:05:00Z",
        freshness: "stale",
        last_success_at: "2026-07-17T20:00:00Z",
        message: "OpenD 历史连接中断",
        missing_rate: 0.08,
        quarantine_count: 2,
        quota_remaining: 80,
        quota_total: 100,
        quota_used: 20,
        readiness: "blocked",
        serving_status: "offline",
        symbol: null,
        watermark: "2026-07-17T20:00:00Z",
      },
      {
        action: "wait_for_next_batch",
        affected_datasets: ["fundamentals"],
        affected_symbols: [],
        completeness: "complete",
        component: "fundamental_snapshot",
        connectivity: "connected",
        coverage_rate: 1,
        dataset: "fundamentals",
        duplicate_count: 0,
        error_class: null,
        expected_next_at: "2026-07-19T12:00:00Z",
        freshness: "delayed",
        last_success_at: "2026-07-18T12:00:00Z",
        message: "等待下一批点时快照",
        missing_rate: 0,
        quarantine_count: 0,
        quota_remaining: null,
        quota_total: null,
        quota_used: null,
        readiness: "degraded",
        serving_status: "delayed",
        symbol: null,
        watermark: "2026-07-18T12:00:00Z",
      },
      {
        action: null,
        affected_datasets: ["features"],
        affected_symbols: [],
        completeness: "complete",
        component: "feature_dag",
        connectivity: "not_applicable",
        coverage_rate: 1,
        dataset: "features",
        duplicate_count: 0,
        error_class: null,
        expected_next_at: "2026-07-18T20:08:00Z",
        freshness: "current",
        last_success_at: "2026-07-18T13:00:00Z",
        message: null,
        missing_rate: 0,
        quarantine_count: 0,
        quota_remaining: null,
        quota_total: null,
        quota_used: null,
        readiness: "ready",
        serving_status: "live",
        symbol: null,
        watermark: "2026-07-18T13:00:00Z",
      },
    ],
    page: 1,
    page_size: 100,
    sort_direction: "asc",
    sort_key: "serving_status",
    total: 3,
  },
  data_status: "degraded",
  effective_abstain: true,
  evaluated_at: "2026-07-18T13:00:00Z",
  exclusion_reason: ["opend_history_blocked"],
  generated_at: "2026-07-18T13:00:00Z",
  request_id: "data-health-request",
  resolved_snapshot: SNAPSHOT,
  serving_status: "partial",
} satisfies DataHealthResponse;

const CAPABILITIES = {
  allowance_status: "available",
  current_health: HEALTH.components,
  data_status: "degraded",
  generated_at: "2026-07-18T13:00:00Z",
  request_id: "capability-request",
  snapshot: {
    capabilities: [
      {
        asset_type: "stock_etf",
        degradation_policy: "block",
        earliest_available_at: "2016-01-04T14:30:00Z",
        interface: "request_history_kline",
        last_error_class: null,
        latest_available_at: "2026-07-17T20:00:00Z",
        market: "US",
        permission_scope: "historical",
        point_in_time_status: "proven",
        probed_at: "2026-07-18T13:00:00Z",
        quota_remaining: 80,
        quota_reset_at: "2026-07-19T00:00:00Z",
        quota_total: 100,
        quota_used: 20,
        rate_limit_calls: 10,
        rate_limit_window: "30s",
        safety_budget: 60,
        status: "available",
        timeframe: "1d",
        training_eligibility: "eligible",
        training_eligibility_reason: "historical_point_in_time_proven",
      },
      {
        asset_type: "stock_etf",
        degradation_policy: "disable_feature",
        earliest_available_at: null,
        interface: "get_capital_flow",
        last_error_class: "permission_denied",
        latest_available_at: "2026-07-18T12:58:00Z",
        market: "US",
        permission_scope: "quote",
        point_in_time_status: "forward_only",
        probed_at: "2026-07-18T13:00:00Z",
        quota_remaining: null,
        quota_reset_at: null,
        quota_total: null,
        quota_used: null,
        rate_limit_calls: null,
        rate_limit_window: null,
        safety_budget: null,
        status: "denied",
        timeframe: null,
        training_eligibility: "ineligible",
        training_eligibility_reason: "historical_observed_at_unproven",
      },
    ],
    compatibility_status: "degraded",
    opend_version: "9.4.5208",
    probed_at: "2026-07-18T13:00:00Z",
    sdk_version: "futu-api-9.4.5208",
    snapshot_id: "capability-20260718",
  },
} satisfies CapabilityResponse;

const MODELS = {
  data: {
    rows: [
      {
        asset_group: "us_equity",
        baseline_version: "logistic-v2",
        calibration_version: "vector-v3",
        challenger_version: "patchtst-shadow-v1",
        champion_version: "lightgbm-v5",
        horizon: 5,
        status: "champion",
        task: "forecast_direction",
        unavailable_reason: null,
      },
      {
        asset_group: "us_etf",
        baseline_version: "channel-atr-v2",
        calibration_version: null,
        challenger_version: "catboost-v1",
        champion_version: null,
        horizon: 20,
        status: "no_champion_candidate",
        task: "breakout_direction",
        unavailable_reason: "oos_skill_gate_failed",
      },
    ],
  },
  data_status: "ready",
  effective_abstain: false,
  evaluated_at: "2026-07-18T13:00:00Z",
  exclusion_reason: [],
  generated_at: "2026-07-18T13:00:00Z",
  request_id: "model-request",
  resolved_snapshot: SNAPSHOT,
  serving_status: "live",
} satisfies ModelResponse;

const UNIVERSES = {
  data: {
    versions: [
      {
        effective_from: "2026-07-01T00:00:00Z",
        effective_to: null,
        member_count: 100,
        point_in_time_status: "proven",
        reason: "quarterly_liquidity_review",
        universe_version: "universe-2026q3",
      },
      {
        effective_from: "2026-04-01T00:00:00Z",
        effective_to: "2026-06-30T23:59:59Z",
        member_count: 100,
        point_in_time_status: "forward_only",
        reason: "static_seed_disclosed_survivorship_bias",
        universe_version: "universe-2026q2",
      },
    ],
  },
  data_status: "ready",
  effective_abstain: false,
  evaluated_at: "2026-07-18T13:00:00Z",
  exclusion_reason: [],
  generated_at: "2026-07-18T13:00:00Z",
  request_id: "universe-request",
  resolved_snapshot: SNAPSHOT,
  serving_status: "live",
} satisfies UniverseResponse;

function renderHealth(
  overrides: Partial<React.ComponentProps<typeof HealthPage>> = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <HealthPage
          loadCapabilities={vi.fn().mockResolvedValue(CAPABILITIES)}
          loadDataHealth={vi.fn().mockResolvedValue(DATA_HEALTH)}
          loadHealth={vi.fn().mockResolvedValue(HEALTH)}
          loadModels={vi.fn().mockResolvedValue(MODELS)}
          loadUniverses={vi.fn().mockResolvedValue(UNIVERSES)}
          {...overrides}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => cleanup());

describe("model and data health workspace", () => {
  test("partial quota fields preserve known remaining values", () => {
    render(
      <DataHealthTable
        datasetRevision="r1"
        items={[
          {
            ...DATA_HEALTH.data.items[0]!,
            quota_used: null,
            quota_total: null,
            quota_remaining: 12,
          },
        ]}
      />,
    );
    expect(screen.getByText("已用 未知 / 总额 未知，剩余 12")).toBeVisible();
    expect(screen.queryByText("不适用")).not.toBeInTheDocument();
    render(
      <CapabilityMatrix
        capabilities={[
          {
            ...CAPABILITIES.snapshot.capabilities[0]!,
            quota_remaining: 12,
            quota_total: null,
          },
        ]}
      />,
    );
    expect(screen.getByText("剩余 12 / 总额 未知（安全预算 60）")).toBeVisible();
  });
  test("renders repeated component datasets without React key collisions", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const repeatedItem = DATA_HEALTH.data.items[0];
    if (repeatedItem === undefined) throw new Error("health fixture must not be empty");
    const repeatedDataHealth = {
      ...DATA_HEALTH,
      data: {
        ...DATA_HEALTH.data,
        items: [
          ...DATA_HEALTH.data.items,
          {
            ...repeatedItem,
            message: "同一数据集的后续健康记录",
          },
        ],
        total: DATA_HEALTH.data.total + 1,
      },
    } satisfies DataHealthResponse;

    try {
      renderHealth({
        loadDataHealth: vi.fn().mockResolvedValue(repeatedDataHealth),
      });

      const table = await screen.findByRole("table", { name: "异常优先级列表" });
      expect(within(table).getAllByRole("row")).toHaveLength(4);
      expect(
        consoleError.mock.calls.some((call) =>
          call.some((value) => String(value).includes("same key")),
        ),
      ).toBe(false);
    } finally {
      consoleError.mockRestore();
    }
  });

  test("orders blocked and stale incidents before healthy components", async () => {
    renderHealth();
    expect(screen.getByRole("region", { name: "运行风险摘要" })).toHaveTextContent(
      "统计中",
    );
    expect(screen.getByRole("status", { name: "全局运行状态" })).toHaveTextContent(
      "状态核对中",
    );

    const incidentTable = await screen.findByRole("table", {
      name: "异常优先级列表",
    });
    const list = incidentTable.closest("section");
    if (list === null) throw new Error("incident table must belong to a section");
    const rows = within(list).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("OpenD 历史行情");
    expect(rows[0]).toHaveTextContent("阻断");
    expect(rows[1]).toHaveTextContent("基本面快照");
    expect(rows.every((row) => !row.textContent?.includes("feature_dag"))).toBe(true);
    expect(screen.getByRole("status", { name: "全局运行状态" })).toHaveTextContent(
      "阻断",
    );
    expect(screen.getByRole("status", { name: "全局数据状态" })).toHaveTextContent(
      "状态查询时间",
    );
    expect(screen.getByRole("status", { name: "全局数据状态" })).not.toHaveTextContent(
      "最后成功数据",
    );
  });

  test("shows OpenD permissions quotas point-in-time proof and training eligibility", async () => {
    renderHealth();

    await screen.findByRole("heading", { name: "模型与数据健康" });
    screen
      .getByText(/历史权限探测与训练资格/)
      .closest("details")
      ?.setAttribute("open", "");
    const matrix = await screen.findByRole("table", { name: "OpenD 数据能力矩阵" });
    const capabilityPanel = screen.getByRole("region", { name: "OpenD 能力" });
    expect(within(capabilityPanel).getByTitle("degraded")).toHaveTextContent(
      "数据状态 降级",
    );
    expect(
      within(screen.getByRole("region", { name: "模型矩阵" })).getByTitle("live"),
    ).toHaveTextContent("服务状态 实时");
    expect(within(matrix).getByText("request_history_kline")).toBeVisible();
    expect(within(matrix).getByText("历史行情 / 可用")).toHaveAttribute(
      "title",
      "historical / available",
    );
    expect(within(matrix).getByText("剩余 80 / 总额 100（安全预算 60）")).toBeVisible();
    expect(within(matrix).getByText("点时可证明 / 可进入历史训练")).toBeVisible();
    expect(within(matrix).getByText("仅前向保存 / 不可进入历史训练")).toBeVisible();
    expect(within(matrix).getByText("禁用相关特征")).toHaveAttribute(
      "title",
      "disable_feature",
    );
    expect(
      within(matrix).getByText("未返回历史起点 → 2026-07-18T12:58:00Z"),
    ).toBeVisible();
    expect(matrix).not.toHaveTextContent("只能从项目启用后保存");
    expect(screen.getByText("OpenD 9.4.5208")).toBeVisible();
  });

  test("shows exact model lineage data revision health and universe history", async () => {
    renderHealth();

    const models = await screen.findByRole("table", { name: "模型任务矩阵" });
    expect(within(models).getByText("方向预测")).toHaveAttribute(
      "title",
      "forecast_direction",
    );
    expect(within(models).getByText("lightgbm-v5")).toBeVisible();
    expect(within(models).getByText("patchtst-shadow-v1")).toBeVisible();
    expect(within(models).getByText("样本外技能门禁未通过")).toHaveAttribute(
      "title",
      "oos_skill_gate_failed",
    );

    screen
      .getByText(/完整数据健康与谱系/)
      .closest("details")
      ?.setAttribute("open", "");
    const data = screen.getByRole("table", { name: "数据健康明细" });
    expect(within(data).getAllByText("dataset-r42").length).toBeGreaterThan(0);
    expect(within(data).getByText("部分完整 / 92.0%")).toHaveAttribute(
      "title",
      "partial",
    );
    expect(within(data).getAllByTitle("2026-07-17T20:00:00Z").length).toBeGreaterThan(
      0,
    );

    const universes = screen.getByRole("table", { name: "标的池版本历史" });
    expect(within(universes).getByText("universe-2026q3")).toBeVisible();
    expect(within(universes).getAllByText("100")).toHaveLength(2);
    expect(within(universes).getByText("季度流动性评审")).toBeVisible();
    expect(within(universes).getByText("静态种子池，已披露幸存者偏差")).toBeVisible();
  });

  test("contains one failed source without blanking healthy sections", async () => {
    renderHealth({
      loadCapabilities: vi.fn().mockRejectedValue(new Error("capability offline")),
    });

    screen
      .getByText(/历史权限探测与训练资格/)
      .closest("details")
      ?.setAttribute("open", "");
    expect(
      await screen.findByRole("alert", { name: "OpenD 能力不可用" }),
    ).toBeVisible();
    const summary = screen.getByRole("region", { name: "运行风险摘要" });
    expect(within(summary).getAllByText("未知")).toHaveLength(1);
    expect(screen.getByRole("table", { name: "模型任务矩阵" })).toBeVisible();
    screen
      .getByText(/完整数据健康与谱系/)
      .closest("details")
      ?.setAttribute("open", "");
    expect(screen.getByRole("table", { name: "数据健康明细" })).toBeVisible();
    expect(screen.getByRole("table", { name: "标的池版本历史" })).toBeVisible();
    expect(screen.getByRole("status", { name: "全局运行状态" })).toHaveTextContent(
      "阻断",
    );
  });
});

test("preserves expanded incident source details when switching language", () => {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn() });
  try {
    render(
      <I18nProvider>
        <LanguageSelect />
        <IncidentList items={DATA_HEALTH.data.items} />
      </I18nProvider>,
    );
    const summary = screen.getAllByText("原始详情")[0]!;
    fireEvent.click(summary);
    const details = summary.closest("details")!;
    expect(details).toHaveAttribute("open");
    fireEvent.change(screen.getByRole("combobox", { name: "界面语言" }), {
      target: { value: "en-US" },
    });
    expect(screen.getAllByText("Original details")[0]!.closest("details")).toBe(
      details,
    );
    expect(details).toHaveAttribute("open");
  } finally {
    vi.unstubAllGlobals();
  }
});
