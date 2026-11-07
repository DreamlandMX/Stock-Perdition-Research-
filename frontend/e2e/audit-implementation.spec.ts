import { writeFile } from "node:fs/promises";
import { expect, test } from "./browser-diagnostics";

test.describe("desktop history recovery", () => {
  test.use({ expectedConsoleErrorIncludes: ["503 (Service Unavailable)"] });
  test("retry preserves snapshot without reloading forecast", async ({
    page,
  }, testInfo) => {
    let unavailable = true;
    let forecasts = 0;
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.endsWith("/forecast")) forecasts += 1;
    });
    await page.route("**/api/v1/instruments/*/history?**", async (route) => {
      if (!unavailable) {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 503,
        json: {
          request_id: "desktop-history-retry",
          generated_at: "2026-07-10T20:00:00Z",
          data_status: "degraded",
          error_code: "history_temporarily_unavailable",
          message: "Temporary history failure",
          retryable: true,
          affected_scope: ["history"],
        },
      });
    });
    await page.goto("/research/US.SPY");
    const retry = page.getByRole("button", { name: "重试价格历史" });
    await expect(retry).toBeVisible();
    await expect(page).toHaveURL(/snapshot_id=/);
    await page.waitForLoadState("networkidle");
    const original = page.url();
    const forecastCount = forecasts;
    await retry.screenshot({ path: testInfo.outputPath("history-retry.png") });
    unavailable = false;
    await retry.click();
    await expect(page.getByRole("combobox", { name: "价格图显示范围" })).toBeVisible();
    await expect(retry).toHaveCount(0);
    expect(page.url()).toBe(original);
    expect(forecasts).toBe(forecastCount);
    await expect(page.getByRole("combobox", { name: "价格图显示范围" })).toBeFocused();
  });
});

test.describe("desktop missing backtest evidence", () => {
  test.use({ expectedConsoleErrorIncludes: ["404 (Not Found)"] });
  test("health link explains current status and back restores the query", async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.route("**/api/v1/backtests?**", (route) =>
      route.fulfill({
        status: 404,
        json: {
          request_id: "desktop-missing-evidence",
          generated_at: "2026-07-10T20:00:00Z",
          data_status: "degraded",
          error_code: "backtest_not_found",
          message: "No matching evidence",
          retryable: false,
          affected_scope: ["backtest"],
        },
      }),
    );
    await page.goto("/backtests?split_version=split-stale&fold=outer-stale&horizon=5");
    await expect(page.getByRole("alert", { name: "回测诊断不可用" })).toBeVisible();
    await expect(
      page.getByText(/健康页显示当前服务状态，不代表本次历史快照的状态/),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "重试只读诊断" })).toHaveCount(0);
    const original = page.url();
    await expect(
      page
        .getByRole("table", { name: "模型任务比较" })
        .getByRole("rowheader", { name: "方向预测" }),
    ).toHaveAttribute("title", "forecast_direction");
    await expect(page.getByText("正在查询数据日期", { exact: true })).toHaveCount(0);
    await page.screenshot({
      path: testInfo.outputPath("desktop-backtest-missing.png"),
      fullPage: true,
    });
    await page.getByRole("link", { name: "检查模型与数据健康" }).click();
    await expect(
      page.getByRole("heading", { name: "模型与数据健康", level: 1 }),
    ).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(original);
    await expect(page.getByRole("alert", { name: "回测诊断不可用" })).toBeVisible();
    expect(new URL(page.url()).searchParams.get("fold")).toBe("outer-stale");
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
    ).toBe(false);
  });
});

