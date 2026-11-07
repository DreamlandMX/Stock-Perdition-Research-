import { describe, expect, test } from "vitest";

import {
  blockSpan,
  packSectorBlocks,
  packSectorRows,
  selectRepresentatives,
} from "./overview-map";
import type { MarketPoint } from "./overview-terminal";

function point(symbol: string, sector: string, sessionReturn: number): MarketPoint {
  return {
    asset_type: sector === "etf" ? "etf" : "stock",
    breakout_down_rule: false,
    breakout_up_rule: false,
    intraday_available: true,
    name: symbol,
    previous_regime: "range",
    regime: "range",
    regime_changed: false,
    return_20: 0,
    return_5: 0,
    sector,
    session_return: sessionReturn,
    symbol: `US.${symbol}`,
    volume_z_20: 0,
  };
}

function universe(counts: Record<string, number>) {
  return Object.entries(counts).flatMap(([sector, count]) =>
    Array.from({ length: count }, (_, index) =>
      point(
        `${sector.slice(0, 3).toUpperCase()}${index}`,
        sector,
        (index - count / 2) / 100,
      ),
    ),
  );
}

const SNAPSHOT_COUNTS = {
  communication: 1,
  consumer: 15,
  energy_materials: 9,
  etf: 28,
  financials: 12,
  healthcare: 13,
  industrials: 10,
  other: 97,
  technology: 19,
};

describe("representative market map", () => {
  test("v6 packs a nine-sector pool and a single-sector pool into nine-column rows", () => {
    for (const counts of [SNAPSHOT_COUNTS, { other: 204 }]) {
      const groups = selectRepresentatives(universe(counts), 1, null),
        packed = packSectorRows(groups);
      expect(packed.columns).toBe(9);
      expect(
        packed.blocks.reduce((sum, block) => sum + block.group.points.length, 0),
      ).toBe(36);
      expect(packed.blocks.every((block) => block.column + block.span - 1 <= 9)).toBe(
        true,
      );
    }
  });
  test("selects exactly 36 tiles, four per sector before redistributing", () => {
    const groups = selectRepresentatives(universe(SNAPSHOT_COUNTS), 1, null);
    const sizes = Object.fromEntries(
      groups.map((group) => [group.sector, group.points.length]),
    );

    expect(groups.reduce((sum, group) => sum + group.points.length, 0)).toBe(36);
    expect(sizes.communication).toBe(1);
    expect(Object.values(sizes).every((size) => size >= 1 && size <= 5)).toBe(true);
    expect(sizes.other).toBe(5);
    for (const group of groups) {
      expect(group.total).toBe(
        SNAPSHOT_COUNTS[group.sector as keyof typeof SNAPSHOT_COUNTS],
      );
    }
  });

  test("ranks inside a sector by return magnitude when anomaly scores tie", () => {
    const groups = selectRepresentatives(
      [
        point("A", "technology", 0.01),
        point("B", "technology", -0.08),
        point("C", "technology", 0.03),
      ],
      1,
      null,
    );
    expect(groups[0]?.points.map((item) => item.symbol)).toEqual([
      "US.B",
      "US.C",
      "US.A",
    ]);
  });

  test("shows the whole pool when it is smaller than 36", () => {
    const groups = selectRepresentatives(universe({ etf: 3, technology: 2 }), 5, null);
    expect(groups.reduce((sum, group) => sum + group.points.length, 0)).toBe(5);
  });

  test("packs blocks onto one column grid without overlap and fills every line", () => {
    const groups = selectRepresentatives(universe(SNAPSHOT_COUNTS), 1, null);
    const packed = packSectorBlocks(groups);
    const lines = new Map<number, number[]>();
    for (const block of packed.blocks) {
      expect(block.span).toBe(blockSpan(block.group));
      expect(block.column + block.span - 1).toBeLessThanOrEqual(packed.columns);
      const used = lines.get(block.line) ?? [];
      for (let column = block.column; column < block.column + block.span; column += 1) {
        expect(used).not.toContain(column);
        used.push(column);
      }
      lines.set(block.line, used);
    }
    const metaLine = lines.get(packed.meta.line) ?? [];
    for (let column = packed.meta.column; column <= packed.columns; column += 1) {
      expect(metaLine).not.toContain(column);
    }
    expect(packed.meta.column + packed.meta.span - 1).toBe(packed.columns);
    expect(packed.lines).toBe(3);
  });
});
