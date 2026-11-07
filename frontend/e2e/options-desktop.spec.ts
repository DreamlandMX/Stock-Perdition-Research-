import { expect, test } from "./browser-diagnostics";

// All nine saved chains require the full managed local research service.
test.use({ baseURL: "http://127.0.0.1:18765" });

const symbols = ["SPY", "QQQ", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA"];
for (const width of [1280, 1920]) {
  test(`numeric option structure for all saved symbols at ${width}px`, async ({
    page,
  }) => {
    test.setTimeout(180000);
    await page.setViewportSize({ width, height: 1080 });
    for (const symbol of symbols) {
      const saved = await page.request.get(
        `/api/v1/instruments/US.${symbol}/options/heatmap?metric=premium&side=call`,
      );
      expect(saved.ok()).toBeTruthy();
      const response = (await saved.json()) as {
        data: {
          snapshot: unknown;
          strikes: number[];
          strike_summary: Record<string, number | null>;
          cells: {
            strike: number;
            expiration: string;
            value: number | null;
            contract_ids: string[];
          }[];
        };
      };
      expect(
        response.data.snapshot,
        `${symbol} requires a saved snapshot`,
      ).toBeTruthy();
      await page.goto(`/research/US.${symbol}?option_tab=structure`);
      const panel = page.getByRole("region", { name: "期权结构", exact: true });
      const table = panel.getByRole("table", { name: "到期日 × 行权价数字热力表" });
      await expect(table).toBeVisible();
      await expect(
        panel.getByRole("combobox", { name: "指标", exact: true }),
      ).toHaveValue("premium");
      await expect(panel.getByText(/数字 = 估算成交额/)).toBeVisible();
      await expect(panel.getByText(/估算成交额 = 成交量/)).toBeVisible();
      await expect(
        table.getByRole("columnheader", { name: "行合计", exact: true }),
      ).toBeAttached();
      await expect(
        table.getByRole("rowheader", { name: "列合计", exact: true }),
      ).toBeAttached();
      await panel.locator(".options-heat-scroll").scrollIntoViewIfNeeded();
      await expect(
        table.getByRole("columnheader", { name: "行合计", exact: true }),
      ).toBeInViewport();
      const contrasts = await table
        .locator(".options-heat-cell")
        .evaluateAll((elements) => {
          const luminance = (rgb: string) =>
            (rgb.match(/[\d.]+/g) ?? []).slice(0, 3).reduce((sum, value, i) => {
              const c = Number(value) / 255;
              return (
                sum +
                (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4) *
                  ([0.2126, 0.7152, 0.0722][i] ?? 0)
              );
            }, 0);
          return elements.map((element) => {
            const style = getComputedStyle(element);
            const a = luminance(style.color),
              b = luminance(style.backgroundColor);
            return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
          });
        });
      expect(Math.min(...contrasts)).toBeGreaterThanOrEqual(4.5);
      const summaries = new Map(
        Object.entries(response.data.strike_summary).map(([strike, value]) => [
          Number(strike),
          value,
        ]),
      );
      expect(
        await table
          .locator(".options-heat-summary")
          .evaluateAll((elements) =>
            elements.map((element) => element.getAttribute("title")),
          ),
      ).toEqual(
        response.data.strikes.map((strike) => {
          const value = summaries.get(strike);
          return value == null
            ? "缺失"
            : `$${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
        }),
      );
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBeTruthy();
      const numeric = response.data.cells.find(
        (cell) => cell.value != null && cell.value > 0,
      );
      expect(numeric, `${symbol} needs a numeric premium`).toBeTruthy();
      if (!numeric) continue;
      const cell = table.locator(
        `button[aria-label^="行权价 $${numeric.strike} · ${numeric.expiration} ·"]`,
      );
      await cell.scrollIntoViewIfNeeded();
      expect(
        await cell.evaluate((element) => element.getBoundingClientRect().height),
      ).toBeGreaterThanOrEqual(28);
      await cell.focus();
      await page.keyboard.press("Enter");
      await expect(
        panel.getByRole("combobox", { name: "合约", exact: true }),
      ).toHaveValue(numeric.contract_ids[0]!);
      await expect(panel.locator(".options-heat-reading")).toContainText(
        numeric.value!.toLocaleString("zh-CN", { maximumFractionDigits: 2 }),
      );
      if (["SPY", "QQQ", "META", "NVDA"].includes(symbol)) {
        await panel
          .getByRole("combobox", { name: "指标", exact: true })
          .selectOption("oi");
        await expect(panel.getByText(/数字 = 持仓 OI（张）/)).toBeVisible();
        await panel
          .getByRole("combobox", { name: "合约侧", exact: true })
          .selectOption("put");
        await expect(
          panel.getByRole("heading", { name: "Put · 持仓 OI", exact: true }),
        ).toBeVisible();
        await panel
          .getByRole("combobox", { name: "指标", exact: true })
          .selectOption("premium");
        await expect(panel.getByText(/数字 = 估算成交额/)).toBeVisible();
        await page.reload();
        await expect(table).toBeVisible();
        await expect(
          panel.getByRole("combobox", { name: "指标", exact: true }),
        ).toHaveValue("premium");
        await expect(
          panel.getByRole("combobox", { name: "合约侧", exact: true }),
        ).toHaveValue("put");
        await panel.locator(".options-heat-scroll").scrollIntoViewIfNeeded();
        await panel
          .locator(".options-heat-scroll")
          .screenshot({ path: `var/options/evidence/numeric-${symbol}-${width}.png` });
        await page.goBack();
        await expect(
          panel.getByRole("combobox", { name: "指标", exact: true }),
        ).toHaveValue("oi");
      }
    }
  });
}
