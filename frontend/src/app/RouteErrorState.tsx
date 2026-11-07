import { translate, type Locale } from "../i18n/core";
import { useI18n } from "../i18n/context";
import { useEffect, useRef } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { useRouteError } from "react-router-dom";
import { ResearchApiError } from "../api/client";
interface ErrorPresentation {
    action: "health" | "overview" | "retry" | "screen" | "universe";
    actionLabel: string;
    description: string;
    title: string;
}
const ERROR_PRESENTATIONS = (locale: Locale): Record<string, ErrorPresentation> => ({
    network_error: {
        action: "retry",
        actionLabel: translate(locale, "common.shared.retry"),
        description: translate(locale, "common.shared.theReadOnlyResearchApiIsUnreachableSaved"),
        title: translate(locale, "common.shared.currentlyOffline2"),
    },
    not_in_universe: {
        action: "universe",
        actionLabel: translate(locale, "common.shared.viewUniverseVersion"),
        description: translate(locale, "common.shared.thisInstrumentIsOutsideTheVersionedUniverseAt"),
        title: translate(locale, "common.shared.instrumentOutsideTheCurrentUniverseVersion"),
    },
    research_snapshot_unavailable: {
        action: "retry",
        actionLabel: translate(locale, "common.shared.retry"),
        description: translate(locale, "common.shared.noVerifiablePublishedResearchSnapshotIsAvailableFor"),
        title: translate(locale, "common.shared.noResearchSnapshotAvailableYet"),
    },
    snapshot_integrity_error: {
        action: "health",
        actionLabel: translate(locale, "common.shared.viewDataHealth"),
        description: translate(locale, "common.shared.theSnapshotFailedIntegrityChecksItsForecastValues"),
        title: translate(locale, "common.shared.researchSnapshotIntegrityCheckFailed"),
    },
    snapshot_version_conflict: {
        action: "overview",
        actionLabel: translate(locale, "common.shared.returnToTheCurrentVersion"),
        description: translate(locale, "common.shared.theUrlVersionCombinationCannotResolveToA"),
        title: translate(locale, "common.shared.snapshotVersionConflict"),
    },
    unknown_symbol: {
        action: "screen",
        actionLabel: translate(locale, "common.shared.returnToScreener"),
        description: translate(locale, "common.shared.theTickerCouldNotBeResolvedSelectAn"),
        title: translate(locale, "common.shared.instrumentNotFound"),
    },
});
const FALLBACK_PRESENTATION = (locale: Locale): ErrorPresentation => ({
    action: "retry",
    actionLabel: translate(locale, "common.shared.retry"),
    description: translate(locale, "common.shared.theReadOnlyResearchViewCannotLoadDemo"),
    title: translate(locale, "common.shared.researchViewTemporarilyUnavailable"),
});
function presentation(error: unknown, locale: Locale = "zh-CN") {
    if (!(error instanceof ResearchApiError))
        return FALLBACK_PRESENTATION(locale);
    const known = ERROR_PRESENTATIONS(locale)[error.code];
    if (known !== undefined)
        return known;
    return error.status === 404
        ? (ERROR_PRESENTATIONS(locale).unknown_symbol ?? FALLBACK_PRESENTATION(locale))
        : FALLBACK_PRESENTATION(locale);
}
function actionHref(action: ErrorPresentation["action"]) {
    if (action === "screen")
        return "/screen";
    if (action === "health" || action === "universe")
        return "/health";
    return "/overview";
}
export interface RouteErrorStateProps {
    error: unknown;
    headingLevel?: 1 | 2;
    onRetry?: () => void;
}
export function RouteErrorState({ error, headingLevel = 1, onRetry, }: RouteErrorStateProps) {
    const { locale } = useI18n();
    const titleRef = useRef<HTMLHeadingElement>(null);
    const view = presentation(error, locale);
    useEffect(() => titleRef.current?.focus(), [view.title]);
    return (<section aria-label={translate(locale, "common.shared.researchPageError")} className="route-error-state" role="alert">
      <AlertTriangle aria-hidden="true" size={24}/>
      <div>
        {headingLevel === 1 ? (<h1 ref={titleRef} tabIndex={-1}>
            {view.title}
          </h1>) : (<h2 ref={titleRef} tabIndex={-1}>
            {view.title}
          </h2>)}
        <p>{view.description}</p>
        {error instanceof ResearchApiError && error.requestId !== null ? (<small>{"" + (translate(locale, "common.shared.requestId") + " ")}{error.requestId}</small>) : null}
        {error instanceof ResearchApiError && error.affectedScope.length > 0 ? (<small>{"" + (translate(locale, "common.shared.affectedScope") + " ")}{error.affectedScope.join(locale === "en-US" ? ", " : "、")}</small>) : null}
        {view.action === "retry" ? (<button onClick={onRetry ?? (() => window.location.reload())} type="button">
            <RefreshCw aria-hidden="true" size={17}/> {view.actionLabel}
          </button>) : (<a href={actionHref(view.action)}>{view.actionLabel}</a>)}
      </div>
    </section>);
}
export function RouteErrorBoundary() {
    return <RouteErrorState error={useRouteError()}/>;
}
