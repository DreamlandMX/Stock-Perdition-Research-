import { instrumentName } from "../../i18n/instruments";
import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo, useState } from "react";
import type { OptionBoardResponse } from "../options/useOptionsBoard";
import type { OptionOverviewItem } from "../options/useOptionsOverview";
import { buildOptionsBoardRows } from "../options/options-board-rows";
import { optionReasonLabel } from "../options/option-labels";
import type { OverviewHistory } from "./overview-history";
import type { MarketLinkState, MarketPoint } from "./overview-terminal";
import { OptionsVerdict } from "./OptionsVerdict";
import { OptionsMarketTide } from "./OptionsMarketTide";
import { OptionsOutlookTable } from "./OptionsOutlookTable";
import { OptionsImpliedDistribution } from "./OptionsImpliedDistribution";
import { OptionResearchCard } from "./OptionResearchCard";
import { OptionsIntentMix } from "./OptionsIntentMix";
import { OptionsMoneyTargets } from "./OptionsMoneyTargets";
import { OptionsMoveLadder } from "./OptionsMoveLadder";
import { OptionsIvHvRank } from "./OptionsIvHvRank";
import { OptionsSkewRank } from "./OptionsSkewRank";
import { OptionsExpiryMix } from "./OptionsExpiryMix";
import { OptionsFlowTable } from "./OptionsFlowTable";
import { OptionsDetailTable } from "./OptionsDetailTable";
import "./options-board.css";

