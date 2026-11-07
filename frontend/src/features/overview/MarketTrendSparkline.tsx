import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useEffect, useRef } from "react";
import {
  ColorType,
  createChart,
  LineSeries,
  type UTCTimestamp,
} from "lightweight-charts";

type TrendTone = "up" | "down" | "neutral";

const CHART_HEIGHT = 34;

const FALLBACK_COLORS: Record<TrendTone, string> = {
  down: "#f26767",
  neutral: "#8c96a8",
  up: "#35c995",
};

function trendColor(tone: TrendTone) {
  const variable =
    tone === "up"
      ? "--color-up"
      : tone === "down"
        ? "--color-down"
        : "--color-text-secondary";
  return (
    getComputedStyle(document.documentElement).getPropertyValue(variable).trim() ||
    FALLBACK_COLORS[tone]
  );
}

function measuredWidth(value: number) {
  return Number.isFinite(value) && value > 0 ? Math.max(1, Math.round(value)) : null;
}

export interface MarketTrendSparklineProps {
  directionLabel: string;
  symbol: string;
  tone: TrendTone;
  trend: readonly number[];
}

export function MarketTrendSparkline({
  directionLabel,
  symbol,
  tone,
  trend,
}: MarketTrendSparklineProps) {
  const { locale } = useI18n();
  const containerRef = useRef<HTMLDivElement>(null);
  const accessibleLabel =
    trend.length === 0
      ? translate(locale, "overview.MarketTrendSparkline.trendDataUnavailable", {
          symbol: symbol,
        })
      : translate(locale, "overview.MarketTrendSparkline.trendObservations", {
          symbol: symbol,
          directionLabel: directionLabel,
          count: trend.length,
        });

  useEffect(() => {
    const container = containerRef.current;
    if (container === null || trend.length === 0) {
      return undefined;
    }

    const color = trendColor(tone);
    const initialWidth = measuredWidth(container.clientWidth) ?? 1;
    const chart = createChart(container, {
      crosshair: {
        horzLine: { labelVisible: false, visible: false },
        vertLine: { labelVisible: false, visible: false },
      },
      grid: {
        horzLines: { visible: false },
        vertLines: { visible: false },
      },
      handleScale: false,
      handleScroll: false,
      height: CHART_HEIGHT,
      layout: {
        attributionLogo: false,
        background: { color: "transparent", type: ColorType.Solid },
        textColor: "transparent",
      },
      leftPriceScale: { visible: false },
      rightPriceScale: { borderVisible: false, visible: false },
      timeScale: { borderVisible: false, visible: false },
      width: initialWidth,
    });
    const series = chart.addSeries(LineSeries, {
      color,
      crosshairMarkerVisible: false,
      lastValueVisible: false,
      lineWidth: 2,
      priceLineVisible: false,
    });
    series.setData(
      trend.map((value, index) => ({
        time: (index + 1) as UTCTimestamp,
        value,
      })),
    );
    chart.timeScale().fitContent();

    let disposed = false;
    let renderedWidth = initialWidth;
    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries.find((candidate) => candidate.target === container);
      const nextWidth = measuredWidth(entry?.contentRect.width ?? 0);
      if (disposed || nextWidth === null || nextWidth === renderedWidth) {
        return;
      }
      renderedWidth = nextWidth;
      chart.resize(nextWidth, CHART_HEIGHT);
    });
    resizeObserver.observe(container);

    return () => {
      disposed = true;
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [tone, trend]);

  return (
    <div
      aria-label={accessibleLabel}
      className="market-proxy__sparkline"
      ref={containerRef}
      role="img"
    />
  );
}
