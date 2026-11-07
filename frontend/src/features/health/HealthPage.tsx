import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Database, RefreshCw, ShieldCheck } from "lucide-react";
import { createResearchApiClient } from "../../api/client";
import type { components, paths } from "../../api/generated/schema";
import {
  formatUsMarketTime,
  servingStatusLabel,
  isRetainedHistoricalGap,
} from "../../app/presentation";
import { snapshotQueryFromSearch } from "../../app/snapshot-refresh";
import { DataStatusBanner } from "../../components/DataStatusBanner";
import { EodUpdatePanel } from "../operations/EodUpdatePanel";
import { M7AuditPanel, type M7AuditPanelProps } from "../m7/M7AuditPanel";
import { CapabilityMatrix } from "./CapabilityMatrix";
import { DataHealthTable } from "./DataHealthTable";
import { IncidentList } from "./IncidentList";
import { ModelTaskMatrix } from "./ModelTaskMatrix";
import { UniverseVersionTable } from "./UniverseVersionTable";
import { OptionCollectionMatrix } from "./OptionCollectionMatrix";
import "./health.css";
type CapabilityResponse = components["schemas"]["CapabilityResponse"];
type DataHealthResponse =
  components["schemas"]["ResearchResponse_DataHealthReadModel_"];
type HealthResponse = components["schemas"]["HealthResponse"];
type ModelResponse = components["schemas"]["ResearchResponse_ModelMatrixReadModel_"];
type UniverseResponse = components["schemas"]["ResearchResponse_UniverseReadModel_"];
type Readiness = components["schemas"]["Readiness"];
type ServingStatus = components["schemas"]["ServingStatus"];
type DataHealthQuery = NonNullable<
  paths["/api/v1/data-health"]["get"]["parameters"]["query"]
