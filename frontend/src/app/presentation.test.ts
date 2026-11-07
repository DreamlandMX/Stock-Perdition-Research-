import { describe, expect, test } from "vitest";

import { formatMarketSessionDate, formatUsMarketTime } from "./presentation";

describe("market date presentation", () => {
  test("does not shift a date-only trading session to the prior US day", () => {
    expect(formatMarketSessionDate("2026-07-31")).toBe("2026-07-31");
    expect(formatUsMarketTime("2026-07-31")).toBe("2026-07-31");
  });

  test("converts a full timestamp to New York date and time", () => {
    expect(formatMarketSessionDate("2026-08-01T00:00:00Z")).toBe("2026-07-31");
    expect(formatUsMarketTime("2026-07-31T20:00:00Z")).toContain("2026/07/31 16:00");
  });
});
