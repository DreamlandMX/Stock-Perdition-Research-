import { detailText } from "../../i18n/details";
import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useSearchParams } from "react-router-dom";
import {
  useOptionsExperiments,
  useOptionsCollection,
  useOptionCutoff,
} from "../options/useOptionsOverview";
import { optionReasonLabel } from "../options/option-labels";
import { OptionAblationTable } from "./OptionAblationTable";
import { OptionIntervalCoverageChart } from "./OptionIntervalCoverageChart";
import { formatUsMarketTime } from "../../app/presentation";
export function OptionBacktestsPanel() {
  const { locale } = useI18n();
  const [params] = useSearchParams();
  const cutoff = useOptionCutoff(
    params.get("as_of") ?? undefined,
    params.get("snapshot_id") ?? undefined,
  );
  const query = { as_of: cutoff.asOf };
  const result = useOptionsExperiments(
    {
      ...query,
      snapshot_id: params.get("snapshot_id") ?? undefined,
    },
    cutoff.ready,
  );
  const collection = useOptionsCollection(query, cutoff.ready);
  return (
    <div className="option-backtests" id="options-ablation">
      <section
        className="backtest-panel"
        aria-label={translate(locale, "backtests.optionsFeatureAblation")}
      >
        <header>
          <h2>{translate(locale, "backtests.optionsFeatureAblation")}</h2>
          <p>
            {" " +
              (translate(locale, "backtests.compareStockBaselineB0WithE1E2E3") + " ")}
          </p>
        </header>
        {result.isPending && (
          <p role="status">
            {translate(locale, "backtests.loadingTheOptionsExperimentReport")}
          </p>
        )}
        {result.isError && (
          <div role="alert">
            {" " + translate(locale, "backtests.optionsExperimentFailedToLoad")}
            <details>
              <summary>{translate(locale, "backtests.originalDetails")}</summary>
              {detailText(result.error.message, locale)}
            </details>
            <button type="button" onClick={() => void result.refetch()}>
              {" " + (translate(locale, "backtests.reloadExperiment") + " ")}
            </button>
          </div>
        )}
        {result.data && (
          <>
            {result.data.experiments.length ? (
              result.data.experiments.map((experiment) => (
                <OptionAblationTable
                  key={experiment.experiment_id}
                  experiment={experiment}
                />
              ))
            ) : (
              <>
                <p className="option-empty">
                  {" " +
                    (translate(
                      locale,
                      "backtests.optionsFeaturesHaveNotDemonstratedForecastImprovementNo",
                    ) +
                      " ")}
                </p>
                <p>
                  {" " + (translate(locale, "backtests.collectedTradingDays") + " ")}
                  {collection.data?.collection.history_days ??
                    translate(locale, "backtests.unconfirmed")}
                  {" " +
                    (translate(
                      locale,
                      "backtests.minimumTestDatesMustBeRegisteredInThe",
                    ) +
                      " ")}
                </p>
              </>
            )}
            {result.data.reasons.length > 0 && (
              <p className="option-risk-neutral">
                {result.data.reasons
                  .map((value) => optionReasonLabel(value, locale))
                  .join(locale === "en-US" ? "; " : "；")}
              </p>
            )}
            <small className="option-risk-neutral">
              {" " + (translate(locale, "backtests.readAt") + " ")}
              {formatUsMarketTime(result.data.generated_at, locale)} |{" "}
              {optionReasonLabel(result.data.data_status, locale)}
            </small>
          </>
        )}
      </section>
      {result.data && <OptionIntervalCoverageChart coverage={result.data.coverage} />}
    </div>
  );
}
