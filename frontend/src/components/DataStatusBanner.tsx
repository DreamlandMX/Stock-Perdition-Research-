import { translate, type Locale } from "../i18n/core";
import { useI18n } from "../i18n/context";
import { CircleSlash2, Clock3, CloudOff, DatabaseZap, TriangleAlert, } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { components } from "../api/generated/schema";
type ServingStatus = components["schemas"]["ServingStatus"];
type BannerStatus = Extract<ServingStatus, "delayed" | "stale" | "offline" | "partial" | "unavailable">;
interface StatusPresentation {
    Icon: LucideIcon;
    message: string;
}
const STATUS_PRESENTATION = (locale: Locale): Record<BannerStatus, StatusPresentation> => ({
    delayed: { Icon: Clock3, message: translate(locale, "common.shared.dataDelayed") },
    stale: { Icon: DatabaseZap, message: translate(locale, "common.shared.dataIsStale") },
    offline: { Icon: CloudOff, message: translate(locale, "common.shared.currentlyOffline") },
    partial: { Icon: TriangleAlert, message: translate(locale, "common.shared.incompleteData") },
    unavailable: { Icon: CircleSlash2, message: translate(locale, "common.shared.dataUnavailable") },
});
export interface DataStatusBannerProps {
    lastSuccessfulAt: string;
    status: ServingStatus;
    timestampLabel?: string;
}
function isBannerStatus(status: ServingStatus, locale: Locale = "zh-CN"): status is BannerStatus {
    return status in STATUS_PRESENTATION(locale);
}
export function DataStatusBanner({ lastSuccessfulAt, status, timestampLabel, }: DataStatusBannerProps) {
    const { locale } = useI18n();
    timestampLabel ??= translate(locale, "common.shared.lastSuccessfulData");
    if (!isBannerStatus(status, locale)) {
        return null;
    }
    const { Icon, message } = STATUS_PRESENTATION(locale)[status];
    return (<div aria-label={translate(locale, "common.shared.globalDataStatus")} className={`data-status-banner data-status-banner--${status}`} role="status">
      <Icon aria-hidden="true" size={18} strokeWidth={1.5}/>
      <strong>{message}</strong>
      <span>
        {timestampLabel}: <time dateTime={lastSuccessfulAt}>{lastSuccessfulAt}</time>
      </span>
      {status === "offline" ? (<span>{"" + (translate(locale, "common.shared.retainedSnapshotsAreViewOnlyPredictionGatingRemains") + "")}</span>) : null}
    </div>);
}
