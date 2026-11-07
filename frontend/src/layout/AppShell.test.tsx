import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { components } from "../api/generated/schema";
import { AppShell } from "./AppShell";
import type { AppShellProps } from "./AppShell";

vi.mock("../features/operations/useEodUpdate", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../features/operations/useEodUpdate")>()),
  useEodUpdate: () => ({
    error: null,
    isActive: false,
    isLoading: false,
    operation: null,
    submit: vi.fn(),
    submitting: false,
    worker: { state: "ready" },
  }),
}));

const EXPECTED_NAVIGATION = [
  "总览",
  "期权脉搏",
  "筛选器",
  "标的研究",
  "回测校准",
  "模型数据",
];

function testQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

function setViewport(width: number, height: number) {
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: width,
  });
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    value: height,
  });
  window.dispatchEvent(new Event("resize"));
}

function renderAppAt(
  path: string,
  viewport: { width: number; height: number },
  opendStatus: AppShellProps["opendStatus"] = "connected",
  dataStatus: components["schemas"]["ServingStatus"] = "live",
) {
  setViewport(viewport.width, viewport.height);
  return render(
    <QueryClientProvider client={testQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            element={
              <AppShell
                dataAsOf="2026-07-10 16:05 ET"
                dataStatus={dataStatus}
                opendStatus={opendStatus}
              />
            }
          >
            <Route path="*" element={<p>路由出口</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("approved application shell", () => {
  test("labels manually activated predictions as unverified", async () => {
    setViewport(1280, 900);
    render(
      <QueryClientProvider client={testQueryClient()}>
        <MemoryRouter>
          <AppShell
            statusLoader={() =>
              Promise.resolve({
                dataAsOf: "2026-11-02",
                dataStatus: "live",
                opendStatus: "connected",
                snapshot: {
                  snapshot_id: "manual-snapshot",
                  revision_no: 1,
                  as_of: "2026-11-02T21:00:00Z",
                  dataset_revision: "data-v1",
                  feature_set_version: "features-v1",
                  universe_version: "universe-v1",
                  model_version: "manual-model-v1",
                  calibration_version: "cal-v1",
                  prediction_readiness: "forecast_ready",
                  research_readiness: "manual_unverified",
                  activation_basis: "manual_override",
                },
              })
            }
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/人工启用，未通过验证/)).toBeVisible();
    expect(screen.queryByText("含正式模型证据")).not.toBeInTheDocument();
  });

  test("shows OpenD health while lightweight snapshot context is pending", async () => {
    setViewport(1280, 900);
    const requests: string[] = [];
    vi.stubGlobal("fetch", (request: Request) => {
      const path = new URL(request.url).pathname;
      requests.push(path);
      if (path === "/api/v1/snapshot-context") return new Promise(() => {});
      return Promise.resolve(
        new Response(
          JSON.stringify({
            data_status: "ready",
            components: [{ component: "market_data", connectivity: "connected" }],
          }),
          { status: 200 },
        ),
      );
    });
    render(
      <QueryClientProvider client={testQueryClient()}>
        <MemoryRouter initialEntries={["/screen"]}>
          <AppShell />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("OpenD 正常")).toBeVisible();
    expect(screen.getByText("数据日期待确认")).toBeVisible();
    expect(requests).toContain("/api/v1/snapshot-context");
    expect(requests).not.toContain("/api/v1/overview");
  });
  test("loads real shell status and refreshes it after an EOD completion event", async () => {
    setViewport(1440, 1024);
    const statusLoader = vi
      .fn()
      .mockResolvedValueOnce({
        dataAsOf: "2026-08-20",
        dataStatus: "live",
        opendStatus: "connected",
      })
      .mockResolvedValueOnce({
        dataAsOf: "2026-08-21",
        dataStatus: "delayed",
        opendStatus: "connected",
      });

    render(
      <QueryClientProvider client={testQueryClient()}>
        <MemoryRouter initialEntries={["/overview"]}>
          <Routes>
            <Route element={<AppShell statusLoader={statusLoader} />}>
              <Route path="*" element={<p>路由出口</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByRole("status", { name: "正在查询数据状态" })).toBeVisible();
    expect(screen.queryByText("数据不可用")).not.toBeInTheDocument();
    expect(await screen.findByText("OpenD 正常")).toBeVisible();
    expect(screen.getByText("2026-08-20")).toBeVisible();

    window.dispatchEvent(new CustomEvent("prediction-stock:eod-update-settled"));

    await waitFor(() => expect(statusLoader).toHaveBeenCalledTimes(2));
    expect(within(screen.getByRole("banner")).getByText("2026-08-21")).toBeVisible();
    expect(screen.getByRole("status", { name: "全局数据状态" })).toHaveTextContent(
      "数据延迟",
    );
    statusLoader.mockRejectedValueOnce(new Error("status endpoint offline"));
    window.dispatchEvent(new CustomEvent("prediction-stock:eod-update-settled"));
    expect(
      await screen.findByRole("status", { name: "状态查询失败" }),
    ).toHaveTextContent("保留上次状态");
    expect(within(screen.getByRole("banner")).getByText("2026-08-21")).toBeVisible();
    expect(screen.queryByText("数据不可用")).not.toBeInTheDocument();
  });

  test("desktop uses horizontal navigation inside the top bar", () => {
    renderAppAt("/overview", { width: 1440, height: 1024 });

    const navigation = screen.getByRole("navigation", { name: "主要导航" });
    expect(navigation).toHaveAttribute("data-layout", "top");
    expect(screen.getByRole("banner")).toContainElement(navigation);
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    expect(
      within(navigation)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(EXPECTED_NAVIGATION);
    expect(
      within(navigation).getByRole("link", { name: "总览，当前页面" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      within(navigation)
        .getByRole("link", { name: "总览，当前页面" })
        .querySelector("svg"),
    ).toHaveClass("lucide-house");
    expect(
      within(navigation).getByRole("link", { name: "筛选器" }).querySelector("svg"),
    ).toHaveClass("lucide-funnel");
  });

  test("tablet uses a 64 px icon rail with complete accessible names", () => {
    renderAppAt("/screen", { width: 900, height: 1024 });

    const navigation = screen.getByRole("navigation", { name: "主要导航" });
    expect(navigation).toHaveAttribute("data-layout", "rail");
    expect(navigation).toHaveAttribute("data-width", "64");
    expect(within(navigation).getAllByRole("link")).toHaveLength(
      EXPECTED_NAVIGATION.length,
    );
    expect(
      within(navigation).getByRole("link", { name: "筛选器，当前页面" }),
    ).toHaveTextContent("筛选");
    expect(within(navigation).getByRole("link", { name: "标的研究" })).toHaveAttribute(
      "href",
      "/research/US.SPY",
    );
  });

  test("mobile uses the shared navigation items in its fixed bottom navigation", () => {
    renderAppAt("/health", { width: 390, height: 844 });

    const navigation = screen.getByRole("navigation", {
      name: "移动端主要导航",
    });
    expect(navigation).toHaveAttribute("data-layout", "bottom");
    expect(navigation).toHaveClass("mobile-nav");
    expect(within(navigation).getAllByRole("link")).toHaveLength(
      EXPECTED_NAVIGATION.length,
    );
    expect(
      within(navigation).getByRole("link", { name: "模型数据，当前页面" }),
    ).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("Signal Lab", { selector: "span" })).toBeVisible();
  });

  test("top bar contains ticker search, OpenD status, data time and EOD action", () => {
    renderAppAt("/overview", { width: 1440, height: 1024 });

    const topBar = screen.getByRole("banner");
    expect(within(topBar).getByRole("combobox", { name: "搜索标的" })).toHaveAttribute(
      "placeholder",
      "搜索当前池代码或名称",
    );
    expect(within(topBar).getByText("OpenD 正常")).toBeVisible();
    expect(within(topBar).getByText("2026-07-10 16:05 ET")).toBeVisible();
    expect(within(topBar).getByText("2026-07-10 16:05 ET")).toBeVisible();
    expect(within(topBar).getByRole("navigation", { name: "主要导航" })).toBeVisible();
    expect(within(topBar).getByRole("button", { name: "一键收盘更新" })).toBeVisible();
  });

  test("OpenD connectivity always combines icon, text and semantic color", () => {
    renderAppAt("/overview", { width: 1440, height: 1024 }, "disconnected");

    const status = screen.getByText("OpenD 已断开").parentElement;
    expect(status).toHaveClass("opend-status--disconnected");
    expect(status?.querySelector("svg")).toBeInTheDocument();
  });

  test("navigation is keyboard reachable and defines a visible focus ring", async () => {
    const user = userEvent.setup();
    renderAppAt("/overview", { width: 1440, height: 1024 });

    await user.tab();
    expect(screen.getByRole("link", { name: "跳至主要内容" })).toHaveFocus();
    await user.tab();

    expect(screen.getByRole("link", { name: "总览，当前页面" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("link", { name: "期权脉搏" })).toHaveFocus();
    const globalCss = readFileSync(
      resolve(process.cwd(), "frontend/src/styles/global.css"),
      "utf8",
    );
    expect(globalCss).toMatch(/:focus-visible[^{]*\{[^}]*outline:/s);
  });

  test("announces rejected URL enum values instead of silently using them", () => {
    renderAppAt("/overview?horizon=99", { width: 1440, height: 1024 });

    const warning = screen.getByRole("alert", { name: "URL 参数警告" });
    expect(warning).toHaveTextContent("URL 参数 horizon 的值“99”无效");
    expect(warning).toHaveTextContent("已使用默认值 5");
  });

  test.each([
    "/screen?return_basis=total_return",
    "/screen/?return_basis=total_return",
  ])("announces total return as invalid on screener URL %s", (path) => {
    renderAppAt(path, { width: 1440, height: 1024 });

    const warning = screen.getByRole("alert", { name: "URL 参数警告" });
    expect(warning).toHaveTextContent("URL 参数 return_basis 的值“total_return”无效");
    expect(warning).toHaveTextContent("已使用默认值 executable");
  });

  test("does not classify an unrelated screen prefix as the screener route", () => {
    renderAppAt("/screening?return_basis=total_return", {
      width: 1440,
      height: 1024,
    });

    expect(
      screen.queryByRole("alert", { name: "URL 参数警告" }),
    ).not.toBeInTheDocument();
  });

  test.each([
    ["stale", "数据已过期"],
    ["offline", "当前离线"],
    ["partial", "数据不完整"],
    ["unavailable", "数据不可用"],
  ] as const)("keeps global %s state visible across routes", (status, label) => {
    renderAppAt("/backtests", { width: 1440, height: 1024 }, "connected", status);

    const banner = screen.getByRole("status", { name: "全局数据状态" });
    expect(banner).toHaveTextContent(label);
    expect(banner).toHaveTextContent("数据截至: 2026-07-10 16:05 ET");
  });

  test("mobile data banner stays below the top bar and above route content", () => {
    renderAppAt("/overview", { width: 390, height: 844 }, "connected", "partial");

    const main = screen.getByRole("main");
    const banner = screen.getByRole("status", { name: "全局数据状态" });
    expect(main.firstElementChild).toContainElement(banner);
    expect(main.firstElementChild?.firstElementChild).toBe(banner);
    expect(screen.getByRole("navigation", { name: "移动端主要导航" })).toHaveAttribute(
      "data-layout",
      "bottom",
    );
    const globalCss = readFileSync(
      resolve(process.cwd(), "frontend/src/styles/global.css"),
      "utf8",
    );
    expect(globalCss).toMatch(
      /@media \(max-width: 767px\)[\s\S]*\.data-status-banner\s*\{[^}]*top:\s*56px/,
    );
    expect(globalCss).toMatch(
      /@media \(max-width: 767px\)[\s\S]*\.app-main\s*\{[^}]*padding-top:\s*56px/,
    );
  });
});
