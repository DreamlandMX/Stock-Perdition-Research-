import type { APIRequestContext, Page } from "@playwright/test";

import { expect, test } from "./browser-diagnostics";

interface OverviewFixture {
  data: {
    proxies: Array<Record<string, unknown>>;
    [key: string]: unknown;
  };
  resolved_snapshot: Record<string, unknown>;
  [key: string]: unknown;
}

async function installSnapshotContext(
  page: Page,
  select: (snapshotId: string | null) => OverviewFixture,
) {
  for (const path of ["snapshot-context", "history/batch"]) {
    await page.route(`**/api/v1/${path}**`, (route) => {
      const params = new URL(route.request().url()).searchParams;
      const envelope = select(params.get("snapshot_id"));
      const distribution = envelope.data.regime_distribution as { count: number }[];
      return route.fulfill({
        status: 200,
        json: {
          ...envelope,
          data:
            path === "snapshot-context"
              ? {
                  symbol_count: distribution.reduce((sum, item) => sum + item.count, 0),
                  instruments: [],
                }
              : {
                  items: params.getAll("symbols").map((symbol) => ({
                    symbol,
                    status: "unavailable",
                    history: null,
                    error_code: "history_not_in_fixture",
                  })),
                },
        },
      });
    });
  }
}

const baseOperation = {
  operation_id: "eod-20260725T200000Z-e2e",
  operation_type: "eod_update",
  status: "queued",
  current_stage: null,
  progress_percent: 0,
  target_session: "2026-07-28",
  actual_cutoff: null,
  snapshot_id: null,
  data_hash: null,
  model_version: null,
  automatic_promotion: null,
  error_code: null,
  retry_recommendation: null,
  retry_attempt: 0,
  next_retry_at: null,
  retry_deadline_at: null,
  previous_cutoff: null,
  session_advanced: null,
  no_change: false,
  created_at: "2026-07-25T20:00:00Z",
  started_at: null,
  finished_at: null,
  updated_at: "2026-07-25T20:00:00Z",
  phases: [],
};

function overviewAt(
  source: OverviewFixture,
  options: {
    asOf: string;
    snapshotId: string;
    fiveDayQ50: number;
    twentyDayQ50: number;
  },
): OverviewFixture {
  const response = structuredClone(source);
  response.resolved_snapshot = {
    ...response.resolved_snapshot,
    as_of: options.asOf,
    calibration_version: `cal-${options.snapshotId}`,
    dataset_revision: `data-${options.snapshotId}`,
    feature_set_version: `features-${options.snapshotId}`,
    model_version: `model-${options.snapshotId}`,
    revision_no: 1,
    snapshot_id: options.snapshotId,
    universe_version: `universe-${options.snapshotId}`,
  };
  response.data = {
    ...response.data,
    proxies: response.data.proxies.map((proxy) => {
      const symbol = String(proxy.symbol);
      const referencePrice = symbol === "US.SPY" ? options.fiveDayQ50 - 5 : 100;
      return {
        ...proxy,
        as_of: options.asOf,
        verified_research_by_horizon:
          symbol === "US.SPY"
            ? [
                projectionEvidence(1, referencePrice + 1, referencePrice, options.asOf),
                projectionEvidence(5, options.fiveDayQ50, referencePrice, options.asOf),
                projectionEvidence(
                  20,
                  options.twentyDayQ50,
                  referencePrice,
                  options.asOf,
                ),
              ]
            : [],
      };
    }),
  };
  return response;
}

function projectionEvidence(
  horizon: number,
  q50: number,
  referencePrice: number,
  asOf: string,
) {
  return {
    as_of: asOf,
    horizon,
    price_projection: {
      currency: "USD",
      endpoint_price_band: {
        q10: q50 - 10,
        q25: q50 - 5,
        q50,
        q75: q50 + 5,
        q90: q50 + 10,
      },
      endpoint_price_unavailable_reason: null,
      reference_price: referencePrice,
      reference_price_at: asOf,
      stage_high_price_q50: q50 + 15,
      stage_high_price_unavailable_reason: null,
      stage_low_price_q50: q50 - 15,
      stage_low_price_unavailable_reason: null,
    },
    sample_abstain_reasons: [],
    scope: "verified",
    symbol: "US.SPY",
    tasks: [],
    values: {
      breakout_down_probability: 0.3,
      breakout_up_probability: 0.6,
      capital_gain_q10: -0.02,
      capital_gain_q50: 0.01,
      mae_q50: -0.01,
      mfe_q50: 0.02,
    },
  };
}

async function deterministicOverviewFixture(request: APIRequestContext) {
  const response = await request.get("/api/v1/overview?horizon=5&mode=research");
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as OverviewFixture;
}

