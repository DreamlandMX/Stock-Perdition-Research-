import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo } from "react";
import type { EChartsOption } from "echarts";

import type { components } from "../../api/generated/schema";
import {
  buildHistogramBins,
  formatReturn,
  type MarketPoint,
  type MarketReturnHorizon,
  symbolLabel,
} from "./overview-terminal";
import { OverviewTerminalChart } from "./OverviewTerminalChart";

type InternalEvidence = components["schemas"]["OverviewInternalEvidence"];

const REGIMES = ["uptrend", "range", "transition", "downtrend", "invalid"] as const;
const getRegimeLabels = (locale: Locale = "zh-CN") => [
  translate(locale, "overview.SectorMarketMap.uptrend"),
  translate(locale, "overview.RegimeDistribution.range"),
  translate(locale, "overview.RegimeDistribution.directionUnconfirmed"),
  translate(locale, "overview.RegimeDistribution.downtrend"),
  translate(locale, "overview.RegimeDistribution.regimeUndetermined"),
];
const AXIS = {
  axisLabel: { color: "#8fa6b8", fontSize: 11 },
  axisLine: { lineStyle: { color: "#2a3947" } },
  splitLine: { lineStyle: { color: "#1d2a35" } },
};

interface MarketStructureTerminalProps {
  evidence: InternalEvidence;
  horizon: MarketReturnHorizon;
  points: readonly MarketPoint[];
}

