import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { formatUsMarketTime } from "../../app/presentation";
import {
  optionNumber,
  optionPercent,
  optionPoints,
  optionReasonLabel,
} from "../options/option-labels";
import type { OptionOverviewItem } from "../options/useOptionsOverview";
import {
  buildMarketResearchHref,
  type MarketLinkState,
  type MarketPoint,
} from "./overview-terminal";

const getColumns = (locale: Locale = "zh-CN") =>
  [
    ["symbol", translate(locale, "overview.OverviewPage.instrument")],
    ["atm_iv_30d", "ATM IV30"],
    ["iv_minus_realized_20sessions", "IV−RV20"],
    ["put_call_skew_25delta", translate(locale, "overview.OptionsPulseTable.25Skew")],
    ["term", translate(locale, "overview.OptionsPulseTable.termStructure")],
    ["put_call_oi", "P/C OI"],
    ["implied", translate(locale, "overview.OptionsPulseTable.5DayImplied80")],
    ["model", translate(locale, "overview.OptionsPulseTable.model5DayQ10Q90")],
    ["ratio", translate(locale, "overview.OptionsPulseTable.widthRatio")],
    ["data", translate(locale, "overview.OptionsPulseTable.data")],
  ] as const;
type SortKey = ReturnType<typeof getColumns>[number][0] | "divergence";
const getTermLabels = (locale: Locale = "zh-CN") => ({
  upward: translate(locale, "options.optionsBoardRows.upward"),
  flat: translate(locale, "options.optionsBoardRows.flat"),
  inverted: translate(locale, "options.optionsBoardRows.inverted"),
  unavailable: translate(locale, "options.OptionRefreshControl.unconfirmed"),
});

function sortValue(item: OptionOverviewItem, key: SortKey): number | string | null {
  const model = item.model_horizons.find((h) => h.horizon_days === 5);
  if (key === "symbol") return item.symbol;
  if (key === "term") return item.analytics?.values.iv_term_slope_per_day ?? null;
  if (key === "data") return item.market_as_of ?? null;
  if (key === "ratio") return model?.width_ratio ?? null;
  if (key === "divergence")
    return model?.width_ratio == null ? null : Math.abs(model.width_ratio - 1);
  if (key === "model")
    return model?.q10 == null || model.q90 == null ? null : model.q90 - model.q10;
  if (key === "implied")
    return item.implied_moves.find((h) => h.horizon_days === 5)?.upper_pct ?? null;
  return item.analytics?.values[key] ?? null;
}

function MiniLine({ values }: { values: number[] }) {
  const { locale } = useI18n();
  if (values.length < 2) return null;
  const low = Math.min(...values),
    range = Math.max(...values) - low;
  const points = values
    .map(
      (value, i) =>
        `${(i * 64) / (values.length - 1)},${range ? 17 - ((value - low) / range) * 15 : 9}`,
    )
    .join(" ");
  return (
    <svg
      className="option-sparkline"
      viewBox="0 0 65 19"
      aria-label={translate(
        locale,
        "overview.OptionsPulseTable.aTMIVOverTheLast20AccumulatedTradingDays",
      )}
    >
      <polyline points={points} fill="none" stroke="#9a8fe7" strokeWidth="1.5" />
    </svg>
  );
}

