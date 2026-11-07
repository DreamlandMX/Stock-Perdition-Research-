import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleSlash2, RefreshCw, TriangleAlert } from "lucide-react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { createResearchApiClient, ResearchApiError } from "../../api/client";
import type { components, paths } from "../../api/generated/schema";
import {
  decodeResearchUrlState,
  encodeResearchUrlState,
  researchStateForSymbol,
  type ResearchUrlState,
} from "../../app/url-state";
import { RouteErrorState } from "../../app/RouteErrorState";
import { formatUsMarketTime, reasonLabel } from "../../app/presentation";
import { ConfidenceLabel } from "../../components/ConfidenceLabel";
import { DataStatusBanner } from "../../components/DataStatusBanner";
import { M7LifecyclePanel, type M7LifecyclePanelProps } from "../m7/M7LifecyclePanel";
import { regimeLabel } from "../../app/presentation";
import { AbstainPanel } from "./AbstainPanel";
import { FeatureEvidence } from "./FeatureEvidence";
import { HorizonEvidence } from "./HorizonEvidence";
import { PriceHistoryChart } from "./PriceHistoryChart";
import { InstrumentIdentity } from "./InstrumentIdentity";
import { OptionsPanel } from "./OptionsPanel";
import { ResearchOptionBadge } from "./ResearchOptionBadge";
import { SimilarCasesTable } from "./SimilarCasesTable";
import { VerifiedResearchEvidence } from "./VerifiedResearchEvidence";
import "./research.css";
type ForecastResponse =
  components["schemas"]["ResearchResponse_Annotated_Union_ForecastAvailable__ForecastUnavailable___FieldInfo_annotation_NoneType__required_True__discriminator__availability____"];
type HistoryResponse = components["schemas"]["ResearchResponse_InstrumentHistory_"];
type ForecastQuery = NonNullable<
  paths["/api/v1/instruments/{symbol}/forecast"]["get"]["parameters"]["query"]
>;
type HistoryQuery = NonNullable<
  paths["/api/v1/instruments/{symbol}/history"]["get"]["parameters"]["query"]
