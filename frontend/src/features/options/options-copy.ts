import { translate, type Locale } from "../../i18n/core";
export const OPTION_COLOR = "#9a8fe7";
export const MODEL_COLOR = "#3ecf8e";
export const impliedCaveat = (locale: Locale = "zh-CN") =>
  translate(
    locale,
    "options.optionsCopy.optionsImpliedIntervalsReflectRiskNeutralPricingNotDirection",
  );
export const IMPLIED_CAVEAT = impliedCaveat();

export const getOptionsCopy = (locale: Locale = "zh-CN") =>
  ({
    heatmap: [
      translate(
        locale,
        "options.optionsCopy.observedOptionsChainMetricsByExpiryStrike",
      ),
      translate(
        locale,
        "options.optionsCopy.columnsAreExpiriesAndRowsAreStrikesEachCell",
      ),
      translate(
        locale,
        "options.optionsCopy.oIAndGammaConcentrationsAreNotPriceTargetsAnd",
      ),
    ],
    term: [
      translate(
        locale,
        "options.optionsCopy.annualizedIVOfNearATMContractsAcrossExpiries",
      ),
      translate(
        locale,
        "options.optionsCopy.referenceLinesMark1520TradingDaysConverted",
      ),
      translate(
        locale,
        "options.optionsCopy.anInvertedCurveIndicatesAPricingStructureChangeIt",
      ),
    ],
    skew: [
      translate(locale, "options.optionsCopy.differenceBetween25PutIVAndCallIVAt"),
      translate(
        locale,
        "options.optionsCopy.higherValuesIndicateMoreExpensiveDownsideProtectionDashedLines",
      ),
      translate(
        locale,
        "options.optionsCopy.skewIsNotADownsideProbabilityBlankWhenDelta",
      ),
    ],
    implied: [
      translate(
        locale,
        "options.optionsCopy.the1ImpliedIntervalSavedBeforeThePreviousSession",
      ),
      translate(
        locale,
        "options.optionsCopy.theGrayIntervalRepresentsAbout68ColoredBarsShow",
      ),
      impliedCaveat(locale),
    ],
    divergence: [
      translate(locale, "options.optionsCopy.widthsOfModelQ10Q90AndOptions80Intervals"),
      translate(
        locale,
        "options.optionsCopy.pointsAboveTheDiagonalIndicateGreaterMarketPricedVolatility",
      ),
      translate(
        locale,
        "options.optionsCopy.widthDifferencesMeasureRiskDisagreementAndAreNotBuy",
      ),
    ],
    coverage: [
      translate(
        locale,
        "options.optionsCopy.historicalIntervalCoverageForTheSameMatureSampleCohort",
      ),
      translate(
        locale,
        "options.optionsCopy.bothCompare80IntervalsDashedLinesMarkNominalCoverage",
      ),
      translate(
        locale,
        "options.optionsCopy.theVolatilityRiskPremiumMayIncreaseOptionsCoverageThis",
      ),
    ],
    cones: [
      translate(
        locale,
        "options.optionsCopy.options80ImpliedIntervalsAndModelQ10Q90Intervals",
      ),
      translate(locale, "options.optionsCopy.modelsHaveOnly1520DayAnchorsConnected"),
      impliedCaveat(locale),
    ],
    history: [
      translate(
        locale,
        "options.optionsCopy.savedObservedMarketTimesAndContractConcentrations",
      ),
      translate(
        locale,
        "options.optionsCopy.onlyEligibleHistoricalSnapshotsAreShownMissingValuesRemain",
      ),
      translate(
        locale,
        "options.optionsCopy.historyIsNotFabricatedStrikeConcentrationDoesNotIndicate",
      ),
    ],
  }) as const;
export const optionsCopy = getOptionsCopy();
export type OptionChartKind = keyof typeof optionsCopy;
