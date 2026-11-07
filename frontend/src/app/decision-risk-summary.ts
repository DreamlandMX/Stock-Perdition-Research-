import { translate, type Locale } from "../i18n/core";
const FLAT_RETURN_TOLERANCE = 0.0005;
const SKEW_TOLERANCE = 0.005;
const BREAKOUT_SKEW_TOLERANCE = 0.05;
const RETURN_CONSISTENCY_TOLERANCE = 0.001;
export const Q10_RESEARCH_DISCLAIMER = translate("zh-CN", "common.shared.q10IsThe10thPercentileOfTheModel");
export const q10ResearchDisclaimer = (locale: Locale = "zh-CN") => translate(locale, "common.shared.q10IsThe10thPercentileOfTheModel");
export interface DecisionRiskSummaryInput {
    breakoutDownProbability?: number | null;
    breakoutUpProbability?: number | null;
    maeReturn?: number | null;
    mfeReturn?: number | null;
    q10Price?: number | null;
    q10Return?: number | null;
    q50Price?: number | null;
    q50Return?: number | null;
    q90Price?: number | null;
    q90Return?: number | null;
    referencePrice?: number | null;
}
export interface DecisionRiskSummary {
    availability: "available" | "invalid" | "partial" | "unavailable";
    breakoutSkew: "balanced" | "downside" | "unknown" | "upside";
    evidence: string[];
    headline: string;
    intervalWidth: number | null;
    medianDirection: "down" | "flat" | "unknown" | "up";
    medianReturn: number | null;
    missingReason: string | null;
    q10Disclaimer: string;
    q10Return: number | null;
    q90Return: number | null;
    scenarioSkew: "balanced" | "downside" | "unknown" | "upside";
}
function finite(value: number | null | undefined) {
    return value == null || !Number.isFinite(value) ? null : value;
}
function priceReturn(price: number | null | undefined, referencePrice: number | null | undefined) {
    const normalizedPrice = finite(price);
    const normalizedReference = finite(referencePrice);
    if (normalizedPrice === null ||
        normalizedReference === null ||
        normalizedReference <= 0) {
        return null;
    }
    return normalizedPrice / normalizedReference - 1;
}
function resolvedReturn(value: number | null | undefined, price: number | null | undefined, referencePrice: number | null | undefined) {
    const suppliedReturn = finite(value);
    const derivedReturn = priceReturn(price, referencePrice);
    return {
        inconsistent: suppliedReturn !== null &&
            derivedReturn !== null &&
            Math.abs(suppliedReturn - derivedReturn) > RETURN_CONSISTENCY_TOLERANCE,
        value: derivedReturn ?? suppliedReturn,
    };
}
function signedPercentage(value: number) {
    const sign = value > 0 ? "+" : "";
    return `${sign}${(value * 100).toFixed(1)}%`;
}
function percentage(value: number) {
    return `${(value * 100).toFixed(1)}%`;
}
function suppliedButInvalid(value: number | null | undefined) {
    return value != null && !Number.isFinite(value);
}
function validProbability(value: number | null | undefined) {
    const normalized = finite(value);
    return normalized !== null && normalized >= 0 && normalized <= 1 ? normalized : null;
}
function scenarioMagnitude(value: number | null, direction: "down" | "up") {
    if (value === null)
        return null;
    return direction === "down" ? Math.max(0, -value) : Math.max(0, value);
}
function largest(values: readonly (number | null)[]) {
    const available = values.filter((value): value is number => value !== null);
    return available.length === 0 ? null : Math.max(...available);
}
function medianPresentation(value: number | null, locale: Locale = "zh-CN") {
    if (value === null) {
        return { direction: "unknown" as const, text: translate(locale, "common.shared.medianScenarioUnavailable") };
    }
    if (Math.abs(value) < FLAT_RETURN_TOLERANCE) {
        return {
            direction: "flat" as const,
            text: translate(locale, "common.shared.medianScenarioNearTheCurrentPrice", { value: String(signedPercentage(value)) }),
        };
    }
    return value > 0
        ? {
            direction: "up" as const,
            text: translate(locale, "common.shared.medianScenarioRises", { value: String(signedPercentage(value)) }),
        }
        : {
            direction: "down" as const,
            text: translate(locale, "common.shared.medianScenarioFalls", { value: String(signedPercentage(value)) }),
        };
}
export function buildDecisionRiskSummary(input: DecisionRiskSummaryInput, locale: Locale = "zh-CN"): DecisionRiskSummary {
    const q10Resolution = resolvedReturn(input.q10Return, input.q10Price, input.referencePrice);
    const medianResolution = resolvedReturn(input.q50Return, input.q50Price, input.referencePrice);
    const q90Resolution = resolvedReturn(input.q90Return, input.q90Price, input.referencePrice);
    const q10Return = q10Resolution.value;
    const medianReturn = medianResolution.value;
    const q90Return = q90Resolution.value;
    const mfeReturn = finite(input.mfeReturn);
    const maeReturn = finite(input.maeReturn);
    const breakoutUp = validProbability(input.breakoutUpProbability);
    const breakoutDown = validProbability(input.breakoutDownProbability);
    const invalidNumericInput = [
        input.breakoutDownProbability,
        input.breakoutUpProbability,
        input.maeReturn,
        input.mfeReturn,
        input.q10Price,
        input.q10Return,
        input.q50Price,
        input.q50Return,
        input.q90Price,
        input.q90Return,
        input.referencePrice,
    ].some(suppliedButInvalid);
    const invalidReference = input.referencePrice != null &&
        (!Number.isFinite(input.referencePrice) || input.referencePrice <= 0);
    const invalidProbability = (input.breakoutUpProbability != null && breakoutUp === null) ||
        (input.breakoutDownProbability != null && breakoutDown === null);
    const inconsistentQuantileSources = q10Resolution.inconsistent ||
        medianResolution.inconsistent ||
        q90Resolution.inconsistent;
    const invalidQuantileOrder = q10Return !== null &&
        medianReturn !== null &&
        q90Return !== null &&
        (q10Return > medianReturn || medianReturn > q90Return);
    const invalidInput = invalidNumericInput ||
        invalidReference ||
        invalidProbability ||
        inconsistentQuantileSources ||
        invalidQuantileOrder;
    const intervalWidth = q10Return === null || q90Return === null || q10Return > q90Return
        ? null
        : q90Return - q10Return;
    const median = medianPresentation(medianReturn, locale);
    const downsideMagnitude = largest([
        scenarioMagnitude(q10Return, "down"),
        scenarioMagnitude(maeReturn, "down"),
    ]);
    const upsideMagnitude = largest([
        scenarioMagnitude(q90Return, "up"),
        scenarioMagnitude(mfeReturn, "up"),
    ]);
    let scenarioSkew: DecisionRiskSummary["scenarioSkew"] = "unknown";
    if (downsideMagnitude !== null && upsideMagnitude !== null) {
        const difference = downsideMagnitude - upsideMagnitude;
        scenarioSkew =
            Math.abs(difference) < SKEW_TOLERANCE
                ? "balanced"
                : difference > 0
                    ? "downside"
                    : "upside";
    }
    let breakoutSkew: DecisionRiskSummary["breakoutSkew"] = "unknown";
    if (breakoutUp !== null && breakoutDown !== null) {
        const difference = breakoutDown - breakoutUp;
        breakoutSkew =
            Math.abs(difference) < BREAKOUT_SKEW_TOLERANCE
                ? "balanced"
                : difference > 0
                    ? "downside"
                    : "upside";
    }
    const evidence: string[] = [];
    if (q10Return !== null && q90Return !== null) {
        evidence.push(`q10 ${signedPercentage(q10Return)}, q90 ${signedPercentage(q90Return)}`);
    }
    else if (q10Return !== null) {
        evidence.push(`q10 ${signedPercentage(q10Return)}`);
    }
    if (mfeReturn !== null || maeReturn !== null) {
        evidence.push(translate(locale, "common.shared.interimFavorableAdverseScenarios", { mfeReturn: String(mfeReturn === null ? translate(locale, "common.shared.unavailable3") : signedPercentage(mfeReturn)), maeReturn: String(maeReturn === null ? translate(locale, "common.shared.unavailable3") : signedPercentage(maeReturn)) }));
    }
    if (breakoutUp !== null && breakoutDown !== null) {
        evidence.push(translate(locale, "common.shared.upwardDownwardBreakout", { breakoutUp: String(percentage(breakoutUp)), breakoutDown: String(percentage(breakoutDown)) }));
    }
    const hasAnyEvidence = medianReturn !== null || evidence.length > 0 || scenarioSkew !== "unknown";
    const availability: DecisionRiskSummary["availability"] = invalidInput
        ? "invalid"
        : !hasAnyEvidence
            ? "unavailable"
            : medianReturn !== null && q10Return !== null && q90Return !== null
                ? "available"
                : "partial";
    const skewText = breakoutSkew === "downside"
        ? translate(locale, "common.shared.butDownwardBreakoutIsMoreLikely") : breakoutSkew === "upside"
        ? translate(locale, "common.shared.andUpwardBreakoutIsMoreLikely") : breakoutSkew === "balanced"
        ? translate(locale, "common.shared.withSimilarUpwardAndDownwardBreakoutProbabilities") : scenarioSkew === "downside"
        ? translate(locale, "common.shared.butTheDownsideScenarioIsLarger") : scenarioSkew === "upside"
        ? translate(locale, "common.shared.andTheUpsideScenarioIsLarger") : scenarioSkew === "balanced"
        ? translate(locale, "common.shared.withSimilarUpsideAndDownsideScenarioMagnitudes") : translate(locale, "common.shared.withInsufficientSkewEvidence");
    const headline = availability === "invalid"
        ? translate(locale, "common.shared.quantilesOrProbabilitiesAreOutsideValidRangesA") : availability === "unavailable"
        ? translate(locale, "common.shared.insufficientEvidenceToGenerateARiskSummary") : `${median.text}${skewText}`;
    const missingReason = availability === "available"
        ? null
        : availability === "invalid"
            ? inconsistentQuantileSources
                ? translate(locale, "common.shared.returnQuantilesDisagreeWithPriceQuantilesDerivedFrom") : translate(locale, "common.shared.quantilesMustSatisfyQ10Q50Q90ProbabilitiesMust")
            : availability === "unavailable"
                ? translate(locale, "common.shared.comparableMedianQuantileIntervalAndBreakoutProbabilitiesAre") : q10Return === null || q90Return === null
                ? translate(locale, "common.shared.theClosingQuantileIntervalIsIncompleteTheSummary") : translate(locale, "common.shared.theMedianScenarioIsMissingTheSummaryUses");
    return {
        availability,
        breakoutSkew,
        evidence,
        headline,
        intervalWidth,
        medianDirection: median.direction,
        medianReturn,
        missingReason,
        q10Disclaimer: q10ResearchDisclaimer(locale),
        q10Return,
        q90Return,
        scenarioSkew,
    };
}
