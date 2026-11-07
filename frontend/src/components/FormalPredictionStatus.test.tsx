import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import type { components } from "../api/generated/schema";
import { FormalPredictionStatus } from "./FormalPredictionStatus";
import { I18nProvider } from "../i18n/context";
import { LanguageSelect } from "../i18n/LanguageSelect";

type ResolvedSnapshot = components["schemas"]["ResolvedSnapshot"];
type ModelResponse = components["schemas"]["ResearchResponse_ModelMatrixReadModel_"];
type TrainingReadiness = components["schemas"]["ModelTrainingReadiness"];
type TrainingOperation = components["schemas"]["UpdateOperation"];

const SNAPSHOT = {
  as_of: "2026-07-28T20:00:00Z",
  calibration_version: null,
  dataset_revision: "bars-r42",
  feature_set_version: "features-v7",
  model_version: null,
  prediction_readiness: "data_only",
  research_readiness: "partial_verified",
  revision_no: 3,
  snapshot_id: "LIVE-20260728",
  universe_version: "us-100-2026q3",
} satisfies ResolvedSnapshot;

const ROWS = [
  {
    asset_group: "all",
    baseline_version: "baseline-v1",
    calibration_version: "cal-v1",
    challenger_version: "direction-v5",
    champion_version: null,
    gate_passed: true,
    horizon: 5,
    status: "candidate",
    task: "forecast_direction",
    unavailable_reason: null,
  },
  {
    asset_group: "all",
    baseline_version: "baseline-v1",
    calibration_gate_passed: true,
    calibration_version: null,
    challenger_version: "direction-v20",
    champion_version: null,
    failure_codes: ["promotion_window_failed"],
    gate_passed: false,
    horizon: 20,
    promotion_window_passed: false,
    stability_gate_passed: true,
    statistical_gate_passed: true,
    status: "candidate",
    task: "forecast_direction",
    unavailable_reason: "baseline_not_beaten",
  },
] satisfies components["schemas"]["ModelTaskMatrixRow"][];

function modelResponse(
  snapshot: ResolvedSnapshot,
  passedTasks: number,
  promotionWindowStatus?: "passed" | "failed" | "consumed",
): ModelResponse {
  return {
    data: {
      candidate_summary: {
        all_gates_passed: passedTasks === 17,
        calibration_version: "live-cal-candidate",
        execution_readiness: snapshot.execution_readiness ?? "blocked",
        failed_tasks: passedTasks === 17 ? [] : ["forecast_direction:20"],
        failure_codes: passedTasks === 17 ? [] : ["promotion_window_failed"],
        generated_at: "2026-07-28T20:15:00Z",
        model_version: "live-model-candidate",
        passed_tasks: passedTasks,
        ...(promotionWindowStatus === undefined
          ? {}
          : { promotion_window_status: promotionWindowStatus }),
        run_id: "live-train-20260728",
        total_tasks: 17,
      },
      rows: passedTasks === 17 ? ROWS.slice(0, 1) : ROWS,
    },
    data_status: "degraded",
    effective_abstain: true,
    evaluated_at: "2026-07-28T20:15:00Z",
    exclusion_reason: ["no_champion_candidate"],
    generated_at: "2026-07-28T20:15:00Z",
    request_id: "models-1",
    resolved_snapshot: snapshot,
    serving_status: "partial",
  };
}

function trainingReadiness(
  state: "collecting" | "ready" | "consumed" = "ready",
): TrainingReadiness {
  return {
    anchor_sessions_collected: state === "collecting" ? 20 : 77,
    automatic_training_at: "2026-11-14T15:00:00Z",
    can_submit: state === "ready",
    can_submit_candidate_training: true,
    can_submit_promotion: state === "ready",
    candidate_data_cutoff: "2026-06-29",
    candidate_passed_tasks: 11,
    candidate_total_tasks: 17,
    candidate_training_state: "development_failed",
    candidate_training_action: "available",
    latest_candidate_recipe_version: "candidate-recipe-v1",
    next_candidate_recipe_version: "candidate-recipe-v2",
    data_cutoff: state === "collecting" ? "2026-07-28" : "2026-11-13",
    detail: `promotion_window_${state}`,
    fully_labeled_sessions: state === "collecting" ? 0 : 77,
    label_complete_through: "2026-11-13",
    label_horizon: 20,
    required_anchor_sessions: 77,
    required_fully_labeled_sessions: 77,
    state,
    window_end: "2026-10-16",
    window_id: "promotion-20260630-20261016-v1",
    window_start: "2026-06-30",
  };
}

