import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, test, vi } from "vitest";

import { I18nProvider, useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
import { M7AuditPanel } from "./M7AuditPanel";
import * as m7Lifecycle from "./M7LifecyclePanel";
import { IndependentTrainingStatus, M7LifecyclePanel } from "./M7LifecyclePanel";

type M7Status = components["schemas"]["M7LifecycleStatus"];
type TrainingOperation = components["schemas"]["UpdateOperation"];
type PartialResearch = components["schemas"]["M7PartialResearchAvailable"];
type Bundle = M7Status["capabilities"][number]["bundle_id"];

const STATUS = {
  schema_version: "m7-lifecycle-status-v1",
  source_status: "partial",
  recipe_id: "candidate-recipe-seed100-targeted-v10",
  latest_training_run_id: "m7-run-001",
  latest_generated_at: "2026-08-24T16:00:00Z",
  executor_configured: false,
  disclaimer: "实验 Challenger；前瞻盲测未完成前不可晋升，不可作为正式预测或交易信号。",
  capabilities: [
    {
      bundle_id: "direction",
      development_state: "passed",
      shadow_state: "frozen",
      blind_state: "collecting",
      promotion_state: "blind_collecting",
      package_id: "m7-direction-001",
      package_sha256: "a".repeat(64),
      model_components: ["catboost-h1", "patchtst-h5", "dlinear-h20"],
      blend_weights: ["catboost=0.5", "patchtst=0.5"],
      blind_window_id: "blind-001",
      incumbent_id: "direction-v1",
      blind_available_anchors: 21,
      blind_mature_labels: 1,
      blind_missing_anchors: 2,
      blind_next_scheduled_session: "2026-09-24",
      blind_last_available_anchor: "2026-09-23",
      blind_report_hash: "b".repeat(64),
      blind_required_anchors: 77,
      promotion_allowed: false,
      is_active_champion: false,
      block_reasons: ["m7_blind_anchor_count_incomplete"],
      evidence_sections: {
        baseline_deltas: {
          status: "completed",
          reason: null,
          payload: {
            "1": {
              status: "passed",
              metrics: {
                candidate_brier_mean: 0.62,
                incumbent_brier_mean: 0.66,
                incumbent_fold_wins: 4,
                ece_fold_passes: 4,
              },
            },
          },
        },
        bootstrap_results: {
          status: "completed",
          reason: null,
          payload: { delta_brier_upper_90: -0.001 },
        },
        feature_ablation: {
          status: "completed",
          reason: null,
          payload: {
            rows: [
              {
                feature_set: "core_v2",
                status: "completed",
                delta_symbol_equal_brier_vs_core: 0,
              },
              {
                feature_set: "full_daily",
                status: "completed",
                delta_symbol_equal_brier_vs_core: -0.02,
              },
            ],
          },
        },
        explanation_manifest: {
          status: "completed",
          reason: null,
          payload: {
            status: "not_run",
            reason: "development_gate_not_publishable",
          },
        },
        economic_diagnostics: {
          status: "completed",
          reason: null,
          payload: {
            horizons: {
              "1": {
                candidate: {
                  net_return: 0.08,
                  annualized_sharpe: 0.9,
                  maximum_drawdown: -0.05,
                  average_gross_exposure: 0.7,
                  average_turnover: 0.2,
                },
              },
            },
          },
        },
        blind_report: {
          status: "completed",
          reason: null,
          payload: {
            schema_version: "m7-blind-review-evidence-v1",
            non_causal_caveat: true,
            reliability: [
              ...["down", "flat", "up"].flatMap((className, classIndex) => [
                {
                  horizon: 1,
                  class_name: className,
                  model: "candidate",
                  bin_index: 6,
                  lower_bound: 0.6,
                  upper_bound: 0.7,
                  mean_prediction: 0.64 + classIndex * 0.01,
                  observed_frequency: 0.62 + classIndex * 0.01,
                  sample_count: 88 + classIndex,
                },
                {
                  horizon: 1,
                  class_name: className,
                  model: "incumbent",
                  bin_index: 5,
                  lower_bound: 0.5,
                  upper_bound: 0.6,
                  mean_prediction: 0.55,
                  observed_frequency: 0.52,
                  sample_count: 91,
                },
              ]),
            ],
          },
        },
      },
    },
    {
      bundle_id: "range_exit_20",
      development_state: "not_started",
      shadow_state: "not_frozen",
      blind_state: "not_started",
      promotion_state: "not_eligible",
      package_id: null,
      package_sha256: null,
      model_components: [],
      blend_weights: [],
      blind_window_id: null,
      incumbent_id: null,
      blind_available_anchors: 0,
      blind_mature_labels: 0,
      blind_missing_anchors: 0,
      blind_required_anchors: 77,
      promotion_allowed: false,
      is_active_champion: false,
      block_reasons: ["m7_shadow_package_unavailable"],
    },
  ],
  drift: {
    state: "warning",
    consecutive_daily_warning_sessions: 3,
    consecutive_mature_weekly_breaches: 0,
    reason: "daily_drift_warning_streak_incomplete",
    last_updated_at: "2026-08-25T22:00:00Z",
    affected_scopes: ["direction"],
    affected_inputs: ["feature:atr_pct_20", "prediction:direction_h1_up"],
    retryability: "automatic",
    recommended_action: "investigate_inputs_and_wait_for_confirmation",
    feature_warning: true,
    prediction_warning: true,
    outcome_warning: false,
    core_feature_psi_fraction_above_020: 0.25,
    missingness_rate: 0.01,
    coverage_rate: 0.99,
    ood_rate: 0.04,
  },
  recent_events: [
    {
      event_id: "m7-audit-00000001",
      event_type: "shadow_frozen",
      recorded_at: "2026-08-24T16:00:00Z",
      actor: "model-training-worker",
      run_id: "m7-run-001",
      bundle_id: "direction",
      detail: { package_id: "m7-direction-001" },
    },
  ],
  errors: [],
} satisfies M7Status;

const READY_STATUS = {
  ...STATUS,
  executor_configured: true,
} satisfies M7Status;

const QUEUED_OPERATION = {
  candidate_generation: 10,
  candidate_recipe_version: "candidate-recipe-seed100-targeted-v10",
  created_at: "2026-08-28T02:00:00Z",
  no_change: false,
  operation_id: "model-training-v7-test",
  operation_type: "model_training",
  phases: [],
  progress_percent: 0,
  requested_bundles: ["direction"],
  retry_attempt: 0,
  status: "queued",
  updated_at: "2026-08-28T02:00:00Z",
} satisfies TrainingOperation;

function renderWithQuery(children: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>,
  );
}

