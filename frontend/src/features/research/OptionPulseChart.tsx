import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo } from "react";
import { OverviewTerminalChart } from "../overview/OverviewTerminalChart";
import { money, number } from "../options/options-board-rows";
import { formatUsMarketTime } from "../../app/presentation";
import { buildOptionPulseOption, type PulseChartProps } from "./option-pulse-chart";
export function OptionPulseChart(props: PulseChartProps) {
  const { locale } = useI18n();
  const option = useMemo(() => buildOptionPulseOption(props, locale), [props, locale]);
  return (
    <OverviewTerminalChart
      title={translate(
        locale,
        "research.optionpulsechart.candlesticksImpliedRangesAndTurnoverByStrike",
      )}
      summary={
        props.timeframe === "5m"
          ? translate(
              locale,
              "research.optionpulsechart.5MinuteCandlesPurple6880Iv30EstimatesScaled",
            )
          : translate(
              locale,
              "research.optionpulsechart.last30TradingDayCandles6880ImpliedScenarios",
            )
      }
      option={option}
      tableLabel={translate(
        locale,
        "research.optionpulsechart.viewCandlesTurnoverAndRangeData",
      )}
    >
      <table>
        <thead>
          <tr>
            <th>{translate(locale, "research.optionpulsechart.et")}</th>
            <th>{translate(locale, "research.optionpulsechart.open")}</th>
            <th>{translate(locale, "research.optionpulsechart.high")}</th>
            <th>{translate(locale, "research.optionpulsechart.low")}</th>
            <th>{translate(locale, "research.optionpulsechart.close")}</th>
            <th>{translate(locale, "research.optionpulsechart.status")}</th>
          </tr>
        </thead>
        <tbody>
          {props.bars.map((bar) => (
            <tr key={bar.at}>
              <td>
                {props.timeframe === "1d"
                  ? bar.at.slice(0, 10)
                  : formatUsMarketTime(bar.at, locale)}
              </td>
              <td>{number(bar.open, undefined, locale)}</td>
              <td>{number(bar.high, undefined, locale)}</td>
              <td>{number(bar.low, undefined, locale)}</td>
              <td>{number(bar.close, undefined, locale)}</td>
              <td>
                {bar.data_gap
                  ? translate(locale, "research.optionpulsechart.gap")
                  : bar.forming
                    ? translate(locale, "research.optionpulsechart.forming")
                    : translate(locale, "research.optionpulsechart.complete")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <table>
        <thead>
          <tr>
            <th>{translate(locale, "research.optionpulsechart.strikeUsd")}</th>
            <th>{translate(locale, "research.optionpulsechart.callTurnover")}</th>
            <th>{translate(locale, "research.optionpulsechart.putTurnover")}</th>
          </tr>
        </thead>
        <tbody>
          {props.profile.map((p) => (
            <tr key={p.strike}>
              <td>{number(p.strike, undefined, locale)}</td>
              <td>
                {p.call == null
                  ? translate(locale, "research.optionanalyticspanel.missing")
                  : money(p.call, locale)}
              </td>
              <td>
                {p.put == null
                  ? translate(locale, "research.optionanalyticspanel.missing")
                  : money(p.put, locale)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <table>
        <thead>
          <tr>
            <th>{translate(locale, "research.optionpulsechart.scenarioTime")}</th>
            <th>{translate(locale, "research.optionpulsechart.68Interval")}</th>
            <th>{translate(locale, "research.optionpulsechart.80Interval")}</th>
          </tr>
        </thead>
        <tbody>
          {props.cone.map((p, i) => (
            <tr key={i}>
              <td>
                {props.timeframe === "1d"
                  ? p.horizon_days == null
                    ? translate(locale, "research.optionPulseChart.referencePrice")
                    : translate(locale, "research.optionpulsechart.tTradingDays", {
                        horizonDays: p.horizon_days,
                      })
                  : formatUsMarketTime(p.at, locale)}
              </td>
              <td>
                {number(p.lower68, undefined, locale)} –{" "}
                {number(p.upper68, undefined, locale)}
              </td>
              <td>
                {number(p.lower80, undefined, locale)} –{" "}
                {number(p.upper80, undefined, locale)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </OverviewTerminalChart>
  );
}
