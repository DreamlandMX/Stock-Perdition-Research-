import { describe, expect, expectTypeOf, test } from "vitest";

import type { components } from "../api/generated/schema";
import {
  decodeResearchUrlState,
  DEFAULT_RESEARCH_URL_STATE,
  encodeResearchUrlState,
} from "./url-state";
import type { ResearchUrlState } from "./url-state";

describe("research URL state codec", () => {
  test("round-trips snapshot, versions, filters, paging and backtest state", () => {
    const state: ResearchUrlState = {
      asOf: "2026-07-10T16:05:00-04:00",
      snapshotId: "snapshot-20260710-1605",
      revisionNo: 7,
      symbol: "NVDA",
      horizon: 20,
      predictionBasis: "forecast",
      returnBasis: "total_return",
      versions: {
        datasetRevision: "dataset-r17",
        featureSetVersion: "features-v8",
        universeVersion: "universe-2026q3",
        modelVersion: "SL-2.6.1",
        calibrationVersion: "CAL-2026-07-08",
        strategyVersion: "strategy-v4",
        splitVersion: "split-v6",
      },
      filters: {
        sector: "Information Technology",
        assetType: "equity",
        regime: "uptrend",
        minimumProbability: 0.62,
        maximumQ10: -0.08,
        confidence: "high",
        dataStatus: "partial",
        effectiveAbstain: false,
      },
      sort: {
        key: "executable_up_probability",
        direction: "desc",
      },
      page: {
        number: 3,
        size: 50,
      },
      backtest: {
        evaluationWindowId: "outer-2025",
        foldId: "fold-4",
        costScenario: "12bps",
      },
      chart: {
        timeframe: "60m",
        from: "2026-06-01T13:30:00Z",
        to: "2026-07-10T20:00:00Z",
        adjustment: "total_return",
      },
      warnings: [],
    };

    const encoded = encodeResearchUrlState(state);
    const decoded = decodeResearchUrlState(encoded);

    expect(encoded.get("feature_set_version")).toBe("features-v8");
    expect(encoded.get("return_basis")).toBe("total_return");
    expect(encoded.get("fold")).toBe("fold-4");
    expect(encoded.get("timeframe")).toBe("60m");
    expect(encoded.get("from")).toBe("2026-06-01T13:30:00Z");
    expect(encoded.get("to")).toBe("2026-07-10T20:00:00Z");
    expect(encoded.get("adjustment")).toBe("total_return");
    expect(decoded).toEqual(state);
  });

  test("uses the generated screener return basis and rejects total return", () => {
    const decoded = decodeResearchUrlState(
      new URLSearchParams({ return_basis: "total_return" }),
      { route: "screen" },
    );

    expectTypeOf(decoded.returnBasis).toEqualTypeOf<
      components["schemas"]["ScreenReturnBasis"]
    >();
    expect(decoded.returnBasis).toBe("executable");
    expect(decoded.warnings).toEqual([
      expect.objectContaining({
        parameter: "return_basis",
        rejectedValue: "total_return",
      }),
    ]);
    expect(
      encodeResearchUrlState(decoded, { route: "screen" }).get("return_basis"),
    ).toBeNull();
  });

  test("defaults legacy screener bookmarks to research and round-trips every display mode", () => {
    const legacy = decodeResearchUrlState(new URLSearchParams(), {
      route: "screen",
    });

    expect(legacy.mode).toBe("research");
    expect(encodeResearchUrlState(legacy, { route: "screen" }).get("mode")).toBe(
      "research",
    );

    for (const mode of ["research", "challenger", "formal"] as const) {
      const decoded = decodeResearchUrlState(new URLSearchParams({ mode }), {
        route: "screen",
      });
      expect(decoded.mode).toBe(mode);
      expect(encodeResearchUrlState(decoded, { route: "screen" }).get("mode")).toBe(
        mode,
      );
    }

    const invalid = decodeResearchUrlState(new URLSearchParams({ mode: "legacy" }), {
      route: "screen",
    });
    expect(invalid.mode).toBe("research");
    expect(invalid.warnings).toEqual([
      expect.objectContaining({ parameter: "mode", rejectedValue: "legacy" }),
    ]);
  });

  test("round-trips research-mode screener sort keys from the generated API contract", () => {
    for (const sort of [
      "rule_return_5",
      "rule_return_20",
      "rule_atr_pct_20",
      "rule_channel_position_20",
      "rule_volume_z_20",
      "verified_q10",
      "verified_q50",
    ] as const) {
      const decoded = decodeResearchUrlState(new URLSearchParams({ sort }), {
        route: "screen",
      });
      expect(decoded.sort.key).toBe(sort);
      expect(decoded.warnings).toHaveLength(0);
    }
  });

  test("drops formal-only legacy controls when a screener bookmark defaults to research", () => {
    const decoded = decodeResearchUrlState(
      new URLSearchParams({
        confidence: "high",
        direction: "desc",
        effective_abstain: "false",
        max_q10: "-0.08",
        min_probability: "0.62",
        page: "4",
        prediction_basis: "forecast",
        return_basis: "capital_gain",
        sort: "executable_up_probability",
      }),
      { route: "screen" },
    );

    expect(decoded).toMatchObject({
      mode: "research",
      page: { number: 1 },
      predictionBasis: "executable",
      returnBasis: "executable",
      sort: { direction: "asc", key: "symbol" },
      filters: {
        confidence: undefined,
        effectiveAbstain: undefined,
        maximumQ10: undefined,
        minimumProbability: undefined,
      },
    });
    expect(decoded.warnings).toHaveLength(1);
    expect(decoded.warnings[0]).toMatchObject({ parameter: "mode" });
    expect(decoded.warnings[0]?.message).toContain("正式预测");
    expect(
      Object.fromEntries(encodeResearchUrlState(decoded, { route: "screen" })),
    ).toMatchObject({ mode: "research", sort: "symbol", direction: "asc" });
    for (const parameter of [
      "confidence",
      "effective_abstain",
      "max_q10",
      "min_probability",
      "page",
      "prediction_basis",
      "return_basis",
    ]) {
      expect(encodeResearchUrlState(decoded, { route: "screen" }).has(parameter)).toBe(
        false,
      );
    }
  });

  test("round-trips every frozen screener filter and rejects unavailable as a confidence floor", () => {
    const params = new URLSearchParams({
      as_of: "2026-07-10T20:05:00Z",
      snapshot_id: "snapshot-20260710-1605",
      revision_no: "3",
      dataset_revision: "bars-r42",
      feature_set_version: "features-v7",
      universe_version: "us-100-2026q3",
      model_version: "SL-2.6.1",
      calibration_version: "CAL-2026-07-08",
      horizon: "20",
      prediction_basis: "forecast",
      return_basis: "capital_gain",
      regime: "range",
      sector: "technology",
      asset_type: "stock",
      min_probability: "0.62",
      max_q10: "-0.08",
      confidence: "high",
      mode: "formal",
      serving_status: "delayed",
      sort: "breakout_up_probability",
      direction: "asc",
      page: "3",
      page_size: "25",
    });
    const state = decodeResearchUrlState(params, { route: "screen" });

    expect(
      Object.fromEntries(encodeResearchUrlState(state, { route: "screen" })),
    ).toEqual(Object.fromEntries(params));

    const unavailable = decodeResearchUrlState(
      new URLSearchParams({ confidence: "unavailable" }),
      { route: "screen" },
    );
    expect(unavailable.filters.confidence).toBeUndefined();
    expect(unavailable.warnings).toEqual([
      expect.objectContaining({ parameter: "confidence" }),
    ]);
  });

  test("defaults unknown chart enums and rejects invalid chart dates", () => {
    const decoded = decodeResearchUrlState(
      new URLSearchParams({
        timeframe: "5m",
        adjustment: "future_adjusted",
        from: "yesterday",
        to: "tomorrow",
      }),
    );

    expect(decoded.chart).toEqual({
      timeframe: "1d",
      from: undefined,
      to: undefined,
      adjustment: "raw",
    });
    expect(decoded.warnings.map((warning) => warning.parameter)).toEqual([
      "timeframe",
      "adjustment",
      "from",
      "to",
    ]);
  });

  test("normalizes legacy split-adjusted research bookmarks to published raw history", () => {
    const decoded = decodeResearchUrlState(
      new URLSearchParams({ adjustment: "split_adjusted" }),
    );

    expect(decoded.chart.adjustment).toBe("raw");
    expect(decoded.warnings).toEqual([
      expect.objectContaining({
        parameter: "adjustment",
        rejectedValue: "split_adjusted",
      }),
    ]);
    expect(encodeResearchUrlState(decoded).get("adjustment")).toBeNull();
  });

  test("rejects a chart range whose start follows its end", () => {
    const decoded = decodeResearchUrlState(
      new URLSearchParams({
        from: "2026-07-11T20:00:00Z",
        to: "2026-07-10T20:00:00Z",
      }),
    );

    expect(decoded.chart).toMatchObject({ from: undefined, to: undefined });
    expect(decoded.warnings.map((warning) => warning.parameter)).toEqual([
      "from",
      "to",
    ]);
  });

  test("defaults rejected enums and reports every rejection", () => {
    const decoded = decodeResearchUrlState(
      new URLSearchParams({
        horizon: "2",
        prediction_basis: "certainty",
        return_basis: "price_target",
        regime: "guaranteed_bull",
        confidence: "certain",
        serving_status: "freshish",
        sort: "alpha_magic",
        direction: "sideways",
      }),
    );

    expect(decoded).toMatchObject({
      horizon: DEFAULT_RESEARCH_URL_STATE.horizon,
      predictionBasis: DEFAULT_RESEARCH_URL_STATE.predictionBasis,
      returnBasis: DEFAULT_RESEARCH_URL_STATE.returnBasis,
      filters: {
        regime: undefined,
        confidence: undefined,
        dataStatus: undefined,
      },
      sort: DEFAULT_RESEARCH_URL_STATE.sort,
    });
    expect(decoded.warnings.map((warning) => warning.parameter)).toEqual([
      "horizon",
      "prediction_basis",
      "return_basis",
      "regime",
      "confidence",
      "serving_status",
      "sort",
      "direction",
    ]);
    expect(decoded.warnings.every((warning) => warning.message.length > 0)).toBe(true);
  });

  test("rejects unsafe paging and numeric filter values with warnings", () => {
    const decoded = decodeResearchUrlState(
      new URLSearchParams({
        revision_no: "-1",
        min_probability: "1.2",
        max_q10: "not-a-number",
        effective_abstain: "sometimes",
        page: "0",
        page_size: "500",
      }),
    );

    expect(decoded).toMatchObject({
      revisionNo: undefined,
      filters: {
        minimumProbability: undefined,
        maximumQ10: undefined,
        effectiveAbstain: undefined,
      },
      page: DEFAULT_RESEARCH_URL_STATE.page,
    });
    expect(decoded.warnings.map((warning) => warning.parameter)).toEqual([
      "revision_no",
      "min_probability",
      "max_q10",
      "effective_abstain",
      "page",
      "page_size",
    ]);
  });

  test("omits defaults while preserving explicit reproducibility fields", () => {
    const encoded = encodeResearchUrlState(DEFAULT_RESEARCH_URL_STATE);

    expect(encoded.toString()).toBe("");
  });
});
