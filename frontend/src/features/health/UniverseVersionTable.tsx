import { translate, translateRef, type LocalizableText } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
import {
  pointInTimeLabel,
  reasonLabel,
  formatUsMarketTime,
} from "../../app/presentation";
type UniverseVersion = components["schemas"]["UniverseVersion"];
export interface UniverseVersionTableProps {
  versions: readonly UniverseVersion[];
}
export function UniverseVersionTable({ versions }: UniverseVersionTableProps) {
  const { locale } = useI18n();
  return (
    <div className="health-table-scroll">
      <table
        aria-label={translate(locale, "health.universeVersionHistory")}
        className="health-table"
      >
        <thead>
          <tr>
            <th scope="col">{translate(locale, "health.version")}</th>
            <th scope="col">{translate(locale, "health.effectivePeriod")}</th>
            <th scope="col">{translate(locale, "health.members")}</th>
            <th scope="col">{translate(locale, "health.pointInTimeStatus")}</th>
            <th scope="col">{translate(locale, "health.changeReasonLimitation")}</th>
          </tr>
        </thead>
        <tbody>
          {versions.map((version) => (
            <tr key={version.universe_version}>
              <th scope="row">{version.universe_version}</th>
              <td>
                {formatUsMarketTime(version.effective_from, locale)} →{" "}
                {version.effective_to
                  ? formatUsMarketTime(version.effective_to, locale)
                  : translate(locale, "health.current")}
              </td>
              <td>{version.member_count}</td>
              <td title={version.point_in_time_status}>
                {pointInTimeLabel(version.point_in_time_status, locale)}
              </td>
              <td>
                {(version as typeof version & { reason_ref?: LocalizableText | null })
                  .reason_ref
                  ? translateRef(
                      locale,
                      (
                        version as typeof version & {
                          reason_ref?: LocalizableText | null;
                        }
                      ).reason_ref,
                    )
                  : reasonLabel(version.reason, locale)}
                <details>
                  <summary>{translate(locale, "health.originalDetails")}</summary>
                  {version.reason}
                </details>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
