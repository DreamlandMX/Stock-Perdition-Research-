import { describe, expect, test } from "vitest";

import type { components } from "../../api/generated/schema";
import {
  anomalyScore,
  buildHistogramBins,
  buildMarketResearchHref,
  marketReturnColor,
  normalizeMarketMapState,
} from "./overview-terminal";

type MarketPoint = components["schemas"]["OverviewMarketPoint"];

const POINT = {
  asset_type: "stock",
  atr_pct_20: 0.04,
  breakout_down_rule: false,
  breakout_up_rule: true,
  channel_position_20: 1.08,
  channel_width_20: 0.12,
  intraday_available: true,
  last_close: 250,
  name: "Example",
  previous_regime: "range",
  regime: "uptrend",
  regime_changed: true,
  return_20: 0.24,
  return_5: 0.12,
  sector: "technology",
  session_return: 0.06,
  symbol: "US.TEST",
  volume_z_20: 2.4,
} satisfies MarketPoint;

describe("overview terminal rules", () => {
  test("normalizes map URL state without inventing sectors", () => {
    const params = new URLSearchParams("map_return=20&map_sector=technology");
    expect(normalizeMarketMapState(params, ["technology", "consumer"])).toEqual({
      horizon: 20,
      sector: "technology",
    });
    expect(
      normalizeMarketMapState(new URLSearchParams("map_return=60&map_sector=unknown"), [
        "technology",
      ]),
    ).toEqual({ horizon: 1, sector: "all" });
  });

  test("uses fixed symmetric map domains and clips only the color", () => {
    expect(marketReturnColor(POINT, 1)).toEqual(
      expect.objectContaining({ cap: 0.03, clipped: 0.03, value: 0.06 }),
    );
    expect(marketReturnColor(POINT, 5)).toEqual(
      expect.objectContaining({ cap: 0.06, clipped: 0.06, value: 0.12 }),
    );
    expect(marketReturnColor(POINT, 20)).toEqual(
      expect.objectContaining({ cap: 0.12, clipped: 0.12, value: 0.24 }),
    );
  });

  test("scores the approved anomaly factors and uses ATR top-decile cutoff", () => {
    expect(anomalyScore(POINT, 0.035)).toBe(7);
    expect(anomalyScore({ ...POINT, atr_pct_20: 0.02 }, 0.035)).toBe(6);
  });

  test("builds ten fixed-width histogram bins from the selected map domain", () => {
    const bins = buildHistogramBins(
      [POINT, { ...POINT, symbol: "US.LOW", session_return: -0.07 }],
      1,
    );
    expect(bins).toHaveLength(10);
    expect(bins.reduce((sum, bin) => sum + bin.count, 0)).toBe(2);
    expect(bins[0]?.count).toBe(1);
    expect(bins[9]?.count).toBe(1);
  });

  test("preserves snapshot and version evidence in every market research link", () => {
    const href = buildMarketResearchHref("US.TEST", {
      asOf: "2026-08-13T20:00:00Z",
      calibrationVersion: "cal-v1",
      datasetRevision: "bars-r42",
      featureSetVersion: "features-v7",
      horizon: 5,
      modelVersion: "model-v1",
      snapshotId: "snapshot-r2",
      universeVersion: "us-100-v1",
    });
    expect(href).toContain("symbol=US.TEST");
    expect(href).toContain("snapshot_id=snapshot-r2");
    expect(href).toContain("dataset_revision=bars-r42");
    expect(href).toContain("feature_set_version=features-v7");
    expect(href).toContain("universe_version=us-100-v1");
    expect(href).toContain("horizon=5");
  });
});
