import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";

type RegimeDistributionItem = components["schemas"]["RegimeDistribution"];
type RegimeLabel = components["schemas"]["RegimeLabel"];
type RuleEvidence = components["schemas"]["RuleEvidence"];

export const getRegimeLabels = (locale: Locale = "zh-CN") =>
  ({
    downtrend: translate(locale, "overview.RegimeDistribution.downtrend"),
    invalid: translate(locale, "overview.RegimeDistribution.regimeUndetermined"),
    range: translate(locale, "overview.RegimeDistribution.range"),
    transition: translate(locale, "overview.RegimeDistribution.directionUnconfirmed"),
    uptrend: translate(locale, "overview.SectorMarketMap.uptrend"),
  }) satisfies Record<RegimeLabel, string>;
export const REGIME_LABELS = getRegimeLabels();

export function regimeLabel(
  regime: RegimeLabel,
  ruleEvidence?: Pick<RuleEvidence, "breakout_down_rule" | "breakout_up_rule"> | null,
  locale: Locale = "zh-CN",
) {
  if (regime !== "transition") return getRegimeLabels(locale)[regime];
  if (ruleEvidence?.breakout_up_rule)
    return translate(locale, "overview.RegimeDistribution.upwardBreakoutUnconfirmed");
  if (ruleEvidence?.breakout_down_rule)
    return translate(locale, "overview.RegimeDistribution.downwardBreakoutUnconfirmed");
  return getRegimeLabels(locale).transition;
}

export interface RegimeDistributionProps {
  items: readonly RegimeDistributionItem[];
}

function percentage(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

export function RegimeDistribution({ items }: RegimeDistributionProps) {
  const { locale } = useI18n();
  const total = items.reduce((sum, item) => sum + item.count, 0);
  const dominant = items.reduce<RegimeDistributionItem | null>(
    (current, item) =>
      current === null || item.fraction > current.fraction ? item : current,
    null,
  );

  return (
    <section
      aria-label={translate(
        locale,
        "overview.RegimeDistribution.marketRegimeDistribution",
      )}
      className="overview-section regime-distribution"
    >
      <header className="overview-section__header">
        <h2>
          {translate(locale, "overview.RegimeDistribution.marketRegimeDistribution")}
        </h2>
        <span>
          {translate(locale, "overview.RegimeDistribution.totalSamples")} {total}{" "}
          {translate(locale, "overview.SectorRegimeMatrix.instruments")}
          {dominant == null
            ? ""
            : translate(locale, "overview.RegimeDistribution.dominant", {
                value: getRegimeLabels(locale)[dominant.regime],
              })}
        </span>
      </header>
      <div className="regime-distribution__rows">
        {items.map((item) => (
          <div
            className={`regime-distribution__row regime-state--${item.regime}${
              item.regime === dominant?.regime
                ? " regime-distribution__row--dominant"
                : ""
            }`}
            key={item.regime}
          >
            <span>
              {getRegimeLabels(locale)[item.regime]}
              {item.regime === dominant?.regime ? (
                <small>
                  {translate(locale, "overview.RegimeDistribution.dominant2")}
                </small>
              ) : null}
            </span>
            <progress
              aria-label={`${getRegimeLabels(locale)[item.regime]} ${percentage(item.fraction)}`}
              max={1}
              value={item.fraction}
            />
            <strong>{percentage(item.fraction)}</strong>
            <small>
              {item.count}{" "}
              {translate(locale, "overview.SectorRegimeMatrix.instruments")}
            </small>
          </div>
        ))}
      </div>
    </section>
  );
}
