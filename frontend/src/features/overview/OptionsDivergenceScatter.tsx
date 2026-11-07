import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { EChartsOption } from "echarts";
import { ChartExplainer } from "../options/ChartExplainer";
import { optionPercent, optionNumber } from "../options/option-labels";
import type { OptionOverviewItem } from "../options/useOptionsOverview";
import { boardColors } from "../options/options-board-rows";
import { OverviewTerminalChart } from "./OverviewTerminalChart";
import { buildMarketResearchHref, type MarketLinkState } from "./overview-terminal";

export function OptionsDivergenceScatter({
  items,
  horizon,
  onHorizonChange,
  linkState,
}: {
  items: OptionOverviewItem[];
  horizon: 1 | 5 | 20;
  onHorizonChange: (h: 1 | 5 | 20) => void;
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const [view, setView] = useState<"market" | "model">("market");
  const navigate = useNavigate();
  const rows = useMemo(
    () =>
      items.flatMap((item) => {
        const m = item.model_horizons.find((h) => h.horizon_days === horizon),
          i = item.implied_moves.find((h) => h.horizon_days === horizon);
        return m?.q10 != null &&
          m.q90 != null &&
          m.width_ratio != null &&
          (m.status === "enhanced" || m.status === "baseline_fallback") &&
          i?.upper_pct != null &&
          i.lower_pct != null
          ? [
              {
                symbol: item.symbol,
                model: m.q90 - m.q10,
                implied: i.upper_pct - i.lower_pct,
                ratio: m.width_ratio,
              },
            ]
          : [];
      }),
    [items, horizon],
  );
  const click = useCallback(
    (index: number) => {
      const row = rows[index];
      if (row)
        void navigate(`${buildMarketResearchHref(row.symbol, linkState)}#options`);
    },
    [rows, linkState, navigate],
  );
  const option = useMemo<EChartsOption>(() => {
    const maximum =
      Math.max(1, ...rows.flatMap((r) => [r.model * 100, r.implied * 100])) * 1.15;
    return {
      animation: false,
      grid: { left: 65, right: 24, top: 34, bottom: 52 },
      tooltip: {
        trigger: "item",
        formatter: (event: unknown) => {
          if (
            typeof event !== "object" ||
            !event ||
            !("dataIndex" in event) ||
            typeof event.dataIndex !== "number"
          )
            return "";
          const r = rows[event.dataIndex];
          return r
            ? translate(
                locale,
                "overview.OptionsDivergenceScatter.modelWidthImpliedWidthModelImplied",
                {
                  symbol: r.symbol,
                  percentage: optionPercent(r.model, undefined, locale),
                  percentage2: optionPercent(r.implied, undefined, locale),
                  number: optionNumber(r.ratio, locale),
                },
              )
            : "";
        },
      },
      xAxis: {
        type: "value",
        min: 0,
        max: maximum,
        name: translate(locale, "overview.OptionsDivergenceScatter.model80Width"),
        nameLocation: "middle",
        nameGap: 32,
        splitLine: { lineStyle: { color: "#303039" } },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: maximum,
        name: translate(locale, "overview.OptionsDivergenceScatter.options80Width"),
        splitLine: { lineStyle: { color: "#303039" } },
      },
      series: [
        {
          type: "scatter",
          symbolSize: 12,
          data: rows.map((r) => ({
            value: [r.model * 100, r.implied * 100],
            name: r.symbol.replace(/^US\./, ""),
          })),
          itemStyle: { color: "#9a8fe7" },
          label: {
            show: true,
            formatter: "{b}",
            position: "top",
            color: "#cec5fb",
            fontSize: 10,
          },
          markLine: {
            symbol: "none",
            silent: true,
            lineStyle: { color: "#777587", type: "dashed" },
            label: {
              formatter: translate(
                locale,
                "overview.OptionsDivergenceScatter.equalWidth",
              ),
            },
            data: [[{ coord: [0, 0] }, { coord: [maximum, maximum] }]],
          },
        },
      ],
    };
  }, [rows, locale]);
  return (
    <div>
      <div
        className="option-horizon-switch"
        aria-label={translate(
          locale,
          "overview.OptionsDivergenceScatter.optionsScatterView",
        )}
      >
        <button
          type="button"
          aria-pressed={view === "market"}
          onClick={() => setView("market")}
        >
          {translate(locale, "overview.OptionsDivergenceScatter.fearVsProtection")}
        </button>
        <button
          type="button"
          aria-pressed={view === "model"}
          onClick={() => setView("model")}
        >
          {translate(
            locale,
            "overview.OptionsDivergenceScatter.modelIntervalDivergence",
          )}
        </button>
      </div>
      {view === "market" ? (
        <MarketTemperatureScatter items={items} linkState={linkState} />
      ) : (
        <>
          <div
            className="option-horizon-switch"
            aria-label={translate(
              locale,
              "overview.OptionsDivergenceScatter.optionsDivergenceHorizon",
            )}
          >
            {([1, 5, 20] as const).map((h) => (
              <button
                type="button"
                key={h}
                aria-pressed={horizon === h}
                onClick={() => onHorizonChange(h)}
              >
                {h} {translate(locale, "overview.SectorHealthTerminal.days")}
              </button>
            ))}
          </div>
          {rows.length ? (
            <OverviewTerminalChart
              title={translate(
                locale,
                "overview.OptionsDivergenceScatter.modelVsMarketIntervalWidthDivergence",
              )}
              summary={translate(
                locale,
                "overview.OptionsDivergenceScatter.aboveOptionsPriceGreaterVolatilityBelowModelIntervalsAre",
              )}
              option={option}
              onCellSelect={click}
              tableLabel={translate(
                locale,
                "overview.OptionsDivergenceScatter.viewIntervalWidthsAndInstruments",
              )}
            >
              <table>
                <thead>
                  <tr>
                    <th>{translate(locale, "overview.OverviewPage.instrument")}</th>
                    <th>
                      {translate(
                        locale,
                        "overview.OptionsDivergenceScatter.modelWidth",
                      )}
                    </th>
                    <th>
                      {translate(
                        locale,
                        "overview.OptionsDivergenceScatter.impliedWidth",
                      )}
                    </th>
                    <th>
                      {translate(locale, "overview.OptionsPulseTable.widthRatio")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.symbol}>
                      <td>
                        <Link
                          to={`${buildMarketResearchHref(r.symbol, linkState)}#options`}
                        >
                          {r.symbol}
                        </Link>
                      </td>
                      <td>{optionPercent(r.model, undefined, locale)}</td>
                      <td>{optionPercent(r.implied, undefined, locale)}</td>
                      <td>{optionNumber(r.ratio, locale)}</td>
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
                  "overview.OptionsDivergenceScatter.modelVsMarketIntervalWidthDivergence",
                )}
              </h3>
              <p>
                {translate(
                  locale,
                  "overview.OptionsDivergenceScatter.requiresModelQuantilesAndOptionsSnapshotsCoveringTheSame",
                )}
              </p>
            </div>
          )}
          <ChartExplainer kind="divergence" />
        </>
      )}
    </div>
  );
}

