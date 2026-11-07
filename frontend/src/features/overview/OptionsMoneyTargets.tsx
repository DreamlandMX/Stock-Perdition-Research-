import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  money,
  number,
  signedPercent,
  type OptionsBoardRows,
} from "../options/options-board-rows";
import { buildMarketResearchHref, type MarketLinkState } from "./overview-terminal";

export function OptionsMoneyTargets({
  rows,
  linkState,
}: {
  rows: OptionsBoardRows;
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const [window, setWindow] = useState<"week" | "month">("week");
  const navigate = useNavigate(),
    open = (symbol: string) =>
      void navigate(`${buildMarketResearchHref(symbol, linkState)}#options`);
  const position = (value: number) => 50 + Math.max(-1, Math.min(1, value / 0.05)) * 50;
  const list = [...rows].sort((a, b) => {
    const span = (r: typeof a) => {
      const t = r.board.targets?.[window];
      return t?.call != null && t.put != null && r.spot
        ? (t.call - t.put) / r.spot
        : -1;
    };
    return span(b) - span(a);
  });
  return (
    <section className="ob-panel">
      <header>
        <div>
          <h3>
            {translate(locale, "overview.OptionsMoneyTargets.wherePremiumConcentrates")}
          </h3>
          <p>
            {translate(
              locale,
              "overview.OptionsMoneyTargets.oTMPremiumWeightedStrikesProtectionTarget",
            )}
          </p>
        </div>
        <div
          className="ob-controls"
          role="group"
          aria-label={translate(
            locale,
            "overview.OptionsMoneyTargets.premiumTargetWindow",
          )}
        >
          {(["week", "month"] as const).map((win) => (
            <button
              type="button"
              key={win}
              aria-pressed={window === win}
              onClick={() => setWindow(win)}
            >
              {win === "week"
                ? translate(locale, "overview.OptionsMoneyTargets.expiresWithin1Week")
                : translate(locale, "overview.OptionsMoneyTargets.expiresWithin1Month")}
            </button>
          ))}
        </div>
      </header>
      <div className="ob-bars">
        {list.map((row) => {
          const t = row.board.targets?.[window],
            pc = t?.put != null && row.spot ? t.put / row.spot - 1 : null,
            cc = t?.call != null && row.spot ? t.call / row.spot - 1 : null;
          return (
            <div
              key={row.symbol}
              className="ob-target-row"
              tabIndex={0}
              role="link"
              onClick={() => open(row.symbol)}
              onKeyDown={(e) => e.key === "Enter" && open(row.symbol)}
              title={translate(
                locale,
                "overview.OptionsMoneyTargets.protectionTarget",
                {
                  s: row.s,
                  number: number(t?.put, 1, locale),
                  value: money(t?.put_premium, locale),
                  number2: number(t?.call, 1, locale),
                  value2: money(t?.call_premium, locale),
                },
              )}
            >
              <b>{row.s}</b>
              <div className="ob-target-track">
                <i className="ob-zero" />
                {pc != null && cc != null && (
                  <span
                    className="ob-target-line"
                    style={{
                      left: `${position(pc)}%`,
                      width: `${Math.max(0, position(cc) - position(pc))}%`,
                    }}
                  />
                )}
                {pc != null && (
                  <i
                    className="ob-target-dot ob-target-dot--put"
                    style={{ left: `${position(pc)}%` }}
                  />
                )}
                {cc != null && (
                  <i
                    className="ob-target-dot ob-target-dot--call"
                    style={{ left: `${position(cc)}%` }}
                  />
                )}
                <span className="ob-target-pct ob-down">
                  {signedPercent(pc, undefined, locale)}
                </span>
                <span className="ob-target-pct ob-up">
                  {signedPercent(cc, undefined, locale)}
                </span>
              </div>
              <span>
                <span className="ob-down">{number(t?.put, 1, locale)}</span> /{" "}
                <span className="ob-up">{number(t?.call, 1, locale)}</span>
              </span>
            </div>
          );
        })}
        <div className="ob-ladder-axis">
          <span />
          <div>
            {[
              "−5%",
              "−2.5%",
              translate(locale, "overview.OptionsMoneyTargets.spot"),
              "+2.5%",
              "+5%",
            ].map((label) => (
              <span key={label}>{label}</span>
            ))}
          </div>
          <span>
            {translate(locale, "overview.OptionsMoneyTargets.protectionTarget2")}
          </span>
        </div>
      </div>
      <p className="ob-note">
        {translate(
          locale,
          "overview.OptionsMoneyTargets.theAxisSpans5OutOfRangePointsClamp",
        )}
      </p>
    </section>
  );
}
