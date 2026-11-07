import type { paths } from "../../api/generated/schema";
export type OptionQuery = NonNullable<
  paths["/api/v1/instruments/{symbol}/options/heatmap"]["get"]["parameters"]["query"]
>;
export const OPTION_PARAMETER_NAMES = [
  "option_metric",
  "option_view",
  "option_map_side",
  "option_rank_window",
  "option_side",
  "option_snapshot",
  "option_dte_min",
  "option_dte_max",
  "option_strike_min",
  "option_strike_max",
  "option_scale",
  "option_tab",
  "pulse_mode",
  "option_cone",
  "model_cone",
] as const;
export function optionQuery(params: URLSearchParams): OptionQuery {
  const metric = params.get("option_metric");
  const number = (name: string, fallback: number, min: number, max: number) => {
    const raw = params.get(name);
    const value = raw == null || raw.trim() === "" ? NaN : Number(raw);
    return Number.isFinite(value) && value >= min && value <= max ? value : fallback;
  };
  return {
    metric:
      metric == null || metric === "premium"
        ? "premium"
        : metric === "volume" ||
            metric === "iv" ||
            metric === "gamma" ||
            metric === "spread"
          ? metric
          : "oi",
    side:
      params.get("option_side") === "net"
        ? "net"
        : params.get("option_side") === "put"
          ? "put"
          : "call",
    ...(params.get("as_of") ? { as_of: params.get("as_of") } : {}),
    ...(params.get("option_snapshot")
      ? { snapshot_id: params.get("option_snapshot") }
      : {}),
    dte_min: number("option_dte_min", 1, 1, 90),
    dte_max: number("option_dte_max", 90, 1, 90),
    strike_min_ratio: number("option_strike_min", 0.7, 0.7, 1.3),
    strike_max_ratio: number("option_strike_max", 1.3, 0.7, 1.3),
  };
}
