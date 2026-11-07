import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Funnel, RefreshCw, TriangleAlert } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { createResearchApiClient, ResearchApiError } from "../../api/client";
import type { components } from "../../api/generated/schema";
import {
  decodeResearchUrlState,
  encodeResearchUrlState,
  normalizeScreenModeState,
  SCREEN_MODE_COMPATIBILITY_MESSAGE,
  type ScreenUrlState,
} from "../../app/url-state";
import { DataStatusBanner } from "../../components/DataStatusBanner";
import { assetLabel, sectorLabel, servingStatusLabel } from "../../app/presentation";
import { regimeLabel } from "../../app/presentation";
import { EmptyState } from "../../components/EmptyState";
import {
  ResearchModeSwitch,
  type ResearchMode,
} from "../../components/ResearchModeSwitch";
import { rejectionReasonLabel } from "../../app/presentation";
import { MobileScreenList } from "./MobileScreenList";
import { ResearchScreenResults } from "./ResearchScreenResults";
import { ScreenFilters } from "./ScreenFilters";
import { ScreenTable } from "./ScreenTable";
import { useOptionsOverview } from "../options/useOptionsOverview";
import {
  decodeScreenOptions,
  encodeScreenOptions,
  optionScreenPage,
  requiresAllScreenRows,
  OPTION_COLUMNS,
  type ScreenOptionState,
} from "./option-screen-state";
import type { ScreenOptionColumnsProps } from "./ScreenOptionColumns";
import {
  buildInstrumentRequest,
  buildScreenRequest,
  resolveScreenUrlState,
  resetScreenControls,
  type InstrumentQuery,
  type ScreenQuery,
} from "./screen-url-state";
import "./screen.css";
type ScreenResponse = components["schemas"]["ResearchResponse_ScreenPage_"];
type InstrumentResponse = components["schemas"]["ResearchResponse_InstrumentPage_"];
type RejectionReason = components["schemas"]["RejectionReason"];
export type ScreenLoader = (query: ScreenQuery) => Promise<ScreenResponse>;
export type InstrumentLoader = (query: InstrumentQuery) => Promise<InstrumentResponse>;
const apiClient = createResearchApiClient();
async function loadScreenFromApi(query: ScreenQuery) {
  const { data } = await apiClient.get("/api/v1/screen", { params: { query } });
  if (data === undefined) {
    throw new Error("screen response unavailable");
  }
  return data;
}
async function loadCompleteScreen(load: ScreenLoader, query: ScreenQuery) {
  const first = await load({ ...query, page: 1, limit: 100 });
  const items = [...first.data.items];
  const pages = Math.ceil(first.data.total / first.data.page_size);
  for (let page = 2; page <= pages; page += 1) {
    const next = await load({
      ...query,
      snapshot_id: first.resolved_snapshot.snapshot_id,
      page,
      limit: 100,
    });
    if (
      next.resolved_snapshot.snapshot_id !== first.resolved_snapshot.snapshot_id ||
      next.data.total !== first.data.total ||
      next.data.page !== page
    ) {
      throw new Error(
        "Screener pagination is incomplete or the snapshot changed; options filters were not applied",
      );
    }
    items.push(...next.data.items);
  }
  if (
    items.length !== first.data.total ||
    new Set(items.map((row) => row.symbol)).size !== items.length
  )
    throw new Error(
      "Screener universe is incomplete; options filters were not applied",
    );
  return { ...first, data: { ...first.data, items } };
}
async function loadInstrumentsFromApi(query: InstrumentQuery) {
  const { data } = await apiClient.get("/api/v1/instruments", { params: { query } });
  if (data === undefined) {
    throw new Error("instrument response unavailable");
  }
  const items = [...data.data.items];
  const pages = Math.ceil(data.data.total / data.data.page_size);
  for (let page = data.data.page + 1; page <= pages; page += 1) {
    const result = await apiClient.get("/api/v1/instruments", {
      params: {
        query: { ...query, snapshot_id: data.resolved_snapshot.snapshot_id, page },
      },
    });
    if (
      result.data === undefined ||
      result.data.resolved_snapshot.snapshot_id !==
        data.resolved_snapshot.snapshot_id ||
      result.data.data.total !== data.data.total
    ) {
      throw new Error("instrument pagination unavailable or snapshot changed");
    }
    items.push(...result.data.data.items);
  }
  if (items.length !== data.data.total) throw new Error("instrument pages incomplete");
  return { ...data, data: { ...data.data, items } };
}
function sameQueryKey(left: readonly unknown[], right: readonly unknown[]) {
  return JSON.stringify(left) === JSON.stringify(right);
}
function currentIsMobile() {
  return window.innerWidth < 768;
}
function useMobileViewport() {
  const [isMobile, setIsMobile] = useState(currentIsMobile);
  useEffect(() => {
    const update = () => setIsMobile(currentIsMobile());
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return isMobile;
}
function uniqueValues(values: readonly (string | null | undefined)[]) {
  return [...new Set(values.filter((value): value is string => Boolean(value)))].sort(
    (left, right) => left.localeCompare(right),
  );
}
export interface ScreenPageProps {
  loadInstruments?: InstrumentLoader;
  loadScreen?: ScreenLoader;
}
export function ScreenPage({
  loadInstruments = loadInstrumentsFromApi,
  loadScreen = loadScreenFromApi,
}: ScreenPageProps) {
  const { locale } = useI18n();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const isMobile = useMobileViewport();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [modeCompatibilityNotice, setModeCompatibilityNotice] = useState<string | null>(
    null,
  );
  const pendingModeRef = useRef<ResearchMode | null>(null);
  const drawerButtonRef = useRef<HTMLButtonElement>(null);
  const focusAfterDrawerRef = useRef<"results" | "trigger" | null>(null);
  const urlState = useMemo(
    () => decodeResearchUrlState(searchParams, { route: "screen" }),
    [searchParams],
  );
  const optionState = useMemo(() => decodeScreenOptions(searchParams), [searchParams]);
  const allRowsRequired = requiresAllScreenRows(optionState);
  const drawerVisible = isMobile && drawerOpen;
  const screenRequest = buildScreenRequest(
    allRowsRequired ? { ...urlState, page: { number: 1, size: 100 } } : urlState,
  );
  const screenQueryKey = [
    "screen",
    screenRequest,
    ...(allRowsRequired ? ["all-rows"] : []),
    urlState.revisionNo ?? null,
  ] as const;
  const instrumentRequest = buildInstrumentRequest(urlState);
  const instrumentQueryKey = [
    "screen-instruments",
    instrumentRequest,
    urlState.revisionNo ?? null,
  ] as const;
  const screenQuery = useQuery({
    placeholderData: (previousData, previousQuery) =>
      previousQuery !== undefined &&
      sameQueryKey(previousQuery.queryKey, screenQueryKey)
        ? previousData
        : undefined,
    queryFn: () =>
      allRowsRequired
        ? loadCompleteScreen(loadScreen, screenRequest)
        : loadScreen(screenRequest),
    queryKey: screenQueryKey,
  });
  const instrumentQuery = useQuery({
    queryFn: () => loadInstruments(instrumentRequest),
    queryKey: instrumentQueryKey,
  });
  const optionQuery = useOptionsOverview(
    {
      snapshot_id:
        screenQuery.data?.resolved_snapshot.snapshot_id ?? urlState.snapshotId,
      as_of: screenQuery.data?.resolved_snapshot.as_of ?? urlState.asOf,
    },
    screenQuery.data !== undefined,
  );
  const optionSnapshotMismatch =
    optionQuery.data !== undefined &&
    screenQuery.data !== undefined &&
    optionQuery.data.resolved_snapshot_id !==
      screenQuery.data.resolved_snapshot.snapshot_id;
  const screenResponse =
    screenQuery.data === undefined
      ? undefined
      : allRowsRequired
        ? optionQuery.data === undefined ||
          optionQuery.isError ||
          optionSnapshotMismatch
          ? undefined
          : {
              ...screenQuery.data,
              data: optionScreenPage(
                screenQuery.data.data,
                optionQuery.data.items,
                optionState,
                urlState,
              ),
            }
        : screenQuery.data;
  const resultsPending =
    screenQuery.isPending ||
    (screenQuery.data !== undefined && allRowsRequired && optionQuery.isPending);
  useEffect(() => {
    if (
      urlState.warnings.some((warning) => warning.id === "url-state-mode-compatibility")
    ) {
      setModeCompatibilityNotice(SCREEN_MODE_COMPATIBILITY_MESSAGE);
    }
  }, [urlState.warnings]);
  useEffect(() => {
    if (screenQuery.data === undefined) {
      return;
    }
    if (pendingModeRef.current !== null) {
      if (urlState.mode !== pendingModeRef.current) return;
      pendingModeRef.current = null;
    }
    const nextState = resolveScreenUrlState(
      urlState,
      screenQuery.data.resolved_snapshot,
    );
    const nextRequest = buildScreenRequest(
      allRowsRequired ? { ...nextState, page: { number: 1, size: 100 } } : nextState,
    );
    const nextKey = [
      "screen",
      nextRequest,
      ...(allRowsRequired ? ["all-rows"] : []),
      nextState.revisionNo ?? null,
    ] as const;
    if (sameQueryKey(screenQueryKey, nextKey)) {
      return;
    }
    queryClient.setQueryData(nextKey, screenQuery.data);
    setSearchParams(
      encodeScreenOptions(
        encodeResearchUrlState(nextState, { route: "screen" }),
        optionState,
      ),
      {
        replace: true,
        preventScrollReset: true,
      },
    );
  }, [
    allRowsRequired,
    optionState,
    queryClient,
    screenQuery.data,
    screenQueryKey,
    setSearchParams,
    urlState,
  ]);
  useEffect(() => {
    if (!drawerOpen) {
      return;
    }
    const focusFirstControl = () => {
      document
        .querySelector<HTMLElement>(
          ".screen-filter-drawer select, .screen-filter-drawer input",
        )
        ?.focus();
    };
    focusFirstControl();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        focusAfterDrawerRef.current = "trigger";
        setDrawerOpen(false);
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [drawerOpen]);
  useEffect(() => {
    if (isMobile || !drawerOpen) {
      return;
    }
    focusAfterDrawerRef.current = "results";
    setDrawerOpen(false);
  }, [drawerOpen, isMobile]);
  useEffect(() => {
    if (drawerOpen || focusAfterDrawerRef.current === null) {
      return;
    }
    const target = focusAfterDrawerRef.current;
    focusAfterDrawerRef.current = null;
    if (target === "results") {
      document.getElementById("screen-results-heading")?.focus();
    } else {
      drawerButtonRef.current?.focus();
    }
  }, [drawerOpen]);
  function writeUrlState(
    nextState: ScreenUrlState,
    focusResults = false,
    nextOptions = optionState,
  ) {
    const resolvedState =
      nextState.snapshotId === undefined && screenQuery.data !== undefined
        ? resolveScreenUrlState(nextState, screenQuery.data.resolved_snapshot)
        : nextState;
    setSearchParams(
      encodeScreenOptions(
        encodeResearchUrlState(resolvedState, { route: "screen" }),
        nextOptions,
      ),
      {
        preventScrollReset: true,
      },
    );
    if (focusResults) {
      focusAfterDrawerRef.current = "results";
      setDrawerOpen(false);
    }
  }
  function changeResearchSort(sort: ScreenUrlState["sort"]) {
    writeUrlState({ ...urlState, page: { ...urlState.page, number: 1 }, sort }, false, {
      ...optionState,
      sort: undefined,
    });
  }
  function changeOptions(next: ScreenOptionState) {
    writeUrlState({ ...urlState, page: { ...urlState.page, number: 1 } }, false, next);
  }
  const optionColumns: ScreenOptionColumnsProps = {
    optionItems:
      optionQuery.isError || optionSnapshotMismatch
        ? []
        : (optionQuery.data?.items ?? []),
    optionState,
    onOptionSort: (sort, direction) =>
      changeOptions({ ...optionState, sort, direction }),
  };
  const instruments = instrumentQuery.data?.data.items ?? [];
  const sectors = uniqueValues(instruments.map((instrument) => instrument.sector));
  const assetTypes = uniqueValues(
    instruments.map((instrument) => instrument.asset_type),
  );
  return (
    <section
      aria-label={translate(locale, "screen.screenpage.researchScreener")}
      className="screen-page"
      inert={drawerVisible ? true : undefined}
    >
      <header className="screen-page__header">
        <div>
          <h1>{translate(locale, "screen.screenpage.researchScreener")}</h1>
          <p>
            {translate(
              locale,
              "screen.screenpage.theResearchSnapshotServiceRunsFiltersSortingAndPagination",
            )}
          </p>
        </div>
        {isMobile ? (
          <button
            aria-expanded={drawerVisible}
            aria-label={translate(locale, "screen.screenpage.openFilters")}
            className="screen-filter-trigger"
            onClick={() => setDrawerOpen(true)}
            ref={drawerButtonRef}
            type="button"
          >
            <Funnel aria-hidden="true" size={18} strokeWidth={1.5} />
            {translate(locale, "screen.screenpage.filter")}
          </button>
        ) : null}
      </header>

      {isMobile ? null : (
        <ScreenFilters
          assetTypes={assetTypes}
          mode="desktop"
          onApply={writeUrlState}
          sectors={sectors}
          state={urlState}
          optionState={optionState}
          onOptionChange={changeOptions}
          onClearAll={(next) =>
            writeUrlState(next, false, decodeScreenOptions(new URLSearchParams()))
          }
        />
      )}
      {instrumentQuery.isError ? (
        <div
          aria-label={translate(locale, "screen.screenpage.filterMetadataUnavailable")}
          className="screen-metadata-status"
          role="status"
        >
          {translate(
            locale,
            "screen.screenpage.filterMetadataIsUnavailableResultsRemainUsableSelectedUrl",
          )}
        </div>
      ) : null}
      {modeCompatibilityNotice === null ? null : (
        <div
          aria-label={translate(locale, "screen.screenpage.modeCompatibilityNotice")}
          className="screen-mode-compatibility"
          role="status"
        >
          {translate(locale, "common.url.modeCompatibility")}
        </div>
      )}
      {drawerVisible ? (
        <ScreenFilters
          assetTypes={assetTypes}
          mode="mobile"
          onApply={(nextState) => writeUrlState(nextState, true)}
          onClose={() => {
            focusAfterDrawerRef.current = "trigger";
            setDrawerOpen(false);
          }}
          sectors={sectors}
          state={urlState}
        />
      ) : null}

      <div className="screen-results-anchor">
        <h2 id="screen-results-heading" tabIndex={-1}>
          {translate(locale, "screen.screenpage.screenerResults")}
        </h2>
      </div>

      <div
        className="screen-active-filters"
        aria-label={translate(locale, "screen.screenpage.appliedFilters")}
      >
        {(Object.keys(urlState.filters) as Array<keyof ScreenUrlState["filters"]>)
          .filter((key) => urlState.filters[key] !== undefined)
          .map((key) => {
            const filters = urlState.filters;
            const labels = {
              sector: translate(locale, "screen.screenpage.sector", {
                sector: sectorLabel(filters.sector, locale),
              }),
              assetType: translate(locale, "screen.screenpage.asset", {
                assetType: assetLabel(filters.assetType, locale),
              }),
              regime: translate(locale, "screen.screenpage.regime", {
                value:
                  filters.regime === undefined
                    ? ""
                    : regimeLabel(filters.regime, locale),
              }),
              dataStatus: translate(locale, "screen.screenpage.data", {
                dataStatus: servingStatusLabel(filters.dataStatus, locale),
              }),
              confidence: translate(locale, "screen.screenpage.minimumConfidence", {
                translate:
                  filters.confidence === "high"
                    ? translate(locale, "screen.screenpage.high")
                    : filters.confidence === "medium"
                      ? translate(locale, "screen.screenpage.medium")
                      : translate(locale, "screen.screenpage.low"),
              }),
              minimumProbability: translate(
                locale,
                "screen.screenpage.minimumProbability",
                {
                  minimumProbability: ((filters.minimumProbability ?? 0) * 100).toFixed(
                    1,
                  ),
                },
              ),
              maximumQ10: translate(locale, "screen.screenpage.q10Ceiling", {
                maximumQ10: ((filters.maximumQ10 ?? 0) * 100).toFixed(1),
              }),
              effectiveAbstain: translate(locale, "screen.screenpage.abstain", {
                translate: filters.effectiveAbstain
                  ? translate(locale, "screen.screenpage.yes")
                  : translate(locale, "screen.screenpage.no"),
              }),
            };
            return (
              <button
                key={key}
                type="button"
                aria-label={translate(locale, "screen.screenpage.remove", {
                  value: labels[key],
                })}
                onClick={() => {
                  writeUrlState({
                    ...urlState,
                    filters: { ...filters, [key]: undefined },
                    page: { ...urlState.page, number: 1 },
                  });
                  document.getElementById("screen-results-heading")?.focus();
                }}
              >
                {labels[key]} ×
              </button>
            );
          })}
      </div>

      <ResearchModeSwitch
        formalAvailable={
          screenQuery.data?.resolved_snapshot.prediction_readiness === "forecast_ready"
        }
        mode={urlState.mode}
        onChange={(mode) => {
          pendingModeRef.current = mode;
          const leavingFormal = urlState.mode === "formal" && mode !== "formal";
          const nextState = normalizeScreenModeState(
            {
              ...urlState,
              mode,
              page: { ...urlState.page, number: 1 },
            },
            { resetPage: true, warn: leavingFormal },
          );
          if (leavingFormal) {
            setModeCompatibilityNotice(SCREEN_MODE_COMPATIBILITY_MESSAGE);
          } else if (mode === "formal") {
            setModeCompatibilityNotice(null);
          }
          writeUrlState(nextState);
        }}
      />
      <div className="screen-option-query-status" role="status">
        {optionQuery.isPending
          ? translate(locale, "screen.screenpage.loadingBatchOptionsData")
          : optionSnapshotMismatch
            ? translate(
                locale,
                "screen.screenpage.theBatchOptionsResponseDoesNotMatchTheResearch",
              )
            : optionQuery.isError
              ? translate(
                  locale,
                  "screen.screenpage.unableToLoadBatchOptionsDataOptionsFilteredResults",
                )
              : translate(
                  locale,
                  "screen.screenpage.optionsMetricsShareOneBatchSnapshotWidthRatioAnd",
                )}
        {optionQuery.isError && (
          <button type="button" onClick={() => void optionQuery.refetch()}>
            {translate(locale, "screen.screenpage.retryOptionsData")}
          </button>
        )}
        {optionState.sort && (
          <button
            type="button"
            onClick={() => changeOptions({ ...optionState, sort: undefined })}
          >
            {translate(locale, "screen.screenpage.clearOptionsSort")}
          </button>
        )}
        {allRowsRequired && (
          <button
            type="button"
            onClick={() => changeOptions(decodeScreenOptions(new URLSearchParams()))}
          >
            {translate(locale, "screen.screenpage.clearOptionsFiltersAndSort")}
          </button>
        )}
      </div>
      {urlState.mode !== "formal" ? (
        <div className="screen-results__header screen-research-sort">
          <label>
            {translate(locale, "screen.screenpage.researchSort")}
            <select
              aria-label={translate(locale, "screen.screenpage.researchSort")}
              value={
                optionState.sort ? `options:${optionState.sort}` : urlState.sort.key
              }
              onChange={(event) =>
                event.target.value.startsWith("options:")
                  ? changeOptions({
                      ...optionState,
                      sort: OPTION_COLUMNS.find(
                        ([key]) => `options:${key}` === event.target.value,
                      )?.[0],
                    })
                  : changeResearchSort({
                      ...urlState.sort,
                      key: event.target.value as ScreenUrlState["sort"]["key"],
                    })
              }
            >
              <option value="symbol">
                {translate(locale, "screen.screenpage.instrumentCode")}
              </option>
              <option value="rule_return_5">
                {translate(locale, "screen.screenpage.5DayHistoricalReturn")}
              </option>
              <option value="rule_return_20">
                {translate(locale, "screen.screenpage.20DayHistoricalReturn")}
              </option>
              <option value="rule_atr_pct_20">
                {translate(locale, "screen.screenpage.20DayAtrRatio")}
              </option>
              <option value="rule_channel_position_20">
                {translate(locale, "screen.screenpage.20DayChannelPosition")}
              </option>
              <option value="rule_volume_z_20">
                {translate(locale, "screen.screenpage.20DayVolumeZScore")}
              </option>
              <option value="data_freshness_seconds">
                {translate(locale, "screen.screenpage.dataAgeInSeconds")}
              </option>
              {OPTION_COLUMNS.map(([key, label]) => (
                <option key={key} value={`options:${key}`}>
                  {translate(locale, label)}
                </option>
              ))}
              {urlState.mode === "research" ? (
                <>
                  <option value="verified_q50">
                    {translate(locale, "screen.screenpage.verifiedReturnQ50")}
                  </option>
                  <option value="verified_q10">
                    {translate(
                      locale,
                      "screen.screenpage.verifiedReturnQ10NotAStopLoss",
                    )}
                  </option>
                </>
              ) : null}
            </select>
          </label>
          <label>
            {translate(locale, "screen.screenpage.sortDirection")}
            <select
              aria-label={translate(locale, "screen.screenpage.researchSortDirection")}
              value={optionState.sort ? optionState.direction : urlState.sort.direction}
              onChange={(event) =>
                optionState.sort
                  ? changeOptions({
                      ...optionState,
                      direction: event.target.value === "asc" ? "asc" : "desc",
                    })
                  : changeResearchSort({
                      ...urlState.sort,
                      direction: event.target.value === "asc" ? "asc" : "desc",
                    })
              }
            >
              <option value="asc">
                {translate(locale, "screen.screenpage.lowToHigh")}
              </option>
              <option value="desc">
                {translate(locale, "screen.screenpage.highToLow")}
              </option>
            </select>
          </label>
          <span
            role="status"
            aria-label={translate(locale, "screen.screenpage.screenerResultsSummary")}
          >
            {screenResponse === undefined
              ? resultsPending
                ? translate(locale, "screen.screenpage.updatingResults")
                : translate(locale, "screen.screenpage.resultsUnavailable")
              : translate(locale, "screen.screenpage.instrumentsPage", {
                  total: screenResponse.data.total,
                  page: screenResponse.data.page,
                })}
          </span>
        </div>
      ) : null}

      {screenResponse?.data.total === 0 && urlState.mode !== "formal" ? (
        <button
          type="button"
          onClick={() =>
            writeUrlState(
              {
                ...urlState,
                filters: resetScreenControls(urlState).filters,
                page: { ...urlState.page, number: 1 },
              },
              false,
              {
                ...decodeScreenOptions(new URLSearchParams()),
                sort: optionState.sort,
                direction: optionState.direction,
              },
            )
          }
        >
          {translate(locale, "screen.screenpage.clearFiltersKeepSortingAndSnapshot")}
        </button>
      ) : null}
      {resultsPending ? (
        <div
          aria-label={translate(locale, "screen.screenpage.loadingScreenerResults")}
          className="screen-loading"
          role="status"
        >
          <RefreshCw aria-hidden="true" size={20} strokeWidth={1.5} />
          <span>
            {translate(locale, "screen.screenpage.loadingSavedScreeningSnapshot")}
          </span>
        </div>
      ) : screenResponse === undefined ? (
        <div
          aria-label={translate(locale, "screen.screenpage.screenerResultsUnavailable")}
          className="error-state"
          role="alert"
        >
          <TriangleAlert aria-hidden="true" size={20} strokeWidth={1.5} />
          <div>
            <h1>{translate(locale, "screen.screenpage.screenerResultsUnavailable")}</h1>
            <p>
              {translate(
                locale,
                "screen.screenpage.theSavedResearchSnapshotCannotBeReadEstimatedOr",
              )}
            </p>
            <button onClick={() => void screenQuery.refetch()} type="button">
              <RefreshCw aria-hidden="true" size={18} strokeWidth={1.5} />
              {translate(locale, "screen.screenpage.retryScreenerResults")}
            </button>
          </div>
        </div>
      ) : (
        <ScreenEvidence
          isMobile={isMobile}
          onPageChange={(page) =>
            writeUrlState({ ...urlState, page: { ...urlState.page, number: page } })
          }
          onSortChange={changeResearchSort}
          queryError={screenQuery.isRefetchError ? screenQuery.error : null}
          response={screenResponse}
          state={resolveScreenUrlState(urlState, screenResponse.resolved_snapshot)}
          {...optionColumns}
        />
      )}
    </section>
  );
}
interface ScreenEvidenceProps extends ScreenOptionColumnsProps {
  isMobile: boolean;
  onPageChange: (page: number) => void;
  onSortChange: (sort: ScreenUrlState["sort"]) => void;
  queryError: Error | null;
  response: ScreenResponse;
  state: ScreenUrlState;
}
function ScreenEvidence({
  isMobile,
  onPageChange,
  onSortChange,
  queryError,
  response,
  state,
  optionItems,
  optionState,
  onOptionSort,
}: ScreenEvidenceProps) {
  const { locale } = useI18n();
  const retainedStatus =
    queryError === null
      ? null
      : queryError instanceof ResearchApiError && queryError.kind === "http"
        ? "stale"
        : "offline";
  const visibleStatus = retainedStatus ?? response.serving_status;
  const rankingUnavailable =
    state.mode === "formal" &&
    (response.resolved_snapshot.prediction_readiness !== "forecast_ready" ||
      response.effective_abstain ||
      visibleStatus === "stale" ||
      visibleStatus === "offline" ||
      visibleStatus === "unavailable");
  const unavailableDescription =
    response.resolved_snapshot.prediction_readiness !== "forecast_ready"
      ? translate(
          locale,
          "screen.screenpage.thisSnapshotHasNoAvailableFormalChampionModelResearch",
        )
      : response.effective_abstain
        ? translate(
            locale,
            "screen.screenpage.thisSnapshotAbstainedItsDataCannotBeRankedReview",
          )
        : translate(locale, "screen.screenpage.thisSnapshotIsItsDataCannotBeRanked", {
            translate:
              visibleStatus === "stale"
                ? translate(locale, "screen.screenfilters.stale")
                : visibleStatus === "offline"
                  ? translate(locale, "screen.screenfilters.offline")
                  : translate(locale, "screen.screenfilters.unavailable"),
          });
  return (
    <div
      className="screen-evidence"
      data-serving-status={visibleStatus}
      data-view-status={
        retainedStatus === null ? visibleStatus : `${retainedStatus}-retained`
      }
    >
      <DataStatusBanner
        lastSuccessfulAt={response.resolved_snapshot.as_of}
        status={visibleStatus}
      />
      {rankingUnavailable ? (
        <div className="screen-ranking-unavailable">
          <EmptyState
            description={unavailableDescription}
            title={translate(locale, "screen.screenpage.rankingUnavailable")}
          />
          {response.exclusion_reason.length > 0 ? (
            <div className="screen-ranking-unavailable__reasons">
              <h3>{translate(locale, "screen.screenpage.actualAbstentionReasons")}</h3>
              <ul
                aria-label={translate(
                  locale,
                  "screen.screenpage.actualAbstentionReasons",
                )}
              >
                {response.exclusion_reason.map((reason) => (
                  <li key={reason}>
                    {rejectionReasonLabel(reason as RejectionReason, locale) ??
                      reason.replaceAll("_", " ")}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : response.data.items.length === 0 ? (
        <EmptyState
          description={translate(
            locale,
            "screen.screenpage.noInstrumentsMatchedTheCurrentServerSideFiltersRelax",
          )}
          title={translate(locale, "screen.screenpage.noMatchingInstruments")}
        />
      ) : state.mode !== "formal" ? (
        <>
          <ResearchScreenResults
            isMobile={isMobile}
            mode={state.mode === "challenger" ? "challenger" : "verified"}
            onPageChange={onPageChange}
            page={response.data}
            rows={response.data.items}
            state={state}
            optionItems={optionItems}
            optionState={optionState}
            onOptionSort={onOptionSort}
          />
        </>
      ) : isMobile ? (
        <section className="screen-results screen-results--mobile">
          <header className="screen-results__header">
            <div>
              <p>
                {translate(locale, "screen.screenpage.total.withValues", {
                  total: response.data.total,
                  page: response.data.page,
                })}
              </p>
            </div>
          </header>
          <MobileScreenList rows={response.data.items} state={state} />
          <nav
            aria-label={translate(
              locale,
              "screen.researchscreenresults.screenerPagination",
            )}
            className="screen-pagination"
          >
            <button
              disabled={response.data.page <= 1}
              onClick={() => onPageChange(response.data.page - 1)}
              type="button"
            >
              {translate(locale, "screen.researchscreenresults.previous")}
            </button>
            <span>
              {translate(locale, "screen.researchscreenresults.day.withValues2", {
                page: response.data.page,
                Math: Math.max(
                  1,
                  Math.ceil(response.data.total / response.data.page_size),
                ),
              })}
            </span>
            <button
              disabled={
                response.data.page * response.data.page_size >= response.data.total
              }
              onClick={() => onPageChange(response.data.page + 1)}
              type="button"
            >
              {translate(locale, "screen.researchscreenresults.next2")}
            </button>
          </nav>
        </section>
      ) : (
        <ScreenTable
          onPageChange={onPageChange}
          onSortChange={onSortChange}
          page={response.data}
          state={state}
          optionItems={optionItems}
          optionState={optionState}
          onOptionSort={onOptionSort}
        />
      )}
    </div>
  );
}
