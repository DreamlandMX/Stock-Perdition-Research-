import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { CSSProperties } from "react";

import { formatResearchPrice } from "../../components/ResearchPriceProjection";

export interface PriceIntervalTrackProps {
  currency: string;
  q10: number;
  q50: number;
  q90: number;
  referencePrice: number;
}

type IntervalStyle = CSSProperties & {
  "--price-q10": string;
  "--price-q50": string;
  "--price-q90": string;
  "--price-reference": string;
};

function position(value: number, minimum: number, span: number) {
  return `${Math.min(100, Math.max(0, ((value - minimum) / span) * 100)).toFixed(2)}%`;
}

export function PriceIntervalTrack({
  currency,
  q10,
  q50,
  q90,
  referencePrice,
}: PriceIntervalTrackProps) {
  const { locale } = useI18n();
  const values = [q10, q50, q90, referencePrice];
  if (
    !values.every(Number.isFinite) ||
    values.some((value) => value <= 0) ||
    q10 > q50 ||
    q50 > q90
  ) {
    return (
      <span className="price-interval-track__unavailable">
        {translate(locale, "overview.PriceIntervalTrack.priceIntervalUnavailable")}
      </span>
    );
  }

  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = maximum - minimum || 1;
  const style: IntervalStyle = {
    "--price-q10": position(q10, minimum, span),
    "--price-q50": position(q50, minimum, span),
    "--price-q90": position(q90, minimum, span),
    "--price-reference": position(referencePrice, minimum, span),
  };
  const label = translate(locale, "overview.PriceIntervalTrack.currentCloseQ10Q50Q90", {
    value: formatResearchPrice(referencePrice, currency, locale),
    value2: formatResearchPrice(q10, currency, locale),
    value3: formatResearchPrice(q50, currency, locale),
    value4: formatResearchPrice(q90, currency, locale),
  });

  return (
    <div aria-label={label} className="price-interval-track" role="img" style={style}>
      <div aria-hidden="true" className="price-interval-track__rail">
        <span className="price-interval-track__band" />
        <span className="price-interval-track__endpoint price-interval-track__endpoint--q10" />
        <span className="price-interval-track__endpoint price-interval-track__endpoint--q90" />
        <span className="price-interval-track__marker price-interval-track__marker--reference" />
        <span className="price-interval-track__marker price-interval-track__marker--q50" />
      </div>
      <div aria-hidden="true" className="price-interval-track__labels">
        <span>{formatResearchPrice(q10, currency, locale)}</span>
        <span>{formatResearchPrice(q50, currency, locale)}</span>
        <span>{formatResearchPrice(q90, currency, locale)}</span>
      </div>
    </div>
  );
}
