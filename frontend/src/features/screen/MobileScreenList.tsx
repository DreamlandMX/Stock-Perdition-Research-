import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { Link } from "react-router-dom";
import type { components } from "../../api/generated/schema";
import type { ScreenUrlState } from "../../app/url-state";
import { ConfidenceLabel } from "../../components/ConfidenceLabel";
import { ProbabilityValue } from "../../components/ProbabilityValue";
import { ReturnValue } from "../../components/ReturnValue";
import { regimeLabel } from "../../app/presentation";
import { displayDataTime, displaySymbol, screenResearchHref } from "./screen-url-state";
type ScreenRow = components["schemas"]["ScreenRow"];
export interface MobileScreenListProps {
  rows: readonly ScreenRow[];
  state: ScreenUrlState;
}
export function MobileScreenList({ rows, state }: MobileScreenListProps) {
  const { locale } = useI18n();
  return (
    <ol
      aria-label={translate(locale, "screen.mobilescreenlist.mobileScreenerResults")}
      className="mobile-screen-list"
    >
      {rows.map((row) => {
        const symbol = displaySymbol(row.symbol);
        const selectedQ10 =
          state.returnBasis === "capital_gain"
            ? row.capital_gain_q10
            : row.executable_q10;
        const q10Label =
          state.returnBasis === "capital_gain"
            ? translate(locale, "screen.mobilescreenlist.capitalGainQ10")
            : translate(locale, "screen.mobilescreenlist.executableQ10");
        return (
          <li
            aria-label={translate(locale, "screen.mobilescreenlist.screeningEvidence", {
              symbol: symbol,
            })}
            key={`${row.symbol}-${row.horizon}`}
          >
            <Link
              className="mobile-screen-list__heading"
              to={screenResearchHref(row, state)}
            >
              <span>
                <strong>{symbol}</strong>
                <small>{row.name}</small>
              </span>
              <span>{regimeLabel(row.regime, locale)}</span>
            </Link>
            <dl>
              <div>
                <dt>{translate(locale, "screen.mobilescreenlist.upProbability")}</dt>
                <dd>
                  <ProbabilityValue value={row.executable_up_probability ?? null} />
                </dd>
              </div>
              <div className="mobile-screen-list__risk">
                <dt>{q10Label}</dt>
                <dd>
                  <ReturnValue value={selectedQ10} />
                </dd>
              </div>
              <div>
                <dt>{translate(locale, "screen.mobilescreenlist.confidence")}</dt>
                <dd>
                  <ConfidenceLabel level={row.confidence_level} />
                </dd>
              </div>
              <div>
                <dt>{translate(locale, "screen.mobilescreenlist.dataTime")}</dt>
                <dd>
                  <time dateTime={row.as_of}>{displayDataTime(row.as_of, locale)}</time>
                </dd>
              </div>
            </dl>
          </li>
        );
      })}
    </ol>
  );
}
