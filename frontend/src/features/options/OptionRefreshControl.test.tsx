import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { OptionRefreshControl } from "./OptionRefreshControl";
import { I18nProvider, useI18n } from "../../i18n/context";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("../../api/client", async (original) => ({
  ...(await original<typeof import("../../api/client")>()),
  createResearchApiClient: () => api,
}));
afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({ data: null });
});
function ChangeLanguage() {
  const i18n = useI18n();
  return <button onClick={() => i18n.setLocale("en-US")}>English</button>;
}

test("language changes translate progress without reading or submitting another operation", async () => {
  const user = userEvent.setup();
  api.get.mockResolvedValue({
    data: {
      operation_id: "saved-operation",
      status: "completed",
      items: [{ symbol: "US.SPY", status: "completed" }],
    },
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <I18nProvider>
        <ChangeLanguage />
        <OptionRefreshControl />
      </I18nProvider>
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("status")).toHaveTextContent("更新完成");
  const reads = api.get.mock.calls.length;
  await user.click(screen.getByRole("button", { name: "English" }));
  expect(screen.getByRole("status")).toHaveTextContent("Update completed");
  expect(screen.getByRole("button", { name: "Update all options" })).toBeEnabled();
  expect(api.get).toHaveBeenCalledTimes(reads);
  expect(api.post).not.toHaveBeenCalled();
});
function mount(
  symbol?: string,
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  return render(
    <QueryClientProvider client={client}>
      <OptionRefreshControl {...(symbol ? { symbol } : {})} />
    </QueryClientProvider>,
  );
}
test("only a click submits current-symbol collection and progress survives a remount", async () => {
  const user = userEvent.setup();
  const first = mount("US.TSM");
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  expect(api.post).not.toHaveBeenCalled();
  const operation = {
    operation_id: "option-refresh-abc",
    status: "queued",
    items: [{ symbol: "US.TSM", status: "queued" }],
  };
  api.post.mockResolvedValue({ data: operation });
  await user.click(screen.getByRole("button", { name: "更新当前标的期权" }));
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith(
      "/api/v1/options/refresh",
      expect.objectContaining({ body: { symbol: "US.TSM" } }),
    ),
  );
  expect(screen.getByRole("button", { name: "期权更新进行中…" })).toBeDisabled();
  first.unmount();
  api.get.mockResolvedValue({ data: operation });
  mount("US.TSM");
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("排队中"));
  expect(api.post).toHaveBeenCalledTimes(1);
});

test("a full refresh reloads saved analyses once at completion, not after every symbol", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const operation = {
    operation_id: "option-refresh-batch",
    status: "running",
    items: [
      { symbol: "US.DIA", status: "completed", snapshot_id: "dia-new" },
      { symbol: "US.INTC", status: "queued" },
    ],
  };
  api.get.mockResolvedValue({ data: operation });
  mount(undefined, client);
  await screen.findByText(/正在更新/);
  expect(invalidate).not.toHaveBeenCalled();
  client.setQueryData(["option-refresh-operation"], {
    ...operation,
    status: "partial",
  });
  await waitFor(() => expect(invalidate).toHaveBeenCalledOnce());
});
test("all-symbol submission reports each failure without discarding successes", async () => {
  const user = userEvent.setup();
  mount();
  api.post.mockResolvedValue({
    data: {
      operation_id: "option-refresh-abc",
      status: "partial",
      items: [
        {
          symbol: "US.SPY",
          status: "completed",
          captured_at: "2026-10-14T20:00:00Z",
          market_at: "2026-10-14T19:59:00Z",
        },
        { symbol: "US.QQQ", status: "failed", reason: "permission_denied" },
      ],
    },
  });
  await user.click(screen.getByRole("button", { name: "更新全部期权" }));
  await screen.findByText(/部分完成/);
  expect(api.post).toHaveBeenCalledWith(
    "/api/v1/options/refresh",
    expect.objectContaining({ body: {} }),
  );
  await user.click(screen.getByText("逐标的更新结果"));
  expect(screen.getByText(/行情权限不足/)).toBeVisible();
});
