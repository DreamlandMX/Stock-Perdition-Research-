import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { sectorLabel } from "../../app/presentation";

import type { MarketLinkState, MarketMapState, MarketPoint } from "./overview-terminal";
import {
  anomalyScore,
  atrTopDecile,
  buildMarketResearchHref,
  formatReturn,
  marketReturnColor,
  marketReturnValue,
  MARKET_RETURN_CAPS,
  symbolLabel,
} from "./overview-terminal";

interface MarketMapTerminalProps {
  linkState: MarketLinkState;
  mapState: MarketMapState;
  onHorizonChange: (horizon: 1 | 5 | 20) => void;
  onSectorChange: (sector: string) => void;
  points: readonly MarketPoint[];
}

const getRegimeLabels = (locale: Locale = "zh-CN") =>
  ({
    downtrend: translate(locale, "overview.RegimeDistribution.downtrend"),
    invalid: translate(locale, "overview.RegimeDistribution.regimeUndetermined"),
    range: translate(locale, "overview.RegimeDistribution.range"),
    transition: translate(locale, "overview.RegimeDistribution.directionUnconfirmed"),
    uptrend: translate(locale, "overview.SectorMarketMap.uptrend"),
  }) as const;

function topBy(
  points: readonly MarketPoint[],
  value: (point: MarketPoint) => number | null,
  direction: "asc" | "desc" = "desc",
) {
  return [...points]
    .filter((point) => value(point) !== null)
    .sort((left, right) => {
      const difference = (value(right) ?? 0) - (value(left) ?? 0);
      return direction === "desc" ? difference : -difference;
    })
    .slice(0, 5);
}