test("desktop empty results recover without clearing research context", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.route("**/api/v1/screen?**", async (route) => {
    const response = await route.fetch();
    if (new URL(route.request().url()).searchParams.get("sector") === "technology") {
      const body = (await response.json()) as {
        data: { items: unknown[]; total: number };
      };
      body.data.items = [];
      body.data.total = 0;
      await route.fulfill({ response, json: body });
    } else {
      await route.fulfill({ response });
    }
  });
  await page.goto(
    "/screen?mode=research&sector=technology&sort=verified_q10&direction=desc&horizon=5",
  );
  await expect(page.getByText("暂无匹配标的", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/snapshot_id=/);
  const snapshot = new URL(page.url()).searchParams.get("snapshot_id");
  await page.getByRole("button", { name: "清除筛选条件，保留排序与快照" }).click();
  await expect(
    page.getByRole("table", { name: "已验证研究结果（不可执行）" }),
  ).toBeVisible();
  const params = new URL(page.url()).searchParams;
  expect(params.get("sector")).toBeNull();
  expect(params.get("snapshot_id")).toBe(snapshot);
  expect(params.get("sort")).toBe("verified_q10");
  expect(params.get("horizon") ?? "5").toBe("5");
  await expect(page.getByRole("combobox", { name: "研究排序方向" })).toHaveValue(
    "desc",
  );
  await expect(page.getByText("暂无匹配标的", { exact: true })).toHaveCount(0);
});

test("phone capability matrix exposes its rightmost columns by scrolling", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/health");
  const table = page.getByRole("table", { name: "OpenD 数据能力矩阵" });
  await expect(table).toBeVisible();
  const scroll = table.locator("..");
  await scroll.evaluate((element) => {
    element.scrollLeft = element.scrollWidth;
  });
  await expect
    .poll(() => scroll.evaluate((element) => element.scrollLeft))
    .toBeGreaterThan(0);
  const end = await table.getByRole("columnheader", { name: "降级策略" }).boundingBox();
  expect(end!.x).toBeGreaterThanOrEqual(0);
  expect(end!.x + end!.width).toBeLessThanOrEqual(390);
  await scroll.screenshot({
    path: testInfo.outputPath("capability-right-columns.png"),
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
  ).toBe(false);
});

test("desktop reconnect preserves expanded results and focus without reload", async ({ page, context }) => {
  test.setTimeout(70_000);
  await page.goto("/screen?mode=research");
  await page.getByRole("button", { name: "展开历史与路径列" }).click();
  const expanded = page.getByRole("button", { name: "收起历史与路径列" });
  await page.waitForLoadState("networkidle");
  const originalUrl = page.url();
  // Let the actual 30-second query freshness expire before reconnecting.
  await page.waitForTimeout(31_000);
  await page.waitForLoadState("networkidle");
  const scroll = await page.evaluate(() => window.scrollY);
  await context.setOffline(true);
  await expect(page.getByText("当前离线", { exact: true })).toBeVisible();
  await expect(expanded).toBeFocused();
  const recovered = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/api/v1/screen" && response.ok(),
  );
  await context.setOffline(false);
  await recovered;
  await expect(page.getByText("当前离线", { exact: true })).toHaveCount(0);
  await expect(expanded).toBeFocused();
  await expect(expanded).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("table", { name: "已验证研究结果（不可执行）" }).getByRole("columnheader")).toHaveCount(9);
  expect(page.url()).toBe(originalUrl);
  expect(await page.evaluate(() => window.scrollY)).toBe(scroll);
});

test("periodic status refresh preserves reading position and expanded comparison", async ({
  page,
}) => {
  test.setTimeout(90_000);
  let screenRequests = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/v1/screen") screenRequests += 1;
  });
  await page.goto("/screen?mode=research");
  await expect(page.getByRole("button", { name: "展开历史与路径列" })).toBeVisible();
  await expect(
    page.getByRole("status", { name: "正在查询数据状态", exact: true }),
  ).toHaveCount(0);
  const toggle = page.getByRole("button", { name: "展开历史与路径列" });
  await toggle.click();
  const expanded = page.getByRole("button", { name: "收起历史与路径列" });
  await page.waitForLoadState("networkidle");
  const table = page.getByRole("table", { name: "已验证研究结果（不可执行）" });
  const originalRows = await table.getByRole("row").allTextContents();
  const originalUrl = page.url();
  const initialRequests = screenRequests;
  const scroll = await page.evaluate(() => window.scrollY);
  for (let cycle = 0; cycle < 2; cycle += 1) {
    await page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/health" && response.ok(),
      { timeout: 40_000 },
    );
    await expect(expanded).toBeFocused();
    await expect(expanded).toHaveAttribute("aria-pressed", "true");
    expect(await page.evaluate(() => window.scrollY)).toBe(scroll);
    expect(await table.getByRole("row").allTextContents()).toEqual(originalRows);
    expect(page.url()).toBe(originalUrl);
    expect(screenRequests).toBe(initialRequests);
  }
});

