import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { CSSProperties } from "react";
import type { components } from "../../api/generated/schema";
import { sectorLabel } from "../../app/presentation";

function signedPp(value: number) {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value * 100).toFixed(1)}pp`;
}

export function SectorChangePanel({
  changes,
}: {
  changes: readonly components["schemas"]["SectorUptrendChange"][];
}) {
  const { locale } = useI18n();
  const visible = [...changes]
    .sort((left, right) => Math.abs(right.change) - Math.abs(left.change))
    .slice(0, 8);
  return (
    <section
      aria-label={translate(locale, "overview.SectorChangePanel.sectorStrengthChanges")}
      className="overview-section sector-change"
    >
      <header className="overview-section__header">
        <h2>{translate(locale, "overview.SectorChangePanel.sectorStrengthChanges")}</h2>
        <span>
          {translate(
            locale,
            "overview.SectorChangePanel.uptrendShareVsPreviousCompatibleSnapshot5SamplesOn",
          )}
        </span>
      </header>
      {visible.length === 0 ? (
        <p className="sector-change__empty">
          {translate(locale, "overview.SectorMarketMap.insufficientHistory")}
        </p>
      ) : (
        <ol>
          {visible.map((item) => (
            <li key={item.sector}>
              <span>{sectorLabel(item.sector, locale)}</span>
              <div className="sector-change__track" aria-hidden="true">
                <span
                  className={
                    item.change >= 0
                      ? "sector-change__bar--up"
                      : "sector-change__bar--down"
                  }
                  style={
                    {
                      "--sector-change-width": `${Math.min(Math.abs(item.change) / 0.3, 1) * 50}%`,
                    } as CSSProperties
                  }
                />
              </div>
              <strong>{signedPp(item.change)}</strong>
              <small>
                {item.sample_count}{" "}
                {translate(locale, "overview.SectorRegimeMatrix.instruments")}
              </small>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
