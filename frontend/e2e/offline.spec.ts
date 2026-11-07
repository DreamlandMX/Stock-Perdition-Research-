import { expect, test } from "./browser-diagnostics";

test.use({
  expectedConsoleErrorIncludes: ["net::ERR_FAILED @ http://127.0.0.1:4173/api/v1/"],
});

test("API disconnect retains only the exact last valid snapshot and abstains", async ({
  page,
}) => {
  await page.goto("/research/US.SPY?horizon=5");
  await expect(
    page.getByRole("heading", { name: "原始分位数与 CQR 校准区间" }),
  ).toBeVisible();
  await expect(page).toHaveURL(/snapshot_id=snapshot-20260710-r1/);
  const exactUrl = page.url();
  const storageKey = "prediction-stock:persisted-views:v1";
  await expect
    .poll(() => page.evaluate((key) => localStorage.getItem(key) !== null, storageKey))
    .toBe(true);
  await page.evaluate(() => {
    const key = "prediction-stock:persisted-views:v1";
    const raw = localStorage.getItem(key);
    if (raw === null) throw new Error("persisted research view was not written");
    const envelope = JSON.parse(raw) as {
      records: Array<{ savedAt: string }>;
    };
    const staleSavedAt = new Date(Date.now() - 60_000).toISOString();
    envelope.records.forEach((record) => {
      record.savedAt = staleSavedAt;
    });
    localStorage.setItem(key, JSON.stringify(envelope));
  });

  await page.route("**/api/v1/**", (route) => route.abort("failed"));
  await page.reload();

  await expect(page).toHaveURL(exactUrl);
  await expect(page.getByText("当前离线", { exact: true })).toBeVisible();
  await expect(page.getByText(/保留快照仅供查看/)).toBeVisible();
  await expect(page.locator("main[data-effective-abstain='true']")).toHaveCount(1);
  await expect(
    page.getByRole("heading", { name: "原始分位数与 CQR 校准区间" }),
  ).toBeVisible();

  await page.unroute("**/api/v1/**");
  await page.reload();
  await expect(page.getByText("当前离线", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("main")).toHaveAttribute("data-connectivity", "online");
  await expect(page).toHaveURL(exactUrl);
});
