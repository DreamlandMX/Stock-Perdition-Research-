import { instrumentName } from "../../i18n/instruments";
import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { optionReasonLabel } from "../options/option-labels";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { buildOptionPulseOption } from "../research/option-pulse-chart";
import {
  money,
  number,
  percent,
  signedPercent,
  termLabel,
  type OptionsBoardRow,
} from "../options/options-board-rows";
import { OverviewTerminalChart } from "./OverviewTerminalChart";
import type { OverviewHistory } from "./overview-history";
import {
  buildMarketResearchHref,
  type MarketLinkState,
  type MarketPoint,
} from "./overview-terminal";

export function OptionResearchCard({
  row,
  history,
  point,
  linkState,
}: {
  row: OptionsBoardRow;
  history: OverviewHistory;
  point: Pick<MarketPoint, "symbol" | "name"> | undefined;
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const navigate = useNavigate();
  const dailyCutoff = row.board.market_date ?? linkState.asOf?.slice(0, 10);
  const bars = useMemo(
    () =>
      (history.histories[row.symbol] ?? [])
        .filter((bar) => dailyCutoff == null || bar.at.slice(0, 10) <= dailyCutoff)
        .slice(-30),
    [history.histories, row.symbol, dailyCutoff],
  );
  const last = bars.at(-1),
    previous = bars.at(-2);
  const change =
    last && !last.data_gap && previous && !previous.data_gap
      ? last.close / previous.close - 1
      : null;
  const option = useMemo(() => {
    const spot = row.spot;
    const cone =
      spot == null
        ? []
        : [
            {
              at: row.board.market_date ?? "",
              lower68: spot,
              upper68: spot,
              lower80: spot,
              upper80: spot,
            },
            ...(row.overview?.implied_moves ?? [])
              .filter(
                (m) =>
                  m.horizon_days <= 10 &&
                  m.sigma_pct != null &&
                  m.lower_price != null &&
                  m.upper_price != null,
              )
              .map((m) => ({
                at: row.board.market_date ?? "",
                horizon_days: m.horizon_days,
                lower68: spot * (1 - m.sigma_pct!),
                upper68: spot * (1 + m.sigma_pct!),
                lower80: m.lower_price!,
                upper80: m.upper_price!,
              })),
          ];
    return buildOptionPulseOption({
      locale,
      bars,
      cone,
      profile: row.board.strike_profile.map((p) => ({
        strike: p[0],
        call: p[1],
        put: p[2],
      })),
      levels: {
        spot: spot ?? null,
        call: row.board.call_peak ?? null,
        put: row.board.put_peak ?? null,
      },
      timeframe: "1d",
      compact: true,
      profileUnit: "contracts",
    });
  }, [row, bars, locale]);
  const open = () =>
    void navigate(`${buildMarketResearchHref(row.symbol, linkState)}#options`);
  return (
    <article
      className="ob-research-card"
      tabIndex={0}
      role="link"
      aria-label={translate(locale, "overview.OptionResearchCard.openResearch", {
        s: row.s,
      })}
      onClick={(e) => {
        if (!(e.target as Element).closest("details")) open();
      }}
      onKeyDown={(e) => {
        if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          open();
        }
      }}
    >
      <header>
        <b>{row.s}</b>
        <span>{instrumentName(row.symbol, point?.name, locale)}</span>
        <strong
          title={
            row.spot == null
              ? translate(locale, "overview.OptionResearchCard.stockSnapshotClose")
              : translate(
                  locale,
                  "overview.OptionResearchCard.optionsCalculationReferencePrice",
                )
          }
        >
          {number(
            row.spot ?? (last && !last.data_gap ? last.close : null),
            undefined,
            locale,
          )}
        </strong>
        <span className={change == null ? "" : change >= 0 ? "ob-up" : "ob-down"}>
          {signedPercent(change, undefined, locale)}
        </span>
      </header>
      {(!row.available || row.board.reasons.length > 0) && (
        <p className="ob-note">
          {row.board.reasons
            .map((argument0) => optionReasonLabel(argument0, locale))
            .join(";") ||
            translate(locale, "options.optionsBoardRows.noSavedOptionsSnapshot")}
        </p>
      )}
      <div className="ob-card-chips">
        <span>
          IV <b>{percent(row.iv, undefined, locale)}</b>
        </span>
        <span>
          {translate(locale, "overview.SectorMarketMap.5Days")}{" "}
          <b>{row.m5 == null ? "—" : `±${percent(row.m5, undefined, locale)}`}</b>
        </span>
        <span>
          P/C{" "}
          <b className={row.pc != null && row.pc > 1 ? "ob-warning" : ""}>
            {number(row.pc, undefined, locale)}
          </b>
        </span>
        <span>
          {translate(locale, "overview.OptionResearchCard.turnover")}{" "}
          <b>{money(row.prem, locale)}</b>
        </span>
        <span className={row.term === "inverted" ? "ob-warning" : ""}>
          {termLabel(row.term, locale)}
        </span>
      </div>
      {row.spot || bars.length > 0 ? (
        <OverviewTerminalChart
          title={translate(
            locale,
            "overview.OptionResearchCard.candlesImpliedIntervalsAndVolumeByStrike",
            { s: row.s },
          )}
          summary={
            row.available
              ? translate(
                  locale,
                  "overview.OptionResearchCard.last30DailyCandlesNext10Day6880",
                )
              : translate(
                  locale,
                  "overview.OptionResearchCard.savedStockDailyBarsOptionsDataUnavailable",
                )
          }
          option={option}
          tableLabel={translate(
            locale,
            "overview.OptionResearchCard.viewCardSourcesAndPrices",
          )}
        >
          <p>
            {row.available
              ? translate(
                  locale,
                  "overview.OptionResearchCard.optionsMarketDateSnapshot",
                  {
                    marketDate: row.board.market_date ?? "—",
                    snapshotId: row.board.snapshot_id ?? "—",
                  },
                )
              : translate(locale, "overview.OptionResearchCard.stockSnapshotCutoff", {
                  asOf:
                    linkState.asOf ??
                    translate(locale, "options.OptionRefreshControl.unconfirmed"),
                  why: row.look.why,
                })}
            {bars.length === 0
              ? translate(
                  locale,
                  "overview.OptionResearchCard.noDailyBarsForThisCutoffDateOnlyThe",
                )
              : translate(
                  locale,
                  "overview.OptionResearchCard.tradingDaysOfDailyBarsLoaded",
                  { count: bars.length },
                )}
          </p>
          <table>
            <thead>
              <tr>
                <th>{translate(locale, "overview.OptionResearchCard.dateStrike")}</th>
                <th>
                  {translate(locale, "overview.OptionResearchCard.closeCallVolume")}
                </th>
                <th>{translate(locale, "overview.OptionResearchCard.putVolume")}</th>
              </tr>
            </thead>
            <tbody>
              {bars.map((bar) => (
                <tr key={bar.at}>
                  <td>{bar.at.slice(0, 10)}</td>
                  <td>
                    {bar.data_gap
                      ? translate(locale, "overview.OptionResearchCard.dataGap")
                      : number(bar.close, undefined, locale)}
                  </td>
                  <td>—</td>
                </tr>
              ))}
              {row.board.strike_profile.map((bucket) => (
                <tr key={bucket[0]}>
                  <td>{number(bucket[0], undefined, locale)}</td>
                  <td>{number(bucket[1], 0, locale)}</td>
                  <td>{number(bucket[2], 0, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </OverviewTerminalChart>
      ) : (
        <p className="ob-empty">
          {translate(
            locale,
            "overview.OptionResearchCard.noEligibleOptionsSnapshotForThisInstrument",
          )}
        </p>
      )}
      <div className="ob-card-outlook">
        <span className={`ob-look ob-look--${row.look.k}`}>{row.look.t}</span>
        <span>
          {translate(locale, "overview.OptionResearchCard.1MonthUpGt5")}{" "}
          <b className="ob-up">{percent(row.pu5, 0, locale)}</b>{" "}
          {translate(locale, "overview.OptionResearchCard.downGt5")}{" "}
          <b className="ob-down">{percent(row.pd5, 0, locale)}</b>
        </span>
        <span>
          {translate(locale, "overview.OptionResearchCard.premiumTarget")}{" "}
          <b className="ob-up">{number(row.tgtC, 1, locale)}</b>{" "}
          {translate(locale, "overview.OptionResearchCard.protection")}{" "}
          <b className="ob-down">{number(row.tgtP, 1, locale)}</b>
        </span>
      </div>
      <footer>
        <span>
          {translate(locale, "overview.OptionResearchCard.putPeak")}{" "}
          <b className="ob-down">{number(row.board.put_peak, 0, locale)}</b>{" "}
          {translate(locale, "overview.OptionResearchCard.callPeak")}{" "}
          <b className="ob-up">{number(row.board.call_peak, 0, locale)}</b>
        </span>
        <span className="ob-model-badge">
          {translate(locale, "overview.OptionResearchCard.modelUnpublished")}
        </span>
      </footer>
    </article>
  );
}
