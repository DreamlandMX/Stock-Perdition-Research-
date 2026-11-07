import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type Time,
  type SeriesMarker,
} from "lightweight-charts";
import type { EChartsOption, SeriesOption } from "echarts";
import type { components } from "../../api/generated/schema";
import {
  sectorLabel,
  formatUsMarketTime,
  reasonLabel,
  servingStatusLabel,
} from "../../app/presentation";
import { OverviewTerminalChart } from "./OverviewTerminalChart";
import { getRegimeLabels } from "./RegimeDistribution";
import {
  buildMarketResearchHref,
  formatReturn,
  symbolLabel,
  type MarketPoint,
  type MarketLinkState,
} from "./overview-terminal";
import {
  cumulativeMedianPath,
  REGIME_ORDER,
  REGIME_COLORS,
  SECTOR_COLORS,
  type DailyBar,
  type OverviewHistory,
} from "./overview-history";

type OverviewData = components["schemas"]["OverviewReadModel"];
export function OverviewChapterNav() {
  const { locale } = useI18n();
  const [active, setActive] = useState("overview-market");
  const chapters = [
    ["overview-market", translate(locale, "overview.OverviewHistoryPanels.market")],
    ["overview-sectors", translate(locale, "overview.SectorRegimeMatrix.sector")],
    ["overview-symbols", translate(locale, "overview.OverviewPage.instrument")],
    ["overview-research", translate(locale, "overview.OverviewPage.research")],
    ["overview-risk", translate(locale, "overview.OverviewPage.risksCoverage")],
  ];
  useEffect(() => {
    const update = () => {
      let selected = "overview-market";
      for (const id of [
        "overview-market",
        "overview-sectors",
        "overview-symbols",
        "overview-research",
        "overview-risk",
      ])
        if (
          (document.getElementById(id)?.getBoundingClientRect().top ?? Infinity) < 140
        )
          selected = id;
      setActive(selected);
    };
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, []);
  return (
    <nav
      className="ov-chapter-tabs"
      aria-label={translate(locale, "overview.OverviewHistoryPanels.chapters")}
    >
      {chapters.map(([id, label], i) => (
        <a
          key={id}
          href={`#${id}`}
          aria-current={active === id ? "location" : undefined}
        >
          <i>0{i + 1}</i>
          {label}
        </a>
      ))}
      <span>
        {translate(
          locale,
          "overview.OverviewHistoryPanels.marketSectorsInstrumentsResearch",
        )}
      </span>
    </nav>
  );
}
const axis = {
  axisLabel: { color: "#706f7d", fontSize: 10 },
  axisLine: { lineStyle: { color: "#303039" } },
  axisTick: { show: false },
  splitLine: { lineStyle: { color: "rgba(232,230,245,.05)" } },
};
const grid = { left: 8, right: 12, top: 35, bottom: 8, containLabel: true };
const legend = {
  top: 0,
  left: 0,
  itemWidth: 10,
  itemHeight: 8,
  textStyle: { color: "#a9a8b6", fontSize: 11 },
};
const percent = (v: number | null | undefined) =>
  v == null ? "—" : `${(v * 100).toFixed(0)}%`;
const pp = (v: number | null | undefined, locale: Locale = "zh-CN") =>
  v == null
    ? translate(locale, "overview.SectorMarketMap.insufficientHistory")
    : `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)}pp`;
const color = (v: number | null | undefined) =>
  v == null || v === 0 ? "#8b8a99" : v > 0 ? "#3ecf8e" : "#f2647a";
const sectorColor = (s: string) => SECTOR_COLORS[s] ?? "#8b8a99";
const pctData = (values: readonly (number | null)[]) =>
  values.map((v) => (v == null ? null : v * 100));

function Chart({
  title,
  summary,
  option,
  rows = [],
  children,
}: {
  title: string;
  summary: string;
  option: EChartsOption;
  rows?: readonly (readonly ReactNode[])[];
  children?: ReactNode;
}) {
  const { locale } = useI18n();
  return (
    <OverviewTerminalChart
      title={title}
      summary={summary}
      option={option}
      tableLabel={translate(locale, "overview.OverviewHistoryPanels.viewData", {
        title: title,
      })}
    >
      {children ?? (
        <table
          aria-label={translate(locale, "overview.OverviewHistoryPanels.data", {
            title: title,
          })}
        >
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </OverviewTerminalChart>
  );
}
function Panel({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: ReactNode;
}) {
  return (
    <section className="ov-section ov-v6-panel" aria-label={title}>
      <header>
        <h3>{title}</h3>
        {note ? <p>{note}</p> : null}
      </header>
      {children}
    </section>
  );
}
function lineOption(dates: string[], series: SeriesOption[]): EChartsOption {
  return {
    grid,
    legend,
    tooltip: {
      trigger: "axis",
      valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(1)}%`),
    },
    xAxis: {
      ...axis,
      type: "category",
      data: dates.map((d) => d.slice(5)),
      boundaryGap: false,
    },
    yAxis: {
      ...axis,
      type: "value",
      axisLabel: { ...axis.axisLabel, formatter: "{value}%" },
    },
    series,
  };
}
function lines(
  name: string,
  values: (number | null)[],
  lineColor: string,
  stacked = false,
): SeriesOption {
  return {
    name,
    type: "line",
    data: pctData(values),
    symbol: "none",
    connectNulls: false,
    itemStyle: { color: lineColor },
    lineStyle: { color: lineColor, width: 1.5 },
    ...(stacked
      ? { stack: "regime", areaStyle: { color: lineColor, opacity: 0.78 } }
      : {}),
  };
}

export function OverviewDailyChart({
  bars,
  history,
  point,
  compact = false,
}: {
  bars: readonly DailyBar[];
  history?: OverviewHistory | undefined;
  point?: MarketPoint | undefined;
  compact?: boolean;
}) {
  const { locale } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const dailyChart = useRef<ReturnType<typeof createChart> | undefined>(undefined);
  const dailyMarkers = useRef<ReturnType<typeof createSeriesMarkers<Time>> | undefined>(
    undefined,
  );
  useEffect(() => {
    if (!ref.current || !bars.length || /jsdom/i.test(navigator.userAgent)) return;
    const chart = createChart(ref.current, {
      localization: { locale },
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: "#706f7d",
        fontSize: 10,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: compact ? "transparent" : "rgba(232,230,245,.05)" },
        horzLines: { color: compact ? "transparent" : "rgba(232,230,245,.05)" },
      },
      rightPriceScale: {
        visible: !compact,
        borderVisible: false,
        scaleMargins: { top: 0.12, bottom: 0.22 },
      },
      timeScale: { visible: !compact, borderVisible: false },
      handleScroll: false,
      handleScale: false,
    });
    dailyChart.current = chart;
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#3ecf8e",
      downColor: "#f2647a",
      wickUpColor: "#3ecf8e",
      wickDownColor: "#f2647a",
      borderVisible: false,
      priceLineVisible: false,
      lastValueVisible: !compact,
    });
    candles.setData(
      bars.map((b) =>
        b.data_gap
          ? { time: b.at.slice(0, 10) as Time }
          : {
              time: b.at.slice(0, 10) as Time,
              open: b.open,
              high: b.high,
              low: b.low,
              close: b.close,
            },
      ),
    );
    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: "volume",
      priceLineVisible: false,
      lastValueVisible: false,
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    volume.setData(
      bars.map((b) =>
        b.data_gap
          ? { time: b.at.slice(0, 10) as Time }
          : {
              time: b.at.slice(0, 10) as Time,
              value: b.volume,
              color: b.close >= b.open ? "rgba(62,207,142,.3)" : "rgba(242,100,122,.3)",
            },
      ),
    );
    if (!compact) {
      const backdrop = chart.addSeries(HistogramSeries, {
        priceScaleId: "regime",
        priceLineVisible: false,
        lastValueVisible: false,
      });
      chart.priceScale("regime").applyOptions({ scaleMargins: { top: 0, bottom: 0 } });
      backdrop.setData(
        bars.map((b) => ({
          time: b.at.slice(0, 10),
          value: 1,
          color: `${REGIME_COLORS[b.regime]}12`,
        })),
      );
      const markers: SeriesMarker<Time>[] = [];
      for (let i = 3; i < bars.length; i++) {
        const b = bars[i]!,
          r = b.regime;
        if (
          b.data_gap ||
          bars[i - 1]!.data_gap ||
          r === bars[i - 1]!.regime ||
          (r !== "uptrend" && r !== "downtrend")
        )
          continue;
        if (
          i < bars.length - 1 &&
          bars.slice(i, i + 3).some((x) => x.regime !== r || x.data_gap)
        )
          continue;
        markers.push({
          time: b.at.slice(0, 10) as Time,
          position: r === "uptrend" ? "belowBar" : "aboveBar",
          color: REGIME_COLORS[r],
          shape: r === "uptrend" ? "arrowUp" : "arrowDown",
        });
      }
      if (point?.breakout_up_rule || point?.breakout_down_rule) {
        const time = bars.at(-1)!.at.slice(0, 10) as Time;
        const i = markers.findIndex((m) => m.time === time);
        if (i >= 0) markers.splice(i, 1);
        markers.push({
          time,
          position: point.breakout_up_rule ? "aboveBar" : "belowBar",
          color: "#f0b860",
          shape: "circle",
          text: "",
        });
      }
      dailyMarkers.current = createSeriesMarkers(candles, markers);
      if (point && history) {
        const peers = (history.sectorSymbols[point.sector] ?? []).filter(
          (s) => s !== point.symbol && history.histories[s]?.length,
        );
        const dates = bars.map((b) => b.at.slice(0, 10));
        const path = cumulativeMedianPath(peers, history.histories, dates);
        const series = chart.addSeries(LineSeries, {
          color: "#b4a7f5",
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        series.setData(
          dates.map((date, i) =>
            peers.length < 2 || path[i] == null
              ? { time: date as Time }
              : { time: date as Time, value: bars[0]!.close * (1 + path[i]) },
          ),
        );
      }
    }
    chart.timeScale().subscribeSizeChange(() => chart.timeScale().fitContent());
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      dailyChart.current = undefined;
      dailyMarkers.current = undefined;
    };
  }, [bars, compact, history, point]);
  useEffect(() => {
    dailyChart.current?.applyOptions({ localization: { locale } });
    const markers = dailyMarkers.current;
    if (markers) {
      markers.setMarkers(
        markers.markers().map((marker) =>
          marker.shape === "circle"
            ? {
                ...marker,
                text: translate(locale, "overview.OverviewPulse.ruleBreakout"),
              }
            : marker,
        ),
      );
    }
  }, [locale, bars, compact, history, point]);
  return (
    <div
      className={`ov-daily-chart${compact ? " ov-daily-chart--compact" : ""}`}
      ref={ref}
      role="img"
      aria-label={translate(
        locale,
        "overview.OverviewHistoryPanels.rawDailyBarsTradingDays",
        {
          symbol:
            point?.symbol ?? translate(locale, "overview.OverviewHistoryPanels.index"),
          count: bars.length,
        },
      )}
    >
      {bars.length < 2 ? (
        <p className="ov-empty">
          {translate(locale, "overview.SectorMarketMap.insufficientHistory")}
        </p>
      ) : null}
    </div>
  );
}

export function OverviewProxyCharts({
  data,
  history,
  linkState,
}: {
  data: OverviewData;
  history: OverviewHistory;
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const order = ["US.SPY", "US.QQQ", "US.DIA", "US.IWM"],
    colors = ["#b4a7f5", "#6fb3ff", "#f0b860", "#5fd4d0"];
  const names: Record<string, string> = {
    "US.SPY": translate(locale, "overview.OverviewHistoryPanels.sP500"),
    "US.QQQ": translate(locale, "overview.OverviewHistoryPanels.nasdaq100"),
    "US.DIA": translate(locale, "overview.OverviewHistoryPanels.dowJones"),
    "US.IWM": translate(locale, "overview.OverviewHistoryPanels.russell2000"),
  };
  const series = order.map((s, i) => {
    const bars = history.histories[s]?.slice(-60) ?? [],
      base = bars[0]?.close;
    return lines(
      symbolLabel(s),
      history.dates.map((date) => {
        const b = bars.find((b) => b.at.slice(0, 10) === date);
        return b && base && !b.data_gap ? b.close / base - 1 : null;
      }),
      colors[i]!,
    );
  });
  return (
    <div
      className="ov-v6-proxies"
      role="region"
      aria-label={translate(
        locale,
        "overview.OverviewHistoryPanels.fourMajorIndexQuotes",
      )}
    >
      {order.map((s) => {
        const p = data.proxies.find((p) => p.symbol === s);
        return (
          <section className="ov-v6-index" key={s}>
            <Link
              to={buildMarketResearchHref(s, linkState)}
              aria-label={translate(
                locale,
                "overview.OverviewHistoryPanels.openResearch",
                { symbol: symbolLabel(s), value: names[s] ?? s },
              )}
            >
              <b data-testid="market-proxy-symbol">{symbolLabel(s)}</b>
              <small>{names[s]}</small>
              <em>
                {p
                  ? getRegimeLabels(locale)[p.regime]
                  : translate(
                      locale,
                      "overview.OverviewHistoryPanels.insufficientData",
                    )}
              </em>
              <strong>{p?.last_close?.toFixed(2) ?? "—"}</strong>
              <span style={{ color: color(p?.session_return) }}>
                {formatReturn(p?.session_return ?? null, locale)}
              </span>
            </Link>
            <OverviewDailyChart bars={history.histories[s]?.slice(-60) ?? []} compact />
          </section>
        );
      })}
      <Chart
        title={translate(
          locale,
          "overview.OverviewHistoryPanels.fourIndexRelativePerformance",
        )}
        summary={translate(
          locale,
          "overview.OverviewHistoryPanels.rebasedToZeroLast60TradingDaysUnadjusted",
        )}
        option={{
          ...lineOption(history.dates, series),
          grid: { left: 4, right: 28, top: 5, bottom: 16 },
          legend: { show: false },
          yAxis: {
            ...axis,
            type: "value",
            axisLabel: { show: false },
            splitLine: { show: false },
          },
        }}
        rows={order.map((s) => [symbolLabel(s), history.histories[s]?.length ?? 0])}
      />
    </div>
  );
}

export function OverviewBreadthStrip({
  data,
  history,
}: {
  data: OverviewData;
  history: OverviewHistory;
}) {
  const { locale } = useI18n();
  const last = history.breadth.at(-1),
    e = data.internal_evidence,
    previous = history.breadth.at(-21),
    r = history.returns["US.SPY"]?.r20.at(-1);
  const entries = [
    [
      translate(locale, "overview.OverviewHistoryPanels.above20DayMovingAverage"),
      percent(last?.p20),
      last?.p20 != null && previous?.p20 != null
        ? pp(last.p20 - previous.p20, locale)
        : translate(locale, "overview.SectorMarketMap.insufficientHistory"),
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.above50DayMovingAverage"),
      percent(last?.p50),
      translate(locale, "overview.OverviewHistoryPanels.eligible", {
        n50: last?.n50 ?? 0,
        count: e?.market_points.length ?? 0,
      }),
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.60DayHighsLows"),
      `${last?.high ?? "—"} / ${last?.low ?? "—"}`,
      translate(locale, "overview.OverviewHistoryPanels.eligible2", {
        n60: last?.n60 ?? 0,
      }),
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.advancersDeclinersADRatio"),
      `${e?.advancers ?? "—"} / ${e?.decliners ?? "—"}`,
      e?.decliners ? ((e.advancers ?? 0) / e.decliners).toFixed(2) : "—",
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.medianReturn"),
      formatReturn(e?.median_session_return ?? null, locale),
      translate(locale, "overview.OverviewHistoryPanels.sPY20Day", {
        returnValue: formatReturn(r ?? null, locale),
      }),
    ],
    [
      translate(locale, "overview.OverviewPulse.returnDispersion"),
      percent(e?.return_dispersion),
      translate(
        locale,
        "overview.OverviewHistoryPanels.crossSectionNotAForecastInterval",
      ),
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.unusualVolume"),
      String(e?.current_anomaly_snapshot?.volume_anomalies ?? "—"),
      "|Z20| ≥ 2",
    ],
  ];
  return (
    <dl className="ov-v6-strip">
      {entries.map(([label, value, note]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>
            {value}
            <small>{note}</small>
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function OverviewMarketHistory({
  data,
  history,
}: {
  data: OverviewData;
  history: OverviewHistory;
}) {
  const { locale } = useI18n();
  const e = data.internal_evidence,
    last = history.breadth.at(-1),
    prev = history.breadth.at(-21),
    spyR = history.returns["US.SPY"]?.r20.at(-1);
  const breadthOption: EChartsOption = {
    grid: [
      { left: 42, right: 16, top: 36, height: "30%" },
      { left: 42, right: 16, top: "44%", height: "24%" },
      { left: 42, right: 16, top: "75%", bottom: 22 },
    ],
    legend,
    tooltip: { trigger: "axis" },
    xAxis: [0, 1, 2].map((i) => ({
      ...axis,
      type: "category",
      gridIndex: i,
      data: history.dates.map((d) => d.slice(5)),
      axisLabel: { ...axis.axisLabel, show: i === 2 },
    })),
    yAxis: [0, 1, 2].map((i) => ({
      ...axis,
      type: "value",
      gridIndex: i,
      scale: i === 0,
      ...(i === 1 ? { min: 0, max: 100 } : {}),
      splitNumber: 2,
    })),
    series: [
      {
        name: translate(locale, "overview.OverviewHistoryPanels.sPYClose"),
        type: "line",
        symbol: "none",
        data: history.dates.map(
          (d) =>
            history.histories["US.SPY"]?.find((b) => b.at.slice(0, 10) === d)?.close ??
            null,
        ),
        lineStyle: { color: "#ecebf2", width: 1.5 },
        itemStyle: { color: "#ecebf2" },
      },
      ...([20, 50] as const).map((n, i): SeriesOption => ({
        name: translate(
          locale,
          "overview.OverviewHistoryPanels.aboveDayMovingAverage",
          { count: n },
        ),
        type: "line",
        xAxisIndex: 1,
        yAxisIndex: 1,
        symbol: "none",
        data: history.breadth.map((b) =>
          b[n === 20 ? "p20" : "p50"] == null
            ? null
            : b[n === 20 ? "p20" : "p50"]! * 100,
        ),
        itemStyle: { color: i ? "#f0b860" : "#6fb3ff" },
        lineStyle: { width: 1.5 },
      })),
      {
        name: translate(locale, "overview.OverviewHistoryPanels.60DayHighs"),
        type: "bar",
        xAxisIndex: 2,
        yAxisIndex: 2,
        data: history.breadth.map((b) => b.high),
        itemStyle: { color: "#3ecf8e" },
        stack: "highlow",
      },
      {
        name: translate(locale, "overview.OverviewHistoryPanels.60DayLows"),
        type: "bar",
        xAxisIndex: 2,
        yAxisIndex: 2,
        data: history.breadth.map((b) => (b.low == null ? null : -b.low)),
        itemStyle: { color: "#f2647a" },
        stack: "highlow",
      },
    ],
  };
  const recent = history.breadth.slice(-10).map((b) => b.p20),
    complete = recent.length === 10 && recent.every((v) => v != null),
    min = complete ? Math.min(...recent) : null;
  const thrust =
    min != null
      ? min < 0.4 && Math.max(...(recent.slice(recent.indexOf(min)) as number[])) > 0.6
      : null;
  const delta = last?.p20 != null && prev?.p20 != null ? last.p20 - prev.p20 : null;
  const ad =
    e?.advancers != null && e?.decliners != null ? e.advancers + e.decliners : 0;
  const upShare = ad ? e!.advancers! / ad : null,
    downShare = ad ? e!.decliners! / ad : null,
    lowShare = last?.n60 && last.low != null ? last.low / last.n60 : null;
  const cards = [
    [
      translate(locale, "overview.OverviewHistoryPanels.priceBreadthDivergence"),
      spyR != null && delta != null
        ? Math.sign(spyR) !== Math.sign(delta) && delta !== 0
        : null,
      `SPY ${formatReturn(spyR ?? null, locale)}`,
      translate(
        locale,
        "overview.OverviewHistoryPanels.20DayPriceDirectionOpposesMovingAverageBreadthChange",
      ),
      translate(locale, "overview.OverviewHistoryPanels.above20DayMA", {
        percentage: percent(prev?.p20),
        percentage2: percent(last?.p20),
      }),
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.breadthThrust"),
      thrust,
      `${percent(min)} → ${percent(last?.p20)}`,
      translate(
        locale,
        "overview.OverviewHistoryPanels.fromBelow40ToAbove60Within10Days",
      ),
      translate(locale, "overview.OverviewHistoryPanels.eligibleDays10", {
        count: recent.filter((v) => v != null).length,
      }),
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.broadParticipation"),
      upShare != null ? upShare >= 0.9 : null,
      percent(upShare),
      translate(
        locale,
        "overview.OverviewHistoryPanels.advancers90OfAdvancersPlusDecliners",
      ),
      translate(locale, "overview.OverviewHistoryPanels.advancingMoving", {
        advancers: e?.advancers ?? "—",
        value: ad || "—",
      }),
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.concentratedSellingPressure"),
      downShare != null && lowShare != null
        ? downShare >= 0.9 || lowShare >= 0.2
        : downShare != null && downShare >= 0.9
          ? true
          : null,
      percent(downShare),
      translate(locale, "overview.OverviewHistoryPanels.decliners90Or60DayLows20"),
      translate(locale, "overview.OverviewHistoryPanels.newLows", {
        low: last?.low ?? "—",
        percentage: percent(lowShare),
      }),
    ],
  ] as const;
  const transitions = e?.regime_transition_cells ?? [],
    regimes = REGIME_ORDER.filter((r) =>
      transitions.some((c) => c.previous_regime === r || c.current_regime === r),
    );
  const sankey: EChartsOption = {
    tooltip: { trigger: "item" },
    series: [
      {
        type: "sankey",
        left: 90,
        right: 90,
        top: 12,
        bottom: 12,
        nodeWidth: 10,
        nodeGap: 12,
        draggable: false,
        layoutIterations: 0,
        label: { color: "#ecebf2", fontSize: 11 },
        data: regimes.flatMap((r) => [
          {
            name: translate(locale, "overview.OverviewHistoryPanels.previous2", {
              value: getRegimeLabels(locale)[r],
            }),
            depth: 0,
            itemStyle: { color: REGIME_COLORS[r] },
            label: { position: "left" as const },
          },
          {
            name: translate(locale, "overview.OverviewHistoryPanels.current", {
              value: getRegimeLabels(locale)[r],
            }),
            depth: 1,
            itemStyle: { color: REGIME_COLORS[r] },
          },
        ]),
        links: transitions
          .filter((c) => c.count > 0)
          .map((c) => ({
            source: translate(locale, "overview.OverviewHistoryPanels.previous2", {
              value: getRegimeLabels(locale)[c.previous_regime],
            }),
            target: translate(locale, "overview.OverviewHistoryPanels.current", {
              value: getRegimeLabels(locale)[c.current_regime],
            }),
            value: c.count,
            lineStyle: {
              color:
                c.previous_regime === c.current_regime
                  ? "#8b8a99"
                  : REGIME_COLORS[c.current_regime],
              opacity: c.previous_regime === c.current_regime ? 0.13 : 0.5,
            },
          })),
      },
    ],
  };
  return (
    <>
      <div className="ov-v6-grid ov-v6-grid--breadth">
        <Chart
          title={translate(
            locale,
            "overview.OverviewHistoryPanels.marketBreadthHistory",
          )}
          summary={translate(
            locale,
            "overview.OverviewHistoryPanels.calculatedFromObservedRawDailyBarsEligible2050",
            { n20: last?.n20 ?? 0, n50: last?.n50 ?? 0, n60: last?.n60 ?? 0 },
          )}
          option={breadthOption}
          rows={history.dates.map((date, i) => [
            date,
            percent(history.breadth[i]?.p20),
            percent(history.breadth[i]?.p50),
            history.breadth[i]?.high ?? "—",
            history.breadth[i]?.low ?? "—",
          ])}
        />
        <Panel
          title={translate(locale, "overview.OverviewHistoryPanels.breadthSignals")}
          note={translate(
            locale,
            "overview.OverviewHistoryPanels.fourFixedRulesNotTradingSignals",
          )}
        >
          <div className="ov-breadth-cards">
            {cards.map(([title, on, value, rule, note]) => (
              <section key={title} data-triggered={on}>
                <header>
                  <b>{title}</b>
                  <span>
                    {on == null
                      ? translate(
                          locale,
                          "overview.SectorMarketMap.insufficientHistory",
                        )
                      : on
                        ? translate(locale, "overview.OverviewHistoryPanels.triggered")
                        : translate(
                            locale,
                            "overview.OverviewHistoryPanels.notTriggered",
                          )}
                  </span>
                </header>
                <strong>{value}</strong>
                <p>{rule}</p>
                <small>{note}</small>
              </section>
            ))}
          </div>
        </Panel>
      </div>
      <div className="ov-v6-grid">
        <Chart
          title={translate(
            locale,
            "overview.OverviewHistoryPanels.marketRegimeComposition",
          )}
          summary={translate(
            locale,
            "overview.OverviewHistoryPanels.dailyBarsWithEligibleRegimeSamplesLastTradingDays",
            {
              value: history.regimeTotals.at(-1) ?? 0,
              count: e?.market_points.length ?? 0,
              count2: history.dates.length,
            },
          )}
          option={lineOption(
            history.dates,
            REGIME_ORDER.map((r) =>
              lines(
                getRegimeLabels(locale)[r],
                history.regimes[r],
                REGIME_COLORS[r],
                true,
              ),
            ),
          )}
          rows={history.dates.map((date, i) => [
            date,
            ...REGIME_ORDER.map((r) => percent(history.regimes[r][i])),
          ])}
        />
        <Chart
          title={translate(locale, "overview.OverviewHistoryPanels.regimeTransitions")}
          summary={
            e?.comparison_snapshot
              ? translate(
                  locale,
                  "overview.OverviewHistoryPanels.currentTwoCompatibleSnapshots",
                  { time: formatUsMarketTime(e.comparison_snapshot.as_of, locale) },
                )
              : translate(
                  locale,
                  "overview.OverviewHistoryPanels.insufficientHistoryNoCompatibleSnapshot",
                )
          }
          option={sankey}
          rows={transitions.map((c) => [
            getRegimeLabels(locale)[c.previous_regime],
            getRegimeLabels(locale)[c.current_regime],
            c.count,
          ])}
        />
      </div>
    </>
  );
}

export function OverviewSignalBoard({
  points,
  linkState,
}: {
  points: readonly MarketPoint[];
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const location = useLocation();
  const definitions: [
    string,
    (p: MarketPoint) => boolean,
    (p: MarketPoint) => number | null | undefined,
    number,
  ][] = [
    [
      translate(locale, "overview.OverviewHistoryPanels.upwardBreakout"),
      (p) => !!p.breakout_up_rule,
      (p) => p.session_return,
      -1,
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.downwardBreakout"),
      (p) => !!p.breakout_down_rule,
      (p) => p.session_return,
      1,
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.unusuallyHighVolume"),
      (p) => (p.volume_z_20 ?? 0) >= 2,
      (p) => p.volume_z_20,
      -1,
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.unusuallyLowVolume"),
      (p) => (p.volume_z_20 ?? 0) <= -2,
      (p) => p.volume_z_20,
      1,
    ],
    ...(["uptrend", "downtrend", "transition", "range"] as const).map(
      (r): (typeof definitions)[number] => [
        translate(locale, "overview.OverviewHistoryPanels.changedTo", {
          value: getRegimeLabels(locale)[r],
        }),
        (p) => !!p.regime_changed && p.regime === r,
        (p) => (r === "uptrend" || r === "downtrend" ? p.return_20 : p.session_return),
        r === "downtrend" ? 1 : -1,
      ],
    ),
    [
      translate(locale, "overview.OverviewHistoryPanels.aboveUpperChannel"),
      (p) => (p.channel_position_20 ?? 0) > 1,
      (p) => p.channel_position_20,
      -1,
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.belowLowerChannel"),
      (p) => (p.channel_position_20 ?? 1) < 0,
      (p) => p.channel_position_20,
      1,
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.20DayGainers"),
      (p) => p.return_20 != null && p.return_20 > 0,
      (p) => p.return_20,
      -1,
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.20DayLosers"),
      (p) => p.return_20 != null && p.return_20 < 0,
      (p) => p.return_20,
      1,
    ],
  ];
  return (
    <Panel
      title={translate(locale, "overview.OverviewHistoryPanels.ruleSignalBoard")}
      note={translate(
        locale,
        "overview.OverviewHistoryPanels.instrumentsTriggeringEachRuleInThisSnapshotRankedBy",
      )}
    >
      <div className="ov-signal-board">
        {definitions.map(([title, filter, value, direction]) => {
          const all = points
            .filter(filter)
            .sort((a, b) => direction * ((value(a) ?? 0) - (value(b) ?? 0)));
          const ranked = title.startsWith(
            translate(locale, "overview.SectorMarketMap.20Days"),
          )
            ? all.slice(0, 12)
            : all;
          return (
            <div key={title}>
              <b>{title}</b>
              <small>{ranked.length}</small>
              <div className="ov-signal-chips">
                {ranked.length ? (
                  ranked.slice(0, 12).map((p) => (
                    <Link
                      key={p.symbol}
                      state={{ returnTo: `${location.pathname}${location.search}` }}
                      to={buildMarketResearchHref(p.symbol, linkState)}
                    >
                      <b>{symbolLabel(p.symbol)}</b>
                      <span style={{ color: color(value(p)) }}>
                        {title.includes(
                          translate(locale, "overview.OverviewHistoryPanels.volume"),
                        )
                          ? `Z ${value(p)?.toFixed(1)}`
                          : formatReturn(value(p) ?? null, locale)}
                      </span>
                    </Link>
                  ))
                ) : (
                  <span>
                    {translate(
                      locale,
                      "overview.OverviewHistoryPanels.noneInThisSnapshot",
                    )}
                  </span>
                )}
                {ranked.length > 12 ? <span>+{ranked.length - 12}</span> : null}
              </div>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

export function OverviewSectorHistory({
  data,
  history,
  linkState,
}: {
  data: OverviewData;
  history: OverviewHistory;
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const cards = [...(data.internal_evidence?.sector_scorecards ?? [])].sort(
      (a, b) => b.sample_count - a.sample_count,
    ),
    changes = data.internal_evidence?.sector_uptrend_changes ?? [];
  const paths = history.sectors.map((s) =>
    lines(sectorLabel(s.sector, locale), s.path, sectorColor(s.sector)),
  );
  const tails = history.sectors.flatMap((s) => s.rotation);
  const xBound = Math.max(2, ...tails.map((p) => Math.abs(p[0]!) * 1.15));
  const yBound = Math.max(2, ...tails.map((p) => Math.abs(p[1]!) * 1.15));
  const rotation: EChartsOption = {
    grid: { ...grid, top: 24, bottom: 30 },
    tooltip: { trigger: "item" },
    xAxis: {
      ...axis,
      type: "value",
      name: translate(locale, "overview.OverviewHistoryPanels.20DayRelativeToSPY"),
      nameLocation: "middle",
      nameGap: 22,
      min: -xBound,
      max: xBound,
    },
    yAxis: {
      ...axis,
      type: "value",
      name: translate(locale, "overview.OverviewHistoryPanels.5DayRelativeToSPY"),
      min: -yBound,
      max: yBound,
    },
    series: [
      {
        type: "scatter",
        data: [],
        markLine: {
          silent: true,
          symbol: "none",
          label: { show: false },
          data: [{ xAxis: 0 }, { yAxis: 0 }],
        },
        markArea: {
          silent: true,
          label: { color: "#a9a8b6", fontSize: 10, position: "insideTop" },
          data: [
            [
              {
                name: translate(locale, "overview.OverviewHistoryPanels.leading"),
                xAxis: 0,
                yAxis: 0,
                itemStyle: { color: "#3ecf8e12" },
              },
              { xAxis: xBound, yAxis: yBound },
            ],
            [
              {
                name: translate(locale, "overview.OverviewHistoryPanels.improving"),
                xAxis: -xBound,
                yAxis: 0,
                itemStyle: { color: "#b4a7f512" },
              },
              { xAxis: 0, yAxis: yBound },
            ],
            [
              {
                name: translate(locale, "overview.OverviewHistoryPanels.lagging"),
                xAxis: -xBound,
                yAxis: -yBound,
                itemStyle: { color: "#f2647a12" },
              },
              { xAxis: 0, yAxis: 0 },
            ],
            [
              {
                name: translate(locale, "overview.OverviewHistoryPanels.weakening"),
                xAxis: 0,
                yAxis: -yBound,
                itemStyle: { color: "#f0b86012" },
              },
              { xAxis: xBound, yAxis: 0 },
            ],
          ],
        },
      },
      ...history.sectors
        .filter((s) => s.rotation.length)
        .flatMap((s): SeriesOption[] => [
          {
            type: "line",
            data: s.rotation,
            symbolSize: 3,
            itemStyle: { color: sectorColor(s.sector) },
            lineStyle: { opacity: 0.4, width: 1 },
          },
          {
            name: sectorLabel(s.sector, locale),
            type: "scatter",
            data: [s.rotation.at(-1)!],
            symbolSize: 10,
            label: {
              show: true,
              formatter: sectorLabel(s.sector, locale),
              position: "right",
              color: "#ecebf2",
              fontSize: 10,
            },
            labelLayout: { moveOverlap: "shiftY" },
            itemStyle: { color: sectorColor(s.sector) },
          },
        ]),
    ],
  };
  const participants = history.sectors
    .map((s) => {
      const pts = data.internal_evidence!.market_points.filter(
          (p) => p.sector === s.sector,
        ),
        valid = pts.filter((p) => p.session_return != null);
      return {
        ...s,
        n: valid.length,
        up: valid.filter((p) => p.session_return! > 0).length,
        down: valid.filter((p) => p.session_return! < 0).length,
      };
    })
    .sort(
      (a, b) => (a.n ? (a.up - a.down) / a.n : 0) - (b.n ? (b.up - b.down) / b.n : 0),
    );
  const participation: EChartsOption = {
    grid,
    legend,
    tooltip: { trigger: "axis" },
    xAxis: {
      ...axis,
      type: "value",
      min: -100,
      max: 100,
      axisLabel: { ...axis.axisLabel, formatter: (v: number) => `${Math.abs(v)}%` },
    },
    yAxis: {
      ...axis,
      type: "category",
      data: participants.map((s) => sectorLabel(s.sector, locale)),
    },
    series: [
      {
        name: translate(locale, "overview.OverviewHistoryPanels.down"),
        type: "bar",
        stack: "p",
        data: participants.map((s) => (s.n ? (-s.down / s.n) * 100 : null)),
        itemStyle: { color: "#f2647a" },
        barWidth: 12,
      },
      {
        name: translate(locale, "overview.OverviewHistoryPanels.up"),
        type: "bar",
        stack: "p",
        data: participants.map((s) => (s.n ? (s.up / s.n) * 100 : null)),
        itemStyle: { color: "#3ecf8e" },
      },
    ],
  };
  const changeOption: EChartsOption = {
    grid,
    legend,
    tooltip: { trigger: "axis" },
    xAxis: { ...axis, type: "value", max: 100 },
    yAxis: {
      ...axis,
      type: "category",
      data: changes.map((s) => sectorLabel(s.sector, locale)),
    },
    series: [
      {
        name: translate(
          locale,
          "overview.OverviewHistoryPanels.previousCompatibleSnapshot",
        ),
        type: "bar",
        data: changes.map((s) => s.previous_uptrend_fraction * 100),
        barWidth: 7,
        itemStyle: { color: "#6f6e7c" },
      },
      {
        name: translate(locale, "overview.OverviewHistoryPanels.current2"),
        type: "bar",
        data: changes.map((s) => s.uptrend_fraction * 100),
        barWidth: 7,
        itemStyle: { color: "#b4a7f5" },
        label: {
          show: true,
          position: "right",
          formatter: (p) => pp(changes[p.dataIndex]?.change, locale),
          color: "#a9a8b6",
          fontSize: 10,
        },
      },
    ],
  };
  return (
    <>
      <div className="ov-v6-grid ov-v6-grid--sector">
        <Panel
          title={translate(
            locale,
            "overview.OverviewHistoryPanels.sectorPerformanceHeatmap",
          )}
          note={translate(
            locale,
            "overview.OverviewHistoryPanels.medianConstituentReturns60DayReturnsFromObservedDaily",
          )}
        >
          <div className="ov-table-scroll">
            <table
              aria-label={translate(
                locale,
                "overview.OverviewHistoryPanels.sectorPerformanceHeatmap",
              )}
            >
              <thead>
                <tr>
                  {[
                    translate(locale, "overview.SectorRegimeMatrix.sector"),
                    translate(locale, "overview.SectorMarketMap.samples"),
                    translate(locale, "overview.OverviewHistoryPanels.session"),
                    translate(locale, "overview.SectorMarketMap.5Days"),
                    translate(locale, "overview.SectorMarketMap.20Days"),
                    translate(locale, "overview.OverviewHistoryPanels.60Days"),
                    translate(locale, "options.optionsBoardRows.bullish"),
                    translate(locale, "overview.OverviewHistoryPanels.change"),
                    translate(locale, "overview.OverviewHistoryPanels.topGainers"),
                    translate(locale, "overview.OverviewHistoryPanels.topLosers"),
                  ].map((t) => (
                    <th key={t}>{t}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {cards.map((s) => {
                  const path = history.sectors.find((p) => p.sector === s.sector);
                  return (
                    <tr key={s.sector}>
                      <th>
                        <i style={{ background: sectorColor(s.sector) }} />
                        {sectorLabel(s.sector, locale)}
                      </th>
                      <td>{s.sample_count}</td>
                      {[
                        s.median_session_return,
                        s.median_return_5,
                        s.median_return_20,
                        path?.path.at(-1),
                      ].map((v, i) => (
                        <td
                          key={i}
                          className="ov-heat-cell"
                          style={{
                            color: color(v),
                            background: v == null ? undefined : `${color(v)}18`,
                          }}
                        >
                          {formatReturn(v ?? null, locale)}
                        </td>
                      ))}
                      <td>{percent(s.uptrend_fraction)}</td>
                      <td>
                        {pp(changes.find((c) => c.sector === s.sector)?.change, locale)}
                      </td>
                      {[s.leader_symbol, s.laggard_symbol].map((symbol, i) => (
                        <td key={i}>
                          {symbol ? (
                            <Link to={buildMarketResearchHref(symbol, linkState)}>
                              {symbolLabel(symbol)}
                            </Link>
                          ) : (
                            "—"
                          )}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
        <Chart
          title={translate(locale, "overview.OverviewHistoryPanels.sectorTrends")}
          summary={translate(
            locale,
            "overview.OverviewHistoryPanels.medianCumulativeConstituentReturnsLast60TradingDaysRaw",
          )}
          option={lineOption(history.dates, paths)}
          rows={history.sectors.map((s) => [
            sectorLabel(s.sector, locale),
            s.count,
            formatReturn(s.path.at(-1) ?? null, locale),
          ])}
        />
      </div>
      <div className="ov-v6-grid ov-v6-grid--three">
        <Chart
          title={translate(
            locale,
            "overview.OverviewHistoryPanels.sectorRotationQuadrants",
          )}
          summary={translate(
            locale,
            "overview.OverviewHistoryPanels.20DayAnd5DayReturnsRelativeToSPY",
          )}
          option={rotation}
          rows={history.sectors.map((s) => [
            sectorLabel(s.sector, locale),
            ...(s.rotation.at(-1) ?? [
              translate(locale, "overview.SectorMarketMap.insufficientHistory"),
              translate(locale, "overview.SectorMarketMap.insufficientHistory"),
            ]),
          ])}
        />
        <Chart
          title={translate(
            locale,
            "overview.OverviewHistoryPanels.sectorParticipation",
          )}
          summary={translate(
            locale,
            "overview.OverviewHistoryPanels.barLengthRepresentsAdvancingDecliningSectorSharesMissingReturns",
          )}
          option={participation}
          rows={participants.map((s) => [
            sectorLabel(s.sector, locale),
            s.up,
            s.down,
            `${s.n}/${s.count}`,
          ])}
        />
        <Panel
          title={translate(locale, "overview.OverviewHistoryPanels.sectorHighsLows")}
          note={translate(
            locale,
            "overview.OverviewHistoryPanels.60DayClosingHighsLowsAndMovingAverageParticipation",
          )}
        >
          <table
            aria-label={translate(
              locale,
              "overview.OverviewHistoryPanels.sectorHighsAndLows",
            )}
          >
            <thead>
              <tr>
                {[
                  translate(locale, "overview.SectorRegimeMatrix.sector"),
                  translate(locale, "overview.OverviewHistoryPanels.newHighs"),
                  translate(locale, "overview.OverviewHistoryPanels.newLows2"),
                  translate(locale, "overview.OverviewHistoryPanels.net"),
                  translate(locale, "overview.OverviewHistoryPanels.above20DayMA2"),
                  translate(locale, "overview.OverviewHistoryPanels.above50DayMA"),
                ].map((t) => (
                  <th key={t}>{t}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {history.sectors.map((s) => (
                <tr key={s.sector}>
                  <th>{sectorLabel(s.sector, locale)}</th>
                  <td style={{ color: "#3ecf8e" }}>{s.stats?.high ?? "—"}</td>
                  <td style={{ color: "#f2647a" }}>{s.stats?.low ?? "—"}</td>
                  <td>
                    {s.stats?.high != null && s.stats.low != null
                      ? s.stats.high - s.stats.low
                      : "—"}
                  </td>
                  <td>{percent(s.stats?.p20)}</td>
                  <td>{percent(s.stats?.p50)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="ov-sample-note">
            {translate(
              locale,
              "overview.OverviewHistoryPanels.eligibleHighsLowsSamples",
            )}{" "}
            {history.breadth.at(-1)?.n60 ?? 0} /{" "}
            {data.internal_evidence?.market_points.length ?? 0}
            {translate(
              locale,
              "overview.OverviewHistoryPanels.insufficientHistoryAndGapsExcluded",
            )}
          </p>
        </Panel>
      </div>
      <div className="ov-v6-grid">
        <Chart
          title={translate(
            locale,
            "overview.OverviewHistoryPanels.changeInSectorUptrendShare",
          )}
          summary={translate(
            locale,
            "overview.OverviewHistoryPanels.twoCompatibleSnapshotsSectorsWithFewerThan5Samples",
          )}
          option={changeOption}
          rows={changes.map((s) => [
            sectorLabel(s.sector, locale),
            percent(s.previous_uptrend_fraction),
            percent(s.uptrend_fraction),
            pp(s.change, locale),
          ])}
        />
        <Panel
          title={translate(locale, "overview.SectorRegimeMatrix.sectorRegimeMatrix")}
          note={translate(
            locale,
            "overview.OverviewHistoryPanels.withinRowShareDarkerColorsMeanLargerShares",
          )}
        >
          <table
            aria-label={translate(
              locale,
              "overview.SectorRegimeMatrix.sectorRegimeMatrix",
            )}
          >
            <thead>
              <tr>
                <th>{translate(locale, "overview.SectorRegimeMatrix.sector")}</th>
                {REGIME_ORDER.map((r) => (
                  <th key={r}>{getRegimeLabels(locale)[r]}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cards.map((s) => (
                <tr key={s.sector}>
                  <th>{sectorLabel(s.sector, locale)}</th>
                  {REGIME_ORDER.map((r) => {
                    const cell = data.sector_regime_matrix.find(
                      (c) => c.sector === s.sector && c.regime === r,
                    );
                    return (
                      <td
                        className="ov-heat-cell"
                        key={r}
                        style={{
                          background: `${REGIME_COLORS[r]}${Math.round(
                            (0.08 + (cell?.fraction ?? 0) * 0.62) * 255,
                          )
                            .toString(16)
                            .padStart(2, "0")}`,
                        }}
                        title={translate(locale, "overview.OverviewPulse.instruments", {
                          transitions: cell?.count ?? 0,
                        })}
                      >
                        {cell ? percent(cell.fraction) : "—"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>
    </>
  );
}

export function OverviewSymbolCharts({
  data,
  linkState,
}: {
  data: OverviewData;
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const points = data.internal_evidence?.market_points ?? [];
  const histogram = (values: number[], lo: number, step: number, count: number) => {
    const bins = Array.from({ length: count + 2 }, () => 0);
    values.forEach((v) => {
      const i = Math.max(0, Math.min(count + 1, Math.floor((v - lo) / step) + 1));
      bins[i] = (bins[i] ?? 0) + 1;
    });
    return bins;
  };
  const returns = points.flatMap((p) =>
      p.session_return == null ? [] : [p.session_return],
    ),
    retBins = histogram(returns, -0.05, 0.005, 20),
    channels = points.flatMap((p) =>
      p.channel_position_20 == null ? [] : [p.channel_position_20],
    ),
    chanBins = Array.from({ length: 12 }, () => 0);
  channels.forEach((v) => {
    const i = v < 0 ? 0 : v > 1 ? 11 : Math.min(10, Math.floor(v * 10) + 1);
    chanBins[i] = (chanBins[i] ?? 0) + 1;
  });
  const bars = (
    values: number[],
    labels: string[],
    colors: string[],
  ): EChartsOption => ({
    grid: { ...grid, top: 12 },
    tooltip: { trigger: "axis" },
    xAxis: { ...axis, type: "category", data: labels },
    yAxis: { ...axis, type: "value", minInterval: 1 },
    series: [
      {
        type: "bar",
        data: values.map((value, i) => ({
          value,
          itemStyle: { color: colors[i] ?? "#8b8a99" },
        })),
      },
    ],
  });
  const retLabels = [
      "<−5",
      ...Array.from({ length: 20 }, (_, i) => (-5 + i * 0.5).toFixed(1)),
      "≥5",
    ],
    retColors = retLabels.map((_, i) => (i < 11 ? "#8a2f3d" : "#24704a")),
    chanLabels = ["<0", ...Array.from({ length: 10 }, (_, i) => `${i * 10}`), ">100"];
  const scatter = (
    x: (p: MarketPoint) => number | null | undefined,
    y: (p: MarketPoint) => number | null | undefined,
    xName: string,
    yName: string,
  ): EChartsOption => {
    const valid = points.filter((p) => x(p) != null && y(p) != null);
    const quantile = (values: number[], q: number) => {
      const sorted = [...values].sort((a, b) => a - b);
      return (
        sorted[
          Math.min(sorted.length - 1, Math.max(0, Math.round(q * (sorted.length - 1))))
        ] ?? 0
      );
    };
    const xs = valid.map((p) => x(p)! * 100),
      ys = valid.map((p) => y(p)! * 100);
    const xmin = xName.includes("ATR") ? 0 : Math.floor(quantile(xs, 0.01) * 1.15),
      xmax = Math.max(
        xmin + 1,
        Math.ceil(quantile(xs, 0.99) * (xName.includes("ATR") ? 1.1 : 1.15)),
      ),
      ymin = Math.floor(quantile(ys, 0.01) * 1.15),
      ymax = Math.max(ymin + 1, Math.ceil(quantile(ys, 0.99) * 1.15));
    return {
      grid: { ...grid, top: 22 },
      legend,
      tooltip: { trigger: "item" },
      xAxis: { ...axis, type: "value", name: xName, min: xmin, max: xmax },
      yAxis: { ...axis, type: "value", name: yName, min: ymin, max: ymax },
      series: REGIME_ORDER.map((r) => ({
        name: getRegimeLabels(locale)[r],
        type: "scatter",
        symbolSize: 5,
        dimensions: [
          xName,
          yName,
          translate(locale, "overview.OverviewHistoryPanels.actual", { xName: xName }),
          translate(locale, "overview.OverviewHistoryPanels.actual", { xName: yName }),
        ],
        encode: { x: 0, y: 1, tooltip: [2, 3] },
        itemStyle: { color: REGIME_COLORS[r], opacity: 0.8 },
        data: valid
          .filter((p) => p.regime === r)
          .map((p) => {
            const a = x(p)! * 100,
              b = y(p)! * 100,
              cx = Math.min(xmax, Math.max(xmin, a)),
              cy = Math.min(ymax, Math.max(ymin, b));
            return {
              name: symbolLabel(p.symbol),
              value: [cx, cy, a, b],
              symbol: cx !== a || cy !== b ? "emptyCircle" : "circle",
            };
          }),
      })),
    };
  };
  const donut: EChartsOption = {
    tooltip: { trigger: "item" },
    series: [
      {
        type: "pie",
        radius: ["48%", "70%"],
        center: ["50%", "48%"],
        label: { show: false },
        data: data.regime_distribution.map((r) => ({
          name: getRegimeLabels(locale)[r.regime],
          value: r.count,
          itemStyle: { color: REGIME_COLORS[r.regime] },
        })),
      },
    ],
  };
  const volume = points
      .filter((p) => p.volume_z_20 != null)
      .sort((a, b) => Math.abs(b.volume_z_20!) - Math.abs(a.volume_z_20!))
      .slice(0, 10),
    maxVolume = Math.max(1, ...volume.map((p) => Math.abs(p.volume_z_20!)));
  return (
    <>
      <div className="ov-v6-grid ov-v6-grid--three">
        <Chart
          title={translate(
            locale,
            "overview.OverviewHistoryPanels.sessionReturnDistribution",
          )}
          summary={translate(
            locale,
            "overview.OverviewHistoryPanels.eligible05BinsTailsCountedSeparately",
            { count: returns.length, count2: points.length },
          )}
          option={bars(retBins, retLabels, retColors)}
          rows={retLabels.map((l, i) => [l, retBins[i]])}
        />
        <Chart
          title={translate(locale, "overview.OverviewHistoryPanels.5Day20DayReturns")}
          summary={translate(
            locale,
            "overview.OverviewHistoryPanels.x5DayY20DayDisplayClippedAt",
          )}
          option={scatter(
            (p) => p.return_5,
            (p) => p.return_20,
            translate(locale, "overview.OverviewHistoryPanels.5Day"),
            translate(locale, "overview.OverviewHistoryPanels.20Day"),
          )}
          rows={points.map((p) => [
            symbolLabel(p.symbol),
            formatReturn(p.return_5 ?? null, locale),
            formatReturn(p.return_20 ?? null, locale),
          ])}
        />
        <div className="ov-v6-donut">
          <Chart
            title={translate(
              locale,
              "overview.OverviewHistoryPanels.regimeDistribution",
            )}
            summary={translate(
              locale,
              "overview.OverviewHistoryPanels.currentSnapshotInstruments",
              { count: points.length },
            )}
            option={donut}
            rows={data.regime_distribution.map((r) => [
              getRegimeLabels(locale)[r.regime],
              r.count,
              `${(r.fraction * 100).toFixed(1)}%`,
            ])}
          />
          <dl>
            {data.regime_distribution.map((r) => (
              <div key={r.regime}>
                <dt>
                  <i style={{ background: REGIME_COLORS[r.regime] }} />
                  {getRegimeLabels(locale)[r.regime]}
                </dt>
                <dd>
                  {r.count}{" "}
                  {translate(locale, "overview.SectorRegimeMatrix.instruments")}{" "}
                  <b>{(r.fraction * 100).toFixed(1)}%</b>
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
      <div className="ov-v6-grid ov-v6-grid--three">
        <Chart
          title={translate(
            locale,
            "overview.OverviewHistoryPanels.20DayChannelPositionDistribution",
          )}
          summary={translate(
            locale,
            "overview.OverviewHistoryPanels.eligibleOutOfChannelValuesKeptInSeparateBins",
            { count: channels.length, count2: points.length },
          )}
          option={bars(
            chanBins,
            chanLabels,
            chanLabels.map((_, i) => (i < 6 ? "#8a2f3d" : "#24704a")),
          )}
          rows={chanLabels.map((l, i) => [l, chanBins[i]])}
        />
        <Chart
          title={translate(
            locale,
            "overview.OverviewHistoryPanels.volatility20DayReturn",
          )}
          summary={translate(
            locale,
            "overview.OverviewHistoryPanels.aTR2020DayReturnDisplayClippedAt1st99th",
          )}
          option={scatter(
            (p) => p.atr_pct_20,
            (p) => p.return_20,
            "ATR20 %",
            translate(locale, "overview.OverviewHistoryPanels.20Day"),
          )}
          rows={points.map((p) => [
            symbolLabel(p.symbol),
            percent(p.atr_pct_20),
            formatReturn(p.return_20 ?? null, locale),
          ])}
        />
        <Panel
          title={translate(
            locale,
            "overview.OverviewHistoryPanels.volumeAnomalyRanking",
          )}
          note={translate(
            locale,
            "overview.OverviewHistoryPanels.rankedBy20DayVolumeZ2CountsAs",
          )}
        >
          <ol className="ov-volume-list">
            {volume.map((p) => (
              <li key={p.symbol}>
                <Link to={buildMarketResearchHref(p.symbol, linkState)}>
                  {symbolLabel(p.symbol)}
                </Link>
                <span>
                  <i
                    style={{
                      width: `${(Math.abs(p.volume_z_20!) / maxVolume) * 100}%`,
                      background: Math.abs(p.volume_z_20!) >= 2 ? "#f0b860" : "#6f6f7d",
                    }}
                  />
                </span>
                <b>{p.volume_z_20!.toFixed(2)}</b>
              </li>
            ))}
          </ol>
        </Panel>
      </div>
    </>
  );
}

export function OverviewSnapshotComparison({
  data,
  mode,
  linkState,
}: {
  data: OverviewData;
  mode: string;
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const e = data.internal_evidence,
    curr = e?.history.at(-1),
    prev = e?.history.at(-2),
    churn =
      mode === "challenger"
        ? e?.challenger_candidate_churn
        : mode === "formal"
          ? null
          : e?.verified_candidate_churn;
  const rows: [
    string,
    number | null | undefined,
    number | null | undefined,
    boolean,
  ][] = [
    [
      translate(locale, "overview.OverviewHistoryPanels.20DayPositiveReturnBreadth"),
      prev?.market_breadth,
      curr?.market_breadth,
      true,
    ],
    [
      translate(locale, "overview.OverviewPulse.uptrendShare"),
      prev?.uptrend_fraction,
      curr?.uptrend_fraction,
      true,
    ],
    [
      translate(locale, "overview.OverviewPulse.crossSectionalMedianReturn"),
      prev?.median_session_return,
      curr?.median_session_return,
      true,
    ],
    [
      translate(locale, "overview.OverviewPulse.returnDispersion"),
      prev?.return_dispersion,
      curr?.return_dispersion,
      true,
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.unusualVolume"),
      prev?.volume_anomaly_count,
      curr?.volume_anomaly_count,
      false,
    ],
  ];
  return (
    <div className="ov-v6-grid">
      <Panel
        title={translate(
          locale,
          "overview.OverviewHistoryPanels.twoSnapshotComparison",
        )}
        note={
          prev && curr
            ? `${formatUsMarketTime(prev.as_of, locale)} → ${formatUsMarketTime(curr.as_of, locale)}`
            : translate(
                locale,
                "overview.OverviewHistoryPanels.insufficientHistoryNoTwoCompatibleSnapshots",
              )
        }
      >
        <table
          aria-label={translate(
            locale,
            "overview.OverviewHistoryPanels.twoSnapshotComparison",
          )}
        >
          <thead>
            <tr>
              <th>{translate(locale, "overview.OverviewHistoryPanels.metric")}</th>
              <th>{translate(locale, "overview.OverviewHistoryPanels.previous")}</th>
              <th>{translate(locale, "overview.OverviewHistoryPanels.current2")}</th>
              <th>{translate(locale, "overview.OverviewHistoryPanels.change")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, a, b, isPct]) => (
              <tr key={label}>
                <th>{label}</th>
                <td>{a == null ? "—" : isPct ? formatReturn(a, locale) : a}</td>
                <td>{b == null ? "—" : isPct ? formatReturn(b, locale) : b}</td>
                <td style={{ color: color(a != null && b != null ? b - a : null) }}>
                  {a == null || b == null
                    ? translate(locale, "overview.SectorMarketMap.insufficientHistory")
                    : isPct
                      ? pp(b - a, locale)
                      : b - a}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <Panel
        title={translate(
          locale,
          "overview.OverviewHistoryPanels.researchRankingEntriesExits",
        )}
        note={translate(
          locale,
          "overview.OverviewHistoryPanels.comparedWithPreviousCompatibleSnapshotFormalModeExcludesCandidate",
        )}
      >
        <div className="ov-v6-churn">
          {(["retained", "entered", "exited"] as const).map((status, i) => (
            <section key={status}>
              <h4>
                {
                  [
                    translate(locale, "overview.OverviewHistoryPanels.retained"),
                    translate(locale, "overview.OverviewHistoryPanels.newEntries"),
                    translate(locale, "overview.OverviewHistoryPanels.exited"),
                  ][i]
                }
              </h4>
              <ul>
                {(churn?.changes ?? [])
                  .filter((c) => c.status === status)
                  .map((c) => (
                    <li key={c.symbol}>
                      <Link to={buildMarketResearchHref(c.symbol, linkState)}>
                        {symbolLabel(c.symbol)}
                      </Link>
                      <small>
                        {c.previous_rank ?? "—"} → {c.current_rank ?? "—"}
                      </small>
                    </li>
                  ))}
              </ul>
            </section>
          ))}
        </div>
        {!churn ? (
          <p className="ov-empty">
            {translate(
              locale,
              "overview.OverviewHistoryPanels.insufficientHistoryOrNoComparableRankingInThisMode",
            )}
          </p>
        ) : null}
      </Panel>
    </div>
  );
}

export function OverviewRiskStatus({
  response,
}: {
  response: components["schemas"]["ResearchResponse_OverviewReadModel_"];
}) {
  const { locale } = useI18n();
  const e = response.data.internal_evidence,
    a = e?.current_anomaly_snapshot,
    b = e?.previous_anomaly_snapshot;
  const measures = [
    [
      translate(locale, "overview.OverviewHistoryPanels.unusualVolume"),
      "volume_anomalies",
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.aboveUpperChannel"),
      "upper_channel_count",
    ],
    [
      translate(locale, "overview.OverviewHistoryPanels.belowLowerChannel"),
      "lower_channel_count",
    ],
    [translate(locale, "overview.OverviewPulse.ruleBreakout"), "breakout_count"],
    [translate(locale, "overview.SectorMarketMap.regimeChange"), "regime_change_count"],
  ] as const;
  const max = Math.max(1, ...measures.flatMap(([, k]) => [a?.[k] ?? 0, b?.[k] ?? 0]));
  return (
    <>
      <Panel
        title={translate(
          locale,
          "overview.OverviewHistoryPanels.anomalySnapshotComparison",
        )}
        note={translate(
          locale,
          "overview.OverviewHistoryPanels.currentVsPreviousCompatibleSnapshotMissingValuesNotFilled",
        )}
      >
        <div className="ov-anomaly-bars">
          {measures.map(([label, k]) => (
            <div key={k}>
              <b>{label}</b>
              <span>
                <i
                  style={{
                    width: `${((b?.[k] ?? 0) / max) * 100}%`,
                    background: "#6f6e7c",
                  }}
                />
                <i
                  style={{
                    width: `${((a?.[k] ?? 0) / max) * 100}%`,
                    background: "#b4a7f5",
                  }}
                />
              </span>
              <small>
                {b?.[k] ?? "—"} → {a?.[k] ?? "—"}
              </small>
            </div>
          ))}
        </div>
      </Panel>
      <Panel
        title={translate(locale, "overview.OverviewHistoryPanels.dataStatus")}
        note={translate(
          locale,
          "overview.OverviewHistoryPanels.statusAndExclusionReasonsReturnedByTheAPI",
        )}
      >
        <dl className="ov-data-status">
          {[
            [
              translate(locale, "overview.OverviewHistoryPanels.dataStatus"),
              translate(locale, `overview.status.${response.data_status}`),
            ],
            [
              translate(locale, "overview.OverviewHistoryPanels.servingStatus"),
              servingStatusLabel(response.serving_status, locale),
            ],
            [
              translate(locale, "overview.OverviewHistoryPanels.forecastReadiness"),
              translate(
                locale,
                response.resolved_snapshot.prediction_readiness === "forecast_ready"
                  ? "overview.status.forecastReady"
                  : "overview.status.dataOnly",
              ),
            ],
            [
              translate(locale, "overview.OverviewHistoryPanels.marketCutoff"),
              formatUsMarketTime(response.resolved_snapshot.as_of, locale),
            ],
            [
              translate(locale, "overview.OverviewHistoryPanels.exclusionReasons"),
              response.exclusion_reason
                .map((code) => reasonLabel(code, locale))
                .join(";") || translate(locale, "overview.OverviewHistoryPanels.none"),
            ],
            [
              translate(locale, "overview.OverviewHistoryPanels.snapshot"),
              response.resolved_snapshot.snapshot_id,
            ],
          ].map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </Panel>
    </>
  );
}
