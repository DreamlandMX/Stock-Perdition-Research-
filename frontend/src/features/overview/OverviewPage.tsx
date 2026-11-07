import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useEffect, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, TriangleAlert } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { ResearchApiError } from "../../api/client";
import { snapshotContextOptions } from "../../api/snapshot-context";
import { loadOverviewFromApi } from "../../api/overview";
import type { components, paths } from "../../api/generated/schema";
import { SNAPSHOT_IDENTITY_KEYS } from "../../app/snapshot-refresh";
import { decodeResearchUrlState, encodeResearchUrlState } from "../../app/url-state";
import type { ResearchUrlState } from "../../app/url-state";
import { FormalPredictionStatus } from "../../components/FormalPredictionStatus";
import {
  ResearchModeSwitch,
  type ResearchMode,
} from "../../components/ResearchModeSwitch";
import { SectorHealthTerminal } from "./SectorHealthTerminal";
import { MarketStructureTerminal } from "./MarketStructureTerminal";
import { AnomalyWorkbench } from "./AnomalyWorkbench";
import { CoverageStatusPanel } from "./CoverageStatusPanel";
import { MarketMoversBoard } from "./MarketMoversBoard";
import { MarketProxyStrip } from "./MarketProxyStrip";
import { OpportunityTable, ResearchOpportunityTable } from "./OpportunityTable";
import { OverviewHeaderBand } from "./OverviewHeaderBand";
import { RiskDivergencePanel } from "./RiskDivergencePanel";
import { useOptionsOverview } from "../options/useOptionsOverview";
import { SectorMarketMap } from "./SectorMarketMap";
import { buildMarketResearchHref, normalizeMarketMapState } from "./overview-terminal";
import "./overview.css";
import "./overview-map.css";
import "./overview-v6.css";
import { buildOverviewHistory } from "./overview-history";
import { useOverviewHistory } from "./useOverviewHistory";
import {
  OverviewChapterNav,
  OverviewProxyCharts,
  OverviewBreadthStrip,
  OverviewMarketHistory,
  OverviewSignalBoard,
  OverviewSectorHistory,
  OverviewSymbolCharts,
  OverviewSnapshotComparison,
  OverviewRiskStatus,
} from "./OverviewHistoryPanels";

type OverviewResponse = components["schemas"]["ResearchResponse_OverviewReadModel_"];
type OverviewQuery = NonNullable<
  paths["/api/v1/overview"]["get"]["parameters"]["query"]
>;

export type OverviewLoader = (query: OverviewQuery) => Promise<OverviewResponse>;

function addOptional<K extends keyof OverviewQuery>(
  query: OverviewQuery,
  key: K,
  value: OverviewQuery[K] | undefined,
) {
  if (value !== undefined) {
    query[key] = value;
  }
}

function buildOverviewRequest(
  state: ResearchUrlState,
  mode: ResearchMode,
): OverviewQuery {
  const query: OverviewQuery = { horizon: state.horizon, mode };
  addOptional(query, "as_of", state.asOf);
  addOptional(query, "snapshot_id", state.snapshotId);
  addOptional(query, "dataset_revision", state.versions.datasetRevision);
  addOptional(query, "feature_set_version", state.versions.featureSetVersion);
  addOptional(query, "universe_version", state.versions.universeVersion);
  addOptional(query, "model_version", state.versions.modelVersion);
  addOptional(query, "calibration_version", state.versions.calibrationVersion);
  return query;
}

export function buildOverviewQueryKey(
  state: ResearchUrlState,
  mode: ResearchMode = "research",
) {
  return [
    "overview",
    {
      asOf: state.asOf ?? null,
      calibrationVersion: state.versions.calibrationVersion ?? null,
      datasetRevision: state.versions.datasetRevision ?? null,
      featureSetVersion: state.versions.featureSetVersion ?? null,
      horizon: state.horizon,
      modelVersion: state.versions.modelVersion ?? null,
      mode,
      revisionNo: state.revisionNo ?? null,
      snapshotId: state.snapshotId ?? null,
      universeVersion: state.versions.universeVersion ?? null,
    },
  ] as const;
}

