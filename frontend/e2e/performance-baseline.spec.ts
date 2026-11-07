import { writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import type { components } from "../src/api/generated/schema";

test("desktop populated chart range baseline", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  await page.route("**/api/v1/instruments/*/history?**", async (route) => {
    const response = await route.fetch();
    const payload = await response.json() as components["schemas"]["ResearchResponse_InstrumentHistory_"];
    payload.data.bars = Array.from({ length: 180 }, (_, index) => ({
      at: new Date(Date.UTC(2026, 0, index + 1)).toISOString(),
      open: 100 + index, high: 102 + index, low: 99 + index,
      close: 101 + index, volume: 1_000_000 + index * 1000,
      data_gap: false, regime: "uptrend",
    }));
    await route.fulfill({ response, json: payload });
  });
  const measurements = [];
  for (const width of [1280, 1920]) {
    await page.setViewportSize({ width, height: 1080 });
    await page.goto("/research/US.SPY");
    const range = page.getByRole("combobox", { name: "价格图显示范围" });
    await expect(range).toBeVisible();
    await expect(page.getByRole("table", { name: "图表末值摘要" })).toContainText("280.00");
    const originalUrl = page.url();
    for (let round = 1; round <= 3; round += 1) {
      for (const value of ["20", "120", "0", "60"]) {
        const started = Date.now();
        await range.selectOption(value);
        await expect(range).toHaveValue(value);
        await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
        measurements.push({ width, round, range: value, actionToTwoFramesMs: Date.now() - started });
        expect(page.url()).toBe(originalUrl);
        if (round === 1 && (value === "20" || value === "0")) {
          await page.getByRole("img", { name: /OHLC、成交量/ }).screenshot({
            path: testInfo.outputPath(`chart-${width}-range-${value}.png`),
          });
        }
      }
    }
    await page.getByRole("img", { name: /OHLC、成交量/ }).screenshot({ path: testInfo.outputPath(`chart-${width}.png`) });
  }
  writeFileSync(testInfo.outputPath("chart-range.json"), JSON.stringify({
    measurements,
    limitation: "Synthetic 180 daily bars (calendar days, not a market calendar), existing fixture envelope. Two-frame opportunity proxy includes automation, not INP or proof of visible range bounds. Screenshots support populated rendering only; no real-price or corporate-action validation.",
  }, null, 2));
});

test("desktop sector application baseline", async ({ browser }, testInfo) => {
  test.setTimeout(90_000);
  const measurements = [];
  for (const width of [1280, 1920]) {
    for (let round = 1; round <= 3; round += 1) {
      const page = await browser.newPage({ viewport: { width, height: 1080 } });
      try {
        const navigationStarted = Date.now();
        await page.goto("http://127.0.0.1:4173/screen?mode=research");
        const sector = page.getByRole("combobox", { name: "行业", exact: true });
        await expect(sector).toBeVisible();
        await expect(page).toHaveURL(/snapshot_id=/);
        await expect(sector.locator('option[value="technology"]')).toHaveCount(1);
        const optionsReadyMs = Date.now() - navigationStarted;
        const snapshot = new URL(page.url()).searchParams.get("snapshot_id");
        const response = page.waitForResponse((result) => {
          const url = new URL(result.url());
          return url.pathname === "/api/v1/screen" &&
            url.searchParams.get("sector") === "technology" && result.ok();
        });
        const started = Date.now();
        await sector.selectOption("technology");
        const selectedAt = Date.now();
        const result = await response;
        const responseAt = Date.now();
        await expect(page.getByRole("table", { name: "已验证研究结果（不可执行）" })).toBeVisible();
        const tableAt = Date.now();
        await expect(page.getByRole("button", { name: /移除行业/ })).toBeVisible();
        await expect(sector).toHaveValue("technology");
        const params = new URL(page.url()).searchParams;
        expect(params.get("sector")).toBe("technology");
        expect(params.get("snapshot_id")).toBe(snapshot);
        measurements.push({
          width, round, optionsReadyMs,
          actionToResponseAndTableMs: Date.now() - started,
          selectActionMs: selectedAt - started,
          responseWaitAfterSelectMs: responseAt - selectedAt,
          tableAssertionAfterResponseMs: tableAt - responseAt,
          conditionAssertionsMs: Date.now() - tableAt,
          requestTiming: result.request().timing(),
        });
        const payload = await result.json() as components["schemas"]["ResearchResponse_ScreenPage_"];
        expect(payload.data.items.length).toBeGreaterThan(0);
        for (const item of payload.data.items) expect(item.sector).toBe("technology");
      } finally {
        await page.close();
      }
    }
  }
  writeFileSync(testInfo.outputPath("sector-baseline.json"), JSON.stringify({
    browser: browser.version(),
    serverMode: process.env.PLAYWRIGHT_USE_PREVIEW === "1" ? "production-preview" : "development",
    measurements,
    limitation: "Three new-context rounds per desktop width, isolated fixture, no throttling. Includes automation and assertions; not INP or user percentiles. Checks matching response, visible result table, applied condition, row sectors and pinned snapshot.",
  }, null, 2));
});

