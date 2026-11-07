import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { AvailabilityNotice } from "./AvailabilityNotice";

afterEach(() => cleanup());

describe("AvailabilityNotice", () => {
  test.each([
    ["awaiting_model", "status"],
    ["unavailable", "status"],
    ["error", "alert"],
  ] as const)(
    "uses %s semantics without treating expected gaps as errors",
    (kind, role) => {
      render(<AvailabilityNotice description="说明" kind={kind} title="状态标题" />);

      expect(screen.getByRole(role, { name: "状态标题" })).toHaveAttribute(
        "data-state-kind",
        kind,
      );
    },
  );
});
