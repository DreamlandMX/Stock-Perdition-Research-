import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { Fragment } from "react";
import type { components } from "../../api/generated/schema";
import { formatUsMarketTime, reasonLabel, taskLabel } from "../../app/presentation";
import { rangeExitUnavailableLabel } from "../../components/range-exit-applicability";
import { ResearchBoundaryLabel } from "../../components/ResearchBoundaryLabel";
import { formatResearchPrice } from "../../components/ResearchPriceProjection";
import { rejectionReasonLabel } from "../../app/presentation";
import "./evidence-ledger.css";
type ResearchModelEvidence = components["schemas"]["ResearchModelEvidence"];
type ResearchModelValues = components["schemas"]["ResearchModelValues"];
type PriceBand = components["schemas"]["PriceBand"];
type RegimeLabel = components["schemas"]["RegimeLabel"];
type TaskEvidence = components["schemas"]["TaskForecastEvidence"];
const HORIZONS = [1, 5, 20] as const;
function percent(value: number | null | undefined, digits = 1) {
  if (value == null || !Number.isFinite(value)) return null;
  return `${(value * 100).toFixed(digits)}%`;
}
function signedPercent(value: number | null | undefined, digits = 2) {
  if (value == null || !Number.isFinite(value)) return null;
  const scaled = value * 100;
  const sign = scaled > 0 ? "+" : scaled < 0 ? "−" : "";
  return `${sign}${Math.abs(scaled).toFixed(digits)}%`;
}
function direction(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value) || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}
function finite(value: number | null | undefined): number | null {
  return value == null || !Number.isFinite(value) ? null : value;
}
function finitePriceBand(band: PriceBand | null | undefined): band is PriceBand {
  return (
    band != null &&
    [band.q10, band.q25, band.q50, band.q75, band.q90].every(Number.isFinite)
  );
}
interface EndpointInterval {
  asOf: string;
  band: PriceBand;
  currency: string;
  horizon: number;
  reference: number;
  values: ResearchModelValues;
}
function DiscreteEndpointIntervals({
  points,
}: {
  points: readonly EndpointInterval[];
}) {
  const { locale } = useI18n();
  return (
    <div className="endpoint-intervals">
      {points.map((point) => {
        const delta = point.reference > 0 ? point.band.q50 / point.reference - 1 : null;
        const lower = finite(point.values.calibrated_lower);
        const upper = finite(point.values.calibrated_upper);
        const calibratedLower =
          lower === null ? null : finite(point.reference * (1 + lower));
        const calibratedUpper =
          upper === null ? null : finite(point.reference * (1 + upper));
        return (
          <section
            aria-label={translate(
              locale,
              "research.verifiedresearchevidence.dayDiscreteEndpointInterval",
              { horizon: point.horizon },
            )}
            className="endpoint-interval"
            key={point.horizon}
          >
            <header>
              <span>
                {translate(
                  locale,
                  "research.verifiedresearchevidence.independentEndpointDistribution",
                )}
              </span>
              <h3>
                {translate(
                  locale,
                  "research.verifiedresearchevidence.tradingDays.withValues",
                  { horizon: point.horizon },
                )}
              </h3>
            </header>
            <dl>
              <div>
                <dt>
                  {translate(locale, "research.verifiedresearchevidence.rawQ10Q90")}
                </dt>
                <dd>{`${formatResearchPrice(point.band.q10, point.currency, locale)} – ${formatResearchPrice(point.band.q90, point.currency, locale)}`}</dd>
              </div>
              <div>
                <dt>
                  {translate(
                    locale,
                    "research.verifiedresearchevidence.calibratedIntervalCqr",
                  )}
                </dt>
                <dd>
                  {calibratedLower === null || calibratedUpper === null
                    ? translate(locale, "research.instrumentidentity.notProvided")
                    : `${formatResearchPrice(calibratedLower, point.currency, locale)} – ${formatResearchPrice(calibratedUpper, point.currency, locale)}`}
                </dd>
              </div>
              <div>
                <dt>q25–q75</dt>
                <dd>{`${formatResearchPrice(point.band.q25, point.currency, locale)} – ${formatResearchPrice(point.band.q75, point.currency, locale)}`}</dd>
              </div>
              <div>
                <dt>
                  {translate(locale, "research.verifiedresearchevidence.q50Endpoint")}
                </dt>
                <dd>
                  {formatResearchPrice(point.band.q50, point.currency, locale)}
                  {delta === null ? null : (
                    <span data-direction={direction(delta)}>
                      {signedPercent(delta)}
                    </span>
                  )}
                </dd>
              </div>
              <div>
                <dt>{translate(locale, "research.optionPulseChart.referencePrice")}</dt>
                <dd>{formatResearchPrice(point.reference, point.currency, locale)}</dd>
              </div>
            </dl>
            <p className="endpoint-interval__note">
              {translate(
                locale,
                "research.verifiedresearchevidence.q50IsAMedianScenarioNotAPriceTarget",
              )}
            </p>
            <time dateTime={point.asOf}>
              {translate(
                locale,
                "research.verifiedresearchevidence.dataAsOf.withValues",
                { asOf: point.asOf.slice(0, 10) },
              )}
            </time>
          </section>
        );
      })}
    </div>
  );
}
type MetricKind = "probability" | "return";
interface MetricRow {
  emphasis?: boolean;
  group?: string;
  key: keyof ResearchModelValues;
  kind: MetricKind;
  label: string;
  unavailableNote: string;
}
const METRIC_ROWS = (locale: Locale = "zh-CN"): readonly MetricRow[] => [
  {
    group: translate(locale, "research.horizonevidence.breakoutProbabilities"),
    key: "breakout_up_probability",
    kind: "probability",
    label: translate(locale, "research.verifiedresearchevidence.firstUpwardBreakout"),
    unavailableNote: translate(
      locale,
      "research.verifiedresearchevidence.upwardBreakoutProbabilityIsNotPublishedForThisSnapshot",
    ),
  },
  {
    key: "breakout_down_probability",
    kind: "probability",
    label: translate(locale, "research.verifiedresearchevidence.firstDownwardBreakout"),
    unavailableNote: translate(
      locale,
      "research.verifiedresearchevidence.downwardBreakoutProbabilityIsNotPublishedForThisSnapshot",
    ),
  },
  {
    group: translate(
      locale,
      "research.verifiedresearchevidence.capitalGainReturnQuantiles",
    ),
    key: "capital_gain_q10",
    kind: "return",
    label: translate(locale, "research.verifiedresearchevidence.returnQ10"),
    unavailableNote: translate(
      locale,
      "research.verifiedresearchevidence.returnQuantilesAreNotPublishedForThisSnapshot",
    ),
  },
  {
    emphasis: true,
    key: "capital_gain_q50",
    kind: "return",
    label: translate(locale, "research.verifiedresearchevidence.returnQ50Median"),
    unavailableNote: translate(
      locale,
      "research.verifiedresearchevidence.medianReturnIsNotPublishedForThisSnapshot",
    ),
  },
  {
    key: "capital_gain_q90",
    kind: "return",
    label: translate(locale, "research.verifiedresearchevidence.returnQ90"),
    unavailableNote: translate(
      locale,
      "research.verifiedresearchevidence.returnQuantilesAreNotPublishedForThisSnapshot",
    ),
  },
  {
    group: translate(locale, "research.verifiedresearchevidence.cqrCalibratedInterval"),
    key: "calibrated_lower",
    kind: "return",
    label: translate(locale, "research.verifiedresearchevidence.calibratedLowerBound"),
    unavailableNote: translate(
      locale,
      "research.verifiedresearchevidence.calibratedIntervalsAreNotPublishedForThisSnapshot",
    ),
  },
  {
    key: "calibrated_upper",
    kind: "return",
    label: translate(locale, "research.verifiedresearchevidence.calibratedUpperBound"),
    unavailableNote: translate(
      locale,
      "research.verifiedresearchevidence.calibratedIntervalsAreNotPublishedForThisSnapshot",
    ),
  },
  {
    group: translate(
      locale,
      "research.verifiedresearchevidence.withinWindowPriceExtremes",
    ),
    key: "mfe_q50",
    kind: "return",
    label: translate(locale, "research.verifiedresearchevidence.medianMfe"),
    unavailableNote: translate(
      locale,
      "research.verifiedresearchevidence.windowExtremesAreNotPublishedForThisSnapshot",
    ),
  },
  {
    key: "mae_q50",
    kind: "return",
    label: translate(locale, "research.verifiedresearchevidence.medianMae"),
    unavailableNote: translate(
      locale,
      "research.verifiedresearchevidence.windowExtremesAreNotPublishedForThisSnapshot",
    ),
  },
  {
    group: translate(locale, "research.verifiedresearchevidence.unpublishedComponents"),
    key: "forecast_up_probability",
    kind: "probability",
    label: translate(
      locale,
      "research.verifiedresearchevidence.upwardDirectionProbability",
    ),
    unavailableNote: translate(
      locale,
      "research.verifiedresearchevidence.theDirectionModelFailedTheOutOfSampleGates",
    ),
  },
  {
    key: "range_exit_probability",
    kind: "probability",
    label: translate(locale, "research.horizonevidence.rangeExitProbability"),
    unavailableNote: translate(
      locale,
      "research.verifiedresearchevidence.rangeExitProbabilityIsNotPublishedForThisSnapshot",
    ),
  },
];
interface MetricTableProps {
  byHorizon: ReadonlyMap<number, ResearchModelEvidence>;
  horizons: readonly number[];
  regime: RegimeLabel | null | undefined;
}
function MetricTable({ byHorizon, horizons, regime }: MetricTableProps) {
  const { locale } = useI18n();
  return (
    <table className="metric-table">
      <caption>
        {translate(locale, "research.verifiedresearchevidence.metricsByHorizon")}
      </caption>
      <thead>
        <tr>
          <th scope="col">{translate(locale, "research.optionspanel.metric")}</th>
          {horizons.map((horizon) => (
            <th key={horizon} scope="col">
              {translate(locale, "research.verifiedresearchevidence.days.withValues", {
                horizon: horizon,
              })}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {horizons.some(
          (horizon) => (byHorizon.get(horizon)?.sample_abstain_reasons.length ?? 0) > 0,
        ) ? (
          <tr className="metric-table__abstain">
            <th scope="row">
              {translate(
                locale,
                "research.verifiedresearchevidence.abstentionReasonsForThisSample",
              )}
            </th>
            {horizons.map((horizon) => {
              const reasons = byHorizon.get(horizon)?.sample_abstain_reasons;
              return (
                <td key={horizon}>
                  {reasons === undefined
                    ? translate(locale, "research.instrumentidentity.notProvided")
                    : reasons.length === 0
                      ? translate(
                          locale,
                          "research.verifiedresearchevidence.noAbstentionReasonReported",
                        )
                      : reasons
                          .map(
                            (reason) => rejectionReasonLabel(reason, locale) ?? reason,
                          )
                          .join(locale === "en-US" ? ", " : "、")}
                </td>
              );
            })}
          </tr>
        ) : null}
        {METRIC_ROWS(locale).map((row) => {
          const cells = horizons.map((horizon) =>
            finite(byHorizon.get(horizon)?.values[row.key]),
          );
          const allMissing = cells.every((value) => value === null);
          return (
            <Fragment key={row.key}>
              {row.group === undefined ? null : (
                <tr className="metric-table__group">
                  <th colSpan={horizons.length + 1} scope="rowgroup">
                    {row.group}
                  </th>
                </tr>
              )}
              <tr>
                <th scope="row">{row.label}</th>
                {row.key === "range_exit_probability" ? (
                  horizons.map((horizon, index) => {
                    const text = percent(cells[index]);
                    return (
                      <td
                        className={text === null ? "metric-table__note" : undefined}
                        key={horizon}
                      >
                        {text ??
                          rangeExitUnavailableLabel(
                            byHorizon.get(horizon),
                            horizon,
                            regime,
                            locale,
                          )}
                      </td>
                    );
                  })
                ) : allMissing ? (
                  <td className="metric-table__note" colSpan={horizons.length}>
                    {row.unavailableNote}
                  </td>
                ) : (
                  cells.map((value, index) => {
                    const text =
                      row.kind === "probability"
                        ? percent(value)
                        : signedPercent(value);
                    if (text === null)
                      return (
                        <td className="metric-table__note" key={horizons[index]}>
                          {translate(locale, "research.researchpage.unpublished")}
                        </td>
                      );
                    return (
                      <td
                        className={
                          row.emphasis ? "metric-table__value--lead" : undefined
                        }
                        data-direction={row.emphasis ? direction(value) : undefined}
                        key={horizons[index]}
                      >
                        {text}
                      </td>
                    );
                  })
                )}
              </tr>
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
function TaskEvidenceDetails({ entry }: { entry: ResearchModelEvidence }) {
  const { locale } = useI18n();
  const unavailable = translate(locale, "research.instrumentidentity.notProvided");
  return (
    <details className="evidence-ledger__tasks">
      <summary>
        {translate(
          locale,
          "research.verifiedresearchevidence.dayTaskEvaluationEvidence.withValues",
          { horizon: entry.horizon },
        )}
      </summary>
      <p>
        {translate(
          locale,
          "research.verifiedresearchevidence.theseAreEvaluationUniverseAggregatesNot.withValues",
          { symbol: entry.symbol },
        )}
      </p>
      {entry.tasks.length === 0 ? (
        <p>
          {translate(
            locale,
            "research.verifiedresearchevidence.noTaskEvaluationEvidenceWasProvidedForThisSnapshot",
          )}
        </p>
      ) : (
        entry.tasks.map((task: TaskEvidence, index) => {
          const windows = (value: number | null | undefined) =>
            finite(value) === null || finite(task.fold_count) === null
              ? unavailable
              : `${value}/${task.fold_count}`;
          const rows = [
            [
              translate(locale, "research.verifiedresearchevidence.historicalGates"),
              task.gate_passed
                ? translate(
                    locale,
                    "research.verifiedresearchevidence.passedDoesNotImplyExecutability",
                  )
                : translate(locale, "research.verifiedresearchevidence.failed"),
            ],
            [
              translate(
                locale,
                "research.verifiedresearchevidence.windowsStatisticallyBeatingBaseline",
              ),
              windows(task.baseline_wins),
            ],
            [
              translate(
                locale,
                "research.verifiedresearchevidence.windowsWithLowerQuantileLoss",
              ),
              windows(task.pinball_wins),
            ],
            [
              translate(
                locale,
                "research.verifiedresearchevidence.windowsPassingIntervalCoverage",
              ),
              windows(task.coverage_windows_passed),
            ],
            [
              translate(
                locale,
                "research.verifiedresearchevidence.windowsWithPositiveReturnsAfterCosts",
              ),
              windows(task.positive_cost_windows),
            ],
            [
              translate(
                locale,
                "research.verifiedresearchevidence.oosEvaluationPeriod",
              ),
              translate(locale, "research.verifiedresearchevidence.to", {
                evaluationStart: task.evaluation_start ?? unavailable,
                evaluationEnd: task.evaluation_end ?? unavailable,
              }),
            ],
            [
              translate(
                locale,
                "research.verifiedresearchevidence.evaluationSamplesUniverse",
              ),
              finite(task.evaluation_sample_count)?.toLocaleString("zh-CN") ??
                unavailable,
            ],
            [
              translate(
                locale,
                "research.verifiedresearchevidence.worstWindowCoverage",
              ),
              percent(task.worst_coverage, 2) ?? unavailable,
            ],
            [
              translate(
                locale,
                "research.verifiedresearchevidence.meanIntervalWidthReturn",
              ),
              percent(task.average_interval_width, 2) ?? unavailable,
            ],
            [
              translate(
                locale,
                "research.verifiedresearchevidence.meanQuantileLossPinball",
              ),
              finite(task.average_pinball_loss)?.toFixed(6) ?? unavailable,
            ],
            [
              translate(
                locale,
                "research.verifiedresearchevidence.baselineQuantileLossPinball",
              ),
              finite(task.baseline_pinball_loss)?.toFixed(6) ?? unavailable,
            ],
            [
              translate(locale, "research.researchpage.modelVersion"),
              task.model_version || unavailable,
            ],
            [
              translate(locale, "research.verifiedresearchevidence.evaluationRun"),
              task.evaluation_run_id || unavailable,
            ],
            [
              translate(locale, "research.quantileintervals.calibrationVersion"),
              task.calibration_version || unavailable,
            ],
            [
              translate(
                locale,
                "research.verifiedresearchevidence.componentEvaluatedAt",
              ),
              task.component_evaluated_at
                ? formatUsMarketTime(task.component_evaluated_at, locale)
                : unavailable,
            ],
          ];
          return (
            <section
              aria-label={translate(
                locale,
                "research.verifiedresearchevidence.dayEvaluation",
                { task: taskLabel(task.task, locale), horizon: task.horizon },
              )}
              key={`${task.task}-${index}`}
            >
              <h3>
                {translate(
                  locale,
                  "research.verifiedresearchevidence.days2.withValues",
                  { task: taskLabel(task.task, locale), horizon: task.horizon },
                )}
              </h3>
              <ResearchBoundaryLabel scope={task.scope} />
              <dl>
                {rows.map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
            </section>
          );
        })
      )}
    </details>
  );
}
export interface VerifiedResearchEvidenceProps {
  entries: readonly ResearchModelEvidence[];
  notGeneratedReason: string;
  regime?: RegimeLabel | null | undefined;
}
export function VerifiedResearchEvidence({
  entries,
  notGeneratedReason,
  regime,
}: VerifiedResearchEvidenceProps) {
  const { locale } = useI18n();
  if (entries.length === 0) return null;
  const grouped = new Map<number, ResearchModelEvidence[]>();
  for (const entry of entries) {
    const group = grouped.get(entry.horizon) ?? [];
    group.push(entry);
    grouped.set(entry.horizon, group);
  }
  const duplicateHorizons = [...grouped.entries()]
    .filter(([, group]) => group.length > 1)
    .map(([horizon]) => horizon)
    .sort((left, right) => left - right);
  const byHorizon = new Map<number, ResearchModelEvidence>();
  for (const horizon of HORIZONS) {
    const group = grouped.get(horizon);
    if (group?.length === 1) byHorizon.set(horizon, group[0]!);
  }
  const availableHorizons = HORIZONS.filter((horizon) => byHorizon.has(horizon));
  if (availableHorizons.length === 0 && duplicateHorizons.length === 0) return null;
  const integrityIssues = new Set<string>();
  if (duplicateHorizons.length > 0) {
    integrityIssues.add(
      translate(
        locale,
        "research.verifiedresearchevidence.researchEvidenceHasDuplicateHorizonsDaysAllValuesAt",
        { duplicateHorizons: duplicateHorizons.join(locale === "en-US" ? ", " : "、") },
      ),
    );
  }
  const points: EndpointInterval[] = [];
  for (const horizon of availableHorizons) {
    const entry = byHorizon.get(horizon);
    if (entry === undefined) continue;
    const projection = entry.price_projection;
    const band = projection?.endpoint_price_band;
    if (
      projection == null ||
      !finitePriceBand(band) ||
      !Number.isFinite(projection.reference_price)
    ) {
      if (projection != null && band != null)
        integrityIssues.add(
          translate(
            locale,
            "research.verifiedresearchevidence.priceEndpointIntervalsContainNonFiniteValuesTheAffected",
          ),
        );
      continue;
    }
    points.push({
      asOf: entry.as_of,
      band,
      currency: projection.currency,
      horizon,
      reference: projection.reference_price,
      values: entry.values,
    });
  }
  const representative =
    availableHorizons.map((horizon) => byHorizon.get(horizon)).find(Boolean) ??
    [...entries].sort(
      (left, right) =>
        left.horizon - right.horizon || left.as_of.localeCompare(right.as_of),
    )[0];
  const scope = representative?.scope ?? "verified";
  return (
    <section aria-labelledby="verified-research-heading" className="evidence-ledger">
      <header className="evidence-ledger__header">
        <div>
          <span className="evidence-ledger__eyebrow">
            {translate(
              locale,
              "research.verifiedresearchevidence.researchComponentsThatPassedTheGates",
            )}
          </span>
          <h2 id="verified-research-heading">
            {translate(
              locale,
              "research.verifiedresearchevidence.verifiedResearchEvidence",
            )}
          </h2>
        </div>
        <ResearchBoundaryLabel scope={scope} />
      </header>
      <p className="evidence-ledger__note">
        {translate(
          locale,
          "research.verifiedresearchevidence.formalForecastNotGenerated",
        )}
        <span title={notGeneratedReason}>
          {reasonLabel(notGeneratedReason, locale)}
        </span>
        {translate(
          locale,
          "research.verifiedresearchevidence.researchComponentsPassedHistoricalGatesRecentPerformanceForThis",
        )}
      </p>
      <details className="evidence-ledger__note">
        <summary>
          {translate(
            locale,
            "research.verifiedresearchevidence.understandingReturnsBreakoutsAndPeriodPrices",
          )}
        </summary>
        <p>
          {translate(
            locale,
            "research.verifiedresearchevidence.returnQuantilesDescribeEndOfWindowScenariosBreakoutProbabilities",
          )}
        </p>
        <p>
          {translate(
            locale,
            "research.verifiedresearchevidence.q50IsAMedianScenarioNotAGuaranteedPrice",
          )}
        </p>
        <p>
          {translate(
            locale,
            "research.verifiedresearchevidence.compareEachColumnUsingItsTradingDayWindowCoverage",
          )}
        </p>
      </details>
      {integrityIssues.size === 0 ? null : (
        <p className="evidence-ledger__integrity" role="alert">
          {[...integrityIssues].join(" ")}
        </p>
      )}
      {points.length === 0 ? null : <DiscreteEndpointIntervals points={points} />}
      <div className="evidence-ledger__matrix-scroll">
        <MetricTable byHorizon={byHorizon} horizons={HORIZONS} regime={regime} />
      </div>
      {availableHorizons.map((horizon) => (
        <TaskEvidenceDetails entry={byHorizon.get(horizon)!} key={horizon} />
      ))}
    </section>
  );
}
