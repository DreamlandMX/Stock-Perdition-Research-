import { expect, test } from "./browser-diagnostics";
import { installOverviewOpportunities } from "./overview-fixtures";

const DESKTOP_VIEWPORTS = [
  { expectedColumns: 7, height: 720, width: 1280 },
  { expectedColumns: 7, height: 1024, width: 1440 },
  { expectedColumns: 7, height: 1296, width: 2359 },
] as const;

async function expectNoDocumentOverflow(page: import("@playwright/test").Page) {
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
  ).toBe(true);
}

test.describe("professional responsive layout", () => {
  for (const viewport of DESKTOP_VIEWPORTS) {
    test(`overview uses ${viewport.expectedColumns} readable columns at ${viewport.width}`, async ({
      page,
    }) => {
      await installOverviewOpportunities(page);
      await page.setViewportSize(viewport);
      await page.goto("/overview?mode=research&proxy_horizon=5");

      const table = page.getByRole("table", { name: "Top 研究候选" });
      await expect(table).toBeVisible();
      const visibleColumnCount = await table
        .getByRole("columnheader")
        .evaluateAll(
          (headers) =>
            headers.filter((header) => getComputedStyle(header).display !== "none")
              .length,
        );
      expect(visibleColumnCount).toBe(viewport.expectedColumns);

      const overflowCells = await table
        .locator(
          "tbody > tr:not(.opportunity-table__detail-row) > th, tbody > tr:not(.opportunity-table__detail-row) > td",
        )
        .evaluateAll((cells) =>
          cells
            .filter((cell) => getComputedStyle(cell).display !== "none")
            .filter(
              (cell) =>
                cell.scrollWidth > cell.clientWidth + 1 &&
                getComputedStyle(cell).overflow !== "hidden",
            )
            .map((cell) => cell.textContent?.trim().slice(0, 80)),
        );
      expect(overflowCells).toEqual([]);

      const firstToggle = table.locator(".opportunity-table__rank button").first();
      await firstToggle.focus();
      await page.keyboard.press("Enter");
      await expect(firstToggle).toHaveAttribute("aria-expanded", "true");
      await expect(table.getByRole("link", { name: "查看标的研究" })).toBeVisible();
      await expectNoDocumentOverflow(page);
    });
  }

  test("mobile uses cards and keeps every page inside the viewport", async ({
    page,
  }) => {
    await installOverviewOpportunities(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/overview?mode=research&proxy_horizon=5");
    await expect(page.getByTestId("mobile-research-opportunity-list")).toBeVisible();
    await expect(page.getByRole("table", { name: "Top 研究候选" })).toBeHidden();
    await expectNoDocumentOverflow(page);

    for (const route of ["/screen", "/research/US.SPY", "/backtests", "/health"]) {
      await page.goto(route);
      await expect(page.locator("main")).not.toBeEmpty();
      await expectNoDocumentOverflow(page);
    }
  });

  test("screener and health use progressive disclosure", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1024 });
    await page.goto("/screen");
    await expect(page.getByText("服务端筛选", { exact: true })).toBeVisible();
    await expect(page.getByRole("group", { name: "研究展示层级" })).toBeVisible();

    await page.goto("/health");
    const healthPage = page.locator(".health-page");
    await expect(
      healthPage.getByRole("status", { exact: true, name: "全局运行状态" }),
    ).toBeVisible();
    await expect(healthPage.getByRole("region", { name: "数据健康" })).toBeVisible();
    await expect(healthPage.getByRole("region", { name: "模型矩阵" })).toBeVisible();
    await expect(page.getByRole("region", { name: "标的池版本" })).toBeVisible();
    await expectNoDocumentOverflow(page);
  });
});
