import { expect, test } from "./browser-diagnostics";

test("screen keeps usable filters above the waiting-Champion disclosure", async ({
  page,
}) => {
  await page.goto("/screen");
  await expect(page.getByRole("heading", { name: "研究筛选器" })).toBeVisible();
  await expect(page.getByText("服务端筛选", { exact: true })).toBeVisible();
  await expect(page.getByRole("group", { name: "研究展示层级" })).toBeVisible();
  await expect(page.locator("main table").first()).toBeVisible();
  await expect(page).toHaveScreenshot("screen-professional.png");
});

test("backtests distinguishes evidence state from transport errors", async ({
  page,
}) => {
  await page.goto("/backtests");
  await expect(page.getByRole("heading", { name: "回测与校准诊断" })).toBeVisible();
  await expect(page.locator("main")).not.toContainText("正在加载独立样本外证据");
  await expect(page).toHaveScreenshot("backtests-professional.png");
});

test("health separates data readiness from model readiness", async ({ page }) => {
  await page.goto("/health");
  await expect(page.getByRole("heading", { name: "模型与数据健康" })).toBeVisible();
  const healthPage = page.locator(".health-page");
  await expect(
    healthPage.getByRole("status", { name: "全局运行状态", exact: true }),
  ).toBeVisible();
  await expect(healthPage.getByRole("region", { name: "数据健康" })).toBeVisible();
  await expect(healthPage.getByRole("region", { name: "模型矩阵" })).toBeVisible();
  await expect(page.locator("main")).not.toContainText("正在读取异常队列");
  await expect(page).toHaveScreenshot("health-professional.png");
});
