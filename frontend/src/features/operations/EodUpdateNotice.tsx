import {
  translate,
  translateRef,
  type Locale,
  type LocalizableText,
} from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { CheckCircle2, Clock3, TriangleAlert, X } from "lucide-react";
import { Link } from "react-router-dom";
import type { ManualEodUpdateMarker } from "../../app/snapshot-refresh";
import type { UpdateOperation } from "./useEodUpdate";
export type EodUpdateNoticeKind = "failed" | "retrying" | "success";
export interface EodUpdateNoticeState {
  kind: EodUpdateNoticeKind;
  message: string;
  operationId: string;
  originalDetails?: string | undefined;
}
function cutoff(value: string | null | undefined, locale: Locale = "zh-CN") {
  return value ?? translate(locale, "operations.noCompleteDataYet");
}
export function buildEodUpdateNotice(
  operation: UpdateOperation,
  marker: ManualEodUpdateMarker,
  locale: Locale = "zh-CN",
): EodUpdateNoticeState | null {
  if (operation.status === "queued" || operation.status === "running") {
    const retryAttempt = operation.retry_attempt ?? 0;
    if (retryAttempt <= 0 || operation.next_retry_at == null) return null;
    return {
      kind: "retrying",
      message: translate(
        locale,
        "operations.opendDailyBarsAreIncompleteRetryingAutomaticallyAttempt",
        {
          retryAttempt: String(retryAttempt),
          actual_cutoff: String(cutoff(operation.actual_cutoff, locale)),
          target_session: String(cutoff(operation.target_session, locale)),
        },
      ),
      operationId: operation.operation_id,
    };
  }
  if (operation.status === "failed") {
    const recommendation = translateRef(
      locale,
      (
        operation as typeof operation & {
          retry_recommendation_ref?: LocalizableText | null;
        }
      ).retry_recommendation_ref,
      "operations.checkUpdateDetailsBeforeSubmittingAgain",
    );
    return {
      kind: "failed",
      originalDetails:
        operation.retry_action ??
        operation.retry_recommendation ??
        operation.error_code ??
        undefined,
      message: translate(
        locale,
        "operations.updateIncompleteTargetCompleteThroughPreviousDataIs",
        {
          target_session: String(cutoff(operation.target_session, locale)),
          actual_cutoff: String(cutoff(operation.actual_cutoff, locale)),
          recommendation: String(recommendation),
        },
      ),
      operationId: operation.operation_id,
    };
  }
  if (
    operation.status !== "succeeded_data_only" &&
    operation.status !== "succeeded_forecast_ready"
  ) {
    return null;
  }
  const previous =
    operation.previous_cutoff ?? marker.previousCutoff ?? operation.actual_cutoff;
  const sessionAdvanced =
    operation.session_advanced ??
    (previous != null && operation.actual_cutoff != null
      ? previous !== operation.actual_cutoff
      : true);
  if (!sessionAdvanced) {
    return {
      kind: "success",
      message: translate(
        locale,
        "operations.validationCompleteTheLatestSessionRemainsWeekendsAnd",
        { actual_cutoff: String(cutoff(operation.actual_cutoff, locale)) },
      ),
      operationId: operation.operation_id,
    };
  }
  return {
    kind: "success",
    message:
      operation.status === "succeeded_data_only"
        ? translate(locale, "operations.dataOnlyNotice", {
            previous: cutoff(previous, locale),
            cutoff: cutoff(operation.actual_cutoff, locale),
          })
        : translate(locale, "operations.updateCompleteMarketDataAdvancedFromToPrices", {
            previous: String(cutoff(previous, locale)),
            actual_cutoff: String(cutoff(operation.actual_cutoff, locale)),
          }),
    operationId: operation.operation_id,
  };
}
export interface EodUpdateNoticeProps {
  notice: EodUpdateNoticeState;
  onDismiss: () => void;
}
export function EodUpdateNotice({ notice, onDismiss }: EodUpdateNoticeProps) {
  const { locale } = useI18n();
  const Icon =
    notice.kind === "failed"
      ? TriangleAlert
      : notice.kind === "retrying"
        ? Clock3
        : CheckCircle2;
  return (
    <div
      aria-label={translate(locale, "operations.eodUpdateResult")}
      className={`eod-update-notice eod-update-notice--${notice.kind}`}
      role={notice.kind === "failed" ? "alert" : "status"}
    >
      <Icon aria-hidden="true" size={18} strokeWidth={1.5} />
      <span>
        {notice.message}
        {notice.originalDetails ? (
          <details>
            <summary>{translate(locale, "operations.originalDetails")}</summary>
            <code>{notice.originalDetails}</code>
          </details>
        ) : null}
      </span>
      {notice.kind === "failed" ? (
        <Link to="/health">{translate(locale, "operations.viewUpdateDetails")}</Link>
      ) : null}
      <button
        aria-label={translate(locale, "operations.dismissEodUpdateNotice")}
        onClick={onDismiss}
        type="button"
      >
        <X aria-hidden="true" size={16} />
      </button>
    </div>
  );
}
