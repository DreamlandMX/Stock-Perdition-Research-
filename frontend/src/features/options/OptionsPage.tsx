import { detailText } from "../../i18n/details";
import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useEffect, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { snapshotContextOptions } from "../../api/snapshot-context";
import {
  snapshotQueryFromSearch,
  SNAPSHOT_IDENTITY_KEYS,
} from "../../app/snapshot-refresh";
import { decodeResearchUrlState } from "../../app/url-state";
import { OptionsBoard } from "../overview/OptionsBoard";
import { OptionsDivergenceScatter } from "../overview/OptionsDivergenceScatter";
import { OptionCollectionMatrix } from "../health/OptionCollectionMatrix";
import { buildOverviewHistory } from "../overview/overview-history";
import { useOverviewHistory } from "../overview/useOverviewHistory";
import { useOptionsOverview } from "./useOptionsOverview";
import { useOptionsBoard } from "./useOptionsBoard";
import { impliedCaveat } from "./options-copy";
import { OptionRefreshControl } from "./OptionRefreshControl";
import "../overview/overview.css";
import "../overview/overview-map.css";
import "../overview/overview-v6.css";

export function OptionsPage() {
  const { locale } = useI18n();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const request = {
    ...snapshotQueryFromSearch(searchParams.toString()),
  };
  const overview = useQuery({
    ...snapshotContextOptions(request),
  });
  const snapshot = overview.data?.resolved_snapshot;
  useEffect(() => {
    if (!overview.data) return;
    const next = new URLSearchParams(searchParams);
    for (const key of SNAPSHOT_IDENTITY_KEYS) {
      if (!(key in overview.data.resolved_snapshot)) continue;
      const value =
        overview.data.resolved_snapshot[
          key as keyof typeof overview.data.resolved_snapshot
        ];
      if (value == null) next.delete(key);
      else next.set(key, String(value));
    }
    if (next.toString() === searchParams.toString()) return;
    queryClient.setQueryData(
      snapshotContextOptions(snapshotQueryFromSearch(next.toString())).queryKey,
      overview.data,
    );
    setSearchParams(next, { replace: true });
  }, [overview.data, searchParams, setSearchParams, queryClient]);

  const historical = searchParams.get("option_time") === "historical";
  const optionQuery = {
    snapshot_id: snapshot?.snapshot_id,
    as_of: snapshot?.as_of,
    latest: !historical,
  };
  const board = useOptionsBoard(optionQuery, !!snapshot);
  const options = useOptionsOverview(optionQuery, !!snapshot && !board.isPending);
  const retryOptions = async () => {
    await board.refetch();
    await options.refetch();
  };
  const symbols = useMemo(
    () => (board.data?.items ?? options.data?.items ?? []).map((item) => item.symbol),
    [board.data?.items, options.data?.items],
  );
  const metadata = useQuery({
    ...snapshotContextOptions({ snapshot_id: snapshot?.snapshot_id ?? null, symbols }),
    enabled: !!snapshot && symbols.length > 0,
  });
  const daily = useOverviewHistory(snapshot, symbols);
  const points = metadata.data?.data.instruments ?? [];
  const history = useMemo(
    () => buildOverviewHistory([], daily.histories, snapshot?.as_of ?? "1970-01-01"),
    [daily.histories, snapshot?.as_of],
  );
  const horizonValue = Number(searchParams.get("options_horizon") ?? "5");
  const horizon = horizonValue === 1 || horizonValue === 20 ? horizonValue : 5;
  const linkState = {
    optionMode: historical ? ("daily" as const) : ("latest" as const),
    asOf: snapshot?.as_of,
    snapshotId: snapshot?.snapshot_id,
    datasetRevision: snapshot?.dataset_revision,
    featureSetVersion: snapshot?.feature_set_version,
    universeVersion: snapshot?.universe_version,
    modelVersion: snapshot?.model_version ?? undefined,
    calibrationVersion: snapshot?.calibration_version ?? undefined,
    horizon: decodeResearchUrlState(searchParams).horizon,
  };

  return (
    <section
      className="overview-page ov-page ov-v6 options-page"
      aria-label={translate(locale, "options.OptionsPage.optionsPulse")}
    >
      <header className="ov-chapter">
        <h1>{translate(locale, "options.OptionsPage.optionsPulse")}</h1>
        <p>
          {translate(
            locale,
            "options.OptionsPage.howOptionsMarketsPriceFutureVolatility",
          )}
        </p>
      </header>
      <OptionRefreshControl
        onSubmitted={() => {
          const next = new URLSearchParams(searchParams);
          next.delete("option_time");
          setSearchParams(next, { preventScrollReset: true });
        }}
      />
      <div
        className="option-time-switch"
        role="group"
        aria-label={translate(locale, "options.OptionsPage.optionsDataTime")}
      >
        <button
          type="button"
          aria-pressed={!historical}
          onClick={() => {
            const next = new URLSearchParams(searchParams);
            next.delete("option_time");
            setSearchParams(next);
          }}
        >
          {translate(locale, "options.OptionsPage.latestOptionsSnapshot")}
        </button>{" "}
        <button
          type="button"
          aria-pressed={historical}
          onClick={() => {
            const next = new URLSearchParams(searchParams);
            next.set("option_time", "historical");
            setSearchParams(next);
          }}
        >
          {translate(locale, "options.OptionsPage.historicalOptionsSnapshot")}
        </button>
        <p className="option-risk-neutral">
          {historical
            ? translate(
                locale,
                "options.OptionsPage.optionsUseTheStockResearchSnapshotCutoff",
              )
            : translate(
                locale,
                "options.OptionsPage.optionsIndependentlyUseTheLatestSuccessfulSnapshotStockDaily",
              )}
        </p>
      </div>
      {overview.isPending ? (
        <p role="status">
          {translate(locale, "options.OptionsPage.loadingSavedResearchSnapshot")}
        </p>
      ) : null}
      {overview.isError ? (
        <div role="alert">
          <p>
            {translate(locale, "options.OptionsPage.snapshotRequestFailed")}
            {translate(
              locale,
              "options.OptionsPage.thePageDoesNotSwitchToLatestDataWhen",
            )}
          </p>
          <details>
            <summary>{translate(locale, "common.originalDetails")}</summary>
            <p>{detailText(overview.error.message, locale)}</p>
          </details>
          <button type="button" onClick={() => void overview.refetch()}>
            {translate(locale, "options.OptionsPage.retrySnapshot")}
          </button>
        </div>
      ) : null}
      {snapshot ? (
        <section
          className="ov-section"
          aria-label={translate(locale, "options.OptionsPage.optionsBoard")}
        >
          {(options.isPending || board.isPending) && (
            <p role="status">
              {translate(
                locale,
                "options.OptionsPage.loadingSavedOptionsSnapshotsForTheFullUniverse",
              )}
            </p>
          )}
          {(options.isError || board.isError) && (
            <div role="alert">
              {translate(locale, "options.OptionsPage.optionsPulseRequestFailed")}
              <details>
                <summary>{translate(locale, "common.originalDetails")}</summary>
                <p>{detailText(options.error?.message ?? board.error?.message ?? "", locale)}</p>
              </details>
              <button type="button" onClick={() => void retryOptions()}>
                {translate(locale, "options.OptionsPage.retryOptions")}
              </button>
            </div>
          )}
          {metadata.isError && (
            <p role="alert">
              {translate(locale, "options.OptionsPage.stockNamesCouldNotBeLoaded")}
              <button type="button" onClick={() => void metadata.refetch()}>
                {translate(locale, "options.OptionsPage.retryStockNames")}
              </button>
            </p>
          )}
          {board.data && (
            <OptionsBoard
              board={board.data}
              items={options.data?.items ?? []}
              points={points}
              history={history}
              linkState={linkState}
            />
          )}
          <div className="ob-pair">
            {options.data && (
              <OptionsDivergenceScatter
                items={options.data.items}
                horizon={horizon}
                onHorizonChange={(value) => {
                  const next = new URLSearchParams(searchParams);
                  next.set("options_horizon", String(value));
                  setSearchParams(next);
                }}
                linkState={linkState}
              />
            )}
            {!board.isPending && !options.isPending && (
              <OptionCollectionMatrix
                asOf={snapshot.as_of}
                snapshotId={snapshot.snapshot_id}
              />
            )}
          </div>
          {options.data && board.data && (
            <p className="option-risk-neutral">
              {translate(
                locale,
                "options.OptionsPage.cardsAnd520DayIntervalComparison",
              )}
              {impliedCaveat(locale)}
            </p>
          )}
        </section>
      ) : null}
      {snapshot ? (
        <div
          className="ov-history-status"
          role="status"
          aria-label={translate(
            locale,
            "options.OptionsPage.historyChartLoadingStatus",
          )}
        >
          {daily.pending
            ? translate(
                locale,
                "options.OptionsPage.dailyBarsLoadedOptionsSnapshotInformationIsAvailable",
                { completed: daily.completed, total: daily.total },
              )
            : translate(
                locale,
                "options.OptionsPage.observedDailyBarsRawPricesUnadjusted",
                {
                  value: daily.completed - Object.keys(daily.failed).length,
                  total: daily.total,
                },
              )}
          {Object.keys(daily.failed).length ? (
            <>
              <button type="button" onClick={daily.retry}>
                {translate(locale, "options.retryFailedHistory", {
                  count: Object.keys(daily.failed).length,
                })}
              </button>
              <details>
                <summary>
                  {translate(locale, "options.OptionsPage.failureReasons")} /{" "}
                  {translate(locale, "common.originalDetails")}
                </summary>
                <ul>
                  {Object.entries(daily.failed).map(([symbol, reason]) => (
                    <li key={symbol}>
                      {symbol}: {detailText(reason, locale)}
                    </li>
                  ))}
                </ul>
              </details>
            </>
          ) : null}
          {history.jumps.length ? (
            <p>
              {translate(locale, "options.OptionsPage.rawPriceJumps")}
              {history.jumps.join(";")}
              {translate(
                locale,
                "options.OptionsPage.unadjustedDailyPricesDoNotRepresentTotalReturn",
              )}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
