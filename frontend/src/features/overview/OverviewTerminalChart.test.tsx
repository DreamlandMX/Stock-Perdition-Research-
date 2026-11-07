import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { EChartsOption } from "echarts";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { OverviewTerminalChart } from "./OverviewTerminalChart";

const chart = vi.hoisted(() => ({
  init: vi.fn(),
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
  on: vi.fn(),
  getOption: vi.fn(),
}));

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  init: chart.init,
}));

class ViewportObserver {
  static instances: ViewportObserver[] = [];
  observe = vi.fn();
  disconnect = vi.fn();

  constructor(
    readonly callback: IntersectionObserverCallback,
    readonly options: IntersectionObserverInit,
  ) {
    ViewportObserver.instances.push(this);
  }

  intersect(isIntersecting: boolean) {
    this.callback(
      [{ isIntersecting } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    );
  }
}

class SizeObserver {
  static instances: SizeObserver[] = [];
  observe = vi.fn();
  disconnect = vi.fn();

  constructor(readonly callback: ResizeObserverCallback) {
    SizeObserver.instances.push(this);
  }

  resize() {
    this.callback([], this as unknown as ResizeObserver);
  }
}

function chartElement(option: EChartsOption, onCellSelect?: (index: number) => void) {
  return (
    <OverviewTerminalChart
      title="市场走势"
      summary="固定快照"
      tableLabel="查看数据"
      option={option}
      onCellSelect={onCellSelect}
    >
      <table aria-label="市场数据">
        <tbody>
          <tr>
            <td>SPY</td>
            <td>10%</td>
          </tr>
        </tbody>
      </table>
    </OverviewTerminalChart>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  chart.init.mockReturnValue(chart);
  ViewportObserver.instances = [];
  SizeObserver.instances = [];
  vi.stubGlobal("IntersectionObserver", ViewportObserver);
  vi.stubGlobal("ResizeObserver", SizeObserver);
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(640);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function (
    this: HTMLElement,
  ) {
    return this.closest("details")?.open === false ? 0 : 290;
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("terminal chart lifecycle", () => {
  test("retains the selected zoom when translated chart options replace labels", () => {
    const view = render(
      chartElement({ dataZoom: [{ type: "inside", start: 0, end: 100 }], series: [] }),
    );
    act(() => ViewportObserver.instances[0]!.intersect(true));
    chart.getOption.mockReturnValue({
      dataZoom: [{ start: 25, end: 65, startValue: 4, endValue: 12 }],
      legend: [{ selected: { 市场宽度: false } }],
      series: [{ name: "市场宽度" }],
    });
    view.rerender(
      chartElement({
        dataZoom: [{ type: "inside", start: 0, end: 100 }],
        legend: {},
        series: [{ type: "line", name: "Market breadth", data: [] }],
      }),
    );
    expect(chart.setOption).toHaveBeenLastCalledWith(
      expect.objectContaining({
        dataZoom: [
          expect.objectContaining({ start: 25, end: 65, startValue: 4, endValue: 12 }),
        ],
        series: [expect.objectContaining({ name: "Market breadth" })],
        legend: [expect.objectContaining({ selected: { "Market breadth": false } })],
      }),
      true,
    );
    expect(chart.init).toHaveBeenCalledTimes(1);
    expect(chart.dispose).not.toHaveBeenCalled();
  });
  test("retains hidden radar data items when translated names replace labels", () => {
    const view = render(
      chartElement({
        legend: {},
        series: [{ type: "radar", data: [{ name: "当前", value: [1] }] }],
      }),
    );
    act(() => ViewportObserver.instances[0]!.intersect(true));
    chart.getOption.mockReturnValue({
      legend: [{ selected: { 当前: false } }],
      series: [{ type: "radar", data: [{ name: "当前", value: [1] }] }],
    });
    view.rerender(
      chartElement({
        legend: {},
        series: [{ type: "radar", data: [{ name: "Current", value: [1] }] }],
      }),
    );
    expect(chart.setOption).toHaveBeenLastCalledWith(
      expect.objectContaining({
        legend: [expect.objectContaining({ selected: { Current: false } })],
      }),
      true,
    );
    expect(chart.init).toHaveBeenCalledTimes(1);
  });
  test("respects explicit animation disabling for live pulse updates", () => {
    render(chartElement({ animation: false, series: [] }));
    act(() => ViewportObserver.instances[0]!.intersect(true));
    expect(chart.setOption).toHaveBeenLastCalledWith(
      expect.objectContaining({ animation: false }),
      true,
    );
  });
  test("waits for the viewport, applies current options and callbacks, and disposes only on unmount", () => {
    const firstOption: EChartsOption = { series: [{ type: "line", data: [1] }] };
    const nextOption: EChartsOption = { series: [{ type: "bar", data: [2] }] };
    const select = vi.fn();
    const nextSelect = vi.fn();
    const view = render(chartElement(firstOption));
    const viewport = ViewportObserver.instances[0]!;

    expect(viewport.options.rootMargin).toBe("300px");
    expect(chart.init).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "市场走势" })).toBeVisible();
    expect(screen.getByText("查看数据")).toBeVisible();
    expect(screen.getByRole("table", { hidden: true })).toHaveTextContent("SPY10%");
    act(() => viewport.intersect(false));
    expect(chart.init).not.toHaveBeenCalled();

    view.rerender(chartElement(nextOption, select));
    act(() => viewport.intersect(true));
    expect(chart.init).toHaveBeenCalledTimes(1);
    expect(chart.setOption).toHaveBeenLastCalledWith(
      expect.objectContaining({ series: nextOption.series }),
      true,
    );

    view.rerender(chartElement(firstOption, nextSelect));
    expect(chart.setOption).toHaveBeenCalledTimes(2);
    expect(chart.setOption).toHaveBeenLastCalledWith(
      expect.objectContaining({ series: firstOption.series }),
      true,
    );
    const click = chart.on.mock.calls[0]?.[1] as (event: unknown) => void;
    click({ dataIndex: 3 });
    click({ dataIndex: "invalid" });
    expect(select).not.toHaveBeenCalled();
    expect(nextSelect).toHaveBeenCalledExactlyOnceWith(3);
    act(() => SizeObserver.instances[0]!.resize());
    expect(chart.resize).toHaveBeenCalledTimes(1);
    expect(chart.init).toHaveBeenCalledTimes(1);
    expect(chart.dispose).not.toHaveBeenCalled();

    view.unmount();
    expect(chart.dispose).toHaveBeenCalledTimes(1);
    expect(viewport.disconnect).toHaveBeenCalled();
    expect(SizeObserver.instances[0]!.disconnect).toHaveBeenCalled();
  });

  test("waits for a collapsed ancestor to open and resizes the same instance on later reveals", () => {
    const view = render(<details>{chartElement({ series: [] })}</details>);
    const details = view.container.querySelector("details")!;
    act(() => ViewportObserver.instances[0]!.intersect(true));
    expect(chart.init).not.toHaveBeenCalled();

    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(chart.init).toHaveBeenCalledTimes(1);

    details.open = false;
    fireEvent(details, new Event("toggle"));
    expect(chart.dispose).not.toHaveBeenCalled();
    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(chart.resize).toHaveBeenCalledTimes(1);
    expect(chart.init).toHaveBeenCalledTimes(1);
  });
});
