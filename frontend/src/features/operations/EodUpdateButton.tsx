import { translate, translateRef, type LocalizableText } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { CheckCircle2, LoaderCircle, RefreshCw, TriangleAlert } from "lucide-react";
import { updateStageLabel, useEodUpdate } from "./useEodUpdate";
import "./eod-update.css";
export interface EodUpdateButtonProps {
  compact?: boolean;
}
export function EodUpdateButton({ compact = false }: EodUpdateButtonProps) {
  const { locale } = useI18n();
  const { error, isActive, isLoading, operation, submit, submitting, worker } =
    useEodUpdate();
  const workerUnavailable = !isLoading && worker?.state !== "ready";
  const waitingForWorker = isActive && workerUnavailable;
  const busy = isActive || submitting;
  const requestFailed = operation?.status === "failed" || error !== null;
  const failed = workerUnavailable || requestFailed;
  const failureDetail = translateRef(
    locale,
    (
      operation as typeof operation & {
        retry_recommendation_ref?: LocalizableText | null;
      }
    )?.retry_recommendation_ref,
    "operations.eodUpdateDidNotCompletePleaseRetry",
  );
  const succeeded =
    operation?.status === "succeeded_data_only" ||
    operation?.status === "succeeded_forecast_ready";
  const Icon = workerUnavailable
    ? TriangleAlert
    : busy
      ? LoaderCircle
      : failed
        ? TriangleAlert
        : succeeded
          ? CheckCircle2
          : RefreshCw;
  const label = workerUnavailable
    ? translate(locale, "operations.updateWorkerDisconnected")
    : busy
      ? operation?.current_stage == null
        ? translate(locale, "operations.waitingForUpdate")
        : updateStageLabel(operation.current_stage, locale)
      : requestFailed
        ? translate(locale, "operations.updateFailedRetry")
        : translate(locale, "operations.runEodUpdate");
  return (
    <button
      aria-busy={busy}
      aria-label={
        requestFailed && !workerUnavailable ? `${label}: ${failureDetail}` : undefined
      }
      className={`eod-update-button${compact ? " eod-update-button--compact" : ""}`}
      disabled={busy || workerUnavailable || isLoading}
      onClick={submit}
      title={
        waitingForWorker
          ? translate(
              locale,
              "operations.taskQueuedButTheBackgroundWorkerIsDisconnected",
            )
          : workerUnavailable
            ? translate(
                locale,
                "operations.theBackgroundUpdateWorkerIsDisconnectedRestartLocal",
              )
            : busy
              ? `${label}; ${operation?.progress_percent ?? 0}%`
              : requestFailed
                ? `${label}: ${failureDetail}`
                : translate(
                    locale,
                    "operations.incrementallyCollectDataBuildFeaturesApplyTrainingGates",
                  )
      }
      type="button"
    >
      <Icon
        aria-hidden="true"
        className={busy && !waitingForWorker ? "eod-update-button__spinner" : undefined}
        size={compact ? 15 : 17}
      />
      <span>
        {compact && busy && !waitingForWorker
          ? `${operation?.progress_percent ?? 0}%`
          : label}
      </span>
    </button>
  );
}
