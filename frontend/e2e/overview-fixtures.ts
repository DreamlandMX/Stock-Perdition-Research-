import type { Page } from "@playwright/test";

import type { components } from "../src/api/generated/schema";

type ResearchEvidence = components["schemas"]["ResearchModelEvidence"];
type ResearchOpportunity = components["schemas"]["ResearchOpportunityRow"];
type OverviewResponse = components["schemas"]["ResearchResponse_OverviewReadModel_"];

const AS_OF = "2026-07-10T20:00:00Z";

function evidence(
  symbol: string,
  horizon: 1 | 5 | 20,
  referencePrice: number,
): ResearchEvidence {
  const projectedGain = horizon === 20 ? 0.024 : horizon === 5 ? 0.011 : 0.003;
  const q50 = referencePrice * (1 + projectedGain);

  return {
    as_of: AS_OF,
    horizon,
    price_projection: {
      currency: "USD",
      endpoint_price_band: {
        q10: q50 * 0.96,
        q25: q50 * 0.98,
        q50,
        q75: q50 * 1.02,
        q90: q50 * 1.04,
      },
      endpoint_price_unavailable_reason: null,
      reference_price: referencePrice,
      reference_price_at: AS_OF,
      stage_high_price_q50: q50 * 1.025,
      stage_high_price_unavailable_reason: null,
      stage_low_price_q50: q50 * 0.975,
      stage_low_price_unavailable_reason: null,
    },
    sample_abstain_reasons: [],
    scope: "verified",
    symbol,
    tasks: [
      {
        baseline_wins: 4,
        calibration_version: "calibration-v1",
        evaluation_run_id: `e2e-${symbol}-${horizon}`,
        fold_count: 4,
        gate_passed: true,
        horizon,
        model_version: `e2e-breakout-${horizon}`,
        scope: "verified",
        task: "breakout",
      },
    ],
    values: {
      breakout_down_probability: 0.22,
      breakout_up_probability: 0.63,
      capital_gain_q10: -0.04,
      capital_gain_q50: projectedGain,
      mae_q50: -0.028,
      mfe_q50: 0.034,
    },
  };
}

function opportunity(
  symbol: string,
  name: string,
  referencePrice: number,
  rank: number,
): ResearchOpportunity {
  const evidence5 = evidence(symbol, 5, referencePrice);

  return {
    as_of: AS_OF,
    asset_type: "etf",
    challenger_preview: null,
    challenger_preview_by_horizon: [],
    horizon: 5,
    name,
    rank_basis: [`第 ${rank} 名：5 日研究证据排序`],
    regime: rank === 2 ? "uptrend" : "range",
    rule_evidence: {
      atr_pct_20: 0.02 + rank * 0.001,
      breakout_down_rule: false,
      breakout_up_rule: rank === 2,
      channel_position_20: 0.54 + rank * 0.05,
      channel_width_20: 0.045 + rank * 0.003,
      intraday_available: true,
      last_close: referencePrice,
      return_20: 0.02 + rank * 0.003,
      return_5: 0.004 + rank * 0.001,
      session_return: 0.001 + rank * 0.0005,
      volume_z_20: 0.1 + rank * 0.12,
    },
    sector: "broad_market",
    symbol,
    verified_research: evidence5,
    verified_research_by_horizon: [
      evidence(symbol, 1, referencePrice),
      evidence5,
      evidence(symbol, 20, referencePrice),
    ],
  };
}

const RESEARCH_OPPORTUNITIES = [
  opportunity("US.SPY", "标普500交易所交易基金（SPDR大盘核心配置）", 738.93, 1),
  opportunity("US.QQQ", "Invesco QQQ Trust", 682.27, 2),
  opportunity("US.IWM", "iShares Russell 2000 ETF", 289.84, 3),
  opportunity("US.DIA", "SPDR Dow Jones Industrial Average ETF", 524.32, 4),
  opportunity("US.TGT", "Target Corporation", 155.51, 5),
  opportunity("US.BLK", "BlackRock", 1182.84, 6),
  opportunity("US.FDX", "FedEx", 339.35, 7),
  opportunity("US.NFLX", "Netflix", 78.24, 8),
  opportunity("US.AAPL", "Apple", 231.59, 9),
];

