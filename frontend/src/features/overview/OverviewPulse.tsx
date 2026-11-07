import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
import { ReturnValue } from "../../components/ReturnValue";
import { buildOverviewInsightTitle, breadthChange } from "./overview-insights";

type OverviewReadModel = components["schemas"]["OverviewReadModel"];

function percentage(value: number | null | undefined, locale: Locale = "zh-CN") {
  return value == null
    ? translate(locale, "overview.OverviewPulse.noData")
    : `${(value * 100).toFixed(1)}%`;
}

function signedPp(value: number | null) {
  if (value === null) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value * 100).toFixed(1)}pp`;
}

function Sparkline({ label, values }: { label: string; values: readonly number[] }) {
  if (values.length < 2) return null;
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const range = maximum - minimum || 1;
  const points = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * 100;
      const y = 28 - ((value - minimum) / range) * 24;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg
      aria-label={label}
      className="overview-sparkline"
      role="img"
      viewBox="0 0 100 32"
    >
      <polyline fill="none" points={points} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
export function OverviewPulse({ data }: { data: OverviewReadModel }) {
  const { locale } = useI18n();
  const evidence = data.internal_evidence;
  const history = evidence?.history ?? [];
  const uptrend = data.regime_distribution.find((item) => item.regime === "uptrend");
  const previous = history.length >= 2 ? history.at(-2) : undefined;
  const uptrendDelta =
    previous === undefined || uptrend === undefined
      ? null
      : uptrend.fraction - previous.uptrend_fraction;
  const breakouts =
    evidence?.breakout_up == null || evidence.breakout_down == null
      ? translate(locale, "overview.OverviewPulse.noData")
      : translate(locale, "overview.OverviewPulse.upDown", {
          breakoutUp: evidence.breakout_up,
          breakoutDown: evidence.breakout_down,
        });

  return (
    <section
      aria-label={translate(locale, "overview.OverviewPulse.todaySMarketPulse")}
      className="overview-pulse overview-section"
    >
      <header className="overview-pulse__headline">
        <div>
          <span>{translate(locale, "overview.OverviewPulse.todaySMarketPulse")}</span>
          <h2>{buildOverviewInsightTitle(data, locale)}</h2>
        </div>
        <span>
          {history.length}
          {translate(locale, "overview.OverviewPulse.5CompatibleSnapshots")}
        </span>
      </header>
      <div className="overview-pulse__rail">
        <article>
          <span>
            {translate(locale, "overview.OverviewPulse.20DayPositiveReturnBreadth")}
          </span>
          <strong>{percentage(data.market_breadth, locale)}</strong>
          <small>{signedPp(breadthChange(data))}</small>
          <Sparkline
            label={translate(
              locale,
              "overview.OverviewPulse.marketBreadthAcrossTheLastFiveSnapshots",
            )}
            values={history.map((point) => point.market_breadth)}
          />
        </article>
        <article>
          <span>{translate(locale, "overview.OverviewPulse.uptrendShare")}</span>
          <strong>{percentage(uptrend?.fraction, locale)}</strong>
          <small>{signedPp(uptrendDelta)}</small>
          <Sparkline
            label={translate(
              locale,
              "overview.OverviewPulse.uptrendShareAcrossTheLastFiveSnapshots",
            )}
            values={history.map((point) => point.uptrend_fraction)}
          />
        </article>
        <article>
          <span>{translate(locale, "overview.OverviewPulse.upDownUnchanged")}</span>
          <strong>
            {evidence?.advancers ?? "—"}{" "}
            {translate(locale, "overview.OverviewPulse.up")}{" "}
            {evidence?.decliners ?? "—"}{" "}
            {translate(locale, "overview.OverviewPulse.down")}{" "}
            {evidence?.unchanged ?? "—"}{" "}
            {translate(locale, "overview.OverviewPulse.unchanged")}
          </strong>
          <small>
            {data.regime_distribution.reduce((total, item) => total + item.count, 0)}{" "}
            {translate(locale, "overview.OverviewPulse.instrumentCrossSection")}
          </small>
        </article>
        <article>
          <span>{translate(locale, "overview.SectorMarketMap.regimeChange")}</span>
          <strong>
            {evidence?.regime_changes == null
              ? "—"
              : translate(locale, "overview.OverviewPulse.instruments", {
                  transitions: evidence.regime_changes,
                })}
          </strong>
          <small>
            {evidence?.to_uptrend == null
              ? translate(locale, "overview.OverviewPulse.awaitingCompatibleSnapshots")
              : translate(locale, "overview.OverviewPulse.toUptrendToDowntrend", {
                  toUptrend: evidence.to_uptrend,
                  toDowntrend: evidence.to_downtrend ?? "—",
                })}
          </small>
        </article>
        <article>
          <span>{translate(locale, "overview.OverviewPulse.volumeAnomalies")}</span>
          <strong>
            {evidence?.volume_anomalies == null
              ? translate(locale, "overview.OverviewPulse.noData")
              : translate(locale, "overview.OverviewPulse.instruments", {
                  transitions: evidence.volume_anomalies,
                })}
          </strong>
          <small>|Z20| ≥ 2</small>
        </article>
        <article>
          <span>{translate(locale, "overview.OverviewPulse.ruleBreakout")}</span>
          <strong>{breakouts}</strong>
          <small>{translate(locale, "overview.OverviewPulse.upDown2")}</small>
        </article>
      </div>
      {history.length < 5 ? (
        <p className="overview-pulse__history-note">
          {translate(
            locale,
            "overview.OverviewPulse.noCompatibleComparisonSnapshotForTheCurrentUniverseCurrently",
          )}{" "}
          {history.length}
          {translate(
            locale,
            "overview.OverviewPulse.5ChangesAndTrendsCompareTheSameUniverseOnly",
          )}
        </p>
      ) : null}
    </section>
  );
}

export function CrossSectionEvidence({ data }: { data: OverviewReadModel }) {
  const { locale } = useI18n();
  const evidence = data.internal_evidence;
  return (
    <section
      aria-label={translate(locale, "overview.OverviewPulse.crossSectionalEvidence")}
      className="cross-section-evidence"
    >
      <div>
        <span>
          {translate(locale, "overview.OverviewPulse.crossSectionalMedianReturn")}
        </span>
        <ReturnValue value={evidence?.median_session_return ?? null} />
      </div>
      <div>
        <span>{translate(locale, "overview.OverviewPulse.returnDispersion")}</span>
        <strong>{percentage(evidence?.return_dispersion, locale)}</strong>
      </div>
      <div>
        <span>{translate(locale, "overview.OverviewPulse.comparisonSnapshot")}</span>
        <strong>
          {evidence?.comparison_snapshot == null
            ? translate(locale, "overview.OverviewPulse.noCompatibleComparisonSnapshot")
            : `${evidence.comparison_snapshot.as_of.slice(5, 10)} ; r${evidence.comparison_snapshot.revision_no}`}
        </strong>
      </div>
    </section>
  );
}
