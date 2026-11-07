import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  money,
  number,
  percent,
  type OptionsBoardRows,
} from "../options/options-board-rows";
import { buildMarketResearchHref, type MarketLinkState } from "./overview-terminal";

export function OptionsFlowTable({
  rows,
  linkState,
}: {
  rows: OptionsBoardRows;
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const [side, setSide] = useState("all"),
    [symbol, setSymbol] = useState("all"),
    [exclude, setExclude] = useState(true);
  const navigate = useNavigate(),
    open = (symbol: string) =>
      void navigate(`${buildMarketResearchHref(symbol, linkState)}#options`);
  const { list, hidden } = useMemo(() => {
    const matching = rows
      .flatMap((row) =>
        row.board.top_contracts.map((contract) => ({
          ...contract,
          symbol: row.symbol,
          s: row.s,
          itm: contract.side === "call" ? -contract.moneyness : contract.moneyness,
        })),
      )
      .filter(
        (contract) =>
          (side === "all" || contract.side === side) &&
          (symbol === "all" || contract.symbol === symbol),
      );
    return {
      hidden: exclude ? matching.filter((contract) => contract.itm > 0.1).length : 0,
      list: matching
        .filter((contract) => !exclude || contract.itm <= 0.1)
        .sort((a, b) => b.premium - a.premium)
        .slice(0, 15),
    };
  }, [rows, side, symbol, exclude]);
  const max = Math.max(1, ...list.map((c) => c.premium));
  return (
    <section className="ob-panel">
      <header>
        <div>
          <h3>{translate(locale, "overview.OptionsFlowTable.largestContracts")}</h3>
          <p>
            {symbol === "all"
              ? translate(locale, "overview.OptionsFlowTable.instruments", {
                  count: rows.length,
                })
              : symbol.replace(/^US\./, "")}
            {translate(locale, "overview.OptionsFlowTable.topEstimatedTurnover")}{" "}
            {list.length} {translate(locale, "overview.OptionsFlowTable.excluded")}{" "}
            {hidden} {translate(locale, "overview.OptionsFlowTable.deepITMContracts")}
          </p>
        </div>
        <div className="ob-flow-controls">
          <div
            className="ob-controls"
            role="group"
            aria-label={translate(
              locale,
              "overview.OptionsFlowTable.contractTypeFilter",
            )}
          >
            {["all", "call", "put"].map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={side === value}
                onClick={() => setSide(value)}
              >
                {value === "all"
                  ? translate(locale, "overview.OptionsIntentMix.all")
                  : value === "call"
                    ? "Call"
                    : "Put"}
              </button>
            ))}
          </div>
          <select
            aria-label={translate(
              locale,
              "overview.OptionsFlowTable.largeContractInstrument",
            )}
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
          >
            <option value="all">
              {translate(locale, "overview.OptionsFlowTable.allInstruments")}
            </option>
            {rows.map((row) => (
              <option key={row.symbol} value={row.symbol}>
                {row.s}
              </option>
            ))}
          </select>
          <label>
            <input
              type="checkbox"
              checked={exclude}
              onChange={(e) => setExclude(e.target.checked)}
            />
            {translate(locale, "overview.OptionsFlowTable.excludeDeepITMGt10")}
          </label>
        </div>
      </header>
      <div className="ob-scroll">
        <table className="ob-flow-table">
          <thead>
            <tr>
              {[
                translate(locale, "overview.OverviewPage.instrument"),
                translate(locale, "overview.OptionsFlowTable.type"),
                translate(locale, "overview.OptionsImpliedDistribution.strike"),
                translate(locale, "overview.OptionsFlowTable.expiry"),
                translate(locale, "overview.OptionsFlowTable.moneyness"),
                translate(locale, "overview.OptionsFlowTable.volume"),
                translate(locale, "overview.OptionsFlowTable.midpoint"),
                "IV",
                translate(locale, "overview.OptionsMarketTide.estimatedTurnover"),
              ].map((label) => (
                <th key={label}>{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {list.map((c) => (
              <tr
                key={`${c.symbol}-${c.contract_id}`}
                tabIndex={0}
                title={c.contract_id}
                onClick={() => open(c.symbol)}
                onKeyDown={(e) => e.key === "Enter" && open(c.symbol)}
              >
                <td>
                  <b>{c.s}</b>
                </td>
                <td>
                  <span
                    className={`ob-look ob-look--${c.side === "call" ? "bull" : "def"}`}
                  >
                    {c.side === "call" ? "Call" : "Put"}
                  </span>
                </td>
                <td>{number(c.strike, c.strike % 1 ? 1 : 0, locale)}</td>
                <td>
                  {c.expiration.slice(5)}{" "}
                  <small>
                    {c.dte}
                    {translate(locale, "overview.OptionsPulseTable.days")}
                  </small>
                </td>
                <td className={c.itm > 0 ? "ob-warning" : ""}>
                  {c.itm > 0
                    ? translate(locale, "overview.OptionsFlowTable.iTM")
                    : c.itm === 0
                      ? translate(locale, "overview.OptionsFlowTable.aTM")
                      : translate(locale, "overview.OptionsFlowTable.oTM")}{" "}
                  {percent(Math.abs(c.itm), undefined, locale)}
                </td>
                <td>{number(c.volume, 0, locale)}</td>
                <td>{number(c.mid, undefined, locale)}</td>
                <td>{percent(c.iv, undefined, locale)}</td>
                <td>
                  <div className="ob-premium-cell">
                    <span>{money(c.premium, locale)}</span>
                    <span className="ob-inline-bar">
                      <i
                        style={{
                          width: `${(c.premium / max) * 100}%`,
                          background:
                            c.side === "call" ? "var(--color-up)" : "var(--color-down)",
                        }}
                      />
                    </span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {list.length === 0 && (
          <p className="ob-note">
            {translate(
              locale,
              "overview.OptionsFlowTable.noEligibleContractsMatchTheCurrentFilters",
            )}
          </p>
        )}
      </div>
      <p className="ob-note">
        {translate(
          locale,
          "overview.OptionsFlowTable.theTop20CallsAndPutsByEstimatedTurnover",
        )}
      </p>
    </section>
  );
}
