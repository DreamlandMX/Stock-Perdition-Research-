import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, test } from "vitest";

import type { components } from "../../api/generated/schema";
import { VerifiedResearchEvidence } from "./VerifiedResearchEvidence";

type ResearchModelEvidence = components["schemas"]["ResearchModelEvidence"];

afterEach(() => cleanup());

function evidence(
  horizon: 1 | 5 | 20,
  overrides: Partial<ResearchModelEvidence["price_projection"]> = {},
): ResearchModelEvidence {
  return {
    as_of: "2026-08-20T20:00:00Z",
    horizon,
    price_projection: {
      currency: "USD",
      endpoint_price_band: {
        q10: 95 + horizon,
        q25: 97 + horizon,
        q50: 100 + horizon,
        q75: 103 + horizon,
        q90: 105 + horizon,
      },
      endpoint_price_unavailable_reason: null,
      reference_price: 100,
      reference_price_at: "2026-08-20T20:00:00Z",
      stage_high_price_q50: null,
      stage_high_price_unavailable_reason: "not_published",
      stage_low_price_q50: null,
      stage_low_price_unavailable_reason: "not_published",
      ...overrides,
    },
    sample_abstain_reasons: [],
    scope: "verified",
    symbol: "US.SPY",
    tasks: [],
    values: { capital_gain_q50: horizon / 100 },
  };
}

