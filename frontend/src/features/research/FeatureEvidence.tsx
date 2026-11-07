import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
import { ReturnValue } from "../../components/ReturnValue";
type ForecastEvidence = components["schemas"]["ForecastEvidence"];
export interface FeatureEvidenceProps {
  evidence: ForecastEvidence;
}
export function FeatureEvidence({ evidence }: FeatureEvidenceProps) {
  const { locale } = useI18n();
  return (
    <section className="research-card feature-evidence">
      <header className="research-card__header">
        <div>
          <span>
            {translate(locale, "research.featureevidence.shapFeatureFamilyEvidence")}
          </span>
          <h2>
            {translate(
              locale,
              "research.featureevidence.featureAssociationExplanation",
            )}
          </h2>
        </div>
      </header>
      <div className="feature-evidence__notice">
        <strong>
          {translate(
            locale,
            "research.featureevidence.associationDoesNotImplyCausation",
          )}
        </strong>
        <span>
          {translate(
            locale,
            "research.featureevidence.contributionDirectionDescribesStatisticalAssociationWithTheCurrentModel",
          )}
        </span>
      </div>
      <div className="research-table-scroll">
        <table
          aria-label={translate(
            locale,
            "research.featureevidence.shapFeatureFamilyAssociationEvidence",
          )}
        >
          <thead>
            <tr>
              <th scope="col">
                {translate(locale, "research.featureevidence.featureFamily")}
              </th>
              <th scope="col">
                {translate(locale, "research.featureevidence.associatedContribution")}
              </th>
              <th scope="col">
                {translate(locale, "research.featureevidence.trainingQuantile")}
              </th>
            </tr>
          </thead>
          <tbody>
            {evidence.top_feature_families.map((family) => (
              <tr key={family.family}>
                <th scope="row">{family.family}</th>
                <td>
                  <ReturnValue value={family.contribution} />
                </td>
                <td className="numeric-value">
                  {(family.training_percentile * 100).toFixed(1)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
