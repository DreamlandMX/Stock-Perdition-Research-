import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import {
  ArrowDownRight,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  Minus,
} from "lucide-react";
import { useRef, useState } from "react";

import type { components } from "../../api/generated/schema";
import { buildDecisionRiskSummary } from "../../app/decision-risk-summary";
import { formatUsMarketTime } from "../../app/presentation";
import { ProbabilityValue } from "../../components/ProbabilityValue";
import { ResearchBoundaryLabel } from "../../components/ResearchBoundaryLabel";
import {
  formatResearchPrice,
  researchPriceUnavailableLabel,
} from "../../components/ResearchPriceProjection";
import { ReturnValue } from "../../components/ReturnValue";
import { MarketTrendSparkline } from "./MarketTrendSparkline";
import { PriceIntervalTrack } from "./PriceIntervalTrack";
import { getRegimeLabels } from "./RegimeDistribution";

type MarketProxy = components["schemas"]["MarketProxy"];

const PROXY_ORDER = ["US.SPY", "US.QQQ", "US.IWM", "US.DIA"] as const;

const getDirectionPresentation = (locale: Locale = "zh-CN") =>
  ({
    down: {
      Icon: ArrowDownRight,
      label: translate(locale, "overview.MarketProxyStrip.weak"),
    },
    flat: { Icon: Minus, label: translate(locale, "options.optionsBoardRows.neutral") },
    up: {
      Icon: ArrowUpRight,
      label: translate(locale, "overview.MarketProxyStrip.strong"),
    },
  }) as const;

export interface MarketProxyStripProps {
  horizon: 1 | 5 | 20;
  onHorizonChange: (horizon: 1 | 5 | 20) => void;
  proxies: readonly MarketProxy[];
}

function displaySymbol(symbol: string) {
  return symbol.startsWith("US.") ? symbol.slice(3) : symbol;
}

function projectedReturn(
  value: number | null | undefined,
  reference: number | undefined,
) {
  if (value == null || reference === undefined || reference <= 0) return null;
  return value / reference - 1;
}

