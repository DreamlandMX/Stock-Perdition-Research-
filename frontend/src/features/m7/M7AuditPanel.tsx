import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { formatUsMarketTime } from "../../app/presentation";
import { useQuery } from "@tanstack/react-query";
import { Activity, CircleAlert, RefreshCw, ShieldCheck } from "lucide-react";
import {
  IndependentTrainingStatus,
  loadM7StatusFromApi,
  type M7Status,
} from "./M7LifecyclePanel";
import "./m7.css";
const EVENT_LABELS = (locale: Locale): Record<string, string> => ({
  automatic_rollback: translate(locale, "m7.automaticRollbackAfterIntegrityFailure"),
  blind_completed: translate(locale, "m7.forwardBlindTestSealed"),
  blind_invalidated: translate(locale, "m7.forwardBlindTestInvalidated"),
  drift_confirmed: translate(locale, "m7.driftConfirmed"),
  drift_warning: translate(locale, "m7.driftWarning"),
  integrity_failure: translate(locale, "m7.runtimeIntegrityFailure"),
  manual_approval: translate(locale, "m7.manualApproval"),
  promotion_completed: translate(locale, "m7.capabilityPromotionCompleted"),
  shadow_frozen: translate(locale, "m7.shadowPackageFrozen"),
  training_cancelled: translate(locale, "m7.trainingCancelled"),
  training_completed: translate(locale, "m7.trainingCompleted"),
  training_failed: translate(locale, "m7.trainingFailed"),
  training_queued: translate(locale, "m7.trainingQueued"),
  training_started: translate(locale, "m7.trainingStarted"),
});
const DRIFT_STATE_LABELS = (locale: Locale): Record<string, string> => ({
  none: translate(locale, "m7.normalMonitoring"),
  warning: translate(locale, "m7.driftWarning"),
  review_required: translate(locale, "m7.reviewRequired"),
  training_authorized: translate(locale, "m7.challengerTrainingAuthorized"),
});
const ACTION_LABELS = (locale: Locale): Record<string, string> => ({
  continue_monitoring: translate(locale, "m7.continueMonitoring"),
  investigate_inputs_and_wait_for_confirmation: translate(
    locale,
    "m7.inspectAffectedInputsAndWaitForMatureOutcomes",
  ),
  review_mature_outcomes: translate(
    locale,
    "m7.reviewMatureLabelsAndPerformanceChanges",
  ),
  review_queued_challenger_training: translate(
    locale,
    "m7.reviewQueuedChallengerTraining",
  ),
});
const SOURCE_STATUS_LABELS = (
  locale: Locale,
): Record<M7Status["source_status"], string> => ({
  partial: translate(locale, "m7.evidencePartiallyAvailable"),
  ready: translate(locale, "m7.evidenceReady"),
  unavailable: translate(locale, "m7.evidenceUnavailable"),
});
function warningLabel(
  value: boolean | null | undefined,
  warning: string,
  normal: string,
  unavailable: string,
) {
  if (value == null) return unavailable;
  return value ? warning : normal;
}
function percent(value: number | null | undefined, locale: Locale = "zh-CN") {
  return value == null
    ? translate(locale, "m7.unavailable")
    : `${(value * 100).toFixed(1)}%`;
}
export interface M7AuditPanelProps {
  loadStatus?: () => Promise<M7Status>;
}
export function M7AuditPanel({ loadStatus = loadM7StatusFromApi }: M7AuditPanelProps) {
  const { locale } = useI18n();
  const query = useQuery({
    queryFn: loadStatus,
    queryKey: ["m7-lifecycle-status"],
    refetchInterval: 30000,
  });
  const status = query.data;
  return (
    <section aria-labelledby="m7-audit-title" className="health-panel m7-audit-panel">
      <header className="health-panel__header">
        <div>
          <h2 id="m7-audit-title">
            {translate(locale, "m7.m7LifecycleAndDriftAudit")}
          </h2>
          <p>
            {translate(locale, "m7.readOnlyHashChainEventsConsecutiveDriftEvidence")}
          </p>
        </div>
        <small>
          {status
            ? SOURCE_STATUS_LABELS(locale)[status.source_status]
            : translate(locale, "m7.loading")}
        </small>
      </header>
      {query.isPending ? (
        <div className="health-source-state" role="status">
          <Activity aria-hidden="true" size={17} />
          {" " + (translate(locale, "m7.loadingTheM7AuditChain") + " ")}
        </div>
      ) : !status ? (
        <div className="health-source-state" role="alert">
          <CircleAlert aria-hidden="true" size={17} />
          {" " + (translate(locale, "m7.m7AuditSourceIsUnavailable") + " ")}
        </div>
      ) : (
        <>
          <IndependentTrainingStatus summary={status.independent_training_summary} />
          <div className="m7-drift-strip" data-state={status.drift.state}>
            <ShieldCheck aria-hidden="true" size={18} />
            <div>
              <span>{translate(locale, "m7.driftState")}</span>
              <strong>
                {status.drift.state === "none" && status.drift.last_updated_at === null
                  ? translate(locale, "m7.noDriftEvidenceYet")
                  : (DRIFT_STATE_LABELS(locale)[status.drift.state] ??
                    status.drift.state)}
              </strong>
            </div>
            <div>
              <span>
                {" " + (translate(locale, "m7.consecutiveDailyWarnings") + " ")}
                {status.drift.consecutive_daily_warning_sessions} / 5
              </span>
              <span>
                {" " + (translate(locale, "m7.matureWeeklyBreaches") + " ")}
                {status.drift.consecutive_mature_weekly_breaches} / 2
              </span>
            </div>
          </div>
          <div className="m7-drift-details">
            <div
              className="m7-drift-alerts"
              aria-label={translate(locale, "m7.driftAlertLevels")}
            >
              <span data-warning={status.drift.feature_warning === true}>
                {warningLabel(
                  status.drift.feature_warning,
                  translate(locale, "m7.featurePsiWarning"),
                  translate(locale, "m7.featureDistributionNormal"),
                  translate(locale, "m7.featureDistributionUnavailable"),
                )}
              </span>
              <span data-warning={status.drift.prediction_warning === true}>
                {warningLabel(
                  status.drift.prediction_warning,
                  translate(locale, "m7.predictionDistributionWarning"),
                  translate(locale, "m7.predictionDistributionNormal"),
                  translate(locale, "m7.predictionDistributionUnavailable"),
                )}
              </span>
              <span data-warning={status.drift.outcome_warning === true}>
                {warningLabel(
                  status.drift.outcome_warning,
                  translate(locale, "m7.matureOutcomeWarning"),
                  translate(locale, "m7.matureOutcomesWithoutWarning"),
                  translate(locale, "m7.matureOutcomesUnavailable"),
                )}
              </span>
            </div>
            <dl className="m7-drift-metrics">
              <div>
                <dt>{translate(locale, "m7.coreFeaturesAboveThreshold")}</dt>
                <dd>
                  {percent(status.drift.core_feature_psi_fraction_above_020, locale)}
                </dd>
              </div>
              <div>
                <dt>{translate(locale, "m7.coverage")}</dt>
                <dd>{percent(status.drift.coverage_rate, locale)}</dd>
              </div>
              <div>
                <dt>{translate(locale, "m7.missingness")}</dt>
                <dd>{percent(status.drift.missingness_rate, locale)}</dd>
              </div>
              <div>
                <dt>{translate(locale, "m7.oodFraction")}</dt>
                <dd>{percent(status.drift.ood_rate, locale)}</dd>
              </div>
            </dl>
            <div className="m7-drift-operations">
              <div>
                <span>{translate(locale, "m7.lastUpdated")}</span>
                <time dateTime={status.drift.last_updated_at ?? undefined}>
                  {status.drift.last_updated_at ??
                    translate(locale, "m7.noEvidenceAvailableYet")}
                </time>
              </div>
              <div>
                <span>{translate(locale, "m7.affectedScope")}</span>
                <strong>
                  {status.drift.affected_scopes.join(locale === "en-US" ? ", " : "、") ||
                    translate(locale, "m7.none")}
                </strong>
              </div>
              <div>
                <span>{translate(locale, "m7.retryMode")}</span>
                <strong>
                  <RefreshCw aria-hidden="true" size={13} />
                  {status.drift.retryability === "automatic"
                    ? translate(locale, "m7.automaticRetry")
                    : status.drift.retryability === "manual"
                      ? translate(locale, "m7.manualRetry")
                      : translate(locale, "m7.noRetryNeeded")}
                </strong>
              </div>
              <div>
                <span>{translate(locale, "m7.recommendedAction")}</span>
                <strong>
                  {ACTION_LABELS(locale)[status.drift.recommended_action] ??
                    translate(locale, "m7.unknownState", {
                      code: status.drift.recommended_action,
                    })}
                </strong>
              </div>
            </div>
            <div className="m7-affected-inputs">
              <span>{translate(locale, "m7.affectedInputs")}</span>
              {status.drift.affected_inputs.length > 0 ? (
                <ul>
                  {status.drift.affected_inputs.map((input) => (
                    <li key={input}>
                      <code>{input}</code>
                    </li>
                  ))}
                </ul>
              ) : (
                <strong>{translate(locale, "m7.none")}</strong>
              )}
            </div>
          </div>
          {status.recent_events.length === 0 ? (
            <div className="health-source-state" role="status">
              {" " + (translate(locale, "m7.noM7LifecycleEventsYetThisDoesNot") + " ")}
            </div>
          ) : (
            <ol className="m7-event-list">
              {status.recent_events.map((event) => (
                <li key={event.event_id}>
                  <div>
                    <strong>
                      {EVENT_LABELS(locale)[event.event_type] ??
                        translate(locale, "m7.unknownState", {
                          code: event.event_type,
                        })}
                    </strong>
                    <span>
                      {event.bundle_id ?? translate(locale, "m7.global")}; {event.actor}
                    </span>
                  </div>
                  <time dateTime={event.recorded_at}>
                    {formatUsMarketTime(event.recorded_at, locale)}
                  </time>
                  <code>{event.event_id}</code>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  );
}
