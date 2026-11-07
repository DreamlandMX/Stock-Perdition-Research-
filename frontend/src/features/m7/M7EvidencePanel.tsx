import { translate, type Locale } from "../../i18n/core";
import { modelStatusLabel, reasonLabel } from "../../app/presentation";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
type M7Capability = components["schemas"]["M7CapabilityLifecycleStatus"];
type EvidenceSection = components["schemas"]["M7LifecycleEvidenceSection"];
function objectValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function rowsValue(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value
        .map(objectValue)
        .filter((row): row is Record<string, unknown> => row !== null)
    : [];
}
function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function scalarText(
  value: unknown,
  fallback: string | undefined = undefined,
  locale: Locale = "zh-CN",
): string {
  return typeof value === "string" || typeof value === "number"
    ? `${value}`
    : (fallback ?? translate(locale, "m7.unavailable"));
}
function fixed(value: unknown, digits = 4, locale: Locale = "zh-CN"): string {
  const number = numberValue(value);
  return number === null ? translate(locale, "m7.unavailable") : number.toFixed(digits);
}
function percent(value: unknown, locale: Locale = "zh-CN"): string {
  const number = numberValue(value);
  return number === null
    ? translate(locale, "m7.unavailable")
    : `${(number * 100).toFixed(2)}%`;
}
function section(capability: M7Capability, name: string): EvidenceSection | undefined {
  return capability.evidence_sections?.[name];
}
function HorizonEvidence({ capability }: { capability: M7Capability }) {
  const { locale } = useI18n();
  const baseline = objectValue(section(capability, "baseline_deltas")?.payload);
  const bootstrap = objectValue(section(capability, "bootstrap_results")?.payload);
  if (!baseline) {
    return (
      <p className="m7-evidence-empty">
        {translate(locale, "m7.developmentMetricsUnavailable")}
      </p>
    );
  }
  const horizons = Object.entries(baseline).map(([horizon, raw]) => {
    const gate = objectValue(raw) ?? {};
    const metrics = objectValue(gate.metrics) ?? {};
    const candidate = metrics.candidate_brier_mean ?? metrics.candidate_time_brier_mean;
    const incumbent = metrics.incumbent_brier_mean ?? metrics.incumbent_time_brier_mean;
    const candidateNumber = numberValue(candidate);
    const incumbentNumber = numberValue(incumbent);
    const improvement =
      candidateNumber !== null && incumbentNumber !== null && incumbentNumber > 0
        ? (incumbentNumber - candidateNumber) / incumbentNumber
        : null;
    return {
      horizon,
      status: typeof gate.status === "string" ? gate.status : "unavailable",
      candidate,
      incumbent,
      improvement,
      foldWins: metrics.incumbent_fold_wins ?? translate(locale, "m7.unavailable"),
      ecePasses:
        metrics.ece_fold_passes ??
        metrics.calibration_fold_passes ??
        translate(locale, "m7.unavailable"),
    };
  });
  return (
    <div className="m7-table-wrap">
      <table className="m7-metric-table">
        <caption>
          {translate(locale, "m7.developmentHorizonProbabilitiesAndCalibrationGates")}
        </caption>
        <thead>
          <tr>
            <th scope="col">{translate(locale, "m7.horizon")}</th>
            <th scope="col">{translate(locale, "m7.challengerBrier")}</th>
            <th scope="col">{translate(locale, "m7.incumbent")}</th>
            <th scope="col">{translate(locale, "m7.relativeImprovement")}</th>
            <th scope="col">{translate(locale, "m7.winningFolds")}</th>
            <th scope="col">{translate(locale, "m7.eceEligibleFolds")}</th>
            <th scope="col">{translate(locale, "m7.gate")}</th>
          </tr>
        </thead>
        <tbody>
          {horizons.map((row) => (
            <tr key={row.horizon}>
              <th scope="row">H{row.horizon}</th>
              <td>{fixed(row.candidate, undefined, locale)}</td>
              <td>{fixed(row.incumbent, undefined, locale)}</td>
              <td>{percent(row.improvement, locale)}</td>
              <td>{scalarText(row.foldWins, undefined, locale)} / 4</td>
              <td>{scalarText(row.ecePasses, undefined, locale)} / 4</td>
              <td>
                {row.status === "passed"
                  ? translate(locale, "m7.pass")
                  : translate(locale, "m7.failed")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="m7-evidence-note">
        {" " + translate(locale, "m7.aggregateBootstrapUpperBound")}
        {fixed(bootstrap?.delta_brier_upper_90, 6, locale)}
      </p>
    </div>
  );
}
function AblationEvidence({ capability }: { capability: M7Capability }) {
  const { locale } = useI18n();
  const payload = objectValue(section(capability, "feature_ablation")?.payload);
  const rows = rowsValue(payload?.rows);
  if (rows.length === 0) return null;
  return (
    <div className="m7-mini-panel">
      <h5>{translate(locale, "m7.featureAblation")}</h5>
      <ul className="m7-compact-list">
        {rows.map((row) => (
          <li key={String(row.feature_set)}>
            <span>{String(row.feature_set)}</span>
            <strong>
              {row.status === "completed"
                ? `ΔBrier ${fixed(row.delta_symbol_equal_brier_vs_core, 5, locale)}`
                : modelStatusLabel(scalarText(row.status, undefined, locale), locale)}
            </strong>
          </li>
        ))}
      </ul>
    </div>
  );
}
function ExplanationEvidence({ capability }: { capability: M7Capability }) {
  const { locale } = useI18n();
  const evidence = section(capability, "explanation_manifest");
  const payload = objectValue(evidence?.payload);
  const status = scalarText(payload?.status, evidence?.status ?? "not_run", locale);
  const reason = scalarText(
    payload?.reason,
    evidence?.reason ?? translate(locale, "m7.explanationEvidenceNotYetGenerated"),
    locale,
  );
  const groups = rowsValue(payload?.feature_groups);
  const factors = rowsValue(payload?.stable_factors);
  return (
    <div className="m7-mini-panel">
      <h5>{translate(locale, "m7.modelExplanations")}</h5>
      <p>
        {status === "available"
          ? translate(locale, "m7.stableExplanationsAvailable")
          : translate(locale, "m7.explanationsUnavailable", {
              reason: reasonLabel(reason, locale),
            })}
      </p>
      {groups.length > 0 ? (
        <ul className="m7-compact-list">
          {groups.slice(0, 5).map((row) => (
            <li key={String(row.feature_group)}>
              <span>{String(row.feature_group)}</span>
              <strong>{percent(row.normalized_importance, locale)}</strong>
            </li>
          ))}
        </ul>
      ) : null}
      {factors.length > 0 ? (
        <p>
          {factors
            .slice(0, 5)
            .map((row) => String(row.feature_name))
            .join("; ")}
        </p>
      ) : null}
      <details>
        <summary>{translate(locale, "m7.originalDetails")}</summary>
        {reason}
      </details>
      <small>{translate(locale, "m7.modelAssociationsDoNotImplyCausation")}</small>
    </div>
  );
}
function EconomicEvidence({ capability }: { capability: M7Capability }) {
  const { locale } = useI18n();
  const payload = objectValue(section(capability, "economic_diagnostics")?.payload);
  const horizons = objectValue(payload?.horizons);
  if (!horizons) {
    return (
      <div className="m7-mini-panel">
        <h5>{translate(locale, "m7.economicDiagnostics")}</h5>
        <p>
          {translate(locale, "m7.economicDiagnosticsAreUnavailableForThisCapability")}
        </p>
      </div>
    );
  }
  return (
    <div className="m7-mini-panel">
      <h5>{translate(locale, "m7.economicDiagnosticsResearchOnly")}</h5>
      <div className="m7-table-wrap">
        <table className="m7-metric-table m7-metric-table--compact">
          <thead>
            <tr>
              <th scope="col">H</th>
              <th scope="col">{translate(locale, "m7.netReturn")}</th>
              <th scope="col">Sharpe</th>
              <th scope="col">{translate(locale, "m7.maximumDrawdown")}</th>
              <th scope="col">{translate(locale, "m7.exposure")}</th>
              <th scope="col">{translate(locale, "m7.turnover")}</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(horizons).map(([horizon, raw]) => {
              const candidate = objectValue(objectValue(raw)?.candidate) ?? {};
              return (
                <tr key={horizon}>
                  <th scope="row">H{horizon}</th>
                  <td>{percent(candidate.net_return, locale)}</td>
                  <td>{fixed(candidate.annualized_sharpe, 2, locale)}</td>
                  <td>{percent(candidate.maximum_drawdown, locale)}</td>
                  <td>{percent(candidate.average_gross_exposure, locale)}</td>
                  <td>{percent(candidate.average_turnover, locale)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <small>{translate(locale, "m7.includesTwoWayTradingCostsThisDoesNot")}</small>
    </div>
  );
}
export function M7EvidencePanel({ capability }: { capability: M7Capability }) {
  const { locale } = useI18n();
  return (
    <section
      aria-label={translate(locale, "m7.m7DevelopmentEvidence")}
      className="m7-development-evidence"
    >
      <h4>{translate(locale, "m7.developmentEvidence")}</h4>
      <HorizonEvidence capability={capability} />
      <div className="m7-evidence-columns">
        <AblationEvidence capability={capability} />
        <ExplanationEvidence capability={capability} />
        <EconomicEvidence capability={capability} />
      </div>
    </section>
  );
}
