import type { components } from "../../api/generated/schema";
import type { ScreenUrlState } from "../../app/url-state";
export const OPTION_COLUMNS = [
  ["atm_iv_30d", "screen.optionScreenState.atmIv30Annualized"],
  ["iv_minus_realized_20sessions", "screen.optionScreenState.ivRv20PercentagePoints"],
  ["put_call_skew_25delta", "screen.optionScreenState.25SkewPercentagePoints"],
  ["put_call_oi", "screen.optionScreenState.putCallOi"],
  ["implied_5d", "screen.optionScreenState.5DayImpliedInterval80"],
  ["width_ratio", "screen.optionScreenState.widthRatioModelImplied"],
] as const;
export type OptionSortKey = (typeof OPTION_COLUMNS)[number][0];
export interface ScreenOptionState {
  onlyData: boolean;
  widthBelow: number | undefined;
  widthAbove: number | undefined;
  ivRvMinimum: number | undefined;
  sort: OptionSortKey | undefined;
  direction: "asc" | "desc";
}
function numberParam(params: URLSearchParams, key: string) {
  const value = params.get(key);
  return value !== null && value.trim() !== "" && Number.isFinite(Number(value))
    ? Number(value)
    : undefined;
}
export function decodeScreenOptions(params: URLSearchParams): ScreenOptionState {
  const sort = OPTION_COLUMNS.find(([key]) => key === params.get("options_sort"))?.[0];
  return {
    onlyData: params.get("options_only") === "1",
    widthBelow: numberParam(params, "options_width_below"),
    widthAbove: numberParam(params, "options_width_above"),
    ivRvMinimum: numberParam(params, "options_iv_rv_min"),
    sort,
    direction: params.get("options_direction") === "asc" ? "asc" : "desc",
  };
}
export function encodeScreenOptions(params: URLSearchParams, state: ScreenOptionState) {
  if (state.onlyData) params.set("options_only", "1");
  for (const [key, value] of [
    ["options_width_below", state.widthBelow],
    ["options_width_above", state.widthAbove],
    ["options_iv_rv_min", state.ivRvMinimum],
  ] as const) {
    if (value !== undefined) params.set(key, String(value));
  }
  if (state.sort) {
    params.set("options_sort", state.sort);
    params.set("options_direction", state.direction);
  }
  return params;
}
export function requiresAllScreenRows(state: ScreenOptionState) {
  return (
    state.onlyData ||
    state.widthBelow !== undefined ||
    state.widthAbove !== undefined ||
    state.ivRvMinimum !== undefined ||
    state.sort !== undefined
  );
}
export type OptionOverviewItem =
  components["schemas"]["OptionOverviewResponse"]["items"][number];
export function optionScreenValue(
  item: OptionOverviewItem | undefined,
  key: OptionSortKey,
) {
  if (!item || item.maturity === "none") return null;
  if (key === "width_ratio")
    return (
      item.model_horizons.find((entry) => entry.horizon_days === 5)?.width_ratio ?? null
    );
  if (key === "implied_5d")
    return (
      item.implied_moves.find((entry) => entry.horizon_days === 5)?.upper_pct ?? null
    );
  return item.analytics?.values[key] ?? null;
}
export function optionScreenPage(
  page: components["schemas"]["ScreenPage"],
  items: readonly OptionOverviewItem[],
  options: ScreenOptionState,
  state: ScreenUrlState,
): components["schemas"]["ScreenPage"] {
  const lookup = new Map(items.map((item) => [item.symbol, item]));
  const rows = page.items.filter((row) => {
    const item = lookup.get(row.symbol);
    if (options.onlyData && (!item || item.maturity === "none")) return false;
    const ratio = optionScreenValue(item, "width_ratio");
    if (
      (options.widthBelow !== undefined || options.widthAbove !== undefined) &&
      (ratio === null ||
        !(
          (options.widthBelow !== undefined && ratio < options.widthBelow) ||
          (options.widthAbove !== undefined && ratio > options.widthAbove)
        ))
    )
      return false;
    const ivRv = optionScreenValue(item, "iv_minus_realized_20sessions");
    if (
      options.ivRvMinimum !== undefined &&
      (ivRv === null || ivRv * 100 <= options.ivRvMinimum)
    )
      return false;
    return true;
  });
  if (options.sort) {
    const key = options.sort;
    rows.sort((left, right) => {
      const a = optionScreenValue(lookup.get(left.symbol), key);
      const b = optionScreenValue(lookup.get(right.symbol), key);
      if (a === null) return b === null ? left.symbol.localeCompare(right.symbol) : 1;
      if (b === null) return -1;
      return (
        (options.direction === "asc" ? a - b : b - a) ||
        left.symbol.localeCompare(right.symbol)
      );
    });
  }
  const number = Math.min(
    state.page.number,
    Math.max(1, Math.ceil(rows.length / state.page.size)),
  );
  return {
    ...page,
    items: rows.slice((number - 1) * state.page.size, number * state.page.size),
    total: rows.length,
    page: number,
    page_size: state.page.size,
  };
}
