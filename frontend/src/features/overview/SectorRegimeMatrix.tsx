import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
import { sectorLabel } from "../../app/presentation";
import { getRegimeLabels } from "./RegimeDistribution";

type SectorRegimeCell = components["schemas"]["SectorRegimeCell"];
type RegimeLabel = components["schemas"]["RegimeLabel"];

const MATRIX_REGIMES: readonly RegimeLabel[] = [
  "uptrend",
  "range",
  "transition",
  "downtrend",
  "invalid",
];

export interface SectorRegimeMatrixProps {
  cells: readonly SectorRegimeCell[];
}

function percentage(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

export function SectorRegimeMatrix({ cells }: SectorRegimeMatrixProps) {
  const { locale } = useI18n();
  const sectors = Array.from(new Set(cells.map((cell) => cell.sector)));
  const sampleCount = cells.reduce((total, cell) => total + cell.count, 0);
  const values = new Map(
    cells.map((cell) => [`${cell.sector}:${cell.regime}`, cell] as const),
  );

  return (
    <section className="overview-section sector-matrix-section">
      <header className="overview-section__header">
        <h2>{translate(locale, "overview.SectorRegimeMatrix.sectorRegimeMatrix")}</h2>
        <span>
          {translate(locale, "overview.SectorRegimeMatrix.sectorSampleTotal")}{" "}
          {sampleCount}{" "}
          {translate(
            locale,
            "overview.SectorRegimeMatrix.instrumentsOrderedBySectorInstrumentCount",
          )}
        </span>
      </header>
      <div className="sector-matrix__scroll">
        <table
          aria-label={translate(
            locale,
            "overview.SectorRegimeMatrix.sectorRegimeMatrix",
          )}
          className="sector-matrix"
        >
          <thead>
            <tr>
              <th scope="col">
                {translate(locale, "overview.SectorRegimeMatrix.sector")}
              </th>
              {MATRIX_REGIMES.map((regime) => (
                <th key={regime} scope="col">
                  {getRegimeLabels(locale)[regime]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sectors.map((sector) => (
              <tr key={sector}>
                <th scope="row">{sectorLabel(sector, locale)}</th>
                {MATRIX_REGIMES.map((regime) => {
                  const cell = values.get(`${sector}:${regime}`);
                  return (
                    <td className={`regime-state--${regime}`} key={regime}>
                      {cell === undefined ? (
                        "—"
                      ) : (
                        <>
                          <span>{percentage(cell.fraction)}</span>
                          <span>
                            {" "}
                            ; {cell.count}{" "}
                            {translate(
                              locale,
                              "overview.SectorRegimeMatrix.instruments",
                            )}
                          </span>
                        </>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
