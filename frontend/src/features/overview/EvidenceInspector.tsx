import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { Ban, Database, Gauge, ScanSearch } from "lucide-react";

import type { components } from "../../api/generated/schema";
import { ProbabilityValue } from "../../components/ProbabilityValue";
import { getRegimeLabels } from "./RegimeDistribution";

type OverviewReadModel = components["schemas"]["OverviewReadModel"];
type RejectionReason = components["schemas"]["RejectionReason"];

export const getRejectionReasonLabels = (locale: Locale = "zh-CN") =>
  ({
    corporate_action_unresolved: translate(
      locale,
      "overview.EvidenceInspector.unresolvedCorporateAction",
    ),
    data_quality: translate(
      locale,
      "overview.EvidenceInspector.insufficientDataQuality",
    ),
    feature_artifact_mismatch: translate(
      locale,
      "overview.EvidenceInspector.inconsistentFeatureArtifacts",
    ),
    insufficient_calibration: translate(
      locale,
      "overview.EvidenceInspector.insufficientCalibrationSamples",
    ),
    insufficient_similar_cases: translate(
      locale,
      "overview.EvidenceInspector.insufficientSimilarCases",
    ),
    interval_too_wide: translate(locale, "overview.EvidenceInspector.intervalTooWide"),
    model_disagreement: translate(
      locale,
      "overview.EvidenceInspector.excessiveModelDisagreement",
    ),
    no_champion_candidate: translate(
      locale,
      "overview.EvidenceInspector.noChampionModelAvailable",
    ),
    out_of_distribution: translate(
      locale,
      "overview.EvidenceInspector.outOfDistributionSample",
    ),
    poor_calibration: translate(
      locale,
      "overview.EvidenceInspector.insufficientCalibrationQuality",
    ),
    schema_mismatch: translate(
      locale,
      "overview.EvidenceInspector.inconsistentDataStructure",
    ),
    stale_data: translate(locale, "overview.EvidenceInspector.staleData"),
  }) satisfies Record<RejectionReason, string>;
export const REJECTION_REASON_LABELS = getRejectionReasonLabels();

export interface EvidenceInspectorProps {
  data: OverviewReadModel;
}

export function EvidenceInspector({ data }: EvidenceInspectorProps) {
  const { locale } = useI18n();
  return (
    <aside
      aria-label={translate(
        locale,
        "overview.EvidenceInspector.overviewEvidenceInspector",
      )}
      className="evidence-inspector"
    >
      <section>
        <header>
          <ScanSearch aria-hidden="true" size={18} strokeWidth={1.5} />
          <h2>
            {translate(locale, "overview.EvidenceInspector.currentMarketAssessment")}
          </h2>
        </header>
        <dl className="evidence-inspector__regimes">
          {data.regime_distribution.map((item) => (
            <div key={item.regime}>
              <dt>{getRegimeLabels(locale)[item.regime]}</dt>
              <dd>
                <ProbabilityValue value={item.fraction} />
              </dd>
            </div>
          ))}
        </dl>
      </section>
      <section>
        <header>
          <Gauge aria-hidden="true" size={18} strokeWidth={1.5} />
          <h2>{translate(locale, "overview.EvidenceInspector.marketBreadth")}</h2>
        </header>
        <div className="evidence-inspector__primary-value">
          <ProbabilityValue value={data.market_breadth} />
          <span>
            {translate(
              locale,
              "overview.EvidenceInspector.instrumentsAboveTheShortTermMovingAverage",
            )}
          </span>
        </div>
      </section>
      <section className="evidence-inspector__abstain">
        <header>
          <Ban aria-hidden="true" size={18} strokeWidth={1.5} />
          <h2>{translate(locale, "overview.EvidenceInspector.whyNoAssessmentYet")}</h2>
        </header>
        <p>
          {translate(locale, "overview.EvidenceInspector.abstain")}{" "}
          <strong>{data.abstain_count}</strong>
        </p>
        {data.abstain_reasons.length === 0 ? (
          <span>
            {translate(
              locale,
              "overview.EvidenceInspector.noForecastRefusalReasonsCurrently",
            )}
          </span>
        ) : (
          <ul>
            {data.abstain_reasons.map((item) => (
              <li key={item.reason}>
                <span>{getRejectionReasonLabels(locale)[item.reason]}</span>
                <span className="numeric-value">{item.count}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <header>
          <Database aria-hidden="true" size={18} strokeWidth={1.5} />
          <h2>{translate(locale, "overview.EvidenceInspector.dataConfidence")}</h2>
        </header>
        <div className="evidence-inspector__primary-value">
          <ProbabilityValue value={data.data_confidence} />
          <span>
            {translate(
              locale,
              "overview.EvidenceInspector.determinedByFreshnessCompletenessAndPointInTimeEligibility",
            )}
          </span>
        </div>
      </section>
    </aside>
  );
}
