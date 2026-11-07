import { instrumentName } from "../../i18n/instruments";
import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo, type KeyboardEvent } from "react";
import { ArrowRight, ArrowUpRight, LayoutGrid, X } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

import type { components } from "../../api/generated/schema";
import { sectorLabel } from "../../app/presentation";
import {
  groupBySector,
  packSectorRows,
  REPRESENTATIVE_COUNT,
  selectRepresentatives,
  type SectorGroup,
} from "./overview-map";
import {
  atrTopDecile,
  buildMarketResearchHref,
  formatReturn,
  MARKET_RETURN_CAPS,
  marketReturnColor,
  marketReturnValue,
  returnScaleColor,
  symbolLabel,
  type MarketLinkState,
  type MarketPoint,
  type MarketReturnHorizon,
} from "./overview-terminal";
import { getRegimeLabels } from "./RegimeDistribution";
import { OverviewDailyChart } from "./OverviewHistoryPanels";
import { sparklinePath } from "./overview-history";
import { rankingBasisPresentation } from "./overview-insights";
import type { OverviewHistory, DailyBar } from "./overview-history";

type SectorScorecard = components["schemas"]["SectorScorecard"];
type ResearchRow = components["schemas"]["ResearchOpportunityRow"];

const LEGEND_STEPS = [-1, -2 / 3, -1 / 3, 0, 1 / 3, 2 / 3, 1] as const;

export interface SectorMarketMapProps {
  history?: OverviewHistory | undefined;
  allSymbolsHref: string;
  horizon: MarketReturnHorizon;
  linkState: MarketLinkState;
  onHorizonChange: (horizon: MarketReturnHorizon) => void;
  onSectorChange: (sector: string) => void;
  onSelect: (symbol: string) => void;
  points: readonly MarketPoint[];
  researchRows: readonly ResearchRow[];
  scorecards: readonly SectorScorecard[];
  sector: string;
  selectedSymbol: string | null;
}

