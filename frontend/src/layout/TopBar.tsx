import { instrumentName, instrumentSearchTerm } from "../i18n/instruments";
import { translate, type Locale } from "../i18n/core";
import { useI18n } from "../i18n/context";
import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Circle, CircleHelp, CircleMinus, Search, Unplug } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import type { components } from "../api/generated/schema";
import { createResearchApiClient } from "../api/client";
import { formatUsMarketTime } from "../app/presentation";
import { snapshotQueryFromSearch } from "../app/snapshot-refresh";
import { decodeResearchUrlState, encodeResearchUrlState } from "../app/url-state";
import { EodUpdateButton } from "../features/operations/EodUpdateButton";
import { LanguageSelect } from "../i18n/LanguageSelect";
type Connectivity = components["schemas"]["Connectivity"];
interface ConnectivityPresentation {
  Icon: LucideIcon;
  label: string;
  tone: string;
}
const CONNECTIVITY_PRESENTATION = (
  locale: Locale,
): Record<Connectivity, ConnectivityPresentation> => ({
  connected: {
    Icon: Circle,
    label: translate(locale, "common.shared.connected"),
    tone: "healthy",
  },
  disconnected: {
    Icon: Unplug,
    label: translate(locale, "common.shared.disconnected"),
    tone: "disconnected",
  },
  unknown: {
    Icon: CircleHelp,
    label: translate(locale, "common.shared.unknown"),
    tone: "unknown",
  },
  not_applicable: {
    Icon: CircleMinus,
    label: translate(locale, "common.shared.notApplicable"),
    tone: "neutral",
  },
});
export interface TopBarProps {
  dataAsOf: string;
  navigation?: ReactNode;
  opendStatus: Connectivity;
  showProductName: boolean;
}
const apiClient = createResearchApiClient();
function normalizedSymbol(value: string) {
  const token = value.trim().toUpperCase();
  if (token === "") return null;
  return token.startsWith("US.") ? token : `US.${token}`;
}
export function TopBar({
  dataAsOf,
  navigation,
  opendStatus,
  showProductName,
}: TopBarProps) {
  const { locale } = useI18n();
  const { Icon, label, tone } = CONNECTIVITY_PRESENTATION(locale)[opendStatus];
  const location = useLocation();
  const navigate = useNavigate();
  const [searchValue, setSearchValue] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const [searchError, setSearchError] = useState("");
  const snapshotQuery = snapshotQueryFromSearch(location.search);
  useEffect(() => {
    const timer = window.setTimeout(
      () => setSearchTerm(searchValue.trim().replace(/^US\./i, "")),
      180,
    );
    return () => window.clearTimeout(timer);
  }, [searchValue]);
  const instruments = useQuery({
    queryKey: ["search-instruments", snapshotQuery, searchTerm],
    enabled: searchTerm !== "",
    queryFn: async () => {
      const { data } = await apiClient.get("/api/v1/instruments", {
        params: {
          query: {
            ...snapshotQuery,
            search: instrumentSearchTerm(searchTerm),
            page: 1,
            limit: 8,
            sort: "symbol",
          },
        },
      });
      if (data === undefined) throw new Error("instrument search unavailable");
      return data;
    },
  });
  const suggestions =
    searchTerm !== "" && searchTerm === searchValue.trim().replace(/^US\./i, "")
      ? (instruments.data?.data.items ?? [])
      : [];
  const openResearch = (value: string) => {
    const symbol = normalizedSymbol(value);
    if (symbol === null) return;
    if (!/^US\.[A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)?$/.test(symbol)) {
      setSearchError("invalid_ticker");
      return;
    }
    let state = decodeResearchUrlState(new URLSearchParams(location.search));
    if (suggestions.some((item) => item.symbol === symbol) && instruments.data) {
      const snapshot = instruments.data.resolved_snapshot;
      state = {
        ...state,
        snapshotId: snapshot.snapshot_id,
        asOf: snapshot.as_of,
        revisionNo: snapshot.revision_no,
        versions: {
          ...state.versions,
          datasetRevision: snapshot.dataset_revision ?? undefined,
          featureSetVersion: snapshot.feature_set_version ?? undefined,
          universeVersion: snapshot.universe_version ?? undefined,
          modelVersion: snapshot.model_version ?? undefined,
          calibrationVersion: snapshot.calibration_version ?? undefined,
        },
      };
    }
    const query = encodeResearchUrlState({
      ...state,
      page: { ...state.page, number: 1 },
      symbol: undefined,
    }).toString();
    setSearchValue("");
    setSearchTerm("");
    setSearchError("");
    setSelectedIndex(-1);
    void navigate(
      `/research/${encodeURIComponent(symbol)}${query === "" ? "" : `?${query}`}`,
      { state: { returnTo: `${location.pathname}${location.search}` } },
    );
  };
  return (
    <header
      className={`top-bar${navigation === undefined ? "" : " top-bar--with-nav"}`}
    >
      {showProductName ? <span className="top-bar__product">Signal Lab</span> : null}
      {navigation}
      <div className="top-bar__utilities">
        <form
          className="ticker-search"
          onSubmit={(event) => {
            event.preventDefault();
            const exact = suggestions.find(
              (item) => item.symbol === normalizedSymbol(searchValue),
            );
            const typed = normalizedSymbol(searchValue);
            const namedMatch =
              typed && !/^US\.[A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)?$/.test(typed)
                ? suggestions[0]?.symbol
                : undefined;
            openResearch(
              suggestions[selectedIndex]?.symbol ??
                exact?.symbol ??
                namedMatch ??
                searchValue,
            );
          }}
          role="search"
        >
          <Search aria-hidden="true" size={18} strokeWidth={1.5} />
          <input
            aria-label={translate(locale, "common.shared.searchInstruments")}
            role="combobox"
            aria-autocomplete="list"
            aria-controls="ticker-search-suggestions"
            aria-expanded={suggestions.length > 0}
            aria-activedescendant={
              selectedIndex >= 0 && suggestions[selectedIndex]
                ? `ticker-option-${selectedIndex}`
                : undefined
            }
            autoComplete="off"
            name="symbol"
            onChange={(event) => {
              setSearchValue(event.target.value);
              setSelectedIndex(-1);
              setSearchError("");
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                setSearchValue("");
                setSelectedIndex(-1);
              }
              if (
                suggestions.length > 0 &&
                (event.key === "ArrowDown" || event.key === "ArrowUp")
              ) {
                event.preventDefault();
                setSelectedIndex((current) =>
                  event.key === "ArrowDown"
                    ? (current + 1) % suggestions.length
                    : current <= 0
                      ? suggestions.length - 1
                      : current - 1,
                );
              }
            }}
            placeholder={translate(
              locale,
              "common.shared.searchCurrentUniverseByTickerOrName",
            )}
            type="search"
            value={searchValue}
          />
          {suggestions.length === 0 ? null : (
            <ul
              aria-label={translate(
                locale,
                "common.shared.instrumentSearchSuggestions",
              )}
              className="ticker-search__suggestions"
              role="listbox"
              id="ticker-search-suggestions"
            >
              {suggestions.map((item, index) => (
                <li key={item.symbol} role="presentation">
                  <button
                    id={`ticker-option-${index}`}
                    aria-selected={selectedIndex === index}
                    onClick={() => openResearch(item.symbol)}
                    role="option"
                    type="button"
                  >
                    {item.symbol.replace(/^US\./, "")} | {instrumentName(item.symbol, item.name, locale)}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {(searchError
            ? translate(locale, "common.shared.enterAValidTickerSuchAsTsmOr")
            : "") ||
          (searchTerm !== "" && !instruments.isPending && suggestions.length === 0) ? (
            <p className="ticker-search__message" role="status">
              {(searchError
                ? translate(locale, "common.shared.enterAValidTickerSuchAsTsmOr")
                : "") ||
                (instruments.isError
                  ? translate(
                      locale,
                      "common.shared.suggestionsAreUnavailableEnterATickerAndPress",
                    )
                  : translate(
                      locale,
                      "common.shared.noMatchingInstrumentInThisSnapshotChangeThe",
                    ))}
            </p>
          ) : null}
        </form>
        <span className={`opend-status opend-status--${tone}`}>
          <Icon
            aria-hidden="true"
            fill={opendStatus === "connected" ? "currentColor" : "none"}
            size={opendStatus === "connected" ? 8 : 16}
            strokeWidth={1.5}
          />
          <span>OpenD {label}</span>
        </span>
        <span className="data-time">
          <span>{"" + (translate(locale, "common.shared.marketDataAsOf") + "")}</span>
          <time dateTime={dataAsOf} title={dataAsOf}>
            {formatUsMarketTime(dataAsOf, locale)}
          </time>
        </span>
        <LanguageSelect />
        <EodUpdateButton compact />
      </div>
    </header>
  );
}
