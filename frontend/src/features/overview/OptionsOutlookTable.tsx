import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useNavigate } from "react-router-dom";
import { optionReasonLabel } from "../options/option-labels";
import {
  number,
  percent,
  signedPercent,
  type OptionsBoardRow,
  type OptionsBoardRows,
} from "../options/options-board-rows";
import { buildMarketResearchHref, type MarketLinkState } from "./overview-terminal";

function Range({
  probability,
  spot,
  window,
}: {
  probability: OptionsBoardRow["board"]["prob_month"];
  spot: number | null;
  window: "prob_week" | "prob_month";
}) {
  const { locale } = useI18n();
  if (!probability || probability.lo80 == null || probability.hi80 == null || !spot)
    return (
      <span>
        {probability?.reason
          ? optionReasonLabel(`${window}:${probability.reason}`, locale)
          : "—"}
      </span>
    );
  return (
    <span className="ob-range">
      {number(probability.lo80, probability.lo80 > 100 ? 0 : 1, locale)}–
      {number(probability.hi80, probability.hi80 > 100 ? 0 : 1, locale)}
      <small>
        {signedPercent(probability.lo80 / spot - 1, undefined, locale)} /{" "}
        {signedPercent(probability.hi80 / spot - 1, undefined, locale)}
      </small>
    </span>
  );
}
export function OptionsOutlookTable({
  rows,
  selected,
  onSelect,
  linkState,
}: {
  rows: OptionsBoardRows;
  selected: string;
  onSelect: (symbol: string) => void;
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const navigate = useNavigate();
  const open = (symbol: string) =>
    void navigate(`${buildMarketResearchHref(symbol, linkState)}#options`);
  return (
    <section
      className="ob-panel ob-outlook"
      aria-label={translate(locale, "overview.OptionsOutlookTable.optionsOutlook2")}
    >
      <header>
        <div>
          <h3>{translate(locale, "overview.OptionsOutlookTable.optionsOutlook2")}</h3>
          <p>
            {translate(
              locale,
              "overview.OptionsOutlookTable.1Week1MonthRiskNeutralIntervalsClickFor",
            )}
          </p>
        </div>
      </header>
      <div className="ob-scroll">
        <table>
          <thead>
            <tr>
              <th>{translate(locale, "overview.OverviewPage.instrument")}</th>
              <th>
                {translate(locale, "overview.OptionsOutlookTable.optionsOutlook")}
              </th>
              <th>
                {translate(locale, "overview.OptionsOutlookTable.1Week80Interval")}
              </th>
              <th>
                {translate(locale, "overview.OptionsOutlookTable.1Month80Interval")}
              </th>
              <th>
                {translate(locale, "overview.OptionsOutlookTable.1Month")}{" "}
                <span className="ob-down">
                  {translate(locale, "overview.OverviewHeaderBand.down")}
                </span>{" "}
                /{" "}
                <span className="ob-up">
                  {translate(locale, "overview.OverviewHeaderBand.up")}
                </span>{" "}
                {translate(locale, "overview.OptionsOutlookTable.5")}
              </th>
              <th
                title={translate(
                  locale,
                  "overview.OptionsOutlookTable.probabilityDown10ProbabilityUp10",
                )}
              >
                {translate(locale, "overview.OptionsOutlookTable.tailRatio")}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.symbol}
                tabIndex={0}
                aria-selected={selected === row.symbol}
                title={`${row.look.why}${(row.board.prob_month?.fit_rmse ?? 0) > 0.03 ? translate(locale, "overview.OptionsOutlookTable.largeFitError") : ""}`}
                onClick={() => onSelect(row.symbol)}
                onDoubleClick={() => open(row.symbol)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") open(row.symbol);
                  if (e.key === " ") {
                    e.preventDefault();
                    onSelect(row.symbol);
                  }
                }}
              >
                <td>
                  <b>{row.s}</b>
                  {(row.board.prob_month?.fit_rmse ?? 0) > 0.03 && (
                    <small className="ob-warning">
                      {translate(locale, "overview.OptionsOutlookTable.largeFitError2")}
                    </small>
                  )}
                </td>
                <td>
                  <span className={`ob-look ob-look--${row.look.k}`}>{row.look.t}</span>
                  {!row.available && <small>{row.look.why}</small>}
                </td>
                <td>
                  <Range
                    probability={row.board.prob_week}
                    spot={row.spot}
                    window="prob_week"
                  />
                </td>
                <td>
                  <Range
                    probability={row.board.prob_month}
                    spot={row.spot}
                    window="prob_month"
                  />
                </td>
                <td>
                  <span className="ob-prob-bar">
                    <span className="ob-down">{percent(row.pd5, 0, locale)}</span>
                    <span className="ob-prob-track">
                      <i
                        style={{
                          width: `${Math.min(1, (row.pd5 ?? 0) / 0.4) * 50}%`,
                          background: "var(--color-down)",
                        }}
                      />
                      <i
                        style={{
                          width: `${Math.min(1, (row.pu5 ?? 0) / 0.4) * 50}%`,
                          background: "var(--color-up)",
                        }}
                      />
                    </span>
                    <span className="ob-up">{percent(row.pu5, 0, locale)}</span>
                  </span>
                </td>
                <td className={row.tail != null && row.tail >= 1.3 ? "ob-down" : ""}>
                  {row.tail == null ? "—" : `${number(row.tail, 1, locale)}×`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="ob-note">
        {translate(locale, "overview.OptionsOutlookTable.classificationRules")}
        <span className="ob-look ob-look--def">
          {translate(locale, "options.optionsBoardRows.downsideProtection")}
        </span>{" "}
        {translate(
          locale,
          "overview.OptionsOutlookTable.probabilityDown1013ProbabilityUp10And",
        )}
        <span className="ob-look ob-look--vol">
          {translate(locale, "options.optionsBoardRows.largeMovePositioning")}
        </span>{" "}
        {translate(
          locale,
          "overview.OptionsOutlookTable.probabilitiesOfUpAndDown10Both17",
        )}
        <span className="ob-look ob-look--bull">
          {translate(locale, "options.optionsBoardRows.bullish")}
        </span>{" "}
        {translate(
          locale,
          "overview.OptionsOutlookTable.oTMCallPremiumShare60AndProbabilityUp5",
        )}{" "}
        <span className="ob-look ob-look--neu">
          {translate(locale, "options.optionsBoardRows.neutral")}
        </span>
        {translate(
          locale,
          "overview.OptionsOutlookTable.probabilityDerivativeOfOptionPriceWithRespectToStrike",
        )}
      </p>
    </section>
  );
}
