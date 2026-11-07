import { instrumentName } from "../../i18n/instruments";
import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { assetLabel, sectorLabel, servingStatusLabel } from "../../app/presentation";
import type { components } from "../../api/generated/schema";
import type { ScreenUrlState } from "../../app/url-state";
import { ProbabilityValue } from "../../components/ProbabilityValue";
import { rangeExitUnavailableLabel } from "../../components/range-exit-applicability";
import { ResearchBoundaryLabel } from "../../components/ResearchBoundaryLabel";
import {
  formatResearchPrice,
  futureTradingDayCloseLabel,
  futureTradingDayWindowLabel,
  researchPriceUnavailableLabel,
} from "../../components/ResearchPriceProjection";
import { ReturnValue } from "../../components/ReturnValue";
import { regimeLabel } from "../overview/RegimeDistribution";
import { displayDataTime, displaySymbol, screenResearchHref } from "./screen-url-state";
import {
  ScreenOptionHeaders,
  ScreenOptionCells,
  type ScreenOptionColumnsProps,
} from "./ScreenOptionColumns";
type ScreenRow = components["schemas"]["ScreenRow"];
type ResearchModelEvidence = components["schemas"]["ResearchModelEvidence"];
interface ResearchScreenResultsProps extends Partial<ScreenOptionColumnsProps> {
  isMobile?: boolean;
  mode: "verified" | "challenger";
  onPageChange: (page: number) => void;
  page: components["schemas"]["ScreenPage"];
  rows: readonly ScreenRow[];
  state: ScreenUrlState;
}
const TASK_LABELS = (locale: Locale = "zh-CN"): Record<string, string> => ({
  breakout: translate(locale, "screen.researchscreenresults.breakoutForecast"),
  forecast_direction: translate(
    locale,
    "screen.researchscreenresults.directionForecast",
  ),
  mae: translate(locale, "screen.researchscreenresults.maximumAdverseExcursion"),
  mfe: translate(locale, "screen.researchscreenresults.maximumFavorableExcursion"),
  range_exit: translate(locale, "screen.researchscreenresults.rangeExit"),
  return_fwd: translate(locale, "screen.researchscreenresults.returnDistribution"),
});
function relativeReturn(
  value: number | null | undefined,
  reference: number | undefined,
) {
  if (
    value == null ||
    reference == null ||
    !Number.isFinite(value) ||
    !Number.isFinite(reference) ||
    reference <= 0
  ) {
    return null;
  }
  return value / reference - 1;
}
function intervalWidth(
  band: components["schemas"]["PriceBand"] | null | undefined,
  reference: number | undefined,
) {
  if (
    band == null ||
    reference == null ||
    !Number.isFinite(reference) ||
    reference <= 0 ||
    ![band.q10, band.q50, band.q90].every(Number.isFinite) ||
    band.q10 > band.q50 ||
    band.q50 > band.q90
  ) {
    return null;
  }
  return (band.q90 - band.q10) / reference;
}
function ModelVersionCopy({ version }: { version: string }) {
  const { locale } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <button
      aria-label={translate(locale, "screen.researchscreenresults.copyModelVersion", {
        version: version,
      })}
      className="research-task-disclosure__copy"
      onClick={() => {
        void navigator.clipboard
          ?.writeText(version)
          .then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          })
          .catch(() => setCopied(false));
      }}
      title={
        copied
          ? translate(locale, "screen.researchscreenresults.copied")
          : translate(locale, "screen.researchscreenresults.copyModelVersion2")
      }
      type="button"
    >
      {copied ? (
        <Check aria-hidden="true" size={14} strokeWidth={1.5} />
      ) : (
        <Copy aria-hidden="true" size={14} strokeWidth={1.5} />
      )}
    </button>
  );
}
function TaskStatusDisclosure({
  evidence,
  mode,
}: {
  evidence: ResearchModelEvidence | null | undefined;
  mode: "verified" | "challenger";
}) {
  const { locale } = useI18n();
  if (evidence == null) {
    return (
      <span className="research-task-disclosure__empty">
        {mode === "verified"
          ? translate(
              locale,
              "screen.researchscreenresults.noVerifiedTasksAtThisHorizon",
            )
          : translate(
              locale,
              "screen.researchscreenresults.noFailedChallengerTasksAtThisHorizon",
            )}
      </span>
    );
  }
  const passed = evidence.tasks.filter((task) => task.gate_passed).length;
  const total = evidence.tasks.length;
  return (
    <details className="research-task-disclosure">
      <summary>
        <strong>
          {translate(
            locale,
            "screen.researchscreenresults.modelTaskCoverage.withValues",
            { passed: passed, total: total },
          )}
        </strong>
        <span>
          {translate(locale, "screen.researchscreenresults.viewModelDetails")}
        </span>
      </summary>
      <ul>
        {evidence.tasks.map((task) => (
          <li key={`${task.task}-${task.horizon}-${task.model_version}`}>
            <span>
              <strong>
                {TASK_LABELS(locale)[task.task] ??
                  translate(locale, "screen.researchscreenresults.unknownTask")}
              </strong>
              <small>
                {task.gate_passed
                  ? translate(locale, "screen.researchscreenresults.passed")
                  : translate(locale, "screen.researchscreenresults.failed")}
              </small>
            </span>
            <code title={task.model_version}>{task.model_version}</code>
            <ModelVersionCopy version={task.model_version} />
          </li>
        ))}
      </ul>
    </details>
  );
}
export function ResearchScreenResults({
  isMobile = false,
  mode,
  onPageChange,
  page,
  rows,
  state,
  optionItems = [],
  optionState = {
    onlyData: false,
    widthBelow: undefined,
    widthAbove: undefined,
    ivRvMinimum: undefined,
    sort: undefined,
    direction: "desc",
  },
  onOptionSort = () => {},
}: ResearchScreenResultsProps) {
  const { locale } = useI18n();
  const [expandedColumns, setExpandedColumns] = useState(false);
  const [showOptions, setShowOptions] = useState(false);
  const location = useLocation();
  const modelSort =
    state.sort.key === "verified_q10" || state.sort.key === "verified_q50";
  const modelColumns = mode === "challenger" || expandedColumns || modelSort;
  const coverage = rows.filter((row) => row.verified_research != null).length;
  if (isMobile) {
    return (
      <ResearchScreenMobileResults
        mode={mode}
        onPageChange={onPageChange}
        page={page}
        rows={rows}
        state={state}
      />
    );
  }
  return (
    <section
      className={`screen-results research-screen-results ${modelColumns ? "" : "research-screen-results--rules"}`}
    >
      <div className="research-screen-results__boundary" role="note">
        {coverage > 0 || mode === "challenger" ? (
          <ResearchBoundaryLabel
            scope={mode === "challenger" ? "challenger" : "verified"}
          />
        ) : (
          <strong>
            {translate(locale, "screen.researchscreenresults.marketAndRuleResearch")}
          </strong>
        )}
        <span>
          {translate(
            locale,
            "screen.researchscreenresults.filteredInstruments.withValues",
            {
              total: page.total,
              rowCount: rows.filter((row) => row.rule_evidence != null).length,
              rowCount2: rows.length,
              horizon: state.horizon,
              coverage: coverage,
              rowCount3: rows.length,
            },
          )}
        </span>
      </div>
      {mode === "verified" ? (
        <button
          className="research-column-toggle"
          type="button"
          aria-pressed={modelColumns}
          disabled={modelSort}
          title={
            modelSort
              ? translate(
                  locale,
                  "screen.researchscreenresults.switchToARuleFieldSortToCollapseModel",
                )
              : undefined
          }
          onClick={() => setExpandedColumns(!expandedColumns)}
        >
          {modelSort
            ? translate(
                locale,
                "screen.researchscreenresults.sortingByAModelFieldForecastColumnsAreExpanded",
              )
            : expandedColumns
              ? translate(
                  locale,
                  "screen.researchscreenresults.collapseModelForecastColumns",
                )
              : translate(
                  locale,
                  "screen.researchscreenresults.expandModelForecastColumns",
                )}
        </button>
      ) : null}
      {mode === "challenger" ? (
        <div className="challenger-warning" role="alert">
          {translate(
            locale,
            "screen.researchscreenresults.failedQualityGatesExperimentalResearchOnlyUpProbabilityDescribes",
          )}
        </div>
      ) : null}
      <details className="screen-column-picker">
        <summary>
          {translate(locale, "screen.researchscreenresults.visibleColumns")}
        </summary>
        <label>
          <input
            type="checkbox"
            checked={showOptions}
            onChange={(event) => setShowOptions(event.target.checked)}
          />
          {translate(locale, "screen.researchscreenresults.options6Columns")}
        </label>
      </details>
      <div className="screen-table-scroll">
        <table
          aria-label={
            mode === "verified"
              ? translate(
                  locale,
                  "screen.researchscreenresults.verifiedResearchResultsNotExecutable",
                )
              : translate(
                  locale,
                  "screen.researchscreenresults.experimentalChallengerResultsNotExecutable",
                )
          }
          className="screen-table research-screen-results__table"
        >
          <thead>
            <tr>
              <th
                className="screen-table__sticky"
                scope="col"
                aria-sort={
                  state.sort.key === "symbol"
                    ? state.sort.direction === "asc"
                      ? "ascending"
                      : "descending"
                    : undefined
                }
              >
                {translate(locale, "screen.researchscreenresults.instrument")}
              </th>
              <th scope="col">
                {translate(locale, "screen.researchscreenresults.status")}
              </th>
              <th
                scope="col"
                aria-sort={
                  state.sort.key === "rule_return_5" ||
                  state.sort.key === "rule_return_20"
                    ? state.sort.direction === "asc"
                      ? "ascending"
                      : "descending"
                    : undefined
                }
              >
                {translate(
                  locale,
                  "screen.researchscreenresults.1520DayHistoricalReturns",
                )}
              </th>
              <th
                scope="col"
                aria-sort={
                  state.sort.key === "rule_atr_pct_20"
                    ? state.sort.direction === "asc"
                      ? "ascending"
                      : "descending"
                    : undefined
                }
              >
                {translate(locale, "screen.researchscreenresults.atrClose20Day")}
              </th>
              <th
                scope="col"
                aria-sort={
                  state.sort.key === "rule_volume_z_20"
                    ? state.sort.direction === "asc"
                      ? "ascending"
                      : "descending"
                    : undefined
                }
              >
                {translate(
                  locale,
                  "screen.researchscreenresults.volumeZ20RuleBreakout",
                )}
              </th>
              <th
                scope="col"
                aria-sort={
                  state.sort.key === "rule_channel_position_20"
                    ? state.sort.direction === "asc"
                      ? "ascending"
                      : "descending"
                    : undefined
                }
              >
                {translate(locale, "screen.researchscreenresults.channelPosition")}
              </th>
              <th scope="col">
                {translate(locale, "screen.researchscreenresults.dataStatus")}
              </th>
              {modelColumns ? (
                <>
                  <th
                    scope="col"
                    aria-sort={
                      state.sort.key === "verified_q50"
                        ? state.sort.direction === "asc"
                          ? "ascending"
                          : "descending"
                        : undefined
                    }
                  >
                    {mode === "challenger"
                      ? translate(
                          locale,
                          "screen.researchscreenresults.dayChallengerUpProbability",
                          { horizon: state.horizon },
                        )
                      : translate(
                          locale,
                          "screen.researchscreenresults.q50ForecastReturn",
                          {
                            futureTradingDayClose: futureTradingDayCloseLabel(
                              state.horizon,
                              locale,
                            ),
                          },
                        )}
                  </th>
                  <th
                    scope="col"
                    aria-sort={
                      state.sort.key === "verified_q10"
                        ? state.sort.direction === "asc"
                          ? "ascending"
                          : "descending"
                        : undefined
                    }
                  >
                    {mode === "challenger"
                      ? translate(
                          locale,
                          "screen.researchscreenresults.dayChallengerUpwardBreakoutProbability",
                          { horizon: state.horizon },
                        )
                      : translate(
                          locale,
                          "screen.researchscreenresults.conditionalQ10NotAStopLossIntervalWidth",
                        )}
                  </th>
                  <th scope="col">
                    {mode === "challenger"
                      ? translate(
                          locale,
                          "screen.researchscreenresults.dayChallengerDownwardBreakoutProbability",
                          { horizon: state.horizon },
                        )
                      : translate(
                          locale,
                          "screen.researchscreenresults.possibleHighLow",
                          {
                            futureTradingDayWindow: futureTradingDayWindowLabel(
                              state.horizon,
                              locale,
                            ),
                          },
                        )}
                  </th>
                  <th scope="col">
                    {mode === "challenger"
                      ? translate(
                          locale,
                          "screen.researchscreenresults.dayChallengerRangeExitRangeBoundOnly",
                          { horizon: state.horizon },
                        )
                      : translate(
                          locale,
                          "screen.researchscreenresults.breakoutProbabilitiesOverTheNextTradingDaysUpDown",
                          { horizon: state.horizon },
                        )}
                  </th>
                  <th scope="col">
                    {translate(
                      locale,
                      "screen.researchscreenresults.modelTaskCoverageNotAccuracy",
                    )}
                  </th>
                </>
              ) : null}
              <th scope="col">
                {translate(locale, "screen.researchscreenresults.marketDataAsOf")}
              </th>
              {showOptions && (
                <ScreenOptionHeaders
                  optionState={optionState}
                  onOptionSort={onOptionSort}
                />
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const evidence =
                mode === "verified" ? row.verified_research : row.challenger_preview;
              const values = evidence?.values;
              const projection = evidence?.price_projection;
              const priceBand = projection?.endpoint_price_band;
              const projectionCurrency = projection?.currency ?? "USD";
              const q50Return = relativeReturn(
                priceBand?.q50,
                projection?.reference_price,
              );
              const q10Return = relativeReturn(
                priceBand?.q10,
                projection?.reference_price,
              );
              const bandWidth = intervalWidth(priceBand, projection?.reference_price);
              return (
                <tr key={`${mode}-${row.symbol}-${row.horizon}`}>
                  <th className="screen-table__sticky" scope="row">
                    <Link
                      state={{ returnTo: `${location.pathname}${location.search}` }}
                      to={screenResearchHref(row, state)}
                    >
                      {displaySymbol(row.symbol)}
                    </Link>
                    <span>{instrumentName(row.symbol, row.name, locale)}</span>
                    <small>
                      {sectorLabel(row.sector, locale)};{" "}
                      {assetLabel(row.asset_type, locale)}
                    </small>
                  </th>
                  <td>{regimeLabel(row.regime, row.rule_evidence, locale)}</td>
                  <td className="research-screen-results__numeric">
                    <ReturnValue value={row.rule_evidence?.session_return ?? null} />
                    {" / "}
                    <ReturnValue value={row.rule_evidence?.return_5 ?? null} />
                    {" / "}
                    <ReturnValue value={row.rule_evidence?.return_20 ?? null} />
                  </td>
                  <td>
                    <ReturnValue value={row.rule_evidence?.atr_pct_20 ?? null} />
                  </td>
                  <td>
                    {row.rule_evidence?.volume_z_20 == null
                      ? "—"
                      : row.rule_evidence.volume_z_20.toFixed(2)}
                    <small>
                      {row.rule_evidence?.breakout_up_rule
                        ? translate(
                            locale,
                            "screen.researchscreenresults.upwardBreakout",
                          )
                        : row.rule_evidence?.breakout_down_rule
                          ? translate(
                              locale,
                              "screen.researchscreenresults.downwardBreakout",
                            )
                          : translate(
                              locale,
                              "screen.researchscreenresults.noRuleBreakout",
                            )}
                    </small>
                  </td>
                  <td>
                    {row.rule_evidence?.channel_position_20 == null
                      ? "—"
                      : `${(row.rule_evidence.channel_position_20 * 100).toFixed(1)}%`}
                  </td>
                  <td>
                    {row.rule_evidence == null
                      ? translate(
                          locale,
                          "screen.researchscreenresults.ruleDataMissing",
                        )
                      : translate(
                          locale,
                          "screen.researchscreenresults.ruleDataAvailable",
                        )}
                    <small>
                      {servingStatusLabel(row.serving_status, locale)} ;{" "}
                      {row.rule_evidence?.intraday_available
                        ? translate(
                            locale,
                            "screen.researchscreenresults.includesIntradayFeatures",
                          )
                        : translate(
                            locale,
                            "screen.researchscreenresults.intradayFeaturesUnavailable",
                          )}
                    </small>
                  </td>
                  {modelColumns ? (
                    <>
                      <td>
                        {mode === "challenger" ? (
                          <ProbabilityValue
                            value={values?.forecast_up_probability ?? null}
                          />
                        ) : (
                          <div className="research-comparable-metric">
                            <ReturnValue value={q50Return} />
                            <small className="numeric-value">
                              {priceBand == null
                                ? researchPriceUnavailableLabel(
                                    projection?.endpoint_price_unavailable_reason,
                                    row.horizon,
                                    locale,
                                  )
                                : formatResearchPrice(
                                    priceBand.q50,
                                    projectionCurrency,
                                    locale,
                                  )}
                            </small>
                          </div>
                        )}
                      </td>
                      <td className="research-screen-results__numeric">
                        {mode === "challenger" ? (
                          <ProbabilityValue
                            value={values?.breakout_up_probability ?? null}
                          />
                        ) : (
                          <div className="research-comparable-metric">
                            <span>
                              <ReturnValue value={q10Return} />
                              <small>
                                {translate(
                                  locale,
                                  "screen.researchscreenresults.intervalWidth.withValues",
                                  {
                                    value:
                                      bandWidth == null
                                        ? "—"
                                        : `${(bandWidth * 100).toFixed(1)}%`,
                                  },
                                )}
                              </small>
                            </span>
                            <small className="numeric-value">
                              {priceBand == null
                                ? researchPriceUnavailableLabel(
                                    projection?.endpoint_price_unavailable_reason,
                                    row.horizon,
                                    locale,
                                  )
                                : `${formatResearchPrice(priceBand.q10, projectionCurrency, locale)} – ${formatResearchPrice(priceBand.q90, projectionCurrency, locale)}`}
                            </small>
                          </div>
                        )}
                      </td>
                      <td>
                        {mode === "challenger" ? (
                          <ProbabilityValue
                            value={values?.breakout_down_probability ?? null}
                          />
                        ) : (
                          <span className="numeric-value">
                            {projection == null
                              ? researchPriceUnavailableLabel(null, row.horizon, locale)
                              : `${formatResearchPrice(projection.stage_high_price_q50, projection.currency, locale)} / ${
                                  projection.stage_low_price_q50 == null
                                    ? researchPriceUnavailableLabel(
                                        projection.stage_low_price_unavailable_reason,
                                        row.horizon,
                                        locale,
                                      )
                                    : formatResearchPrice(
                                        projection.stage_low_price_q50,
                                        projection.currency,
                                        locale,
                                      )
                                }`}
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
                          <>
                            <ProbabilityValue
                              value={values?.breakout_up_probability ?? null}
                            />
                            {" / "}
                            <ProbabilityValue
                              value={values?.breakout_down_probability ?? null}
                            />
                          </>
                        )}
                      </td>
                      <td className="research-screen-results__tasks">
                        <TaskStatusDisclosure evidence={evidence} mode={mode} />
                      </td>
                    </>
                  ) : null}
                  <td>{displayDataTime(row.as_of, locale)}</td>
                  {showOptions && (
                    <ScreenOptionCells symbol={row.symbol} optionItems={optionItems} />
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <ResearchResultsPagination onPageChange={onPageChange} page={page} />
    </section>
  );
}
function ResearchScreenMobileResults({
  mode,
  onPageChange,
  page,
  rows,
  state,
}: Omit<ResearchScreenResultsProps, "isMobile">) {
  const { locale } = useI18n();
  return (
    <section className="screen-results research-screen-results research-screen-results--mobile">
      <div className="research-screen-results__boundary" role="note">
        <ResearchBoundaryLabel
          scope={mode === "challenger" ? "challenger" : "verified"}
        />
        <span>
          {translate(
            locale,
            "screen.researchscreenresults.q10IsNeitherMaximumLossNorAStopLoss",
          )}
        </span>
      </div>
      {mode === "challenger" ? (
        <div className="challenger-warning" role="alert">
          {translate(
            locale,
            "screen.researchscreenresults.failedQualityGatesExperimentalResearchOnlyDoNotUse",
          )}
        </div>
      ) : null}
      <ul
        aria-label={
          mode === "verified"
            ? translate(
                locale,
                "screen.researchscreenresults.mobileVerifiedResearchPrices",
              )
            : translate(locale, "screen.researchscreenresults.mobileChallengerPreview")
        }
        className="research-screen-mobile-list"
      >
        {rows.map((row) => {
          const evidence =
            mode === "verified" ? row.verified_research : row.challenger_preview;
          const values = evidence?.values;
          const projection = evidence?.price_projection;
          const priceBand = projection?.endpoint_price_band;
          const currency = projection?.currency ?? "USD";
          const q50Return = relativeReturn(priceBand?.q50, projection?.reference_price);
          const q10Return = relativeReturn(priceBand?.q10, projection?.reference_price);
          const bandWidth = intervalWidth(priceBand, projection?.reference_price);
          return (
            <li key={`${mode}-mobile-${row.symbol}-${row.horizon}`}>
              <article>
                <header>
                  <Link to={screenResearchHref(row, state)}>
                    <strong>{displaySymbol(row.symbol)}</strong>
                    <span>{instrumentName(row.symbol, row.name, locale)}</span>
                  </Link>
                  <span>{regimeLabel(row.regime, row.rule_evidence, locale)}</span>
                </header>
                <ResearchBoundaryLabel
                  scope={mode === "challenger" ? "challenger" : "verified"}
                />
                {mode === "verified" ? (
                  <>
                    <p className="research-screen-mobile-list__target">
                      <span>
                        {translate(
                          locale,
                          "screen.researchscreenresults.q50ForecastReturn2.withValues",
                          {
                            futureTradingDayClose: futureTradingDayCloseLabel(
                              row.horizon,
                              locale,
                            ),
                          },
                        )}
                      </span>
                      <strong>
                        <ReturnValue value={q50Return} />
                      </strong>
                      <small>
                        {translate(
                          locale,
                          "screen.researchscreenresults.modelMedianScenarioPriceQ50.withValues",
                          {
                            researchPriceUnavailable:
                              priceBand == null
                                ? researchPriceUnavailableLabel(
                                    projection?.endpoint_price_unavailable_reason,
                                    row.horizon,
                                    locale,
                                  )
                                : `${formatResearchPrice(priceBand.q50, currency, locale)}; ${formatResearchPrice(priceBand.q10, currency, locale)}–${formatResearchPrice(priceBand.q90, currency, locale)}`,
                          },
                        )}
                      </small>
                    </p>
                    <p>
                      {translate(
                        locale,
                        "screen.researchscreenresults.downsideQ10ScenarioNotAStopLoss",
                      )}
                      <ReturnValue value={q10Return} />
                    </p>
                    <details>
                      <summary>
                        {translate(
                          locale,
                          "screen.researchscreenresults.viewIntervalsAndPathMetrics",
                        )}
                      </summary>
                      <dl>
                        <div>
                          <dt>
                            {translate(
                              locale,
                              "screen.researchscreenresults.conditionalQ10NotAStopLoss",
                            )}
                          </dt>
                          <dd>
                            <ReturnValue value={q10Return} />
                          </dd>
                        </div>
                        <div>
                          <dt>
                            {translate(
                              locale,
                              "screen.researchscreenresults.intervalWidth",
                            )}
                          </dt>
                          <dd>
                            {bandWidth == null
                              ? "—"
                              : `${(bandWidth * 100).toFixed(1)}%`}
                          </dd>
                        </div>
                        <div>
                          <dt>
                            {translate(
                              locale,
                              "screen.researchscreenresults.possiblePeriodHighMfeQ50.withValues",
                              {
                                futureTradingDayWindow: futureTradingDayWindowLabel(
                                  row.horizon,
                                  locale,
                                ),
                              },
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
                          </dd>
                        </div>
                        <div>
                          <dt>
                            {translate(
                              locale,
                              "screen.researchscreenresults.possiblePeriodLowMaeQ50.withValues",
                              {
                                futureTradingDayWindow: futureTradingDayWindowLabel(
                                  row.horizon,
                                  locale,
                                ),
                              },
                            )}
                          </dt>
                          <dd>
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
                              "screen.researchscreenresults.next.withValues",
                              { horizon: row.horizon },
                            )}
                          </dt>
                          <dd>
                            <ProbabilityValue
                              value={values?.breakout_up_probability ?? null}
                            />
                            {" / "}
                            <ProbabilityValue
                              value={values?.breakout_down_probability ?? null}
                            />
                          </dd>
                        </div>
                      </dl>
                    </details>
                  </>
                ) : (
                  <dl>
                    <div>
                      <dt>
                        {translate(
                          locale,
                          "screen.researchscreenresults.dayChallengerUpProbability2.withValues",
                          { horizon: row.horizon },
                        )}
                      </dt>
                      <dd>
                        <ProbabilityValue
                          value={values?.forecast_up_probability ?? null}
                        />
                      </dd>
                    </div>
                    <div>
                      <dt>
                        {translate(
                          locale,
                          "screen.researchscreenresults.upwardDownwardBreakoutProbabilities",
                        )}
                      </dt>
                      <dd>
                        <ProbabilityValue
                          value={values?.breakout_up_probability ?? null}
                        />
                        {" / "}
                        <ProbabilityValue
                          value={values?.breakout_down_probability ?? null}
                        />
                      </dd>
                    </div>
                    <div>
                      <dt>
                        {translate(
                          locale,
                          "screen.researchscreenresults.rangeExitRangeBoundOnly",
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
                          value={values?.range_exit_probability ?? null}
                        />
                      </dd>
                    </div>
                  </dl>
                )}
                <TaskStatusDisclosure evidence={evidence} mode={mode} />
                <div className="research-screen-mobile-list__time">
                  <time dateTime={row.as_of}>
                    {translate(
                      locale,
                      "screen.researchscreenresults.dataAsOf.withValues",
                      { displayDataTime: displayDataTime(row.as_of, locale) },
                    )}
                  </time>
                </div>
              </article>
            </li>
          );
        })}
      </ul>
      <ResearchResultsPagination onPageChange={onPageChange} page={page} />
    </section>
  );
}
function ResearchResultsPagination({
  onPageChange,
  page,
}: {
  onPageChange: (page: number) => void;
  page: components["schemas"]["ScreenPage"];
}) {
  const { locale } = useI18n();
  const totalPages = Math.max(1, Math.ceil(page.total / page.page_size));
  return (
    <nav
      aria-label={translate(locale, "screen.researchscreenresults.screenerPagination")}
      className="screen-pagination"
    >
      <button
        disabled={page.page <= 1}
        onClick={() => onPageChange(page.page - 1)}
        type="button"
      >
        {translate(locale, "screen.researchscreenresults.previous")}
      </button>
      <span>
        {translate(locale, "screen.researchscreenresults.day.withValues", {
          page: page.page,
          totalPages: totalPages,
        })}
      </span>
      <button
        disabled={page.page >= totalPages}
        onClick={() => onPageChange(page.page + 1)}
        type="button"
      >
        {translate(locale, "screen.researchscreenresults.next2")}
      </button>
    </nav>
  );
}
