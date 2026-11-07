import type { components } from "../src/api/generated/schema";
import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

test.skip(
  process.env.UI_V6_LIVE_QA !== "1",
  "Live-server acceptance is opt-in; use UI_V6_LIVE_QA=1.",
);
const output = resolve("var/ui-v6-implementation/screenshots");
async function waitForPageData(page: Page, name: string) {
  await expect(page.getByLabel("所选快照身份")).toContainText("固定快照", {
    timeout: 90_000,
  });
  await expect(page.getByLabel("正在查询数据状态")).toHaveCount(0);
  if (name === "screen") {
    await expect(
      page.getByRole("table", { name: "已验证研究结果（不可执行）" }),
    ).toBeVisible({ timeout: 90_000 });
    await expect(page.locator(".screen-results tbody tr").first()).toBeVisible();
  } else if (name === "health") {
    await expect(page.getByRole("table", { name: "异常优先级列表" })).toBeVisible({
      timeout: 90_000,
    });
    await expect(page.getByRole("table", { name: "模型任务矩阵" })).toBeVisible({
      timeout: 90_000,
    });
    await expect(page.locator(".health-summary-strip")).not.toContainText("统计中", {
      timeout: 90_000,
    });
    await expect(page.getByLabel("全局运行状态")).not.toContainText("核对中", {
      timeout: 90_000,
    });
  } else if (name === "research") {
    await expect(page.locator(".price-history canvas").first()).toBeVisible({
      timeout: 90_000,
    });
  } else if (name === "backtests") {
    await expect(page.getByRole("table", { name: "模型任务比较" })).toBeVisible({
      timeout: 90_000,
    });
  }
}
for (const width of [1280, 1920]) {
  test(`v6 five-page desktop acceptance ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: width === 1280 ? 800 : 1080 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const result = await request.get("http://127.0.0.1:18765/api/v1/overview", {
      timeout: 90_000,
    });
    expect(result.ok()).toBe(true);
    const overview =
      (await result.json()) as components["schemas"]["ResearchResponse_OverviewReadModel_"];
    const snapshot = overview.resolved_snapshot;
    const query = new URLSearchParams({
      snapshot_id: snapshot.snapshot_id,
      as_of: snapshot.as_of,
      dataset_revision: snapshot.dataset_revision,
      feature_set_version: snapshot.feature_set_version,
      universe_version: snapshot.universe_version,
      mode: "research",
      map_symbol: "US.AXTI",
    });
    await page.goto(`http://127.0.0.1:18765/overview?${query}`);
    await expect(page.locator(".ov-v6")).toBeVisible({ timeout: 30000 });
    await expect(page.getByLabel("历史图表加载状态")).toContainText("真实日线", {
      timeout: 180_000,
    });
    expect(
      await page.locator(".ov-v6-proxies").evaluate((parent) => {
        const bounds = parent.getBoundingClientRect();
        return Array.from(parent.querySelectorAll("canvas")).every(
          (canvas) => canvas.getBoundingClientRect().bottom <= bounds.bottom + 1,
        );
      }),
      "index chart canvases stay inside the first-fold row",
    ).toBe(true);
    await expect(page.locator(".ov-map-cell")).toHaveCount(
      Math.min(36, overview.data.internal_evidence?.market_points.length ?? 0),
    );
    for (const title of [
      "市场宽度历史",
      "市场状态构成",
      "行业轮动象限",
      "20 日通道位置分布",
      "两次快照对比",
      "异常快照对比",
    ])
      await expect(
        page.getByRole("heading", { name: title, exact: true }),
      ).toBeAttached();
    await page
      .locator('[aria-label="地图收益周期"] button')
      .filter({ hasText: "20 日收益" })
      .click();
    await expect(page).toHaveURL(/map_return=20/);
    await page.reload();
    await expect(
      page
        .locator('[aria-label="地图收益周期"] button')
        .filter({ hasText: "20 日收益" }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByLabel("历史图表加载状态")).toContainText("真实日线", {
      timeout: 180_000,
    });
    await page
      .locator('[aria-label="地图收益周期"] button')
      .filter({ hasText: "1 日收益" })
      .click();
    const cells = page.locator(".ov-map-cell");
    await cells.first().focus();
    await page.keyboard.press("ArrowRight");
    await expect(cells.nth(1)).toBeFocused();
    await page.locator(".ov-map-cell").filter({ hasText: "AXTI" }).click();
    await expect(page).toHaveURL(/map_symbol=US.AXTI/);
    const detailHref = await page.locator(".ov-inspector__cta").getAttribute("href");
    expect(detailHref).toContain(encodeURIComponent(snapshot.snapshot_id));
    mkdirSync(output, { recursive: true });
    await page.screenshot({ path: resolve(output, `overview-${width}-fold.png`) });
    await page.screenshot({
      path: resolve(output, `overview-${width}-full.png`),
      fullPage: true,
    });
    await page.locator('a[href="#overview-sectors"]').click();
    await expect(page).toHaveURL(/#overview-sectors$/);
    const selectedSnapshot = snapshot.snapshot_id;
    for (const [name, label, target] of [
      ["screen", "筛选器", ".screen-page"],
      ["research", "标的研究", ".research-page"],
      ["backtests", "回测校准", ".backtests-page"],
      ["health", "模型数据", ".health-page"],
    ] as const) {
      await page
        .getByRole("navigation", { name: "主要导航" })
        .getByRole("link", { name: label, exact: true })
        .click();
      await expect(page.locator(target)).toBeVisible({ timeout: 30_000 });
      await waitForPageData(page, name);
      await expect(page).toHaveURL(new RegExp(`snapshot_id=${selectedSnapshot}`));
      await expect(
        page.locator(target).getByRole("heading", { level: 1 }),
      ).toBeVisible();
      await page.screenshot({ path: resolve(output, `${name}-${width}-fold.png`) });
      await page.screenshot({
        path: resolve(output, `${name}-${width}-full.png`),
        fullPage: true,
      });
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      );
      expect(overflow, `${name} horizontal page overflow at ${width}`).toBe(false);
    }
    await page.getByRole("combobox", { name: "搜索标的" }).fill("AXTI");
    await page.getByRole("combobox", { name: "搜索标的" }).press("Enter");
    await expect(page).toHaveURL(/research\/US.AXTI/);
    await expect(page).toHaveURL(new RegExp(`snapshot_id=${selectedSnapshot}`));
    expect(errors).toEqual([]);
  });
  test(`v6 loaded-page screenshots ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: width === 1280 ? 800 : 1080 });
    const result = await request.get("http://127.0.0.1:18765/api/v1/overview", {
      timeout: 90_000,
    });
    expect(result.ok()).toBe(true);
    const response =
      (await result.json()) as components["schemas"]["ResearchResponse_OverviewReadModel_"];
    const snapshot = response.resolved_snapshot;
    const query = new URLSearchParams({
      snapshot_id: snapshot.snapshot_id,
      as_of: snapshot.as_of,
      dataset_revision: snapshot.dataset_revision,
      feature_set_version: snapshot.feature_set_version,
      universe_version: snapshot.universe_version,
      mode: "research",
    });
    mkdirSync(output, { recursive: true });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    for (const [name, path] of [
      ["screen", "screen"],
      ["research", "research/US.SPY"],
      ["backtests", "backtests"],
      ["health", "health"],
    ] as const) {
      await page.goto(`http://127.0.0.1:18765/${path}?${query}`);
      await waitForPageData(page, name);
      await page.screenshot({ path: resolve(output, `${name}-${width}-fold.png`) });
      await page.screenshot({
        path: resolve(output, `${name}-${width}-full.png`),
        fullPage: true,
      });
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
        `${name} overflow at ${width}`,
      ).toBe(false);
      if (name === "screen") {
        await page
          .getByRole("navigation", { name: "筛选结果分页" })
          .getByRole("button", { name: "下一页" })
          .click();
        await expect(page).toHaveURL(/page=2/);
        await expect(page.getByLabel("筛选结果摘要")).toContainText("第 2 页", {
          timeout: 90_000,
        });
        await page
          .getByLabel("研究排序", { exact: true })
          .selectOption("rule_return_20");
        await page.getByLabel("研究排序方向").selectOption("desc");
        await expect(page).toHaveURL(/sort=rule_return_20/);
        await expect(page.getByLabel("研究排序方向")).toHaveValue("desc");
        await expect(page.getByLabel("筛选结果摘要")).toContainText("第 1 页", {
          timeout: 90_000,
        });
        await page
          .getByRole("combobox", { name: "行业", exact: true })
          .selectOption("technology");
        await expect(page).toHaveURL(/sector=technology/);
        const count =
          response.data.internal_evidence?.market_points.filter(
            (p) => p.sector === "technology",
          ).length ?? 0;
        await expect(page.getByLabel("筛选结果摘要")).toContainText(
          `共 ${count} 个标的`,
          { timeout: 90_000 },
        );
        await expect(
          page.getByRole("columnheader", { name: "当日 / 5日 / 20日历史收益" }),
        ).toHaveAttribute("aria-sort", "descending");
        await expect(page).toHaveURL(new RegExp(`snapshot_id=${snapshot.snapshot_id}`));
      }
    }
    expect(errors).toEqual([]);
  });
}
