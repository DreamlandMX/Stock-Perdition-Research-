import { expect, test } from "./browser-diagnostics";

import { EXPECTED_VERSIONS } from "./fixtures/expected-versions";
import { installOverviewOpportunities } from "./overview-fixtures";

test("five-page workflow preserves exact snapshot identity and forecast states", async ({
  page,
}) => {
  await installOverviewOpportunities(page);
  await page.goto("/overview");
  await expect(page.getByRole("heading", { name: "市场总览", level: 1 })).toBeVisible();
  await expect(page.getByRole("table", { name: "Top 研究候选" })).toBeVisible();

  await page.getByRole("link", { name: "筛选器" }).click();
  await expect(
    page.getByRole("heading", { name: "研究筛选器", level: 1 }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "筛选结果", level: 2 })).toBeVisible();

  await page.goto("/research/US.SPY?horizon=20");
  await expect(
    page.getByRole("heading", { name: "US.SPY 标的研究室", level: 1 }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "原始分位数与 CQR 校准区间" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "相似案例" })).toBeVisible();
  await expect(page).toHaveURL(/horizon=20/);
  for (const [field, value] of Object.entries(EXPECTED_VERSIONS)) {
    await expect(page).toHaveURL(new RegExp(`${field}=${value}`));
  }

  await page.goto("/research/US.POST");
  await expect(page.getByRole("heading", { name: "本次预测已拒绝执行" })).toBeVisible();
  await expect(page.locator("[data-effective-abstain='true']")).toHaveCount(1);

  await page.goto("/research/US.PRE");
  await expect(page.getByRole("status", { name: "研究与正式模型状态" })).toContainText(
    "正式模型未就绪 · 不可执行",
  );
  await expect(page.getByRole("region", { name: "预测不可用证据" })).toBeVisible();
  await expect(page.getByTestId("probability-node")).toHaveCount(0);
  await expect(page).toHaveURL(/calibration_version=calibration-v1/);

  await page.getByRole("link", { name: "回测校准" }).click();
  await expect(
    page.getByRole("heading", { name: "回测与校准诊断", level: 1 }),
  ).toBeVisible();
  await expect(page.getByText("Deflated Sharpe Ratio")).toBeVisible();

  await page.getByRole("link", { name: "模型数据" }).click();
  await expect(
    page.getByRole("heading", { name: "模型与数据健康", level: 1 }),
  ).toBeVisible();
  await expect(page.getByRole("table", { name: "模型任务矩阵" })).toBeVisible();
  await expect(page.getByRole("table", { name: "OpenD 数据能力矩阵" })).toBeVisible();
});

test("screen and backtest URLs replay through browser history", async ({ page }) => {
  const screenUrl =
    "/screen?horizon=5&sort=executable_up_probability&direction=desc&confidence=high";
  await page.goto(screenUrl);
  await expect(page.getByRole("heading", { name: "研究筛选器" })).toBeVisible();
  await expect(page).toHaveURL(/snapshot_id=/);
  await expect(
    page.getByRole("combobox", { name: "研究排序", exact: true }),
  ).toHaveValue("symbol");
  const resolvedScreenUrl = page.url();

  await page.goto(
    "/backtests?split_version=split-v1&fold=outer-1&horizon=5&cost_scenario=base_costs",
  );
  await expect(page.getByRole("heading", { name: "回测与校准诊断" })).toBeVisible();
  await expect(page.getByText("Deflated Sharpe Ratio")).toBeVisible();
  const resolvedBacktestUrl = page.url();

  await page.goBack();
  await expect(page).toHaveURL(resolvedScreenUrl);
  await page.goForward();
  await expect(page).toHaveURL(resolvedBacktestUrl);
});