>;
export type HealthLoader = () => Promise<HealthResponse>;
export type CapabilityLoader = () => Promise<CapabilityResponse>;
export type DataHealthLoader = () => Promise<DataHealthResponse>;
export type ModelLoader = () => Promise<ModelResponse>;
export type UniverseLoader = () => Promise<UniverseResponse>;
const apiClient = createResearchApiClient();
function requireData<T>(data: T | undefined, label: string) {
  if (data === undefined) throw new Error(`${label} response unavailable`);
  return data;
}
async function loadHealthFromApi() {
  const { data } = await apiClient.get("/api/v1/health");
  return requireData(data, "health");
}
async function loadCapabilitiesFromApi() {
  const { data } = await apiClient.get("/api/v1/data-capabilities");
  return requireData(data, "capability");
}
async function loadDataHealthFromApi(snapshotQuery: DataHealthQuery = {}) {
  const query: DataHealthQuery = {
    direction: "asc",
    limit: 100,
    page: 1,
    sort: "serving_status",
    ...snapshotQuery,
  };
  const { data } = await apiClient.get("/api/v1/data-health", {
    params: { query },
  });
  const first = requireData(data, "data health");
  const pages = Math.ceil(first.data.total / first.data.page_size);
  const remaining = await Promise.all(
    Array.from({ length: Math.max(0, pages - 1) }, async (_, index) => {
      const result = await apiClient.get("/api/v1/data-health", {
        params: {
          query: {
            ...query,
            snapshot_id: first.resolved_snapshot.snapshot_id,
            page: index + 2,
          },
        },
      });
      const response = requireData(result.data, "data health page");
      if (
        response.resolved_snapshot.snapshot_id !==
          first.resolved_snapshot.snapshot_id ||
        response.data.total !== first.data.total
      ) {
        throw new Error("data health snapshot changed during pagination");
      }
      return response.data.items;
    }),
  );
  const items = [...first.data.items, ...remaining.flat()];
  if (items.length !== first.data.total)
    throw new Error("data health pages incomplete");
  return { ...first, data: { ...first.data, items } };
}
async function loadModelsFromApi(
  query: ReturnType<typeof snapshotQueryFromSearch> = {},
) {
  const { data } = await apiClient.get("/api/v1/models", {
    params: { query },
  });
  return requireData(data, "models");
}
async function loadUniversesFromApi(
  query: ReturnType<typeof snapshotQueryFromSearch> = {},
) {
  const { data } = await apiClient.get("/api/v1/universes", {
    params: { query },
  });
  return requireData(data, "universes");
}
const READINESS_RANK: Record<Readiness, number> = {
  ready: 0,
  degraded: 1,
  blocked: 2,
};
function worstReadiness(values: readonly Readiness[]) {
  return values.reduce<Readiness>(
    (worst, value) => (READINESS_RANK[value] > READINESS_RANK[worst] ? value : worst),
    "ready",
  );
}
function readinessLabel(readiness: Readiness, locale: Locale = "zh-CN") {
  return {
    blocked: translate(locale, "health.blocked"),
    degraded: translate(locale, "health.degraded"),
    ready: translate(locale, "health.ready"),
  }[readiness];
}
function readinessServingStatus(readiness: Readiness): ServingStatus {
  if (readiness === "blocked") return "unavailable";
  if (readiness === "degraded") return "partial";
  return "live";
}
interface SourcePanelProps {
  children: ReactNode;
  description: string;
  error: unknown;
  generatedAt: string | undefined;
  label: string;
  pending: boolean;
  status: ServingStatus | Readiness | undefined;
  statusLabel?: string;
  title: string;
}
function SourcePanel({
  children,
  description,
  error,
  generatedAt,
  label,
  pending,
  status,
  statusLabel,
  title,
}: SourcePanelProps) {
  const { locale } = useI18n();
  return (
    <section aria-label={label} className="health-panel">
      <header className="health-panel__header">
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        <small title={status}>
          {statusLabel ?? translate(locale, "health.servingStatus")}{" "}
          {error !== null
            ? translate(locale, "health.unavailable")
            : status === undefined
              ? translate(locale, "health.loading")
              : status === "ready" || status === "degraded" || status === "blocked"
                ? readinessLabel(status, locale)
                : servingStatusLabel(status, locale)}{" "}
          {" " + (translate(locale, "health.statusCheckedAt") + " ")}
          {generatedAt
            ? formatUsMarketTime(generatedAt, locale)
            : translate(locale, "health.noneYet")}
        </small>
      </header>
      {pending ? (
        <div className="health-source-state" role="status">
          <RefreshCw aria-hidden="true" size={17} />
          {" " + (translate(locale, "health.loadingTheReadOnlySnapshot") + " ")}
        </div>
      ) : error !== null && generatedAt === undefined ? (
        <div
          aria-label={translate(locale, "health.unavailable2", {
            label: String(label),
          })}
          className="health-source-state"
          role="alert"
        >
          <AlertTriangle aria-hidden="true" size={17} />{" "}
          {" " +
            (translate(
              locale,
              "health.thisSourceIsUnavailableOtherHealthSourcesRemain",
            ) +
              " ")}
        </div>
      ) : (
        children
      )}
    </section>
  );
}
export interface HealthPageProps {
  loadCapabilities?: CapabilityLoader;
  loadDataHealth?: DataHealthLoader;
  loadHealth?: HealthLoader;
  loadModels?: ModelLoader;
  loadM7Status?: M7AuditPanelProps["loadStatus"];
  loadUniverses?: UniverseLoader;
}
export function HealthPage({
  loadCapabilities = loadCapabilitiesFromApi,
  loadDataHealth,
  loadHealth = loadHealthFromApi,
  loadModels,
  loadM7Status,
  loadUniverses,
}: HealthPageProps) {
  const { locale } = useI18n();
  const { search } = useLocation();
  const snapshotQuery = snapshotQueryFromSearch(search);
  const [detailPage, setDetailPage] = useState(1);
  const [onlyIncidents, setOnlyIncidents] = useState(false);
  const health = useQuery({ queryFn: loadHealth, queryKey: ["health", "service"] });
  const capabilities = useQuery({
    queryFn: loadCapabilities,
    queryKey: ["health", "capabilities"],
  });
  const dataHealth = useQuery({
    queryFn: () =>
      loadDataHealth ? loadDataHealth() : loadDataHealthFromApi(snapshotQuery),
    queryKey: ["health", "data", snapshotQuery],
  });
  const models = useQuery({
    queryFn: () => (loadModels ? loadModels() : loadModelsFromApi(snapshotQuery)),
    queryKey: ["health", "models", snapshotQuery],
  });
  const universes = useQuery({
    queryFn: () =>
      loadUniverses ? loadUniverses() : loadUniversesFromApi(snapshotQuery),
    queryKey: ["health", "universes", snapshotQuery],
  });
  const queries = [health, capabilities, dataHealth, models, universes] as const;
  const readiness = useMemo(() => {
    const values: Readiness[] = [];
    if (health.data !== undefined) {
      values.push(health.data.service_status, health.data.data_status);
    }
    for (const query of [capabilities, dataHealth, models, universes] as const) {
      if (query.data !== undefined) values.push(query.data.data_status);
    }
    if (queries.some((query) => query.error !== null)) values.push("blocked");
    if (queries.some((query) => query.isPending)) values.push("degraded");
    if (values.length === 0) return "degraded";
    return worstReadiness(values);
  }, [capabilities, dataHealth, health, models, queries, universes, locale]);
  const dataItems = dataHealth.data?.data.items ?? [];
  const capabilityRows = capabilities.data?.snapshot.capabilities ?? [];
  const historicalGaps = dataItems.filter(
    (item) => item.error_class === "historical_source_gap",
  );
  const gapSymbols = new Set(
    historicalGaps.flatMap((item) =>
      item.symbol ? [item.symbol] : item.affected_symbols,
    ),
  );
  const blockedCount = dataItems.filter(
    (item) => item.readiness === "blocked" && !isRetainedHistoricalGap(item),
  ).length;
  const visibleItems = onlyIncidents
    ? dataItems.filter(
        (item) =>
          item.error_class !== null ||
          item.readiness !== "ready" ||
          item.freshness !== "current" ||
          item.completeness !== "complete",
      )
    : dataItems;
  const detailPages = Math.max(1, Math.ceil(visibleItems.length / 100));
  const currentPage = Math.min(detailPage, detailPages);
  const pageItems = visibleItems.slice((currentPage - 1) * 100, currentPage * 100);
  const quotaSource = dataItems.find((item) => item.quota_remaining != null);
  const ineligibleCount = capabilityRows.filter(
    (item) => item.training_eligibility === "ineligible",
  ).length;
  const noChampionCount =
    models.data?.data.rows.filter((row) => row.champion_version === null).length ?? 0;
  const quotaRiskCount = capabilityRows.filter(
    (item) =>
      item.quota_remaining != null &&
      item.safety_budget != null &&
      item.quota_remaining < item.safety_budget,
  ).length;
  const latestGeneratedAt = queries
    .map((query) => query.data?.generated_at)
    .filter((value): value is string => value !== undefined)
    .sort()
    .at(-1);
  return (
    <section
      aria-label={translate(locale, "health.modelsAndDataHealthWorkbench")}
      className="health-page"
    >
      <header className="health-page__header">
        <div>
          <h1>{translate(locale, "health.modelsDataHealth")}</h1>
          <p>
            {translate(locale, "health.resolveBlockersQuotaAndPointInTimeEvidence")}
          </p>
        </div>
        <div
          aria-label={translate(locale, "health.currentRuntimeStatus")}
          className={`health-readiness health-readiness--${readiness}`}
          role="status"
        >
          {readiness === "ready" ? (
            <ShieldCheck aria-hidden="true" size={18} />
          ) : (
            <AlertTriangle aria-hidden="true" size={18} />
          )}
          {queries.some((query) => query.isPending) && readiness !== "blocked"
            ? translate(locale, "health.checkingStatus")
            : translate(locale, "health.runtimeAndHistoricalEvidence", {
                readiness: String(readinessLabel(readiness, locale)),
              })}
        </div>
      </header>
      <DataStatusBanner
        lastSuccessfulAt={
          latestGeneratedAt
            ? formatUsMarketTime(latestGeneratedAt, locale)
            : translate(locale, "health.noStatusResponseYet")
        }
        timestampLabel={translate(locale, "health.statusCheckedAt2")}
        status={readinessServingStatus(readiness)}
      />
      <section
        aria-label={translate(locale, "health.runtimeRiskSummary")}
        className="health-summary-strip"
      >
        <div>
          <span>{translate(locale, "health.blockedDataSources")}</span>
          <strong>
            {dataHealth.data === undefined
              ? dataHealth.isPending
                ? translate(locale, "health.counting")
                : translate(locale, "health.unknown")
              : blockedCount}
          </strong>
        </div>
        <div>
          <span>{translate(locale, "health.snapshotSourceQuotaAtCollection")}</span>
          <strong>
            {quotaSource
              ? translate(locale, "health.remainingTotal2", {
                  quota_remaining: String(quotaSource.quota_remaining),
                  quota_total: String(
                    quotaSource.quota_total ?? translate(locale, "health.unknown"),
                  ),
                })
              : translate(locale, "health.currentQuotaUnverified")}
          </strong>
          {capabilities.data !== undefined &&
          capabilityRows.some(
            (item) => item.quota_remaining == null || item.safety_budget == null,
          ) ? (
            <span>{translate(locale, "health.quotaUnknownForSomeInterfaces")}</span>
          ) : null}
        </div>
        <div>
          <span>
            {translate(locale, "health.interfacesIneligibleForHistoricalTraining")}
          </span>
          <strong>
            {capabilities.data === undefined
              ? capabilities.isPending
                ? translate(locale, "health.counting")
                : translate(locale, "health.unknown")
              : ineligibleCount}
          </strong>
        </div>
        <div>
          <span>{translate(locale, "health.tasksWithoutAChampion")}</span>
          <strong>
            {models.data === undefined
              ? models.isPending
                ? translate(locale, "health.counting")
                : translate(locale, "health.unknown")
              : noChampionCount}
          </strong>
        </div>
      </section>

      <p>{translate(locale, "health.readyDataSourcesDoNotMeanAnExecutable")}</p>
      <p role="status">
        {" " + (translate(locale, "health.read") + " ")}
        {dataItems.length}/{dataHealth.data?.data.total ?? "—"}{" "}
        {" " + (translate(locale, "health.healthRecordsNotASymbolCount") + " ")}
        {gapSymbols.size > 0
          ? translate(
              locale,
              "health.symbolsHaveRetainedHistoricalGapsCheckIncidentsFor",
              { size: String(gapSymbols.size) },
            )
          : ""}
        {quotaSource?.last_success_at
          ? translate(locale, "health.quotaCollectedAt", {
              last_success_at: String(
                formatUsMarketTime(quotaSource.last_success_at, locale),
              ),
            })
          : ""}
      </p>

      {dataHealth.isPending ? (
        <section
          aria-label={translate(locale, "health.incidentsAndActions")}
          className="health-panel"
        >
          <div className="health-source-state" role="status">
            <RefreshCw aria-hidden="true" size={17} />
            {" " + (translate(locale, "health.loadingTheIncidentQueue") + " ")}
          </div>
        </section>
      ) : dataHealth.data === undefined ? (
        <section
          aria-label={translate(locale, "health.incidentsAndActions")}
          className="health-panel"
        >
          <div className="health-source-state" role="alert">
            <AlertTriangle aria-hidden="true" size={17} />
            {" " + (translate(locale, "health.theIncidentQueueIsUnavailable") + " ")}
          </div>
        </section>
      ) : (
        <IncidentList items={dataItems} />
      )}

      <SourcePanel
        description={translate(
          locale,
          "health.eachTaskDisclosesBaselineChallengerChampionAndCalibration",
        )}
        error={models.error}
        generatedAt={models.data?.generated_at}
        label={translate(locale, "health.modelMatrix")}
        pending={models.isPending}
        status={models.data?.serving_status}
        title={translate(locale, "health.modelsAndTaskMatrix")}
      >
        <ModelTaskMatrix rows={models.data?.data.rows ?? []} />
      </SourcePanel>

      <OptionCollectionMatrix
        asOf={snapshotQuery.as_of ?? undefined}
        snapshotId={snapshotQuery.snapshot_id ?? undefined}
      />

      <details className="health-panel">
        <summary>
          {" " + (translate(locale, "health.fullDataHealthAndLineage") + " ")}
          {dataItems.length}/{dataHealth.data?.data.total ?? "—"}{" "}
          {" " + (translate(locale, "health.records") + " ")}
        </summary>
        <SourcePanel
          description={translate(
            locale,
            "health.freshnessCompletenessRevisionsWatermarksLastSuccessAndQuotas",
          )}
          error={dataHealth.error}
          generatedAt={dataHealth.data?.generated_at}
          label={translate(locale, "health.dataHealth")}
          pending={dataHealth.isPending}
          status={dataHealth.data?.serving_status}
          title={translate(locale, "health.dataHealthAndLineage")}
        >
          <DataHealthTable
            datasetRevision={
              dataHealth.data?.resolved_snapshot.dataset_revision ??
              translate(locale, "health.unresolved")
            }
            items={pageItems}
          />
          <div className="health-pagination">
            <label>
              <input
                type="checkbox"
                checked={onlyIncidents}
                onChange={(event) => {
                  setOnlyIncidents(event.target.checked);
                  setDetailPage(1);
                }}
              />
              {" " + (translate(locale, "health.showIncidentsAndGapsOnly") + " ")}
            </label>
            <span>
              {translate(locale, "health.pageSummary", {
                count: pageItems.length,
                total: visibleItems.length,
                page: currentPage,
                pages: detailPages,
              })}
            </span>
            <button
              type="button"
              disabled={currentPage <= 1}
              onClick={() => setDetailPage(currentPage - 1)}
            >
              {" " + (translate(locale, "health.previous") + " ")}
            </button>
            <button
              type="button"
              disabled={currentPage >= detailPages}
              onClick={() => setDetailPage(currentPage + 1)}
            >
              {" " + (translate(locale, "health.next") + " ")}
            </button>
          </div>
        </SourcePanel>
      </details>

      <details className="health-panel">
        <summary>
          {" " +
            (translate(
              locale,
              "health.historicalPermissionProbesAndTrainingEligibilityNotCurrent",
            ) +
              " ")}
          {quotaRiskCount}
        </summary>
        <SourcePanel
          description={translate(
            locale,
            "health.latestCapabilityProbeVersionsAndQuotasAreHistorical",
          )}
          error={capabilities.error}
          generatedAt={capabilities.data?.generated_at}
          label={translate(locale, "health.opendCapabilities")}
          pending={capabilities.isPending}
          status={capabilities.data?.data_status}
          statusLabel={translate(locale, "health.dataStatus")}
          title={translate(
            locale,
            "health.opendPermissionsQuotasAndPointInTimeEligibility",
          )}
        >
          <div className="health-capability-meta">
            <Database aria-hidden="true" size={17} />
            <span>
              OpenD{" "}
              {capabilities.data?.snapshot.opend_version ??
                translate(locale, "health.unknown")}
            </span>
            <span>
              SDK{" "}
              {capabilities.data?.snapshot.sdk_version ??
                translate(locale, "health.unknown")}
            </span>
            <span>
              {" " + (translate(locale, "health.compatibility") + " ")}
              {capabilities.data?.snapshot.compatibility_status ?? "unknown"}
            </span>
            <span>
              {translate(locale, "health.probedAt") + " "}
              {capabilities.data?.snapshot.probed_at ??
                translate(locale, "health.noneYet")}
            </span>
          </div>
          <p>
            {" " +
              (translate(
                locale,
                "health.historicalProbeSnapshotCompareQuotasWithSourceAnd",
              ) +
                " ")}
          </p>
          <CapabilityMatrix capabilities={capabilityRows} />
        </SourcePanel>
      </details>

      <SourcePanel
        description={translate(
          locale,
          "health.universeVersionsAndEffectiveDatesCannotBeSilently",
        )}
        error={universes.error}
        generatedAt={universes.data?.generated_at}
        label={translate(locale, "health.universeVersion")}
        pending={universes.isPending}
        status={universes.data?.serving_status}
        title={translate(locale, "health.universeVersionHistory")}
      >
        <UniverseVersionTable versions={universes.data?.data.versions ?? []} />
      </SourcePanel>
      <details className="health-panel">
        <summary>{translate(locale, "health.eodUpdateTasksAndActions")}</summary>
        <EodUpdatePanel />
      </details>
      <details className="health-panel">
        <summary>{translate(locale, "health.m7ModelGovernanceDetails")}</summary>
        <M7AuditPanel
          {...(loadM7Status === undefined ? {} : { loadStatus: loadM7Status })}
        />
      </details>
    </section>
  );
}
