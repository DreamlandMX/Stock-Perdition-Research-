import { expect, test } from "./browser-diagnostics";

test("research desktop and mobile keep discrete horizons and risk evidence", async ({
  page,
}) => {
  await page.goto("/research/US.SPY");
  await expect(page.getByText("1 个交易日", { exact: true })).toBeVisible();
  await expect(page.getByText("5 个交易日", { exact: true })).toBeVisible();
  await expect(page.getByText("20 个交易日", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "原始分位数与 CQR 校准区间" }),
  ).toBeVisible();
  await expect(page).toHaveScreenshot("research-desktop.png");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page).toHaveScreenshot("research-mobile.png");
});
