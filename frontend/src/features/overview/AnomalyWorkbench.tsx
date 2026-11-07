import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import { Link } from "react-router-dom";

import type { components } from "../../api/generated/schema";
import type { ResearchMode } from "../../components/ResearchModeSwitch";
import { OverviewTerminalChart } from "./OverviewTerminalChart";
import {
  anomalyScore,
  atrTopDecile,
  buildMarketResearchHref,
  symbolLabel,
  type MarketLinkState,
  type MarketPoint,
} from "./overview-terminal";

type InternalEvidence = components["schemas"]["OverviewInternalEvidence"];
type CandidateChurn = components["schemas"]["OverviewCandidateChurn"];

interface AnomalyWorkbenchProps {
  evidence: InternalEvidence;
  linkState: MarketLinkState;
  mode: ResearchMode;
  points: readonly MarketPoint[];
}

function percentCount(count: number | null, total: number) {
  return count === null || total === 0 ? null : (count / total) * 100;
}

function Churn({
  churn,
  linkState,
  title,
}: {
  churn: CandidateChurn | null;
  linkState: MarketLinkState;
  title: string;
}) {
  const { locale } = useI18n();
  if (churn === null) {
    return (
      <section>
        <h3>{title}</h3>
        <p>
          {translate(
            locale,
            "overview.AnomalyWorkbench.insufficientHistoryToCompareCandidateChanges",
          )}
        </p>
      </section>
    );
  }
  return (
    <section>
      <h3>{title}</h3>
      {churn.changes.length === 0 ? (
        <p>
          {translate(locale, "overview.AnomalyWorkbench.theCandidateSetIsUnchanged")}
        </p>
      ) : (
        <ul className="candidate-churn-list">
          {churn.changes.slice(0, 10).map((change) => {
            const rankChange = change.rank_change ?? 0;
            return (
              <li key={change.symbol}>
                <Link to={buildMarketResearchHref(change.symbol, linkState)}>
                  {symbolLabel(change.symbol)}
                </Link>
                <span>
                  {change.status === "entered"
                    ? translate(locale, "overview.OverviewHistoryPanels.newEntries")
                    : change.status === "exited"
                      ? translate(locale, "overview.AnomalyWorkbench.exited")
                      : rankChange === 0
                        ? translate(locale, "overview.AnomalyWorkbench.rankUnchanged")
                        : translate(locale, "overview.AnomalyWorkbench.rank", {
                            value: rankChange > 0 ? "↑" : "↓",
                            value2: Math.abs(rankChange),
                          })}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function AnomalyWorkbench({
  evidence,
  linkState,
  mode,
  points,
}: AnomalyWorkbenchProps) {
  const { locale } = useI18n();
  const atrCutoff = atrTopDecile(points);
  const ranked = [...points].sort(
    (left, right) =>
      anomalyScore(right, atrCutoff) - anomalyScore(left, atrCutoff) ||
      Math.abs(right.volume_z_20 ?? 0) - Math.abs(left.volume_z_20 ?? 0),
  );
  const intersections = new Map<string, number>();
  for (const point of points) {
    const conditions = [
      point.breakout_up_rule || point.breakout_down_rule
        ? translate(locale, "overview.AnomalyWorkbench.breakout")
        : null,
      point.volume_z_20 != null && Math.abs(point.volume_z_20) >= 2
        ? translate(locale, "overview.OverviewHistoryPanels.volume")
        : null,
      point.regime_changed
        ? translate(locale, "overview.AnomalyWorkbench.transition")
        : null,
      point.channel_position_20 != null &&
      (point.channel_position_20 >= 1 || point.channel_position_20 <= 0)
        ? translate(locale, "overview.AnomalyWorkbench.channelExtreme")
        : null,
      atrCutoff !== null && point.atr_pct_20 != null && point.atr_pct_20 >= atrCutoff
        ? translate(locale, "overview.AnomalyWorkbench.highVolatility")
        : null,
    ].filter((value): value is string => value !== null);
    if (conditions.length > 1) {
      const key = conditions.join(" + ");
      intersections.set(key, (intersections.get(key) ?? 0) + 1);
    }
  }
  const current = evidence.current_anomaly_snapshot ?? null;
  const previous = evidence.previous_anomaly_snapshot ?? null;
  const indicators = [
    translate(locale, "overview.OverviewHistoryPanels.unusualVolume"),
    translate(locale, "overview.AnomalyWorkbench.upperChannel"),
    translate(locale, "overview.AnomalyWorkbench.lowerChannel"),
    translate(locale, "overview.AnomalyWorkbench.breakout"),
    translate(locale, "overview.SectorMarketMap.regimeChange"),
  ];
  const currentValues =
    current === null
      ? []
      : [
          current.volume_anomalies,
          current.upper_channel_count,
          current.lower_channel_count,
          current.breakout_count,
          current.regime_change_count,
        ].map((count) => percentCount(count ?? null, current.total_symbols));
  const previousValues =
    previous === null
      ? []
      : [
          previous.volume_anomalies,
          previous.upper_channel_count,
          previous.lower_channel_count,
          previous.breakout_count,
          previous.regime_change_count,
        ].map((count) => percentCount(count ?? null, previous.total_symbols));
  const radarOption = useMemo<EChartsOption>(
    () => ({
      legend: {
        bottom: 0,
        data:
          previous === null
            ? [translate(locale, "overview.MarketStructureTerminal.current")]
            : [
                translate(locale, "overview.MarketStructureTerminal.current"),
                translate(
                  locale,
                  "overview.OverviewHistoryPanels.previousCompatibleSnapshot",
                ),
              ],
        itemGap: 14,
        itemHeight: 8,
        itemWidth: 16,
        textStyle: { color: "#8fa6b8", fontSize: 11 },
        type: "scroll",
      },
      radar: {
        axisName: { color: "#a9bac8", fontSize: 11 },
        center: ["50%", "45%"],
        indicator: indicators.map((name) => ({ max: 100, name })),
        radius: "56%",
        splitArea: { areaStyle: { color: ["#111b24", "#15232d"] } },
        splitLine: { lineStyle: { color: "#2a3947" } },
      },
      series: [
        {
          data: [
            {
              name: translate(locale, "overview.MarketStructureTerminal.current"),
              value: currentValues.map((value) => value ?? 0),
            },
            ...(previous === null
              ? []
              : [
                  {
                    name: translate(
                      locale,
                      "overview.OverviewHistoryPanels.previousCompatibleSnapshot",
                    ),
                    value: previousValues.map((value) => value ?? 0),
                  },
                ]),
          ],
          type: "radar",
        },
      ],
      tooltip: {},
    }),
    [currentValues, previous, previousValues, locale],
  );
  const coverage = evidence.evidence_coverage ?? null;

  return (
    <section
      aria-label={translate(
        locale,
        "overview.AnomalyWorkbench.anomalyRadarAndEvidenceCoverage",
      )}
      className="overview-terminal anomaly-workbench"
    >
      <header className="overview-terminal__header">
        <div>
          <span>{translate(locale, "overview.AnomalyWorkbench.researchPriority")}</span>
          <h2>
            {translate(
              locale,
              "overview.AnomalyWorkbench.anomalyRadarConditionOverlapAndEvidenceCoverage",
            )}
          </h2>
          <p>
            {translate(
              locale,
              "overview.AnomalyWorkbench.multiFactorAnomaliesRankByBreakout2Volume2",
            )}
          </p>
        </div>
      </header>
      <div className="anomaly-workbench-grid">
        <OverviewTerminalChart
          option={radarOption}
          summary={translate(
            locale,
            "overview.AnomalyWorkbench.eachAxisIsAPercentageOfAllInstrumentsMissing",
          )}
          tableLabel={translate(
            locale,
            "overview.AnomalyWorkbench.viewAnomalyRadarData",
          )}
          title={translate(locale, "overview.AnomalyWorkbench.anomalyRadar")}
        >
          <table
            aria-label={translate(locale, "overview.AnomalyWorkbench.anomalyRadarData")}
          >
            <thead>
              <tr>
                <th>{translate(locale, "overview.AnomalyWorkbench.condition")}</th>
                <th>{translate(locale, "overview.MarketStructureTerminal.current")}</th>
                <th>{translate(locale, "overview.OverviewHistoryPanels.previous")}</th>
              </tr>
            </thead>
            <tbody>
              {indicators.map((label, index) => (
                <tr key={label}>
                  <th>{label}</th>
                  <td>
                    {currentValues[index] === null || currentValues[index] === undefined
                      ? translate(locale, "overview.SectorHealthTerminal.missing")
                      : `${currentValues[index]?.toFixed(1)}%`}
                  </td>
                  <td>
                    {previousValues[index] === null ||
                    previousValues[index] === undefined
                      ? translate(
                          locale,
                          "overview.SectorMarketMap.insufficientHistory",
                        )
                      : `${previousValues[index]?.toFixed(1)}%`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </OverviewTerminalChart>
        <section className="anomaly-ranking">
          <h3>
            {translate(locale, "overview.AnomalyWorkbench.top10MultiFactorAnomalies")}
          </h3>
          <ol>
            {ranked.slice(0, 10).map((point) => (
              <li key={point.symbol}>
                <Link to={buildMarketResearchHref(point.symbol, linkState)}>
                  {symbolLabel(point.symbol)}
                </Link>
                <span>
                  {anomalyScore(point, atrCutoff)}{" "}
                  {translate(locale, "overview.AnomalyWorkbench.points")}
                </span>
              </li>
            ))}
          </ol>
        </section>
        <section className="anomaly-intersections">
          <h3>
            {translate(
              locale,
              "overview.AnomalyWorkbench.overlappingAnomalyConditions",
            )}
          </h3>
          {intersections.size === 0 ? (
            <p>
              {translate(
                locale,
                "overview.AnomalyWorkbench.noOverlappingConditionsCurrently",
              )}
            </p>
          ) : (
            <ul>
              {[...intersections.entries()]
                .sort((left, right) => right[1] - left[1])
                .slice(0, 8)
                .map(([label, count]) => (
                  <li key={label}>
                    <span>{label}</span>
                    <strong>{count}</strong>
                  </li>
                ))}
            </ul>
          )}
        </section>
        <div className="candidate-churn-panel">
          {mode === "formal" ? (
            <section>
              <h3>
                {translate(
                  locale,
                  "overview.AnomalyWorkbench.formalCoverageAndQualityGates",
                )}
              </h3>
              <p>
                {translate(
                  locale,
                  "overview.AnomalyWorkbench.formalModeExcludesCandidateRankingsCurrentRuleEvidence",
                )}{" "}
                {coverage?.rule_evidence_count ?? 0}/
                {coverage?.total_symbols ?? points.length}
                {translate(locale, "overview.AnomalyWorkbench.comparable")}{" "}
                {coverage?.comparable_symbols ?? 0}.
              </p>
            </section>
          ) : (
            <Churn
              churn={
                (mode === "challenger"
                  ? evidence.challenger_candidate_churn
                  : evidence.verified_candidate_churn) ?? null
              }
              linkState={linkState}
              title={
                mode === "challenger"
                  ? translate(
                      locale,
                      "overview.AnomalyWorkbench.experimentalCandidateChanges",
                    )
                  : translate(
                      locale,
                      "overview.AnomalyWorkbench.verifiedCandidateChanges",
                    )
              }
            />
          )}
          <section>
            <h3>{translate(locale, "overview.AnomalyWorkbench.evidenceCoverage")}</h3>
            {coverage === null ? (
              <p>
                {translate(
                  locale,
                  "overview.AnomalyWorkbench.coverageStatisticsUnavailable",
                )}
              </p>
            ) : (
              <dl className="coverage-grid">
                <div>
                  <dt>
                    {translate(locale, "overview.AnomalyWorkbench.allInstruments")}
                  </dt>
                  <dd>{coverage.total_symbols}</dd>
                </div>
                <div>
                  <dt>{translate(locale, "overview.AnomalyWorkbench.comparable2")}</dt>
                  <dd>{coverage.comparable_symbols}</dd>
                </div>
                <div>
                  <dt>
                    {translate(locale, "overview.OpportunityTable.1520DayReturns")}
                  </dt>
                  <dd>
                    {coverage.session_return_count} / {coverage.return_5_count} /{" "}
                    {coverage.return_20_count}
                  </dd>
                </div>
                <div>
                  <dt>
                    {translate(locale, "overview.AnomalyWorkbench.aTRChannelVolume")}
                  </dt>
                  <dd>
                    {coverage.atr_count} / {coverage.channel_count} /{" "}
                    {coverage.volume_z_count}
                  </dd>
                </div>
                <div>
                  <dt>
                    {translate(locale, "overview.AnomalyWorkbench.intradayEvidence")}
                  </dt>
                  <dd>{coverage.intraday_available_count}</dd>
                </div>
              </dl>
            )}
          </section>
        </div>
      </div>
    </section>
  );
}
