import { ResearchErrorDetail } from "./ResearchErrorDetail";
import { researchEvidenceLabel } from "./research-labels";
import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { Ban, CircleCheck, Gauge, ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";
import type { components } from "../../api/generated/schema";
import { ConfidenceLabel } from "../../components/ConfidenceLabel";
import { rejectionReasonLabel } from "../../app/presentation";
type ForecastBundle = components["schemas"]["ForecastBundle"];
type ForecastEvidence = components["schemas"]["ForecastEvidence"];
type ServingOverlay = components["schemas"]["ServingOverlay"];
function oosSummaryHref(bundle: ForecastBundle) {
  const params = new URLSearchParams({
    as_of: bundle.as_of,
    calibration_version: bundle.calibration_version,
    dataset_revision: bundle.dataset_revision,
    feature_set_version: bundle.feature_set_version,
    model_version: bundle.model_version,
    snapshot_id: bundle.snapshot_id,
    universe_version: bundle.universe_version,
  });
  return `/backtests?${params.toString()}`;
}
export interface AbstainPanelProps {
  bundle: ForecastBundle;
  evidence: ForecastEvidence;
  serving: ServingOverlay;
}
export function AbstainPanel({ bundle, evidence, serving }: AbstainPanelProps) {
  const { locale } = useI18n();
  const intervals = bundle.horizons.flatMap(
    (horizon) => horizon.calibrated_return_intervals,
  );
  const widestInterval = intervals.reduce(
    (widest, interval) => Math.max(widest, interval.upper - interval.lower),
    0,
  );
  const reasons = Array.from(
    new Set([...bundle.abstain_reasons, ...serving.exclusion_reason]),
  );
  return (
    <aside
      aria-label={translate(
        locale,
        "research.abstainpanel.confidenceAbstentionAndFailureEvidence",
      )}
      className={`abstain-panel ${serving.effective_abstain ? "abstain-panel--active" : ""}`}
    >
      <header>
        {serving.effective_abstain ? (
          <Ban aria-hidden="true" size={20} strokeWidth={1.5} />
        ) : (
          <CircleCheck aria-hidden="true" size={20} strokeWidth={1.5} />
        )}
        <div>
          <span>{translate(locale, "research.abstainpanel.executionBoundary")}</span>
          <h2>
            {serving.effective_abstain
              ? translate(
                  locale,
                  "research.abstainpanel.executionIsBlockedForThisForecast",
                )
              : translate(
                  locale,
                  "research.abstainpanel.forecastAvailableForResearchEvaluation",
                )}
          </h2>
        </div>
        <ConfidenceLabel level={bundle.confidence_level} />
      </header>

      {serving.effective_abstain ? (
        <div className="abstain-panel__reasons">
          <strong>
            {translate(
              locale,
              "research.abstainpanel.thisStatusIsNotAnExecutableSignal",
            )}
          </strong>
          <ul
            aria-label={translate(locale, "research.abstainpanel.allAbstentionReasons")}
          >
            {reasons.map((reason) => (
              <li key={reason}>{rejectionReasonLabel(reason, locale)}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <details>
        <summary>
          {translate(
            locale,
            "research.abstainpanel.viewConfidenceCalibrationAndFailureDetails",
          )}
        </summary>
        <dl className="abstain-panel__metrics">
          <div>
            <dt>{translate(locale, "research.abstainpanel.confidenceScore")}</dt>
            <dd className="numeric-value">{bundle.confidence_score.toFixed(3)}</dd>
          </div>
          <div>
            <dt>{translate(locale, "research.abstainpanel.dataFreshness")}</dt>
            <dd className="numeric-value">
              {translate(locale, "research.abstainpanel.seconds.withValues", {
                dataFreshnessSeconds: bundle.data_freshness_seconds,
              })}
            </dd>
          </div>
          <div>
            <dt>OOD</dt>
            <dd className="numeric-value">
              {translate(locale, "research.abstainpanel.threshold.withValues", {
                oodScore: evidence.ood_score.toFixed(3),
                oodThreshold: evidence.ood_threshold.toFixed(3),
              })}
            </dd>
          </div>
          <div>
            <dt>{translate(locale, "research.abstainpanel.modelDisagreement")}</dt>
            <dd className="numeric-value">{evidence.model_disagreement.toFixed(3)}</dd>
          </div>
          <div>
            <dt>
              {translate(locale, "research.abstainpanel.widestCalibratedInterval")}
            </dt>
            <dd className="numeric-value">{(widestInterval * 100).toFixed(1)}%</dd>
          </div>
          <div>
            <dt>{translate(locale, "research.abstainpanel.cqrEmpiricalCoverage")}</dt>
            <dd className="numeric-value">
              {(evidence.calibration_evidence.cqr_empirical_coverage * 100).toFixed(1)}%
            </dd>
          </div>
          <div>
            <dt>{translate(locale, "research.abstainpanel.calibrationError")}</dt>
            <dd className="numeric-value">
              {evidence.calibration_evidence.calibration_error.toFixed(3)}
            </dd>
          </div>
          <div>
            <dt>{translate(locale, "research.abstainpanel.calibrationSamples")}</dt>
            <dd className="numeric-value">
              {evidence.calibration_evidence.probability_bin_sample_count}
            </dd>
          </div>
        </dl>

        <div className="abstain-panel__detail-grid">
          <section>
            <h3>
              <Gauge aria-hidden="true" size={16} strokeWidth={1.5} />
              {translate(
                locale,
                "research.abstainpanel.disagreementAndCalibrationNotes",
              )}
            </h3>
            <p>
              {researchEvidenceLabel(evidence.model_disagreement_summary, locale) ??
                translate(locale, "research.evidence.originalSummary")}
            </p>
            <ResearchErrorDetail message={evidence.model_disagreement_summary} />
            <p>
              {translate(locale, "research.abstainpanel.averageCqrWidth.withValues", {
                oodMethod: evidence.ood_method,
                cqrAverageWidth: (
                  evidence.calibration_evidence.cqr_average_width * 100
                ).toFixed(1),
              })}
            </p>
          </section>
          <section>
            <h3>
              <ShieldAlert aria-hidden="true" size={16} strokeWidth={1.5} />
              {translate(locale, "research.abstainpanel.invalidationConditions")}
            </h3>
            <ul>
              {bundle.invalidation_conditions.map((condition) => (
                <li key={condition}>
                  {researchEvidenceLabel(condition, locale) ??
                    translate(locale, "research.evidence.originalInvalidation")}
                  <ResearchErrorDetail message={condition} />
                </li>
              ))}
            </ul>
          </section>
        </div>

        {bundle.confidence_level === "high" ? (
          <Link className="abstain-panel__oos-link" to={oosSummaryHref(bundle)}>
            {translate(
              locale,
              "research.abstainpanel.viewHistoricalOosPerformanceSummary",
            )}
          </Link>
        ) : null}
      </details>
    </aside>
  );
}