function mockApi(
  response: ModelResponse,
  readiness: TrainingReadiness = trainingReadiness(),
  options: {
    latest?: TrainingOperation | null;
    submit?: TrainingOperation;
  } = {},
) {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url.includes("/api/v1/models")) {
      return Promise.resolve(jsonResponse(response));
    }
    if (url.includes("/api/v1/operations/model-training/latest")) {
      return Promise.resolve(jsonResponse(options.latest ?? null));
    }
    if (url.includes("/api/v1/operations/model-training/worker")) {
      return Promise.resolve(
        jsonResponse({
          detail: "model_worker_ready",
          last_heartbeat: "2026-07-28T20:15:00Z",
          stale_after_seconds: 10,
          state: "ready",
        }),
      );
    }
    if (url.includes("/api/v1/operations/model-promotion/latest")) {
      return Promise.resolve(jsonResponse(null));
    }
    if (url.includes("/api/v1/operations/model-promotion/worker")) {
      return Promise.resolve(
        jsonResponse({
          detail: "model_promotion_worker_ready",
          last_heartbeat: "2026-07-28T20:15:00Z",
          stale_after_seconds: 10,
          state: "ready",
        }),
      );
    }
    if (url.includes("/api/v1/operations/model-training/readiness")) {
      return Promise.resolve(jsonResponse(readiness));
    }
    if (
      url.endsWith("/api/v1/operations/model-training") &&
      (init?.method ?? (input instanceof Request ? input.method : "GET")) === "POST"
    ) {
      if (options.submit === undefined) {
        throw new Error("Unexpected model training submission");
      }
      return Promise.resolve(jsonResponse(options.submit));
    }
    throw new Error(`Unexpected URL ${url}`);
  });
}

function trainingOperation(
  disposition: "created" | "reused_active" | "reused_completed",
): TrainingOperation {
  return {
    candidate_generation: 2,
    candidate_recipe_version: "candidate-recipe-v2",
    created_at: "2026-07-29T18:53:02Z",
    current_stage: disposition === "reused_completed" ? "champion_publication" : null,
    no_change: disposition === "reused_completed",
    operation_id: "model-training-v2",
    operation_type: "model_training",
    phases: [],
    progress_percent: disposition === "reused_completed" ? 100 : 0,
    retry_attempt: 0,
    status: disposition === "reused_completed" ? "succeeded_data_only" : "queued",
    submission_disposition: disposition,
    updated_at: "2026-07-29T18:53:02Z",
  };
}

function jsonResponse(value: unknown) {
  return new Response(JSON.stringify(value), {
    headers: { "content-type": "application/json" },
    status: 200,
  });
}

function renderStatus(snapshot: ResolvedSnapshot) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <FormalPredictionStatus snapshot={snapshot} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("keeps manually activated failed gates truthful in both languages without new requests", async () => {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn() });
  const snapshot = {
    ...SNAPSHOT, model_version: "manual-test", calibration_version: "manual-cal",
    prediction_readiness: "forecast_ready", research_readiness: "manual_unverified",
    activation_basis: "manual_override", execution_readiness: "blocked",
  } satisfies ResolvedSnapshot;
  mockApi(modelResponse(snapshot, 13));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<I18nProvider><MemoryRouter><QueryClientProvider client={client}>
    <LanguageSelect /><FormalPredictionStatus snapshot={snapshot} />
  </QueryClientProvider></MemoryRouter></I18nProvider>);
  expect(await screen.findByText(/人工启用，未通过验证。13\/17/)).toBeVisible();
  const requestCount = vi.mocked(globalThis.fetch).mock.calls.length;
  fireEvent.change(screen.getByRole("combobox", { name: "界面语言" }), { target: { value: "en-US" } });
  expect(screen.getByText(/Manually activated, unverified. 13 of 17/)).toBeVisible();
  expect(screen.queryByText(/All 17 forecast tasks passed and were published/)).not.toBeInTheDocument();
  expect(vi.mocked(globalThis.fetch).mock.calls).toHaveLength(requestCount);
});

