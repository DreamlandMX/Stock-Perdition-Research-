import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "../../api/generated/schema";
import {
  OptionAnalyticsPanel,
  loadOptionAnalyticsFromApi,
} from "./OptionAnalyticsPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
type Response = components["schemas"]["OptionAnalyticsResponse"];
const empty: Response = {
  request_id: "test",
  generated_at: "2026-10-10T18:00:00Z",
  data: null,
  reasons: ["no_option_snapshot"],
};
const partial: Response = {
  ...empty,
  reasons: [],
  data: {
    symbol: "US.AAPL",
    snapshot_id: "real-saved",
    as_of: "2026-10-10T18:00:00Z",
    feature_version: "option-numeric-v2",
    status: "partial",
    provider: "futu",
    feed: "account-verified",
    normalization_version: "options-v2",
    dataset_revision: "forward-v2",
    captured_at: "2026-10-10T17:59:00Z",
    market_as_of: "2026-10-10T17:58:00Z",
    source_available_at: "2026-10-10T17:59:00Z",
    values: { atm_iv_30d: null },
    missing_reasons: { atm_iv_30d: ["field_time_unit_or_coverage_unproven"] },
    source_snapshot_ids: ["real-saved"],
    iv_terms: [],
    skew_points: [],
    concentration: [],
    history: [],
    reasons: ["market_time_unproven"],
  },
};

function mount(
  loadData: NonNullable<Parameters<typeof OptionAnalyticsPanel>[0]["loadData"]>,
  enabled = true,
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rendered = render(
    <QueryClientProvider client={client}>
      <OptionAnalyticsPanel
        symbol="US.AAPL"
        query={{ metric: "oi", side: "call", snapshot_id: "real-saved" }}
        loadData={loadData}
        enabled={enabled}
      />
    </QueryClientProvider>,
  );
  return { ...rendered, client };
}

describe("saved option analytics", () => {
  it("keeps absent data and history empty without invented charts", async () => {
    mount(() => Promise.resolve(empty));
    expect(await screen.findByText(/期权分析不可用：尚无已保存期权快照/)).toBeVisible();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
  it("shows source and timing failures beside partial retained evidence", async () => {
    mount(() => Promise.resolve(partial));
    await userEvent.setup().click(await screen.findByText("分析来源与时间"));
    expect(await screen.findByText(/市场时间未确认/)).toBeVisible();
    expect(screen.getByText(/来源 futu \/ account-verified/)).toBeVisible();
    expect(screen.getByText(/输入快照 real-saved/)).toHaveTextContent("分析截止");
    expect(screen.getByText(/字段时间、单位或覆盖未证实/)).toBeInTheDocument();
    expect(screen.getByText(/可比较的已保存快照不足两个市场时刻/)).toBeVisible();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
  it("renders actual term/skew/history values with source snapshots and zero OI", async () => {
    const data = partial.data!;
    mount(() =>
      Promise.resolve({
        ...partial,
        data: {
          ...data,
          iv_terms: [
            {
              expiration: "2026-11-06",
              dte: 27,
              strike: 250,
              moneyness: 1,
              call_iv: 0.2,
              put_iv: 0.22,
              atm_iv: 0.21,
              contract_ids: ["call", "put"],
            },
          ],
          skew_points: [
            {
              expiration: "2026-11-06",
              dte: 27,
              put_25delta_iv: 0.25,
              call_25delta_iv: 0.2,
              skew: 0.05,
              contract_ids: ["25-put", "25-call"],
              reasons: [],
            },
          ],
          history: [
            {
              snapshot_id: "prior",
              market_as_of: "2026-10-09T17:58:00Z",
              source_available_at: "2026-10-09T17:59:00Z",
              underlying_price: 249,
              strike: 250,
              oi: 0,
              gamma: null,
              contract_ids: ["call", "put"],
            },
            {
              snapshot_id: "real-saved",
              market_as_of: "2026-10-10T17:58:00Z",
              source_available_at: "2026-10-10T17:59:00Z",
              underlying_price: 250,
              strike: 250,
              oi: 12,
              gamma: null,
              contract_ids: ["call", "put"],
            },
          ],
        },
      }),
    );
    expect(
      await screen.findByRole("heading", { name: "ATM IV 期限结构" }),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "25-Delta IV 偏斜" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "历史结构；OI" })).toBeVisible();
    expect(screen.getByText("0", { selector: "td" })).toBeInTheDocument();
    expect(screen.getByText(/仅使用 2 个实际市场时刻/)).toHaveTextContent(
      "黄线为当时标的价格",
    );
  });
  it("does not read analytics without a saved snapshot", () => {
    const load = vi.fn(() => Promise.resolve(empty));
    mount(load, false);
    expect(screen.getByText(/尚无可分析的已保存快照/)).toBeVisible();
    expect(load).not.toHaveBeenCalled();
  });
  it("loads saved analytics using only its independent snapshot and cutoff", async () => {
    const transport = vi.fn<(request: Request) => Promise<globalThis.Response>>(() =>
      Promise.resolve(
        new globalThis.Response(JSON.stringify(empty), {
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", transport);
    await loadOptionAnalyticsFromApi("US.AAPL", {
      snapshot_id: "saved-1",
      as_of: "2026-10-10T18:00:00Z",
      side: "put",
      metric: "gamma",
      dte_min: 3,
    });
    await waitFor(() => expect(transport).toHaveBeenCalledOnce());
    const request = transport.mock.calls[0]?.[0];
    const url = new URL(request!.url);
    expect(url.pathname).toBe("/api/v1/instruments/US.AAPL/options/analytics");
    expect(url.searchParams.get("snapshot_id")).toBe("saved-1");
    expect(url.searchParams.get("as_of")).toBe("2026-10-10T18:00:00Z");
    expect(url.searchParams.has("metric")).toBe(false);
    expect(request!.method).toBe("GET");
  });
  it("reports invalid persisted sources as a readable error", async () => {
    mount(() =>
      Promise.reject(new Error("Persisted option snapshot could not be read")),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Persisted option snapshot could not be read",
    );
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
  it("retains the original source and timing when a reread fails", async () => {
    const load = vi.fn(() => Promise.resolve(partial));
    const { client } = mount(load);
    await userEvent.setup().click(await screen.findByText("分析来源与时间"));
    expect(await screen.findByText(/输入快照 real-saved/)).toBeVisible();
    load.mockRejectedValueOnce(new Error("saved source temporarily locked"));
    await client.invalidateQueries({ queryKey: ["option-analytics", "US.AAPL"] });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "保留上次成功读取的分析及原时间",
    );
    expect(screen.getByText(/来源 futu \/ account-verified/)).toBeVisible();
    expect(screen.getByText(/输入快照 real-saved/)).toHaveTextContent("分析截止");
  });
});
