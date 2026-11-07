import { translate, type Locale } from "../i18n/core";
import { useI18n } from "../i18n/context";
import type { components } from "../api/generated/schema";
import { formatUsMarketTime } from "../app/presentation";
import "./research-price-projection.css";
type ResearchPriceProjectionValue = components["schemas"]["ResearchPriceProjection"];
const UNAVAILABLE_LABELS = (locale: Locale): Readonly<Record<string, string>> => ({
    mae_model_not_verified: translate(locale, "common.shared.theMaeModelHasNotPassedTheGates"),
    mae_not_in_candidate_scope: translate(locale, "common.shared.theCandidateLayerDoesNotIncludeAnMae"),
    mfe_model_not_verified: translate(locale, "common.shared.theMfeModelHasNotPassedTheGates"),
    mfe_not_in_candidate_scope: translate(locale, "common.shared.theCandidateLayerDoesNotIncludeAnMfe"),
    return_fwd_model_not_verified: translate(locale, "common.shared.theReturnDistributionModelHasNotPassedThe"),
    return_fwd_not_in_candidate_scope: translate(locale, "common.shared.theCandidateLayerDoesNotIncludeAReturn"),
});
export function formatResearchPrice(value: number | null | undefined, currency = "USD", locale: Locale = "zh-CN") {
    if (value == null || !Number.isFinite(value)) {
        return "—";
    }
    return new Intl.NumberFormat(locale, {
        currency,
        currencyDisplay: "narrowSymbol",
        maximumFractionDigits: 2,
        minimumFractionDigits: 2,
        style: "currency",
    }).format(value);
}
export function researchPriceUnavailableLabel(reason: string | null | undefined, horizon: number, locale: Locale = "zh-CN") {
    if (reason == null) {
        return translate(locale, "common.shared.priceComponentForTradingDaysNotPublished", { horizon: String(horizon) });
    }
    return translate(locale, "common.shared.tradingDays2", { horizon: String(horizon), reason: String(UNAVAILABLE_LABELS(locale)[reason] ?? reason.replaceAll("_", " ")) });
}
export function futureTradingDayCloseLabel(horizon: number, locale: Locale = "zh-CN") {
    return translate(locale, "common.shared.closeOfTradingDayAhead", { horizon: String(horizon) });
}
export function futureTradingDayWindowLabel(horizon: number, locale: Locale = "zh-CN") {
    if(horizon === 1) return translate(locale,"common.projection.windowOne");
    return translate(locale, "common.shared.withinTheNextTradingDays", { horizon: String(horizon) });
}
interface ResearchPriceProjectionProps {
    ariaLabel?: string;
    compact?: boolean;
    disclaimer?: string;
    horizon: number;
    projection: ResearchPriceProjectionValue | null | undefined;
    statusLabel?: string;
}
export function ResearchPriceProjection({ ariaLabel, compact = false, disclaimer, horizon, projection, statusLabel, }: ResearchPriceProjectionProps) {
    const { locale } = useI18n();
    disclaimer ??= translate(locale, "common.shared.researchEvidenceNotAFormalTradingSignal");
    statusLabel ??= translate(locale, "common.shared.verifiedResearch");
    if (projection == null) {
        return (<div className="research-price-projection research-price-projection--unavailable">
        {translate(locale,"common.projection.unpublished",{horizon})}</div>);
    }
    const band = projection.endpoint_price_band;
    return (<section aria-label={ariaLabel ?? translate(locale,"common.projection.aria",{horizon})} className={`research-price-projection${compact ? " research-price-projection--compact" : ""}`}>
      <header>
        <span>
          {statusLabel}: {translate(locale,"common.projection.median",{horizon})}</span>
        <strong>
          {band == null
            ? researchPriceUnavailableLabel(projection.endpoint_price_unavailable_reason, horizon, locale)
            : formatResearchPrice(band.q50, projection.currency, locale)}
        </strong>
      </header>
      <dl>
        <div>
          <dt>{translate(locale,"common.projection.interval",{horizon})}</dt>
          <dd>
            {band == null
            ? researchPriceUnavailableLabel(projection.endpoint_price_unavailable_reason, horizon, locale)
            : `${formatResearchPrice(band.q10, projection.currency, locale)} – ${formatResearchPrice(band.q90, projection.currency, locale)}`}
          </dd>
        </div>
        <div>
          <dt>{translate(locale,"common.projection.high",{horizon})}</dt>
          <dd>
            {projection.stage_high_price_q50 == null
            ? researchPriceUnavailableLabel(projection.stage_high_price_unavailable_reason, horizon, locale)
            : formatResearchPrice(projection.stage_high_price_q50, projection.currency, locale)}
          </dd>
        </div>
        <div>
          <dt>{translate(locale,"common.projection.low",{horizon})}</dt>
          <dd>
            {projection.stage_low_price_q50 == null
            ? researchPriceUnavailableLabel(projection.stage_low_price_unavailable_reason, horizon, locale)
            : formatResearchPrice(projection.stage_low_price_q50, projection.currency, locale)}
          </dd>
        </div>
      </dl>
      <footer>
        <span>{" " + (translate(locale, "common.shared.referencePrice") + " ")}{formatResearchPrice(projection.reference_price, projection.currency, locale)}
        </span>
        <time dateTime={projection.reference_price_at}>{" " + (translate(locale, "common.shared.dataAsOf") + " ")}{formatUsMarketTime(projection.reference_price_at, locale)}
        </time>
        {compact ? null : <span>{disclaimer}</span>}
      </footer>
    </section>);
}