describe("verified research discrete horizon evidence", () => {
  test("renders unconnected endpoint intervals without path or cone wording", () => {
    render(
      <VerifiedResearchEvidence
        entries={[evidence(1), evidence(5), evidence(20)]}
        notGeneratedReason="no_champion_candidate"
      />,
    );

    for (const horizon of [1, 5, 20]) {
      const endpoint = screen.getByRole("region", {
        name: `+${horizon} 日离散端点区间`,
      });
      expect(within(endpoint).getByText(`+${horizon} 个交易日`)).toBeVisible();
      expect(within(endpoint).getByText("原始 q10–q90")).toBeVisible();
      expect(within(endpoint).getByText("q50：中位情景，非目标价。")).toBeVisible();
    }
    expect(screen.queryByRole("img", { name: /情景锥/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/路径|插值|情景锥/)).not.toBeInTheDocument();
  });

  test("keeps price bands with inconsistent anchors as separate endpoints", () => {
    render(
      <VerifiedResearchEvidence
        entries={[evidence(1), evidence(5, { reference_price: 101 }), evidence(20)]}
        notGeneratedReason="no_champion_candidate"
      />,
    );

    expect(screen.getByRole("region", { name: "+1 日离散端点区间" })).toHaveTextContent(
      "$100.00",
    );
    expect(screen.getByRole("region", { name: "+5 日离散端点区间" })).toHaveTextContent(
      "$101.00",
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByRole("table", { name: "分窗口指标" })).toBeVisible();
  });

  test("suppresses duplicate-horizon numbers independent of payload order", () => {
    const low = evidence(5);
    low.values = { capital_gain_q50: 0.05 };
    const high = evidence(5);
    high.values = { capital_gain_q50: 0.19 };
    const { container, rerender } = render(
      <VerifiedResearchEvidence
        entries={[low, high]}
        notGeneratedReason="no_champion_candidate"
      />,
    );
    const first = container.textContent;
    expect(screen.getByRole("alert")).toHaveTextContent("重复期限");
    expect(container).not.toHaveTextContent("+5.00%");
    expect(container).not.toHaveTextContent("+19.00%");

    rerender(
      <VerifiedResearchEvidence
        entries={[high, low]}
        notGeneratedReason="no_champion_candidate"
      />,
    );
    expect(container.textContent).toBe(first);
    expect(container).not.toHaveTextContent("+5.00%");
    expect(container).not.toHaveTextContent("+19.00%");
  });

  test("keeps the 1/5/20 matrix explicit and degrades nonfinite evidence", () => {
    const invalid = evidence(5, {
      endpoint_price_band: {
        q10: Number.NaN,
        q25: Number.NEGATIVE_INFINITY,
        q50: Number.POSITIVE_INFINITY,
        q75: 108,
        q90: 110,
      },
    });
    invalid.values = {
      breakout_up_probability: Number.NaN,
      capital_gain_q50: Number.POSITIVE_INFINITY,
    };
    const { container } = render(
      <VerifiedResearchEvidence
        entries={[invalid]}
        notGeneratedReason="no_champion_candidate"
      />,
    );

    const table = screen.getByRole("table", { name: "分窗口指标" });
    expect(table.parentElement).toHaveClass("evidence-ledger__matrix-scroll");
    expect(screen.getByRole("columnheader", { name: "+1 日" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "+5 日" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "+20 日" })).toBeVisible();
    expect(table).toHaveTextContent("未发布");
    expect(screen.queryByRole("img", { name: /收盘价情景锥/ })).not.toBeInTheDocument();
    expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
  });

  test("uses exact range-exit applicability reasons across 1/5/20 columns", () => {
    const one = evidence(1);
    const five = evidence(5);
    const twenty = evidence(20);
    twenty.tasks = [
      {
        baseline_wins: null,
        calibration_version: null,
        component_evaluated_at: null,
        coverage_windows_passed: null,
        evaluation_run_id: "eval-range-exit-20",
        fold_count: 3,
        gate_passed: true,
        horizon: 20,
        model_version: "range-exit-v1",
        pinball_wins: null,
        positive_cost_windows: null,
        scope: "verified",
        task: "range_exit",
      },
    ];
    render(
      <VerifiedResearchEvidence
        entries={[one, five, twenty]}
        notGeneratedReason="no_champion_candidate"
        regime="uptrend"
      />,
    );

    const row = screen.getByRole("row", { name: /区间退出概率/ });
    expect(within(row).getByText("不适用（1日任务不启用）")).toBeVisible();
    expect(within(row).getByText("该周期无候选退出任务")).toBeVisible();
    expect(within(row).getByText("不适用（当前非震荡）")).toBeVisible();
  });

  test("distinguishes original and calibrated prices using unrounded returns", () => {
    const one = evidence(1);
    one.values = { calibrated_lower: -0.0105123, calibrated_upper: 0.0092123 };
    const five = evidence(5);
    five.values = { calibrated_lower: 0, calibrated_upper: 0.1 };
    const twenty = evidence(20);
    twenty.values = { calibrated_lower: Number.NaN, calibrated_upper: 0.2 };
    render(
      <VerifiedResearchEvidence
        entries={[one, five, twenty]}
        notGeneratedReason="no_champion_candidate"
      />,
    );
    const first = screen.getByRole("region", { name: "+1 日离散端点区间" });
    expect(within(first).getByText("$96.00 – $106.00")).toBeVisible();
    expect(within(first).getByText("$98.95 – $100.92")).toBeVisible();
    const fifth = screen.getByRole("region", { name: "+5 日离散端点区间" });
    expect(within(fifth).getAllByText("$100.00 – $110.00")).toHaveLength(2);
    expect(
      within(screen.getByRole("region", { name: "+20 日离散端点区间" })).getByText(
        "未提供",
      ),
    ).toBeVisible();
  });

  test("shows sample rejection reasons only in their horizon beside metrics", () => {
    const five = evidence(5);
    five.sample_abstain_reasons = ["out_of_distribution", "data_quality"];
    render(
      <VerifiedResearchEvidence
        entries={[evidence(1), five]}
        notGeneratedReason="no_champion_candidate"
      />,
    );
    const cells = within(
      screen.getByRole("row", { name: /本样本拒绝原因/ }),
    ).getAllByRole("cell");
    expect(cells[0]).toHaveTextContent("未报告拒绝原因");
    expect(cells[1]).toHaveTextContent("分布外样本、数据质量不足");
    expect(cells[2]).toHaveTextContent("未提供");
    expect(
      screen.getByText(/研究组件通过历史门槛；本标的近期效果未验证/),
    ).toBeVisible();
  });

  test("exposes actual task evidence in native details", async () => {
    const twenty = evidence(20);
    const task = {
      task: "mae",
      horizon: 20 as const,
      scope: "verified" as const,
      gate_passed: true,
      fold_count: 4,
      baseline_wins: null,
      pinball_wins: 4,
      coverage_windows_passed: 3,
      positive_cost_windows: 0,
      model_version: "mae20-model",
      evaluation_run_id: "mae20-run",
      calibration_version: "mae20-cal",
      evaluation_sample_count: 50398,
      evaluation_start: "2024-01-02",
      evaluation_end: "2026-02-06",
      worst_coverage: 0.7172,
      average_interval_width: 0.08284,
      average_pinball_loss: 0.006008,
      baseline_pinball_loss: 0.006247,
    };
    twenty.tasks = [task];
    render(
      <VerifiedResearchEvidence
        entries={[evidence(1), twenty]}
        notGeneratedReason="no_champion_candidate"
      />,
    );
    const summary = screen.getByText("+20 日任务评估证据");
    const details = summary.closest("details")!;
    expect(details.open).toBe(false);
    await userEvent.click(summary);
    expect(details.open).toBe(true);
    const taskRegion = screen.getByRole("region", {
      name: "最大不利波动；+20 日评估",
    });
    for (const text of [
      "71.72%",
      "8.28%",
      "50,398",
      "3/4",
      "4/4",
      "0/4",
      "0.006008",
      "0.006247",
      "mae20-model",
      "mae20-run",
      "mae20-cal",
      "2024-01-02 至 2026-02-06",
    ]) {
      expect(within(taskRegion).getByText(text)).toBeVisible();
    }
    expect(details).toHaveTextContent("评估标的池汇总，不是 US.SPY 专属表现");
    expect(details).toHaveTextContent("区间退出仅涵盖震荡状态样本");
    await userEvent.click(summary);
    expect(details.open).toBe(false);
  });

  test("keeps absent old-snapshot evaluation fields explicitly unavailable", async () => {
    const five = evidence(5);
    five.tasks = [
      {
        task: "return_fwd",
        horizon: 5,
        scope: "verified",
        gate_passed: true,
        fold_count: 4,
        model_version: "legacy-model",
        evaluation_run_id: "legacy-run",
        calibration_version: null,
      },
    ];
    render(
      <VerifiedResearchEvidence
        entries={[five]}
        notGeneratedReason="no_champion_candidate"
      />,
    );
    await userEvent.click(screen.getByText("+5 日任务评估证据"));
    const task = screen.getByRole("region", { name: "收益分布；+5 日评估" });
    for (const label of [
      "最差窗口覆盖率",
      "评估样本数（标的池）",
      "统计胜过基线窗口",
      "校准版本",
      "平均分位数损失（pinball）",
    ]) {
      expect(within(task).getByText(label).nextElementSibling).toHaveTextContent(
        "未提供",
      );
    }
    expect(within(task).getByText("未提供 至 未提供")).toBeVisible();
  });

  test("defines first-touch breakout with fixed buffered boundaries and ambiguity exclusion", async () => {
    render(
      <VerifiedResearchEvidence
        entries={[evidence(1)]}
        notGeneratedReason="no_champion_candidate"
      />,
    );
    expect(screen.getByRole("rowheader", { name: "首次向上突破" })).toBeVisible();
    expect(screen.getByRole("rowheader", { name: "首次向下突破" })).toBeVisible();
    await userEvent.click(screen.getByText("如何理解收益、突破与阶段价格"));
    expect(screen.getByText(/固定锚点通道/)).toHaveTextContent("通道上沿 +0.25 ATR");
    expect(screen.getByText(/固定锚点通道/)).toHaveTextContent("通道下沿 −0.25 ATR");
    expect(screen.getByText(/固定锚点通道/)).toHaveTextContent(
      "同一天首次越过两侧的歧义样本排除",
    );
  });
});
