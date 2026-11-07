import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import type { OptionOverviewItem } from "../options/useOptionsOverview";
import { ChartExplainer } from "../options/ChartExplainer";
import { optionPercent } from "../options/option-labels";
import { formatUsMarketTime } from "../../app/presentation";
import { OverviewTerminalChart } from "./OverviewTerminalChart";

export function OptionsImpliedMoveChart({ items }: { items: OptionOverviewItem[] }) {
  const { locale } = useI18n();
  const rows = useMemo(
    () =>
      items.flatMap((item) =>
        item.daily_pair
          ? [{ symbol: item.symbol.replace(/^US\./, ""), ...item.daily_pair }]
          : [],
      ),
    [items],
  );
  const option = useMemo<EChartsOption>(
    () => ({
      animation: false,
      grid: { left: 55, right: 30, top: 32, bottom: 38 },
      legend: { top: 0, textStyle: { color: "#b8b6c8" } },
      tooltip: {
        trigger: "axis",
        formatter: (events: unknown) => {
          const entry: unknown = Array.isArray(events) ? events[0] : undefined;
          const row =
            typeof entry === "object" &&
            entry !== null &&
            "dataIndex" in entry &&
            typeof entry.dataIndex === "number"
              ? rows[entry.dataIndex]
              : undefined;
          if (!row) return "";
          return translate(
            locale,
            "overview.OptionsImpliedMoveChart.previousSession1RealizedReturnSnapshot",
            {
              symbol: row.symbol,
              session: row.session,
              percentage: optionPercent(row.sigma_pct, undefined, locale),
              percentage2: optionPercent(row.realized_return, undefined, locale),
              value:
                Math.abs(row.realized_return) > row.sigma_pct
                  ? translate(
                      locale,
                      "overview.OptionsImpliedMoveChart.outsideTheOptionsImplied1DayInterval",
                    )
                  : "",
              time: formatUsMarketTime(row.previous_market_as_of, locale),
            },
          );
        },
      },
      xAxis: {
        type: "value",
        name: "%",
        splitLine: { lineStyle: { color: "#303039" } },
      },
      yAxis: { type: "category", data: rows.map((r) => r.symbol), inverse: true },
      series: [
        {
          name: translate(locale, "overview.OptionsImpliedMoveChart.implied1About68"),
          type: "bar",
          barWidth: 16,
          barGap: "-100%",
          stack: "range",
          itemStyle: { color: "#50505e" },
          data: rows.map((r) => -r.sigma_pct * 100),
        },
        {
          name: translate(locale, "overview.OptionsImpliedMoveChart.implied1About68"),
          type: "bar",
          barWidth: 16,
          stack: "range",
          itemStyle: { color: "#50505e" },
          data: rows.map((r) => r.sigma_pct * 100),
        },
        {
          name: translate(
            locale,
            "overview.OptionsImpliedMoveChart.realized1DayReturn",
          ),
          type: "bar",
          barWidth: 8,
          barGap: "-75%",
          data: rows.map((r) => ({
            value: r.realized_return * 100,
            itemStyle: {
              color: r.realized_return >= 0 ? "#3ecf8e" : "#f2647a",
              borderColor: "#e4d9ff",
              borderWidth: Math.abs(r.realized_return) > r.sigma_pct ? 2 : 0,
            },
          })),
        },
      ],
    }),
    [rows, locale],
  );
  return (
    <div>
      {rows.length ? (
        <OverviewTerminalChart
          title={translate(
            locale,
            "overview.OptionsImpliedMoveChart.1DayRealizedMoveVsImpliedInterval",
          )}
          summary={translate(
            locale,
            "overview.OptionsImpliedMoveChart.previousSessionPreCloseSnapshotsPairedWithTheNext",
          )}
          option={option}
          tableLabel={translate(
            locale,
            "overview.OptionsImpliedMoveChart.viewPairedValuesAndSources",
          )}
        >
          <table>
            <thead>
              <tr>
                <th>{translate(locale, "overview.OverviewPage.instrument")}</th>
                <th>
                  {translate(locale, "overview.OptionsImpliedMoveChart.returnDate")}
                </th>
                <th>
                  {translate(locale, "overview.OptionsImpliedMoveChart.implied1")}
                </th>
                <th>
                  {translate(locale, "overview.OptionsImpliedMoveChart.realizedReturn")}
                </th>
                <th>
                  {translate(locale, "overview.OptionsImpliedMoveChart.sourceSnapshot")}
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol}>
                  <td>{r.symbol}</td>
                  <td>{r.session}</td>
                  <td>±{optionPercent(r.sigma_pct, undefined, locale)}</td>
                  <td>{optionPercent(r.realized_return, undefined, locale)}</td>
                  <td>{formatUsMarketTime(r.previous_market_as_of, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </OverviewTerminalChart>
      ) : (
        <div className="option-empty">
          <h3>
            {translate(
              locale,
              "overview.OptionsImpliedMoveChart.1DayRealizedMoveVsImpliedInterval",
            )}
          </h3>
          <p>
            {translate(
              locale,
              "overview.OptionsImpliedMoveChart.requiresAPreviousSessionPreCloseSnapshotAndThe",
            )}
          </p>
        </div>
      )}
      <ChartExplainer kind="implied" />
    </div>
  );
}
