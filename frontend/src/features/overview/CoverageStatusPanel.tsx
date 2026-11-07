import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { Link } from "react-router-dom";

import type { components } from "../../api/generated/schema";
import { rejectionReasonLabel } from "../../app/presentation";
import type { OptionOverviewResponse } from "../options/useOptionsOverview";
import { optionReasonLabel } from "../options/option-labels";

type OverviewReadModel = components["schemas"]["OverviewReadModel"];
type ResolvedSnapshot = components["schemas"]["ResolvedSnapshot"];

function CoverageRow({
  count,
  label,
  total,
}: {
  count: number;
  label: string;
  total: number;
}) {
  const ratio = total === 0 ? 0 : count / total;
  return (
    <div className="ov-coverage__row">
      <span>{label}</span>
      <span aria-hidden="true" className="ov-meter">
        <i style={{ width: `${(ratio * 100).toFixed(1)}%` }} />
      </span>
      <strong>
        {count}/{total}
      </strong>
    </div>
  );
}

export function CoverageStatusPanel({
  data,
  healthHref,
  snapshot,
  options,
}: {
  data: OverviewReadModel;
  healthHref: string;
  snapshot: ResolvedSnapshot;
  options?: OptionOverviewResponse | undefined;
}) {
  const { locale } = useI18n();
  const coverage = data.internal_evidence?.evidence_coverage ?? null;
  const total = coverage?.total_symbols ?? 0;
  const summary = data.candidate_summary ?? null;
  const covered = total - data.abstain_count;
  return (
    <section
      aria-label={translate(
        locale,
        "overview.CoverageStatusPanel.dataAndModelCoverageStatus",
      )}
      className="ov-section ov-coverage"
    >
      <header className="ov-section__header">
        <div>
          <h2>
            {translate(locale, "overview.CoverageStatusPanel.dataAndModelCoverage")}
          </h2>
          <p>
            {translate(
              locale,
              "overview.CoverageStatusPanel.dataCoverageModelTaskCoverageAndAccuracyAreDifferent",
            )}
          </p>
        </div>
        <Link className="ov-link-button" to={healthHref}>
          {translate(locale, "overview.CoverageStatusPanel.viewModelsData")}
        </Link>
      </header>
      <p className="option-risk-neutral">
        {translate(locale, "overview.CoverageStatusPanel.optionsCollection")}
        {options
          ? translate(
              locale,
              "overview.CoverageStatusPanel.completeBatchesTradingDaysAccumulated",
              {
                value: options.collection.latest_automatic
                  ? optionReasonLabel(
                      options.collection.latest_automatic.status,
                      locale,
                    )
                  : translate(
                      locale,
                      "overview.CoverageStatusPanel.noAutomaticRunsRecorded",
                    ),
                completeSymbols:
                  options.collection.complete_symbols ??
                  translate(locale, "options.OptionRefreshControl.unconfirmed"),
                value2:
                  new Set(options.collection.cells.map((cell) => cell.symbol)).size ||
                  translate(locale, "options.OptionRefreshControl.unconfirmed"),
                historyDays:
                  options.collection.history_days ??
                  translate(locale, "options.OptionRefreshControl.unconfirmed"),
              },
            )
          : translate(
              locale,
              "overview.CoverageStatusPanel.loadingOrTemporarilyUnavailable",
            )}{" "}
        ;{" "}
        <Link to={`${healthHref}#options`}>
          {translate(locale, "overview.CoverageStatusPanel.viewCollectionMatrix")}
        </Link>
      </p>
      <div className="ov-coverage__grid">
        <section
          aria-label={translate(locale, "overview.CoverageStatusPanel.dataCoverage")}
        >
          <h3>{translate(locale, "overview.CoverageStatusPanel.dataCoverage")}</h3>
          {coverage === null ? (
            <p className="ov-empty">
              {translate(
                locale,
                "overview.CoverageStatusPanel.noCrossSectionalCoverageEvidenceInThisSnapshot",
              )}
            </p>
          ) : (
            <>
              <CoverageRow
                count={coverage.rule_evidence_count}
                label={translate(locale, "overview.OpportunityTable.ruleEvidence")}
                total={total}
              />
              <CoverageRow
                count={coverage.session_return_count}
                label={translate(locale, "overview.CoverageStatusPanel.sessionReturn")}
                total={total}
              />
              <CoverageRow
                count={coverage.return_5_count}
                label={translate(locale, "overview.CoverageStatusPanel.5DayReturn")}
                total={total}
              />
              <CoverageRow
                count={coverage.return_20_count}
                label={translate(locale, "overview.CoverageStatusPanel.20DayReturn")}
                total={total}
              />
              <CoverageRow
                count={coverage.volume_z_count}
                label={translate(locale, "overview.SectorMarketMap.volumeZ20")}
                total={total}
              />
              <CoverageRow
                count={coverage.intraday_available_count}
                label={translate(locale, "overview.OpportunityTable.intradayData")}
                total={total}
              />
            </>
          )}
        </section>
        <section
          aria-label={translate(
            locale,
            "overview.OverviewHeaderBand.modelTaskCoverage",
          )}
        >
          <h3>{translate(locale, "overview.OverviewHeaderBand.modelTaskCoverage")}</h3>
          <p className="ov-coverage__big">
            {summary === null
              ? translate(locale, "overview.OverviewHeaderBand.noEvaluationEvidence")
              : `${summary.passed_tasks}/${summary.total_tasks}`}
          </p>
          <p className="ov-coverage__note">
            {summary === null
              ? translate(
                  locale,
                  "overview.CoverageStatusPanel.noChallengerEvaluationsRecordedTaskPassCountsAreUnavailable",
                )
              : translate(
                  locale,
                  "overview.CoverageStatusPanel.tasksPassingTheQualityGatesThisIsCoverageNot",
                  {
                    value: summary.all_gates_passed
                      ? translate(
                          locale,
                          "overview.CoverageStatusPanel.allQualityGatesPassed",
                        )
                      : translate(
                          locale,
                          "overview.CoverageStatusPanel.gatesNotPassed",
                          { count: summary.failed_tasks.length },
                        ),
                  },
                )}
          </p>
          <dl>
            <div>
              <dt>{translate(locale, "overview.CoverageStatusPanel.modelVersion")}</dt>
              <dd>
                {snapshot.model_version ??
                  translate(locale, "overview.OverviewPage.noChampionModel")}
              </dd>
            </div>
            <div>
              <dt>
                {translate(locale, "overview.CoverageStatusPanel.calibrationVersion")}
              </dt>
              <dd>
                {snapshot.calibration_version ??
                  translate(locale, "overview.OverviewPage.notCalibrated")}
              </dd>
            </div>
          </dl>
        </section>
        <section
          aria-label={translate(
            locale,
            "overview.overviewInsights.formalForecastCoverage",
          )}
        >
          <h3>
            {translate(locale, "overview.overviewInsights.formalForecastCoverage")}
          </h3>
          <p className="ov-coverage__big">
            {Math.max(0, covered)}/{total || data.abstain_count}
          </p>
          <p className="ov-coverage__note">
            {snapshot.activation_basis === "manual_override"
              ? translate(locale, "common.shared.manuallyActivatedUnverified")
              : snapshot.prediction_readiness === "forecast_ready"
                ? translate(
                    locale,
                    "overview.CoverageStatusPanel.theCompleteChampionModelIsReady",
                  )
                : translate(
                    locale,
                    "overview.CoverageStatusPanel.researchDataIsReadyFormalForecastsAwaitAComplete",
                  )}
          </p>
          {data.abstain_reasons.length === 0 ? null : (
            <ul>
              {data.abstain_reasons.map((reason) => (
                <li key={reason.reason}>
                  <span>{rejectionReasonLabel(reason.reason, locale)}</span>
                  <strong>
                    {reason.count}{" "}
                    {translate(
                      locale,
                      reason.count === 1
                        ? "overview.OverviewPage.instrument"
                        : "overview.SectorRegimeMatrix.instruments",
                    )}
                  </strong>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section
          aria-label={translate(locale, "overview.CoverageStatusPanel.snapshotVersion")}
        >
          <h3>{translate(locale, "overview.CoverageStatusPanel.snapshotVersion")}</h3>
          <dl>
            <div>
              <dt>{translate(locale, "overview.OverviewPage.forecastBatch")}</dt>
              <dd>{snapshot.snapshot_id}</dd>
            </div>
            <div>
              <dt>
                {translate(locale, "overview.CoverageStatusPanel.datasetRevision")}
              </dt>
              <dd>{snapshot.dataset_revision}</dd>
            </div>
            <div>
              <dt>{translate(locale, "overview.CoverageStatusPanel.featureSet")}</dt>
              <dd>{snapshot.feature_set_version}</dd>
            </div>
            <div>
              <dt>{translate(locale, "overview.CoverageStatusPanel.universe")}</dt>
              <dd>{snapshot.universe_version}</dd>
            </div>
            <div>
              <dt>{translate(locale, "overview.CoverageStatusPanel.inferenceTime")}</dt>
              <dd>
                {(data.total_inference_latency_ms / 1000).toFixed(2)}{" "}
                {translate(locale, "overview.OverviewPage.seconds")}
              </dd>
            </div>
          </dl>
        </section>
      </div>
    </section>
  );
}
