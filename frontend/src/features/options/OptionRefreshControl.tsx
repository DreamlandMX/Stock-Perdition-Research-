import { detailText } from "../../i18n/details";
import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createResearchApiClient, ResearchApiError } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { formatUsMarketTime } from "../../app/presentation";
import "./options-shared.css";

type Operation = components["schemas"]["OptionRefreshOperation"];
const api = createResearchApiClient({ trackConnectivity: false });
const key = ["option-refresh-operation"];
const getLabels = (locale: Locale = "zh-CN"): Record<string, string> => ({
  queued: translate(locale, "options.OptionRefreshControl.queued"),
  running: translate(locale, "options.OptionRefreshControl.updating"),
  completed: translate(locale, "options.OptionRefreshControl.updateCompleted"),
  partial: translate(locale, "options.OptionRefreshControl.partiallyCompleted"),
  failed: translate(locale, "options.OptionRefreshControl.updateFailed"),
  no_option_contracts: translate(
    locale,
    "options.OptionRefreshControl.noEligibleOptionsContractsFound",
  ),
  symbol_outside_option_scope: translate(
    locale,
    "options.OptionRefreshControl.outsideTheSiteUniverse",
  ),
  permission_denied: translate(
    locale,
    "options.OptionRefreshControl.insufficientMarketDataPermissions",
  ),
  rate_limited: translate(locale, "options.OptionRefreshControl.providerRateLimit"),
  connection_failed: translate(
    locale,
    "options.OptionRefreshControl.openDConnectionFailed",
  ),
  collection_timeout: translate(
    locale,
    "options.OptionRefreshControl.collectionTimedOut",
  ),
  incomplete_snapshots: translate(
    locale,
    "options.OptionRefreshControl.incompleteOptionsChain",
  ),
  worker_interrupted: translate(
    locale,
    "options.OptionRefreshControl.workerRestartInterruptedThisCollection",
  ),
  collection_failed: translate(
    locale,
    "options.OptionRefreshControl.optionsCollectionFailed",
  ),
  collector_busy: translate(
    locale,
    "options.OptionRefreshControl.optionsCollectorBusy",
  ),
  snapshot_writer_busy: translate(
    locale,
    "options.OptionRefreshControl.snapshotWriterBusy",
  ),
  option_refresh_worker_offline: translate(
    locale,
    "options.OptionRefreshControl.optionsUpdateWorkerDisconnected",
  ),
  option_refresh_busy: translate(
    locale,
    "options.OptionRefreshControl.anotherOptionsUpdateIsRunningWaitForItTo",
  ),
});
const time = (at?: string | null, locale: Locale = "zh-CN") =>
  at
    ? formatUsMarketTime(at, locale)
    : translate(locale, "options.OptionRefreshControl.unconfirmed");
const message = (error: Error, locale: Locale = "zh-CN") =>
  error instanceof ResearchApiError
    ? (getLabels(locale)[error.code] ??
      `${translate(locale, "common.unavailable")} (${error.code})`)
    : translate(locale, "common.unavailable");
const active = (operation?: Operation | null) =>
  operation?.status === "queued" || operation?.status === "running";

