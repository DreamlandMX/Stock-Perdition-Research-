import { translate, type Locale } from "../../i18n/core";
import type { components } from "../../api/generated/schema";
import { sectorLabel } from "../../app/presentation";

type OverviewReadModel = components["schemas"]["OverviewReadModel"];

export function previousHistoryPoint(data: OverviewReadModel) {
  const history = data.internal_evidence?.history ?? [];
  return history.length >= 2 ? history.at(-2) : undefined;
}

export function breadthChange(data: OverviewReadModel) {
  const previous = previousHistoryPoint(data);
  return previous === undefined ? null : data.market_breadth - previous.market_breadth;
}

export function buildOverviewInsightTitle(
  data: OverviewReadModel,
  locale: Locale = "zh-CN",
) {
  const change = breadthChange(data);
  const weakest = data.internal_evidence?.sector_uptrend_changes.reduce<
    components["schemas"]["SectorUptrendChange"] | null
  >(
    (current, item) =>
      current === null || item.change < current.change ? item : current,
    null,
  );
  const breadthClause =
    change === null
      ? translate(
          locale,
          "overview.overviewInsights.awaitingCompatibleHistoryToAssessMarketParticipation",
        )
      : change >= 0.03
        ? translate(locale, "overview.overviewInsights.marketParticipationImproving")
        : change <= -0.03
          ? translate(locale, "overview.overviewInsights.marketParticipationWeakening")
          : translate(
              locale,
              "overview.overviewInsights.limitedChangeInMarketParticipation",
            );
  if (weakest !== null && weakest !== undefined && weakest.change <= -0.05) {
    return translate(
      locale,
      change !== null && change >= 0.03
        ? "overview.insight.weakSectorDespiteBreadth"
        : "overview.insight.weakSector",
      { breadth: breadthClause, sector: sectorLabel(weakest.sector, locale) },
    );
  }
  return breadthClause;
}

const RANKING_BASIS_KEYS = [
  "overview.ranking.upwardBreakout",
  "overview.ranking.relativeStrength",
  "overview.ranking.lowerReturnScenario",
  "overview.ranking.medianReturnScenario",
] as const;

export function rankingBasisPresentation(value: string, locale: Locale = "zh-CN") {
  const key = RANKING_BASIS_KEYS.find(
    (candidate) =>
      translate("zh-CN", candidate) === value ||
      translate("en-US", candidate) === value,
  );
  return {
    label: translate(locale, key ?? "overview.ranking.unsupported"),
    original: key === undefined ? value : null,
  };
}

export interface OverviewRiskItem {
  detail: string;
  key: "alignment" | "weakest-sector" | "regime-change" | "volume" | "coverage";
  label: string;
  tone: "positive" | "warning" | "negative" | "neutral";
  value: string;
}

