import { expect, it } from "vitest";
import {
  decodeResearchUrlState,
  encodeResearchUrlState,
  researchStateForSymbol,
} from "../../app/url-state";

it("cross-symbol navigation keeps filters and clears the option snapshot pin", () => {
  const state = decodeResearchUrlState(
    new URLSearchParams(
      "option_metric=iv&option_side=put&option_snapshot=aapl-snapshot",
    ),
  );
  const next = encodeResearchUrlState(
    researchStateForSymbol(state, "US.AAPL", "US.SPY"),
  );
  expect(next.get("option_snapshot")).toBeNull();
  expect(next.get("option_metric")).toBe("iv");
  expect(
    encodeResearchUrlState(researchStateForSymbol(state, "US.AAPL", "US.AAPL")).get(
      "option_snapshot",
    ),
  ).toBe("aapl-snapshot");
});
