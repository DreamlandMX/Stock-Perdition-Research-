import { describe, expect, it } from "vitest";

import { isIsoUtcTimestamp, isProbability, isSchemaVersion } from "./contract-smoke";

describe("generic client boundary guards", () => {
  it.each([
    ["2026-07-11T12:30:00Z", true],
    ["2026-07-11T12:30:00.123Z", true],
    ["2026-07-11T08:30:00-04:00", false],
    ["not-a-date", false],
  ])("validates UTC timestamp %s", (value, expected) => {
    expect(isIsoUtcTimestamp(value)).toBe(expected);
  });

  it.each([
    [0, true],
    [0.5, true],
    [1, true],
    [-0.01, false],
    [1.01, false],
    [Number.NaN, false],
    [Number.POSITIVE_INFINITY, false],
  ])("validates probability %s", (value, expected) => {
    expect(isProbability(value)).toBe(expected);
  });

  it.each([
    ["v1", true],
    ["v12", true],
    ["1", false],
    ["v0", false],
    ["latest", false],
  ])("validates schema version %s", (value, expected) => {
    expect(isSchemaVersion(value)).toBe(expected);
  });
});