function signedPp(value: number) {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value * 100).toFixed(1)}pp`;
}

export function buildOverviewRiskItems(
  data: OverviewReadModel,
  predictionReadiness: components["schemas"]["PredictionReadiness"],
  locale: Locale = "zh-CN",
): OverviewRiskItem[] {
  const breadthDelta = breadthChange(data);
  const proxyAverage =
    data.proxies.length === 0
      ? null
      : data.proxies.reduce((sum, proxy) => sum + proxy.session_return, 0) /
        data.proxies.length;
  const aligned =
    breadthDelta !== null &&
    proxyAverage !== null &&
    (Math.sign(breadthDelta) === Math.sign(proxyAverage) || breadthDelta === 0);
  const weakest = data.internal_evidence?.sector_uptrend_changes.reduce<
    components["schemas"]["SectorUptrendChange"] | null
  >(
    (current, item) =>
      current === null || item.change < current.change ? item : current,
    null,
  );
  const transitions = data.internal_evidence?.regime_changes;
  const volume = data.internal_evidence?.volume_anomalies;

  return [
    {
      detail:
        breadthDelta === null || proxyAverage === null
          ? translate(
              locale,
              "overview.overviewInsights.requiresAtLeastTwoCompatibleSnapshotsWithMarketProxy",
            )
          : translate(
              locale,
              "overview.overviewInsights.breadthFourProxyAverageSessionReturn",
              {
                change: signedPp(breadthDelta),
                value: (proxyAverage * 100).toFixed(1),
              },
            ),
      key: "alignment",
      label: aligned
        ? translate(locale, "overview.overviewInsights.breadthAndIndicesAligned")
        : translate(locale, "overview.overviewInsights.breadthAndIndicesDiverging"),
      tone:
        breadthDelta === null || proxyAverage === null
          ? "neutral"
          : aligned
            ? "positive"
            : "warning",
      value:
        breadthDelta === null
          ? translate(locale, "overview.SectorMarketMap.insufficientHistory")
          : aligned
            ? translate(locale, "overview.overviewInsights.aligned")
            : translate(locale, "overview.overviewInsights.diverging"),
    },
    {
      detail:
        weakest == null
          ? translate(
              locale,
              "overview.overviewInsights.atLeast5SectorSamplesRequiredInBothCurrent",
            )
          : translate(locale, "overview.overviewInsights.uptrendShare", {
              value: (weakest.previous_uptrend_fraction * 100).toFixed(1),
              value2: (weakest.uptrend_fraction * 100).toFixed(1),
            }),
      key: "weakest-sector",
      label: translate(locale, "overview.overviewInsights.weakestSector"),
      tone: weakest == null ? "neutral" : weakest.change < 0 ? "negative" : "positive",
      value:
        weakest == null
          ? translate(locale, "overview.SectorMarketMap.insufficientHistory")
          : `${sectorLabel(weakest.sector, locale)} ${signedPp(weakest.change)}`,
    },
    {
      detail:
        transitions == null
          ? translate(
              locale,
              "overview.overviewInsights.noCompatibleComparisonSnapshotChangeIsNotShownAs",
            )
          : translate(locale, "overview.overviewInsights.toUptrendToDowntrend", {
              toUptrend: data.internal_evidence?.to_uptrend ?? 0,
              toDowntrend: data.internal_evidence?.to_downtrend ?? 0,
            }),
      key: "regime-change",
      label: translate(locale, "overview.SectorMarketMap.regimeChange"),
      tone: transitions == null ? "neutral" : transitions > 20 ? "warning" : "neutral",
      value:
        transitions == null
          ? translate(locale, "overview.SectorMarketMap.insufficientHistory")
          : translate(locale, "overview.OverviewPulse.instruments", {
              transitions: transitions,
            }),
    },
    {
      detail: translate(locale, "overview.overviewInsights.threshold20DayVolumeZ2"),
      key: "volume",
      label: translate(locale, "overview.OverviewPulse.volumeAnomalies"),
      tone: volume == null ? "neutral" : volume > 0 ? "warning" : "positive",
      value:
        volume == null
          ? translate(locale, "overview.OverviewPulse.noData")
          : translate(locale, "overview.OverviewPulse.instruments", {
              transitions: volume,
            }),
    },
    {
      detail:
        predictionReadiness === "forecast_ready"
          ? translate(
              locale,
              "overview.overviewInsights.completeChampionModelAvailableInTheCurrentSnapshot",
            )
          : translate(
              locale,
              "overview.overviewInsights.researchEvidenceAvailableNoExecutableSignals",
            ),
      key: "coverage",
      label: translate(locale, "overview.overviewInsights.formalForecastCoverage"),
      tone: predictionReadiness === "forecast_ready" ? "positive" : "warning",
      value:
        predictionReadiness === "forecast_ready"
          ? translate(locale, "overview.overviewInsights.ready")
          : translate(locale, "overview.overviewInsights.instrumentsUncovered", {
              abstainCount: data.abstain_count,
            }),
    },
  ];
}
