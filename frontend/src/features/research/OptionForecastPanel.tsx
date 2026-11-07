import { ResearchErrorDetail } from "./ResearchErrorDetail";
import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { createResearchApiClient } from "../../api/client";
import type { components, paths } from "../../api/generated/schema";
import { formatUsMarketTime } from "../../app/presentation";
type Response = components["schemas"]["OptionForecastResponse"];
type Query = NonNullable<
  paths["/api/v1/instruments/{symbol}/options/forecast"]["get"]["parameters"]["query"]
>;
const client = createResearchApiClient({ trackConnectivity: false });
const percent = (value: number) => `${(value * 100).toFixed(2)}%`;
const time = (value: string | null | undefined, locale: Locale = "zh-CN") =>
  value
    ? formatUsMarketTime(value, locale)
    : translate(locale, "research.optionanalyticspanel.unconfirmed");
const statusLabels = (locale: Locale = "zh-CN") => ({
  enhanced: translate(locale, "research.optionforecastpanel.optionsEnhancedModel"),
  baseline_fallback: translate(
    locale,
    "research.optionforecastpanel.fallbackToStockBaseline",
  ),
  refused: translate(locale, "research.optionforecastpanel.abstain"),
  research_only: translate(
    locale,
    "research.optionforecastpanel.researchChallengerUnpublished",
  ),
});
const candidateLabels = (locale: Locale = "zh-CN"): Record<string, string> => ({
  promoted: translate(locale, "research.optionforecastpanel.promoted"),
  research_only: translate(locale, "research.optionforecastpanel.researchOnly"),
  unavailable: translate(locale, "research.optionforecastpanel.unavailable"),
  candidate_evidence_invalid: translate(
    locale,
    "research.optionforecastpanel.challengerEvidenceInvalid",
  ),
  untrained_historical_evidence_missing: translate(
    locale,
    "research.optionforecastpanel.notTrainedHistoricalEvidenceMissing",
  ),
});
const reasonLabels = (locale: Locale = "zh-CN"): Record<string, string> => ({
  option_candidate_unpromoted: translate(
    locale,
    "research.optionforecastpanel.optionsChallengerNotPromoted",
  ),
  candidate_not_promoted: translate(
    locale,
    "research.optionforecastpanel.optionsChallengerNotPromoted",
  ),
  option_candidate_not_promoted: translate(
    locale,
    "research.optionforecastpanel.optionsChallengerNotPromoted",
  ),
  option_candidate_integrity_failed: translate(
    locale,
    "research.optionforecastpanel.challengerIntegrityCheckFailed",
  ),
  stock_feature_definition_changed: translate(
    locale,
    "research.optionforecastpanel.stockFeatureDefinitionChanged",
  ),
  no_option_snapshot_available_at_stock_cutoff: translate(
    locale,
    "research.optionforecastpanel.noOptionsSnapshotWasAvailableAtTheStockCutoff",
  ),
  option_source_definition_changed_requires_reevaluation: translate(
    locale,
    "research.optionforecastpanel.optionsSourceDefinitionChangedReevaluationRequired",
  ),
  no_promoted_model_precedes_inference_cutoff: translate(
    locale,
    "research.optionforecastpanel.noPromotedModelAtTheForecastCutoff",
  ),
  enhanced_model_unavailable: translate(
    locale,
    "research.optionforecastpanel.enhancedModelUnavailable",
  ),
  no_enhanced_model: translate(
    locale,
    "research.optionforecastpanel.noPromotedEnhancedModel",
  ),
  no_option_snapshot: translate(
    locale,
    "research.optionanalyticspanel.noSavedOptionsSnapshot",
  ),
  option_snapshot_invalid: translate(
    locale,
    "research.optionforecastpanel.optionsInputSnapshotInvalid",
  ),
  option_features_unavailable: translate(
    locale,
    "research.optionforecastpanel.optionsInputFeaturesUnavailable",
  ),
  option_snapshot_too_old: translate(
    locale,
    "research.optionforecastpanel.optionsSnapshotExceedsTheRegisteredModelAgeLimit",
  ),
  baseline_unavailable: translate(
    locale,
    "research.optionforecastpanel.stockBaselineForecastUnavailable",
  ),
  stock_baseline_unavailable: translate(
    locale,
    "research.optionforecastpanel.stockBaselineForecastUnavailable",
  ),
  stock_forecast_unavailable: translate(
    locale,
    "research.optionforecastpanel.currentStockForecastUnavailable",
  ),
  option_inference_unavailable: translate(
    locale,
    "research.optionforecastpanel.noSavedEnhancedForecastAvailable",
  ),
  option_inference_snapshot_mismatch: translate(
    locale,
    "research.optionforecastpanel.enhancedForecastInputSnapshotDoesNotMatchTheSelection",
  ),
  no_champion_candidate: translate(
    locale,
    "research.optionforecastpanel.noStockChampionModelHasPassedAllGates",
  ),
  stock_prediction_refused: translate(
    locale,
    "research.optionforecastpanel.stockModelAbstained",
  ),
  historical_time_evidence_missing: translate(
    locale,
    "research.optionanalyticspanel.historicalAvailabilityEvidenceMissing",
  ),
  feature_version_mismatch: translate(
    locale,
    "research.optionforecastpanel.inputFeatureVersionMismatch",
  ),
  model_not_promoted: translate(
    locale,
    "research.optionforecastpanel.modelNotPromoted",
  ),
});
export async function loadOptionForecastFromApi(
  symbol: string,
  query: Query,
): Promise<Response> {
  const { data } = await client.get("/api/v1/instruments/{symbol}/options/forecast", {
    params: { path: { symbol }, query },
    signal: AbortSignal.timeout(15000),
  });
  if (!data) throw new Error("Stock forecast source response unavailable");
  return data;
}
export function OptionForecastPanel({
  symbol,
  optionSnapshotId,
  optionAsOf,
  loadData = loadOptionForecastFromApi,
}: {
  symbol: string;
  optionSnapshotId?: string | null | undefined;
  optionAsOf?: string | null | undefined;
  loadData?: (symbol: string, query: Query) => Promise<Response>;
}) {
  const { locale } = useI18n();
  const [params] = useSearchParams();
  const query = useMemo<Query>(
    () => ({
      ...(optionSnapshotId ? { option_snapshot_id: optionSnapshotId } : {}),
      ...(optionAsOf ? { option_as_of: optionAsOf } : {}),
      ...(params.get("snapshot_id")
        ? { stock_snapshot_id: params.get("snapshot_id") }
        : {}),
      ...(params.get("as_of") ? { stock_as_of: params.get("as_of") } : {}),
    }),
    [optionSnapshotId, optionAsOf, params],
  );
  const result = useQuery({
    queryKey: ["option-forecast", symbol, query],
    queryFn: () => loadData(symbol, query),
    retry: false,
    staleTime: 60000,
  });
  const data = result.data?.data;
  return (
    <section
      aria-label={translate(
        locale,
        "research.optionforecastpanel.optionsEnhancedStockForecastsAndFallback",
      )}
      className="options-forecast"
    >
      <h3>
        {translate(
          locale,
          "research.optionforecastpanel.stockForecastsOver1520TradingDays",
        )}
      </h3>
      {result.isPending && (
        <p role="status">
          {translate(
            locale,
            "research.optionforecastpanel.checkingSavedModelsAndForecastSources",
          )}
        </p>
      )}
      {result.isError && (
        <div role="alert">
          {translate(locale, "research.optionforecastpanel.unableToLoadForecastSource")}
          <ResearchErrorDetail message={result.error.message} />{locale === "en-US" ? "." : "。"}
          {data
            ? translate(
                locale,
                "research.optionforecastpanel.keepingThePreviousForecastAndItsOriginalSourceTime",
              )
            : translate(
                locale,
                "research.optionforecastpanel.forecastValuesAreHiddenRetryLoadingTheSnapshot",
              )}
        </div>
      )}
      {data && (
        <>
          <p className="options-notice">
            <strong>{statusLabels(locale)[data.status]}</strong>{" "}
            {translate(locale, "research.optionforecastpanel.outputSource", {
              source:
                data.model_source ??
                translate(locale, "research.optionforecastpanel.noAvailableOutput"),
              version:
                data.model_version ??
                translate(locale, "research.optionanalyticspanel.none"),
            })}
          </p>
          <p className="options-notice">
            {translate(
              locale,
              "research.optionforecastpanel.optionsChallengerStatus.withValues",
              {
                value:
                  candidateLabels(locale)[data.candidate.status] ??
                  data.candidate.status,
                version:
                  data.candidate.version ??
                  translate(locale, "research.optionanalyticspanel.none"),
                translate: data.candidate.promoted
                  ? translate(locale, "research.optionforecastpanel.promoted")
                  : translate(locale, "research.optionforecastpanel.notPromoted"),
              },
            )}
          </p>
          {data.reasons.length > 0 && (
            <p className="options-notice">
              {translate(locale, "research.optionforecastpanel.reason.withValues", {
                reasons: data.reasons
                  .map((reason) => reasonLabels(locale)[reason] ?? reason)
                  .join(locale === "en-US" ? "; " : "；"),
              })}
            </p>
          )}
          <dl className="options-metadata">
            <div>
              <dt>
                {translate(
                  locale,
                  "research.optionforecastpanel.stockForecastSnapshotCutoff",
                )}
              </dt>
              <dd>
                {data.stock_snapshot_id ??
                  translate(locale, "research.optionanalyticspanel.none")}{" "}
                / {time(data.stock_cutoff, locale)}
              </dd>
            </div>
            <div>
              <dt>
                {translate(
                  locale,
                  "research.optionforecastpanel.optionsInputSnapshotCutoff",
                )}
              </dt>
              <dd>
                {data.option_snapshot_id ??
                  translate(locale, "research.optionanalyticspanel.none")}{" "}
                / {time(data.option_cutoff, locale)}
              </dd>
            </div>
          </dl>
          <details>
            <summary>
              {translate(
                locale,
                "research.optionforecastpanel.modelArtifactAndGenerationTime",
              )}
            </summary>
            <dl className="options-metadata">
              <div>
                <dt>
                  {translate(
                    locale,
                    "research.optionforecastpanel.forecastGeneratedAt",
                  )}
                </dt>
                <dd>{time(data.generated_at, locale)}</dd>
              </div>
              <div>
                <dt>
                  {translate(
                    locale,
                    "research.optionforecastpanel.inputHashModelArtifactHash",
                  )}
                </dt>
                <dd>
                  {data.input_hash ??
                    translate(
                      locale,
                      "research.optionforecastpanel.sourceNotProvided",
                    )}{" "}
                  /{" "}
                  {data.model_bundle_hash ??
                    translate(locale, "research.optionforecastpanel.sourceNotProvided")}
                </dd>
              </div>
            </dl>
          </details>
          {data.horizons.length > 0 &&
          data.status !== "refused" &&
          data.status !== "research_only" ? (
            <>
              <p className="options-notice">
                {translate(
                  locale,
                  "research.optionforecastpanel.q10Q50Q90AreEndOfHorizonStockReturn",
                )}
              </p>
              <table>
                <thead>
                  <tr>
                    <th>
                      {translate(locale, "research.optionanalyticspanel.tradingDays")}
                    </th>
                    <th>q10</th>
                    <th>q50</th>
                    <th>q90</th>
                    <th>
                      {translate(
                        locale,
                        "research.optionforecastpanel.separateCalibratedInterval",
                      )}
                    </th>
                    <th>
                      {translate(
                        locale,
                        "research.optionforecastpanel.modelDirectionProbabilitiesUpFlatDown",
                      )}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.horizons.map((row) => (
                    <tr key={row.horizon_days}>
                      <td>{row.horizon_days}</td>
                      <td>{percent(row.q10)}</td>
                      <td>{percent(row.q50)}</td>
                      <td>{percent(row.q90)}</td>
                      <td>
                        {row.interval_low != null && row.interval_high != null ? (
                          <>
                            {percent(row.interval_low)}–{percent(row.interval_high)}
                            <br />
                            {translate(
                              locale,
                              "research.optionforecastpanel.intervalEvidence",
                              {
                                coverage:
                                  row.target_coverage == null
                                    ? translate(
                                        locale,
                                        "research.instrumentidentity.notProvided",
                                      )
                                    : percent(row.target_coverage),
                                method:
                                  row.interval_method ??
                                  translate(
                                    locale,
                                    "research.optionforecastpanel.methodNotProvided",
                                  ),
                              },
                            )}
                          </>
                        ) : (
                          translate(
                            locale,
                            "research.optionforecastpanel.sourceDidNotProvideACalibratedInterval",
                          )
                        )}
                      </td>
                      <td>
                        {row.probabilities?.up != null &&
                        row.probabilities.flat != null &&
                        row.probabilities.down != null
                          ? `${percent(row.probabilities.up)} / ${percent(row.probabilities.flat)} / ${percent(row.probabilities.down)}`
                          : translate(
                              locale,
                              "research.optionforecastpanel.notProvidedByTheSourceModel",
                            )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <p className="options-notice">
              {translate(
                locale,
                "research.optionforecastpanel.noForecastValuesAreReadyForPublicationResearchChallengers",
              )}
            </p>
          )}
        </>
      )}
    </section>
  );
}
