import { describe, expect, test } from "vitest";
import { buildOptionPulseOption } from "./option-pulse-chart";

describe("option pulse chart", () => {
  test("keeps missing daily horizons as gaps at their actual offsets", () => {
    const option = buildOptionPulseOption({
      bars: [{ at: "2026-10-13T20:00:00Z", open: 100, close: 100, low: 99, high: 101 }],
      profile: [],
      levels: { spot: 100, call: null, put: null },
      timeframe: "1d",
      cone: [
        {
          at: "2026-10-13T20:00:00Z",
          lower68: 100,
          upper68: 100,
          lower80: 100,
          upper80: 100,
        },
        {
          at: "2026-10-13T20:00:00Z",
          horizon_days: 2,
          lower68: 98,
          upper68: 102,
          lower80: 97,
          upper80: 103,
        },
        {
          at: "2026-10-13T20:00:00Z",
          horizon_days: 10,
          lower68: 90,
          upper68: 110,
          lower80: 85,
          upper80: 115,
        },
      ],
    });
    const axes = option.xAxis as { data: string[] }[];
    expect(axes[0]?.data[1]).toBe("T+1");
    expect(axes[0]?.data[10]).toBe("T+10");
    const series = option.series as { name?: string; data?: unknown[] }[];
    const lower = series.find((s) => s.name === "80% 下界")?.data;
    expect(lower?.[1]).toBeNull();
    expect(lower?.[2]).toBe(97);
    expect(lower?.[10]).toBe(85);
  });
  test("uses one price range and preserves absent turnover instead of drawing zero", () => {
    const option = buildOptionPulseOption({
      bars: [
        {
          at: "2026-10-13T14:00:00Z",
          open: 100,
          close: 101,
          low: 99,
          high: 102,
          forming: true,
        },
      ],
      profile: [{ strike: 100, call: 5000, put: null }],
      cone: [],
      levels: { spot: 101, call: 100, put: null },
      timeframe: "5m",
    });
    const axes = option.yAxis as { min: number; max: number }[];
    expect(axes[0]?.min).toBe(axes[1]?.min);
    expect(axes[0]?.max).toBe(axes[1]?.max);
    const series = option.series as { name: string; data?: unknown[] }[];
    expect(series.find((s) => s.name === "Call 成交额")?.data).toEqual([[100, 5000]]);
    expect(series.find((s) => s.name === "Put 成交额")?.data).toEqual([]);
  });
});

test("localizes chart series, reference levels and tooltips while retaining numerical data", () => {
  const input = {
    bars: [{ at: "2026-10-13T14:00:00Z", open: 100, close: 101, low: 99, high: 102 }],
    profile: [{ strike: 100, call: 5000, put: null }],
    cone: [],
    levels: { spot: 101, call: 100, put: null },
    timeframe: "5m" as const,
  };
  const zh = buildOptionPulseOption(input, "zh-CN");
  const en = buildOptionPulseOption({ ...input, locale: "en-US" });
  type Series = {
    name?: string;
    data?: unknown[];
    markLine?: { data?: { name?: string }[][] };
  };
  const zhSeries = zh.series as Series[];
  const enSeries = en.series as Series[];
  expect(enSeries.map((series) => series.data)).toEqual(
    zhSeries.map((series) => series.data),
  );
  expect(enSeries.some((series) => series.name === "Call turnover")).toBe(true);
  expect(enSeries.some((series) => series.name === "Stock candlesticks")).toBe(true);
  const candle = enSeries.find((series) => series.name === "Stock candlesticks");
  expect(candle?.markLine?.data?.[0]?.[0]?.name).toBe("Reference price");
  const tooltip = en.tooltip as {
    formatter: (value: { dataIndex: number }[]) => string;
  };
  expect(tooltip.formatter([{ dataIndex: 0 }])).toContain(
    "Open $100.00; close $101.00",
  );
});