function signedPercent(value: number | null | undefined, digits = 1) {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value * 100).toFixed(digits)}%`;
}

function plainPercent(value: number | null | undefined) {
  return value == null || !Number.isFinite(value)
    ? "—"
    : `${(value * 100).toFixed(0)}%`;
}

function toneOf(value: number | null | undefined) {
  if (value == null || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}

function sectorReturn(card: SectorScorecard | undefined, horizon: MarketReturnHorizon) {
  if (card === undefined) return null;
  if (horizon === 1) return card.median_session_return ?? null;
  if (horizon === 5) return card.median_return_5 ?? null;
  return card.median_return_20 ?? null;
}

function focusSibling(event: KeyboardEvent<HTMLElement>) {
  const keys = ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"];
  if (!keys.includes(event.key)) return;
  const grid = event.currentTarget.closest("[data-map-grid]");
  if (grid === null) return;
  const cells = Array.from(grid.querySelectorAll<HTMLButtonElement>("[data-map-cell]"));
  const index = cells.indexOf(event.currentTarget as HTMLButtonElement);
  const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
  const next = cells[index + step];
  if (next !== undefined) {
    event.preventDefault();
    next.focus();
  }
}

function MapCell({
  bars,
  calendar,
  horizon,
  onSelect,
  point,
  selected,
}: {
  bars?: readonly DailyBar[] | undefined;
  calendar?: readonly string[] | undefined;
  horizon: MarketReturnHorizon;
  onSelect: (symbol: string) => void;
  point: MarketPoint;
  selected: boolean;
}) {
  const { locale } = useI18n();
  const color = marketReturnColor(point, horizon);
  const flags = [
    point.volume_z_20 != null && Math.abs(point.volume_z_20) >= 2
      ? translate(locale, "overview.SectorMarketMap.vol")
      : null,
    point.breakout_up_rule
      ? translate(locale, "overview.SectorMarketMap.break")
      : point.breakout_down_rule
        ? translate(locale, "overview.SectorMarketMap.break2")
        : null,
    point.regime_changed ? translate(locale, "overview.SectorMarketMap.shift") : null,
  ].filter((flag): flag is string => flag !== null);
  return (
    <button
      aria-label={translate(locale, "overview.SectorMarketMap.dayReturn2", {
        symbol: symbolLabel(point.symbol),
        value: getRegimeLabels(locale)[point.regime],
        horizon: horizon,
        returnValue: formatReturn(color.value, locale),
        value2: flags.length > 0 ? `,${flags.join(", ")}` : "",
      })}
      aria-pressed={selected}
      className="ov-map-cell"
      data-map-cell=""
      data-selected={selected}
      onClick={() => onSelect(point.symbol)}
      onKeyDown={focusSibling}
      style={{ background: color.background }}
      type="button"
    >
      <span className="ov-map-cell__symbol">{symbolLabel(point.symbol)}</span>
      <span className="ov-map-cell__return">{formatReturn(color.value, locale)}</span>
      <span className="ov-map-cell__meta">
        <span>{getRegimeLabels(locale)[point.regime]}</span>
        {flags.length > 0 ? <em>{flags.join(" ")}</em> : null}
      </span>
      {bars && bars.length >= 2 ? (
        <svg
          className="ov-map-spark"
          aria-hidden="true"
          viewBox="0 0 100 20"
          preserveAspectRatio="none"
        >
          <path
            fill="none"
            stroke="currentColor"
            strokeWidth="1"
            d={sparklinePath(bars.slice(-20), 100, 20, calendar)}
          />
        </svg>
      ) : null}
    </button>
  );
}

function SectorHeader({
  card,
  group,
  horizon,
  onSectorChange,
}: {
  card: SectorScorecard | undefined;
  group: SectorGroup;
  horizon: MarketReturnHorizon;
  onSectorChange: (sector: string) => void;
}) {
  const { locale } = useI18n();
  const value = sectorReturn(card, horizon);
  return (
    <button
      className="ov-map-sector__header"
      onClick={() => onSectorChange(group.sector)}
      title={translate(locale, "overview.SectorMarketMap.showAllInstrumentsIn", {
        sector: sectorLabel(group.sector, locale),
        total: group.total,
      })}
      type="button"
    >
      <strong>{sectorLabel(group.sector, locale)}</strong>
      <span className={`ov-tone--${toneOf(value)}`}>{signedPercent(value)}</span>
      <small>
        {group.points.length}/{group.total}
      </small>
    </button>
  );
}

function Inspector({
  history,
  card,
  horizon,
  linkState,
  onSelect,
  peers,
  point,
  rank,
}: {
  history?: OverviewHistory | undefined;
  card: SectorScorecard | undefined;
  horizon: MarketReturnHorizon;
  linkState: MarketLinkState;
  onSelect: (symbol: string) => void;
  peers: readonly MarketPoint[];
  point: MarketPoint | undefined;
  rank: { index: number; row: ResearchRow } | null;
}) {
  const { locale } = useI18n();
  const location = useLocation();
  const rankBasis =
    rank?.row.rank_basis[0] === undefined
      ? null
      : rankingBasisPresentation(rank.row.rank_basis[0], locale);
  if (point === undefined) {
    return (
      <aside
        aria-label={translate(
          locale,
          "overview.SectorMarketMap.selectedInstrumentDetails",
        )}
        className="ov-inspector ov-inspector--empty"
      >
        <p>
          {translate(
            locale,
            "overview.SectorMarketMap.selectAnInstrumentOnTheMapToViewPrices",
          )}
        </p>
      </aside>
    );
  }
  const channel = point.channel_position_20 ?? null;
  const channelClamped = channel === null ? null : Math.max(0, Math.min(1, channel));
  const metrics: readonly [string, string, string?][] = [
    [
      translate(locale, "overview.SectorMarketMap.1Day"),
      signedPercent(point.session_return),
      toneOf(point.session_return),
    ],
    [
      translate(locale, "overview.SectorMarketMap.5Days"),
      signedPercent(point.return_5),
      toneOf(point.return_5),
    ],
    [
      translate(locale, "overview.SectorMarketMap.20Days"),
      signedPercent(point.return_20),
      toneOf(point.return_20),
    ],
    ["ATR 20", plainPercentFine(point.atr_pct_20)],
    [
      translate(locale, "overview.SectorMarketMap.volumeZ20"),
      point.volume_z_20 == null ? "—" : point.volume_z_20.toFixed(2),
    ],
    [
      translate(locale, "overview.SectorMarketMap.channelPosition"),
      channel === null ? "—" : `${(channel * 100).toFixed(0)}%`,
    ],
  ];
  return (
    <aside
      aria-label={translate(
        locale,
        "overview.SectorMarketMap.selectedInstrumentDetails",
      )}
      className="ov-inspector"
    >
      <header className="ov-inspector__identity">
        <div>
          <span className="ov-kicker">
            {translate(locale, "overview.SectorMarketMap.selectedInstrument")}{" "}
            {sectorLabel(point.sector, locale)}
          </span>
          <h3>{symbolLabel(point.symbol)}</h3>
          <p title={instrumentName(point.symbol, point.name, locale)}>{instrumentName(point.symbol, point.name, locale)}</p>
        </div>
        <div className="ov-inspector__price">
          <strong>
            {point.last_close == null ? "—" : point.last_close.toFixed(2)}
          </strong>
          <span className={`ov-tone--${toneOf(point.session_return)}`}>
            {signedPercent(point.session_return, 2)}
          </span>
        </div>
      </header>
      <div className="ov-inspector__regime">
        <span>
          {point.previous_regime
            ? getRegimeLabels(locale)[point.previous_regime]
            : translate(locale, "overview.SectorMarketMap.insufficientHistory")}
        </span>
        <ArrowRight aria-hidden="true" size={14} strokeWidth={1.75} />
        <strong>{getRegimeLabels(locale)[point.regime]}</strong>
        {point.regime_changed ? (
          <em>{translate(locale, "overview.SectorMarketMap.regimeChange")}</em>
        ) : null}
      </div>
      <div className="ov-inspector-chart">
        <small>
          {translate(locale, "overview.SectorMarketMap.last")}{" "}
          {history?.histories[point.symbol]?.slice(-126).length ?? 0}{" "}
          {translate(
            locale,
            "overview.SectorMarketMap.tradingDaysRawDailyBarsUnadjustedMedianSectorTrend",
          )}
        </small>
        <OverviewDailyChart
          bars={history?.histories[point.symbol]?.slice(-126) ?? []}
          history={history}
          point={point}
        />
      </div>
      <dl className="ov-inspector__metrics">
        {metrics.map(([label, value, tone]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd className={tone === undefined ? undefined : `ov-tone--${tone}`}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
      <div className="ov-inspector__channel">
        <div>
          <span>
            {translate(locale, "overview.SectorMarketMap.20DayChannelPosition")}
          </span>
          <strong>{channel === null ? "—" : `${(channel * 100).toFixed(0)}%`}</strong>
        </div>
        <div aria-hidden="true" className="ov-channel-track">
          {channelClamped === null ? null : (
            <i style={{ left: `${(channelClamped * 100).toFixed(1)}%` }} />
          )}
        </div>
        <small>
          {translate(
            locale,
            "overview.SectorMarketMap.0IsTheLowerChannelBoundary100IsThe",
          )}
          {channel !== null && (channel > 1 || channel < 0)
            ? translate(locale, "overview.SectorMarketMap.currentlyOutsideTheChannel")
            : ""}
        </small>
      </div>
      <section
        aria-label={translate(locale, "overview.SectorMarketMap.sector")}
        className="ov-inspector__sector"
      >
        <span className="ov-kicker">
          {translate(locale, "overview.SectorMarketMap.sector2")}{" "}
          {sectorLabel(point.sector, locale)}
        </span>
        <dl>
          <div>
            <dt>
              {horizon} {translate(locale, "overview.SectorMarketMap.dayMedian")}
            </dt>
            <dd className={`ov-tone--${toneOf(sectorReturn(card, horizon))}`}>
              {signedPercent(sectorReturn(card, horizon))}
            </dd>
          </div>
          <div>
            <dt>{translate(locale, "overview.SectorMarketMap.uptrend")}</dt>
            <dd>{plainPercent(card?.uptrend_fraction)}</dd>
          </div>
          <div>
            <dt>{translate(locale, "overview.SectorMarketMap.samples")}</dt>
            <dd>{card?.sample_count ?? "—"}</dd>
          </div>
        </dl>
        {peers.length === 0 ? (
          <p>
            {translate(
              locale,
              "overview.SectorMarketMap.noOtherInstrumentsInThisSector",
            )}
          </p>
        ) : (
          <ol
            aria-label={translate(
              locale,
              "overview.SectorMarketMap.sectorTopByDayReturn",
              { horizon: horizon, count: peers.length },
            )}
            className="ov-inspector__peers"
          >
            {peers.map((peer) => {
              const value = marketReturnValue(peer, horizon);
              return (
                <li key={peer.symbol}>
                  <button onClick={() => onSelect(peer.symbol)} type="button">
                    <strong>{symbolLabel(peer.symbol)}</strong>
                    <span className="ov-inspector__peer-bar" aria-hidden="true">
                      <i
                        className={`ov-inspector__peer-fill--${toneOf(value)}`}
                        style={{
                          width: `${Math.min(100, (Math.abs(value ?? 0) / MARKET_RETURN_CAPS[horizon]) * 100).toFixed(1)}%`,
                        }}
                      />
                    </span>
                    <span className={`ov-tone--${toneOf(value)}`}>
                      {formatReturn(value, locale)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        )}
      </section>
      <section
        aria-label={translate(locale, "overview.SectorMarketMap.researchRank")}
        className="ov-inspector__research"
      >
        {rank === null ? (
          <p>
            {translate(
              locale,
              "overview.SectorMarketMap.outsideTheCurrentResearchRankingFullPriceAndRule",
            )}
          </p>
        ) : (
          <div>
            <strong>
              {translate(locale, "overview.SectorMarketMap.researchRank2")}{" "}
              {rank.index + 1} {translate(locale, "overview.SectorMarketMap.position")}
            </strong>
            {rankBasis === null ? null : <span>{rankBasis.label}</span>}
            {rankBasis?.original && (
              <details>
                <summary>{translate(locale, "common.originalDetails")}</summary>
                <p>{rankBasis.original}</p>
              </details>
            )}
          </div>
        )}
        <small>
          {translate(
            locale,
            "overview.SectorMarketMap.researchResultsAreNonExecutableAndDoNotConstitute",
          )}
        </small>
      </section>
      <Link
        className="ov-inspector__cta"
        state={{ returnTo: `${location.pathname}${location.search}` }}
        to={buildMarketResearchHref(point.symbol, linkState)}
      >
        {translate(locale, "overview.SectorMarketMap.viewInstrumentResearch")}
        <ArrowUpRight aria-hidden="true" size={16} strokeWidth={1.75} />
      </Link>
    </aside>
  );
}

function plainPercentFine(value: number | null | undefined) {
  return value == null || !Number.isFinite(value)
    ? "—"
    : `${(value * 100).toFixed(1)}%`;
}

export function SectorMarketMap({
  history,
  allSymbolsHref,
  horizon,
  linkState,
  onHorizonChange,
  onSectorChange,
  onSelect,
  points,
  researchRows,
  scorecards,
  sector,
  selectedSymbol,
}: SectorMarketMapProps) {
  const { locale } = useI18n();
  const atrCutoff = useMemo(() => atrTopDecile(points), [points]);
  const cards = useMemo(
    () => new Map(scorecards.map((card) => [card.sector, card] as const)),
    [scorecards],
  );
  const representatives = useMemo(
    () =>
      selectRepresentatives(points, horizon, atrCutoff).map((group) => {
        const score = (p: MarketPoint) =>
          (p.breakout_up_rule || p.breakout_down_rule ? 2 : 0) +
          (Math.abs(p.volume_z_20 ?? 0) >= 2 ? 1.5 : 0) +
          (p.regime_changed ? 1 : 0) +
          Math.abs(p.session_return ?? 0) * 20;
        return {
          ...group,
          points: [...(groupBySector(points).get(group.sector) ?? [])]
            .sort((a, b) => score(b) - score(a) || a.symbol.localeCompare(b.symbol))
            .slice(0, group.points.length)
            .sort(
              (a, b) =>
                (marketReturnValue(b, horizon) ?? -Infinity) -
                  (marketReturnValue(a, horizon) ?? -Infinity) ||
                a.symbol.localeCompare(b.symbol),
            ),
        };
      }),
    [atrCutoff, horizon, points],
  );
  const packed = useMemo(() => packSectorRows(representatives), [representatives]);
  const sectorPoints = useMemo(() => {
    if (sector === "all") return null;
    return [...(groupBySector(points).get(sector) ?? [])].sort(
      (left, right) =>
        (marketReturnValue(right, horizon) ?? Number.NEGATIVE_INFINITY) -
          (marketReturnValue(left, horizon) ?? Number.NEGATIVE_INFINITY) ||
        left.symbol.localeCompare(right.symbol),
    );
  }, [horizon, points, sector]);
  const shownCount =
    sectorPoints?.length ??
    representatives.reduce((sum, group) => sum + group.points.length, 0);
  const visible = sectorPoints ?? representatives.flatMap((group) => group.points);
  const selected =
    points.find((point) => point.symbol === selectedSymbol) ??
    researchRows
      .map((row) => visible.find((point) => point.symbol === row.symbol))
      .find((point) => point !== undefined) ??
    visible[0];
  const rankIndex =
    selected === undefined
      ? -1
      : researchRows.findIndex((row) => row.symbol === selected.symbol);
  const rankRow = rankIndex === -1 ? undefined : researchRows[rankIndex];
  const peers =
    selected === undefined
      ? []
      : [...(groupBySector(points).get(selected.sector) ?? [])]
          .filter((point) => point.symbol !== selected.symbol)
          .sort(
            (left, right) =>
              (marketReturnValue(right, horizon) ?? Number.NEGATIVE_INFINITY) -
                (marketReturnValue(left, horizon) ?? Number.NEGATIVE_INFINITY) ||
              left.symbol.localeCompare(right.symbol),
          )
          .slice(0, 5);
  const cap = MARKET_RETURN_CAPS[horizon];
  const sectors = [...groupBySector(points).entries()].sort(
    (left, right) => right[1].length - left[1].length,
  );

  return (
    <section
      aria-label={
        sectorPoints === null
          ? translate(
              locale,
              "overview.SectorMarketMap.marketMapRepresentativeInstruments",
              { shownCount: shownCount },
            )
          : translate(locale, "overview.SectorMarketMap.marketMapAllInstruments", {
              sector: sectorLabel(sector, locale),
              shownCount: shownCount,
            })
      }
      className="ov-hero"
    >
      <div className="ov-map">
        <header className="ov-map__header">
          <div className="ov-map__title">
            <h2>
              {sectorPoints === null
                ? translate(
                    locale,
                    "overview.SectorMarketMap.marketMapRepresentativeInstruments2",
                    { shownCount: shownCount },
                  )
                : translate(locale, "overview.SectorMarketMap.allInstruments", {
                    sector: sectorLabel(sector, locale),
                    shownCount: shownCount,
                  })}
            </h2>
            <span className="ov-badge">
              {translate(
                locale,
                "overview.SectorMarketMap.equalAreaNotMarketCapWeighted",
              )}
            </span>
          </div>
          <div className="ov-map__controls">
            <label className="ov-select">
              <span>{translate(locale, "overview.SectorRegimeMatrix.sector")}</span>
              <select
                onChange={(event) => onSectorChange(event.target.value)}
                value={sector}
              >
                <option value="all">
                  {translate(locale, "overview.SectorMarketMap.allSectors")}{" "}
                  {REPRESENTATIVE_COUNT}{" "}
                  {translate(locale, "overview.SectorMarketMap.representatives")}
                </option>
                {sectors.map(([key, members]) => (
                  <option key={key} value={key}>
                    {sectorLabel(key, locale)}({members.length})
                  </option>
                ))}
              </select>
            </label>
            <div
              aria-label={translate(
                locale,
                "overview.SectorMarketMap.mapReturnHorizon",
              )}
              className="ov-segmented"
              role="group"
            >
              {([1, 5, 20] as const).map((value) => (
                <button
                  aria-pressed={horizon === value}
                  key={value}
                  onClick={() => onHorizonChange(value)}
                  type="button"
                >
                  {value} {translate(locale, "overview.SectorMarketMap.dayReturn")}
                </button>
              ))}
            </div>
          </div>
        </header>
        {sectorPoints === null ? (
          <div
            className="ov-map__grid"
            data-map-grid=""
            style={{
              gridTemplateColumns: `repeat(${packed.columns}, minmax(0, 1fr))`,
              gridTemplateRows: `repeat(${packed.lines}, auto)`,
            }}
          >
            {packed.blocks.map((block) => {
              const empty = block.span - block.group.points.length;
              const hidden = block.group.total - block.group.points.length;
              return (
                <section
                  aria-label={translate(locale, "overview.SectorMarketMap.showing", {
                    sector: sectorLabel(block.group.sector, locale),
                    count: block.group.points.length,
                    total: block.group.total,
                  })}
                  className="ov-map-sector"
                  data-active={selected?.sector === block.group.sector}
                  key={`${block.group.sector}-${block.line}-${block.column}`}
                  style={{
                    gridColumn: `${block.column} / span ${block.span}`,
                    gridRow: block.line + 1,
                    gridTemplateColumns: `repeat(${block.span}, minmax(0, 1fr))`,
                  }}
                >
                  <SectorHeader
                    card={cards.get(block.group.sector)}
                    group={block.group}
                    horizon={horizon}
                    onSectorChange={onSectorChange}
                  />
                  {block.group.points.map((point) => (
                    <MapCell
                      bars={history?.histories[point.symbol]}
                      calendar={history?.dates}
                      horizon={horizon}
                      key={point.symbol}
                      onSelect={onSelect}
                      point={point}
                      selected={point.symbol === selected?.symbol}
                    />
                  ))}
                  {empty > 0 ? (
                    hidden > 0 ? (
                      <button
                        className="ov-map-more"
                        onClick={() => onSectorChange(block.group.sector)}
                        type="button"
                      >
                        <strong>+{hidden}</strong>
                        <span>
                          {translate(
                            locale,
                            "overview.SectorMarketMap.viewAllInSector",
                          )}
                        </span>
                      </button>
                    ) : (
                      <div className="ov-map-more ov-map-more--static">
                        <span>
                          {translate(locale, "overview.SectorMarketMap.sectorTotal")}{" "}
                          {block.group.total}{" "}
                          {translate(locale, "overview.SectorRegimeMatrix.instruments")}
                        </span>
                      </div>
                    )
                  ) : null}
                </section>
              );
            })}
            {packed.meta.span > 0 ? (
              <div
                className="ov-map-meta"
                style={{
                  gridColumn: `${packed.meta.column} / span ${packed.meta.span}`,
                  gridRow: packed.meta.line + 1,
                }}
              >
                <span className="ov-map-meta__count">
                  <strong>{points.length}</strong>
                  <small>
                    {translate(locale, "overview.SectorMarketMap.instruments")}
                  </small>
                </span>
                <span className="ov-map-meta__note">
                  {translate(
                    locale,
                    "overview.SectorMarketMap.equalAreaNotMarketCapWeighted2",
                  )}
                </span>
                <Link
                  aria-label={translate(
                    locale,
                    "overview.SectorMarketMap.viewAllInstruments",
                    { count: points.length },
                  )}
                  className="ov-link-button"
                  to={allSymbolsHref}
                >
                  <LayoutGrid aria-hidden="true" size={15} strokeWidth={1.75} />
                  {translate(locale, "overview.SectorMarketMap.viewAll")}
                </Link>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="ov-map__sector-view">
            <button
              className="ov-link-button ov-map__back"
              onClick={() => onSectorChange("all")}
              type="button"
            >
              <X aria-hidden="true" size={15} strokeWidth={1.75} />
              {translate(locale, "overview.SectorMarketMap.backTo")}{" "}
              {REPRESENTATIVE_COUNT}{" "}
              {translate(locale, "overview.SectorMarketMap.representativeView")}
            </button>
            <div className="ov-map__flat-grid" data-map-grid="">
              {sectorPoints.map((point) => (
                <MapCell
                  bars={history?.histories[point.symbol]}
                  calendar={history?.dates}
                  horizon={horizon}
                  key={point.symbol}
                  onSelect={onSelect}
                  point={point}
                  selected={point.symbol === selected?.symbol}
                />
              ))}
            </div>
          </div>
        )}
        <footer className="ov-map__legend">
          <div
            aria-label={translate(
              locale,
              "overview.SectorMarketMap.dayReturnColorScaleClippedAt",
              { horizon: horizon, value: (cap * 100).toFixed(0) },
            )}
            className="ov-scale"
            role="img"
          >
            <span>−{(cap * 100).toFixed(0)}%</span>
            {LEGEND_STEPS.map((step) => (
              <i key={step} style={{ background: returnScaleColor(step * cap, cap) }} />
            ))}
            <span>+{(cap * 100).toFixed(0)}%</span>
          </div>
          <span>
            {translate(locale, "overview.SectorMarketMap.greenGainsRedLossesBeyond")}
            {(cap * 100).toFixed(0)}
            {translate(locale, "overview.SectorMarketMap.usesEndpointColors")}
          </span>
          <span className="ov-map__flags">
            {translate(
              locale,
              "overview.SectorMarketMap.volVolumeZ202BreakBreakRuleBreakoutShift",
            )}
          </span>
          {sectorPoints === null ? (
            <span className="ov-map__flags">
              {translate(
                locale,
                "overview.SectorMarketMap.selection4PerSectorInitiallyRankedByAnomalyScore",
              )}{" "}
              {horizon}{" "}
              {translate(
                locale,
                "overview.SectorMarketMap.dayAbsoluteReturnUnusedSlotsGoToLargerSectors",
              )}
            </span>
          ) : null}
        </footer>
      </div>
      <Inspector
        history={history}
        card={selected === undefined ? undefined : cards.get(selected.sector)}
        horizon={horizon}
        linkState={linkState}
        onSelect={onSelect}
        peers={peers}
        point={selected}
        rank={rankRow === undefined ? null : { index: rankIndex, row: rankRow }}
      />
    </section>
  );
}
