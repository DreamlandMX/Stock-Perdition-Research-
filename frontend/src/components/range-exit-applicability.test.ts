import { describe, expect, test } from "vitest";

import { rangeExitUnavailableLabel } from "./range-exit-applicability";

const rangeExitTask = {
  tasks: [{ task: "range_exit" }],
};

describe("range-exit applicability", () => {
  test("preserves exact one-day and regime-specific labels", () => {
    expect(rangeExitUnavailableLabel(undefined, 1, "range")).toBe(
      "不适用（1日任务不启用）",
    );
    expect(rangeExitUnavailableLabel(rangeExitTask, 5, "range")).toBe("候选输出未生成");
    expect(rangeExitUnavailableLabel(rangeExitTask, 20, "uptrend")).toBe(
      "不适用（当前非震荡）",
    );
    expect(rangeExitUnavailableLabel(undefined, 5, "range")).toBe(
      "该周期无候选退出任务",
    );
  });
});
