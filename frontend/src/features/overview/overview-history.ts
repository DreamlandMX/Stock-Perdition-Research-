import type { components } from "../../api/generated/schema";
import { groupBySector } from "./overview-map";
import type { MarketPoint } from "./overview-terminal";

export type DailyBar = components["schemas"]["HistoryBar"];
export type DailyHistories = Record<string, DailyBar[]>;
export const REGIME_ORDER = [
  "uptrend",
  "range",
  "transition",
  "downtrend",
  "invalid",
] as const;
export const REGIME_COLORS = {
  uptrend: "#3ecf8e",
  range: "#8b8a99",
  transition: "#b4a7f5",
  downtrend: "#f2647a",
  invalid: "#4a4955",
};
export const SECTOR_COLORS: Record<string, string> = {
  other: "#8b8a99",
  etf: "#b4a7f5",
  technology: "#6fb3ff",
  consumer: "#f0b860",
  healthcare: "#3ecf8e",
  financials: "#e58bd0",
  industrials: "#5fd4d0",
  energy_materials: "#f28b5a",
  communication: "#c9c26b",
};

export function normalizeHistoryBars(
  input: readonly DailyBar[],
  cutoff: string,
): DailyBar[] {
  const byDate = new Map<string, DailyBar>();
  for (const bar of input) {
    if (
      !Number.isFinite(Date.parse(bar.at)) ||
      Date.parse(bar.at) > Date.parse(cutoff) ||
      ![bar.open, bar.high, bar.low, bar.close].every(
        (v) => Number.isFinite(v) && v > 0,
      ) ||
      !Number.isFinite(bar.volume) ||
      bar.volume < 0 ||
      bar.high < Math.max(bar.open, bar.close, bar.low) ||
      bar.low > Math.min(bar.open, bar.close)
    )
      continue;
    byDate.set(bar.at.slice(0, 10), bar);
  }
  return [...byDate.values()].sort((a, b) => a.at.localeCompare(b.at)).slice(-130);
}

export function median(values: readonly (number | null | undefined)[]): number | null {
  const sorted = values
    .filter((v): v is number => v != null && Number.isFinite(v))
    .sort((a, b) => a - b);
  if (!sorted.length) return null;
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2;
}

export function historyDates(histories: DailyHistories) {
  return [
    ...new Set(
      Object.values(histories).flatMap((bars) =>
        bars.map((bar) => bar.at.slice(0, 10)),
      ),
    ),
  ]
    .sort()
    .slice(-130);
}

export function cumulativeMedianPath(
  symbols: readonly string[],
  histories: DailyHistories,
  dates: readonly string[],
) {
  const calendar = historyDates(histories);
  const start = dates[0],
    startIndex = calendar.indexOf(start ?? "");
  const indexes = Object.fromEntries(
    symbols.map((symbol) => [
      symbol,
      new Map((histories[symbol] ?? []).map((bar) => [bar.at.slice(0, 10), bar])),
    ]),
  );
  return dates.map((date) =>
    median(
      symbols.map((symbol) => {
        const index = indexes[symbol]!,
          first = index.get(start ?? ""),
          current = index.get(date),
          endIndex = calendar.indexOf(date);
        if (!first || !current || startIndex < 0 || endIndex < startIndex) return null;
        const valid = calendar.slice(startIndex, endIndex + 1).every((day) => {
          const bar = index.get(day);
          return bar && !bar.data_gap;
        });
        return valid ? current.close / first.close - 1 : null;
      }),
    ),
  );
}

export function sparklinePath(
  bars: readonly DailyBar[],
  width: number,
  height: number,
  calendar: readonly string[] = [],
) {
  const valid = bars.filter((bar) => !bar.data_gap);
  if (!valid.length) return "";
  const min = Math.min(...valid.map((bar) => bar.close)),
    span = Math.max(...valid.map((bar) => bar.close)) - min || 1;
  let drawing = false;
  return bars
    .map((bar, i) => {
      if (bar.data_gap) {
        drawing = false;
        return "";
      }
      if (
        i > 0 &&
        calendar.length &&
        calendar.indexOf(bar.at.slice(0, 10)) -
          calendar.indexOf(bars[i - 1]!.at.slice(0, 10)) >
          1
      )
        drawing = false;
      const command = drawing ? "L" : "M";
      drawing = true;
      return `${command}${((i / Math.max(1, bars.length - 1)) * width).toFixed(1)},${(height - 2 - ((bar.close - min) / span) * (height - 4)).toFixed(1)}`;
    })
    .join(" ");
}

