import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { ResearchModeSwitch } from "./ResearchModeSwitch";

afterEach(cleanup);

test("keeps formal prediction selectable while Champion is incomplete", async () => {
  const onChange = vi.fn();
  const user = userEvent.setup();
  render(
    <ResearchModeSwitch formalAvailable={false} mode="research" onChange={onChange} />,
  );

  const formal = screen.getByRole("button", { name: /正式预测/ });
  expect(formal).toBeEnabled();
  expect(formal).toHaveAttribute("title", expect.stringContaining("发布状态"));

  await user.click(formal);

  expect(onChange).toHaveBeenCalledWith("formal");
});
