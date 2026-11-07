import { ResearchErrorDetail } from "./ResearchErrorDetail";
import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo, useState } from "react";
import { ChartExplainer } from "../options/ChartExplainer";
import { useQuery } from "@tanstack/react-query";
import type { EChartsOption } from "echarts";
import { createResearchApiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { formatUsMarketTime } from "../../app/presentation";
import { OverviewTerminalChart } from "../overview/OverviewTerminalChart";
import type { OptionQuery } from "./options-state";
type Response = components["schemas"]["OptionAnalyticsResponse"];
type Analytics = components["schemas"]["OptionAnalyticsReadModel"];
const client = createResearchApiClient({ trackConnectivity: false });
const percent = (value: number | null | undefined, locale: Locale = "zh-CN") =>
  value == null
    ? translate(locale, "research.optionanalyticspanel.missing")
    : `${(value * 100).toFixed(2)}%`;
const number = (value: number | null | undefined, locale: Locale = "zh-CN") =>
  value == null
    ? translate(locale, "research.optionanalyticspanel.missing")
    : value.toLocaleString(locale, { maximumFractionDigits: 2 });
const time = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  value
    ? formatUsMarketTime(value, locale)
    : translate(locale, "research.optionanalyticspanel.unconfirmed");
const reasonLabels = (locale: Locale = "zh-CN"): Record<string, string> => ({
  no_option_snapshot: translate(
    locale,
    "research.optionanalyticspanel.noSavedOptionsSnapshot",
  ),
  incomplete_discovery: translate(
    locale,
    "research.optionanalyticspanel.chainDiscoveryIncomplete",
  ),
  snapshot_not_complete: translate(
    locale,
    "research.optionanalyticspanel.snapshotBatchIncomplete",
  ),
  historical_snapshots_insufficient: translate(
    locale,
    "research.optionanalyticspanel.insufficientComparableHistoricalSnapshots",
  ),
  comparable_history_insufficient: translate(
    locale,
    "research.optionanalyticspanel.insufficientComparableHistoricalSnapshots",
  ),
  iv_history_insufficient: translate(
    locale,
    "research.optionanalyticspanel.insufficientIvHistoryForAHistoricalPercentile",
  ),
  historical_time_evidence_missing: translate(
    locale,
    "research.optionanalyticspanel.historicalAvailabilityEvidenceMissing",
  ),
  greeks_timestamp_unproven: translate(
    locale,
    "research.optionanalyticspanel.greeksCalculationTimeUnconfirmed",
  ),
  delta_units_unproven: translate(
    locale,
    "research.optionanalyticspanel.deltaUnitsUnconfirmed",
  ),
  delta_legs_missing: translate(
    locale,
    "research.optionanalyticspanel.eligibleContractsOnBoth25DeltaSidesAreMissing",
  ),
  future_market_time: translate(
    locale,
    "research.optionanalyticspanel.marketTimeIsLaterThanSourceAvailability",
  ),
  future_underlying_time: translate(
    locale,
    "research.optionanalyticspanel.underlyingTimeIsLaterThanSourceAvailability",
  ),
  market_time_unproven: translate(
    locale,
    "research.optionanalyticspanel.marketTimeUnconfirmed",
  ),
  iv_unavailable: translate(
    locale,
    "research.optionanalyticspanel.eligibleIvDataUnavailable",
  ),
  realized_volatility_unavailable: translate(
    locale,
    "research.optionanalyticspanel.insufficientRealizedVolatilityData",
  ),
  snapshot_not_available: translate(
    locale,
    "research.optionanalyticspanel.snapshotIsLaterThanTheAnalysisCutoffAndCannot",
  ),
  underlying_price_unavailable: translate(
    locale,
    "research.optionanalyticspanel.underlyingPriceUnavailable",
  ),
  incomplete_snapshot: translate(
    locale,
    "research.optionanalyticspanel.snapshotBatchIncomplete",
  ),
  delta_units_or_25delta_legs_missing: translate(
    locale,
    "research.optionanalyticspanel.deltaUnitsOrEligible25DeltaContractsMissing",
  ),
  historical_returns_or_annualization_unproven: translate(
    locale,
    "research.optionanalyticspanel.historicalStockReturnsOrAnnualizationRulesAreUnproven",
  ),
  comparable_history_missing: translate(
    locale,
    "research.optionanalyticspanel.noComparableHistoricalSnapshot",
  ),
  field_time_unit_or_coverage_unproven: translate(
    locale,
    "research.optionanalyticspanel.fieldTimeUnitsOrCoverageUnproven",
  ),
  metric_numeric_overflow: translate(
    locale,
    "research.optionanalyticspanel.derivedMetricExceedsTheValidNumericRange",
  ),
  symbol_outside_option_scope: translate(
    locale,
    "research.optionanalyticspanel.instrumentOutsideOptionsResearchScope",
  ),
});
const explain = (reasons: string[], locale: Locale = "zh-CN") =>
  [...new Set(reasons)]
    .map((reason) => reasonLabels(locale)[reason] ?? reason)
    .join(locale === "en-US" ? "; " : "；");
export async function loadOptionAnalyticsFromApi(
  symbol: string,
  query: OptionQuery,
  stockSnapshotId?: string,
): Promise<Response> {
  const { data } = await client.get("/api/v1/instruments/{symbol}/options/analytics", {
    params: {
      path: { symbol },
      query: {
        ...(query.snapshot_id ? { snapshot_id: query.snapshot_id } : {}),
        ...(query.as_of ? { as_of: query.as_of } : {}),
        ...(stockSnapshotId ? { stock_snapshot_id: stockSnapshotId } : {}),
      },
    },
    signal: AbortSignal.timeout(15000),
  });
  if (!data) throw new Error("Options analysis response unavailable");
  return data;
}
function featureUnit(name: string, locale: Locale = "zh-CN"): string {
  if (name.endsWith("_seconds"))
    return translate(locale, "research.abstainpanel.seconds");
  if (name.endsWith("_dte"))
    return translate(locale, "research.optionanalyticspanel.calendarDays");
  if (name.startsWith("realized_annualization") || name === "realized_observations")
    return translate(locale, "research.optionanalyticspanel.tradingDays");
  if (name === "call_oi" || name === "put_oi")
    return translate(locale, "research.optionanalyticspanel.contracts");
  if (name === "call_volume" || name === "put_volume")
    return translate(locale, "research.optionanalyticspanel.cumulativeContractsToday");
  if (name === "call_gamma" || name === "put_gamma")
    return translate(locale, "research.optionanalyticspanel.usd1UnderlyingMove");
  if (name === "iv_term_slope_per_day")
    return translate(
      locale,
      "research.optionanalyticspanel.annualizedIvDecimalCalendarDay",
    );
  if (
    (name.includes("iv") && !name.endsWith("_ratio")) ||
    name === "put_call_skew_25delta" ||
    name === "realized_volatility_20sessions"
  )
    return translate(
      locale,
      "research.optionanalyticspanel.annualizedVolatilityDecimalSameUnitsForDifferences",
    );
  return translate(locale, "research.optionanalyticspanel.ratioIndexDecimal");
}
function termChart(data: Analytics, locale: Locale = "zh-CN"): EChartsOption {
  return {
    animation: false,
    grid: { left: 64, right: 24, top: 42, bottom: 50 },
    legend: { top: 0, textStyle: { color: "#b8b6c8" } },
    tooltip: {
      trigger: "axis",
      valueFormatter: (value) =>
        `${number(typeof value === "number" ? value : null, locale)}%`,
    },
    xAxis: {
      type: "value",
      name: translate(locale, "research.optionanalyticspanel.calendarDaysToExpiryDte"),
      nameLocation: "middle",
      nameGap: 30,
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      name: translate(locale, "research.optionanalyticspanel.annualizedIv"),
      scale: true,
      splitLine: { lineStyle: { color: "#343340" } },
    },
    series: [
      {
        name: "ATM Call",
        type: "line",
        markLine: {
          symbol: "none",
          lineStyle: { color: "#88849e", type: "dashed" },
          label: { formatter: "{b}" },
          data: [1, 5, 20].map((h) => ({
            name: translate(locale, "research.optionanalyticspanel.tradingDays2", {
              horizon: h,
            }),
            xAxis: (h * 365) / 252,
          })),
        },
        connectNulls: false,
        data: data.iv_terms.map((row) => [
          row.dte,
          row.call_iv == null ? null : row.call_iv * 100,
        ]),
      },
      {
        name: "ATM Put",
        type: "line",
        connectNulls: false,
        data: data.iv_terms.map((row) => [
          row.dte,
          row.put_iv == null ? null : row.put_iv * 100,
        ]),
      },
    ],
  };
}
function skewChart(
  data: Analytics,
  previous: Analytics["skew_points"],
  showPrevious: boolean,
  locale: Locale = "zh-CN",
): EChartsOption {
  return {
    animation: false,
    grid: { left: 64, right: 24, top: 42, bottom: 50 },
    legend: { top: 0, textStyle: { color: "#b8b6c8" } },
    tooltip: {
      trigger: "axis",
      valueFormatter: (value) =>
        translate(locale, "research.optionanalyticspanel.percentagePoints", {
          value: number(typeof value === "number" ? value : null, locale),
        }),
    },
    xAxis: {
      type: "value",
      name: translate(locale, "research.optionanalyticspanel.calendarDaysToExpiryDte"),
      nameLocation: "middle",
      nameGap: 30,
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      name: translate(
        locale,
        "research.optionanalyticspanel.ivDifferencePercentagePoints",
      ),
      scale: true,
      splitLine: { lineStyle: { color: "#343340" } },
    },
    series: [
      {
        name: "25Δ Put − Call",
        type: "line",
        connectNulls: false,
        data: data.skew_points.map((row) => [
          row.dte,
          row.skew == null ? null : row.skew * 100,
        ]),
      },
      ...(showPrevious && previous.some((row) => row.skew != null)
        ? [
            {
              name: translate(
                locale,
                "research.optionanalyticspanel.previousComparableSnapshot",
              ),
              type: "line" as const,
              connectNulls: false,
              lineStyle: { type: "dashed" as const, color: "#e0ad58" },
              data: previous.map((row) => [
                row.dte,
                row.skew == null ? null : row.skew * 100,
              ]),
            },
          ]
        : []),
    ],
  };
}
function historyChart(
  data: Analytics,
  metric: "oi" | "gamma",
  locale: Locale = "zh-CN",
): EChartsOption {
  const dates = [...new Set(data.history.map((row) => row.market_as_of))].sort();
  const rows = data.history.filter((row) => row[metric] != null);
  const maximum = Math.max(1, ...rows.map((row) => row[metric] ?? 0));
  const unit =
    metric === "oi"
      ? translate(locale, "research.optionanalyticspanel.openContracts")
      : translate(locale, "research.optionanalyticspanel.usd1UnderlyingMove");
  return {
    animation: false,
    grid: { left: 72, right: 28, top: 38, bottom: 106 },
    legend: { top: 0, textStyle: { color: "#b8b6c8" } },
    tooltip: { trigger: "item" },
    xAxis: {
      type: "category",
      data: dates.map((value) => time(value, locale)),
      axisLabel: { rotate: 20 },
      name: translate(locale, "research.optionanalyticspanel.actualMarketTimeNewYork"),
      nameLocation: "middle",
      nameGap: 60,
    },
    yAxis: {
      type: "value",
      name: translate(locale, "research.optionanalyticspanel.strikeUnderlyingPrice"),
      scale: true,
      splitLine: { lineStyle: { color: "#343340" } },
    },
    visualMap: {
      min: 0,
      max: maximum,
      dimension: 2,
      seriesIndex: 0,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      text: [`${number(maximum, locale)} ${unit}`, "0"],
      inRange: { color: ["#525782", "#9a8fe7", "#ede9ff"] },
    },
    series: [
      {
        name:
          metric === "oi"
            ? translate(locale, "research.optionanalyticspanel.oiConcentration")
            : translate(locale, "research.optionanalyticspanel.unsignedGamma"),
        type: "scatter",
        symbol: "rect",
        symbolSize: [18, 8],
        data: rows.map((row) => [
          dates.indexOf(row.market_as_of),
          row.strike,
          row[metric],
        ]),
      },
      {
        name: translate(locale, "research.optionanalyticspanel.actualUnderlyingPrice"),
        type: "line",
        connectNulls: false,
        lineStyle: { color: "#f0b860" },
        itemStyle: { color: "#f0b860" },
        data: dates.map(
          (date) =>
            data.history.find((row) => row.market_as_of === date)?.underlying_price ??
            null,
        ),
      },
    ],
  };
}
export function OptionAnalyticsPanel({
  symbol,
  query,
  enabled = true,
  loadData = loadOptionAnalyticsFromApi,
  previousSkewPoints = [],
  previousSnapshotId,
  previousError,
  stockSnapshotId,
}: {
  symbol: string;
  query: OptionQuery;
  enabled?: boolean;
  loadData?: (
    symbol: string,
    query: OptionQuery,
    stockSnapshotId?: string,
  ) => Promise<Response>;
  stockSnapshotId?: string | undefined;
  previousSkewPoints?: Analytics["skew_points"];
  previousSnapshotId?: string | null | undefined;
  previousError?: string | undefined;
}) {
  const { locale } = useI18n();
  const result = useQuery({
    queryKey: [
      "option-analytics",
      symbol,
      query.snapshot_id,
      query.as_of,
      stockSnapshotId,
    ],
    queryFn: () => loadData(symbol, query, stockSnapshotId),
    enabled,
    retry: false,
    staleTime: 60000,
  });
  const data = result.data?.data;
  const previous = previousSkewPoints;
  const [showPrevious, setShowPrevious] = useState(true);
  const historyMetric = query.metric === "gamma" ? "gamma" : "oi";
  const term = useMemo(() => (data ? termChart(data, locale) : {}), [data, locale]);
  const skew = useMemo(
    () => (data ? skewChart(data, previous, showPrevious, locale) : {}),
    [data, previous, showPrevious, locale],
  );
  const history = useMemo(
    () => (data ? historyChart(data, historyMetric, locale) : {}),
    [data, historyMetric, locale],
  );
  const historyTimes = new Set(data?.history.map((row) => row.market_as_of));
  const hasHistory =
    historyTimes.size > 1 && data?.history.some((row) => row[historyMetric] != null);
  return (
    <section
      className="options-analytics"
      aria-label={translate(
        locale,
        "research.optionanalyticspanel.optionsTermSkewAndHistoricalAnalysis",
      )}
    >
      <h3>
        {translate(
          locale,
          "research.optionanalyticspanel.termSkewAndHistoricalStructure",
        )}
      </h3>
      {!enabled && (
        <p className="options-notice">
          {translate(
            locale,
            "research.optionanalyticspanel.noSavedSnapshotIsAvailableForAnalysisTermSkew",
          )}
        </p>
      )}
      {enabled && result.isPending && (
        <p role="status">
          {translate(
            locale,
            "research.optionanalyticspanel.loadingSavedOptionsAnalysis",
          )}
        </p>
      )}
      {result.isError && (
        <div role="alert">
          {translate(
            locale,
            "research.optionanalyticspanel.unableToLoadOptionsAnalysis",
          )}
          <ResearchErrorDetail message={result.error.message} />{locale === "en-US" ? "." : "。"}
          {data
            ? translate(
                locale,
                "research.optionanalyticspanel.keepingTheLastSuccessfulAnalysisWithItsOriginalTime",
              )
            : translate(
                locale,
                "research.optionanalyticspanel.retryLoadingTheSnapshot",
              )}
        </div>
      )}
      {result.data && !data && (
        <p className="options-notice">
          {translate(
            locale,
            "research.optionanalyticspanel.optionsAnalysisUnavailable.withValues",
            { explain: explain(result.data.reasons, locale) },
          )}
        </p>
      )}
      {data && (
        <>
          <details>
            <summary>
              {translate(locale, "research.optionanalyticspanel.analysisSourceAndTime")}
            </summary>
            <p className="options-notice">
              {translate(locale, "research.optionanalyticspanel.source.withValues", {
                translate:
                  data.status === "available"
                    ? translate(
                        locale,
                        "research.optionanalyticspanel.analysisAvailable",
                      )
                    : data.status === "partial"
                      ? translate(
                          locale,
                          "research.optionanalyticspanel.partialAnalysisCoverage",
                        )
                      : translate(
                          locale,
                          "research.optionanalyticspanel.analysisUnavailable",
                        ),
                provider: data.provider,
                feed: data.feed,
                featureVersion: data.feature_version,
                datasetRevision: data.dataset_revision,
                normalizationVersion: data.normalization_version,
              })}
            </p>
            <p className="options-notice">
              {translate(
                locale,
                "research.optionanalyticspanel.inputSnapshot.withValues",
                {
                  snapshotId: data.snapshot_id,
                  marketAsOf: time(data.market_as_of, locale),
                  sourceAvailableAt: time(data.source_available_at, locale),
                  asOf: time(data.as_of, locale),
                },
              )}
            </p>
            <p className="options-notice">
              {translate(
                locale,
                "research.optionanalyticspanel.analysisUsesTheFullRangeSavedInThisSnapshot",
              )}
            </p>
            {data.reasons.length > 0 && (
              <p className="options-notice">{explain(data.reasons, locale)}</p>
            )}
          </details>
          {previous.some((row) => row.skew != null) ? (
            <label className="options-previous-toggle">
              <input
                type="checkbox"
                checked={showPrevious}
                onChange={(event) => setShowPrevious(event.target.checked)}
              />
              {translate(
                locale,
                "research.optionanalyticspanel.overlaySkewFromThePreviousComparableSnapshot",
              )}
              {previousSnapshotId}
            </label>
          ) : (
            <p className="options-notice">
              {translate(
                locale,
                "research.optionanalyticspanel.previousSnapshotSkew.withValues",
                {
                  translate: previousError
                    ? translate(
                        locale,
                        "research.optionanalyticspanel.loadFailedHistoricalComparisonCannotBeConfirmed",
                      )
                    : translate(
                        locale,
                        "research.optionanalyticspanel.onlyOneSnapshotOrNoComparableSkewOverlayUnavailable",
                      ),
                },
              )}
            </p>
          )}
          <div className="options-analytics__pair">
            <div>
              {data.iv_terms.some(
                (row) => row.call_iv != null || row.put_iv != null,
              ) ? (
                <OverviewTerminalChart
                  title={translate(
                    locale,
                    "research.optionanalyticspanel.atmIvTermStructure",
                  )}
                  summary={translate(
                    locale,
                    "research.optionanalyticspanel.compareAnnualizedNearAtmIvAcrossActualExpirationsMissing",
                  )}
                  option={term}
                  tableLabel={translate(
                    locale,
                    "research.optionanalyticspanel.viewTermStructureValues",
                  )}
                >
                  <table>
                    <thead>
                      <tr>
                        <th>
                          {translate(locale, "research.optionanalyticspanel.expiryDte")}
                        </th>
                        <th>
                          {translate(
                            locale,
                            "research.optionanalyticspanel.strikeMoneyness",
                          )}
                        </th>
                        <th>Call IV</th>
                        <th>Put IV</th>
                        <th>
                          {translate(
                            locale,
                            "research.optionanalyticspanel.sourceContracts",
                          )}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.iv_terms.map((row) => (
                        <tr key={row.expiration}>
                          <td>
                            {row.expiration} / {row.dte}
                          </td>
                          <td>
                            ${row.strike} / {number(row.moneyness, locale)}
                          </td>
                          <td>{percent(row.call_iv, locale)}</td>
                          <td>{percent(row.put_iv, locale)}</td>
                          <td>{row.contract_ids.join(" / ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </OverviewTerminalChart>
              ) : (
                <p className="options-notice">
                  {translate(
                    locale,
                    "research.optionanalyticspanel.atmIvTermStructureEligibleNearAtmIvIs",
                  )}
                </p>
              )}
              <ChartExplainer kind="term" />
            </div>
            <div>
              {data.skew_points.some((row) => row.skew != null) ? (
                <OverviewTerminalChart
                  title={translate(
                    locale,
                    "research.optionanalyticspanel.25DeltaIvSkew",
                  )}
                  summary={translate(
                    locale,
                    "research.optionanalyticspanel.25PutIvCallIvAtTheSameMaturity",
                  )}
                  option={skew}
                  tableLabel={translate(
                    locale,
                    "research.optionanalyticspanel.viewSkewValuesAndContracts",
                  )}
                >
                  <table>
                    <thead>
                      <tr>
                        <th>
                          {translate(locale, "research.optionanalyticspanel.expiryDte")}
                        </th>
                        <th>25Δ Put IV</th>
                        <th>25Δ Call IV</th>
                        <th>
                          {translate(
                            locale,
                            "research.optionanalyticspanel.differencePercentagePoints",
                          )}
                        </th>
                        <th>
                          {translate(
                            locale,
                            "research.optionanalyticspanel.sourceMissingReason",
                          )}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.skew_points.map((row) => (
                        <tr key={row.expiration}>
                          <td>
                            {row.expiration} / {row.dte}
                          </td>
                          <td>{percent(row.put_25delta_iv, locale)}</td>
                          <td>{percent(row.call_25delta_iv, locale)}</td>
                          <td>
                            {number(row.skew == null ? null : row.skew * 100, locale)}
                          </td>
                          <td>
                            {row.contract_ids.join(" / ")}{" "}
                            {explain(row.reasons, locale)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </OverviewTerminalChart>
              ) : (
                <p className="options-notice">
                  {translate(
                    locale,
                    "research.optionanalyticspanel.25DeltaIvSkewEligibleContractsOnBothSides.withValues",
                    {
                      explain: explain(
                        data.skew_points.flatMap((row) => row.reasons),
                        locale,
                      ),
                    },
                  )}
                </p>
              )}
              <ChartExplainer kind="skew" />
            </div>
          </div>
          {hasHistory ? (
            <OverviewTerminalChart
              title={translate(
                locale,
                "research.optionanalyticspanel.historicalStructure",
                {
                  value:
                    historyMetric === "oi"
                      ? "OI"
                      : translate(
                          locale,
                          "research.optionanalyticspanel.unsignedGamma",
                        ),
                },
              )}
              summary={translate(
                locale,
                "research.optionanalyticspanel.usesComparableSavedSnapshotsAtActualMarketTimesSquares",
                { historyTimeCount: historyTimes.size },
              )}
              option={history}
              tableLabel={translate(
                locale,
                "research.optionanalyticspanel.viewHistoricalValuesAndSnapshotTimes",
              )}
            >
              <table>
                <thead>
                  <tr>
                    <th>
                      {translate(locale, "research.optionanalyticspanel.marketTime2")}
                    </th>
                    <th>{translate(locale, "research.optionanalyticspanel.strike")}</th>
                    <th>
                      {historyMetric === "oi"
                        ? translate(locale, "research.optionanalyticspanel.oiContracts")
                        : "Gamma (USD / 1%)"}
                    </th>
                    <th>
                      {translate(
                        locale,
                        "research.optionanalyticspanel.underlyingPrice",
                      )}
                    </th>
                    <th>
                      {translate(
                        locale,
                        "research.optionanalyticspanel.sourceAvailabilitySnapshot",
                      )}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.history.map((row) => (
                    <tr key={`${row.snapshot_id}-${row.strike}`}>
                      <td>{time(row.market_as_of, locale)}</td>
                      <td>${row.strike}</td>
                      <td>{number(row[historyMetric], locale)}</td>
                      <td>${row.underlying_price}</td>
                      <td>
                        {time(row.source_available_at, locale)} / {row.snapshot_id}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </OverviewTerminalChart>
          ) : (
            <p className="options-notice">
              {translate(
                locale,
                "research.optionanalyticspanel.historicalStructureFewerThanTwoComparableSavedMarketTimes",
              )}
            </p>
          )}
          <ChartExplainer kind="history" />
          <details>
            <summary>
              {translate(
                locale,
                "research.optionanalyticspanel.strikeConcentrationAndDistanceEligibleContracts",
              )}
            </summary>
            <p className="options-notice">
              {translate(
                locale,
                "research.optionanalyticspanel.concentrationShowsTheStructuralDistributionOnlyDistanceStrikeUnderlying",
              )}
            </p>
            <table>
              <thead>
                <tr>
                  <th>
                    {translate(locale, "research.optionanalyticspanel.metricUnit")}
                  </th>
                  <th>{translate(locale, "research.optionanalyticspanel.strike")}</th>
                  <th>{translate(locale, "research.optionanalyticspanel.rawValue")}</th>
                  <th>{translate(locale, "research.optionanalyticspanel.share")}</th>
                  <th>
                    {translate(
                      locale,
                      "research.optionanalyticspanel.distanceFromSpot",
                    )}
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.concentration.map((row) => (
                  <tr key={`${row.metric}-${row.strike}`}>
                    <td>
                      {row.metric} /{" "}
                      {row.metric === "gamma"
                        ? "USD / 1%"
                        : translate(locale, "research.optionanalyticspanel.contracts")}
                    </td>
                    <td>${row.strike}</td>
                    <td>{number(row.value, locale)}</td>
                    <td>{percent(row.share, locale)}</td>
                    <td>{percent(row.distance_ratio, locale)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
          <details>
            <summary>
              {translate(
                locale,
                "research.optionanalyticspanel.featureValuesMissingReasonsAndSourceSnapshots",
              )}
            </summary>
            <p className="options-notice">
              {translate(
                locale,
                "research.optionanalyticspanel.sourceSnapshot.withValues",
                {
                  sourceSnapshotIds:
                    data.source_snapshot_ids.join(" / ") ||
                    translate(locale, "research.optionanalyticspanel.none"),
                },
              )}
            </p>
            <table>
              <thead>
                <tr>
                  <th>
                    {translate(
                      locale,
                      "research.optionanalyticspanel.featureVersion.withValues",
                      { featureVersion: data.feature_version },
                    )}
                  </th>
                  <th>
                    {translate(locale, "research.optionanalyticspanel.rawValue2")}
                  </th>
                  <th>{translate(locale, "research.optionanalyticspanel.unit")}</th>
                  <th>
                    {translate(locale, "research.optionanalyticspanel.missingReason")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(data.values).map(([key, value]) => (
                  <tr key={key}>
                    <td>{key}</td>
                    <td>{number(value, locale)}</td>
                    <td>{featureUnit(key, locale)}</td>
                    <td>{explain(data.missing_reasons[key] ?? [], locale) || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
    </section>
  );
}
