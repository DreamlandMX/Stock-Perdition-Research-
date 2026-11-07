import { translate, type Locale } from "../../i18n/core";
import type { OptionBoardItem } from "./useOptionsBoard";
import type { OptionOverviewItem } from "./useOptionsOverview";
import { optionReasonLabel } from "./option-labels";

export type OptionLook = {
  k: "def" | "vol" | "bull" | "neu" | "missing";
  t: string;
  why: string;
};
export const LOOK_RANK = { def: 0, vol: 1, neu: 2, bull: 3, missing: 4 };
export const shortSymbol = (symbol: string) => symbol.replace(/^US\./, "");
export const ratio = (a: number | null | undefined, b: number | null | undefined) =>
  a != null && b != null && Number.isFinite(a) && Number.isFinite(b) && b > 0
    ? a / b
    : null;
export const number = (
  v: number | null | undefined,
  digits = 2,
  locale: Locale = "zh-CN",
) =>
  v == null || !Number.isFinite(v)
    ? "—"
    : v.toLocaleString(locale, {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
export const percent = (
  v: number | null | undefined,
  digits = 1,
  locale: Locale = "zh-CN",
) =>
  v == null || !Number.isFinite(v)
    ? "—"
    : new Intl.NumberFormat(locale, {
        style: "percent",
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }).format(v);
export const signedPercent = (
  v: number | null | undefined,
  digits = 1,
  locale: Locale = "zh-CN",
) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${percent(v, digits, locale)}`);
export const points = (v: number | null | undefined, locale: Locale = "zh-CN") =>
  v == null
    ? "—"
    : translate(locale, "options.optionsBoardRows.points", {
        value: v >= 0 ? "+" : "",
        number: number(v * 100, 1, locale),
      });
export const money = (v: number | null | undefined, locale: Locale = "zh-CN") => {
  if (v == null || !Number.isFinite(v)) return "—";
  const [scale, suffix] =
    Math.abs(v) >= 1e9
      ? [1e9, "B"]
      : Math.abs(v) >= 1e6
        ? [1e6, "M"]
        : Math.abs(v) >= 1e3
          ? [1e3, "K"]
          : [1, ""];
  return `$${number(v / scale, suffix === "B" ? 2 : 1, locale)}${suffix}`;
};
export const termLabel = (
  term: OptionOverviewItem["term_structure"],
  locale: Locale = "zh-CN",
) =>
  term === "inverted"
    ? translate(locale, "options.optionsBoardRows.inverted")
    : term === "upward"
      ? translate(locale, "options.optionsBoardRows.upward")
      : term === "flat"
        ? translate(locale, "options.optionsBoardRows.flat")
        : translate(locale, "options.optionsBoardRows.insufficientTermData");
export function outlookLabel(
  r: {
    tail: number | null;
    skew: number | null;
    pu10: number | null;
    pd10: number | null;
    pu5: number | null;
    pd5: number | null;
    otmCallOfOtm: number | null;
  },
  locale: Locale = "zh-CN",
): OptionLook {
  if (r.tail != null && r.tail >= 1.3 && r.skew != null && r.skew >= 0.04)
    return {
      k: "def",
      t: translate(locale, "options.optionsBoardRows.downsideProtection"),
      why: translate(
        locale,
        "options.optionsBoardRows.probabilityDown10IsMuchHigherThanUp10",
      ),
    };
  if (r.pu10 != null && r.pd10 != null && r.pu10 >= 0.17 && r.pd10 >= 0.17)
    return {
      k: "vol",
      t: translate(locale, "options.optionsBoardRows.largeMovePositioning"),
      why: translate(
        locale,
        "options.optionsBoardRows.probabilitiesOfUpAndDownMoreThan10Within",
      ),
    };
  if (
    r.otmCallOfOtm != null &&
    r.otmCallOfOtm >= 0.6 &&
    r.pu5 != null &&
    r.pd5 != null &&
    r.pu5 >= r.pd5
  )
    return {
      k: "bull",
      t: translate(locale, "options.optionsBoardRows.bullish"),
      why: translate(
        locale,
        "options.optionsBoardRows.over60OfOTMTurnoverIsInCallsAnd",
      ),
    };
  if ([r.skew, r.pu10, r.pd10, r.pu5, r.pd5, r.otmCallOfOtm].some((v) => v == null))
    return {
      k: "missing",
      t: translate(locale, "options.optionsBoardRows.insufficientMetrics"),
      why: translate(
        locale,
        "options.optionsBoardRows.someClassificationInputsAreMissingNotClassified",
      ),
    };
  return {
    k: "neu",
    t: translate(locale, "options.optionsBoardRows.neutral"),
    why: translate(
      locale,
      "options.optionsBoardRows.probabilitiesAreNearlySymmetricWithNoClearPremiumTilt",
    ),
  };
}

export function buildOptionsBoardRows(
  items: readonly OptionBoardItem[],
  overview: readonly OptionOverviewItem[],
  locale: Locale = "zh-CN",
) {
  return items.map((board) => {
    const candidate = overview.find((item) => item.symbol === board.symbol);
    const item =
      board.snapshot_id && candidate?.snapshot_id === board.snapshot_id
        ? candidate
        : undefined;
    const available =
      board.spot != null &&
      board.spot > 0 &&
      !!board.snapshot_id &&
      board.data_status !== "unavailable" &&
      board.data_status !== "unsupported";
    const pm = available ? board.prob_month : undefined;
    const intent = available ? board.intent?.all : undefined;
    const iv = available
      ? (board.atm_iv_30d ?? item?.analytics?.values.atm_iv_30d ?? null)
      : null;
    const rv = board.realized_vol_20 ?? null;
    const derived = {
      pu5: pm?.p_up5 ?? null,
      pd5: pm?.p_dn5 ?? null,
      pu10: pm?.p_up10 ?? null,
      pd10: pm?.p_dn10 ?? null,
      tail: ratio(pm?.p_dn10, pm?.p_up10),
      otmC: available ? ratio(intent?.otm_call, board.total_premium) : null,
      otmP: available ? ratio(intent?.otm_put, board.total_premium) : null,
      otmCallOfOtm: intent
        ? ratio(intent.otm_call, intent.otm_call + intent.otm_put)
        : null,
      short7: available
        ? ratio(board.expiry_buckets_volume[0], board.total_volume)
        : null,
      ivhv: iv != null && rv != null ? iv - rv : null,
      skew: board.skew_30?.skew ?? null,
    };
    return {
      ...derived,
      board,
      overview: item,
      symbol: board.symbol,
      s: shortSymbol(board.symbol),
      available,
      spot: available ? (board.spot ?? null) : null,
      iv,
      rv,
      prem: available ? board.total_premium : null,
      vol: available ? board.total_volume : null,
      pc: available ? ratio(board.put_volume, board.call_volume) : null,
      m5:
        item?.implied_moves.find((move) => move.horizon_days === 5)?.upper_pct ?? null,
      m20:
        item?.implied_moves.find((move) => move.horizon_days === 20)?.upper_pct ?? null,
      term: item?.term_structure,
      tgtC: board.targets?.month?.call ?? null,
      tgtP: board.targets?.month?.put ?? null,
      look: available
        ? outlookLabel(derived, locale)
        : {
            k: "missing" as const,
            t: board.reasons.includes("permission_denied")
              ? translate(locale, "options.optionsBoardRows.insufficientPermissions")
              : board.reasons.includes("no_option_contracts")
                ? translate(locale, "options.optionsBoardRows.noEligibleContracts")
                : board.reasons.some(
                      (reason) =>
                        ![
                          "option_snapshot_unavailable",
                          "no_complete_snapshot_available",
                        ].includes(reason),
                    )
                  ? translate(locale, "options.optionsBoardRows.collectionFailed")
                  : translate(locale, "options.optionsBoardRows.awaitingCollection"),
            why:
              board.reasons
                .map((argument0) => optionReasonLabel(argument0, locale))
                .join(";") ||
              translate(locale, "options.optionsBoardRows.noSavedOptionsSnapshot"),
          },
    };
  });
}
export type OptionsBoardRow = ReturnType<typeof buildOptionsBoardRows>[number];
export type OptionsBoardRows = readonly OptionsBoardRow[];

export function boardColors() {
  const style =
    typeof document === "undefined" ? null : getComputedStyle(document.documentElement);
  const color = (name: string, fallback: string) =>
    style?.getPropertyValue(`--color-${name}`).trim() || fallback;
  return {
    up: color("up", "#3ecf8e"),
    down: color("down", "#f2647a"),
    cone: color("cone", "#8f87c9"),
    text: color("text", "#ecebf2"),
    secondary: color("text-secondary", "#a9a8b6"),
    divider: color("divider", "#2c2c34"),
  };
}