export function buildOverviewHistory(
  points: readonly MarketPoint[],
  input: DailyHistories,
  cutoff: string,
) {
  const histories = Object.fromEntries(
    Object.entries(input).map(([symbol, bars]) => [
      symbol,
      normalizeHistoryBars(bars, cutoff),
    ]),
  );
  const fullDates = historyDates(histories);
  const dates = fullDates.slice(-60);
  const indexes = Object.fromEntries(
    Object.entries(histories).map(([symbol, bars]) => [
      symbol,
      new Map(bars.map((bar, i) => [bar.at.slice(0, 10), i])),
    ]),
  );
  const validWindow = (symbol: string, date: string, count: number) => {
    const bars = histories[symbol] ?? [],
      i = indexes[symbol]?.get(date),
      master = fullDates.indexOf(date);
    if (i == null || i < count - 1 || master < count - 1) return null;
    const window = bars.slice(i - count + 1, i + 1);
    return window.every(
      (bar, offset) =>
        !bar.data_gap && bar.at.slice(0, 10) === fullDates[master - count + 1 + offset],
    )
      ? window
      : null;
  };
  const returns = Object.fromEntries(
    Object.keys(histories).map((symbol) => [
      symbol,
      {
        r5: dates.map((date) => {
          const w = validWindow(symbol, date, 6);
          return w ? w.at(-1)!.close / w[0]!.close - 1 : null;
        }),
        r20: dates.map((date) => {
          const w = validWindow(symbol, date, 21);
          return w ? w.at(-1)!.close / w[0]!.close - 1 : null;
        }),
      },
    ]),
  );
  const measure = (members: readonly MarketPoint[], date: string) => {
    let n20 = 0,
      above20 = 0,
      n50 = 0,
      above50 = 0,
      n60 = 0,
      high = 0,
      low = 0;
    for (const p of members) {
      for (const days of [20, 50] as const) {
        const w = validWindow(p.symbol, date, days);
        if (!w) continue;
        const above =
          w.at(-1)!.close > w.reduce((sum, b) => sum + b.close, 0) / days ? 1 : 0;
        if (days === 20) {
          n20++;
          above20 += above;
        } else {
          n50++;
          above50 += above;
        }
      }
      const w = validWindow(p.symbol, date, 60);
      if (w) {
        n60++;
        const previous = w.slice(0, -1).map((b) => b.close),
          last = w.at(-1)!.close;
        if (last > Math.max(...previous)) high++;
        if (last < Math.min(...previous)) low++;
      }
    }
    return {
      n20,
      above20,
      n50,
      above50,
      n60,
      high: n60 ? high : null,
      low: n60 ? low : null,
      p20: n20 ? above20 / n20 : null,
      p50: n50 ? above50 / n50 : null,
    };
  };
  const breadth = dates.map((date) => measure(points, date));
  const regimeTotals = dates.map(
    (date) =>
      points.filter((p) => {
        const i = indexes[p.symbol]?.get(date);
        return i != null && !histories[p.symbol]![i]!.data_gap;
      }).length,
  );
  const regimes = Object.fromEntries(
    REGIME_ORDER.map((regime) => [
      regime,
      dates.map((date, day) => {
        const count = points.filter((p) => {
          const i = indexes[p.symbol]?.get(date);
          return (
            i != null &&
            !histories[p.symbol]![i]!.data_gap &&
            histories[p.symbol]![i]!.regime === regime
          );
        }).length;
        return regimeTotals[day] ? count / regimeTotals[day] : null;
      }),
    ]),
  ) as Record<(typeof REGIME_ORDER)[number], (number | null)[]>;
  const sectors = [...groupBySector(points)]
    .sort((a, b) => b[1].length - a[1].length)
    .map(([sector, members]) => {
      const path = cumulativeMedianPath(
        members.map((p) => p.symbol),
        histories,
        dates,
      );
      const rotation = dates.flatMap((_, i) => {
        if (i < dates.length - 10) return [];
        const x = median(members.map((p) => returns[p.symbol]?.r20[i])),
          y = median(members.map((p) => returns[p.symbol]?.r5[i]));
        const spy20 = returns["US.SPY"]?.r20[i],
          spy5 = returns["US.SPY"]?.r5[i];
        return x != null && y != null && spy20 != null && spy5 != null
          ? [[(x - spy20) * 100, (y - spy5) * 100]]
          : [];
      });
      return {
        sector,
        count: members.length,
        path,
        rotation,
        stats: dates.length ? measure(members, dates.at(-1)!) : null,
      };
    });
  const jumps = Object.entries(histories).flatMap(([symbol, bars]) =>
    bars.flatMap((bar, i) =>
      i > 0 && Math.abs(bar.close / bars[i - 1]!.close - 1) > 0.4
        ? [`${symbol} ${bar.at.slice(0, 10)}`]
        : [],
    ),
  );
  const sectorSymbols = Object.fromEntries(
    [...groupBySector(points)].map(([sector, members]) => [
      sector,
      members.map((p) => p.symbol),
    ]),
  );
  return {
    dates,
    histories,
    breadth,
    regimes,
    regimeTotals,
    sectors,
    sectorSymbols,
    returns,
    jumps,
    excluded: points.filter((p) => !histories[p.symbol]?.length).length,
  };
}
export type OverviewHistory = ReturnType<typeof buildOverviewHistory>;
