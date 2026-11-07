import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { DataStatusBanner } from "../../components/DataStatusBanner";
import { Link } from "react-router-dom";
import type { components } from "../../api/generated/schema";
import { sectorLabel, formatUsMarketTime } from "../../app/presentation";
import {
  breadthChange,
  buildOverviewInsightTitle,
  previousHistoryPoint,
} from "./overview-insights";
import { buildOverviewFactSummary } from "./overview-summary";
import { formatReturn } from "./overview-terminal";
import type { OptionOverviewResponse } from "../options/useOptionsOverview";
import { optionNumber, optionPercent, optionPoints } from "../options/option-labels";

type Data = components["schemas"]["OverviewReadModel"];
type Snapshot = components["schemas"]["ResolvedSnapshot"];
export interface OverviewHeaderBandProps {
  data: Data;
  gateHref: string;
  optionsHref: string;
  isHistorical: boolean;
  mode: components["schemas"]["ResearchDisplayMode"];
  proxyHref: (symbol: string) => string;
  servingStatus: components["schemas"]["ServingStatus"];
  snapshot: Snapshot;
  options?: OptionOverviewResponse | undefined;
  optionsPending?: boolean;
  optionsError?: boolean;
}
export function OverviewHeaderBand({
  data,
  gateHref,
  optionsHref,
  isHistorical,
  mode,
  servingStatus,
  snapshot,
  options,
  optionsPending,
  optionsError,
  proxyHref,
}: OverviewHeaderBandProps) {
  const { locale } = useI18n();
  const e = data.internal_evidence,
    up = data.regime_distribution.find((r) => r.regime === "uptrend"),
    prev = previousHistoryPoint(data);
  const delta = up && prev ? up.fraction - prev.uptrend_fraction : null;
  const research =
    mode === "challenger" ? data.challenger_opportunities : data.research_opportunities;
  const coverage =
    data.candidate_summary == null
      ? translate(locale, "overview.OverviewHeaderBand.noEvaluationEvidence")
      : `${data.candidate_summary.passed_tasks}/${data.candidate_summary.total_tasks}`;
  const manual =
    snapshot.activation_basis === "manual_override" ||
    snapshot.research_readiness === "manual_unverified";
  const executable = !manual && snapshot.execution_readiness !== "blocked";
  return (
    <>
      <section
        className="ov-band ov-v6-brief"
        aria-label={translate(locale, "overview.OverviewHeaderBand.marketSummary")}
      >
        <div className="ov-v6-hero-number">
          <span>{translate(locale, "overview.OverviewPulse.uptrendShare")}</span>
          <div>
            <strong>{up ? (up.fraction * 100).toFixed(1) + "%" : "—"}</strong>
            <small
              style={{
                color:
                  delta == null
                    ? undefined
                    : delta < 0
                      ? "var(--color-down)"
                      : "var(--color-up)",
              }}
            >
              {delta == null
                ? translate(locale, "overview.SectorMarketMap.insufficientHistory")
                : `${delta > 0 ? "+" : ""}${(delta * 100).toFixed(1)}pp`}
            </small>
          </div>
          <p>
            <span className="ov-tone--up">
              {e?.advancers ?? "—"}{" "}
              {translate(locale, "overview.OverviewHeaderBand.up")}
            </span>{" "}
            ;{" "}
            <span className="ov-tone--down">
              {e?.decliners ?? "—"}{" "}
              {translate(locale, "overview.OverviewHeaderBand.down")}
            </span>{" "}
            ; {e?.unchanged ?? "—"}{" "}
            {translate(locale, "overview.OverviewPulse.unchanged")}
          </p>
          <span className="ov-breadth-bar" aria-hidden="true">
            <i className="ov-breadth-bar__up" style={{ flexGrow: e?.advancers ?? 0 }} />
            <i
              className="ov-breadth-bar__down"
              style={{ flexGrow: e?.decliners ?? 0 }}
            />
          </span>
        </div>
        <div className="ov-v6-story">
          <header>
            <h1
              aria-label={translate(locale, "overview.OverviewPage.marketOverview")}
              className="overview-page__title"
            >
              {translate(
                locale,
                "overview.OverviewHeaderBand.marketOverviewTodaySBriefing",
              )}
            </h1>
            <DataStatusBanner
              status={servingStatus}
              lastSuccessfulAt={formatUsMarketTime(snapshot.as_of, locale)}
              timestampLabel={translate(
                locale,
                "overview.OverviewHeaderBand.selectedSnapshotMarketCutoff",
              )}
            />
          </header>
          <dl>
            <div>
              <dt>{translate(locale, "overview.OverviewHeaderBand.marketContext")}</dt>
              <dd>
                {translate(locale, "overview.brief.marketContext", {
                  insight: buildOverviewInsightTitle(data, locale),
                  facts: buildOverviewFactSummary(data, locale),
                })}
              </dd>
            </div>
            <div>
              <dt>{translate(locale, "overview.OverviewHeaderBand.sectorDrivers")}</dt>
              <dd>
                {translate(locale, "overview.brief.sectorDrivers", {
                  leaders: e?.sector_scorecards.length
                    ? [...e.sector_scorecards]
                        .sort(
                          (a, b) =>
                            (b.median_session_return ?? -Infinity) -
                            (a.median_session_return ?? -Infinity),
                        )
                        .slice(0, 3)
                        .map(
                          (s) =>
                            `${sectorLabel(s.sector, locale)} ${formatReturn(s.median_session_return ?? null, locale)}`,
                        )
                        .join(locale === "en-US" ? ", " : "、")
                    : translate(
                        locale,
                        "overview.OverviewHeaderBand.insufficientSectorEvidence",
                      ),
                  breadth: (data.market_breadth * 100).toFixed(1),
                  change:
                    breadthChange(data) == null
                      ? translate(
                          locale,
                          "overview.SectorMarketMap.insufficientHistory",
                        )
                      : `${(breadthChange(data)! * 100).toFixed(1)}pp`,
                })}
              </dd>
            </div>
            <div>
              <dt>{translate(locale, "overview.OverviewHeaderBand.unusualMoves")}</dt>
              <dd>
                {translate(locale, "overview.brief.unusualMoves", {
                  up: e?.breakout_up ?? "—",
                  down: e?.breakout_down ?? "—",
                  volume: e?.volume_anomalies ?? "—",
                  regime: e?.regime_changes ?? "—",
                })}
              </dd>
            </div>
          </dl>
          <small>
            {isHistorical
              ? translate(locale, "overview.OverviewHeaderBand.historicalSnapshot")
              : translate(locale, "overview.OverviewHeaderBand.selectedSnapshot")}{" "}
            ; {formatUsMarketTime(snapshot.as_of, locale)}{" "}
            {translate(
              locale,
              "overview.OverviewHeaderBand.generatedFromCurrentSnapshotFiguresAndRules",
            )}
          </small>
        </div>
        <div
          className="ov-readiness ov-v6-readiness"
          role="status"
          aria-label={
            executable
              ? translate(
                  locale,
                  "overview.OverviewHeaderBand.formalForecastsExcludeInstrumentsResearchRankingModelTaskCoverage",
                  {
                    abstainCount: data.abstain_count,
                    count: research.length,
                    coverage: coverage,
                  },
                )
              : translate(locale, "overview.brief.blockedReadiness", {
                  scenarios: data.top_opportunities.length,
                  research: research.length,
                  coverage,
                })
          }
        >
          <div>
            {translate(
              locale,
              manual
                ? "overview.manualScenarios"
                : executable
                  ? "overview.OverviewHeaderBand.formalExecutableOpportunities"
                  : "overview.blockedScenarios",
            )}{" "}
            <strong>{data.top_opportunities.length}</strong>
            {!executable && (
              <strong>{translate(locale, "overview.executionBlocked")}</strong>
            )}
          </div>
          <div>
            {translate(locale, "overview.OverviewPage.researchRanking")}{" "}
            {research.length} {translate(locale, "overview.OverviewHeaderBand.rows")}{" "}
            <strong>
              {translate(locale, "overview.OverviewHeaderBand.nonExecutable")}
            </strong>
          </div>
          <div
            title={translate(
              locale,
              "overview.OverviewHeaderBand.modelTaskCoverageNotAccuracy",
            )}
          >
            {translate(locale, "overview.OverviewHeaderBand.modelTaskCoverage")}{" "}
            <strong>{coverage}</strong>
          </div>
          <Link to={gateHref}>
            {translate(locale, "overview.OverviewHeaderBand.modelGateDetails")}
          </Link>
          {snapshot.prediction_readiness === "data_only" ? (
            <small>
              {translate(
                locale,
                "overview.OverviewHeaderBand.formalForecastsAwaitACompleteChampionModelInstrumentsWithout",
              )}
              {data.abstain_count}{" "}
              {translate(locale, "overview.SectorMarketMap.instruments")}
            </small>
          ) : null}
        </div>
      </section>
      <div
        className="option-header-cards"
        aria-label={translate(locale, "overview.OverviewHeaderBand.optionsSentiment")}
      >
        {["US.SPY", "US.QQQ"].map((symbol) => {
          const item = options?.items.find((row) => row.symbol === symbol);
          const v = item?.analytics?.values;
          return (
            <Link
              key={symbol}
              className="option-sentiment-card"
              to={`${proxyHref(symbol)}#options`}
            >
              <strong>
                {symbol.replace(/^US\./, "")}{" "}
                {translate(locale, "overview.OverviewHeaderBand.optionsSentiment2")}
              </strong>
              {item?.maturity !== "none" && v ? (
                <>
                  <p>
                    ATM IV30 {optionPercent(v.atm_iv_30d, undefined, locale)} ; IV−RV20{" "}
                    {optionPoints(v.iv_minus_realized_20sessions, locale)}{" "}
                    {v.iv_minus_realized_20sessions == null
                      ? ""
                      : v.iv_minus_realized_20sessions > 0
                        ? translate(
                            locale,
                            "overview.OverviewHeaderBand.relativelyExpensiveOptions",
                          )
                        : v.iv_minus_realized_20sessions < 0
                          ? translate(
                              locale,
                              "overview.OverviewHeaderBand.relativelyCheaperOptions",
                            )
                          : translate(locale, "overview.OverviewHeaderBand.unchanged")}
                  </p>
                  <p>
                    P/C OI {optionNumber(v.put_call_oi, locale)} ;{" "}
                    {item?.term_structure === "upward"
                      ? translate(
                          locale,
                          "overview.OverviewHeaderBand.normalUpwardCurve",
                        )
                      : item?.term_structure === "inverted"
                        ? translate(locale, "options.optionsBoardRows.inverted")
                        : item?.term_structure === "flat"
                          ? translate(locale, "options.optionsBoardRows.flat")
                          : translate(
                              locale,
                              "overview.OverviewHeaderBand.termStructureUnconfirmed",
                            )}
                  </p>
                  <small>
                    {item?.market_as_of
                      ? formatUsMarketTime(item.market_as_of, locale)
                      : translate(
                          locale,
                          "overview.OverviewHeaderBand.marketTimeUnconfirmed",
                        )}{" "}
                    {translate(locale, "overview.OverviewHeaderBand.coverage")}{" "}
                    {optionPercent(v.contract_coverage_ratio, 0, locale)}
                  </small>
                </>
              ) : (
                <p>
                  {optionsError
                    ? translate(
                        locale,
                        "overview.OverviewHeaderBand.optionsRequestFailed",
                      )
                    : optionsPending
                      ? translate(
                          locale,
                          "overview.OverviewHeaderBand.loadingOptionsSnapshot",
                        )
                      : translate(
                          locale,
                          "options.optionsBoardRows.noSavedOptionsSnapshot",
                        )}
                </p>
              )}
            </Link>
          );
        })}
      </div>
      {optionsError ? (
        <Link to={optionsHref}>
          {translate(locale, "overview.OverviewHeaderBand.openOptionsPulseToRetry")}
        </Link>
      ) : null}
    </>
  );
}
