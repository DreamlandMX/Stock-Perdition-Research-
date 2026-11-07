import { translate, type Locale, type MessageKey } from "../../i18n/core";

const evidenceLabels: Readonly<Record<string, MessageKey>> = {
  direction_breakout_probability_gap: "research.evidence.directionBreakoutGap",
  classifier_quantile_aligned: "research.evidence.classifierQuantileAligned",
  data_revision_changed: "research.evidence.dataRevisionChanged",
  corporate_action_revision: "research.evidence.corporateActionRevision",
  model_version_changed: "research.evidence.modelVersionChanged",
  close_above_frozen_channel: "research.evidence.closeAboveFrozenChannel",
  right_censored_at_horizon: "research.evidence.rightCensoredAtHorizon",
  "new corporate action": "research.evidence.newCorporateAction",
  american: "research.contract.american",
  American: "research.contract.american",
  european: "research.contract.european",
  European: "research.contract.european",
  physical: "research.contract.physical",
  cash: "research.contract.cash",
};

export function researchEvidenceLabel(code: string, locale: Locale): string | null {
  const key = evidenceLabels[code];
  return key === undefined ? null : translate(locale, key);
}
