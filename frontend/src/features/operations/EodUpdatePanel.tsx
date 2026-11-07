import { detailText } from "../../i18n/details";
import {
  translate,
  translateRef,
  type LocalizableText,
  type Locale,
} from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { Circle, Clock3, X } from "lucide-react";
import { ResearchApiError } from "../../api/client";
import { formatUsMarketTime } from "../../app/presentation";
import { EodUpdateButton } from "./EodUpdateButton";
import { EOD_UPDATE_STAGES, updateStageLabel, useEodUpdate } from "./useEodUpdate";
type LocalizedOperation = { retry_recommendation_ref?: LocalizableText | null };
const STAGES = EOD_UPDATE_STAGES;
const STATUS_LABELS = (locale: Locale): Readonly<Record<string, string>> => ({
  failed: translate(locale, "operations.updateFailed"),
  queued: translate(locale, "operations.queued"),
  running: translate(locale, "operations.running"),
  succeeded_data_only: translate(locale, "operations.dataUpdatedModelDidNotPassGates"),
  succeeded_forecast_ready: translate(locale, "operations.dataAndForecastsUpdated"),
});
const ERROR_LABELS = (locale: Locale): Readonly<Record<string, string>> => ({
  daily_cutoff_incomplete: translate(
    locale,
    "operations.storedDataHasNotReachedTheTargetSession",
  ),
  daily_universe_incomplete: translate(
    locale,
    "operations.dailyBarsAreIncompleteForTheCurrentUniverse",
  ),
  history_quota_insufficient: translate(
    locale,
    "operations.insufficientOpendHistoryQuotaForAddedSymbols",
  ),
  history_quota_detail_unavailable: translate(
    locale,
    "operations.opendDidNotReturnVerifiableHistoryQuotaDetails",
  ),
  universe_scope_mismatch: translate(
    locale,
    "operations.datasetVersionAndUniverseBindingDoNotMatch",
  ),
  duckdb_file_locked: translate(
    locale,
    "operations.liveDataStorageIsLockedByAnotherProcess",
  ),
  live_pipeline_busy: translate(locale, "operations.anotherLiveDataTaskIsRunning"),
  opend_preflight_blocked: translate(locale, "operations.opendPreflightFailed"),
});
function operationErrorLabel(errorCode: string, locale: Locale = "zh-CN") {
  return `${ERROR_LABELS(locale)[errorCode] ?? translate(locale, "operations.theUpdatePipelineDidNotComplete")} (${errorCode})`;
}
const PHASE_DETAIL_LABELS = (locale: Locale): Readonly<Record<string, string>> => ({
  data_only: translate(locale, "operations.marketAndRuleSnapshotOnly"),
  forecast_ready: translate(locale, "operations.forecastSnapshotReady"),
  kept_current: translate(locale, "operations.currentChampionRetained"),
  no_change: translate(locale, "operations.dataAndArtifactsUnchanged"),
  promoted: translate(locale, "operations.challengerPromoted"),
});
function valueOrDash(value: string | number | null | undefined) {
  return value == null ? "—" : value;
}
function phaseDetail(value: string | null | undefined, locale: Locale = "zh-CN") {
  if (value == null || value === "") return translate(locale, "operations.waiting");
  const localized = detailText(value, locale);
  if (localized !== value) return localized;
  const collection = /^提交 (\d+) 个任务，失败 (\d+) 个$/.exec(value);
  if (collection !== null) {
    return translate(
      locale,
      "operations.wroteItemsIncrementalRequestsBlockedFinalCoverageDepends",
      { collection: String(collection[1]), collection2: String(collection[2]) },
    );
  }
  return (
    PHASE_DETAIL_LABELS(locale)[value] ?? translate(locale, "operations.phaseDetails")
  );
}
export interface EodUpdatePanelProps {
  expectedTargetSession?: string | null | undefined;
}
function workerStateLabel(state: string | undefined, locale: Locale = "zh-CN") {
  if (state === "stale")
    return translate(locale, "operations.backgroundWorkerHeartbeatLost");
  return translate(locale, "operations.backgroundWorkerDisconnected");
}
export function EodUpdatePanel({ expectedTargetSession }: EodUpdatePanelProps) {
  const { locale } = useI18n();
  const { error, isLoading, operation, worker } = useEodUpdate();
  const phaseByStage = new Map(
    operation?.phases.map((phase) => [phase.stage, phase]) ?? [],
  );
  const workerUnavailable = !isLoading && worker?.state !== "ready";
  const queuedWithoutWorker = operation?.status === "queued" && workerUnavailable;
  const queuedSeconds =
    operation?.status === "queued"
      ? Math.max(
          0,
          Math.floor((Date.now() - new Date(operation.created_at).getTime()) / 1000),
        )
      : 0;
  const errorMessage =
    error instanceof ResearchApiError && error.code === "eod_worker_unavailable"
      ? translate(locale, "operations.backgroundWorkerDisconnectedNoNewUpdateTaskWas")
      : translate(locale, "operations.taskStatusIsTemporarilyUnavailable");
  return (
    <section
      aria-label={translate(locale, "operations.runEodUpdate")}
      className="eod-operation-panel"
    >
      <header className="eod-operation-panel__header">
        <div>
          <h2>{translate(locale, "operations.runEodUpdate")}</h2>
          <p>
            {translate(
              locale,
              "operations.incrementalMarketDataQualityChecksFeatureUpdatesInference",
            )}
          </p>
        </div>
        <EodUpdateButton />
      </header>

      <div className="eod-operation-cutoffs">
        <div>
          <span>{translate(locale, "operations.targetSession")}</span>
          <strong>
            {valueOrDash(operation?.target_session ?? expectedTargetSession)}
          </strong>
        </div>
        <div>
          <span>{translate(locale, "operations.storedThrough")}</span>
          <strong>{valueOrDash(operation?.actual_cutoff)}</strong>
        </div>
        <div>
          <span>{translate(locale, "operations.snapshotVersion")}</span>
          <strong>{valueOrDash(operation?.snapshot_id)}</strong>
        </div>
        <div>
          <span>{translate(locale, "operations.modelVersion")}</span>
          <strong>{valueOrDash(operation?.model_version)}</strong>
        </div>
      </div>

      {operation?.status === "running" &&
      (operation.retry_attempt ?? 0) > 0 &&
      operation.next_retry_at != null ? (
        <div className="eod-operation-retry" role="status">
          <strong>
            {translate(locale, "operations.retryAttempt", {
              attempt: operation.retry_attempt ?? 0,
            })}
          </strong>
          <span>
            {translate(locale, "operations.retryStatus", {
              cutoff: valueOrDash(operation.actual_cutoff),
              target: valueOrDash(operation.target_session),
              time: formatUsMarketTime(operation.next_retry_at, locale),
            })}
          </span>
        </div>
      ) : null}

      {workerUnavailable ? (
        <div className="eod-operation-worker-alert" role="alert">
          <strong>{workerStateLabel(worker?.state, locale)}</strong>
          <span>
            {queuedWithoutWorker
              ? translate(
                  locale,
                  "operations.taskQueuedForSecondsWithoutAWorkerRestarting",
                  { queuedSeconds: String(queuedSeconds) },
                )
              : translate(
                  locale,
                  "operations.eodUpdatesAreUnavailableRestartLocalServicesTo",
                )}
          </span>
        </div>
      ) : null}

      {isLoading ? (
        <p className="eod-operation-panel__empty">
          {translate(locale, "operations.restoringTheLatestUpdateTask")}
        </p>
      ) : error !== null ? (
        <p className="eod-operation-panel__error">{errorMessage}</p>
      ) : operation === null ? (
        <p className="eod-operation-panel__empty">
          {" " + (translate(locale, "operations.noRunYetUseTheButtonAfterThe") + " ")}
        </p>
      ) : (
        <>
          <div
            className="eod-operation-progress"
            role="progressbar"
            aria-valuemax={100}
            aria-valuemin={0}
            aria-valuenow={operation.progress_percent}
          >
            <span style={{ width: `${operation.progress_percent}%` }} />
          </div>
          <div className="eod-operation-status">
            <strong>{operation.progress_percent}%</strong>
            <span>
              {operation.status === "queued" || operation.status === "running"
                ? queuedWithoutWorker
                  ? translate(locale, "operations.queuedWaitingForTheBackgroundWorker")
                  : operation.current_stage == null
                    ? (STATUS_LABELS(locale)[operation.status] ??
                      translate(locale, "operations.unknownStatus"))
                    : updateStageLabel(operation.current_stage, locale)
                : (STATUS_LABELS(locale)[operation.status] ??
                  translate(locale, "operations.unknownStatus"))}
            </span>
            <small>
              {translate(locale, "operations.task") + " "}
              {operation.operation_id}
            </small>
          </div>
          <ol className="eod-operation-timeline">
            {STAGES.map((stage) => {
              const phase = phaseByStage.get(stage);
              const failed = phase?.status === "failed";
              const completed = phase?.status === "completed";
              const running =
                phase?.status === "running" || operation.current_stage === stage;
              const Icon = failed ? X : running && !completed ? Clock3 : Circle;
              return (
                <li
                  className={
                    failed
                      ? "is-failed"
                      : completed
                        ? undefined
                        : running
                          ? "is-running"
                          : undefined
                  }
                  key={stage}
                >
                  <Icon aria-hidden="true" size={16} />
                  <span>
                    {updateStageLabel(stage, locale)}
                    {completed ? translate(locale, "operations.stageFinished") : null}
                  </span>
                  <small>
                    {phaseDetail(phase?.detail, locale)}
                    {phase?.detail && !PHASE_DETAIL_LABELS(locale)[phase.detail] ? (
                      <details>
                        <summary>
                          {translate(locale, "operations.originalDetails")}
                        </summary>
                        <code>{detailText(phase.detail, locale)}</code>
                      </details>
                    ) : null}
                  </small>
                </li>
              );
            })}
          </ol>
          <p>
            {" " +
              (translate(locale, "operations.aFinishedStageOnlyMeansItWasProcessed") +
                " ")}
          </p>
          {operation.error_code == null ? null : (
            <div className="eod-operation-panel__failure" role="alert">
              <strong>
                {translate(locale, "operations.failureReason")}
                {operationErrorLabel(operation.error_code, locale)}
              </strong>
              <span>
                {translateRef(
                  locale,
                  (operation as typeof operation & LocalizedOperation)
                    .retry_recommendation_ref,
                  "operations.fixTheFailedStageBeforeSubmittingAgain",
                )}
              </span>
            </div>
          )}
        </>
      )}
    </section>
  );
}