function sameQueryKey(left: readonly unknown[], right: readonly unknown[]) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function resolvedUrlState(
  state: ResearchUrlState,
  snapshot: components["schemas"]["ResolvedSnapshot"],
): ResearchUrlState {
  return {
    ...state,
    asOf: snapshot.as_of,
    revisionNo: snapshot.revision_no,
    snapshotId: snapshot.snapshot_id,
    versions: {
      ...state.versions,
      calibrationVersion: snapshot.calibration_version ?? undefined,
      datasetRevision: snapshot.dataset_revision,
      featureSetVersion: snapshot.feature_set_version,
      modelVersion: snapshot.model_version ?? undefined,
      universeVersion: snapshot.universe_version,
    },
  };
}

function snapshotOnlySearch(state: ResearchUrlState) {
  const encoded = encodeResearchUrlState(state);
  const params = new URLSearchParams();
  for (const key of SNAPSHOT_IDENTITY_KEYS) {
    const value = encoded.get(key);
    if (value !== null) params.set(key, value);
  }
  const search = params.toString();
  return search === "" ? "" : `?${search}`;
}

function isOlderSnapshot(
  selected: components["schemas"]["ResolvedSnapshot"],
  latest: components["schemas"]["ResolvedSnapshot"] | undefined,
) {
  if (latest === undefined || selected.snapshot_id === latest.snapshot_id) return false;
  const selectedTime = Date.parse(selected.as_of);
  const latestTime = Date.parse(latest.as_of);
  return (
    selectedTime < latestTime ||
    (selectedTime === latestTime && selected.revision_no < latest.revision_no)
  );
}

function proxyHorizon(params: URLSearchParams): 1 | 5 | 20 {
  const value = Number(params.get("proxy_horizon") ?? "5");
  return value === 1 || value === 20 ? value : 5;
}

export function normalizeOverviewSearchParams(params: URLSearchParams) {
  const normalized = new URLSearchParams(params);
  const legacyProxyHorizon = normalized.get("horizon");
  if (legacyProxyHorizon === "1" || legacyProxyHorizon === "20") {
    if (!normalized.has("proxy_horizon")) {
      normalized.set("proxy_horizon", legacyProxyHorizon);
    }
    normalized.delete("horizon");
  }
  return normalized;
}

export interface OverviewPageProps {
  loadOverview?: OverviewLoader;
}