const E2E_SECTORS = ["科技", "消费", "医疗保健", "工业", "金融"];
const E2E_MARKET_POINTS = Array.from({ length: 100 }, (_, index) => {
  const opportunity = RESEARCH_OPPORTUNITIES[index];
  const symbol = opportunity?.symbol ?? `US.M${String(index).padStart(2, "0")}`;
  const sessionReturn = (index - 44) / 900;
  return {
    asset_type: opportunity?.asset_type ?? "stock",
    atr_pct_20: 0.012 + index / 5000,
    breakout_down_rule: index === 95,
    breakout_up_rule: index < 6,
    channel_position_20: index / 99,
    channel_width_20: 0.08,
    intraday_available: true,
    last_close: opportunity?.rule_evidence.last_close ?? 100 + index,
    name: opportunity?.name ?? `Market symbol ${index}`,
    previous_regime: index < 11 ? "range" : index < 55 ? "uptrend" : "downtrend",
    regime:
      index < 55
        ? "uptrend"
        : index < 75
          ? "range"
          : index < 94
            ? "downtrend"
            : "transition",
    regime_changed: index < 11,
    return_20: sessionReturn * 3.4,
    return_5: sessionReturn * 1.8,
    sector: E2E_SECTORS[index % E2E_SECTORS.length] ?? "科技",
    session_return: sessionReturn,
    symbol,
    volume_z_20: index < 4 ? 2.2 + index / 10 : 0.2,
  } satisfies components["schemas"]["OverviewMarketPoint"];
});

const E2E_SECTOR_SCORECARDS = E2E_SECTORS.map((sector, index) => ({
  breakout_down_count: index === 0 ? 1 : 0,
  breakout_up_count: index === 0 ? 5 : 0,
  laggard_symbol:
    E2E_MARKET_POINTS.filter((point) => point.sector === sector).at(0)?.symbol ?? null,
  leader_symbol:
    E2E_MARKET_POINTS.filter((point) => point.sector === sector).at(-1)?.symbol ?? null,
  median_atr_pct_20: 0.02,
  median_return_20: 0.08 - index * 0.03,
  median_return_5: 0.03 - index * 0.012,
  median_session_return: 0.012 - index * 0.005,
  previous_sample_count: 20,
  previous_uptrend_fraction: 0.45,
  sample_count: 20,
  sector,
  uptrend_change: 0.15 - index * 0.06,
  uptrend_fraction: 0.6 - index * 0.06,
  volume_anomaly_count: index === 0 ? 4 : 0,
})) satisfies components["schemas"]["SectorScorecard"][];

