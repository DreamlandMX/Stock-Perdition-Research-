import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "../../api/generated/schema";
import { OptionForecastPanel } from "./OptionForecastPanel";

afterEach(cleanup);
type Response = components["schemas"]["OptionForecastResponse"];
const fallback: Response = {
  request_id: "test",
  generated_at: "2026-10-10T18:00:00Z",
  data: {
    symbol: "US.AAPL",
    status: "baseline_fallback",
    model_source: "stock_baseline",
    model_version: "stock-baseline-verified-v3",
    reasons: ["candidate_not_promoted"],
    candidate: { status: "research_only", version: "option-E1-v2", promoted: false },
    option_snapshot_id: "option-saved",
    option_cutoff: "2026-10-10T17:58:00Z",
    stock_snapshot_id: "stock-saved",
    stock_cutoff: "2026-10-09T20:00:00Z",
    horizons: [
      { horizon_days: 5, q10: -0.04, q50: 0.01, q90: 0.08, probabilities: null },
    ],
  },
};
function mount(
  loadData: NonNullable<Parameters<typeof OptionForecastPanel>[0]["loadData"]>,
) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter
        initialEntries={[
          "/research/US.AAPL?snapshot_id=stock-pinned&as_of=2026-10-09T20%3A00%3A00Z",
        ]}
      >
        <OptionForecastPanel
          symbol="US.AAPL"
          optionSnapshotId="option-pinned"
          loadData={loadData}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
describe("option-enhanced forecast source", () => {
  it("labels an unpromoted candidate and actual baseline source/version", async () => {
    const load = vi.fn(() => Promise.resolve(fallback));
    mount(load);
    expect(await screen.findByText(/回退至股票基线/)).toBeVisible();
    expect(screen.getByText(/实际输出来源 stock_baseline/)).toHaveTextContent(
      "stock-baseline-verified-v3",
    );
    expect(screen.getByText(/候选版本 option-E1-v2/)).toHaveTextContent("未晋升");
    expect(screen.getByText("-4.00%", { selector: "td" })).toBeVisible();
    expect(screen.getByText("来源模型未提供")).toBeVisible();
    expect(load).toHaveBeenCalledWith("US.AAPL", {
      option_snapshot_id: "option-pinned",
      stock_snapshot_id: "stock-pinned",
      stock_as_of: "2026-10-09T20:00:00Z",
    });
  });
  it("shows refusal reasons and no inferred baseline values", async () => {
    mount(() =>
      Promise.resolve({
        ...fallback,
        data: {
          ...fallback.data,
          status: "refused",
          model_source: null,
          model_version: null,
          reasons: ["baseline_unavailable"],
          horizons: [],
        },
      }),
    );
    expect(await screen.findByText(/拒绝预测/)).toBeVisible();
    expect(screen.getByText(/股票基线预测不可用/)).toBeVisible();
    expect(screen.getByText(/模型版本 无/)).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
  it("does not publish candidate values for a research-only response", async () => {
    mount(() =>
      Promise.resolve({
        ...fallback,
        data: { ...fallback.data, status: "research_only" },
      }),
    );
    expect(await screen.findByText(/研究候选，未发布/)).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText(/研究候选不会冒充已发布预测/)).toBeVisible();
  });
  it("keeps calibrated interval bounds separate from raw return quantiles", async () => {
    mount(() =>
      Promise.resolve({
        ...fallback,
        data: {
          ...fallback.data,
          status: "enhanced",
          model_source: "option_enhanced",
          model_version: "promoted-E1",
          candidate: { status: "promoted", version: "promoted-E1", promoted: true },
          reasons: [],
          horizons: [
            {
              horizon_days: 5,
              q10: -0.04,
              q50: 0.01,
              q90: 0.08,
              probabilities: null,
              interval_low: -0.06,
              interval_high: 0.12,
              interval_method: "CQR",
              target_coverage: 0.8,
            },
          ],
        },
      }),
    );
    expect(await screen.findByText(/实际输出来源 option_enhanced/)).toHaveTextContent(
      "promoted-E1",
    );
    expect(screen.getByText("-4.00%", { selector: "td" })).toBeVisible();
    expect(screen.getByText("8.00%", { selector: "td" })).toBeVisible();
    expect(screen.getByText(/-6.00%–12.00%/)).toHaveTextContent("目标覆盖 80.00%；CQR");
  });
});
