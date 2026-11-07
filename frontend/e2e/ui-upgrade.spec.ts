import { writeFile } from "node:fs/promises";
import { expect, test } from "./browser-diagnostics";
import { installOverviewOpportunities } from "./overview-fixtures";

test("overview decision controls preserve snapshot identity and expose ranked evidence", async ({
  page,
}) => {
  await installOverviewOpportunities(page);
  await page.goto("/overview?mode=research&proxy_horizon=5");
  await expect(page.getByRole("heading", { name: "今日异动与研究线索" })).toBeVisible();

  const resolvedUrl = new URL(page.url());
  const snapshotId = resolvedUrl.searchParams.get("snapshot_id");
  expect(snapshotId).toBeTruthy();
  await expect(page.getByText(/大盘代理 \d+ 涨 \d+ 跌/)).toBeVisible();

  await page.getByRole("button", { name: "未来 20 个交易日" }).click();
  await expect(page.getByRole("button", { name: "未来 20 个交易日" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(new URL(page.url()).searchParams.get("snapshot_id")).toBe(snapshotId);
  expect(new URL(page.url()).searchParams.get("proxy_horizon")).toBe("20");

  const table = page.getByRole("table", { name: "Top 研究候选" });
  const stickyHeader = await table
    .getByRole("columnheader", { name: "排名" })
    .evaluate((element) => getComputedStyle(element).position);
  expect(stickyHeader).toBe("sticky");

  const toggles = table.locator(".opportunity-table__rank button");
  expect(await toggles.count()).toBeGreaterThan(0);
  const firstToggle = toggles.first();
  await firstToggle.click();
  await expect(firstToggle).toHaveAttribute("aria-expanded", "true");
  const details = table.locator(".opportunity-evidence-details");
  await expect(details).toContainText("排序依据");
  await expect(details).toContainText("20 日 ATR");
  await expect(details).toContainText("成交量 Z 分数");

  const researchLink = details.getByRole("link", { name: "查看标的研究" });
  await researchLink.click();
  await expect(page).toHaveURL(/\/research\//);
  expect(new URL(page.url()).searchParams.get("snapshot_id")).toBe(snapshotId);
  expect(new URL(page.url()).searchParams.get("horizon")).toBeNull();
});

test("overview mobile prioritizes pulse and the 20-symbol map before progressive evidence", async ({
  page,
}) => {
  await installOverviewOpportunities(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/overview?mode=research&proxy_horizon=5");

  const summary = page.locator(".overview-page__summary");
  const pulse = page.getByRole("region", { name: "今日市场脉搏" });
  const marketMap = page.getByRole("region", { name: "100 标的市场地图" });
  const modeSwitch = page.getByRole("group", { name: "研究展示层级" });
  const periodSwitch = page.getByRole("group", { name: "预测周期" });
  const firstProxy = page.locator(".market-proxy").first();
  const firstMobileRow = page
    .getByTestId("mobile-research-opportunity-list")
    .locator(":scope > li")
    .first();
  for (const element of [summary, pulse, marketMap]) {
    const box = await element.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.y).toBeLessThan(850);
  }

  const mapCells = marketMap.locator(".market-map-cell");
  await expect(mapCells).toHaveCount(100);
  expect(
    await mapCells.evaluateAll(
      (items) =>
        items.filter((item) => getComputedStyle(item).display !== "none").length,
    ),
  ).toBe(20);
  await marketMap.getByRole("button", { name: "展开全部 100 个标的" }).click();
  expect(
    await mapCells.evaluateAll(
      (items) =>
        items.filter((item) => getComputedStyle(item).display !== "none").length,
    ),
  ).toBe(100);
  await marketMap.getByRole("button", { name: "收起至异常优先 20 个" }).click();

  await expect(modeSwitch).toBeVisible();
  await expect(firstMobileRow).toBeVisible();

  const periodButtons = periodSwitch.getByRole("button");
  const buttonBoxes = await periodButtons.evaluateAll((buttons) =>
    buttons.map((button) => {
      const rect = button.getBoundingClientRect();
      return { height: rect.height, width: rect.width };
    }),
  );
  expect(buttonBoxes).toHaveLength(3);
  expect(buttonBoxes.every((box) => box.height >= 44)).toBe(true);
  expect(Math.max(...buttonBoxes.map((box) => box.width))).toBeCloseTo(
    Math.min(...buttonBoxes.map((box) => box.width)),
    0,
  );

  await expect(firstProxy).toBeVisible();
  expect(
    await page
      .locator(".market-proxy-strip")
      .evaluate((element) => element.scrollWidth > element.clientWidth),
  ).toBe(true);
  await expect(firstProxy).toContainText("q10 是模型条件分布的第 10 分位");

  await expect(firstMobileRow).toContainText("#1");
  await firstMobileRow.getByRole("button", { name: "查看完整证据" }).click();
  await expect(
    firstMobileRow.getByRole("button", { name: "收起完整证据" }),
  ).toHaveAttribute("aria-expanded", "true");
  await expect(firstMobileRow).toContainText("通道宽度");

  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
  ).toBe(true);
});

for (const cpuRate of [1, 6]) {
  test(`global instrument search supports keyboard navigation and preserves research identity (CPU ${cpuRate}x)`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(cpuRate === 6 ? 90_000 : 45_000);
    const navigations: Array<{ url: string; at: number }> = [];
    page.on("framenavigated", (frame) => {
      if (frame === page.mainFrame()) {
        navigations.push({ url: frame.url(), at: Date.now() });
      }
    });
    try {
      const cdp = await page.context().newCDPSession(page);
      await cdp.send("Emulation.setCPUThrottlingRate", { rate: cpuRate });
      await page.goto("/research/US.SPY?horizon=20&mode=research&page=3");

      const search = page.getByRole("searchbox", { name: "搜索标的" });
      await search.fill("AAPL");
      await expect(page.getByRole("option").first()).toContainText("AAPL");
      await page.keyboard.press("Enter");

      await expect(page).toHaveURL(/\/research\/US\.AAPL/);
      await expect(
        page.getByRole("heading", { name: /US\.AAPL/ }).first(),
      ).toBeVisible();
      await expect(page).toHaveURL(/snapshot_id=/);
      await expect(page).toHaveURL(/horizon=20/);
      expect(new URL(page.url()).searchParams.get("mode")).toBeNull();
      await expect(page).not.toHaveURL(/page=3/);
    } finally {
      const path = testInfo.outputPath("search-navigation-history.json");
      await writeFile(path, JSON.stringify(navigations, null, 2));
      await testInfo.attach("search-navigation-history", {
        path,
        contentType: "application/json",
      });
    }
  });
}

test("late source history cannot restore pagination after instrument search", async ({
  page,
}) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let intercepted!: () => void;
  const ready = new Promise<void>((resolve) => {
    intercepted = resolve;
  });
  await page.route("**/api/v1/instruments/US.SPY/history?**", async (route) => {
    const response = await route.fetch();
    intercepted();
    await gate;
    await route.fulfill({ response });
  });
  try {
    await page.goto("/research/US.SPY?horizon=20&page=3");
    await ready;
    await page.getByRole("searchbox", { name: "搜索标的" }).fill("AAPL");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/research\/US\.AAPL/);
    const lateResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith("/instruments/US.SPY/history"),
    );
    release();
    await lateResponse;
    await expect(page.getByRole("heading", { name: /US\.AAPL/ }).first()).toBeVisible();
    await expect(page).toHaveURL(/snapshot_id=/);
    expect(new URL(page.url()).searchParams.get("page")).toBeNull();
    expect(new URL(page.url()).searchParams.get("horizon")).toBe("20");
  } finally {
    release();
  }
});

