import { translate } from "../i18n/core";
import { useI18n } from "../i18n/context";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
export interface ReturnValueProps {
    label?: string;
    value: number | null;
}
export function ReturnValue({ label, value }: ReturnValueProps) {
    const { locale } = useI18n();
    label ??= translate(locale, "common.shared.return");
    if (value === null || !Number.isFinite(value)) {
        return (<span aria-label={translate(locale, "common.shared.unavailable2", { label: String(label) })} className="numeric-value numeric-value--unavailable">{" " + (translate(locale, "common.shared.unavailable") + " ")}</span>);
    }
    const roundedPercentage = Number((value * 100).toFixed(1));
    const percentage = roundedPercentage === 0 ? 0 : roundedPercentage;
    const direction = percentage > 0 ? translate(locale, "common.shared.up") : percentage < 0 ? translate(locale, "common.shared.down") : translate(locale, "common.shared.flat");
    const formatted = `${percentage > 0 ? "+" : ""}${percentage.toFixed(1)}%`;
    const className = percentage > 0
        ? "return-value--up"
        : percentage < 0
            ? "return-value--down"
            : "return-value--flat";
    const Icon = percentage > 0 ? ArrowUpRight : percentage < 0 ? ArrowDownRight : Minus;
    return (<span aria-label={`${label}: ${direction} ${formatted}`} className={`return-value numeric-value ${className}`}>
      <Icon aria-hidden="true" size={16} strokeWidth={1.5}/>
      <span>{formatted}</span>
    </span>);
}
