import { describe, expect, test } from "vitest";

import type { components } from "../../api/generated/schema";
import { buildOverviewFactSummary } from "./overview-summary";

type OverviewReadModel = components["schemas"]["OverviewReadModel"];

function summaryData(
  sessionReturns: readonly number[],
  regimeDistribution: OverviewReadModel["regime_distribution"],
  marketBreadth = 0.48,
) {
  return {
    market_breadth: marketBreadth,
    proxies: sessionReturns.map((sessionReturn, index) => ({
      session_return: sessionReturn,
      symbol: `US.TEST${index}`,
    })),
    regime_distribution: regimeDistribution,
  } as unknown as OverviewReadModel;
}

describe("buildOverviewFactSummary", () => {
  test("builds a deterministic sentence from existing overview facts", () => {
    const data = summaryData(
      [0.01, 0.006, -0.005, 0],
      [
        { count: 39, fraction: 0.39, regime: "downtrend" },
        { count: 34, fraction: 0.34, regime: "uptrend" },
      ],
    );

    expect(buildOverviewFactSummary(data)).toBe(
      "大盘代理 2 涨 1 跌 1 平；趋势偏空占比最高 39.0%；市场宽度 48.0%。",
    );
  });

  test("states missing proxy and regime facts without inventing values", () => {
    expect(buildOverviewFactSummary(summaryData([], []))).toBe(
      "暂无大盘代理行情；市场状态暂无完整分布；市场宽度 48.0%。",
    );
  });

  test("builds the English briefing from the same snapshot facts", () => {
    const data = summaryData(
      [0.01, -0.01, 0],
      [{ count: 4, fraction: 0.4, regime: "uptrend" }],
    );
    expect(buildOverviewFactSummary(data, "en-US")).toBe(
      "Market proxies: 1 up, 1 down, 1 unchanged; Uptrend has the largest share: 40.0%; market breadth 48.0%.",
    );
    expect(buildOverviewFactSummary(summaryData([], []), "en-US")).toContain(
      "No broad-market proxy prices",
    );
  });
});
