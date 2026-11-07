import { I18nProvider, useI18n } from "../../i18n/context";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { components } from "../../api/generated/schema";
import { APP_ROUTES } from "../../app/router";

type ScreenResponse = components["schemas"]["ResearchResponse_ScreenPage_"];
type InstrumentResponse = components["schemas"]["ResearchResponse_InstrumentPage_"];

const SNAPSHOT = {
  as_of: "2026-07-10T20:05:00Z",
  calibration_version: "CAL-2026-07-08",
  dataset_revision: "bars-r42",
  feature_set_version: "features-v7",
  model_version: "SL-2.6.1",
  prediction_readiness: "forecast_ready",
  research_readiness: "full_verified",
  revision_no: 3,
  snapshot_id: "SL-20260710-1605",
  universe_version: "us-100-2026q3",
} satisfies components["schemas"]["ResolvedSnapshot"];

const ROW = {
  abstain_reasons: [],
  as_of: SNAPSHOT.as_of,
  asset_type: "stock",
  breakout_down_probability: 0.15,
  breakout_up_probability: 0.7,
  capital_gain_q10: -0.1,
  capital_gain_q50: 0.07,
  confidence_level: "medium",
  data_freshness_seconds: 18,
  effective_abstain: false,
  executable_q10: -0.112,
  executable_q50: 0.064,
  executable_up_probability: 0.52,
  forecast_up_probability: 0.76,
  horizon: 20,
  interval_width: 0.428,
  model_version: SNAPSHOT.model_version,
  name: "NVIDIA",
  regime: "uptrend",
  sector: "technology",
  serving_status: "live",
  similar_case_count: 28,
  symbol: "US.NVDA",
} satisfies components["schemas"]["ScreenRow"];

const SECOND_ROW = {
  ...ROW,
  breakout_up_probability: 0.61,
  executable_up_probability: 0.81,
  name: "Microsoft",
  symbol: "US.MSFT",
} satisfies components["schemas"]["ScreenRow"];

function screenResponse(page = 2): ScreenResponse {
  return {
    data: {
      items: [ROW],
      page,
      page_size: 25,
      prediction_basis: "forecast",
      return_basis: "capital_gain",
      sort_direction: "asc",
      sort_key: "breakout_up_probability",
      total: 26,
    },
    data_status: "ready",
    effective_abstain: false,
    evaluated_at: "2026-07-10T20:05:04Z",
    exclusion_reason: [],
    generated_at: "2026-07-10T20:05:04Z",
    request_id: "request-screen-1",
    resolved_snapshot: SNAPSHOT,
    serving_status: "live",
  };
}

const INSTRUMENTS = {
  data: {
    items: [
      {
        asset_type: "stock",
        currency: "USD",
        effective_from: "2026-01-02T00:00:00Z",
        effective_to: null,
        exchange: "NASDAQ",
        name: "NVIDIA",
        sector: "technology",
        symbol: "US.NVDA",
        universe_version: SNAPSHOT.universe_version,
      },
    ],
    page: 1,
    page_size: 100,
    sort_direction: "asc",
    sort_key: "symbol",
    total: 1,
  },
  data_status: "ready",
  effective_abstain: false,
  evaluated_at: "2026-07-10T20:05:04Z",
  exclusion_reason: [],
  generated_at: "2026-07-10T20:05:04Z",
  request_id: "request-instruments-1",
  resolved_snapshot: SNAPSHOT,
  serving_status: "live",
} satisfies InstrumentResponse;

function createClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 60_000 } },
  });
}

