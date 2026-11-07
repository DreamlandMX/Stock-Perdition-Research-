import { describe, expect, test } from "vitest";
import { messages } from "./catalog";
import { resolveLocale, translate, translateRef } from "./core";

describe("language resources", () => {
  test("defaults to Chinese and accepts only the supported locales", () => {
    expect(resolveLocale(null)).toBe("zh-CN");
    expect(resolveLocale("en-US")).toBe("en-US");
    expect(resolveLocale("en")).toBe("zh-CN");
  });
  test("uses the selected language and a safe unknown-reference fallback", () => {
    expect(translate("en-US", "common.language")).toBe("Interface language");
    expect(translate("zh-CN", "common.language")).toBe("界面语言");
    expect(translateRef("en-US", { key: "not_registered" })).toBe("This content is currently unavailable.");
  });
  test("every entry has both languages and identical named placeholders", () => {
    for (const [key, entry] of Object.entries(messages)) {
      expect(entry.zh.trim(), key).not.toBe("");
      expect(entry.en.trim(), key).not.toBe("");
      expect(entry.en, key).not.toMatch(/[\u3400-\u9fff]|TRANSLATION MISSING/);
      expect(entry.zh, key).not.toMatch(/&(gt|lt|amp);/);
      expect(entry.en, key).not.toMatch(/&(gt|lt|amp);/);
      const params = (text: string) => [...text.matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)].map(match => match[1]).sort();
      expect(params(entry.en), key).toEqual(params(entry.zh));
    }
  });
});
