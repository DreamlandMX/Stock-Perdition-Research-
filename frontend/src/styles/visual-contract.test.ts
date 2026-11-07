import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "vitest";

function readStyle(name: string) {
  return readFileSync(
    resolve(process.cwd(), `frontend/src/styles/${name}.css`),
    "utf8",
  );
}

describe("approved visual contract", () => {
  test("freezes the charcoal and lavender dark palette and spacing scale", () => {
    const tokens = readStyle("tokens");
    const normalizedTokens = tokens.toUpperCase();

    for (const color of [
      "--COLOR-PAGE: #0E0F11",
      "--COLOR-SURFACE: #15161A",
      "--COLOR-SURFACE-SECONDARY: #1B1C21",
      "--COLOR-DIVIDER: #2C2C34",
      "--COLOR-TEXT: #ECEBF2",
      "--COLOR-TEXT-SECONDARY: #A9A8B6",
      "--COLOR-FOCUS: #B4A7F5",
      "--COLOR-UP: #3ECF8E",
      "--COLOR-DOWN: #F2647A",
      "--COLOR-WARNING: #F0B860",
    ]) {
      expect(normalizedTokens).toContain(color);
    }
    for (const spacing of [
      "--space-1: 4px",
      "--space-2: 8px",
      "--space-3: 12px",
      "--space-4: 16px",
      "--space-6: 24px",
      "--space-8: 32px",
    ]) {
      expect(tokens).toContain(spacing);
    }
    expect(tokens).toContain("--radius-small: 4px");
    expect(tokens).toContain("--radius-medium: 6px");
  });

  test("keeps responsive navigation, safe-area and motion contracts in CSS", () => {
    const css = readStyle("global");

    expect(css).toMatch(/\.primary-navigation--top\s*\{[^}]*flex-direction:\s*row/s);
    expect(css).toMatch(/\.app-shell--sidebar \.app-main\s*\{[^}]*margin-left:\s*0/s);
    expect(css).toMatch(/\.sidebar--rail\s*\{[^}]*width:\s*64px/s);
    expect(css).toContain("env(safe-area-inset-bottom)");
    expect(css).toMatch(/\.mobile-nav__link\s*\{[^}]*min-height:\s*60px/s);
    expect(css).toMatch(
      /@media \(max-width: 767px\)[\s\S]*\.data-status-banner\s*\{[^}]*top:\s*56px/,
    );
    expect(css).toMatch(
      /@media \(max-width: 767px\)[\s\S]*\.ticker-search input\s*\{[^}]*min-height:\s*44px/,
    );
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
  });

  test("limits the reference gradient to the product mark", () => {
    const css = `${readStyle("tokens")}\n${readStyle("global")}`;

    expect(css).not.toMatch(/drop-shadow|perspective|transform-style/i);
  });
});
