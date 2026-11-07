import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, test } from "vitest";

import { APP_ROUTES } from "./router";

afterEach(() => {
  cleanup();
});

describe("research application routes", () => {
  test("defines the page routes including the standalone options pulse", () => {
    const root = APP_ROUTES[0];

    expect(
      root?.children?.filter((route) => !route.index).map((route) => route.path),
    ).toEqual([
      "overview",
      "options",
      "screen",
      "research/:symbol",
      "backtests",
      "health",
    ]);
    expect(root?.children?.filter((route) => route.index)).toHaveLength(1);
  });

  test("provides an accessible initial fallback while lazy routes load", () => {
    const Fallback = APP_ROUTES[0]?.HydrateFallback;

    expect(Fallback).toBeTypeOf("function");
    if (Fallback == null) {
      return;
    }

    render(<Fallback />);

    expect(screen.getByRole("status", { name: "正在载入研究页面" })).toBeVisible();
  });

  test("routes backtests to the calibration evidence workspace", async () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1440,
    });
    const router = createMemoryRouter(APP_ROUTES, {
      initialEntries: ["/backtests"],
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole(
        "heading",
        {
          name: "回测与校准诊断",
          level: 1,
        },
        { timeout: 15_000 },
      ),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("route-outlet-backtests")).not.toBeInTheDocument();
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "回测校准，当前页面" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  }, 20_000);

  test("routes health without nesting a second main landmark", async () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1440,
    });
    const router = createMemoryRouter(APP_ROUTES, { initialEntries: ["/health"] });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole(
        "heading",
        { name: "模型与数据健康", level: 1 },
        { timeout: 15_000 },
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("main")).toHaveLength(1);
  }, 20_000);

  test("routes overview to the evidence workspace", async () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1440,
    });
    const router = createMemoryRouter(APP_ROUTES, { initialEntries: ["/overview"] });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole("heading", { name: "市场总览", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("route-outlet-overview")).not.toBeInTheDocument();
  });

  test("routes screen to the reproducible screener workspace", async () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1440,
    });
    const router = createMemoryRouter(APP_ROUTES, { initialEntries: ["/screen"] });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole("heading", { name: "研究筛选器", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("route-outlet-screen")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "筛选器，当前页面" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  test("redirects the structural root to overview", async () => {
    const router = createMemoryRouter(APP_ROUTES, { initialEntries: ["/"] });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    await screen.findByRole("heading", { name: "市场总览", level: 1 });
    expect(router.state.location.pathname).toBe("/overview");
  });
});
