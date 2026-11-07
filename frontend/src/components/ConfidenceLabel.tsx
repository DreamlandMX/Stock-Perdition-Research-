import { translate, type Locale } from "../i18n/core";
import { useI18n } from "../i18n/context";
import { CircleMinus, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { components } from "../api/generated/schema";
type ConfidenceLevel = components["schemas"]["ConfidenceLevel"];
interface ConfidencePresentation {
    Icon: LucideIcon;
    label: string;
}
const CONFIDENCE_PRESENTATION = (locale: Locale): Record<ConfidenceLevel, ConfidencePresentation> => ({
    high: { Icon: ShieldCheck, label: translate(locale, "common.shared.highConfidence") },
    medium: { Icon: ShieldQuestion, label: translate(locale, "common.shared.mediumConfidence") },
    low: { Icon: ShieldAlert, label: translate(locale, "common.shared.lowConfidence") },
    unavailable: { Icon: CircleMinus, label: translate(locale, "common.shared.confidenceUnavailable") },
});
export interface ConfidenceLabelProps {
    level: ConfidenceLevel;
}
export function ConfidenceLabel({ level }: ConfidenceLabelProps) {
    const { locale } = useI18n();
    const { Icon, label } = CONFIDENCE_PRESENTATION(locale)[level];
    return (<span className={`confidence-label confidence-label--${level}`}>
      <Icon aria-hidden="true" size={16} strokeWidth={1.5}/>
      <span>{label}</span>
    </span>);
}
