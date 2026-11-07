import { detailText } from "../i18n/details";
import { ResearchApiError } from "../api/client";
import { useI18n } from "../i18n/context";

export function OriginalDetails({
  value,
}: {
  value: string | Error | null | undefined;
}) {
  const { t, locale } = useI18n();
  const detail =
    value instanceof ResearchApiError
      ? [value.code, value.requestId, value.message].filter(Boolean).join("\n")
      : value instanceof Error
        ? value.message
        : value;
  return detail ? (
    <details>
      <summary>{t("common.originalDetails")}</summary>
      <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{detailText(detail, locale)}</pre>
    </details>
  ) : null;
}