test("EOD update survives refresh and exposes progress, success, and failure evidence", async ({
  page,
}) => {
  let operation: Record<string, unknown> | null = null;
  await page.route(/\/api\/v1\/operations\/eod-update(?:\/.*)?$/, async (route) => {
    if (route.request().url().endsWith("/worker")) {
      await route.fulfill({
        json: {
          detail: "eod_worker_heartbeat_current",
          last_heartbeat: "2026-07-28T22:12:47Z",
          stale_after_seconds: 10,
          state: "ready",
        },
        status: 200,
      });
      return;
    }
    if (route.request().method() === "POST") {
      operation = {
        ...baseOperation,
        status: "running",
        current_stage: "incremental_collection",
        progress_percent: 10,
        started_at: "2026-07-25T20:00:01Z",
        updated_at: "2026-07-25T20:00:02Z",
        phases: [
          {
            stage: "opend_preflight",
            status: "completed",
            started_at: "2026-07-25T20:00:01Z",
            finished_at: "2026-07-25T20:00:02Z",
            detail: "目标交易日 2026-07-24",
          },
          {
            stage: "incremental_collection",
            status: "running",
            started_at: "2026-07-25T20:00:02Z",
            finished_at: null,
            detail: "增量采集固定 100 个标的",
          },
        ],
      };
      await route.fulfill({ json: operation, status: 202 });
      return;
    }
    if (operation === null) {
      await route.fulfill({ json: null, status: 200 });
      return;
    }
    await route.fulfill({ json: operation, status: 200 });
  });
  await page.goto("/health");
  await page.getByText("收盘更新任务与操作", { exact: true }).click();
  const panel = page.getByRole("region", { name: "一键收盘更新" });
  await panel.getByRole("button", { name: "一键收盘更新" }).click();
  await expect(panel).toContainText("增量采集");
  await expect(panel).toContainText("10%");

  await page.reload();
  await page.getByText("收盘更新任务与操作", { exact: true }).click();
  await expect(panel).toContainText("任务 eod-20260725T200000Z-e2e");

  operation = {
    ...(operation ?? baseOperation),
    status: "succeeded_data_only",
    current_stage: "final_snapshot_publication",
    progress_percent: 100,
    actual_cutoff: "2026-07-28",
    snapshot_id: "live-20260728-data",
    automatic_promotion: "kept_current",
    finished_at: "2026-07-25T20:06:00Z",
    updated_at: "2026-07-25T20:06:00Z",
  };
  await page.reload();
  await page.getByText("收盘更新任务与操作", { exact: true }).click();
  await expect(panel).toContainText("2026-07-28");
  await expect(panel).toContainText("live-20260728-data");

  operation = {
    ...(operation ?? baseOperation),
    status: "failed",
    error_code: "opend_preflight_blocked",
    retry_recommendation: "确认 OpenD 已登录后重试。",
    updated_at: "2026-07-25T20:07:00Z",
  };
  await page.reload();
  await page.getByText("收盘更新任务与操作", { exact: true }).click();
  await expect(panel).toContainText(
    "失败原因：OpenD 预检未通过（opend_preflight_blocked）",
  );
  await expect(panel).toContainText("确认 OpenD 已登录后重试。");
});

test("manual EOD update retries, switches to the latest snapshot, and preserves view state", async ({
  page,
  request,
}) => {
  const fixture = await deterministicOverviewFixture(request);
  const oldOverview = overviewAt(fixture, {
    asOf: "2026-07-30T20:00:00Z",
    fiveDayQ50: 705,
    snapshotId: "old-snapshot",
    twentyDayQ50: 720,
  });
  const newOverview = overviewAt(fixture, {
    asOf: "2026-07-31T20:00:00Z",
    fiveDayQ50: 780,
    snapshotId: "new-snapshot",
    twentyDayQ50: 799,
  });
  let submitted = false;
  let latestReads = 0;
  await installSnapshotContext(page, (snapshotId) =>
    snapshotId === "old-snapshot" ? oldOverview : newOverview,
  );

  await page.route("**/api/v1/overview**", (route) => {
    const snapshotId = new URL(route.request().url()).searchParams.get("snapshot_id");
    return route.fulfill({
      json: snapshotId === "old-snapshot" ? oldOverview : newOverview,
      status: 200,
    });
  });
  await page.route("**/api/v1/operations/eod-update**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/worker")) {
      return route.fulfill({
        json: {
          detail: "eod_worker_heartbeat_current",
          last_heartbeat: "2026-07-31T20:10:00Z",
          stale_after_seconds: 10,
          state: "ready",
        },
        status: 200,
      });
    }
    if (route.request().method() === "POST") {
      submitted = true;
      return route.fulfill({
        json: {
          ...baseOperation,
          current_stage: "incremental_collection",
          status: "running",
          target_session: "2026-07-31",
        },
        status: 202,
      });
    }
    if (!submitted) return route.fulfill({ json: null, status: 200 });
    latestReads += 1;
    if (latestReads <= 2) {
      return route.fulfill({
        json: {
          ...baseOperation,
          actual_cutoff: "2026-07-30",
          current_stage: "data_quality",
          next_retry_at: "2026-07-31T20:12:00Z",
          previous_cutoff: "2026-07-30",
          progress_percent: 30,
          retry_attempt: 1,
          retry_deadline_at: "2026-07-31T20:26:00Z",
          status: "running",
          target_session: "2026-07-31",
          updated_at: "2026-07-31T20:11:00Z",
        },
        status: 200,
      });
    }
    return route.fulfill({
      json: {
        ...baseOperation,
        actual_cutoff: "2026-07-31",
        current_stage: "final_snapshot_publication",
        finished_at: "2026-07-31T20:13:00Z",
        previous_cutoff: "2026-07-30",
        progress_percent: 100,
        retry_attempt: 1,
        session_advanced: true,
        snapshot_id: "new-snapshot",
        status: "succeeded_data_only",
        target_session: "2026-07-31",
        updated_at: "2026-07-31T20:13:00Z",
      },
      status: 200,
    });
  });

  await page.goto(
    "/overview?as_of=2026-07-30T20%3A00%3A00Z&snapshot_id=old-snapshot&revision_no=1&dataset_revision=data-old&feature_set_version=features-old&universe_version=universe-old&model_version=model-old&calibration_version=cal-old&horizon=20&mode=research&sector=technology&page=3",
  );
  await page.getByRole("button", { name: "未来 20 个交易日" }).click();
  await expect(
    page.getByRole("region", { name: "未来第 20 个交易日收盘价研究预测" }),
  ).toContainText("$720.00");

  await page.getByRole("button", { name: "一键收盘更新" }).click();
  await expect(page.getByRole("button", { name: "30%" })).toBeVisible();
  await expect(page.getByRole("status", { name: "收盘更新结果" })).toContainText(
    "价格与研究预测已刷新",
  );
  await expect(
    page.getByRole("region", { name: "未来第 20 个交易日收盘价研究预测" }),
  ).toContainText("$799.00");

  await expect
    .poll(() => new URL(page.url()).searchParams.get("snapshot_id"))
    .toBe("new-snapshot");
  const finalParams = new URL(page.url()).searchParams;
  expect(finalParams.get("horizon")).toBeNull();
  expect(finalParams.get("proxy_horizon")).toBe("20");
  expect(finalParams.get("mode")).toBe("research");
  expect(finalParams.get("sector")).toBe("technology");
  expect(finalParams.get("page")).toBe("3");
});

