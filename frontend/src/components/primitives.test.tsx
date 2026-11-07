import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { ConfidenceLabel } from "./ConfidenceLabel";
import { DataStatusBanner } from "./DataStatusBanner";
import { EmptyState } from "./EmptyState";
import { ErrorBoundary } from "./ErrorBoundary";
import { ProbabilityValue } from "./ProbabilityValue";
import { ReturnValue } from "./ReturnValue";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("semantic research values", () => {
  test("formats decimal probabilities as one-decimal percentages", () => {
    render(<ProbabilityValue value={0.643} />);

    expect(screen.getByText("64.3%")).toHaveClass("numeric-value");
  });

  test("uses a supplied explanation when a research probability is unavailable", () => {
    render(<ProbabilityValue unavailableLabel="该周期无候选退出任务" value={null} />);

    expect(screen.getByText("该周期无候选退出任务")).toHaveClass(
      "numeric-value--unavailable",
    );
  });

  test("formats returns with a sign and a textual direction", () => {
    const { rerender } = render(<ReturnValue value={0.064} />);

    expect(screen.getByLabelText("收益: 上行 +6.4%")).toHaveTextContent("+6.4%");

    rerender(<ReturnValue value={-0.112} />);
    expect(screen.getByLabelText("收益: 下行 -11.2%")).toHaveTextContent("-11.2%");
    for (const value of [0.0001, -0.0001, -0]) {
      rerender(<ReturnValue value={value} />);
      expect(screen.getByLabelText("收益: 持平 0.0%")).toHaveClass(
        "return-value--flat",
      );
    }
  });

  test("renders confidence with generated enum semantics and visible text", () => {
    render(<ConfidenceLabel level="high" />);

    const label = screen.getByText("高置信");
    expect(label.parentElement).toHaveClass("confidence-label--high");
  });
});

describe("shared data states", () => {
  test.each([
    ["delayed", "数据延迟"],
    ["stale", "数据已过期"],
    ["offline", "当前离线"],
    ["partial", "数据不完整"],
  ] as const)("keeps %s status visibly labelled", (status, message) => {
    render(<DataStatusBanner lastSuccessfulAt="2026-07-10 16:05 ET" status={status} />);

    const banner = screen.getByRole("status", { name: "全局数据状态" });
    expect(banner).toHaveTextContent(message);
    expect(banner).toHaveTextContent("最后成功数据: 2026-07-10 16:05 ET");
    expect(banner).not.toHaveTextContent("实时");
    expect(banner.querySelector("svg")).toBeInTheDocument();
  });

  test("does not show a global banner for live data", () => {
    const { container } = render(
      <DataStatusBanner lastSuccessfulAt="2026-07-10 16:05 ET" status="live" />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  test("provides a named empty state without inventing a value", () => {
    render(
      <EmptyState
        description="当前快照没有满足条件的研究标的。"
        title="没有研究结果"
      />,
    );

    expect(screen.getByRole("status", { name: "没有研究结果" })).toHaveTextContent(
      "当前快照没有满足条件的研究标的。",
    );
  });
});

describe("application error boundary", () => {
  test("replaces an application crash with an accessible recovery state", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const CrashingChild = () => {
      throw new Error("private upstream detail");
    };

    render(
      <ErrorBoundary>
        <CrashingChild />
      </ErrorBoundary>,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("研究工作台暂时不可用");
    expect(alert).not.toHaveTextContent("private upstream detail");
    expect(screen.getByRole("button", { name: "重新载入研究工作台" })).toBeVisible();
  });
});