>;
export interface ResearchPageProps {
  loadForecast?: (symbol: string, query: ForecastQuery) => Promise<ForecastResponse>;
  loadHistory?: (symbol: string, query: HistoryQuery) => Promise<HistoryResponse>;
  loadM7Status?: M7LifecyclePanelProps["loadStatus"];
}
const apiClient = createResearchApiClient();
async function loadForecastFromApi(symbol: string, query: ForecastQuery) {
  const { data } = await apiClient.get("/api/v1/instruments/{symbol}/forecast", {
    params: { path: { symbol }, query },
  });
  if (data === undefined) {
    throw new Error("forecast response unavailable");
  }
  return data;
}
async function loadHistoryFromApi(symbol: string, query: HistoryQuery) {
  const { data } = await apiClient.get("/api/v1/instruments/{symbol}/history", {
    params: { path: { symbol }, query },
  });
  if (data === undefined) {
    throw new Error("history response unavailable");
  }
  return data;
}
function forecastRequest(
  state: ReturnType<typeof decodeResearchUrlState>,
): ForecastQuery {
  return {
    ...(state.asOf == null ? {} : { as_of: state.asOf }),
    ...(state.snapshotId == null ? {} : { snapshot_id: state.snapshotId }),
    ...(state.versions.datasetRevision == null
      ? {}
      : { dataset_revision: state.versions.datasetRevision }),
    ...(state.versions.featureSetVersion == null
      ? {}
      : { feature_set_version: state.versions.featureSetVersion }),
    ...(state.versions.universeVersion == null
      ? {}
      : { universe_version: state.versions.universeVersion }),
    ...(state.versions.modelVersion == null
      ? {}
      : { model_version: state.versions.modelVersion }),
    ...(state.versions.calibrationVersion == null
      ? {}
      : { calibration_version: state.versions.calibrationVersion }),
    horizon: state.horizon,
    include: "evidence,similar_cases",
  };
}
function historyRequest(
  state: ReturnType<typeof decodeResearchUrlState>,
): HistoryQuery {
  return {
    ...(state.asOf == null ? {} : { as_of: state.asOf }),
    ...(state.snapshotId == null ? {} : { snapshot_id: state.snapshotId }),
    ...(state.versions.datasetRevision == null
      ? {}
      : { dataset_revision: state.versions.datasetRevision }),
    ...(state.versions.featureSetVersion == null
      ? {}
      : { feature_set_version: state.versions.featureSetVersion }),
    ...(state.versions.universeVersion == null
      ? {}
      : { universe_version: state.versions.universeVersion }),
    ...(state.versions.modelVersion == null
      ? {}
      : { model_version: state.versions.modelVersion }),
    ...(state.versions.calibrationVersion == null
      ? {}
      : { calibration_version: state.versions.calibrationVersion }),
    ...(state.chart.from == null ? {} : { from: state.chart.from }),
    ...(state.chart.to == null ? {} : { to: state.chart.to }),
    adjustment: state.chart.adjustment,
    timeframe: state.chart.timeframe,
  };
}
function withResolvedSnapshot(
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
      datasetRevision: snapshot.dataset_revision ?? undefined,
      featureSetVersion: snapshot.feature_set_version ?? undefined,
      modelVersion: snapshot.model_version ?? undefined,
      universeVersion: snapshot.universe_version ?? undefined,
    },
  };
}
function sameQueryKey(left: readonly unknown[], right: readonly unknown[]) {
  return JSON.stringify(left) === JSON.stringify(right);
}
function sameResolvedSnapshot(
  left: components["schemas"]["ResolvedSnapshot"],
  right: components["schemas"]["ResolvedSnapshot"],
) {
  return (
    left.as_of === right.as_of &&
    left.snapshot_id === right.snapshot_id &&
    left.revision_no === right.revision_no &&
    left.dataset_revision === right.dataset_revision &&
    left.feature_set_version === right.feature_set_version &&
    left.universe_version === right.universe_version &&
    left.model_version === right.model_version &&
    left.calibration_version === right.calibration_version
  );
}
interface HistoryWorkspaceProps {
  onRetry?: (() => void) | undefined;
  history: HistoryResponse | undefined;
  isPending: boolean;
  symbol: string;
  waitingForPinnedSnapshot: boolean;
}
function HistoryWorkspace({
  onRetry,
  history,
  isPending,
  symbol,
  waitingForPinnedSnapshot,
}: HistoryWorkspaceProps) {
  const { locale } = useI18n();
  const restoreFocus = useRef(false);
  useEffect(() => {
    if (history === undefined || !restoreFocus.current) return;
    restoreFocus.current = false;
    if (document.activeElement === document.body) {
      document
        .querySelector<HTMLSelectElement>("select[data-price-history-range]")
        ?.focus();
    }
  }, [history]);
  if (waitingForPinnedSnapshot) {
    return (
      <div
        aria-label={translate(
          locale,
          "research.researchpage.waitingForPinnedSnapshotPriceHistory",
        )}
        className="research-inline-status research-history-loading"
        role="status"
      >
        <RefreshCw aria-hidden="true" size={18} strokeWidth={1.5} />
        {translate(
          locale,
          "research.researchpage.waitingForPriceHistoryFromTheForecastSPinned",
        )}
      </div>
    );
  }
  if (isPending) {
    return (
      <div
        aria-label={translate(locale, "research.researchpage.loadingPriceHistory")}
        className="research-inline-status research-history-loading"
        role="status"
      >
        <RefreshCw aria-hidden="true" size={18} strokeWidth={1.5} />
        {translate(locale, "research.researchpage.loadingPriceHistory2")}
      </div>
    );
  }
  if (history === undefined) {
    return (
      <div
        className="research-inline-status research-inline-status--error"
        role="alert"
      >
        <TriangleAlert aria-hidden="true" size={18} strokeWidth={1.5} />
        {translate(
          locale,
          "research.researchpage.priceHistoryIsUnavailableForecastEvidenceIsStillShown",
        )}
        {onRetry ? (
          <button
            type="button"
            onClick={(event) => {
              restoreFocus.current = document.activeElement === event.currentTarget;
              onRetry();
            }}
          >
            {translate(locale, "research.researchpage.retryPriceHistory")}
          </button>
        ) : null}
      </div>
    );
  }
  return <PriceHistoryChart history={history.data} symbol={symbol} />;
}
interface UnavailableForecastProps {
  data: components["schemas"]["ForecastUnavailable"];
}
function UnavailableForecast({ data }: UnavailableForecastProps) {
  const { locale } = useI18n();
  const staleSnapshot =
    data.serving_status === "stale" ? data.last_available_snapshot : null;
  const hasResearch = (data.verified_research ?? []).length > 0;
  return (
    <section
      aria-label={translate(
        locale,
        "research.researchpage.forecastUnavailableEvidence",
      )}
      className={`gate-diagnostics gate-diagnostics--${data.serving_status}`}
    >
      <details>
        <summary>
          <span>
            <CircleSlash2 aria-hidden="true" size={17} strokeWidth={1.5} />
            {translate(locale, "research.researchpage.modelGateDiagnostics")}
          </span>
          <span title={data.not_generated_reason}>
            {reasonLabel(data.not_generated_reason, locale)}
          </span>
        </summary>
        <div className="gate-diagnostics__body">
          <h2>
            {staleSnapshot === null
              ? translate(locale, "research.researchpage.forecastNotGenerated")
              : translate(
                  locale,
                  "research.researchpage.showingTheLastAvailableSnapshotOnly",
                )}
          </h2>
          <p>
            {translate(
              locale,
              "research.researchpage.noProbabilitiesOrReturnDistributionsWereGeneratedForThis",
            )}{" "}
            <span title={data.not_generated_reason}>
              {reasonLabel(data.not_generated_reason, locale)}
            </span>
            {hasResearch
              ? translate(
                  locale,
                  "research.researchpage.researchEvidenceAboveRemainsAvailableButIsNotExecutable",
                )
              : "。"}
          </p>
          {data.affected_inputs.length === 0 ? null : (
            <ul
              aria-label={translate(locale, "research.researchpage.affectedInputs")}
              className="gate-notice__inputs"
            >
              {data.affected_inputs.map((input) => (
                <li key={input}>
                  <code>{input}</code>
                </li>
              ))}
            </ul>
          )}
          {staleSnapshot === null ? null : (
            <dl className="gate-notice__snapshot">
              <div>
                <dt>{translate(locale, "research.researchpage.lastSnapshot")}</dt>
                <dd>{staleSnapshot.snapshot_id}</dd>
              </div>
              <div>
                <dt>{translate(locale, "research.researchpage.lastValidTime")}</dt>
                <dd>
                  <time dateTime={staleSnapshot.as_of}>{staleSnapshot.as_of}</time>
                </dd>
              </div>
              <div>
                <dt>{translate(locale, "research.researchpage.modelVersion")}</dt>
                <dd>{staleSnapshot.model_version}</dd>
              </div>
            </dl>
          )}
        </div>
      </details>
    </section>
  );
}
const INDEX_SHORTCUTS = ["SPY", "DIA", "QQQ", "IWM"] as const;
function indexResearchHref(
  ticker: (typeof INDEX_SHORTCUTS)[number],
  state: ResearchUrlState,
  current: string,
) {
  const query = encodeResearchUrlState(
    researchStateForSymbol(state, current, `US.${ticker}`),
  ).toString();
  return `/research/US.${ticker}${query === "" ? "" : `?${query}`}`;
}
function ResearchQuickNavigation({
  state,
  symbol,
}: {
  state: ResearchUrlState;
  symbol: string;
}) {
  const { locale } = useI18n();
  const location = useLocation();
  const proposedReturn = (
    location.state as {
      returnTo?: unknown;
    } | null
  )?.returnTo;
  const returnTo =
    typeof proposedReturn === "string" &&
    /^\/(?:screen|overview)(?:\?|$)/.test(proposedReturn)
      ? proposedReturn
      : undefined;
  const [recentSymbols, setRecentSymbols] = useState<string[]>([]);
  useEffect(() => {
    try {
      const stored: unknown = JSON.parse(
        window.sessionStorage.getItem("prediction-stock:recent-symbols") ?? "[]",
      );
      const previous = Array.isArray(stored)
        ? stored.filter(
            (item): item is string =>
              typeof item === "string" &&
              /^US\.[A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)?$/.test(item),
          )
        : [];
      const recent = [symbol, ...previous.filter((item) => item !== symbol)].slice(
        0,
        6,
      );
      setRecentSymbols(recent);
      window.sessionStorage.setItem(
        "prediction-stock:recent-symbols",
        JSON.stringify(recent),
      );
    } catch {
      setRecentSymbols([symbol]);
    }
  }, [symbol]);
  return (
    <nav
      aria-label={translate(
        locale,
        "research.researchpage.researchQuickNavigationAndReturn",
      )}
      className="research-quick-nav"
    >
      {returnTo ? (
        <Link to={returnTo}>
          {returnTo.startsWith("/screen")
            ? translate(locale, "research.researchpage.returnToScreenerResults")
            : translate(locale, "research.researchpage.returnToMarketMap")}
        </Link>
      ) : null}
      {recentSymbols.length > 1 ? (
        <>
          <span>{translate(locale, "research.researchpage.recentInstruments")}</span>
          {recentSymbols.map((target) => (
            <Link
              key={target}
              aria-current={symbol === target ? "page" : undefined}
              state={returnTo ? { returnTo } : undefined}
              to={`/research/${encodeURIComponent(target)}?${encodeResearchUrlState(researchStateForSymbol(state, symbol, target)).toString()}`}
            >
              {target.replace(/^US\./, "")}
            </Link>
          ))}{" "}
        </>
      ) : null}
      <span>{translate(locale, "research.researchpage.indexShortcuts")}</span>
      {INDEX_SHORTCUTS.map((ticker) => {
        const target = `US.${ticker}`;
        return (
          <Link
            aria-current={symbol === target ? "page" : undefined}
            key={ticker}
            to={indexResearchHref(ticker, state, symbol)}
            state={returnTo ? { returnTo } : undefined}
          >
            {ticker}
          </Link>
        );
      })}
    </nav>
  );
}
const AGGREGATE_STATUS_LABELS = (locale: Locale = "zh-CN") =>
  ({
    delayed: translate(locale, "research.researchpage.delayed"),
    live: translate(locale, "research.researchpage.available"),
    offline: translate(locale, "research.researchpage.offline"),
    partial: translate(locale, "research.researchpage.partiallyAvailable"),
    stale: translate(locale, "research.researchpage.stale"),
    unavailable: translate(locale, "research.optionforecastpanel.unavailable"),
  }) satisfies Record<components["schemas"]["ServingStatus"], string>;