afterEach(cleanup);

const independentSummary = {
  run_id: "training-20260921T190600Z-30e27d55",
  state: "completed",
  source_status: "ready",
  selected_for_research: true,
  completed_at: "2026-09-28T14:15:40Z",
  feature_as_of: "2026-09-14T20:00:00Z",
  development_data_cutoff: "2026-06-29",
  main_model_version: "live-model-20260921193943-r3-90125fc0",
  main_passed_tasks: 12,
  main_total_tasks: 17,
  m7_run_id: "m7-training-20260921T190600Z-30e27d55",
  m7_gate_status: "failed",
  m7_failed_gates: ["horizon_1"],
  errors: [],
} satisfies NonNullable<M7Status["independent_training_summary"]>;

test("shows independent completion separately from gates and historical lifecycle", () => {
  render(<IndependentTrainingStatus summary={independentSummary} />);
  expect(screen.getByText("独立完整训练：已完成")).toBeVisible();
  expect(screen.getByText("12/17 项通过质量门槛；这不是准确率。")).toBeVisible();
  expect(screen.getByText("未通过")).toBeVisible();
  expect(screen.getByText("2026-06-29")).toBeVisible();
  expect(screen.getByText(/2026\/0?9\/28/)).toBeVisible();
  expect(screen.getByText(/2026\/0?9\/14/)).toBeVisible();
  expect(screen.getByText(/独立的历史证据/)).toBeVisible();
  expect(screen.queryByText(/60%/)).not.toBeInTheDocument();
});

