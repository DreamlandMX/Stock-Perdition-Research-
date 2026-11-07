import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { getOptionsCopy, type OptionChartKind } from "./options-copy";

export function ChartExplainer({ kind }: { kind: OptionChartKind }) {
  const { locale } = useI18n();
  const copy = getOptionsCopy(locale)[kind];
  return (
    <details className="option-chart-explainer">
      <summary>
        {translate(locale, "options.ChartExplainer.whatThisChartShows")}
      </summary>
      <p>
        <b>{translate(locale, "options.ChartExplainer.whatItShows")}</b>
        {copy[0]}
      </p>
      <p>
        <b>{translate(locale, "options.ChartExplainer.howToReadIt")}</b>
        {copy[1]}
      </p>
      <p>
        <b>{translate(locale, "options.ChartExplainer.limitations")}</b>
        {copy[2]}
      </p>
    </details>
  );
}
