import { translate, type Locale } from "../../i18n/core";
export const getOptionLabels = (locale: Locale = "zh-CN"): Record<string, string> => ({
  option_experiments_unavailable: translate(
    locale,
    "options.optionLabels.noSavedOptionsAblationReportAvailable",
  ),
  automatic_collection_not_detected: translate(
    locale,
    "options.optionLabels.noAutomaticCollectionRecordsDetected",
  ),
  scheduler_runtime_unverified: translate(
    locale,
    "options.optionLabels.schedulerRuntimeUnverified",
  ),
  stock_snapshot_unavailable: translate(
    locale,
    "options.optionLabels.stockSnapshotUnavailable",
  ),
  stock_snapshot_invalid: translate(
    locale,
    "options.optionLabels.stockHistoryCannotCurrentlyBeVerified",
  ),
  stock_snapshot_after_option_cutoff: translate(
    locale,
    "options.optionLabels.stockSnapshotIsAfterTheOptionsCutoffRealizedVolatility",
  ),
  option_snapshot_unavailable: translate(
    locale,
    "options.optionLabels.noSavedOptionsSnapshotAtThisCutoff",
  ),
  snapshot_not_available: translate(
    locale,
    "options.optionLabels.snapshotUnavailableAtTheSelectedCutoff",
  ),
  market_time_unproven: translate(
    locale,
    "options.optionLabels.snapshotMarketTimeUnconfirmed",
  ),
  contracts_outside_verified_window: translate(
    locale,
    "options.optionLabels.someContractsOutsideTheVerifiedWindowWereExcluded",
  ),
  realized_vol_20_closes_unavailable: translate(
    locale,
    "options.optionLabels.21ConsecutiveEligibleClosesRequiredToCalculateRealizedVolatility",
  ),
  realized_vol_20_stock_date_mismatch: translate(
    locale,
    "options.optionLabels.stockHistoryEndDateDiffersFromTheOptionsMarket",
  ),
  premium_quotes_or_volume_missing: translate(
    locale,
    "options.optionLabels.someQuotesOrVolumesAreMissingRelatedAmountsExcluded",
  ),
  premium_numeric_overflow: translate(
    locale,
    "options.optionLabels.someContractAmountsCouldNotBeCalculatedAndWere",
  ),
  board_numeric_overflow: translate(
    locale,
    "options.optionLabels.boardAmountsCannotBeVerifiedAndAreHidden",
  ),
  atm_iv_30d_unavailable: translate(
    locale,
    "options.optionLabels.30DayATMIVUnavailable",
  ),
  skew_30_iv_not_bracketed: translate(
    locale,
    "options.optionLabels.oTMPutOrCallLacksBracketingIVSkewUnavailable",
  ),
  "prob_week:expiration_unavailable": translate(
    locale,
    "options.optionLabels.1WeekProbabilitiesLackExpiringContracts",
  ),
  "prob_month:expiration_unavailable": translate(
    locale,
    "options.optionLabels.1MonthProbabilitiesLackExpiringContracts",
  ),
  "prob_week:insufficient_smile_points": translate(
    locale,
    "options.optionLabels.1WeekProbabilitiesLackEligibleSmileFitPoints",
  ),
  "prob_month:insufficient_smile_points": translate(
    locale,
    "options.optionLabels.1MonthProbabilitiesLackEligibleSmileFitPoints",
  ),
  "prob_week:smile_fit_failed": translate(
    locale,
    "options.optionLabels.1WeekProbabilityFitFailedHidden",
  ),
  "prob_month:smile_fit_failed": translate(
    locale,
    "options.optionLabels.1MonthProbabilityFitFailedHidden",
  ),
  "prob_week:probability_interval_outside_grid": translate(
    locale,
    "options.optionLabels.1WeekIntervalIsOutsideThe20GridNo",
  ),
  "prob_month:probability_interval_outside_grid": translate(
    locale,
    "options.optionLabels.1MonthIntervalIsOutsideThe20GridNo",
  ),
  term_structure_inverted: translate(
    locale,
    "options.optionLabels.invertedTermStructure",
  ),
  iv_minus_realized_extreme: translate(
    locale,
    "options.optionLabels.excessiveIVRealizedVolatilityGap",
  ),
  width_ratio_1d_extreme: translate(
    locale,
    "options.optionLabels.1DayModelMarketIntervalWidthDivergence",
  ),
  width_ratio_5d_extreme: translate(
    locale,
    "options.optionLabels.5DayModelMarketIntervalWidthDivergence",
  ),
  width_ratio_20d_extreme: translate(
    locale,
    "options.optionLabels.20DayModelMarketIntervalWidthDivergence",
  ),
  model_option_cutoff_mismatch: translate(
    locale,
    "options.optionLabels.modelAndOptionsCutoffsDifferComparisonUnavailable",
  ),
  previous_session_implied_pair_unavailable: translate(
    locale,
    "options.optionLabels.noEligiblePreviousSessionSnapshotPairedWithRealizedReturn",
  ),
  history_requires_two_snapshots: translate(
    locale,
    "options.optionLabels.atLeast2SnapshotsRequired",
  ),
  history_requires_twenty_sessions: translate(
    locale,
    "options.optionLabels.atLeast20TradingDaysOfHistoryRequired",
  ),
  iv_horizon_not_bracketed: translate(
    locale,
    "options.optionLabels.expiriesDoNotBracketThisHorizonNoExtrapolation",
  ),
  verified_calendar_unavailable: translate(
    locale,
    "options.optionLabels.verifiedTradingCalendarUnavailable",
  ),
  automatic_collection_not_observed: translate(
    locale,
    "options.optionLabels.noAutomaticCollectionRecordsYet",
  ),
  mature_paired_samples_insufficient: translate(
    locale,
    "options.optionLabels.insufficientMaturePairedSamples",
  ),
  no_option_contracts: translate(
    locale,
    "options.OptionRefreshControl.noEligibleOptionsContractsFound",
  ),
  permission_denied: translate(
    locale,
    "options.optionLabels.insufficientOptionsMarketDataPermissions",
  ),
  connection_failed: translate(
    locale,
    "options.OptionRefreshControl.openDConnectionFailed",
  ),
  rate_limited: translate(locale, "options.OptionRefreshControl.providerRateLimit"),
  no_option_snapshot: translate(
    locale,
    "options.optionsBoardRows.noSavedOptionsSnapshot",
  ),
  no_complete_snapshot_available: translate(
    locale,
    "options.optionLabels.noCompleteOptionsSnapshot",
  ),
  symbol_outside_option_scope: translate(
    locale,
    "options.optionLabels.outsideOptionsCoverage",
  ),
  metric_unavailable: translate(
    locale,
    "options.optionLabels.eligibleDataIsUnavailableForThisMetric",
  ),
  sample_only_not_full_chain: translate(
    locale,
    "options.optionLabels.sampleOnlyNotACompleteOptionsChain",
  ),
  oi_date_unproven: translate(locale, "options.optionLabels.oIDateUnconfirmed"),
  greeks_timestamp_unproven: translate(
    locale,
    "options.optionLabels.greeksCalculationTimeUnconfirmed",
  ),
  quote_timestamp_unproven: translate(
    locale,
    "options.optionLabels.bidAskQuoteTimeUnconfirmed",
  ),
  nonstandard_or_unknown_delivery: translate(
    locale,
    "options.optionLabels.nonstandardOrUnknownDeliverable",
  ),
  option_fields_unavailable: translate(
    locale,
    "options.optionLabels.optionsFieldsUnavailable",
  ),
  observed_time_missing_or_invalid: translate(
    locale,
    "options.optionLabels.quoteTimeMissingOrInvalid",
  ),
  crossed_quote: translate(locale, "options.optionLabels.crossedQuotesExcluded"),
  zero_or_one_sided_quote: translate(
    locale,
    "options.optionLabels.zeroBidOrOneSidedQuote",
  ),
  underlying_price_missing_range_unfiltered: translate(
    locale,
    "options.optionLabels.underlyingPriceMissingStrikesCannotBeFiltered",
  ),
  partial_response: translate(
    locale,
    "options.optionLabels.incompleteProviderResponse",
  ),
  sdk_error: translate(locale, "options.optionLabels.providerRequestFailed"),
  future_market_time: translate(
    locale,
    "options.optionLabels.futureMarketTimesExcluded",
  ),
  completed: translate(locale, "options.optionLabels.completedInFull"),
  complete: translate(locale, "options.optionLabels.complete"),
  partial: translate(locale, "options.optionLabels.partialCoverage"),
  failed: translate(locale, "options.optionLabels.failed"),
  skipped: translate(locale, "options.optionLabels.skipped"),
  unavailable: translate(locale, "options.optionLabels.unavailable"),
  not_trading_day: translate(locale, "options.optionLabels.nonTradingDay"),
  non_trading_day: translate(locale, "options.optionLabels.nonTradingDay"),
  half_day_skipped: translate(locale, "options.optionLabels.halfDaySessionSkipped"),
  opend_busy: translate(locale, "options.optionLabels.openDBusy"),
  collector_busy: translate(locale, "options.optionLabels.collectorBusy"),
  snapshot_writer_busy: translate(locale, "options.optionLabels.snapshotWriterBusy"),
  before_market_open: translate(locale, "options.optionLabels.beforeMarketOpen"),
  after_market_close: translate(
    locale,
    "options.optionLabels.marketClosedNoCatchUpCollection",
  ),
  calendar_invalid: translate(locale, "options.optionLabels.tradingDayEvidenceInvalid"),
  calendar_unavailable: translate(
    locale,
    "options.optionLabels.tradingDayEvidenceUnavailable",
  ),
  collection_record_invalid: translate(
    locale,
    "options.optionLabels.collectionRecordCorrupted",
  ),
  incomplete_snapshots: translate(
    locale,
    "options.optionLabels.notAllSnapshotsComplete",
  ),
  snapshot_artifacts_unreadable: translate(
    locale,
    "options.optionLabels.snapshotUnreadable",
  ),
  term_horizon_not_bracketed: translate(
    locale,
    "options.optionLabels.expiriesDoNotBracketThisHorizonNoExtrapolation",
  ),
  iv_term_not_bracketed: translate(
    locale,
    "options.optionLabels.expiriesDoNotBracketThisHorizonNoExtrapolation",
  ),
  underlying_price_unavailable: translate(
    locale,
    "options.optionLabels.underlyingPriceUnavailable",
  ),
  stock_forecast_unavailable: translate(locale, "options.optionLabels.noModelForecast"),
  model_refused: translate(locale, "options.optionLabels.modelAbstained"),
  previous_session_snapshot_missing: translate(
    locale,
    "options.optionLabels.previousSessionSnapshotRequired",
  ),
  mature_samples_missing: translate(
    locale,
    "options.optionLabels.insufficientMatureSamples",
  ),
  scheduler_unconfirmed: translate(
    locale,
    "options.optionLabels.noAutomaticCollectionRecordsDetectedSchedulerMayNeedRestarting",
  ),
  incomplete_snapshot: translate(
    locale,
    "options.optionLabels.incompleteSnapshotBatch",
  ),
  delta_units_or_25delta_legs_missing: translate(
    locale,
    "options.optionLabels.deltaUnitsOrEligible25ContractsMissing",
  ),
  historical_returns_or_annualization_unproven: translate(
    locale,
    "options.optionLabels.historicalReturnsOrAnnualizationRulesUnverified",
  ),
  comparable_history_missing: translate(
    locale,
    "options.optionLabels.atLeast2ComparableSnapshotsRequired",
  ),
  field_time_unit_or_coverage_unproven: translate(
    locale,
    "options.optionLabels.fieldTimingUnitsOrCoverageUnverified",
  ),
  term_inverted: translate(locale, "options.optionLabels.invertedTermStructure"),
  skew_increase: translate(locale, "options.optionLabels.25SkewRoseSignificantly"),
  iv_rv_premium: translate(locale, "options.optionLabels.iVAboveRealizedVolatility"),
  width_ratio_extreme: translate(
    locale,
    "options.optionLabels.modelMarketIntervalWidthDivergence",
  ),
});
export const optionLabels: Record<string, string> = getOptionLabels();
export const optionReasonLabel = (code: string, locale: Locale = "zh-CN") =>
  getOptionLabels(locale)[code] ??
  `${translate(locale, "options.unknownReason")} (${code})`;
export const optionPercent = (
  v: number | null | undefined,
  digits = 2,
  locale: Locale = "zh-CN",
) =>
  v == null
    ? "—"
    : new Intl.NumberFormat(locale, {
        style: "percent",
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }).format(v);
export const optionPoints = (v: number | null | undefined, locale: Locale = "zh-CN") =>
  v == null
    ? "—"
    : `${v > 0 ? "+" : ""}${new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v * 100)} ${translate(locale, "options.percentagePointUnit")}`;
export const optionNumber = (v: number | null | undefined, locale: Locale = "zh-CN") =>
  v == null ? "—" : v.toLocaleString(locale, { maximumFractionDigits: 2 });
