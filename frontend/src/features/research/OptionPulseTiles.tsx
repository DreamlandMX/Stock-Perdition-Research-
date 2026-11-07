import { ResearchErrorDetail } from "./ResearchErrorDetail";
import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
import {
  optionNumber,
  optionPercent,
  optionPoints,
  optionReasonLabel,
} from "../options/option-labels";
type Analytics = components["schemas"]["OptionAnalyticsReadModel"];
export function OptionPulseTiles({
  data,
  pending,
  error,
}: {
  data?: Analytics | null | undefined;
  pending: boolean;
  error?: string | undefined;
}) {
  const { locale } = useI18n();
  const values = data?.values ?? {};
  const move = data?.implied_moves?.find((row) => row.horizon_days === 5);
  const tiles = [
    {
      label: "ATM IV30",
      key: "atm_iv_30d",
      value: optionPercent(values.atm_iv_30d, undefined, locale),
      note: translate(
        locale,
        "research.optionpulsetiles.annualizedImpliedVolatilityForTheNext30CalendarDays",
      ),
      change: values.atm_iv_change,
    },
    {
      label: "IV−RV20",
      key: "iv_minus_realized_20sessions",
      value: optionPoints(values.iv_minus_realized_20sessions, locale),
      note: translate(
        locale,
        "research.optionpulsetiles.differenceFromRealizedVolatilityOverTheLast20Trading",
      ),
    },
    {
      label: translate(locale, "research.optionpulsetiles.25Skew"),
      key: "put_call_skew_25delta",
      value: optionPoints(values.put_call_skew_25delta, locale),
      note: translate(
        locale,
        "research.optionpulsetiles.putIvCallIvDownsideProtectionPricing",
      ),
    },
    {
      label: translate(locale, "research.optionpulsetiles.termSlope"),
      key: "iv_term_slope_per_day",
      value:
        values.iv_term_slope_per_day == null
          ? "—"
          : translate(locale, "research.optionpulsetiles.ppDay", {
              ivTermSlopePerDay: (values.iv_term_slope_per_day * 100).toFixed(4),
            }),
      note: translate(
        locale,
        "research.optionpulsetiles.changeInAnnualizedIvPerCalendarDayToExpiry",
      ),
    },
    {
      label: "P/C OI",
      key: "put_call_oi",
      value: optionNumber(values.put_call_oi, locale),
      note: translate(locale, "research.optionpulsetiles.putCallOpenContracts"),
    },
    {
      label: translate(locale, "research.optionpulsetiles.pCVolume"),
      key: "put_call_volume",
      value: optionNumber(values.put_call_volume, locale),
      note: translate(locale, "research.optionpulsetiles.putCallCumulativeVolumeToday"),
    },
    {
      label: translate(locale, "research.optionpulsetiles.5DayImpliedInterval"),
      key: "implied_move",
      value:
        move?.upper_pct == null
          ? "—"
          : `±${optionPercent(move.upper_pct, undefined, locale)}`,
      note: translate(
        locale,
        "research.optionpulsetiles.80SymmetricIntervalRiskNeutralPricing",
      ),
    },
  ];
  return (
    <>
      {pending && (
        <p role="status">
          {translate(locale, "research.optionpulsetiles.loadingOptionsPulse")}
        </p>
      )}
      {error && (
        <div role="alert">
          {translate(locale, "research.optionpulsetiles.unableToLoadOptionsMetrics")}
          <ResearchErrorDetail message={error} />{locale === "en-US" ? "." : "。"}
        </div>
      )}
      {data && data.source_snapshot_ids.length < 2 && (
        <p className="options-notice">
          {translate(
            locale,
            "research.optionpulsetiles.changesRequireAtLeast2ComparableSnapshotsIvHistorical",
          )}
        </p>
      )}
      <div className="option-pulse-tiles">
        {tiles.map((tile) => (
          <article
            key={tile.key}
            title={(
              data?.missing_reasons[tile.key] ??
              (tile.key === "implied_move" && move?.reason ? [move.reason] : [])
            )
              .map((reason) => optionReasonLabel(reason, locale))
              .join(locale === "en-US" ? "; " : "；")}
          >
            <span>{tile.label}</span>
            <strong>{tile.value}</strong>
            <p>{tile.note}</p>
            {tile.change != null && (
              <small>
                {translate(
                  locale,
                  "research.optionpulsetiles.vsPreviousComparable.withValues",
                  {
                    value: tile.change > 0 ? "↑" : tile.change < 0 ? "↓" : "→",
                    optionPoints: optionPoints(tile.change, locale),
                  },
                )}
              </small>
            )}
          </article>
        ))}
      </div>
    </>
  );
}
