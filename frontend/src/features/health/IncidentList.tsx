import {
  translate,
  translateRef,
  type Locale,
  type LocalizableText,
} from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { AlertTriangle, CircleCheck } from "lucide-react";
import type { components } from "../../api/generated/schema";
import {
  componentLabel,
  datasetLabel,
  formatUsMarketTime,
  isRetainedHistoricalGap,
} from "../../app/presentation";
type DataHealth = components["schemas"]["DataHealth"] & {
  message_ref?: LocalizableText | null;
  action_ref?: LocalizableText | null;
};
function incidentPriority(item: DataHealth) {
  if (isRetainedHistoricalGap(item)) return 2;
  if (
    item.readiness === "blocked" ||
    item.serving_status === "offline" ||
    item.connectivity === "disconnected"
  ) {
    return 0;
  }
  if (item.error_class !== null || item.freshness === "stale") {
    if (item.error_class === "historical_source_gap" && item.freshness !== "stale")
      return 2;
    return 1;
  }
  if (
    item.readiness === "degraded" ||
    item.serving_status === "delayed" ||
    item.serving_status === "partial" ||
    item.completeness === "partial"
  ) {
    return 2;
  }
  return 3;
}
function incidentLabel(item: DataHealth, locale: Locale = "zh-CN") {
  if (isRetainedHistoricalGap(item))
    return translate(locale, "health.knownHistoricalGapRetainedForBackfill");
  const priority = incidentPriority(item);
  if (priority === 0) return translate(locale, "health.blocked");
  if (item.error_class === "historical_source_gap" && item.freshness !== "stale")
    return translate(locale, "health.knownHistoricalGapRetainedForBackfill");
  if (item.error_class !== null) return translate(locale, "health.error");
  if (item.freshness === "stale") return translate(locale, "health.stale");
  if (priority === 2) return translate(locale, "health.degraded");
  return translate(locale, "health.healthy");
}
export interface IncidentListProps {
  items: readonly DataHealth[];
}
export function IncidentList({ items }: IncidentListProps) {
  const { locale } = useI18n();
  const sorted = items
    .filter((item) => incidentPriority(item) < 3)
    .sort((left, right) => {
      const priority = incidentPriority(left) - incidentPriority(right);
      return priority === 0 ? left.component.localeCompare(right.component) : priority;
    });
  return (
    <section
      aria-label={translate(locale, "health.incidentsAndActions")}
      className="health-panel health-incidents"
    >
      <header className="health-panel__header">
        <div>
          <h2>{translate(locale, "health.incidentsAndActions")}</h2>
          <p>
            {translate(
              locale,
              "health.currentFailuresComeFirstRetainedHistoricalGapsAre",
            )}
          </p>
        </div>
      </header>
      {sorted.length === 0 ? (
        <p role="status">
          {translate(locale, "health.noIncidents", { count: items.length })}
        </p>
      ) : null}
      <div className="health-table-scroll">
        <table
          aria-label={translate(locale, "health.incidentPriorityList")}
          className="health-table"
        >
          <thead>
            <tr>
              <th scope="col">{translate(locale, "health.status")}</th>
              <th scope="col">{translate(locale, "health.componentDataset")}</th>
              <th scope="col">{translate(locale, "health.issue")}</th>
              <th scope="col">{translate(locale, "health.lastSuccess")}</th>
              <th scope="col">{translate(locale, "health.recommendedAction")}</th>
              <th scope="col">{translate(locale, "health.affectedScope")}</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((item, index) => {
              const priority = incidentPriority(item);
              return (
                <tr
                  data-priority={priority}
                  key={`${item.component}-${item.dataset}-${item.symbol}-${item.watermark}-${index}`}
                >
                  <td>
                    <span className={`health-severity health-severity--${priority}`}>
                      {priority === 3 ? (
                        <CircleCheck aria-hidden="true" size={15} />
                      ) : (
                        <AlertTriangle aria-hidden="true" size={15} />
                      )}
                      {incidentLabel(item, locale)}
                    </span>
                  </td>
                  <th scope="row">
                    {componentLabel(item.component, locale)}
                    <small>
                      {item.symbol ?? translate(locale, "health.global")};{" "}
                      {datasetLabel(item.dataset, locale)}
                    </small>
                  </th>
                  <td>
                    {translateRef(locale, item.message_ref, "health.sourceIssue")}
                    {item.message || item.error_class ? (
                      <details>
                        <summary>{translate(locale, "health.originalDetails")}</summary>
                        <code>{item.error_class}</code>
                        <p>{item.message}</p>
                      </details>
                    ) : null}
                  </td>
                  <td title={item.last_success_at ?? undefined}>
                    {item.last_success_at
                      ? formatUsMarketTime(item.last_success_at, locale)
                      : translate(locale, "health.noSuccessfulRunYet")}
                  </td>
                  <td>
                    {item.action_ref
                      ? translateRef(locale, item.action_ref)
                      : translate(
                          locale,
                          item.action === "reconnect_opend"
                            ? "health.reconnectOpend"
                            : item.action === "wait_for_next_batch"
                              ? "health.waitNextBatch"
                              : item.action
                                ? "health.reviewSource"
                                : "health.continueMonitoring",
                        )}
                    {item.action ? (
                      <details>
                        <summary>{translate(locale, "health.originalDetails")}</summary>
                        {item.action}
                      </details>
                    ) : null}
                  </td>
                  <td>
                    {item.affected_symbols.length > 0
                      ? item.affected_symbols.join(locale === "en-US" ? ", " : "、")
                      : item.affected_datasets.join(locale === "en-US" ? ", " : "、") || "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