for (const width of [1280, 1920]) {
  test(`desktop ${width} applied filters retain snapshot when one condition is removed`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1080 });
    await page.goto(
      "/screen?mode=research&sector=technology&asset_type=stock&serving_status=live&sort=verified_q10",
    );
    await expect(page).toHaveURL(/snapshot_id=/);
    const snapshot = new URL(page.url()).searchParams.get("snapshot_id");
    const filters = page.getByLabel("已应用筛选条件");
    await expect(filters.getByRole("button")).toHaveCount(3);
    for (const button of await filters.getByRole("button").all()) {
      const bounds = await button.boundingBox();
      expect(bounds?.height).toBeGreaterThanOrEqual(44);
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    }
    await filters.screenshot({
      path: testInfo.outputPath("desktop-applied-filters.png"),
    });
    await filters.getByRole("button", { name: /移除行业/ }).click();
    await expect(filters.getByRole("button")).toHaveCount(2);
    const params = new URL(page.url()).searchParams;
    expect(params.has("sector")).toBe(false);
    expect(params.get("asset_type")).toBe("stock");
    expect(params.get("serving_status")).toBe("live");
    expect(params.get("sort")).toBe("verified_q10");
    expect(params.get("snapshot_id")).toBe(snapshot);
    await page.reload();
    await expect(filters.getByRole("button")).toHaveCount(2);
    expect(new URL(page.url()).searchParams.get("snapshot_id")).toBe(snapshot);
    expect(new URL(page.url()).searchParams.get("sector")).toBeNull();
    await filters.getByRole("button", { name: /移除/ }).first().click();
    await expect(page.getByRole("heading", { name: "筛选结果" })).toBeFocused();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
    ).toBe(false);
  });
}

test("research filters and sorting survive detail navigation and reload", async ({
  page,
}) => {
  await page.goto(
    "/screen?mode=research&sector=technology&asset_type=stock&serving_status=live&horizon=5&sort=rule_return_20&direction=desc",
  );
  const table = page.getByRole("table", { name: "已验证研究结果（不可执行）" });
  await expect(table).toBeVisible();
  await expect(page).toHaveURL(/snapshot_id=/);
  await expect(
    page.getByRole("status", { name: "正在查询数据状态", exact: true }),
  ).toHaveCount(0);
  const originalUrl = page.url();
  const link = table.getByRole("link").last();
  await link.scrollIntoViewIfNeeded();
  const originalScroll = await page.evaluate(() => window.scrollY);
  await link.click();
  await expect(page).toHaveURL(/\/research\//);
  await page.goBack();
  await expect(page).toHaveURL(originalUrl);
  await expect(table).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => window.scrollY))
    .toBeCloseTo(originalScroll, -1);
  await page.reload();
  await expect(table).toBeVisible();
  await expect(page).toHaveURL(originalUrl);
  await expect
    .poll(() => page.evaluate(() => window.scrollY))
    .toBeCloseTo(originalScroll, -1);
  await expect(page.getByRole("combobox", { name: "行业", exact: true })).toHaveValue(
    "technology",
  );
  await expect(
    page.getByRole("combobox", { name: "资产类型", exact: true }),
  ).toHaveValue("stock");
  await expect(
    page.getByRole("combobox", { name: "研究排序", exact: true }),
  ).toHaveValue("rule_return_20");
});

