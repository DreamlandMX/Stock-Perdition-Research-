import { ResearchErrorDetail } from "./ResearchErrorDetail";
import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useOptionsOverview } from "../options/useOptionsOverview";
import { ChartExplainer } from "../options/ChartExplainer";
import { OPTION_COLOR, MODEL_COLOR, impliedCaveat } from "../options/options-copy";
import { optionPercent } from "../options/option-labels";
import { Expand, Minimize2 } from "lucide-react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  type ISeriesApi,
  type Logical,
  type IPrimitivePaneRenderer,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { components } from "../../api/generated/schema";
import { formatUsMarketTime } from "../../app/presentation";
type HistoryBar = components["schemas"]["HistoryBar"];
type HistoryGap = components["schemas"]["HistoryGap"];
type RegimeLabel = components["schemas"]["RegimeLabel"];
type MarkerCategory = "breakout" | "gaps" | "state";
const CHART_HEIGHT = 360;
const FALLBACK_COLORS = {
  background: "#111820",
  divider: "#24303a",
  down: "#f26767",
  neutral: "#94a3b8",
  up: "#35c995",
  warning: "#e0ad58",
};
interface IndicatorPoint {
  atr: number;
  bufferLower: number;
  bufferUpper: number;
  channelLower: number;
  channelUpper: number;
  time: UTCTimestamp;
}
type ConePoint = {
  horizon: number;
  lower: number;
  upper: number;
  lowerPct: number;
  upperPct: number;
};
type ConeBand = {
  color: string;
  points: (ConePoint | null)[];
};
interface ResearchChartSeries {
  atrLower: ISeriesApi<"Line">;
  atrUpper: ISeriesApi<"Line">;
  candles: ISeriesApi<"Candlestick">;
  channelLower: ISeriesApi<"Line">;
  channelUpper: ISeriesApi<"Line">;
  markers: ISeriesMarkersPluginApi<Time>;
  volume: ISeriesApi<"Histogram">;
}
interface ChartMarkerCandidate {
  category: MarkerCategory;
  marker: SeriesMarker<UTCTimestamp>;
}
type MarkerFilters = Record<MarkerCategory, boolean>;
const DEFAULT_MARKER_FILTERS: MarkerFilters = {
  breakout: true,
  gaps: true,
  state: true,
};
const MARKER_CATEGORY_ORDER: Record<MarkerCategory, number> = {
  state: 0,
  breakout: 1,
  gaps: 3,
};
const MARKER_FILTER_LABELS = (
  locale: Locale = "zh-CN",
): ReadonlyArray<{
  category: MarkerCategory;
  label: string;
}> => [
  { category: "state", label: translate(locale, "research.optionpulsechart.status") },
  {
    category: "breakout",
    label: translate(locale, "research.pricehistorychart.breakout"),
  },
  { category: "gaps", label: translate(locale, "research.optionpulsechart.gap") },
];
function cssColor(variable: string, fallback: string) {
  return (
    getComputedStyle(document.documentElement).getPropertyValue(variable).trim() ||
    fallback
  );
}
function toTimestamp(value: string) {
  return Math.floor(Date.parse(value) / 1000) as UTCTimestamp;
}
function measuredWidth(value: number) {
  return Number.isFinite(value) && value > 0 ? Math.max(1, Math.round(value)) : 1;
}
function chartHeight(landscape: boolean) {
  return landscape
    ? Math.max(CHART_HEIGHT, Math.round(window.innerHeight - 210))
    : CHART_HEIGHT;
}
function calculateIndicators(bars: readonly HistoryBar[]): IndicatorPoint[] {
  const trueRanges = bars.map((bar, index) => {
    const previousClose = bars[index - 1]?.close ?? bar.close;
    return Math.max(
      bar.high - bar.low,
      Math.abs(bar.high - previousClose),
      Math.abs(bar.low - previousClose),
    );
  });
  return bars.map((bar, index) => {
    const channelWindow = bars.slice(Math.max(0, index - 19), index + 1);
    const atrWindow = trueRanges.slice(Math.max(0, index - 13), index + 1);
    const channelUpper = Math.max(...channelWindow.map((item) => item.high));
    const channelLower = Math.min(...channelWindow.map((item) => item.low));
    const atr = atrWindow.reduce((sum, value) => sum + value, 0) / atrWindow.length;
    return {
      atr,
      bufferLower: channelLower - atr,
      bufferUpper: channelUpper + atr,
      channelLower,
      channelUpper,
      time: toTimestamp(bar.at),
    };
  });
}
function nearestBarTime(bars: readonly HistoryBar[], at: string) {
  const target = Date.parse(at);
  const nearest = bars.reduce<HistoryBar | undefined>((best, bar) => {
    if (best === undefined) {
      return bar;
    }
    return Math.abs(Date.parse(bar.at) - target) <
      Math.abs(Date.parse(best.at) - target)
      ? bar
      : best;
  }, undefined);
  return nearest === undefined ? null : toTimestamp(nearest.at);
}
function chartMarkers(
  bars: readonly HistoryBar[],
  gaps: readonly HistoryGap[],
  locale: Locale = "zh-CN",
) {
  const markers: ChartMarkerCandidate[] = [];
  let activeBreakout:
    | {
        direction: "up" | "down";
        lower: number;
        upper: number;
      }
    | undefined;
  bars.forEach((bar, index) => {
    const previous = bars[index - 1];
    if (previous !== undefined && previous.regime !== bar.regime) {
      markers.push({
        category: "state",
        marker: {
          color: FALLBACK_COLORS.neutral,
          position: "aboveBar",
          shape: "circle",
          text: translate(locale, "research.pricehistorychart.regime", {
            value: REGIME_LABELS(locale)[bar.regime],
          }),
          time: toTimestamp(bar.at),
        },
      });
    }
    if (
      activeBreakout !== undefined &&
      bar.close >= activeBreakout.lower &&
      bar.close <= activeBreakout.upper
    ) {
      markers.push({
        category: "breakout",
        marker: {
          color: FALLBACK_COLORS.warning,
          position: activeBreakout.direction === "up" ? "aboveBar" : "belowBar",
          shape: "square",
          text: translate(locale, "research.pricehistorychart.falseBreakout"),
          time: toTimestamp(bar.at),
        },
      });
      activeBreakout = undefined;
    }
    if (index < 20) {
      return;
    }
    const comparisonWindow = bars.slice(index - 20, index);
    const upper = Math.max(...comparisonWindow.map((item) => item.high));
    const lower = Math.min(...comparisonWindow.map((item) => item.low));
    if (bar.close > upper) {
      markers.push({
        category: "breakout",
        marker: {
          color: FALLBACK_COLORS.up,
          position: "belowBar",
          shape: "arrowUp",
          text: translate(locale, "research.pricehistorychart.upwardBreakout"),
          time: toTimestamp(bar.at),
        },
      });
      activeBreakout = { direction: "up", lower, upper };
    } else if (bar.close < lower) {
      markers.push({
        category: "breakout",
        marker: {
          color: FALLBACK_COLORS.down,
          position: "aboveBar",
          shape: "arrowDown",
          text: translate(locale, "research.pricehistorychart.downwardBreakout"),
          time: toTimestamp(bar.at),
        },
      });
      activeBreakout = { direction: "down", lower, upper };
    }
  });
  gaps.forEach((gap) => {
    const time = nearestBarTime(bars, gap.start_at);
    if (time !== null) {
      markers.push({
        category: "gaps",
        marker: {
          color: FALLBACK_COLORS.down,
          position: "belowBar",
          shape: "square",
          text: translate(locale, "research.pricehistorychart.dataGap"),
          time,
        },
      });
    }
  });
  return markers.sort((left, right) => {
    const byTime = Number(left.marker.time) - Number(right.marker.time);
    if (byTime !== 0) return byTime;
    const byCategory =
      MARKER_CATEGORY_ORDER[left.category] - MARKER_CATEGORY_ORDER[right.category];
    if (byCategory !== 0) return byCategory;
    return (left.marker.text ?? "").localeCompare(right.marker.text ?? "", "zh-CN");
  });
}
function visibleChartMarkers(
  bars: readonly HistoryBar[],
  candidates: readonly ChartMarkerCandidate[],
  filters: MarkerFilters,
) {
  const latestWindow = bars.slice(-60);
  const earliest = latestWindow[0];
  if (earliest === undefined) return [];
  const earliestTime = toTimestamp(earliest.at);
  const grouped = new Map<number, ChartMarkerCandidate[]>();
  for (const candidate of candidates) {
    const time = Number(candidate.marker.time);
    if (!filters[candidate.category] || time < Number(earliestTime)) continue;
    const current = grouped.get(time) ?? [];
    current.push(candidate);
    grouped.set(time, current);
  }
  return [...grouped.entries()]
    .sort(([left], [right]) => left - right)
    .map(([, group]) => {
      const first = group[0]!;
      const labels = [
        ...new Set(group.map(({ marker }) => marker.text).filter(Boolean)),
      ];
      return { ...first.marker, text: labels.join("; ") };
    })
    .slice(-12);
}
const REGIME_LABELS = (locale: Locale = "zh-CN") =>
  ({
    downtrend: translate(locale, "research.pricehistorychart.downtrend"),
    invalid: translate(locale, "research.pricehistorychart.invalidRegime"),
    range: translate(locale, "research.pricehistorychart.rangeBound"),
    transition: translate(locale, "research.pricehistorychart.regimeTransition"),
    uptrend: translate(locale, "research.pricehistorychart.uptrend"),
  }) satisfies Record<RegimeLabel, string>;
