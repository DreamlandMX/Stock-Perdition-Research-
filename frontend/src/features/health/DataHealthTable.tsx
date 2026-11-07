import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
import {
  componentLabel,
  datasetLabel,
  completenessLabel,
  freshnessLabel,
  servingStatusLabel,
  formatUsMarketTime,
} from "../../app/presentation";
type DataHealth = components["schemas"]["DataHealth"];
function percent(value: number | null | undefined) {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}
function quota(item: DataHealth, locale: Locale = "zh-CN") {
  if (
    item.quota_used == null &&
    item.quota_total == null &&
    item.quota_remaining == null
  )
    return translate(locale, "health.quotaNotReported");
  return translate(locale, "health.usedTotalRemaining", {
    quota_used: String(item.quota_used ?? translate(locale, "health.unknown")),
    quota_total: String(item.quota_total ?? translate(locale, "health.unknown")),
    quota_remaining: String(
      item.quota_remaining ?? translate(locale, "health.unknown"),
    ),
  });
}
export interface DataHealthTableProps {
  datasetRevision: string;
  items: readonly DataHealth[];
}
export function DataHealthTable({ datasetRevision, items }: DataHealthTableProps) {
  const { locale } = useI18n();
  return (
    <div className="health-table-scroll">
      <table
        aria-label={translate(locale, "health.dataHealthDetails")}
        className="health-table"
      >
        <thead>
          <tr>
            <th scope="col">{translate(locale, "health.componentDataset")}</th>
            <th scope="col">{translate(locale, "health.servingStatus")}</th>
            <th scope="col">{translate(locale, "health.freshness")}</th>
            <th scope="col">{translate(locale, "health.completenessCoverage")}</th>
            <th scope="col">{translate(locale, "health.dataRevision")}</th>
            <th scope="col">{translate(locale, "health.lastSuccess")}</th>
            <th scope="col">{translate(locale, "health.watermark")}</th>
            <th scope="col">{translate(locale, "health.quota")}</th>
            <th scope="col">{translate(locale, "health.quarantine")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => (
            <tr
              key={`${item.component}-${item.dataset}-${item.symbol}-${item.watermark}-${index}`}
            >
              <th scope="row">
                {componentLabel(item.component, locale)}
                <small>
                  {item.symbol ?? translate(locale, "health.global")};{" "}
                  {datasetLabel(item.dataset, locale)}
                </small>
              </th>
              <td title={item.serving_status}>
                {servingStatusLabel(item.serving_status, locale)}
              </td>
              <td title={item.freshness}>{freshnessLabel(item.freshness, locale)}</td>
              <td title={item.completeness}>
                {completenessLabel(item.completeness, locale)} /{" "}
                {percent(item.coverage_rate)}
              </td>
              <td>{datasetRevision}</td>
              <td title={item.last_success_at ?? undefined}>
                {item.last_success_at
                  ? formatUsMarketTime(item.last_success_at, locale)
                  : translate(locale, "health.noSuccessfulRunYet")}
              </td>
              <td title={item.watermark ?? undefined}>
                {item.watermark ? formatUsMarketTime(item.watermark, locale) : "—"}
              </td>
              <td>{quota(item, locale)}</td>
              <td>{item.quarantine_count ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