test("failed manual EOD update keeps the historical snapshot and old values", async ({
  page,
  request,
}) => {
  const fixture = await deterministicOverviewFixture(request);
  const oldOverview = overviewAt(fixture, {
    asOf: "2026-07-30T20:00:00Z",
    fiveDayQ50: 705,
    snapshotId: "old-snapshot",
    twentyDayQ50: 720,
  });
  let submitted = false;
  await installSnapshotContext(page, () => oldOverview);

  await page.route("**/api/v1/overview**", (route) =>
    route.fulfill({ json: oldOverview, status: 200 }),
  );
  await page.route("**/api/v1/operations/eod-update**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/worker")) {
      return route.fulfill({
        json: {
          detail: "eod_worker_heartbeat_current",
          last_heartbeat: "2026-07-31T20:10:00Z",
          stale_after_seconds: 10,
          state: "ready",
        },
        status: 200,
      });
    }
    if (route.request().method() === "POST") {
      submitted = true;
      return route.fulfill({
        json: { ...baseOperation, status: "running" },
        status: 202,
      });
    }
    if (!submitted) return route.fulfill({ json: null, status: 200 });
    return route.fulfill({
      json: {
        ...baseOperation,
        actual_cutoff: "2026-07-30",
        error_code: "daily_cutoff_incomplete",
        retry_attempt: 15,
        retry_recommendation: "等待富途日线可用后重试。",
        status: "failed",
        target_session: "2026-07-31",
        updated_at: "2026-07-31T20:26:00Z",
      },
      status: 200,
    });
  });

  await page.goto(
    "/overview?as_of=2026-07-30T20%3A00%3A00Z&snapshot_id=old-snapshot&proxy_horizon=20&mode=research&sector=technology",
  );
  await page.getByRole("button", { name: "未来 20 个交易日" }).click();
  await page.getByRole("button", { name: "一键收盘更新" }).click();

  const failure = page.getByRole("alert", { name: "收盘更新结果" });
  await expect(failure).toContainText("旧数据已保留");
  await expect(failure).toContainText("等待富途日线可用后重试");
  await expect(failure.getByRole("link", { name: "查看更新详情" })).toBeVisible();
  await expect(
    page.getByRole("region", { name: "未来第 20 个交易日收盘价研究预测" }),
  ).toContainText("$720.00");
  expect(new URL(page.url()).searchParams.get("snapshot_id")).toBe("old-snapshot");
});

test("mobile header keeps a compact EOD action", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route(/\/api\/v1\/operations\/eod-update(?:\/.*)?$/, (route) => {
    if (route.request().url().endsWith("/worker")) {
      return route.fulfill({
        json: {
          detail: "eod_worker_heartbeat_current",
          last_heartbeat: "2026-07-28T22:12:47Z",
          stale_after_seconds: 10,
          state: "ready",
        },
        status: 200,
      });
    }
    return route.fulfill({ json: null, status: 200 });
  });
  await page.goto("/overview");
  await expect(page.getByRole("button", { name: "一键收盘更新" })).toBeVisible();
});
