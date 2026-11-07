import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { OptionBoardResponse } from "../options/useOptionsBoard";
import { number, percent, type OptionsBoardRows } from "../options/options-board-rows";
import { useNavigate } from "react-router-dom";
import { buildMarketResearchHref, type MarketLinkState } from "./overview-terminal";

const getExpiryNames = (locale: Locale = "zh-CN") => [
  translate(locale, "overview.OptionsIntentMix.07Days"),
  translate(locale, "overview.OptionsExpiryMix.830Days"),
  translate(locale, "overview.OptionsExpiryMix.3160Days"),
  translate(locale, "overview.OptionsExpiryMix.over60Days"),
];
const expiryColors = [
  "var(--color-expiry-1)",
  "var(--color-expiry-2)",
  "var(--color-expiry-3)",
  "var(--color-expiry-4)",
];
export function OptionsStackBar({
  values,
  names,
  colors,
  amount,
  threshold = 0.07,
}: {
  values: readonly number[];
  names: readonly string[];
  colors: readonly string[];
  amount: (value: number) => string;
  threshold?: number;
}) {
  const { locale } = useI18n();
  const total = values.reduce((sum, value) => sum + value, 0);
  if (!total)
    return (
      <div className="ob-stack ob-stack--empty">
        {translate(locale, "overview.OptionsExpiryMix.noValidVolume")}
      </div>
    );
  return (
    <div className="ob-stack">
      {values.map((value, i) => (
        <span
          key={names[i]}
          style={{
            width: `${(value / total) * 100}%`,
            background: colors[i],
            color: i === 0 || i === 2 ? "var(--color-page)" : "var(--color-text)",
          }}
          title={`${names[i]} ${amount(value)} ; ${percent(value / total, undefined, locale)}`}
        >
          {value / total >= threshold ? percent(value / total, 0, locale) : ""}
        </span>
      ))}
    </div>
  );
}
export function OptionsExpiryMix({
  rows,
  market,
  linkState,
}: {
  rows: OptionsBoardRows;
  market: OptionBoardResponse["market"];
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const navigate = useNavigate(),
    open = (symbol: string) =>
      void navigate(`${buildMarketResearchHref(symbol, linkState)}#options`);
  const list = [...rows].sort((a, b) => (b.short7 ?? -1) - (a.short7 ?? -1));
  const bar = (values: readonly number[]) => (
    <OptionsStackBar
      values={values}
      names={getExpiryNames(locale)}
      colors={expiryColors}
      amount={(v) =>
        translate(locale, "overview.OptionsExpiryMix.contracts", {
          number: number(v, 0, locale),
        })
      }
    />
  );
  return (
    <section className="ob-panel">
      <header>
        <div>
          <h3>{translate(locale, "overview.OptionsExpiryMix.volumeByExpiry")}</h3>
          <p>
            {translate(
              locale,
              "overview.OptionsExpiryMix.nearTermOrLongerDatedPositioningByVolume",
            )}
          </p>
        </div>
      </header>
      <div className="ob-legend">
        {getExpiryNames(locale).map((name, i) => (
          <span key={name}>
            <i style={{ background: expiryColors[i] }} />
            {name}
          </span>
        ))}
      </div>
      <div className="ob-bars">
        {market && (
          <div className="ob-mix-row ob-mix-row--all">
            <b>{translate(locale, "overview.OptionsIntentMix.total")}</b>
            {bar(market.expiry_buckets_volume)}
            <span>{number(market.volume, 0, locale)}</span>
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
              bar(row.board.expiry_buckets_volume)
            ) : (
              <span>
                {translate(locale, "overview.OptionsIntentMix.noAvailableSnapshot")}
              </span>
            )}
            <span>{number(row.vol, 0, locale)}</span>
          </div>
        ))}
      </div>
      <p className="ob-note">
        {translate(
          locale,
          "overview.OptionsExpiryMix.calendarDTEIsMeasuredFromEachSnapshotSMarket",
        )}
      </p>
    </section>
  );
}
