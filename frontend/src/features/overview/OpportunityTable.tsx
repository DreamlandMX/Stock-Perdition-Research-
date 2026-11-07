import { instrumentName } from "../../i18n/instruments";
import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { sparklinePath, historyDates } from "./overview-history";
import { Fragment, useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Link } from "react-router-dom";

import type { components } from "../../api/generated/schema";
import { formatUsMarketTime } from "../../app/presentation";
import { encodeResearchUrlState, type ResearchUrlState } from "../../app/url-state";
import { ConfidenceLabel } from "../../components/ConfidenceLabel";
import { EmptyState } from "../../components/EmptyState";
import { ProbabilityValue } from "../../components/ProbabilityValue";
import { ResearchBoundaryLabel } from "../../components/ResearchBoundaryLabel";
import {
  formatResearchPrice,
  futureTradingDayCloseLabel,
  futureTradingDayWindowLabel,
  researchPriceUnavailableLabel,
} from "../../components/ResearchPriceProjection";
import { ReturnValue } from "../../components/ReturnValue";
import { regimeLabel } from "./RegimeDistribution";
import { rankingBasisPresentation } from "./overview-insights";

type ScreenRow = components["schemas"]["ScreenRow"];
type ResearchOpportunityRow = components["schemas"]["ResearchOpportunityRow"];
type ResearchMode = components["schemas"]["ResearchDisplayMode"];
type ResearchModelEvidence = components["schemas"]["ResearchModelEvidence"];
type RegimeLabel = components["schemas"]["RegimeLabel"];

export interface OpportunityTableProps {
  rows: readonly ScreenRow[];
  snapshotState: ResearchUrlState;
  executionReady?: boolean;
}

function displaySymbol(symbol: string) {
  return symbol.startsWith("US.") ? symbol.slice(3) : symbol;
}

function displayTime(value: string, locale: Locale = "zh-CN") {
  return {
    date: value.slice(0, 10),
    time: formatUsMarketTime(value, locale),
  };
}

function formatOptionalPercent(
  value: number | null | undefined,
  locale: Locale = "zh-CN",
) {
  return value == null
    ? translate(locale, "overview.OverviewPulse.noData")
    : `${(value * 100).toFixed(1)}%`;
}

function formatOptionalNumber(
  value: number | null | undefined,
  locale: Locale = "zh-CN",
) {
  return value == null
    ? translate(locale, "overview.OverviewPulse.noData")
    : value.toFixed(2);
}

function opportunityKey(row: ResearchOpportunityRow) {
  return `${row.symbol}-${row.horizon}`;
}

function evidenceDetailsId(
  row: ResearchOpportunityRow,
  mode: ResearchMode,
  surface: "desktop" | "mobile",
) {
  return `opportunity-evidence-${mode}-${surface}-${opportunityKey(row)}`.replace(
    /[^a-zA-Z0-9_-]/g,
    "-",
  );
}

interface ResearchEvidenceDetailsProps {
  mode: ResearchMode;
  row: ResearchOpportunityRow;
  snapshotState: ResearchUrlState;
}

