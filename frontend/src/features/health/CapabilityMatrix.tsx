import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
import {
  assetLabel,
  capabilityStatusLabel,
  degradationLabel,
  eligibilityLabel,
  permissionLabel,
  pointInTimeLabel,
  reasonLabel,
  timeframeLabel,
} from "../../app/presentation";
type Capability = components["schemas"]["DataCapability"];
function quota(capability: Capability, locale: Locale = "zh-CN") {
  if (
    capability.quota_remaining == null &&
    capability.quota_total == null &&
    capability.safety_budget == null
  ) {
    return translate(locale, "health.quotaNotReported");
  }
  const safety =
    capability.safety_budget == null
      ? ""
      : translate(locale, "health.safetyBudget", {
          safety_budget: String(capability.safety_budget),
        });
  return translate(locale, "health.remainingTotal", {
    quota_remaining: String(
      capability.quota_remaining ?? translate(locale, "health.unknown"),
    ),
    quota_total: String(capability.quota_total ?? translate(locale, "health.unknown")),
    safety: String(safety),
  });
}
function historyDepth(capability: Capability, locale: Locale = "zh-CN") {
  return `${capability.earliest_available_at ?? translate(locale, "health.historyStartNotReported")} → ${capability.latest_available_at ?? translate(locale, "health.historyEndNotReported")}`;
}
export interface CapabilityMatrixProps {
  capabilities: readonly Capability[];
}
export function CapabilityMatrix({ capabilities }: CapabilityMatrixProps) {
  const { locale } = useI18n();
  return (
    <div className="health-table-scroll">
      <table
        aria-label={translate(locale, "health.opendDataCapabilityMatrix")}
        className="health-table"
      >
        <thead>
          <tr>
            <th scope="col">{translate(locale, "health.interface")}</th>
            <th scope="col">{translate(locale, "health.marketAssetTimeframe")}</th>
            <th scope="col">{translate(locale, "health.permissionStatus")}</th>
            <th scope="col">{translate(locale, "health.historyDepth")}</th>
            <th scope="col">{translate(locale, "health.quota")}</th>
            <th scope="col">
              {translate(locale, "health.pointInTimeTrainingEligibility")}
            </th>
            <th scope="col">{translate(locale, "health.eligibilityReason")}</th>
            <th scope="col">{translate(locale, "health.degradationPolicy")}</th>
          </tr>
        </thead>
        <tbody>
          {capabilities.map((capability) => (
            <tr
              data-training-eligibility={capability.training_eligibility}
              key={`${capability.interface}-${capability.market}-${capability.asset_type}-${capability.timeframe}`}
            >
              <th scope="row">{capability.interface}</th>
              <td>
                {capability.market} / {assetLabel(capability.asset_type, locale)} /{" "}
                {capability.timeframe == null
                  ? translate(locale, "health.timeframeUnspecified")
                  : timeframeLabel(capability.timeframe, locale)}
              </td>
              <td>
                <span title={`${capability.permission_scope} / ${capability.status}`}>
                  {permissionLabel(capability.permission_scope, locale)} /{" "}
                  {capabilityStatusLabel(capability.status, locale)}
                </span>
                {capability.last_error_class === null ? null : (
                  <small>{capability.last_error_class}</small>
                )}
              </td>
              <td>{historyDepth(capability, locale)}</td>
              <td>{quota(capability, locale)}</td>
              <td>
                <span
                  title={`${capability.point_in_time_status} / ${capability.training_eligibility}`}
                >
                  {pointInTimeLabel(capability.point_in_time_status, locale)} /{" "}
                  {eligibilityLabel(capability.training_eligibility, locale)}
                </span>
              </td>
              <td title={capability.training_eligibility_reason}>
                {reasonLabel(capability.training_eligibility_reason, locale)}
              </td>
              <td title={capability.degradation_policy}>
                {degradationLabel(capability.degradation_policy, locale)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
