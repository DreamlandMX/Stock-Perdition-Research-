import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("../features/operations/EodUpdateButton", () => ({
  EodUpdateButton: () => null,
}));

import { AppShell } from "../layout/AppShell";

afterEach(() => cleanup());

describe("cross-page accessibility guarantees", () => {
  test("keeps five-route navigation keyboard reachable with a main landmark", async () => {
    const user = userEvent.setup();
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1440 });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/overview"]}>
          <Routes>
            <Route
              element={
                <AppShell
                  dataAsOf="2026-07-18T13:00:00Z"
                  dataStatus="live"
                  opendStatus="connected"
                />
              }
            >
              <Route path="*" element={<h1>页面内容</h1>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
    expect(screen.getByRole("navigation", { name: "主要导航" })).toBeVisible();
    await user.tab();
    expect(screen.getByRole("link", { name: "跳至主要内容" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("link", { name: "总览，当前页面" })).toHaveFocus();
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    await user.keyboard("{Enter}");
    expect(screen.getByRole("main")).toHaveFocus();
  });

  test("defines visible focus, 44 px touch targets and reduced-motion fallbacks", () => {
    const css = readFileSync(
      resolve(process.cwd(), "frontend/src/styles/global.css"),
      "utf8",
    );
    expect(css).toMatch(/:focus-visible[^{]*\{[^}]*outline:/s);
    expect(css).toMatch(/min-height:\s*44px/);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)/);
  });
});
