import { expect, test } from "./browser-diagnostics";
import { installOverviewOpportunities } from "./overview-fixtures";
import type { components } from "../src/api/generated/schema";

test("overview follows the v6 desktop chapter layout", async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1920, height: 1080 });
  const overviewResponse = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/v1/overview",
  );
  await page.route("**/api/v1/history/batch?*", async (route) => {
    const envelope = (await (
      await overviewResponse
    ).json()) as components["schemas"]["ResearchResponse_OverviewReadModel_"];
    const symbols = new URL(route.request().url()).searchParams.getAll("symbols");
    await route.fulfill({
      json: {
        ...envelope,
        data: {
          items: symbols.map((symbol) => ({
            symbol,
            status: "available",
            error_code: null,
            history: {
              symbol,
              adjustment: "raw",
              timeframe: "1d",
              bars: [],
              events: [],
              gaps: [],
            },
          })),
        },
      },
    });
  });
  await installOverviewOpportunities(page);
  await page.goto("/overview");
  await expect(page.getByLabel("历史图表加载状态")).toContainText("真实日线", {
    timeout: 30_000,
  });
  const workspace = page.getByRole("region", { name: "市场总览证据" });
  await expect(workspace).toHaveAttribute("data-layout", "v6-chapters");
  await expect(page.getByRole("navigation", { name: "主要导航" })).toBeVisible();
  await expect(page.getByRole("region", { name: "四大指数行情" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Top 研究候选" })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "风险与分歧" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "状态分布", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("table", { name: "行业 × 状态矩阵" })).toBeVisible();
  await expect(page.getByRole("contentinfo", { name: "预测版本与延迟" })).toBeVisible();
  await expect(page).toHaveScreenshot("overview-desktop.png");
  await expect(page).toHaveScreenshot("overview-full-page.png", { fullPage: true });
  await expect(page.locator(".ov-v6-more canvas")).toHaveCount(0);
  await page.getByText("更多行业与研究诊断", { exact: true }).click();
  await expect(page.locator(".ov-v6-more canvas").first()).toBeVisible();
});
