import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import type { components } from "../../api/generated/schema";
import { AccessibleEChart } from "./ReliabilityChart";
type FoldMetrics = components["schemas"]["FoldMetrics"];
type SurvivalPoint = components["schemas"]["SurvivalCalibrationPoint"];
function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}
export interface SurvivalCalibrationChartProps {
  folds: readonly FoldMetrics[];
  points: readonly SurvivalPoint[];
}
export function SurvivalCalibrationChart({
  folds,
  points,
}: SurvivalCalibrationChartProps) {
  const { locale } = useI18n();
  const option = useMemo<EChartsOption>(
    () => ({
      grid: { bottom: 52, left: 58, right: 22, top: 24 },
      legend: {
        data: [
          translate(locale, "backtests.observed5DayExit"),
          translate(locale, "backtests.observed20DayExit"),
          translate(locale, "backtests.idealCalibration"),
        ],
        textStyle: { color: "#94a3b8" },
      },
      series: [
        {
          data: points
            .filter((point) => point.horizon === 5)
            .map((point) => [
              point.predicted_exit_probability,
              point.observed_exit_frequency,
            ]),
          name: translate(locale, "backtests.observed5DayExit"),
          type: "line",
        },
        {
          data: points
            .filter((point) => point.horizon === 20)
            .map((point) => [
              point.predicted_exit_probability,
              point.observed_exit_frequency,
            ]),
          name: translate(locale, "backtests.observed20DayExit"),
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
        name: translate(locale, "backtests.predictedCumulativeExitProbability"),
        nameLocation: "middle",
        nameGap: 34,
        type: "value",
      },
      yAxis: {
        max: 1,
        min: 0,
        name: translate(locale, "backtests.observedCumulativeExitFrequency"),
        type: "value",
      },
    }),
    [points, locale],
  );
  const summary = translate(
    locale,
    "backtests.5And20DayCumulativeExitProbabilitiesAre",
  );
  return (
    <AccessibleEChart
      option={option}
      summary={summary}
      tableLabel={translate(locale, "backtests.viewSurvivalCalibrationData")}
      title={translate(locale, "backtests.survivalCalibration")}
    >
      <div className="backtest-stacked-tables">
        <table aria-label={translate(locale, "backtests.survivalCalibrationData")}>
          <thead>
            <tr>
              <th scope="col">{translate(locale, "backtests.horizon")}</th>
              <th scope="col">
                {translate(locale, "backtests.predictedCumulativeExit")}
              </th>
              <th scope="col">
                {translate(locale, "backtests.observedCumulativeExit")}
              </th>
              <th scope="col">{translate(locale, "backtests.samples")}</th>
            </tr>
          </thead>
          <tbody>
            {points.map((point) => (
              <tr key={`${point.horizon}-${point.predicted_exit_probability}`}>
                <th scope="row">
                  {point.horizon}
                  {" " + translate(locale, "backtests.days")}
                </th>
                <td>{percent(point.predicted_exit_probability)}</td>
                <td>{percent(point.observed_exit_frequency)}</td>
                <td>{point.sample_count.toLocaleString(locale)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <table aria-label={translate(locale, "backtests.survivalFoldMetrics")}>
          <thead>
            <tr>
              <th scope="col">{translate(locale, "backtests.foldTime")}</th>
              <th scope="col">{translate(locale, "backtests.timeBrier")}</th>
              <th scope="col">C-index</th>
              <th scope="col">{translate(locale, "backtests.samples")}</th>
            </tr>
          </thead>
          <tbody>
            {folds.map((fold) => (
              <tr key={fold.fold_id}>
                <th scope="row">
                  {fold.fold_id} / {fold.evaluation_end.slice(0, 10)}
                </th>
                <td>{fold.survival_brier.toFixed(3)}</td>
                <td>{fold.concordance_index.toFixed(3)}</td>
                <td>{fold.sample_count.toLocaleString(locale)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </AccessibleEChart>
  );
}