test("audit changes remain readable on desktop widths", async ({ page }, testInfo) => {
  const measurements: Array<{ route: string; width: number; readyMs: number }> = [];
  for (const width of [1280, 1920]) {
    await page.setViewportSize({ width, height: width === 1280 ? 720 : 1080 });
    for (const [route, heading] of [
      ["overview", "市场总览"],
      ["screen", "研究筛选器"],
      ["research/US.SPY", "US.SPY 标的研究室"],
      ["backtests", "回测与校准诊断"],
      ["health", "模型与数据健康"],
    ] as const) {
      const started = Date.now();
      await page.goto(`/${route}`);
      await expect(
        page.getByRole("heading", { name: heading, level: 1 }),
      ).toBeVisible();
      await expect(page.getByText("正在查询数据日期", { exact: true })).toHaveCount(0);
      if (route === "health") {
        await expect(page.getByRole("table", { name: "模型任务矩阵" })).toBeVisible();
        await expect(
          page.getByRole("table", { name: "OpenD 数据能力矩阵" }),
        ).toBeVisible();
        await expect(page.getByText("统计中", { exact: true })).toHaveCount(0);
      }
      if (route === "backtests") {
        await expect(page.getByText("Deflated Sharpe Ratio")).toBeVisible();
        const governance = page.getByRole("region", { name: "研究治理证据" });
        await expect(governance.getByTitle("insufficient_trial_variance")).toHaveText(
          "试验差异不足",
        );
        await expect(governance.getByTitle("insufficient_blocks")).toHaveText(
          "数据分块不足",
        );
        await expect(governance).toContainText("过拟合概率： 数据分块不足");
      }
      if (route === "overview") {
        await expect(page.locator(".overview-page details").first()).toBeVisible();
      }
      if (route === "screen") {
        await expect(
          page.getByRole("combobox", { name: "研究排序", exact: true }),
        ).toBeVisible();
        if (width >= 1280) {
          const table = page.getByRole("table", { name: "已验证研究结果（不可执行）" });
          await expect(table.getByRole("columnheader")).toHaveCount(6);
          await page.getByRole("button", { name: "展开历史与路径列" }).click();
          await expect(table.getByRole("columnheader")).toHaveCount(9);
          await page.getByRole("button", { name: "收起历史与路径列" }).click();
        }
      }
      if (route.startsWith("research")) {
        await expect(
          page.getByRole("combobox", { name: "价格图显示范围" }),
        ).toHaveValue("60");
        const diagnostics = page.getByText("查看置信、校准与失效明细", { exact: true });
        await expect(diagnostics.locator("..")).not.toHaveAttribute("open", "");
        await diagnostics.click();
        await expect(page.getByText("校准样本", { exact: true })).toBeVisible();
        await diagnostics.click();
        await page.evaluate(() => window.scrollTo(0, 0));
        await expect(
          page.getByText("模型运行与治理详情", { exact: true }).locator(".."),
        ).not.toHaveAttribute("open", "");
      }
      measurements.push({ route, width, readyMs: Date.now() - started });
      const overflow = await page.evaluate(
        () =>
          document.documentElement.scrollWidth > document.documentElement.clientWidth,
      );
      expect(overflow, `${route} at ${width}px`).toBe(false);
      await page.screenshot({
        path: testInfo.outputPath(
          `${route.replaceAll("/", "-")}-${width}-viewport.png`,
        ),
      });
      await page.screenshot({
        path: testInfo.outputPath(`${route.replaceAll("/", "-")}-${width}.png`),
        fullPage: true,
      });
    }
  }
  await writeFile(
    testInfo.outputPath("local-task-baseline.json"),
    JSON.stringify(measurements, null, 2),
  );
  await testInfo.attach("local-task-baseline", {
    body: JSON.stringify(
      {
        environment:
          "local Vite + isolated fixture API; includes assertions and interactions; not Web Vitals",
        measurements,
      },
      null,
      2,
    ),
    contentType: "application/json",
  });
});
