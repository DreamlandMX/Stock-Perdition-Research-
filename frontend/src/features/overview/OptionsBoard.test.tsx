import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { OptionBoardResponse } from "../options/useOptionsBoard";
import type { OverviewHistory } from "./overview-history";
import { OptionsBoard } from "./OptionsBoard";
import { I18nProvider, useI18n } from "../../i18n/context";

function LanguageControls() {
  const i18n = useI18n();
  return (
    <>
      <button onClick={() => i18n.setLocale("en-US")}>English</button>
      <button onClick={() => i18n.setLocale("zh-CN")}>中文</button>
    </>
  );
}

vi.mock("./OverviewTerminalChart", () => ({ OverviewTerminalChart: () => null }));
afterEach(cleanup);

test("all-symbol cards paginate after sorting, search names, and keep research links usable", async () => {
  const user = userEvent.setup();
  const items = Array.from({ length: 50 }, (_, i) => ({
    symbol: `US.S${i}`,
    snapshot_id: `snapshot-${i}`,
    data_status: "available",
    spot: 100,
    atm_iv_30d: i / 100,
    total_volume: 10,
    total_premium: 100,
    expiry_buckets_volume: [1, 2, 3],
    reasons: [],
    market_date: "2026-10-30",
    prob_month: i === 49 ? { reason: "probability_interval_outside_grid" } : null,
    top_contracts: [],
    strike_profile: [],
  }));
  render(
    <MemoryRouter>
      <I18nProvider>
        <LanguageControls />
        <OptionsBoard
          board={
            {
              items,
              market: null,
              reasons: [],
              data_status: "partial",
            } as unknown as OptionBoardResponse
          }
          items={[]}
          points={[{ symbol: "US.S0", name: "测试股票" }]}
          history={{ histories: {} } as OverviewHistory}
          linkState={{
            asOf: undefined,
            calibrationVersion: undefined,
            datasetRevision: undefined,
            featureSetVersion: undefined,
            horizon: 5,
            modelVersion: undefined,
            snapshotId: undefined,
            universeVersion: undefined,
          }}
        />
      </I18nProvider>
    </MemoryRouter>,
  );
  const cards = () =>
    within(screen.getByRole("region", { name: "标的期权卡片" })).getAllByRole("link");
  expect(cards()).toHaveLength(24);
  expect(screen.getByText("1 月区间超出 ±20% 网格，不外推")).toBeVisible();
  expect(cards()[0]).toHaveAccessibleName("打开 S49 研究页");
  await user.click(screen.getByRole("button", { name: "下一页" }));
  expect(cards()[0]).toHaveAccessibleName("打开 S25 研究页");
  await user.click(screen.getByRole("button", { name: "English" }));
  const englishCards = () =>
    within(
      screen.getByRole("region", { name: "Instrument options cards" }),
    ).getAllByRole("link");
  expect(englishCards()[0]).toHaveAccessibleName("Open S25 research");
  expect(screen.getByRole("button", { name: "By IV" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByText(/2\/3/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "中文" }));
  await user.type(screen.getByRole("searchbox", { name: "搜索期权标的" }), "测试股票");
  expect(cards()).toHaveLength(1);
  expect(cards()[0]).toHaveAccessibleName("打开 S0 研究页");
  expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "English" }));
  expect(
    screen.getByRole("searchbox", { name: "Search options instruments" }),
  ).toHaveValue("测试股票");
  expect(englishCards()).toHaveLength(1);
  expect(englishCards()[0]).toHaveAccessibleName("Open S0 research");
  await user.click(screen.getByRole("button", { name: "中文" }));
  cards()[0]!.focus();
  await user.keyboard("{Enter}");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
}, 15000);