export function OptionsPulseTable({
  items,
  points,
  linkState,
}: {
  items: OptionOverviewItem[];
  points: readonly MarketPoint[];
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const [sort, setSort] = useState<{ key: SortKey; ascending: boolean }>({
    key: "divergence",
    ascending: false,
  });
  const sorted = useMemo(
    () =>
      [...items].sort((left, right) => {
        const a = sortValue(left, sort.key),
          b = sortValue(right, sort.key);
        if (a == null) return b == null ? left.symbol.localeCompare(right.symbol) : 1;
        if (b == null) return -1;
        const value =
          typeof a === "number" && typeof b === "number"
            ? a - b
            : String(a).localeCompare(String(b));
        return (
          (sort.ascending ? value : -value) || left.symbol.localeCompare(right.symbol)
        );
      }),
    [items, sort],
  );
  return (
    <section
      className="ov-options-pulse"
      aria-label={translate(
        locale,
        "overview.OptionsPulseTable.10InstrumentOptionsPulse",
      )}
    >
      <header>
        <h3>
          {translate(locale, "overview.OptionsPulseTable.10InstrumentOptionsPulse")}
        </h3>
        <p>
          {translate(
            locale,
            "overview.OptionsPulseTable.defaultsToModelMarketIntervalDivergenceRankingIVIs",
          )}
        </p>
      </header>
      <div className="option-table-wrap">
        <table>
          <thead>
            <tr>
              {getColumns(locale).map(([key, label]) => (
                <th
                  key={key}
                  className={key === "data" ? "option-data-column" : undefined}
                  aria-sort={
                    sort.key === key
                      ? sort.ascending
                        ? "ascending"
                        : "descending"
                      : "none"
                  }
                >
                  <button
                    type="button"
                    onClick={() =>
                      setSort({
                        key,
                        ascending: sort.key === key ? !sort.ascending : false,
                      })
                    }
                  >
                    {label}
                    {sort.key === key ? (sort.ascending ? " ↑" : " ↓") : ""}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((item) => {
              const values = item.analytics?.values;
              const model = item.model_horizons.find((h) => h.horizon_days === 5);
              const implied = item.implied_moves.find((h) => h.horizon_days === 5);
              const actual = points.find(
                (p) => p.symbol === item.symbol,
              )?.session_return;
              const ratio = model?.width_ratio;
              const coverage = values?.contract_coverage_ratio;
              const dataText = translate(
                locale,
                "overview.OptionsPulseTable.coverageTradingDaysAccumulated",
                {
                  value: item.market_as_of
                    ? formatUsMarketTime(item.market_as_of, locale)
                    : translate(
                        locale,
                        "overview.OptionsPulseTable.noCompleteSnapshot",
                      ),
                  percentage: optionPercent(coverage, 0, locale),
                  value2:
                    item.data_status === "partial"
                      ? translate(locale, "overview.OptionsPulseTable.partialCoverage")
                      : "",
                  historyDays:
                    item.history_days ??
                    translate(locale, "options.OptionRefreshControl.unconfirmed"),
                },
              );
              return (
                <tr key={item.symbol}>
                  <td>
                    <Link
                      to={`${buildMarketResearchHref(item.symbol, linkState)}#options`}
                    >
                      {item.symbol.replace(/^US\./, "")}
                    </Link>
                    <br />
                    <small
                      className={
                        actual != null && actual < 0
                          ? "ov-tone--down"
                          : actual != null && actual > 0
                            ? "ov-tone--up"
                            : undefined
                      }
                    >
                      {optionPercent(actual, 1, locale)}
                    </small>
                  </td>
                  <td title={dataText}>
                    {optionPercent(values?.atm_iv_30d, undefined, locale)}
                    {(item.history_days ?? 0) >= 20 ? (
                      <MiniLine
                        values={item.iv_history
                          .slice(-20)
                          .flatMap((p) => (p.atm_iv == null ? [] : [p.atm_iv]))}
                      />
                    ) : (
                      <small className="option-history-days">
                        {" "}
                        ;{" "}
                        {item.history_days ??
                          translate(
                            locale,
                            "options.OptionRefreshControl.unconfirmed",
                          )}{" "}
                        {translate(locale, "overview.OptionsPulseTable.days")}
                      </small>
                    )}
                  </td>
                  <td>
                    <span
                      className={
                        values?.iv_minus_realized_20sessions != null
                          ? "option-deviation"
                          : undefined
                      }
                    >
                      {optionPoints(values?.iv_minus_realized_20sessions, locale)}
                    </span>
                  </td>
                  <td>{optionPoints(values?.put_call_skew_25delta, locale)}</td>
                  <td
                    title={translate(
                      locale,
                      "overview.OptionsPulseTable.slopeThresholdCalendarDay",
                      { termSlopeThreshold: item.term_slope_threshold },
                    )}
                  >
                    {item.term_structure
                      ? getTermLabels(locale)[item.term_structure]
                      : translate(locale, "options.OptionRefreshControl.unconfirmed")}
                  </td>
                  <td>
                    <span
                      className={
                        values?.put_call_oi != null &&
                        (values.put_call_oi < 0.5 || values.put_call_oi > 2)
                          ? "option-deviation"
                          : undefined
                      }
                    >
                      {optionNumber(values?.put_call_oi, locale)}
                    </span>
                  </td>
                  <td
                    title={
                      implied?.reason
                        ? optionReasonLabel(implied.reason, locale)
                        : translate(
                            locale,
                            "overview.OptionsPulseTable.symmetric80Interval",
                          )
                    }
                  >
                    {implied?.upper_pct == null
                      ? "—"
                      : `±${optionPercent(implied.upper_pct, undefined, locale)}`}
                  </td>
                  <td
                    title={model?.reasons
                      .map((argument0) => optionReasonLabel(argument0, locale))
                      .join(";")}
                  >
                    {model?.q10 == null || model.q90 == null
                      ? translate(locale, "options.optionLabels.noModelForecast")
                      : `${optionPercent(model.q10, undefined, locale)} ~ ${optionPercent(model.q90, undefined, locale)}`}
                  </td>
                  <td
                    title={translate(
                      locale,
                      "overview.OptionsPulseTable.modelImplied80IntervalWidth13MeansThe",
                    )}
                  >
                    <span
                      className={
                        ratio != null && (ratio < 0.7 || ratio > 1.3)
                          ? "option-deviation"
                          : undefined
                      }
                    >
                      {optionNumber(ratio, locale)}
                    </span>
                  </td>
                  <td className="option-data-column" title={dataText}>
                    {item.market_as_of
                      ? formatUsMarketTime(item.market_as_of, locale)
                      : translate(
                          locale,
                          "overview.OptionsPulseTable.noCompleteSnapshot",
                        )}
                    <br />
                    {optionPercent(coverage, 0, locale)} ;{" "}
                    {item.data_status === "partial"
                      ? translate(locale, "options.optionLabels.partialCoverage")
                      : item.maturity === "none"
                        ? translate(locale, "overview.OverviewPulse.noData")
                        : translate(locale, "overview.OptionsPulseTable.saved")}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {items.every((item) => item.maturity === "none") && (
        <p className="option-empty">
          {translate(
            locale,
            "overview.OptionsPulseTable.noCompleteSavedOptionsSnapshotMetricsAndHistoryAre",
          )}
        </p>
      )}
    </section>
  );
}
