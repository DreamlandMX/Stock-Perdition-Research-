import { expect, test } from "./browser-diagnostics";

async function expectNoPageOverflow(page: import("@playwright/test").Page) {
  const widths = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(widths.scroll).toBeLessThanOrEqual(widths.client);
}

test("desktop, tablet, phone and landscape preserve the approved layout hierarchy", async ({
  page,
}) => {
  for (const viewport of [
    { width: 1440, height: 1024 },
    { width: 900, height: 1024 },
    { width: 390, height: 844 },
    { width: 844, height: 390 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto(viewport.width === 844 ? "/research/US.SPY" : "/overview");
    await expect(page.getByRole("main")).toHaveCount(1);
    await expectNoPageOverflow(page);
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/overview");
  await expect(page.getByRole("navigation", { name: "移动端主要导航" })).toBeVisible();

  await page.emulateMedia({ reducedMotion: "reduce" });
  const duration = await page
    .locator(".app-shell")
    .evaluate((element) => getComputedStyle(element).transitionDuration);
  expect(Number.parseFloat(duration)).toBeLessThanOrEqual(0.00001);
});

test("keyboard navigation exposes a skip link and restores focus", async ({ page }) => {
  await page.goto("/overview");
  await expect(page.getByRole("heading", { name: "市场总览", level: 1 })).toBeVisible();
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "跳至主要内容" });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();
});
