import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useState } from "react";
import { Link } from "react-router-dom";

import type { components } from "../../api/generated/schema";
import { sectorLabel } from "../../app/presentation";
import {
  buildMarketResearchHref,
  formatReturn,
  symbolLabel,
  type MarketLinkState,
  type MarketPoint,
} from "./overview-terminal";

type SectorScorecard = components["schemas"]["SectorScorecard"];
type SectorHorizon = 1 | 5 | 20;

const HORIZONS = [1, 5, 20] as const;

interface SectorHealthTerminalProps {
  linkState: MarketLinkState;
  points: readonly MarketPoint[];
  scorecards: readonly SectorScorecard[];
}

export function SectorHealthTerminal({
  linkState,
  points,
  scorecards,
}: SectorHealthTerminalProps) {
  const { locale } = useI18n();
  const [horizon, setHorizon] = useState<SectorHorizon>(20);
  const spy = points.find((point) => point.symbol === "US.SPY");
  const sectorReturn = (card: SectorScorecard) =>
    horizon === 1
      ? card.median_session_return
      : horizon === 5
        ? card.median_return_5
        : card.median_return_20;
  const sorted = [...scorecards].sort((left, right) => {
    if (left.sample_count < 5 !== right.sample_count < 5) {
      return left.sample_count < 5 ? 1 : -1;
    }
    const returnDifference =
      (sectorReturn(right) ?? Number.NEGATIVE_INFINITY) -
      (sectorReturn(left) ?? Number.NEGATIVE_INFINITY);
    return returnDifference === 0
      ? left.sector.localeCompare(right.sector)
      : returnDifference;
  });
  const spyReturn =
    horizon === 1
      ? spy?.session_return
      : horizon === 5
        ? spy?.return_5
        : spy?.return_20;
  return (
    <section
      aria-label={translate(
        locale,
        "overview.SectorHealthTerminal.sectorHealthAndRelativeStrength",
      )}
      className="overview-terminal sector-health-terminal"
    >
      <header className="overview-terminal__header">
        <div>
          <span>
            {translate(locale, "overview.SectorHealthTerminal.sectorCrossSection")}
          </span>
          <h2>
            {translate(
              locale,
              "overview.SectorHealthTerminal.sectorHealthAndStrengthRelativeToSPY",
            )}
          </h2>
          <p>
            {translate(
              locale,
              "overview.SectorHealthTerminal.medianReturnsAndAnomalyCountsUseTheCurrentSnapshot",
            )}
          </p>
        </div>
        <div
          aria-label={translate(
            locale,
            "overview.SectorHealthTerminal.sectorReturnHorizon",
          )}
          className="sector-health-horizons"
          role="group"
        >
          {HORIZONS.map((value) => (
            <button
              aria-pressed={horizon === value}
              key={value}
              onClick={() => setHorizon(value)}
              type="button"
            >
              {value} {translate(locale, "overview.SectorHealthTerminal.days")}
            </button>
          ))}
        </div>
      </header>
      <div className="sector-health-grid">
        <div className="terminal-table-scroll">
          <table
            aria-label={translate(
              locale,
              "overview.SectorHealthTerminal.sectorHealthTable",
            )}
            className="terminal-data-table"
          >
            <thead>
              <tr>
                <th>{translate(locale, "overview.SectorRegimeMatrix.sector")}</th>
                <th>{translate(locale, "overview.SectorMarketMap.samples")}</th>
                <th>
                  {horizon}{" "}
                  {translate(locale, "overview.SectorHealthTerminal.dayMedianReturn")}
                </th>
                <th>
                  {translate(locale, "overview.SectorHealthTerminal.relativeToSPY")}
                </th>
                <th>
                  {translate(locale, "overview.SectorHealthTerminal.relativeStrength")}
                </th>
                <th>{translate(locale, "overview.SectorMarketMap.uptrend")}</th>
                <th>{translate(locale, "overview.SectorHealthTerminal.vsPrevious")}</th>
                <th>ATR</th>
                <th>
                  {translate(locale, "overview.SectorHealthTerminal.volumeBreakouts")}
                </th>
                <th>
                  {translate(locale, "overview.SectorHealthTerminal.topGainerLoser")}
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((card) => {
                const value = sectorReturn(card);
                const relative =
                  value == null || spyReturn == null ? null : value - spyReturn;
                const comparableSample = Math.min(
                  5,
                  card.sample_count,
                  card.previous_sample_count ?? 0,
                );
                return (
                  <tr key={card.sector}>
                    <th scope="row">{sectorLabel(card.sector, locale)}</th>
                    <td>{card.sample_count}</td>
                    <td>{formatReturn(value ?? null, locale)}</td>
                    <td>{formatReturn(relative, locale)}</td>
                    <td>
                      {card.sample_count < 5
                        ? translate(
                            locale,
                            "overview.SectorHealthTerminal.insufficientSamples5SectorStrengthNotAssessed",
                            { sampleCount: card.sample_count },
                          )
                        : relative == null
                          ? translate(
                              locale,
                              "overview.SectorHealthTerminal.insufficientEvidence",
                            )
                          : relative > 0
                            ? translate(
                                locale,
                                "overview.SectorHealthTerminal.relativelyStrong",
                              )
                            : relative < 0
                              ? translate(
                                  locale,
                                  "overview.SectorHealthTerminal.relativelyWeak",
                                )
                              : translate(
                                  locale,
                                  "overview.SectorHealthTerminal.relativelyUnchanged",
                                )}
                    </td>
                    <td>
                      {formatReturn(card.uptrend_fraction, locale)}{" "}
                      {translate(locale, "overview.SectorHealthTerminal.samples")}{" "}
                      {card.sample_count}
                    </td>
                    <td>
                      {card.uptrend_change == null
                        ? translate(
                            locale,
                            "overview.SectorHealthTerminal.insufficientSectorSamples5",
                            { comparableSample: comparableSample },
                          )
                        : `${card.uptrend_change > 0 ? "+" : ""}${(card.uptrend_change * 100).toFixed(1)}pp`}
                    </td>
                    <td>{formatReturn(card.median_atr_pct_20 ?? null, locale)}</td>
                    <td>
                      {card.volume_anomaly_count ??
                        translate(locale, "overview.SectorHealthTerminal.missing")}{" "}
                      /{" "}
                      {card.breakout_up_count == null
                        ? translate(locale, "overview.SectorHealthTerminal.missing")
                        : `${card.breakout_up_count}↑ ${card.breakout_down_count ?? 0}↓`}
                    </td>
                    <td>
                      {card.leader_symbol ? (
                        <Link
                          to={buildMarketResearchHref(card.leader_symbol, linkState)}
                        >
                          {symbolLabel(card.leader_symbol)}
                        </Link>
                      ) : (
                        translate(locale, "overview.SectorHealthTerminal.missing")
                      )}{" "}
                      {card.sample_count === 1 ? (
                        translate(
                          locale,
                          "overview.SectorHealthTerminal.singleInstrument",
                        )
                      ) : (
                        <>
                          {" "}
                          /{" "}
                          {card.laggard_symbol ? (
                            <Link
                              to={buildMarketResearchHref(
                                card.laggard_symbol,
                                linkState,
                              )}
                            >
                              {symbolLabel(card.laggard_symbol)}
                            </Link>
                          ) : (
                            translate(locale, "overview.SectorHealthTerminal.missing")
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
