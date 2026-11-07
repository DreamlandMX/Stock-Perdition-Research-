import { describe, expect, test } from "vitest";
import {
  getOptionLabels,
  optionNumber,
  optionPercent,
  optionPoints,
  optionReasonLabel,
} from "./option-labels";
import {
  buildOptionsBoardRows,
  money,
  outlookLabel,
  termLabel,
} from "./options-board-rows";
import { getOptionsCopy, impliedCaveat } from "./options-copy";
import type { OptionBoardItem } from "./useOptionsBoard";

describe("options localization", () => {
  test("translates reasons and chart explanations without weakening missing-data limits", () => {
    const chinese = getOptionLabels("zh-CN");
    const english = getOptionLabels("en-US");
    expect(Object.keys(english)).toEqual(Object.keys(chinese));
    for (const text of Object.values(english))
      expect(text).not.toMatch(/[\u3400-\u9fff]/);
    expect(optionReasonLabel("history_requires_twenty_sessions", "en-US")).toBe(
      "At least 20 trading days of history required",
    );
    expect(optionReasonLabel("vendor_unknown_code", "en-US")).toBe(
      "Unknown (vendor_unknown_code)",
    );
    expect(termLabel("inverted", "en-US")).toBe("Inverted");
    expect(getOptionsCopy("en-US").term[1]).toContain("trading days");
    expect(impliedCaveat("en-US")).toContain("risk-neutral pricing");
  });

  test("keeps numeric precision, missing values and USD across locales", () => {
    for (const locale of ["zh-CN", "en-US"] as const) {
      expect(optionNumber(null, locale)).toBe("—");
      expect(optionNumber(0, locale)).toBe("0");
      expect(optionNumber(-1234.567, locale)).toBe("-1,234.57");
      expect(optionPercent(-0.12345, 2, locale)).toBe("-12.35%");
      expect(money(1_250_000, locale)).toBe("$1.3M");
    }
    expect(optionPoints(0.0123, "en-US")).toBe("+1.23 pp");
    expect(optionPoints(0.0123, "zh-CN")).toBe("+1.23 百分点");
  });

  test("changes row explanations while retaining source facts and classification", () => {
    const item = {
      symbol: "US.TEST",
      snapshot_id: "saved",
      data_status: "available",
      spot: 100,
      atm_iv_30d: 0.3,
      realized_vol_20: 0.2,
      total_volume: 100,
      total_premium: 1000,
      put_volume: 60,
      call_volume: 40,
      expiry_buckets_volume: [20, 30, 50],
      prob_month: { p_up5: 0.15, p_dn5: 0.25, p_up10: 0.1, p_dn10: 0.2 },
      skew_30: { skew: 0.05 },
      intent: { all: { otm_call: 100, otm_put: 300 } },
      reasons: [],
    } as unknown as OptionBoardItem;
    const zh = buildOptionsBoardRows([item], [], "zh-CN")[0]!;
    const en = buildOptionsBoardRows([item], [], "en-US")[0]!;
    expect(en.board).toBe(item);
    expect(en.look.k).toBe(zh.look.k);
    expect(en.look.t).toBe("Downside protection");
    expect(en.look.why).not.toBe(zh.look.why);
    expect({ ...en, look: zh.look }).toEqual(zh);
    expect(
      outlookLabel(
        {
          tail: null,
          skew: null,
          pu10: null,
          pd10: null,
          pu5: null,
          pd5: null,
          otmCallOfOtm: null,
        },
        "en-US",
      ).t,
    ).toBe("Insufficient metrics");
  });
});
