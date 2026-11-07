import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo } from "react";
import type { EChartsOption, SeriesOption } from "echarts";
import type { components } from "../../api/generated/schema";
import { AccessibleEChart } from "./ReliabilityChart";
type EconomicPoint = components["schemas"]["EconomicBacktestPoint"];
function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}
export interface EconomicBacktestChartProps {
  points: readonly EconomicPoint[];
}
export function EconomicBacktestChart({ points }: EconomicBacktestChartProps) {
  const { locale } = useI18n();
  const foldIds = useMemo(
    () => Array.from(new Set(points.map((point) => point.fold_id))),
    [points, locale],
  );
  const option = useMemo<EChartsOption>(() => {
    const series = foldIds.flatMap<SeriesOption>((foldId) => {
      const foldPoints = points.filter((point) => point.fold_id === foldId);
      return [
        {
          data: foldPoints.map((point) => [point.at, point.equity]),
          name: translate(locale, "backtests.equity", { foldId: String(foldId) }),
          type: "line",
          yAxisIndex: 0,
        },
        ...(["drawdown", "cash_fraction", "turnover", "cumulative_cost"] as const).map(
          (key) => ({
            data: foldPoints.map((point) => [point.at, point[key]]),
            name: `${foldId} ${
              {
                cash_fraction: translate(locale, "backtests.cashFraction"),
                cumulative_cost: translate(locale, "backtests.cumulativeCost"),
                drawdown: translate(locale, "backtests.drawdown"),
                turnover: translate(locale, "backtests.turnover"),
              }[key]
            }`,
            type: "line" as const,
            yAxisIndex: 1,
          }),
        ),
      ];
    });
    return {
      grid: { bottom: 52, left: 58, right: 58, top: 24 },
      legend: { textStyle: { color: "#94a3b8" }, type: "scroll" },
      series,
      xAxis: {
        name: translate(locale, "backtests.oosDate"),
        nameLocation: "middle",
        nameGap: 34,
        type: "time",
      },
      yAxis: [
        { min: 0, name: translate(locale, "backtests.equity2"), type: "value" },
        { name: translate(locale, "backtests.fractionCost"), type: "value" },
      ],
    };
  }, [foldIds, points, locale]);
  const last = points.at(-1);
  const summary =
    last === undefined
      ? translate(locale, "backtests.noEconomicSeriesInTheSelectedWindowOther")
      : translate(locale, "backtests.endingEquityDrawdownCumulativeCostTurnover", {
          toFixed: String(last.equity.toFixed(3)),
          drawdown: String(percent(last.drawdown)),
          cumulative_cost: String(percent(last.cumulative_cost)),
          turnover: String(percent(last.turnover)),
        });
  return (
    <AccessibleEChart
      option={option}
      summary={summary}
      tableLabel={translate(locale, "backtests.viewEconomicBacktestData")}
      title={translate(locale, "backtests.costsAndTurnoverSeries")}
    >
      <table aria-label={translate(locale, "backtests.economicBacktestData")}>
        <thead>
          <tr>
            <th scope="col">{translate(locale, "backtests.fold")}</th>
            <th scope="col">{translate(locale, "backtests.date")}</th>
            <th scope="col">{translate(locale, "backtests.equity2")}</th>
            <th scope="col">{translate(locale, "backtests.drawdown")}</th>
            <th scope="col">{translate(locale, "backtests.cashFraction")}</th>
            <th scope="col">{translate(locale, "backtests.turnover")}</th>
            <th scope="col">{translate(locale, "backtests.cumulativeCost")}</th>
          </tr>
        </thead>
        <tbody>
          {points.map((point) => (
            <tr key={`${point.fold_id}-${point.at}`}>
              <th scope="row">{point.fold_id}</th>
              <td>{point.at.slice(0, 10)}</td>
              <td>{point.equity.toFixed(3)}</td>
              <td>{percent(point.drawdown)}</td>
              <td>{percent(point.cash_fraction)}</td>
              <td>{percent(point.turnover)}</td>
              <td>{percent(point.cumulative_cost)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </AccessibleEChart>
  );
}
