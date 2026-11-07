import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useEffect, useRef, type ReactNode } from "react";
import {
  BarChart,
  HeatmapChart,
  LineChart,
  RadarChart,
  ScatterChart,
  SankeyChart,
  PieChart,
  CandlestickChart,
  CustomChart,
} from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  RadarComponent,
  TooltipComponent,
  VisualMapComponent,
  MarkLineComponent,
  MarkAreaComponent,
  DataZoomComponent,
  GraphicComponent,
} from "echarts/components";
import { init, use, type EChartsCoreOption } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { LabelLayout } from "echarts/features";
import type { EChartsOption } from "echarts";

use([
  BarChart,
  CanvasRenderer,
  GridComponent,
  HeatmapChart,
  LegendComponent,
  LineChart,
  RadarChart,
  RadarComponent,
  ScatterChart,
  SankeyChart,
  PieChart,
  CandlestickChart,
  CustomChart,
  LabelLayout,
  TooltipComponent,
  VisualMapComponent,
  MarkLineComponent,
  MarkAreaComponent,
  DataZoomComponent,
  GraphicComponent,
]);

interface OverviewTerminalChartProps {
  onCellSelect?: ((dataIndex: number) => void) | undefined;
  children: ReactNode;
  option: EChartsOption;
  summary: string;
  tableLabel: string;
  title: string;
}

export function OverviewTerminalChart({
  onCellSelect,
  children,
  option,
  summary,
  tableLabel,
  title,
}: OverviewTerminalChartProps) {
  const { locale } = useI18n();
  const chartRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<ReturnType<typeof init> | undefined>(undefined);
  const optionRef = useRef<EChartsCoreOption>({});
  const onCellSelectRef = useRef(onCellSelect);

  useEffect(() => {
    onCellSelectRef.current = onCellSelect;
  }, [onCellSelect]);

  useEffect(() => {
    optionRef.current = {
      textStyle: {
        color: "#a9a8b6",
        fontFamily: "Segoe UI, Microsoft YaHei UI, sans-serif",
      },
      color: ["#b4a7f5", "#3ecf8e", "#f2647a", "#f0b860"],
      ...option,
      animation:
        option.animation !== false &&
        !(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false),
    };
    const current = instanceRef.current?.getOption?.();
    const savedZooms = current?.dataZoom;
    if (Array.isArray(savedZooms) && option.dataZoom) {
      const zooms = Array.isArray(option.dataZoom)
        ? option.dataZoom
        : [option.dataZoom];
      optionRef.current.dataZoom = zooms.map((zoom, index) => {
        const saved = savedZooms[index] as Record<string, unknown> | undefined;
        return {
          ...zoom,
          ...Object.fromEntries(
            ["start", "end", "startValue", "endValue"].flatMap((key) =>
              saved?.[key] === undefined ? [] : [[key, saved[key]]],
            ),
          ),
        };
      });
    }
    const savedLegends = current?.legend;
    if (Array.isArray(savedLegends) && option.legend) {
      const legends = Array.isArray(option.legend) ? option.legend : [option.legend];
      const oldSeries = (Array.isArray(current?.series) ? current.series : []) as {
        name?: string | number;
        data?: { name?: string | number }[];
      }[];
      const series = Array.isArray(option.series)
        ? option.series
        : option.series
          ? [option.series]
          : [];
      optionRef.current.legend = legends.map((legend, index) => {
        const selected = (
          savedLegends[index] as { selected?: Record<string, boolean> } | undefined
        )?.selected;
        return {
          ...legend,
          selected: {
            ...legend.selected,
            ...Object.fromEntries(
              series.flatMap((item, seriesIndex) => {
                const old = oldSeries[seriesIndex];
                const pairs = [[old?.name, item.name]];
                if (Array.isArray(item.data)) {
                  item.data.forEach((datum: unknown, dataIndex) => {
                    if (
                      typeof datum === "object" &&
                      datum !== null &&
                      "name" in datum
                    ) {
                      const name: unknown = datum.name;
                      if (typeof name === "string" || typeof name === "number")
                        pairs.push([old?.data?.[dataIndex]?.name, name]);
                    }
                  });
                }
                return pairs.flatMap(([oldName, newName]) =>
                  oldName == null ||
                  newName == null ||
                  selected?.[String(oldName)] === undefined
                    ? []
                    : [[String(newName), selected[String(oldName)]]],
                );
              }),
            ),
          },
        };
      });
    }
    instanceRef.current?.setOption(optionRef.current, true);
  }, [option]);

  useEffect(() => {
    const container = chartRef.current;
    if (container === null || typeof ResizeObserver === "undefined") {
      return;
    }
    let nearViewport = typeof IntersectionObserver === "undefined";
    let chart: ReturnType<typeof init> | undefined;
    const resize = () => {
      if (
        !nearViewport ||
        container.clientWidth === 0 ||
        container.clientHeight === 0 ||
        container.checkVisibility?.() === false
      )
        return;
      if (chart) {
        chart.resize();
        return;
      }
      chart = init(container);
      instanceRef.current = chart;
      chart.on("click", (event: unknown) => {
        if (
          typeof event === "object" &&
          event !== null &&
          "dataIndex" in event &&
          typeof event.dataIndex === "number"
        )
          onCellSelectRef.current?.(event.dataIndex);
      });
      chart.setOption(optionRef.current, true);
      viewportObserver?.disconnect();
    };
    const viewportObserver =
      typeof IntersectionObserver === "undefined"
        ? undefined
        : new IntersectionObserver(
            (entries) => {
              nearViewport = entries.some((entry) => entry.isIntersecting);
              resize();
            },
            { rootMargin: "300px" },
          );
    viewportObserver?.observe(container);
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    const details = container.closest("details");
    details?.addEventListener("toggle", resize);
    resize();
    return () => {
      observer.disconnect();
      viewportObserver?.disconnect();
      details?.removeEventListener("toggle", resize);
      chart?.dispose();
      instanceRef.current = undefined;
    };
  }, []);

  return (
    <section aria-label={title} className="overview-terminal-chart">
      <header>
        <h3>{title}</h3>
        <p>{summary}</p>
      </header>
      <div
        aria-label={translate(locale, "overview.OverviewTerminalChart.chart", {
          title: title,
          summary: summary,
        })}
        className="overview-terminal-chart__canvas"
        ref={chartRef}
        role="img"
      />
      <details>
        <summary>{tableLabel}</summary>
        <div className="overview-terminal-chart__table">{children}</div>
      </details>
    </section>
  );
}