test("shows strict 9/17 gates and an immediate candidate-training action", async () => {
  mockApi(modelResponse(SNAPSHOT, 9));
  renderStatus(SNAPSHOT);

  const status = screen.getByRole("status", {
    name: "正式预测发布状态",
  });
  expect(await within(status).findByText("最近开发 9/17")).toBeVisible();
  expect(
    within(status).getByRole("progressbar", {
      name: "正式预测门禁进度 53%",
    }),
  ).toHaveAttribute("aria-valuenow", "53");
  expect(within(status).getByText("预测门禁已通过（1）")).toBeVisible();
  expect(within(status).getByText("预测门禁尚未通过（1）")).toBeVisible();
  expect(within(status).getAllByText(/一次性晋升窗口未胜出/)).toHaveLength(2);
  expect(
    within(status).getByRole("button", { name: "开始第 2 轮候选优化" }),
  ).toBeEnabled();
  expect(within(status).getByText("预计开放日期")).toBeVisible();
  expect(
    within(status).getByText(
      "候选今天可训练；正式发布最早在 2026-11-13 后完成独立盲测",
    ),
  ).toBeVisible();
  expect(
    within(status).getByText("候选训练现在可手动触发；独立证据完整后自动或手动盲测"),
  ).toBeVisible();
  expect(
    within(status).getByRole("list", { name: "正式预测训练阶段" }),
  ).toHaveTextContent("数据修复标签健康候选搜索样本外验证晋升窗口Champion 发布");
});

test("keeps a completed 11/17 result trainable while promotion data collects", async () => {
  mockApi(modelResponse(SNAPSHOT, 11), trainingReadiness("collecting"));
  renderStatus(SNAPSHOT);

  const status = screen.getByRole("status", {
    name: "正式预测发布状态",
  });
  expect(await within(status).findByText("最近开发 11/17")).toBeVisible();
  expect(
    within(status).getByText(
      "第 2 轮候选优化可运行：17 项同批次重训，5 项失败任务使用扩展搜索",
    ),
  ).toBeVisible();
  expect(
    within(status).getByRole("button", { name: "开始第 2 轮候选优化" }),
  ).toBeEnabled();
  const nextWindow = within(status).getByRole("region", {
    name: "下一次正式晋升进度",
  });
  expect(within(nextWindow).getByText("20/77")).toBeVisible();
  expect(within(nextWindow).getByText("0/77")).toBeVisible();
  expect(
    within(nextWindow).getByText(/最早自动盲测：\s*2026\/11\/14 10:00 美东时间/),
  ).toBeVisible();
  expect(
    within(status).getByText("候选训练现在可手动触发；独立证据完整后自动或手动盲测"),
  ).toBeVisible();
  expect(
    within(nextWindow).getByRole("button", { name: "开始正式晋升盲测" }),
  ).toBeDisabled();
  expect(within(status).getByText("晋升窗口").closest("li")).toHaveAttribute(
    "data-state",
    "pending",
  );
});

test("enables promotion only when independent evidence is ready", async () => {
  mockApi(modelResponse(SNAPSHOT, 11), trainingReadiness("ready"));
  renderStatus(SNAPSHOT);

  const status = screen.getByRole("status", {
    name: "正式预测发布状态",
  });
  expect(
    await within(status).findByText(
      "第 2 轮候选优化可运行：17 项同批次重训，5 项失败任务使用扩展搜索",
    ),
  ).toBeVisible();
  expect(
    within(status).getByRole("button", { name: "开始第 2 轮候选优化" }),
  ).toBeEnabled();
  expect(
    within(status).getByRole("button", { name: "开始正式晋升盲测" }),
  ).toBeEnabled();
});

test("disables candidate training after the finite second recipe is exhausted", async () => {
  const readiness = {
    ...trainingReadiness("collecting"),
    can_submit_candidate_training: false,
    candidate_training_action: "exhausted",
    candidate_training_block_reason: "no_additional_candidate_recipe_registered",
    candidate_passed_tasks: 13,
    latest_candidate_recipe_version: "candidate-recipe-v2",
    next_candidate_recipe_version: null,
  } satisfies TrainingReadiness;
  mockApi(modelResponse(SNAPSHOT, 13), readiness);
  renderStatus(SNAPSHOT);

  const status = screen.getByRole("status", {
    name: "正式预测发布状态",
  });
  expect(
    await within(status).findByText(
      "第 2 轮有限配方已经训练完成；尚未注册第 3 轮，不会继续原样重跑",
    ),
  ).toBeVisible();
  expect(
    within(status).getByRole("button", { name: "当前配方已训练完成" }),
  ).toBeDisabled();
});

