import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
type FoldMetrics = components["schemas"]["FoldMetrics"];
function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}
export interface FoldMetricsTableProps {
  folds: readonly FoldMetrics[];
}
export function FoldMetricsTable({ folds }: FoldMetricsTableProps) {
  const { locale } = useI18n();
  return (
    <div className="backtest-table-scroll">
      <table
        aria-label={translate(
          locale,
          "backtests.independentOutOfSampleFoldDiagnostics",
        )}
        className="backtest-data-table"
      >
        <thead>
          <tr>
            <th scope="col">{translate(locale, "backtests.outerFold")}</th>
            <th scope="col">{translate(locale, "backtests.evaluationWindow")}</th>
            <th scope="col">{translate(locale, "backtests.samples")}</th>
            <th scope="col">Brier</th>
            <th scope="col">{translate(locale, "backtests.skill")}</th>
            <th scope="col">PR-AUC</th>
            <th scope="col">ECE</th>
            <th scope="col">{translate(locale, "backtests.pinball")}</th>
            <th scope="col">{translate(locale, "backtests.rankingIc")}</th>
            <th scope="col">{translate(locale, "backtests.rawCoverage")}</th>
            <th scope="col">{translate(locale, "backtests.cqrCoverage")}</th>
            <th scope="col">{translate(locale, "backtests.rawWidth")}</th>
            <th scope="col">{translate(locale, "backtests.cqrWidth")}</th>
            <th scope="col">{translate(locale, "backtests.tailFailures")}</th>
            <th scope="col">{translate(locale, "backtests.survivalBrier")}</th>
            <th scope="col">C-index</th>
            <th scope="col">{translate(locale, "backtests.windowStatus")}</th>
          </tr>
        </thead>
        <tbody>
          {folds.map((fold) => (
            <tr
              className={fold.worst_window ? "is-worst-window" : undefined}
              key={fold.fold_id}
            >
              <th scope="row">{fold.fold_id}</th>
              <td>
                {fold.evaluation_start.slice(0, 10)} –{" "}
                {fold.evaluation_end.slice(0, 10)}
              </td>
              <td>n={fold.sample_count.toLocaleString(locale)}</td>
              <td>{fold.brier_score.toFixed(3)}</td>
              <td>{fold.brier_skill.toFixed(3)}</td>
              <td>{fold.pr_auc.toFixed(3)}</td>
              <td>{fold.expected_calibration_error.toFixed(3)}</td>
              <td>{fold.pinball_loss.toFixed(3)}</td>
              <td>{fold.ranking_ic.toFixed(3)}</td>
              <td>{percent(fold.raw_interval_coverage)}</td>
              <td>{percent(fold.interval_coverage)}</td>
              <td>{percent(fold.raw_interval_width)}</td>
              <td>{percent(fold.interval_width)}</td>
              <td>{percent(fold.tail_failure_rate)}</td>
              <td>{fold.survival_brier.toFixed(3)}</td>
              <td>{fold.concordance_index.toFixed(3)}</td>
              <td>
                {fold.worst_window ? (
                  <strong>{translate(locale, "backtests.worstWindow")}</strong>
                ) : (
                  translate(locale, "backtests.regularOuterFold")
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
