import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
import { modelStatusLabel, reasonLabel, taskLabel } from "../../app/presentation";
type ModelRow = components["schemas"]["ModelTaskMatrixRow"];
export interface ModelComparisonTableProps {
  rows: readonly ModelRow[];
}
export function ModelComparisonTable({ rows }: ModelComparisonTableProps) {
  const { locale } = useI18n();
  return (
    <div className="backtest-table-scroll">
      <table
        aria-label={translate(locale, "backtests.modelTaskComparison")}
        className="backtest-data-table"
      >
        <thead>
          <tr>
            <th scope="col">{translate(locale, "backtests.task")}</th>
            <th scope="col">{translate(locale, "backtests.horizon")}</th>
            <th scope="col">{translate(locale, "backtests.assetGroup")}</th>
            <th scope="col">{translate(locale, "backtests.baseline")}</th>
            <th scope="col">{translate(locale, "backtests.challenger")}</th>
            <th scope="col">{translate(locale, "backtests.champion")}</th>
            <th scope="col">{translate(locale, "backtests.calibrationVersion")}</th>
            <th scope="col">{translate(locale, "backtests.status")}</th>
            <th scope="col">{translate(locale, "backtests.reasonNotPromoted")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.task}-${row.horizon}-${row.asset_group}`}>
              <th scope="row" title={row.task}>
                {taskLabel(row.task, locale)}
              </th>
              <td>
                {row.horizon}
                {" " + translate(locale, "backtests.days")}
              </td>
              <td>{row.asset_group}</td>
              <td>{row.baseline_version}</td>
              <td>{row.challenger_version ?? translate(locale, "backtests.none")}</td>
              <td>
                {row.champion_version ?? translate(locale, "backtests.noneNotPromoted")}
              </td>
              <td>
                {row.calibration_version ?? translate(locale, "backtests.notProvided")}
              </td>
              <td title={row.status}>{modelStatusLabel(row.status, locale)}</td>
              <td title={row.unavailable_reason ?? undefined}>
                {(row.failure_codes ?? []).length > 0
                  ? row.failure_codes
                      ?.map((value) => reasonLabel(value, locale))
                      .join(locale === "en-US" ? "; " : "；")
                  : row.unavailable_reason == null
                    ? "—"
                    : reasonLabel(row.unavailable_reason, locale)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