function ResearchStatusStrip({
  data,
  history,
  historyPending,
}: {
  data: components["schemas"]["ForecastUnavailable"];
  history: HistoryResponse | undefined;
  historyPending: boolean;
}) {
  const { locale } = useI18n();
  const lastBar = history?.data.bars.at(-1);
  const barCount = history?.data.bars.length ?? 0;
  const marketLabel = historyPending
    ? translate(locale, "research.researchpage.loadingMarketHistory")
    : lastBar === undefined
      ? translate(locale, "research.researchpage.noMarketHistory")
      : translate(locale, "research.researchpage.historyCoversThrough", {
          translate:
            history?.data.timeframe === "1d"
              ? translate(locale, "research.pricehistorychart.tradingDay", {
                  at: lastBar.at.slice(0, 10),
                })
              : formatUsMarketTime(lastBar.at, locale),
        });
  const aggregateLabel =
    historyPending || history === undefined
      ? translate(locale, "research.researchpage.pendingConfirmation")
      : AGGREGATE_STATUS_LABELS(locale)[history.serving_status];
  return (
    <div
      aria-label={translate(
        locale,
        "research.researchpage.researchAndFormalModelStatus",
      )}
      className="research-status-strip"
      data-aggregate-status={history?.serving_status ?? "unavailable"}
      data-formal-status={data.serving_status}
      data-market-status={
        historyPending
          ? "loading"
          : lastBar === undefined
            ? "no-history"
            : "history-present"
      }
      role="status"
    >
      <div data-testid="market-history-coverage">
        <span>{translate(locale, "research.researchpage.marketHistory")}</span>
        <strong title={lastBar?.at}>{marketLabel}</strong>
        <span>
          {translate(locale, "research.researchpage.bars.withValues", {
            barCount: barCount,
          })}
        </span>
      </div>
      <div data-testid="aggregate-platform-status">
        <span>
          {translate(locale, "research.researchpage.aggregatePlatformStatus")}
        </span>
        <strong>{aggregateLabel}</strong>
      </div>
      <div>
        <span>{translate(locale, "research.researchpage.verifiedResearch")}</span>
        <strong>
          {(data.verified_research ?? []).length > 0
            ? translate(locale, "research.researchpage.available")
            : translate(locale, "research.researchpage.unpublished")}
        </strong>
      </div>
      <div>
        <span>{translate(locale, "research.researchpage.formalModel")}</span>
        <strong>
          {translate(locale, "research.researchpage.notReadyNotExecutable")}
        </strong>
      </div>
    </div>
  );
}
export function ResearchPage({
  loadForecast = loadForecastFromApi,
  loadHistory = loadHistoryFromApi,
  loadM7Status,
}: ResearchPageProps) {
  const { locale } = useI18n();
  const location = useLocation();
  const params = useParams<{
    symbol: string;
  }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const symbol = (params.symbol ?? "").trim().toUpperCase();
  const state = useMemo(() => decodeResearchUrlState(searchParams), [searchParams]);
  const canonicalSearch = useMemo(
    () => encodeResearchUrlState(state).toString(),
    [state],
  );
  const forecastQuery = useMemo(() => forecastRequest(state), [state]);
  const historyQuery = useMemo(() => historyRequest(state), [state]);
  const forecast = useQuery({
    queryFn: () => loadForecast(symbol, forecastQuery),
    queryKey: ["instrument-forecast", symbol, forecastQuery],
  });
  const history = useQuery({
    queryFn: () => loadHistory(symbol, historyQuery),
    queryKey: ["instrument-history", symbol, historyQuery],
  });
  const forecastSnapshot = forecast.data?.resolved_snapshot;
  const historySnapshotAligned =
    forecastSnapshot === undefined ||
    history.data === undefined ||
    sameResolvedSnapshot(forecastSnapshot, history.data.resolved_snapshot);
  const alignedHistory = historySnapshotAligned ? history.data : undefined;
  const retryHistory =
    history.isError &&
    !(history.error instanceof ResearchApiError && !history.error.retryable)
      ? () => {
          void history.refetch();
        }
      : undefined;
  const historyRequestPinsForecastSnapshot =
    forecastSnapshot !== undefined &&
    state.asOf === forecastSnapshot.as_of &&
    state.revisionNo === forecastSnapshot.revision_no &&
    state.snapshotId === forecastSnapshot.snapshot_id &&
    (state.versions.calibrationVersion ?? null) ===
      forecastSnapshot.calibration_version &&
    (state.versions.datasetRevision ?? null) === forecastSnapshot.dataset_revision &&
    (state.versions.featureSetVersion ?? null) ===
      forecastSnapshot.feature_set_version &&
    (state.versions.modelVersion ?? null) === forecastSnapshot.model_version &&
    (state.versions.universeVersion ?? null) === forecastSnapshot.universe_version;
  const waitingForPinnedHistory =
    forecastSnapshot !== undefined &&
    (!historySnapshotAligned ||
      (history.isPending && historyRequestPinsForecastSnapshot));
  useEffect(() => {
    if (searchParams.toString() !== canonicalSearch) {
      setSearchParams(
        (current) => encodeResearchUrlState(decodeResearchUrlState(current)),
        {
          replace: true,
          preventScrollReset: true,
          state: location.state as unknown,
        },
      );
    }
  }, [canonicalSearch, searchParams, setSearchParams, location.state]);
  useEffect(() => {
    if (forecast.data === undefined || history.isPending) {
      return;
    }
    const nextState = withResolvedSnapshot(state, forecast.data.resolved_snapshot);
    const nextForecastRequest = forecastRequest(nextState);
    const nextHistoryRequest = historyRequest(nextState);
    const currentForecastKey = ["instrument-forecast", symbol, forecastQuery] as const;
    const nextForecastKey = [
      "instrument-forecast",
      symbol,
      nextForecastRequest,
    ] as const;
    const currentHistoryKey = ["instrument-history", symbol, historyQuery] as const;
    const nextHistoryKey = ["instrument-history", symbol, nextHistoryRequest] as const;
    if (
      sameQueryKey(currentForecastKey, nextForecastKey) &&
      sameQueryKey(currentHistoryKey, nextHistoryKey)
    ) {
      return;
    }
    queryClient.setQueryData(nextForecastKey, forecast.data);
    if (
      history.data !== undefined &&
      sameResolvedSnapshot(
        forecast.data.resolved_snapshot,
        history.data.resolved_snapshot,
      )
    ) {
      queryClient.setQueryData(nextHistoryKey, history.data);
    }
    const resolved = forecast.data.resolved_snapshot;
    setSearchParams(
      (current) =>
        encodeResearchUrlState(
          withResolvedSnapshot(decodeResearchUrlState(current), resolved),
        ),
      {
        replace: true,
        preventScrollReset: true,
        state: location.state as unknown,
      },
    );
  }, [
    forecast.data,
    forecastQuery,
    history.data,
    history.isPending,
    historyQuery,
    queryClient,
    setSearchParams,
    state,
    symbol,
    location.state,
  ]);
  if (forecast.isPending) {
    return (
      <section
        aria-label={translate(
          locale,
          "research.researchpage.loadingInstrumentResearch",
        )}
        className="research-loading"
        role="status"
      >
        <h1>
          {translate(locale, "research.researchpage.instrumentResearch.withValues", {
            symbol: symbol,
          })}
        </h1>
        <RefreshCw aria-hidden="true" size={20} strokeWidth={1.5} />
        {translate(locale, "research.researchpage.resolvingForecastsAndEvidence")}
      </section>
    );
  }
  if (forecast.isError) {
    return (
      <section className="research-page research-page--error">
        <h1>
          {translate(locale, "research.researchpage.instrumentResearch.withValues", {
            symbol: symbol,
          })}
        </h1>
        {alignedHistory === undefined ? null : (
          <InstrumentIdentity
            symbol={symbol}
            snapshot={alignedHistory.resolved_snapshot}
          />
        )}
        <RouteErrorState
          error={forecast.error}
          headingLevel={2}
          onRetry={() => void forecast.refetch()}
        />
        <p>
          {translate(
            locale,
            "research.researchpage.forecastEvidenceCouldNotBeLoadedFormalExecutabilityIs",
          )}
        </p>
        {alignedHistory === undefined ? null : (
          <p>
            {translate(locale, "research.researchpage.marketDataSnapshot.withValues", {
              snapshotId: alignedHistory.resolved_snapshot.snapshot_id,
              asOf: formatUsMarketTime(alignedHistory.resolved_snapshot.as_of, locale),
            })}
          </p>
        )}
        <HistoryWorkspace
          history={alignedHistory}
          onRetry={retryHistory}
          isPending={history.isPending}
          symbol={symbol}
          waitingForPinnedSnapshot={waitingForPinnedHistory}
        />
      </section>
    );
  }
  const response = forecast.data;
  const data = response.data;
  if (data.availability === "unavailable") {
    return (
      <section
        className="research-page research-page--unavailable"
        data-serving-status={response.serving_status}
      >
        <header className="research-masthead">
          <div className="research-masthead__identity">
            <span className="research-masthead__eyebrow">
              {translate(locale, "research.researchpage.instrumentResearch")}
            </span>
            <h1 className="research-masthead__title">
              <span className="research-masthead__symbol">{symbol}</span>{" "}
              <span className="research-masthead__suffix">
                {translate(locale, "research.researchpage.instrumentResearch")}
              </span>
            </h1>
          </div>
          <div className="research-masthead__meta">
            <span className="research-masthead__status">
              {(data.verified_research ?? []).length > 0
                ? translate(
                    locale,
                    "research.researchpage.researchAvailableFormalExecutionUnavailable",
                  )
                : translate(locale, "research.researchpage.formalForecastUnavailable")}
            </span>
          </div>
        </header>
        <InstrumentIdentity symbol={symbol} snapshot={response.resolved_snapshot} />
        <ResearchQuickNavigation state={state} symbol={symbol} />
        <ResearchOptionBadge symbol={symbol} />
        <div className="research-unavailable-grid">
          <HistoryWorkspace
            history={alignedHistory}
            onRetry={retryHistory}
            isPending={history.isPending}
            symbol={symbol}
            waitingForPinnedSnapshot={waitingForPinnedHistory}
          />
          <ResearchStatusStrip
            data={data}
            history={alignedHistory}
            historyPending={history.isPending || waitingForPinnedHistory}
          />
          <VerifiedResearchEvidence
            entries={data.verified_research ?? []}
            notGeneratedReason={data.not_generated_reason}
            regime={alignedHistory?.data.bars.at(-1)?.regime}
          />
          <UnavailableForecast data={data} />
        </div>
        <OptionsPanel symbol={symbol} />
        <details className="research-governance">
          <summary>
            {translate(
              locale,
              "research.researchpage.modelOperationsAndGovernanceDetails",
            )}
          </summary>
          <M7LifecyclePanel
            {...(loadM7Status === undefined ? {} : { loadStatus: loadM7Status })}
            symbol={symbol}
          />
        </details>
      </section>
    );
  }
  const evidence = data.evidence ?? data.bundle.evidence;
  const horizons = [1, 5, 20] as const;
  return (
    <section
      className="research-page"
      data-effective-abstain={data.serving.effective_abstain}
      data-serving-status={data.serving.serving_status}
    >
      <header className="research-masthead">
        <div className="research-masthead__identity">
          <span className="research-masthead__eyebrow">
            {data.bundle.current_event_flags.length === 0
              ? translate(locale, "research.researchpage.instrumentResearch")
              : translate(locale, "research.researchpage.instrumentResearch2", {
                  currentEventFlags: data.bundle.current_event_flags.join("; "),
                })}
          </span>
          <h1 className="research-masthead__title">
            <span className="research-masthead__symbol">{symbol}</span>{" "}
            <span className="research-masthead__suffix">
              {translate(locale, "research.researchpage.instrumentResearch")}
            </span>
          </h1>
        </div>
        <div className="research-masthead__meta">
          <span>{regimeLabel(data.bundle.current_regime, locale)}</span>
          <ConfidenceLabel level={data.bundle.confidence_level} />
          <time dateTime={data.bundle.as_of} title={data.bundle.as_of}>
            {formatUsMarketTime(data.bundle.as_of, locale)}
          </time>
        </div>
      </header>
      <InstrumentIdentity symbol={symbol} snapshot={response.resolved_snapshot} />
      <ResearchQuickNavigation state={state} symbol={symbol} />
      <ResearchOptionBadge symbol={symbol} />
      <DataStatusBanner
        lastSuccessfulAt={response.resolved_snapshot.as_of}
        status={data.serving.serving_status}
      />

      <div
        className="research-workbench-grid research-workbench-grid--responsive"
        data-layout="main-evidence-with-right-diagnostics"
        data-testid="research-workbench-layout"
      >
        <div className="research-workbench-grid__risk" data-mobile-priority="key-risk">
          <AbstainPanel
            bundle={data.bundle}
            evidence={evidence}
            serving={data.serving}
          />
        </div>

        <div className="research-workbench-grid__main">
          <HistoryWorkspace
            history={alignedHistory}
            onRetry={retryHistory}
            isPending={history.isPending}
            symbol={symbol}
            waitingForPinnedSnapshot={waitingForPinnedHistory}
          />
          <section
            aria-labelledby="return-evidence-heading"
            className="return-evidence"
          >
            <header className="return-evidence__header">
              <div>
                <span>
                  {translate(
                    locale,
                    "research.researchpage.forecastWindowsAreNotConnectedAndDoNotImply",
                  )}
                </span>
                <h2 id="return-evidence-heading">
                  {translate(
                    locale,
                    "research.researchpage.rawQuantilesAndCqrCalibratedIntervals",
                  )}
                </h2>
                <p>
                  {translate(
                    locale,
                    "research.researchpage.returnsDescribeEndOfWindowScenariosBreakoutsDescribeWithin",
                  )}
                </p>
              </div>
            </header>
            <div className="horizon-stack">
              {horizons.map((horizon) => {
                const item = data.bundle.horizons.find(
                  (candidate) => candidate.horizon === horizon,
                );
                return item === undefined ? (
                  <section
                    aria-label={translate(
                      locale,
                      "research.horizonevidence.forecastOverTradingDays",
                      { horizon: horizon },
                    )}
                    className="horizon-evidence horizon-evidence--unavailable"
                    key={horizon}
                  >
                    <h2>
                      {translate(
                        locale,
                        "research.horizonevidence.tradingDays.withValues",
                        { horizon: horizon },
                      )}
                    </h2>
                    <p>
                      {translate(
                        locale,
                        "research.researchpage.noForecastEvidenceWasReturnedForThisDiscreteWindow",
                      )}
                    </p>
                  </section>
                ) : (
                  <HorizonEvidence forecast={item} key={horizon} />
                );
              })}
            </div>
          </section>
        </div>

        <aside
          aria-label={translate(
            locale,
            "research.researchpage.researchDiagnosticEvidence",
          )}
          className="research-workbench-grid__diagnostics"
        >
          <SimilarCasesTable
            cases={data.similar_cases}
            summary={evidence.similar_case_summary}
          />
          <FeatureEvidence evidence={evidence} />
        </aside>
      </div>

      <OptionsPanel symbol={symbol} />
      <details className="research-governance">
        <summary>
          {translate(
            locale,
            "research.researchpage.modelOperationsAndGovernanceDetails",
          )}
        </summary>
        <M7LifecyclePanel
          {...(loadM7Status === undefined ? {} : { loadStatus: loadM7Status })}
          symbol={symbol}
        />
      </details>
      <footer className="research-page__footer">
        <span>
          {translate(locale, "research.researchpage.model.withValues", {
            modelVersion: data.bundle.model_version,
          })}
        </span>
        <span>
          {translate(locale, "research.researchpage.data.withValues", {
            datasetRevision: data.bundle.dataset_revision,
          })}
        </span>
        <span>
          {translate(locale, "research.researchpage.features.withValues", {
            featureSetVersion: data.bundle.feature_set_version,
          })}
        </span>
        <span>
          {translate(locale, "research.researchpage.calibration.withValues", {
            calibrationVersion: data.bundle.calibration_version,
          })}
        </span>
        <span>
          {translate(locale, "research.optionspanel.snapshot.withValues", {
            snapshotId: data.bundle.snapshot_id,
          })}
        </span>
      </footer>
    </section>
  );
}
