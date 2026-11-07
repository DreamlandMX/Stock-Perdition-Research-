import { describe, expect, test } from "vitest";
import type { components } from "../../api/generated/schema";
import {
  buildOverviewHistory,
  normalizeHistoryBars,
  sparklinePath,
} from "./overview-history";
import type { MarketPoint } from "./overview-terminal";

const cutoff = "2026-06-01T20:00:00Z";
function bars(count: number): components["schemas"]["HistoryBar"][] {
  return Array.from({ length: count }, (_, i) => ({
    at: new Date(Date.UTC(2026, 0, i + 1)).toISOString(),
    open: i + 10,
    high: i + 12,
    low: i + 9,
    close: i + 11,
    volume: 100,
    regime: "uptrend",
    data_gap: false,
  }));
}
const point: MarketPoint = {
  symbol: "US.SPY",
  name: "SPY",
  asset_type: "etf",
  sector: "etf",
  regime: "uptrend",
  previous_regime: "range",
  regime_changed: true,
  breakout_up_rule: false,
  breakout_down_rule: false,
  intraday_available: false,
};

describe("overview daily history", () => {
  test("sorts and deduplicates, drops future and invalid prices, retains gaps and 130 bars", () => {
    const input = bars(140);
    input[130]!.data_gap = true;
    const result = normalizeHistoryBars(
      [
        ...input.reverse(),
        input[2]!,
        { ...input[0]!, at: "2027-01-01", close: 9 },
        { ...input[1]!, close: 0 },
      ],
      cutoff,
    );
    expect(result).toHaveLength(130);
    expect(result[0]!.at < result.at(-1)!.at).toBe(true);
    expect(result.some((bar) => bar.data_gap)).toBe(true);
  });
  test("computes moving average breadth, 60-day closing highs and sector paths on shared dates", () => {
    const result = buildOverviewHistory([point], { "US.SPY": bars(130) }, cutoff);
    expect(result.dates).toHaveLength(60);
    expect(result.breadth.at(-1)).toMatchObject({
      p20: 1,
      p50: 1,
      high: 1,
      low: 0,
      n60: 1,
    });
    expect(result.sectors[0]!.path[0]).toBe(0);
    expect(result.sectors[0]!.path.at(-1)).toBeGreaterThan(0);
    expect(result.regimes.uptrend.at(-1)).toBe(1);
  });
  test("does not count insufficient, gapped or absent history as zero", () => {
    const input = bars(130);
    input[120]!.data_gap = true;
    const result = buildOverviewHistory(
      [point, { ...point, symbol: "US.MISSING" }],
      { "US.SPY": input },
      cutoff,
    );
    expect(result.breadth.at(-1)).toMatchObject({
      p20: null,
      p50: null,
      high: null,
      n60: 0,
    });
    expect(result.excluded).toBe(1);
    const empty = buildOverviewHistory([point], {}, cutoff);
    expect(empty.dates).toEqual([]);
    expect(empty.sectors[0]!.path).toEqual([]);
  });
  test("zero or flat return remains valid and missing SPY does not invent rotation", () => {
    const input = bars(70).map((bar) => ({
      ...bar,
      open: 10,
      close: 10,
      high: 10,
      low: 10,
    }));
    const result = buildOverviewHistory(
      [{ ...point, symbol: "US.A" }],
      { "US.A": input },
      cutoff,
    );
    expect(result.breadth.at(-1)?.p20).toBe(0);
    expect(result.sectors[0]!.rotation).toEqual([]);
    expect(result.sectors[0]!.path.at(-1)).toBe(0);
  });
  test("sector paths do not bridge a missing middle benchmark trading date", () => {
    const complete = bars(130);
    const missing = complete.filter((_, i) => i !== 120);
    const result = buildOverviewHistory(
      [point, { ...point, symbol: "US.A", sector: "technology" }],
      { "US.SPY": complete, "US.A": missing },
      cutoff,
    );
    expect(
      result.sectors.find((s) => s.sector === "technology")?.path.at(-1),
    ).toBeNull();
  });
  test("one-bar SPY does not suppress a complete other-symbol history", () => {
    const complete = bars(130);
    const result = buildOverviewHistory(
      [point, { ...point, symbol: "US.A", sector: "technology" }],
      { "US.SPY": complete.slice(-1), "US.A": complete },
      cutoff,
    );
    expect(result.dates).toHaveLength(60);
    expect(result.breadth.at(-1)?.n20).toBe(1);
    expect(result.sectors.find((s) => s.sector === "technology")?.rotation).toEqual([]);
  });
  test("sparklines break at flagged and missing trading observations", () => {
    const complete = bars(5),
      gap = complete.map((bar, i) => ({ ...bar, data_gap: i === 2 }));
    expect(sparklinePath(gap, 100, 20).match(/M/g)).toHaveLength(2);
    expect(
      sparklinePath(
        complete.filter((_, i) => i !== 2),
        100,
        20,
        complete.map((b) => b.at.slice(0, 10)),
      ).match(/M/g),
    ).toHaveLength(2);
  });
});
