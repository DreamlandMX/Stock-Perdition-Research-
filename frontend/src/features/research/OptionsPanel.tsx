import { researchEvidenceLabel } from "./research-labels";
import { ResearchErrorDetail } from "./ResearchErrorDetail";
import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Link } from "react-router-dom";
import { OptionPulseTiles } from "./OptionPulseTiles";
import { ResearchOptionPulse } from "./ResearchOptionPulse";
import { OptionModelVsMarket } from "./OptionModelVsMarket";
import { OptionStructureMap } from "./OptionStructureMap";
import { ChartExplainer } from "../options/ChartExplainer";
import { useOptionsOverview } from "../options/useOptionsOverview";
import { OptionRefreshControl } from "../options/OptionRefreshControl";
import { createResearchApiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { formatUsMarketTime } from "../../app/presentation";
import { money } from "../options/options-board-rows";
import { optionQuery, type OptionQuery } from "./options-state";
import {
  OptionAnalyticsPanel,
  loadOptionAnalyticsFromApi,
} from "./OptionAnalyticsPanel";
import { OptionForecastPanel, loadOptionForecastFromApi } from "./OptionForecastPanel";
import "./options.css";
type Response = components["schemas"]["OptionResponse"];
const client = createResearchApiClient({ trackConnectivity: false });
const labels = (locale: Locale = "zh-CN") => ({
  premium: translate(locale, "research.optionspanel.estimatedTurnover"),
  oi: translate(locale, "research.optionspanel.openInterestOi"),
  volume: translate(locale, "research.optionPulseChart.volume"),
  iv: translate(locale, "research.optionspanel.impliedVolatilityIv"),
  gamma: translate(locale, "research.optionspanel.gammaConcentration"),
  spread: translate(locale, "research.optionspanel.bidAskSpread"),
});
const reasons = (locale: Locale = "zh-CN"): Record<string, string> => ({
  permission_denied: translate(
    locale,
    "research.optionspanel.insufficientOptionsMarketDataPermissions",
  ),
  connection_failed: translate(locale, "research.optionspanel.opendConnectionFailed"),
  rate_limited: translate(locale, "research.optionspanel.providerRateLimit"),
  no_option_snapshot: translate(
    locale,
    "research.optionanalyticspanel.noSavedOptionsSnapshot",
  ),
  symbol_outside_option_scope: translate(
    locale,
    "research.optionspanel.optionsDataCoversSpyQqqSevenTechnologyStocksAnd",
  ),
  metric_unavailable: translate(
    locale,
    "research.optionspanel.eligibleDataIsUnavailableForThisMetric",
  ),
  sample_only_not_full_chain: translate(
    locale,
    "research.optionspanel.sampleOnlyNotAFullOptionsChain",
  ),
  oi_date_unproven: translate(locale, "research.optionspanel.oiDateUnconfirmed"),
  greeks_timestamp_unproven: translate(
    locale,
    "research.optionanalyticspanel.greeksCalculationTimeUnconfirmed",
  ),
  quote_timestamp_unproven: translate(
    locale,
    "research.optionspanel.independentBidAskQuoteTimeUnconfirmed",
  ),
  nonstandard_or_unknown_delivery: translate(
    locale,
    "research.optionspanel.nonstandardOrUnknownDeliverable",
  ),
  option_fields_unavailable: translate(
    locale,
    "research.optionspanel.optionsFieldsUnavailable",
  ),
  observed_time_missing_or_invalid: translate(
    locale,
    "research.optionspanel.marketDataTimeMissingOrInvalid",
  ),
  crossed_quote: translate(locale, "research.optionspanel.bidExceedsAskQuoteExcluded"),
  zero_or_one_sided_quote: translate(
    locale,
    "research.optionspanel.zeroBidOrOneSidedQuote",
  ),
  underlying_price_missing_range_unfiltered: translate(
    locale,
    "research.optionspanel.underlyingPriceMissingStrikeFilteringBySpotIsUnavailable",
  ),
  partial_response: translate(
    locale,
    "research.optionspanel.incompleteProviderResponse",
  ),
  sdk_error: translate(locale, "research.optionspanel.providerRequestFailed"),
  future_market_time: translate(
    locale,
    "research.optionspanel.marketTimeIsLaterThanLocalAvailabilityExcluded",
  ),
});
export async function loadOptionsFromApi(
  symbol: string,
  query: OptionQuery,
): Promise<Response> {
  const { data } = await client.get("/api/v1/instruments/{symbol}/options/heatmap", {
    params: { path: { symbol }, query },
    signal: AbortSignal.timeout(15000),
  });
  if (!data) throw new Error("Options response unavailable");
  return data;
}
const time = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  value
    ? formatUsMarketTime(value, locale)
    : translate(locale, "research.optionanalyticspanel.unconfirmed");
function OptionCollectionStatus() {
  const { locale } = useI18n();
  const [params] = useSearchParams();
  return (
    <p className="options-notice">
      {translate(
        locale,
        "research.optionspanel.collectionRunsTradingCalendarAndSchedulerStatus",
      )}
      <Link to={`/health?${params.toString()}#options`}>
        {translate(locale, "research.optionspanel.viewCollectionMatrix")}
      </Link>
    </p>
  );
}
export function OptionsPanel({
  symbol,
  loadData = loadOptionsFromApi,
  loadAnalytics = loadOptionAnalyticsFromApi,
  loadForecast = loadOptionForecastFromApi,
}: {
  symbol: string;
  loadData?: (symbol: string, query: OptionQuery) => Promise<Response>;
  loadAnalytics?: typeof loadOptionAnalyticsFromApi;
  loadForecast?: typeof loadOptionForecastFromApi;
}) {
  const { locale } = useI18n();
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const historical =
    params.get("pulse_mode") === "daily" || params.has("option_snapshot");
  const asOf = historical ? (params.get("as_of") ?? undefined) : undefined;
  const query = useMemo(() => {
    const selection = new URLSearchParams(params);
    if (!asOf) selection.delete("as_of");
    return optionQuery(selection);
  }, [params, asOf]);
  const overview = useOptionsOverview(
    {
      symbol,
      snapshot_id: params.get("snapshot_id") ?? undefined,
      option_snapshot_id: query.snapshot_id ?? undefined,
      as_of: asOf,
      latest: !historical,
    },
    params.get("option_tab") === "term",
  );
  const result = useQuery({
    queryKey: ["options", symbol, query],
    queryFn: () => loadData(symbol, query),
    retry: false,
    staleTime: 60000,
  });
  const [selectedId, setSelectedId] = useState<string>();
  const data = result.data?.data;
  const snapshot = data?.snapshot;
  const tabNames = ["structure", "term", "comparison", "sources"] as const;
  const tab = tabNames.find((name) => name === params.get("option_tab")) ?? "structure";
  const hasSelectedTab = params.has("option_tab");
  const structureMap =
    tab === "structure" &&
    params.get("option_view") !== "legacy" &&
    !params.has("option_metric");
  useEffect(() => {
    if (hasSelectedTab)
      document.getElementById("options")?.scrollIntoView({ block: "start" });
  }, [tab, hasSelectedTab]);
  const metric = query.metric ?? "premium";
  const analyticsQuery = {
    ...query,
    ...(snapshot ? { snapshot_id: snapshot.snapshot_id } : {}),
  };
  const analytics = useQuery({
    queryKey: [
      "option-analytics",
      symbol,
      analyticsQuery.snapshot_id,
      analyticsQuery.as_of,
      params.get("snapshot_id") ?? undefined,
    ],
    queryFn: () =>
      loadAnalytics(symbol, analyticsQuery, params.get("snapshot_id") ?? undefined),
    enabled: !!snapshot,
    retry: false,
    staleTime: 60000,
  });
  const cells = useMemo(() => data?.cells ?? [], [data]);
  const visibleContracts = useMemo(() => {
    const ids = new Set(cells.flatMap((c) => c.contract_ids));
    return snapshot?.contracts.filter((row) => ids.has(row.contract_id)) ?? [];
  }, [cells, snapshot]);
  const selected =
    visibleContracts.find((row) => row.contract_id === selectedId) ??
    visibleContracts[0];
  const update = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { flushSync: true, preventScrollReset: true });
  };
  const selectCell = useCallback(
    (index: number) => setSelectedId(cells[index]?.contract_ids[0]),
    [cells],
  );
  const format = useCallback(
    (value: number | null | undefined) =>
      value == null
        ? translate(locale, "research.optionanalyticspanel.missing")
        : metric === "iv"
          ? `${(value * 100).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}%`
          : `${metric === "premium" || metric === "spread" || metric === "gamma" ? "$" : ""}${value.toLocaleString(locale, { maximumFractionDigits: 2 })}`,
    [metric, locale],
  );
  const requestedScale = Number(params.get("option_scale"));
  const scale =
    requestedScale > 0 && Number.isFinite(requestedScale) ? requestedScale : undefined;
  const net = query.side === "net";
  const autoMax = Math.max(0, ...cells.map((c) => Math.abs(c.value ?? 0)));
  const scaleMax = scale ?? (autoMax > 0 ? autoMax : 1);
  const unit =
    metric === "iv"
      ? translate(locale, "research.optionanalyticspanel.annualizedIv")
      : metric === "oi" || metric === "volume"
        ? translate(locale, "research.optionspanel.contracts")
        : metric === "gamma"
          ? translate(locale, "research.optionspanel.usd1UnderlyingMove")
          : translate(locale, "research.optionspanel.usd");
  const compact = (value: number | null | undefined) =>
    value == null
      ? translate(locale, "research.optionanalyticspanel.missing")
      : metric === "premium" || metric === "gamma"
        ? money(value, locale)
        : format(value);
  const additive =
    data?.aggregation === "sum" || data?.aggregation === "call_minus_put_sum";
  const summaryLabel = additive
    ? translate(locale, "research.optionspanel.total")
    : translate(locale, "research.optionspanel.mean");
  const minStrike = data?.strikes[0];
  const maxStrike = data?.strikes.at(-1);
  const underlying = snapshot?.underlying_price;
  const summaries: [string, Record<string, number | null>][] = [
    [
      translate(locale, "research.optionanalyticspanel.strike"),
      data?.strike_summary ?? {},
    ],
    [translate(locale, "research.optionspanel.expiry"), data?.expiry_summary ?? {}],
  ];
  const strikeSummary = useMemo(
    () =>
      new Map(
        Object.entries(data?.strike_summary ?? {}).map(([strike, value]) => [
          Number(strike),
          value,
        ]),
      ),
    [data?.strike_summary],
  );
  const heatRef = useRef<HTMLDivElement>(null);
  const [focusedCell, setFocusedCell] = useState<number>();
  const cellLookup = useMemo(
    () =>
      new Map(
        cells.map((cell, index) => [
          `${cell.strike}:${cell.expiration}`,
          { cell, index },
        ]),
      ),
    [cells],
  );
  const nearStrike = data?.strikes.reduce(
    (nearest, strike) =>
      underlying != null &&
      Math.abs(strike - underlying) < Math.abs(nearest - underlying)
        ? strike
        : nearest,
    data.strikes[0] ?? 0,
  );
  useEffect(() => {
    const container = heatRef.current;
    const row = container?.querySelector<HTMLElement>("[data-near-spot='true']");
    if (container && row)
      container.scrollTop +=
        row.getBoundingClientRect().top -
        container.getBoundingClientRect().top -
        container.clientHeight / 2 +
        row.offsetHeight / 2;
  }, [snapshot?.snapshot_id, minStrike, maxStrike, tab]);
  const activeCell =
    cells[focusedCell ?? -1] ??
    cells.find((cell) => selected && cell.contract_ids.includes(selected.contract_id));
  const cellDescription = (cell: (typeof cells)[number]) =>
    translate(locale, "research.optionspanel.strikeValidMissingExcluded", {
      strike: cell.strike,
      expiration: cell.expiration,
      value: labels(locale)[metric],
      format: format(cell.value),
      unit: unit,
      validCount: cell.valid_count,
      missingCount: cell.missing_count,
      excludedCount: cell.excluded_count,
    });
  return (
    <section
      id="options"
      aria-label={translate(locale, "research.optionspanel.optionsStructure")}
      className="options-panel"
    >
      <header className="options-header">
        <div>
          <span className="options-eyebrow">
            {translate(locale, "research.optionspanel.optionsStructureMarketData")}
          </span>
          <h2>{translate(locale, "research.optionspanel.optionsStructure")}</h2>
          <p>
            {translate(
              locale,
              "research.optionspanel.viewOpenInterestAndVolatilityStructureByExpiryAnd",
            )}
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            void result.refetch();
            void queryClient.invalidateQueries({ queryKey: ["options-overview"] });
            void queryClient.invalidateQueries({
              queryKey: ["option-collection-monitor", symbol],
            });
            void queryClient.invalidateQueries({
              queryKey: ["option-analytics", symbol],
            });
            void queryClient.invalidateQueries({
              queryKey: ["option-forecast", symbol],
            });
          }}
          disabled={result.isFetching}
        >
          {translate(locale, "research.optionspanel.reloadSnapshot")}
        </button>
      </header>
      <div className="options-snapshot-bar">
        <label>
          {translate(locale, "research.optionspanel.snapshot")}
          <select
            value={query.snapshot_id ?? ""}
            onChange={(e) => update("option_snapshot", e.target.value)}
          >
            <option value="">
              {translate(
                locale,
                "research.optionspanel.latestCompleteSnapshotLatestAttemptIfNoneIsComplete",
              )}
            </option>
            {result.data?.available_snapshots.map((s) => (
              <option key={s.snapshot_id} value={s.snapshot_id}>
                {time(s.captured_at, locale)};{" "}
                {s.complete
                  ? translate(locale, "research.optionspanel.batchComplete")
                  : translate(locale, "research.optionspanel.partialFailed")}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => update("option_tab", "sources")}>
          {snapshot
            ? translate(locale, "research.optionspanel.options", {
                translate:
                  snapshot.market_as_of || snapshot.underlying_at
                    ? translate(locale, "research.optionspanel.marketData")
                    : translate(locale, "research.optionspanel.collection"),
                marketAsOf: time(
                  snapshot.market_as_of ??
                    snapshot.underlying_at ??
                    snapshot.captured_at,
                  locale,
                ),
                translate2: snapshot.complete
                  ? translate(locale, "research.optionspanel.complete")
                  : translate(locale, "research.optionspanel.partial"),
              })
            : translate(locale, "research.optionanalyticspanel.noSavedOptionsSnapshot")}
        </button>
        <Link to="/health#options">
          {translate(locale, "research.optionspanel.collectionMonitoring")}
        </Link>
      </div>
      <OptionRefreshControl
        symbol={symbol}
        onSubmitted={() => {
          const next = new URLSearchParams(params);
          next.set("pulse_mode", "latest");
          next.delete("option_snapshot");
          setParams(next, { preventScrollReset: true });
        }}
      />
      <ResearchOptionPulse symbol={symbol} />
      {!structureMap && (
        <OptionPulseTiles
          data={analytics.data?.data}
          pending={analytics.isPending && !!snapshot}
          error={analytics.isError ? analytics.error.message : undefined}
        />
      )}
      <div
        className="options-tabs"
        role="tablist"
        aria-label={translate(locale, "research.optionspanel.optionsResearchViews")}
      >
        {tabNames.map((name, index) => (
          <button
            key={name}
            id={`option-tab-${name}`}
            role="tab"
            type="button"
            aria-controls={`option-panel-${name}`}
            aria-selected={tab === name}
            tabIndex={tab === name ? 0 : -1}
            onClick={() => update("option_tab", name)}
            onKeyDown={(event) => {
              const nextIndex =
                event.key === "ArrowRight"
                  ? (index + 1) % 4
                  : event.key === "ArrowLeft"
                    ? (index + 3) % 4
                    : event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? 3
                        : -1;
              if (nextIndex < 0) return;
              event.preventDefault();
              const nextName = tabNames[nextIndex]!;
              update("option_tab", nextName);
              document.getElementById(`option-tab-${nextName}`)?.focus();
            }}
          >
            {
              [
                translate(locale, "research.optionspanel.structureHeatmap"),
                translate(locale, "research.optionspanel.termAndSkew"),
                translate(locale, "research.optionspanel.modelVsMarket"),
                translate(locale, "research.optionspanel.dataSources"),
              ][index]
            }
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`option-panel-${tab}`}
        aria-labelledby={`option-tab-${tab}`}
      >
        {structureMap && (
          <OptionStructureMap
            symbol={symbol}
            query={analyticsQuery}
            enabled={!!result.data}
          />
        )}
        {tab === "structure" && !structureMap && (
          <button
            type="button"
            onClick={() => {
              const next = new URLSearchParams(params);
              next.delete("option_metric");
              next.set("option_view", "exposure");
              setParams(next, { preventScrollReset: true });
            }}
          >
            {translate(locale, "research.optionspanel.returnToGexVexHeatmap")}
          </button>
        )}
        {tab === "structure" && !structureMap && (
          <div className="options-controls">
            <label>
              {translate(locale, "research.optionspanel.metric")}
              <select
                value={metric}
                onChange={(e) => update("option_metric", e.target.value)}
              >
                {Object.entries(labels(locale)).map(([key, label]) => (
                  <option value={key} key={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {translate(locale, "research.optionspanel.contractSide")}
              <select
                value={query.side}
                onChange={(e) => update("option_side", e.target.value)}
              >
                <option value="call">Call</option>
                <option value="put">Put</option>
                <option value="net" disabled={metric === "iv"}>
                  {translate(locale, "research.optionspanel.callPutNet")}
                </option>
              </select>
            </label>
            <label>
              {translate(locale, "research.optionspanel.minimumDaysToExpiry")}
              <input
                type="number"
                min="1"
                max="90"
                value={query.dte_min}
                onChange={(e) => update("option_dte_min", e.target.value)}
              />
            </label>
            <label>
              {translate(locale, "research.optionspanel.maximumDaysToExpiry")}
              <input
                type="number"
                min="1"
                max="90"
                value={query.dte_max}
                onChange={(e) => update("option_dte_max", e.target.value)}
              />
            </label>
            <label>
              {translate(locale, "research.optionspanel.minimumStrikeSpot")}
              <input
                type="number"
                min="0.7"
                max="1.3"
                step="0.05"
                value={query.strike_min_ratio}
                onChange={(e) => update("option_strike_min", e.target.value)}
              />
            </label>
            <label>
              {translate(locale, "research.optionspanel.maximumStrikeSpot")}
              <input
                type="number"
                min="0.7"
                max="1.3"
                step="0.05"
                value={query.strike_max_ratio}
                onChange={(e) => update("option_strike_max", e.target.value)}
              />
            </label>
            <details className="options-advanced">
              <summary>{translate(locale, "research.optionspanel.advanced")}</summary>
              <label>
                {translate(locale, "research.optionspanel.fixedColorScaleMaximum")}
                {metric === "iv"
                  ? translate(locale, "research.optionspanel.annualizedDecimal")
                  : (data?.unit ?? translate(locale, "research.optionspanel.rawUnits"))}
                {locale === "en-US" ? ")" : "）"}
                <input
                  type="number"
                  min="0"
                  step="any"
                  placeholder={translate(
                    locale,
                    "research.optionspanel.automaticFixForComparisonsAcrossSnapshots",
                  )}
                  value={params.get("option_scale") ?? ""}
                  onChange={(e) => update("option_scale", e.target.value)}
                />
              </label>
            </details>
          </div>
        )}
        {result.isPending && (
          <p role="status">
            {translate(locale, "research.optionspanel.loadingSavedOptionsData")}
          </p>
        )}
        {result.isError && (
          <div role="alert">
            {translate(locale, "research.optionspanel.unableToLoadOptionsData")}
            <ResearchErrorDetail message={result.error.message} />
            {translate(locale, "research.optionspanel.checkTheFilterRangeOrRetry")}
          </div>
        )}
        {data && !structureMap && (
          <>
            <div className="options-status">
              <strong>
                {data.status === "available"
                  ? translate(locale, "research.optionspanel.metricAvailable")
                  : data.status === "partial"
                    ? translate(locale, "research.optionspanel.partialCoverage")
                    : translate(locale, "research.optionspanel.currentlyUnavailable")}
              </strong>
              <span>
                {translate(locale, "research.optionspanel.valid.withValues", {
                  valid: data.coverage.valid,
                  expected: data.coverage.expected,
                  missing: data.coverage.missing,
                  excluded: data.coverage.excluded,
                })}
              </span>
              <span>
                {data.coverage.discovery_complete
                  ? translate(locale, "research.optionspanel.chainDiscoveryComplete")
                  : translate(
                      locale,
                      "research.optionspanel.chainDiscoveryIncompleteCoverageDenominatorUnproven",
                    )}
              </span>
            </div>
            {data.reasons.length > 0 && (
              <p className="options-notice">
                {data.reasons.map((r) => reasons(locale)[r] ?? r).join(locale === "en-US" ? "; " : "；")}
              </p>
            )}
            {data.latest_attempt && (
              <p role="status" className="options-notice">
                {translate(
                  locale,
                  "research.optionspanel.latestCollectionIncompleteKeepingThePreviousCompleteSnapshotFailed.withValues",
                  {
                    capturedAt: time(data.latest_attempt.captured_at, locale),
                    errors: data.latest_attempt.errors
                      .map((r) => reasons(locale)[r] ?? r)
                      .join(locale === "en-US" ? "; " : "；"),
                  },
                )}
              </p>
            )}
            {snapshot && (
              <>
                {tab === "sources" && (
                  <>
                    <dl className="options-metadata">
                      <div>
                        <dt>{translate(locale, "research.optionspanel.sourceFeed")}</dt>
                        <dd>
                          {snapshot.provider} / {snapshot.feed}
                        </dd>
                      </div>
                      <div>
                        <dt>
                          {translate(
                            locale,
                            "research.optionspanel.collectionLocalAvailability",
                          )}
                        </dt>
                        <dd>
                          {time(snapshot.captured_at, locale)} /{" "}
                          {time(snapshot.source_available_at, locale)}
                        </dd>
                      </div>
                      <div>
                        <dt>
                          {translate(
                            locale,
                            "research.optionspanel.marketDataTimeTimeEvidence",
                          )}
                        </dt>
                        <dd>
                          {time(snapshot.market_as_of, locale)} /{" "}
                          {snapshot.time_evidence === "historical_verified"
                            ? translate(
                                locale,
                                "research.optionspanel.historicalAvailabilityVerified",
                              )
                            : translate(
                                locale,
                                "research.optionspanel.forwardCollectionEvidenceOnly",
                              )}
                        </dd>
                      </div>
                      <div>
                        <dt>
                          {translate(
                            locale,
                            "research.optionspanel.rawUnderlyingPriceTime",
                          )}
                        </dt>
                        <dd>
                          {snapshot.underlying_price == null
                            ? translate(locale, "research.optionanalyticspanel.missing")
                            : `$${snapshot.underlying_price}`}{" "}
                          ; {time(snapshot.underlying_at, locale)}
                        </dd>
                      </div>
                      <div>
                        <dt>
                          {translate(locale, "research.optionspanel.snapshotVersion")}
                        </dt>
                        <dd>{snapshot.snapshot_id}</dd>
                      </div>
                      <div>
                        <dt>
                          {translate(
                            locale,
                            "research.optionspanel.dataNormalizationFormulaVersion",
                          )}
                        </dt>
                        <dd>
                          {snapshot.dataset_revision} / {snapshot.normalization_version}{" "}
                          / {data.formula_version}
                        </dd>
                      </div>
                      <div>
                        <dt>
                          {translate(
                            locale,
                            "research.optionspanel.providerSdkOpendRawBatch",
                          )}
                        </dt>
                        <dd>
                          {snapshot.sdk_version} / {snapshot.opend_version} /{" "}
                          {snapshot.raw_batch_id}
                        </dd>
                      </div>
                      <div>
                        <dt>
                          {translate(locale, "research.optionspanel.licenseReference")}
                        </dt>
                        <dd>
                          {snapshot.license_reference ??
                            translate(
                              locale,
                              "research.optionanalyticspanel.unconfirmed",
                            )}
                        </dd>
                      </div>
                    </dl>
                    <p className="options-notice">
                      {translate(
                        locale,
                        "research.optionspanel.oiDatesAndGreeksCalculationTimesShowUnconfirmedWhen.withValues",
                        {
                          translate:
                            metric === "premium"
                              ? translate(
                                  locale,
                                  "research.optionspanel.estimatedTurnoverVolumeBidAsk2ActualContractMultiplier",
                                )
                              : metric === "gamma"
                                ? translate(
                                    locale,
                                    "research.optionspanel.formulaGammaOiContractMultiplierContemporaneousUnderlyingPrice0",
                                  )
                                : metric === "iv"
                                  ? translate(
                                      locale,
                                      "research.optionspanel.ivIsAnAnnualizedPercentageMultipleContractsInA",
                                    )
                                  : metric === "volume"
                                    ? translate(
                                        locale,
                                        "research.optionspanel.volumeIsCumulativeThroughTheSnapshotTimeAndDoes",
                                      )
                                    : metric === "spread"
                                      ? translate(
                                          locale,
                                          "research.optionspanel.bidAskSpreadIsInUsdCrossedOneSided",
                                        )
                                      : translate(
                                          locale,
                                          "research.optionspanel.oiCountsOpenContractsAndDoesNotIndicateNet",
                                        ),
                        },
                      )}
                    </p>
                  </>
                )}
                {tab === "structure" && (
                  <>
                    <div className="options-content">
                      <div>
                        <h3>
                          {net
                            ? translate(locale, "research.optionspanel.callPutNet")
                            : query.side === "call"
                              ? "Call"
                              : "Put"}{" "}
                          ; {labels(locale)[metric]}
                        </h3>
                        <div className="options-heat-key">
                          <strong>
                            {translate(
                              locale,
                              "research.optionspanel.number.withValues",
                              { value: labels(locale)[metric], unit: unit },
                            )}
                          </strong>
                          <span>
                            {underlying == null
                              ? translate(locale, "research.optionspanel.spotMissing")
                              : translate(locale, "research.optionspanel.spot", {
                                  underlying: underlying.toFixed(2),
                                })}
                          </span>
                          {additive && (
                            <strong>
                              {translate(
                                locale,
                                "research.optionspanel.totalWithinRange.withValues",
                                { compact: compact(data.total), unit: unit },
                              )}
                            </strong>
                          )}
                        </div>
                        <p className="options-notice">
                          {translate(
                            locale,
                            "research.optionspanel.colorScale.withValues",
                            {
                              format: net ? format(-scaleMax) : "0",
                              format2: format(scaleMax),
                              unit: unit,
                              translate:
                                scale == null
                                  ? translate(
                                      locale,
                                      "research.optionspanel.automaticallyScaledForThisSnapshotFixTheMaximumFor",
                                    )
                                  : translate(
                                      locale,
                                      "research.optionspanel.fixedMaximum",
                                    ),
                              translate2: net
                                ? translate(
                                    locale,
                                    "research.optionspanel.positiveValuesMeanCallExceedsPutNegativeValuesMean",
                                  )
                                : translate(
                                    locale,
                                    "research.optionspanel.brighterColorsIndicateLargerValues",
                                  ),
                              summaryLabel: summaryLabel,
                              status:
                                data.status === "partial" ||
                                data.coverage.missing > 0 ||
                                data.coverage.excluded > 0
                                  ? translate(
                                      locale,
                                      "research.optionspanel.coverageIsIncompleteTotalsContainEligibleDataOnly",
                                    )
                                  : "",
                            },
                          )}
                        </p>
                        {metric === "premium" && (
                          <p className="options-notice">
                            {translate(
                              locale,
                              "research.optionspanel.estimatedTurnoverVolumeBidAsk2ActualContractMultiplier2",
                            )}
                          </p>
                        )}
                        <div
                          className="options-heat-scroll"
                          ref={heatRef}
                          tabIndex={0}
                          role="region"
                          aria-label={translate(
                            locale,
                            "research.optionspanel.scrollableNumericOptionsHeatmap",
                          )}
                        >
                          <table
                            className="options-heat-table"
                            aria-label={translate(
                              locale,
                              "research.optionspanel.expiryStrikeNumericHeatmap",
                            )}
                          >
                            <thead>
                              <tr>
                                <th scope="col">
                                  {translate(
                                    locale,
                                    "research.optionspanel.strikeExpiry",
                                  )}
                                </th>
                                {data.expirations.map((expiry) => (
                                  <th scope="col" key={expiry}>
                                    {expiry}
                                  </th>
                                ))}
                                <th scope="col">
                                  {translate(
                                    locale,
                                    "research.optionspanel.row.withValues",
                                    { summaryLabel: summaryLabel },
                                  )}
                                </th>
                              </tr>
                            </thead>
                            <tbody>
                              {[...data.strikes]
                                .sort((a, b) => b - a)
                                .map((strike) => (
                                  <tr
                                    key={strike}
                                    data-near-spot={
                                      underlying != null && strike === nearStrike
                                    }
                                  >
                                    <th scope="row">
                                      ${strike}
                                      {underlying != null && strike === nearStrike && (
                                        <small>
                                          {translate(
                                            locale,
                                            "research.optionspanel.nearSpot",
                                          )}
                                        </small>
                                      )}
                                    </th>
                                    {data.expirations.map((expiry) => {
                                      const entry = cellLookup.get(
                                        `${strike}:${expiry}`,
                                      );
                                      const cell = entry?.cell;
                                      const value = cell?.value;
                                      const strength = Math.min(
                                        1,
                                        Math.abs(value ?? 0) / scaleMax,
                                      );
                                      const high = net
                                        ? value != null && value >= 0
                                          ? [145, 181, 239]
                                          : [239, 139, 145]
                                        : [237, 233, 255];
                                      const low = [37, 39, 53];
                                      const color = low.map((channel, i) =>
                                        Math.round(
                                          channel +
                                            ((high[i] ?? channel) - channel) * strength,
                                        ),
                                      );
                                      const luminance = color.reduce(
                                        (sum, channel, i) => {
                                          const s = channel / 255;
                                          return (
                                            sum +
                                            (s <= 0.04045
                                              ? s / 12.92
                                              : ((s + 0.055) / 1.055) ** 2.4) *
                                              ([0.2126, 0.7152, 0.0722][i] ?? 0)
                                          );
                                        },
                                        0,
                                      );
                                      return (
                                        <td key={expiry}>
                                          {cell && entry ? (
                                            <button
                                              type="button"
                                              className="options-heat-cell"
                                              style={{
                                                backgroundColor:
                                                  value == null
                                                    ? "#44444f"
                                                    : `rgb(${color.join(",")})`,
                                                color:
                                                  luminance > 0.179 && value != null
                                                    ? "#000"
                                                    : "#fff",
                                              }}
                                              aria-label={cellDescription(cell)}
                                              title={cellDescription(cell)}
                                              aria-pressed={activeCell === cell}
                                              onMouseEnter={() =>
                                                setFocusedCell(entry.index)
                                              }
                                              onFocus={() =>
                                                setFocusedCell(entry.index)
                                              }
                                              onClick={() => {
                                                setFocusedCell(entry.index);
                                                selectCell(entry.index);
                                              }}
                                            >
                                              {compact(value)}
                                              {cell.missing_count > 0 &&
                                                value != null && (
                                                  <span
                                                    title={translate(
                                                      locale,
                                                      "research.optionspanel.someContractsMissing",
                                                    )}
                                                  >
                                                    *
                                                  </span>
                                                )}
                                            </button>
                                          ) : (
                                            <span
                                              className="options-heat-absent"
                                              title={translate(
                                                locale,
                                                "research.optionspanel.noMatchingContract",
                                              )}
                                            >
                                              —
                                            </span>
                                          )}
                                        </td>
                                      );
                                    })}
                                    <td
                                      className="options-heat-summary"
                                      title={format(strikeSummary.get(strike))}
                                    >
                                      {compact(strikeSummary.get(strike))}
                                    </td>
                                  </tr>
                                ))}
                            </tbody>
                            <tfoot>
                              <tr>
                                <th scope="row">
                                  {translate(
                                    locale,
                                    "research.optionspanel.column.withValues",
                                    { summaryLabel: summaryLabel },
                                  )}
                                </th>
                                {data.expirations.map((expiry) => (
                                  <td
                                    key={expiry}
                                    title={format(data.expiry_summary[expiry])}
                                  >
                                    {compact(data.expiry_summary[expiry])}
                                  </td>
                                ))}
                                <td>
                                  {additive
                                    ? compact(data.total)
                                    : translate(
                                        locale,
                                        "research.optionspanel.notApplicable",
                                      )}
                                </td>
                              </tr>
                            </tfoot>
                          </table>
                        </div>
                        <p className="options-heat-reading" aria-live="polite">
                          {activeCell
                            ? cellDescription(activeCell)
                            : translate(
                                locale,
                                "research.optionspanel.hoverFocusOrClickACellToViewThe",
                              )}
                        </p>
                        {!cells.some((cell) => cell.value != null) && (
                          <p className="options-empty">
                            {translate(
                              locale,
                              "research.optionspanel.noEligibleValuesForThisMetricTheChartStays",
                            )}
                          </p>
                        )}
                        <ChartExplainer kind="heatmap" />
                      </div>
                      <aside
                        aria-label={translate(
                          locale,
                          "research.optionspanel.optionsContractDetails",
                        )}
                        className="options-detail"
                      >
                        <h3>
                          {translate(locale, "research.optionspanel.contractDetails")}
                        </h3>
                        <label>
                          {translate(locale, "research.optionspanel.contract")}
                          <select
                            value={selected?.contract_id ?? ""}
                            onChange={(e) => setSelectedId(e.target.value)}
                          >
                            {visibleContracts.map((r) => (
                              <option key={r.contract_id}>{r.contract_id}</option>
                            ))}
                          </select>
                        </label>
                        {selected ? (
                          <>
                            <dl className="options-detail-key">
                              <dt>
                                {translate(
                                  locale,
                                  "research.optionanalyticspanel.strike",
                                )}
                              </dt>
                              <dd>${selected.strike}</dd>
                              <dt>
                                {translate(locale, "research.optionspanel.expiry")}
                              </dt>
                              <dd>{selected.expiration}</dd>
                              <dt>
                                {translate(locale, "research.optionspanel.bidAsk")}
                              </dt>
                              <dd>
                                {selected.bid ??
                                  translate(
                                    locale,
                                    "research.optionanalyticspanel.missing",
                                  )}{" "}
                                /{" "}
                                {selected.ask ??
                                  translate(
                                    locale,
                                    "research.optionanalyticspanel.missing",
                                  )}
                              </dd>
                              <dt>OI</dt>
                              <dd>
                                {selected.open_interest ??
                                  translate(
                                    locale,
                                    "research.optionanalyticspanel.missing",
                                  )}
                              </dd>
                              <dt>
                                {translate(locale, "research.optionPulseChart.volume")}
                              </dt>
                              <dd>
                                {selected.volume ??
                                  translate(
                                    locale,
                                    "research.optionanalyticspanel.missing",
                                  )}
                              </dd>
                              <dt>IV</dt>
                              <dd>
                                {selected.iv == null
                                  ? translate(
                                      locale,
                                      "research.optionanalyticspanel.missing",
                                    )
                                  : `${(selected.iv * 100).toFixed(2)}%`}
                              </dd>
                              <dt>Delta</dt>
                              <dd>
                                {selected.delta ??
                                  translate(
                                    locale,
                                    "research.optionanalyticspanel.missing",
                                  )}
                              </dd>
                              <dt>Gamma</dt>
                              <dd>
                                {selected.gamma ??
                                  translate(
                                    locale,
                                    "research.optionanalyticspanel.missing",
                                  )}
                              </dd>
                            </dl>
                            <details>
                              <summary>
                                {translate(
                                  locale,
                                  "research.optionspanel.moreFieldsAndQualityEvidence",
                                )}
                              </summary>
                              <dl>
                                <dt>
                                  {translate(
                                    locale,
                                    "research.optionspanel.underlyingContractSide",
                                  )}
                                </dt>
                                <dd>
                                  {selected.underlying} / {selected.side}
                                </dd>
                                <dt>
                                  {translate(
                                    locale,
                                    "research.optionspanel.multiplierDeliverable",
                                  )}
                                </dt>
                                <dd>
                                  {selected.multiplier ??
                                    translate(
                                      locale,
                                      "research.optionanalyticspanel.unconfirmed",
                                    )}{" "}
                                  /{" "}
                                  {selected.standard === true
                                    ? translate(
                                        locale,
                                        "research.optionspanel.standard",
                                      )
                                    : selected.standard === false
                                      ? translate(
                                          locale,
                                          "research.optionspanel.adjustedContract",
                                        )
                                      : translate(
                                          locale,
                                          "research.optionanalyticspanel.unconfirmed",
                                        )}
                                </dd>
                                <dt>
                                  {translate(
                                    locale,
                                    "research.optionspanel.exerciseSettlement",
                                  )}
                                </dt>
                                <dd>
                                  {selected.exercise
                                    ? (researchEvidenceLabel(
                                        selected.exercise,
                                        locale,
                                      ) ??
                                      translate(locale, "common.unknownCode", {
                                        code: selected.exercise,
                                      }))
                                    : translate(
                                        locale,
                                        "research.optionanalyticspanel.unconfirmed",
                                      )}{" "}
                                  /{" "}
                                  {selected.settlement
                                    ? (researchEvidenceLabel(
                                        selected.settlement,
                                        locale,
                                      ) ??
                                      translate(locale, "common.unknownCode", {
                                        code: selected.settlement,
                                      }))
                                    : translate(
                                        locale,
                                        "research.optionanalyticspanel.unconfirmed",
                                      )}
                                </dd>
                                <dt>
                                  {translate(locale, "research.optionspanel.bidAsk")}
                                </dt>
                                <dd>
                                  {selected.bid ??
                                    translate(
                                      locale,
                                      "research.optionanalyticspanel.missing",
                                    )}{" "}
                                  /{" "}
                                  {selected.ask ??
                                    translate(
                                      locale,
                                      "research.optionanalyticspanel.missing",
                                    )}
                                </dd>
                                <dt>
                                  {translate(
                                    locale,
                                    "research.optionspanel.independentQuoteTime",
                                  )}
                                </dt>
                                <dd>{time(selected.quote_at, locale)}</dd>
                                <dt>
                                  {translate(
                                    locale,
                                    "research.optionspanel.marketObservationTime",
                                  )}
                                </dt>
                                <dd>{time(selected.observed_at, locale)}</dd>
                                <dt>
                                  {translate(
                                    locale,
                                    "research.optionspanel.localIngestionSourceAvailability",
                                  )}
                                </dt>
                                <dd>
                                  {time(selected.ingested_at, locale)} /{" "}
                                  {time(selected.source_available_at, locale)}
                                </dd>
                                <dt>
                                  {translate(
                                    locale,
                                    "research.optionspanel.contractUnderlyingPriceTime",
                                  )}
                                </dt>
                                <dd>
                                  {selected.underlying_price == null
                                    ? translate(
                                        locale,
                                        "research.optionanalyticspanel.missing",
                                      )
                                    : `$${selected.underlying_price}`}{" "}
                                  / {time(selected.underlying_at, locale)}
                                </dd>
                                <dt>
                                  {translate(locale, "research.optionspanel.oiDate")}
                                </dt>
                                <dd>
                                  {selected.open_interest ??
                                    translate(
                                      locale,
                                      "research.optionanalyticspanel.missing",
                                    )}{" "}
                                  /{" "}
                                  {selected.oi_date ??
                                    translate(
                                      locale,
                                      "research.optionanalyticspanel.unconfirmed",
                                    )}
                                </dd>
                                <dt>
                                  {translate(
                                    locale,
                                    "research.optionspanel.cumulativeVolumeToday",
                                  )}
                                </dt>
                                <dd>
                                  {selected.volume ??
                                    translate(
                                      locale,
                                      "research.optionanalyticspanel.missing",
                                    )}
                                </dd>
                                <dt>IV / Gamma</dt>
                                <dd>
                                  {selected.iv == null
                                    ? translate(
                                        locale,
                                        "research.optionanalyticspanel.missing",
                                      )
                                    : `${(selected.iv * 100).toFixed(2)}%`}{" "}
                                  /{" "}
                                  {selected.gamma ??
                                    translate(
                                      locale,
                                      "research.optionanalyticspanel.missing",
                                    )}
                                </dd>
                                <dt>Delta / Theta / Vega</dt>
                                <dd>
                                  {selected.delta ??
                                    translate(
                                      locale,
                                      "research.optionanalyticspanel.missing",
                                    )}{" "}
                                  /{" "}
                                  {selected.theta ??
                                    translate(
                                      locale,
                                      "research.optionanalyticspanel.missing",
                                    )}{" "}
                                  /{" "}
                                  {selected.vega ??
                                    translate(
                                      locale,
                                      "research.optionanalyticspanel.missing",
                                    )}
                                </dd>
                                <dt>
                                  {translate(
                                    locale,
                                    "research.optionspanel.greeksTimeUnitVersion",
                                  )}
                                </dt>
                                <dd>
                                  {time(selected.greeks_at, locale)} /{" "}
                                  {selected.greek_units}
                                </dd>
                                <dt>
                                  {translate(
                                    locale,
                                    "research.optionspanel.deltaUnitVersion",
                                  )}
                                </dt>
                                <dd>{selected.delta_units}</dd>
                                <dt>
                                  {translate(
                                    locale,
                                    "research.optionspanel.qualityNotes",
                                  )}
                                </dt>
                                <dd>
                                  {selected.quality_flags
                                    .map((r) => reasons(locale)[r] ?? r)
                                    .join(locale === "en-US" ? "; " : "；") ||
                                    translate(
                                      locale,
                                      "research.optionspanel.noAdditionalFlags",
                                    )}
                                </dd>
                              </dl>
                            </details>
                          </>
                        ) : (
                          <p>
                            {translate(
                              locale,
                              "research.optionspanel.noContractsInTheCurrentRange",
                            )}
                          </p>
                        )}
                      </aside>
                    </div>
                    <details>
                      <summary>
                        {translate(
                          locale,
                          "research.optionspanel.strikeAndMaturitySummariesSameEligibleContracts",
                        )}
                      </summary>
                      <div className="options-summaries">
                        {summaries.map(([title, values]) => (
                          <table key={title}>
                            <thead>
                              <tr>
                                <th>{title}</th>
                                <th>{summaryLabel}</th>
                              </tr>
                            </thead>
                            <tbody>
                              {Object.entries(values).map(([key, value]) => (
                                <tr key={key}>
                                  <td>{key}</td>
                                  <td>{format(value)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        ))}
                      </div>
                    </details>
                  </>
                )}
              </>
            )}
          </>
        )}
        {tab === "term" && (
          <OptionAnalyticsPanel
            symbol={symbol}
            query={analyticsQuery}
            enabled={!!snapshot}
            loadData={loadAnalytics}
            stockSnapshotId={params.get("snapshot_id") ?? undefined}
            previousSkewPoints={
              overview.data?.items.find((row) => row.symbol === symbol)
                ?.previous_skew_points ?? []
            }
            previousSnapshotId={
              overview.data?.items.find((row) => row.symbol === symbol)
                ?.previous_snapshot_id
            }
            previousError={overview.error?.message}
          />
        )}
        {tab === "comparison" && (
          <>
            <OptionModelVsMarket
              symbol={symbol}
              optionSnapshotId={snapshot?.snapshot_id ?? query.snapshot_id}
            />
            <details>
              <summary>
                {translate(
                  locale,
                  "research.optionspanel.modelSourceHashesAndChallengerStatus",
                )}
              </summary>
              <OptionForecastPanel
                symbol={symbol}
                optionSnapshotId={snapshot?.snapshot_id ?? query.snapshot_id}
                optionAsOf={query.as_of}
                loadData={loadForecast}
              />
            </details>
          </>
        )}
        {tab === "sources" && <OptionCollectionStatus />}
      </div>
      <footer className="options-footer">
        {translate(
          locale,
          "research.optionspanel.optionsEnhancedForecastsChallengerResearchAndPublicationStatusAre",
        )}
      </footer>
    </section>
  );
}
