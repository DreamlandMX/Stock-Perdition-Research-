import { translate, type Locale } from "../../i18n/core";
import type { CustomSeriesOption, EChartsOption } from "echarts";
import { boardColors, money, number } from "../options/options-board-rows";
export interface PulseChartBar {
  at: string;
  open: number;
  close: number;
  low: number;
  high: number;
  data_gap?: boolean;
  forming?: boolean;
}
export interface PulseChartProfile {
  strike: number;
  call?: number | null;
  put?: number | null;
}
export interface PulseChartCone {
  at: string;
  horizon_days?: number | null;
  lower68: number;
  upper68: number;
  lower80: number;
  upper80: number;
}
export interface PulseChartProps {
  locale?: Locale;
  bars: readonly PulseChartBar[];
  profile: readonly PulseChartProfile[];
  cone: readonly PulseChartCone[];
  levels: {
    spot: number | null;
    call: number | null;
    put: number | null;
  };
  timeframe: "5m" | "1d";
  compact?: boolean;
  profileUnit?: "USD" | "contracts";
}
export function buildOptionPulseOption(
  {
    bars,
    profile,
    cone,
    levels,
    timeframe,
    compact = false,
    profileUnit = "USD",
    locale: inputLocale = "zh-CN",
  }: PulseChartProps,
  locale: Locale = inputLocale,
): EChartsOption {
  const colors = boardColors();
  const price = levels.spot;
  if (price == null) return {};
  const daily = timeframe === "1d";
  const timeLabel = (at: string) =>
    daily
      ? at.slice(5, 10)
      : new Intl.DateTimeFormat(locale, {
          timeZone: "America/New_York",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        }).format(new Date(at));
  const offset = daily && bars.length > 0 ? bars.length - 1 : bars.length;
  const dates = bars.map((bar) => timeLabel(bar.at));
  const timestamps = bars.map((bar) => Date.parse(bar.at));
  const pointsByIndex = new Map<number, PulseChartCone>();
  cone.forEach((point, i) => {
    const index = offset + (daily ? (point.horizon_days ?? i) : i);
    if (daily) {
      for (let j = dates.length; j <= index; j++) dates[j] = `T+${j - offset}`;
    }
    const label = daily
      ? i === 0
        ? timeLabel(bars.at(-1)?.at ?? point.at)
        : `T+${point.horizon_days ?? i}`
      : timeLabel(point.at);
    dates[index] = label;
    timestamps[index] = Date.parse(point.at);
    pointsByIndex.set(index, point);
  });
  const prices = [
    price,
    ...bars.flatMap((bar) => (bar.data_gap ? [] : [bar.low, bar.high])),
    ...cone.flatMap((point) => [point.lower80, point.upper80]),
    ...(compact ? profile.map((p) => p.strike) : []),
  ];
  const low = Math.min(...prices),
    high = Math.max(...prices);
  const pad = Math.max((high - low) * 0.06, price * (compact ? 0.01 : 0.025));
  const min = Math.max(0, low - pad),
    max = high + pad;
  const visible = profile.filter((p) => p.strike >= min && p.strike <= max);
  const maxAmount = Math.max(1, ...visible.flatMap((p) => [p.call ?? 0, p.put ?? 0]));
  const step =
    visible.length > 1
      ? Math.min(
          ...visible
            .slice(1)
            .map((p, i) => Math.abs(p.strike - visible[i]!.strike))
            .filter((v) => v > 0),
        )
      : price * 0.01;
  const range = (
    field: keyof Pick<PulseChartCone, "lower68" | "upper68" | "lower80" | "upper80">,
  ) => dates.map((_, i) => pointsByIndex.get(i)?.[field] ?? null);
  const lower = range("lower80"),
    upper = range("upper80"),
    lower68 = range("lower68"),
    upper68 = range("upper68");
  const width = (a: (number | null)[], b: (number | null)[]) =>
    a.map((v, i) => (v == null || b[i] == null ? null : b[i] - v));
  const lineData = (values: (number | null)[]) =>
    daily ? values : values.map((value, i) => [timestamps[i], value]);
  const formatAmount = (value: number) =>
    profileUnit === "USD"
      ? money(value, locale)
      : translate(locale, "research.optionPulseChart.contracts", {
          value: number(value, 0, locale),
        });
  const rect = (
    side: "call" | "put",
    sign: number,
    color: string,
  ): CustomSeriesOption => ({
    name: translate(
      locale,
      profileUnit === "USD"
        ? "research.optionPulseChart.profileTurnover"
        : "research.optionPulseChart.profileVolume",
      { side: side === "call" ? "Call" : "Put" },
    ),
    type: "custom",
    xAxisIndex: 1,
    yAxisIndex: 1,
    silent: compact,
    encode: { x: 1, y: 0 },
    data: visible.flatMap((p) => (p[side] == null ? [] : [[p.strike, p[side]]])),
    tooltip: {
      trigger: "item",
      formatter: (raw) => {
        const item = raw as {
          value?: number[];
        };
        return translate(locale, "research.optionPulseChart.strikeBr", {
          value: side === "call" ? "Call" : "Put",
          value2: number(item.value?.[0], undefined, locale),
          value3: formatAmount(item.value?.[1] ?? 0),
        });
      },
    },
    renderItem: (_params, api) => {
      const strike = Number(api.value(0)),
        amount = Number(api.value(1));
      const start = api.coord([0, strike]),
        end = api.coord([sign * amount, strike]);
      const size = api.size?.([0, step]);
      return {
        type: "rect",
        shape: {
          x: Math.min(start[0]!, end[0]!),
          y:
            start[1]! -
            Math.max(2, Math.abs(Array.isArray(size) ? Number(size[1]) : 3) * 0.65) / 2,
          width: Math.abs(end[0]! - start[0]!),
          height: Math.max(
            2,
            Math.abs(Array.isArray(size) ? Number(size[1]) : 3) * 0.65,
          ),
          r: 1,
        },
        style: { fill: color, opacity: strike === levels[side] ? 1 : 0.65 },
      };
    },
  });
  const marks = [
    {
      yAxis: price,
      name: translate(locale, "research.optionPulseChart.referencePrice"),
      lineStyle: { color: colors.secondary },
      label: { show: false },
    },
    ...(["call", "put"] as const).flatMap((side) =>
      levels[side] == null
        ? []
        : [
            {
              yAxis: levels[side],
              name: translate(locale, "research.optionPulseChart.peak", {
                value: side === "call" ? "Call" : "Put",
              }),
              lineStyle: {
                color: side === "call" ? colors.up : colors.down,
                type: "dashed" as const,
              },
              label: { show: false },
            },
          ],
    ),
  ];
  return {
    animation: false,
    grid: [
      {
        left: compact ? 6 : 60,
        right: compact ? 120 : 265,
        top: 16,
        bottom: compact ? 20 : 42,
      },
      {
        right: compact ? 38 : 65,
        width: compact ? 76 : 180,
        top: 16,
        bottom: compact ? 20 : 42,
      },
    ],
    tooltip: compact
      ? { show: false }
      : {
          trigger: "axis",
          backgroundColor: "#191a20",
          borderColor: "#45434f",
          textStyle: { color: "#ecebf2" },
          formatter: (raw) => {
            const items = (Array.isArray(raw) ? raw : [raw]) as {
              dataIndex?: number;
            }[];
            const i = items[0]?.dataIndex ?? 0;
            const bar = bars[i];
            const point = pointsByIndex.get(i);
            if (point && (!bar || i === offset))
              return translate(
                locale,
                "research.optionPulseChart.impliedScenarioRangeBr68Br80",
                {
                  at: dates[i] ?? "",
                  lower68: number(point.lower68, undefined, locale),
                  upper68: number(point.upper68, undefined, locale),
                  lower80: number(point.lower80, undefined, locale),
                  upper80: number(point.upper80, undefined, locale),
                },
              );
            return bar
              ? translate(locale, "research.optionPulseChart.brOpenCloseBrLowHigh", {
                  at: dates[i] ?? "",
                  translate: bar.forming
                    ? translate(locale, "research.optionPulseChart.forming")
                    : "",
                  open: number(bar.open, undefined, locale),
                  close: number(bar.close, undefined, locale),
                  low: number(bar.low, undefined, locale),
                  high: number(bar.high, undefined, locale),
                  translate2: bar.data_gap
                    ? translate(locale, "research.optionPulseChart.brDataGap")
                    : "",
                })
              : "";
          },
        },
    xAxis: [
      {
        ...(daily
          ? { type: "category" as const, data: dates, boundaryGap: true }
          : { type: "time" as const }),
        show: !compact,
        axisLine: { lineStyle: { color: colors.divider } },
        axisTick: { show: false },
        axisLabel: {
          color: colors.secondary,
          fontSize: 11,
          hideOverlap: true,
          ...(daily
            ? {}
            : {
                formatter: (value: string | number) =>
                  timeLabel(new Date(value).toISOString()),
              }),
        },
      },
      { type: "value", gridIndex: 1, min: -maxAmount, max: maxAmount, show: false },
    ],
    yAxis: [
      { type: "value", min, max, show: false },
      {
        type: "value",
        gridIndex: 1,
        min,
        max,
        position: "right",
        axisLine: { show: false },
        splitLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          fontSize: compact ? 12 : 13,
          showMinLabel: false,
          showMaxLabel: false,
          formatter: (v: number) => number(v, v > 100 ? 0 : 1, locale),
        },
        splitNumber: compact ? 4 : 6,
      },
    ],
    series: [
      {
        type: "line",
        name: translate(locale, "research.optionPulseChart.80LowerBound"),
        data: lineData(lower),
        stack: "80",
        symbol: "none",
        silent: true,
        lineStyle: { color: colors.cone, type: "dashed", width: 1 },
      },
      {
        type: "line",
        data: lineData(width(lower, upper)),
        stack: "80",
        symbol: "none",
        silent: true,
        lineStyle: { opacity: 0 },
        areaStyle: { color: colors.cone, opacity: 0.16 },
      },
      {
        type: "line",
        data: lineData(lower68),
        stack: "68",
        symbol: "none",
        silent: true,
        lineStyle: { opacity: 0 },
      },
      {
        type: "line",
        data: lineData(width(lower68, upper68)),
        stack: "68",
        symbol: "none",
        silent: true,
        lineStyle: { opacity: 0 },
        areaStyle: { color: colors.cone, opacity: 0.3 },
      },
      {
        type: "line",
        name: translate(locale, "research.optionPulseChart.80UpperBound"),
        data: lineData(upper),
        symbol: "none",
        silent: true,
        lineStyle: { color: colors.cone, type: "dashed", width: 1 },
      },
      {
        type: "candlestick",
        name: translate(locale, "research.optionPulseChart.stockCandlesticks"),
        silent: compact,
        ...(daily ? {} : { encode: { x: 0, y: [1, 2, 3, 4] } }),
        data: dates.map((_, i) => {
          const bar = bars[i];
          return !bar || bar.data_gap
            ? [...(daily ? [] : [timestamps[i]]), "-", "-", "-", "-"]
            : {
                value: [
                  ...(daily ? [] : [timestamps[i]]),
                  bar.open,
                  bar.close,
                  bar.low,
                  bar.high,
                ],
                itemStyle: { opacity: bar.forming ? 0.5 : 1 },
              };
        }),
        barMaxWidth: compact ? 6 : 11,
        itemStyle: {
          color: colors.up,
          color0: colors.down,
          borderColor: colors.up,
          borderColor0: colors.down,
        },
        markLine: {
          symbol: "none",
          silent: true,
          data: marks.map(
            (m) =>
              [
                {
                  coord: [daily ? 0 : (timestamps[0] ?? Date.now()), m.yAxis],
                  name: m.name,
                  lineStyle: m.lineStyle,
                  label: m.label,
                },
                {
                  coord: [
                    daily ? dates.length - 1 : (timestamps.at(-1) ?? Date.now()),
                    m.yAxis,
                  ],
                },
              ] satisfies [object, object],
          ),
        },
      },
      rect("call", 1, colors.up),
      rect("put", -1, colors.down),
      {
        type: "line",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: [],
        markLine: {
          symbol: "none",
          silent: true,
          data: [
            ...marks.map(
              (m) =>
                [
                  {
                    coord: [-maxAmount, m.yAxis],
                    name: m.name,
                    lineStyle: m.lineStyle,
                    label: m.label,
                  },
                  { coord: [maxAmount, m.yAxis] },
                ] satisfies [object, object],
            ),
            { xAxis: 0, lineStyle: { color: colors.divider }, label: { show: false } },
          ],
        },
      },
    ],
  };
}
