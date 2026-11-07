import { translate } from "../i18n/core";
import { useI18n } from "../i18n/context";
export interface ProbabilityValueProps {
    unavailableLabel?: string;
    value: number | null;
}
export function ProbabilityValue({ unavailableLabel, value, }: ProbabilityValueProps) {
    const { locale } = useI18n();
    unavailableLabel ??= translate(locale, "common.shared.unavailable");
    if (value === null || !Number.isFinite(value)) {
        return (<span className="numeric-value numeric-value--unavailable">
        {unavailableLabel}
      </span>);
    }
    return <span className="numeric-value">{`${(value * 100).toFixed(1)}%`}</span>;
}