export function OverviewPage({
  loadOverview = loadOverviewFromApi,
}: OverviewPageProps) {
  const { locale } = useI18n();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const overviewSearchParams = useMemo(
    () => normalizeOverviewSearchParams(searchParams),
    [searchParams],
  );
  const urlState = useMemo(
    () => decodeResearchUrlState(overviewSearchParams),
    [overviewSearchParams],
  );
  const selectedProxyHorizon = proxyHorizon(overviewSearchParams);
  const mode: ResearchMode =
    overviewSearchParams.get("mode") === "challenger" ||
    overviewSearchParams.get("mode") === "formal"
      ? (overviewSearchParams.get("mode") as ResearchMode)
      : "research";
  const queryKey = buildOverviewQueryKey(urlState, mode);
  const request = buildOverviewRequest(urlState, mode);
  const query = useQuery({
    placeholderData: (previousData, previousQuery) =>
      previousQuery !== undefined && sameQueryKey(previousQuery.queryKey, queryKey)
        ? previousData
        : undefined,
    queryFn: () => loadOverview(request),
    queryKey,
  });
  // The application shell owns this query; the overview only reads its cache.
  const latestIdentity = useQuery({
    enabled: false,
    ...snapshotContextOptions(),
  });

  useEffect(() => {
    if (query.data === undefined) {
      return;
    }
    const nextState = resolvedUrlState(urlState, query.data.resolved_snapshot);
    const nextKey = buildOverviewQueryKey(nextState, mode);
    if (sameQueryKey(queryKey, nextKey)) {
      return;
    }
    queryClient.setQueryData(nextKey, query.data);
    const nextParams = encodeResearchUrlState(nextState);
    nextParams.set("mode", mode);
    if (selectedProxyHorizon !== 5 || searchParams.has("proxy_horizon")) {
      nextParams.set("proxy_horizon", String(selectedProxyHorizon));
    }
    for (const parameter of ["map_return", "map_sector", "map_symbol"] as const) {
      const value = searchParams.get(parameter);
      if (value !== null) {
        nextParams.set(parameter, value);
      }
    }
    setSearchParams(nextParams, { replace: true });
  }, [
    mode,
    query.data,
    queryClient,
    queryKey,
    searchParams,
    selectedProxyHorizon,
    setSearchParams,
    urlState,
  ]);

  const historySymbols = useMemo(
    () => [
      ...new Set([
        ...(query.data?.data.proxies.map((p) => p.symbol) ?? []),
        ...(query.data?.data.internal_evidence?.market_points.map((p) => p.symbol) ??
          []),
      ]),
    ],
    [query.data],
  );
  const daily = useOverviewHistory(query.data?.resolved_snapshot, historySymbols);
  const options = useOptionsOverview(
    {
      snapshot_id: query.data?.resolved_snapshot.snapshot_id,
      as_of: query.data?.resolved_snapshot.as_of,
    },
    !!query.data,
  );
  const history = useMemo(
    () =>
      buildOverviewHistory(
        query.data?.data.internal_evidence?.market_points ?? [],
        daily.histories,
        query.data?.resolved_snapshot.as_of ?? "1970-01-01",
      ),
    [query.data, daily.histories],
  );

  if (query.isPending) {
    return (
      <section
        aria-label={translate(locale, "overview.OverviewPage.loadingOverviewEvidence")}
        className="overview-loading"
        role="status"
      >
        <h1 className="overview-page__title">
          {translate(locale, "overview.OverviewPage.marketOverview")}
        </h1>
        <RefreshCw aria-hidden="true" size={20} strokeWidth={1.5} />
        <span>
          {translate(locale, "options.OptionsPage.loadingSavedResearchSnapshot")}
        </span>
      </section>
    );
  }

  if (query.data === undefined) {
    return (
      <section
        aria-label={translate(locale, "overview.OverviewPage.overviewDataUnavailable")}
        className="overview-error"
        role="alert"
      >
        <TriangleAlert aria-hidden="true" size={20} strokeWidth={1.5} />
        <div>
          <h1>{translate(locale, "overview.OverviewPage.overviewDataUnavailable")}</h1>
          <p>
            {translate(
              locale,
              "overview.OverviewPage.theSavedResearchSnapshotCannotBeReadUnverifiedProbabilities",
            )}
          </p>
          <button onClick={() => void query.refetch()} type="button">
            <RefreshCw aria-hidden="true" size={18} strokeWidth={1.5} />
            {translate(locale, "overview.OverviewPage.retryOverviewData")}
          </button>
        </div>
      </section>
    );
  }

  const response = query.data;
  const retainedStatus =
    query.isRefetchError && query.error !== null
      ? query.error instanceof ResearchApiError && query.error.kind === "http"
        ? "stale"
        : "offline"
      : null;
  const visibleStatus = retainedStatus ?? response.serving_status;
  const viewStatus =
    retainedStatus === null ? visibleStatus : `${retainedStatus}-retained`;
  const snapshotState = resolvedUrlState(urlState, response.resolved_snapshot);
  const formalPending =
    mode === "formal" &&
    response.resolved_snapshot.prediction_readiness !== "forecast_ready";
  const internalEvidence = response.data.internal_evidence ?? null;
  const marketPoints = internalEvidence?.market_points ?? [];
  const sectors = [...new Set(marketPoints.map((point) => point.sector))];
  const mapState = normalizeMarketMapState(overviewSearchParams, sectors);
  const marketLinkState = {
    asOf: snapshotState.asOf,
    calibrationVersion: snapshotState.versions.calibrationVersion ?? undefined,
    datasetRevision: snapshotState.versions.datasetRevision,
    featureSetVersion: snapshotState.versions.featureSetVersion,
    horizon: snapshotState.horizon,
    modelVersion: snapshotState.versions.modelVersion ?? undefined,
    snapshotId: snapshotState.snapshotId,
    universeVersion: snapshotState.versions.universeVersion,
  };
  const snapshotSearch = snapshotOnlySearch(snapshotState);
  const isHistorical = isOlderSnapshot(
    response.resolved_snapshot,
    latestIdentity.data?.resolved_snapshot,
  );
  const researchRows =
    mode === "challenger"
      ? (response.data.challenger_opportunities ?? [])
      : (response.data.research_opportunities ?? []);
  const updateParam = (key: string, value: string, replace = false) => {
    const next = new URLSearchParams(searchParams);
    next.set(key, value);
    setSearchParams(next, { replace });
  };

  return (
    <section
      aria-label={translate(locale, "overview.OverviewPage.marketOverviewEvidence")}
      className="overview-page ov-page ov-v6"
      data-layout="v6-chapters"
      data-serving-status={visibleStatus}
      data-view-status={viewStatus}
    >
      <div
        className="ov-v6-tape"
        aria-label={translate(locale, "overview.OverviewPage.marketTicker")}
      >
        <div>
          {marketPoints.map((p) => (
            <a key={p.symbol} href={buildMarketResearchHref(p.symbol, marketLinkState)}>
              <b>{p.symbol.replace(/^US\./, "")}</b> {p.last_close?.toFixed(2) ?? "—"}{" "}
              <span
                className={
                  p.session_return != null && p.session_return < 0
                    ? "ov-tone--down"
                    : "ov-tone--up"
                }
              >
                {p.session_return == null
                  ? "—"
                  : `${p.session_return > 0 ? "+" : ""}${(p.session_return * 100).toFixed(1)}%`}
              </span>
            </a>
          ))}
        </div>
      </div>
      <OverviewHeaderBand
        data={response.data}
        optionsHref={`/options${snapshotSearch}`}
        gateHref={`/backtests?${encodeResearchUrlState(snapshotState).toString()}`}
        isHistorical={isHistorical}
        mode={mode}
        proxyHref={(symbol) => buildMarketResearchHref(symbol, marketLinkState)}
        servingStatus={visibleStatus}
        snapshot={response.resolved_snapshot}
        options={options.data}
        optionsPending={options.isPending}
        optionsError={options.isError}
      />
      <OverviewProxyCharts
        data={response.data}
        history={history}
        linkState={marketLinkState}
      />
      <OverviewBreadthStrip data={response.data} history={history} />
      <OverviewChapterNav />
      <div id="overview-market" className="ov-chapter-anchor" />
      {marketPoints.length > 0 && internalEvidence ? (
        <SectorMarketMap
          allSymbolsHref={`/screen${snapshotSearch}`}
          history={history}
          horizon={mapState.horizon}
          linkState={marketLinkState}
          onHorizonChange={(horizon) => updateParam("map_return", String(horizon))}
          onSectorChange={(sector) => updateParam("map_sector", sector)}
          onSelect={(symbol) => updateParam("map_symbol", symbol, true)}
          points={marketPoints}
          researchRows={researchRows}
          scorecards={internalEvidence.sector_scorecards}
          sector={mapState.sector}
          selectedSymbol={overviewSearchParams.get("map_symbol")}
        />
      ) : (
        <section className="ov-section" role="status">
          <h2>
            {translate(
              locale,
              "overview.OverviewPage.fullCrossSectionalEvidenceUnavailable",
            )}
          </h2>
          <p>
            {translate(
              locale,
              "overview.OverviewPage.noObservedInstrumentsAvailableInThisSnapshotPlaceholderData",
            )}
          </p>
        </section>
      )}
      <OverviewMarketHistory data={response.data} history={history} />
      <OverviewSignalBoard points={marketPoints} linkState={marketLinkState} />
      <header className="ov-chapter" id="overview-sectors">
        <i>02</i>
        <h2>{translate(locale, "overview.SectorRegimeMatrix.sector")}</h2>
        <p>
          {translate(
            locale,
            "overview.OverviewPage.whichSectorsAreDrivingOrDraggingTheMarket",
          )}
        </p>
        <a href="#overview-symbols">
          {translate(locale, "overview.OverviewPage.nextInstruments")}
        </a>
      </header>
      <OverviewSectorHistory
        data={response.data}
        history={history}
        linkState={marketLinkState}
      />
      <header className="ov-chapter" id="overview-symbols">
        <i>03</i>
        <h2>{translate(locale, "overview.OverviewPage.instrument")}</h2>
        <p>
          {translate(locale, "overview.OverviewPage.whoSMovingTodayHowFarAndHowMoves")}
        </p>
        <a href="#overview-research">
          {translate(locale, "overview.OverviewPage.nextResearch")}
        </a>
      </header>
      <div className="ov-section ov-v6-movers">
        <MarketMoversBoard
          horizon={1}
          linkState={marketLinkState}
          points={marketPoints}
          histories={history.histories}
        />
      </div>
      <OverviewSymbolCharts data={response.data} linkState={marketLinkState} />
      <header className="ov-chapter" id="overview-research">
        <i>04</i>
        <h2>{translate(locale, "overview.OverviewPage.research")}</h2>
        <p>
          {translate(
            locale,
            "overview.OverviewPage.rankedRuleEvidenceResearchResultsAreNonExecutable",
          )}
        </p>
        <a href="#overview-risk">
          {translate(locale, "overview.OverviewPage.nextRisksCoverage")}
        </a>
      </header>
      <section
        aria-label={translate(locale, "overview.OverviewPage.researchRanking")}
        className="ov-section ov-research"
      >
        <header className="ov-section__header">
          <div>
            <h2>{translate(locale, "overview.OverviewPage.researchRanking")}</h2>
            <p>
              {translate(
                locale,
                "overview.OverviewPage.rankedByUpwardRuleBreakoutTrendAnd20Day",
              )}
            </p>
          </div>
          <ResearchModeSwitch
            formalAvailable={
              response.resolved_snapshot.prediction_readiness === "forecast_ready"
            }
            mode={mode}
            onChange={(next) => updateParam("mode", next)}
          />
        </header>
        {mode === "challenger" ? (
          <div className="challenger-warning" role="alert">
            {translate(
              locale,
              "overview.OverviewPage.qualityGatesNotPassedExperimentalResearchOnlyChallengerOutput",
            )}
          </div>
        ) : null}
        {formalPending ? (
          <FormalPredictionStatus snapshot={response.resolved_snapshot} />
        ) : mode === "formal" ? (
          <OpportunityTable
            executionReady={
              response.resolved_snapshot.execution_readiness !== "blocked" &&
              response.resolved_snapshot.activation_basis !== "manual_override" &&
              response.resolved_snapshot.research_readiness !== "manual_unverified"
            }
            rows={response.data.top_opportunities}
            snapshotState={snapshotState}
          />
        ) : (
          <ResearchOpportunityTable
            mode={mode}
            rows={researchRows}
            snapshotState={snapshotState}
            histories={history.histories}
          />
        )}
      </section>
      {
        <details className="ov-section">
          <summary>
            {translate(locale, "overview.OverviewPage.verifiedIndexResearchEvidence")}
          </summary>
          <MarketProxyStrip
            horizon={selectedProxyHorizon}
            onHorizonChange={(h) => updateParam("proxy_horizon", String(h))}
            proxies={response.data.proxies}
          />
        </details>
      }
      <OverviewSnapshotComparison
        data={response.data}
        mode={mode}
        linkState={marketLinkState}
      />
      <header className="ov-chapter" id="overview-risk">
        <i>05</i>
        <h2>{translate(locale, "overview.OverviewPage.risksCoverage")}</h2>
        <p>
          {translate(locale, "overview.OverviewPage.changesToWatchAndHowFarTheDataCan")}
        </p>
      </header>
      <div className="ov-v6-grid ov-v6-grid--risk">
        <div className="ov-section">
          <RiskDivergencePanel
            data={response.data}
            predictionReadiness={response.resolved_snapshot.prediction_readiness}
            options={options.data}
          />
        </div>
        <OverviewRiskStatus response={{ ...response, serving_status: visibleStatus }} />
      </div>
      <CoverageStatusPanel
        data={response.data}
        healthHref={`/health${snapshotSearch}`}
        snapshot={response.resolved_snapshot}
        options={options.data}
      />
      <div
        className="ov-history-status"
        role="status"
        aria-label={translate(locale, "options.OptionsPage.historyChartLoadingStatus")}
      >
        {daily.pending
          ? translate(
              locale,
              "overview.OverviewPage.dailyBarsLoadedCurrentSnapshotInformationIsAvailable",
              { completed: daily.completed, total: daily.total },
            )
          : translate(
              locale,
              "overview.OverviewPage.observedDailyBarsRawPricesUnadjusted",
              {
                value: daily.completed - Object.keys(daily.failed).length,
                total: daily.total,
              },
            )}
        {history.excluded && !daily.pending
          ? translate(locale, "overview.OverviewPage.historyMissingForInstruments", {
              excluded: history.excluded,
            })
          : ""}
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
                    {symbol}: {reason}
                  </li>
                ))}
              </ul>
            </details>
          </>
        ) : null}
        {history.jumps.length ? (
          <details>
            <summary>
              {translate(locale, "overview.rawPriceJumps", {
                count: history.jumps.length,
              })}
            </summary>
            <p>
              {translate(
                locale,
                "overview.OverviewPage.adjacentClosesDifferByMoreThan40PossiblyDue",
              )}
            </p>
            <p>{history.jumps.join(";")}</p>
          </details>
        ) : null}
      </div>
      {internalEvidence && marketPoints.length ? (
        <details className="ov-section ov-v6-more">
          <summary>
            {translate(
              locale,
              "overview.OverviewPage.moreSectorAndResearchDiagnostics",
            )}
          </summary>
          <SectorHealthTerminal
            linkState={marketLinkState}
            points={marketPoints}
            scorecards={internalEvidence.sector_scorecards}
          />
          <MarketStructureTerminal
            evidence={internalEvidence}
            horizon={mapState.horizon}
            points={marketPoints}
          />
          <AnomalyWorkbench
            evidence={internalEvidence}
            linkState={marketLinkState}
            mode={mode}
            points={marketPoints}
          />
        </details>
      ) : null}
      <footer
        aria-label={translate(
          locale,
          "overview.OverviewPage.forecastVersionsAndLatency",
        )}
        className="overview-footer ov-footer"
        role="contentinfo"
      >
        <span>
          {translate(locale, "overview.OverviewPage.forecastBatch")}{" "}
          {response.resolved_snapshot.snapshot_id}{" "}
          {translate(locale, "overview.OverviewPage.model")}{" "}
          {response.resolved_snapshot.model_version ??
            translate(locale, "overview.OverviewPage.noChampionModel")}{" "}
          {translate(locale, "overview.OverviewPage.calibration")}{" "}
          {response.resolved_snapshot.calibration_version ??
            translate(locale, "overview.OverviewPage.notCalibrated")}{" "}
          {translate(locale, "overview.OverviewPage.inferenceTime")}{" "}
          {(response.data.total_inference_latency_ms / 1000).toFixed(2)}{" "}
          {translate(locale, "overview.OverviewPage.seconds")}
        </span>
        <p>
          {translate(
            locale,
            "overview.OverviewPage.forResearchPurposesOnlyThisDoesNotConstituteInvestment",
          )}
        </p>
      </footer>
    </section>
  );
}
