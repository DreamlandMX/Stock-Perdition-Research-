import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { OptionBoardItem, OptionBoardResponse } from "../options/useOptionsBoard";
import { money, ratio, type OptionsBoardRows } from "../options/options-board-rows";
import { OptionsStackBar } from "./OptionsExpiryMix";
import { buildMarketResearchHref, type MarketLinkState } from "./overview-terminal";

const keys = ["otm_call", "itm_call", "otm_put", "itm_put"] as const;
const getNames = (locale: Locale = "zh-CN") => [
  translate(locale, "overview.OptionsIntentMix.oTMCall"),
  translate(locale, "overview.OptionsIntentMix.iTMCall"),
  translate(locale, "overview.OptionsIntentMix.oTMPut"),
  translate(locale, "overview.OptionsIntentMix.iTMPut"),
];
const colors = [
  "var(--color-up)",
  "var(--color-intent-itm-call)",
  "var(--color-down)",
  "var(--color-intent-itm-put)",
];
export function OptionsIntentMix({
  rows,
  market,
  linkState,
}: {
  rows: OptionsBoardRows;
  market: OptionBoardResponse["market"];
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const [window, setWindow] = useState<"all" | "short" | "long">("all");
  const navigate = useNavigate(),
    open = (symbol: string) =>
      void navigate(`${buildMarketResearchHref(symbol, linkState)}#options`);
  const values = (intent: NonNullable<OptionBoardItem["intent"]>[string] | undefined) =>
    keys.map((key) => intent?.[key] ?? 0);
  const total = (intent: NonNullable<OptionBoardItem["intent"]>[string] | undefined) =>
    intent ? values(intent).reduce((a, b) => a + b, 0) : null;
  const list = [...rows].sort(
    (a, b) =>
      (ratio(b.board.intent?.[window]?.otm_call, total(b.board.intent?.[window])) ??
        -1) -
      (ratio(a.board.intent?.[window]?.otm_call, total(a.board.intent?.[window])) ??
        -1),
  );
  const bar = (intent: NonNullable<OptionBoardItem["intent"]>[string] | undefined) =>
    intent ? (
      <OptionsStackBar
        values={values(intent)}
        names={getNames(locale)}
        colors={colors}
        amount={(argument0) => money(argument0, locale)}
        threshold={0.09}
      />
    ) : (
      <span>
        {translate(locale, "overview.OptionsIntentMix.intentMetricsUnavailable")}
      </span>
    );
  return (
    <section className="ob-panel">
      <header>
        <div>
          <h3>{translate(locale, "overview.OptionsIntentMix.wherePremiumFlows")}</h3>
          <p>
            {translate(
              locale,
              "overview.OptionsIntentMix.contractTypesReceivingPremiumEstimatedTurnover",
            )}
          </p>
        </div>
        <div
          className="ob-controls"
          role="group"
          aria-label={translate(
            locale,
            "overview.OptionsIntentMix.premiumIntentWindow",
          )}
        >
          {(["all", "short", "long"] as const).map((win) => (
            <button
              type="button"
              key={win}
              aria-pressed={window === win}
              onClick={() => setWindow(win)}
            >
              {win === "all"
                ? translate(locale, "overview.OptionsIntentMix.all")
                : win === "short"
                  ? translate(locale, "overview.OptionsIntentMix.07Days")
                  : translate(locale, "overview.OptionsIntentMix.over30Days")}
            </button>
          ))}
        </div>
      </header>
      <div className="ob-legend">
        {getNames(locale).map((name, i) => (
          <span key={name}>
            <i style={{ background: colors[i] }} />
            {name}
          </span>
        ))}
      </div>
      <div className="ob-bars">
        {market && (
          <div className="ob-mix-row ob-mix-row--all">
            <b>{translate(locale, "overview.OptionsIntentMix.total")}</b>
            {bar(market.intent[window])}
            <span>{money(total(market.intent[window]), locale)}</span>
          </div>
        )}
        {list.map((row) => (
          <div
            key={row.symbol}
            className="ob-mix-row"
            tabIndex={0}
            role="link"
            onClick={() => open(row.symbol)}
            onKeyDown={(e) => e.key === "Enter" && open(row.symbol)}
          >
            <b>{row.s}</b>
            {row.available ? (
              bar(row.board.intent?.[window])
            ) : (
              <span>
                {translate(locale, "overview.OptionsIntentMix.noAvailableSnapshot")}
              </span>
            )}
            <span>
              {row.available ? money(total(row.board.intent?.[window]), locale) : "—"}
            </span>
          </div>
        ))}
      </div>
      <p className="ob-note">
        {translate(
          locale,
          "overview.OptionsIntentMix.withoutTradePricesBuyerSellerDirectionIsUnknownOnly",
        )}
      </p>
    </section>
  );
}
