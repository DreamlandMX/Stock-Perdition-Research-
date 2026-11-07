import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import type { components } from "../../api/generated/schema";
import { modelStatusLabel, reasonLabel } from "../../app/presentation";
import { AccessibleEChart } from "./ReliabilityChart";
type FoldMetrics = components["schemas"]["FoldMetrics"];
type IntervalCalibration = components["schemas"]["IntervalCalibrationEvidence"];
function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}
export interface IntervalCoverageChartProps {
  calibration: IntervalCalibration;
  folds: readonly FoldMetrics[];
}
export function IntervalCoverageChart({
  calibration,
  folds,
}: IntervalCoverageChartProps) {
  const { locale } = useI18n();
  const option = useMemo<EChartsOption>(
    () => ({
      grid: { bottom: 52, left: 58, right: 58, top: 24 },
      legend: {
        data: [
          translate(locale, "backtests.rawCoverage2"),
          translate(locale, "backtests.cqrCoverage2"),
          translate(locale, "backtests.targetCoverage"),
          translate(locale, "backtests.cqrMeanWidth"),
        ],
        textStyle: { color: "#94a3b8" },
      },
      series: [
        {
          data: folds.map((fold) => fold.raw_interval_coverage),
          name: translate(locale, "backtests.rawCoverage2"),
          type: "line",
          yAxisIndex: 0,
        },
        {
          data: folds.map((fold) => fold.interval_coverage),
          name: translate(locale, "backtests.cqrCoverage2"),
          type: "line",
          yAxisIndex: 0,
        },
        {
          data: folds.map(() => calibration.target_coverage),
          lineStyle: { type: "dashed" },
          name: translate(locale, "backtests.targetCoverage"),
          showSymbol: false,
          type: "line",
          yAxisIndex: 0,
        },
        {
          data: folds.map((fold) => fold.interval_width),
          name: translate(locale, "backtests.cqrMeanWidth"),
          type: "line",
          yAxisIndex: 1,
        },
      ],
      xAxis: {
        data: folds.map((fold) => fold.fold_id),
        name: translate(locale, "backtests.independentOosFold"),
        nameLocation: "middle",
        nameGap: 34,
        type: "category",
      },
      yAxis: [
        {
          max: 1,
          min: 0,
          name: translate(locale, "backtests.coverage"),
          type: "value",
        },
        { min: 0, name: translate(locale, "backtests.intervalWidth"), type: "value" },
      ],
    }),
    [calibration.target_coverage, folds, locale],
  );
  const summary =
    calibration.status === "available"
      ? translate(locale, "backtests.targetCoverageRawAndCalibratedCoverageWidthAnd", {
          method: String(calibration.method),
          target_coverage: String(percent(calibration.target_coverage)),
        })
      : translate(locale, "backtests.unavailable", {
          method: String(calibration.method),
          unknown_reason: reasonLabel(calibration.unavailable_reason, locale),
        });
  return (
    <AccessibleEChart
      option={option}
      summary={summary}
      tableLabel={translate(locale, "backtests.viewCqrIntervalCoverageData")}
      title={translate(locale, "backtests.cqrIntervalCoverageAndWidth")}
    >
      <table aria-label={translate(locale, "backtests.cqrIntervalCoverageData")}>
        <thead>
          <tr>
            <th scope="col">{translate(locale, "backtests.fold")}</th>
            <th scope="col">{translate(locale, "backtests.methodStatus")}</th>
            <th scope="col">{translate(locale, "backtests.targetCoverage")}</th>
            <th scope="col">{translate(locale, "backtests.rawCoverage2")}</th>
            <th scope="col">{translate(locale, "backtests.cqrCoverage2")}</th>
            <th scope="col">{translate(locale, "backtests.rawWidth")}</th>
            <th scope="col">{translate(locale, "backtests.cqrWidth")}</th>
            <th scope="col">{translate(locale, "backtests.tailFailures")}</th>
            <th scope="col">{translate(locale, "backtests.samples")}</th>
          </tr>
        </thead>
        <tbody>
          {folds.map((fold) => (
            <tr key={fold.fold_id}>
              <th scope="row">{fold.fold_id}</th>
              <td>
                {calibration.method} / {modelStatusLabel(calibration.status, locale)}
              </td>
              <td>{percent(calibration.target_coverage)}</td>
              <td>{percent(fold.raw_interval_coverage)}</td>
              <td>{percent(fold.interval_coverage)}</td>
              <td>{percent(fold.raw_interval_width)}</td>
              <td>{percent(fold.interval_width)}</td>
              <td>{percent(fold.tail_failure_rate)}</td>
              <td>{fold.sample_count.toLocaleString(locale)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </AccessibleEChart>
  );
}
