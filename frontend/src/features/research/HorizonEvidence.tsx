import { ResearchErrorDetail } from "./ResearchErrorDetail";
import { researchEvidenceLabel } from "./research-labels";
import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
import { ProbabilityValue } from "../../components/ProbabilityValue";
import { QuantileIntervals } from "./QuantileIntervals";
type DirectionProbabilities = components["schemas"]["DirectionProbabilities"];
type BreakoutProbabilities = components["schemas"]["BreakoutProbabilities"];
type HorizonForecast = components["schemas"]["HorizonForecast"];
interface ProbabilityGroupProps {
  label: string;
  values: DirectionProbabilities | BreakoutProbabilities;
}
function ProbabilityGroup({ label, values }: ProbabilityGroupProps) {
  const { locale } = useI18n();
  const middleKey = "flat" in values ? "flat" : "none";
  const middleValue = "flat" in values ? values.flat : values.none;
  return (
    <div className="horizon-probabilities__group">
      <h3>{label}</h3>
      <dl>
        <div>
          <dt>{translate(locale, "research.horizonevidence.up")}</dt>
          <dd data-testid="probability-node">
            <ProbabilityValue value={values.up} />
          </dd>
        </div>
        <div>
          <dt>
            {middleKey === "flat"
              ? translate(locale, "research.horizonevidence.flat")
              : translate(locale, "research.horizonevidence.noBreakout")}
          </dt>
          <dd data-testid="probability-node">
            <ProbabilityValue value={middleValue} />
          </dd>
        </div>
        <div>
          <dt>{translate(locale, "research.horizonevidence.down")}</dt>
          <dd data-testid="probability-node">
            <ProbabilityValue value={values.down} />
          </dd>
        </div>
      </dl>
    </div>
  );
}
export interface HorizonEvidenceProps {
  forecast: HorizonForecast;
}
export function HorizonEvidence({ forecast }: HorizonEvidenceProps) {
  const { locale } = useI18n();
  return (
    <section
      aria-label={translate(
        locale,
        "research.horizonevidence.forecastOverTradingDays",
        { horizon: forecast.horizon },
      )}
      className="horizon-evidence"
    >
      <header className="horizon-evidence__header">
        <div>
          <span className="horizon-evidence__eyebrow">
            {translate(locale, "research.horizonevidence.discreteForecastWindow")}
          </span>
          <h2>
            {translate(
              locale,
              forecast.horizon === 1
                ? "research.horizonevidence.tradingDayOne"
                : "research.horizonevidence.tradingDays.withValues",
              {
                horizon: forecast.horizon,
              },
            )}
          </h2>
        </div>
        <dl className="horizon-evidence__metadata">
          <div>
            <dt>{translate(locale, "research.horizonevidence.targetCoverage")}</dt>
            <dd>{(forecast.coverage_target * 100).toFixed(1)}%</dd>
          </div>
          <div>
            <dt>{translate(locale, "research.horizonevidence.intervalMethod")}</dt>
            <dd>{forecast.interval_method}</dd>
          </div>
        </dl>
      </header>

      <div className="horizon-probabilities">
        <ProbabilityGroup
          label={translate(
            locale,
            "research.horizonevidence.forecastDirectionProbabilities",
          )}
          values={forecast.forecast_direction_probabilities}
        />
        <ProbabilityGroup
          label={translate(
            locale,
            "research.horizonevidence.executableDirectionProbabilities",
          )}
          values={forecast.executable_direction_probabilities}
        />
        <ProbabilityGroup
          label={translate(locale, "research.horizonevidence.breakoutProbabilities")}
          values={forecast.breakout_probabilities}
        />
      </div>

      {forecast.horizon === 1 ? (
        <p className="horizon-evidence__confirmation-note">
          {translate(
            locale,
            "research.horizonevidence.a1DayWindowDoesNotProduceRangeExit.withValues",
            { confirmationDays: forecast.range_exit_metadata.confirmation_days },
          )}
        </p>
      ) : (
        <dl className="horizon-evidence__range-exit">
          <div>
            <dt>
              {translate(locale, "research.horizonevidence.rangeExitProbability")}
            </dt>
            <dd data-testid="probability-node">
              <ProbabilityValue value={forecast.range_exit_probability ?? null} />
            </dd>
          </div>
          <div>
            <dt>{translate(locale, "research.horizonevidence.effectiveSamples")}</dt>
            <dd className="numeric-value">
              {forecast.range_exit_metadata.sample_denominator}
            </dd>
          </div>
          <div>
            <dt>{translate(locale, "research.horizonevidence.confirmationRule")}</dt>
            <dd>
              {researchEvidenceLabel(
                forecast.range_exit_metadata.censoring_rule,
                locale,
              ) ??
                (forecast.range_exit_metadata.censoring_rule ===
                "two-close confirmation"
                  ? translate(locale, "research.evidence.consecutiveCloses", {
                      days: forecast.range_exit_metadata.confirmation_days,
                    })
                  : translate(locale, "research.evidence.originalConfirmation"))}
              <ResearchErrorDetail
                message={forecast.range_exit_metadata.censoring_rule}
              />
            </dd>
          </div>
        </dl>
      )}

      <QuantileIntervals forecast={forecast} />
    </section>
  );
}