export function OptionsBoard({
  board,
  items,
  points,
  history,
  linkState,
}: {
  board: OptionBoardResponse;
  items: OptionOverviewItem[] | undefined;
  points: readonly Pick<MarketPoint, "symbol" | "name">[];
  history: OverviewHistory;
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const rows = useMemo(
    () => buildOptionsBoardRows(board.items, items ?? [], locale),
    [board.items, items, locale],
  );
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState("US.SPY");
  const [sort, setSort] = useState<"iv" | "prem" | "m5" | "pc" | "none">("iv");
  const selectedRow =
    rows.find((row) => row.symbol === selected) ??
    rows.find((row) => row.s === "SPY") ??
    rows[0];
  const cards =
    sort === "none"
      ? rows
      : [...rows].sort((a, b) => (b[sort] ?? -Infinity) - (a[sort] ?? -Infinity));
  const filtered = cards.filter((row) =>
    `${row.symbol} ${points.find((point) => point.symbol === row.symbol)?.name ?? ""} ${instrumentName(row.symbol, points.find((point) => point.symbol === row.symbol)?.name, locale)}`
      .toLocaleLowerCase()
      .includes(search.trim().toLocaleLowerCase()),
  );
  const pageCount = Math.max(1, Math.ceil(filtered.length / 24));
  const currentPage = Math.min(page, pageCount);
  const visibleCards = filtered.slice((currentPage - 1) * 24, currentPage * 24);
  const availableCount = rows.filter((row) => row.available).length;
  const failedCount = rows.filter(
    (row) =>
      !row.available &&
      row.board.reasons.some(
        (reason) =>
          !["option_snapshot_unavailable", "no_complete_snapshot_available"].includes(
            reason,
          ),
      ),
  ).length;
  return (
    <div className="options-board">
      <p className="ob-note" role="status">
        {translate(locale, "overview.OptionsBoard.universe")} {rows.length}{" "}
        {translate(locale, "overview.OptionsBoard.instrumentsSnapshotsAvailable")}{" "}
        {availableCount}
        {translate(locale, "overview.OptionsBoard.awaitingCollection")}{" "}
        {rows.length - availableCount - failedCount}
        {translate(
          locale,
          "overview.OptionsBoard.collectionFailedOrDataUnavailable",
        )}{" "}
        {failedCount}
        {translate(
          locale,
          "overview.OptionsBoard.aggregatesIncludeOnlyInstrumentsWithEligibleData",
        )}
      </p>
      <p className="ob-note">
        {translate(locale, "overview.OptionsBoard.optionsMarketDate")}
        {[
          ...new Set(
            board.items.flatMap((item) => (item.market_date ? [item.market_date] : [])),
          ),
        ].join(" / ") ||
          translate(locale, "options.OptionRefreshControl.unconfirmed")}{" "}
        ;{" "}
        {board.data_status === "available"
          ? translate(locale, "overview.OptionsBoard.snapshotAvailable")
          : board.data_status === "partial"
            ? translate(locale, "options.optionLabels.partialCoverage")
            : translate(locale, "overview.OptionsBoard.dataUnavailable")}{" "}
        {translate(locale, "overview.OptionsBoard.savedSnapshotReadOnlyResearch")}
        {board.reasons
          .map((argument0) => optionReasonLabel(argument0, locale))
          .join(";")}
      </p>
      <OptionsVerdict rows={rows} market={board.market} />
      <OptionsMarketTide rows={rows} market={board.market} />
      <div className="ob-outlook-row">
        <OptionsOutlookTable
          rows={rows}
          selected={selectedRow?.symbol ?? selected}
          onSelect={setSelected}
          linkState={linkState}
        />
        <OptionsImpliedDistribution row={selectedRow} />
      </div>
      {items && (
        <>
          <div className="ob-card-heading">
            <div>
              <h3>
                {rows.length}{" "}
                {translate(locale, "overview.OptionsBoard.instrumentOptionsPulse")}
              </h3>
              <p>
                {translate(
                  locale,
                  "overview.OptionsBoard.candlesImpliedIntervalsAndVolumeByStrikeClickTo",
                )}
              </p>
            </div>
            <div
              className="ob-controls"
              role="group"
              aria-label={translate(locale, "overview.OptionsBoard.optionsCardSorting")}
            >
              {(["none", "iv", "m5", "pc", "prem"] as const).map((key) => (
                <button
                  type="button"
                  key={key}
                  aria-pressed={sort === key}
                  onClick={() => {
                    setSort(key);
                    setPage(1);
                  }}
                >
                  {key === "iv"
                    ? translate(locale, "overview.OptionsBoard.byIV")
                    : key === "prem"
                      ? translate(locale, "overview.OptionsBoard.byTurnover")
                      : key === "m5"
                        ? translate(locale, "overview.OptionsBoard.by5DayInterval")
                        : key === "pc"
                          ? translate(locale, "overview.OptionsBoard.byPutCall")
                          : translate(locale, "overview.OptionsBoard.default")}
                </button>
              ))}
            </div>
          </div>
          <div className="ob-controls ob-card-tools">
            <label>
              {translate(locale, "overview.OptionsBoard.searchInstruments")}
              <input
                type="search"
                aria-label={translate(
                  locale,
                  "overview.OptionsBoard.searchOptionsInstruments",
                )}
                placeholder={translate(locale, "overview.OptionsBoard.tickerOrName")}
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(1);
                }}
              />
            </label>
            <span role="status">
              {translate(locale, "overview.OptionsBoard.matches")} {filtered.length}{" "}
              {translate(locale, "overview.OptionsBoard.page")} {currentPage}/
              {pageCount} {translate(locale, "overview.OptionsBoard.ofResults")}
            </span>
            <button
              type="button"
              disabled={currentPage === 1}
              onClick={() => setPage(currentPage - 1)}
            >
              {translate(locale, "overview.OptionsBoard.previousPage")}
            </button>
            <button
              type="button"
              disabled={currentPage === pageCount}
              onClick={() => setPage(currentPage + 1)}
            >
              {translate(locale, "overview.OptionsBoard.nextPage")}
            </button>
          </div>
          {filtered.length === 0 && (
            <p role="status">
              {translate(locale, "overview.OptionsBoard.noMatchingInstruments")}
            </p>
          )}
          <section
            className="ob-card-grid"
            aria-label={translate(
              locale,
              "overview.OptionsBoard.instrumentOptionsCards",
            )}
          >
            {visibleCards.map((row) => (
              <OptionResearchCard
                key={row.symbol}
                row={row}
                history={history}
                point={points.find((point) => point.symbol === row.symbol)}
                linkState={linkState}
              />
            ))}
          </section>
          <p className="ob-note">
            {translate(
              locale,
              "overview.OptionsBoard.cardGainLossColorsUseSavedDailyBarsPurple",
            )}
          </p>
        </>
      )}
      <div className="ob-pair">
        <OptionsIntentMix rows={rows} market={board.market} linkState={linkState} />
        <OptionsMoneyTargets rows={rows} linkState={linkState} />
      </div>
      <div className="ob-pair">
        {items && <OptionsMoveLadder rows={rows} linkState={linkState} />}
        <OptionsIvHvRank rows={rows} linkState={linkState} />
      </div>
      <div className="ob-pair">
        <OptionsSkewRank rows={rows} linkState={linkState} />
        <OptionsExpiryMix rows={rows} market={board.market} linkState={linkState} />
      </div>
      <OptionsFlowTable rows={rows} linkState={linkState} />
      {items && <OptionsDetailTable rows={rows} linkState={linkState} />}
      {board.items.some((item) => item.premium_missing_count > 0) && (
        <p className="ob-note ob-warning">
          {translate(
            locale,
            "overview.OptionsBoard.contractsWithIncompleteQuotesOrVolume",
          )}{" "}
          {board.items.reduce((sum, item) => sum + item.premium_missing_count, 0)}{" "}
          {translate(locale, "overview.OptionsBoard.excludedFromEstimatedTurnover")}
        </p>
      )}
      {board.items.some((item) => item.reasons.length > 0) && (
        <details className="ob-data-reasons">
          <summary>
            {translate(
              locale,
              "overview.OptionsBoard.viewInstrumentDataLimitsAndSources",
            )}
          </summary>
          {board.items.map((item) => (
            <p key={item.symbol}>
              <b>{item.symbol.replace(/^US\./, "")}</b> ;{" "}
              {item.market_date ??
                translate(locale, "overview.OptionsBoard.marketDateUnconfirmed")}{" "}
              ;{" "}
              {item.snapshot_id ??
                translate(locale, "overview.OptionsBoard.noSnapshot")}
              {item.reasons.length
                ? ` ; ${item.reasons.map((argument0) => optionReasonLabel(argument0, locale)).join(";")}`
                : translate(locale, "overview.OptionsBoard.dataAvailable")}
            </p>
          ))}
        </details>
      )}
    </div>
  );
}
