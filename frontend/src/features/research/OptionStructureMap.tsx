import { ResearchErrorDetail } from "./ResearchErrorDetail";
import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { X } from "lucide-react";
import { createResearchApiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { formatUsMarketTime } from "../../app/presentation";
import { money, shortSymbol } from "../options/options-board-rows";
import { optionQuery, type OptionQuery } from "./options-state";
import "./structure-map.css";
type Response = components["schemas"]["OptionStructureMapResponse"];
type Group = components["schemas"]["OptionMapGroup"];
type Cell = components["schemas"]["OptionMapCell"];
type Window = components["schemas"]["OptionMapWindow"];
type Metric = "gex" | "vex" | "turnover";
type Side = "all" | "call" | "put";
const windows = (locale: Locale = "zh-CN") =>
  [
    [5, translate(locale, "research.optionstructuremap.next5Days")],
    [14, translate(locale, "research.optionstructuremap.2Weeks")],
    [30, translate(locale, "research.optionstructuremap.1Month")],
    [90, translate(locale, "research.optionstructuremap.3Months")],
  ] as const;
const client = createResearchApiClient({ trackConnectivity: false });
const reasons = (locale: Locale = "zh-CN"): Record<string, string> => ({
  oi_date_unproven: translate(locale, "research.optionstructuremap.oiDateUnconfirmed"),
  future_oi_date: translate(
    locale,
    "research.optionstructuremap.oiDateIsLaterThanModelTime",
  ),
  turnover_session_range_conflict: translate(
    locale,
    "research.optionstructuremap.turnoverImpliedPriceConflictsWithTheIntradayRangeExcluded",
  ),
  turnover_unavailable: translate(
    locale,
    "research.optionstructuremap.turnoverFieldMissing",
  ),
  stale_or_unproven_session: translate(
    locale,
    "research.optionstructuremap.marketDataCrossesDatesOrTimeIsUnconfirmedExcluded",
  ),
  model_inputs_unavailable: translate(
    locale,
    "research.optionstructuremap.ivOiOrContractMultiplierMissing",
  ),
  model_anchor_unavailable: translate(
    locale,
    "research.optionstructuremap.modelReferencePriceOrTimeMissing",
  ),
  nonstandard_or_unknown_delivery: translate(
    locale,
    "research.optionstructuremap.nonstandardOrUnconfirmedDeliverableExcluded",
  ),
  metric_numeric_overflow: translate(
    locale,
    "research.optionstructuremap.valueOutsideTheComputableRange",
  ),
  no_option_snapshot: translate(
    locale,
    "research.optionanalyticspanel.noSavedOptionsSnapshot",
  ),
  symbol_outside_option_scope: translate(
    locale,
    "research.optionspanel.optionsDataCoversSpyQqqSevenTechnologyStocksAnd",
  ),
});
const time = (at: string | null | undefined, locale: Locale = "zh-CN") =>
  at
    ? formatUsMarketTime(at, locale)
    : translate(locale, "research.optionanalyticspanel.unconfirmed");
const signed = (v: number | null, locale: Locale = "zh-CN") =>
  v == null
    ? translate(locale, "research.optionanalyticspanel.missing")
    : `${v > 0 ? "+" : v < 0 ? "−" : ""}${money(Math.abs(v), locale)}`;
const sum = (a: number | null | undefined, b: number | null | undefined) =>
  a == null && b == null ? null : (a ?? 0) + (b ?? 0);
const value = (g: Group | undefined, metric: Metric, side: Side) =>
  side === "all"
    ? sum(g?.call?.[metric], g?.put?.[metric])
    : (g?.[side]?.[metric] ?? null);
const partial = (g: Group | undefined, metric: Metric, side: Side) =>
  (side === "all" ? [g?.call, g?.put] : [g?.[side]]).some(
    (r) =>
      r &&
      (r.contracts ?? 0) >
        (metric === "turnover" ? (r.turnover_valid ?? 0) : (r.exposure_valid ?? 0)),
  );
const ranks = (window: Window | undefined, side: Side) =>
  (side === "all"
    ? window?.rankings
    : side === "call"
      ? window?.call_rankings
      : window?.put_rankings) ?? [];
const dte = (expiry: string, at: string | null | undefined) => {
  if (!at) return null;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(at));
  const part = (type: string) => parts.find((p) => p.type === type)?.value;
  return Math.round(
    (Date.parse(`${expiry}T00:00:00Z`) -
      Date.parse(`${part("year")}-${part("month")}-${part("day")}T00:00:00Z`)) /
      86400000,
  );
};
export async function loadStructureMap(
  symbol: string,
  query: OptionQuery,
): Promise<Response> {
  const { data } = await client.get(
    "/api/v1/instruments/{symbol}/options/structure-map",
    {
      params: { path: { symbol }, query },
      signal: AbortSignal.timeout(15000),
    },
  );
  if (!data) throw new Error("Options structure response unavailable");
  return data;
}
function Ranking({ window, side }: { window: Window | undefined; side: Side }) {
  const { locale } = useI18n();
  const rows = ranks(window, side);
  return (
    <>
      <table
        aria-label={translate(
          locale,
          "research.optionstructuremap.turnoverConcentrationRanking",
        )}
        className="os-map__ranking"
      >
        <thead>
          <tr>
            <th>#</th>
            <th>{translate(locale, "research.optionanalyticspanel.strike")}</th>
            <th>{translate(locale, "research.optionstructuremap.turnoverUsd")}</th>
            <th>{translate(locale, "research.optionanalyticspanel.share")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.strike}>
              <td>{i + 1}</td>
              <th>${row.strike}</th>
              <td>{money(row.turnover, locale)}</td>
              <td>
                <span className="os-map__share">
                  <span style={{ width: `${row.share * 100}%` }} />
                </span>
                {(row.share * 100).toFixed(1)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && (
        <p className="options-notice">
          {translate(
            locale,
            "research.optionstructuremap.noEligibleTurnoverInThisWindow",
          )}
        </p>
      )}
      {window?.partial && (
        <small className="os-map__quality">
          {translate(
            locale,
            "research.optionstructuremap.rankingOfCoveredRangeMissingOrAnomalousRecordsExcluded",
          )}
        </small>
      )}
    </>
  );
}
export function OptionStructureMap({
  symbol,
  query,
  enabled = true,
  loadData = loadStructureMap,
}: {
  symbol: string;
  query: OptionQuery;
  enabled?: boolean;
  loadData?: typeof loadStructureMap;
}) {
  const { locale } = useI18n();
  const [params, setParams] = useSearchParams();
  const mapQuery = useMemo(
    () => ({
      ...query,
      dte_min: params.has("option_dte_min") ? (optionQuery(params).dte_min ?? 1) : 1,
      dte_max: params.has("option_dte_max") ? (optionQuery(params).dte_max ?? 30) : 30,
      strike_min_ratio: params.has("option_strike_min")
        ? (optionQuery(params).strike_min_ratio ?? 0.9)
        : 0.9,
      strike_max_ratio: params.has("option_strike_max")
        ? (optionQuery(params).strike_max_ratio ?? 1.1)
        : 1.1,
    }),
    [query, params],
  );
  const result = useQuery({
    queryKey: ["option-structure-map", symbol, mapQuery],
    queryFn: () => loadData(symbol, mapQuery),
    enabled,
    retry: false,
    staleTime: 60000,
  });
  const data = result.data?.data;
  const cells = useMemo(() => data?.cells ?? [], [data]);
  const strikes = useMemo(
    () => [...(data?.strikes ?? [])].sort((a, b) => b - a),
    [data],
  );
  const expirations = data?.expirations ?? [];
  const snapshot = data?.snapshot;
  const spot = snapshot?.underlying_price;
  const side: Side =
    params.get("option_map_side") === "call"
      ? "call"
      : params.get("option_map_side") === "put"
        ? "put"
        : "all";
  const metric = params.get("option_view") === "turnover" ? "turnover" : "exposure";
  const [selectedKey, setSelectedKey] = useState<string>();
  const [detailOpen, setDetailOpen] = useState(false);
  const requestedRankWindow = Number(params.get("option_rank_window"));
  const rankWindow = windows(locale).some(([v]) => v === requestedRankWindow)
    ? requestedRankWindow
    : 30;
  const heatRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const key = (c: Cell) => `${c.strike}:${c.expiration}`;
  const nearStrike = strikes.reduce(
    (nearest, strike) =>
      spot != null && Math.abs(strike - spot) < Math.abs(nearest - spot)
        ? strike
        : nearest,
    strikes[0] ?? 0,
  );
  const sideCells =
    side === "all" ? cells : cells.filter((c) => (c[side]?.contracts ?? 0) > 0);
  const selected =
    sideCells.find((c) => key(c) === selectedKey) ??
    sideCells.find((c) => c.strike === nearStrike) ??
    sideCells[0];
  const lookup = useMemo(
    () => new Map(cells.map((c) => [`${c.strike}:${c.expiration}`, c])),
    [cells],
  );
  const strikeSummaries = useMemo(
    () =>
      new Map(
        Object.entries(data?.strike_summary ?? {}).map(([strike, group]) => [
          Number(strike),
          group,
        ]),
      ),
    [data?.strike_summary],
  );
  const max = (metric: Metric) =>
    Math.max(0, ...cells.map((c) => Math.abs(value(c, metric, side) ?? 0)));
  const gmax = max("gex"),
    vmax = max("vex"),
    tmax = max("turnover");
  const update = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [name, v] of Object.entries(changes)) {
      if (v) next.set(name, v);
      else next.delete(name);
    }
    setParams(next, { preventScrollReset: true });
  };
  useEffect(() => {
    const container = heatRef.current;
    const row = container?.querySelector<HTMLElement>("[data-near-spot='true']");
    if (container && row)
      container.scrollTop +=
        row.getBoundingClientRect().top -
        container.getBoundingClientRect().top -
        container.clientHeight / 2 +
        row.offsetHeight / 2;
  }, [snapshot?.snapshot_id, nearStrike, mapQuery.dte_max, strikes]);
  const choose = (c: Cell) => {
    setSelectedKey(key(c));
    setDetailOpen(true);
  };
  const close = () => {
    setDetailOpen(false);
    closeRef.current?.focus();
  };
  const tone = (v: number | null) =>
    v == null ? "missing" : v < 0 ? "negative" : "positive";
  const heat = (v: number | null, maximum: number, cash = false) => {
    if (v == null) return { backgroundColor: "#30313b", color: "#c1bfcc" };
    const strength = maximum ? Math.min(1, Math.abs(v) / maximum) : 0;
    const high = cash ? [180, 167, 245] : v < 0 ? [150, 58, 96] : [43, 139, 100];
    const low = [24, 27, 33];
    return {
      backgroundColor: `rgb(${low.map((v, i) => Math.round(v + ((high[i] ?? v) - v) * strength)).join(",")})`,
      color: cash && strength > 0.65 ? "#101116" : "#f3f2f8",
    };
  };
  const cellLabel = (c: Cell) =>
    translate(locale, "research.optionstructuremap.strikeGexVexTurnover", {
      strike: c.strike,
      expiration: c.expiration,
      value: signed(value(c, "gex", side), locale),
      value2: signed(value(c, "vex", side), locale),
      value3: money(value(c, "turnover", side), locale),
    });
  const line = (g: Group, m: "gex" | "vex") => {
    const v = value(g, m, side);
    return (
      <span className="os-map__cell-line" style={heat(v, m === "gex" ? gmax : vmax)}>
        <small>{m === "gex" ? "G" : "V"}</small>
        {signed(v, locale)}
        {v != null && partial(g, m, side) && (
          <sup
            title={translate(
              locale,
              "research.optionstructuremap.someContractsExcluded",
            )}
          >
            *
          </sup>
        )}
      </span>
    );
  };
  const wallText = (values: number[] | undefined) =>
    values?.length
      ? values.map((s) => `$${s}`).join(" / ")
      : translate(locale, "research.optionstructuremap.notFormed");
  const selectedContracts =
    snapshot?.contracts?.filter((c) =>
      selected?.contract_ids?.includes(c.contract_id),
    ) ?? [];
  const multipliers = [
    ...new Set(selectedContracts.map((c) => c.multiplier).filter((v) => v != null)),
  ];
  const selectedReasons = [
    ...new Set([...(selected?.call?.reasons ?? []), ...(selected?.put?.reasons ?? [])]),
  ];
  const combinedPartial =
    data &&
    (data.exposure_coverage.missing > 0 ||
      data.exposure_coverage.excluded > 0 ||
      !data.exposure_coverage.discovery_complete);
  return (
    <div className="os-map">
      <div className="os-map__title">
        <strong>
          {translate(
            locale,
            "research.optionstructuremap.optionsStructure.withValues",
            { shortSymbol: shortSymbol(symbol) },
          )}
        </strong>
        <span>
          {translate(locale, "research.optionstructuremap.snapshotSpot.withValues", {
            spot: money(spot, locale),
          })}
        </span>
        <small>
          {translate(
            locale,
            "research.optionstructuremap.optionsMarketData.withValues",
            {
              marketAt: time(data?.market_at, locale),
              capturedAt: time(snapshot?.captured_at, locale),
              modelAt: time(data?.model_at, locale),
            },
          )}
        </small>
      </div>
      <div className="os-map__toolbar">
        <div
          aria-label={translate(locale, "research.optionstructuremap.structureMapView")}
        >
          {[
            ["exposure", "GEX + VEX"],
            ["turnover", translate(locale, "research.optionPulseChart.turnover")],
            ["legacy", translate(locale, "research.optionstructuremap.otherMetrics")],
          ].map(([v, label]) => (
            <button
              key={v}
              type="button"
              aria-pressed={metric === v}
              onClick={() => update({ option_view: v!, option_metric: "" })}
            >
              {label}
            </button>
          ))}
        </div>
        <div aria-label={translate(locale, "research.optionspanel.contractSide")}>
          {[
            ["all", translate(locale, "research.optionstructuremap.all")],
            ["call", "Call"],
            ["put", "Put"],
          ].map(([v, label]) => (
            <button
              key={v}
              type="button"
              aria-pressed={side === v}
              onClick={() => update({ option_map_side: v! })}
            >
              {label}
            </button>
          ))}
        </div>
        <div aria-label={translate(locale, "research.optionstructuremap.expiryWindow")}>
          {windows(locale).map(([v, label]) => (
            <button
              key={v}
              type="button"
              aria-pressed={mapQuery.dte_max === v}
              onClick={() => update({ option_dte_min: "1", option_dte_max: String(v) })}
            >
              {label}
            </button>
          ))}
        </div>
        <label>
          {translate(locale, "research.optionstructuremap.strikeRange")}
          <select
            aria-label={translate(
              locale,
              "research.optionstructuremap.structureMapStrikeRange",
            )}
            value={`${mapQuery.strike_min_ratio}:${mapQuery.strike_max_ratio}`}
            onChange={(e) => {
              const [lo, hi] = e.target.value.split(":");
              update({ option_strike_min: lo!, option_strike_max: hi! });
            }}
          >
            <option value="0.95:1.05">
              {translate(locale, "research.optionstructuremap.spot5")}
            </option>
            <option value="0.9:1.1">
              {translate(locale, "research.optionstructuremap.spot10")}
            </option>
            <option value="0.7:1.3">
              {translate(locale, "research.optionstructuremap.spot30")}
            </option>
            {!["0.95:1.05", "0.9:1.1", "0.7:1.3"].includes(
              `${mapQuery.strike_min_ratio}:${mapQuery.strike_max_ratio}`,
            ) && (
              <option
                value={`${mapQuery.strike_min_ratio}:${mapQuery.strike_max_ratio}`}
              >
                {translate(locale, "research.optionstructuremap.customRange")}
              </option>
            )}
          </select>
        </label>
        <button type="button" onClick={() => void result.refetch()}>
          {translate(locale, "research.optionstructuremap.reload")}
        </button>
        <button
          ref={closeRef}
          className="os-map__open"
          type="button"
          aria-expanded={detailOpen}
          onClick={() => setDetailOpen(!detailOpen)}
        >
          {translate(locale, "research.optionstructuremap.detailsAndRanking")}
        </button>
      </div>
      <p className="os-map__caveat">
        {translate(
          locale,
          "research.optionstructuremap.savedSnapshotNotLiveModelEstimatesOiDateUnconfirmed.withValues",
          {
            translate: combinedPartial
              ? translate(locale, "research.optionstructuremap.coveredRangeEstimate")
              : translate(
                  locale,
                  "research.optionstructuremap.currentFilteredRangeEstimate",
                ),
          },
        )}
      </p>
      {result.isPending && enabled && (
        <p role="status">
          {translate(locale, "research.optionstructuremap.loadingGexVexAndTurnover")}
        </p>
      )}
      {result.isError && (
        <div role="alert">
          {translate(locale, "research.optionstructuremap.unableToLoadStructureMap")}
          <ResearchErrorDetail message={result.error.message} />
          {translate(locale, "research.optionstructuremap.checkTheRangeOrReload")}
        </div>
      )}
      {data && (
        <>
          <div className="os-map__stats">
            {[
              [
                side === "all"
                  ? translate(locale, "research.optionstructuremap.netGex")
                  : `${side === "call" ? "Call" : "Put"} GEX`,
                signed(value(data.total, "gex", side), locale),
              ],
              [
                side === "all"
                  ? translate(locale, "research.optionstructuremap.netVex")
                  : `${side === "call" ? "Call" : "Put"} VEX`,
                signed(value(data.total, "vex", side), locale),
              ],
              [
                translate(locale, "research.optionstructuremap.estimatedCallWall"),
                wallText(data.call_walls),
              ],
              [
                translate(locale, "research.optionstructuremap.estimatedPutWall"),
                wallText(data.put_walls),
              ],
              [
                translate(locale, "research.optionstructuremap.turnoverPeak"),
                wallText(data.turnover_peaks),
              ],
            ].map(([label, v], i) => (
              <div key={label}>
                <small>{label}</small>
                <strong
                  data-tone={
                    i < 2
                      ? tone(value(data.total, i === 0 ? "gex" : "vex", side))
                      : undefined
                  }
                >
                  {v}
                </strong>
              </div>
            ))}
          </div>
          <div className="os-map__workspace">
            <div className="os-map__main">
              <div
                ref={heatRef}
                className="os-map__scroll"
                tabIndex={0}
                role="region"
                aria-label={translate(
                  locale,
                  "research.optionstructuremap.scrollableOptionsStructureHeatmap",
                )}
              >
                <table
                  className="os-map__table"
                  aria-label={
                    metric === "exposure"
                      ? translate(
                          locale,
                          "research.optionstructuremap.gexAndVexTwoRowHeatmap",
                        )
                      : translate(
                          locale,
                          "research.optionstructuremap.providerReportedTurnoverHeatmap",
                        )
                  }
                >
                  <thead>
                    <tr>
                      <th scope="col">
                        {translate(locale, "research.optionstructuremap.strikeUsd")}
                      </th>
                      {expirations.map((e) => (
                        <th key={e} scope="col">
                          {e.slice(5).replace("-", "/")}
                          <small>{dte(e, data.model_at) ?? "—"} DTE</small>
                        </th>
                      ))}
                      <th scope="col">
                        {translate(locale, "research.optionstructuremap.rangeSummary")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {strikes.map((strike) => (
                      <tr
                        key={strike}
                        data-near-spot={spot != null && strike === nearStrike}
                      >
                        <th scope="row">
                          <span>${strike}</span>
                          {data.call_walls?.includes(strike) && (
                            <small className="os-map__wall">{translate(locale, "options.callWall")}</small>
                          )}
                          {data.put_walls?.includes(strike) && (
                            <small className="os-map__wall">{translate(locale, "options.putWall")}</small>
                          )}
                          {data.turnover_peaks?.includes(strike) && (
                            <small className="os-map__peak">
                              {translate(
                                locale,
                                "research.optionstructuremap.turnoverPeak",
                              )}
                            </small>
                          )}
                          {spot != null && strike === nearStrike && (
                            <small className="os-map__spot">
                              {translate(locale, "research.optionspanel.nearSpot")}
                            </small>
                          )}
                        </th>
                        {expirations.map((expiry) => {
                          const c = lookup.get(`${strike}:${expiry}`);
                          return (
                            <td key={expiry}>
                              {c &&
                              (side === "all" || (c[side]?.contracts ?? 0) > 0) ? (
                                <button
                                  type="button"
                                  aria-label={cellLabel(c)}
                                  aria-pressed={
                                    selected != null && key(selected) === key(c)
                                  }
                                  title={cellLabel(c)}
                                  onClick={() => choose(c)}
                                  className="os-map__cell"
                                >
                                  {metric === "exposure" ? (
                                    <>
                                      {line(c, "gex")}
                                      {line(c, "vex")}
                                    </>
                                  ) : (
                                    <span
                                      className="os-map__money-cell"
                                      style={heat(
                                        value(c, "turnover", side),
                                        tmax,
                                        true,
                                      )}
                                    >
                                      {value(c, "turnover", side) == null
                                        ? translate(
                                            locale,
                                            "research.optionanalyticspanel.missing",
                                          )
                                        : money(value(c, "turnover", side), locale)}
                                      {value(c, "turnover", side) != null &&
                                        partial(c, "turnover", side) &&
                                        "*"}
                                    </span>
                                  )}
                                </button>
                              ) : (
                                <span
                                  className="os-map__absent"
                                  title={translate(
                                    locale,
                                    "research.optionstructuremap.noMatchingContract",
                                  )}
                                >
                                  —
                                </span>
                              )}
                            </td>
                          );
                        })}
                        <td className="os-map__sum">
                          {metric === "exposure" ? (
                            <>
                              <span>
                                G{" "}
                                {signed(
                                  value(strikeSummaries.get(strike), "gex", side),
                                  locale,
                                )}
                              </span>
                              <span>
                                V{" "}
                                {signed(
                                  value(strikeSummaries.get(strike), "vex", side),
                                  locale,
                                )}
                              </span>
                            </>
                          ) : (
                            money(
                              value(strikeSummaries.get(strike), "turnover", side),
                              locale,
                            )
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <th scope="row">
                        {translate(locale, "research.optionstructuremap.rangeTotal")}
                      </th>
                      {expirations.map((e) => (
                        <td key={e}>
                          {metric === "exposure" ? (
                            <>
                              <span>
                                G{" "}
                                {signed(
                                  value(data.expiry_summary?.[e], "gex", side),
                                  locale,
                                )}
                              </span>
                              <span>
                                V{" "}
                                {signed(
                                  value(data.expiry_summary?.[e], "vex", side),
                                  locale,
                                )}
                              </span>
                            </>
                          ) : (
                            money(
                              value(data.expiry_summary?.[e], "turnover", side),
                              locale,
                            )
                          )}
                        </td>
                      ))}
                      <td>
                        {metric === "exposure" ? (
                          <>
                            <span>
                              G {signed(value(data.total, "gex", side), locale)}
                            </span>
                            <span>
                              V {signed(value(data.total, "vex", side), locale)}
                            </span>
                          </>
                        ) : (
                          money(value(data.total, "turnover", side), locale)
                        )}
                      </td>
                    </tr>
                  </tfoot>
                </table>
              </div>
              {!cells.length && (
                <p className="options-notice">
                  {data.reasons?.map((r) => reasons(locale)[r] ?? r).join(locale === "en-US" ? "; " : "；") ||
                    translate(
                      locale,
                      "research.optionstructuremap.noContractsInTheCurrentFilteredRange",
                    )}
                </p>
              )}
              <div className="os-map__legends">
                {metric === "exposure" ? (
                  (
                    [
                      ["GEX", gmax],
                      ["VEX", vmax],
                    ] as const
                  ).map(([label, m]) => (
                    <div key={label}>
                      <strong>{label}</strong>
                      <span className="os-map__scale">
                        <i />
                        <i />
                      </span>
                      <small>
                        {signed(-m, locale)} / 0 / {signed(m, locale)}
                      </small>
                    </div>
                  ))
                ) : (
                  <div>
                    <strong>
                      {translate(locale, "research.optionstructuremap.turnoverUsd")}
                    </strong>
                    <span className="os-map__cash-scale" />
                    <small>$0 / {money(tmax, locale)}</small>
                  </div>
                )}
              </div>
              <p className="os-map__quality">
                {translate(
                  locale,
                  "research.optionstructuremap.gexUsd1UnderlyingMoveVexUsd1Percentage",
                )}
              </p>
              <p className="os-map__quality">
                {translate(
                  locale,
                  "research.optionstructuremap.modelValid.withValues",
                  {
                    valid: data.exposure_coverage.valid,
                    expected: data.exposure_coverage.expected,
                    valid2: data.turnover_coverage.valid,
                    expected2: data.turnover_coverage.expected,
                    excluded: data.turnover_coverage.excluded,
                  },
                )}
              </p>
            </div>
            <aside
              className="os-map__aside"
              data-open={detailOpen}
              aria-label={translate(
                locale,
                "research.optionstructuremap.cellDetailsAndTurnoverRanking",
              )}
              onKeyDown={(e) => {
                if (e.key === "Escape") close();
              }}
            >
              <section className="os-map__detail">
                <header>
                  <h3>
                    {translate(locale, "research.optionstructuremap.cellDetails")}
                  </h3>
                  <button
                    type="button"
                    className="os-map__close"
                    aria-label={translate(
                      locale,
                      "research.optionstructuremap.closeDetails",
                    )}
                    onClick={close}
                  >
                    <X size={18} />
                  </button>
                </header>
                {selected ? (
                  <>
                    <h4>
                      {shortSymbol(symbol)} ${selected.strike}; {selected.expiration} (
                      {dte(selected.expiration, data.model_at) ?? "—"} DTE)
                    </h4>
                    <div className="os-map__detail-exposures">
                      {(["gex", "vex"] as const).map((m) => (
                        <div key={m}>
                          <small>
                            {m === "gex"
                              ? translate(
                                  locale,
                                  "research.optionstructuremap.gexUsd1UnderlyingMove",
                                )
                              : translate(
                                  locale,
                                  "research.optionstructuremap.vexUsd1PercentagePointIvIncrease",
                                )}
                          </small>
                          <strong data-tone={tone(value(selected, m, side))}>
                            {signed(value(selected, m, side), locale)}
                          </strong>
                          <span className="os-map__magnitude">
                            <span
                              data-tone={tone(value(selected, m, side))}
                              style={{
                                width: `${Math.min(100, (100 * Math.abs(value(selected, m, side) ?? 0)) / ((m === "gex" ? gmax : vmax) || 1))}%`,
                              }}
                            />
                          </span>
                        </div>
                      ))}
                    </div>
                    <div className="os-map__detail-money">
                      <div>
                        <small>
                          {translate(
                            locale,
                            "research.optionstructuremap.turnoverProviderReportedUsd",
                          )}
                        </small>
                        <strong>
                          {money(value(selected, "turnover", side), locale)}
                        </strong>
                      </div>
                      <dl>
                        <dt>
                          {translate(locale, "research.optionpulsechart.callTurnover")}
                        </dt>
                        <dd>{money(selected.call?.turnover, locale)}</dd>
                        <dt>
                          {translate(locale, "research.optionpulsechart.putTurnover")}
                        </dt>
                        <dd>{money(selected.put?.turnover, locale)}</dd>
                      </dl>
                    </div>
                    <dl className="os-map__facts">
                      <dt>
                        {translate(
                          locale,
                          "research.optionstructuremap.contractMultiplier",
                        )}
                      </dt>
                      <dd>
                        {multipliers.length
                          ? multipliers.join(" / ")
                          : translate(
                              locale,
                              "research.optionanalyticspanel.unconfirmed",
                            )}
                      </dd>
                      <dt>{translate(locale, "research.optionspanel.dataSources")}</dt>
                      <dd>
                        {translate(
                          locale,
                          "research.optionstructuremap.providerReportedTurnover",
                        )}
                      </dd>
                      <dt>{translate(locale, "research.optionstructuremap.reason")}</dt>
                      <dd>
                        {translate(
                          locale,
                          "research.optionstructuremap.modelEstimate.withValues",
                          {
                            selectedReasons:
                              selectedReasons
                                .map((r) => reasons(locale)[r] ?? r)
                                .join(locale === "en-US" ? "; " : "；") ||
                              translate(
                                locale,
                                "research.optionstructuremap.seeContractEvidenceForOiDates",
                              ),
                          },
                        )}
                      </dd>
                    </dl>
                    <p className="os-map__quality">
                      {translate(
                        locale,
                        "research.optionstructuremap.gexUsd1UnderlyingMoveVexUsd1Percentage2",
                      )}
                    </p>
                    <details>
                      <summary>
                        {translate(
                          locale,
                          "research.optionstructuremap.contractsAndRawTurnover",
                        )}
                      </summary>
                      <ul>
                        {selectedContracts.map((c) => (
                          <li key={c.contract_id}>
                            {translate(
                              locale,
                              "research.optionstructuremap.rawTurnover.withValues",
                              {
                                contractId: c.contract_id,
                                turnover: money(c.turnover, locale),
                                oiDate:
                                  c.oi_date ??
                                  translate(
                                    locale,
                                    "research.optionanalyticspanel.unconfirmed",
                                  ),
                              },
                            )}
                          </li>
                        ))}
                      </ul>
                    </details>
                  </>
                ) : (
                  <p>
                    {translate(
                      locale,
                      "research.optionstructuremap.clickACellForDetails",
                    )}
                  </p>
                )}
              </section>
              <section className="os-map__rank-panel">
                <h3>
                  {translate(
                    locale,
                    "research.optionstructuremap.turnoverConcentrationByContractExpiryWindow",
                  )}
                </h3>
                <div
                  className="os-map__rank-tabs"
                  aria-label={translate(
                    locale,
                    "research.optionstructuremap.rankingExpiryWindow",
                  )}
                >
                  {windows(locale).map(([v, label]) => (
                    <button
                      type="button"
                      key={v}
                      aria-pressed={rankWindow === v}
                      onClick={() => update({ option_rank_window: String(v) })}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <Ranking window={data.windows?.[String(rankWindow)]} side={side} />
                <p className="os-map__quality">
                  {translate(
                    locale,
                    "research.optionstructuremap.providerReportedTurnoverAnomalousRecordsExcluded",
                  )}
                </p>
              </section>
            </aside>
          </div>
          <section
            className="os-map__windows"
            aria-label={translate(
              locale,
              "research.optionstructuremap.turnoverComparisonAcrossFourExpiryWindows",
            )}
          >
            <h3>
              {translate(
                locale,
                "research.optionstructuremap.turnoverConcentrationByContractExpiryWindow",
              )}
            </h3>
            <div>
              {windows(locale).map(([v, label]) => (
                <section key={v}>
                  <h4>
                    {label} <small>(1–{v} DTE)</small>
                  </h4>
                  <Ranking window={data.windows?.[String(v)]} side={side} />
                </section>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
