import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { I18nProvider } from "../../i18n/context";
import { LanguageSelect } from "../../i18n/LanguageSelect";
import { EodUpdatePanel } from "./EodUpdatePanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
});

test("shows actual publication work in both languages and retains running progress", async () => {
  localStorage.clear();
  const operation = {
    operation_id: "isolated-progress-test", status: "running",
    current_stage: "market_snapshot_publication", progress_percent: 52,
    phases: [{ stage: "market_snapshot_publication", status: "running", detail: "snapshot_publication:candidate:80/227" }],
    updated_at: "2026-11-04T23:00:00Z",
  };
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation((request) => {
    const url = request instanceof Request ? request.url : String(request);
    const payload = url.endsWith("/latest") ? operation : { state: "ready", last_heartbeat: "2026-11-04T23:00:00Z" };
    return Promise.resolve(new Response(JSON.stringify(payload), { headers: { "content-type": "application/json" } }));
  });
  render(<I18nProvider><LanguageSelect /><EodUpdatePanel /></I18nProvider>);
  await waitFor(() => expect(screen.getAllByText("候选模型推理：80/227 步").length).toBeGreaterThan(0));
  expect(screen.getByText("52%")).toBeVisible();
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "界面语言" }), "en-US");
  expect(screen.getAllByText("Candidate model inference: 80/227 steps").length).toBeGreaterThan(0);
  expect(screen.getByText("52%")).toBeVisible();
  expect(fetch.mock.calls.every(([request]) => !(request instanceof Request) || request.method === "GET")).toBe(true);
});