export function MarketStructureTerminal({
  evidence,
  horizon,
  points,
}: MarketStructureTerminalProps) {
  const { locale } = useI18n();
  const bins = useMemo(() => buildHistogramBins(points, horizon), [horizon, points]);
  const histogramOption = useMemo<EChartsOption>(
    () => ({
      grid: { bottom: 48, left: 46, right: 14, top: 16 },
      series: [
        {
          data: bins.map((bin) => bin.count),
          itemStyle: { color: "#4a9fea" },
          type: "bar",
        },
      ],
      tooltip: { trigger: "axis" },
      xAxis: {
        ...AXIS,
        axisLabel: { ...AXIS.axisLabel, hideOverlap: true, interval: 0, rotate: 40 },
        data: bins.map(
          (bin) => `${(bin.lower * 100).toFixed(0)}~${(bin.upper * 100).toFixed(0)}`,
        ),
        type: "category",
      },
      yAxis: { ...AXIS, minInterval: 1, splitNumber: 4, type: "value" },
    }),
    [bins],
  );
  const transitionOption = useMemo<EChartsOption>(
    () => ({
      grid: { bottom: 74, left: 80, right: 14, top: 16 },
      series: [
        {
          data: evidence.regime_transition_cells.map((cell) => [
            REGIMES.indexOf(cell.current_regime),
            REGIMES.indexOf(cell.previous_regime),
            cell.count,
          ]),
          label: {
            color: "#e6edf3",
            fontSize: 11,
            formatter: ({ value }: { value?: unknown }) =>
              Array.isArray(value) ? String(value[2]) : "",
            show: true,
          },
          type: "heatmap",
        },
      ],
      tooltip: { position: "top" },
      visualMap: {
        calculable: false,
        inRange: { color: ["#17232d", "#176b59", "#23c69a"] },
        max: Math.max(1, ...evidence.regime_transition_cells.map((cell) => cell.count)),
        min: 0,
        show: false,
      },
      xAxis: {
        ...AXIS,
        axisLabel: { ...AXIS.axisLabel, interval: 0, rotate: 32 },
        data: getRegimeLabels(locale),
        type: "category",
      },
      yAxis: {
        ...AXIS,
        axisLabel: { ...AXIS.axisLabel, interval: 0 },
        data: getRegimeLabels(locale),
        type: "category",
      },
    }),
    [evidence.regime_transition_cells, locale],
  );
  const timelineOption = useMemo<EChartsOption>(
    () => ({
      grid: { bottom: 40, left: 56, right: 20, top: 48 },
      legend: {
        data: [
          translate(locale, "overview.MarketStructureTerminal.20DayPositiveReturns"),
          translate(locale, "overview.SectorMarketMap.uptrend"),
          translate(locale, "overview.OverviewPulse.returnDispersion"),
        ],
        itemGap: 10,
        itemHeight: 6,
        itemWidth: 10,
        textStyle: { color: "#8fa6b8", fontSize: 10 },
        top: 2,
      },
      series: [
        {
          data: evidence.history.map((point) => point.market_breadth * 100),
          name: translate(
            locale,
            "overview.MarketStructureTerminal.20DayPositiveReturns",
          ),
          type: "line",
        },
        {
          data: evidence.history.map((point) => point.uptrend_fraction * 100),
          name: translate(locale, "overview.SectorMarketMap.uptrend"),
          type: "line",
        },
        {
          data: evidence.history.map((point) =>
            point.return_dispersion == null ? null : point.return_dispersion * 100,
          ),
          name: translate(locale, "overview.OverviewPulse.returnDispersion"),
          type: "line",
        },
      ],
      tooltip: { trigger: "axis" },
      xAxis: {
        ...AXIS,
        data: evidence.history.map((point) => point.as_of.slice(5, 10)),
        type: "category",
      },
      yAxis: {
        ...AXIS,
        axisLabel: {
          color: "#8fa6b8",
          formatter: "{value}%",
          fontSize: 11,
          hideOverlap: true,
        },
        splitNumber: 4,
        type: "value",
      },
    }),
    [evidence.history, locale],
  );
  const scatterPoints = points.filter(
    (point) => point.return_20 != null && point.volume_z_20 != null,
  );
  const scatterOption = useMemo<EChartsOption>(
    () => ({
      grid: { bottom: 54, left: 60, right: 20, top: 34 },
      series: [
        {
          data: scatterPoints.map((point) => ({
            label: {
              formatter: symbolLabel(point.symbol),
              show: Boolean(
                point.breakout_up_rule ||
                point.breakout_down_rule ||
                point.regime_changed ||
                (point.volume_z_20 != null && Math.abs(point.volume_z_20) >= 2),
              ),
            },
            name: point.symbol,
            value: [(point.return_20 ?? 0) * 100, point.volume_z_20 ?? 0],
          })),
          itemStyle: { color: "#4a9fea" },
          symbolSize: 7,
          type: "scatter",
        },
      ],
      tooltip: { trigger: "item" },
      xAxis: {
        ...AXIS,
        axisLabel: {
          color: "#8fa6b8",
          formatter: "{value}%",
          fontSize: 11,
          hideOverlap: true,
        },
        name: translate(locale, "overview.MarketStructureTerminal.20DayReturn"),
        nameGap: 30,
        nameTextStyle: { color: "#8fa6b8", fontSize: 11 },
        splitNumber: 4,
        type: "value",
      },
      yAxis: {
        ...AXIS,
        axisLabel: { ...AXIS.axisLabel, hideOverlap: true },
        name: translate(locale, "overview.OpportunityTable.volumeZ"),
        nameGap: 14,
        nameTextStyle: { color: "#8fa6b8", fontSize: 11 },
        splitNumber: 4,
        type: "value",
      },
    }),
    [scatterPoints, locale],
  );

  return (
    <section
      aria-label={translate(
        locale,
        "overview.MarketStructureTerminal.fourMarketStructureCharts",
      )}
      className="overview-terminal market-structure-terminal"
    >
      <header className="overview-terminal__header">
        <div>
          <span>
            {translate(locale, "overview.MarketStructureTerminal.structureEvidence")}
          </span>
          <h2>
            {translate(
              locale,
              "overview.MarketStructureTerminal.fourMarketStructureCharts",
            )}
          </h2>
          <p>
            {translate(
              locale,
              "overview.MarketStructureTerminal.distributionsTransitionsTimeAndAnomalyPositionsUseTheSame",
            )}
          </p>
        </div>
      </header>
      <div
        aria-label={translate(
          locale,
          "overview.MarketStructureTerminal.marketStructureChartGrid",
        )}
        className="market-structure-grid"
        data-responsive-layout="desktop-2x2 narrow-1-column"
        role="group"
      >
        <OverviewTerminalChart
          option={histogramOption}
          summary={translate(
            locale,
            "overview.MarketStructureTerminal.instrumentsAcross10FixedEqualWidthBins",
            { count: points.length },
          )}
          tableLabel={translate(
            locale,
            "overview.MarketStructureTerminal.viewReturnDistributionData",
          )}
          title={translate(
            locale,
            "overview.MarketStructureTerminal.dayReturnDistribution",
            { horizon: horizon },
          )}
        >
          <table
            aria-label={translate(
              locale,
              "overview.MarketStructureTerminal.dayReturnDistributionData",
              { horizon: horizon },
            )}
          >
            <thead>
              <tr>
                <th>
                  {translate(locale, "overview.MarketStructureTerminal.interval")}
                </th>
                <th>{translate(locale, "overview.MarketStructureTerminal.count")}</th>
              </tr>
            </thead>
            <tbody>
              {bins.map((bin) => (
                <tr key={bin.lower}>
                  <td>
                    {formatReturn(bin.lower, locale)}–{formatReturn(bin.upper, locale)}
                  </td>
                  <td>{bin.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </OverviewTerminalChart>
        <OverviewTerminalChart
          option={transitionOption}
          summary={
            evidence.comparison_snapshot
              ? translate(locale, "overview.MarketStructureTerminal.compare", {
                  snapshotId: evidence.comparison_snapshot.snapshot_id,
                })
              : translate(locale, "overview.SectorMarketMap.insufficientHistory")
          }
          tableLabel={translate(
            locale,
            "overview.MarketStructureTerminal.viewRegimeTransitionData",
          )}
          title={translate(
            locale,
            "overview.MarketStructureTerminal.regimeTransitions",
          )}
        >
          <table
            aria-label={translate(
              locale,
              "overview.MarketStructureTerminal.regimeTransitionData",
            )}
          >
            <thead>
              <tr>
                <th>{translate(locale, "overview.OverviewHistoryPanels.previous")}</th>
                <th>{translate(locale, "overview.MarketStructureTerminal.current")}</th>
                <th>{translate(locale, "overview.MarketStructureTerminal.count")}</th>
              </tr>
            </thead>
            <tbody>
              {evidence.regime_transition_cells.map((cell) => (
                <tr key={`${cell.previous_regime}-${cell.current_regime}`}>
                  <td>{cell.previous_regime}</td>
                  <td>{cell.current_regime}</td>
                  <td>{cell.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </OverviewTerminalChart>
        <OverviewTerminalChart
          option={timelineOption}
          summary={translate(
            locale,
            "overview.MarketStructureTerminal.5CompatibleSnapshotsMissingValuesAreNotFilledWith",
            { count: evidence.history.length },
          )}
          tableLabel={translate(
            locale,
            "overview.MarketStructureTerminal.viewFiveSnapshotData",
          )}
          title={translate(
            locale,
            "overview.MarketStructureTerminal.fiveSnapshotTimeline",
          )}
        >
          <table
            aria-label={translate(
              locale,
              "overview.MarketStructureTerminal.fiveSnapshotData",
            )}
          >
            <thead>
              <tr>
                <th>{translate(locale, "overview.MarketStructureTerminal.date")}</th>
                <th>{translate(locale, "overview.MarketStructureTerminal.breadth")}</th>
                <th>{translate(locale, "options.optionsBoardRows.bullish")}</th>
                <th>
                  {translate(locale, "overview.MarketStructureTerminal.dispersion")}
                </th>
              </tr>
            </thead>
            <tbody>
              {evidence.history.map((point) => (
                <tr key={point.snapshot_id}>
                  <td>{point.as_of.slice(0, 10)}</td>
                  <td>{formatReturn(point.market_breadth, locale)}</td>
                  <td>{formatReturn(point.uptrend_fraction, locale)}</td>
                  <td>{formatReturn(point.return_dispersion ?? null, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </OverviewTerminalChart>
        <OverviewTerminalChart
          option={scatterOption}
          summary={translate(
            locale,
            "overview.MarketStructureTerminal.onlyBreakoutsTransitionsOrInstrumentsWithZ2Are",
          )}
          tableLabel={translate(
            locale,
            "overview.MarketStructureTerminal.viewReturnAndVolumeData",
          )}
          title={translate(
            locale,
            "overview.MarketStructureTerminal.20DayReturnVolumeZ",
          )}
        >
          <table
            aria-label={translate(
              locale,
              "overview.MarketStructureTerminal.returnAndVolumeData",
            )}
          >
            <thead>
              <tr>
                <th>{translate(locale, "overview.OverviewPage.instrument")}</th>
                <th>
                  {translate(locale, "overview.MarketStructureTerminal.20DayReturn")}
                </th>
                <th>{translate(locale, "overview.MarketStructureTerminal.volumeZ")}</th>
              </tr>
            </thead>
            <tbody>
              {scatterPoints.map((point) => (
                <tr key={point.symbol}>
                  <td>{symbolLabel(point.symbol)}</td>
                  <td>{formatReturn(point.return_20 ?? null, locale)}</td>
                  <td>{point.volume_z_20?.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </OverviewTerminalChart>
      </div>
    </section>
  );
}
