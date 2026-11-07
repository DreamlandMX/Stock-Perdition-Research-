import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { LineChart } from "echarts/charts";
import { GridComponent } from "echarts/components";
import { init, use, type EChartsCoreOption } from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef } from "react";
import type { components } from "../../api/generated/schema";
type M7Capability = components["schemas"]["M7CapabilityLifecycleStatus"];
interface ReliabilityRow {
  bin_index: number;
  class_name: string;
  horizon: number;
  lower_bound: number;
  mean_prediction: number;
  model: "candidate" | "incumbent";
  observed_frequency: number;
  sample_count: number;
  upper_bound: number;
}
const CLASS_LABELS = (locale: Locale): Record<string, string> => ({
  down: translate(locale, "m7.down"),
  exit_by_20: translate(locale, "m7.exitWithin20Days"),
  flat: translate(locale, "m7.flat"),
  up: translate(locale, "m7.up"),
});
const MODEL_LABELS = (locale: Locale): Record<ReliabilityRow["model"], string> => ({
  candidate: translate(locale, "m7.challenger"),
  incumbent: translate(locale, "m7.incumbent"),
});
use([SVGRenderer, GridComponent, LineChart]);
function objectValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function reliabilityRows(capability: M7Capability): ReliabilityRow[] {
  const section = capability.evidence_sections?.blind_report;
  const payload = objectValue(section?.payload);
  if (section?.status !== "completed" || !Array.isArray(payload?.reliability))
    return [];
  return payload.reliability.flatMap((value) => {
    const row = objectValue(value);
    if (
      row === null ||
      !Number.isInteger(row.bin_index) ||
      typeof row.class_name !== "string" ||
      !Number.isInteger(row.horizon) ||
      typeof row.lower_bound !== "number" ||
      typeof row.mean_prediction !== "number" ||
      (row.model !== "candidate" && row.model !== "incumbent") ||
      typeof row.observed_frequency !== "number" ||
      !Number.isInteger(row.sample_count) ||
      typeof row.upper_bound !== "number"
    ) {
      return [];
    }
    return [row as unknown as ReliabilityRow];
  });
}
function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}
function ReliabilityChart({
  className,
  rows,
}: {
  className: string;
  rows: readonly ReliabilityRow[];
}) {
  const { locale } = useI18n();
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<ReturnType<typeof init> | null>(null);
  const label =
    CLASS_LABELS(locale)[className] ??
    translate(locale, "m7.unknownState", { code: className });
  const option = useMemo<EChartsCoreOption>(() => {
    const modelSeries = (["candidate", "incumbent"] as const).map((model) => ({
      data: rows
        .filter((row) => row.model === model)
        .sort((left, right) => left.bin_index - right.bin_index)
        .map((row) => [row.mean_prediction, row.observed_frequency]),
      endLabel: {
        color: model === "candidate" ? "#38bdf8" : "#cbd5e1",
        formatter: MODEL_LABELS(locale)[model],
        show: true,
      },
      itemStyle: { color: model === "candidate" ? "#38bdf8" : "#cbd5e1" },
      lineStyle: {
        color: model === "candidate" ? "#38bdf8" : "#cbd5e1",
        type: model === "candidate" ? "solid" : "dotted",
        width: 2,
      },
      name: MODEL_LABELS(locale)[model],
      showSymbol: true,
      symbolSize: 6,
      type: "line" as const,
    }));
    return {
      animation: false,
      grid: { bottom: 38, containLabel: true, left: 8, right: 70, top: 12 },
      series: [
        ...modelSeries,
        {
          data: [
            [0, 0],
            [1, 1],
          ],
          endLabel: {
            color: "#64748b",
            formatter: translate(locale, "m7.idealCalibration"),
            show: true,
          },
          lineStyle: { color: "#64748b", type: "dashed", width: 1 },
          name: translate(locale, "m7.idealCalibration"),
          showSymbol: false,
          silent: true,
          type: "line",
        },
      ],
      xAxis: {
        axisLabel: { color: "#94a3b8", formatter: "{value}" },
        max: 1,
        min: 0,
        name: translate(locale, "m7.predictedRate"),
        nameGap: 24,
        nameLocation: "middle",
        nameTextStyle: { color: "#94a3b8" },
        splitLine: { lineStyle: { color: "rgba(148, 163, 184, 0.12)" } },
        type: "value",
      },
      yAxis: {
        axisLabel: { color: "#94a3b8", formatter: "{value}" },
        max: 1,
        min: 0,
        name: translate(locale, "m7.observedRate"),
        nameGap: 30,
        nameLocation: "middle",
        nameTextStyle: { color: "#94a3b8" },
        splitLine: { lineStyle: { color: "rgba(148, 163, 184, 0.12)" } },
        type: "value",
      },
    };
  }, [rows, locale]);
  useEffect(() => {
    const container = chartRef.current;
    if (container === null || container.clientWidth === 0) return;
    const chart = init(container, undefined, { renderer: "svg" });
    chartInstanceRef.current = chart;
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
      chartInstanceRef.current = null;
    };
  }, []);
  useEffect(() => {
    chartInstanceRef.current?.setOption(option);
  }, [option]);
  return (
    <article className="m7-reliability-card">
      <h5>{label}</h5>
      <div
        aria-label={translate(
          locale,
          "m7.reliabilityChartChallengerIncumbentAndIdealCalibrationAre",
          { label: String(label) },
        )}
        className="m7-reliability-chart"
        ref={chartRef}
        role="img"
        tabIndex={0}
      />
    </article>
  );
}
export function M7BlindEvidencePanel({
  capability,
  horizon,
}: {
  capability: M7Capability;
  horizon: 1 | 5 | 20;
}) {
  const { locale } = useI18n();
  const allRows = reliabilityRows(capability);
  const rows = allRows.filter((row) => row.horizon === horizon);
  const classNames =
    capability.bundle_id === "direction" ? ["down", "flat", "up"] : ["exit_by_20"];
  if (rows.length === 0) {
    return (
      <section
        aria-label={translate(locale, "m7.m7ForwardBlindTestEvidence")}
        className="m7-development-evidence"
      >
        <h4>
          {translate(locale, "m7.forwardReliabilityH")}
          {horizon}
        </h4>
        <p className="m7-evidence-empty">
          {" " +
            (translate(locale, "m7.forwardReliabilityUnavailableCurrentAnchors") + " ")}
          {capability.blind_available_anchors}
          {" " + (translate(locale, "m7.77MatureLabels") + " ")}
          {capability.blind_mature_labels} / 77.
        </p>
      </section>
    );
  }
  return (
    <section
      aria-label={translate(locale, "m7.m7ForwardBlindTestEvidence")}
      className="m7-development-evidence"
    >
      <h4>
        {translate(locale, "m7.forwardReliabilityH2")}
        {horizon}
      </h4>
      <p className="m7-evidence-note">
        {" " + (translate(locale, "m7.forwardSamplesIn10FixedBinsPointsCloser") + " ")}
      </p>
      <div className="m7-reliability-grid">
        {classNames.map((className) => (
          <ReliabilityChart
            className={className}
            key={className}
            rows={rows.filter((row) => row.class_name === className)}
          />
        ))}
      </div>
      <div className="m7-table-wrap">
        <table
          aria-label={translate(locale, "m7.hForwardReliabilityData", {
            direction: String(
              capability.bundle_id === "direction"
                ? translate(locale, "m7.directionProbability")
                : translate(locale, "m7.20DayRangeExit"),
            ),
            horizon: String(horizon),
          })}
          className="m7-metric-table"
        >
          <thead>
            <tr>
              <th scope="col">{translate(locale, "m7.class")}</th>
              <th scope="col">{translate(locale, "m7.model")}</th>
              <th scope="col">{translate(locale, "m7.probabilityBin")}</th>
              <th scope="col">{translate(locale, "m7.meanPrediction")}</th>
              <th scope="col">{translate(locale, "m7.observedFrequency")}</th>
              <th scope="col">{translate(locale, "m7.samples")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.class_name}-${row.model}-${row.bin_index}`}>
                <th scope="row">
                  {CLASS_LABELS(locale)[row.class_name] ?? row.class_name}
                </th>
                <td>{MODEL_LABELS(locale)[row.model]}</td>
                <td>
                  {percent(row.lower_bound)}–{percent(row.upper_bound)}
                </td>
                <td>{percent(row.mean_prediction)}</td>
                <td>{percent(row.observed_frequency)}</td>
                <td>{row.sample_count.toLocaleString(locale)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
