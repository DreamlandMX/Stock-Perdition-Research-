import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { formatUsMarketTime } from "../../app/presentation";
import {
  optionNumber,
  optionPercent,
  optionPoints,
  optionReasonLabel,
} from "../options/option-labels";
import {
  OPTION_COLUMNS,
  optionScreenValue,
  type OptionOverviewItem,
  type OptionSortKey,
  type ScreenOptionState,
} from "./option-screen-state";
export interface ScreenOptionColumnsProps {
  optionItems: readonly OptionOverviewItem[];
  optionState: ScreenOptionState;
  onOptionSort: (key: OptionSortKey, direction: "asc" | "desc") => void;
}
export function OptionColumnHeader({
  column,
  state,
  onSort,
}: {
  column: (typeof OPTION_COLUMNS)[number];
  state: ScreenOptionState;
  onSort: ScreenOptionColumnsProps["onOptionSort"];
}) {
  const { t } = useI18n();
  const active = state.sort === column[0];
  const Icon = active ? (state.direction === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
  return (
    <button
      type="button"
      onClick={() =>
        onSort(column[0], active && state.direction === "desc" ? "asc" : "desc")
      }
    >
      {t(column[1])} <Icon aria-hidden="true" size={15} />
    </button>
  );
}
export function OptionColumnValue({
  item,
  column,
  available = true,
}: {
  item: OptionOverviewItem | undefined;
  column: OptionSortKey;
  available?: boolean;
}) {
  const { locale } = useI18n();
  if (!available)
    return (
      <span className="screen-option-missing">
        {translate(locale, "screen.screenoptioncolumns.batchOptionsDataUnavailable")}
      </span>
    );
  if (
    !item ||
    item.data_status === "unsupported" ||
    item.reasons.includes("symbol_outside_option_scope")
  )
    return (
      <span className="screen-option-missing">
        {translate(locale, "screen.screenoptioncolumns.outsideOptionsCoverage")}
      </span>
    );
  if (item.maturity === "none")
    return (
      <span
        className="screen-option-missing"
        title={item.reasons
          .map((reason) => optionReasonLabel(reason, locale))
          .join(locale === "en-US" ? "; " : "；")}
      >
        {translate(locale, "screen.screenoptioncolumns.noCompleteOptionsSnapshot")}
      </span>
    );
  const value = optionScreenValue(item, column);
  const move = item.implied_moves.find((entry) => entry.horizon_days === 5);
  const reason =
    column === "implied_5d"
      ? move?.reason
      : column === "width_ratio"
        ? item.model_horizons
            .find((entry) => entry.horizon_days === 5)
            ?.reasons.join(locale === "en-US" ? "; " : "；")
        : item.analytics?.missing_reasons[column]
            ?.map((reason) => optionReasonLabel(reason, locale))
            .join(locale === "en-US" ? "; " : "；");
  const maturity =
    item.maturity === "single"
      ? translate(
          locale,
          "screen.screenoptioncolumns.atLeast2SnapshotsAreRequiredToCompareChanges",
        )
      : translate(locale, "screen.screenoptioncolumns.collectedTradingDays", {
          historyDays:
            item.history_days ??
            translate(locale, "screen.screenoptioncolumns.unconfirmed"),
        });
  const title = [
    item.market_as_of
      ? formatUsMarketTime(item.market_as_of, locale)
      : translate(locale, "screen.screenoptioncolumns.snapshotTimeUnconfirmed"),
    maturity,
    reason,
    column === "implied_5d"
      ? translate(
          locale,
          "screen.screenoptioncolumns.80ImpliedIntervalRiskNeutralPricingSymmetricAssumptionNot",
        )
      : "",
  ]
    .filter(Boolean)
    .join("; ");
  const divergent =
    value !== null &&
    ((column === "width_ratio" && (value < 0.7 || value > 1.3)) ||
      column === "iv_minus_realized_20sessions");
  return (
    <span className={divergent ? "screen-option-deviation" : undefined} title={title}>
      {value === null
        ? "—"
        : column === "implied_5d"
          ? `${optionPercent(move?.lower_pct, undefined, locale)} ~ ${optionPercent(value, undefined, locale)}`
          : column === "atm_iv_30d"
            ? optionPercent(value, undefined, locale)
            : column === "iv_minus_realized_20sessions" ||
                column === "put_call_skew_25delta"
              ? optionPoints(value, locale)
              : optionNumber(value, locale)}
    </span>
  );
}
export function ScreenOptionHeaders({
  optionState,
  onOptionSort,
}: Pick<ScreenOptionColumnsProps, "optionState" | "onOptionSort">) {
  return OPTION_COLUMNS.map((column) => (
    <th
      key={column[0]}
      scope="col"
      aria-sort={
        optionState.sort === column[0]
          ? optionState.direction === "asc"
            ? "ascending"
            : "descending"
          : "none"
      }
    >
      <OptionColumnHeader column={column} state={optionState} onSort={onOptionSort} />
    </th>
  ));
}
export function ScreenOptionCells({
  symbol,
  optionItems,
}: {
  symbol: string;
  optionItems: readonly OptionOverviewItem[];
}) {
  const item = optionItems.find((entry) => entry.symbol === symbol);
  return OPTION_COLUMNS.map(([column]) => (
    <td key={column}>
      <OptionColumnValue
        item={item}
        column={column}
        available={optionItems.length > 0}
      />
    </td>
  ));
}
