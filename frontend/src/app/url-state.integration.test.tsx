import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import { ResearchApiError } from "../api/client";
import { APP_ROUTES } from "./router";
import { RouteErrorState } from "./RouteErrorState";

afterEach(() => cleanup());

function renderRoutes(path: string) {
  const router = createMemoryRouter(APP_ROUTES, { initialEntries: [path] });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("route and URL integration", () => {
  test("reloads the document when a route retry has no local callback", async () => {
    const user = userEvent.setup();
    const reload = vi.fn();
    const originalWindow = window;
    render(<RouteErrorState error={new Error("failed lazy import")} />);
    vi.stubGlobal("window", new Proxy(originalWindow, {
      get(target, key): unknown {
        return key === "location" ? { reload } : Reflect.get(target, key, target);
      },
    }));
    try {
      await user.click(screen.getByRole("button", { name: "重试" }));
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  test("redirects only the structural root to overview", async () => {
    const router = createMemoryRouter(APP_ROUTES, { initialEntries: ["/"] });
    try {
      await waitFor(() => expect(router.state.location.pathname).toBe("/overview"), {
        timeout: 5_000,
      });
      await router.navigate("/health?horizon=20");
      expect(router.state.location.pathname).toBe("/health");
      expect(router.state.location.search).toBe("?horizon=20");
    } finally {
      router.dispose();
    }
  });

  test("preserves exact screen and backtest URLs across browser back and forward", async () => {
    const router = renderRoutes(
      "/screen?horizon=5&sort=brier_skill&direction=desc&confidence=high",
    );
    const screenSearch = router.state.location.search;

    await router.navigate(
      "/backtests?split_version=split-v6&fold=outer-2&horizon=20&cost_scenario=stress",
    );
    const backtestSearch = router.state.location.search;
    await router.navigate(-1);
    expect(router.state.location.pathname).toBe("/screen");
    expect(router.state.location.search).toBe(screenSearch);

    await router.navigate(1);
    expect(router.state.location.pathname).toBe("/backtests");
    expect(router.state.location.search).toBe(backtestSearch);
  }, 20_000);

  test("mounts the real research and health workspaces instead of placeholders", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => Promise.reject(new TypeError("offline")));
    const researchRouter = renderRoutes("/research/NVDA?horizon=20");

    expect(
      await screen.findByRole(
        "heading",
        { name: "NVDA 标的研究室", level: 1 },
        { timeout: 15_000 },
      ),
    ).toBeVisible();
    expect(screen.queryByTestId("route-outlet-research")).not.toBeInTheDocument();
    expect(researchRouter.state.location.search).toContain("horizon=20");
    cleanup();

    const healthRouter = renderRoutes("/health");
    expect(
      await screen.findByRole(
        "heading",
        { name: "模型与数据健康", level: 1 },
        { timeout: 15_000 },
      ),
    ).toBeVisible();
    expect(screen.queryByTestId("route-outlet-health")).not.toBeInTheDocument();
    expect(healthRouter.state.location.pathname).toBe("/health");
    globalThis.fetch = originalFetch;
  }, 20_000);

  test.each([
    ["unknown_symbol", "未找到标的", "返回筛选器"],
    ["not_in_universe", "标的不在当前版本标的池", "查看标的池版本"],
    ["snapshot_version_conflict", "快照版本冲突", "返回当前版本"],
    ["research_snapshot_unavailable", "尚无可用研究快照", "重试"],
    ["snapshot_integrity_error", "研究快照完整性校验失败", "查看数据健康"],
    ["network_error", "当前网络离线", "重试"],
  ])("maps %s to a specific safe recovery state", async (code, title, action) => {
    const user = userEvent.setup();
    const retry = vi.fn();
    render(
      <RouteErrorState
        error={
          new ResearchApiError({
            affectedScope: ["snapshot"],
            code,
            kind: code === "network_error" ? "network" : "http",
            message: "sanitized",
            requestId: "request-safe",
            retryable: true,
            status: code === "network_error" ? null : 503,
          })
        }
        onRetry={retry}
      />,
    );

    expect(screen.getByRole("heading", { name: title })).toBeVisible();
    const recovery = screen.getByRole(action === "重试" ? "button" : "link", {
      name: action,
    });
    if (action === "重试") {
      await user.click(recovery);
      expect(retry).toHaveBeenCalledTimes(1);
    }
  });
});
