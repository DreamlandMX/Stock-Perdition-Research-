import { ResearchErrorDetail } from "./ResearchErrorDetail";
import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { EChartsOption } from "echarts";
import { useOptionsOverview } from "../options/useOptionsOverview";
import { ChartExplainer } from "../options/ChartExplainer";
import { impliedCaveat, MODEL_COLOR, OPTION_COLOR } from "../options/options-copy";
import {
  optionNumber,
  optionPercent,
  optionReasonLabel,
} from "../options/option-labels";
import { OverviewTerminalChart } from "../overview/OverviewTerminalChart";
export function OptionModelVsMarket({
  symbol,
  optionSnapshotId,
}: {
  symbol: string;
  optionSnapshotId?: string | null | undefined;
}) {
  const { locale } = useI18n();
  const [params] = useSearchParams();
  const result = useOptionsOverview({
    symbol,
    snapshot_id: params.get("snapshot_id") ?? undefined,
    as_of: params.get("as_of") ?? undefined,
    option_snapshot_id: optionSnapshotId ?? undefined,
  });
  const item = result.data?.items.find((row) => row.symbol === symbol);
  const coverage = result.data?.coverage ?? [];
  const option = useMemo<EChartsOption>(
    () => ({
      animation: false,
      grid: { left: 60, right: 24, top: 45, bottom: 50 },
      legend: { textStyle: { color: "#b8b6c8" } },
      tooltip: {
        trigger: "axis",
        valueFormatter: (value) =>
          typeof value === "number"
            ? `${value.toFixed(1)}%`
            : translate(locale, "research.optionanalyticspanel.missing"),
      },
      xAxis: {
        type: "category",
        data: [
          ...new Set(
            coverage.flatMap((row) => row.history.map((point) => point.market_as_of)),
          ),
        ].sort(),
        axisLabel: { formatter: (v: string) => v.slice(0, 10) },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: 100,
        name: translate(locale, "research.optionmodelvsmarket.rollingCoverage"),
      },
      series: coverage.flatMap((row) => [
        {
          name: translate(locale, "research.optionmodelvsmarket.dayModel", {
            horizonDays: row.horizon_days,
          }),
          type: "line" as const,
          smooth: false,
          connectNulls: false,
          lineStyle: {
            color: MODEL_COLOR,
            type:
              row.horizon_days === 1
                ? ("dotted" as const)
                : row.horizon_days === 20
                  ? ("dashed" as const)
                  : ("solid" as const),
          },
          data: row.history.map((p) => [
            p.market_as_of,
            p.model_samples >= row.minimum_samples && p.model_coverage != null
              ? p.model_coverage * 100
              : null,
          ]),
          markLine: {
            symbol: "none",
            data: [
              {
                yAxis: 80,
                name: translate(
                  locale,
                  "research.optionmodelvsmarket.nominalCoverage80",
                ),
              },
            ],
          },
        },
        {
          name: translate(locale, "research.optionmodelvsmarket.dayImplied", {
            horizonDays: row.horizon_days,
          }),
          type: "line" as const,
          smooth: false,
          connectNulls: false,
          lineStyle: {
            color: OPTION_COLOR,
            type:
              row.horizon_days === 1
                ? ("dotted" as const)
                : row.horizon_days === 20
                  ? ("dashed" as const)
                  : ("solid" as const),
          },
          data: row.history.map((p) => [
            p.market_as_of,
            p.implied_samples >= row.minimum_samples && p.implied_coverage != null
              ? p.implied_coverage * 100
              : null,
          ]),
        },
      ]),
    }),
    [coverage, locale],
  );
  const hasHistory = coverage.some((row) =>
    row.history.some(
      (p) =>
        (p.model_samples >= row.minimum_samples && p.model_coverage != null) ||
        (p.implied_samples >= row.minimum_samples && p.implied_coverage != null),
    ),
  );
  return (
    <section
      className="option-model-market"
      aria-label={translate(
        locale,
        "research.optionmodelvsmarket.modelAndOptionsMarketIntervalComparison",
      )}
    >
      <h3>
        {translate(
          locale,
          "research.optionmodelvsmarket.modelVsMarket80EndOfHorizonInterval",
        )}
      </h3>
      <p className="options-notice">{impliedCaveat(locale)}</p>
      {result.isPending && (
        <p role="status">
          {translate(
            locale,
            "research.optionmodelvsmarket.aligningStockAndOptionsSnapshots",
          )}
        </p>
      )}
      {result.isError && (
        <div role="alert">
          {translate(
            locale,
            "research.optionmodelvsmarket.unableToLoadModelMarketComparison",
          )}
          <ResearchErrorDetail message={result.error.message} />
        </div>
      )}
      <div className="research-table-scroll">
        <table>
          <thead>
            <tr>
              <th>{translate(locale, "research.optionanalyticspanel.tradingDays")}</th>
              <th>{translate(locale, "research.optionmodelvsmarket.modelQ10")}</th>
              <th>q50</th>
              <th>q90</th>
              <th>{translate(locale, "research.optionmodelvsmarket.modelWidth")}</th>
              <th>
                {translate(locale, "research.optionmodelvsmarket.options80Interval")}
              </th>
              <th>{translate(locale, "research.optionmodelvsmarket.widthRatio")}</th>
              <th>{translate(locale, "research.optionmodelvsmarket.modelSource")}</th>
            </tr>
          </thead>
          <tbody>
            {[1, 5, 20].map((h) => {
              const model = item?.model_horizons.find((row) => row.horizon_days === h);
              const implied = item?.implied_moves.find((row) => row.horizon_days === h);
              return (
                <tr key={h}>
                  <td>{h}</td>
                  <td>{optionPercent(model?.q10, undefined, locale)}</td>
                  <td>{optionPercent(model?.q50, undefined, locale)}</td>
                  <td>{optionPercent(model?.q90, undefined, locale)}</td>
                  <td>
                    {optionPercent(
                      model?.q10 == null || model.q90 == null
                        ? null
                        : model.q90 - model.q10,
                      undefined,
                      locale,
                    )}
                  </td>
                  <td
                    title={
                      implied?.reason
                        ? optionReasonLabel(implied.reason, locale)
                        : implied?.formula_version
                    }
                  >
                    {implied?.upper_pct == null
                      ? translate(
                          locale,
                          "research.optionmodelvsmarket.horizonNotCoveredMissing",
                        )
                      : `±${optionPercent(implied.upper_pct, undefined, locale)}`}
                  </td>
                  <td
                    title={translate(
                      locale,
                      "research.optionmodelvsmarket.modelQ10Q90WidthOptions80ImpliedIntervalWidth",
                    )}
                  >
                    {optionNumber(model?.width_ratio, locale)}
                  </td>
                  <td
                    title={model?.reasons
                      .map((reason) => optionReasonLabel(reason, locale))
                      .join(locale === "en-US" ? "; " : "；")}
                  >
                    {model?.status === "enhanced"
                      ? translate(locale, "research.optionmodelvsmarket.enhanced")
                      : model?.status === "baseline_fallback"
                        ? translate(
                            locale,
                            "research.optionmodelvsmarket.baselineFallback",
                          )
                        : translate(
                            locale,
                            "research.optionmodelvsmarket.abstainedNoForecast",
                          )}
                    {model?.model_source ? `; ${model.model_source}` : ""}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <h3>
        {translate(
          locale,
          "research.optionmodelvsmarket.rollingCoverageOnMaturedSamples",
        )}
      </h3>
      {hasHistory ? (
        <>
          <OverviewTerminalChart
            title={translate(
              locale,
              "research.optionmodelvsmarket.modelAndImpliedIntervalRollingCoverage",
            )}
            summary={translate(
              locale,
              "research.optionmodelvsmarket.usesMaturedActualReturnsOnlyLinesRequireTheMinimum",
            )}
            option={option}
            tableLabel={translate(
              locale,
              "research.optionmodelvsmarket.viewMaturedSamplesAndCoverage",
            )}
          >
            <table>
              <thead>
                <tr>
                  <th>{translate(locale, "research.optionmodelvsmarket.horizon")}</th>
                  <th>{translate(locale, "research.optionmodelvsmarket.time")}</th>
                  <th>
                    {translate(
                      locale,
                      "research.optionmodelvsmarket.modelSamplesCoverage",
                    )}
                  </th>
                  <th>
                    {translate(
                      locale,
                      "research.optionmodelvsmarket.impliedSamplesCoverage",
                    )}
                  </th>
                </tr>
              </thead>
              <tbody>
                {coverage.flatMap((row) =>
                  row.history.map((p) => (
                    <tr key={`${row.horizon_days}-${p.market_as_of}`}>
                      <td>{row.horizon_days}</td>
                      <td>{p.market_as_of}</td>
                      <td>
                        {p.model_samples} /{" "}
                        {p.model_samples >= row.minimum_samples
                          ? optionPercent(p.model_coverage, undefined, locale)
                          : translate(
                              locale,
                              "research.optionmodelvsmarket.insufficientSamples",
                            )}
                      </td>
                      <td>
                        {p.implied_samples} /{" "}
                        {p.implied_samples >= row.minimum_samples
                          ? optionPercent(p.implied_coverage, undefined, locale)
                          : translate(
                              locale,
                              "research.optionmodelvsmarket.insufficientSamples",
                            )}
                      </td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </OverviewTerminalChart>
          <ChartExplainer kind="coverage" />
        </>
      ) : (
        <div className="options-empty">
          {[1, 5, 20].map((h) => {
            const row = coverage.find((c) => c.horizon_days === h);
            return (
              <p key={h}>
                {translate(
                  locale,
                  "research.optionmodelvsmarket.dayMaturedModelSamples.withValues",
                  {
                    horizon: h,
                    modelSamples:
                      row?.model_samples ??
                      translate(locale, "research.optionanalyticspanel.unconfirmed"),
                    impliedSamples:
                      row?.implied_samples ??
                      translate(locale, "research.optionanalyticspanel.unconfirmed"),
                    minimumSamples:
                      row?.minimum_samples ??
                      translate(
                        locale,
                        "research.optionforecastpanel.sourceNotProvided",
                      ),
                  },
                )}
              </p>
            );
          })}
        </div>
      )}
    </section>
  );
}