function ResearchEvidenceDetails({
  mode,
  row,
  snapshotState,
}: ResearchEvidenceDetailsProps) {
  const { locale } = useI18n();
  const asOf = displayTime(row.as_of, locale);
  const rule = row.rule_evidence;
  const selectedEvidence =
    mode === "challenger" ? row.challenger_preview : row.verified_research;
  const modelAvailability =
    selectedEvidence?.tasks.length === 0 || selectedEvidence == null
      ? translate(
          locale,
          "overview.OpportunityTable.dayModelTaskHasNotPassedTheGatesOr",
          { horizon: row.horizon },
        )
      : translate(
          locale,
          "overview.OpportunityTable.researchTasksPublishedFormalForecastsRemainSubjectToPage",
          { count: selectedEvidence.tasks.length },
        );

  return (
    <div className="opportunity-evidence-details">
      <div className="opportunity-evidence-details__basis">
        <strong>{translate(locale, "overview.OpportunityTable.rankingBasis")}</strong>
        <ResearchBoundaryLabel
          scope={mode === "challenger" ? "challenger" : "verified"}
        />
        {row.rank_basis.length === 0 ? (
          <span>
            {translate(
              locale,
              "overview.OpportunityTable.noRankingBasisSuppliedForThisSnapshot",
            )}
          </span>
        ) : (
          <ul>
            {row.rank_basis.map((basis) => {
              const presentation = rankingBasisPresentation(basis, locale);
              return (
                <li key={basis}>
                  {presentation.label}
                  {presentation.original && (
                    <details>
                      <summary>{translate(locale, "common.originalDetails")}</summary>
                      <p>{presentation.original}</p>
                    </details>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <dl>
        <div>
          <dt>{translate(locale, "overview.OpportunityTable.20DayATROfPrice")}</dt>
          <dd>{formatOptionalPercent(rule.atr_pct_20, locale)}</dd>
        </div>
        <div>
          <dt>{translate(locale, "overview.OpportunityTable.volumeZScore")}</dt>
          <dd>{formatOptionalNumber(rule.volume_z_20, locale)}</dd>
        </div>
        <div>
          <dt>{translate(locale, "overview.SectorMarketMap.channelPosition")}</dt>
          <dd>{formatOptionalPercent(rule.channel_position_20, locale)}</dd>
        </div>
        <div>
          <dt>{translate(locale, "overview.OpportunityTable.channelWidth")}</dt>
          <dd>{formatOptionalPercent(rule.channel_width_20, locale)}</dd>
        </div>
        <div>
          <dt>{translate(locale, "overview.OpportunityTable.intradayData")}</dt>
          <dd>
            {rule.intraday_available
              ? translate(locale, "overview.OpportunityTable.available")
              : translate(locale, "options.optionLabels.unavailable")}
          </dd>
        </div>
        <div>
          <dt>{translate(locale, "overview.OpportunityTable.evidenceTime")}</dt>
          <dd>
            <time dateTime={row.as_of}>
              {asOf.date} {asOf.time}
            </time>
          </dd>
        </div>
        <div className="opportunity-evidence-details__gate">
          <dt>{translate(locale, "overview.OpportunityTable.modelAvailability")}</dt>
          <dd>{modelAvailability}</dd>
        </div>
      </dl>
      <Link
        className="opportunity-evidence-details__link"
        to={researchHref(row, snapshotState, null)}
      >
        {translate(locale, "overview.SectorMarketMap.viewInstrumentResearch")}
        <span aria-hidden="true">→</span>
      </Link>
      {mode === "challenger" ? (
        <span className="opportunity-evidence-details__notice">
          {translate(
            locale,
            "overview.OpportunityTable.experimentalCandidatesHaveNotPassedModelQualityGatesFor",
          )}
        </span>
      ) : null}
    </div>
  );
}

function researchHref(
  row: Pick<ScreenRow, "symbol" | "horizon">,
  snapshotState: ResearchUrlState,
  horizon: number | null = row.horizon,
) {
  const symbol = row.symbol;
  const params = encodeResearchUrlState({ ...snapshotState, symbol });
  if (horizon === null) {
    params.delete("horizon");
  } else {
    params.set("horizon", String(horizon));
  }
  return `/research/${encodeURIComponent(symbol)}?${params.toString()}`;
}

function horizonEvidence(
  row: ResearchOpportunityRow,
  mode: ResearchMode,
  horizon: number,
) {
  const rows =
    mode === "challenger"
      ? row.challenger_preview_by_horizon
      : row.verified_research_by_horizon;
  return rows?.find((evidence) => evidence.horizon === horizon);
}

function breakoutUnavailableLabel(
  evidence: ResearchModelEvidence | null | undefined,
  horizon: number,
  locale: Locale = "zh-CN",
) {
  if (evidence?.tasks.some((task) => task.task === "breakout")) {
    return translate(
      locale,
      "overview.OpportunityTable.modelPublishedButNoProbabilityGeneratedForThisRun",
    );
  }
  return translate(
    locale,
    "overview.OpportunityTable.unpublishedDayBreakoutModelHasNotPassedGates",
    { horizon: horizon },
  );
}

function rangeExitUnavailableLabel(
  evidence: ResearchModelEvidence | null | undefined,
  horizon: number,
  regime: RegimeLabel,
  locale: Locale = "zh-CN",
) {
  if (horizon === 1) {
    return translate(locale, "overview.OpportunityTable.notApplicable1DayTaskDisabled");
  }
  if (!evidence?.tasks.some((task) => task.task === "range_exit")) {
    return translate(
      locale,
      "overview.OpportunityTable.noChallengerRangeExitTaskForThisHorizon",
    );
  }
  return regime === "range"
    ? translate(locale, "overview.OpportunityTable.challengerOutputNotGenerated")
    : translate(
        locale,
        "overview.OpportunityTable.notApplicableCurrentRegimeIsNotRange",
      );
}

interface ResearchOpportunityTableProps {
  histories?: import("./overview-history").DailyHistories | undefined;
  mode: ResearchMode;
  rows: readonly ResearchOpportunityRow[];
  snapshotState: ResearchUrlState;
}

function researchLeadReason(row: ResearchOpportunityRow, locale: Locale = "zh-CN") {
  const reasons: string[] = [];
  if (row.rule_evidence.breakout_up_rule)
    reasons.push(
      translate(locale, "overview.OpportunityTable.ruleBasedUpwardBreakout"),
    );
  if (row.rule_evidence.breakout_down_rule)
    reasons.push(
      translate(locale, "overview.OpportunityTable.ruleBasedDownwardBreakout"),
    );
  return reasons.length === 0
    ? translate(
        locale,
        "overview.OpportunityTable.ranksHighlyUnderTheCurrentSortNoRuleBreakout",
      )
    : reasons.join(";");
}

function ResearchLeadTable({
  histories,
  rows,
  snapshotState,
}: Omit<ResearchOpportunityTableProps, "mode">) {
  const { locale } = useI18n();
  const desktopRows = rows.slice(0, 10);
  const mobileRows = rows.slice(0, 4);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const toggleExpanded = (row: ResearchOpportunityRow) => {
    const key = opportunityKey(row);
    setExpandedKey((current) => (current === key ? null : key));
  };

  return (
    <section className="overview-section opportunity-table-section">
      <header className="overview-section__header">
        <h2>
          {translate(locale, "overview.OpportunityTable.todaySMovesAndResearchLeads")}
        </h2>
        <span>
          {translate(
            locale,
            "overview.OpportunityTable.crossSectionalRuleEvidenceFirst10DesktopRows",
          )}
        </span>
      </header>
      {desktopRows.length === 0 ? (
        <EmptyState
          description={translate(
            locale,
            "overview.OpportunityTable.noResearchInstrumentsMeetTheRuleRankingConditionsIn",
          )}
          title={translate(locale, "overview.OpportunityTable.noResearchCandidates")}
        />
      ) : (
        <>
          <div className="opportunity-table__scroll">
            <table
              aria-label={translate(
                locale,
                "overview.OpportunityTable.topResearchCandidates",
              )}
              className="opportunity-table opportunity-table--research opportunity-table--research-leads"
            >
              <thead>
                <tr>
                  <th scope="col">
                    {translate(locale, "overview.OpportunityTable.rank")}
                  </th>
                  <th scope="col">
                    {translate(locale, "overview.OverviewPage.instrument")}
                  </th>
                  <th scope="col">
                    {translate(locale, "overview.OpportunityTable.whyItAppearsToday")}
                  </th>
                  {histories ? (
                    <th scope="col">
                      {translate(locale, "overview.OpportunityTable.trend30Days")}
                    </th>
                  ) : null}
                  <th scope="col">
                    {translate(locale, "overview.OpportunityTable.1520DayReturns")}
                  </th>
                  <th scope="col">
                    {translate(locale, "overview.OpportunityTable.volumeZ")}
                  </th>
                  <th scope="col">
                    {translate(locale, "overview.SectorMarketMap.channelPosition")}
                  </th>
                  <th scope="col">
                    {translate(locale, "overview.OpportunityTable.research")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {desktopRows.map((row, index) => {
                  const rowKey = opportunityKey(row);
                  const isExpanded = expandedKey === rowKey;
                  const detailId = evidenceDetailsId(row, "research", "desktop");
                  return (
                    <Fragment key={`research-lead-${rowKey}`}>
                      <tr>
                        <td className="opportunity-table__rank">
                          <button
                            aria-controls={detailId}
                            aria-expanded={isExpanded}
                            aria-label={translate(
                              locale,
                              "overview.OpportunityTable.researchEvidence",
                              {
                                value: isExpanded
                                  ? translate(
                                      locale,
                                      "overview.OpportunityTable.collapse",
                                    )
                                  : translate(
                                      locale,
                                      "overview.OpportunityTable.expand",
                                    ),
                                value2: displaySymbol(row.symbol),
                              },
                            )}
                            onClick={() => toggleExpanded(row)}
                            type="button"
                          >
                            <span>{index + 1}</span>
                            <ChevronDown
                              aria-hidden="true"
                              size={15}
                              strokeWidth={1.7}
                            />
                          </button>
                        </td>
                        <th scope="row">
                          <strong>{displaySymbol(row.symbol)}</strong>
                          <span>{instrumentName(row.symbol, row.name, locale)}</span>
                          <ResearchBoundaryLabel />
                        </th>
                        <td className="opportunity-table__lead-reason">
                          {researchLeadReason(row, locale)}
                        </td>
                        {histories ? (
                          <td>
                            {histories[row.symbol]?.length ? (
                              <svg
                                className="ov-research-spark"
                                viewBox="0 0 90 24"
                                aria-label={translate(
                                  locale,
                                  "overview.OpportunityTable.last30DailyCloses",
                                  { symbol: displaySymbol(row.symbol) },
                                )}
                              >
                                <path
                                  stroke="var(--color-up)"
                                  fill="none"
                                  strokeWidth="1.3"
                                  d={sparklinePath(
                                    histories[row.symbol]!.slice(-30),
                                    90,
                                    24,
                                    historyDates(histories),
                                  )}
                                />
                              </svg>
                            ) : (
                              translate(
                                locale,
                                "overview.SectorMarketMap.insufficientHistory",
                              )
                            )}
                          </td>
                        ) : null}
                        <td className="opportunity-table__stacked-values opportunity-table__lead-returns">
                          <span>
                            <small>
                              {translate(locale, "overview.OpportunityTable.1Day")}
                            </small>
                            <ReturnValue
                              value={row.rule_evidence.session_return ?? null}
                            />
                          </span>
                          <span>
                            <small>
                              {translate(locale, "overview.OpportunityTable.5Days")}
                            </small>
                            <ReturnValue value={row.rule_evidence.return_5 ?? null} />
                          </span>
                          <span>
                            <small>
                              {translate(locale, "overview.OpportunityTable.20Days")}
                            </small>
                            <ReturnValue value={row.rule_evidence.return_20 ?? null} />
                          </span>
                        </td>
                        <td
                          className={
                            Math.abs(row.rule_evidence.volume_z_20 ?? 0) >= 2
                              ? "opportunity-table__anomaly"
                              : undefined
                          }
                        >
                          {formatOptionalNumber(row.rule_evidence.volume_z_20, locale)}
                        </td>
                        <td>
                          {formatOptionalPercent(
                            row.rule_evidence.channel_position_20,
                            locale,
                          )}
                        </td>
                        <td>
                          <Link
                            className="opportunity-evidence-details__link"
                            to={researchHref(row, snapshotState)}
                          >
                            {translate(locale, "overview.OverviewPage.research")}{" "}
                            {displaySymbol(row.symbol)}
                          </Link>
                        </td>
                      </tr>
                      {isExpanded ? (
                        <tr className="opportunity-table__detail-row">
                          <td colSpan={histories ? 8 : 7} id={detailId}>
                            <ResearchEvidenceDetails
                              mode="research"
                              row={row}
                              snapshotState={snapshotState}
                            />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <ol
            aria-label={translate(
              locale,
              "overview.OpportunityTable.compactResearchOpportunities",
            )}
            className="opportunity-mobile-list"
            data-testid="mobile-research-opportunity-list"
          >
            {mobileRows.map((row, index) => {
              const rowKey = opportunityKey(row);
              const isExpanded = expandedKey === rowKey;
              const detailId = evidenceDetailsId(row, "research", "mobile");
              return (
                <li key={`research-lead-mobile-${rowKey}`}>
                  <Link
                    className="opportunity-mobile-list__link"
                    to={researchHref(row, snapshotState)}
                  >
                    <span>
                      <strong>
                        <span className="opportunity-mobile-list__rank">
                          #{index + 1}
                        </span>
                        {displaySymbol(row.symbol)}
                      </strong>
                      <small>{instrumentName(row.symbol, row.name, locale)}</small>
                    </span>
                    <span>{regimeLabel(row.regime, row.rule_evidence, locale)}</span>
                  </Link>
                  <p className="opportunity-mobile-list__reason">
                    {researchLeadReason(row, locale)}
                  </p>
                  <dl>
                    <div>
                      <dt>
                        {translate(locale, "overview.OpportunityTable.1520DayReturns")}
                      </dt>
                      <dd>
                        <ReturnValue value={row.rule_evidence.session_return ?? null} />{" "}
                        / <ReturnValue value={row.rule_evidence.return_5 ?? null} /> /{" "}
                        <ReturnValue value={row.rule_evidence.return_20 ?? null} />
                      </dd>
                    </div>
                    <div>
                      <dt>{translate(locale, "overview.OpportunityTable.volumeZ")}</dt>
                      <dd>
                        {formatOptionalNumber(row.rule_evidence.volume_z_20, locale)}
                      </dd>
                    </div>
                    <div>
                      <dt>
                        {translate(locale, "overview.SectorMarketMap.channelPosition")}
                      </dt>
                      <dd>
                        {formatOptionalPercent(
                          row.rule_evidence.channel_position_20,
                          locale,
                        )}
                      </dd>
                    </div>
                  </dl>
                  <button
                    aria-controls={detailId}
                    aria-expanded={isExpanded}
                    className="opportunity-mobile-list__expand"
                    onClick={() => toggleExpanded(row)}
                    type="button"
                  >
                    {isExpanded
                      ? translate(
                          locale,
                          "overview.OpportunityTable.collapseFullEvidence",
                        )
                      : translate(locale, "overview.OpportunityTable.viewFullEvidence")}
                    <ChevronDown aria-hidden="true" size={16} strokeWidth={1.7} />
                  </button>
                  {isExpanded ? (
                    <div className="opportunity-mobile-list__details" id={detailId}>
                      <ResearchEvidenceDetails
                        mode="research"
                        row={row}
                        snapshotState={snapshotState}
                      />
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </>
      )}
    </section>
  );
}

function ModelResearchOpportunityTable({
  mode,
  rows,
  snapshotState,
}: ResearchOpportunityTableProps) {
  const { locale } = useI18n();
  const visible = rows.slice(0, 10);
  const displayedHorizon = visible[0]?.horizon ?? 5;
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  useEffect(() => {
    if (
      expandedKey !== null &&
      !visible.some((row) => opportunityKey(row) === expandedKey)
    ) {
      setExpandedKey(null);
    }
  }, [expandedKey, visible]);

  useEffect(() => {
    setExpandedKey(null);
  }, [mode]);

  const toggleExpanded = (row: ResearchOpportunityRow) => {
    const key = opportunityKey(row);
    setExpandedKey((current) => (current === key ? null : key));
  };

  return (
    <section className="overview-section opportunity-table-section">
      <header className="overview-section__header">
        <h2>
          {mode === "challenger"
            ? translate(
                locale,
                "overview.OpportunityTable.experimentalCandidatePreview",
              )
            : translate(locale, "overview.OpportunityTable.topResearchCandidates")}
        </h2>
        <span>
          {mode === "challenger"
            ? translate(
                locale,
                "overview.OpportunityTable.qualityGatesNotPassedExperimentalResearchOnly",
              )
            : translate(
                locale,
                "overview.OpportunityTable.rankedByObservedRuleEvidenceAndVerifiedModelTasks",
              )}
        </span>
      </header>
      {mode === "challenger" ? (
        <p className="research-task-note">
          {translate(
            locale,
            "overview.OpportunityTable.upProbabilityMeasuresTheClosingDirectionAtTheEnd",
          )}
        </p>
      ) : null}
      {visible.length === 0 ? (
        <EmptyState
          description={
            mode === "challenger"
              ? translate(
                  locale,
                  "overview.OpportunityTable.noChallengerTaskOutputAvailableInThisSnapshot",
                )
              : translate(
                  locale,
                  "overview.OpportunityTable.thisSnapshotFallsBackToRuleRankingNoResearch",
                )
          }
          title={translate(locale, "overview.OpportunityTable.noResearchCandidates")}
        />
      ) : (
        <>
          <div className="opportunity-table__scroll">
            <table
              aria-label={
                mode === "challenger"
                  ? translate(
                      locale,
                      "overview.OpportunityTable.experimentalCandidatePreview",
                    )
                  : translate(locale, "overview.OpportunityTable.topResearchCandidates")
              }
              className="opportunity-table opportunity-table--research"
            >
              <colgroup>
                <col className="opportunity-table__col-rank" />
                <col className="opportunity-table__col-symbol" />
                <col className="opportunity-table__col-status" />
                <col className="opportunity-table__col-price" />
                <col className="opportunity-table__col-returns" />
                <col className="opportunity-table__col-forecast" />
                <col className="opportunity-table__col-stage" />
                <col className="opportunity-table__col-breakout" />
                <col className="opportunity-table__col-evidence" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">
                    {translate(locale, "overview.OpportunityTable.rank")}
                  </th>
                  <th scope="col">
                    {translate(locale, "overview.OverviewPage.instrument")}
                  </th>
                  <th scope="col">
                    {translate(locale, "overview.OpportunityTable.currentRuleRegime")}
                  </th>
                  <th scope="col">
                    {translate(locale, "overview.OpportunityTable.latestPrice")}
                  </th>
                  <th scope="col">
                    {translate(
                      locale,
                      "overview.OpportunityTable.returnsOverThePast520TradingDays",
                    )}
                  </th>
                  <th scope="col">
                    {mode === "challenger"
                      ? translate(
                          locale,
                          "overview.OpportunityTable.dayChallengerUpProbability",
                          { displayedHorizon: displayedHorizon },
                        )
                      : translate(locale, "overview.OpportunityTable.forecast", {
                          closeHorizon: futureTradingDayCloseLabel(
                            displayedHorizon,
                            locale,
                          ),
                        })}
                  </th>
                  <th scope="col">
                    {mode === "challenger"
                      ? translate(
                          locale,
                          "overview.OpportunityTable.dayChallengerUpwardDownwardBreakoutProbability",
                          { displayedHorizon: displayedHorizon },
                        )
                      : translate(
                          locale,
                          "overview.OpportunityTable.potentialHighLow",
                          {
                            window: futureTradingDayWindowLabel(
                              displayedHorizon,
                              locale,
                            ),
                          },
                        )}
                  </th>
                  <th scope="col">
                    {mode === "challenger"
                      ? translate(
                          locale,
                          "overview.OpportunityTable.dayChallengerRangeExitRangeOnly",
                          { displayedHorizon: displayedHorizon },
                        )
                      : translate(
                          locale,
                          "overview.OpportunityTable.breakoutProbabilityOverTheNext20TradingDaysUp",
                        )}
                  </th>
                  <th scope="col">
                    {mode === "challenger"
                      ? translate(locale, "overview.OpportunityTable.failedTasks")
                      : translate(locale, "overview.OpportunityTable.ruleEvidence")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((row, index) => {
                  const evidence =
                    mode === "challenger"
                      ? row.challenger_preview
                      : row.verified_research;
                  const values = evidence?.values;
                  const projection = evidence?.price_projection;
                  const priceBand = projection?.endpoint_price_band;
                  const projectionCurrency = projection?.currency ?? "USD";
                  const breakout20 = horizonEvidence(row, mode, 20);
                  const rowKey = opportunityKey(row);
                  const isExpanded = expandedKey === rowKey;
                  const detailId = evidenceDetailsId(row, mode, "desktop");
                  return (
                    <Fragment key={`${mode}-${rowKey}`}>
                      <tr>
                        <td className="opportunity-table__rank">
                          <button
                            aria-controls={detailId}
                            aria-expanded={isExpanded}
                            aria-label={translate(
                              locale,
                              "overview.OpportunityTable.researchEvidence",
                              {
                                value: isExpanded
                                  ? translate(
                                      locale,
                                      "overview.OpportunityTable.collapse",
                                    )
                                  : translate(
                                      locale,
                                      "overview.OpportunityTable.expand",
                                    ),
                                value2: displaySymbol(row.symbol),
                              },
                            )}
                            onClick={() => toggleExpanded(row)}
                            type="button"
                          >
                            <span>{index + 1}</span>
                            <ChevronDown
                              aria-hidden="true"
                              size={15}
                              strokeWidth={1.7}
                            />
                          </button>
                        </td>
                        <th scope="row">
                          <Link to={researchHref(row, snapshotState)}>
                            {displaySymbol(row.symbol)}
                          </Link>
                          <span>{instrumentName(row.symbol, row.name, locale)}</span>
                          <ResearchBoundaryLabel
                            scope={mode === "challenger" ? "challenger" : "verified"}
                          />
                        </th>
                        <td>
                          <span
                            title={translate(
                              locale,
                              "overview.OpportunityTable.currentMarketRuleRegimeDoesNotIndicateFutureForecast",
                            )}
                          >
                            {regimeLabel(row.regime, row.rule_evidence, locale)}
                          </span>
                        </td>
                        <td>{row.rule_evidence.last_close.toFixed(2)}</td>
                        <td className="opportunity-table__stacked-values">
                          <span>
                            <small>
                              {translate(locale, "overview.SectorMarketMap.5Days")}
                            </small>
                            <ReturnValue value={row.rule_evidence.return_5 ?? null} />
                          </span>
                          <span>
                            <small>
                              {translate(locale, "overview.SectorMarketMap.20Days")}
                            </small>
                            <ReturnValue value={row.rule_evidence.return_20 ?? null} />
                          </span>
                        </td>
                        <td>
                          {mode === "challenger" ? (
                            <ProbabilityValue
                              value={values?.forecast_up_probability ?? null}
                            />
                          ) : (
                            <span className="opportunity-table__price-forecast">
                              {priceBand == null ? (
                                researchPriceUnavailableLabel(
                                  projection?.endpoint_price_unavailable_reason,
                                  row.horizon,
                                  locale,
                                )
                              ) : (
                                <>
                                  <strong>
                                    {formatResearchPrice(
                                      priceBand.q50,
                                      projectionCurrency,
                                      locale,
                                    )}
                                  </strong>
                                  <small>
                                    q10–q90{" "}
                                    {formatResearchPrice(
                                      priceBand.q10,
                                      projectionCurrency,
                                      locale,
                                    )}{" "}
                                    –{" "}
                                    {formatResearchPrice(
                                      priceBand.q90,
                                      projectionCurrency,
                                      locale,
                                    )}
                                  </small>
                                </>
                              )}
                            </span>
                          )}
                        </td>
                        <td>
                          {mode === "challenger" ? (
                            <span className="opportunity-table__stacked-values">
                              <span>
                                <small>
                                  {translate(locale, "overview.OpportunityTable.up")}
                                </small>
                                <ProbabilityValue
                                  value={values?.breakout_up_probability ?? null}
                                />
                              </span>
                              <span>
                                <small>
                                  {translate(locale, "overview.OpportunityTable.down")}
                                </small>
                                <ProbabilityValue
                                  value={values?.breakout_down_probability ?? null}
                                />
                              </span>
                            </span>
                          ) : (
                            <span className="opportunity-table__stacked-values">
                              <span>
                                <small>
                                  {translate(locale, "overview.OpportunityTable.high")}
                                </small>
                                <span className="numeric-value">
                                  {projection?.stage_high_price_q50 == null
                                    ? researchPriceUnavailableLabel(
                                        projection?.stage_high_price_unavailable_reason,
                                        row.horizon,
                                        locale,
                                      )
                                    : formatResearchPrice(
                                        projection.stage_high_price_q50,
                                        projection.currency,
                                        locale,
                                      )}
                                </span>
                              </span>
                              <span>
                                <small>
                                  {translate(locale, "overview.OpportunityTable.low")}
                                </small>
                                <span className="numeric-value">
                                  {projection?.stage_low_price_q50 == null
                                    ? researchPriceUnavailableLabel(
                                        projection?.stage_low_price_unavailable_reason,
                                        row.horizon,
                                        locale,
                                      )
                                    : formatResearchPrice(
                                        projection.stage_low_price_q50,
                                        projection.currency,
                                        locale,
                                      )}
                                </span>
                              </span>
                            </span>
                          )}
                        </td>
                        <td>
                          {mode === "challenger" ? (
                            <ProbabilityValue
                              unavailableLabel={rangeExitUnavailableLabel(
                                evidence,
                                row.horizon,
                                row.regime,
                                locale,
                              )}
                              value={values?.range_exit_probability ?? null}
                            />
                          ) : (
                            <Link
                              className="opportunity-table__evidence-link opportunity-table__stacked-values"
                              title={breakoutUnavailableLabel(breakout20, 20, locale)}
                              to={researchHref(row, snapshotState, 20)}
                            >
                              <span>
                                <small>
                                  {translate(locale, "overview.OpportunityTable.up")}
                                </small>
                                <ProbabilityValue
                                  unavailableLabel={translate(
                                    locale,
                                    "overview.OpportunityTable.unpublished",
                                  )}
                                  value={
                                    breakout20?.values.breakout_up_probability ?? null
                                  }
                                />
                              </span>
                              <span>
                                <small>
                                  {translate(locale, "overview.OpportunityTable.down")}
                                </small>
                                <ProbabilityValue
                                  unavailableLabel={translate(
                                    locale,
                                    "overview.OpportunityTable.unpublished",
                                  )}
                                  value={
                                    breakout20?.values.breakout_down_probability ?? null
                                  }
                                />
                              </span>
                            </Link>
                          )}
                        </td>
                        <td>
                          {mode === "challenger"
                            ? evidence?.tasks
                                .map((task) => {
                                  if (task.task === "forecast_direction") {
                                    return translate(
                                      locale,
                                      "overview.OpportunityTable.directionForecast",
                                    );
                                  }
                                  if (task.task === "breakout") {
                                    return translate(
                                      locale,
                                      "overview.OpportunityTable.breakoutForecast",
                                    );
                                  }
                                  if (task.task === "range_exit") {
                                    return translate(
                                      locale,
                                      "overview.OpportunityTable.rangeExit",
                                    );
                                  }
                                  if (task.task === "mae") {
                                    return translate(
                                      locale,
                                      "overview.OpportunityTable.maximumAdverseExcursion",
                                    );
                                  }
                                  return task.task;
                                })
                                .map(
                                  (label, index) =>
                                    `${label}(${evidence?.tasks[index]?.model_version ?? translate(locale, "overview.OpportunityTable.unknownVersion")})`,
                                )
                                .join(";")
                            : row.rule_evidence.breakout_up_rule
                              ? translate(
                                  locale,
                                  "overview.OpportunityTable.ruleBasedUpwardBreakout",
                                )
                              : translate(
                                  locale,
                                  "overview.OpportunityTable.channelPosition",
                                  {
                                    value:
                                      row.rule_evidence.channel_position_20 == null
                                        ? "—"
                                        : `${(
                                            row.rule_evidence.channel_position_20 * 100
                                          ).toFixed(0)}%`,
                                  },
                                )}
                        </td>
                      </tr>
                      {isExpanded ? (
                        <tr className="opportunity-table__detail-row">
                          <td colSpan={9} id={detailId}>
                            <ResearchEvidenceDetails
                              mode={mode}
                              row={row}
                              snapshotState={snapshotState}
                            />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <ol
            aria-label={translate(
              locale,
              "overview.OpportunityTable.compactResearchOpportunities",
            )}
            className="opportunity-mobile-list"
            data-testid="mobile-research-opportunity-list"
          >
            {visible.map((row, index) => {
              const evidence =
                mode === "challenger" ? row.challenger_preview : row.verified_research;
              const breakout20 = horizonEvidence(row, mode, 20);
              const projection = evidence?.price_projection;
              const priceBand = projection?.endpoint_price_band;
              const currency = projection?.currency ?? "USD";
              const rowKey = opportunityKey(row);
              const isExpanded = expandedKey === rowKey;
              const detailId = evidenceDetailsId(row, mode, "mobile");
              return (
                <li key={`${mode}-mobile-${rowKey}`}>
                  <Link
                    className="opportunity-mobile-list__link"
                    to={researchHref(row, snapshotState)}
                  >
                    <span>
                      <strong>
                        <span className="opportunity-mobile-list__rank">
                          #{index + 1}
                        </span>
                        {displaySymbol(row.symbol)}
                      </strong>
                      <small>{instrumentName(row.symbol, row.name, locale)}</small>
                      <ResearchBoundaryLabel
                        scope={mode === "challenger" ? "challenger" : "verified"}
                      />
                    </span>
                    <span>{regimeLabel(row.regime, row.rule_evidence, locale)}</span>
                  </Link>
                  <dl>
                    {mode === "challenger" ? (
                      <>
                        <div>
                          <dt>
                            {row.horizon}
                            {translate(
                              locale,
                              "overview.OpportunityTable.dayChallengerUpProbability2",
                            )}
                          </dt>
                          <dd>
                            <ProbabilityValue
                              value={evidence?.values.forecast_up_probability ?? null}
                            />
                          </dd>
                        </div>
                        <div>
                          <dt>
                            {row.horizon}
                            {translate(
                              locale,
                              "overview.OpportunityTable.dayChallengerRangeExit",
                            )}
                          </dt>
                          <dd>
                            <ProbabilityValue
                              unavailableLabel={rangeExitUnavailableLabel(
                                evidence,
                                row.horizon,
                                row.regime,
                                locale,
                              )}
                              value={evidence?.values.range_exit_probability ?? null}
                            />
                          </dd>
                        </div>
                        <div>
                          <dt>
                            {row.horizon}
                            {translate(
                              locale,
                              "overview.OpportunityTable.dayChallengerUpwardBreakoutProbability",
                            )}
                          </dt>
                          <dd>
                            <ProbabilityValue
                              value={evidence?.values.breakout_up_probability ?? null}
                            />
                          </dd>
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="opportunity-mobile-list__primary">
                          <dt>
                            {futureTradingDayCloseLabel(row.horizon, locale)}
                            {translate(
                              locale,
                              "overview.OpportunityTable.medianScenarioPriceQ50",
                            )}
                          </dt>
                          <dd>
                            {priceBand == null
                              ? researchPriceUnavailableLabel(
                                  projection?.endpoint_price_unavailable_reason,
                                  row.horizon,
                                  locale,
                                )
                              : formatResearchPrice(priceBand.q50, currency, locale)}
                          </dd>
                        </div>
                        <div>
                          <dt>
                            {futureTradingDayCloseLabel(row.horizon, locale)}
                            {translate(
                              locale,
                              "overview.OpportunityTable.priceIntervalQ10Q90",
                            )}
                          </dt>
                          <dd>
                            {priceBand == null
                              ? researchPriceUnavailableLabel(
                                  projection?.endpoint_price_unavailable_reason,
                                  row.horizon,
                                  locale,
                                )
                              : `${formatResearchPrice(
                                  priceBand.q10,
                                  currency,
                                  locale,
                                )} – ${formatResearchPrice(priceBand.q90, currency, locale)}`}
                          </dd>
                        </div>
                      </>
                    )}
                    {mode === "challenger" ? null : (
                      <div>
                        <dt>
                          {translate(
                            locale,
                            "overview.OpportunityTable.returnsOverThePast520TradingDays",
                          )}
                        </dt>
                        <dd>
                          <ReturnValue value={row.rule_evidence.return_5 ?? null} />
                          {" / "}
                          <ReturnValue value={row.rule_evidence.return_20 ?? null} />
                        </dd>
                      </div>
                    )}
                  </dl>
                  <button
                    aria-controls={detailId}
                    aria-expanded={isExpanded}
                    className="opportunity-mobile-list__expand"
                    onClick={() => toggleExpanded(row)}
                    type="button"
                  >
                    {isExpanded
                      ? translate(
                          locale,
                          "overview.OpportunityTable.collapseFullEvidence",
                        )
                      : translate(locale, "overview.OpportunityTable.viewFullEvidence")}
                    <ChevronDown aria-hidden="true" size={16} strokeWidth={1.7} />
                  </button>
                  {isExpanded ? (
                    <div className="opportunity-mobile-list__details" id={detailId}>
                      {mode === "challenger" ? null : (
                        <dl>
                          <div>
                            <dt>
                              {futureTradingDayWindowLabel(row.horizon, locale)}
                              {translate(
                                locale,
                                "overview.OpportunityTable.potentialHighLow2",
                              )}
                            </dt>
                            <dd>
                              {projection?.stage_high_price_q50 == null
                                ? researchPriceUnavailableLabel(
                                    projection?.stage_high_price_unavailable_reason,
                                    row.horizon,
                                    locale,
                                  )
                                : formatResearchPrice(
                                    projection.stage_high_price_q50,
                                    currency,
                                    locale,
                                  )}
                              {" / "}
                              {projection?.stage_low_price_q50 == null
                                ? researchPriceUnavailableLabel(
                                    projection?.stage_low_price_unavailable_reason,
                                    row.horizon,
                                    locale,
                                  )
                                : formatResearchPrice(
                                    projection.stage_low_price_q50,
                                    currency,
                                    locale,
                                  )}
                            </dd>
                          </div>
                          <div>
                            <dt>
                              {translate(
                                locale,
                                "overview.OpportunityTable.breakoutOverTheNext20TradingDaysUpDown",
                              )}
                            </dt>
                            <dd>
                              <Link to={researchHref(row, snapshotState, 20)}>
                                <ProbabilityValue
                                  unavailableLabel={breakoutUnavailableLabel(
                                    breakout20,
                                    20,
                                    locale,
                                  )}
                                  value={
                                    breakout20?.values.breakout_up_probability ?? null
                                  }
                                />
                                {" / "}
                                <ProbabilityValue
                                  unavailableLabel={breakoutUnavailableLabel(
                                    breakout20,
                                    20,
                                    locale,
                                  )}
                                  value={
                                    breakout20?.values.breakout_down_probability ?? null
                                  }
                                />
                              </Link>
                            </dd>
                          </div>
                        </dl>
                      )}
                      <ResearchEvidenceDetails
                        mode={mode}
                        row={row}
                        snapshotState={snapshotState}
                      />
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </>
      )}
    </section>
  );
}

export function ResearchOpportunityTable(props: ResearchOpportunityTableProps) {
  return props.mode === "research" ? (
    <ResearchLeadTable
      rows={props.rows}
      snapshotState={props.snapshotState}
      histories={props.histories}
    />
  ) : (
    <ModelResearchOpportunityTable {...props} />
  );
}

export function OpportunityTable({
  rows,
  snapshotState,
  executionReady = true,
}: OpportunityTableProps) {
  const { locale } = useI18n();
  const executableRows = rows.filter((row) => !row.effective_abstain);
  const titleKey = executionReady
    ? "overview.OpportunityTable.topOpportunitiesExecutableEvidenceRanking"
    : "overview.scenarioEvidenceRanking";

  return (
    <section className="overview-section opportunity-table-section">
      <header className="overview-section__header">
        <h2>{translate(locale, titleKey)}</h2>
        <span>
          {translate(
            locale,
            "overview.OpportunityTable.q10WeakReturnScenarioReceivesTheSameProminenceAs",
          )}
        </span>
      </header>
      {!executionReady && (
        <p role="status">
          {translate(locale, "overview.blockedScenarios")};{" "}
          {translate(locale, "overview.executionBlocked")}
        </p>
      )}
      {executableRows.length === 0 ? (
        <EmptyState
          description={translate(
            locale,
            executionReady
              ? "overview.OpportunityTable.noResearchInstrumentsPassDataCalibrationAndAbstentionGates"
              : "overview.noPublishedScenarios",
          )}
          title={translate(
            locale,
            executionReady
              ? "overview.OpportunityTable.noExecutableOpportunities"
              : "overview.noPublishedScenarios",
          )}
        />
      ) : (
        <div className="opportunity-table__scroll">
          <table aria-label={translate(locale, titleKey)} className="opportunity-table">
            <thead>
              <tr>
                <th scope="col">
                  {translate(locale, "overview.OverviewPage.instrument")}
                </th>
                <th scope="col">
                  {translate(locale, "overview.OpportunityTable.status")}
                </th>
                <th scope="col">
                  {translate(
                    locale,
                    executionReady
                      ? "overview.OpportunityTable.executableUpProbability"
                      : "overview.scenarioUpProbability",
                  )}
                </th>
                <th scope="col">
                  {translate(locale, "overview.OverviewHistoryPanels.upwardBreakout")}
                </th>
                <th scope="col">
                  {translate(locale, "overview.OverviewHistoryPanels.downwardBreakout")}
                </th>
                <th className="opportunity-table__risk" scope="col">
                  {translate(
                    locale,
                    executionReady
                      ? "overview.OpportunityTable.executableQ10Downside"
                      : "overview.scenarioQ10",
                  )}
                </th>
                <th scope="col">
                  {translate(
                    locale,
                    executionReady
                      ? "overview.OpportunityTable.executableQ50Median"
                      : "overview.scenarioQ50",
                  )}
                </th>
                <th scope="col">
                  {translate(locale, "overview.OpportunityTable.intervalWidth")}
                </th>
                <th scope="col">
                  {translate(locale, "overview.OpportunityTable.confidenceLevel")}
                </th>
                <th scope="col">
                  {translate(locale, "overview.OpportunityTable.dataTime")}
                </th>
              </tr>
            </thead>
            <tbody>
              {executableRows.map((row) => {
                const asOf = displayTime(row.as_of, locale);
                return (
                  <tr key={`${row.symbol}-${row.horizon}`}>
                    <th scope="row">
                      <Link to={researchHref(row, snapshotState)}>
                        {displaySymbol(row.symbol)}
                      </Link>
                      <span>{instrumentName(row.symbol, row.name, locale)}</span>
                    </th>
                    <td>{regimeLabel(row.regime, row.rule_evidence, locale)}</td>
                    <td className="opportunity-table__probability">
                      <ProbabilityValue value={row.executable_up_probability ?? null} />
                    </td>
                    <td>
                      <ProbabilityValue value={row.breakout_up_probability ?? null} />
                    </td>
                    <td>
                      <ProbabilityValue value={row.breakout_down_probability ?? null} />
                    </td>
                    <td className="opportunity-table__risk">
                      <ReturnValue value={row.executable_q10} />
                    </td>
                    <td>
                      <ReturnValue value={row.executable_q50} />
                    </td>
                    <td>
                      <ProbabilityValue value={row.interval_width ?? null} />
                    </td>
                    <td>
                      <ConfidenceLabel level={row.confidence_level} />
                    </td>
                    <td>
                      <time dateTime={row.as_of}>
                        <span>{asOf.date}</span>
                        <span>{asOf.time}</span>
                      </time>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {executableRows.length === 0 ? null : (
        <ol
          aria-label={translate(
            locale,
            "overview.OpportunityTable.topOpportunityEvidence",
          )}
          className="opportunity-mobile-list"
          data-testid="mobile-opportunity-list"
        >
          {executableRows.map((row) => {
            const asOf = displayTime(row.as_of, locale);
            const symbol = displaySymbol(row.symbol);
            return (
              <li
                aria-label={translate(
                  locale,
                  "overview.OpportunityTable.evidenceSummary",
                  { symbol: symbol },
                )}
                key={`${row.symbol}-${row.horizon}`}
              >
                <Link
                  className="opportunity-mobile-list__link"
                  to={researchHref(row, snapshotState)}
                >
                  <span>
                    <strong>{symbol}</strong>
                    <small>{instrumentName(row.symbol, row.name, locale)}</small>
                  </span>
                  <span>{regimeLabel(row.regime, row.rule_evidence, locale)}</span>
                </Link>
                <dl>
                  <div>
                    <dt>
                      {translate(locale, "overview.OpportunityTable.upProbability")}
                    </dt>
                    <dd>
                      <ProbabilityValue value={row.executable_up_probability ?? null} />
                    </dd>
                  </div>
                  <div className="opportunity-mobile-list__risk">
                    <dt>
                      {translate(locale, "overview.OpportunityTable.q10Downside")}
                    </dt>
                    <dd>
                      <ReturnValue value={row.executable_q10} />
                    </dd>
                  </div>
                  <div>
                    <dt>
                      {translate(locale, "overview.OpportunityTable.confidenceLevel")}
                    </dt>
                    <dd>
                      <ConfidenceLabel level={row.confidence_level} />
                    </dd>
                  </div>
                  <div>
                    <dt>{translate(locale, "overview.OpportunityTable.dataTime")}</dt>
                    <dd>
                      <time dateTime={row.as_of}>
                        {asOf.date} {asOf.time}
                      </time>
                    </dd>
                  </div>
                </dl>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
