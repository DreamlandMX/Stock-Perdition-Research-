import { translate } from "../i18n/core";
import { useI18n } from "../i18n/context";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { matchPath, Outlet, useLocation, useNavigate } from "react-router-dom";
import { createResearchApiClient } from "../api/client";
import { snapshotContextOptions } from "../api/snapshot-context";
import { reportApiConnectivity, useConnectivity } from "../app/connectivity";
import { appQueryClient } from "../app/query-client";
import type { components } from "../api/generated/schema";
import {
  EOD_REFRESH_QUERY_ROOTS,
  EOD_UPDATE_SETTLED_EVENT,
  readManualEodUpdate,
  withoutSnapshotIdentity,
  snapshotQueryFromSearch,
} from "../app/snapshot-refresh";
import { decodeResearchUrlState, urlStateWarningLabel } from "../app/url-state";
import { formatUsMarketTime } from "../app/presentation";
import { DataStatusBanner } from "../components/DataStatusBanner";
import { EodUpdateResult } from "../features/operations/EodUpdateResult";
import { MobileNav } from "./MobileNav";
import { Sidebar, TopNavigation } from "./Sidebar";
import { TopBar } from "./TopBar";
type ViewportMode = "sidebar" | "rail" | "bottom";
export interface AppShellProps {
  dataAsOf?: string;
  dataStatus?: components["schemas"]["ServingStatus"];
  opendStatus?: components["schemas"]["Connectivity"];
  statusLoader?: AppShellStatusLoader;
}
export interface AppShellStatus {
  dataAsOf: string;
  dataStatus: components["schemas"]["ServingStatus"];
  opendStatus: components["schemas"]["Connectivity"];
  snapshot?: components["schemas"]["ResolvedSnapshot"];
  symbolCount?: number;
}
export type AppShellStatusLoader = () => Promise<AppShellStatus>;
const apiClient = createResearchApiClient();
function statusFromHealth(
  health: components["schemas"]["HealthResponse"] | undefined,
  marketData: components["schemas"]["ComponentHealth"] | undefined,
): components["schemas"]["ServingStatus"] {
  if (health?.data_status === "blocked") return "unavailable";
  if (marketData?.completeness === "partial" || health?.data_status === "degraded") {
    return "partial";
  }
  if (marketData?.freshness === "stale") return "stale";
  if (marketData?.freshness === "delayed") return "delayed";
  return health?.data_status === "ready" ? "live" : "unavailable";
}
function currentViewportMode(): ViewportMode {
  if (window.innerWidth < 768) {
    return "bottom";
  }
  if (window.innerWidth < 1024) {
    return "rail";
  }
  return "sidebar";
}
export function AppShell({
  dataAsOf,
  dataStatus,
  opendStatus,
  statusLoader,
}: AppShellProps) {
  const { locale } = useI18n();
  const connectivity = useConnectivity();
  const navigate = useNavigate();
  const [viewportMode, setViewportMode] = useState(currentViewportMode);
  const [statusResolved, setStatusResolved] = useState(false);
  const [statusFailed, setStatusFailed] = useState(false);
  const [runtimeStatus, setRuntimeStatus] = useState<AppShellStatus>({
    dataAsOf: translate(locale, "common.shared.noSuccessfulDataYet"),
    dataStatus: "unavailable",
    opendStatus: "unknown",
  });
  const { pathname, search } = useLocation();
  const hasExplicitStatus =
    dataAsOf !== undefined && dataStatus !== undefined && opendStatus !== undefined;
  const useApiStatus = statusLoader === undefined && !hasExplicitStatus;
  const health = useQuery({
    queryKey: ["health", "shell"],
    queryFn: async () => {
      const { data } = await apiClient.get("/api/v1/health");
      if (!data) throw new Error("application shell health unavailable");
      return data;
    },
    enabled: useApiStatus,
    retry: false,
    refetchInterval: 30000,
  });
  const context = useQuery({
    ...snapshotContextOptions(snapshotQueryFromSearch(search)),
    enabled: useApiStatus,
    refetchInterval: 30000,
  });
  const latestIdentity = useQuery({
    ...snapshotContextOptions(),
    enabled: useApiStatus && context.data !== undefined,
  });
  const marketData = health.data?.components.find(
    (component) => component.component === "market_data",
  );
  const selectedSnapshot = useApiStatus
    ? context.data?.resolved_snapshot
    : runtimeStatus.snapshot;
  const symbolCount = useApiStatus
    ? context.data?.data.symbol_count
    : runtimeStatus.symbolCount;
  const isHistorical =
    latestIdentity.data !== undefined &&
    selectedSnapshot !== undefined &&
    selectedSnapshot.snapshot_id !==
      latestIdentity.data.resolved_snapshot.snapshot_id &&
    (Date.parse(selectedSnapshot.as_of) <
      Date.parse(latestIdentity.data.resolved_snapshot.as_of) ||
      (selectedSnapshot.as_of === latestIdentity.data.resolved_snapshot.as_of &&
        selectedSnapshot.revision_no <
          latestIdentity.data.resolved_snapshot.revision_no));
  const fixedSnapshot = Object.keys(snapshotQueryFromSearch(search)).length > 0;
  const urlWarnings = useMemo(() => {
    const params = new URLSearchParams(search);
    return matchPath({ path: "/screen", end: true }, pathname) !== null
      ? decodeResearchUrlState(params, { route: "screen" }).warnings
      : decodeResearchUrlState(params).warnings;
  }, [pathname, search, locale]);
  useEffect(() => {
    const updateViewportMode = () => {
      setViewportMode(currentViewportMode());
    };
    window.addEventListener("resize", updateViewportMode);
    return () => {
      window.removeEventListener("resize", updateViewportMode);
    };
  }, []);
  useEffect(() => {
    if (statusLoader === undefined) return;
    let active = true;
    const refresh = () => {
      void statusLoader()
        .then((status) => {
          if (!active) return;
          setRuntimeStatus(status);
          setStatusResolved(true);
          setStatusFailed(false);
          reportApiConnectivity("online");
        })
        .catch(() => {
          if (active) setStatusFailed(true);
        });
    };
    refresh();
    const interval = window.setInterval(refresh, 30000);
    window.addEventListener(EOD_UPDATE_SETTLED_EVENT, refresh);
    return () => {
      active = false;
      window.clearInterval(interval);
      window.removeEventListener(EOD_UPDATE_SETTLED_EVENT, refresh);
    };
  }, [dataAsOf, dataStatus, opendStatus, statusLoader, search]);
  useEffect(() => {
    const refreshManualUpdate = (event: Event) => {
      const operation = (
        event as CustomEvent<components["schemas"]["UpdateOperation"] | undefined>
      ).detail;
      const marker = readManualEodUpdate();
      const succeeded =
        operation?.status === "succeeded_data_only" ||
        operation?.status === "succeeded_forecast_ready";
      if (!succeeded || marker?.operationId !== operation?.operation_id) {
        if (useApiStatus) {
          void health.refetch();
          void context.refetch();
          if (fixedSnapshot) void latestIdentity.refetch();
        }
        return;
      }
      void appQueryClient.invalidateQueries({
        predicate: (query) => {
          const root = query.queryKey[0];
          return typeof root === "string" && EOD_REFRESH_QUERY_ROOTS.has(root);
        },
      });
      const nextSearch = withoutSnapshotIdentity(search);
      if (nextSearch !== search) {
        void navigate({ pathname, search: nextSearch }, { replace: true });
      }
    };
    window.addEventListener(EOD_UPDATE_SETTLED_EVENT, refreshManualUpdate);
    return () =>
      window.removeEventListener(EOD_UPDATE_SETTLED_EVENT, refreshManualUpdate);
  }, [
    navigate,
    pathname,
    search,
    useApiStatus,
    fixedSnapshot,
    health.refetch,
    context.refetch,
    latestIdentity.refetch,
  ]);
  const isMobile = viewportMode === "bottom";
  const statusQueryFailed =
    statusFailed || (useApiStatus && (health.isError || context.isError));
  const hasKnownStatus = useApiStatus
    ? context.data !== undefined
    : statusResolved || dataStatus !== undefined;
  const resolvedDataAsOf =
    dataAsOf ??
    (useApiStatus
      ? (context.data?.resolved_snapshot.as_of ??
        (context.isError
          ? translate(locale, "common.shared.statusQueryFailed")
          : translate(locale, "common.shared.dataDateAwaitingConfirmation")))
      : statusResolved
        ? runtimeStatus.dataAsOf
        : statusFailed
          ? translate(locale, "common.shared.statusQueryFailed")
          : translate(locale, "common.shared.checkingDataDate"));
  const resolvedDataStatus =
    dataStatus ??
    (useApiStatus
      ? context.data?.serving_status === "live" &&
        health.data?.data_status === "degraded"
        ? "partial"
        : (context.data?.serving_status ?? statusFromHealth(health.data, marketData))
      : runtimeStatus.dataStatus);
  const resolvedOpendStatus =
    opendStatus ??
    (useApiStatus
      ? (marketData?.connectivity ?? "unknown")
      : runtimeStatus.opendStatus);
  const effectiveDataStatus =
    connectivity === "offline" ? "offline" : resolvedDataStatus;
  return (
    <div className={`app-shell app-shell--${viewportMode}`}>
      <a
        className="skip-link"
        href="#main-content"
        onClick={() => document.querySelector<HTMLElement>("#main-content")?.focus()}
      >
        {" " + (translate(locale, "common.shared.skipToMainContent") + " ")}
      </a>
      {viewportMode === "rail" ? <Sidebar variant="rail" /> : null}
      <TopBar
        dataAsOf={resolvedDataAsOf}
        {...(viewportMode === "sidebar" ? { navigation: <TopNavigation /> } : {})}
        opendStatus={resolvedOpendStatus}
        showProductName={viewportMode !== "rail"}
      />
      <main
        className="app-main"
        data-connectivity={connectivity}
        data-effective-abstain={connectivity === "offline" ? "true" : undefined}
        id="main-content"
        tabIndex={-1}
      >
        {statusQueryFailed ? (
          <div
            role="status"
            aria-label={translate(locale, "common.shared.statusQueryFailed")}
          >
            {" " + (translate(locale, "common.shared.statusQueryFailed2") + " ")}
            {hasKnownStatus
              ? translate(
                  locale,
                  "common.shared.retainingTheLastStatusUntilTheNextRefresh",
                )
              : translate(locale, "common.shared.dataAvailabilityCannotYetBeConfirmed")}
          </div>
        ) : !hasKnownStatus ? (
          <div
            role="status"
            aria-label={translate(locale, "common.shared.checkingDataStatus")}
          >
            {" " + (translate(locale, "common.shared.checkingDataStatus2") + " ")}
          </div>
        ) : null}
        <div
          className="shell-context"
          data-manual={selectedSnapshot?.activation_basis === "manual_override"}
        >
          {hasKnownStatus || connectivity === "offline" ? (
            <DataStatusBanner
              lastSuccessfulAt={resolvedDataAsOf}
              status={effectiveDataStatus}
              timestampLabel={translate(locale, "common.shared.dataAsOf")}
            />
          ) : null}
          {selectedSnapshot ? (
            <section
              aria-label={translate(locale, "common.shared.selectedSnapshotIdentity")}
              className="snapshot-identity"
              data-historical={isHistorical}
            >
              <div>
                <strong>
                  {isHistorical
                    ? translate(locale, "common.shared.historicalSnapshot")
                    : fixedSnapshot
                      ? translate(locale, "common.shared.pinnedSnapshot")
                      : translate(locale, "common.shared.latestSnapshot")}{" "}
                  | {symbolCount ?? "—"}
                  {" " + (translate(locale, "common.shared.instruments") + " ")}
                </strong>
                <span>
                  {" " + (translate(locale, "common.shared.marketDataAsOf") + " ")}
                  {formatUsMarketTime(selectedSnapshot.as_of, locale)} |{" "}
                  {selectedSnapshot.prediction_readiness === "data_only"
                    ? translate(
                        locale,
                        "common.shared.marketDataAndRulesResearchModelForecastsNot",
                      )
                    : selectedSnapshot.activation_basis === "manual_override"
                      ? translate(locale, "common.shared.manuallyActivatedUnverified")
                      : translate(locale, "common.shared.includesFormalModelEvidence")}
                </span>
              </div>
              {fixedSnapshot ? (
                <button
                  type="button"
                  onClick={() =>
                    void navigate({ pathname, search: withoutSnapshotIdentity(search) })
                  }
                >
                  {" " + (translate(locale, "common.shared.viewLatestSnapshot") + " ")}
                </button>
              ) : null}
              <details>
                <summary>
                  {"" +
                    (translate(locale, "common.shared.snapshotVersionDetails") + "")}
                </summary>
                <dl>
                  <dt>{"" + (translate(locale, "common.shared.snapshot") + "")}</dt>
                  <dd>{selectedSnapshot.snapshot_id}</dd>
                  <dt>
                    {"" + (translate(locale, "common.shared.universeVersion") + "")}
                  </dt>
                  <dd>{selectedSnapshot.universe_version}</dd>
                  <dt>{"" + (translate(locale, "common.shared.revision") + "")}</dt>
                  <dd>{selectedSnapshot.revision_no}</dd>
                  <dt>
                    {"" + (translate(locale, "common.shared.originalCutoffTime") + "")}
                  </dt>
                  <dd>{selectedSnapshot.as_of}</dd>
                </dl>
                <p>
                  {" " +
                    (translate(
                      locale,
                      "common.shared.connectionStatusDescribesTheCurrentServiceMarketData",
                    ) +
                      " ")}
                </p>
              </details>
            </section>
          ) : null}
        </div>
        <EodUpdateResult />
        {urlWarnings.length === 0 ? null : (
          <div
            aria-label={translate(locale, "common.shared.urlParameterWarnings")}
            className="url-state-warning"
            role="alert"
          >
            <TriangleAlert aria-hidden="true" size={18} strokeWidth={1.5} />
            <div>
              {urlWarnings.map((warning) => (
                <p key={warning.id}>{urlStateWarningLabel(warning, locale)}</p>
              ))}
            </div>
          </div>
        )}
        <Outlet />
      </main>
      {isMobile ? <MobileNav /> : null}
    </div>
  );
}
