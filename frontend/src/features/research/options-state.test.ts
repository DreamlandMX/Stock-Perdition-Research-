import { describe, expect, it } from "vitest";
import { decodeResearchUrlState, encodeResearchUrlState } from "../../app/url-state";
import { optionQuery } from "./options-state";

describe("option reading state", () => {
  it("defaults to estimated dollars while preserving explicit existing metrics", () => {
    expect(optionQuery(new URLSearchParams()).metric).toBe("premium");
    expect(optionQuery(new URLSearchParams("option_metric=premium")).metric).toBe(
      "premium",
    );
    expect(optionQuery(new URLSearchParams("option_metric=oi")).metric).toBe("oi");
  });
  it("survives stock snapshot pinning and preserves range/metric/side", () => {
    const params = new URLSearchParams(
      "option_metric=iv&option_side=put&option_snapshot=option-test&option_dte_max=30&option_scale=0.8",
    );
    const encoded = encodeResearchUrlState({
      ...decodeResearchUrlState(params),
      snapshotId: "stock-test",
    });
    expect(encoded.get("option_metric")).toBe("iv");
    expect(encoded.get("option_snapshot")).toBe("option-test");
    expect(optionQuery(encoded)).toMatchObject({
      metric: "iv",
      side: "put",
      snapshot_id: "option-test",
      dte_max: 30,
    });
  });
  it("uses defaults for malformed controls", () => {
    expect(
      optionQuery(new URLSearchParams("option_metric=net_gex&option_dte_max=bad")),
    ).toMatchObject({ metric: "oi", dte_max: 90 });
  });
});
