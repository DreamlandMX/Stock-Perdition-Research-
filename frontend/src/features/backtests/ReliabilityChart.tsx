import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { LineChart } from "echarts/charts";
import { GridComponent, LegendComponent } from "echarts/components";
import { init, use, type EChartsCoreOption } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsOption } from "echarts";
import type { components } from "../../api/generated/schema";
type CalibrationBin = components["schemas"]["CalibrationBin"];
use([CanvasRenderer, GridComponent, LegendComponent, LineChart]);
interface AccessibleEChartProps {
  children: ReactNode;
  option: EChartsOption;
  summary: string;
  tableLabel: string;
  title: string;
}
export function AccessibleEChart({
  children,
  option,
  summary,
  tableLabel,
  title,
}: AccessibleEChartProps) {
  const { locale } = useI18n();
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<ReturnType<typeof init> | null>(null);
  const previousSeriesNames = useRef<readonly string[]>([]);
  useEffect(() => {
    const container = chartRef.current;
    if (container === null) {
      return;
    }
    const axisText = {
      axisLabel: { color: "#94a3b8" },
      nameTextStyle: { color: "#94a3b8" },
    };
    const chart = init(container, {
      categoryAxis: axisText,
      valueAxis: axisText,
      timeAxis: axisText,
    });
    chartInstanceRef.current = chart;
    let disposed = false;
    const observer = new ResizeObserver(() => {
      if (!disposed) {
        chart.resize();
      }
    });
    observer.observe(container);
    return () => {
      disposed = true;
      observer.disconnect();
      chart.dispose();
      chartInstanceRef.current = null;
    };
  }, []);
  useEffect(() => {
    const reducedMotion =
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    const current = chartInstanceRef.current?.getOption?.() as
      { legend?: Array<{ selected?: Record<string, boolean> }> } | undefined;
    const seriesNames = (Array.isArray(option.series) ? option.series : []).map(
      (series) => (typeof series.name === "string" ? series.name : ""),
    );
    const selection = current?.legend?.[0]?.selected;
    const selected = Object.fromEntries(
      seriesNames.map((name, index) => [
        name,
        selection?.[previousSeriesNames.current[index] ?? name] ?? true,
      ]),
    );
    chartInstanceRef.current?.setOption({
      ...option,
      ...(option.legend && !Array.isArray(option.legend)
        ? { legend: { ...option.legend, selected } }
        : {}),
      animation: !reducedMotion,
    } as EChartsCoreOption);
    previousSeriesNames.current = seriesNames;
  }, [option]);
  return (
    <section aria-label={title} className="backtest-chart-card">
      <header>
        <h2>{title}</h2>
        <p>{summary}</p>
      </header>
      <div
        aria-label={translate(locale, "backtests.chart", {
          title: String(title),
          summary: String(summary),
        })}
        className="backtest-chart"
        ref={chartRef}
        role="img"
      />
      <details className="backtest-chart-table" open>
        <summary>{tableLabel}</summary>
        <div className="backtest-table-scroll">{children}</div>
      </details>
    </section>
  );
}
function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}
export interface ReliabilityChartProps {
  bins: readonly CalibrationBin[];
}
export function ReliabilityChart({ bins }: ReliabilityChartProps) {
  const { locale } = useI18n();
  const total = bins.reduce((sum, bin) => sum + bin.sample_count, 0);
  const option = useMemo<EChartsOption>(
    () => ({
      grid: { bottom: 52, left: 58, right: 22, top: 24 },
      legend: {
        data: [
          translate(locale, "backtests.observedFrequency"),
          translate(locale, "backtests.idealCalibration"),
        ],
        textStyle: { color: "#94a3b8" },
      },
      series: [
        {
          data: bins.map((bin) => [bin.mean_prediction, bin.observed_frequency]),
          name: translate(locale, "backtests.observedFrequency"),
          symbolSize: 9,
          type: "line",
        },
        {
          data: [
            [0, 0],
            [1, 1],
          ],
          lineStyle: { type: "dashed" },
          name: translate(locale, "backtests.idealCalibration"),
          showSymbol: false,
          type: "line",
        },
      ],
      xAxis: {
        max: 1,
        min: 0,
        name: translate(locale, "backtests.meanPredictedProbability"),
        nameLocation: "middle",
        nameGap: 34,
        type: "value",
      },
      yAxis: {
        max: 1,
        min: 0,
        name: translate(locale, "backtests.observedFrequency"),
        nameLocation: "middle",
        nameGap: 42,
        type: "value",
      },
    }),
    [bins, locale],
  );
  const summary = translate(
    locale,
    "backtests.reliabilityBinsContainSamplesPointsCloserToThe",
    { US: String(total.toLocaleString(locale)) },
  );
  return (
    <AccessibleEChart
      option={option}
      summary={summary}
      tableLabel={translate(locale, "backtests.viewProbabilityReliabilityData")}
      title={translate(locale, "backtests.probabilityReliability")}
    >
      <table aria-label={translate(locale, "backtests.probabilityReliabilityData")}>
        <thead>
          <tr>
            <th scope="col">{translate(locale, "backtests.probabilityBin")}</th>
            <th scope="col">{translate(locale, "backtests.meanPrediction")}</th>
            <th scope="col">{translate(locale, "backtests.observedFrequency")}</th>
            <th scope="col">{translate(locale, "backtests.samples")}</th>
          </tr>
        </thead>
        <tbody>
          {bins.map((bin) => (
            <tr key={`${bin.lower_bound}-${bin.upper_bound}`}>
              <th scope="row">
                {percent(bin.lower_bound)}–{percent(bin.upper_bound)}
              </th>
              <td>{percent(bin.mean_prediction)}</td>
              <td>{percent(bin.observed_frequency)}</td>
              <td>{bin.sample_count.toLocaleString(locale)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </AccessibleEChart>
  );
}