test("keeps independent completion visible alongside historical audit evidence", async () => {
  renderWithQuery(
    <M7AuditPanel
      loadStatus={() =>
        Promise.resolve({ ...STATUS, independent_training_summary: independentSummary })
      }
    />,
  );
  expect(await screen.findByText("独立完整训练：已完成")).toBeVisible();
  expect(screen.getByText("12/17 项通过质量门槛；这不是准确率。")).toBeVisible();
});

test("shows unavailable independent source without a completed claim", () => {
  render(
    <IndependentTrainingStatus
      summary={{
        ...independentSummary,
        source_status: "unavailable",
        errors: ["research_source_report_hash_mismatch"],
      }}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("独立训练来源不可用");
  expect(screen.queryByText("独立完整训练：已完成")).not.toBeInTheDocument();
});

test("labels an older failed operation as history after independent completion", async () => {
  renderWithQuery(
    <M7LifecyclePanel
      loadStatus={() =>
        Promise.resolve({
          ...READY_STATUS,
          independent_training_summary: independentSummary,
        })
      }
      loadWorkerAction={() =>
        Promise.resolve({
          detail: "offline",
          last_heartbeat: null,
          stale_after_seconds: 10,
          state: "offline",
        })
      }
      loadLatestTrainingAction={() =>
        Promise.resolve({
          ...QUEUED_OPERATION,
          status: "failed",
          error_code: "model_training_failed",
          updated_at: "2026-08-30T02:22:43Z",
        })
      }
    />,
  );
  expect(await screen.findByText("历史生命周期训练失败")).toBeVisible();
  expect(screen.queryByText("最近一次训练失败")).not.toBeInTheDocument();
});

test("binds the Research training action to generation ten", () => {
  expect(m7Lifecycle).toHaveProperty(
    "M7_CANDIDATE_RECIPE_VERSION",
    "candidate-recipe-seed100-targeted-v10",
  );
});

describe("M7 lifecycle visibility", () => {
  test("shows challenger disclaimers, independent capability progress, and disabled execution", async () => {
    const user = userEvent.setup();
    renderWithQuery(
      <M7LifecyclePanel
        actionsEnabled={false}
        loadStatus={() => Promise.resolve(STATUS)}
      />,
    );

    expect(await screen.findByText("实验 Challenger")).toBeVisible();
    expect(screen.getByText("前瞻盲测未完成")).toBeVisible();
    expect(screen.getByText("不可作为正式预测或交易信号")).toBeVisible();
    const direction = screen.getByRole("group", { name: "方向概率能力" });
    expect(within(direction).getByText("21 / 77")).toBeVisible();
    expect(within(direction).getByText("成熟标签 1 / 77")).toBeVisible();
    expect(within(direction).getByText("缺失锚点 2")).toBeVisible();
    expect(within(direction).getByText("下一采集 2026-09-24")).toBeVisible();
    expect(within(direction).getAllByText("H1")).toHaveLength(2);
    expect(within(direction).getByText("0.6200")).toBeVisible();
    expect(within(direction).getByText("模型解释")).toBeVisible();
    expect(within(direction).getByText("经济诊断 ； 研究用途")).toBeVisible();
    expect(
      within(direction).getByText("仅表示模型关联，不构成因果解释。"),
    ).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "前瞻盲测" }));
    expect(within(direction).getByText("前瞻可靠性 ； H1")).toBeVisible();
    const reliability = within(direction).getByRole("table", {
      name: "方向概率 H1 前瞻可靠性数据",
    });
    expect(reliability).toHaveTextContent("向上");
    expect(reliability).toHaveTextContent("候选");
    expect(reliability).toHaveTextContent("64.0%");
    expect(reliability).toHaveTextContent("62.0%");
    expect(reliability).toHaveTextContent("88");
    expect(screen.getByRole("button", { name: "执行器未接入" })).toBeDisabled();
  });

  test("falls back from invalid M7 URL state and writes the valid selection", async () => {
    window.history.replaceState(
      {},
      "",
      "/research/US.AAPL?m7_run=stale&m7_bundle=bogus&m7_horizon=99&m7_tab=bogus",
    );

    renderWithQuery(
      <M7LifecyclePanel
        actionsEnabled={false}
        loadStatus={() => Promise.resolve(STATUS)}
      />,
    );

    expect(
      await screen.findByText("M7 视图参数无效，已回退到最新有效证据。"),
    ).toBeVisible();
    const params = new URLSearchParams(window.location.search);
    expect(params.get("m7_run")).toBe("m7-run-001");
    expect(params.get("m7_bundle")).toBe("direction");
    expect(params.get("m7_horizon")).toBe("1");
    expect(params.get("m7_tab")).toBe("development");
  });

  test("preserves an exact research snapshot URL until the user changes the M7 view", async () => {
    const exactUrl =
      "/research/US.AAPL?as_of=2026-07-10T20%3A00%3A00Z&snapshot_id=snapshot-001";
    window.history.replaceState({}, "", exactUrl);

    renderWithQuery(
      <M7LifecyclePanel
        actionsEnabled={false}
        loadStatus={() => Promise.resolve(STATUS)}
      />,
    );

    expect(await screen.findByText("实验 Challenger")).toBeVisible();
    expect(`${window.location.pathname}${window.location.search}`).toBe(exactUrl);
  });

  test("distinguishes active training from the latest complete report", async () => {
    renderWithQuery(
      <M7LifecyclePanel
        loadLatestTrainingAction={() =>
          Promise.resolve({
            ...QUEUED_OPERATION,
            active_trial_id: "m7-outer-01.m7-catboost_multiclass-01.h1",
            current_stage: "candidate_search",
            phases: [
              {
                detail: "候选 13/280 · outer 1/4 · H1 · CPU catboost_multiclass",
                stage: "candidate_search",
                started_at: "2026-08-28T02:00:10Z",
                status: "running",
              },
            ],
            progress_percent: 10,
            started_at: "2026-08-28T02:00:10Z",
            status: "running",
            updated_at: "2026-08-28T02:00:10Z",
          })
        }
        loadStatus={() =>
          Promise.resolve({
            ...READY_STATUS,
            latest_generated_at: null,
            latest_training_run_id: null,
          })
        }
        loadWorkerAction={() =>
          Promise.resolve({
            detail: "model_worker_ready",
            last_heartbeat: "2026-08-28T02:00:10Z",
            stale_after_seconds: 10,
            state: "ready",
          })
        }
      />,
    );

    expect(
      await screen.findByRole("button", { name: "训练运行中 10%" }),
    ).toBeDisabled();
    expect(
      screen.getByText("候选 13/280 · outer 1/4 · H1 · CPU catboost_multiclass"),
    ).toBeInTheDocument();
    expect(screen.getByText("最近活动 2026-08-28T02:00:10Z")).toBeVisible();
    expect(
      screen.getByText("当前 trial m7-outer-01.m7-catboost_multiclass-01.h1"),
    ).toBeVisible();
    expect(screen.getByText(/最近完整生命周期报告 尚无/)).toBeVisible();
    expect(screen.queryByText(/最近运行 尚无/)).not.toBeInTheDocument();
  });

  test("warns when an active training worker is stale instead of showing progress", async () => {
    renderWithQuery(
      <M7LifecyclePanel
        loadLatestTrainingAction={() =>
          Promise.resolve({
            ...QUEUED_OPERATION,
            current_stage: "candidate_search",
            phases: [
              {
                detail: "候选 13/280 · outer 1/4 · H1 · CPU catboost_multiclass",
                stage: "candidate_search",
                started_at: "2026-08-28T02:00:10Z",
                status: "running",
              },
            ],
            progress_percent: 12,
            status: "running",
          })
        }
        loadStatus={() => Promise.resolve(READY_STATUS)}
        loadWorkerAction={() =>
          Promise.resolve({
            detail: "model_worker_stale",
            last_heartbeat: "2026-08-28T01:55:10Z",
            stale_after_seconds: 10,
            state: "stale",
          })
        }
      />,
    );

    expect(
      await screen.findByRole("alert", { name: "训练 Worker 失联" }),
    ).toBeVisible();
    expect(screen.getByText(/不把历史百分比伪装为训练中/)).toBeVisible();
    expect(screen.getByRole("button", { name: "训练 Worker 失联" })).toBeDisabled();
  });

  test("surfaces the latest terminal training failure without exposing local paths", async () => {
    renderWithQuery(
      <M7LifecyclePanel
        loadLatestTrainingAction={() =>
          Promise.resolve({
            ...QUEUED_OPERATION,
            current_stage: "candidate_search",
            diagnostic_detail:
              "[Errno 22] Invalid argument: 'C:/example/stock-prediction\\var\\live\\stderr.log'",
            error_code: "model_training_failed",
            finished_at: "2026-08-30T02:22:43Z",
            status: "failed",
            updated_at: "2026-08-30T02:22:43Z",
          })
        }
        loadStatus={() => Promise.resolve(READY_STATUS)}
        loadWorkerAction={() =>
          Promise.resolve({
            detail: "model_worker_offline",
            last_heartbeat: null,
            stale_after_seconds: 10,
            state: "offline",
          })
        }
      />,
    );

    expect(await screen.findByText("最近一次训练失败")).toBeVisible();
    expect(screen.getByText("model_training_failed | candidate_search")).toBeVisible();
    expect(screen.queryByText(/Prediction_Stock/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "训练 Worker 未就绪" })).toBeDisabled();
  });

  test("starts direction only and cancels the resulting operation", async () => {
    const user = userEvent.setup();
    const startCalls: Bundle[][] = [];
    const cancelCalls: string[] = [];

    renderWithQuery(
      <M7LifecyclePanel
        cancelTrainingAction={(operationId) => {
          cancelCalls.push(operationId);
          return Promise.resolve({
            ...QUEUED_OPERATION,
            cancel_requested_at: "2026-08-28T02:01:00Z",
            status: "cancel_requested",
            updated_at: "2026-08-28T02:01:00Z",
          });
        }}
        loadLatestTrainingAction={() => Promise.resolve(null)}
        loadStatus={() => Promise.resolve(READY_STATUS)}
        loadWorkerAction={() =>
          Promise.resolve({
            detail: "model_worker_ready",
            last_heartbeat: "2026-08-28T02:00:00Z",
            stale_after_seconds: 10,
            state: "ready",
          })
        }
        startTrainingAction={(bundles) => {
          startCalls.push(bundles);
          return Promise.resolve(QUEUED_OPERATION);
        }}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "启动方向训练" }));
    expect(startCalls).toEqual([["direction"]]);

    await user.click(await screen.findByRole("button", { name: "取消当前训练" }));
    expect(cancelCalls).toEqual(["model-training-v7-test"]);
  });

  test("renders verified partial probabilities beside the research-only warning", async () => {
    const partial = {
      availability: "available",
      aggregate_hash: "c".repeat(64),
      as_of: "2026-08-31",
      bundle_id: "direction",
      candidate_matrix_hash: "d".repeat(64),
      contributing_folds: ["m7-outer-01"],
      dataset_revision: "dataset-v1",
      feature_input_hash: "e".repeat(64),
      feature_set_version: "m7-v5",
      predictions: [
        {
          symbol: "US.AAPL",
          horizon: 1,
          down_probability: 0.1,
          flat_probability: 0.2,
          up_probability: 0.7,
        },
      ],
      recipe_id: "candidate-recipe-seed100-targeted-v10",
      required_folds: 4,
      run_id: "model-training-v7-test",
      training_devices: ["cpu", "cuda:0"],
      universe_version: "seed-100-v1",
    } satisfies PartialResearch;

    renderWithQuery(
      <M7LifecyclePanel
        loadLatestTrainingAction={() => Promise.resolve(QUEUED_OPERATION)}
        loadPartialResultAction={() => Promise.resolve(partial)}
        loadStatus={() => Promise.resolve(READY_STATUS)}
        loadWorkerAction={() =>
          Promise.resolve({
            detail: "model_worker_ready",
            last_heartbeat: "2026-08-28T02:00:00Z",
            stale_after_seconds: 10,
            state: "ready",
          })
        }
        symbol="US.AAPL"
      />,
    );

    expect(
      await screen.findByText("训练未完成 ； 仅供研究 ； 非正式预测"),
    ).toBeVisible();
    expect(screen.getByText(/有效 fold 1\/4/)).toBeVisible();
    expect(screen.getByText("70.0%")).toBeVisible();
  });

  test("renders drift and hash-chained lifecycle events for Health", async () => {
    renderWithQuery(<M7AuditPanel loadStatus={() => Promise.resolve(STATUS)} />);

    expect(screen.getByText("M7 生命周期与漂移审计")).toBeVisible();
    expect(await screen.findByText("连续日警告 3 / 5")).toBeVisible();
    expect(screen.getByText("特征 PSI 警告")).toBeVisible();
    expect(screen.getByText("预测分布警告")).toBeVisible();
    expect(screen.getByText("覆盖率")).toBeVisible();
    expect(screen.getByText("99.0%")).toBeVisible();
    expect(screen.getByText("feature:atr_pct_20")).toBeVisible();
    expect(screen.getByText("自动重试")).toBeVisible();
    expect(screen.getByText("Shadow 包冻结")).toBeVisible();
    expect(screen.getByText("m7-audit-00000001")).toBeVisible();
  });

  test("labels missing drift evidence as unavailable instead of healthy", async () => {
    const unavailableStatus = {
      ...STATUS,
      source_status: "unavailable",
      drift: {
        ...STATUS.drift,
        affected_inputs: [],
        affected_scopes: [],
        consecutive_daily_warning_sessions: 0,
        consecutive_mature_weekly_breaches: 0,
        core_feature_psi_fraction_above_020: null,
        coverage_rate: null,
        feature_warning: null,
        last_updated_at: null,
        missingness_rate: null,
        ood_rate: null,
        outcome_warning: null,
        prediction_warning: null,
        reason: "m7_drift_evidence_unavailable",
        recommended_action: "continue_monitoring",
        retryability: "not_applicable",
        state: "none",
      },
      recent_events: [],
    } satisfies M7Status;

    renderWithQuery(
      <M7AuditPanel loadStatus={() => Promise.resolve(unavailableStatus)} />,
    );

    expect(await screen.findByText("证据不可用")).toBeVisible();
    expect(screen.getByText("尚无漂移证据")).toBeVisible();
    expect(screen.getByText("特征分布不可用")).toBeVisible();
    expect(screen.getByText("预测分布不可用")).toBeVisible();
    expect(screen.getByText("成熟结果不可用")).toBeVisible();
    expect(screen.queryByText("正常监控")).not.toBeInTheDocument();
    expect(screen.queryByText("unavailable")).not.toBeInTheDocument();
  });
});

