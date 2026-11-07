import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { expect, expectTypeOf, test } from "vitest";

import type { components, paths } from "../api/generated/schema";

type ForecastResponse =
  paths["/api/v1/instruments/{symbol}/forecast"]["get"]["responses"]["200"]["content"]["application/json"];
type ForecastData = ForecastResponse["data"];
type AvailableForecast = Extract<ForecastData, { availability: "available" }>;
type UnavailableForecast = Extract<ForecastData, { availability: "unavailable" }>;
type ScreenResponse =
  paths["/api/v1/screen"]["get"]["responses"]["200"]["content"]["application/json"];

test("generated contract exposes available and unavailable forecasts", () => {
  const generatedSchema = resolve(
    process.cwd(),
    "frontend/src/api/generated/schema.d.ts",
  );

  expect(existsSync(generatedSchema)).toBe(true);
  expectTypeOf<ForecastData["availability"]>().toEqualTypeOf<
    "available" | "unavailable"
  >();
  expectTypeOf<AvailableForecast>().not.toEqualTypeOf<never>();
  expectTypeOf<UnavailableForecast>().not.toEqualTypeOf<never>();
  expectTypeOf<AvailableForecast>().toHaveProperty("bundle");
  expectTypeOf<UnavailableForecast>().toHaveProperty("not_generated_reason");
  expectTypeOf<ScreenResponse["resolved_snapshot"]>().toEqualTypeOf<
    components["schemas"]["ResolvedSnapshot"]
  >();
});
