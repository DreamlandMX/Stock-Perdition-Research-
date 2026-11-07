import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, test, vi } from "vitest";

import { I18nProvider, useI18n } from "../../i18n/context";
import { EodUpdateButton } from "./EodUpdateButton";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("EOD update action", () => {
  test("submits once and restores the queued progress state", async () => {
    const operation = {
      automatic_promotion: null,
      created_at: "2026-07-25T20:00:00Z",
      current_stage: null,
      error_code: null,
      finished_at: null,
      model_version: null,
      no_change: false,
      operation_id: "eod-20260725T200000Z-test",
      operation_type: "eod_update",
      phases: [],
      progress_percent: 0,
      retry_recommendation: null,
      snapshot_id: null,
      started_at: null,
      status: "queued",
      target_session: null,
      actual_cutoff: null,
      data_hash: null,
      updated_at: "2026-07-25T20:00:00Z",
    };
    const worker = {
      detail: "后台更新执行器已连接，可领取收盘更新任务。",
      last_heartbeat: "2026-07-25T20:00:01Z",
      stale_after_seconds: 10,
      state: "ready",
    };
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation((request, init) => {
      const url = request instanceof Request ? request.url : String(request);
      const method = request instanceof Request ? request.method : init?.method;
      if (url.endsWith("/latest")) {
        return Promise.resolve(
          new Response(JSON.stringify(null), {
            headers: { "content-type": "application/json" },
            status: 200,
          }),
        );
      }
      if (url.endsWith("/worker")) {
        return Promise.resolve(
          new Response(JSON.stringify(worker), {
            headers: { "content-type": "application/json" },
            status: 200,
          }),
        );
      }
      if (url.endsWith("/eod-update") && method === "POST") {
        return Promise.resolve(
          new Response(JSON.stringify(operation), {
            headers: { "content-type": "application/json" },
            status: 202,
          }),
        );
      }
      throw new Error(`Unexpected request: ${method ?? "GET"} ${url}`);
    });
    render(<EodUpdateButton />);
    const button = await screen.findByRole("button", { name: "一键收盘更新" });

    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);

    await waitFor(() => expect(button).toBeDisabled());
    expect(button).toHaveTextContent("等待更新");
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  test("explains why a queued update cannot start while the worker is offline", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((request) => {
      const url = request instanceof Request ? request.url : String(request);
      if (url.endsWith("/latest")) {
        return Promise.resolve(
          new Response(JSON.stringify(null), {
            headers: { "content-type": "application/json" },
            status: 200,
          }),
        );
      }
      if (url.endsWith("/worker")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              detail: "后台更新执行器未连接。请重新启动本地服务。",
              last_heartbeat: null,
              stale_after_seconds: 10,
              state: "offline",
            }),
            {
              headers: { "content-type": "application/json" },
              status: 200,
            },
          ),
        );
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<EodUpdateButton />);

    const button = await screen.findByRole("button", {
      name: "更新执行器未连接",
    });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", expect.stringContaining("重新启动"));
  });

  test("shows an actionable retry state after the update request fails", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((request, init) => {
      const url = request instanceof Request ? request.url : String(request);
      const method = request instanceof Request ? request.method : init?.method;
      if (url.endsWith("/latest")) {
        return Promise.resolve(
          new Response(JSON.stringify(null), {
            headers: { "content-type": "application/json" },
            status: 200,
          }),
        );
      }
      if (url.endsWith("/worker")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              detail: "后台更新执行器已连接，可领取收盘更新任务。",
              last_heartbeat: "2026-07-25T20:00:01Z",
              stale_after_seconds: 10,
              state: "ready",
            }),
            {
              headers: { "content-type": "application/json" },
              status: 200,
            },
          ),
        );
      }
      if (url.endsWith("/eod-update") && method === "POST") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              affected_scope: ["eod_update"],
              data_status: "degraded",
              error_code: "eod_update_failed",
              generated_at: "2026-07-25T20:00:02Z",
              message: "收盘更新请求失败，请重试。",
              request_id: "request-eod-failed",
              retryable: true,
            }),
            {
              headers: { "content-type": "application/json" },
              status: 503,
            },
          ),
        );
      }
      throw new Error(`Unexpected request: ${method ?? "GET"} ${url}`);
    });

    render(<EodUpdateButton />);
    const button = await screen.findByRole("button", { name: "一键收盘更新" });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);

    const retry = await screen.findByRole("button", { name: /更新失败，重试/ });
    expect(retry).toBeEnabled();
    expect(retry).toHaveAttribute("title", expect.stringContaining("收盘更新未完成"));
  });
});

function LocaleControl() {
  const i18n = useI18n();
  return <button onClick={() => i18n.setLocale("en-US")}>English</button>;
}

test("changing language retains progress without a read or write request", async () => {
  localStorage.clear();
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation((request) => {
    const url = request instanceof Request ? request.url : String(request);
    const value = url.endsWith("/worker")
      ? {
          state: "ready",
          last_heartbeat: "2026-07-25T20:00:01Z",
          stale_after_seconds: 10,
          detail: "worker_ready",
        }
      : {
          operation_id: "existing-eod",
          operation_type: "eod_update",
          status: "running",
          current_stage: "data_quality",
          progress_percent: 37,
          phases: [],
          created_at: "2026-07-25T20:00:00Z",
          updated_at: "2026-07-25T20:00:00Z",
          no_change: false,
        };
    return Promise.resolve(
      new Response(JSON.stringify(value), {
        headers: { "content-type": "application/json" },
      }),
    );
  });
  render(
    <I18nProvider>
      <LocaleControl />
      <EodUpdateButton />
    </I18nProvider>,
  );
  expect(await screen.findByRole("button", { name: "数据质量检查" })).toBeDisabled();
  const calls = fetch.mock.calls.length;
  await userEvent.click(screen.getByRole("button", { name: "English" }));
  expect(screen.getByRole("button", { name: "Data quality checks" })).toHaveAttribute(
    "title",
    expect.stringContaining("37%"),
  );
  expect(fetch).toHaveBeenCalledTimes(calls);
  expect(
    fetch.mock.calls.every(
      ([request, init]) =>
        (request instanceof Request ? request.method : init?.method) !== "POST",
    ),
  ).toBe(true);
  localStorage.clear();
});
