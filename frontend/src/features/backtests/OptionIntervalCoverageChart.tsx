import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import type { components } from "../../api/generated/schema";
import { AccessibleEChart } from "./ReliabilityChart";
import { ChartExplainer } from "../options/ChartExplainer";
import { optionPercent } from "../options/option-labels";
import { MODEL_COLOR, OPTION_COLOR } from "../options/options-copy";
export function OptionIntervalCoverageChart({
  coverage,
}: {
  coverage: components["schemas"]["OptionIntervalCoverage"][];
}) {
  const { locale } = useI18n();
  const rows = useMemo(
    () =>
      coverage.filter(
        (row) =>
          row.status === "available" &&
          row.model_coverage != null &&
          row.implied_coverage != null &&
          row.model_samples >= row.minimum_samples &&
          row.implied_samples >= row.minimum_samples,
      ),
    [coverage, locale],
  );
  const option = useMemo<EChartsOption>(
    () => ({
      animation: false,
      grid: { bottom: 50, left: 58, right: 25, top: 42 },
      legend: { top: 0, textStyle: { color: "#aaa8bb" } },
      tooltip: {
        trigger: "axis",
        valueFormatter: (value) =>
          typeof value === "number"
            ? `${value.toFixed(1)}%`
            : translate(locale, "backtests.missing"),
      },
      xAxis: {
        type: "category",
        data: rows.map((r) =>
          translate(locale, "backtests.days3", {
            horizon_days: String(r.horizon_days),
          }),
        ),
        name: translate(locale, "backtests.tradingDayHorizon"),
      },
      yAxis: {
        type: "value",
        min: 0,
        max: 100,
        name: translate(locale, "backtests.actualCoverage"),
      },
      series: [
        {
          name: translate(locale, "backtests.model80Interval"),
          type: "bar",
          itemStyle: { color: MODEL_COLOR },
          data: rows.map((r) => r.model_coverage! * 100),
          markLine: {
            silent: true,
            symbol: "none",
            data: [{ yAxis: 80, name: translate(locale, "backtests.nominal80") }],
            lineStyle: { type: "dashed", color: "#aaa8bb" },
            label: { formatter: translate(locale, "backtests.nominal80") },
          },
        },
        {
          name: translate(locale, "backtests.optionsImplied80Interval"),
          type: "bar",
          itemStyle: { color: OPTION_COLOR },
          data: rows.map((r) => r.implied_coverage! * 100),
        },
      ],
    }),
    [rows, locale],
  );
  return (
    <section
      className="backtest-panel"
      aria-label={translate(
        locale,
        "backtests.historicalCoverageOfImpliedAndModelIntervals",
      )}
    >
      <h2>
        {translate(locale, "backtests.impliedVsModelIntervalsHistoricalCoverage")}
      </h2>
      {rows.length ? (
        <AccessibleEChart
          title={translate(locale, "backtests.intervalCoverageInMatureSamples")}
          summary={translate(
            locale,
            "backtests.compareOnlyMatureSamplesWithTheSameHorizon",
          )}
          option={option}
          tableLabel={translate(locale, "backtests.viewHistoricalCoverageSamples")}
        >
          <table>
            <thead>
              <tr>
                <th>{translate(locale, "backtests.horizon2")}</th>
                <th>{translate(locale, "backtests.modelCoverage")}</th>
                <th>{translate(locale, "backtests.optionsCoverage")}</th>
                <th>{translate(locale, "backtests.pairedSamples")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.horizon_days}>
                  <td>
                    {r.horizon_days}
                    {" " + translate(locale, "backtests.days")}
                  </td>
                  <td>{optionPercent(r.model_coverage, undefined, locale)}</td>
                  <td>{optionPercent(r.implied_coverage, undefined, locale)}</td>
                  <td>
                    {r.model_samples} / {r.implied_samples}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </AccessibleEChart>
      ) : (
        <p className="option-empty">
          {translate(locale, "backtests.noMatureMatchedSampleMeetsTheRequiredCount")}
        </p>
      )}
      {coverage
        .filter((r) => !rows.includes(r))
        .map((r) => (
          <p className="option-risk-neutral" key={r.horizon_days}>
            {r.horizon_days}
            {" " + (translate(locale, "backtests.dayMatureModelSamples") + " ")}
            {r.model_samples}
            {translate(locale, "backtests.impliedSamples")} {r.implied_samples}
            {translate(locale, "backtests.required") + " "}
            {r.minimum_samples}
            {" " + (translate(locale, "backtests.samples2") + " ")}
          </p>
        ))}
      <ChartExplainer kind="coverage" />
    </section>
  );
}
