import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { PriceIntervalTrack } from "./PriceIntervalTrack";

afterEach(cleanup);

describe("PriceIntervalTrack", () => {
  test("maps reference price and q50 independently inside the q10-q90 context", () => {
    render(
      <PriceIntervalTrack
        currency="USD"
        q10={90}
        q50={105}
        q90={110}
        referencePrice={100}
      />,
    );

    const track = screen.getByRole("img", {
      name: "当前收盘 $100.00；q10 $90.00；q50 $105.00；q90 $110.00",
    });
    expect(track).toHaveStyle({
      "--price-q10": "0.00%",
      "--price-q50": "75.00%",
      "--price-q90": "100.00%",
      "--price-reference": "50.00%",
    });
  });

  test("keeps an equal interval deterministic", () => {
    render(
      <PriceIntervalTrack
        currency="USD"
        q10={100}
        q50={100}
        q90={100}
        referencePrice={100}
      />,
    );

    expect(screen.getByRole("img")).toHaveStyle({
      "--price-q10": "0.00%",
      "--price-q50": "0.00%",
      "--price-q90": "0.00%",
      "--price-reference": "0.00%",
    });
  });

  test("renders an explicit unavailable state for an invalid interval", () => {
    render(
      <PriceIntervalTrack
        currency="USD"
        q10={110}
        q50={105}
        q90={90}
        referencePrice={100}
      />,
    );

    expect(screen.getByText("价格区间暂不可用")).toBeVisible();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  test("rejects q50 outside the interval and non-positive prices", () => {
    const { rerender } = render(
      <PriceIntervalTrack
        currency="USD"
        q10={90}
        q50={120}
        q90={110}
        referencePrice={100}
      />,
    );

    expect(screen.getByText("价格区间暂不可用")).toBeVisible();

    rerender(
      <PriceIntervalTrack
        currency="USD"
        q10={0}
        q50={100}
        q90={110}
        referencePrice={100}
      />,
    );
    expect(screen.getByText("价格区间暂不可用")).toBeVisible();
  });
});