export async function installOverviewOpportunities(page: Page, count = 8) {
  await page.route("**/api/v1/snapshot-context**", async (route) => {
    const response = await route.fetch();
    const payload =
      (await response.json()) as components["schemas"]["ResearchResponse_SnapshotContextReadModel_"];
    payload.serving_status = "live";
    payload.data.symbol_count = E2E_MARKET_POINTS.length;
    await route.fulfill({ response, json: payload });
  });
  await page.route("**/api/v1/overview**", async (route) => {
    const response = await route.fetch();
    const payload = (await response.json()) as OverviewResponse;
    payload.serving_status = "live";
    payload.data.market_breadth = 0.71;
    payload.data.internal_evidence = {
      advancers: 66,
      breakout_down: 0,
      breakout_up: 6,
      comparison_snapshot: {
        as_of: "2026-07-09T20:00:00Z",
        generated_at: "2026-07-09T20:06:00Z",
        revision_no: 2,
        snapshot_id: "e2e-overview-previous-r2",
      },
      decliners: 33,
      history: [
        {
          as_of: "2026-07-06T20:00:00Z",
          market_breadth: 0.61,
          median_session_return: -0.001,
          revision_no: 1,
          snapshot_id: "e2e-overview-1",
          uptrend_fraction: 0.47,
          volume_anomaly_count: 2,
        },
        {
          as_of: "2026-07-07T20:00:00Z",
          market_breadth: 0.64,
          median_session_return: 0.0008,
          revision_no: 1,
          snapshot_id: "e2e-overview-2",
          uptrend_fraction: 0.49,
          volume_anomaly_count: 3,
        },
        {
          as_of: "2026-07-08T20:00:00Z",
          market_breadth: 0.66,
          median_session_return: 0.0019,
          revision_no: 1,
          snapshot_id: "e2e-overview-3",
          uptrend_fraction: 0.51,
          volume_anomaly_count: 3,
        },
        {
          as_of: "2026-07-09T20:00:00Z",
          market_breadth: 0.67,
          median_session_return: 0.0028,
          revision_no: 2,
          snapshot_id: "e2e-overview-previous-r2",
          uptrend_fraction: 0.49,
          volume_anomaly_count: 3,
        },
        {
          as_of: AS_OF,
          market_breadth: 0.71,
          median_session_return: 0.0052,
          revision_no: payload.resolved_snapshot.revision_no,
          snapshot_id: payload.resolved_snapshot.snapshot_id,
          uptrend_fraction: 0.55,
          volume_anomaly_count: 4,
        },
      ],
      median_session_return: 0.0052,
      market_points: E2E_MARKET_POINTS,
      regime_changes: 11,
      regime_transition_cells: [
        { count: 44, current_regime: "uptrend", previous_regime: "uptrend" },
        { count: 11, current_regime: "uptrend", previous_regime: "range" },
        { count: 20, current_regime: "range", previous_regime: "downtrend" },
        { count: 19, current_regime: "downtrend", previous_regime: "downtrend" },
        { count: 6, current_regime: "transition", previous_regime: "downtrend" },
      ],
      return_dispersion: 0.0078,
      sector_scorecards: E2E_SECTOR_SCORECARDS,
      sector_uptrend_changes: [
        {
          change: 0.263,
          previous_sample_count: 19,
          previous_uptrend_fraction: 0.421,
          sample_count: 19,
          sector: "科技",
          uptrend_fraction: 0.684,
        },
        {
          change: 0.231,
          previous_sample_count: 13,
          previous_uptrend_fraction: 0.462,
          sample_count: 13,
          sector: "医疗保健",
          uptrend_fraction: 0.693,
        },
        {
          change: 0.1,
          previous_sample_count: 10,
          previous_uptrend_fraction: 0.6,
          sample_count: 10,
          sector: "工业",
          uptrend_fraction: 0.7,
        },
        {
          change: -0.05,
          previous_sample_count: 20,
          previous_uptrend_fraction: 0.6,
          sample_count: 20,
          sector: "ETF",
          uptrend_fraction: 0.55,
        },
        {
          change: -0.133,
          previous_sample_count: 15,
          previous_uptrend_fraction: 0.4,
          sample_count: 15,
          sector: "消费",
          uptrend_fraction: 0.267,
        },
      ],
      to_downtrend: 3,
      to_uptrend: 8,
      unchanged: 1,
      verified_candidate_churn: { changes: [] },
      challenger_candidate_churn: { changes: [] },
      evidence_coverage: {
        atr_count: 100,
        channel_count: 100,
        comparable_symbols: 100,
        intraday_available_count: 100,
        return_20_count: 100,
        return_5_count: 100,
        rule_evidence_count: 100,
        session_return_count: 100,
        total_symbols: 100,
        volume_z_count: 100,
      },
      current_anomaly_snapshot: {
        breakout_count: 7,
        lower_channel_count: 1,
        regime_change_count: 11,
        snapshot_id: payload.resolved_snapshot.snapshot_id,
        total_symbols: 100,
        upper_channel_count: 1,
        volume_anomalies: 4,
      },
      previous_anomaly_snapshot: {
        breakout_count: 5,
        lower_channel_count: 2,
        regime_change_count: 9,
        snapshot_id: "e2e-overview-previous-r2",
        total_symbols: 100,
        upper_channel_count: 3,
        volume_anomalies: 3,
      },
      volume_anomalies: 4,
    };
    payload.data.research_opportunities = RESEARCH_OPPORTUNITIES.slice(0, count);
    payload.data.proxies = payload.data.proxies.map((proxy) => {
      const source = RESEARCH_OPPORTUNITIES.find(
        (candidate) => candidate.symbol === proxy.symbol,
      );
      return source == null
        ? proxy
        : {
            ...proxy,
            verified_research_by_horizon: source.verified_research_by_horizon,
          };
    });
    await route.fulfill({ response, json: payload });
  });
}
