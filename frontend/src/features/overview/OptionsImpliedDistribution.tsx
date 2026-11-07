import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import {
  boardColors,
  number,
  percent,
  type OptionsBoardRow,
} from "../options/options-board-rows";
import { OverviewTerminalChart } from "./OverviewTerminalChart";

export function OptionsImpliedDistribution({
  row,
}: {
  row: OptionsBoardRow | undefined;
}) {
  const { locale } = useI18n();
  const [window, setWindow] = useState<"week" | "month">("month");
  const probability = window === "week" ? row?.board.prob_week : row?.board.prob_month;
  const target = row?.board.targets?.[window];
  const spot = row?.spot;
  const option = useMemo<EChartsOption>(() => {
    if (!spot || !probability?.curve.length) return {};
    const colors = boardColors();
    const curve = probability.curve;
    const density = curve.map((point, i) => {
      const a = curve[Math.max(0, i - 1)]!,
        b = curve[Math.min(curve.length - 1, i + 1)]!;
      return [
        point.strike,
        b.strike > a.strike
          ? Math.max(
              0,
              (a.p_above - b.p_above) / (((b.strike - a.strike) / spot) * 100),
            )
          : 0,
      ] as [number, number];
    });
    const max = Math.max(...density.map((point) => point[1]));
    const live = density
      .filter((point) => point[1] >= max * 0.03)
      .map((point) => point[0]);
    const span =
      Math.max(spot - Math.min(...live), Math.max(...live) - spot, spot * 0.06) +
      spot * 0.01;
    const line = (
      name: string,
      condition: (price: number) => boolean,
      color: string,
    ) => ({
      name,
      type: "line" as const,
      symbol: "none",
      smooth: 0.35,
      connectNulls: false,
      lineStyle: { color, width: 1.5 },
      areaStyle: { color, opacity: 0.24 },
      data: density.map(([price, value]) => [price, condition(price) ? value : null]),
    });
    return {
      animation: false,
      grid: { left: 48, right: 22, top: 32, bottom: 34 },
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "value",
        min: Math.max(curve[0]!.strike, spot - span),
        max: Math.min(curve.at(-1)!.strike, spot + span),
        axisLabel: { formatter: (v: number) => number(v, v > 100 ? 0 : 1, locale) },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        axisLabel: { formatter: (v: number) => percent(v, 0, locale) },
        splitLine: { lineStyle: { color: colors.divider } },
      },
      series: [
        line(
          translate(locale, "overview.OptionsImpliedDistribution.down5"),
          (price) => price <= spot * 0.95,
          colors.down,
        ),
        line(
          translate(locale, "overview.OptionsImpliedDistribution.within5"),
          (price) => price >= spot * 0.95 && price <= spot * 1.05,
          colors.cone,
        ),
        {
          ...line(
            translate(locale, "overview.OptionsImpliedDistribution.up5"),
            (price) => price >= spot * 1.05,
            colors.up,
          ),
          markLine: {
            symbol: "none",
            silent: true,
            data: [
              {
                xAxis: spot,
                lineStyle: { color: colors.text, width: 1 },
                label: {
                  formatter: translate(
                    locale,
                    "overview.OptionsImpliedDistribution.spot",
                    { number: number(spot, undefined, locale) },
                  ),
                  color: colors.text,
                  fontSize: 12,
                },
              },
              ...[target?.put, target?.call].flatMap((price, i) =>
                price == null
                  ? []
                  : [
                      {
                        xAxis: price,
                        lineStyle: {
                          color: i === 0 ? colors.down : colors.up,
                          type: "dashed" as const,
                          width: 1,
                        },
                        label: { show: false },
                      },
                    ],
              ),
            ],
          },
        },
      ],
      graphic: [
        {
          type: "text",
          left: 52,
          top: 8,
          style: {
            text: translate(locale, "overview.OptionsImpliedDistribution.down52", {
              percentage: percent(probability.p_dn5, undefined, locale),
            }),
            fill: colors.down,
            font: "600 12px Consolas",
          },
        },
        {
          type: "text",
          right: 26,
          top: 8,
          style: {
            text: translate(locale, "overview.OptionsImpliedDistribution.up52", {
              percentage: percent(probability.p_up5, undefined, locale),
            }),
            fill: colors.up,
            font: "600 12px Consolas",
          },
        },
      ],
    };
  }, [spot, probability, target, locale]);
  return (
    <section
      className="ob-panel ob-distribution"
      aria-label={translate(
        locale,
        "overview.OptionsImpliedDistribution.impliedPriceDistribution",
        { s: row?.s ?? translate(locale, "overview.OverviewPage.instrument") },
      )}
    >
      <div
        className="ob-controls"
        role="group"
        aria-label={translate(
          locale,
          "overview.OptionsImpliedDistribution.impliedDistributionHorizon",
        )}
      >
        {(["week", "month"] as const).map((win) => (
          <button
            type="button"
            key={win}
            aria-pressed={window === win}
            onClick={() => setWindow(win)}
          >
            {win === "week"
              ? translate(locale, "overview.OptionsImpliedDistribution.1Week")
              : translate(locale, "overview.OptionsOutlookTable.1Month")}
          </button>
        ))}
      </div>
      {spot && probability?.curve.length ? (
        <OverviewTerminalChart
          title={translate(
            locale,
            "overview.OptionsImpliedDistribution.impliedPriceDistribution2",
            { s: row?.s },
          )}
          summary={translate(
            locale,
            "overview.OptionsImpliedDistribution.expiresDaysProbabilityPer1PriceBin",
            { expiration: probability.expiration, dte: probability.dte },
          )}
          option={option}
          tableLabel={translate(
            locale,
            "overview.OptionsImpliedDistribution.viewProbabilityCurveAndFitSources",
          )}
        >
          <p>
            {translate(
              locale,
              "overview.OptionsImpliedDistribution.cubicIVSmileFitFitPoints",
            )}{" "}
            {probability.fit_n} ; RMSE {percent(probability.fit_rmse, 2, locale)} ;{" "}
            {probability.formula_version}
          </p>
          <table>
            <thead>
              <tr>
                <th>
                  {translate(locale, "overview.OptionsImpliedDistribution.strike")}
                </th>
                <th>
                  {translate(
                    locale,
                    "overview.OptionsImpliedDistribution.probabilityAboveThisPrice",
                  )}
                </th>
                <th>
                  {translate(locale, "overview.OptionsImpliedDistribution.fittedIV")}
                </th>
              </tr>
            </thead>
            <tbody>
              {probability.curve.map((point) => (
                <tr key={point.strike}>
                  <td>{number(point.strike, undefined, locale)}</td>
                  <td>{percent(point.p_above, undefined, locale)}</td>
                  <td>{percent(point.sigma, undefined, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </OverviewTerminalChart>
      ) : (
        <div className="ob-empty">
          <h3>
            {row?.s ?? translate(locale, "overview.OverviewPage.instrument")}{" "}
            {translate(
              locale,
              "overview.OptionsImpliedDistribution.impliedPriceDistribution3",
            )}
          </h3>
          <p>
            {translate(
              locale,
              "overview.OptionsImpliedDistribution.insufficientEligibleVolatilitySmilePointsForThisHorizonImplied",
            )}
          </p>
        </div>
      )}
      <div className="ob-dist-stats">
        <div>
          <span>
            {translate(locale, "overview.OptionsImpliedDistribution.80Interval")}
          </span>
          <b>
            {number(probability?.lo80, 1, locale)}–
            {number(probability?.hi80, 1, locale)}
          </b>
        </div>
        <div>
          <span>
            {translate(locale, "overview.OptionsImpliedDistribution.downGt5DownGt10")}
          </span>
          <b className="ob-down">
            {percent(probability?.p_dn5, undefined, locale)} ;{" "}
            {percent(probability?.p_dn10, undefined, locale)}
          </b>
        </div>
        <div>
          <span>
            {translate(locale, "overview.OptionsImpliedDistribution.upGt5UpGt10")}
          </span>
          <b className="ob-up">
            {percent(probability?.p_up5, undefined, locale)} ;{" "}
            {percent(probability?.p_up10, undefined, locale)}
          </b>
        </div>
        <div>
          <span>
            {translate(
              locale,
              "overview.OptionsImpliedDistribution.premiumProtectionTargetDashed",
            )}
          </span>
          <b>
            <span className="ob-down">{number(target?.put, 1, locale)}</span> /{" "}
            <span className="ob-up">{number(target?.call, 1, locale)}</span>
          </b>
        </div>
      </div>
      <p className="ob-note">
        {translate(
          locale,
          "overview.OptionsImpliedDistribution.riskNeutralProbabilitiesNotForecastsGridLimitedToSpot",
        )}
        {(probability?.fit_rmse ?? 0) > 0.03 && (
          <span className="ob-warning">
            {translate(locale, "overview.OptionsImpliedDistribution.largeFitError")}
          </span>
        )}
      </p>
    </section>
  );
}
