import { describe, expect, test } from "vitest";

import type { ManualEodUpdateMarker } from "../../app/snapshot-refresh";
import { buildEodUpdateNotice } from "./EodUpdateNotice";
import type { UpdateOperation } from "./useEodUpdate";

const marker: ManualEodUpdateMarker = {
  operationId: "eod-manual-1",
  previousCutoff: "2026-07-30",
  previousSnapshotId: "old-snapshot",
};

const baseOperation: UpdateOperation = {
  created_at: "2026-07-31T20:10:00Z",
  current_stage: "data_quality",
  no_change: false,
  operation_id: marker.operationId,
  operation_type: "eod_update",
  phases: [],
  progress_percent: 30,
  retry_attempt: 0,
  status: "running",
  target_session: "2026-07-31",
  updated_at: "2026-07-31T20:10:00Z",
};

describe("global EOD update messaging", () => {
  test("reports the current cutoff, target, and retry progress", () => {
    const notice = buildEodUpdateNotice(
      {
        ...baseOperation,
        actual_cutoff: "2026-07-30",
        next_retry_at: "2026-07-31T20:11:00Z",
        retry_attempt: 1,
      },
      marker,
    );

    expect(notice?.kind).toBe("retrying");
    expect(notice?.message).toContain("第 1 次");
    expect(notice?.message).toContain("当前完整至 2026-07-30");
    expect(notice?.message).toContain("目标 2026-07-31");
  });

  test("does not claim forecast readiness when data-only update advances the session", () => {
    const notice = buildEodUpdateNotice(
      {
        ...baseOperation,
        actual_cutoff: "2026-07-31",
        previous_cutoff: "2026-07-30",
        progress_percent: 100,
        session_advanced: true,
        status: "succeeded_data_only",
      },
      marker,
    );

    expect(notice?.message).toContain("从 2026-07-30 更新到 2026-07-31");
    expect(notice?.message).toContain("模型未通过门禁");
    expect(notice?.message).not.toContain("研究预测已刷新");
  });

  test("explains why weekend validation can leave values unchanged", () => {
    const notice = buildEodUpdateNotice(
      {
        ...baseOperation,
        actual_cutoff: "2026-07-31",
        previous_cutoff: "2026-07-31",
        progress_percent: 100,
        session_advanced: false,
        status: "succeeded_data_only",
      },
      marker,
    );

    expect(notice?.message).toContain("最新交易日仍为 2026-07-31");
    expect(notice?.message).toContain("数值可能不变");
  });

  test("keeps old data and exposes the retry recommendation on failure", () => {
    const notice = buildEodUpdateNotice(
      {
        ...baseOperation,
        actual_cutoff: "2026-07-30",
        error_code: "daily_cutoff_incomplete",
        retry_recommendation: "等待富途日线可用后重试。",
        status: "failed",
      },
      marker,
    );

    expect(notice?.kind).toBe("failed");
    expect(notice?.message).toContain("旧数据已保留");
    expect(notice?.originalDetails).toContain("等待富途日线可用后重试");
    expect(notice?.message).toContain("请检查更新详情后重新提交");
  });
});

test("English failure messages keep untranslated diagnostics in original details", () => {
  const operation = {
    ...baseOperation,
    status: "failed",
    retry_recommendation: "供应商返回未登记错误",
    actual_cutoff: "2026-07-30",
  } satisfies UpdateOperation;
  const notice = buildEodUpdateNotice(operation, marker, "en-US");
  expect(notice?.message).toContain("Previous data is retained");
  expect(notice?.message).not.toMatch(/[\u3400-\u9fff]/);
  expect(notice?.originalDetails).toBe(operation.retry_recommendation);
});

test("English data-only notices retain the quality gate limitation", () => {
  const notice = buildEodUpdateNotice(
    {
      ...baseOperation,
      status: "succeeded_data_only",
      session_advanced: true,
      actual_cutoff: "2026-07-31",
    },
    marker,
    "en-US",
  );
  expect(notice?.message).toContain("did not pass its gates");
  expect(notice?.message).toContain("no new production forecasts");
});
