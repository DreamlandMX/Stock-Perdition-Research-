import { messages } from "./catalog";

export type Locale = "zh-CN" | "en-US";
export type MessageKey = keyof typeof messages;
export type TextParams = Readonly<Record<string, string | number>>;
export type Translator = (key: MessageKey, params?: TextParams) => string;
export interface LocalizableText { key: string; params?: TextParams | null }
export const LOCALE_STORAGE_KEY = "signal-lab.locale.v1";

export function resolveLocale(value: unknown): Locale {
  return value === "en-US" ? "en-US" : "zh-CN";
}

export function isMessageKey(key: string): key is MessageKey {
  return Object.hasOwn(messages, key);
}

export function translate(locale: Locale, key: MessageKey, params: TextParams = {}): string {
  const entry = messages[key];
  if (entry === undefined) {
    if (import.meta.env.DEV) console.warn(`Missing translation: ${key}`);
    return locale === "en-US" ? messages["common.unavailable"].en : messages["common.unavailable"].zh;
  }
  const template = locale === "en-US" ? entry.en : entry.zh;
  return template.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (placeholder, name: string) => {
    const value = params[name];
    if (value === undefined) {
      if (import.meta.env.DEV) console.warn(`Missing translation parameter: ${key}.${name}`);
      return placeholder;
    }
    return String(value);
  });
}

export function translateRef(locale: Locale, ref: LocalizableText | null | undefined, fallbackKey: MessageKey = "common.unavailable"): string {
  return translate(locale, ref && isMessageKey(ref.key) ? ref.key : fallbackKey, ref?.params ?? undefined);
}