interface ApiOptions {
  failInstruments?: boolean;
  screen?: (url: URL) => Promise<ScreenResponse> | ScreenResponse;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function responseForRequest(url: URL): ScreenResponse {
  const sort = url.searchParams.get("sort") ?? "default";
  const direction = url.searchParams.get("direction") ?? "desc";
  const page = Number(url.searchParams.get("page") ?? 1);
  const items =
    sort === "executable_up_probability" ? [SECOND_ROW, ROW] : [ROW, SECOND_ROW];
  return {
    ...screenResponse(page),
    data: {
      ...screenResponse(page).data,
      items,
      page,
      sort_direction: direction as "asc" | "desc",
      sort_key: sort,
    },
  };
}

function emptyScreenResponse(
  servingStatus: ScreenResponse["serving_status"],
  effectiveAbstain: boolean,
): ScreenResponse {
  const response = screenResponse(1);
  return {
    ...response,
    data: { ...response.data, items: [], page: 1, total: 0 },
    effective_abstain: effectiveAbstain,
    serving_status: servingStatus,
  };
}

function installApi(options: ApiOptions = {}) {
  const requests: URL[] = [];
  const fetch = vi.fn((request: Request) => {
    const url = new URL(request.url);
    requests.push(url);
    if (url.pathname === "/api/v1/instruments" && options.failInstruments === true) {
      return Promise.resolve(
        new Response(JSON.stringify({ detail: "metadata unavailable" }), {
          headers: { "content-type": "application/json" },
          status: 503,
        }),
      );
    }
    const body =
      url.pathname === "/api/v1/health"
        ? { data_status: "ready", components: [] }
        : url.pathname === "/api/v1/snapshot-context"
          ? {
              ...screenResponse(1),
              data: { symbol_count: 204, instruments: INSTRUMENTS.data.items },
            }
          : url.pathname === "/api/v1/instruments"
            ? INSTRUMENTS
            : (options.screen?.(url) ?? responseForRequest(url));
    return Promise.resolve(body).then(
      (resolvedBody) =>
        new Response(JSON.stringify(resolvedBody), {
          headers: { "content-type": "application/json" },
          status: 200,
        }),
    );
  });
  vi.stubGlobal("fetch", fetch);
  return { fetch, requests };
}

function renderRoute(entry: string, width = 1440, client = createClient()) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  const router = createMemoryRouter(APP_ROUTES, { initialEntries: [entry] });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("reproducible research screener", () => {
  test.each([false, true])(
    "removing one applied filter preserves the other filters and snapshot (initially pinned: %s)",
    async (pinned) => {
      installApi();
      const router = renderRoute(
        `/screen?mode=research&sector=technology&asset_type=stock&sort=verified_q10&page=2${pinned ? `&snapshot_id=${SNAPSHOT.snapshot_id}&as_of=${SNAPSHOT.as_of}` : ""}`,
      );
      const remove = await screen.findByRole(
        "button",
        { name: /移除行业/ },
        { timeout: 5_000 },
      );
      await waitFor(() =>
        expect(router.state.location.search).toContain("snapshot_id="),
      );
      const before = new URLSearchParams(router.state.location.search);
      await userEvent.setup().click(remove);
      await waitFor(() =>
        expect(new URLSearchParams(router.state.location.search).has("sector")).toBe(
          false,
        ),
      );
      const after = new URLSearchParams(router.state.location.search);
      expect(after.get("asset_type")).toBe("stock");
      expect(after.get("sort")).toBe("verified_q10");
      expect(after.get("snapshot_id")).toBe(before.get("snapshot_id"));
      expect(after.get("page") ?? "1").toBe("1");
      expect(screen.getByRole("heading", { name: "筛选结果" })).toHaveFocus();
    },
  );
  test("background rank changes preserve sorting control state and keyboard focus", async () => {
    const client = createClient();
    const pending = deferred<ScreenResponse>();
    let refreshing = false;
    installApi({
      screen: (url) => (refreshing ? pending.promise : responseForRequest(url)),
    });
    const router = renderRoute("/screen?mode=research", 1440, client);
    const toggle = await screen.findByRole(
      "combobox",
      { name: "研究排序" },
      { timeout: 5_000 },
    );
    await screen.findByRole("table", { name: "已验证研究结果（不可执行）" });
    await waitFor(() =>
      expect(
        client
          .getQueryCache()
          .getAll()
          .find(
            (query) => query.queryKey[0] === "screen" && query.getObserversCount() > 0,
          )
          ?.queryKey.at(-1),
      ).toBe(SNAPSHOT.revision_no),
    );
    toggle.focus();
    expect(toggle).toHaveFocus();
    const beforeSearch = router.state.location.search;
    const rowSymbols = () =>
      within(screen.getByRole("table", { name: "已验证研究结果（不可执行）" }))
        .getAllByRole("row")
        .slice(1)
        .map((row) => row.textContent?.match(/NVDA|MSFT/)?.[0]);
    expect(rowSymbols()).toEqual(["NVDA", "MSFT"]);
    refreshing = true;
    let refresh!: Promise<void>;
    act(() => {
      refresh = client.invalidateQueries({ queryKey: ["screen"], type: "active" });
    });
    await waitFor(() => expect(client.isFetching()).toBeGreaterThan(0));
    expect(screen.getByRole("combobox", { name: "研究排序" })).toBe(toggle);
    expect(toggle).toHaveFocus();
    expect(screen.queryByLabelText("正在加载筛选结果")).not.toBeInTheDocument();
    await act(async () => {
      const updated = responseForRequest(new URL("http://localhost/screen"));
      pending.resolve({
        ...updated,
        data: { ...updated.data, items: [...updated.data.items].reverse() },
      });
      await refresh;
    });
    expect(screen.getByRole("combobox", { name: "研究排序" })).toBe(toggle);
    expect(toggle).toHaveFocus();
    await waitFor(() => expect(rowSymbols()).toEqual(["MSFT", "NVDA"]));
    expect(toggle).toHaveFocus();
    expect(router.state.location.search).toBe(beforeSearch);
  });
  test("empty research results clear filters without changing sort or snapshot", async () => {
    installApi({ screen: () => emptyScreenResponse("live", false) });
    const router = renderRoute(
      "/screen?mode=research&sector=technology&sort=verified_q10&direction=desc",
    );
    await userEvent
      .setup()
      .click(
        await screen.findByRole(
          "button",
          { name: "清除筛选条件，保留排序与快照" },
          { timeout: 5_000 },
        ),
      );
    await waitFor(() => {
      const params = new URLSearchParams(router.state.location.search);
      expect(params.has("sector")).toBe(false);
      expect(params.get("sort")).toBe("verified_q10");
      expect(screen.getByRole("combobox", { name: "研究排序方向" })).toHaveValue(
        "desc",
      );
      expect(params.get("snapshot_id")).toBe(SNAPSHOT.snapshot_id);
    });
  });
  test("research sort controls send verified fields and reset pagination", async () => {
    const { requests } = installApi();
    const router = renderRoute("/screen?mode=research&page=2");
    const user = userEvent.setup();
    const sortControl = await screen.findByRole(
      "combobox",
      { name: "研究排序" },
      { timeout: 5_000 },
    );
    sortControl.focus();
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "研究排序" }, { timeout: 5_000 }),
      "verified_q50",
    );
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "研究排序" })).toHaveValue(
        "verified_q50",
      ),
    );
    expect(sortControl).toHaveFocus();
    await user.selectOptions(
      screen.getByRole("combobox", { name: "研究排序方向" }),
      "desc",
    );
    await waitFor(() => {
      expect(
        requests.some(
          (url) =>
            url.pathname === "/api/v1/screen" &&
            url.searchParams.get("sort") === "verified_q50" &&
            url.searchParams.get("direction") === "desc" &&
            url.searchParams.get("page") === "1",
        ),
      ).toBe(true);
      expect(router.state.location.search).not.toContain("page=2");
    });
    await user.click(screen.getByRole("button", { name: /实验候选/ }));
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "研究排序" })).toHaveValue("symbol"),
    );
    expect(
      screen.queryByRole("option", { name: "已验证收益 q50" }),
    ).not.toBeInTheDocument();
  });
  test("defaults old bookmarks to verified research and sends the mode to the API", async () => {
    const { requests } = installApi();
    const router = renderRoute("/screen");

    expect(
      await screen.findByRole("table", { name: "已验证研究结果（不可执行）" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: /已验证研究/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      requests
        .find((url) => url.pathname === "/api/v1/screen")
        ?.searchParams.get("mode"),
    ).toBe("research");
    await waitFor(() =>
      expect(router.state.location.search).toContain("mode=research"),
    );
    expect(
      screen.queryByRole("table", { name: "研究筛选结果" }),
    ).not.toBeInTheDocument();
  });

  test("normalizes a formal-only legacy bookmark before requesting research rows", async () => {
    const { requests } = installApi();
    const router = renderRoute(
      "/screen?prediction_basis=forecast&return_basis=capital_gain&min_probability=0.6&max_q10=-0.08&confidence=high&effective_abstain=false&sort=executable_up_probability&direction=desc&page=4",
    );

    expect(
      await screen.findByRole("alert", { name: "URL 参数警告" }),
    ).toHaveTextContent("正式预测");
    expect(
      await screen.findByRole("table", { name: "已验证研究结果（不可执行）" }),
    ).toBeVisible();
    const request = requests.find((url) => url.pathname === "/api/v1/screen");
    expect(Object.fromEntries(request?.searchParams ?? [])).toMatchObject({
      direction: "asc",
      mode: "research",
      page: "1",
      sort: "symbol",
    });
    for (const parameter of [
      "prediction_basis",
      "return_basis",
      "min_probability",
      "max_q10",
      "confidence",
      "effective_abstain",
    ]) {
      expect(request?.searchParams.has(parameter)).toBe(false);
    }
    await waitFor(() => {
      expect(router.state.location.search).not.toContain("min_probability");
      expect(router.state.location.search).not.toContain("page=4");
    });
  });

  test("switches challenger and formal result renderers through URL-backed requests", async () => {
    const { requests } = installApi();
    renderRoute("/screen?mode=research");
    const user = userEvent.setup();

    await screen.findByRole("table", { name: "已验证研究结果（不可执行）" });
    await user.click(screen.getByRole("button", { name: /实验候选/ }));
    expect(
      await screen.findByRole("table", { name: "实验候选结果（不可执行）" }),
    ).toBeVisible();
    await waitFor(() =>
      expect(
        requests.some(
          (url) =>
            url.pathname === "/api/v1/screen" &&
            url.searchParams.get("mode") === "challenger",
        ),
      ).toBe(true),
    );

    await user.click(screen.getByRole("button", { name: /正式预测/ }));
    expect(await screen.findByRole("table", { name: "研究筛选结果" })).toBeVisible();
    expect(
      screen.queryByRole("table", { name: "实验候选结果（不可执行）" }),
    ).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        requests.some(
          (url) =>
            url.pathname === "/api/v1/screen" &&
            url.searchParams.get("mode") === "formal",
        ),
      ).toBe(true),
    );
  });

  test("drops formal-only controls and resets the page when switching to research", async () => {
    const { requests } = installApi();
    const router = renderRoute(
      "/screen?mode=formal&min_probability=0.6&confidence=high&sort=executable_up_probability&page=3",
    );
    const user = userEvent.setup();

    await screen.findByRole("table", { name: "研究筛选结果" });
    await user.click(screen.getByRole("button", { name: /已验证研究/ }));

    expect(
      await screen.findByRole("status", { name: "模式兼容性提示" }),
    ).toHaveTextContent("正式预测");
    await waitFor(() => {
      expect(router.state.location.search).toContain("mode=research");
      expect(router.state.location.search).toContain("sort=symbol");
      expect(router.state.location.search).not.toContain("min_probability");
      expect(router.state.location.search).not.toContain("confidence");
      expect(router.state.location.search).not.toContain("page=3");
    });
    await waitFor(() =>
      expect(
        requests.some(
          (url) =>
            url.pathname === "/api/v1/screen" &&
            url.searchParams.get("mode") === "research" &&
            url.searchParams.get("sort") === "symbol" &&
            !url.searchParams.has("min_probability") &&
            !url.searchParams.has("confidence"),
        ),
      ).toBe(true),
    );
  });

  test("gates non-empty abstained rows in formal mode but keeps research evidence available", async () => {
    const dataOnlyResponse = {
      ...screenResponse(1),
      data: { ...screenResponse(1).data, items: [{ ...ROW, effective_abstain: true }] },
      effective_abstain: false,
      exclusion_reason: ["no_champion_candidate"],
      resolved_snapshot: {
        ...SNAPSHOT,
        calibration_version: null,
        model_version: null,
        prediction_readiness: "data_only",
        research_readiness: "rules_only",
      },
    } satisfies ScreenResponse;
    installApi({ screen: () => dataOnlyResponse });
    const router = renderRoute("/screen?mode=formal");

    expect(await screen.findByRole("heading", { name: "排名暂不可用" })).toBeVisible();
    expect(screen.getByText(/当前快照没有可用的 Champion 正式模型/)).toBeVisible();
    expect(
      screen.queryByRole("table", { name: "研究筛选结果" }),
    ).not.toBeInTheDocument();

    await act(async () => {
      await router.navigate("/screen?mode=research");
    });
    expect(
      await screen.findByRole("table", { name: "已验证研究结果（不可执行）" }),
    ).toBeVisible();
  });

  test("sends every URL filter to the server and renders the returned page without client filtering", async () => {
    const { requests } = installApi();
    renderRoute(
      "/screen?mode=formal&horizon=20&prediction_basis=forecast&return_basis=capital_gain&regime=range&sector=technology&asset_type=stock&min_probability=0.6&max_q10=-0.08&confidence=medium&serving_status=live&sort=breakout_up_probability&direction=asc&page=2&page_size=25",
    );

    const table = await screen.findByRole(
      "table",
      { name: "研究筛选结果" },
      { timeout: 5_000 },
    );
    expect(within(table).getByText("NVDA")).toBeVisible();
    expect(within(table).getByText("52.0%")).toBeVisible();
    const request = requests.find((url) => url.pathname === "/api/v1/screen");
    expect(Object.fromEntries(request?.searchParams ?? [])).toMatchObject({
      asset_type: "stock",
      confidence: "medium",
      direction: "asc",
      horizon: "20",
      limit: "25",
      max_q10: "-0.08",
      min_probability: "0.6",
      mode: "formal",
      page: "2",
      prediction_basis: "forecast",
      regime: "range",
      return_basis: "capital_gain",
      sector: "technology",
      serving_status: "live",
      sort: "breakout_up_probability",
    });
  });

  test("uses allowlisted server sorting and restores requests, order and table state on back and forward", async () => {
    const { requests } = installApi();
    const router = renderRoute(
      "/screen?mode=formal&horizon=20&sort=breakout_up_probability&direction=asc&page=2&page_size=25",
    );
    const user = userEvent.setup();

    const table = await screen.findByRole("table", { name: "研究筛选结果" });
    await user.click(within(table).getByRole("button", { name: /可成交上涨概率/ }));

    await waitFor(() => {
      expect(router.state.location.search).toContain("sort=executable_up_probability");
    });
    expect(router.state.location.search).not.toContain("page=2");
    await waitFor(() => {
      expect(
        requests.some(
          (url) =>
            url.pathname === "/api/v1/screen" &&
            url.searchParams.get("sort") === "executable_up_probability" &&
            url.searchParams.get("page") === "1",
        ),
      ).toBe(true);
    });
    await waitFor(() => {
      const currentTable = screen.getByRole("table", { name: "研究筛选结果" });
      const rows = within(currentTable).getAllByRole("row");
      expect(rows[1]).toHaveTextContent("MSFT");
      expect(
        within(currentTable).getByRole("columnheader", {
          name: /可成交上涨概率/,
        }),
      ).toHaveAttribute("aria-sort", "descending");
    });

    await router.navigate(-1);
    await waitFor(() => {
      expect(router.state.location.search).toContain("page=2");
      expect(router.state.location.search).toContain("sort=breakout_up_probability");
      expect(
        requests.some(
          (url) =>
            url.pathname === "/api/v1/screen" &&
            url.searchParams.get("sort") === "breakout_up_probability" &&
            url.searchParams.get("page") === "2",
        ),
      ).toBe(true);
      const currentTable = screen.getByRole("table", { name: "研究筛选结果" });
      const rows = within(currentTable).getAllByRole("row");
      expect(rows[1]).toHaveTextContent("NVDA");
      expect(
        within(currentTable).getByRole("columnheader", { name: /向上突破/ }),
      ).toHaveAttribute("aria-sort", "ascending");
    });

    await router.navigate(1);
    await waitFor(() => {
      expect(router.state.location.search).toContain("sort=executable_up_probability");
      expect(router.state.location.search).not.toContain("page=2");
      const currentTable = screen.getByRole("table", { name: "研究筛选结果" });
      const rows = within(currentTable).getAllByRole("row");
      expect(rows[1]).toHaveTextContent("MSFT");
      expect(
        within(currentTable).getByRole("columnheader", {
          name: /可成交上涨概率/,
        }),
      ).toHaveAttribute("aria-sort", "descending");
    });
  });

  test("shows static mobile evidence and carries full resolved identity into research links", async () => {
    installApi();
    renderRoute("/screen?mode=formal&horizon=20", 390);

    const list = await screen.findByRole("list", { name: "移动端筛选结果" });
    const row = within(list).getByRole("listitem", { name: /NVDA/ });
    expect(row).toHaveTextContent("NVDA");
    expect(row).toHaveTextContent("趋势偏多");
    expect(row).toHaveTextContent("上涨概率52.0%");
    expect(row).toHaveTextContent("可成交 q10-11.2%");
    expect(row).toHaveTextContent("中置信");
    expect(row).toHaveTextContent("2026/07/10 16:05 美东时间");

    const url = new URL(
      within(row).getByRole("link", { name: /NVDA/ }).getAttribute("href") ?? "",
      "http://localhost",
    );
    expect(url.pathname).toBe("/research/US.NVDA");
    expect(Object.fromEntries(url.searchParams)).toMatchObject({
      as_of: SNAPSHOT.as_of,
      calibration_version: SNAPSHOT.calibration_version,
      dataset_revision: SNAPSHOT.dataset_revision,
      feature_set_version: SNAPSHOT.feature_set_version,
      horizon: "20",
      model_version: SNAPSHOT.model_version,
      revision_no: "3",
      snapshot_id: SNAPSHOT.snapshot_id,
      universe_version: SNAPSHOT.universe_version,
    });
  });

  test("uses the selected return basis for the mobile q10 evidence", async () => {
    installApi();
    renderRoute("/screen?mode=formal&horizon=20&return_basis=capital_gain", 390);

    const row = within(
      await screen.findByRole("list", { name: "移动端筛选结果" }),
    ).getByRole("listitem", { name: /NVDA/ });
    expect(row).toHaveTextContent("资本利得 q10-10.0%");
    expect(row).not.toHaveTextContent("q10 下行-11.2%");
  });

  test.each([
    ["stale", false],
    ["offline", false],
    ["unavailable", false],
    ["live", true],
  ] as const)(
    "shows an honest ranking-unavailable reason for %s with effective abstain %s",
    async (servingStatus, effectiveAbstain) => {
      installApi({
        screen: () => emptyScreenResponse(servingStatus, effectiveAbstain),
      });
      renderRoute("/screen?mode=formal");

      expect(
        await screen.findByRole("heading", { name: "排名暂不可用" }),
      ).toBeVisible();
      expect(screen.queryByText(/放宽筛选条件/)).not.toBeInTheDocument();
      expect(screen.getByText(/数据不可用于排名/)).toBeVisible();
    },
  );

  test("applies and clears the mobile filter drawer while returning focus to results", async () => {
    installApi();
    const router = renderRoute("/screen?mode=formal&page=2", 390);
    const user = userEvent.setup();

    await screen.findByRole("heading", { name: "研究筛选器" });
    await user.click(screen.getByRole("button", { name: "打开筛选条件" }));
    const drawer = screen.getByRole("dialog", { name: "筛选条件" });
    const page = document.querySelector(".screen-page");
    const firstControl = within(drawer).getByLabelText("预测周期");
    const closeButton = within(drawer).getByRole("button", { name: "关闭筛选条件" });
    const applyButton = within(drawer).getByRole("button", { name: "应用筛选" });
    expect(firstControl).toHaveFocus();
    expect(page).toHaveAttribute("inert");
    await user.tab({ shift: true });
    expect(closeButton).toHaveFocus();
    await user.tab({ shift: true });
    expect(applyButton).toHaveFocus();
    await user.tab();
    expect(closeButton).toHaveFocus();
    firstControl.focus();
    await user.selectOptions(within(drawer).getByLabelText("预测周期"), "20");
    await user.selectOptions(within(drawer).getByLabelText("最低置信"), "high");
    await user.click(within(drawer).getByRole("button", { name: "应用筛选" }));

    const resultsHeading = screen.getByRole("heading", { name: "筛选结果" });
    expect(resultsHeading).toHaveFocus();
    expect(router.state.location.search).toContain("horizon=20");
    expect(router.state.location.search).toContain("confidence=high");
    expect(router.state.location.search).not.toContain("page=2");

    await user.click(screen.getByRole("button", { name: "打开筛选条件" }));
    await user.click(
      within(screen.getByRole("dialog", { name: "筛选条件" })).getByRole("button", {
        name: "清除全部",
      }),
    );
    expect(router.state.location.search).not.toContain("confidence=");
    expect(router.state.location.search).not.toContain("horizon=");
  });

  test("closes the mobile drawer with Escape and restores focus to its trigger", async () => {
    installApi();
    renderRoute("/screen", 390);
    const user = userEvent.setup();

    await screen.findByRole("heading", { name: "研究筛选器" });
    const trigger = screen.getByRole("button", { name: "打开筛选条件" });
    await user.click(trigger);
    expect(screen.getByRole("dialog", { name: "筛选条件" })).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "筛选条件" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  test("closes the drawer and clears inert when resizing from mobile to desktop", async () => {
    installApi();
    renderRoute("/screen", 390);
    const user = userEvent.setup();

    await screen.findByRole("heading", { name: "研究筛选器" });
    await user.click(screen.getByRole("button", { name: "打开筛选条件" }));
    expect(screen.getByRole("dialog", { name: "筛选条件" })).toBeVisible();

    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1024,
    });
    fireEvent(window, new Event("resize"));

    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "筛选条件" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "打开筛选条件" }),
      ).not.toBeInTheDocument();
      expect(document.querySelector(".screen-page")).not.toHaveAttribute("inert");
      expect(screen.getByRole("heading", { name: "筛选结果" })).toHaveFocus();
    });
  });

  test("preserves an unapplied mobile draft across resolved snapshot updates", async () => {
    const pendingScreen = deferred<ScreenResponse>();
    installApi({ screen: () => pendingScreen.promise });
    const router = renderRoute("/screen", 390);
    const user = userEvent.setup();

    await screen.findByRole("heading", { name: "研究筛选器" });
    await user.click(screen.getByRole("button", { name: "打开筛选条件" }));
    const drawer = screen.getByRole("dialog", { name: "筛选条件" });
    const horizon = within(drawer).getByLabelText("预测周期");
    await user.selectOptions(horizon, "20");
    expect(horizon).toHaveValue("20");

    pendingScreen.resolve(screenResponse(1));
    await waitFor(() => {
      expect(router.state.location.search).toContain(
        `snapshot_id=${SNAPSHOT.snapshot_id}`,
      );
    });
    await waitFor(() => {
      expect(screen.getByRole("link", { name: /NVDA/ })).toHaveAttribute(
        "href",
        expect.stringContaining(`snapshot_id=${SNAPSHOT.snapshot_id}`),
      );
    });
    await waitFor(() => {
      expect(within(drawer).getByLabelText("预测周期")).toHaveValue("20");
    });
  });

  test("shows the actual exclusion reasons for an abstained empty ranking", async () => {
    installApi({
      screen: () => ({
        ...emptyScreenResponse("live", true),
        exclusion_reason: ["stale_data", "model_disagreement"],
      }),
    });
    renderRoute("/screen?mode=formal");

    const reasons = await screen.findByRole("list", { name: "实际拒绝原因" });
    expect(reasons).toHaveTextContent("数据过期");
    expect(reasons).toHaveTextContent("模型分歧过大");
  });

  test("explains that the capital-gain optional column is already active", async () => {
    installApi();
    renderRoute("/screen?mode=formal&return_basis=capital_gain");
    const user = userEvent.setup();

    await user.click(await screen.findByText("列显示"));
    expect(
      screen.queryByRole("checkbox", { name: "资本利得口径" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("资本利得 q10/q50 已作为当前收益口径显示。")).toBeVisible();
  });

  test("keeps screen results usable and reports instrument metadata degradation", async () => {
    installApi({ failInstruments: true });
    renderRoute("/screen?mode=formal&sector=technology");

    const table = await screen.findByRole("table", { name: "研究筛选结果" });
    expect(within(table).getByText("NVDA")).toBeVisible();
    expect(
      await screen.findByRole("status", { name: "筛选选项元数据暂不可用" }),
    ).toHaveTextContent("当前结果仍可使用");
    expect(screen.getByLabelText("行业")).toHaveValue("technology");
  });
});

