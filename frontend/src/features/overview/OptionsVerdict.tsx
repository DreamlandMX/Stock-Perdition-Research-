import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { OptionBoardResponse } from "../options/useOptionsBoard";
import {
  money,
  number,
  percent,
  ratio,
  type OptionsBoardRows,
} from "../options/options-board-rows";

export function OptionsVerdict({
  rows,
  market,
}: {
  rows: OptionsBoardRows;
  market: OptionBoardResponse["market"];
}) {
  const { locale } = useI18n();
  const valid = rows.filter((r) => r.available);
  const names = (items: OptionsBoardRows) =>
    items.map((r) => r.s).join(", ") ||
    translate(locale, "options.OptionRefreshControl.unconfirmed");
  const grouped = ["def", "vol", "bull", "neu"].flatMap((k) => {
    const group = valid.filter((r) => r.look.k === k);
    return group.length ? [`${names(group)} ${group[0]!.look.t}`] : [];
  });
  const spy = valid.find((r) => r.s === "SPY");
  const lead = valid
    .filter((r) => r.otmCallOfOtm != null)
    .sort((a, b) => b.otmCallOfOtm! - a.otmCallOfOtm!)
    .slice(0, 3);
  const hot = valid
    .filter((r) => r.iv != null)
    .sort((a, b) => b.iv! - a.iv!)
    .slice(0, 2);
  const skew = valid
    .filter((r) => r.skew != null)
    .sort((a, b) => b.skew! - a.skew!)
    .slice(0, 2);
  const cheap = valid.filter((r) => r.ivhv != null && r.ivhv < 0);
  const compared = valid.filter((r) => r.ivhv != null);
  return (
    <section
      className="ob-verdict"
      aria-label={translate(locale, "overview.OptionsVerdict.todaySOptionsSummary")}
    >
      <h3>{translate(locale, "overview.OptionsVerdict.todaySOptionsSummary")}</h3>
      <p>
        <b>{translate(locale, "overview.OptionsVerdict.nextMonth")}</b>{" "}
        {grouped.join("; ") ||
          translate(locale, "overview.OptionsVerdict.noClassifiableData")}
        .{" "}
        {spy?.pu5 != null && spy.pd5 != null && (
          <>
            <span className="ob-down">
              {translate(locale, "overview.optionsVerdict.spyDown", {
                probability: percent(spy.pd5, undefined, locale),
              })}
            </span>{" "}
            <span className="ob-up">
              {translate(locale, "overview.optionsVerdict.spyUp", {
                probability: percent(spy.pu5, undefined, locale),
              })}
            </span>{" "}
            {spy.pu5 > 0
              ? translate(locale, "overview.optionsVerdict.tailRatio", {
                  ratio: number(ratio(spy.pd5, spy.pu5), 1, locale),
                })
              : translate(locale, "overview.optionsVerdict.zeroUpside")}
          </>
        )}
      </p>
      <p>
        <b>{translate(locale, "overview.OptionsVerdict.premium")}</b>{" "}
        <span className="ob-up">
          {translate(locale, "overview.optionsVerdict.callPremium", {
            amount: money(market?.intent.all?.otm_call, locale),
          })}
        </span>{" "}
        <span className="ob-down">
          {translate(locale, "overview.optionsVerdict.putPremium", {
            amount: money(market?.intent.all?.otm_put, locale),
          })}
        </span>{" "}
        {lead.length > 0 && (
          <>
            {translate(locale, "overview.optionsVerdict.otmCallShare", {
              symbols: names(lead),
              share: percent(Math.min(...lead.map((r) => r.otmCallOfOtm!)), 0, locale),
            })}{" "}
          </>
        )}
        {translate(locale, "overview.optionsVerdict.putCallAboveOne", {
          symbols: names(valid.filter((r) => r.pc != null && r.pc > 1)),
        })}{" "}
        {translate(locale, "overview.optionsVerdict.largestSkew", {
          symbols: names(skew),
        })}
      </p>
      <p>
        <b>{translate(locale, "overview.OptionsVerdict.volatility")}</b>{" "}
        {translate(locale, "overview.optionsVerdict.highestIv", {
          symbols: names(hot),
        })}{" "}
        {translate(locale, "overview.optionsVerdict.invertedTerms", {
          count: valid.filter((r) => r.term === "inverted").length,
        })}{" "}
        {compared.length
          ? translate(
              locale,
              "overview.OptionsVerdict.instrumentsHaveIVAboveRecent20DayRealizedVolatility",
              {
                count: compared.filter((r) => r.ivhv! > 0).length,
                count2: compared.length,
              },
            )
          : translate(
              locale,
              "overview.OptionsVerdict.noComparable20DayRealizedVolatility",
            )}
        .{" "}
        {cheap.length > 0 && (
          <>
            {translate(locale, "overview.optionsVerdict.cheaperOptions", {
              symbols: names(cheap),
            })}{" "}
          </>
        )}
        {translate(locale, "overview.optionsVerdict.marketCallPremium", {
          share: percent(ratio(market?.call_premium, market?.premium), 0, locale),
          symbols: names([...valid].sort((a, b) => b.prem! - a.prem!).slice(0, 2)),
        })}
      </p>
      <p className="ob-note">
        {translate(
          locale,
          "overview.OptionsVerdict.probabilitiesAreOptionsImpliedRiskNeutralProbabilitiesModelsAre",
        )}{" "}
        {rows.some((r) => !r.available || r.look.k === "missing") &&
          translate(
            locale,
            "overview.OptionsVerdict.instrumentsWithMissingMetricsAreExcludedFromTheRelevant",
          )}
      </p>
    </section>
  );
}