export function MarketMapTerminal({
  linkState,
  mapState,
  onHorizonChange,
  onSectorChange,
  points,
}: MarketMapTerminalProps) {
  const { locale } = useI18n();
  const location = useLocation();
  const returnTo = `${location.pathname}${location.search}`;
  const [expanded, setExpanded] = useState(false);
  const atrCutoff = useMemo(() => atrTopDecile(points), [points]);
  const sectors = useMemo(
    () => [...new Set(points.map((point) => point.sector))].sort(),
    [points],
  );
  const filtered = useMemo(
    () =>
      points
        .filter(
          (point) => mapState.sector === "all" || point.sector === mapState.sector,
        )
        .sort(
          (left, right) =>
            left.sector.localeCompare(right.sector) ||
            anomalyScore(right, atrCutoff) - anomalyScore(left, atrCutoff) ||
            left.symbol.localeCompare(right.symbol),
        ),
    [atrCutoff, mapState.sector, points],
  );
  const grouped = useMemo(() => {
    const result = new Map<string, MarketPoint[]>();
    for (const point of filtered) {
      const sectorPoints = result.get(point.sector) ?? [];
      sectorPoints.push(point);
      result.set(point.sector, sectorPoints);
    }
    return result;
  }, [filtered]);
  const mobileRanks = new Map(
    [...filtered]
      .sort(
        (left, right) =>
          anomalyScore(right, atrCutoff) - anomalyScore(left, atrCutoff) ||
          Math.abs(marketReturnValue(right, mapState.horizon) ?? 0) -
            Math.abs(marketReturnValue(left, mapState.horizon) ?? 0) ||
          left.symbol.localeCompare(right.symbol),
      )
      .map((point, index) => [point.symbol, index]),
  );
  const focusLists = [
    {
      label: translate(locale, "overview.OverviewHistoryPanels.topGainers"),
      points: topBy(filtered, (point) => marketReturnValue(point, mapState.horizon)),
      value: (point: MarketPoint) =>
        formatReturn(marketReturnValue(point, mapState.horizon), locale),
    },
    {
      label: translate(locale, "overview.OverviewHistoryPanels.topLosers"),
      points: topBy(
        filtered,
        (point) => marketReturnValue(point, mapState.horizon),
        "asc",
      ),
      value: (point: MarketPoint) =>
        formatReturn(marketReturnValue(point, mapState.horizon), locale),
    },
    {
      label: translate(locale, "overview.OverviewHistoryPanels.unusualVolume"),
      points: topBy(filtered, (point) =>
        point.volume_z_20 == null ? null : Math.abs(point.volume_z_20),
      ),
      value: (point: MarketPoint) =>
        `Z ${point.volume_z_20?.toFixed(2) ?? translate(locale, "overview.SectorHealthTerminal.missing")}`,
    },
    {
      label: translate(locale, "overview.OverviewPulse.ruleBreakout"),
      points: topBy(
        filtered.filter((point) => point.breakout_up_rule || point.breakout_down_rule),
        (point) => point.return_20 ?? null,
      ),
      value: (point: MarketPoint) =>
        point.breakout_up_rule
          ? translate(locale, "overview.OverviewHistoryPanels.upwardBreakout")
          : translate(locale, "overview.OverviewHistoryPanels.downwardBreakout"),
    },
    {
      label: translate(locale, "overview.SectorMarketMap.regimeChange"),
      points: topBy(
        filtered.filter((point) => point.regime_changed),
        (point) =>
          point.session_return == null ? null : Math.abs(point.session_return),
      ),
      value: (point: MarketPoint) =>
        `${point.previous_regime ? getRegimeLabels(locale)[point.previous_regime] : translate(locale, "overview.SectorMarketMap.insufficientHistory")} → ${getRegimeLabels(locale)[point.regime]}`,
    },
  ];

  return (
    <section
      aria-label={translate(locale, "overview.MarketMapTerminal.marketMapInstruments", {
        count: points.length,
      })}
      className="overview-terminal market-map-terminal"
    >
      <header className="overview-terminal__header">
        <div>
          <span>
            {translate(locale, "overview.MarketMapTerminal.fullCrossSection")}
          </span>
          <h2>
            {points.length}{" "}
            {translate(locale, "overview.MarketMapTerminal.instrumentMarketMap")}
          </h2>
          <p>
            {translate(
              locale,
              "overview.MarketMapTerminal.equalAreaCellsDoNotRepresentMarketCapWeights",
            )}
          </p>
        </div>
        <div className="market-map-controls">
          <div
            aria-label={translate(locale, "overview.SectorMarketMap.mapReturnHorizon")}
            className="segmented-control"
            role="group"
          >
            {([1, 5, 20] as const).map((horizon) => (
              <button
                aria-pressed={mapState.horizon === horizon}
                key={horizon}
                onClick={() => onHorizonChange(horizon)}
                type="button"
              >
                {horizon} {translate(locale, "overview.SectorMarketMap.dayReturn")}
              </button>
            ))}
          </div>
          <label>
            <span>{translate(locale, "overview.SectorRegimeMatrix.sector")}</span>
            <select
              value={mapState.sector}
              onChange={(event) => onSectorChange(event.target.value)}
            >
              <option value="all">
                {translate(locale, "overview.MarketMapTerminal.allSectors")}
              </option>
              {sectors.map((sector) => (
                <option key={sector} value={sector}>
                  {sectorLabel(sector, locale)}(
                  {points.filter((point) => point.sector === sector).length})
                </option>
              ))}
            </select>
          </label>
        </div>
      </header>
      <div className="market-map-layout">
        <div>
          <div
            className="market-map-legend"
            aria-label={translate(
              locale,
              "overview.MarketMapTerminal.fixedMapColorScale",
            )}
          >
            <span>
              {translate(locale, "overview.MarketMapTerminal.lossCap")}
              {(MARKET_RETURN_CAPS[mapState.horizon] * 100).toFixed(0)}%
            </span>
            <i aria-hidden="true" />
            <span>
              {translate(locale, "overview.MarketMapTerminal.gainCap")}
              {(MARKET_RETURN_CAPS[mapState.horizon] * 100).toFixed(0)}%
            </span>
          </div>
          <div className="market-map-grid" data-expanded={expanded}>
            {[...grouped.entries()].map(([sector, sectorPoints]) => (
              <section className="market-map-sector" key={sector}>
                <h3>
                  {sectorLabel(sector, locale)} ; {sectorPoints.length}{" "}
                  {translate(locale, "overview.SectorRegimeMatrix.instruments")}
                </h3>
                <div>
                  {sectorPoints.map((point) => {
                    const color = marketReturnColor(point, mapState.horizon);
                    const score = anomalyScore(point, atrCutoff);
                    return (
                      <Link
                        aria-label={translate(
                          locale,
                          "overview.MarketMapTerminal.dayReturnAnomalyScoreOpenInstrumentResearch",
                          {
                            symbol: symbolLabel(point.symbol),
                            value: getRegimeLabels(locale)[point.regime],
                            horizon: mapState.horizon,
                            returnValue: formatReturn(color.value, locale),
                            score: score,
                          },
                        )}
                        className={
                          (mobileRanks.get(point.symbol) ?? 0) >= 20
                            ? "market-map-cell market-map-cell--mobile-extra"
                            : "market-map-cell"
                        }
                        key={point.symbol}
                        style={{ background: color.background }}
                        to={buildMarketResearchHref(point.symbol, linkState)}
                        state={{ returnTo }}
                      >
                        <strong>{symbolLabel(point.symbol)}</strong>
                        <span>{formatReturn(color.value, locale)}</span>
                        <small>{getRegimeLabels(locale)[point.regime]}</small>
                        <em>
                          {point.volume_z_20 != null && Math.abs(point.volume_z_20) >= 2
                            ? translate(locale, "overview.SectorMarketMap.vol")
                            : ""}
                          {point.breakout_up_rule
                            ? translate(locale, "overview.SectorMarketMap.break")
                            : point.breakout_down_rule
                              ? translate(locale, "overview.SectorMarketMap.break2")
                              : ""}
                          {point.regime_changed
                            ? translate(locale, "overview.SectorMarketMap.shift")
                            : ""}
                        </em>
                      </Link>
                    );
                  })}
                </div>
              </section>
            ))}
          </div>
          {filtered.length > 20 ? (
            <button
              aria-expanded={expanded}
              className="market-map-expand"
              onClick={() => setExpanded((value) => !value)}
              type="button"
            >
              {expanded
                ? translate(
                    locale,
                    "overview.MarketMapTerminal.collapseToTheTop20Anomalies",
                  )
                : translate(locale, "overview.MarketMapTerminal.showAllInstruments", {
                    count: filtered.length,
                  })}
            </button>
          ) : null}
        </div>
        <aside
          aria-label={translate(
            locale,
            "overview.MarketMapTerminal.fiveMarketFocusLists",
          )}
          className="market-focus-board"
        >
          <p className="market-focus-board__context">
            {mapState.horizon}{" "}
            {translate(locale, "overview.MarketMapTerminal.dayReturn")}{" "}
            {mapState.sector === "all"
              ? translate(locale, "overview.MarketMapTerminal.allSectors")
              : sectorLabel(mapState.sector, locale)}{" "}
            ; {filtered.length}{" "}
            {translate(locale, "overview.MarketMapTerminal.samples")}
          </p>
          {focusLists.map((list) => (
            <section key={list.label}>
              <h3>{list.label} {translate(locale, "common.topFive")}</h3>
              {list.points.length === 0 ? (
                <p>
                  {translate(
                    locale,
                    "overview.MarketMoversBoard.noComparableEvidenceCurrently",
                  )}
                </p>
              ) : (
                <ol>
                  {list.points.map((point) => (
                    <li key={point.symbol}>
                      <Link
                        state={{ returnTo }}
                        to={buildMarketResearchHref(point.symbol, linkState)}
                      >
                        {symbolLabel(point.symbol)}
                      </Link>
                      <span>{list.value(point)}</span>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          ))}
        </aside>
      </div>
      <p className="market-map-classification">
        {translate(
          locale,
          "overview.MarketMapTerminal.sectorsUseTheSelectedSnapshotSPublishedClassificationUnclassified",
        )}{" "}
        {points.filter((point) => point.sector === "other").length}/{points.length}
        {translate(
          locale,
          "overview.MarketMapTerminal.eTFsFormASeparateAssetGroupSectorsAreNot",
        )}
      </p>
    </section>
  );
}
