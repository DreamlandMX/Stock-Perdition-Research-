import { expect, test } from "./browser-diagnostics";

test("M5 exposes read-only research plus explicit operations and no trading/account controls", async ({
  page,
  request,
}) => {
  const openapi = (await (
    await request.get("http://127.0.0.1:8766/openapi.json")
  ).json()) as {
    paths: Record<string, Record<string, unknown>>;
  };
  const allowedPosts = new Set([
    "/api/v1/operations/eod-update",
    "/api/v1/operations/model-promotion",
    "/api/v1/operations/model-training",
    "/api/v1/operations/{operation_id}/cancel",
  ]);
  for (const [path, operations] of Object.entries(openapi.paths)) {
    const methods = Object.keys(operations).filter((key) => !key.startsWith("x-"));
    for (const method of methods) {
      expect(method === "get" || (method === "post" && allowedPosts.has(path))).toBe(
        true,
      );
    }
  }

  for (const route of [
    "/overview",
    "/screen",
    "/research/US.SPY",
    "/backtests",
    "/health",
  ]) {
    await page.goto(route);
    await expect(
      page.getByRole("button", { name: /(下单|买入|卖出|交易账户|订单|place order)/i }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("link", { name: /(下单|买入|卖出|交易账户|订单|place order)/i }),
    ).toHaveCount(0);
  }
});
