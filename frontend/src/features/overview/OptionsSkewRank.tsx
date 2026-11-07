import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useNavigate } from "react-router-dom";
import { percent, points, type OptionsBoardRows } from "../options/options-board-rows";
import { buildMarketResearchHref, type MarketLinkState } from "./overview-terminal";

export function OptionsSkewRank({
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
  const list = [...rows].sort((a, b) => (b.skew ?? -Infinity) - (a.skew ?? -Infinity));
  const scale = Math.max(0.01, ...rows.map((r) => Math.abs(r.skew ?? 0))) * 1.25;
  return (
    <section className="ob-panel">
      <header>
        <div>
          <h3>
            {translate(locale, "overview.OptionsSkewRank.skewProtectionVsUpside")}
          </h3>
          <p>
            {translate(
              locale,
              "overview.OptionsSkewRank.approx5OTMPutIVCallIVExpiryNearest",
            )}
          </p>
        </div>
      </header>
      <div className="ob-bars">
        <div className="ob-rank-head">
          <span />
          <div>
            <span>{translate(locale, "overview.OptionsSkewRank.upsideDemand")}</span>
            <span>
              {translate(locale, "overview.OptionsSkewRank.downsideProtection")}
            </span>
          </div>
          <span>{translate(locale, "overview.OptionsSkewRank.putCallIVByExpiry")}</span>
        </div>
        {list.map((row) => {
          const value = row.skew,
            width = value == null ? 0 : (Math.abs(value) / scale) * 50,
            k = row.board.skew_30;
          return (
            <div
              key={row.symbol}
              className="ob-rank-row"
              tabIndex={0}
              role="link"
              onClick={() => open(row.symbol)}
              onKeyDown={(e) => e.key === "Enter" && open(row.symbol)}
              title={translate(locale, "overview.OptionsSkewRank.days", {
                expiration:
                  k?.expiration ??
                  translate(locale, "overview.OptionsSkewRank.noExpiry"),
                dte: k?.dte ?? "—",
              })}
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
                      background: value >= 0 ? "var(--color-down)" : "var(--color-up)",
                    }}
                  />
                )}
                <span className="ob-rank-value">{points(value, locale)}</span>
              </div>
              <span>
                {k?.expiration?.slice(5) ?? "—"} ;{" "}
                {percent(k?.put_iv, undefined, locale)} /{" "}
                {percent(k?.call_iv, undefined, locale)}
              </span>
            </div>
          );
        })}
      </div>
      <p className="ob-note">
        {translate(
          locale,
          "overview.OptionsSkewRank.linearInterpolationRequiresEligibleIVOnBothSidesOf",
        )}
      </p>
    </section>
  );
}