function describeCone(horizon: number, bands: readonly ConeBand[], locale: Locale) {
  const descriptions = bands.map((band) => {
    const actual = band.points.find((point) => point?.horizon === horizon);
    let point = actual;
    if (!point && band.color === MODEL_COLOR) {
      const anchors = band.points.filter((p): p is ConePoint => p !== null);
      const before = [...anchors].reverse().find((p) => p.horizon < horizon);
      const after = anchors.find((p) => p.horizon > horizon);
      if (
        before &&
        after &&
        !band.points
          .slice(band.points.indexOf(before), band.points.indexOf(after))
          .includes(null)
      ) {
        const weight = (horizon - before.horizon) / (after.horizon - before.horizon);
        point = {
          horizon,
          lower: before.lower + weight * (after.lower - before.lower),
          upper: before.upper + weight * (after.upper - before.upper),
          lowerPct: before.lowerPct + weight * (after.lowerPct - before.lowerPct),
          upperPct: before.upperPct + weight * (after.upperPct - before.upperPct),
        };
      }
    }
    return `${band.color === OPTION_COLOR ? translate(locale, "research.pricehistorychart.options80") : translate(locale, "research.pricehistorychart.modelQ10Q90LineThrough3Anchors")}: ${point ? `$${point.lower.toFixed(2)}–$${point.upper.toFixed(2)} / ${optionPercent(point.lowerPct, undefined, locale)}–${optionPercent(point.upperPct, undefined, locale)}` : translate(locale, "research.pricehistorychart.missingAtThisHorizonNoExtrapolation")}`;
  });
  return translate(locale, "research.pricehistorychart.nextTradingDays", {
    horizon: horizon,
    descriptions: descriptions.join("; "),
  });
}
export interface PriceHistoryChartProps {
  history: components["schemas"]["InstrumentHistory"];
  symbol: string;
}
export function PriceHistoryChart({ history, symbol }: PriceHistoryChartProps) {
  const { locale } = useI18n();
  const rootRef = useRef<HTMLElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof createChart> | null>(null);
  const seriesRef = useRef<ResearchChartSeries | null>(null);
  const landscapeRef = useRef(false);
  const [params, setParams] = useSearchParams();
  const optionCone = params.get("option_cone") === "1";
  const modelCone = params.get("model_cone") === "1";
  const coneEnabled = optionCone || modelCone;
  const coneQuery = useOptionsOverview(
    {
      symbol,
      snapshot_id: params.get("snapshot_id") ?? undefined,
      as_of: params.get("as_of") ?? undefined,
      option_snapshot_id: params.get("option_snapshot") ?? undefined,
    },
    coneEnabled,
  );
  const coneItem = coneQuery.data?.items.find((row) => row.symbol === symbol);
  const last = history.bars.at(-1);
  const marketDay = (value: string) =>
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/New_York",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date(value));
  const optionAnchorAligned =
    !!last &&
    !!coneItem?.market_as_of &&
    marketDay(last.at) === marketDay(coneItem.market_as_of) &&
    !coneItem.model_horizons.some((m) =>
      m.reasons.includes("model_option_cutoff_mismatch"),
    );
  const coneBands = useMemo<ConeBand[]>(() => {
    const bands: ConeBand[] = [];
    const moves = coneItem?.implied_moves ?? [];
    const anchor = moves.find((m) => m.upper_price != null && m.upper_pct != null);
    const optionSpot =
      anchor?.upper_price != null && anchor.upper_pct != null
        ? anchor.upper_price / (1 + anchor.upper_pct)
        : null;
    if (optionCone && optionSpot != null && optionAnchorAligned)
      bands.push({
        color: OPTION_COLOR,
        points: [
          {
            horizon: 0,
            lower: optionSpot,
            upper: optionSpot,
            lowerPct: 0,
            upperPct: 0,
          },
          ...Array.from({ length: 20 }, (_, index) => {
            const m = moves.find((row) => row.horizon_days === index + 1);
            return m?.lower_price == null ||
              m.upper_price == null ||
              m.lower_pct == null ||
              m.upper_pct == null
              ? null
              : {
                  horizon: index + 1,
                  lower: m.lower_price,
                  upper: m.upper_price,
                  lowerPct: m.lower_pct,
                  upperPct: m.upper_pct,
                };
          }),
        ],
      });
    if (modelCone && last)
      bands.push({
        color: MODEL_COLOR,
        points: [
          {
            horizon: 0,
            lower: last.close,
            upper: last.close,
            lowerPct: 0,
            upperPct: 0,
          },
          ...[1, 5, 20].map((horizon) => {
            const m = coneItem?.model_horizons.find(
              (row) => row.horizon_days === horizon,
            );
            return m?.q10 == null ||
              m.q90 == null ||
              m.status === "refused" ||
              m.status === "research_only"
              ? null
              : {
                  horizon,
                  lower: last.close * (1 + m.q10),
                  upper: last.close * (1 + m.q90),
                  lowerPct: m.q10,
                  upperPct: m.q90,
                };
          }),
        ],
      });
    return bands;
  }, [coneItem, optionCone, modelCone, last, optionAnchorAligned]);
  const coneBandsRef = useRef<ConeBand[]>([]);
  const lastLogicalRef = useRef(0);
  const redrawConesRef = useRef<(() => void) | undefined>(undefined);
  const [hoveredConeHorizon, setHoveredConeHorizon] = useState<number | null>(null);
  const hoveredCone =
    hoveredConeHorizon === null
      ? ""
      : describeCone(hoveredConeHorizon, coneBands, locale);
  const toggleCone = (name: string, checked: boolean) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (checked) next.set(name, "1");
        else next.delete(name);
        return next;
      },
      { preventScrollReset: true },
    );
  const [landscape, setLandscape] = useState(false);
  const [visibleBars, setVisibleBars] = useState(60);
  const [hoveredMarkerTime, setHoveredMarkerTime] = useState<Time | undefined>(
    undefined,
  );
  const [markerFilters, setMarkerFilters] =
    useState<MarkerFilters>(DEFAULT_MARKER_FILTERS);
  const indicators = useMemo(() => calculateIndicators(history.bars), [history.bars]);
  const markerCandidates = useMemo(
    () => chartMarkers(history.bars, history.gaps, locale),
    [history.bars, history.gaps, locale],
  );
  const markers = useMemo(
    () => visibleChartMarkers(history.bars, markerCandidates, markerFilters),
    [history.bars, markerCandidates, markerFilters],
  );
  const hoveredMarker =
    markers.find((marker) => marker.time === hoveredMarkerTime)?.text ?? "";
  const structureMarkers = useMemo(
    () =>
      markerCandidates
        .filter(({ category }) => category === "state" || category === "breakout")
        .map(({ marker }) => marker)
        .slice(-3),
    [markerCandidates],
  );
  useEffect(() => {
    const container = containerRef.current;
    if (container === null) {
      return undefined;
    }
    const up = cssColor("--color-up", FALLBACK_COLORS.up);
    const down = cssColor("--color-down", FALLBACK_COLORS.down);
    const neutral = cssColor("--color-text-secondary", FALLBACK_COLORS.neutral);
    const warning = cssColor("--color-warning", FALLBACK_COLORS.warning);
    const background = cssColor("--color-surface", FALLBACK_COLORS.background);
    const divider = cssColor("--color-divider", FALLBACK_COLORS.divider);
    const chart = createChart(container, {
      grid: {
        horzLines: { color: divider },
        vertLines: { color: divider },
      },
      height: CHART_HEIGHT,
      layout: {
        attributionLogo: false,
        background: { color: background, type: ColorType.Solid },
        textColor: neutral,
      },
      rightPriceScale: { borderColor: divider },
      timeScale: {
        borderColor: divider,
        timeVisible: history.timeframe !== "1d",
      },
      width: measuredWidth(container.clientWidth),
    });
    const candles = chart.addSeries(CandlestickSeries, {
      downColor: down,
      borderDownColor: down,
      borderUpColor: up,
      upColor: up,
      wickDownColor: down,
      wickUpColor: up,
    });
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    volume.priceScale().applyOptions({ scaleMargins: { bottom: 0, top: 0.8 } });
    const channelUpper = chart.addSeries(LineSeries, {
      color: neutral,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const channelLower = chart.addSeries(LineSeries, {
      color: neutral,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const atrUpper = chart.addSeries(LineSeries, {
      color: warning,
      lineStyle: 2,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const atrLower = chart.addSeries(LineSeries, {
      color: warning,
      lineStyle: 2,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const coneRenderer: IPrimitivePaneRenderer = {
      draw(target) {
        target.useMediaCoordinateSpace(({ context }) => {
          for (const band of coneBandsRef.current) {
            let segment: ConePoint[] = [];
            const paint = () => {
              if (segment.length < 2) return;
              const points = segment.map((point) => ({
                x: chart
                  .timeScale()
                  .logicalToCoordinate(
                    (lastLogicalRef.current + point.horizon) as Logical,
                  ),
                low: candles.priceToCoordinate(point.lower),
                high: candles.priceToCoordinate(point.upper),
              }));
              if (points.some((p) => p.x == null || p.low == null || p.high == null))
                return;
              context.save();
              context.beginPath();
              points.forEach((p, index) => {
                if (index === 0) context.moveTo(p.x!, p.high!);
                else context.lineTo(p.x!, p.high!);
              });
              [...points].reverse().forEach((p) => context.lineTo(p.x!, p.low!));
              context.closePath();
              context.fillStyle = `${band.color}22`;
              context.fill();
              context.strokeStyle = band.color;
              context.lineWidth = 1.5;
              for (const edge of ["low", "high"] as const) {
                context.beginPath();
                points.forEach((p, index) => {
                  if (index === 0) context.moveTo(p.x!, p[edge]!);
                  else context.lineTo(p.x!, p[edge]!);
                });
                context.stroke();
              }
              context.restore();
            };
            for (const point of band.points) {
              if (point) segment.push(point);
              else {
                paint();
                segment = [];
              }
            }
            paint();
          }
        });
      },
    };
    candles.attachPrimitive({
      attached({ requestUpdate }) {
        redrawConesRef.current = requestUpdate;
      },
      detached() {
        redrawConesRef.current = undefined;
      },
      paneViews: () => [
        { zOrder: () => "normal" as const, renderer: () => coneRenderer },
      ],
      autoscaleInfo(start, end) {
        if (end < lastLogicalRef.current || start > lastLogicalRef.current + 20)
          return null;
        const values = coneBandsRef.current.flatMap((band) =>
          band.points.flatMap((point) => (point ? [point.lower, point.upper] : [])),
        );
        return values.length
          ? {
              priceRange: {
                minValue: Math.min(...values),
                maxValue: Math.max(...values),
              },
            }
          : null;
      },
    });
    const markerApi = createSeriesMarkers(candles, []);
    chartRef.current = chart;
    seriesRef.current = {
      atrLower,
      atrUpper,
      candles,
      channelLower,
      channelUpper,
      markers: markerApi,
      volume,
    };
    let disposed = false;
    let renderedWidth = measuredWidth(container.clientWidth);
    const observer = new ResizeObserver((entries) => {
      const entry = entries.find((candidate) => candidate.target === container);
      const width = measuredWidth(entry?.contentRect.width ?? container.clientWidth);
      if (disposed || width === renderedWidth) {
        return;
      }
      renderedWidth = width;
      chart.resize(width, chartHeight(landscapeRef.current));
    });
    observer.observe(container);
    const handleFullscreenChange = () => {
      const active = document.fullscreenElement === rootRef.current;
      if (active !== landscapeRef.current) {
        landscapeRef.current = active;
        setLandscape(active);
      }
    };
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    chart.subscribeCrosshairMove((event) => {
      setHoveredMarkerTime(event.time);
      const logical = event.point
        ? chart.timeScale().coordinateToLogical(event.point.x)
        : null;
      const horizon =
        logical == null ? 0 : Math.round(Number(logical) - lastLogicalRef.current);
      if (horizon < 1 || horizon > 20) {
        setHoveredConeHorizon(null);
        return;
      }
      setHoveredConeHorizon(horizon);
    });
    return () => {
      disposed = true;
      observer.disconnect();
      document.removeEventListener("fullscreenchange", handleFullscreenChange);
      seriesRef.current = null;
      chartRef.current = null;
      chart.remove();
    };
  }, []);
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (series === null || chart === null) {
      return;
    }
    chart.applyOptions({ timeScale: { timeVisible: history.timeframe !== "1d" } });
    const up = cssColor("--color-up", FALLBACK_COLORS.up);
    const down = cssColor("--color-down", FALLBACK_COLORS.down);
    const warning = cssColor("--color-warning", FALLBACK_COLORS.warning);
    series.candles.setData(
      history.bars.map((bar) => ({
        close: bar.close,
        ...(bar.data_gap
          ? { borderColor: warning, color: warning, wickColor: warning }
          : {}),
        high: bar.high,
        low: bar.low,
        open: bar.open,
        time: toTimestamp(bar.at),
      })),
    );
    series.volume.setData(
      history.bars.map((bar) => ({
        color: bar.close >= bar.open ? `${up}88` : `${down}88`,
        time: toTimestamp(bar.at),
        value: bar.volume,
      })),
    );
    series.channelUpper.setData(
      indicators.map((point) => ({ time: point.time, value: point.channelUpper })),
    );
    series.channelLower.setData(
      indicators.map((point) => ({ time: point.time, value: point.channelLower })),
    );
    series.atrUpper.setData(
      indicators.map((point) => ({ time: point.time, value: point.bufferUpper })),
    );
    series.atrLower.setData(
      indicators.map((point) => ({ time: point.time, value: point.bufferLower })),
    );
  }, [history.bars, history.timeframe, indicators]);
  useEffect(() => {
    coneBandsRef.current = coneBands;
    lastLogicalRef.current = history.bars.length - 1;
    redrawConesRef.current?.();
    chartRef.current?.applyOptions({ rightPriceScale: { autoScale: true } });
  }, [coneBands, history.bars.length]);
  useEffect(() => {
    seriesRef.current?.markers.setMarkers(
      markers.map((marker) => ({ ...marker, text: "" })),
    );
  }, [markers]);
  useEffect(() => {
    const chart = chartRef.current;
    if (chart === null) return;
    if (visibleBars === 0 || history.bars.length <= visibleBars) {
      if (coneEnabled)
        chart
          .timeScale()
          .setVisibleLogicalRange({ from: 0, to: history.bars.length - 1 + 22 });
      else chart.timeScale().fitContent();
    } else {
      chart.timeScale().setVisibleLogicalRange({
        from: history.bars.length - visibleBars,
        to: history.bars.length - 1 + (coneEnabled ? 22 : 0),
      });
    }
  }, [history.bars, visibleBars, coneEnabled]);
  useEffect(() => {
    const chart = chartRef.current;
    if (chart === null) {
      return;
    }
    chart.applyOptions({ height: chartHeight(landscape) });
  }, [landscape]);
  useEffect(() => {
    chartRef.current?.applyOptions({ localization: { locale } });
  }, [locale]);
  async function toggleLandscape() {
    const next = !landscape;
    landscapeRef.current = next;
    setLandscape(next);
    if (next) {
      try {
        await rootRef.current?.requestFullscreen?.();
      } catch {
        // The CSS full-screen presentation remains available when the browser denies it.
      }
      const orientation = globalThis.screen?.orientation as
        | (ScreenOrientation & {
            lock?: (value: "landscape") => Promise<void>;
          })
        | undefined;
      try {
        await orientation?.lock?.("landscape");
      } catch {
        // Orientation locking is optional and commonly unavailable on desktop.
      }
    } else if (document.fullscreenElement !== null) {
      try {
        await document.exitFullscreen();
      } catch {
        // CSS state has already exited even if the browser API rejects.
      }
    }
  }
  const lastBar = history.bars.at(-1);
  const lastIndicator = indicators.at(-1);
  return (
    <section
      aria-label={translate(locale, "research.pricehistorychart.priceHistory", {
        symbol: symbol,
      })}
      className={`price-history ${landscape ? "price-history--landscape" : ""}`}
      ref={rootRef}
    >
      <header className="price-history__header">
        <div>
          <span>
            {translate(
              locale,
              "research.pricehistorychart.candlestickResearchWorkspace.withValues",
              { timeframe: history.timeframe },
            )}
          </span>
          <h2>
            {translate(locale, "research.pricehistorychart.priceChannelsAndStructure")}
          </h2>
          <span>
            {history.adjustment === "raw"
              ? translate(locale, "research.pricehistorychart.unadjustedRaw")
              : history.adjustment}
          </span>
        </div>
        <button
          aria-label={
            landscape
              ? translate(
                  locale,
                  "research.pricehistorychart.exitFullScreenLandscapeChart",
                )
              : translate(
                  locale,
                  "research.pricehistorychart.enterFullScreenLandscapeChart",
                )
          }
          aria-pressed={landscape}
          onClick={() => void toggleLandscape()}
          type="button"
        >
          {landscape ? (
            <Minimize2 aria-hidden="true" size={17} strokeWidth={1.5} />
          ) : (
            <Expand aria-hidden="true" size={17} strokeWidth={1.5} />
          )}
          {landscape
            ? translate(locale, "research.pricehistorychart.exitLandscape")
            : translate(locale, "research.pricehistorychart.fullScreenLandscape")}
        </button>
      </header>
      <fieldset className="price-history__filters">
        <label>
          {translate(locale, "research.pricehistorychart.displayRange")}
          <select
            data-price-history-range
            aria-label={translate(
              locale,
              "research.pricehistorychart.priceChartDisplayRange",
            )}
            value={visibleBars}
            onChange={(event) => setVisibleBars(Number(event.target.value))}
          >
            <option value={20}>
              {translate(locale, "research.pricehistorychart.last20Bars")}
            </option>
            <option value={60}>
              {translate(locale, "research.pricehistorychart.last60Bars")}
            </option>
            <option value={120}>
              {translate(locale, "research.pricehistorychart.last120Bars")}
            </option>
            <option value={0}>
              {translate(locale, "research.pricehistorychart.allHistory")}
            </option>
          </select>
        </label>
        <legend>
          {translate(locale, "research.pricehistorychart.chartMarkerFilters")}
        </legend>
        {MARKER_FILTER_LABELS(locale).map(({ category, label }) => (
          <label key={category}>
            <input
              checked={markerFilters[category]}
              onChange={(event) =>
                setMarkerFilters((current) => ({
                  ...current,
                  [category]: event.target.checked,
                }))
              }
              type="checkbox"
            />
            <span>{label}</span>
          </label>
        ))}
        <label>
          <input
            type="checkbox"
            checked={optionCone}
            onChange={(e) => toggleCone("option_cone", e.target.checked)}
          />
          {translate(locale, "research.pricehistorychart.optionsImpliedInterval80")}
        </label>
        <label>
          <input
            type="checkbox"
            checked={modelCone}
            onChange={(e) => toggleCone("model_cone", e.target.checked)}
          />
          {translate(
            locale,
            "research.pricehistorychart.modelQuantileInterval3HorizonAnchorsOnly",
          )}
        </label>
        <span className="price-history__marker-count">
          {translate(locale, "research.pricehistorychart.loadedMarkers.withValues", {
            markerCount: markers.length,
          })}
        </span>
      </fieldset>
      <div
        aria-label={translate(
          locale,
          "research.pricehistorychart.ohlcVolume20DayChannelAtrBufferRegimesAnd",
          { symbol: symbol },
        )}
        className="price-history__canvas"
        ref={containerRef}
        role="img"
      />
      {coneEnabled && (
        <div className="price-history__cone-info">
          {coneQuery.isPending && (
            <p role="status">
              {translate(
                locale,
                "research.pricehistorychart.loadingFutureIntervalEvidence",
              )}
            </p>
          )}
          {coneQuery.isError && (
            <div role="alert">
              {translate(
                locale,
                "research.pricehistorychart.unableToLoadIntervalEvidence",
              )}
              <ResearchErrorDetail message={coneQuery.error.message} />
            </div>
          )}
          {optionCone && coneItem && !optionAnchorAligned && (
            <p role="status">
              {translate(
                locale,
                "research.pricehistorychart.theOptionsSnapshotDoesNotMatchTheLastPrice",
              )}
            </p>
          )}
          {coneQuery.isSuccess &&
            coneBands.every((band) => band.points.filter(Boolean).length < 2) && (
              <p>
                {translate(
                  locale,
                  "research.pricehistorychart.noFutureIntervalsCanBeDrawnForThisSnapshot",
                )}
              </p>
            )}
          {modelCone &&
            coneQuery.isSuccess &&
            !coneBands.some((band) => band.color === MODEL_COLOR) && (
              <p>
                {translate(
                  locale,
                  "research.pricehistorychart.theModelDidNotProvideEligibleQ10Q90Intervals",
                )}
              </p>
            )}
          <p>
            {translate(
              locale,
              "research.pricehistorychart.futureHorizontalPositionsRepresent120TradingDaysCalendar.withValues",
              {
                marketAsOf: coneItem?.market_as_of
                  ? formatUsMarketTime(coneItem.market_as_of, locale)
                  : translate(locale, "research.pricehistorychart.timeUnconfirmed"),
                close:
                  last?.close.toFixed(2) ??
                  translate(locale, "research.optionanalyticspanel.missing"),
                translate: last
                  ? history.timeframe === "1d"
                    ? translate(locale, "research.pricehistorychart.tradingDay", {
                        at: last.at.slice(0, 10),
                      })
                    : formatUsMarketTime(last.at, locale)
                  : translate(locale, "research.optionanalyticspanel.missing"),
              },
            )}
          </p>
          <p role="status">
            {hoveredCone ||
              translate(
                locale,
                "research.pricehistorychart.hoverOverAFutureIntervalToViewPriceAnd",
              )}
          </p>
          <p>{impliedCaveat(locale)}</p>
          <ChartExplainer kind="cones" />
        </div>
      )}
      <p className="price-history__note">
        {translate(
          locale,
          "research.pricehistorychart.theChartShowsUnadjustedPricesNotTotalPositionReturns",
        )}
      </p>
      <p className="price-history__marker-hover" role="status">
        {hoveredMarker ||
          translate(
            locale,
            "research.pricehistorychart.hoverOverAMarkedCandleForDetailsOrExpand",
          )}
      </p>
      <details className="research-governance">
        <summary>
          {translate(
            locale,
            "research.pricehistorychart.loadedMarkerDetails.withValues",
            { markerCount: markers.length },
          )}
        </summary>
        <p>
          {translate(
            locale,
            "research.pricehistorychart.circlesDirectionalArrowsAndSquaresShowRegimesBreakoutsAnd",
          )}
        </p>
        <ul aria-label={translate(locale, "research.pricehistorychart.loadedMarkers")}>
          {markers.map((marker) => {
            const at = new Date(Number(marker.time) * 1000).toISOString();
            return (
              <li key={String(marker.time)}>
                <time dateTime={at} title={at}>
                  {history.timeframe === "1d"
                    ? translate(locale, "research.pricehistorychart.tradingDay", {
                        at: at.slice(0, 10),
                      })
                    : formatUsMarketTime(at, locale)}
                </time>
                {"; "}
                <button
                  type="button"
                  onFocus={() => setHoveredMarkerTime(marker.time)}
                  onClick={() => setHoveredMarkerTime(marker.time)}
                >
                  {marker.text}
                </button>
              </li>
            );
          })}
        </ul>
      </details>

      {lastBar !== undefined && lastIndicator !== undefined ? (
        <div className="research-table-scroll price-history__summary">
          <table
            aria-label={translate(
              locale,
              "research.pricehistorychart.latestChartValues",
            )}
          >
            <thead>
              <tr>
                <th scope="col">
                  {history.timeframe === "1d"
                    ? translate(locale, "research.pricehistorychart.latestTradingDay")
                    : translate(
                        locale,
                        "research.pricehistorychart.latestIntervalTime",
                      )}
                </th>
                <th scope="col">
                  {translate(locale, "research.pricehistorychart.open")}
                </th>
                <th scope="col">
                  {translate(locale, "research.pricehistorychart.high")}
                </th>
                <th scope="col">
                  {translate(locale, "research.pricehistorychart.low")}
                </th>
                <th scope="col">
                  {translate(locale, "research.pricehistorychart.close")}
                </th>
                <th scope="col">
                  {translate(locale, "research.optionPulseChart.volume")}
                </th>
                <th scope="col">
                  {translate(
                    locale,
                    "research.pricehistorychart.20DayChannelLowerBound",
                  )}
                </th>
                <th scope="col">
                  {translate(
                    locale,
                    "research.pricehistorychart.20DayChannelUpperBound",
                  )}
                </th>
                <th scope="col">
                  {translate(locale, "research.pricehistorychart.atrBuffer")}
                </th>
                <th scope="col">
                  {translate(locale, "research.pricehistorychart.currentRegime")}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <time dateTime={lastBar.at} title={lastBar.at}>
                    {history.timeframe === "1d"
                      ? translate(locale, "research.pricehistorychart.tradingDay", {
                          at: lastBar.at.slice(0, 10),
                        })
                      : formatUsMarketTime(lastBar.at, locale)}
                  </time>
                </td>
                <td className="numeric-value">{lastBar.open.toFixed(2)}</td>
                <td className="numeric-value">{lastBar.high.toFixed(2)}</td>
                <td className="numeric-value">{lastBar.low.toFixed(2)}</td>
                <td className="numeric-value">{lastBar.close.toFixed(2)}</td>
                <td className="numeric-value">
                  {lastBar.volume.toLocaleString(locale)}
                </td>
                <td className="numeric-value">
                  {lastIndicator.channelLower.toFixed(2)}
                </td>
                <td className="numeric-value">
                  {lastIndicator.channelUpper.toFixed(2)}
                </td>
                <td className="numeric-value">
                  {lastIndicator.bufferLower.toFixed(2)} –
                  {lastIndicator.bufferUpper.toFixed(2)}
                </td>
                <td>{REGIME_LABELS(locale)[lastBar.regime]}</td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <p className="research-card__empty">
          {translate(locale, "research.pricehistorychart.noPriceBarsInTheCurrentRange")}
        </p>
      )}

      <div className="price-history__annotations">
        <section>
          <h3>{translate(locale, "research.pricehistorychart.priceStructure")}</h3>
          {structureMarkers.length === 0 ? (
            <p>
              {translate(
                locale,
                "research.pricehistorychart.noRecentBreakoutOrRegimeTransitionInTheCurrent",
              )}
            </p>
          ) : (
            <ul
              aria-label={translate(
                locale,
                "research.pricehistorychart.recentPriceStructureMarkers",
              )}
            >
              {structureMarkers.map((marker, index) => {
                const at = new Date(Number(marker.time) * 1000).toISOString();
                return (
                  <li key={`${String(marker.time)}-${marker.text ?? index}`}>
                    <time dateTime={at} title={at}>
                      {history.timeframe === "1d"
                        ? translate(locale, "research.pricehistorychart.tradingDay", {
                            at: at.slice(0, 10),
                          })
                        : formatUsMarketTime(at, locale)}
                    </time>
                    <strong>{marker.text}</strong>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
        <section>
          <h3>{translate(locale, "research.optionpulsechart.gap")}</h3>
          <p>
            {history.gaps.length === 0
              ? translate(
                  locale,
                  "research.pricehistorychart.noGapRecordsWereProvidedForThisSnapshotThis",
                )
              : translate(
                  locale,
                  "research.pricehistorychart.theChartContainsGapIntervals",
                  { gapCount: history.gaps.length },
                )}
          </p>
          {history.gaps.length === 0 ? null : (
            <ul aria-label={translate(locale, "research.pricehistorychart.dataGap")}>
              {history.gaps.map((gap) => (
                <li key={`${gap.start_at}-${gap.end_at}`}>
                  <time dateTime={gap.start_at} title={gap.start_at}>
                    {history.timeframe === "1d"
                      ? translate(locale, "research.pricehistorychart.tradingDay", {
                          at: gap.start_at.slice(0, 10),
                        })
                      : formatUsMarketTime(gap.start_at, locale)}
                  </time>
                  <span>{translate(locale, "research.pricehistorychart.to")}</span>
                  <time dateTime={gap.end_at} title={gap.end_at}>
                    {history.timeframe === "1d"
                      ? translate(locale, "research.pricehistorychart.tradingDay", {
                          at: gap.end_at.slice(0, 10),
                        })
                      : formatUsMarketTime(gap.end_at, locale)}
                  </time>
                  <strong>
                    {translate(locale, "research.evidence.originalGapReason")}
                  </strong>
                  <ResearchErrorDetail message={gap.reason} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </section>
  );
}
