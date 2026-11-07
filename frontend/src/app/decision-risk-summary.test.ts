import { describe, expect, test } from "vitest";

import {
  Q10_RESEARCH_DISCLAIMER,
  buildDecisionRiskSummary,
} from "./decision-risk-summary";

describe("buildDecisionRiskSummary", () => {
  test("describes a positive median with materially stronger downside evidence", () => {
    const summary = buildDecisionRiskSummary({
      breakoutDownProbability: 0.216,
      breakoutUpProbability: 0.039,
      maeReturn: -0.034,
      mfeReturn: 0.03,
      q10Return: -0.051,
      q50Return: 0.01,
      q90Return: 0.056,
    });

    expect(summary.availability).toBe("available");
    expect(summary.breakoutSkew).toBe("downside");
    expect(summary.scenarioSkew).toBe("upside");
    expect(summary.headline).toBe("中位情景上涨 +1.0%，但向下突破概率更高");
    expect(summary.evidence).toContain("向上 / 向下突破 3.9% / 21.6%");
    expect(summary.q10Disclaimer).toBe(Q10_RESEARCH_DISCLAIMER);
  });

  test("derives returns and interval width from reference and band prices", () => {
    const summary = buildDecisionRiskSummary({
      q10Price: 90,
      q50Price: 105,
      q90Price: 112,
      referencePrice: 100,
    });

    expect(summary.medianReturn).toBeCloseTo(0.05);
    expect(summary.q10Return).toBeCloseTo(-0.1);
    expect(summary.q90Return).toBeCloseTo(0.12);
    expect(summary.intervalWidth).toBeCloseTo(0.22);
    expect(summary.breakoutSkew).toBe("unknown");
    expect(summary.scenarioSkew).toBe("upside");
  });

  test("returns a partial summary when the interval is incomplete", () => {
    const summary = buildDecisionRiskSummary({
      breakoutDownProbability: 0.14,
      breakoutUpProbability: 0.08,
      q50Return: 0.004,
    });

    expect(summary.availability).toBe("partial");
    expect(summary.missingReason).toContain("分位区间不完整");
    expect(summary.headline).toContain("向下突破概率更高");
  });

  test("does not invent a conclusion when all evidence is missing", () => {
    expect(buildDecisionRiskSummary({})).toMatchObject({
      availability: "unavailable",
      breakoutSkew: "unknown",
      evidence: [],
      headline: "当前证据不足，无法生成风险摘要",
      medianDirection: "unknown",
    });
  });

  test("rejects invalid quantile order and out-of-range probabilities", () => {
    expect(
      buildDecisionRiskSummary({
        breakoutUpProbability: 1.2,
        q10Return: 0.1,
        q50Return: 0,
        q90Return: -0.1,
      }),
    ).toMatchObject({
      availability: "invalid",
      intervalWidth: null,
      missingReason:
        "分位数必须满足 q10 ≤ q50 ≤ q90，概率必须在 0–100%，参考价必须大于 0。",
    });
  });

  test("rejects inconsistent price and return quantile sources instead of mixing them", () => {
    expect(
      buildDecisionRiskSummary({
        q10Price: 90,
        q10Return: -0.2,
        referencePrice: 100,
      }),
    ).toMatchObject({
      availability: "invalid",
      missingReason: "收益分位与当前价换算出的价格分位不一致，无法生成同口径风险摘要。",
    });
  });

  test("does not call scenario magnitude a breakout skew", () => {
    const summary = buildDecisionRiskSummary({
      q10Return: -0.2,
      q50Return: 0.01,
      q90Return: 0.05,
    });

    expect(summary.scenarioSkew).toBe("downside");
    expect(summary.breakoutSkew).toBe("unknown");
  });
});
