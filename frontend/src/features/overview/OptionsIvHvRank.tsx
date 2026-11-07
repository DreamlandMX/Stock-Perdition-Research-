import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useNavigate } from "react-router-dom";
import { percent, points, type OptionsBoardRows } from "../options/options-board-rows";
import { buildMarketResearchHref, type MarketLinkState } from "./overview-terminal";

export function OptionsIvHvRank({
  rows,
  linkState,
}: {
  rows: OptionsBoardRows;
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const navigate = useNavigate(),
    open = (symbol: string) =>
      void navigate(`${buildMarketResearchHref(symbol, linkState)}#options`);
  const list = [...rows].sort((a, b) => (b.ivhv ?? -Infinity) - (a.ivhv ?? -Infinity));
  const scale = Math.max(0.01, ...rows.map((r) => Math.abs(r.ivhv ?? 0))) * 1.25;
  return (
    <section className="ob-panel">
      <header>
        <div>
          <h3>{translate(locale, "overview.OptionsIvHvRank.iVRealizedVolatility")}</h3>
          <p>
            {translate(
              locale,
              "overview.OptionsIvHvRank.optionsPricingAnnualizedIVVsRecent20DayRealized",
            )}
          </p>
        </div>
      </header>
      <div className="ob-bars">
        <div className="ob-rank-head">
          <span />
          <div>
            <span>
              {translate(locale, "overview.OptionsIvHvRank.relativelyCheaperOptions")}
            </span>
            <span>
              {translate(locale, "overview.OptionsIvHvRank.relativelyExpensiveOptions")}
            </span>
          </div>
          <span>
            {translate(locale, "overview.OptionsIvHvRank.iVRealizedVolatility2")}
          </span>
        </div>
        {list.map((row) => {
          const value = row.ivhv,
            width = value == null ? 0 : (Math.abs(value) / scale) * 50;
          return (
            <div
              key={row.symbol}
              className="ob-rank-row"
              tabIndex={0}
              role="link"
              onClick={() => open(row.symbol)}
              onKeyDown={(e) => e.key === "Enter" && open(row.symbol)}
            >
              <b>{row.s}</b>
              <div className="ob-rank-track">
                <i className="ob-zero" />
                {value != null && (
                  <span
                    className="ob-rank-band"
                    style={{
                      left: `${value >= 0 ? 50 : 50 - width}%`,
                      width: `${width}%`,
                      background: value >= 0 ? "var(--color-cone)" : "var(--color-up)",
                    }}
                  />
                )}
                <span className="ob-rank-value">{points(value, locale)}</span>
              </div>
              <span>
                IV {percent(row.iv, undefined, locale)}{" "}
                {translate(locale, "overview.OptionsIvHvRank.realized")}{" "}
                {percent(row.rv, undefined, locale)}
              </span>
            </div>
          );
        })}
      </div>
      <p className="ob-note">
        {translate(
          locale,
          "overview.OptionsIvHvRank.realizedVolatilityRequires21ClosesBeforeTheSameMarket",
        )}
      </p>
    </section>
  );
}
