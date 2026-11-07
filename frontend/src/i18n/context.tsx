import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { LOCALE_STORAGE_KEY, resolveLocale, translate, type Locale, type Translator } from "./core";

interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: Translator;
}

export function readLocale(): Locale {
  try { return resolveLocale(globalThis.localStorage?.getItem(LOCALE_STORAGE_KEY)); }
  catch { return "zh-CN"; }
}

export function applyDocumentLocale(locale: Locale): void {
  document.documentElement.lang = locale;
  document.title = translate(locale, "common.title");
}

const I18nContext = createContext<I18nContextValue>({
  locale: "zh-CN", setLocale: () => undefined,
  t: (key, params) => translate("zh-CN", key, params),
});

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, updateLocale] = useState<Locale>(readLocale);
  const setLocale = useCallback((next: Locale) => {
    const valid = resolveLocale(next);
    updateLocale(valid);
    try { globalThis.localStorage?.setItem(LOCALE_STORAGE_KEY, valid); }
    catch { /* The selection remains usable when browser storage is disabled. */ }
  }, []);

  useLayoutEffect(() => {
    applyDocumentLocale(locale);
  }, [locale]);
  useLayoutEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key === LOCALE_STORAGE_KEY || event.key === null) {
        updateLocale(resolveLocale(event.newValue));
      }
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);

  const value = useMemo<I18nContextValue>(() => ({
    locale, setLocale, t: (key, params) => translate(locale, key, params),
  }), [locale, setLocale]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue { return useContext(I18nContext); }
