import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import type { components } from "../../api/generated/schema";
import { ReturnValue } from "../../components/ReturnValue";
type HorizonForecast = components["schemas"]["HorizonForecast"];
type ReturnQuantiles = components["schemas"]["ReturnQuantiles"];
const QUANTILE_KEYS = ["q10", "q25", "q50", "q75", "q90"] as const;
interface QuantileRowProps {
  label: string;
  values: ReturnQuantiles;
}
function QuantileRow({ label, values }: QuantileRowProps) {
  return (
    <tr>
      <th scope="row">{label}</th>
      {QUANTILE_KEYS.map((key) => (
        <td key={key}>
          <ReturnValue value={values[key]} />
        </td>
      ))}
    </tr>
  );
}
export interface QuantileIntervalsProps {
  forecast: HorizonForecast;
}
export function QuantileIntervals({ forecast }: QuantileIntervalsProps) {
  const { locale } = useI18n();
  return (
    <div className="quantile-evidence">
      <div className="research-table-scroll">
        <table
          aria-label={translate(
            locale,
            "research.quantileintervals.dayRawReturnQuantiles",
            { horizon: forecast.horizon },
          )}
        >
          <thead>
            <tr>
              <th scope="col">
                {translate(locale, "research.quantileintervals.returnBasis")}
              </th>
              {QUANTILE_KEYS.map((key) => (
                <th key={key} scope="col">
                  {key.toUpperCase()}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <QuantileRow
              label={translate(locale, "research.quantileintervals.executableReturn")}
              values={forecast.executable_return_quantiles}
            />
            <QuantileRow
              label={translate(locale, "research.quantileintervals.capitalGain")}
              values={forecast.capital_gain_return_quantiles}
            />
            <QuantileRow
              label={translate(locale, "research.quantileintervals.totalReturn")}
              values={forecast.total_return_quantiles}
            />
          </tbody>
        </table>
      </div>

      <div className="research-table-scroll">
        <table
          aria-label={translate(
            locale,
            "research.quantileintervals.dayCqrCalibratedInterval",
            { horizon: forecast.horizon },
          )}
        >
          <thead>
            <tr>
              <th scope="col">
                {translate(locale, "research.quantileintervals.basis")}
              </th>
              <th scope="col">
                {translate(locale, "research.quantileintervals.lowerEndpoint")}
              </th>
              <th scope="col">
                {translate(locale, "research.quantileintervals.upperEndpoint")}
              </th>
              <th scope="col">
                {translate(locale, "research.quantileintervals.method")}
              </th>
              <th scope="col">
                {translate(locale, "research.horizonevidence.targetCoverage")}
              </th>
              <th scope="col">
                {translate(locale, "research.quantileintervals.calibrationVersion")}
              </th>
            </tr>
          </thead>
          <tbody>
            {forecast.calibrated_return_intervals.map((interval) => (
              <tr
                key={`${interval.return_basis}-${interval.method}-${interval.coverage_target}`}
              >
                <th scope="row">{interval.return_basis}</th>
                <td>
                  <ReturnValue value={interval.lower} />
                </td>
                <td>
                  <ReturnValue value={interval.upper} />
                </td>
                <td>{interval.method}</td>
                <td className="numeric-value">
                  {(interval.coverage_target * 100).toFixed(1)}%
                </td>
                <td>{interval.calibration_version}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="research-table-scroll">
        <table
          aria-label={translate(
            locale,
            "research.quantileintervals.dayMfeAndMaeQuantiles",
            { horizon: forecast.horizon },
          )}
        >
          <thead>
            <tr>
              <th scope="col">
                {translate(locale, "research.quantileintervals.withinPathEvidence")}
              </th>
              {QUANTILE_KEYS.map((key) => (
                <th key={key} scope="col">
                  {key.toUpperCase()}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <QuantileRow
              label={translate(locale, "research.quantileintervals.forecastMfe")}
              values={forecast.forecast_mfe_quantiles}
            />
            <QuantileRow
              label={translate(locale, "research.quantileintervals.forecastMae")}
              values={forecast.forecast_mae_quantiles}
            />
            <QuantileRow
              label={translate(locale, "research.quantileintervals.executableMfe")}
              values={forecast.executable_mfe_quantiles}
            />
            <QuantileRow
              label={translate(locale, "research.quantileintervals.executableMae")}
              values={forecast.executable_mae_quantiles}
            />
          </tbody>
        </table>
      </div>
    </div>
  );
}