test("screener exposes sticky comparison columns and replayable filters", async ({
  page,
}) => {
  await page.goto("/screen?sector=technology&horizon=5&mode=research");

  await expect(page.getByRole("combobox", { name: "行业" })).toHaveValue("technology");
  await expect(
    page.getByRole("table", { name: "已验证研究结果（不可执行）" }),
  ).toBeVisible();

  const stickyHeader = await page
    .locator(".research-screen-results table thead th")
    .first()
    .evaluate((element) => getComputedStyle(element).position);
  const stickySymbol = await page
    .locator(".research-screen-results table tbody tr")
    .first()
    .locator(":scope > *")
    .first()
    .evaluate((element) => getComputedStyle(element).position);
  expect(stickyHeader).toBe("sticky");
  expect(stickySymbol).toBe("sticky");

  await page.reload();
  await expect(page.getByRole("combobox", { name: "行业" })).toHaveValue("technology");
});

test("research mobile prioritizes core price evidence and chart controls stay operable", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/research/US.SPY?horizon=5");

  await expect(
    page.getByRole("complementary", { name: "置信、拒绝与失效证据" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "SPY" })).toBeVisible();

  await page.locator(".price-history").scrollIntoViewIfNeeded();
  const markerFilters = page.getByRole("group", { name: "图表标记筛选" });
  const statusFilter = markerFilters.getByRole("checkbox", { name: "状态" });
  await expect(statusFilter).toBeChecked();
  await expect(markerFilters.getByRole("checkbox", { name: "突破" })).toBeChecked();
  await expect(markerFilters.getByRole("checkbox", { name: "事件" })).toBeChecked();
  await expect(markerFilters.getByRole("checkbox", { name: "缺口" })).toBeChecked();
  await statusFilter.uncheck();
  await expect(statusFilter).not.toBeChecked();
  await statusFilter.check();
  await expect(page.getByRole("img", { name: /US\.SPY OHLC/ })).toBeVisible();
});
