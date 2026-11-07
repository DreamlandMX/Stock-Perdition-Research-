import { translate } from "../i18n/core";
import { useI18n } from "../i18n/context";
export interface ResearchBoundaryLabelProps {
    scope?: "challenger" | "shadow" | "verified";
}
export function ResearchBoundaryLabel({ scope = "verified", }: ResearchBoundaryLabelProps) {
    const { locale } = useI18n();
    const text = scope === "challenger"
        ? translate(locale, "common.shared.experimentalCandidateNotExecutable") : scope === "shadow"
        ? translate(locale, "common.shared.shadowModelBlindEvaluationIncomplete") : translate(locale, "common.shared.verifiedResearchNotExecutable");
    return (<span aria-label={translate(locale, "common.shared.notATradingRecommendation", { text: String(text) })} className={`research-boundary-label research-boundary-label--${scope}`} title={translate(locale, "common.shared.forQuantitativeResearchRankingAndEvidenceReviewOnly")}>
      {text}
    </span>);
}