function LocaleControl() {
  const i18n = useI18n();
  return <button onClick={() => i18n.setLocale("en-US")}>English</button>;
}

test("language switch retains selected M7 evidence and never starts training", async () => {
  localStorage.clear();
  const loadStatus = vi.fn().mockResolvedValue(READY_STATUS);
  const loadLatestTrainingAction = vi.fn().mockResolvedValue(null);
  const loadWorkerAction = vi
    .fn()
    .mockResolvedValue({
      state: "ready",
      detail: "ready",
      last_heartbeat: "2026-08-28T02:00:00Z",
      stale_after_seconds: 10,
    });
  const startTrainingAction = vi.fn().mockResolvedValue(QUEUED_OPERATION);
  renderWithQuery(
    <I18nProvider>
      <LocaleControl />
      <M7LifecyclePanel
        loadStatus={loadStatus}
        loadLatestTrainingAction={loadLatestTrainingAction}
        loadWorkerAction={loadWorkerAction}
        startTrainingAction={startTrainingAction}
      />
    </I18nProvider>,
  );
  await screen.findByText("实验 Challenger");
  await userEvent.click(screen.getByRole("tab", { name: "前瞻盲测" }));
  const originalUrl = window.location.href;
  const calls = [
    loadStatus.mock.calls.length,
    loadLatestTrainingAction.mock.calls.length,
    loadWorkerAction.mock.calls.length,
  ];
  await userEvent.click(screen.getByRole("button", { name: "English" }));
  expect(screen.getByRole("tab", { name: "Forward blind test" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(
    screen.getByText("Unavailable as production forecasts or trading signals"),
  ).toBeVisible();
  expect(window.location.href).toBe(originalUrl);
  expect(startTrainingAction).not.toHaveBeenCalled();
  expect([
    loadStatus.mock.calls.length,
    loadLatestTrainingAction.mock.calls.length,
    loadWorkerAction.mock.calls.length,
  ]).toEqual(calls);
  localStorage.clear();
});