function ScreenerLocaleSwitch() {
  const i18n = useI18n();
  return <button onClick={() => i18n.setLocale("en-US")}>English screener</button>;
}

test("switches all screener labels without resetting filters, sort, pagination, snapshot or fetching business data", async () => {
  const { requests } = installApi();
  const client = createClient();
  const router = createMemoryRouter(APP_ROUTES, {
    initialEntries: [
      "/screen?mode=research&sector=technology&sort=verified_q10&direction=asc&page=2&horizon=5",
    ],
  });
  render(
    <I18nProvider>
      <QueryClientProvider client={client}>
        <ScreenerLocaleSwitch />
        <RouterProvider router={router} />
      </QueryClientProvider>
    </I18nProvider>,
  );
  await screen.findByRole("table", { name: "已验证研究结果（不可执行）" });
  await waitFor(() => expect(router.state.location.search).toContain("snapshot_id="));
  await waitFor(() =>
    expect(
      requests.some(
        (url) =>
          url.pathname === "/api/v1/instruments" &&
          url.searchParams.get("snapshot_id") === SNAPSHOT.snapshot_id,
      ),
    ).toBe(true),
  );
  await waitFor(() => expect(client.isFetching()).toBe(0));
  const beforeUrl = router.state.location.pathname + router.state.location.search;
  const beforeRequests = requests
    .filter((url) =>
      ["/api/v1/screen", "/api/v1/instruments", "/api/v1/options/overview"].includes(
        url.pathname,
      ),
    )
    .map((url) => url.href);
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "English screener" }));
  expect(
    screen.getByRole("table", { name: "Verified research results (not executable)" }),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: /Remove Sector/ })).toBeVisible();
  expect(
    screen.getByRole("table", { name: "Verified research results (not executable)" }),
  ).not.toHaveTextContent(/趋势偏多|趋势偏空|震荡区间|突破待确认/);
  expect(screen.getByRole("combobox", { name: "Research sort" })).toHaveValue(
    "verified_q10",
  );
  expect(router.state.location.pathname + router.state.location.search).toBe(beforeUrl);
  expect(
    requests
      .filter((url) =>
        ["/api/v1/screen", "/api/v1/instruments", "/api/v1/options/overview"].includes(
          url.pathname,
        ),
      )
      .map((url) => url.href),
  ).toEqual(beforeRequests);
});
