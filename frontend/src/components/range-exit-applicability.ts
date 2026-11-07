import { translate, type Locale } from "../i18n/core";
import type { components } from "../api/generated/schema";
type RegimeLabel = components["schemas"]["RegimeLabel"];
interface RangeExitEvidence {
    tasks: readonly {
        task: string;
    }[];
}
export function rangeExitUnavailableLabel(evidence: RangeExitEvidence | null | undefined, horizon: number, regime: RegimeLabel | null | undefined, locale: Locale = "zh-CN") {
    if (horizon === 1) {
        return translate(locale, "common.shared.notApplicableDisabledForThe1DayTask");
    }
    if (!evidence?.tasks.some((task) => task.task === "range_exit")) {
        return translate(locale, "common.shared.noCandidateExitTaskForThisHorizon");
    }
    if (regime == null || regime === "invalid") {
        return translate(locale, "common.shared.unknownRegimeApplicabilityCannotBeDetermined");
    }
    return regime === "range" ? translate(locale, "common.shared.candidateOutputNotGenerated") : translate(locale, "common.shared.notApplicableNotCurrentlyRangeBound");
}
