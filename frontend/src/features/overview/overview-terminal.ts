import { translate, type Locale } from "../../i18n/core";
import type { components } from "../../api/generated/schema";

export type MarketReturnHorizon = 1 | 5 | 20;
export type MarketPoint = components["schemas"]["OverviewMarketPoint"];

export interface MarketMapState {
  horizon: MarketReturnHorizon;
  sector: string;
}

export interface MarketLinkState {
  optionMode?: "latest" | "daily";
  asOf: string | undefined;
  calibrationVersion: string | undefined;
  datasetRevision: string | undefined;
  featureSetVersion: string | undefined;
  horizon: 1 | 5 | 20;
  modelVersion: string | undefined;
  snapshotId: string | undefined;
  universeVersion: string | undefined;
}

export interface HistogramBin {
  count: number;
  lower: number;
  upper: number;
}

export const MARKET_RETURN_CAPS: Readonly<Record<MarketReturnHorizon, number>> = {
  1: 0.03,
  5: 0.06,
  20: 0.12,
};

export function normalizeMarketMapState(
  params: URLSearchParams,
  sectors: readonly string[],
): MarketMapState {
  const requestedHorizon = Number(params.get("map_return") ?? "1");
  const horizon: MarketReturnHorizon =
    requestedHorizon === 5 || requestedHorizon === 20 ? requestedHorizon : 1;
  const requestedSector = params.get("map_sector") ?? "all";
  return {
    horizon,
    sector:
      requestedSector === "all" || sectors.includes(requestedSector)
        ? requestedSector
        : "all",
  };
}

export function marketReturnValue(
  point: MarketPoint,
  horizon: MarketReturnHorizon,
): number | null {
  if (horizon === 1) {
    return point.session_return ?? null;
  }
  if (horizon === 5) {
    return point.return_5 ?? null;
  }
  return point.return_20 ?? null;
}

export function returnScaleColor(value: number | null, cap: number) {
  if (value == null || !Number.isFinite(value)) return "#202127";
  const magnitude = Math.abs(value);
  if (magnitude < cap / 12) return "#2a2b31";
  const index = magnitude < cap / 6 ? 0 : magnitude < cap / 2 ? 1 : 2;
  return (
    value > 0 ? ["#1f3329", "#214733", "#24704a"] : ["#3d272c", "#5a2a33", "#8a2f3d"]
  )[index]!;
}

export function marketReturnColor(point: MarketPoint, horizon: MarketReturnHorizon) {
  const value = marketReturnValue(point, horizon);
  const cap = MARKET_RETURN_CAPS[horizon];
  return {
    background: returnScaleColor(value, cap),
    cap,
    clipped: value === null ? null : Math.max(-cap, Math.min(cap, value)),
    value,
  };
}

export function anomalyScore(point: MarketPoint, atrTopDecileCutoff: number | null) {
  const breakout = point.breakout_up_rule || point.breakout_down_rule ? 2 : 0;
  const volumeZ = point.volume_z_20 ?? null;
  const volume = volumeZ !== null && Math.abs(volumeZ) >= 2 ? 2 : 0;
  const transition = point.regime_changed === true ? 1 : 0;
  const channelPosition = point.channel_position_20 ?? null;
  const channel =
    channelPosition !== null && (channelPosition >= 1 || channelPosition <= 0) ? 1 : 0;
  const atr = point.atr_pct_20 ?? null;
  const highVolatility =
    atrTopDecileCutoff !== null && atr !== null && atr >= atrTopDecileCutoff ? 1 : 0;
  return breakout + volume + transition + channel + highVolatility;
}

export function atrTopDecile(points: readonly MarketPoint[]): number | null {
  const values = points
    .map((point) => point.atr_pct_20 ?? null)
    .filter((value): value is number => value !== null)
    .sort((left, right) => left - right);
  if (values.length === 0) {
    return null;
  }
  return values[Math.max(0, Math.ceil(values.length * 0.9) - 1)] ?? null;
}

export function buildHistogramBins(
  points: readonly MarketPoint[],
  horizon: MarketReturnHorizon,
): HistogramBin[] {
  const cap = MARKET_RETURN_CAPS[horizon];
  const width = (cap * 2) / 10;
  const bins = Array.from({ length: 10 }, (_, index) => ({
    count: 0,
    lower: -cap + index * width,
    upper: -cap + (index + 1) * width,
  }));
  for (const point of points) {
    const value = marketReturnValue(point, horizon);
    if (value === null) {
      continue;
    }
    const clipped = Math.max(-cap, Math.min(cap, value));
    const index = Math.min(9, Math.max(0, Math.floor((clipped + cap) / width)));
    const bin = bins[index];
    if (bin !== undefined) {
      bin.count += 1;
    }
  }
  return bins;
}

export function buildMarketResearchHref(symbol: string, state: MarketLinkState) {
  const params = new URLSearchParams();
  params.set("symbol", symbol);
  params.set("horizon", String(state.horizon));
  const optional: readonly [string, string | undefined][] = [
    ["as_of", state.asOf],
    ["snapshot_id", state.snapshotId],
    ["dataset_revision", state.datasetRevision],
    ["feature_set_version", state.featureSetVersion],
    ["universe_version", state.universeVersion],
    ["model_version", state.modelVersion],
    ["calibration_version", state.calibrationVersion],
    ["pulse_mode", state.optionMode],
  ];
  for (const [key, value] of optional) {
    if (value !== undefined) {
      params.set(key, value);
    }
  }
  return `/research/${encodeURIComponent(symbol)}?${params.toString()}`;
}

export function formatReturn(value: number | null, locale: Locale = "zh-CN") {
  if (value === null) {
    return translate(locale, "overview.SectorHealthTerminal.missing");
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(1)}%`;
}

export function symbolLabel(symbol: string) {
  return symbol.replace(/^US\./, "");
}
