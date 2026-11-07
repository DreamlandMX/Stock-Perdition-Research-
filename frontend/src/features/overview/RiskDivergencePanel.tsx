import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useState } from "react";
import type { components } from "../../api/generated/schema";
import { buildOverviewRiskItems } from "./overview-insights";
import type { OptionOverviewResponse } from "../options/useOptionsOverview";
import { optionReasonLabel, optionNumber } from "../options/option-labels";
import { formatUsMarketTime } from "../../app/presentation";

export function RiskDivergencePanel({
  data,
  predictionReadiness,
  options,
}: {
  data: components["schemas"]["OverviewReadModel"];
  predictionReadiness: components["schemas"]["PredictionReadiness"];
  options?: OptionOverviewResponse | undefined;
}) {
  const { locale } = useI18n();
  const [showAll, setShowAll] = useState(false);
  const risks = buildOverviewRiskItems(data, predictionReadiness, locale);
  return (
    <aside
      aria-label={translate(locale, "overview.RiskDivergencePanel.risksAndDivergence")}
      className="overview-section risk-divergence"
    >
      <header className="overview-section__header">
        <h2>{translate(locale, "overview.RiskDivergencePanel.risksAndDivergence")}</h2>
        <span>
          {translate(
            locale,
            "overview.RiskDivergencePanel.fixedRuleSummaryNotTradingAdvice",
          )}
        </span>
      </header>
      <ul data-expanded={showAll}>
        {risks.map((risk) => (
          <li
            className={`risk-divergence__item risk-divergence__item--${risk.tone}`}
            key={risk.key}
          >
            <div>
              <span>{risk.label}</span>
              <strong>{risk.value}</strong>
            </div>
            <small>{risk.detail}</small>
          </li>
        ))}
      </ul>
      <button
        aria-expanded={showAll}
        className="risk-divergence__toggle"
        onClick={() => setShowAll((value) => !value)}
        type="button"
      >
        {showAll
          ? translate(locale, "overview.RiskDivergencePanel.collapseRisks")
          : translate(locale, "overview.RiskDivergencePanel.viewAll5Risks")}
      </button>
      <details open>
        <summary>
          {translate(
            locale,
            "overview.RiskDivergencePanel.optionsRiskAlertsFixedBackendRules",
          )}
        </summary>
        {options ? (
          options.items.some((item) => item.risk_alerts.length > 0) ? (
            <ul>
              {options.items.flatMap((item) =>
                item.risk_alerts.map((risk, index) => (
                  <li key={`${item.symbol}-${risk.rule}-${index}`}>
                    <strong>
                      {item.symbol.replace(/^US\./, "")} ;{" "}
                      {optionReasonLabel(risk.rule, locale)}
                    </strong>
                    <p>
                      {translate(locale, "overview.RiskDivergencePanel.currentValue")}{" "}
                      {risk.rule.startsWith("width_ratio")
                        ? translate(locale, "overview.RiskDivergencePanel.undefined", {
                            number: optionNumber(risk.value, locale),
                          })
                        : translate(
                            locale,
                            "overview.RiskDivergencePanel.percentagePoints",
                            {
                              value: (risk.value * 100).toFixed(3),
                              value2:
                                risk.rule === "term_structure_inverted"
                                  ? translate(
                                      locale,
                                      "overview.RiskDivergencePanel.calendarDay",
                                    )
                                  : "",
                            },
                          )}{" "}
                      {translate(locale, "overview.RiskDivergencePanel.threshold")}{" "}
                      {risk.rule.startsWith("width_ratio")
                        ? translate(locale, "overview.RiskDivergencePanel.undefined", {
                            number: optionNumber(risk.threshold, locale),
                          })
                        : translate(
                            locale,
                            "overview.RiskDivergencePanel.percentagePoints",
                            {
                              value: (risk.threshold * 100).toFixed(3),
                              value2:
                                risk.rule === "term_structure_inverted"
                                  ? translate(
                                      locale,
                                      "overview.RiskDivergencePanel.calendarDay",
                                    )
                                  : "",
                            },
                          )}
                    </p>
                    <small>
                      {risk.market_as_of
                        ? formatUsMarketTime(risk.market_as_of, locale)
                        : translate(
                            locale,
                            "overview.RiskDivergencePanel.marketTimeUnconfirmed",
                          )}
                    </small>
                  </li>
                )),
              )}
            </ul>
          ) : (
            <p className="option-risk-neutral">
              {translate(
                locale,
                "overview.RiskDivergencePanel.availableEvidenceHasNotTriggeredRulesRulesWithMissing",
              )}
            </p>
          )
        ) : (
          <p className="option-risk-neutral">
            {translate(
              locale,
              "overview.RiskDivergencePanel.optionsRiskEvidenceNotLoadedYet",
            )}
          </p>
        )}
      </details>
    </aside>
  );
}
