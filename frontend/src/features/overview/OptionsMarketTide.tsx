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

export function OptionsMarketTide({
  market,
  rows,
}: {
  market: OptionBoardResponse["market"];
  rows: OptionsBoardRows;
}) {
  const { locale } = useI18n();
  if (!market)
    return (
      <p className="ob-note">
        {translate(
          locale,
          "overview.OptionsMarketTide.optionsMarketAggregateUnavailable",
        )}
      </p>
    );
  const share = ratio(market.call_premium, market.premium);
  const pc = ratio(market.put_volume, market.call_volume);
  const short = ratio(market.expiry_buckets_volume[0], market.volume);
  const top = [...rows]
    .filter((r) => r.prem != null)
    .sort((a, b) => b.prem! - a.prem!)[0];
  const ivs = [...rows].filter((r) => r.iv != null).sort((a, b) => b.iv! - a.iv!);
  const lean =
    share == null
      ? translate(locale, "options.OptionRefreshControl.unconfirmed")
      : share >= 0.6
        ? translate(locale, "overview.OptionsMarketTide.strongCallTilt")
        : share >= 0.52
          ? translate(locale, "overview.OptionsMarketTide.slightCallTilt")
          : share > 0.48
            ? translate(locale, "overview.OptionsMarketTide.balanced")
            : share > 0.4
              ? translate(locale, "overview.OptionsMarketTide.slightPutTilt")
              : translate(locale, "overview.OptionsMarketTide.strongPutTilt");
  return (
    <section
      className="ob-tide"
      aria-label={translate(locale, "overview.OptionsMarketTide.marketTemperature")}
    >
      <div>
        <span>
          {translate(locale, "overview.OptionsMarketTide.totalOptionsVolume")}
        </span>
        <strong>
          {number(market.volume, 0, locale)}{" "}
          <small>{translate(locale, "overview.OptionsMarketTide.contracts")}</small>
        </strong>
        <small>
          Call {number(market.call_volume, 0, locale)} ; Put{" "}
          {number(market.put_volume, 0, locale)}
        </small>
      </div>
      <div>
        <span>{translate(locale, "overview.OptionsMarketTide.estimatedTurnover")}</span>
        <strong>{money(market.premium, locale)}</strong>
        <small>
          {translate(locale, "overview.OptionsMarketTide.largest")} {top?.s ?? "—"}{" "}
          {money(top?.prem, locale)}
        </small>
      </div>
      <div className="ob-tide-flow">
        <span>
          {translate(locale, "overview.OptionsMarketTide.estimatedPremiumFlow")}{" "}
          <b>{lean}</b>
        </span>
        <div className="ob-stack">
          <i
            style={{ width: `${(share ?? 0) * 100}%`, background: "var(--color-up)" }}
          />
          <i
            style={{
              width: `${share == null ? 0 : (1 - share) * 100}%`,
              background: "var(--color-down)",
            }}
          />
        </div>
        <small className="ob-split">
          <span className="ob-up">
            Call {money(market.call_premium, locale)} ; {percent(share, 0, locale)}
          </span>
          <span className="ob-down">
            Put {money(market.put_premium, locale)} ;{" "}
            {percent(share == null ? null : 1 - share, 0, locale)}
          </span>
        </small>
      </div>
      <div>
        <span>{translate(locale, "overview.OptionsMarketTide.overallPutCall")}</span>
        <strong className={pc != null && pc > 1 ? "ob-warning" : ""}>
          {number(pc, undefined, locale)}
        </strong>
        <small>
          {translate(locale, "overview.OptionsMarketTide.putVolumeCallVolume")}
        </small>
      </div>
      <div>
        <span>{translate(locale, "overview.OptionsMarketTide.averageATMIV")}</span>
        <strong>{percent(market.avg_atm_iv_30d, undefined, locale)}</strong>
        <small>
          {ivs[0]?.s ?? "—"} {percent(ivs[0]?.iv, undefined, locale)} ;{" "}
          {ivs.at(-1)?.s ?? "—"} {percent(ivs.at(-1)?.iv, undefined, locale)}
        </small>
      </div>
      <div>
        <span>{translate(locale, "overview.OptionsMarketTide.07DayVolumeShare")}</span>
        <strong>{percent(short, undefined, locale)}</strong>
        <small>
          {short == null
            ? translate(locale, "options.OptionRefreshControl.unconfirmed")
            : short > 0.6
              ? translate(locale, "overview.OptionsMarketTide.mostlyNearTermContracts")
              : translate(locale, "overview.OptionsMarketTide.moreBalancedExpiryMix")}
        </small>
      </div>
    </section>
  );
}
