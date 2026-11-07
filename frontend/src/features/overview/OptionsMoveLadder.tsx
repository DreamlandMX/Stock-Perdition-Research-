import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { number, percent, type OptionsBoardRows } from "../options/options-board-rows";
import { buildMarketResearchHref, type MarketLinkState } from "./overview-terminal";

export function OptionsMoveLadder({
  rows,
  linkState,
}: {
  rows: OptionsBoardRows;
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const [horizon, setHorizon] = useState<5 | 20>(20);
  const navigate = useNavigate(),
    open = (symbol: string) =>
      void navigate(`${buildMarketResearchHref(symbol, linkState)}#options`);
  const scale = Math.max(
    0.2,
    Math.ceil((Math.max(0, ...rows.map((r) => r.m20 ?? 0)) * 100) / 5) * 0.05,
  );
  const position = (value: number) => 50 + (value / scale) * 50;
  const list = [...rows].sort(
    (a, b) =>
      (horizon === 5 ? (b.m5 ?? -1) : (b.m20 ?? -1)) -
      (horizon === 5 ? (a.m5 ?? -1) : (a.m20 ?? -1)),
  );
  return (
    <section className="ob-panel">
      <header>
        <div>
          <h3>
            {translate(
              locale,
              "overview.OptionsMoveLadder.impliedMoveIntervalComparison",
            )}
          </h3>
          <p>
            {translate(
              locale,
              "overview.OptionsMoveLadder.5DayBright20DayFaint80IntervalsSame",
            )}
          </p>
        </div>
        <div
          className="ob-controls"
          role="group"
          aria-label={translate(
            locale,
            "overview.OptionsMoveLadder.impliedIntervalSorting",
          )}
        >
          {([5, 20] as const).map((h) => (
            <button
              key={h}
              type="button"
              aria-pressed={horizon === h}
              onClick={() => setHorizon(h)}
            >
              {translate(locale, "overview.OptionsMoveLadder.sortBy")} {h}{" "}
              {translate(locale, "overview.SectorHealthTerminal.days")}
            </button>
          ))}
        </div>
      </header>
      <div className="ob-bars">
        {list.map((row) => {
          const moves = [20, 5].map((h) =>
            row.overview?.implied_moves.find((m) => m.horizon_days === h),
          );
          return (
            <div
              key={row.symbol}
              className="ob-ladder-row"
              tabIndex={0}
              role="link"
              onClick={() => open(row.symbol)}
              onKeyDown={(e) => e.key === "Enter" && open(row.symbol)}
              title={moves
                .map((m, i) =>
                  translate(locale, "overview.OptionsMoveLadder.days", {
                    value: i === 0 ? 20 : 5,
                    number: number(m?.lower_price, undefined, locale),
                    number2: number(m?.upper_price, undefined, locale),
                  }),
                )
                .join(" ; ")}
            >
              <b>{row.s}</b>
              <div className="ob-ladder-track">
                <i className="ob-zero" />
                {moves.map((move, i) =>
                  move?.lower_pct != null && move.upper_pct != null ? (
                    <span
                      key={i}
                      className={`ob-ladder-band ob-ladder-band--${i === 0 ? 20 : 5}`}
                      style={{
                        left: `${position(move.lower_pct)}%`,
                        width: `${position(move.upper_pct) - position(move.lower_pct)}%`,
                      }}
                    />
                  ) : null,
                )}
              </div>
              <span>
                <b>{row.m5 == null ? "—" : `±${percent(row.m5, undefined, locale)}`}</b>{" "}
                / {row.m20 == null ? "—" : `±${percent(row.m20, undefined, locale)}`}
              </span>
            </div>
          );
        })}
        <div className="ob-ladder-axis">
          <span />
          <div>
            {[-1, -0.5, 0, 0.5, 1].map((value) => (
              <span key={value}>
                {value === 0
                  ? "0"
                  : `${value > 0 ? "+" : ""}${percent(value * scale, 0, locale)}`}
              </span>
            ))}
          </div>
          <span>{translate(locale, "overview.OptionsMoveLadder.5Day20Day")}</span>
        </div>
      </div>
      <p className="ob-note">
        {translate(
          locale,
          "overview.OptionsMoveLadder.usesExistingImpliedMovesFromTheSameSnapshotMissing",
        )}
      </p>
    </section>
  );
}