function MarketTemperatureScatter({
  items,
  linkState,
}: {
  items: OptionOverviewItem[];
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const navigate = useNavigate();
  const rows = useMemo(
    () =>
      items.flatMap((item) => {
        const iv = item.analytics?.values.atm_iv_30d;
        const ratio = item.analytics?.values.put_call_volume;
        return iv != null && ratio != null ? [{ symbol: item.symbol, iv, ratio }] : [];
      }),
    [items],
  );
  const select = useCallback(
    (index: number) => {
      const row = rows[index];
      if (row)
        void navigate(`${buildMarketResearchHref(row.symbol, linkState)}#options`);
    },
    [rows, navigate, linkState],
  );
  const option = useMemo<EChartsOption>(
    () => ({
      grid: { left: 55, right: 30, top: 45, bottom: 50 },
      tooltip: { trigger: "item" },
      xAxis: {
        type: "value",
        name: translate(locale, "overview.OptionsDivergenceScatter.aTMIVAnnualized"),
        nameLocation: "middle",
        nameGap: 30,
        min: 0,
      },
      yAxis: {
        type: "value",
        name: translate(locale, "overview.OptionsDivergenceScatter.putCallVolumeRatio"),
        min: 0,
      },
      series: [
        {
          type: "scatter",
          symbolSize: 14,
          data: rows.map((row) => ({
            name: row.symbol.replace(/^US\./, ""),
            value: [row.iv * 100, row.ratio],
          })),
          label: {
            show: true,
            formatter: "{b}",
            position: "top",
            fontSize: 12,
            color: boardColors().secondary,
            textBorderWidth: 0,
          },
          labelLayout: { hideOverlap: true, moveOverlap: "shiftY" },
          itemStyle: { color: boardColors().cone },
          markLine: {
            silent: true,
            symbol: "none",
            label: {
              formatter: "Put/Call = 1",
              position: "insideEndTop",
              fontSize: 12,
            },
            data: [{ yAxis: 1 }],
          },
        },
      ],
    }),
    [rows, locale],
  );
  return (
    <OverviewTerminalChart
      title={translate(locale, "overview.OptionsDivergenceScatter.fearVsProtection")}
      summary={translate(
        locale,
        "overview.OptionsDivergenceScatter.xATMIVYPutCallVolumeRatioHigher",
      )}
      tableLabel={translate(
        locale,
        "overview.OptionsDivergenceScatter.viewIVAndPutCallValues",
      )}
      option={option}
      onCellSelect={select}
    >
      <table>
        <thead>
          <tr>
            <th>{translate(locale, "overview.OverviewPage.instrument")}</th>
            <th>ATM IV</th>
            <th>
              {translate(
                locale,
                "overview.OptionsDivergenceScatter.putCallVolumeRatio",
              )}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.symbol}>
              <td>
                <Link to={`${buildMarketResearchHref(row.symbol, linkState)}#options`}>
                  {row.symbol.replace(/^US\./, "")}
                </Link>
              </td>
              <td>{optionPercent(row.iv, undefined, locale)}</td>
              <td>{optionNumber(row.ratio, locale)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && (
        <p>
          {translate(
            locale,
            "overview.OptionsDivergenceScatter.noSnapshotsHaveBothValidIVAndVolumeRatios",
          )}
        </p>
      )}
    </OverviewTerminalChart>
  );
}
