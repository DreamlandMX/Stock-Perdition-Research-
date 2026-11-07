import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, expect, test } from "vitest";

import { TopBar } from "./TopBar";

afterEach(cleanup);

function Location() {
  return <output aria-label="目标路径">{useLocation().pathname}</output>;
}

test.each(["BRK.B", "US.BRK.B", "TSM"])(
  "搜索股票 %s 保留正确的美股代码",
  async (symbol) => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <TopBar
            dataAsOf="2026-09-29"
            opendStatus="connected"
            showProductName={false}
          />
          <Routes>
            <Route path="*" element={<Location />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await userEvent.type(
      screen.getByRole("combobox", { name: "搜索标的" }),
      `${symbol}{Enter}`,
    );
    expect(screen.getByLabelText("目标路径")).toHaveTextContent(
      `/research/US.${symbol.replace(/^US\./, "")}`,
    );
  },
);
