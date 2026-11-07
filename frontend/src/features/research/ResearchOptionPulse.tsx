import { ResearchErrorDetail } from "./ResearchErrorDetail";
import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { createResearchApiClient } from "../../api/client";
import type { components, paths } from "../../api/generated/schema";
import { formatUsMarketTime } from "../../app/presentation";
import { money, number, percent } from "../options/options-board-rows";
import { OptionPulseChart } from "./OptionPulseChart";
import "./option-pulse.css";
type Pulse = components["schemas"]["OptionPulseResponse"];
const api = createResearchApiClient({ trackConnectivity: false });
const base = "/api/v1/instruments/{symbol}/options/pulse" as const;
const time = (at: string | null | undefined, locale: Locale = "zh-CN") =>
  at
    ? formatUsMarketTime(at, locale)
    : translate(locale, "research.optionanalyticspanel.unconfirmed");
const reasons = (locale: Locale = "zh-CN"): Record<string, string> => ({
  inactive: translate(locale, "research.researchoptionpulse.intradayCollectionPaused"),
  active: translate(
    locale,
    "research.researchoptionpulse.intradayUpdateForThisInstrument",
  ),
  collecting: translate(
    locale,
    "research.researchoptionpulse.collectingTheFullOptionsChain",
  ),
  delayed: translate(
    locale,
    "research.researchoptionpulse.marketDataOrCollectionDelayed",
  ),
  worker_offline: translate(
    locale,
    "research.researchoptionpulse.intradayCollectionWorkerDisconnected",
  ),
  market_closed: translate(
    locale,
    "research.researchoptionpulse.outsideRegularTradingHoursCollectionPaused",
  ),
  waiting_first_collection: translate(
    locale,
    "research.researchoptionpulse.waitingForTheFirstIntradayCollection",
  ),
  opend_busy: translate(
    locale,
    "research.researchoptionpulse.marketDataChannelBusyThisCycleSkipped",
  ),
  incomplete_snapshots: translate(
    locale,
    "research.researchoptionpulse.incompleteOptionsChainKeepingThePreviousSuccessfulData",
  ),
  no_option_contracts: translate(
    locale,
    "research.researchoptionpulse.noAvailableOptionsContractsFound",
  ),
  permission_denied: translate(
    locale,
    "research.researchoptionpulse.insufficientOptionsOrCandlestickMarketDataPermissions",
  ),
  rate_limited: translate(
    locale,
    "research.researchoptionpulse.providerRateLimitWaitingForTheNextCycle",
  ),
  calendar_or_data_invalid: translate(
    locale,
    "research.researchoptionpulse.tradingCalendarOrMarketDataInvalid",
  ),
  no_option_snapshot: translate(
    locale,
    "research.researchoptionpulse.noOptionsSnapshotAtThisFixedCutoff",
  ),
  stock_history_unavailable: translate(
    locale,
    "research.researchoptionpulse.stockHistoryLoadFailedOptionsDataRemainsAvailable",
  ),
  pulse_data_invalid: translate(
    locale,
    "research.researchoptionpulse.savedDataCouldNotBeRead",
  ),
  connection_failed: translate(locale, "research.optionspanel.opendConnectionFailed"),
  sdk_error: translate(locale, "research.optionspanel.providerRequestFailed"),
  intraday_bars_unavailable: translate(
    locale,
    "research.researchoptionpulse.todayS5MinuteCandlesUnavailable",
  ),
  collection_timeout: translate(
    locale,
    "research.researchoptionpulse.collectionTimedOutThisCycleStopped",
  ),
  not_trading_day: translate(
    locale,
    "research.researchoptionpulse.marketHolidayCollectionPaused",
  ),
});
export function ResearchOptionPulse({ symbol }: { symbol: string }) {
  const { locale } = useI18n();
  const [params, setParams] = useSearchParams();
  const historical =
    params.get("pulse_mode") === "daily" || params.has("option_snapshot");
  const mode = historical ? "daily" : "latest";
  const snapshotId = params.get("snapshot_id") ?? undefined;
  const optionSnapshot = params.get("option_snapshot") ?? undefined;
  const asOf = params.get("as_of") ?? undefined;
  const query: NonNullable<paths[typeof base]["get"]["parameters"]["query"]> = {
    mode,
    ...(snapshotId ? { snapshot_id: snapshotId } : {}),
    ...(historical && optionSnapshot ? { option_snapshot_id: optionSnapshot } : {}),
    ...(historical && asOf ? { as_of: asOf } : {}),
  };
  const result = useQuery({
    queryKey: ["research-option-pulse", symbol, query],
    queryFn: async ({ signal }): Promise<Pulse> => {
      const { data } = await api.get(base, {
        params: { path: { symbol }, query },
        signal,
      });
      if (!data) throw new Error("Options Pulse response unavailable");
      return data;
    },
    retry: false,
    staleTime: Infinity,
  });
  const data = result.data;
  const supported = data?.data_status !== "unsupported";
  const missingHorizons =
    data?.mode !== "live" && data?.cone.length
      ? Array.from({ length: 10 }, (_, i) => i + 1).filter(
          (h) => !data.cone.some((p) => p.horizon_days === h),
        )
      : [];
  const choose = (value: "latest" | "daily") => {
    const next = new URLSearchParams(params);
    next.set("pulse_mode", value);
    if (value === "latest") next.delete("option_snapshot");
    setParams(next, { preventScrollReset: true });
  };
  return (
    <section
      className="research-option-pulse"
      aria-label={translate(
        locale,
        "research.researchoptionpulse.instrumentOptionsPulse",
      )}
    >
      <header className="research-option-pulse__header">
        <div>
          <h3>
            {translate(locale, "research.researchoptionpulse.instrumentOptionsPulse")}
          </h3>
          <p>
            {mode === "latest"
              ? translate(
                  locale,
                  "research.researchoptionpulse.manualOptionsSnapshotStockShowsSavedClosingDailyBars",
                )
              : translate(
                  locale,
                  "research.researchoptionpulse.historicalSnapshotRawClosingDailyBarsImpliedVolatilityAnd",
                )}
          </p>
        </div>
        <div
          className="research-option-pulse__switch"
          role="group"
          aria-label={translate(
            locale,
            "research.researchoptionpulse.optionsPulsePeriod",
          )}
        >
          <button
            type="button"
            aria-pressed={mode === "latest"}
            onClick={() => choose("latest")}
            disabled={!supported}
          >
            {translate(locale, "research.researchoptionpulse.latestOptionsSnapshot")}
          </button>
          <button
            type="button"
            aria-pressed={mode === "daily"}
            onClick={() => choose("daily")}
          >
            {translate(locale, "research.researchoptionpulse.historicalSnapshot")}
          </button>
        </div>
      </header>
      {!supported ? (
        <p className="options-notice">
          {translate(
            locale,
            "research.researchoptionpulse.thisInstrumentIsOutsideTheSiteWideOptionsResearch",
          )}
        </p>
      ) : (
        <>
          {result.isPending && (
            <p role="status">
              {translate(locale, "research.optionpulsetiles.loadingOptionsPulse")}
            </p>
          )}
          {result.isError && (
            <div role="alert">
              {translate(
                locale,
                "research.researchoptionpulse.unableToLoadOptionsPulse",
              )}
              <ResearchErrorDetail message={result.error.message} />{locale === "en-US" ? "." : "。"}
              <button type="button" onClick={() => void result.refetch()}>
                {translate(locale, "research.researchoptionpulse.retry")}
              </button>
            </div>
          )}
          {mode === "latest" && (
            <p className="research-option-pulse__status" role="status">
              {translate(
                locale,
                "research.researchoptionpulse.useTheUpdateButtonAboveToCollectOptionsData.withValues",
                {
                  translate: data?.captured_at
                    ? translate(locale, "research.researchoptionpulse.lastCollected", {
                        capturedAt: time(data.captured_at, locale),
                      })
                    : "",
                },
              )}
            </p>
          )}
          {data?.spot != null && (
            <>
              <div className="research-option-pulse__metrics">
                <span>
                  {translate(locale, "research.optionPulseChart.referencePrice")}
                  <b>${number(data.spot, undefined, locale)}</b>
                </span>
                <span>
                  ATM IV30 <b>{percent(data.iv30, undefined, locale)}</b>
                </span>
                <span>
                  {translate(locale, "research.optionpulsetiles.pCVolume")}
                  <b>{number(data.put_call_volume, undefined, locale)}</b>
                </span>
                <span>
                  {translate(
                    locale,
                    "research.researchoptionpulse.cumulativeTurnoverToday",
                  )}
                  <b>{money(data.total_turnover, locale)}</b>
                </span>
                <span>
                  {translate(locale, "research.researchoptionpulse.callTurnoverPeak")}
                  <b className="pulse-call">
                    {number(data.call_peak, undefined, locale)}
                  </b>
                </span>
                <span>
                  {translate(locale, "research.researchoptionpulse.putTurnoverPeak")}
                  <b className="pulse-put">
                    {number(data.put_peak, undefined, locale)}
                  </b>
                </span>
              </div>
              <OptionPulseChart
                bars={data.bars}
                profile={data.profile}
                cone={data.cone}
                levels={{
                  spot: data.spot,
                  call: data.call_peak ?? null,
                  put: data.put_peak ?? null,
                }}
                timeframe={data.timeframe}
              />
              {!data.bars.length && (
                <p className="options-notice">
                  {translate(
                    locale,
                    "research.researchoptionpulse.noStockCandlesAreAvailableAtThisCutoffOnly",
                  )}
                </p>
              )}
              {!data.cone.length && (
                <p className="options-notice">
                  {translate(
                    locale,
                    "research.researchoptionpulse.insufficientOptionsMaturitiesForMultiDayImpliedIntervals",
                  )}
                </p>
              )}
              {!!missingHorizons.length && (
                <p className="options-notice">
                  {translate(locale, "research.researchoptionpulse.day.withValues", {
                    missingHorizons: missingHorizons.join(locale === "en-US" ? ", " : "、"),
                  })}
                </p>
              )}
              <p className="research-option-pulse__meta">
                {translate(
                  locale,
                  "research.researchoptionpulse.underlyingMarketData.withValues",
                  {
                    marketAt: time(data.market_at, locale),
                    capturedAt: time(data.captured_at, locale),
                    validTurnoverContracts: data.valid_turnover_contracts,
                    expectedContracts: data.expected_contracts,
                    missingTurnoverContracts: data.missing_turnover_contracts,
                    excludedTurnoverContracts: data.excluded_turnover_contracts,
                    translate: data.complete
                      ? translate(locale, "research.optionspanel.batchComplete")
                      : translate(locale, "research.researchoptionpulse.partialBatch"),
                  },
                )}
              </p>
              <p className="research-option-pulse__note">
                {translate(
                  locale,
                  "research.researchoptionpulse.greenCallRedPutCumulativeTurnoverTodayUsdNot",
                )}
              </p>
            </>
          )}
          {data && data.spot == null && (
            <p className="options-notice">
              {data.reasons.map((r) => reasons(locale)[r] ?? r).join(locale === "en-US" ? "; " : "；") ||
                (mode === "latest"
                  ? translate(
                      locale,
                      "research.researchoptionpulse.noSuccessfulOptionsSnapshotUseTheUpdateButtonAbove",
                    )
                  : translate(
                      locale,
                      "research.researchoptionpulse.noFixedSnapshotAvailable",
                    ))}
            </p>
          )}
          {data?.snapshot_id && (
            <details className="research-option-pulse__source">
              <summary>
                {translate(locale, "research.researchoptionpulse.dataSourceAndScope")}
              </summary>
              <p>
                {translate(
                  locale,
                  "research.researchoptionpulse.sourceFutuOpendExpiry.withValues",
                  {
                    dteMin: data.dte_min,
                    dteMax: data.dte_max,
                    strikeMinRatio: data.strike_min_ratio,
                    strikeMaxRatio: data.strike_max_ratio,
                  },
                )}
              </p>
              <p>
                {translate(
                  locale,
                  "research.researchoptionpulse.optionsSnapshot.withValues",
                  {
                    snapshotId: data.snapshot_id,
                    stockBatchId:
                      data.stock_batch_id ??
                      translate(locale, "research.instrumentidentity.notProvided"),
                  },
                )}
              </p>
              <p>
                {translate(
                  locale,
                  "research.researchoptionpulse.contractMarketTimeRange.withValues",
                  {
                    contractMarketMinAt: time(data.contract_market_min_at, locale),
                    contractMarketMaxAt: time(data.contract_market_max_at, locale),
                  },
                )}
              </p>
              <p>
                {translate(
                  locale,
                  "research.researchoptionpulse.calculationVersion.withValues",
                  { formulaVersion: data.formula_version },
                )}
              </p>
              {!!data.reasons.length && (
                <p>
                  {translate(
                    locale,
                    "research.researchoptionpulse.dataStatus.withValues",
                    {
                      reasons: data.reasons
                        .map((r) => reasons(locale)[r] ?? r)
                        .join(locale === "en-US" ? "; " : "；"),
                    },
                  )}
                </p>
              )}
            </details>
          )}
        </>
      )}
    </section>
  );
}
