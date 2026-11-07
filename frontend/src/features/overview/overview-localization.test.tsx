import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { components } from "../../api/generated/schema";
import { I18nProvider, useI18n } from "../../i18n/context";
import { OverviewHeaderBand } from "./OverviewHeaderBand";
import { CoverageStatusPanel } from "./CoverageStatusPanel";
import {
  buildOverviewInsightTitle,
  rankingBasisPresentation,
} from "./overview-insights";

afterEach(cleanup);

test("localizes abstention reasons and keeps manual coverage unverified", async () => {
  const data = {
    abstain_count: 1,
    abstain_reasons: [{ reason: "out_of_distribution", count: 1 }],
    candidate_summary: null,
  } as components["schemas"]["OverviewReadModel"];
  const snapshot = {
    activation_basis: "manual_override",
    prediction_readiness: "forecast_ready",
  } as components["schemas"]["ResolvedSnapshot"];
  render(
    <MemoryRouter>
      <I18nProvider>
        <ChangeLanguage />
        <CoverageStatusPanel data={data} snapshot={snapshot} healthHref="/health" />
      </I18nProvider>
    </MemoryRouter>,
  );
  await userEvent.setup().click(screen.getByRole("button", { name: "English" }));
  const coverage = screen.getByRole("region", {
    name: "Data and model coverage status",
  });
  expect(coverage).toHaveTextContent("Manually activated, unverified");
  expect(coverage).toHaveTextContent("Out-of-distribution sample");
  expect(coverage).not.toHaveTextContent("The complete Champion model is ready");
  expect(coverage).not.toHaveTextContent("Unknown (out_of_distribution)");
  expect(coverage).not.toHaveTextContent("1 instruments");
});

function ChangeLanguage() {
  const i18n = useI18n();
  return <button onClick={() => i18n.setLocale("en-US")}>English</button>;
}

test("strictly adapts known saved ranking explanations and retains unknown originals", () => {
  expect(rankingBasisPresentation("规则型向上突破优先", "en-US")).toEqual({
    label: "Rule-based upward breakouts first",
    original: null,
  });
  expect(rankingBasisPresentation("趋势和20日相对强弱排序", "en-US").label).toBe(
    "Ranked by trend and 20-day relative strength",
  );
  expect(rankingBasisPresentation("future unknown explanation", "en-US")).toEqual({
    label:
      "An explanation for this ranking basis is unavailable. See original details.",
    original: "future unknown explanation",
  });
});

test("keeps manual scenario counts and renders complete English briefing sentences", async () => {
  const data = {
    market_breadth: 0.55,
    proxies: [],
    regime_distribution: [],
    abstain_count: 0,
    research_opportunities: [],
    challenger_opportunities: [],
    top_opportunities: Array(10).fill({}),
    candidate_summary: null,
    internal_evidence: {
      history: [{ market_breadth: 0.5 }, { market_breadth: 0.55 }],
      sector_uptrend_changes: [{ sector: "etf", change: -0.1 }],
      sector_scorecards: [{ sector: "etf", median_session_return: 0.01 }],
      breakout_up: 2,
      breakout_down: 3,
      volume_anomalies: 4,
      regime_changes: 5,
    },
  } as unknown as components["schemas"]["OverviewReadModel"];
  const snapshot = {
    as_of: "2026-11-03T21:00:00Z",
    activation_basis: "manual_override",
    execution_readiness: "blocked",
    research_readiness: "manual_unverified",
    prediction_readiness: "forecast_ready",
  } as components["schemas"]["ResolvedSnapshot"];
  render(
    <MemoryRouter>
      <I18nProvider>
        <ChangeLanguage />
        <OverviewHeaderBand
          data={data}
          snapshot={snapshot}
          mode="research"
          servingStatus="live"
          gateHref="/backtests"
          optionsHref="/options"
          isHistorical={false}
          proxyHref={() => "/research/US.SPY"}
        />
      </I18nProvider>
    </MemoryRouter>,
  );
  await userEvent.setup().click(screen.getByRole("button", { name: "English" }));
  expect(buildOverviewInsightTitle(data, "en-US")).toBe(
    "Market participation improving, but the ETF sector is weakening against the trend",
  );
  const summary = screen.getByRole("region", { name: "Market summary" });
  expect(summary).toHaveTextContent("against the trend. No broad-market proxy prices");
  expect(summary).toHaveTextContent(
    "ETF +1.0%; 20-day positive-return breadth 55.0%; change from previous 5.0pp.",
  );
  expect(summary).toHaveTextContent(
    "2 upward breakouts, 3 downward breakouts, 4 volume anomalies; 5 regime changes.",
  );
  expect(summary).toHaveTextContent("Manually activated scenarios (not executable) 10");
  expect(summary).toHaveTextContent("Execution blocked");
  expect(summary).not.toHaveTextContent("Formal executable opportunities");
});
