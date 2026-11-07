import { describe, expect, test } from "vitest";
import { researchEvidenceLabel } from "./research-labels";

describe("research evidence codes", () => {
  test("localizes current forecast reasons without changing their machine codes", () => {
    const code = "corporate_action_revision";
    expect(researchEvidenceLabel(code, "zh-CN")).toBe("公司行动数据发生修订");
    expect(researchEvidenceLabel(code, "en-US")).toBe("Corporate-action data revised");
    expect(code).toBe("corporate_action_revision");
    expect(researchEvidenceLabel("right_censored_at_horizon", "en-US")).toContain(
      "right-censored",
    );
  });

  test("leaves unrecognized historical text for a localized explanation and original details", () => {
    expect(
      researchEvidenceLabel("an unrecognized provider explanation", "en-US"),
    ).toBeNull();
  });
});
