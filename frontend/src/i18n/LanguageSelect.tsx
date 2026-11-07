import { useI18n } from "./context";
import { resolveLocale } from "./core";

export function LanguageSelect() {
  const { locale, setLocale, t } = useI18n();
  return (
    <select className="language-select" aria-label={t("common.language")} value={locale}
      onChange={event => setLocale(resolveLocale(event.target.value))}>
      <option value="zh-CN">简体中文</option>
      <option value="en-US">English</option>
    </select>
  );
}