function formatSignedPrice(value: number, currency: string, locale: Locale = "zh-CN") {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${formatResearchPrice(Math.abs(value), currency, locale)}`;
}

export function MarketProxyStrip({
  horizon,
  onHorizonChange,
  proxies,
}: MarketProxyStripProps) {
  const { locale } = useI18n();
  const [activeProxy, setActiveProxy] = useState(0);
  const stripRef = useRef<HTMLDivElement>(null);
  const bySymbol = new Map(proxies.map((proxy) => [proxy.symbol, proxy]));
  const ordered = PROXY_ORDER.flatMap((symbol) => {
    const proxy = bySymbol.get(symbol);
    return proxy === undefined ? [] : [proxy];
  });
  const modelCoverage = ordered.filter((proxy) =>
    (proxy.verified_research_by_horizon ?? []).some((item) => item.horizon === horizon),
  ).length;

  function scrollToProxy(index: number) {
    const nextIndex = Math.max(0, Math.min(ordered.length - 1, index));
    const target = stripRef.current?.children.item(nextIndex) as HTMLElement | null;
    target?.scrollIntoView({
      behavior: "smooth",
      block: "nearest",
      inline: "start",
    });
    setActiveProxy(nextIndex);
  }

  return (
    <section
      aria-label={translate(locale, "overview.MarketProxyStrip.uSBroadMarketProxies")}
      className="market-proxy-workbench"
    >
      <header className="market-proxy-workbench__header">
        <div>
          <strong>
            {translate(locale, "overview.MarketProxyStrip.marketPricesAndRuleResearch")}
          </strong>
          <span>
            {modelCoverage === 0
              ? translate(
                  locale,
                  "overview.MarketProxyStrip.modelForecastsForThisHorizonAreUnpublishedCardsShow",
                )
              : translate(
                  locale,
                  "overview.MarketProxyStrip.verifiedComponentsAvailableForProxiesAtThisHorizonExpand",
                  { modelCoverage: modelCoverage, count: ordered.length },
                )}
          </span>
        </div>
        <div
          aria-label={translate(locale, "overview.MarketProxyStrip.forecastHorizon")}
          className="market-proxy-horizons"
          role="group"
        >
          <span>{translate(locale, "overview.MarketProxyStrip.forecastHorizon")}</span>
          {([1, 5, 20] as const).map((value) => (
            <button
              aria-pressed={horizon === value}
              key={value}
              onClick={() => onHorizonChange(value)}
              type="button"
            >
              {translate(locale, "overview.MarketProxyStrip.next")} {value}{" "}
              {translate(locale, "overview.MarketProxyStrip.tradingDays")}
            </button>
          ))}
        </div>
      </header>
      <div className="market-proxy-carousel-status">
        <span>
          {Math.min(activeProxy + 1, ordered.length)}/{ordered.length}
        </span>
        <div>
          <button
            aria-label={translate(
              locale,
              "overview.MarketProxyStrip.previousMarketProxy",
            )}
            disabled={activeProxy <= 0}
            onClick={() => scrollToProxy(activeProxy - 1)}
            type="button"
          >
            <ChevronLeft aria-hidden="true" size={17} strokeWidth={1.5} />
          </button>
          <button
            aria-label={translate(locale, "overview.MarketProxyStrip.nextMarketProxy")}
            disabled={activeProxy >= ordered.length - 1}
            onClick={() => scrollToProxy(activeProxy + 1)}
            type="button"
          >
            <ChevronRight aria-hidden="true" size={17} strokeWidth={1.5} />
          </button>
        </div>
      </div>
      <div
        className="market-proxy-strip"
        onScroll={(event) => {
          const container = event.currentTarget;
          const children = Array.from(container.children) as HTMLElement[];
          if (children.length === 0) return;
          const closest = children.reduce(
            (best, child, index) => {
              const distance = Math.abs(child.offsetLeft - container.scrollLeft);
              return distance < best.distance ? { distance, index } : best;
            },
            { distance: Number.POSITIVE_INFINITY, index: 0 },
          );
          setActiveProxy(closest.index);
        }}
        ref={stripRef}
      >
        {ordered.map((proxy) => {
          const direction =
            proxy.direction === null
              ? null
              : getDirectionPresentation(locale)[proxy.direction];
          const DirectionIcon = direction?.Icon;
          const symbol = displaySymbol(proxy.symbol);
          const evidence = (proxy.verified_research_by_horizon ?? []).find(
            (item) => item.horizon === horizon,
          );
          const projection = evidence?.price_projection;
          const currentClose = proxy.last_close ?? proxy.trend.at(-1);
          const band = projection?.endpoint_price_band;
          const q50Return = projectedReturn(band?.q50, projection?.reference_price);
          const q50Change =
            band === null ||
            band === undefined ||
            projection === null ||
            projection === undefined
              ? null
              : band.q50 - projection.reference_price;
          const currency = projection?.currency ?? "USD";
          const referenceTime = proxy.as_of;
          const riskSummary = buildDecisionRiskSummary(
            {
              breakoutDownProbability:
                evidence?.values.breakout_down_probability ?? null,
              breakoutUpProbability: evidence?.values.breakout_up_probability ?? null,
              maeReturn: evidence?.values.mae_q50 ?? null,
              mfeReturn: evidence?.values.mfe_q50 ?? null,
              q10Price: band?.q10 ?? null,
              q50Price: band?.q50 ?? null,
              q90Price: band?.q90 ?? null,
              referencePrice: projection?.reference_price ?? null,
            },
            locale,
          );
          return (
            <article className="market-proxy" key={proxy.symbol}>
              <div className="market-proxy__identity">
                <strong data-testid="market-proxy-symbol">{symbol}</strong>
                <span>{getRegimeLabels(locale)[proxy.regime]}</span>
                {modelCoverage > 0 ? <ResearchBoundaryLabel /> : null}
              </div>
              <div className="market-proxy__signal">
                {proxy.regime_probability == null ? null : (
                  <span className="market-proxy__regime-probability">
                    <span>
                      {translate(locale, "overview.MarketProxyStrip.regimeProbability")}
                    </span>
                    <ProbabilityValue value={proxy.regime_probability} />
                  </span>
                )}
                {direction === null || DirectionIcon === undefined ? null : (
                  <span
                    className={`market-proxy__direction market-proxy__direction--${proxy.direction}`}
                  >
                    <DirectionIcon aria-hidden="true" size={15} strokeWidth={1.5} />
                    {translate(
                      locale,
                      "overview.MarketProxyStrip.researchDirection",
                    )}{" "}
                    {direction.label}
                  </span>
                )}
              </div>
              <MarketTrendSparkline
                directionLabel={
                  direction?.label ??
                  translate(
                    locale,
                    "overview.MarketProxyStrip.directionModelUnpublished",
                  )
                }
                symbol={symbol}
                tone={
                  proxy.direction === "up"
                    ? "up"
                    : proxy.direction === "down"
                      ? "down"
                      : "neutral"
                }
                trend={proxy.trend}
              />
              <div className="market-proxy__current-price">
                <span>
                  {translate(
                    locale,
                    "overview.MarketProxyStrip.marketCloseSamePriceBasisAsTrend",
                  )}
                </span>
                <strong>
                  {currentClose === undefined || currentClose <= 0
                    ? translate(locale, "overview.MarketProxyStrip.noCurrentPrice")
                    : formatResearchPrice(currentClose, currency, locale)}
                </strong>
              </div>
              <div className="market-proxy__session-meta">
                <div className="market-proxy__session-return">
                  <span>
                    {translate(locale, "overview.MarketProxyStrip.sessionChange")}
                  </span>
                  <ReturnValue
                    label={translate(locale, "overview.MarketProxyStrip.sessionChange")}
                    value={proxy.session_return}
                  />
                </div>
                <time dateTime={referenceTime}>
                  {formatUsMarketTime(referenceTime, locale)}
                </time>
              </div>
              <details className="market-proxy__model-details">
                <summary>
                  {translate(locale, "overview.MarketProxyStrip.modelResearchDetails")}{" "}
                  {evidence
                    ? translate(
                        locale,
                        "overview.MarketProxyStrip.componentEvidenceAvailable",
                      )
                    : translate(locale, "overview.OpportunityTable.unpublished")}
                </summary>
                <section
                  aria-label={
                    projection === undefined
                      ? undefined
                      : translate(
                          locale,
                          "overview.MarketProxyStrip.researchForecastOfTheCloseTradingDaysAhead",
                          { horizon: horizon },
                        )
                  }
                  className="market-proxy__forecast"
                >
                  <div className="market-proxy__forecast-heading">
                    <span>
                      {translate(locale, "overview.MarketProxyStrip.next")} {horizon}{" "}
                      {translate(
                        locale,
                        "overview.MarketProxyStrip.tradingDayForecastMedianPrice",
                      )}
                    </span>
                    {band === null || band === undefined ? (
                      <strong className="numeric-value--unavailable">
                        {researchPriceUnavailableLabel(
                          projection?.endpoint_price_unavailable_reason,
                          horizon,
                          locale,
                        )}
                      </strong>
                    ) : (
                      <>
                        <strong>
                          {formatResearchPrice(band.q50, currency, locale)}
                        </strong>
                        <span className="market-proxy__forecast-change">
                          {q50Change === null
                            ? null
                            : formatSignedPrice(q50Change, currency, locale)}
                          <ReturnValue value={q50Return} />
                        </span>
                      </>
                    )}
                  </div>
                  {band === null || band === undefined || projection == null ? null : (
                    <PriceIntervalTrack
                      currency={currency}
                      q10={band.q10}
                      q50={band.q50}
                      q90={band.q90}
                      referencePrice={projection.reference_price}
                    />
                  )}
                  <div className="market-proxy__forecast-footnote">
                    {band != null && projection != null ? (
                      <span>
                        {translate(
                          locale,
                          "overview.MarketProxyStrip.modelReferencePriceAndQ50AreMarked",
                        )}
                      </span>
                    ) : null}
                    <span>
                      {band == null
                        ? translate(
                            locale,
                            "overview.MarketProxyStrip.q10Q90IntervalUnavailable",
                          )
                        : `q10–q90 ${formatResearchPrice(
                            band.q10,
                            currency,
                            locale,
                          )}–${formatResearchPrice(band.q90, currency, locale)}`}
                    </span>
                  </div>
                  <div
                    aria-label={riskSummary.headline + "." + riskSummary.q10Disclaimer}
                    className={`market-proxy__risk-summary market-proxy__risk-summary--${riskSummary.availability}`}
                    role="note"
                  >
                    <strong>{riskSummary.headline}</strong>
                    {riskSummary.evidence[0] === undefined ? null : (
                      <span>{riskSummary.evidence[0]}</span>
                    )}
                    <small>{riskSummary.q10Disclaimer}</small>
                  </div>
                </section>
              </details>
            </article>
          );
        })}
      </div>
    </section>
  );
}
