import { detailText } from "../../i18n/details";
import { useI18n } from "../../i18n/context";
export function ResearchErrorDetail({ message }: { message: string }) {
  const { t, locale } = useI18n();
  return (
    <details className="research-error-detail">
      <summary>{t("research.error.originalDetails")}</summary>
      <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{detailText(message, locale)}</pre>
    </details>
  );
}
