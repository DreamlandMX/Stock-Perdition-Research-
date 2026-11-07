import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
import { ReturnValue } from "../../components/ReturnValue";
import { regimeLabel } from "../../app/presentation";
type SimilarCaseSummary = components["schemas"]["SimilarCaseSummary"];
type SimilarCaseView = components["schemas"]["SimilarCaseView"];
const BREAKOUT_LABELS = (locale: Locale = "zh-CN") =>
  ({
    down: translate(locale, "research.pricehistorychart.downwardBreakout"),
    none: translate(locale, "research.horizonevidence.noBreakout"),
    up: translate(locale, "research.pricehistorychart.upwardBreakout"),
  }) satisfies Record<SimilarCaseView["breakout_result"], string>;
export interface SimilarCasesTableProps {
  cases: readonly SimilarCaseView[];
  summary: SimilarCaseSummary;
}
export function SimilarCasesTable({ cases, summary }: SimilarCasesTableProps) {
  const { locale } = useI18n();
  return (
    <section className="research-card similar-cases">
      <header className="research-card__header">
        <div>
          <span>
            {translate(locale, "research.similarcasestable.historicalNeighborhood")}
          </span>
          <h2>{translate(locale, "research.similarcasestable.similarCases")}</h2>
        </div>
        <span>
          {translate(locale, "research.similarcasestable.effectiveIndependentSamples")}
          <strong>{summary.effective_independent_count.toFixed(1)}</strong>
        </span>
      </header>
      {cases.length === 0 ? (
        <p className="research-card__empty">
          {translate(
            locale,
            "research.similarcasestable.noSimilarCasesAreAvailableForThisSnapshot",
          )}
        </p>
      ) : (
        <div className="research-table-scroll">
          <table
            aria-label={translate(
              locale,
              "research.similarcasestable.historicalSimilarCases",
            )}
          >
            <thead>
              <tr>
                <th scope="col">
                  {translate(locale, "research.optionmodelvsmarket.time")}
                </th>
                <th scope="col">
                  {translate(locale, "research.similarcasestable.distance")}
                </th>
                <th scope="col">
                  {translate(locale, "research.optionpulsechart.status")}
                </th>
                <th scope="col">Q10</th>
                <th scope="col">Q50</th>
                <th scope="col">Q90</th>
                <th scope="col">
                  {translate(locale, "research.similarcasestable.breakoutOutcome")}
                </th>
                <th scope="col">
                  {translate(locale, "research.similarcasestable.worstMae")}
                </th>
              </tr>
            </thead>
            <tbody>
              {cases.map((item) => (
                <tr key={item.case_id}>
                  <td>
                    <time dateTime={item.as_of}>{item.as_of}</time>
                  </td>
                  <td className="numeric-value">{item.distance.toFixed(3)}</td>
                  <td>{regimeLabel(item.regime, locale)}</td>
                  <td>
                    <ReturnValue value={item.return_quantiles.q10} />
                  </td>
                  <td>
                    <ReturnValue value={item.return_quantiles.q50} />
                  </td>
                  <td>
                    <ReturnValue value={item.return_quantiles.q90} />
                  </td>
                  <td>{BREAKOUT_LABELS(locale)[item.breakout_result]}</td>
                  <td>
                    {item.worst_case ? (
                      <strong className="similar-cases__worst">
                        {translate(locale, "research.similarcasestable.yes")}
                      </strong>
                    ) : (
                      translate(locale, "research.similarcasestable.no")
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