test("desktop sort request baseline", async ({ browser }, testInfo) => {
  test.setTimeout(90_000);
  const measurements = [];
  for (const width of [1280, 1920]) {
    for (let round = 1; round <= 3; round += 1) {
      const page = await browser.newPage({ viewport: { width, height: 1080 } });
      try {
        await page.goto("http://127.0.0.1:4173/screen?mode=research");
        const sort = page.getByRole("combobox", { name: "研究排序", exact: true });
        await expect(sort).toBeVisible();
        await expect(page).toHaveURL(/snapshot_id=/);
        const snapshot = new URL(page.url()).searchParams.get("snapshot_id");
        for (const value of ["rule_return_20", "verified_q10", "rule_volume_z_20"]) {
          const response = page.waitForResponse((result) => {
            const url = new URL(result.url());
            return (
              url.pathname === "/api/v1/screen" &&
              url.searchParams.get("sort") === value &&
              result.ok()
            );
          });
          const started = Date.now();
          await sort.selectOption(value);
          await response;
          await expect(
            page.getByRole("table", { name: "已验证研究结果（不可执行）" }),
          ).toBeVisible();
          await expect(sort).toHaveValue(value);
          expect(new URL(page.url()).searchParams.get("snapshot_id")).toBe(snapshot);
          measurements.push({
            width,
            round,
            sort: value,
            actionToResponseAndTableMs: Date.now() - started,
          });
        }
      } finally {
        await page.close();
      }
    }
  }
  writeFileSync(
    testInfo.outputPath("sort-baseline.json"),
    JSON.stringify(
      {
        browser: browser.version(),
        serverMode:
          process.env.PLAYWRIGHT_USE_PREVIEW === "1"
            ? "production-preview"
            : "development",
        measurements,
        limitation:
          "Local server (see serverMode), isolated fixtures, three rounds with a new context per round and desktop width. Includes Playwright actionability and assertion overhead, not INP or user percentiles. Verifies successful matching API response and visible table, not multi-row ranking correctness.",
      },
      null,
      2,
    ),
  );
});

for (const reuseContext of [false, true]) {
  test(`desktop comparison interaction baseline reuse=${reuseContext}`, async ({
    browser,
    page: sharedPage,
  }, testInfo) => {
    const measurements = [];
    for (const width of [1920, 1280]) {
      const page = reuseContext
        ? sharedPage
        : await browser.newPage({ viewport: { width, height: 1080 } });
      await page.setViewportSize({ width, height: 1080 });
      const session = await page.context().newCDPSession(page);
      await session.send("Performance.enable");
      await page.goto("http://127.0.0.1:4173/screen?mode=research");
      await expect(
        page.getByRole("button", { name: "展开历史与路径列" }),
      ).toBeVisible();
      await expect(page.getByText("正在查询数据日期", { exact: true })).toHaveCount(0);
      for (let round = 1; round <= 3; round += 1) {
        for (const expanded of [true, false]) {
          const button = page.getByRole("button", {
            name: expanded ? "展开历史与路径列" : "收起历史与路径列",
          });
          await button.evaluate((element) => {
            element.addEventListener(
              "click",
              () => {
                const start = performance.now();
                requestAnimationFrame(() =>
                  requestAnimationFrame(() => {
                    element.setAttribute(
                      "data-audit-paint-ms",
                      String(performance.now() - start),
                    );
                  }),
                );
              },
              { once: true },
            );
            element.removeAttribute("data-audit-paint-ms");
          });
          const before = await session.send("Performance.getMetrics");
          await button.click();
          const updated = page.getByRole("button", {
            name: expanded ? "收起历史与路径列" : "展开历史与路径列",
          });
          await expect(updated).toHaveAttribute("data-audit-paint-ms", /\d/);
          await expect(
            page
              .getByRole("table", { name: "已验证研究结果（不可执行）" })
              .getByRole("columnheader"),
          ).toHaveCount(expanded ? 9 : 6);
          const after = await session.send("Performance.getMetrics");
          const mainThread = Object.fromEntries(
            [
              "TaskDuration",
              "ScriptDuration",
              "LayoutDuration",
              "RecalcStyleDuration",
            ].map((name) => [
              name,
              1000 *
                (after.metrics.find((metric) => metric.name === name)!.value -
                  before.metrics.find((metric) => metric.name === name)!.value),
            ]),
          );
          measurements.push({
            width,
            round,
            expanded,
            mainThreadMs: mainThread,
            clickToFrameMs: Number(await updated.getAttribute("data-audit-paint-ms")),
          });
        }
      }
      await session.detach();
      if (!reuseContext) await page.close();
    }
    writeFileSync(
      testInfo.outputPath("interaction.json"),
      JSON.stringify(
        {
          browser: browser.version(),
          reuseContext,
          serverMode:
            process.env.PLAYWRIGHT_USE_PREVIEW === "1"
              ? "production-preview"
              : "development",
          measurements,
          limitation:
            "Desktop isolated fixture, no CPU throttle; see serverMode and reuseContext. CDP session is attached per navigation. Click-handler start to second animation frame is a rendering opportunity proxy, excludes input delay and does not prove paint completion or measure INP. Column counts independently verify the resulting state. CDP main-thread deltas include automation and unrelated work; durations overlap and must not be summed.",
        },
        null,
        2,
      ),
    );
  });
}

