import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
import { optionNumber, optionPercent } from "../options/option-labels";
type Experiment = components["schemas"]["OptionExperimentReadModel"];
const groups = (locale: Locale): Record<string, string> => ({
  B0: translate(locale, "backtests.b0StockBaseline"),
  E1: translate(locale, "backtests.e1IvTermSkew"),
  E2: translate(locale, "backtests.e2OiVolume"),
  E3: translate(locale, "backtests.e3AllFeatures"),
  quality: translate(locale, "backtests.qualityQualityFeaturesOnly"),
});
const checkLabels = (locale: Locale): Record<string, string> => ({
  test_dates_insufficient: translate(locale, "backtests.testDates"),
  outer_folds_insufficient: translate(locale, "backtests.independentOuterFolds"),
  fold_gain_unstable: translate(locale, "backtests.positiveReturnFoldFraction"),
  brier_time_block_gain_unproven: translate(
    locale,
    "backtests.timeBlockBrierImprovementEvidence",
  ),
  log_loss_degraded: translate(locale, "backtests.directionLogLossDoesNotDegrade"),
  calibration_failed: translate(
    locale,
    "backtests.directionProbabilityCalibrationError",
  ),
  deployment_coverage_failed: translate(locale, "backtests.deploymentCoverage"),
  quality_only_explains_gain: translate(
    locale,
    "backtests.returnExceedsQualityOnlyFeatures",
  ),
  all_nine_symbols_required: translate(locale, "backtests.evidenceForAllNineSymbols"),
  symbol_stability_failed: translate(locale, "backtests.perSymbolBrierStability"),
  net_economic_gain_failed: translate(locale, "backtests.netEconomicReturnImproves"),
  drawdown_degraded: translate(locale, "backtests.maximumDrawdownDoesNotDegrade"),
  catastrophic_economic_window: translate(
    locale,
    "backtests.noCatastrophicEconomicWindow",
  ),
  horizon_evaluation_missing: translate(locale, "backtests.horizonEvaluationEvidence"),
  registered_promotion_rules_missing: translate(
    locale,
    "backtests.registeredPromotionRules",
  ),
  synthetic_evidence_not_promotable: translate(
    locale,
    "backtests.syntheticEvidenceCannotBePromoted",
  ),
  research_scope_not_promotable: translate(
    locale,
    "backtests.researchScopeCannotBePromoted",
  ),
  production_calendar_evidence_missing: translate(
    locale,
    "backtests.productionTradingCalendarEvidence",
  ),
  registered_evaluation_scope_mismatch: translate(
    locale,
    "backtests.registeredAndEvaluatedScopesMatch",
  ),
  executable_economic_evidence_missing: translate(
    locale,
    "backtests.executableEconomicEvaluationEvidence",
  ),
  event_cohort_evidence_missing: translate(locale, "backtests.eventGroupEvidence"),
});
function checkLabel(name: string, locale: Locale = "zh-CN") {
  if (checkLabels(locale)[name]) return checkLabels(locale)[name];
  const tasks: Record<string, string> = {
    capital_gain_return: translate(locale, "backtests.capitalGainReturn"),
    executable_return: translate(locale, "backtests.executableReturn"),
    forecast_mfe: translate(locale, "backtests.maximumFavorableExcursion"),
    forecast_mae: translate(locale, "backtests.maximumAdverseExcursion"),
    earnings: translate(locale, "backtests.earnings"),
    high_volatility: translate(locale, "backtests.highVolatility"),
    low_volatility: translate(locale, "backtests.lowVolatility"),
  };
  for (const [task, label] of Object.entries(tasks)) {
    if (name === `${task}_evaluation_missing`)
      return translate(locale, "backtests.evaluationEvidence", {
        label: String(label),
      });
    if (name === `${task}_pinball_degraded`)
      return translate(locale, "backtests.quantileLossDoesNotDegrade", {
        label: String(label),
      });
    if (name === `${task}_coverage_failed`)
      return translate(locale, "backtests.coverageError", { label: String(label) });
    if (name === `${task}_cohort_missing_or_small`)
      return translate(locale, "backtests.groupSamples", { label: String(label) });
    if (name === `${task}_cohort_degraded`)
      return translate(locale, "backtests.groupDoesNotDegrade", {
        label: String(label),
      });
  }
  return name;
}
export function OptionAblationTable({ experiment }: { experiment: Experiment }) {
  const { locale } = useI18n();
  return (
    <article
      aria-label={translate(locale, "backtests.optionsAblation", {
        experiment_id: String(experiment.experiment_id),
      })}
    >
      <h3>{experiment.experiment_id}</h3>
      {experiment.synthetic || experiment.evaluation_scope !== "production" ? (
        <p role="status" className="option-evidence-warning">
          {experiment.synthetic
            ? translate(locale, "backtests.syntheticEvidencePromotionUnavailable")
            : translate(
                locale,
                "backtests.researchScopeEvidencePromotionUnavailable",
              )}{" "}
          |{" "}
          {experiment.evaluation_scope ??
            translate(locale, "backtests.scopeUnconfirmed")}
        </p>
      ) : null}
      <p>
        {experiment.eligible
          ? translate(
              locale,
              "backtests.theOptionsCandidateMeetsTheRegisteredGatesPromotion",
            )
          : translate(
              locale,
              "backtests.optionsFeaturesHaveNotDemonstratedForecastImprovementTest",
              {
                dates: String(
                  experiment.horizons.length
                    ? Math.min(...experiment.horizons.map((h) => h.dates ?? 0))
                    : translate(locale, "backtests.notProvided"),
                ),
                minimum_test_dates: String(
                  experiment.minimum_test_dates ??
                    translate(locale, "backtests.notRegistered"),
                ),
              },
            )}
      </p>
      <div className="option-table-wrap">
        <table
          aria-label={translate(locale, "backtests.optionsFeatureAblationMetrics")}
        >
          <thead>
            <tr>
              <th>{translate(locale, "backtests.horizonGroup")}</th>
              <th>{translate(locale, "backtests.directionBrier")}</th>
              <th>{translate(locale, "backtests.vsBaseline")}</th>
              <th>{translate(locale, "backtests.pinballQuantiles")}</th>
              <th>{translate(locale, "backtests.actualCoverageOf80Interval")}</th>
              <th>{translate(locale, "backtests.testDates")}</th>
            </tr>
          </thead>
          <tbody>
            {experiment.horizons.flatMap((horizon) =>
              horizon.groups.map((group) => (
                <tr key={`${horizon.horizon_days}-${group.group}`}>
                  <td>
                    {horizon.horizon_days}
                    {" " + (translate(locale, "backtests.days2") + " ")}
                    {groups(locale)[group.group] ?? group.group}
                  </td>
                  <td>{optionNumber(group.brier, locale)}</td>
                  <td>
                    {group.group === "B0" ? (
                      "—"
                    ) : (
                      <>
                        {optionNumber(group.delta_brier, locale)}
                        {group.group === "E3" &&
                          typeof horizon.bootstrap?.one_sided_upper_90 === "number" && (
                            <small>
                              <br />
                              {" " +
                                translate(locale, "backtests.90OneSidedInterval")}{" "}
                              {horizon.bootstrap?.one_sided_upper_90.toFixed(4)}]
                              {horizon.bootstrap?.one_sided_upper_90 >= 0
                                ? translate(locale, "backtests.improvementUnproven")
                                : ""}
                            </small>
                          )}
                      </>
                    )}
                  </td>
                  <td>
                    {["0.1", "0.5", "0.9"]
                      .map((q) =>
                        group.pinball?.[q] == null
                          ? "—"
                          : group.pinball?.[q]?.toFixed(4),
                      )
                      .join(" / ")}
                  </td>
                  <td>{optionPercent(group.empirical_coverage, undefined, locale)}</td>
                  <td>{horizon.dates ?? translate(locale, "backtests.notProvided")}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
      <p className="option-risk-neutral">
        {" " +
          (translate(locale, "backtests.brierGroupB0NegativeValuesMeanLowerLoss") +
            " ")}
      </p>
      {experiment.horizons.map((horizon) => (
        <details key={horizon.horizon_days} open>
          <summary>
            {horizon.horizon_days}
            {" " + (translate(locale, "backtests.dayPromotionGates") + " ")}
            {horizon.eligible
              ? translate(locale, "backtests.passed")
              : translate(locale, "backtests.notMet")}
          </summary>
          <p>
            {" " + (translate(locale, "backtests.independentOuterFolds2") + " ")}
            {horizon.fold_count ?? translate(locale, "backtests.notProvided")}
            {" " + translate(locale, "backtests.positiveReturnFoldFraction2")}{" "}
            {optionPercent(horizon.positive_fold_fraction, undefined, locale)}
          </p>
          <ul className="option-gate-list">
            {horizon.checks.map((check) => (
              <li key={check.name} data-status={check.status} title={check.name}>
                <span>{checkLabel(check.name, locale)}</span>
                <b>
                  {check.status === "passed"
                    ? translate(locale, "backtests.pass")
                    : check.status === "failed"
                      ? translate(locale, "backtests.fail")
                      : translate(locale, "backtests.insufficientData")}
                </b>
              </li>
            ))}
          </ul>
        </details>
      ))}
      <p>
        {" " + (translate(locale, "backtests.collectedTradingDays") + " ")}
        {experiment.history_days ?? translate(locale, "backtests.unconfirmed")}
        {" " + translate(locale, "backtests.gateRequires")}{" "}
        {experiment.minimum_test_dates ?? translate(locale, "backtests.notRegistered")}
      </p>
      {experiment.history_days != null && experiment.minimum_test_dates != null && (
        <progress
          className="option-progress"
          value={Math.min(experiment.history_days, experiment.minimum_test_dates)}
          max={experiment.minimum_test_dates}
          aria-label={translate(
            locale,
            "backtests.optionsCollectionSampleAccumulation",
          )}
        />
      )}
      <p className="option-risk-neutral">
        {" " + (translate(locale, "backtests.estimatedDate") + " ")}
        {experiment.estimated_ready_date ??
          translate(
            locale,
            "backtests.cannotEstimateWithoutARegisteredThresholdOrFuture",
          )}
        {" " +
          (translate(
            locale,
            "backtests.assumesSuccessfulCollectionEveryTradingDayCollectionDays",
          ) +
            " ")}
      </p>
    </article>
  );
}