export function OptionRefreshControl({
  symbol,
  onSubmitted,
}: {
  symbol?: string;
  onSubmitted?: () => void;
}) {
  const { locale } = useI18n();
  const client = useQueryClient();
  const seen = useRef("");
  const progress = useQuery({
    queryKey: key,
    queryFn: async () => {
      const response = await api.get("/api/v1/options/refresh/latest", {
        signal: AbortSignal.timeout(10000),
      });
      return response.data ?? null;
    },
    retry: false,
    refetchInterval: (query) => (active(query.state.data) ? 2000 : false),
  });
  const submit = useMutation({
    mutationFn: async () => {
      const { data } = await api.post("/api/v1/options/refresh", {
        body: symbol ? { symbol } : {},
        signal: AbortSignal.timeout(10000),
      });
      if (!data) throw new Error("OPTIONS_REFRESH_UNAVAILABLE");
      return data;
    },
    retry: false,
    onSuccess: (operation) => {
      client.setQueryData(key, operation);
      onSubmitted?.();
    },
    onError: () => {
      void progress.refetch();
    },
  });
  const operation = progress.data;
  const version =
    operation?.items
      .filter((i) => i.status === "completed")
      .map((i) => i.snapshot_id)
      .join(",") ?? "";
  useEffect(() => {
    if (active(operation) || !version || version === seen.current) return;
    seen.current = version;
    void client.invalidateQueries({
      predicate: (query) =>
        [
          "options",
          "option-analytics",
          "option-structure-map",
          "research-option-pulse",
          "options-overview",
          "options-board",
          "options-collection",
        ].includes(String(query.queryKey[0])),
    });
  }, [client, version, operation]);
  const busy = submit.isPending || active(operation);
  const visible =
    operation && (!symbol || operation.items.some((i) => i.symbol === symbol));
  const done =
    operation?.items.filter((i) => i.status === "completed" || i.status === "failed")
      .length ?? 0;
  const failed = operation?.items.filter((i) => i.status === "failed").length ?? 0;
  return (
    <div className="option-refresh-control">
      <button type="button" disabled={busy} onClick={() => submit.mutate()}>
        {busy
          ? translate(locale, "options.OptionRefreshControl.optionsUpdateInProgress")
          : symbol
            ? translate(
                locale,
                "options.OptionRefreshControl.updateThisInstrumentSOptions",
              )
            : translate(locale, "options.OptionRefreshControl.updateAllOptions")}
      </button>
      <span>
        {translate(
          locale,
          "options.OptionRefreshControl.manuallyCollectsOpenDOptionsAndCalculationSpotPricesStock",
        )}
      </span>
      {submit.isError && (
        <div role="alert">
          {message(submit.error, locale)}
          {translate(
            locale,
            "options.OptionRefreshControl.theLastSuccessfulSnapshotIsRetained",
          )}
          <details>
            <summary>{translate(locale, "common.originalDetails")}</summary>
            <p>{detailText(submit.error.message, locale)}</p>
            {submit.error instanceof ResearchApiError && (
              <code>
                {submit.error.code} {submit.error.requestId}
              </code>
            )}
          </details>
        </div>
      )}
      {progress.isError && (
        <div role="alert">
          {translate(locale, "options.OptionRefreshControl.taskStatusRequestFailed")}
          {message(progress.error, locale)}.
          <button type="button" onClick={() => void progress.refetch()}>
            {translate(locale, "options.OptionRefreshControl.retryTaskStatus")}
          </button>
          <details>
            <summary>{translate(locale, "common.originalDetails")}</summary>
            <p>{detailText(progress.error.message, locale)}</p>
            {progress.error instanceof ResearchApiError && (
              <code>
                {progress.error.code} {progress.error.requestId}
              </code>
            )}
          </details>
        </div>
      )}
      {visible && (
        <>
          <p role="status">
            {getLabels(locale)[operation.status]}{" "}
            {translate(locale, "options.OptionRefreshControl.processed")} {done}/
            {operation.items.length}{" "}
            {translate(locale, "options.OptionRefreshControl.succeeded")}{" "}
            {done - failed} {translate(locale, "options.OptionRefreshControl.failed")}{" "}
            {failed}
            {active(operation)
              ? translate(
                  locale,
                  "options.OptionRefreshControl.continuesInTheBackgroundAcrossPagesMayRemainQueued",
                )
              : translate(
                  locale,
                  "options.OptionRefreshControl.successfulCollectionDoesNotMeanEveryContractQuoteWas",
                )}
          </p>
          <details>
            <summary>
              {translate(
                locale,
                "options.OptionRefreshControl.updateResultsByInstrument",
              )}
            </summary>
            <ul>
              {operation.items.map((item) => (
                <li key={item.symbol}>
                  {item.symbol.replace("US.", "")}:{getLabels(locale)[item.status]}
                  {item.reason
                    ? translate(
                        locale,
                        "options.OptionRefreshControl.lastSuccessfulDataRetained",
                        {
                          value:
                            getLabels(locale)[item.reason] ??
                            `${translate(locale, "common.unknown")} (${item.reason})`,
                        },
                      )
                    : ""}
                  {item.captured_at
                    ? translate(
                        locale,
                        "options.OptionRefreshControl.collectedUnderlyingQuote",
                        {
                          time: time(item.captured_at, locale),
                          time2: time(item.market_at, locale),
                        },
                      )
                    : ""}
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </div>
  );
}