declare global {
  interface Window {
    auditLoadEvents: {
      supported: string[];
      entries: Array<{
        type: string;
        at: number;
        duration: number;
        value: number | null;
        recentInput: boolean | null;
        sources: Array<{ element: string; before: unknown; after: unknown }>;
      }>;
    };
  }
}

for (const [route, heading] of [
  ["overview", "市场总览"],
  ["screen", "研究筛选器"],
  ["research/US.SPY", "US.SPY 标的研究室"],
  ["backtests", "回测与校准诊断"],
  ["health", "模型与数据健康"],
] as const) {
  test(`local load baseline: ${route}`, async ({ browser }, testInfo) => {
    test.setTimeout(120_000);
    const measurements = [];
    for (const width of [1280]) {
      for (let round = 1; round <= 3; round += 1) {
        const context = await browser.newContext({ viewport: { width, height: 844 } });
        try {
          await context.addInitScript(() => {
            const types = ["layout-shift", "largest-contentful-paint", "longtask"];
            window.auditLoadEvents = {
              supported: types.filter((type) =>
                PerformanceObserver.supportedEntryTypes.includes(type),
              ),
              entries: [],
            };
            for (const type of window.auditLoadEvents.supported) {
              new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                  window.auditLoadEvents.entries.push({
                    type: entry.entryType,
                    at: entry.startTime,
                    duration: entry.duration,
                    value:
                      "value" in entry && typeof entry.value === "number"
                        ? entry.value
                        : null,
                    recentInput:
                      "hadRecentInput" in entry &&
                      typeof entry.hadRecentInput === "boolean"
                        ? entry.hadRecentInput
                        : null,
                    sources:
                      "sources" in entry && Array.isArray(entry.sources)
                        ? (
                            entry.sources as Array<{
                              node?: Node;
                              previousRect: DOMRectReadOnly;
                              currentRect: DOMRectReadOnly;
                            }>
                          ).map((source) => ({
                            element:
                              source.node instanceof Element
                                ? `${source.node.tagName.toLowerCase()}#${source.node.id}.${source.node.className}`
                                : "unknown",
                            before: source.previousRect.toJSON() as unknown,
                            after: source.currentRect.toJSON() as unknown,
                          }))
                        : [],
                  });
                }
              }).observe({ type, buffered: true });
            }
          });
          const page = await context.newPage();
          for (const cache of ["new-context", "repeat-navigation"]) {
            const started = Date.now();
            await page.goto(`http://127.0.0.1:4173/${route}`);
            await expect(
              page.getByRole("heading", { name: heading, level: 1 }),
            ).toBeVisible();
            await expect(
              page.getByText("正在查询数据日期", { exact: true }),
            ).toHaveCount(0);
            if (route === "screen")
              await expect(page.locator(".screen-loading")).toHaveCount(0);
            if (route === "research/US.SPY")
              await expect(
                page.getByRole("combobox", { name: "价格图显示范围" }),
              ).toBeVisible();
            if (route === "backtests")
              await expect(page.getByText("Deflated Sharpe Ratio")).toBeVisible();
            if (route === "health")
              await expect(
                page.getByRole("table", { name: "OpenD 数据能力矩阵" }),
              ).toBeVisible();
            measurements.push({
              width,
              round,
              cache,
              readinessMs: Date.now() - started,
              loadEvents: await page.evaluate(() => ({
                ...window.auditLoadEvents,
                sampledAt: performance.now(),
              })),
              api: await page.evaluate(() =>
                performance
                  .getEntriesByType("resource")
                  .filter((entry) => entry.name.includes("/api/"))
                  .map((entry) => ({
                    path: new URL(entry.name).pathname,
                    durationMs: entry.duration,
                  })),
              ),
            });
          }
        } finally {
          await context.close();
        }
      }
    }
    writeFileSync(
      testInfo.outputPath("baseline.json"),
      JSON.stringify(
        {
          route,
          browser: browser.version(),
          measurements,
          limitation:
            "Local Vite development server and isolated fixtures. Raw paint, shift and long-task events stop at readiness sampling; they are not final LCP, session-window CLS, INP or real-user percentiles. Empty events do not prove absence of later work. New context resets browser cache, not backend or OS caches.",
        },
        null,
        2,
      ),
    );
  });
}
