import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
import { modelStatusLabel, reasonLabel, taskLabel } from "../../app/presentation";
type ModelRow = components["schemas"]["ModelTaskMatrixRow"];
export interface ModelTaskMatrixProps {
  rows: readonly ModelRow[];
}
export function ModelTaskMatrix({ rows }: ModelTaskMatrixProps) {
  const { locale } = useI18n();
  return (
    <div className="health-table-scroll">
      <table
        aria-label={translate(locale, "health.modelTaskMatrix")}
        className="health-table"
      >
        <thead>
          <tr>
            <th scope="col">{translate(locale, "health.task")}</th>
            <th scope="col">{translate(locale, "health.horizon")}</th>
            <th scope="col">{translate(locale, "health.assetGroup")}</th>
            <th scope="col">{translate(locale, "health.champion")}</th>
            <th scope="col">{translate(locale, "health.challenger")}</th>
            <th scope="col">{translate(locale, "health.baseline")}</th>
            <th scope="col">{translate(locale, "health.calibrationVersion")}</th>
            <th scope="col">{translate(locale, "health.statusReason")}</th>
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
                {" " + translate(locale, "health.days")}
              </td>
              <td>{row.asset_group}</td>
              <td>{row.champion_version ?? translate(locale, "health.notPromoted")}</td>
              <td>
                {row.challenger_version ?? translate(locale, "health.notRegistered")}
              </td>
              <td>{row.baseline_version}</td>
              <td>
                {row.calibration_version ?? translate(locale, "health.notApplicable")}
              </td>
              <td>
                <span title={row.status}>{modelStatusLabel(row.status, locale)}</span>
                {(row.failure_codes ?? []).length > 0 ? (
                  <small title={row.failure_codes?.join(locale === "en-US" ? "; " : "；")}>
                    {row.failure_codes
                      ?.map((value) => reasonLabel(value, locale))
                      .join(locale === "en-US" ? "; " : "；")}
                  </small>
                ) : row.unavailable_reason === null ? null : (
                  <small title={row.unavailable_reason}>
                    {reasonLabel(row.unavailable_reason, locale)}
                  </small>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