test("submits recipe v2 and immediately identifies a newly created task", async () => {
  const user = userEvent.setup();
  const submitted = trainingOperation("created");
  mockApi(modelResponse(SNAPSHOT, 12), trainingReadiness("collecting"), {
    submit: submitted,
  });
  renderStatus(SNAPSHOT);

  const button = await screen.findByRole("button", {
    name: "开始第 2 轮候选优化",
  });
  await user.click(button);

  expect(await screen.findByText(/第 2 轮训练已启动/)).toHaveTextContent(
    "model-training-v2 ； candidate-recipe-v2",
  );
  const fetchMock = vi.mocked(globalThis.fetch);
  const submission = fetchMock.mock.calls.find(([input]) =>
    (input instanceof Request ? input.url : String(input)).endsWith(
      "/api/v1/operations/model-training",
    ),
  );
  const submissionBody =
    submission?.[0] instanceof Request
      ? await submission[0].clone().text()
      : submission?.[1]?.body;
  expect(submissionBody).toBe(
    JSON.stringify({
      candidate_recipe_version: "candidate-recipe-v2",
      trigger: "manual",
    }),
  );
});

test("shows the queued or running v2 task instead of offering a duplicate", async () => {
  const readiness = {
    ...trainingReadiness("collecting"),
    can_submit_candidate_training: false,
    candidate_training_action: "active",
    candidate_training_block_reason: "candidate_training_already_active",
  } satisfies TrainingReadiness;
  mockApi(modelResponse(SNAPSHOT, 12), readiness, {
    latest: trainingOperation("created"),
  });
  renderStatus(SNAPSHOT);

  expect(
    await screen.findByText(/任务 model-training-v2 ； candidate-recipe-v2/),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "训练进行中" })).toBeDisabled();
});

test("explains when a submit races and connects to the active v2 task", async () => {
  const user = userEvent.setup();
  mockApi(modelResponse(SNAPSHOT, 12), trainingReadiness("collecting"), {
    submit: trainingOperation("reused_active"),
  });
  renderStatus(SNAPSHOT);

  await user.click(
    await screen.findByRole("button", {
      name: "开始第 2 轮候选优化",
    }),
  );

  expect(await screen.findByText(/已连接到正在运行的训练/)).toBeVisible();
});

test("keeps an actionable retry state when v2 training fails", async () => {
  const failed = {
    ...trainingOperation("created"),
    diagnostic_detail: "candidate_search_failed",
    error_code: "model_training_failed",
    status: "failed",
  } satisfies TrainingOperation;
  mockApi(modelResponse(SNAPSHOT, 12), trainingReadiness("collecting"), {
    latest: failed,
  });
  renderStatus(SNAPSHOT);

  expect(await screen.findByText("第 2 轮训练失败")).toBeVisible();
  expect(
    screen.getByText("model_training_failed ； 修复原因后可重试同一有限配方"),
  ).toBeVisible();
  expect(screen.getByText("candidate_search_failed")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "开始第 2 轮候选优化" })).toBeEnabled();
});

test("keeps formal forecasts visible when the independent execution gate is blocked", async () => {
  const snapshot = {
    ...SNAPSHOT,
    calibration_version: "cal-v17",
    execution_readiness: "blocked",
    model_version: "forecast-bundle-v17",
    prediction_readiness: "forecast_ready",
    snapshot_id: "LIVE-20260728-FORMAL",
  } satisfies ResolvedSnapshot;
  mockApi(modelResponse(snapshot, 17));
  renderStatus(snapshot);

  const status = screen.getByRole("status", {
    name: "正式预测发布状态",
  });
  expect(await within(status).findByText("最近开发 17/17")).toBeVisible();
  expect(within(status).getByText("正式预测已发布")).toBeVisible();
  expect(
    within(status).getByText(
      "17 项预测任务已通过并发布；独立交易门禁未通过，暂无可执行交易信号。",
    ),
  ).toBeVisible();
});

test("distinguishes a released forecast with an execution-ready strategy", async () => {
  const snapshot = {
    ...SNAPSHOT,
    calibration_version: "cal-v17",
    execution_readiness: "ready",
    model_version: "forecast-bundle-v17",
    prediction_readiness: "forecast_ready",
    snapshot_id: "LIVE-20260728-EXECUTABLE",
  } satisfies ResolvedSnapshot;
  mockApi(modelResponse(snapshot, 17));
  renderStatus(snapshot);

  const status = screen.getByRole("status", {
    name: "正式预测发布状态",
  });
  expect(await within(status).findByText("最近开发 17/17")).toBeVisible();
  expect(
    within(status).getByText("17 项预测任务已通过同批次门禁；独立交易门禁也已通过。"),
  ).toBeVisible();
});
